import json
import importlib.util
import math
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "shared"))
from setup_validator import evaluate_equity_setup, evaluate_crypto_setup


def _load_module(module_name, relative_path):
    file_path = os.path.join(ROOT, relative_path)
    module_dir = os.path.dirname(file_path)
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {module_name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == module_dir:
            sys.path.pop(0)
    return module


_previous_config = sys.modules.get("config")
trading_config = _load_module("setup_scorecard_trading_config", "trading_bot/config.py")
sys.modules["config"] = trading_config
trading_fetcher = _load_module("setup_scorecard_trading_fetcher", "trading_bot/data_fetcher.py")
crypto_config = _load_module("setup_scorecard_crypto_config", "crypto_bot/config.py")
sys.modules["config"] = crypto_config
crypto_fetcher = _load_module("setup_scorecard_crypto_fetcher", "crypto_bot/data_fetcher.py")
if _previous_config is not None:
    sys.modules["config"] = _previous_config
else:
    sys.modules.pop("config", None)

WATCHLIST = trading_config.WATCHLIST
ETF_SYMBOLS = trading_config.ETF_SYMBOLS
STOCK_BUY_THRESHOLD = trading_config.BUY_THRESHOLD_PCT
fetch_stock_data = trading_fetcher.fetch_stock_data
preprocess_stock_data = trading_fetcher.preprocess_data

CRYPTO_WATCHLIST = crypto_config.CRYPTO_WATCHLIST
CRYPTO_RSI_BUY_THRESHOLD = crypto_config.CRYPTO_RSI_BUY_THRESHOLD
CRYPTO_RSI_SELL_THRESHOLD = crypto_config.CRYPTO_RSI_SELL_THRESHOLD
CRYPTO_MACD_FAST = crypto_config.CRYPTO_MACD_FAST
CRYPTO_MACD_SLOW = crypto_config.CRYPTO_MACD_SLOW
CRYPTO_MACD_SIGNAL = crypto_config.CRYPTO_MACD_SIGNAL
fetch_crypto_data = crypto_fetcher.fetch_crypto_data
preprocess_crypto_data = crypto_fetcher.preprocess_data


def _score_row(expectancy, win_rate, sample_size, passed):
    sample_boost = min(1.5, math.log(max(2, sample_size), 10.0))
    base = (expectancy * 100.0 * 2.0) + ((win_rate - 0.5) * 100.0)
    score = base * sample_boost
    if passed:
        score += 10.0
    return round(score, 2)


def _stock_current_setup(close, current_price, short_trend, long_trend, recent_return, trend_strength):
    trend_confirmation = bool(trend_strength >= 0.004 and current_price > short_trend > long_trend)
    positive_momentum = bool(recent_return >= STOCK_BUY_THRESHOLD)
    if trend_confirmation and positive_momentum:
        return "trend_continuation"
    if current_price > long_trend and recent_return > -0.01:
        return "pullback_recovery"
    return None


def build_stock_scorecard(symbols):
    out = []
    for symbol in symbols:
        try:
            data = preprocess_stock_data(fetch_stock_data(symbol, period="2y", use_cache=False))
            if len(data) < 120:
                continue
            close = data["Close"].astype(float)
            current_price = float(close.iloc[-1])
            short_trend = float(close.tail(min(20, len(close))).mean())
            long_trend = float(close.tail(min(50, len(close))).mean())
            recent_return = float(close.pct_change(5).fillna(0.0).iloc[-1])
            trend_strength = (short_trend - long_trend) / max(abs(long_trend), 1e-9)
            current_setup = _stock_current_setup(close, current_price, short_trend, long_trend, recent_return, trend_strength)
            if symbol in ETF_SYMBOLS and current_setup == "trend_continuation":
                current_setup = "etf_momentum"
            validation = evaluate_equity_setup(close, current_setup=current_setup)
            out.append(
                {
                    "symbol": symbol,
                    "asset_class": "stock",
                    "setup": validation.get("setup", "none"),
                    "passed": bool(validation.get("passed", False)),
                    "sample_size": int(validation.get("sample_size", 0)),
                    "win_rate": float(validation.get("win_rate", 0.0)),
                    "avg_return": float(validation.get("avg_return", 0.0)),
                    "expectancy": float(validation.get("expectancy", 0.0)),
                    "score": _score_row(
                        expectancy=float(validation.get("expectancy", 0.0)),
                        win_rate=float(validation.get("win_rate", 0.0)),
                        sample_size=int(validation.get("sample_size", 0)),
                        passed=bool(validation.get("passed", False)),
                    ),
                    "trend_strength_pct": round(trend_strength * 100.0, 2),
                    "recent_return_pct": round(recent_return * 100.0, 2),
                }
            )
        except Exception as exc:
            out.append({"symbol": symbol, "asset_class": "stock", "error": str(exc)})
    return sorted(out, key=lambda row: float(row.get("score", -9999)), reverse=True)


def _compute_macd(close, fast, slow, signal):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def _crypto_current_setup(close, data):
    ema_fast = float(data["ema_fast"].iloc[-1])
    ema_slow = float(data["ema_slow"].iloc[-1])
    rsi = float(data["rsi"].iloc[-1])
    momentum_pct = float(data["momentum_pct"].iloc[-1])
    trend_strength = (ema_fast - ema_slow) / max(abs(ema_slow), 1e-9)
    macd_line, macd_signal, macd_hist = _compute_macd(close, CRYPTO_MACD_FAST, CRYPTO_MACD_SLOW, CRYPTO_MACD_SIGNAL)
    macd_bullish = macd_line > macd_signal and macd_hist > 0
    oversold_rebound = rsi <= CRYPTO_RSI_BUY_THRESHOLD and momentum_pct >= 0 and macd_bullish
    bullish_trend = trend_strength >= 0.002 and ema_fast > ema_slow
    trend_continuation = bullish_trend and 45 <= rsi <= CRYPTO_RSI_SELL_THRESHOLD and momentum_pct > 0 and macd_bullish
    if oversold_rebound:
        return "oversold_rebound", trend_strength, rsi, momentum_pct
    if trend_continuation:
        return "trend_continuation", trend_strength, rsi, momentum_pct
    if bullish_trend and macd_bullish and momentum_pct > 0.005:
        return "pattern_breakout", trend_strength, rsi, momentum_pct
    return None, trend_strength, rsi, momentum_pct


def build_crypto_scorecard(symbols):
    out = []
    for symbol in symbols:
        try:
            data = preprocess_crypto_data(fetch_crypto_data(symbol, period="180d", interval="1h"))
            if len(data) < 120:
                continue
            close = data["Close"].astype(float)
            setup, trend_strength, rsi, momentum_pct = _crypto_current_setup(close, data)
            validation = evaluate_crypto_setup(close, current_setup=setup, rsi_period=14)
            out.append(
                {
                    "symbol": symbol,
                    "asset_class": "crypto",
                    "setup": validation.get("setup", "none"),
                    "passed": bool(validation.get("passed", False)),
                    "sample_size": int(validation.get("sample_size", 0)),
                    "win_rate": float(validation.get("win_rate", 0.0)),
                    "avg_return": float(validation.get("avg_return", 0.0)),
                    "expectancy": float(validation.get("expectancy", 0.0)),
                    "score": _score_row(
                        expectancy=float(validation.get("expectancy", 0.0)),
                        win_rate=float(validation.get("win_rate", 0.0)),
                        sample_size=int(validation.get("sample_size", 0)),
                        passed=bool(validation.get("passed", False)),
                    ),
                    "trend_strength_pct": round(trend_strength * 100.0, 2),
                    "rsi": round(rsi, 2),
                    "momentum_pct": round(momentum_pct * 100.0, 2),
                }
            )
        except Exception as exc:
            out.append({"symbol": symbol, "asset_class": "crypto", "error": str(exc)})
    return sorted(out, key=lambda row: float(row.get("score", -9999)), reverse=True)


def main():
    stocks = build_stock_scorecard(WATCHLIST)
    crypto = build_crypto_scorecard(CRYPTO_WATCHLIST)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
        "crypto": crypto,
        "top_stock_candidates": [row for row in stocks if row.get("passed")][:5],
        "top_crypto_candidates": [row for row in crypto if row.get("passed")][:5],
    }
    out_path = os.path.join(ROOT, "scripts", ".setup_scorecard_latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print("=== Setup Scorecard ===")
    print(f"Generated: {payload['generated_at']}")
    print("Top stock setups:")
    for row in payload["top_stock_candidates"][:5]:
        print(
            f"  {row['symbol']}: setup={row['setup']} score={row['score']:.2f} "
            f"exp={row['expectancy']*100:.2f}% win={row['win_rate']*100:.1f}% n={row['sample_size']}"
        )
    if not payload["top_stock_candidates"]:
        print("  none")

    print("Top crypto setups:")
    for row in payload["top_crypto_candidates"][:5]:
        print(
            f"  {row['symbol']}: setup={row['setup']} score={row['score']:.2f} "
            f"exp={row['expectancy']*100:.2f}% win={row['win_rate']*100:.1f}% n={row['sample_size']}"
        )
    if not payload["top_crypto_candidates"]:
        print("  none")


if __name__ == "__main__":
    main()
