"""Alpaca broker wrapper for forex (paper or live)."""
from __future__ import annotations

from typing import Optional
import json
import os

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from config import (
    ALPACA_API_KEY,
    ALPACA_API_SECRET,
    ALPACA_PAPER,
    FOREX_BROKER_MODE,
    FOREX_SIM_START_BALANCE,
    FOREX_SIM_STATE_FILE,
)
from data_fetcher import fetch_latest_price


class ForexBroker:
    def __init__(self):
        self._mode = FOREX_BROKER_MODE if FOREX_BROKER_MODE in {"alpaca", "sim"} else "alpaca"
        self._state_file = FOREX_SIM_STATE_FILE
        self._sim_state = {"cash": float(FOREX_SIM_START_BALANCE), "positions": {}}
        self._client = None

        if self._mode == "sim":
            self._load_sim_state()
            print(f"[Broker] Forex simulated execution enabled. cash=${self._sim_state['cash']:.2f}")
            return

        try:
            self._client = TradingClient(ALPACA_API_KEY, ALPACA_API_SECRET, paper=ALPACA_PAPER)
        except Exception as e:
            # Fall back to simulator so strategy can continue learning if broker is unavailable.
            print(f"[Broker] Alpaca init failed ({e}); switching to simulated execution.")
            self._mode = "sim"
            self._load_sim_state()

    @staticmethod
    def _to_order_symbol(pair: str) -> str:
        # Alpaca trading expects compact forex symbols (e.g. EURUSD).
        return str(pair or "").upper().replace("/", "")

    def _load_sim_state(self) -> None:
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._sim_state["cash"] = float(raw.get("cash", self._sim_state["cash"]))
                self._sim_state["positions"] = dict(raw.get("positions") or {})
        except Exception:
            return

    def _save_sim_state(self) -> None:
        if not self._state_file:
            return
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(self._sim_state, f, indent=2, sort_keys=True)
        except Exception:
            return

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_balance(self) -> float:
        if self._mode == "sim":
            return float(self._sim_state.get("cash", 0.0))
        return float(self._client.get_account().cash)

    def get_portfolio_value(self) -> float:
        if self._mode == "sim":
            total = float(self._sim_state.get("cash", 0.0))
            positions = self._sim_state.get("positions") or {}
            for symbol, pos in positions.items():
                units = float((pos or {}).get("units", 0.0) or 0.0)
                if units == 0:
                    continue
                pair = f"{symbol[:3]}/{symbol[3:]}" if len(symbol) == 6 else symbol
                px = fetch_latest_price(pair)
                if px is None:
                    px = float((pos or {}).get("avg_entry_price", 0.0) or 0.0)
                total += units * float(px)
            return float(total)
        try:
            return float(self._client.get_account().portfolio_value)
        except Exception:
            return self.get_account_balance()

    # ── Orders ───────────────────────────────────────────────────────────────

    def buy(self, pair: str, units: int) -> None:
        """Buy `units` of `pair` (e.g. 'EUR/USD')."""
        symbol = self._to_order_symbol(pair)
        if self._mode == "sim":
            price = fetch_latest_price(pair)
            if price is None:
                raise RuntimeError(f"No market price available for {pair}")
            price = float(price)
            qty = float(units)
            notional = qty * price
            cash = float(self._sim_state.get("cash", 0.0))
            if notional > cash:
                raise RuntimeError(
                    f"insufficient simulated cash for {pair} (need={notional:.2f}, cash={cash:.2f})"
                )
            positions = self._sim_state.setdefault("positions", {})
            current = positions.get(symbol) or {"units": 0.0, "avg_entry_price": 0.0}
            cur_units = float(current.get("units", 0.0) or 0.0)
            cur_avg = float(current.get("avg_entry_price", 0.0) or 0.0)
            new_units = cur_units + qty
            if new_units <= 0:
                new_avg = 0.0
            elif cur_units > 0:
                new_avg = ((cur_units * cur_avg) + (qty * price)) / new_units
            else:
                new_avg = price
            positions[symbol] = {"units": new_units, "avg_entry_price": new_avg}
            self._sim_state["cash"] = cash - notional
            self._save_sim_state()
            print(f"[Broker] SIM BUY  {int(qty)} {pair} ({symbol}) @ {price:.5f}")
            return

        self._client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=units,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.IOC,   # Immediate-or-cancel suits forex
        ))
        print(f"[Broker] BUY  {units} {pair} ({symbol})")

    def sell(self, pair: str, units: int) -> None:
        """Sell `units` of `pair`."""
        symbol = self._to_order_symbol(pair)
        if self._mode == "sim":
            price = fetch_latest_price(pair)
            if price is None:
                raise RuntimeError(f"No market price available for {pair}")
            price = float(price)
            qty = float(units)
            positions = self._sim_state.setdefault("positions", {})
            current = positions.get(symbol) or {"units": 0.0, "avg_entry_price": 0.0}
            cur_units = float(current.get("units", 0.0) or 0.0)
            cur_avg = float(current.get("avg_entry_price", 0.0) or 0.0)
            new_units = cur_units - qty
            # credit proceeds
            self._sim_state["cash"] = float(self._sim_state.get("cash", 0.0)) + (qty * price)
            if abs(new_units) < 1e-9:
                positions.pop(symbol, None)
            else:
                positions[symbol] = {"units": new_units, "avg_entry_price": cur_avg if cur_avg > 0 else price}
            self._save_sim_state()
            print(f"[Broker] SIM SELL {int(qty)} {pair} ({symbol}) @ {price:.5f}")
            return

        self._client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=units,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.IOC,
        ))
        print(f"[Broker] SELL {units} {pair} ({symbol})")

    # ── Positions ────────────────────────────────────────────────────────────

    def get_position(self, pair: str) -> Optional[dict]:
        symbol = self._to_order_symbol(pair)
        if self._mode == "sim":
            pos = (self._sim_state.get("positions") or {}).get(symbol)
            if not pos:
                return None
            units = float((pos or {}).get("units", 0.0) or 0.0)
            if abs(units) < 1e-9:
                return None
            px = fetch_latest_price(pair)
            if px is None:
                px = float((pos or {}).get("avg_entry_price", 0.0) or 0.0)
            return {
                "pair": pair,
                "units": units,
                "entry_price": float((pos or {}).get("avg_entry_price", 0.0) or 0.0),
                "market_value": units * float(px),
            }

        try:
            for pos in self._client.get_all_positions():
                if str(pos.symbol).upper() == symbol:
                    return {
                        "pair":        pair,
                        "units":       float(pos.qty),
                        "entry_price": float(getattr(pos, "avg_entry_price", 0) or 0),
                        "market_value": float(getattr(pos, "market_value", 0) or 0),
                    }
        except Exception:
            pass
        return None

    def get_open_positions_count(self) -> int:
        if self._mode == "sim":
            positions = self._sim_state.get("positions") or {}
            return sum(1 for _, p in positions.items() if abs(float((p or {}).get("units", 0.0) or 0.0)) > 1e-9)
        try:
            return len(self._client.get_all_positions())
        except Exception:
            return 0

    def close_position(self, pair: str) -> None:
        """Close the entire position for a pair."""
        symbol = self._to_order_symbol(pair)
        if self._mode == "sim":
            pos = self.get_position(pair)
            if not pos:
                return
            units = float(pos.get("units", 0.0) or 0.0)
            if units > 0:
                self.sell(pair, int(units))
            elif units < 0:
                self.buy(pair, int(abs(units)))
            return

        try:
            self._client.close_position(symbol)
            print(f"[Broker] Closed position for {pair}")
        except Exception as e:
            print(f"[Broker] Failed to close {pair}: {e}")
