"""
Paper trading broker for the ASX bot.

Simulates realistic order execution without a real broker connection:
  - Market orders filled at last known price ± configurable slippage
  - Flat brokerage fee per order (CommSec-style $9.95)
  - Persistent state saved to paper_state.json (survives restarts)
  - All trades appended to logs/trades_log.csv

To wire in a real broker (e.g. IBKR via ib_insync) later, implement the
same interface (buy / sell / get_positions / get_account_balance /
get_portfolio_value) in a separate class and swap it in main.py.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from config import (
    BROKERAGE_FLAT,
    PAPER_CAPITAL,
    PAPER_STATE_FILE,
    PAPER_TRADES_LOG,
    SLIPPAGE_PCT,
)
from data_fetcher import fetch_latest_price


class PaperBroker:
    """
    Fully in-memory paper broker with disk persistence.

    State schema (paper_state.json):
    {
      "cash": 100000.0,
      "positions": {
        "BHP.AX": {"qty": 50, "avg_cost": 45.20, "stop": 44.53, "target": 46.56}
      }
    }
    """

    def __init__(self):
        os.makedirs(os.path.dirname(PAPER_TRADES_LOG), exist_ok=True)
        self._state = self._load_state()
        print(
            f"[Broker] Paper broker ready  |  "
            f"Cash: ${self._state['cash']:,.2f}  |  "
            f"Positions: {list(self._state['positions'].keys()) or 'none'}"
        )

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_balance(self) -> float:
        """Return available cash."""
        return self._state["cash"]

    def get_portfolio_value(self) -> float:
        """Return cash + mark-to-market value of all open positions."""
        total = self._state["cash"]
        for symbol, pos in self._state["positions"].items():
            price = fetch_latest_price(symbol) or pos["avg_cost"]
            total += price * pos["qty"]
        return total

    def get_positions(self) -> Dict[str, dict]:
        """Return a copy of the positions dict."""
        return dict(self._state["positions"])

    # ── Order execution ───────────────────────────────────────────────────────

    def buy(
        self,
        symbol: str,
        qty: int,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Simulate a market buy order.
        Returns fill dict on success, None if insufficient funds.
        """
        live_price = fetch_latest_price(symbol)
        if live_price is None:
            print(f"  [Broker] Cannot buy {symbol}: no price data")
            return None

        fill_price = live_price * (1 + SLIPPAGE_PCT)
        cost       = fill_price * qty + BROKERAGE_FLAT

        if cost > self._state["cash"]:
            print(f"  [Broker] Insufficient cash for {symbol}: need ${cost:,.2f}, have ${self._state['cash']:,.2f}")
            return None

        self._state["cash"] -= cost

        pos = self._state["positions"].get(symbol, {"qty": 0, "avg_cost": 0.0})
        total_qty  = pos["qty"] + qty
        avg_cost   = (pos["avg_cost"] * pos["qty"] + fill_price * qty) / total_qty
        self._state["positions"][symbol] = {
            "qty":      total_qty,
            "avg_cost": avg_cost,
            "stop":     stop_price,
            "target":   target_price,
        }

        self._log_trade(symbol, "BUY", qty, fill_price, BROKERAGE_FLAT)
        self._save_state()

        return {"symbol": symbol, "qty": qty, "fill_price": fill_price, "cost": cost}

    def sell(self, symbol: str, qty: int) -> Optional[dict]:
        """
        Simulate a market sell order.
        Returns fill dict on success, None if no position or price unavailable.
        """
        pos = self._state["positions"].get(symbol)
        if pos is None or pos["qty"] < qty:
            print(f"  [Broker] Cannot sell {symbol}: insufficient position")
            return None

        live_price = fetch_latest_price(symbol)
        if live_price is None:
            print(f"  [Broker] Cannot sell {symbol}: no price data")
            return None

        fill_price = live_price * (1 - SLIPPAGE_PCT)
        proceeds   = fill_price * qty - BROKERAGE_FLAT
        pnl        = (fill_price - pos["avg_cost"]) * qty - BROKERAGE_FLAT

        self._state["cash"] += proceeds

        remaining = pos["qty"] - qty
        if remaining <= 0:
            del self._state["positions"][symbol]
        else:
            self._state["positions"][symbol]["qty"] = remaining

        self._log_trade(symbol, "SELL", qty, fill_price, BROKERAGE_FLAT, pnl=pnl)
        self._save_state()

        return {"symbol": symbol, "qty": qty, "fill_price": fill_price,
                "proceeds": proceeds, "pnl": pnl}

    def check_stop_take_profit(self) -> list[dict]:
        """
        Scan open positions against their stop-loss / take-profit levels.
        Executes automatic exits and returns list of triggered trades.
        """
        triggered = []
        for symbol, pos in list(self._state["positions"].items()):
            price = fetch_latest_price(symbol)
            if price is None:
                continue

            hit_stop   = pos.get("stop")   and price <= pos["stop"]
            hit_target = pos.get("target") and price >= pos["target"]

            if hit_stop or hit_target:
                reason = "STOP" if hit_stop else "TARGET"
                result = self.sell(symbol, pos["qty"])
                if result:
                    triggered.append({**result, "reason": reason})
                    print(
                        f"  [Broker] {reason} hit on {symbol} @ ${price:.3f}  "
                        f"P&L: ${result['pnl']:+.2f}"
                    )
        return triggered

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if os.path.exists(PAPER_STATE_FILE):
            try:
                with open(PAPER_STATE_FILE) as f:
                    state = json.load(f)
                # Validate keys
                if "cash" in state and "positions" in state:
                    return state
            except (json.JSONDecodeError, KeyError):
                pass
        return {"cash": PAPER_CAPITAL, "positions": {}}

    def _save_state(self) -> None:
        with open(PAPER_STATE_FILE, "w") as f:
            json.dump(self._state, f, indent=2)

    def _log_trade(
        self,
        symbol: str,
        action: str,
        qty: int,
        price: float,
        brokerage: float,
        pnl: Optional[float] = None,
    ) -> None:
        file_exists = os.path.exists(PAPER_TRADES_LOG)
        with open(PAPER_TRADES_LOG, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "symbol", "action", "qty", "price",
                                 "brokerage", "pnl", "portfolio_value"])
            pv = self.get_portfolio_value()
            writer.writerow([
                datetime.now(tz=timezone.utc).isoformat(),
                symbol, action, qty, f"{price:.4f}",
                f"{brokerage:.2f}",
                f"{pnl:.2f}" if pnl is not None else "",
                f"{pv:.2f}",
            ])
