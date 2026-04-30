"""Fetch OHLCV bars and live quotes for forex pairs via yfinance."""
from __future__ import annotations

from typing import Optional
import time

import pandas as pd
import yfinance as yf
import requests

from config import (
    ATR_PERIOD,
    EMA_LONG,
    EMA_SHORT,
    RSI_PERIOD,
    EXTERNAL_RESEARCH_CACHE_TTL_SECONDS,
    EXTERNAL_RESEARCH_ENABLED,
    SEARCH_API_KEY,
    SEARCH_ENGINE,
    SEARCH_PROVIDER,
)

_GLOBAL_NEWS_CACHE = None
_GLOBAL_NEWS_CACHE_TS = 0.0

_POSITIVE_KEYWORDS = {
    "rally", "surge", "gain", "rise", "growth", "bullish", "breakthrough", "recovery", "deal",
}
_NEGATIVE_KEYWORDS = {
    "crash", "plunge", "fall", "drop", "loss", "bearish", "war", "recession", "inflation", "sanction",
}
_TOPIC_KEYWORDS = {
    "policy": {"policy", "regulation", "fed", "fomc", "ecb", "boj", "rba", "boe"},
    "global_economy": {"gdp", "pmi", "inflation", "unemployment", "rates", "yield", "currency"},
    "technology": {"ai", "chip", "cloud", "automation", "software", "cyber"},
    "commodities": {"oil", "gas", "gold", "silver", "copper", "wheat", "iron ore"},
    "market_movers": {"central bank", "president", "prime minister", "treasury", "minister"},
}

_SEARCH_RESEARCH_QUERIES = [
    "central bank policy outlook forex pairs",
    "global macro trends currency market risk",
    "commodity moves and FX correlation",
    "geopolitics and safe haven currency flows",
]


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
    """Build an internet research signal from global macro and FX-relevant sources."""
    global _GLOBAL_NEWS_CACHE, _GLOBAL_NEWS_CACHE_TS
    if not EXTERNAL_RESEARCH_ENABLED:
        return {"score": 0.0, "headline_count": 0, "headlines": [], "topic_scores": {}}

    now = time.time()
    if _GLOBAL_NEWS_CACHE and now - _GLOBAL_NEWS_CACHE_TS < EXTERNAL_RESEARCH_CACHE_TTL_SECONDS:
        return _GLOBAL_NEWS_CACHE

    sources = ["^GSPC", "^DJI", "DX-Y.NYB", "GC=F", "CL=F", "EURUSD=X", "GBPUSD=X", "JPY=X"]
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

# yfinance ticker suffixes and interval mappings
_YF_PAIR_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CAD": "USDCAD=X",
    "USD/CHF": "USDCHF=X",
    "NZD/USD": "NZDUSD=X",
}

_TF_MAP = {
    "1Min":  ("1m",  "1d"),
    "5Min":  ("5m",  "5d"),
    "15Min": ("15m", "5d"),
    "1Hour": ("1h",  "30d"),
    "1Day":  ("1d",  "1y"),
}


def _pair_to_yf(pair: str) -> str:
    return _YF_PAIR_MAP.get(pair, pair.replace("/", "") + "=X")


def fetch_bars(pair: str, lookback_bars: int = 200, timeframe: str = "1Min") -> pd.DataFrame:
    """Return a DataFrame of OHLCV bars with technical indicators added."""
    yf_symbol = _pair_to_yf(pair)
    interval, period = _TF_MAP.get(timeframe, ("1m", "1d"))

    df = yf.download(yf_symbol, period=period, interval=interval,
                     progress=False, auto_adjust=True)

    if df.empty:
        return df

    # yfinance may return MultiIndex columns when downloading a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.sort_index().dropna()
    df = _add_indicators(df)
    return df.iloc[-lookback_bars:]


def fetch_latest_price(pair: str) -> Optional[float]:
    """Return the latest price for a pair."""
    try:
        yf_symbol = _pair_to_yf(pair)
        df = yf.download(yf_symbol, period="1d", interval="1m",
                         progress=False, auto_adjust=True)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return float(df["Close"].iloc[-1])
    except Exception as e:
        print(f"[DataFetcher] Failed to get quote for {pair}: {e}")
    return None


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA, RSI, ATR, and MACD columns to an OHLCV DataFrame."""
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]

    # EMAs
    df[f"EMA_{EMA_SHORT}"] = close.ewm(span=EMA_SHORT, adjust=False).mean()
    df[f"EMA_{EMA_LONG}"]  = close.ewm(span=EMA_LONG,  adjust=False).mean()

    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI"] = 100 - (100 / (1 + rs))

    # ATR (Average True Range)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"]        = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"]   = df["MACD"] - df["MACD_signal"]

    return df.dropna()
