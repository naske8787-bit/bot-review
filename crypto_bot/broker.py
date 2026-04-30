from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoLatestBarRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
import math
import os

import config as cfg
from data_fetcher import fetch_crypto_data, to_alpaca_symbol


class Broker:
    def __init__(self):
        self._api_key = ""
        self._api_secret = ""
        self._set_credentials_from_env()
        self.api = TradingClient(self._api_key, self._api_secret, paper=cfg.CRYPTO_PAPER_ONLY)
        self._data_client = CryptoHistoricalDataClient(self._api_key, self._api_secret)
        self._last_account_snapshot = {
            "cash": 0.0,
            "buying_power": 0.0,
            "portfolio_value": 0.0,
        }

    @staticmethod
    def _pick_first_nonempty(names):
        for name in names:
            value = (os.getenv(name) or "").strip()
            if value:
                return value
        return ""

    def _set_credentials_from_env(self):
        # Support common Alpaca key names used across different deploy setups.
        key = self._pick_first_nonempty([
            "ALPACA_API_KEY",
            "APCA_API_KEY_ID",
            "ALPACA_PAPER_API_KEY",
        ]) or (cfg.ALPACA_API_KEY or "").strip()
        secret = self._pick_first_nonempty([
            "ALPACA_API_SECRET",
            "APCA_API_SECRET_KEY",
            "ALPACA_PAPER_API_SECRET",
        ]) or (cfg.ALPACA_API_SECRET or "").strip()

        if not key or not secret:
            raise RuntimeError(
                "Missing Alpaca credentials. Set ALPACA_API_KEY/ALPACA_API_SECRET "
                "or APCA_API_KEY_ID/APCA_API_SECRET_KEY."
            )
        self._api_key = key
        self._api_secret = secret

    @staticmethod
    def _is_auth_error(exc):
        text = str(exc).lower()
        return "unauthorized" in text or "forbidden" in text or " 401" in text or " 403" in text

    def _refresh_clients(self):
        self._set_credentials_from_env()
        self.api = TradingClient(self._api_key, self._api_secret, paper=cfg.CRYPTO_PAPER_ONLY)
        self._data_client = CryptoHistoricalDataClient(self._api_key, self._api_secret)

    def _fetch_account_with_retry(self):
        try:
            return self.api.get_account()
        except Exception as exc:
            if not self._is_auth_error(exc):
                raise
            # Transient auth hiccup: rebuild clients and retry once.
            self._refresh_clients()
            return self.api.get_account()

    def get_account_balance(self):
        try:
            account = self._fetch_account_with_retry()
            # Use buying_power if available (accounts for margin/positions)
            buying_power = float(getattr(account, "buying_power", None) or 0.0)
            cash = float(account.cash or 0.0)
            self._last_account_snapshot["cash"] = cash
            self._last_account_snapshot["buying_power"] = buying_power
            self._last_account_snapshot["portfolio_value"] = float(
                getattr(account, "portfolio_value", self._last_account_snapshot.get("portfolio_value", 0.0)) or 0.0
            )
            # If buying_power is 0 but cash is positive, use cash
            # If both are negative, return 0 (margin call or debt)
            # If buying_power > cash, there's margin available, use buying_power
            if buying_power > 0:
                return buying_power
            elif cash > 0:
                return cash
            else:
                # Account in negative state (margin call, debt, or other issue)
                return 0.0
        except Exception as e:
            # Handle Pydantic validation errors (e.g., ACCOUNT_CLOSED_PENDING)
            print(f"[Alpaca] Error fetching account balance: {e}")
            # Fall back to the most recent good value to avoid transient zeroing.
            fallback = float(self._last_account_snapshot.get("buying_power", 0.0) or 0.0)
            if fallback <= 0:
                fallback = float(self._last_account_snapshot.get("cash", 0.0) or 0.0)
            return fallback

    def get_portfolio_value(self):
        try:
            account = self._fetch_account_with_retry()
            portfolio_value = getattr(account, "portfolio_value", account.cash)
            value = float(portfolio_value or 0.0)
            self._last_account_snapshot["portfolio_value"] = value
            self._last_account_snapshot["cash"] = float(account.cash or self._last_account_snapshot.get("cash", 0.0) or 0.0)
            self._last_account_snapshot["buying_power"] = float(
                getattr(account, "buying_power", self._last_account_snapshot.get("buying_power", 0.0)) or 0.0
            )
            return value
        except Exception as e:
            print(f"[Alpaca] Error fetching portfolio value: {e}")
            # Use cached portfolio value if available, else balance fallback.
            cached_value = float(self._last_account_snapshot.get("portfolio_value", 0.0) or 0.0)
            if cached_value > 0:
                return cached_value
            return self.get_account_balance()

    def get_current_price(self, symbol):
        alpaca_symbol = to_alpaca_symbol(symbol)
        try:
            bars = self._data_client.get_crypto_latest_bar(
                CryptoLatestBarRequest(symbol_or_symbols=alpaca_symbol)
            )
            bar = bars.get(alpaca_symbol)
            if bar is not None:
                return float(bar.close)
        except Exception as e:
            print(f"Alpaca crypto price fetch failed for {symbol}, falling back to yfinance: {e}")
        data = fetch_crypto_data(symbol, period="5d", interval="1h")
        if data.empty:
            raise RuntimeError(f"No price data returned for {symbol}")
        price = data["Close"].iloc[-1]
        return float(price.item() if hasattr(price, "item") else price)

    def buy(self, symbol, qty):
        quantized_qty = math.floor(max(0.0, float(qty)) * 1_000_000) / 1_000_000
        if quantized_qty <= 0:
            raise ValueError(f"Invalid BUY quantity after quantization for {symbol}: {qty}")
        order_data = MarketOrderRequest(
            symbol=to_alpaca_symbol(symbol),
            qty=quantized_qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
        )
        self.api.submit_order(order_data)

    def sell(self, symbol, qty):
        quantized_qty = math.floor(max(0.0, float(qty)) * 1_000_000) / 1_000_000
        if quantized_qty <= 0:
            raise ValueError(f"Invalid SELL quantity after quantization for {symbol}: {qty}")
        order_data = MarketOrderRequest(
            symbol=to_alpaca_symbol(symbol),
            qty=quantized_qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
        )
        self.api.submit_order(order_data)

    def get_position_size(self, symbol):
        normalized_symbol = to_alpaca_symbol(symbol)
        normalized_symbol_compact = normalized_symbol.replace("/", "")
        try:
            for position in self.api.get_all_positions():
                position_symbol = str(position.symbol).upper()
                if position_symbol == normalized_symbol or position_symbol.replace("/", "") == normalized_symbol_compact:
                    return float(position.qty)
        except Exception:
            pass
        return 0.0

    def get_open_positions_count(self):
        try:
            return len(self.api.get_all_positions())
        except Exception:
            return 0

    def get_open_notional(self):
        try:
            total = 0.0
            for pos in self.api.get_all_positions():
                total += abs(float(getattr(pos, "market_value", 0.0) or 0.0))
            return total
        except Exception:
            return 0.0

    def get_account_details(self):
        try:
            account = self._fetch_account_with_retry()
            return {
                "cash": float(getattr(account, "cash", 0.0) or 0.0),
                "buying_power": float(getattr(account, "buying_power", 0.0) or 0.0),
                "portfolio_value": float(getattr(account, "portfolio_value", 0.0) or 0.0),
                "status": str(getattr(account, "status", "unknown")),
            }
        except Exception:
            return {
                "cash": float(self._last_account_snapshot.get("cash", 0.0) or 0.0),
                "buying_power": float(self._last_account_snapshot.get("buying_power", 0.0) or 0.0),
                "portfolio_value": float(self._last_account_snapshot.get("portfolio_value", 0.0) or 0.0),
                "status": "error",
            }
