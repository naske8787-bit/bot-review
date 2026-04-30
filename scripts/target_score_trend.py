#!/usr/bin/env python3
"""Track scorecard trend over time for trading and crypto bots.

Usage:
  python3 scripts/target_score_trend.py
  python3 scripts/target_score_trend.py --window-minutes 30 --target-return-pct 10 --autofix
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

TS_RX = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s?(.*)$")
AUTH_RX = re.compile(
    r"unauthorized|forbidden|\b(?:http\s*)?(?:401|403)\b|status(?:\s*code)?\s*[:=]\s*(?:401|403)",
    re.IGNORECASE,
)
HISTORY_PATH = Path("scripts/.target_score_history.jsonl")


@dataclass
class BotConfig:
    name: str
    log_path: Path
    startup_marker: str
    tmux_session: str
    run_script: str


BOTS = [
    BotConfig(
        "crypto",
        Path("crypto_bot/bot.log"),
        "Crypto bot started in paper-trading mode",
        "crypto_bot",
        "crypto_bot/run_tmux.sh",
    ),
    BotConfig(
        "trading",
        Path("trading_bot/bot.log"),
        "Trading bot started. Press Ctrl+C to stop.",
        "trading_bot",
        "trading_bot/run_tmux.sh",
    ),
]


def parse_rows(path: Path) -> List[Tuple[datetime, str]]:
    rows: List[Tuple[datetime, str]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = TS_RX.match(raw)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        rows.append((ts, m.group(2)))
    return rows


def anchor_rows(rows: List[Tuple[datetime, str]], marker: str) -> List[Tuple[datetime, str]]:
    if not rows:
        return rows
    idx = 0
    for i in range(len(rows) - 1, -1, -1):
        if marker in rows[i][1]:
            idx = i
            break
    return rows[idx:]


def score_bot(rows: List[Tuple[datetime, str]], target_return_pct: float) -> Dict[str, object]:
    buy = sum(1 for _, l in rows if "BUY signal for" in l)
    sell = sum(1 for _, l in rows if "SELL signal for" in l)
    hold = sum(1 for _, l in rows if re.search(r": HOLD\b", l))
    exec_err = sum(1 for _, l in rows if "Error executing trade" in l)
    auth_err = sum(1 for _, l in rows if AUTH_RX.search(l))
    insuff = sum(1 for _, l in rows if re.search(r"insufficient balance", l, re.IGNORECASE))

    ports = [
        float(m.group(1))
        for _, l in rows
        for m in [re.search(r"Portfolio snapshot:.*?(?:portfolio|value)=\$([0-9]+(?:\.[0-9]+)?)", l, re.IGNORECASE)]
        if m
    ]
    mode = [
        m.group(1)
        for _, l in rows
        for m in [re.search(r"Autonomy profile:.*mode=([^\s]+)", l, re.IGNORECASE)]
        if m
    ]

    first_portfolio = ports[0] if ports else None
    last_portfolio = ports[-1] if ports else None
    delta = (last_portfolio - first_portfolio) if (first_portfolio is not None and last_portfolio is not None) else 0.0
    roi_pct = ((delta / first_portfolio) * 100.0) if first_portfolio and first_portfolio > 0 else 0.0
    target_gap_pct = float(target_return_pct) - roi_pct

    activity = buy + sell
    reliability_errors = exec_err + auth_err + insuff
    reliable = reliability_errors == 0

    # Weighted score out of 100
    score = 0
    score += 35 if reliable else max(0, 35 - 12 * reliability_errors)
    score += min(25, activity * 8)
    score += 20 if delta > 0 else (10 if delta > -50 else 0)
    score += 20 if (mode[-1] if mode else "unknown") in ("normal", "aggressive") else 8

    verdict = "needs-improvement"
    if score >= 80:
        verdict = "excellent"
    elif score >= 65:
        verdict = "good"
    elif score >= 50:
        verdict = "stable"

    return {
        "score": score,
        "verdict": verdict,
        "mode": (mode[-1] if mode else None),
        "BUY": buy,
        "SELL": sell,
        "HOLD": hold,
        "exec_err": exec_err,
        "auth_err": auth_err,
        "insuff": insuff,
        "portfolio_first": round(first_portfolio, 2) if first_portfolio is not None else None,
        "portfolio_last": round(last_portfolio, 2) if last_portfolio is not None else None,
        "portfolio_delta": round(delta, 2),
        "roi_pct": round(roi_pct, 4),
        "target_gap_pct": round(target_gap_pct, 4),
    }


def load_history() -> List[dict]:
    if not HISTORY_PATH.exists():
        return []
    out: List[dict] = []
    for line in HISTORY_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_history(event: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")))
        f.write("\n")


def latest_for_bot(history: List[dict], bot: str, before_ts: str) -> dict | None:
    for item in reversed(history):
        if item.get("bot") == bot and item.get("ts") < before_ts:
            return item
    return None


def restart_bot(bot: BotConfig) -> bool:
    try:
        subprocess.run(["tmux", "kill-session", "-t", bot.tmux_session], check=False)
        subprocess.run(["bash", bot.run_script], check=True)
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Trend tracker for bot target progress.")
    parser.add_argument("--window-minutes", type=int, default=30, help="Window size for scoring")
    parser.add_argument("--target-return-pct", type=float, default=10.0, help="Target ROI percent")
    parser.add_argument("--autofix", action="store_true", help="Restart bot session on runtime error spikes")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    history = load_history()
    events: List[dict] = []

    print(f"Target trend run @ {ts}")
    print("=" * 72)

    for bot in BOTS:
        rows = parse_rows(bot.log_path)
        anchored = anchor_rows(rows, bot.startup_marker)
        if not anchored:
            print(f"{bot.name}: no timestamped rows found")
            continue

        end = anchored[-1][0]
        window_rows = [x for x in anchored if x[0] >= end - timedelta(minutes=max(1, args.window_minutes))]
        stats = score_bot(window_rows, target_return_pct=args.target_return_pct)

        actions: List[str] = []
        if args.autofix and (int(stats["exec_err"]) > 0 or int(stats["auth_err"]) > 0):
            restarted = restart_bot(bot)
            actions.append("restart_triggered" if restarted else "restart_failed")
        if int(stats["BUY"]) + int(stats["SELL"]) == 0:
            actions.append("low_activity")
        if float(stats["target_gap_pct"]) > 0:
            actions.append("below_target")

        event = {
            "ts": ts,
            "bot": bot.name,
            "window": f"last_{max(1, args.window_minutes)}m",
            "target_return_pct": float(args.target_return_pct),
            "actions": actions,
            **stats,
        }
        append_history(event)
        events.append(event)

        prev = latest_for_bot(history, bot.name, ts)
        delta_score = None if not prev else int(stats["score"]) - int(prev.get("score", 0))

        trend = "new"
        if delta_score is not None:
            if delta_score > 0:
                trend = f"up (+{delta_score})"
            elif delta_score < 0:
                trend = f"down ({delta_score})"
            else:
                trend = "flat (0)"

        print(
            f"{bot.name.upper()} score={stats['score']}/100 verdict={stats['verdict']} "
            f"mode={stats['mode']} trend={trend}"
        )
        print(
            f"  BUY={stats['BUY']} SELL={stats['SELL']} HOLD={stats['HOLD']} "
            f"errors(exec/auth/insuff)={stats['exec_err']}/{stats['auth_err']}/{stats['insuff']} "
            f"delta={stats['portfolio_delta']} roi={stats['roi_pct']}% gap_to_{args.target_return_pct:.1f}%={stats['target_gap_pct']}%"
        )
        if actions:
            print(f"  actions: {', '.join(actions)}")
        if int(stats["score"]) < 60:
            print("  target: raise score above 60 by improving activity or reducing drawdown drift.")
        elif int(stats["score"]) < 75:
            print("  target: maintain reliability and push selective high-quality entries.")
        else:
            print("  target: protect gains; avoid over-tuning.")
        print("-" * 72)

    if events:
        print(f"History written to {HISTORY_PATH}")


if __name__ == "__main__":
    main()
