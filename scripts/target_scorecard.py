#!/usr/bin/env python3
"""Human-style target scorecard for trading and crypto bots.

Usage:
  python3 scripts/target_scorecard.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple


TS_RX = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s?(.*)$")
AUTH_RX = re.compile(
    r"unauthorized|forbidden|\b(?:http\s*)?(?:401|403)\b|status(?:\s*code)?\s*[:=]\s*(?:401|403)",
    re.IGNORECASE,
)


@dataclass
class BotConfig:
    name: str
    log_path: Path
    startup_marker: str


BOTS = [
    BotConfig("crypto", Path("crypto_bot/bot.log"), "Crypto bot started in paper-trading mode"),
    BotConfig("trading", Path("trading_bot/bot.log"), "Trading bot started. Press Ctrl+C to stop."),
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


def score_bot(rows: List[Tuple[datetime, str]]) -> dict:
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

    delta = (ports[-1] - ports[0]) if len(ports) >= 2 else 0.0
    activity = buy + sell
    reliability_errors = exec_err + auth_err + insuff
    reliable = reliability_errors == 0

    # Weighted score out of 100
    score = 0
    score += 35 if reliable else max(0, 35 - 12 * reliability_errors)
    score += min(25, activity * 8)
    score += 20 if delta > 0 else (10 if delta > -50 else 0)
    score += 20 if (mode[-1] if mode else "unknown") in ("normal", "aggressive") else 8

    if score >= 80:
        verdict = "excellent"
    elif score >= 65:
        verdict = "good"
    elif score >= 50:
        verdict = "stable"
    else:
        verdict = "needs-improvement"

    return {
        "BUY": buy,
        "SELL": sell,
        "HOLD": hold,
        "exec_err": exec_err,
        "auth_err": auth_err,
        "insuff": insuff,
        "portfolio_delta": round(delta, 2),
        "mode": (mode[-1] if mode else None),
        "score": score,
        "verdict": verdict,
    }


def anchor_rows(rows: List[Tuple[datetime, str]], startup_marker: str) -> List[Tuple[datetime, str]]:
    if not rows:
        return rows
    idx = 0
    for i in range(len(rows) - 1, -1, -1):
        if startup_marker in rows[i][1]:
            idx = i
            break
    return rows[idx:]


def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"Target scorecard run @ {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print("=" * 72)

    for bot in BOTS:
        rows = parse_rows(bot.log_path)
        anchored = anchor_rows(rows, bot.startup_marker)
        if not anchored:
            print(f"{bot.name}: no timestamped rows found")
            continue

        end = anchored[-1][0]
        last_30m = [x for x in anchored if x[0] >= end - timedelta(minutes=30)]
        stats = score_bot(last_30m)

        minutes = round((anchored[-1][0] - anchored[0][0]).total_seconds() / 60.0, 2)
        print(f"{bot.name.upper()} | anchor_minutes={minutes} | window=last_30m")
        print(
            f"  score={stats['score']}/100 verdict={stats['verdict']} mode={stats['mode']} "
            f"delta={stats['portfolio_delta']}"
        )
        print(
            f"  signals BUY={stats['BUY']} SELL={stats['SELL']} HOLD={stats['HOLD']} | "
            f"errors exec={stats['exec_err']} auth={stats['auth_err']} insuff={stats['insuff']}"
        )
        if stats["verdict"] in ("stable", "needs-improvement"):
            print("  coaching: keep risk tight; tune one gate at a time and re-measure.")
        elif stats["verdict"] == "good":
            print("  coaching: momentum is healthy; keep changes minimal and monitor drift.")
        else:
            print("  coaching: strong execution quality; protect gains and avoid over-tuning.")
        print("-" * 72)


if __name__ == "__main__":
    main()
