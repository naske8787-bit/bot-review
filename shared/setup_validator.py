import math
from typing import Dict, Optional

import pandas as pd


def _summarize_forward_returns(forward_returns: pd.Series, min_occurrences: int, min_avg_return: float, min_win_rate: float) -> Dict[str, float]:
    clean = pd.to_numeric(forward_returns, errors="coerce").dropna()
    sample_size = int(clean.shape[0])
    if sample_size == 0:
        return {
            "passed": False,
            "sample_size": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "expectancy": 0.0,
        }

    wins = clean[clean > 0]
    losses = clean[clean <= 0]
    avg_win = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = abs(float(losses.mean())) if not losses.empty else 0.0
    win_rate = float((clean > 0).mean())
    loss_rate = 1.0 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    avg_return = float(clean.mean())
    median_return = float(clean.median())
    passed = bool(
        sample_size >= int(min_occurrences)
        and avg_return >= float(min_avg_return)
        and win_rate >= float(min_win_rate)
        and expectancy > 0
    )
    return {
        "passed": passed,
        "sample_size": sample_size,
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_return, 4),
        "median_return": round(median_return, 4),
        "expectancy": round(expectancy, 4),
    }


def evaluate_equity_setup(
    close: pd.Series,
    current_setup: Optional[str],
    horizon_bars: int = 5,
    min_occurrences: int = 12,
    min_avg_return: float = 0.002,
    min_win_rate: float = 0.53,
) -> Dict[str, float]:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if current_setup is None or close.shape[0] < 120:
        return {
            "setup": current_setup or "none",
            "passed": False,
            "sample_size": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "expectancy": 0.0,
        }

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    momentum_5d = close.pct_change(5)
    pullback_3d = close.pct_change(3)
    forward_returns = close.shift(-horizon_bars) / close - 1.0

    masks = {
        "trend_continuation": (close > sma20) & (sma20 > sma50) & (momentum_5d > 0.003),
        "pullback_recovery": (close > sma50) & (pullback_3d.between(-0.03, -0.005)) & (momentum_5d > -0.01),
        "etf_momentum": (close > sma20) & (sma20 > sma50) & (momentum_5d > 0.001),
    }

    mask = masks.get(current_setup)
    if mask is None:
        return {
            "setup": current_setup,
            "passed": False,
            "sample_size": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "expectancy": 0.0,
        }

    summary = _summarize_forward_returns(
        forward_returns[mask],
        min_occurrences=min_occurrences,
        min_avg_return=min_avg_return,
        min_win_rate=min_win_rate,
    )
    summary["setup"] = current_setup
    return summary


def _compute_macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def evaluate_crypto_setup(
    close: pd.Series,
    current_setup: Optional[str],
    rsi_period: int = 14,
    horizon_bars: int = 12,
    min_occurrences: int = 15,
    min_avg_return: float = 0.003,
    min_win_rate: float = 0.54,
) -> Dict[str, float]:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if current_setup is None or close.shape[0] < 120:
        return {
            "setup": current_setup or "none",
            "passed": False,
            "sample_size": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "expectancy": 0.0,
        }

    ema_fast = close.ewm(span=10, adjust=False).mean()
    ema_slow = close.ewm(span=30, adjust=False).mean()
    momentum_3 = close.pct_change(3)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / max(rsi_period, 1), min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / max(rsi_period, 1), min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = (100 - (100 / (1 + rs))).fillna(50.0)
    macd_hist = _compute_macd_hist(close)
    forward_returns = close.shift(-horizon_bars) / close - 1.0

    masks = {
        "trend_continuation": (ema_fast > ema_slow) & (momentum_3 > 0.002) & (rsi.between(45, 68)) & (macd_hist > 0),
        "oversold_rebound": (rsi <= 40) & (momentum_3 >= 0) & (macd_hist > 0),
        "pattern_breakout": (ema_fast > ema_slow) & (momentum_3 > 0.005) & (macd_hist > 0),
    }

    mask = masks.get(current_setup)
    if mask is None:
        return {
            "setup": current_setup,
            "passed": False,
            "sample_size": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "expectancy": 0.0,
        }

    summary = _summarize_forward_returns(
        forward_returns[mask],
        min_occurrences=min_occurrences,
        min_avg_return=min_avg_return,
        min_win_rate=min_win_rate,
    )
    summary["setup"] = current_setup
    return summary
