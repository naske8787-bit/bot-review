"""
Fetch OHLCV bars for ASX stocks via yfinance.

Adds the following technical indicators used by the day-trading strategy:
  - EMA_9, EMA_21           — trend direction
  - RSI                     — momentum / overbought-oversold
  - ATR                     — volatility / position sizing
  - MACD, MACD_signal, MACD_hist — momentum confirmation
  - BB_upper, BB_mid, BB_lower   — Bollinger Bands (mean reversion)
  - VWAP                    — intraday fair-value anchor (cumulative daily)
  - Volume_ratio            — current volume vs 20-bar rolling average (spike detection)
"""
from __future__ import annotations

from typing import Optional
import time

import pandas as pd
import yfinance as yf
import requests

from config import (
    ATR_PERIOD,
    BB_PERIOD,
    BB_STD,
    EMA_LONG,
    EMA_SHORT,
    RSI_PERIOD,
    EXTERNAL_RESEARCH_CACHE_TTL_SECONDS,
    EXTERNAL_RESEARCH_ENABLED,
    SEARCH_API_KEY,
    SEARCH_ENGINE,
    SEARCH_PROVIDER,
)

_STOCK_DATA_CACHE: dict = {}
_STOCK_DATA_CACHE_TTL_SECONDS = 900
_GLOBAL_NEWS_CACHE = None
_GLOBAL_NEWS_CACHE_TS = 0.0

_POSITIVE_KEYWORDS = {
    "rally", "surge", "gain", "rise", "growth", "bullish", "deal", "recovery", "breakthrough",
}
_NEGATIVE_KEYWORDS = {
    "crash", "plunge", "fall", "drop", "loss", "bearish", "war", "recession", "inflation", "sanction",
}
_TOPIC_KEYWORDS = {
    "policy": {"policy", "regulation", "rba", "fed", "budget", "tax", "tariff", "subsidy"},
    "global_economy": {"gdp", "pmi", "inflation", "unemployment", "debt", "trade", "currency"},
    "technology": {"ai", "chip", "software", "cloud", "automation", "cyber"},
    "commodities": {"iron ore", "coal", "lithium", "gold", "oil", "gas", "copper", "uranium"},
    "market_movers": {"ceo", "central bank", "minister", "prime minister", "treasury"},
}

_SEARCH_RESEARCH_QUERIES = [
    "ASX market outlook policy and rates",
    "commodity cycle impact on australian equities",
    "global macro trend impact on ASX sectors",
    "emerging technology adoption in listed companies",
]

# Intraday timeframe used by the day-trading strategy
_INTRADAY_INTERVAL = "5m"
_INTRADAY_PERIOD   = "5d"   # yfinance max for 5-min data is 60 days, 5d gives plenty

# Longer timeframe used only for initial bulk model training
_DAILY_INTERVAL    = "1d"
_DAILY_PERIOD      = "2y"


def fetch_bars(symbol: str, lookback_bars: int = 200, use_daily: bool = False) -> pd.DataFrame:
    """
    Return a DataFrame of OHLCV bars with all indicators attached.

    Parameters
    ----------
    symbol       : ASX ticker with .AX suffix  (e.g. "BHP.AX")
    lookback_bars: how many bars to return after indicator warm-up trim
    use_daily    : if True, fetch daily bars (used for initial bulk training)
    """
    interval = _DAILY_INTERVAL if use_daily else _INTRADAY_INTERVAL
    period   = _DAILY_PERIOD   if use_daily else _INTRADAY_PERIOD

    df = yf.download(symbol, period=period, interval=interval,
                     progress=False, auto_adjust=True)

    if df.empty:
        return df

    # yfinance returns MultiIndex columns for single-ticker downloads in newer versions
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.sort_index().dropna(subset=["Close"])

    df = _add_indicators(df)
    return df.iloc[-lookback_bars:] if len(df) >= lookback_bars else df


def fetch_latest_price(symbol: str) -> Optional[float]:
    """Return the most recent close price for a symbol."""
    try:
        df = yf.download(symbol, period="1d", interval="1m",
                         progress=False, auto_adjust=True)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return float(df["Close"].iloc[-1])
    except Exception as e:
        print(f"[DataFetcher] Price fetch failed for {symbol}: {e}")
    return None


def fetch_stock_data(
    symbol: str,
    period: str = "1y",
    start=None,
    end=None,
    use_cache: bool = True,
    interval: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch historical stock data with optional interval control.

    Used by long-horizon event-learner bootstrap and macro proxy features.
    """
    cache_key = (str(symbol).upper(), period, start, end, interval)
    now = time.time()

    if use_cache and start is None and end is None:
        cached = _STOCK_DATA_CACHE.get(cache_key)
        if cached and now - cached[0] < _STOCK_DATA_CACHE_TTL_SECONDS:
            return cached[1].copy()

    kwargs = {"progress": False, "auto_adjust": False}
    if interval:
        kwargs["interval"] = interval
    if start is not None or end is not None:
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
    else:
        kwargs["period"] = period

    data = yf.download(symbol, **kwargs)
    if isinstance(data, pd.Series):
        data = data.to_frame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    if use_cache and start is None and end is None:
        _STOCK_DATA_CACHE[cache_key] = (now, data.copy())
    return data


def _score_headline(text):
    raw = str(text or "").lower().strip()
    topic_scores = {k: 0.0 for k in _TOPIC_KEYWORDS}
    if not raw:
        return 0.0, topic_scores
    pos = sum(1 for kw in _POSITIVE_KEYWORDS if kw in raw)
    neg = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in raw)
    score = float(pos - neg)
    for topic, kws in _TOPIC_KEYWORDS.items():
        if any(kw in raw for kw in kws):
            topic_scores[topic] += score
    return score, topic_scores


def fetch_external_research_sentiment():
    """Build a macro/policy/technology/commodity internet research signal for ASX."""
    global _GLOBAL_NEWS_CACHE, _GLOBAL_NEWS_CACHE_TS
    if not EXTERNAL_RESEARCH_ENABLED:
        return {"score": 0.0, "headline_count": 0, "headlines": [], "topic_scores": {}}

    now = time.time()
    if _GLOBAL_NEWS_CACHE and now - _GLOBAL_NEWS_CACHE_TS < EXTERNAL_RESEARCH_CACHE_TTL_SECONDS:
        return _GLOBAL_NEWS_CACHE

    sources = ["^AXJO", "^GSPC", "^IXIC", "GC=F", "CL=F", "HG=F", "DX-Y.NYB"]
    topic_scores = {k: 0.0 for k in _TOPIC_KEYWORDS}
    score = 0.0
    headlines = []
    seen = set()

    for src in sources:
        try:
            ticker = yf.Ticker(src)
            for item in (ticker.news or [])[:10]:
                title = str(item.get("title", "") or "").strip()
                if not title:
                    continue
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                s, contrib = _score_headline(title)
                score += s
                for topic, value in contrib.items():
                    topic_scores[topic] += value
                headlines.append(title)
        except Exception:
            continue

    search_payload = _fetch_search_engine_research()
    score += float(search_payload.get("score", 0.0))
    for topic, val in (search_payload.get("topic_scores", {}) or {}).items():
        topic_scores[topic] = float(topic_scores.get(topic, 0.0)) + float(val)
    headlines.extend(search_payload.get("headlines", [])[:10])

    _GLOBAL_NEWS_CACHE = {
        "score": score,
        "headline_count": len(headlines),
        "headlines": headlines[:10],
        "topic_scores": topic_scores,
        "search_provider": SEARCH_PROVIDER or "disabled",
        "search_enabled": bool(SEARCH_PROVIDER and SEARCH_API_KEY),
    }
    _GLOBAL_NEWS_CACHE_TS = now
    return _GLOBAL_NEWS_CACHE


def _fetch_search_engine_research():
    if not SEARCH_PROVIDER or not SEARCH_API_KEY:
        return {"score": 0.0, "headlines": [], "topic_scores": {}}
    if SEARCH_PROVIDER == "brave":
        return _search_brave()
    if SEARCH_PROVIDER == "serpapi":
        return _search_serpapi()
    return {"score": 0.0, "headlines": [], "topic_scores": {}}


def _score_search_items(items):
    topic_scores = {k: 0.0 for k in _TOPIC_KEYWORDS}
    score = 0.0
    headlines = []
    seen = set()
    for text in items:
        raw = str(text or "").strip()
        if not raw:
            continue
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        s, contrib = _score_headline(raw)
        score += s
        for topic, val in contrib.items():
            topic_scores[topic] = float(topic_scores.get(topic, 0.0)) + float(val)
        headlines.append(raw[:180])
    return {"score": score, "headlines": headlines[:20], "topic_scores": topic_scores}


def _search_brave():
    items = []
    headers = {"Accept": "application/json", "X-Subscription-Token": SEARCH_API_KEY}
    for q in _SEARCH_RESEARCH_QUERIES:
        try:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": q, "count": 8},
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            for row in (payload.get("web", {}) or {}).get("results", [])[:8]:
                items.append(f"{row.get('title','')} {row.get('description','')}")
        except Exception:
            continue
    return _score_search_items(items)


def _search_serpapi():
    items = []
    for q in _SEARCH_RESEARCH_QUERIES:
        try:
            r = requests.get(
                "https://serpapi.com/search.json",
                params={"engine": SEARCH_ENGINE or "google", "q": q, "api_key": SEARCH_API_KEY, "num": 8},
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            for row in payload.get("organic_results", [])[:8]:
                items.append(f"{row.get('title','')} {row.get('snippet','')}")
        except Exception:
            continue
    return _score_search_items(items)


# ── Indicator computation ─────────────────────────────────────────────────────

def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close  = df["Close"]
    high   = df["High"]
    low    = df["Low"]
    volume = df["Volume"]

    # ── EMAs ──────────────────────────────────────────────────────────────────
    df[f"EMA_{EMA_SHORT}"] = close.ewm(span=EMA_SHORT, adjust=False).mean()
    df[f"EMA_{EMA_LONG}"]  = close.ewm(span=EMA_LONG,  adjust=False).mean()

    # ── RSI ───────────────────────────────────────────────────────────────────
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI"] = 100 - (100 / (1 + rs))

    # ── ATR ───────────────────────────────────────────────────────────────────
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()

    # ── MACD ─────────────────────────────────────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"]        = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"]   = df["MACD"] - df["MACD_signal"]

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    sma              = close.rolling(BB_PERIOD).mean()
    std              = close.rolling(BB_PERIOD).std()
    df["BB_mid"]     = sma
    df["BB_upper"]   = sma + BB_STD * std
    df["BB_lower"]   = sma - BB_STD * std
    df["BB_width"]   = (df["BB_upper"] - df["BB_lower"]) / sma.replace(0, float("nan"))  # normalised width

    # ── VWAP (daily cumulative reset) ─────────────────────────────────────────
    # Group by trading date, then compute cumsum within each day
    typical_price = (high + low + close) / 3.0
    df["_tp_vol"]  = typical_price * volume

    if hasattr(df.index, "date"):
        date_col = pd.Series(df.index.date, index=df.index, name="_date")
        df["VWAP"] = (
            df.groupby(date_col)["_tp_vol"].cumsum()
            / df.groupby(date_col)["Volume"].cumsum().replace(0, float("nan"))
        )
    else:
        # Fallback: rolling approximation if index is not datetime
        df["VWAP"] = df["_tp_vol"].cumsum() / volume.cumsum().replace(0, float("nan"))

    df.drop(columns=["_tp_vol"], inplace=True)

    # ── Volume ratio (vs 20-bar rolling mean) ────────────────────────────────
    vol_avg = volume.rolling(20).mean().replace(0, float("nan"))
    df["Volume_ratio"] = volume / vol_avg

    return df.dropna()
