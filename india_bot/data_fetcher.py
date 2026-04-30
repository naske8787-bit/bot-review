"""Data fetching for the India bot.

- Historical OHLCV: yfinance (with NSE/BSE suffix)
- Real-time last price: IBKR via ib_insync snapshot, falls back to yfinance
"""

import time

import pandas as pd
import yfinance as yf

from config import (
    DEFAULT_EXCHANGE,
    IBKR_CURRENCY,
    STOCK_DATA_CACHE_TTL_SECONDS,
    YF_SUFFIX,
)

_STOCK_DATA_CACHE: dict = {}


def _yf_symbol(symbol: str) -> str:
    """Append the correct yfinance exchange suffix if not already present."""
    symbol = symbol.upper()
    # Keep global indices/futures untouched (e.g. ^GSPC, ^TNX, CL=F, GC=F).
    if symbol.startswith("^") or "=" in symbol:
        return symbol
    if "." in symbol:
        return symbol  # already has suffix (e.g. RELIANCE.NS)
    return symbol + YF_SUFFIX


def fetch_realtime_price(symbol: str, ib=None) -> float | None:
    """Fetch latest traded price via IBKR snapshot, fallback to yfinance.
    
    Pass the IB() instance from broker.py as `ib` for live data.
    """
    if ib is not None and ib.isConnected():
        try:
            from ib_insync import Stock
            contract = Stock(symbol.upper(), DEFAULT_EXCHANGE, IBKR_CURRENCY)
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, snapshot=True)
            ib.sleep(2)  # Wait for snapshot
            price = ticker.last or ticker.close or ticker.bid
            ib.cancelMktData(contract)
            if price and price > 0:
                return float(price)
        except Exception as e:
            print(f"[DataFetcher] IBKR price fetch failed for {symbol}, falling back to yfinance: {e}")

    # yfinance fallback
    try:
        yf_sym = _yf_symbol(symbol)
        data = yf.download(yf_sym, period="5d", progress=False, auto_adjust=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        price = data["Close"].iloc[-1]
        return float(price.item() if hasattr(price, "item") else price)
    except Exception as e:
        print(f"[DataFetcher] yfinance also failed for {symbol}: {e}")
        return None


def fetch_stock_data(
    symbol: str,
    period: str = "1y",
    start=None,
    end=None,
    use_cache: bool = True,
    interval: str | None = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV data via yfinance."""
    yf_sym = _yf_symbol(symbol)
    cache_key = (yf_sym, period, start, end, interval)
    now = time.time()

    if use_cache and start is None and end is None:
        cached = _STOCK_DATA_CACHE.get(cache_key)
        if cached and now - cached[0] < STOCK_DATA_CACHE_TTL_SECONDS:
            return cached[1].copy()

    kwargs: dict = {"progress": False}
    if interval:
        kwargs["interval"] = interval
    if start is not None or end is not None:
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
    else:
        kwargs["period"] = period

    data = yf.download(yf_sym, **kwargs)
    if isinstance(data, pd.Series):
        data = data.to_frame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    if use_cache and start is None and end is None:
        _STOCK_DATA_CACHE[cache_key] = (now, data.copy())
    return data


def preprocess_data(data: pd.DataFrame) -> pd.DataFrame:
    data = data.dropna()
    data = data.copy()
    data["Returns"] = data["Close"].pct_change()
    return data
