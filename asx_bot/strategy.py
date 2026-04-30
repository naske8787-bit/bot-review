"""
ASX Day-Trading Strategy
========================

Signal logic combines five layers of evidence before entering a trade:

  1. LSTM predicted next-close   — directional bias (most important)
  2. VWAP position               — buy below VWAP (discount), sell above (premium)
  3. Bollinger Bands             — avoid entering when price is already at extremes
  4. EMA crossover               — trend confirmation (9 / 21 EMA)
  5. RSI guard                   — block trades in overbought/oversold territory
  6. Volume spike                — confirm with above-average volume (>1.1×)

Position sizing:
  Shares = (equity × RISK_PER_TRADE) / (price × STOP_LOSS_PCT)
  Capped so a single position never exceeds 25% of equity.

End-of-day forced close:
  All open positions are closed ~10 min before ASX market close (15:50 AEST)
  to avoid overnight risk and after-market spreads.
"""
from __future__ import annotations

import time
import os
import json
from datetime import datetime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd

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
    AUTONOMY_LOSS_EVENT_MIN_PNL,
    AUTONOMY_RECOVERY_EVENT_MIN_PNL,
    ASX_TIMEZONE,
    EMA_LONG,
    EMA_SHORT,
    EOD_CLOSE_HOUR,
    EOD_CLOSE_MIN,
    EVENT_BOOTSTRAP_ENABLED,
    EVENT_BOOTSTRAP_INTERVAL,
    EVENT_BOOTSTRAP_MIN_OBSERVATIONS,
    EVENT_BOOTSTRAP_YEARS,
    EVENT_LEARNER_ALPHA,
    EVENT_LEARNER_LAGS,
    EVENT_MAX_EDGE_ADJUSTMENT_PCT,
    INITIAL_TRAIN_BARS,
    LOOKBACK_BARS,
    MAX_POSITIONS,
    RETRAIN_EVERY_N_BARS,
    RISK_PER_TRADE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRADE_COOLDOWN_SECS,
)
from data_fetcher import fetch_bars, fetch_external_research_sentiment, fetch_latest_price, fetch_stock_data
from event_learner import EventImpactLearner
from model import ASXModel

BUY  = "BUY"
SELL = "SELL"
HOLD = "HOLD"

# Minimum predicted price change (%) to act on — filter noise
_BUY_THRESHOLD  =  0.10    # +0.10%
_SELL_THRESHOLD = -0.10    # -0.10%
_ASX_TZ = ZoneInfo(ASX_TIMEZONE)


class ASXStrategy:
    def __init__(self):
        self._models:      Dict[str, ASXModel]   = {}
        self._bar_buffers: Dict[str, pd.DataFrame] = {}
        self._last_trade:  Dict[str, float]      = {}
        self._bars_seen:   Dict[str, int]        = {}
        self.trade_history = []
        self.portfolio_history = []
        self.symbol_risk_multipliers: Dict[str, float] = {}
        self.blocked_symbols_by_improvement: set[str] = set()
        self.last_improvement_rebalance_ts: Optional[datetime] = None
        self.autonomy_profile = {
            "mode": "normal",
            "score": 0,
            "allow_new_entries": True,
            "risk_multiplier": 1.0,
            "max_positions_multiplier": 1.0,
            "buy_threshold_multiplier": 1.0,
            "blocked_symbols": [],
        }
        self._historical_bootstrap_attempted: set[str] = set()
        learner_state_path = os.path.join(os.path.dirname(__file__), "models", "event_impact_state.json")
        self.event_learner = EventImpactLearner(
            storage_path=learner_state_path,
            alpha=EVENT_LEARNER_ALPHA,
            max_adjustment_abs=EVENT_MAX_EDGE_ADJUSTMENT_PCT / 100.0,
            lags=EVENT_LEARNER_LAGS,
        )
        self.learning_enabled = bool(AUTONOMY_LEARNING_ENABLED)
        self.state_path = os.path.join(os.path.dirname(__file__), "models", "autonomy_state.json")
        self.autonomy_state = {
            "last_mode": "normal",
            "last_realized_pnl_7d": 0.0,
            "last_drawdown_7d": 0.0,
            "aggressive_cooldown_until": "",
            "mode_stats": {
                "aggressive": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
                "normal": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
                "cautious": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
                "capital_preservation": {"wins": 0, "losses": 0, "pnl_sum": 0.0},
            },
        }
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
                cooldown_until = now + pd.Timedelta(hours=max(1, AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS))
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

    def observe_portfolio_value(self, value):
        self.portfolio_history.append({"ts": datetime.now(timezone.utc), "value": float(value)})
        cutoff = datetime.now(timezone.utc) - pd.Timedelta(days=7)
        self.portfolio_history = [x for x in self.portfolio_history if x["ts"] >= cutoff]

    def apply_autonomy_profile(self, profile):
        if isinstance(profile, dict):
            self.autonomy_profile.update(profile)

    def _symbol_trade_stats(self):
        cutoff = datetime.now(timezone.utc) - pd.Timedelta(days=AUTO_IMPROVEMENT_LOOKBACK_DAYS)
        by_symbol: Dict[str, list[float]] = {}
        for t in self.trade_history:
            if t["ts"] < cutoff:
                continue
            sym = str(t.get("symbol") or "").upper()
            if not sym:
                continue
            by_symbol.setdefault(sym, []).append(float(t.get("pnl") or 0.0))

        out: Dict[str, dict] = {}
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

        new_multipliers: Dict[str, float] = {}
        new_blocked: set[str] = set()
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

    def evaluate_autonomy_profile(self, research_payload=None):
        now = datetime.now(timezone.utc)
        cutoff = now - pd.Timedelta(days=7)
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

        research_score = float((research_payload or {}).get("score", 0.0))
        score = 0
        score += 10 if closed >= AUTONOMOUS_MIN_CLOSED_TRADES else -8
        score += 10 if win_rate >= AUTONOMOUS_MIN_WIN_RATE else -8
        score += 10 if profit_factor >= AUTONOMOUS_MIN_PROFIT_FACTOR else -8
        score += 8 if realized_pnl_7d >= AUTONOMOUS_MIN_REALIZED_PNL_7D else -8
        score += 8 if max_dd <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT else -10
        if research_score > 3:
            score += 4
        elif research_score < -3:
            score -= 4

        reasons = []
        reasons.extend(self._update_mode_learning(now, realized_pnl_7d, max_dd))

        aggressive_penalty = self._mode_confidence_penalty("aggressive")
        if aggressive_penalty > 0:
            score -= aggressive_penalty
            reasons.append(
                f"historical penalty: aggressive mode reliability is weak ({aggressive_penalty:.0f} score points removed)"
            )

        passed_checks = 0
        passed_checks += 1 if closed >= AUTONOMOUS_MIN_CLOSED_TRADES else 0
        passed_checks += 1 if win_rate >= AUTONOMOUS_MIN_WIN_RATE else 0
        passed_checks += 1 if profit_factor >= AUTONOMOUS_MIN_PROFIT_FACTOR else 0
        passed_checks += 1 if realized_pnl_7d >= AUTONOMOUS_MIN_REALIZED_PNL_7D else 0
        passed_checks += 1 if max_dd <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT else 0
        confidence = passed_checks / 5.0

        cooldown_until = self._parse_state_ts(self.autonomy_state.get("aggressive_cooldown_until"))
        aggressive_cooldown_active = bool(cooldown_until and cooldown_until > now)

        if score >= 28:
            mode = "aggressive"
            profile = {"allow_new_entries": True, "risk_multiplier": 1.25, "max_positions_multiplier": 1.1, "buy_threshold_multiplier": 0.9}
        elif score >= 14:
            mode = "normal"
            profile = {"allow_new_entries": True, "risk_multiplier": 1.0, "max_positions_multiplier": 1.0, "buy_threshold_multiplier": 1.0}
        elif score >= 4:
            mode = "cautious"
            profile = {"allow_new_entries": True, "risk_multiplier": 0.6, "max_positions_multiplier": 0.8, "buy_threshold_multiplier": 1.2}
        else:
            mode = "capital_preservation"
            profile = {"allow_new_entries": False, "risk_multiplier": 0.0, "max_positions_multiplier": 0.6, "buy_threshold_multiplier": 1.8}

        if mode == "aggressive":
            if aggressive_cooldown_active:
                mode = "cautious"
                profile = {"allow_new_entries": True, "risk_multiplier": 0.6, "max_positions_multiplier": 0.8, "buy_threshold_multiplier": 1.2}
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

        tech_base = 0.20 if symbol.upper() in {"XRO.AX", "WTC.AX", "APT.AX"} else 0.0
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
                    f"  [Strategy] Historical bootstrap skipped for {symbol}: "
                    f"{len(observations)} observations (target={EVENT_BOOTSTRAP_MIN_OBSERVATIONS})."
                )
                return
            consumed = self.event_learner.bootstrap_symbol_history(symbol, observations)
            print(
                f"  [Strategy] Historical bootstrap complete for {symbol}: "
                f"{consumed} observations (up to {EVENT_BOOTSTRAP_YEARS}y, interval={EVENT_BOOTSTRAP_INTERVAL})."
            )
        except Exception as e:
            print(f"  [Strategy] Historical bootstrap failed for {symbol}: {e}")

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

    # ── Public interface ──────────────────────────────────────────────────────

    def analyse(self, symbol: str) -> dict:
        """
        Fetch intraday bars, train/update LSTM, and return a signal dict with:
          signal, predicted_close, current_price, predicted_chg_pct,
          vwap_position, rsi, ema_cross, atr, volume_ratio, bb_position
        """
        self._bootstrap_symbol_history_if_needed(symbol)
        df = fetch_bars(symbol, lookback_bars=max(LOOKBACK_BARS + 50, INITIAL_TRAIN_BARS))

        if df.empty or len(df) < LOOKBACK_BARS:
            return {"signal": HOLD, "reason": "insufficient_data", "symbol": symbol}

        model = self._get_model(symbol, df)
        self._maybe_retrain(symbol, model, df)
        model.update(df)    # incremental online fine-tune on the new bar

        # ── Extract latest indicator values ───────────────────────────────────
        last          = df.iloc[-1]
        current_price = float(last["Close"])
        ema_s         = float(last[f"EMA_{EMA_SHORT}"])
        ema_l         = float(last[f"EMA_{EMA_LONG}"])
        rsi           = float(last["RSI"])
        atr           = float(last["ATR"])
        vwap          = float(last["VWAP"])
        bb_upper      = float(last["BB_upper"])
        bb_lower      = float(last["BB_lower"])
        bb_mid        = float(last["BB_mid"])
        vol_ratio     = float(last["Volume_ratio"])

        # ── LSTM prediction ───────────────────────────────────────────────────
        predicted = model.predict_next_close(df)
        if predicted is None:
            return {"signal": HOLD, "reason": "model_not_ready", "symbol": symbol}

        pred_chg_pct = (predicted - current_price) / current_price * 100.0
        profile = self.autonomy_profile
        external_research = fetch_external_research_sentiment()
        external_research_score = float(external_research.get("score", 0.0))
        blocked_symbols = set(profile.get("blocked_symbols", []) or []) | set(self.blocked_symbols_by_improvement)
        if symbol in blocked_symbols:
            return {"signal": HOLD, "reason": "blocked_symbol", "symbol": symbol, "external_research_score": external_research_score}
        topic_scores = self._current_topic_scores(symbol)
        self.event_learner.observe(symbol, current_price, topic_scores)
        learned_edge_adjustment_pct = self.event_learner.get_edge_adjustment(symbol, topic_scores) * 100.0
        effective_pred_chg_pct = pred_chg_pct + learned_edge_adjustment_pct + (0.05 * external_research_score)
        dynamic_buy_threshold = _BUY_THRESHOLD * float(profile.get("buy_threshold_multiplier", 1.0))
        dynamic_sell_threshold = _SELL_THRESHOLD * float(profile.get("buy_threshold_multiplier", 1.0))

        # ── Derived conditions ────────────────────────────────────────────────
        ema_bullish   = ema_s > ema_l
        ema_bearish   = ema_s < ema_l
        below_vwap    = current_price < vwap
        above_vwap    = current_price > vwap
        near_bb_lower = current_price <= bb_lower * 1.005      # within 0.5% of lower band
        near_bb_upper = current_price >= bb_upper * 0.995      # within 0.5% of upper band
        vol_ok        = vol_ratio >= 1.1                        # above-average volume
        rsi_ok_buy    = 35 < rsi < 65                           # not extended in either direction
        rsi_ok_sell   = rsi > 35                                # something to sell into

        # ── Signal rules ─────────────────────────────────────────────────────
        # BUY: model bullish + price below VWAP (value zone) + EMA trend up +
        #      RSI neutral + volume confirms + not already above BB upper
        if (
            effective_pred_chg_pct >= dynamic_buy_threshold
            and ema_bullish
            and (below_vwap or near_bb_lower)
            and rsi_ok_buy
            and vol_ok
            and not near_bb_upper
        ):
            signal = BUY

        # SELL: model bearish + price above VWAP (premium zone) + EMA trend down
        elif (
            effective_pred_chg_pct <= dynamic_sell_threshold
            and ema_bearish
            and (above_vwap or near_bb_upper)
            and rsi_ok_sell
            and vol_ok
            and not near_bb_lower
        ):
            signal = SELL

        else:
            signal = HOLD

        vwap_pos = "below" if below_vwap else "above"
        return {
            "symbol":          symbol,
            "signal":          signal,
            "current_price":   current_price,
            "predicted_close": predicted,
            "predicted_chg_pct": pred_chg_pct,
            "learned_edge_adjustment_pct": learned_edge_adjustment_pct,
            "effective_predicted_chg_pct": effective_pred_chg_pct,
            "rsi":             rsi,
            "ema_cross":       "bullish" if ema_bullish else "bearish",
            "vwap_position":   vwap_pos,
            "bb_position":     "lower" if near_bb_lower else ("upper" if near_bb_upper else "mid"),
            "atr":             atr,
            "volume_ratio":    vol_ratio,
            "topic_scores":    topic_scores,
            "external_research_score": external_research_score,
        }

    def execute(self, analysis: dict, symbol: str, broker) -> Optional[dict]:
        """
        Apply the signal to the broker.  Returns a trade dict or None.
        Enforces: cooldown, max positions, stop-loss, take-profit guards.
        """
        signal = analysis.get("signal", HOLD)
        if signal == HOLD:
            return None

        # ── Cooldown check ────────────────────────────────────────────────────
        now = time.time()
        if now - self._last_trade.get(symbol, 0) < TRADE_COOLDOWN_SECS:
            return None

        price   = analysis["current_price"]
        atr     = analysis["atr"]
        equity  = broker.get_portfolio_value()
        profile = self.autonomy_profile

        if signal == BUY:
            if symbol in self.blocked_symbols_by_improvement:
                return None
            if not bool(profile.get("allow_new_entries", True)):
                return None
            # ── Check open position count ─────────────────────────────────────
            positions = broker.get_positions()
            effective_max_positions = max(1, int(MAX_POSITIONS * float(profile.get("max_positions_multiplier", 1.0))))
            if len(positions) >= effective_max_positions:
                return None

            # ── Already long this symbol ──────────────────────────────────────
            if symbol in positions and positions[symbol]["qty"] > 0:
                return None

            # ── Position size via ATR-based risk ──────────────────────────────
            stop_distance = max(price * STOP_LOSS_PCT, atr)
            shares = int((equity * RISK_PER_TRADE * float(profile.get("risk_multiplier", 1.0)) * float(self.symbol_risk_multipliers.get(symbol, 1.0))) / stop_distance)
            # Cap at 25% of equity in one position
            max_shares = int((equity * 0.25) / price)
            shares = max(1, min(shares, max_shares))

            stop_price   = price * (1 - STOP_LOSS_PCT)
            target_price = price * (1 + TAKE_PROFIT_PCT)

            result = broker.buy(symbol, shares, stop_price, target_price)
            if result:
                self._last_trade[symbol] = now
                return {
                    "action": "BUY", "symbol": symbol,
                    "qty": shares, "price": result["fill_price"],
                    "stop": stop_price, "target": target_price, "atr": atr,
                }

        elif signal == SELL:
            # Only sell if we actually hold shares (day-trading long-only)
            positions = broker.get_positions()
            if symbol not in positions or positions[symbol]["qty"] <= 0:
                return None

            shares = positions[symbol]["qty"]
            entry_price = float(positions[symbol].get("avg_cost", price))
            result = broker.sell(symbol, shares)
            if result:
                self._last_trade[symbol] = now
                pnl = float(result.get("pnl", (float(result.get("fill_price", price)) - entry_price) * shares))
                self.trade_history.append({"ts": datetime.now(timezone.utc), "symbol": symbol, "pnl": pnl})
                return {
                    "action": "SELL", "symbol": symbol,
                    "qty": shares, "price": result["fill_price"], "atr": atr,
                }

        return None

    def close_all_positions(self, broker) -> list[dict]:
        """Close every open position — called at EOD."""
        closed = []
        for symbol, pos in broker.get_positions().items():
            if pos["qty"] > 0:
                result = broker.sell(symbol, pos["qty"])
                if result:
                    closed.append({"symbol": symbol, "qty": pos["qty"],
                                   "price": result["fill_price"]})
        return closed

    def is_eod_close_time(self) -> bool:
        """Return True when it's time to close all intraday positions."""
        now = datetime.now(tz=timezone.utc).astimezone(_ASX_TZ)
        return (
            now.hour == EOD_CLOSE_HOUR
            and now.minute >= EOD_CLOSE_MIN
        ) or now.hour > EOD_CLOSE_HOUR

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_model(self, symbol: str, df: pd.DataFrame) -> ASXModel:
        if symbol not in self._models:
            m = ASXModel(symbol)
            if not m.load():
                print(f"  [Strategy] Training initial model for {symbol} on {len(df)} bars…")
                m.train(df, epochs=40)
            self._models[symbol] = m
            self._bars_seen[symbol] = 0
        return self._models[symbol]

    def _maybe_retrain(self, symbol: str, model: ASXModel, df: pd.DataFrame) -> None:
        self._bars_seen[symbol] = self._bars_seen.get(symbol, 0) + 1
        if self._bars_seen[symbol] >= RETRAIN_EVERY_N_BARS:
            print(f"  [Strategy] Scheduled retrain for {symbol} ({self._bars_seen[symbol]} bars seen)…")
            model.train(df, epochs=20)
            self._bars_seen[symbol] = 0
