"""ASX bot performance dashboard.

Shows paper-trading performance so strategy quality can be validated before
switching execution to IBKR.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from config import (
    ALLOW_BROKER_FALLBACK,
    BROKER_MODE,
    EVENT_BOOTSTRAP_ENABLED,
    EVENT_BOOTSTRAP_YEARS,
    INITIAL_TRAIN_BARS,
    IBKR_CLIENT_ID,
    IBKR_HOST,
    IBKR_PORT,
    LOOKBACK_BARS,
    MAX_POSITIONS,
    PAPER_CAPITAL,
    PAPER_STATE_FILE,
    PAPER_TRADES_LOG,
    RETRAIN_EVERY_N_BARS,
    RISK_PER_TRADE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRADE_COOLDOWN_SECS,
)
from data_fetcher import fetch_latest_price

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "dashboard_static")
REPO_ROOT = os.path.dirname(HERE)

app = Flask(__name__, static_folder=STATIC_DIR)

_BROKER_STATUS_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "value": None,
}

_EVENT_LEARNER_STATE_PATH = os.path.join(HERE, "models", "event_impact_state.json")


def _strategy_snapshot() -> dict[str, Any]:
    """Return a compact strategy/risk snapshot for chat responses."""
    return {
        "model": "LSTM directional forecast + technical confirmation",
        "signals": [
            "Predicted next-close directional edge",
            "VWAP position (value entry zone)",
            "EMA trend alignment",
            "RSI guard rails",
            "Volume confirmation",
            "Bollinger-band extremes filter",
        ],
        "risk": {
            "risk_per_trade": RISK_PER_TRADE,
            "stop_loss_pct": STOP_LOSS_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "max_positions": MAX_POSITIONS,
            "trade_cooldown_secs": TRADE_COOLDOWN_SECS,
        },
        "training": {
            "lookback_bars": LOOKBACK_BARS,
            "initial_train_bars": INITIAL_TRAIN_BARS,
            "retrain_every_n_bars": RETRAIN_EVERY_N_BARS,
            "event_bootstrap_enabled": EVENT_BOOTSTRAP_ENABLED,
            "event_bootstrap_years": EVENT_BOOTSTRAP_YEARS,
        },
    }


def _top_event_learnings(limit: int = 5) -> list[dict[str, Any]]:
    """Load strongest learned topic impacts from persisted event learner state."""
    if not os.path.exists(_EVENT_LEARNER_STATE_PATH):
        return []

    try:
        with open(_EVENT_LEARNER_STATE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    impacts = payload.get("global_topic_impacts") or {}
    if not isinstance(impacts, dict):
        return []

    ranked = sorted(
        [(str(topic), float(score)) for topic, score in impacts.items()],
        key=lambda x: abs(x[1]),
        reverse=True,
    )

    out = []
    for topic, score in ranked[:limit]:
        out.append(
            {
                "topic": topic,
                "score": score,
                "direction": "positive" if score >= 0 else "negative",
            }
        )
    return out


def _recent_trade_patterns(trades: list[dict], limit: int = 30) -> dict[str, Any]:
    sample = list(trades[-limit:]) if trades else []
    if not sample:
        return {
            "sample_size": 0,
            "buys": 0,
            "sells": 0,
            "realized_pnl": 0.0,
            "top_symbols": [],
        }

    buys = [t for t in sample if str(t.get("action", "")).upper() == "BUY"]
    sells = [t for t in sample if str(t.get("action", "")).upper() == "SELL"]

    symbol_counts: dict[str, int] = {}
    for t in sample:
        sym = str(t.get("symbol", "")).upper().strip()
        if not sym:
            continue
        symbol_counts[sym] = symbol_counts.get(sym, 0) + 1

    top_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    realized_pnl = sum(float(t.get("pnl", 0.0) or 0.0) for t in sells)

    return {
        "sample_size": len(sample),
        "buys": len(buys),
        "sells": len(sells),
        "realized_pnl": realized_pnl,
        "top_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
    }


def _copilot_answer(question: str, overview_data: dict, trades: list[dict]) -> str:
    q = (question or "").strip().lower()
    strategy = _strategy_snapshot()
    learning = _top_event_learnings()
    patterns = _recent_trade_patterns(trades)

    equity = float(overview_data.get("current_equity") or 0.0)
    pnl = float(overview_data.get("total_pnl") or 0.0)
    pnl_pct = float(overview_data.get("total_pnl_pct") or 0.0)
    win_rate = float(overview_data.get("win_rate") or 0.0)
    profit_factor = float(overview_data.get("profit_factor") or 0.0)
    open_positions = int(overview_data.get("open_positions") or 0)
    closed_trades = int(overview_data.get("closed_trades") or 0)

    if any(k in q for k in ["learn", "learned", "learning", "adapt", "improv", "improve"]):
        lines = [
            "Here is what the bot has learned and how it is adapting:",
            f"- Event-impact learner tracks topic/market relationships over time (bootstrap={strategy['training']['event_bootstrap_enabled']}, years={strategy['training']['event_bootstrap_years']}).",
        ]
        if learning:
            lines.append("- Strongest learned topic impacts right now:")
            for item in learning:
                lines.append(
                    f"  • {item['topic']}: {item['direction']} bias ({item['score']:+.4f})"
                )
        else:
            lines.append("- No persisted event-impact history found yet, so learning is still in warm-up.")

        lines.append(
            f"- Recent execution sample: {patterns['sample_size']} trades ({patterns['buys']} buys / {patterns['sells']} sells), realized PnL from closes ${patterns['realized_pnl']:.2f}."
        )
        return "\n".join(lines)

    if any(k in q for k in ["strategy", "signal", "edge", "why buy", "why sell", "rules"]):
        lines = [
            "Current strategy framework:",
            f"- Core model: {strategy['model']}.",
            "- Entry/exit signals:",
        ]
        for s in strategy["signals"]:
            lines.append(f"  • {s}")
        lines.extend(
            [
                "- Risk controls:",
                f"  • Risk per trade: {strategy['risk']['risk_per_trade']*100:.2f}% of equity",
                f"  • Stop loss: {strategy['risk']['stop_loss_pct']*100:.2f}% | Take profit: {strategy['risk']['take_profit_pct']*100:.2f}%",
                f"  • Max positions: {strategy['risk']['max_positions']} | Cooldown: {strategy['risk']['trade_cooldown_secs']}s",
                f"- Training cadence: lookback {strategy['training']['lookback_bars']} bars, initial train {strategy['training']['initial_train_bars']} bars, retrain every {strategy['training']['retrain_every_n_bars']} bars.",
            ]
        )
        return "\n".join(lines)

    if any(k in q for k in ["return", "performance", "pnl", "profit", "results"]):
        lines = [
            "Latest performance snapshot:",
            f"- Equity: ${equity:,.2f}",
            f"- Total PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
            f"- Win rate: {win_rate:.2f}% | Profit factor: {profit_factor:.2f}",
            f"- Closed trades: {closed_trades} | Open positions: {open_positions}",
        ]
        if patterns["top_symbols"]:
            tops = ", ".join([f"{x['symbol']} ({x['count']})" for x in patterns["top_symbols"]])
            lines.append(f"- Most active symbols in recent sample: {tops}")
        lines.append("- To improve returns, the bot currently relies on strict risk sizing + adaptive event-impact bias updates.")
        return "\n".join(lines)

    return (
        "I can help with strategy, learning, and returns. Try asking:\n"
        "- What has the bot learned recently?\n"
        "- What strategy signals is it using?\n"
        "- How is performance and what is improving returns?"
    )


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_state() -> dict:
    if os.path.exists(PAPER_STATE_FILE):
        try:
            with open(PAPER_STATE_FILE) as f:
                state = json.load(f)
            if "cash" in state and "positions" in state:
                return state
        except (json.JSONDecodeError, OSError):
            pass
    return {"cash": PAPER_CAPITAL, "positions": {}}


def _load_trades() -> list[dict]:
    if not os.path.exists(PAPER_TRADES_LOG):
        return []

    rows = []
    try:
        with open(PAPER_TRADES_LOG, newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(
                    {
                        "timestamp": r.get("timestamp", ""),
                        "symbol": r.get("symbol", ""),
                        "action": r.get("action", ""),
                        "qty": int(_parse_float(r.get("qty"), 0)),
                        "price": _parse_float(r.get("price"), 0.0),
                        "brokerage": _parse_float(r.get("brokerage"), 0.0),
                        "pnl": _parse_float(r.get("pnl"), 0.0),
                        "portfolio_value": _parse_float(r.get("portfolio_value"), 0.0),
                    }
                )
    except OSError:
        return []

    rows.sort(key=lambda x: x["timestamp"])
    return rows


def _compute_positions_snapshot(state: dict) -> list[dict]:
    out = []
    for symbol, pos in state.get("positions", {}).items():
        qty = int(pos.get("qty", 0))
        avg_cost = _parse_float(pos.get("avg_cost"), 0.0)
        live_price = fetch_latest_price(symbol) or avg_cost
        market_value = live_price * qty
        cost_basis = avg_cost * qty
        unrealized_pnl = market_value - cost_basis

        out.append(
            {
                "symbol": symbol,
                "qty": qty,
                "avg_cost": avg_cost,
                "live_price": live_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "stop": pos.get("stop"),
                "target": pos.get("target"),
            }
        )

    out.sort(key=lambda x: x["symbol"])
    return out


def _build_equity_curve(trades: list[dict], current_equity: float) -> list[dict]:
    curve = [
        {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "equity": PAPER_CAPITAL,
            "label": "start",
        }
    ]

    for t in trades:
        pv = t.get("portfolio_value", 0.0)
        if pv > 0:
            curve.append(
                {
                    "timestamp": t["timestamp"],
                    "equity": pv,
                    "label": t["action"],
                }
            )

    if not trades:
        curve.append(
            {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "equity": current_equity,
                "label": "now",
            }
        )
    else:
        curve.append(
            {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "equity": current_equity,
                "label": "now",
            }
        )

    return curve


def _compute_metrics(state: dict, trades: list[dict]) -> dict:
    positions = _compute_positions_snapshot(state)
    cash = _parse_float(state.get("cash"), PAPER_CAPITAL)
    market_value = sum(p["market_value"] for p in positions)
    current_equity = cash + market_value

    total_fees = sum(t["brokerage"] for t in trades)
    realized_pnl = sum(t["pnl"] for t in trades if t["action"].upper() == "SELL")
    total_pnl = current_equity - PAPER_CAPITAL

    closed = [t for t in trades if t["action"].upper() == "SELL"]
    wins = len([t for t in closed if t["pnl"] > 0])
    losses = len([t for t in closed if t["pnl"] < 0])
    win_rate = (wins / len(closed) * 100.0) if closed else 0.0

    gross_profit = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in closed if t["pnl"] < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    return {
        "initial_capital": PAPER_CAPITAL,
        "cash": cash,
        "market_value": market_value,
        "current_equity": current_equity,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / PAPER_CAPITAL * 100.0) if PAPER_CAPITAL else 0.0,
        "realized_pnl": realized_pnl,
        "total_fees": total_fees,
        "open_positions": len(positions),
        "closed_trades": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "positions": positions,
        "equity_curve": _build_equity_curve(trades, current_equity),
    }


def _probe_ibkr_connection() -> dict:
    """Best-effort IBKR connectivity probe used by the dashboard status panel."""
    try:
        from ib_insync import IB
    except Exception as e:
        return {
            "connected": False,
            "error": f"ib_insync unavailable: {e}",
            "accounts": [],
        }

    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID + 99, timeout=2, readonly=True)
        accounts = ib.managedAccounts() if ib.isConnected() else []
        return {
            "connected": bool(ib.isConnected()),
            "error": "",
            "accounts": accounts,
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "accounts": [],
        }
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass


def _get_broker_status(force: bool = False) -> dict:
    """Return broker status with a short cache to avoid probing IBKR on every refresh."""
    now = time.time()
    cache_age = now - float(_BROKER_STATUS_CACHE.get("ts") or 0.0)
    if not force and _BROKER_STATUS_CACHE.get("value") is not None and cache_age < 15:
        return _BROKER_STATUS_CACHE["value"]

    status = {
        "configured_mode": BROKER_MODE,
        "fallback_allowed": ALLOW_BROKER_FALLBACK,
        "ibkr": {
            "host": IBKR_HOST,
            "port": IBKR_PORT,
            "client_id": IBKR_CLIENT_ID,
            "connected": False,
            "error": "",
            "accounts": [],
        },
    }

    if BROKER_MODE == "ibkr":
        status["ibkr"] = {
            **status["ibkr"],
            **_probe_ibkr_connection(),
        }

    active_mode = "ibkr" if (BROKER_MODE == "ibkr" and status["ibkr"].get("connected")) else "paper"
    status["active_mode"] = active_mode

    _BROKER_STATUS_CACHE["ts"] = now
    _BROKER_STATUS_CACHE["value"] = status
    return status


def _resolve_gantt_path() -> str | None:
    candidates = [
        os.path.join(REPO_ROOT, "motor_gantt.html"),
        os.path.join(REPO_ROOT, "data_bot", "static", "JM6451_three_motors_gantt.html"),
        os.path.join(REPO_ROOT, "data_bot", "static", "JM6451_motor_gantt.html"),
        os.path.join(REPO_ROOT, "data_bot", "static", "gantt_dashboard.html"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/motor-gantt")
@app.route("/gantt")
def motor_gantt():
    gantt_path = _resolve_gantt_path()
    if not gantt_path:
        return jsonify(
            {
                "ok": False,
                "error": "No Gantt HTML file found. Generate one first (for example: motor_gantt.html).",
            }
        ), 404
    return send_from_directory(os.path.dirname(gantt_path), os.path.basename(gantt_path))


@app.route("/motor-gantt/download")
def motor_gantt_download():
    gantt_path = _resolve_gantt_path()
    if not gantt_path:
        return jsonify(
            {
                "ok": False,
                "error": "No Gantt HTML file found. Generate one first (for example: motor_gantt.html).",
            }
        ), 404
    return send_from_directory(
        os.path.dirname(gantt_path),
        os.path.basename(gantt_path),
        as_attachment=True,
        download_name="motor_gantt_offline.html",
    )


@app.route("/api/overview")
def overview():
    state = _load_state()
    trades = _load_trades()
    data = _compute_metrics(state, trades)
    status = _get_broker_status()
    data["configured_broker_mode"] = status["configured_mode"]
    data["active_broker_mode"] = status["active_mode"]
    data["data_source"] = "paper_state_and_trade_log"
    data["last_updated_utc"] = datetime.now(tz=timezone.utc).isoformat()
    return jsonify(data)


@app.route("/api/trades")
def trades():
    rows = _load_trades()
    rows.reverse()
    return jsonify(rows[:200])


@app.route("/api/broker-status")
def broker_status():
    force = os.getenv("ASX_DASHBOARD_FORCE_STATUS", "false").lower() == "true"
    status = _get_broker_status(force=force)
    status["checked_at_utc"] = datetime.now(tz=timezone.utc).isoformat()
    return jsonify(status)


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/copilot-chat", methods=["POST"])
def copilot_chat():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("message", "")).strip()
    if not question:
        return jsonify({"error": "message is required"}), 400

    state = _load_state()
    trades = _load_trades()
    overview_data = _compute_metrics(state, trades)

    answer = _copilot_answer(question, overview_data, trades)

    return jsonify(
        {
            "answer": answer,
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
            "meta": {
                "closed_trades": int(overview_data.get("closed_trades") or 0),
                "open_positions": int(overview_data.get("open_positions") or 0),
                "win_rate": float(overview_data.get("win_rate") or 0.0),
            },
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("ASX_DASHBOARD_PORT", "5052"))
    host = os.getenv("ASX_DASHBOARD_HOST", "0.0.0.0")
    print(f"[ASX Dashboard] Serving on http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
