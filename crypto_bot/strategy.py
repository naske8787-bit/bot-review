import json
import os
import pandas as pd
from datetime import datetime, timedelta, timezone

from config import (
    AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED,
    AUTO_IMPROVEMENT_LOOKBACK_DAYS,
    AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL,
    AUTO_IMPROVEMENT_REBALANCE_HOURS,
    AUTONOMOUS_MAX_DRAWDOWN_7D_PCT,
    AUTONOMOUS_MIN_CLOSED_TRADES,
    AUTONOMOUS_MIN_PROFIT_FACTOR,
    AUTONOMOUS_MIN_REALIZED_PNL_7D,
    AUTONOMOUS_MIN_WIN_RATE,
    AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS,
    AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES,
    AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE,
    AUTONOMY_LEARNING_ENABLED,
    AUTONOMY_DYNAMIC_TUNING_ENABLED,
    AUTONOMY_DYNAMIC_STEP,
    AUTONOMY_RISK_MULT_MIN,
    AUTONOMY_RISK_MULT_MAX,
    AUTONOMY_BUY_THRESHOLD_MULT_MIN,
    AUTONOMY_BUY_THRESHOLD_MULT_MAX,
    AUTONOMY_MAX_POSITIONS_MULT_MIN,
    AUTONOMY_MAX_POSITIONS_MULT_MAX,
    AUTONOMY_FAILSAFE_DRAWDOWN_PCT,
    AUTONOMY_LOSS_EVENT_MIN_PNL,
    AUTONOMY_RECOVERY_EVENT_MIN_PNL,
    CRYPTO_MAX_POSITIONS,
    CRYPTO_MIN_NOTIONAL_PER_TRADE,
    CRYPTO_SELL_QTY_BUFFER_PCT,
    CRYPTO_MIN_TREND_STRENGTH_PCT,
    CRYPTO_RISK_PER_TRADE,
    CRYPTO_RSI_BUY_THRESHOLD,
    CRYPTO_RSI_SELL_THRESHOLD,
    CRYPTO_STOP_LOSS_PCT,
    CRYPTO_TAKE_PROFIT_PCT,
    CRYPTO_ATR_PERIOD,
    CRYPTO_ATR_STOP_MULTIPLIER,
    CRYPTO_MACD_FAST,
    CRYPTO_MACD_SLOW,
    CRYPTO_MACD_SIGNAL,
    CRYPTO_MIN_VOLUME_PERCENTILE,
    CRYPTO_RESEARCH_ENTRY_GUARD_SCORE,
    CRYPTO_RESEARCH_HARD_BLOCK_SCORE,
    CRYPTO_RESEARCH_SOFT_BLOCK_SCORE,
    CRYPTO_LOG_HOLD_REASONS,
    INFLUENCER_PUMP_TRADE_SCORE,
    INFLUENCER_DUMP_SELL_SCORE,
    INFLUENCER_MANIPULATION_RIDE_SCORE,
    INFLUENCER_MANIPULATION_DUMP_SCORE,
    INFLUENCER_REQUIRE_TECHNICAL_CONFIRM,
    LONG_TERM_MIN_HOLD_HOURS,
    LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT,
    LONG_TERM_MAX_TOTAL_EXPOSURE_PCT,
    LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT,
    LONG_HORIZON_CASH_BUFFER_PCT,
    LONG_HORIZON_ENABLED,
    LONG_HORIZON_MAX_RISK_PER_TRADE,
    LONG_HORIZON_MONTHLY_CONTRIBUTION,
)
from data_fetcher import fetch_crypto_data, preprocess_data, fetch_external_research_sentiment
from influencer_monitor import get_symbol_signal
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "shared"))
from proven_patterns import score_conditions_against_patterns, build_crypto_conditions
from setup_validator import evaluate_crypto_setup
from regime_detector import detect_crypto_regime
from long_term_policy import LongTermPolicy
from execution_quality import ExecutionQualityTracker as _ExecQualTracker
from promotion_pipeline import PromotionPipeline as _PromotionPipeline

_EXEC_LOG_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs", "execution_quality.jsonl")
_exec_tracker = _ExecQualTracker(_EXEC_LOG_PATH)
_PIPELINE_STATE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
_pipeline = _PromotionPipeline("crypto", _PIPELINE_STATE_DIR)


class TradingStrategy:
    def __init__(self):
        self.positions = {}
        self.last_analysis = {}
        self.trade_history = []
        self.portfolio_history = []
        self.symbol_risk_multipliers = {}
        self.setup_rank_multipliers = {}
        self.drift_risk_multiplier = 1.0
        self.confidence_risk_multiplier = 1.0
        self.blocked_symbols_by_improvement = set()
        self.active_setup_candidates = set()
        self.last_improvement_rebalance_ts = None
        self.autonomy_profile = {
            "mode": "normal",
            "score": 0,
            "allow_new_entries": True,
            "risk_multiplier": 1.0,
            "max_positions_multiplier": 1.0,
            "buy_threshold_multiplier": 1.0,
            "blocked_symbols": [],
        }
        self.learning_enabled = bool(AUTONOMY_LEARNING_ENABLED)
        self.state_path = os.path.join(os.path.dirname(__file__), "models", "autonomy_state.json")
        self.autonomy_state = {
            "last_mode": "normal",
            "last_realized_pnl_7d": 0.0,
            "last_drawdown_7d": 0.0,
            "aggressive_cooldown_until": "",
            "dynamic_offsets": {
                "risk": 0.0,
                "buy_threshold": 0.0,
                "max_positions": 0.0,
            },
            "mode_stats": {
                "aggressive": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
                "normal": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
                "cautious": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
                "capital_preservation": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
            },
        }
        self.long_term_policy = LongTermPolicy(
            bot_name="crypto_bot",
            max_total_exposure_pct=LONG_TERM_MAX_TOTAL_EXPOSURE_PCT,
            max_symbol_exposure_pct=LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT,
            max_drawdown_pct=LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT,
        )
        self._load_autonomy_state()

    def _load_autonomy_state(self):
        if not self.learning_enabled or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                self.autonomy_state.update(payload)
        except Exception:
            return

    def _persist_autonomy_state(self):
        if not self.learning_enabled:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.autonomy_state, f, indent=2, sort_keys=True)
        except Exception:
            return

    @staticmethod
    def _safe_mode_stats(mode_stats, mode):
        base = mode_stats.get(mode) or {}
        return {
            "wins": int(base.get("wins", 0) or 0),
            "losses": int(base.get("losses", 0) or 0),
            "pnl_sum": float(base.get("pnl_sum", 0.0) or 0.0),
        }

    @staticmethod
    def _parse_state_ts(value):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    @staticmethod
    def _clamp(value, lo, hi):
        return max(lo, min(hi, float(value)))

    def _update_mode_learning(self, now, realized_pnl_7d, drawdown_7d):
        reasons = []
        if not self.learning_enabled:
            return reasons

        last_mode = str(self.autonomy_state.get("last_mode") or "normal")
        prev_pnl = float(self.autonomy_state.get("last_realized_pnl_7d", 0.0) or 0.0)
        prev_dd = float(self.autonomy_state.get("last_drawdown_7d", 0.0) or 0.0)
        delta_pnl = float(realized_pnl_7d) - prev_pnl
        delta_dd = float(drawdown_7d) - prev_dd

        mode_stats = dict(self.autonomy_state.get("mode_stats") or {})
        stats = self._safe_mode_stats(mode_stats, last_mode)

        if delta_pnl <= float(AUTONOMY_LOSS_EVENT_MIN_PNL) or (delta_pnl < 0 and delta_dd > 0.005):
            stats["losses"] += 1
            stats["pnl_sum"] += delta_pnl
            reasons.append(
                f"learning update: {last_mode} underperformed (delta_pnl={delta_pnl:.2f}, delta_dd={delta_dd:.2%})"
            )
            if last_mode == "aggressive":
                cooldown_until = now + timedelta(hours=max(1, AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS))
                self.autonomy_state["aggressive_cooldown_until"] = cooldown_until.isoformat()
                reasons.append(
                    f"aggressive cooldown enabled for {AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS}h after loss event"
                )
        elif delta_pnl >= float(AUTONOMY_RECOVERY_EVENT_MIN_PNL):
            stats["wins"] += 1
            stats["pnl_sum"] += delta_pnl
            reasons.append(f"learning update: {last_mode} delivered positive outcome (delta_pnl={delta_pnl:.2f})")

        mode_stats[last_mode] = stats
        self.autonomy_state["mode_stats"] = mode_stats
        return reasons

    def _mode_confidence_penalty(self, mode):
        mode_stats = dict(self.autonomy_state.get("mode_stats") or {})
        stats = self._safe_mode_stats(mode_stats, mode)
        total = stats["wins"] + stats["losses"]
        if total < 4:
            return 0.0
        success_rate = stats["wins"] / max(1, total)
        avg_outcome = stats["pnl_sum"] / max(1, total)
        penalty = 0.0
        if success_rate < 0.45:
            penalty += 4.0
        if avg_outcome < 0:
            penalty += 4.0
        return penalty

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

        def rank_key(item):
            s = item[1]
            return (
                float(s.get("expectancy", 0.0)),
                float(s.get("profit_factor", 0.0)),
                float(s.get("win_rate", 0.0)),
            )

        ranked = sorted(eligible, key=rank_key, reverse=True)

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

        top = ", ".join([
            f"{sym} x{new_multipliers.get(sym, 1.0):.2f}" for sym, _ in ranked[:3]
        ]) or "none"
        blocked = ", ".join(sorted(new_blocked)) or "none"
        return [
            "Auto-improvement applied (daily return-per-risk rebalance).",
            f"Top allocations: {top}",
            f"Underperformer cap/block list: {blocked}",
        ]

    def observe_portfolio_value(self, value):
        self.portfolio_history.append({"ts": datetime.now(timezone.utc), "value": float(value)})
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self.portfolio_history = [x for x in self.portfolio_history if x["ts"] >= cutoff]

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

    def evaluate_autonomy_profile(self, research_payload=None):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [t for t in self.trade_history if t["ts"] >= cutoff]
        pnls = [t["pnl"] for t in recent]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        closed = len(pnls)
        win_rate = (len(wins) / closed) if closed else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
        realized_pnl_7d = sum(pnls)

        values = [x["value"] for x in self.portfolio_history if x["ts"] >= cutoff]
        max_dd = 0.0
        if len(values) >= 2:
            peak = values[0]
            for v in values:
                peak = max(peak, v)
                dd = (peak - v) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)

        research_score = float((research_payload or {}).get("weighted_score", (research_payload or {}).get("score", 0.0)))
        score = 0
        if closed >= AUTONOMOUS_MIN_CLOSED_TRADES:
            score += 10
        else:
            score -= 8
        # Cold-start grace: with zero closed trades, win rate and profit factor
        # are undefined rather than bad. Treat them as neutral so a fresh funded
        # account can start trading and generate the feedback data it needs.
        if closed > 0:
            if win_rate >= AUTONOMOUS_MIN_WIN_RATE:
                score += 10
            else:
                score -= 8
            if profit_factor >= AUTONOMOUS_MIN_PROFIT_FACTOR:
                score += 10
            else:
                score -= 8
        if realized_pnl_7d >= AUTONOMOUS_MIN_REALIZED_PNL_7D:
            score += 8
        else:
            score -= 8
        if max_dd <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT:
            score += 8
        else:
            score -= 10
        if research_score > 3:
            score += 4
        elif research_score < -3:
            score -= 4

        reasons = []
        reasons.extend(self._update_mode_learning(datetime.now(timezone.utc), realized_pnl_7d, max_dd))

        aggressive_penalty = self._mode_confidence_penalty("aggressive")
        if aggressive_penalty > 0:
            score -= aggressive_penalty
            reasons.append(
                f"historical penalty: aggressive mode reliability is weak ({aggressive_penalty:.0f} score points removed)"
            )

        passed_checks = 0
        passed_checks += 1 if closed >= 5 else 0
        passed_checks += 1 if (closed == 0 or win_rate >= 0.4) else 0
        passed_checks += 1 if (closed == 0 or profit_factor >= 1.0) else 0
        passed_checks += 1 if realized_pnl_7d >= -500 else 0
        passed_checks += 1 if max_dd <= 0.15 else 0
        confidence = passed_checks / 5.0
        cooldown_until = self._parse_state_ts(self.autonomy_state.get("aggressive_cooldown_until"))
        aggressive_cooldown_active = bool(cooldown_until and cooldown_until > datetime.now(timezone.utc))

        if score >= 28:
            mode = "aggressive"
            profile = {"allow_new_entries": True, "risk_multiplier": 1.25, "max_positions_multiplier": 1.1, "buy_threshold_multiplier": 0.85}
        elif score >= 14:
            mode = "normal"
            profile = {"allow_new_entries": True, "risk_multiplier": 1.0, "max_positions_multiplier": 1.0, "buy_threshold_multiplier": 1.0}
        elif score >= 4:
            mode = "cautious"
            profile = {"allow_new_entries": True, "risk_multiplier": 0.6, "max_positions_multiplier": 0.8, "buy_threshold_multiplier": 1.1}
        else:
            mode = "capital_preservation"
            profile = {"allow_new_entries": False, "risk_multiplier": 0.0, "max_positions_multiplier": 0.6, "buy_threshold_multiplier": 2.0}

        if mode == "aggressive":
            if aggressive_cooldown_active:
                mode = "cautious"
                profile = {"allow_new_entries": True, "risk_multiplier": 0.6, "max_positions_multiplier": 0.8, "buy_threshold_multiplier": 1.1}
                reasons.append("aggressive blocked during cooldown after recent underperformance")
            elif closed < max(1, AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES):
                mode = "normal"
                profile = {"allow_new_entries": True, "risk_multiplier": 1.0, "max_positions_multiplier": 1.0, "buy_threshold_multiplier": 1.0}
                reasons.append(
                    f"aggressive held back until closed trades >= {AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES}"
                )
            elif confidence < max(0.0, min(1.0, AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE)):
                mode = "normal"
                profile = {"allow_new_entries": True, "risk_multiplier": 1.0, "max_positions_multiplier": 1.0, "buy_threshold_multiplier": 1.0}
                reasons.append(
                    f"aggressive held back: confidence {confidence:.0%} below {AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE:.0%}"
                )

        # Autonomous guardrail tuning: bounded and stateful for smoother adaptation.
        offsets = dict(self.autonomy_state.get("dynamic_offsets") or {})
        risk_offset = float(offsets.get("risk", 0.0) or 0.0)
        buy_offset = float(offsets.get("buy_threshold", 0.0) or 0.0)
        max_pos_offset = float(offsets.get("max_positions", 0.0) or 0.0)

        if AUTONOMY_DYNAMIC_TUNING_ENABLED:
            step = max(0.01, float(AUTONOMY_DYNAMIC_STEP))
            robust_sample = closed >= max(4, AUTONOMOUS_MIN_CLOSED_TRADES)
            quality_good = (
                robust_sample
                and profit_factor >= max(1.0, AUTONOMOUS_MIN_PROFIT_FACTOR)
                and max_dd <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT
                and win_rate >= max(0.45, AUTONOMOUS_MIN_WIN_RATE - 0.03)
                and research_score > -2.0
            )
            quality_weak = (
                robust_sample
                and (
                    profit_factor < 0.95
                    or max_dd > AUTONOMOUS_MAX_DRAWDOWN_7D_PCT * 1.15
                    or win_rate < max(0.35, AUTONOMOUS_MIN_WIN_RATE - 0.15)
                )
            )

            if quality_good:
                risk_offset += step
                buy_offset -= step * 0.5
                max_pos_offset += step * 0.5
                reasons.append("dynamic tuning: quality supportive, easing entry guardrails")
            elif quality_weak:
                risk_offset -= step
                buy_offset += step
                max_pos_offset -= step * 0.5
                reasons.append("dynamic tuning: quality weak, tightening guardrails")

            if closed >= 8 and (profit_factor < 0.90 or max_dd > AUTONOMY_FAILSAFE_DRAWDOWN_PCT):
                mode = "capital_preservation"
                profile = {
                    "allow_new_entries": False,
                    "risk_multiplier": 0.0,
                    "max_positions_multiplier": 0.6,
                    "buy_threshold_multiplier": 2.0,
                }
                risk_offset = min(risk_offset, -0.20)
                buy_offset = max(buy_offset, 0.25)
                max_pos_offset = min(max_pos_offset, -0.20)
                reasons.append("failsafe rollback: pausing new entries after weak realized quality")

        profile["risk_multiplier"] = self._clamp(
            float(profile.get("risk_multiplier", 1.0)) + risk_offset,
            AUTONOMY_RISK_MULT_MIN,
            AUTONOMY_RISK_MULT_MAX,
        )
        profile["buy_threshold_multiplier"] = self._clamp(
            float(profile.get("buy_threshold_multiplier", 1.0)) + buy_offset,
            AUTONOMY_BUY_THRESHOLD_MULT_MIN,
            AUTONOMY_BUY_THRESHOLD_MULT_MAX,
        )
        profile["max_positions_multiplier"] = self._clamp(
            float(profile.get("max_positions_multiplier", 1.0)) + max_pos_offset,
            AUTONOMY_MAX_POSITIONS_MULT_MIN,
            AUTONOMY_MAX_POSITIONS_MULT_MAX,
        )

        if not bool(profile.get("allow_new_entries", True)):
            profile["risk_multiplier"] = 0.0

        self.autonomy_state["dynamic_offsets"] = {
            "risk": risk_offset,
            "buy_threshold": buy_offset,
            "max_positions": max_pos_offset,
        }

        by_symbol = {}
        for t in recent:
            by_symbol.setdefault(t["symbol"], []).append(t["pnl"])
        blocked = sorted([s for s, vals in by_symbol.items() if vals and (sum(vals) / len(vals)) < 0])[:6]

        self.autonomy_state["last_mode"] = mode
        self.autonomy_state["last_realized_pnl_7d"] = realized_pnl_7d
        self.autonomy_state["last_drawdown_7d"] = max_dd
        self._persist_autonomy_state()

        return {
            "mode": mode,
            "score": score,
            "blocked_symbols": blocked,
            **profile,
            "metrics": {
                "closed_trades_7d": closed,
                "win_rate_7d": win_rate,
                "profit_factor_7d": profit_factor,
                "realized_pnl_7d": realized_pnl_7d,
                "max_drawdown_7d": max_dd,
                "research_score": research_score,
                "confidence": confidence,
            },
            "reasons": reasons,
        }

    @staticmethod
    def _compute_macd(close, fast, slow, signal):
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])

    @staticmethod
    def _compute_atr(data, period):
        high = data["High"].astype(float)
        low = data["Low"].astype(float)
        close = data["Close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])

    def analyze_signal(self, symbol):
        symbol = symbol.upper()
        try:
            def hold(reason):
                if symbol in self.last_analysis:
                    self.last_analysis[symbol]["hold_reason"] = reason
                if CRYPTO_LOG_HOLD_REASONS:
                    print(f"{symbol}: HOLD reason={reason}")
                return "HOLD"

            data = preprocess_data(fetch_crypto_data(symbol))
            if len(data) < max(35, CRYPTO_MACD_SLOW + CRYPTO_MACD_SIGNAL):
                self.last_analysis[symbol] = {"reason": "not_enough_data"}
                return hold("not_enough_data")

            close = data["Close"].astype(float)
            volume = data["Volume"].astype(float) if "Volume" in data.columns else None

            current_price = float(close.iloc[-1])
            ema_fast = float(data["ema_fast"].iloc[-1])
            ema_slow = float(data["ema_slow"].iloc[-1])
            rsi = float(data["rsi"].iloc[-1])
            momentum_pct = float(data["momentum_pct"].iloc[-1])
            trend_strength = (ema_fast - ema_slow) / max(abs(ema_slow), 1e-9)
            macd_line, macd_signal, macd_hist = self._compute_macd(
                close, CRYPTO_MACD_FAST, CRYPTO_MACD_SLOW, CRYPTO_MACD_SIGNAL
            )
            macd_bullish = macd_line > macd_signal and macd_hist > 0
            oversold_rebound = rsi <= CRYPTO_RSI_BUY_THRESHOLD and momentum_pct >= 0 and macd_bullish

            atr = self._compute_atr(data, CRYPTO_ATR_PERIOD)
            regime_state = detect_crypto_regime(
                close,
                atr_pct=(atr / current_price) if current_price > 0 else None,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
            )
            regime_label = str(regime_state.get("label") or "unknown")
            regime_confidence = float(regime_state.get("confidence", 0.0) or 0.0)
            regime_entry_multiplier = float(regime_state.get("entry_threshold_multiplier", 1.0) or 1.0)
            regime_risk_multiplier = float(regime_state.get("risk_multiplier", 1.0) or 1.0)
            regime_allow_new_entries = bool(regime_state.get("allow_new_entries", True))

            # Volume filter: only trade when volume is above the configured percentile
            volume_ok = True
            current_volume = 0.0
            volume_threshold = 0.0
            if volume is not None and len(volume) >= 20:
                current_volume = float(volume.iloc[-1])
                volume_threshold = float(volume.quantile(CRYPTO_MIN_VOLUME_PERCENTILE / 100.0))
                volume_ok = current_volume >= volume_threshold

            position = self.positions.get(symbol)
            profile = self.autonomy_profile
            external_research = fetch_external_research_sentiment()
            external_research_score = float(external_research.get("weighted_score", external_research.get("score", 0.0)))
            topic_scores = external_research.get("topic_scores") or {}
            risk_on_signal = float(topic_scores.get("etf_flows", 0.0)) + float(topic_scores.get("liquidity_rates", 0.0)) + float(topic_scores.get("stablecoin_liquidity", 0.0)) + float(topic_scores.get("onchain_activity", 0.0))
            risk_off_signal = float(topic_scores.get("regulation_policy", 0.0)) + float(topic_scores.get("exchange_security", 0.0)) + float(topic_scores.get("derivatives_leverage", 0.0))

            # Influencer manipulation signal for this specific symbol
            influencer_signals = external_research.get("influencer_signals", {})
            inf_signal = get_symbol_signal(influencer_signals, symbol)
            inf_net = float(inf_signal.get("net_signal", 0.0))
            inf_manip_score = float(inf_signal.get("manipulation_score", 0.0))
            inf_pump_flag = inf_manip_score >= INFLUENCER_MANIPULATION_RIDE_SCORE
            inf_dump_flag = inf_manip_score <= INFLUENCER_MANIPULATION_DUMP_SCORE
            inf_coordination = bool(inf_signal.get("coordination", False))
            inf_top_influencers = inf_signal.get("top_influencers", [])

            blocked_union = set(profile.get("blocked_symbols", []) or set()) | set(self.blocked_symbols_by_improvement)
            if symbol in blocked_union:
                return hold("blocked_by_improvement")

            self.last_analysis[symbol] = {
                "current_price": current_price,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "rsi": rsi,
                "momentum_pct": momentum_pct * 100,
                "trend_strength_pct": trend_strength * 100,
                "macd_line": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist,
                "atr": atr,
                "volume_ok": volume_ok,
                "current_volume": current_volume,
                "volume_threshold": volume_threshold,
                "has_position": bool(position),
                "external_research_score": external_research_score,
                "research_risk_on_signal": risk_on_signal,
                "research_risk_off_signal": risk_off_signal,
                "research_topics": external_research.get("dominant_topics", []),
                "risk_multiplier_symbol": float(self.symbol_risk_multipliers.get(symbol, 1.0)),
                "regime": regime_label,
                "regime_confidence": regime_confidence,
                "regime_entry_multiplier": regime_entry_multiplier,
                "regime_risk_multiplier": regime_risk_multiplier,
                # Influencer manipulation signals
                "influencer_net_signal": round(inf_net, 2),
                "influencer_manipulation_score": round(inf_manip_score, 2),
                "influencer_pump_flag": inf_pump_flag,
                "influencer_dump_flag": inf_dump_flag,
                "influencer_coordination": inf_coordination,
                "influencer_top_actors": inf_top_influencers,
                "influencer_pump_mode": False,  # updated below if position has pump tag
            }

            if position:
                entry_price = float(position["entry_price"])
                pump_mode = bool(position.get("influencer_pump_mode", False))
                self.last_analysis[symbol]["influencer_pump_mode"] = pump_mode
                entry_ts = position.get("entry_ts")
                hold_hours = LONG_TERM_MIN_HOLD_HOURS
                if isinstance(entry_ts, datetime):
                    hold_hours = max(0.0, (datetime.now(timezone.utc) - entry_ts).total_seconds() / 3600.0)
                min_hold_reached = hold_hours >= max(0, LONG_TERM_MIN_HOLD_HOURS)

                # ATR-based trailing stop: tighter of fixed % or ATR multiple
                atr_stop = current_price - (CRYPTO_ATR_STOP_MULTIPLIER * atr)
                fixed_stop = entry_price * (1 - CRYPTO_STOP_LOSS_PCT)
                # Update trailing high-water mark
                hwm = float(position.get("hwm", entry_price))
                hwm = max(hwm, current_price)
                position["hwm"] = hwm
                trailing_stop = hwm - (CRYPTO_ATR_STOP_MULTIPLIER * atr)
                stop_loss_price = max(fixed_stop, trailing_stop)

                # Pump-ride mode: use tighter take-profit (60% of normal) so we
                # exit before the inevitable influencer-triggered dump.
                if pump_mode:
                    take_profit_price = entry_price * (1 + CRYPTO_TAKE_PROFIT_PCT * 0.6)
                else:
                    take_profit_price = entry_price * (1 + CRYPTO_TAKE_PROFIT_PCT)

                self.last_analysis[symbol].update(
                    {
                        "entry_price": entry_price,
                        "stop_loss_price": stop_loss_price,
                        "take_profit_price": take_profit_price,
                        "hwm": hwm,
                    }
                )

                # Influencer dump signal: exit immediately before macro stop hits
                if inf_dump_flag:
                    print(
                        f"[INFLUENCER] Dump/FUD signal detected for {symbol} "
                        f"(manip_score={inf_manip_score:.2f}, actors={inf_top_influencers}). "
                        "Exiting position."
                    )
                    return "SELL"

                # Pump-ride exit: if we rode the pump and influencer signal turns
                # negative, exit before the coordinated dump arrives.
                if pump_mode and inf_net < 0:
                    print(
                        f"[INFLUENCER] Pump reversed for {symbol}: inf_net={inf_net:.2f}. "
                        "Exiting pump-ride position."
                    )
                    return "SELL"

                if current_price <= stop_loss_price:
                    return "SELL"
                if not min_hold_reached:
                    return hold("min_hold_not_reached")
                if current_price >= take_profit_price and rsi >= CRYPTO_RSI_SELL_THRESHOLD:
                    return "SELL"
                # MACD bearish crossover exit
                if macd_line < macd_signal and macd_hist < 0 and ema_fast < ema_slow:
                    return "SELL"
                if ema_fast < ema_slow and momentum_pct < 0:
                    return "SELL"
                return hold("position_hold")

            if not volume_ok:
                return hold("volume_filter")

            if self.active_setup_candidates and symbol not in self.active_setup_candidates:
                return hold("not_in_setup_candidates")

            if not bool(profile.get("allow_new_entries", True)):
                return hold("autonomy_blocks_entries")
            if self.long_term_policy.drawdown_blocked():
                return hold("long_term_drawdown_block")
            if not regime_allow_new_entries and not oversold_rebound:
                return hold("regime_blocks_entries")
            if regime_confidence < 0.35 and not oversold_rebound:
                return hold("low_regime_confidence")

            # Macro filter: During extreme bearish research regime, block all new entries
            # unless we have a strong oversold rebound signal with positive momentum
            if external_research_score <= CRYPTO_RESEARCH_HARD_BLOCK_SCORE:
                if not (oversold_rebound and momentum_pct > 0):
                    return hold("research_hard_block")

            effective_max_positions = max(1, int(CRYPTO_MAX_POSITIONS * float(profile.get("max_positions_multiplier", 1.0))))
            dynamic_trend_threshold = CRYPTO_MIN_TREND_STRENGTH_PCT * float(profile.get("buy_threshold_multiplier", 1.0))
            dynamic_trend_threshold *= regime_entry_multiplier

            has_capacity = len(self.positions) < effective_max_positions
            bullish_trend = trend_strength >= dynamic_trend_threshold and ema_fast > ema_slow
            trend_continuation = (
                bullish_trend
                and 45 <= rsi <= CRYPTO_RSI_SELL_THRESHOLD
                and momentum_pct > 0
                and macd_bullish
            )
            current_setup = None
            if oversold_rebound:
                current_setup = "oversold_rebound"
            elif trend_continuation:
                current_setup = "trend_continuation"
            if risk_off_signal <= -2.5 and not oversold_rebound:
                return hold("risk_off_block")
            if external_research_score <= CRYPTO_RESEARCH_SOFT_BLOCK_SCORE and not oversold_rebound:
                return hold("research_soft_block")

            # Influencer dump/FUD guard: block new entries when influencers are
            # signalling a coordinated dump or spreading FUD for this symbol.
            if inf_net <= INFLUENCER_DUMP_SELL_SCORE and not oversold_rebound:
                print(
                    f"[INFLUENCER] Blocking new entry for {symbol}: "
                    f"dump signal inf_net={inf_net:.2f}, actors={inf_top_influencers}"
                )
                return hold("influencer_dump_block")

            # ── Proven historical pattern scoring ─────────────────────────────
            # Score current crypto conditions against documented historical patterns.
            # The result can loosen or tighten the entry guard threshold.
            research_confidence = float(external_research.get("confidence", 0.5))
            topic_scores_all = external_research.get("topic_scores") or {}
            _etf_flow = float(topic_scores_all.get("etf_flows", 0.0))
            _stable = float(topic_scores_all.get("stablecoin_liquidity", 0.0))
            _deriv = float(topic_scores_all.get("derivatives_leverage", 0.0))
            _reg = float(topic_scores_all.get("regulation_policy", 0.0))
            _exch = float(topic_scores_all.get("exchange_security", 0.0))
            _onchain = float(topic_scores_all.get("onchain_activity", 0.0))
            # Try to get VIX from yfinance (cached globally; 0 = unknown)
            try:
                import yfinance as _yf
                _vix_data = _yf.download("^VIX", period="5d", progress=False, auto_adjust=False)
                _vix = float(_vix_data["Close"].iloc[-1]) if len(_vix_data) > 0 else 20.0
            except Exception:
                _vix = 20.0
            crypto_conditions = build_crypto_conditions(
                rsi=rsi,
                macd_bullish=macd_bullish,
                trend_positive=bullish_trend,
                momentum_positive=momentum_pct > 0,
                volume_ok=volume_ok,
                etf_flow_score=_etf_flow,
                stablecoin_score=_stable,
                regulation_score=_reg,
                onchain_score=_onchain,
                funding_rate_score=_deriv,
                macro_risk_off=_vix > 25,
                vix=_vix,
                above_long_ma=bool(ema_fast > ema_slow),
            )
            pattern_result = score_conditions_against_patterns(crypto_conditions, asset_class="crypto")
            pattern_score = float(pattern_result["total_score"])
            # Store in analysis for logging
            self.last_analysis[symbol]["pattern_hits"] = pattern_result.get("pattern_hits", [])
            self.last_analysis[symbol]["pattern_score"] = pattern_score
            if current_setup is None and pattern_score >= 2.0 and momentum_pct > 0:
                current_setup = "pattern_breakout"

            setup_validation = evaluate_crypto_setup(close, current_setup=current_setup, rsi_period=14)
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

            # When strong proven bullish patterns fire, allow entry even if the
            # research score is slightly below the soft guard threshold.
            pattern_override = pattern_score >= 1.0 and research_confidence >= 0.3
            setup_passed = bool(setup_validation.get("passed", False))

            if has_capacity and (oversold_rebound or trend_continuation):
                if not setup_passed:
                    return hold("setup_validation_failed")
                if external_research_score <= CRYPTO_RESEARCH_ENTRY_GUARD_SCORE and risk_on_signal <= 0 and not pattern_override:
                    return hold("research_entry_guard")
                return "BUY"

            # Research-assisted momentum entry: allow strong trend continuation
            # when market-impact topics are broadly supportive.
            if has_capacity and bullish_trend and macd_bullish and momentum_pct > 0 and risk_on_signal >= 2.0 and risk_off_signal >= -1.0:
                if not setup_passed:
                    return hold("setup_validation_failed")
                return "BUY"

            # Pattern-driven entry: if multiple proven bullish patterns are firing
            # and the bot has capacity, allow a cautious entry even without a strong
            # MACD/RSI signal — patterns represent 60-80% win-rate historical setups.
            if has_capacity and pattern_score >= 2.0 and external_research_score > CRYPTO_RESEARCH_SOFT_BLOCK_SCORE and momentum_pct > 0:
                if not setup_passed:
                    return hold("setup_validation_failed")
                return "BUY"

            # Influencer pump-ride entry: buy early to capture a likely influencer-
            # driven price move. Apply a tighter take-profit on exit (see position logic).
            # Optionally require a basic technical confirmation (price not already
            # overbought and momentum positive).
            if has_capacity and inf_net >= INFLUENCER_PUMP_TRADE_SCORE and not inf_dump_flag:
                if INFLUENCER_REQUIRE_TECHNICAL_CONFIRM:
                    tech_ok = momentum_pct > 0 and rsi < 75 and not (ema_fast < ema_slow and rsi > 70)
                    if not tech_ok:
                        return hold("influencer_tech_confirm_failed")
                print(
                    f"[INFLUENCER] Pump signal for {symbol}: "
                    f"inf_net={inf_net:.2f}, manip_score={inf_manip_score:.2f}, "
                    f"coordination={inf_coordination}, actors={inf_top_influencers}. "
                    "Entering pump-ride trade."
                )
                self.last_analysis[symbol]["influencer_pump_mode"] = True
                return "BUY"

            return hold("no_entry_condition_met")
        except Exception as e:
            print(f"Error analyzing signal for {symbol}: {e}")
            return "HOLD"

    def execute_trade(self, signal, symbol, broker):
        symbol = symbol.upper()
        try:
            if signal == "BUY":
                profile = self.autonomy_profile
                if not bool(profile.get("allow_new_entries", True)):
                    return None
                if symbol in self.positions:
                    return None
                if symbol in self.blocked_symbols_by_improvement:
                    return None

                capital = broker.get_account_balance()
                if capital <= 0:
                    details = broker.get_account_details() if hasattr(broker, 'get_account_details') else {}
                    cash = details.get('cash', 'unknown')
                    buying_power = details.get('buying_power', 'unknown')
                    status = details.get('status', 'unknown')
                    print(f"Skipping BUY for {symbol}: insufficient account balance (balance={capital:.2f}, cash={cash}, buying_power={buying_power}, status={status})")
                    return None
                current_price = broker.get_current_price(symbol)
                portfolio_value = broker.get_portfolio_value()
                if portfolio_value > 0:
                    policy_state = self.long_term_policy.record_portfolio_value(portfolio_value)
                    if policy_state.get("drawdown", 0.0) >= LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT:
                        print(
                            f"Skipping BUY for {symbol}: long-term drawdown guard active "
                            f"({policy_state.get('drawdown', 0.0):.1%})."
                        )
                        return None
                effective_risk = CRYPTO_RISK_PER_TRADE * float(profile.get("risk_multiplier", 1.0))
                regime_risk = float(self.last_analysis.get(symbol, {}).get("regime_risk_multiplier", 1.0) or 1.0)
                effective_risk *= regime_risk
                effective_risk *= float(self.symbol_risk_multipliers.get(symbol, 1.0))
                effective_risk *= float(self.setup_rank_multipliers.get(symbol, 1.0))
                effective_risk *= max(0.25, min(1.0, float(self.drift_risk_multiplier)))
                effective_risk *= max(0.25, min(1.2, float(self.confidence_risk_multiplier)))
                deployable_capital = capital
                if LONG_HORIZON_ENABLED:
                    deployable_capital = max(0.0, capital * max(0.0, 1.0 - LONG_HORIZON_CASH_BUFFER_PCT))
                    effective_risk = min(effective_risk, float(LONG_HORIZON_MAX_RISK_PER_TRADE))
                notional = deployable_capital * effective_risk
                if current_price <= 0 or notional < CRYPTO_MIN_NOTIONAL_PER_TRADE:
                    print(f"Skipping BUY for {symbol}: notional={notional:.2f} < min={CRYPTO_MIN_NOTIONAL_PER_TRADE} or price invalid (capital={capital:.2f}, risk={effective_risk:.2%}, price={current_price:.2f})")
                    return None

                qty = round(notional / current_price, 6)
                if qty <= 0:
                    print(f"Skipping BUY for {symbol}: quantity rounded to zero.")
                    return None

                open_notional = broker.get_open_notional() if hasattr(broker, "get_open_notional") else 0.0
                allowed, reason = self.long_term_policy.can_open_position(
                    symbol=symbol,
                    proposed_notional=notional,
                    portfolio_value=portfolio_value if portfolio_value > 0 else capital,
                    open_notional=open_notional,
                )
                if not allowed:
                    print(f"Skipping BUY for {symbol}: {reason}.")
                    return None

                # Promotion pipeline gate
                if _pipeline.stage == "shadow":
                    _pipeline.log_shadow("BUY", symbol, qty, current_price)
                    print(f"[shadow] Would BUY {symbol}: {qty} units at ${current_price:.2f} — not submitted")
                    return None
                if _pipeline.stage == "canary":
                    qty = round(qty * _pipeline.canary_size_fraction, 6)
                    if qty <= 0:
                        return None

                _eq_rec = _exec_tracker.start_record("BUY", symbol, qty, current_price)
                try:
                    broker.buy(symbol, qty)
                    _fill = _exec_tracker.poll_fill(broker, symbol, current_price)
                    _exec_tracker.finish_record(_eq_rec, fill_price=_fill)
                except Exception as _eq_exc:
                    _exec_tracker.finish_record(_eq_rec, rejected=True, reject_reason=str(_eq_exc))
                    raise
                self.positions[symbol] = {
                    "entry_price": current_price,
                    "qty": qty,
                    "entry_ts": datetime.now(timezone.utc),
                    "influencer_pump_mode": bool(
                        self.last_analysis.get(symbol, {}).get("influencer_pump_mode", False)
                    ),
                }
                pump_tag = " [PUMP-RIDE]" if self.positions[symbol]["influencer_pump_mode"] else ""
                print(f"BUY signal for {symbol}: {qty} units at ${current_price:.2f}{pump_tag}")
                if LONG_HORIZON_ENABLED:
                    print(
                        f"Long-horizon sizing active: monthly_contribution=${LONG_HORIZON_MONTHLY_CONTRIBUTION:.2f}, "
                        f"cash_buffer={LONG_HORIZON_CASH_BUFFER_PCT:.0%}, risk_cap={LONG_HORIZON_MAX_RISK_PER_TRADE:.2%}"
                    )
                return {"action": "BUY", "symbol": symbol, "qty": qty, "price": current_price}

            if signal == "SELL":
                broker_qty = float(broker.get_position_size(symbol) or 0.0)
                tracked_qty = float(self.positions.get(symbol, {}).get("qty", 0.0) or 0.0)
                qty_basis = broker_qty if broker_qty > 0 else tracked_qty
                qty = round(max(0.0, qty_basis * float(CRYPTO_SELL_QTY_BUFFER_PCT)), 6)

                if qty <= 0:
                    print(f"Skipping SELL for {symbol}: no open quantity found.")
                    self.positions.pop(symbol, None)
                    return None

                current_price = broker.get_current_price(symbol)
                entry_price = float(self.positions.get(symbol, {}).get("entry_price", current_price))

                # Promotion pipeline gate
                if _pipeline.stage == "shadow":
                    _pipeline.log_shadow("SELL", symbol, qty, current_price)
                    print(f"[shadow] Would SELL {symbol}: {qty} units at ${current_price:.2f} — not submitted")
                    return None

                _eq_rec = _exec_tracker.start_record("SELL", symbol, qty, current_price)
                try:
                    broker.sell(symbol, qty)
                    _fill = _exec_tracker.poll_fill(broker, symbol, current_price)
                    _exec_tracker.finish_record(_eq_rec, fill_price=_fill)
                except Exception as _eq_exc:
                    _exec_tracker.finish_record(_eq_rec, rejected=True, reject_reason=str(_eq_exc))
                    raise
                pnl = (current_price - entry_price) * float(qty)
                self.trade_history.append({
                    "ts": datetime.now(timezone.utc),
                    "symbol": symbol,
                    "pnl": float(pnl),
                })
                self.positions.pop(symbol, None)
                print(f"SELL signal for {symbol}: {qty} units at ${current_price:.2f}")
                return {"action": "SELL", "symbol": symbol, "qty": qty, "price": current_price}
        except Exception as e:
            print(f"Error executing trade for {symbol}: {e}")
        return None
