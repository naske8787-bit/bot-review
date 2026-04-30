"""Trading strategy for the India bot.

Uses a combination of:
  - EMA crossover (9/21 EMA) for trend direction
  - RSI (14) for momentum confirmation
  - MACD for additional confirmation
  - Market regime filter using NIFTY 50

No pre-trained ML model is required — pure technical analysis.
"""

import time
import os

import pandas as pd

from config import (
    BUY_THRESHOLD_PCT,
    EVENT_BOOTSTRAP_ENABLED,
    EVENT_BOOTSTRAP_INTERVAL,
    EVENT_BOOTSTRAP_MIN_OBSERVATIONS,
    EVENT_BOOTSTRAP_YEARS,
    EVENT_LEARNER_ALPHA,
    EVENT_LEARNER_LAGS,
    EVENT_MAX_EDGE_ADJUSTMENT_PCT,
    MARKET_REGIME_LONG_WINDOW,
    MARKET_REGIME_SHORT_WINDOW,
    MARKET_REGIME_SYMBOL,
    MAX_POSITIONS,
    RISK_PER_TRADE,
    SELL_THRESHOLD_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRADE_COOLDOWN_MINUTES,
)
from data_fetcher import fetch_realtime_price, fetch_stock_data, preprocess_data
from event_learner import EventImpactLearner

# RSI / EMA config
RSI_PERIOD = 14
EMA_SHORT = 9
EMA_LONG = 21
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _macd(series: pd.Series):
    fast = _ema(series, MACD_FAST)
    slow = _ema(series, MACD_SLOW)
    macd_line = fast - slow
    signal_line = _ema(macd_line, MACD_SIGNAL)
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


class TradingStrategy:
    def __init__(self):
        self.positions: dict = {}
        self.last_analysis: dict = {}
        self.last_trade_times: dict = {}
        self._market_state_cache = None
        self._market_state_ts = 0.0
        self._historical_bootstrap_attempted = set()
        learner_state_path = os.path.join(os.path.dirname(__file__), "models", "event_impact_state.json")
        self.event_learner = EventImpactLearner(
            storage_path=learner_state_path,
            alpha=EVENT_LEARNER_ALPHA,
            max_adjustment_abs=EVENT_MAX_EDGE_ADJUSTMENT_PCT / 100.0,
            lags=EVENT_LEARNER_LAGS,
        )

    @staticmethod
    def _safe_return(close_series: pd.Series, periods: int) -> float:
        if len(close_series) <= periods:
            return 0.0
        base = float(close_series.iloc[-(periods + 1)])
        if base <= 0:
            return 0.0
        return (float(close_series.iloc[-1]) - base) / base

    @staticmethod
    def _clip(value: float, lo: float = -3.0, hi: float = 3.0) -> float:
        return max(lo, min(hi, float(value)))

    def _build_topic_scores_from_closes(self, symbol_close: pd.Series, spx_close: pd.Series, rates_close: pd.Series, ndx_close: pd.Series, oil_close: pd.Series, gold_close: pd.Series, symbol: str) -> dict:
        sym_ret_1 = self._safe_return(symbol_close, 1)
        sym_ret_3 = self._safe_return(symbol_close, 3)
        sym_ret_12 = self._safe_return(symbol_close, 12)
        spx_ret_12 = self._safe_return(spx_close, 12)
        ndx_ret_12 = self._safe_return(ndx_close, 12)
        rates_ret_3 = self._safe_return(rates_close, 3)
        oil_ret_6 = self._safe_return(oil_close, 6)
        gold_ret_6 = self._safe_return(gold_close, 6)

        rolling_vol = float(symbol_close.pct_change().tail(6).std() or 0.0)
        rolling_drawdown = float((symbol_close.iloc[-1] / max(symbol_close.tail(12))) - 1.0)
        ndx_rel_spx = ndx_ret_12 - spx_ret_12
        sym_rel_spx = sym_ret_12 - spx_ret_12

        tech_base = 0.2 if symbol.upper() in {"INFY", "TCS", "WIPRO", "HCLTECH", "TECHM"} else 0.0
        inflation_proxy = (oil_ret_6 + gold_ret_6) / 2.0

        return {
            "technology": self._clip((2.8 * ndx_rel_spx) + (1.8 * sym_rel_spx) + tech_base),
            "rates": self._clip(-8.0 * rates_ret_3),
            "inflation": self._clip(6.0 * inflation_proxy),
            "energy": self._clip(6.0 * oil_ret_6),
            "earnings": self._clip(6.0 * sym_rel_spx),
            "geopolitics": self._clip((8.0 * oil_ret_6) + (10.0 * rolling_vol) - (4.0 * sym_ret_1)),
            "regulation": self._clip((-5.0 * sym_ret_3) + (10.0 * min(0.0, rolling_drawdown))),
            "supply_chain": self._clip((5.0 * oil_ret_6) + (4.0 * rolling_vol)),
        }

    def _build_historical_observations(self, symbol: str):
        years = max(1, int(EVENT_BOOTSTRAP_YEARS))
        interval = EVENT_BOOTSTRAP_INTERVAL or "1mo"
        period = f"{years}y"

        symbol_df = fetch_stock_data(symbol, period=period, interval=interval, use_cache=False)
        spx_df = fetch_stock_data("^GSPC", period=period, interval=interval, use_cache=False)
        rates_df = fetch_stock_data("^TNX", period=period, interval=interval, use_cache=False)
        ndx_df = fetch_stock_data("^IXIC", period=period, interval=interval, use_cache=False)
        oil_df = fetch_stock_data("CL=F", period=period, interval=interval, use_cache=False)
        gold_df = fetch_stock_data("GC=F", period=period, interval=interval, use_cache=False)

        if symbol_df.empty or spx_df.empty:
            return []

        merged = pd.DataFrame(index=symbol_df.index)
        merged["symbol_close"] = symbol_df["Close"].astype(float)
        merged["spx_close"] = spx_df["Close"].astype(float)
        merged["rates_close"] = rates_df["Close"].astype(float) if not rates_df.empty else 0.0
        merged["ndx_close"] = ndx_df["Close"].astype(float) if not ndx_df.empty else merged["spx_close"]
        merged["oil_close"] = oil_df["Close"].astype(float) if not oil_df.empty else merged["spx_close"]
        merged["gold_close"] = gold_df["Close"].astype(float) if not gold_df.empty else merged["spx_close"]
        merged = merged.ffill().dropna()

        observations = []
        for i in range(24, len(merged)):
            window = merged.iloc[: i + 1]
            topics = self._build_topic_scores_from_closes(
                window["symbol_close"],
                window["spx_close"],
                window["rates_close"],
                window["ndx_close"],
                window["oil_close"],
                window["gold_close"],
                symbol,
            )
            observations.append({"price": float(window["symbol_close"].iloc[-1]), "topic_scores": topics})

        return observations

    def _bootstrap_symbol_history_if_needed(self, symbol: str):
        symbol = symbol.upper()
        if not EVENT_BOOTSTRAP_ENABLED:
            return
        if symbol in self._historical_bootstrap_attempted:
            return
        if self.event_learner.is_bootstrap_completed(symbol):
            self._historical_bootstrap_attempted.add(symbol)
            return

        self._historical_bootstrap_attempted.add(symbol)
        try:
            observations = self._build_historical_observations(symbol)
            if len(observations) < max(10, EVENT_BOOTSTRAP_MIN_OBSERVATIONS):
                print(
                    f"[Strategy] Historical bootstrap skipped for {symbol}: "
                    f"{len(observations)} observations (target={EVENT_BOOTSTRAP_MIN_OBSERVATIONS})."
                )
                return
            consumed = self.event_learner.bootstrap_symbol_history(symbol, observations)
            print(
                f"[Strategy] Historical bootstrap complete for {symbol}: "
                f"{consumed} observations (up to {EVENT_BOOTSTRAP_YEARS}y, interval={EVENT_BOOTSTRAP_INTERVAL})."
            )
        except Exception as e:
            print(f"[Strategy] Historical bootstrap failed for {symbol}: {e}")

    def _current_topic_scores(self, symbol: str) -> dict:
        symbol_df = fetch_stock_data(symbol, period="1y", interval="1d", use_cache=True)
        spx_df = fetch_stock_data("^GSPC", period="1y", interval="1d", use_cache=True)
        rates_df = fetch_stock_data("^TNX", period="1y", interval="1d", use_cache=True)
        ndx_df = fetch_stock_data("^IXIC", period="1y", interval="1d", use_cache=True)
        oil_df = fetch_stock_data("CL=F", period="1y", interval="1d", use_cache=True)
        gold_df = fetch_stock_data("GC=F", period="1y", interval="1d", use_cache=True)

        if symbol_df.empty or spx_df.empty:
            return {}

        return self._build_topic_scores_from_closes(
            symbol_df["Close"].astype(float),
            spx_df["Close"].astype(float),
            rates_df["Close"].astype(float) if not rates_df.empty else spx_df["Close"].astype(float),
            ndx_df["Close"].astype(float) if not ndx_df.empty else spx_df["Close"].astype(float),
            oil_df["Close"].astype(float) if not oil_df.empty else spx_df["Close"].astype(float),
            gold_df["Close"].astype(float) if not gold_df.empty else spx_df["Close"].astype(float),
            symbol,
        )

    def _in_cooldown(self, symbol: str) -> tuple[bool, float]:
        cooldown_secs = max(0, TRADE_COOLDOWN_MINUTES) * 60
        last_ts = self.last_trade_times.get(symbol.upper(), 0.0)
        remaining = max(0.0, cooldown_secs - (time.time() - last_ts))
        return remaining > 0, remaining

    def _sync_position(self, symbol: str, broker=None):
        symbol = symbol.upper()
        if broker:
            pos = broker.get_position(symbol)
            if pos:
                prev = self.positions.get(symbol, {})
                pos["entry_price"] = float(pos.get("entry_price") or prev.get("entry_price") or 0.0)
                self.positions[symbol] = pos
                return pos
            self.positions.pop(symbol, None)
        return self.positions.get(symbol)

    def _get_market_regime(self) -> dict:
        now = time.time()
        if self._market_state_cache and now - self._market_state_ts < 300:
            return self._market_state_cache

        try:
            data = preprocess_data(fetch_stock_data(MARKET_REGIME_SYMBOL, period="1y"))
            close = data["Close"].astype(float)
            short_ma = float(close.tail(min(MARKET_REGIME_SHORT_WINDOW, len(close))).mean())
            long_ma = float(close.tail(min(MARKET_REGIME_LONG_WINDOW, len(close))).mean())
            current_price = float(close.iloc[-1])
            favorable = current_price >= short_ma and short_ma >= long_ma * 0.995
            self._market_state_cache = {
                "current_price": current_price,
                "short_ma": short_ma,
                "long_ma": long_ma,
                "favorable": favorable,
            }
        except Exception as e:
            self._market_state_cache = {"favorable": True, "error": str(e)}

        self._market_state_ts = now
        return self._market_state_cache

    def analyze_signal(self, symbol: str, broker=None) -> str:
        symbol = symbol.upper()
        self._bootstrap_symbol_history_if_needed(symbol)
        try:
            data = preprocess_data(fetch_stock_data(symbol, period="1y"))
            if len(data) < MACD_SLOW + MACD_SIGNAL + 5:
                self.last_analysis[symbol] = {"reason": "not_enough_data"}
                return "HOLD"

            close = data["Close"].astype(float)
            current_price = float(close.iloc[-1])

            # Indicators
            ema_short_val = float(_ema(close, EMA_SHORT).iloc[-1])
            ema_long_val = float(_ema(close, EMA_LONG).iloc[-1])
            rsi_val = _rsi(close)
            macd_line, macd_sig, macd_hist = _macd(close)
            recent_return = float(close.pct_change(5).fillna(0.0).iloc[-1])

            topic_scores = self._current_topic_scores(symbol)
            self.event_learner.observe(symbol, current_price, topic_scores)
            learned_edge_adjustment_pct = self.event_learner.get_edge_adjustment(symbol, topic_scores) * 100.0

            ema_edge_pct = ((ema_short_val - ema_long_val) / max(current_price, 1e-9)) * 100.0
            macd_edge_pct = (macd_hist / max(current_price, 1e-9)) * 100.0 * 50.0
            momentum_edge_pct = recent_return * 100.0
            base_edge_pct = (0.5 * ema_edge_pct) + (0.3 * macd_edge_pct) + (0.2 * momentum_edge_pct)
            effective_edge_pct = base_edge_pct + learned_edge_adjustment_pct

            ema_bullish = ema_short_val > ema_long_val
            rsi_oversold = rsi_val < 35
            rsi_overbought = rsi_val > 70
            macd_bullish = macd_hist > 0

            market = self._get_market_regime()
            in_cooldown, cooldown_remaining = self._in_cooldown(symbol)
            position = self._sync_position(symbol, broker)

            self.last_analysis[symbol] = {
                "current_price": current_price,
                "ema_short": ema_short_val,
                "ema_long": ema_long_val,
                "rsi": rsi_val,
                "macd_line": macd_line,
                "macd_signal": macd_sig,
                "macd_histogram": macd_hist,
                "recent_return_pct": recent_return * 100,
                "base_edge_pct": base_edge_pct,
                "learned_edge_adjustment_pct": learned_edge_adjustment_pct,
                "effective_edge_pct": effective_edge_pct,
                "market_favorable": bool(market.get("favorable", True)),
                "has_position": bool(position),
                "cooldown_remaining_minutes": cooldown_remaining / 60,
            }

            # --- Exit logic ---
            if position:
                entry_price = float(position.get("entry_price") or current_price)
                if entry_price <= 0:
                    entry_price = current_price
                stop_loss_price = entry_price * (1 - STOP_LOSS_PCT)
                take_profit_price = entry_price * (1 + TAKE_PROFIT_PCT)
                self.last_analysis[symbol].update(
                    {"entry_price": entry_price, "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price}
                )

                if current_price <= stop_loss_price:
                    return "SELL"
                if current_price >= take_profit_price:
                    if rsi_overbought or not macd_bullish:
                        return "SELL"
                if not ema_bullish and rsi_overbought:
                    return "SELL"
                if effective_edge_pct <= -(SELL_THRESHOLD_PCT * 100) and not macd_bullish:
                    return "SELL"
                return "HOLD"

            # --- Entry logic ---
            if in_cooldown:
                return "HOLD"

            open_count = broker.get_open_positions_count() if broker else len(self.positions)
            if open_count >= MAX_POSITIONS:
                return "HOLD"

            if not market.get("favorable", True):
                return "HOLD"

            # Buy when EMA crossover up + MACD bullish + RSI not overbought
            if ema_bullish and macd_bullish and not rsi_overbought:
                if effective_edge_pct >= (BUY_THRESHOLD_PCT * 100) and (rsi_oversold or recent_return >= BUY_THRESHOLD_PCT):
                    return "BUY"

            return "HOLD"

        except Exception as e:
            print(f"[Strategy] Error analyzing {symbol}: {e}")
            self.last_analysis[symbol] = {"error": str(e)}
            return "HOLD"

    def execute_trade(self, signal: str, symbol: str, broker) -> dict | None:
        symbol = symbol.upper()
        if signal == "BUY":
            qty = broker.calculate_qty(symbol)
            broker.buy(symbol, qty)
            price = broker.get_current_price(symbol)
            self.last_trade_times[symbol] = time.time()
            return {"action": "BUY", "symbol": symbol, "qty": qty, "price": price}

        if signal == "SELL":
            qty = int(broker.get_position_size(symbol))
            if qty <= 0:
                return None
            broker.sell(symbol, qty)
            price = broker.get_current_price(symbol)
            self.last_trade_times[symbol] = time.time()
            return {"action": "SELL", "symbol": symbol, "qty": qty, "price": price}

        return None
