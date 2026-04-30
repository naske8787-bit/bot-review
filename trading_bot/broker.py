from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from config import (
    ALPACA_API_KEY, ALPACA_API_SECRET,
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_ENABLED,
)
from data_fetcher import fetch_realtime_price


_OPEN_BUY_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "partially_filled",
    "held",
}


# ── Alpaca broker (US markets) ──────────────────────────────────────────────

class AlpacaBroker:
    def __init__(self):
        self.api = TradingClient(ALPACA_API_KEY, ALPACA_API_SECRET, paper=True)

    @staticmethod
    def _is_open_buy_order(order, symbol):
        return (
            str(getattr(order, "symbol", "")).upper() == symbol
            and str(getattr(order, "side", "")).lower() == "buy"
            and str(getattr(order, "status", "")).lower() in _OPEN_BUY_ORDER_STATUSES
        )

    def get_account_balance(self):
        """Return buying_power — the amount Alpaca will actually allow us to use.

        Using `cash` was wrong: cash can go negative due to unsettled trades or
        accumulated paper losses while buying_power is what Alpaca actually gates
        order submission on.  A negative cash balance with zero buying_power means
        no new orders will be accepted regardless of what we pass.
        """
        try:
            account = self.api.get_account()
            bp = float(getattr(account, "buying_power", 0.0) or 0.0)
            return bp
        except Exception as e:
            print(f"[Alpaca] Error fetching account balance: {e}")
            return 0.0

    def get_portfolio_value(self):
        try:
            account = self.api.get_account()
            return float(getattr(account, "portfolio_value", account.cash))
        except Exception as e:
            print(f"[Alpaca] Error fetching portfolio value: {e}")
            return 0.0

    def get_account_details(self):
        """Return a dict with cash, buying_power, equity and status for diagnostics."""
        try:
            a = self.api.get_account()
            return {
                "cash": float(getattr(a, "cash", 0.0) or 0.0),
                "buying_power": float(getattr(a, "buying_power", 0.0) or 0.0),
                "equity": float(getattr(a, "equity", 0.0) or 0.0),
                "portfolio_value": float(getattr(a, "portfolio_value", 0.0) or 0.0),
                "status": str(getattr(a, "status", "unknown")),
            }
        except Exception as e:
            print(f"[Alpaca] Error fetching account details: {e}")
            return {"cash": 0.0, "buying_power": 0.0, "equity": 0.0, "portfolio_value": 0.0, "status": "error"}

    def get_current_price(self, symbol):
        price = fetch_realtime_price(symbol)
        return price if price is not None else 100.0

    def buy(self, symbol, qty):
        self.api.submit_order(MarketOrderRequest(
            symbol=symbol, qty=int(qty),
            side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
        ))

    def sell(self, symbol, qty):
        self.api.submit_order(MarketOrderRequest(
            symbol=symbol, qty=int(qty),
            side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
        ))

    def get_position(self, symbol):
        symbol = str(symbol).upper()
        try:
            for pos in self.api.get_all_positions():
                if str(pos.symbol).upper() == symbol:
                    return {
                        "symbol": symbol,
                        "qty": float(pos.qty),
                        "entry_price": float(getattr(pos, "avg_entry_price", 0.0) or 0.0),
                        "market_value": float(getattr(pos, "market_value", 0.0) or 0.0),
                    }
        except Exception:
            pass
        return None

    def get_position_size(self, symbol):
        pos = self.get_position(symbol)
        return float(pos["qty"]) if pos else 0

    def get_open_positions_count(self):
        try:
            return len(self.api.get_all_positions())
        except Exception as e:
            print(f"[Alpaca] Unable to fetch open positions: {e}")
            return 0

    def get_open_notional(self):
        try:
            total = 0.0
            for pos in self.api.get_all_positions():
                total += abs(float(getattr(pos, "market_value", 0.0) or 0.0))
            return total
        except Exception as e:
            print(f"[Alpaca] Unable to fetch open notional: {e}")
            return 0.0

    def is_market_open(self):
        try:
            clock = self.api.get_clock()
            return bool(getattr(clock, "is_open", False))
        except Exception as e:
            print(f"[Alpaca] Unable to fetch market clock: {e}")
            return False

    def has_pending_buy_order(self, symbol):
        symbol = str(symbol).upper()
        try:
            for order in self.api.get_orders():
                if self._is_open_buy_order(order, symbol):
                    return True
        except Exception as e:
            print(f"[Alpaca] Unable to fetch open orders for {symbol}: {e}")
        return False


# ── IBKR broker (international markets) ────────────────────────────────────

class IBKRBroker:
    """Connects to IB Gateway / TWS via ib_insync."""

    def __init__(self):
        try:
            from ib_insync import IB, Stock, MarketOrder # pyright: ignore[reportMissingImports]
            self._IB = IB
            self._Stock = Stock
            self._MarketOrder = MarketOrder
        except ImportError:
            raise RuntimeError("ib_insync not installed. Run: pip install ib_insync")

        self.ib = self._IB()
        self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
        print(f"[IBKR] Connected to {IBKR_HOST}:{IBKR_PORT} — accounts: {self.ib.managedAccounts()}")

    def _contract(self, symbol):
        """Parse SYMBOL or SYMBOL:EXCHANGE into an ib_insync Stock contract."""
        if ":" in symbol:
            sym, exch = symbol.split(":", 1)
        else:
            sym, exch = symbol, "SMART"
        return self._Stock(sym, exch, "USD")

    def get_account_balance(self):
        for v in self.ib.accountValues():
            if v.tag == "CashBalance" and v.currency == "BASE":
                return float(v.value)
        return 0.0

    def get_portfolio_value(self):
        for v in self.ib.accountValues():
            if v.tag == "NetLiquidation" and v.currency == "BASE":
                return float(v.value)
        return self.get_account_balance()

    def get_current_price(self, symbol):
        contract = self._contract(symbol)
        self.ib.qualifyContracts(contract)
        ticker = self.ib.reqMktData(contract, "", False, False)
        self.ib.sleep(2)
        price = ticker.marketPrice()
        self.ib.cancelMktData(contract)
        if price and price == price:  # not NaN
            return float(price)
        # fallback to yfinance
        price = fetch_realtime_price(symbol.split(":")[0])
        return price if price is not None else 100.0

    def buy(self, symbol, qty):
        contract = self._contract(symbol)
        self.ib.qualifyContracts(contract)
        order = self._MarketOrder("BUY", int(qty))
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)
        print(f"[IBKR] BUY {qty} {symbol} — status: {trade.orderStatus.status}")

    def sell(self, symbol, qty):
        contract = self._contract(symbol)
        self.ib.qualifyContracts(contract)
        order = self._MarketOrder("SELL", int(qty))
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)
        print(f"[IBKR] SELL {qty} {symbol} — status: {trade.orderStatus.status}")

    def get_position(self, symbol):
        sym = symbol.split(":")[0].upper()
        for pos in self.ib.positions():
            if pos.contract.symbol.upper() == sym:
                return {
                    "symbol": symbol,
                    "qty": float(pos.position),
                    "entry_price": float(pos.avgCost),
                    "market_value": float(pos.position) * float(pos.avgCost),
                }
        return None

    def get_position_size(self, symbol):
        pos = self.get_position(symbol)
        return float(pos["qty"]) if pos else 0

    def get_open_positions_count(self):
        return len(self.ib.positions())

    def get_open_notional(self):
        total = 0.0
        for pos in self.ib.positions():
            try:
                total += abs(float(pos.position) * float(pos.avgCost))
            except Exception:
                continue
        return total

    def is_market_open(self):
        return True

    def has_pending_buy_order(self, symbol):
        return False

    def disconnect(self):
        self.ib.disconnect()


# ── Unified Broker — routes to Alpaca or IBKR based on symbol ──────────────

class Broker:
    """
    Routes orders to the correct broker:
      - Symbols containing ':' (e.g. SHELL:AEB) or listed in IBKR_WATCHLIST → IBKR
      - Everything else → Alpaca
    Set IBKR_ENABLED=true in .env to activate IBKR.
    """

    def __init__(self):
        self._alpaca = AlpacaBroker()
        self._ibkr = None
        if IBKR_ENABLED:
            try:
                self._ibkr = IBKRBroker()
            except Exception as e:
                print(f"[IBKR] Connection failed — IBKR disabled for this session: {e}")

    def _route(self, symbol):
        """Return the broker to use for a given symbol."""
        if self._ibkr and (":" in symbol):
            return self._ibkr
        return self._alpaca

    # ── Unified interface (delegates to whichever broker owns the symbol) ──

    def get_account_balance(self):
        balance = self._alpaca.get_account_balance()
        if self._ibkr:
            balance += self._ibkr.get_account_balance()
        return balance

    def get_portfolio_value(self):
        value = self._alpaca.get_portfolio_value()
        if self._ibkr:
            value += self._ibkr.get_portfolio_value()
        return value

    def get_current_price(self, symbol):
        return self._route(symbol).get_current_price(symbol)

    def buy(self, symbol, qty):
        return self._route(symbol).buy(symbol, qty)

    def sell(self, symbol, qty):
        return self._route(symbol).sell(symbol, qty)

    def get_position(self, symbol):
        return self._route(symbol).get_position(symbol)

    def get_position_size(self, symbol):
        return self._route(symbol).get_position_size(symbol)

    def get_open_positions_count(self):
        count = self._alpaca.get_open_positions_count()
        if self._ibkr:
            count += self._ibkr.get_open_positions_count()
        return count

    def get_open_notional(self):
        total = self._alpaca.get_open_notional()
        if self._ibkr:
            total += self._ibkr.get_open_notional()
        return total

    def is_market_open(self, symbol):
        route = self._route(symbol)
        if hasattr(route, "is_market_open"):
            return bool(route.is_market_open())
        return True

    def has_pending_buy_order(self, symbol):
        route = self._route(symbol)
        if hasattr(route, "has_pending_buy_order"):
            return bool(route.has_pending_buy_order(symbol))
        return False