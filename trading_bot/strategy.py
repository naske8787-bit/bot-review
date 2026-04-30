import time
import os
import json
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import (
    AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED,
    AUTO_IMPROVEMENT_LOOKBACK_DAYS,
    AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL,
    AUTO_IMPROVEMENT_REBALANCE_HOURS,
    ADAPTIVE_POLICY_DECAY,
    ADAPTIVE_POLICY_ENABLED,
    ADAPTIVE_POLICY_LEARNING_RATE,
    ADAPTIVE_POLICY_MAX_ADJUSTMENT_PCT,
    BUY_THRESHOLD_PCT,
    ETF_SYMBOLS,
    MARKET_REGIME_LONG_WINDOW,
    MARKET_REGIME_SHORT_WINDOW,
    MARKET_REGIME_SYMBOL,
    MAX_POSITIONS,
    MIN_SENTIMENT_TO_BUY,
    MIN_TREND_STRENGTH_PCT,
    RISK_PER_TRADE,
    SELL_THRESHOLD_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRADE_COOLDOWN_MINUTES,
    CAPITOL_DATA_MIN_CONFIDENCE_TO_TRADE,
    CAPITOL_DATA_LOW_CONFIDENCE_RISK_MULTIPLIER,
    EVENT_LEARNER_ALPHA,
    EVENT_MAX_EDGE_ADJUSTMENT_PCT,
    EVENT_LEARNER_LAGS,
    EVENT_BOOTSTRAP_ENABLED,
    EVENT_BOOTSTRAP_YEARS,
    EVENT_BOOTSTRAP_INTERVAL,
    EVENT_BOOTSTRAP_MIN_OBSERVATIONS,
    TECH_RESEARCH_FORCE_BUY_ENABLED,
    TECH_RESEARCH_FORCE_BUY_MIN_PROBABILITY,
    TECH_RESEARCH_FORCE_BUY_MIN_IMPACT_SCORE,
    TECH_RESEARCH_FORCE_BUY_MIN_EVIDENCE_COUNT,
    TECH_RESEARCH_FORCE_BUY_MAX_SIGNAL_AGE_HOURS,
    TECH_RESEARCH_FORCE_BUY_MAX_CANDIDATES,
    TECH_RESEARCH_FORCE_BUY_RISK_MULTIPLIER,
    FUNDAMENTALS_GATE_ENABLED,
    FUNDAMENTALS_MIN_SCORE,
    FUNDAMENTALS_MIN_MARKET_CAP_BILLION,
    FUNDAMENTALS_MAX_DEBT_TO_EQUITY,
    FUNDAMENTALS_REQUIRE_POSITIVE_FCF,
    LONG_TERM_MIN_HOLD_HOURS,
    LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT,
    LONG_TERM_MAX_TOTAL_EXPOSURE_PCT,
    LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT,
    LONG_HORIZON_CASH_BUFFER_PCT,
    LONG_HORIZON_ENABLED,
    LONG_HORIZON_MAX_RISK_PER_TRADE,
    LONG_HORIZON_MONTHLY_CONTRIBUTION,
    GROWTH_MOMENTUM_BUY_ENABLED,
    GROWTH_MOMENTUM_MIN_TREND_MULTIPLIER,
    GROWTH_MOMENTUM_MIN_RETURN_MULTIPLIER,
)
from data_fetcher import fetch_capitol_trades, fetch_stock_data, preprocess_data, fetch_vix_level, fetch_news_sentiment, fetch_sector_momentum
from data_fetcher import fetch_global_macro_sentiment
from data_fetcher import fetch_external_research_sentiment
from data_fetcher import get_capitol_data_health
from experience_policy import ExperiencePolicy
from event_learner import EventImpactLearner
from model import load_trained_model, predict_price
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "shared"))
from proven_patterns import score_conditions_against_patterns, build_equity_conditions
from setup_validator import evaluate_equity_setup
from regime_detector import detect_equity_regime
from fundamentals import evaluate_company_fundamentals
from long_term_policy import LongTermPolicy
from execution_quality import ExecutionQualityTracker as _ExecQualTracker
from promotion_pipeline import PromotionPipeline as _PromotionPipeline

_EXEC_LOG_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs", "execution_quality.jsonl")
_exec_tracker = _ExecQualTracker(_EXEC_LOG_PATH)
_PIPELINE_STATE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
_pipeline = _PromotionPipeline("trading", _PIPELINE_STATE_DIR)


class TradingStrategy:
    def __init__(self):
        self.positions = {}
        self.trade_history = []
        self.model_cache = {}
        self.last_analysis = {}
        self.last_trade_times = {}
        self.symbol_risk_multipliers = {}
        self.setup_rank_multipliers = {}
        self.drift_risk_multiplier = 1.0
        self.confidence_risk_multiplier = 1.0
        self.blocked_symbols_by_improvement = set()
        self.active_setup_candidates = set()
        self.last_improvement_rebalance_ts = None
        self.market_state_cache = None
        self.market_state_ts = 0.0
        self.tech_research_cache = None
        self.tech_research_cache_ts = 0.0
        self.tech_research_cache_mtime = 0.0
        self.tech_research_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tech_research_bot",
            "output",
            "latest_research.json",
        )
        self.research_symbol_aliases = {
            "AAPL": ["apple"],
            "MSFT": ["microsoft"],
            "NVDA": ["nvidia"],
            "GOOGL": ["google", "alphabet"],
            "TSLA": ["tesla"],
            "AMZN": ["amazon", "aws"],
            "META": ["meta", "facebook"],
            "AMD": ["amd", "advanced micro devices"],
            "PLTR": ["palantir"],
            "COIN": ["coinbase"],
        }
        self.autonomy_profile = {
            "mode": "normal",
            "allow_new_entries": True,
            "risk_multiplier": 1.0,
            "buy_threshold_multiplier": 1.0,
            "max_positions_multiplier": 1.0,
            "blocked_symbols": [],
            "score": 0,
        }
        self._historical_bootstrap_attempted = set()
        learner_state_path = os.path.join(os.path.dirname(__file__), "models", "event_impact_state.json")
        self.event_learner = EventImpactLearner(
            storage_path=learner_state_path,
            alpha=EVENT_LEARNER_ALPHA,
            max_adjustment_abs=EVENT_MAX_EDGE_ADJUSTMENT_PCT / 100.0,
            lags=EVENT_LEARNER_LAGS,
        )
        policy_state_path = os.path.join(os.path.dirname(__file__), "models", "experience_policy_state.json")
        self.experience_policy = ExperiencePolicy(
            storage_path=policy_state_path,
            enabled=ADAPTIVE_POLICY_ENABLED,
            learning_rate=ADAPTIVE_POLICY_LEARNING_RATE,
            decay=ADAPTIVE_POLICY_DECAY,
            max_adjustment_abs=ADAPTIVE_POLICY_MAX_ADJUSTMENT_PCT / 100.0,
        )
        self.long_term_policy = LongTermPolicy(
            bot_name="trading_bot",
            max_total_exposure_pct=LONG_TERM_MAX_TOTAL_EXPOSURE_PCT,
            max_symbol_exposure_pct=LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT,
            max_drawdown_pct=LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT,
        )

    def apply_autonomy_profile(self, profile):
        if isinstance(profile, dict):
            self.autonomy_profile.update(profile)

    def apply_setup_candidates(self, symbols):
        self.active_setup_candidates = {str(symbol).upper() for symbol in (symbols or []) if str(symbol).strip()}

    def apply_setup_rank_multipliers(self, multipliers):
        self.setup_rank_multipliers = {
            str(symbol).upper(): float(mult)
            for symbol, mult in (multipliers or {}).items()
            if str(symbol).strip()
        }

    def _symbol_trade_stats(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=AUTO_IMPROVEMENT_LOOKBACK_DAYS)
        by_symbol = {}
        for t in self.trade_history:
            if t["ts"] < cutoff:
                continue
            sym = str(t.get("symbol") or "").upper()
            if not sym:
                continue
            by_symbol.setdefault(sym, []).append(float(t.get("pnl") or 0.0))

        out = {}
        for sym, pnls in by_symbol.items():
            if not pnls:
                continue
            wins = [x for x in pnls if x > 0]
            losses = [x for x in pnls if x < 0]
            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
            out[sym] = {
                "trades": len(pnls),
                "expectancy": sum(pnls) / len(pnls),
                "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
                "profit_factor": profit_factor,
            }
        return out

    def auto_apply_improvements(self, force=False):
        if not AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED:
            return []

        now = datetime.now(timezone.utc)
        if not force and self.last_improvement_rebalance_ts is not None:
            elapsed_h = (now - self.last_improvement_rebalance_ts).total_seconds() / 3600.0
            if elapsed_h < max(1, AUTO_IMPROVEMENT_REBALANCE_HOURS):
                return []

        stats = self._symbol_trade_stats()
        if not stats:
            self.last_improvement_rebalance_ts = now
            return ["Auto-improvement: not enough closed-trade history yet."]

        eligible = [
            (sym, s) for sym, s in stats.items()
            if int(s.get("trades", 0)) >= AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL
        ]
        if not eligible:
            self.last_improvement_rebalance_ts = now
            return ["Auto-improvement: waiting for more closes per symbol before tuning."]

        ranked = sorted(
            eligible,
            key=lambda item: (
                float(item[1].get("expectancy", 0.0)),
                float(item[1].get("profit_factor", 0.0)),
                float(item[1].get("win_rate", 0.0)),
            ),
            reverse=True,
        )

        new_multipliers = {}
        new_blocked = set()
        n = len(ranked)
        for i, (sym, s) in enumerate(ranked):
            if i == 0:
                mult = 1.25
            elif i < max(1, n // 2):
                mult = 1.05
            elif i == n - 1:
                mult = 0.55
            else:
                mult = 0.80

            if float(s.get("expectancy", 0.0)) < 0 and float(s.get("profit_factor", 0.0)) < 0.9:
                new_blocked.add(sym)
                mult = 0.0

            new_multipliers[sym] = mult

        self.symbol_risk_multipliers = new_multipliers
        self.blocked_symbols_by_improvement = new_blocked
        self.last_improvement_rebalance_ts = now

        top = ", ".join([f"{sym} x{new_multipliers.get(sym, 1.0):.2f}" for sym, _ in ranked[:4]]) or "none"
        blocked = ", ".join(sorted(new_blocked)) or "none"
        return [
            "Auto-improvement applied (daily return-per-risk rebalance).",
            f"Top allocations: {top}",
            f"Underperformer cap/block list: {blocked}",
        ]

    @staticmethod
    def _safe_return(close_series, periods):
        if len(close_series) <= periods:
            return 0.0
        base = float(close_series.iloc[-(periods + 1)])
        if base <= 0:
            return 0.0
        return (float(close_series.iloc[-1]) - base) / base

    @staticmethod
    def _clip(value, lo=-3.0, hi=3.0):
        return max(lo, min(hi, float(value)))

    def _build_historical_observations(self, symbol):
        years = max(1, int(EVENT_BOOTSTRAP_YEARS))
        interval = EVENT_BOOTSTRAP_INTERVAL or "1mo"
        period = f"{years}y"

        symbol_df = preprocess_data(fetch_stock_data(symbol, period=period, interval=interval, use_cache=False))
        spx_df = preprocess_data(fetch_stock_data("^GSPC", period=period, interval=interval, use_cache=False))
        rates_df = preprocess_data(fetch_stock_data("^TNX", period=period, interval=interval, use_cache=False))
        ndx_df = preprocess_data(fetch_stock_data("^IXIC", period=period, interval=interval, use_cache=False))
        oil_df = preprocess_data(fetch_stock_data("CL=F", period=period, interval=interval, use_cache=False))
        gold_df = preprocess_data(fetch_stock_data("GC=F", period=period, interval=interval, use_cache=False))

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
        tech_symbols = {"AAPL", "MSFT", "NVDA", "GOOGL", "AMD", "META", "TSLA", "COIN", "PLTR"}

        for i in range(24, len(merged)):
            window = merged.iloc[: i + 1]

            sym_close = window["symbol_close"]
            spx_close = window["spx_close"]
            rates_close = window["rates_close"]
            ndx_close = window["ndx_close"]
            oil_close = window["oil_close"]
            gold_close = window["gold_close"]

            sym_ret_1 = self._safe_return(sym_close, 1)
            sym_ret_3 = self._safe_return(sym_close, 3)
            sym_ret_12 = self._safe_return(sym_close, 12)
            spx_ret_12 = self._safe_return(spx_close, 12)
            ndx_ret_12 = self._safe_return(ndx_close, 12)
            rates_ret_3 = self._safe_return(rates_close, 3)
            oil_ret_6 = self._safe_return(oil_close, 6)
            gold_ret_6 = self._safe_return(gold_close, 6)

            rolling_vol = float(sym_close.pct_change().tail(6).std() or 0.0)
            rolling_drawdown = float((sym_close.iloc[-1] / max(sym_close.tail(12))) - 1.0)
            ndx_rel_spx = ndx_ret_12 - spx_ret_12
            sym_rel_spx = sym_ret_12 - spx_ret_12

            tech_base = 0.25 if symbol in tech_symbols else 0.0
            inflation_proxy = (oil_ret_6 + gold_ret_6) / 2.0

            topic_scores = {
                "technology": self._clip((2.8 * ndx_rel_spx) + (1.8 * sym_rel_spx) + tech_base),
                "rates": self._clip(-8.0 * rates_ret_3),
                "inflation": self._clip(6.0 * inflation_proxy),
                "energy": self._clip(6.0 * oil_ret_6),
                "earnings": self._clip(6.0 * sym_rel_spx),
                "geopolitics": self._clip((8.0 * oil_ret_6) + (10.0 * rolling_vol) - (4.0 * sym_ret_1)),
                "regulation": self._clip((-5.0 * sym_ret_3) + (10.0 * min(0.0, rolling_drawdown))),
                "supply_chain": self._clip((5.0 * oil_ret_6) + (4.0 * rolling_vol)),
            }

            observations.append(
                {
                    "price": float(sym_close.iloc[-1]),
                    "topic_scores": topic_scores,
                }
            )

        return observations

    def _bootstrap_symbol_history_if_needed(self, symbol):
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
                    f"Historical bootstrap skipped for {symbol}: "
                    f"only {len(observations)} observations available "
                    f"(target={EVENT_BOOTSTRAP_MIN_OBSERVATIONS})."
                )
                return
            consumed = self.event_learner.bootstrap_symbol_history(symbol, observations)
            print(
                f"Historical bootstrap complete for {symbol}: "
                f"{consumed} observations replayed "
                f"(requested up to {EVENT_BOOTSTRAP_YEARS}y, interval={EVENT_BOOTSTRAP_INTERVAL})."
            )
        except Exception as e:
            print(f"Historical bootstrap failed for {symbol}: {e}")

    def _get_model_bundle(self, symbol):
        symbol = symbol.upper()
        if symbol not in self.model_cache:
            self.model_cache[symbol] = load_trained_model(symbol=symbol)
        return self.model_cache[symbol]

    @staticmethod
    def _calculate_sentiment(trades, symbol):
        symbol = symbol.upper()
        buy_signals = 0
        sell_signals = 0

        for trade in trades:
            if str(trade.get("symbol") or "").upper() != symbol:
                continue
            action = str(trade.get("action") or trade.get("trade_type") or "").lower()
            if "buy" in action or "purchase" in action:
                buy_signals += 1
            elif "sell" in action or "sale" in action:
                sell_signals += 1

        return buy_signals - sell_signals, buy_signals, sell_signals

    def _sync_position(self, symbol, broker=None):
        symbol = symbol.upper()
        if broker and hasattr(broker, "get_position"):
            broker_position = broker.get_position(symbol)
            if broker_position:
                previous = self.positions.get(symbol, {})
                entry_price = float(broker_position.get("entry_price") or previous.get("entry_price") or 0.0)
                broker_position["entry_price"] = entry_price
                if "entry_context" in previous and "entry_context" not in broker_position:
                    broker_position["entry_context"] = previous["entry_context"]
                if "entry_ts" in previous and "entry_ts" not in broker_position:
                    broker_position["entry_ts"] = previous["entry_ts"]
                self.positions[symbol] = broker_position
                return broker_position
            self.positions.pop(symbol, None)
        return self.positions.get(symbol)

    @staticmethod
    def _build_adaptive_context(
        predicted_change,
        trend_strength,
        sentiment,
        news_score,
        sector_tailwind,
        high_fear,
        market_favorable,
    ):
        return {
            "predicted_change": float(predicted_change),
            "trend_strength": float(trend_strength),
            "sentiment": float(sentiment),
            "news_score": float(news_score),
            "sector_tailwind": bool(sector_tailwind),
            "high_fear": bool(high_fear),
            "market_favorable": bool(market_favorable),
        }

    def _get_market_regime(self):
        now = time.time()
        if self.market_state_cache and now - self.market_state_ts < 300:
            return self.market_state_cache

        try:
            market_data = preprocess_data(fetch_stock_data(MARKET_REGIME_SYMBOL, period="1y"))
            close = market_data["Close"].astype(float)
            regime = detect_equity_regime(
                close,
                short_window=MARKET_REGIME_SHORT_WINDOW,
                long_window=MARKET_REGIME_LONG_WINDOW,
            )
            self.market_state_cache = {
                "symbol": MARKET_REGIME_SYMBOL,
                **regime,
            }
        except Exception as e:
            self.market_state_cache = {
                "symbol": MARKET_REGIME_SYMBOL,
                "favorable": True,
                "label": "unknown",
                "confidence": 0.0,
                "risk_multiplier": 0.8,
                "entry_threshold_multiplier": 1.2,
                "allow_new_entries": True,
                "error": str(e),
            }

        self.market_state_ts = now
        return self.market_state_cache

    def _in_cooldown(self, symbol):
        cooldown_seconds = max(0, TRADE_COOLDOWN_MINUTES) * 60
        last_trade_ts = self.last_trade_times.get(symbol.upper(), 0.0)
        remaining_seconds = max(0.0, cooldown_seconds - (time.time() - last_trade_ts))
        return remaining_seconds > 0, remaining_seconds

    @staticmethod
    def _safe_parse_iso(value):
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None

    def _load_tech_research_candidates(self):
        if not TECH_RESEARCH_FORCE_BUY_ENABLED:
            return []

        now = time.time()
        if (
            self.tech_research_cache is not None
            and (now - self.tech_research_cache_ts) < 300
        ):
            return self.tech_research_cache

        candidates = []
        try:
            if not os.path.exists(self.tech_research_path):
                self.tech_research_cache = []
                self.tech_research_cache_ts = now
                return []

            mtime = os.path.getmtime(self.tech_research_path)
            if self.tech_research_cache is not None and mtime == self.tech_research_cache_mtime:
                self.tech_research_cache_ts = now
                return self.tech_research_cache

            with open(self.tech_research_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            raw_candidates = payload.get("top_candidates") or []
            if isinstance(raw_candidates, list):
                candidates = [x for x in raw_candidates if isinstance(x, dict)]

            self.tech_research_cache = candidates
            self.tech_research_cache_mtime = mtime
            self.tech_research_cache_ts = now
            return candidates
        except Exception:
            self.tech_research_cache = []
            self.tech_research_cache_ts = now
            return []

    def _research_candidate_matches_symbol(self, symbol, candidate):
        symbol = str(symbol or "").upper()
        title = str(candidate.get("title") or "")
        text = title.lower()
        if not text:
            return False

        if re.search(rf"\b{re.escape(symbol)}\b", title, flags=re.IGNORECASE):
            return True

        for alias in self.research_symbol_aliases.get(symbol, []):
            if alias in text:
                return True
        return False

    def _research_force_buy_signal(self, symbol):
        if not TECH_RESEARCH_FORCE_BUY_ENABLED:
            return {"triggered": False}

        now_dt = datetime.now(timezone.utc)
        candidates = self._load_tech_research_candidates()[: max(1, int(TECH_RESEARCH_FORCE_BUY_MAX_CANDIDATES))]
        matched = []
        for cand in candidates:
            if not self._research_candidate_matches_symbol(symbol, cand):
                continue

            prob = float(cand.get("probability_significant_impact", 0.0) or 0.0)
            impact = float(cand.get("impact_score", 0.0) or 0.0)
            rationale = cand.get("rationale") or []
            if not isinstance(rationale, list):
                rationale = []

            evidence_count = len(rationale)
            if cand.get("link"):
                evidence_count += 1
            if cand.get("published") or cand.get("published_iso"):
                evidence_count += 1

            published_dt = self._safe_parse_iso(cand.get("published_iso") or cand.get("published"))
            age_hours = None
            if published_dt is not None:
                age_hours = (now_dt - published_dt).total_seconds() / 3600.0

            enough_prob = prob >= float(TECH_RESEARCH_FORCE_BUY_MIN_PROBABILITY)
            enough_impact = impact >= float(TECH_RESEARCH_FORCE_BUY_MIN_IMPACT_SCORE)
            enough_evidence = evidence_count >= int(TECH_RESEARCH_FORCE_BUY_MIN_EVIDENCE_COUNT)
            fresh_enough = (age_hours is None) or (age_hours <= float(TECH_RESEARCH_FORCE_BUY_MAX_SIGNAL_AGE_HOURS))

            if enough_prob and enough_impact and enough_evidence and fresh_enough:
                matched.append({
                    "title": str(cand.get("title") or ""),
                    "probability": prob,
                    "impact_score": impact,
                    "evidence_count": evidence_count,
                    "age_hours": age_hours,
                    "theme": str(cand.get("theme") or ""),
                })

        if not matched:
            return {"triggered": False}

        best = sorted(
            matched,
            key=lambda x: (x["probability"], x["impact_score"], x["evidence_count"]),
            reverse=True,
        )[0]
        return {
            "triggered": True,
            "title": best["title"],
            "probability": best["probability"],
            "impact_score": best["impact_score"],
            "evidence_count": best["evidence_count"],
            "age_hours": best["age_hours"],
            "theme": best["theme"],
        }

    def analyze_signal(self, symbol, broker=None):
        """Analyze buy/sell signals using ML prediction, sentiment, trend, and regime filters."""
        symbol = symbol.upper()
        self._bootstrap_symbol_history_if_needed(symbol)
        try:
            data = preprocess_data(fetch_stock_data(symbol, period="1y"))
            if len(data) < 60:
                self.last_analysis[symbol] = {"reason": "not_enough_data"}
                return "HOLD"

            model, scaler = self._get_model_bundle(symbol)
            close = data["Close"].astype(float)
            recent_prices = close.tail(60).to_numpy()
            predicted_price = predict_price(model, scaler, recent_prices)

            current_price = float(close.iloc[-1])
            predicted_change = (predicted_price - current_price) / current_price
            short_trend = float(close.tail(min(20, len(close))).mean())
            long_trend = float(close.tail(min(50, len(close))).mean())
            recent_return = float(close.pct_change(5).fillna(0.0).iloc[-1])
            trend_strength = (short_trend - long_trend) / max(abs(long_trend), 1e-9)

            trades = fetch_capitol_trades()
            data_health = get_capitol_data_health()
            sentiment, buy_signals, sell_signals = self._calculate_sentiment(trades, symbol)
            position = self._sync_position(symbol, broker)
            market_state = self._get_market_regime()
            regime_label = str(market_state.get("label") or "unknown")
            regime_confidence = float(market_state.get("confidence", 0.0) or 0.0)
            regime_entry_multiplier = float(market_state.get("entry_threshold_multiplier", 1.0) or 1.0)
            regime_risk_multiplier = float(market_state.get("risk_multiplier", 1.0) or 1.0)
            regime_allow_new_entries = bool(market_state.get("allow_new_entries", True))
            capitol_data_confidence = float(data_health.get("confidence", 0.0) or 0.0)
            capitol_data_source = str(data_health.get("source") or "unknown")
            capitol_data_degraded = bool(data_health.get("degraded", True))
            low_confidence_risk_mult = min(1.0, max(0.1, float(CAPITOL_DATA_LOW_CONFIDENCE_RISK_MULTIPLIER)))
            if capitol_data_confidence < CAPITOL_DATA_MIN_CONFIDENCE_TO_TRADE:
                data_confidence_risk_multiplier = low_confidence_risk_mult
            elif capitol_data_confidence < 0.75:
                # Partial degradation still cuts size while allowing selective entries.
                data_confidence_risk_multiplier = 0.80
            else:
                data_confidence_risk_multiplier = 1.0
            regime_risk_multiplier *= data_confidence_risk_multiplier
            in_cooldown, cooldown_remaining = self._in_cooldown(symbol)
            force_signal = self._research_force_buy_signal(symbol)

            # --- Global macro / news enrichment ---
            vix_data = fetch_vix_level()
            vix = vix_data["vix"] if vix_data else 20.0
            fear_level = vix_data["fear_level"] if vix_data else "moderate"
            extreme_fear = fear_level == "extreme"  # VIX > 30 — avoid new entries
            high_fear = fear_level in ("high", "extreme")  # VIX > 20

            news = fetch_news_sentiment(symbol)
            symbol_news_score = float(news.get("score", 0.0))
            symbol_news_topics = news.get("topic_scores", {}) or {}

            global_news = fetch_global_macro_sentiment()
            global_news_score = float(global_news.get("score", 0.0))
            global_news_topics = global_news.get("topic_scores", {}) or {}
            external_research = fetch_external_research_sentiment()
            external_research_score = float(external_research.get("score", 0.0))
            external_research_topics = external_research.get("topic_scores", {}) or {}

            # Blend symbol-specific and global topic exposure.
            # Symbol headlines get stronger weight; macro headlines provide context.
            blended_topic_scores = {}
            all_topics = set(symbol_news_topics) | set(global_news_topics) | set(external_research_topics)
            for topic in all_topics:
                blended_topic_scores[topic] = (
                    float(symbol_news_topics.get(topic, 0.0))
                    + 0.6 * float(global_news_topics.get(topic, 0.0))
                    + 0.5 * float(external_research_topics.get(topic, 0.0))
                )

            # Composite score used in decision gates.
            news_score = symbol_news_score + (0.5 * global_news_score) + (0.35 * external_research_score)

            # Online cause/effect learning: map topic exposure -> next-cycle returns.
            self.event_learner.observe(symbol, current_price, blended_topic_scores)
            learned_edge_adjustment = self.event_learner.get_edge_adjustment(symbol, blended_topic_scores)

            sector_momentum = fetch_sector_momentum()
            # Determine which sector ETF to check based on symbol
            _TECH_SYMBOLS = {"AAPL", "MSFT", "NVDA", "GOOGL", "AMD", "META", "TSLA", "COIN", "PLTR"}
            _ENERGY_SYMBOLS = {"XOM", "CVX", "USO"}
            if symbol in _TECH_SYMBOLS:
                sector_etf = "XLK"
            elif symbol in _ENERGY_SYMBOLS:
                sector_etf = "XLE"
            else:
                sector_etf = "SPY"
            sector_data = sector_momentum.get(sector_etf, {})
            sector_tailwind = sector_data.get("momentum_5d", 0.0) > 0.0

            adaptive_context = self._build_adaptive_context(
                predicted_change=predicted_change,
                trend_strength=trend_strength,
                sentiment=sentiment,
                news_score=news_score,
                sector_tailwind=sector_tailwind,
                high_fear=high_fear,
                market_favorable=bool(market_state.get("favorable", True)),
            )
            adaptive_policy_adjustment = self.experience_policy.edge_adjustment(symbol, adaptive_context)
            adaptive_policy_score = self.experience_policy.diagnostic_score(symbol, adaptive_context)

            # ── Proven historical pattern scoring ──────────────────────────────
            # Score current conditions against documented multi-decade market patterns.
            # The pattern edge is applied as a small, capped adjustment to the effective
            # predicted change so it nudges (not overrides) the ML model output.
            _tech_symbols = {"AAPL", "MSFT", "NVDA", "GOOGL", "AMD", "META", "TSLA", "COIN", "PLTR"}
            _above_long_ma = bool(current_price > long_trend)
            _earnings_topic = float(blended_topic_scores.get("earnings", 0.0))
            _geopolitics_active = float(blended_topic_scores.get("geopolitics", 0.0)) < -1.0
            _rate_cut_signal = float(blended_topic_scores.get("rates", 0.0)) > 1.0
            _multi_buy = buy_signals >= 3
            _multi_sell = sell_signals >= 3
            _tech_pos = sector_tailwind and symbol in _tech_symbols
            equity_conditions = build_equity_conditions(
                recent_return=recent_return,
                trend_positive=bool(trend_strength > 0),
                rsi=float(news.get("rsi", 50.0) if isinstance(news.get("rsi"), (int, float)) else 50.0),
                sentiment=int(sentiment),
                vix=float(vix),
                tech_sector_positive=_tech_pos,
                rate_cut_signal=_rate_cut_signal,
                geopolitics_active=_geopolitics_active,
                multi_politician_buy=_multi_buy,
                multi_politician_sell=_multi_sell,
                earnings_topic_score=_earnings_topic,
                above_long_ma=_above_long_ma,
            )
            pattern_result = score_conditions_against_patterns(equity_conditions, asset_class="equity")
            # Scale pattern score to a small edge contribution: max ±0.5% per strong pattern hit
            pattern_edge_adjustment = max(-0.005, min(0.005, float(pattern_result["total_score"]) * 0.002))

            effective_predicted_change = predicted_change + learned_edge_adjustment + adaptive_policy_adjustment + pattern_edge_adjustment

            self.last_analysis[symbol] = {
                "predicted_price": predicted_price,
                "current_price": current_price,
                "predicted_change_pct": predicted_change * 100,
                "sentiment": sentiment,
                "buy_signals": buy_signals,
                "sell_signals": sell_signals,
                "short_trend": short_trend,
                "long_trend": long_trend,
                "trend_strength_pct": trend_strength * 100,
                "recent_return_pct": recent_return * 100,
                "market_favorable": bool(market_state.get("favorable", True)),
                "market_regime": regime_label,
                "market_regime_confidence": regime_confidence,
                "regime_entry_multiplier": regime_entry_multiplier,
                "regime_risk_multiplier": regime_risk_multiplier,
                "capitol_data_source": capitol_data_source,
                "capitol_data_confidence": capitol_data_confidence,
                "capitol_data_degraded": capitol_data_degraded,
                "capitol_data_risk_multiplier": data_confidence_risk_multiplier,
                "cooldown_remaining_minutes": cooldown_remaining / 60,
                "has_position": bool(position),
                "vix": vix,
                "fear_level": fear_level,
                "news_score": news_score,
                "symbol_news_score": symbol_news_score,
                "global_news_score": global_news_score,
                "external_research_score": external_research_score,
                "news_topics": blended_topic_scores,
                "learned_edge_adjustment_pct": learned_edge_adjustment * 100,
                "adaptive_policy_adjustment_pct": adaptive_policy_adjustment * 100,
                "adaptive_policy_score": adaptive_policy_score,
                "pattern_edge_adjustment_pct": pattern_edge_adjustment * 100,
                "pattern_hits": pattern_result.get("pattern_hits", []),
                "effective_predicted_change_pct": effective_predicted_change * 100,
                "sector_etf": sector_etf,
                "sector_tailwind": sector_tailwind,
                "research_force_buy_triggered": bool(force_signal.get("triggered", False)),
                "research_force_buy_probability": float(force_signal.get("probability", 0.0) or 0.0),
                "research_force_buy_impact_score": float(force_signal.get("impact_score", 0.0) or 0.0),
                "research_force_buy_evidence_count": int(force_signal.get("evidence_count", 0) or 0),
                "research_force_buy_title": str(force_signal.get("title") or ""),
                "research_force_buy_theme": str(force_signal.get("theme") or ""),
            }

            if position:
                entry_price = float(position.get("entry_price") or current_price)
                if entry_price <= 0:
                    entry_price = current_price
                entry_ts = float(position.get("entry_ts") or 0.0)
                hold_hours = ((time.time() - entry_ts) / 3600.0) if entry_ts > 0 else LONG_TERM_MIN_HOLD_HOURS
                min_hold_reached = hold_hours >= max(0, LONG_TERM_MIN_HOLD_HOURS)
                is_etf = symbol in ETF_SYMBOLS
                sl_pct = STOP_LOSS_PCT * 0.6 if is_etf else STOP_LOSS_PCT
                tp_pct = TAKE_PROFIT_PCT * 0.5 if is_etf else TAKE_PROFIT_PCT
                stop_loss_price   = entry_price * (1 - sl_pct)
                take_profit_price = entry_price * (1 + tp_pct)
                self.last_analysis[symbol].update(
                    {
                        "entry_price": entry_price,
                        "stop_loss_price": stop_loss_price,
                        "take_profit_price": take_profit_price,
                        "hold_hours": hold_hours,
                    }
                )

                if current_price <= stop_loss_price:
                    return "SELL"
                if not min_hold_reached:
                    return "HOLD"
                if current_price >= take_profit_price and (
                    effective_predicted_change <= BUY_THRESHOLD_PCT or sentiment <= 0 or trend_strength < 0
                ):
                    return "SELL"
                if effective_predicted_change <= -SELL_THRESHOLD_PCT and (
                    sentiment < 0 or trend_strength < 0 or recent_return < 0
                ):
                    return "SELL"
                if sentiment <= -2 and recent_return < 0:
                    return "SELL"
                if short_trend < long_trend and recent_return <= -SELL_THRESHOLD_PCT:
                    return "SELL"
                # Exit if news turns very negative while holding
                if news_score <= -3 and recent_return < 0:
                    return "SELL"
                return "HOLD"

            try:
                open_positions_count = broker.get_open_positions_count() if broker else len(self.positions)
            except Exception:
                open_positions_count = len(self.positions)

            profile = self.autonomy_profile
            blocked_symbols = set(profile.get("blocked_symbols", []) or []) | set(self.blocked_symbols_by_improvement)
            if symbol in blocked_symbols:
                self.last_analysis[symbol]["autonomy_block_reason"] = "blocked_symbol_underperforming"
                return "HOLD"
            if self.active_setup_candidates and symbol not in self.active_setup_candidates:
                self.last_analysis[symbol]["autonomy_block_reason"] = "outside_top_validated_candidates"
                return "HOLD"
            if not bool(profile.get("allow_new_entries", True)):
                self.last_analysis[symbol]["autonomy_block_reason"] = "autonomous_gate_entries_disabled"
                return "HOLD"

            effective_max_positions = max(
                1,
                int(MAX_POSITIONS * float(profile.get("max_positions_multiplier", 1.0))),
            )
            dynamic_buy_threshold = BUY_THRESHOLD_PCT * float(profile.get("buy_threshold_multiplier", 1.0))
            dynamic_buy_threshold *= regime_entry_multiplier

            has_capacity = open_positions_count < effective_max_positions
            is_etf = symbol in ETF_SYMBOLS

            # ETFs use tighter risk params: smaller stop, smaller target, longer cooldown
            etf_stop_loss   = STOP_LOSS_PCT * 0.6        # 3% default
            etf_take_profit = TAKE_PROFIT_PCT * 0.5      # 6% default
            etf_cooldown    = TRADE_COOLDOWN_MINUTES * 4  # 60 min default

            has_model_edge = effective_predicted_change >= dynamic_buy_threshold
            has_strong_model_edge = effective_predicted_change >= dynamic_buy_threshold * 1.5
            has_positive_sentiment = sentiment >= MIN_SENTIMENT_TO_BUY
            has_strong_sentiment = sentiment >= MIN_SENTIMENT_TO_BUY + 2
            trend_confirmation = (
                trend_strength >= MIN_TREND_STRENGTH_PCT and current_price > short_trend > long_trend
            )
            positive_momentum = recent_return >= BUY_THRESHOLD_PCT

            current_setup = None
            if is_etf and trend_confirmation and positive_momentum:
                current_setup = "etf_momentum"
            elif trend_confirmation and positive_momentum:
                current_setup = "trend_continuation"
            elif current_price > long_trend and recent_return > -0.01:
                current_setup = "pullback_recovery"

            setup_validation = evaluate_equity_setup(close, current_setup=current_setup)
            self.last_analysis[symbol].update(
                {
                    "setup_validation": setup_validation,
                    "validated_setup": setup_validation.get("setup", "none"),
                    "setup_passed": bool(setup_validation.get("passed", False)),
                    "setup_expectancy_pct": float(setup_validation.get("expectancy", 0.0)) * 100,
                    "setup_win_rate_pct": float(setup_validation.get("win_rate", 0.0)) * 100,
                    "setup_sample_size": int(setup_validation.get("sample_size", 0)),
                }
            )

            if self.long_term_policy.drawdown_blocked():
                self.last_analysis[symbol]["autonomy_block_reason"] = "long_term_drawdown_guard"
                return "HOLD"

            fundamentals = {"passed": True, "score": 1.0}
            if FUNDAMENTALS_GATE_ENABLED and not is_etf:
                fundamentals = evaluate_company_fundamentals(
                    symbol=symbol,
                    min_score=FUNDAMENTALS_MIN_SCORE,
                    min_market_cap_billion=FUNDAMENTALS_MIN_MARKET_CAP_BILLION,
                    max_debt_to_equity=FUNDAMENTALS_MAX_DEBT_TO_EQUITY,
                    require_positive_fcf=FUNDAMENTALS_REQUIRE_POSITIVE_FCF,
                )
                self.last_analysis[symbol]["fundamentals"] = fundamentals
                if not bool(fundamentals.get("passed", False)) and not force_signal.get("triggered", False):
                    self.last_analysis[symbol]["autonomy_block_reason"] = "fundamentals_gate_failed"
                    return "HOLD"

            # --- Global macro filters on new entries ---
            # Block all new entries when VIX signals extreme fear (market panic)
            if extreme_fear and not is_etf:
                return "HOLD"
            # News score boosts or penalises entry confidence
            news_bullish = news_score >= 1
            news_bearish = news_score <= -2
            # Don't open new positions into strongly negative news
            if news_bearish and not has_strong_model_edge:
                return "HOLD"

            if not has_capacity or in_cooldown:
                return "HOLD"
            if (
                capitol_data_confidence < CAPITOL_DATA_MIN_CONFIDENCE_TO_TRADE
                and not force_signal.get("triggered", False)
            ):
                self.last_analysis[symbol]["autonomy_block_reason"] = "capitol_data_confidence_too_low"
                return "HOLD"
            if regime_confidence < 0.35 and not is_etf and not has_strong_model_edge and not force_signal.get("triggered", False):
                self.last_analysis[symbol]["autonomy_block_reason"] = "regime_uncertain"
                return "HOLD"
            if not regime_allow_new_entries and not is_etf and not has_strong_model_edge and not force_signal.get("triggered", False):
                self.last_analysis[symbol]["autonomy_block_reason"] = "regime_disallows_new_entries"
                return "HOLD"
            if (
                not market_state.get("favorable", True)
                and not has_strong_sentiment
                and not is_etf
                and not force_signal.get("triggered", False)
            ):
                return "HOLD"
            if not bool(setup_validation.get("passed", False)) and not force_signal.get("triggered", False):
                self.last_analysis[symbol]["autonomy_block_reason"] = "historical_setup_validation_failed"
                return "HOLD"

            # Research-assisted force-buy path:
            # a high-conviction external signal can trigger a BUY even when
            # setup validation would otherwise block entries.
            if force_signal.get("triggered", False):
                self.last_analysis[symbol]["autonomy_block_reason"] = ""
                self.last_analysis[symbol]["entry_path"] = "research_force_buy"
                return "BUY"

            if is_etf:
                # ETFs: sentiment is unreliable — rely on ML prediction + trend only
                # Also allow buying ETFs in unfavorable markets as a hedge (e.g. GLD)
                if trend_confirmation and has_model_edge and positive_momentum:
                    return "BUY"
                if has_strong_model_edge and trend_strength >= MIN_TREND_STRENGTH_PCT:
                    return "BUY"
            else:
                # Sector tailwind + positive news = bonus confirmation, allows slightly relaxed ML threshold
                macro_boost = sector_tailwind and news_bullish
                if trend_confirmation:
                    if has_model_edge and has_positive_sentiment:
                        return "BUY"
                    if has_strong_model_edge and positive_momentum:
                        return "BUY"
                    if has_strong_sentiment and positive_momentum:
                        return "BUY"
                    # With strong macro backing, a moderate model edge is enough
                    if macro_boost and has_model_edge and positive_momentum:
                        return "BUY"

                # Growth-momentum path: buys strongly trending stocks independently of
                # Capitol Trades political signal (which can be up to 45 days stale).
                # Requires a tighter technical bar to compensate for absent political confirmation.
                if GROWTH_MOMENTUM_BUY_ENABLED:
                    growth_trend = (
                        trend_strength >= MIN_TREND_STRENGTH_PCT * GROWTH_MOMENTUM_MIN_TREND_MULTIPLIER
                        and current_price > short_trend
                        and short_trend > long_trend
                    )
                    growth_momentum = recent_return >= BUY_THRESHOLD_PCT * GROWTH_MOMENTUM_MIN_RETURN_MULTIPLIER
                    macro_backing = sector_tailwind or news_bullish
                    if (
                        has_model_edge
                        and growth_trend
                        and growth_momentum
                        and macro_backing
                        and not news_bearish
                    ):
                        self.last_analysis[symbol]["entry_path"] = "growth_momentum"
                        return "BUY"

            return "HOLD"
        except Exception as e:
            print(f"Error analyzing signal for {symbol}: {e}")
            return "HOLD"

    def execute_trade(self, signal, symbol, broker):
        """Execute trades with tighter position sizing and restart-safe risk controls."""
        symbol = symbol.upper()
        try:
            if signal == "BUY":
                if symbol in self.positions:
                    return None
                if symbol in self.blocked_symbols_by_improvement:
                    print(f"Skipping BUY for {symbol}: auto-improvement blocked symbol.")
                    return None
                profile = self.autonomy_profile
                if not bool(profile.get("allow_new_entries", True)):
                    print(f"Skipping BUY for {symbol}: autonomous gate disabled new entries.")
                    return None

                effective_max_positions = max(
                    1,
                    int(MAX_POSITIONS * float(profile.get("max_positions_multiplier", 1.0))),
                )
                if broker.get_open_positions_count() >= effective_max_positions:
                    print(f"Skipping BUY for {symbol}: already at max positions.")
                    return None
                if hasattr(broker, "has_pending_buy_order") and broker.has_pending_buy_order(symbol):
                    print(f"Skipping BUY for {symbol}: pending buy order already exists.")
                    return None
                if hasattr(broker, "is_market_open") and not broker.is_market_open(symbol):
                    print(f"Skipping BUY for {symbol}: regular market is closed for market orders.")
                    return None

                capital = broker.get_account_balance()
                current_price = broker.get_current_price(symbol)
                if current_price <= 0 or capital <= 0:
                    print(
                        f"Skipping BUY for {symbol}: invalid capital or price "
                        f"(capital={capital:.2f}, price={current_price:.4f})."
                    )
                    return None
                portfolio_value = broker.get_portfolio_value()
                if portfolio_value > 0:
                    policy_state = self.long_term_policy.record_portfolio_value(portfolio_value)
                    if policy_state.get("drawdown", 0.0) >= LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT:
                        print(
                            f"Skipping BUY for {symbol}: long-term drawdown guard active "
                            f"({policy_state.get('drawdown', 0.0):.1%})."
                        )
                        return None

                entry_analysis = self.last_analysis.get(symbol, {})
                effective_risk_per_trade = RISK_PER_TRADE * float(profile.get("risk_multiplier", 1.0))
                regime_risk = float(entry_analysis.get("regime_risk_multiplier", 1.0) or 1.0)
                effective_risk_per_trade *= regime_risk
                effective_risk_per_trade *= float(self.symbol_risk_multipliers.get(symbol, 1.0))
                effective_risk_per_trade *= float(self.setup_rank_multipliers.get(symbol, 1.0))
                effective_risk_per_trade *= max(0.25, min(1.0, float(self.drift_risk_multiplier)))
                effective_risk_per_trade *= max(0.25, min(1.2, float(self.confidence_risk_multiplier)))
                if bool(entry_analysis.get("research_force_buy_triggered", False)):
                    effective_risk_per_trade *= max(0.05, min(1.0, float(TECH_RESEARCH_FORCE_BUY_RISK_MULTIPLIER)))
                deployable_capital = capital
                if LONG_HORIZON_ENABLED:
                    deployable_capital = max(0.0, capital * max(0.0, 1.0 - LONG_HORIZON_CASH_BUFFER_PCT))
                    effective_risk_per_trade = min(effective_risk_per_trade, float(LONG_HORIZON_MAX_RISK_PER_TRADE))
                target_qty = int((deployable_capital * effective_risk_per_trade) / current_price)
                max_affordable_qty = int(deployable_capital // current_price)
                qty = min(max_affordable_qty, max(1, target_qty)) if max_affordable_qty > 0 else 0
                if qty <= 0:
                    print(
                        f"Skipping BUY for {symbol}: insufficient buying power for one share "
                        f"(capital={capital:.2f}, price={current_price:.4f}, "
                        f"risk={effective_risk_per_trade:.4f}, target_qty={target_qty}, "
                        f"max_affordable_qty={max_affordable_qty})."
                    )
                    return None

                proposed_notional = float(qty) * float(current_price)
                open_notional = broker.get_open_notional() if hasattr(broker, "get_open_notional") else 0.0
                allowed, reason = self.long_term_policy.can_open_position(
                    symbol=symbol,
                    proposed_notional=proposed_notional,
                    portfolio_value=portfolio_value if portfolio_value > 0 else capital,
                    open_notional=open_notional,
                )
                if not allowed:
                    print(f"Skipping BUY for {symbol}: {reason}.")
                    return None

                # Promotion pipeline gate
                if _pipeline.stage == "shadow":
                    _pipeline.log_shadow("BUY", symbol, qty, current_price)
                    print(f"[shadow] Would BUY {symbol}: {qty} shares at ${current_price:.2f} — not submitted")
                    return None
                if _pipeline.stage == "canary":
                    qty = max(1, int(qty * _pipeline.canary_size_fraction))

                _eq_rec = _exec_tracker.start_record("BUY", symbol, qty, current_price)
                try:
                    broker.buy(symbol, qty)
                    _fill = _exec_tracker.poll_fill(broker, symbol, current_price)
                    _exec_tracker.finish_record(_eq_rec, fill_price=_fill)
                except Exception as _eq_exc:
                    _exec_tracker.finish_record(_eq_rec, rejected=True, reject_reason=str(_eq_exc))
                    raise
                entry_context = self._build_adaptive_context(
                    predicted_change=float(entry_analysis.get("effective_predicted_change_pct", 0.0)) / 100.0,
                    trend_strength=float(entry_analysis.get("trend_strength_pct", 0.0)) / 100.0,
                    sentiment=float(entry_analysis.get("sentiment", 0.0)),
                    news_score=float(entry_analysis.get("news_score", 0.0)),
                    sector_tailwind=bool(entry_analysis.get("sector_tailwind", False)),
                    high_fear=str(entry_analysis.get("fear_level", "")).lower() in ("high", "extreme"),
                    market_favorable=bool(entry_analysis.get("market_favorable", True)),
                )
                self.positions[symbol] = {
                    "entry_price": current_price,
                    "qty": qty,
                    "entry_context": entry_context,
                    "entry_ts": time.time(),
                }
                self.last_trade_times[symbol] = time.time()
                if bool(entry_analysis.get("research_force_buy_triggered", False)):
                    print(
                        f"BUY signal for {symbol}: {qty} shares at ${current_price:.2f} "
                        f"[research_force_buy p={float(entry_analysis.get('research_force_buy_probability', 0.0))*100:.1f}% "
                        f"impact={float(entry_analysis.get('research_force_buy_impact_score', 0.0)):.2f} "
                        f"evidence={int(entry_analysis.get('research_force_buy_evidence_count', 0))}]"
                    )
                else:
                    print(f"BUY signal for {symbol}: {qty} shares at ${current_price:.2f}")
                if LONG_HORIZON_ENABLED:
                    print(
                        f"Long-horizon sizing active: monthly_contribution=${LONG_HORIZON_MONTHLY_CONTRIBUTION:.2f}, "
                        f"cash_buffer={LONG_HORIZON_CASH_BUFFER_PCT:.0%}, risk_cap={LONG_HORIZON_MAX_RISK_PER_TRADE:.2%}"
                    )
                return {"action": "BUY", "symbol": symbol, "qty": qty, "price": current_price}

            if signal == "SELL":
                local_position = self.positions.get(symbol, {})
                synced_position = self._sync_position(symbol, broker)
                qty = int(round(broker.get_position_size(symbol)))
                if qty <= 0:
                    qty = int((synced_position or self.positions.get(symbol, {})).get("qty", 0))
                if qty <= 0:
                    print(f"Skipping SELL for {symbol}: no open quantity found.")
                    self.positions.pop(symbol, None)
                    return None

                current_price = broker.get_current_price(symbol)
                entry_price_for_learning = float(
                    local_position.get("entry_price")
                    or (synced_position or {}).get("entry_price")
                    or current_price
                )
                entry_context = local_position.get("entry_context")
                if not entry_context:
                    analysis = self.last_analysis.get(symbol, {})
                    entry_context = self._build_adaptive_context(
                        predicted_change=float(analysis.get("effective_predicted_change_pct", 0.0)) / 100.0,
                        trend_strength=float(analysis.get("trend_strength_pct", 0.0)) / 100.0,
                        sentiment=float(analysis.get("sentiment", 0.0)),
                        news_score=float(analysis.get("news_score", 0.0)),
                        sector_tailwind=bool(analysis.get("sector_tailwind", False)),
                        high_fear=str(analysis.get("fear_level", "")).lower() in ("high", "extreme"),
                        market_favorable=bool(analysis.get("market_favorable", True)),
                    )
                hold_minutes = 0.0
                if local_position.get("entry_ts"):
                    hold_minutes = (time.time() - float(local_position["entry_ts"])) / 60.0

                # Promotion pipeline gate
                if _pipeline.stage == "shadow":
                    _pipeline.log_shadow("SELL", symbol, qty, current_price)
                    print(f"[shadow] Would SELL {symbol}: {qty} shares at ${current_price:.2f} — not submitted")
                    return None

                _eq_rec = _exec_tracker.start_record("SELL", symbol, qty, current_price)
                try:
                    broker.sell(symbol, qty)
                    _fill = _exec_tracker.poll_fill(broker, symbol, current_price)
                    _exec_tracker.finish_record(_eq_rec, fill_price=_fill)
                except Exception as _eq_exc:
                    _exec_tracker.finish_record(_eq_rec, rejected=True, reject_reason=str(_eq_exc))
                    raise
                self.experience_policy.observe_trade(
                    symbol=symbol,
                    entry_context=entry_context,
                    entry_price=entry_price_for_learning,
                    exit_price=current_price,
                    hold_minutes=hold_minutes,
                )
                pnl = (float(current_price) - float(entry_price_for_learning)) * float(qty)
                self.trade_history.append({
                    "ts": datetime.now(timezone.utc),
                    "symbol": symbol,
                    "pnl": float(pnl),
                })
                self.positions.pop(symbol, None)
                self.last_trade_times[symbol] = time.time()
                print(f"SELL signal for {symbol}: {qty} shares at ${current_price:.2f}")
                return {"action": "SELL", "symbol": symbol, "qty": qty, "price": current_price}
        except Exception as e:
            print(f"Error executing trade for {symbol}: {e}")
        return None