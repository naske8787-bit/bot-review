"""Crypto Influencer Monitor

Tracks known crypto market influencers, detects coordinated pump/dump/FUD
manipulation patterns, and produces per-symbol trading signals for the bot.

Signal logic:
  - pump_score  > INFLUENCER_PUMP_TRADE_SCORE  → BUY early (ride the pump)
  - dump_score  > INFLUENCER_DUMP_SELL_SCORE   → SELL / block new entries
  - coordination detected (2+ influencers same direction) → multiply strength

The module is intentionally read-only: it searches public news/web results via
the already-configured Brave Search API and scores keywords. No social API
credentials are required.
"""

import os
import time
import requests
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Influencer Registry
# ---------------------------------------------------------------------------
# Each entry describes a well-known market mover with:
#   name             – display name
#   search_terms     – query strings used to find their recent statements
#   affected_symbols – crypto tickers they typically move (base symbol only)
#   influence_weight – multiplier applied to raw signal (1.0 = neutral)
#   manipulation_risk – 0..1 probability this person historically manipulates
#   known_for        – description used in log output
# ---------------------------------------------------------------------------
INFLUENCERS: Dict[str, dict] = {
    "elon_musk": {
        "name": "Elon Musk",
        "search_terms": [
            "Elon Musk bitcoin crypto statement",
            "Elon Musk dogecoin tweet today",
            "Elon Musk cryptocurrency announcement",
        ],
        "affected_symbols": ["BTC", "DOGE", "ETH"],
        "influence_weight": 3.0,
        "manipulation_risk": 0.90,
        "known_for": "DOGE pumps, BTC adoption tweets, SNL dump",
    },
    "michael_saylor": {
        "name": "Michael Saylor",
        "search_terms": [
            "Michael Saylor bitcoin buy accumulate",
            "MicroStrategy bitcoin purchase announcement",
        ],
        "affected_symbols": ["BTC"],
        "influence_weight": 2.5,
        "manipulation_risk": 0.50,
        "known_for": "BTC institutional accumulation, price targets",
    },
    "cz_binance": {
        "name": "CZ (Changpeng Zhao)",
        "search_terms": [
            "CZ Binance crypto market statement",
            "Changpeng Zhao bitcoin ethereum announcement",
        ],
        "affected_symbols": ["BTC", "ETH", "SOL"],
        "influence_weight": 2.8,
        "manipulation_risk": 0.70,
        "known_for": "Binance exchange moves, market commentary",
    },
    "vitalik_buterin": {
        "name": "Vitalik Buterin",
        "search_terms": [
            "Vitalik Buterin ethereum statement today",
            "Vitalik crypto market opinion",
        ],
        "affected_symbols": ["ETH"],
        "influence_weight": 2.5,
        "manipulation_risk": 0.30,
        "known_for": "ETH development, Ethereum roadmap",
    },
    "arthur_hayes": {
        "name": "Arthur Hayes",
        "search_terms": [
            "Arthur Hayes bitcoin prediction crypto",
            "Arthur Hayes market call buy sell",
        ],
        "affected_symbols": ["BTC", "ETH"],
        "influence_weight": 1.8,
        "manipulation_risk": 0.60,
        "known_for": "crypto macro analysis, leveraged trading calls",
    },
    "cathie_wood": {
        "name": "Cathie Wood",
        "search_terms": [
            "Cathie Wood ARK bitcoin price target",
            "Cathie Wood crypto bullish prediction",
        ],
        "affected_symbols": ["BTC"],
        "influence_weight": 2.0,
        "manipulation_risk": 0.30,
        "known_for": "institutional crypto adoption, BTC price targets",
    },
    "raoul_pal": {
        "name": "Raoul Pal",
        "search_terms": [
            "Raoul Pal bitcoin ethereum macro prediction",
            "Real Vision crypto market outlook",
        ],
        "affected_symbols": ["BTC", "ETH", "SOL"],
        "influence_weight": 1.7,
        "manipulation_risk": 0.30,
        "known_for": "macro crypto analysis, Real Vision",
    },
    "trump_crypto": {
        "name": "Donald Trump",
        "search_terms": [
            "Trump bitcoin crypto executive order policy",
            "Trump World Liberty Financial crypto announcement",
        ],
        "affected_symbols": ["BTC", "ETH"],
        "influence_weight": 2.5,
        "manipulation_risk": 0.60,
        "known_for": "pro-crypto policies, TRUMP memecoin, executive orders",
    },
    "gary_gensler": {
        "name": "Gary Gensler / SEC",
        "search_terms": [
            "SEC crypto enforcement action lawsuit",
            "Gary Gensler bitcoin ethereum regulation statement",
        ],
        "affected_symbols": ["BTC", "ETH", "SOL"],
        "influence_weight": 2.2,
        "manipulation_risk": 0.40,
        "known_for": "regulatory FUD, SEC enforcement",
        "sentiment_bias": "negative",  # SEC news typically bearish
    },
    "anthony_pompliano": {
        "name": "Anthony Pompliano",
        "search_terms": [
            "Pompliano bitcoin buy accumulate prediction",
            "Anthony Pompliano crypto statement today",
        ],
        "affected_symbols": ["BTC"],
        "influence_weight": 1.5,
        "manipulation_risk": 0.40,
        "known_for": "BTC maximalism, media appearances",
    },
    "blackrock_etf": {
        "name": "BlackRock / Fidelity ETF",
        "search_terms": [
            "BlackRock bitcoin ETF inflows record",
            "Fidelity IBIT bitcoin spot ETF news today",
        ],
        "affected_symbols": ["BTC", "ETH"],
        "influence_weight": 2.6,
        "manipulation_risk": 0.20,
        "known_for": "institutional ETF flows, IBIT inflow records",
    },
}

# ---------------------------------------------------------------------------
# Keyword scoring tables
# ---------------------------------------------------------------------------
_PUMP_KEYWORDS: Dict[str, float] = {
    "to the moon": 3.0,
    "moon": 1.5,
    "buy the dip": 2.5,
    "accumulating": 2.0,
    "loading up": 2.5,
    "buying more": 2.0,
    "bullish": 1.5,
    "all in": 2.0,
    "going higher": 1.5,
    "buy now": 2.5,
    "100x": 2.0,
    "gem": 1.5,
    "undervalued": 1.5,
    "hodl": 1.0,
    "pump": 1.5,
    "massive gains": 2.0,
    "price target": 1.5,
    "new ath": 2.0,
    "breakout": 1.5,
    "parabolic": 2.0,
    "double": 1.5,
    "rally": 1.5,
    "surge": 1.5,
    "accumulate": 2.0,
    "bought": 1.5,
    "buying": 1.5,
    "long": 1.0,
    "upside": 1.5,
    "institutional": 1.5,
    "inflow": 1.5,
    "record inflows": 2.0,
    "adoption": 1.5,
    "endorses": 2.0,
    "supports": 1.0,
    "partnership": 1.5,
    "approved": 2.0,
    "strategic reserve": 2.5,
}

_DUMP_KEYWORDS: Dict[str, float] = {
    "taking profits": 2.5,
    "sold my": 2.5,
    "selling": 1.5,
    "exit": 1.5,
    "overvalued": 1.5,
    "bubble": 2.0,
    "crash": 1.5,
    "dump": 2.0,
    "cashing out": 2.5,
    "reduce exposure": 2.0,
    "bearish": 1.5,
    "short": 1.5,
    "heading down": 1.5,
    "get out": 2.0,
    "sell signal": 2.5,
    "distribution": 2.0,
    "overbought": 1.5,
    "sold": 1.5,
    "outflow": 1.5,
    "redemption": 1.5,
    "withdrawals": 1.5,
    "liquidation": 2.0,
}

_FUD_KEYWORDS: Dict[str, float] = {
    "scam": 2.0,
    "fraud": 2.0,
    "hack": 2.0,
    "exploit": 1.5,
    "insolvency": 2.5,
    "bankrupt": 2.5,
    "sec charges": 2.5,
    "charges": 1.5,
    "ban": 2.0,
    "illegal": 1.5,
    "ponzi": 2.5,
    "warning": 1.0,
    "collapse": 2.0,
    "rug pull": 3.0,
    "delisted": 2.0,
    "arrested": 2.5,
    "indicted": 2.5,
    "manipulation": 1.5,
    "probe": 1.5,
    "investigation": 1.5,
    "crackdown": 2.0,
    "restrict": 1.5,
    "suspend": 2.0,
}

# ---------------------------------------------------------------------------
# Cache state
# ---------------------------------------------------------------------------
_INFLUENCER_CACHE: Optional[dict] = None
_INFLUENCER_CACHE_TS: float = 0.0


def _score_text(text: str) -> dict:
    """Return pump/dump/fud scores for a piece of text."""
    raw = text.lower()
    pump = sum(w for kw, w in _PUMP_KEYWORDS.items() if kw in raw)
    dump = sum(w for kw, w in _DUMP_KEYWORDS.items() if kw in raw)
    fud = sum(w for kw, w in _FUD_KEYWORDS.items() if kw in raw)
    return {"pump": pump, "dump": dump, "fud": fud}


def _search_brave(query: str, api_key: str) -> List[str]:
    """Return a list of title+description snippets from Brave Search."""
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 8, "freshness": "pd"},  # pd = past day
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            f"{r.get('title', '')} {r.get('description', '')}"
            for r in results[:8]
        ]
    except Exception:
        return []


def _detect_symbol_mentions(text: str, candidates: List[str]) -> List[str]:
    """Return which candidate symbols are explicitly mentioned in text."""
    raw = text.lower()
    mentioned = []
    _SYMBOL_ALIASES = {
        "BTC": ["bitcoin", "btc", "xbt"],
        "ETH": ["ethereum", "eth", "ether"],
        "SOL": ["solana", "sol"],
        "DOGE": ["dogecoin", "doge"],
        "BNB": ["bnb", "binance coin"],
        "ADA": ["cardano", "ada"],
        "XRP": ["xrp", "ripple"],
        "AVAX": ["avalanche", "avax"],
        "MATIC": ["polygon", "matic"],
        "LINK": ["chainlink", "link"],
    }
    for sym in candidates:
        aliases = _SYMBOL_ALIASES.get(sym, [sym.lower()])
        if any(alias in raw for alias in aliases):
            mentioned.append(sym)
    # If none explicitly mentioned, assume all affected symbols
    return mentioned or candidates


def monitor_influencers(
    api_key: str,
    cache_ttl_seconds: int = 900,
) -> dict:
    """
    Search for recent influencer activity and return per-symbol manipulation signals.

    Returns a dict with structure:
    {
      "by_symbol": {
        "BTC": {
          "pump_score": float,
          "dump_score": float,
          "fud_score": float,
          "net_signal": float,       # pump_score - dump_score - fud_score
          "manipulation_score": float, # net_signal * max(influence_weight of contributors)
          "manipulation_flag": bool,
          "coordination": bool,      # 2+ influencers same direction
          "top_influencers": list,
          "sample_headlines": list,
        },
        ...
      },
      "global": {
        "dominant_signal": str,      # "pump" | "dump" | "fud" | "neutral"
        "manipulation_detected": bool,
        "coordination_count": int,
        "influencer_count": int,
      },
      "cached_at": float,
    }
    """
    global _INFLUENCER_CACHE, _INFLUENCER_CACHE_TS

    now = time.time()
    if _INFLUENCER_CACHE and now - _INFLUENCER_CACHE_TS < cache_ttl_seconds:
        return _INFLUENCER_CACHE

    if not api_key:
        return _empty_result()

    # Aggregate raw scores per symbol
    # symbol → list of {influencer_id, name, pump, dump, fud, weight, manip_risk, headlines}
    symbol_hits: Dict[str, List[dict]] = {}

    for inf_id, inf in INFLUENCERS.items():
        agg_pump, agg_dump, agg_fud = 0.0, 0.0, 0.0
        headlines: List[str] = []

        bias = inf.get("sentiment_bias", "neutral")

        for query in inf["search_terms"]:
            snippets = _search_brave(query, api_key)
            for snippet in snippets:
                sc = _score_text(snippet)
                # Apply sentiment bias override for regulatory/enforcement figures
                if bias == "negative":
                    sc["fud"] += 0.5
                    sc["dump"] += 0.3
                agg_pump += sc["pump"]
                agg_dump += sc["dump"]
                agg_fud += sc["fud"]
                if sc["pump"] > 1 or sc["dump"] > 1 or sc["fud"] > 1:
                    headlines.append(snippet[:160])

        if agg_pump < 0.5 and agg_dump < 0.5 and agg_fud < 0.5:
            continue  # No signal for this influencer right now

        weight = float(inf["influence_weight"])
        manip_risk = float(inf["manipulation_risk"])

        # Determine affected symbols from headlines + influencer profile
        all_text = " ".join(headlines)
        symbols = _detect_symbol_mentions(all_text, inf["affected_symbols"])

        for sym in symbols:
            if sym not in symbol_hits:
                symbol_hits[sym] = []
            symbol_hits[sym].append(
                {
                    "influencer_id": inf_id,
                    "name": inf["name"],
                    "pump": agg_pump * weight,
                    "dump": agg_dump * weight,
                    "fud": agg_fud * weight,
                    "weight": weight,
                    "manip_risk": manip_risk,
                    "headlines": headlines[:3],
                    "known_for": inf.get("known_for", ""),
                }
            )

    # Build per-symbol signals
    by_symbol: Dict[str, dict] = {}
    coordination_count = 0

    for sym, hits in symbol_hits.items():
        total_pump = sum(h["pump"] for h in hits)
        total_dump = sum(h["dump"] for h in hits)
        total_fud = sum(h["fud"] for h in hits)
        max_weight = max(h["weight"] for h in hits)
        max_manip_risk = max(h["manip_risk"] for h in hits)

        # Cap raw scores to prevent extreme outliers
        total_pump = min(total_pump, 20.0)
        total_dump = min(total_dump, 20.0)
        total_fud = min(total_fud, 20.0)

        net_signal = total_pump - total_dump - (total_fud * 0.8)

        # Coordination: 2+ influencers pushing same direction
        pump_contributors = sum(1 for h in hits if h["pump"] > h["dump"] and h["pump"] > 1.0)
        dump_contributors = sum(1 for h in hits if h["dump"] > h["pump"] and h["dump"] > 1.0)
        coordination = pump_contributors >= 2 or dump_contributors >= 2
        if coordination:
            coordination_count += 1
            # Amplify coordinated signals
            if pump_contributors >= 2:
                net_signal *= 1.5
            elif dump_contributors >= 2:
                net_signal *= 1.5

        manipulation_score = net_signal * max_manip_risk
        manipulation_flag = abs(manipulation_score) > 3.0

        top_influencers = sorted(
            [h["name"] for h in hits],
            key=lambda n: next((h["weight"] for h in hits if h["name"] == n), 0),
            reverse=True,
        )[:3]

        sample_headlines = []
        for h in hits:
            sample_headlines.extend(h["headlines"])
        sample_headlines = sample_headlines[:5]

        by_symbol[sym] = {
            "pump_score": round(total_pump, 2),
            "dump_score": round(total_dump, 2),
            "fud_score": round(total_fud, 2),
            "net_signal": round(net_signal, 2),
            "manipulation_score": round(manipulation_score, 2),
            "manipulation_flag": manipulation_flag,
            "coordination": coordination,
            "pump_contributors": pump_contributors,
            "dump_contributors": dump_contributors,
            "max_influencer_weight": round(max_weight, 2),
            "top_influencers": top_influencers,
            "sample_headlines": sample_headlines,
        }

    # Global summary
    all_net = [v["net_signal"] for v in by_symbol.values()]
    if all_net:
        avg_net = sum(all_net) / len(all_net)
        if avg_net > 2.0:
            dominant_signal = "pump"
        elif avg_net < -2.0:
            dominant_signal = "dump"
        elif any(v["fud_score"] > 3.0 for v in by_symbol.values()):
            dominant_signal = "fud"
        else:
            dominant_signal = "neutral"
    else:
        avg_net = 0.0
        dominant_signal = "neutral"

    manipulation_detected = any(v["manipulation_flag"] for v in by_symbol.values())

    result = {
        "by_symbol": by_symbol,
        "global": {
            "dominant_signal": dominant_signal,
            "manipulation_detected": manipulation_detected,
            "coordination_count": coordination_count,
            "influencer_count": len(symbol_hits),
            "avg_net_signal": round(avg_net, 2),
        },
        "cached_at": now,
    }

    _INFLUENCER_CACHE = result
    _INFLUENCER_CACHE_TS = now
    return result


def _empty_result() -> dict:
    return {
        "by_symbol": {},
        "global": {
            "dominant_signal": "neutral",
            "manipulation_detected": False,
            "coordination_count": 0,
            "influencer_count": 0,
            "avg_net_signal": 0.0,
        },
        "cached_at": time.time(),
    }


def get_symbol_signal(influencer_data: dict, symbol: str) -> dict:
    """
    Convenience helper: extract the signal for a specific ticker symbol.
    Accepts full symbol (e.g. 'BTC/USD') or base (e.g. 'BTC').
    Returns the per-symbol dict, or a neutral default if not found.
    """
    base = symbol.split("/")[0].upper()
    by_symbol = influencer_data.get("by_symbol", {})
    return by_symbol.get(
        base,
        {
            "pump_score": 0.0,
            "dump_score": 0.0,
            "fud_score": 0.0,
            "net_signal": 0.0,
            "manipulation_score": 0.0,
            "manipulation_flag": False,
            "coordination": False,
            "pump_contributors": 0,
            "dump_contributors": 0,
            "max_influencer_weight": 0.0,
            "top_influencers": [],
            "sample_headlines": [],
        },
    )
