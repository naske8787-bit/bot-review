"""Interactive Brokers broker adapter for the ASX bot.

This class mirrors the `PaperBroker` interface used by strategy/main:
  - get_account_balance
  - get_portfolio_value
  - get_positions
  - buy
  - sell
  - check_stop_take_profit

Design notes:
- Uses IBKR market orders for entry/exit.
- Tracks stop/target levels locally and triggers market exits when breached.
- Accepts symbols from strategy in yfinance format (e.g. "BHP.AX") and
  maps them to IB contracts (e.g. Stock("BHP", "ASX", "AUD")).
"""
from __future__ import annotations

from typing import Dict, Optional

from config import (
    IBKR_ACCOUNT,
    IBKR_CLIENT_ID,
    IBKR_CURRENCY,
    IBKR_EXCHANGE,
    IBKR_HOST,
    IBKR_PORT,
)
from data_fetcher import fetch_latest_price


class IBKRBroker:
    def __init__(self):
        from ib_insync import IB

        self._ib = IB()
        self._ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)

        self._account = IBKR_ACCOUNT or (
            self._ib.managedAccounts()[0] if self._ib.managedAccounts() else ""
        )
        if not self._account:
            raise RuntimeError("No IBKR account found. Check TWS/Gateway login and API settings.")

        # Local risk map used by check_stop_take_profit()
        # {"BHP.AX": {"stop": 44.5, "target": 46.2}}
        self._risk_levels: Dict[str, dict] = {}

        print(
            f"[IBKR] Connected to {IBKR_HOST}:{IBKR_PORT} | "
            f"Account: {self._account} | Exchange: {IBKR_EXCHANGE}"
        )

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_balance(self) -> float:
        values = self._ib.accountValues(self._account)
        for v in values:
            if v.tag == "AvailableFunds" and v.currency == IBKR_CURRENCY:
                return float(v.value)
        for v in values:
            if v.tag == "CashBalance" and v.currency == IBKR_CURRENCY:
                return float(v.value)
        return 0.0

    def get_portfolio_value(self) -> float:
        values = self._ib.accountValues(self._account)
        for v in values:
            if v.tag == "NetLiquidation" and v.currency == IBKR_CURRENCY:
                return float(v.value)

        # Fallback if NetLiquidation is unavailable
        total = self.get_account_balance()
        for sym, pos in self.get_positions().items():
            px = fetch_latest_price(sym) or pos["avg_cost"]
            total += px * pos["qty"]
        return total

    def get_positions(self) -> Dict[str, dict]:
        """Return current long ASX positions in strategy symbol format (e.g. BHP.AX)."""
        result: Dict[str, dict] = {}
        for p in self._ib.positions(self._account):
            c = p.contract
            if getattr(c, "secType", "") != "STK":
                continue
            if getattr(c, "exchange", "") not in {IBKR_EXCHANGE, "SMART"}:
                continue

            qty = int(p.position)
            if qty == 0:
                continue

            yf_sym = self._to_strategy_symbol(c.symbol)
            result[yf_sym] = {
                "qty": qty,
                "avg_cost": float(p.avgCost),
                "stop": self._risk_levels.get(yf_sym, {}).get("stop"),
                "target": self._risk_levels.get(yf_sym, {}).get("target"),
            }
        return result

    # ── Orders ────────────────────────────────────────────────────────────────

    def buy(
        self,
        symbol: str,
        qty: int,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> Optional[dict]:
        from ib_insync import MarketOrder

        if qty <= 0:
            return None

        contract = self._contract_for(symbol)
        order = MarketOrder("BUY", qty)
        trade = self._ib.placeOrder(contract, order)

        self._ib.sleep(1.5)
        fill_price = float(trade.orderStatus.avgFillPrice or 0.0)
        if fill_price <= 0:
            fill_price = fetch_latest_price(symbol) or 0.0

        if fill_price <= 0:
            return None

        self._risk_levels[symbol] = {"stop": stop_price, "target": target_price}
        return {"symbol": symbol, "qty": qty, "fill_price": fill_price}

    def sell(self, symbol: str, qty: int) -> Optional[dict]:
        from ib_insync import MarketOrder

        if qty <= 0:
            return None

        contract = self._contract_for(symbol)
        order = MarketOrder("SELL", qty)
        trade = self._ib.placeOrder(contract, order)

        self._ib.sleep(1.5)
        fill_price = float(trade.orderStatus.avgFillPrice or 0.0)
        if fill_price <= 0:
            fill_price = fetch_latest_price(symbol) or 0.0

        if fill_price <= 0:
            return None

        self._risk_levels.pop(symbol, None)
        return {"symbol": symbol, "qty": qty, "fill_price": fill_price, "pnl": 0.0}

    def check_stop_take_profit(self) -> list[dict]:
        """Local stop/target checker for IBKR positions."""
        triggered = []
        positions = self.get_positions()

        for symbol, pos in positions.items():
            risk = self._risk_levels.get(symbol)
            if not risk:
                continue

            price = fetch_latest_price(symbol)
            if price is None:
                continue

            stop = risk.get("stop")
            target = risk.get("target")
            hit_stop = stop is not None and price <= stop
            hit_target = target is not None and price >= target

            if hit_stop or hit_target:
                reason = "STOP" if hit_stop else "TARGET"
                result = self.sell(symbol, pos["qty"])
                if result:
                    result["reason"] = reason
                    result["pnl"] = (result["fill_price"] - pos["avg_cost"]) * pos["qty"]
                    triggered.append(result)

        return triggered

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _to_strategy_symbol(ib_symbol: str) -> str:
        return f"{ib_symbol.upper()}.AX"

    @staticmethod
    def _to_ib_symbol(strategy_symbol: str) -> str:
        s = strategy_symbol.upper()
        if s.endswith(".AX"):
            return s[:-3]
        return s

    def _contract_for(self, strategy_symbol: str):
        from ib_insync import Stock

        ib_symbol = self._to_ib_symbol(strategy_symbol)
        return Stock(ib_symbol, IBKR_EXCHANGE, IBKR_CURRENCY)
