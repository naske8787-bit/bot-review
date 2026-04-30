import math


def _clip(v, lo, hi):
    return max(lo, min(hi, float(v)))


def _safe_pct_change(series, periods):
    if len(series) <= periods:
        return 0.0
    base = float(series.iloc[-periods - 1])
    cur = float(series.iloc[-1])
    if abs(base) <= 1e-12:
        return 0.0
    return (cur / base) - 1.0


def _volatility_lookback(close, lookback=20):
    if len(close) < lookback + 2:
        return 0.0
    returns = close.pct_change().dropna().tail(lookback)
    if returns.empty:
        return 0.0
    return float(returns.std())


def detect_equity_regime(close, short_window=50, long_window=200):
    close = close.astype(float).dropna()
    if len(close) < 30:
        return {
            "label": "unknown",
            "confidence": 0.0,
            "favorable": True,
            "risk_multiplier": 0.8,
            "entry_threshold_multiplier": 1.2,
            "allow_new_entries": True,
        }

    short_window = max(5, min(int(short_window), len(close)))
    long_window = max(short_window + 1, min(int(long_window), len(close)))

    last = float(close.iloc[-1])
    short_ma = float(close.tail(short_window).mean())
    long_ma = float(close.tail(long_window).mean())
    trend_ratio = (short_ma - long_ma) / max(abs(long_ma), 1e-9)
    ret_20 = _safe_pct_change(close, 20)
    vol_20 = _volatility_lookback(close, lookback=20)

    label = "transitional"
    confidence = 0.4
    if vol_20 >= 0.035:
        label = "high_volatility"
        confidence = _clip(vol_20 / 0.06, 0.35, 0.98)
    elif trend_ratio >= 0.012 and ret_20 >= 0.0:
        label = "trend_up"
        confidence = _clip((abs(trend_ratio) / 0.03 + abs(ret_20) / 0.08) * 0.5, 0.35, 0.98)
    elif trend_ratio <= -0.012 and ret_20 <= 0.0:
        label = "trend_down"
        confidence = _clip((abs(trend_ratio) / 0.03 + abs(ret_20) / 0.08) * 0.5, 0.35, 0.98)
    elif abs(trend_ratio) <= 0.006 and abs(ret_20) <= 0.03:
        label = "range_bound"
        base = (1.0 - (abs(trend_ratio) / 0.006)) * 0.6 + (1.0 - (abs(ret_20) / 0.03)) * 0.4
        confidence = _clip(base, 0.30, 0.9)

    profile = {
        "trend_up": {
            "risk_multiplier": 1.0,
            "entry_threshold_multiplier": 0.9,
            "allow_new_entries": True,
            "favorable": True,
        },
        "range_bound": {
            "risk_multiplier": 0.85,
            "entry_threshold_multiplier": 1.1,
            "allow_new_entries": True,
            "favorable": True,
        },
        "trend_down": {
            "risk_multiplier": 0.55,
            "entry_threshold_multiplier": 1.4,
            "allow_new_entries": False,
            "favorable": False,
        },
        "high_volatility": {
            "risk_multiplier": 0.50,
            "entry_threshold_multiplier": 1.35,
            "allow_new_entries": False,
            "favorable": False,
        },
        "transitional": {
            "risk_multiplier": 0.70,
            "entry_threshold_multiplier": 1.2,
            "allow_new_entries": True,
            "favorable": False,
        },
        "unknown": {
            "risk_multiplier": 0.8,
            "entry_threshold_multiplier": 1.2,
            "allow_new_entries": True,
            "favorable": True,
        },
    }[label]

    return {
        "label": label,
        "confidence": float(confidence),
        "current_price": last,
        "short_ma": short_ma,
        "long_ma": long_ma,
        "trend_ratio": float(trend_ratio),
        "return_20d": float(ret_20),
        "volatility_20d": float(vol_20),
        **profile,
    }


def detect_crypto_regime(close, atr_pct=None, ema_fast=None, ema_slow=None):
    close = close.astype(float).dropna()
    if len(close) < 30:
        return {
            "label": "unknown",
            "confidence": 0.0,
            "risk_multiplier": 0.7,
            "entry_threshold_multiplier": 1.2,
            "allow_new_entries": True,
        }

    ret_20 = _safe_pct_change(close, 20)
    vol_20 = _volatility_lookback(close, lookback=20)

    if ema_fast is not None and ema_slow is not None and abs(float(ema_slow)) > 1e-9:
        trend_ratio = (float(ema_fast) - float(ema_slow)) / max(abs(float(ema_slow)), 1e-9)
    else:
        fast_ma = float(close.tail(15).mean())
        slow_ma = float(close.tail(45).mean())
        trend_ratio = (fast_ma - slow_ma) / max(abs(slow_ma), 1e-9)

    atr_pct = float(atr_pct) if atr_pct is not None else 0.0

    label = "transitional"
    confidence = 0.4
    if max(vol_20, atr_pct) >= 0.045:
        label = "high_volatility"
        confidence = _clip(max(vol_20 / 0.08, atr_pct / 0.09), 0.35, 0.98)
    elif trend_ratio >= 0.015 and ret_20 >= 0.0:
        label = "trend_up"
        confidence = _clip((abs(trend_ratio) / 0.04 + abs(ret_20) / 0.12) * 0.5, 0.35, 0.98)
    elif trend_ratio <= -0.015 and ret_20 <= 0.0:
        label = "trend_down"
        confidence = _clip((abs(trend_ratio) / 0.04 + abs(ret_20) / 0.12) * 0.5, 0.35, 0.98)
    elif abs(trend_ratio) <= 0.008 and abs(ret_20) <= 0.05:
        label = "range_bound"
        base = (1.0 - abs(trend_ratio) / 0.008) * 0.6 + (1.0 - abs(ret_20) / 0.05) * 0.4
        confidence = _clip(base, 0.30, 0.9)

    profile = {
        "trend_up": {
            "risk_multiplier": 1.0,
            "entry_threshold_multiplier": 0.95,
            "allow_new_entries": True,
        },
        "range_bound": {
            "risk_multiplier": 0.8,
            "entry_threshold_multiplier": 1.15,
            "allow_new_entries": True,
        },
        "trend_down": {
            "risk_multiplier": 0.5,
            "entry_threshold_multiplier": 1.4,
            "allow_new_entries": False,
        },
        "high_volatility": {
            "risk_multiplier": 0.45,
            "entry_threshold_multiplier": 1.5,
            "allow_new_entries": False,
        },
        "transitional": {
            "risk_multiplier": 0.65,
            "entry_threshold_multiplier": 1.2,
            "allow_new_entries": True,
        },
        "unknown": {
            "risk_multiplier": 0.7,
            "entry_threshold_multiplier": 1.2,
            "allow_new_entries": True,
        },
    }[label]

    return {
        "label": label,
        "confidence": float(confidence),
        "trend_ratio": float(trend_ratio),
        "return_20d": float(ret_20),
        "volatility_20d": float(vol_20),
        "atr_pct": float(atr_pct),
        **profile,
    }