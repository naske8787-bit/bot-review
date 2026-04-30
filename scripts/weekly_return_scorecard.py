#!/usr/bin/env python3
import argparse
import csv
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class WeeklyMetrics:
    closed_trades: int
    win_rate: float
    profit_factor: float
    realized_pnl: float
    max_drawdown: float
    estimated_return_pct: float
    data_quality: str


def trading_metrics(repo_root):
    trade_rows = read_csv_rows(os.path.join(repo_root, "trading_bot", "logs", "trade_log.csv"))
    equity_rows = read_csv_rows(os.path.join(repo_root, "trading_bot", "logs", "equity_log.csv"))

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    # Reconstruct closed PnL using FIFO lots.
    buys = {}
    closed_pnls = []
    for row in trade_rows:
        ts = parse_ts(row.get("timestamp"))
        if not ts or ts < since:
            continue
        symbol = str(row.get("symbol") or "").upper()
        action = str(row.get("action") or "").upper()
        qty = max(0.0, to_float(row.get("qty"), 0.0))
        price = max(0.0, to_float(row.get("price"), 0.0))
        if not symbol or qty <= 0 or price <= 0:
            continue

        if action == "BUY":
            buys.setdefault(symbol, []).append([qty, price])
            continue
        if action != "SELL":
            continue

        remaining = qty
        pnl = 0.0
        lots = buys.get(symbol, [])
        while remaining > 0 and lots:
            lot_qty, lot_price = lots[0]
            matched = min(remaining, lot_qty)
            pnl += (price - lot_price) * matched
            lot_qty -= matched
            remaining -= matched
            if lot_qty <= 1e-9:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        if pnl != 0.0:
            closed_pnls.append(pnl)

    wins = [x for x in closed_pnls if x > 0]
    losses = [x for x in closed_pnls if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
    wr = (len(wins) / len(closed_pnls)) if closed_pnls else 0.0

    values = []
    for row in equity_rows:
        ts = parse_ts(row.get("timestamp"))
        if not ts or ts < since:
            continue
        v = to_float(row.get("portfolio_value"), 0.0)
        if v > 0:
            values.append(v)
    est_ret = 0.0
    max_dd = 0.0
    if len(values) >= 2:
        est_ret = ((values[-1] - values[0]) / values[0]) * 100.0
        peak = values[0]
        for v in values:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)

    quality = "low"
    if len(closed_pnls) >= 30 and len(values) >= 30:
        quality = "high"
    elif len(closed_pnls) >= 10 and len(values) >= 10:
        quality = "medium"

    return WeeklyMetrics(
        closed_trades=len(closed_pnls),
        win_rate=wr,
        profit_factor=pf,
        realized_pnl=sum(closed_pnls),
        max_drawdown=max_dd,
        estimated_return_pct=est_ret,
        data_quality=quality,
    )


def crypto_metrics(repo_root):
    trade_rows = read_csv_rows(os.path.join(repo_root, "crypto_bot", "logs", "trade_log.csv"))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    closed = []
    for row in trade_rows:
        ts = parse_ts(row.get("exit_time") or row.get("timestamp"))
        if not ts or ts < since:
            continue
        pnl = to_float(row.get("pnl"), 0.0)
        closed.append(pnl)

    wins = [x for x in closed if x > 0]
    losses = [x for x in closed if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
    wr = (len(wins) / len(closed)) if closed else 0.0

    # Crypto log often lacks equity snapshots; estimate return from a 100k baseline only as rough proxy.
    est_ret = (sum(closed) / 100000.0) * 100.0 if closed else 0.0
    quality = "low"
    if len(closed) >= 40:
        quality = "high"
    elif len(closed) >= 12:
        quality = "medium"

    return WeeklyMetrics(
        closed_trades=len(closed),
        win_rate=wr,
        profit_factor=pf,
        realized_pnl=sum(closed),
        max_drawdown=0.0,
        estimated_return_pct=est_ret,
        data_quality=quality,
    )


def probability_of_target(metrics, target_return_pct):
    # Heuristic score transformed into probability with logistic function.
    score = 0.0
    score += min(20.0, metrics.closed_trades * 0.6)
    score += max(-10.0, min(20.0, (metrics.win_rate - 0.50) * 80.0))
    score += max(-8.0, min(20.0, (metrics.profit_factor - 1.0) * 20.0))
    score += max(-15.0, min(15.0, metrics.estimated_return_pct * 1.2))
    score -= max(0.0, (metrics.max_drawdown - 0.10) * 120.0)

    if metrics.data_quality == "low":
        score -= 10.0
    elif metrics.data_quality == "medium":
        score -= 4.0

    # Hard penalty when there is no closed-trade evidence.
    if metrics.closed_trades == 0:
        score -= 15.0

    # Adjust difficulty by target level.
    difficulty = max(0.0, target_return_pct - 10.0) * 0.8
    z = (score - difficulty) / 12.0
    prob = 1.0 / (1.0 + math.exp(-z))

    # Confidence caps to avoid overfitting tiny or synthetic-looking samples.
    if metrics.data_quality == "low":
        prob = min(prob, 0.35)
    if metrics.closed_trades < 10:
        prob = min(prob, 0.40)
    if metrics.closed_trades < 5:
        prob = min(prob, 0.25)
    return prob


def main():
    parser = argparse.ArgumentParser(description="Weekly return probability scorecard for trading and crypto bots")
    parser.add_argument("--repo-root", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    parser.add_argument("--target-return", type=float, default=20.0, help="Target return percentage")
    args = parser.parse_args()

    t = trading_metrics(args.repo_root)
    c = crypto_metrics(args.repo_root)

    t_prob = probability_of_target(t, args.target_return)
    c_prob = probability_of_target(c, args.target_return)
    joint = 1.0 - ((1.0 - t_prob) * (1.0 - c_prob))

    payload = {
        "target_return_pct": args.target_return,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trading_bot": {
            "closed_trades_7d": t.closed_trades,
            "win_rate_7d": t.win_rate,
            "profit_factor_7d": t.profit_factor,
            "realized_pnl_7d": t.realized_pnl,
            "max_drawdown_7d": t.max_drawdown,
            "estimated_return_7d_pct": t.estimated_return_pct,
            "data_quality": t.data_quality,
            "probability_target_hit": t_prob,
        },
        "crypto_bot": {
            "closed_trades_7d": c.closed_trades,
            "win_rate_7d": c.win_rate,
            "profit_factor_7d": c.profit_factor,
            "realized_pnl_7d": c.realized_pnl,
            "max_drawdown_7d": c.max_drawdown,
            "estimated_return_7d_pct": c.estimated_return_pct,
            "data_quality": c.data_quality,
            "probability_target_hit": c_prob,
        },
        "combined_probability_at_least_one_hits_target": joint,
    }

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
