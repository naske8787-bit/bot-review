import pandas as pd
import yfinance as yf
import time
import requests

from config import (
    CRYPTO_DATA_INTERVAL,
    CRYPTO_FAST_EMA_WINDOW,
    CRYPTO_LOOKBACK_PERIOD,
    CRYPTO_RSI_PERIOD,
    CRYPTO_SLOW_EMA_WINDOW,
    EXTERNAL_RESEARCH_CACHE_TTL_SECONDS,
    EXTERNAL_RESEARCH_ENABLED,
    INFLUENCER_MONITOR_ENABLED,
    INFLUENCER_MONITOR_CACHE_TTL_SECONDS,
    SEARCH_API_KEY,
    SEARCH_ENGINE,
    SEARCH_PROVIDER,
)
from influencer_monitor import monitor_influencers

_GLOBAL_NEWS_CACHE = None
_GLOBAL_NEWS_CACHE_TS = 0.0

_POSITIVE_KEYWORDS = {
    "rally", "surge", "gain", "rise", "growth", "beat", "bullish", "breakthrough",
    "approval", "expansion", "recovery", "innovation", "deal", "inflow", "accumulation",
    "adoption", "easing", "cut", "breakout", "upgrade", "partnership",
}
_NEGATIVE_KEYWORDS = {
    "crash", "plunge", "fall", "drop", "loss", "bearish", "war", "conflict", "recession",
    "inflation", "tariff", "sanction", "default", "ban", "risk", "sell-off", "outflow",
    "hack", "exploit", "lawsuit", "rejection", "tightening", "liquidation", "depeg",
}
_TOPIC_KEYWORDS = {
    "etf_flows": {"etf", "inflow", "outflow", "spot etf", "blackrock", "fidelity", "ibit"},
    "liquidity_rates": {"fed", "fomc", "rates", "yield", "liquidity", "dollar", "dxy", "qe", "qt"},
    "regulation_policy": {"regulation", "sec", "cftc", "lawsuit", "ban", "compliance", "policy"},
    "onchain_activity": {"active address", "transaction volume", "network activity", "on-chain", "fees"},
    "stablecoin_liquidity": {"stablecoin", "usdt", "usdc", "depeg", "mint", "burn", "treasury"},
    "derivatives_leverage": {"funding rate", "open interest", "liquidation", "perpetual", "futures"},
    "exchange_security": {"hack", "exploit", "exchange", "outage", "custody", "insolvency"},
    "adoption_technology": {"institutional", "adoption", "layer 2", "l2", "defi", "tokenization", "rwa"},
    "miner_supply": {"hashrate", "miner", "halving", "issuance", "supply", "difficulty"},
    "risk_sentiment": {"risk-on", "risk-off", "volatility", "fear", "greed", "correlation", "nasdaq"},
}

_TOPIC_WEIGHTS = {
    "etf_flows": 1.4,
    "stablecoin_liquidity": 1.3,
    "derivatives_leverage": 1.2,
    "liquidity_rates": 1.2,
    "regulation_policy": 1.2,
    "onchain_activity": 1.1,
    "exchange_security": 1.2,
    "adoption_technology": 1.0,
    "miner_supply": 0.9,
    "risk_sentiment": 1.1,
}

_TOPIC_STRATEGY_HINTS = {
    "etf_flows": "Track trend-continuation setups when ETF flow headlines stay positive.",
    "stablecoin_liquidity": "Increase conviction only when stablecoin liquidity expands.",
    "derivatives_leverage": "Reduce size during crowded leverage/liquidation conditions.",
    "liquidity_rates": "Favor long bias when macro liquidity/rate headlines turn supportive.",
    "regulation_policy": "Tighten risk around regulatory shocks and legal uncertainty.",
    "onchain_activity": "Prefer entries when on-chain usage growth confirms momentum.",
    "exchange_security": "De-risk quickly when exchange/security stress headlines appear.",
    "adoption_technology": "Hold winners longer when institutional adoption narrative strengthens.",
    "miner_supply": "Watch supply-pressure windows around miner stress/halving narratives.",
    "risk_sentiment": "Align exposure with broad risk-on/risk-off regime.",
}

_SEARCH_RESEARCH_QUERIES = [
    "bitcoin etf inflows outflows impact on crypto returns",
    "crypto derivatives funding rate open interest liquidation risk",
    "stablecoin supply growth USDT USDC market liquidity crypto",
    "fed rates dollar liquidity effect on bitcoin ethereum",
    "crypto regulation sec cftc enforcement latest",
    "on-chain activity active addresses transaction volume bitcoin ethereum",
    "crypto exchange hack outage custody risk headlines",
    "institutional crypto adoption rwa tokenization layer 2 growth",
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
        if not any(kw in raw for kw in kws):
            continue
        if score != 0:
            topic_scores[topic] += score
        elif pos > 0:
            topic_scores[topic] += 0.5
        elif neg > 0:
            topic_scores[topic] -= 0.5
    return score, topic_scores


def _derive_strategy_notes(topic_scores):
    ranked = sorted(topic_scores.items(), key=lambda kv: abs(float(kv[1])), reverse=True)
    notes = []
    for topic, value in ranked:
        if abs(float(value)) < 0.5:
            continue
        hint = _TOPIC_STRATEGY_HINTS.get(topic)
        if hint:
            notes.append(hint)
        if len(notes) >= 3:
            break
    return notes


def fetch_external_research_sentiment():
    """Build an internet research signal from broad-market and macro news feeds."""
    global _GLOBAL_NEWS_CACHE, _GLOBAL_NEWS_CACHE_TS
    if not EXTERNAL_RESEARCH_ENABLED:
        return {"score": 0.0, "headline_count": 0, "headlines": [], "topic_scores": {}}

    now = time.time()
    if _GLOBAL_NEWS_CACHE and now - _GLOBAL_NEWS_CACHE_TS < EXTERNAL_RESEARCH_CACHE_TTL_SECONDS:
        return _GLOBAL_NEWS_CACHE

    sources = ["^GSPC", "^IXIC", "^DJI", "BTC-USD", "ETH-USD", "GC=F", "CL=F", "DX-Y.NYB"]
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

    weighted_score = sum(float(topic_scores.get(t, 0.0)) * float(_TOPIC_WEIGHTS.get(t, 1.0)) for t in _TOPIC_KEYWORDS)
    dominant_topics = [t for t, _ in sorted(topic_scores.items(), key=lambda kv: abs(float(kv[1])), reverse=True)[:5]]

    # Normalize the weighted_score so that extreme raw keyword counts do not
    # permanently lock out the bot. Cap at ±12 which brackets all configured
    # CRYPTO_RESEARCH_*_BLOCK_SCORE thresholds.  This prevents a single session
    # of very bearish RSS headlines from producing scores like -39.20 that would
    # lock entries for hours even when market conditions have improved.
    headline_count = len(headlines)
    # Apply a confidence-scaled cap: fewer headlines → tighter cap so noise
    # doesn't masquerade as strong signal.
    cap_magnitude = min(12.0, max(4.0, 0.5 * headline_count))
    normalized_score = max(-cap_magnitude, min(cap_magnitude, weighted_score))

    # Confidence: how much of the signal is coming from many diverse headlines vs
    # a thin stream of same-sourced content.
    source_count = sum(1 for _ in dominant_topics if _)  # approximation via topic breadth
    confidence = min(1.0, headline_count / 20.0) * min(1.0, source_count / 5.0)

    _GLOBAL_NEWS_CACHE = {
        "score": normalized_score,
        "raw_score": score,
        "weighted_score": normalized_score,
        "headline_count": headline_count,
        "headlines": headlines[:10],
        "topic_scores": topic_scores,
        "dominant_topics": dominant_topics,
        "strategy_notes": _derive_strategy_notes(topic_scores),
        "search_provider": SEARCH_PROVIDER or "disabled",
        "search_enabled": bool(SEARCH_PROVIDER and SEARCH_API_KEY),
        "confidence": round(confidence, 4),
        "source_count": source_count,
    }

    # Attach influencer manipulation signals if enabled
    if INFLUENCER_MONITOR_ENABLED and SEARCH_API_KEY:
        try:
            influencer_data = monitor_influencers(
                api_key=SEARCH_API_KEY,
                cache_ttl_seconds=INFLUENCER_MONITOR_CACHE_TTL_SECONDS,
            )
            _GLOBAL_NEWS_CACHE["influencer_signals"] = influencer_data

            # Adjust the global research score based on influencer signal
            global_inf = influencer_data.get("global", {})
            avg_inf_signal = float(global_inf.get("avg_net_signal", 0.0))
            # Weight influencer signal at 30% vs 70% existing macro signal
            blended_score = _GLOBAL_NEWS_CACHE["score"] * 0.70 + avg_inf_signal * 0.30
            blended_score = max(-cap_magnitude, min(cap_magnitude, blended_score))
            _GLOBAL_NEWS_CACHE["score"] = blended_score
            _GLOBAL_NEWS_CACHE["weighted_score"] = blended_score
            _GLOBAL_NEWS_CACHE["influencer_dominant_signal"] = global_inf.get("dominant_signal", "neutral")
            _GLOBAL_NEWS_CACHE["influencer_manipulation_detected"] = bool(
                global_inf.get("manipulation_detected", False)
            )
        except Exception:
            _GLOBAL_NEWS_CACHE["influencer_signals"] = {"by_symbol": {}, "global": {}}
    else:
        _GLOBAL_NEWS_CACHE["influencer_signals"] = {"by_symbol": {}, "global": {}}

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


def to_yfinance_symbol(symbol):
    return symbol.replace("/", "-").upper()


def to_alpaca_symbol(symbol):
    return symbol.upper()


def fetch_crypto_data(symbol, period=None, interval=None):
    ticker = to_yfinance_symbol(symbol)
    data = yf.download(
        ticker,
        period=period or CRYPTO_LOOKBACK_PERIOD,
        interval=interval or CRYPTO_DATA_INTERVAL,
        progress=False,
        auto_adjust=False,
    )
    if isinstance(data, pd.Series):
        data = data.to_frame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def preprocess_data(data):
    data = data.copy().dropna()
    close = pd.to_numeric(data["Close"], errors="coerce")
    data["ema_fast"] = close.ewm(span=CRYPTO_FAST_EMA_WINDOW, adjust=False).mean()
    data["ema_slow"] = close.ewm(span=CRYPTO_SLOW_EMA_WINDOW, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / max(CRYPTO_RSI_PERIOD, 1), min_periods=CRYPTO_RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / max(CRYPTO_RSI_PERIOD, 1), min_periods=CRYPTO_RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    data["rsi"] = (100 - (100 / (1 + rs))).fillna(50.0)
    data["momentum_pct"] = close.pct_change(3).fillna(0.0)
    return data.dropna()
