"""Broker abstraction for Interactive Brokers (IBKR) via ib_insync.

Connects to a locally running TWS (Trader Workstation) or IB Gateway.
In PAPER_TRADING mode, connects to the paper trading port (7497) — no
real money is at risk.

Prerequisites:
  - Download TWS or IB Gateway from interactivebrokers.com.au
  - Log in and enable API connections:
      TWS: Edit > Global Configuration > API > Settings
        - Enable "Enable ActiveX and Socket Clients"
        - Set port to 7497 (paper) or 7496 (live)
        - Add 127.0.0.1 to trusted IPs
"""

from ib_insync import IB, MarketOrder, Stock

from config import (
    DEFAULT_EXCHANGE,
    IBKR_ACCOUNT,
    IBKR_CLIENT_ID,
    IBKR_CURRENCY,
    IBKR_HOST,
    IBKR_PORT,
    PAPER_TRADING,
    RISK_PER_TRADE,
)
from data_fetcher import fetch_realtime_price


class Broker:
    def __init__(self):
        self.paper = PAPER_TRADING
        self._ib = IB()
        self._ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
        self._account = IBKR_ACCOUNT or (
            self._ib.managedAccounts()[0] if self._ib.managedAccounts() else ""
        )
        mode = "PAPER" if self.paper else "LIVE"
        print(f"[Broker] Connected to IBKR ({IBKR_HOST}:{IBKR_PORT}) | Account: {self._account} | Mode: {mode}")

    def _contract(self, symbol: str) -> Stock:
        return Stock(symbol.upper(), DEFAULT_EXCHANGE, IBKR_CURRENCY)

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account_balance(self) -> float:
        for v in self._ib.accountValues(self._account):
            if v.tag == "AvailableFunds" and v.currency == IBKR_CURRENCY:
                return float(v.value)
        for v in self._ib.accountValues(self._account):
            if v.tag == "CashBalance" and v.currency == IBKR_CURRENCY:
                return float(v.value)
        return 0.0

    def get_portfolio_value(self) -> float:
        for v in self._ib.accountValues(self._account):
            if v.tag == "NetLiquidation" and v.currency == IBKR_CURRENCY:
                return float(v.value)
        return self.get_account_balance()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def get_current_price(self, symbol: str) -> float:
        price = fetch_realtime_price(symbol, ib=self._ib)
        if price is None:
            raise RuntimeError(f"Could not fetch price for {symbol}")
        return price

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def buy(self, symbol: str, qty: int):
        price = self.get_current_price(symbol)
        contract = self._contract(symbol)
        self._ib.qualifyContracts(contract)
        trade = self._ib.placeOrder(contract, MarketOrder("BUY", qty))
        self._ib.sleep(1)
        print(f"[{'Paper' if self.paper else 'Live'}] BUY {qty} x {symbol} @ ₹{price:.2f} | status={trade.orderStatus.status}")

    def sell(self, symbol: str, qty: int):
        price = self.get_current_price(symbol)
        contract = self._contract(symbol)
        self._ib.qualifyContracts(contract)
        trade = self._ib.placeOrder(contract, MarketOrder("SELL", qty))
        self._ib.sleep(1)
        print(f"[{'Paper' if self.paper else 'Live'}] SELL {qty} x {symbol} @ ₹{price:.2f} | status={trade.orderStatus.status}")

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_position(self, symbol: str) -> dict | None:
        symbol = symbol.upper()
        if self.paper:
            pos = self._positions.get(symbol)
            if pos and pos["qty"] > 0:
                price = self.get_current_price(symbol)
                return {
                    "symbol": symbol,
                    "qty": float(pos["qty"]),
                    "entry_price": float(pos["entry_price"]),
                    "market_value": float(pos["qty"]) * price,
                }
            return None

        for pos in self._ib.positions(self._account):
            if pos.contract.symbol.upper() == symbol and pos.position > 0:
                price = self.get_current_price(symbol)
                return {
                    "symbol": symbol,
                    "qty": float(pos.position),
                    "entry_price": float(pos.avgCost),
                    "market_value": float(pos.position) * price,
                }
        return None

    def get_position_size(self, symbol: str) -> float:
        pos = self.get_position(symbol)
        return float(pos["qty"]) if pos else 0.0

    def get_open_positions_count(self) -> int:
        return len([p for p in self._ib.positions(self._account) if p.position > 0])

    # ------------------------------------------------------------------
    # Sizing helper
    # ------------------------------------------------------------------

    def calculate_qty(self, symbol: str) -> int:
        price = self.get_current_price(symbol)
        cash = self.get_account_balance()
        risk_amount = cash * RISK_PER_TRADE
        qty = int(risk_amount / price)
        return max(qty, 1)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def disconnect(self):
        self._ib.disconnect()
        print("[Broker] Disconnected from IBKR.")

    # ------------------------------------------------------------------
    # Sizing helper
    # ------------------------------------------------------------------

    def calculate_qty(self, symbol: str) -> int:
        price = self.get_current_price(symbol)
        cash = self.get_account_balance()
        risk_amount = cash * RISK_PER_TRADE
        qty = int(risk_amount / price)
        return max(qty, 1)
