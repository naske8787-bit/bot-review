"""
Trading strategy for forex.

Signal logic combines:
  1. LSTM predicted next-close vs current price  (directional bias)
  2. EMA crossover confirmation                  (trend filter)
  3. RSI overbought/oversold guard               (avoid chasing extremes)
  4. ATR-based position sizing                   (risk-proportional lot size)
"""
from __future__ import annotations

import time
import json
import os
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone

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
    EMA_LONG,
    EMA_SHORT,
    INITIAL_TRAIN_BARS,
    LOOKBACK_BARS,
    MAX_POSITIONS,
    RETRAIN_EVERY_N_BARS,
    RISK_PER_TRADE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRADE_COOLDOWN_SECS,
)
from data_fetcher import fetch_bars, fetch_external_research_sentiment, fetch_latest_price
from model import ForexModel

BUY  = "BUY"
SELL = "SELL"
HOLD = "HOLD"


class ForexStrategy:
    def __init__(self):
        self._models:       Dict[str, ForexModel] = {}
        self._bar_buffers:  Dict[str, pd.DataFrame] = {}
        self._last_trade:   Dict[str, float] = {}
        self._bars_seen:    Dict[str, int] = {}
        self._entry_book:   Dict[str, dict] = {}
        self.trade_history = []
        self.portfolio_history = []
        self.pair_risk_multipliers: Dict[str, float] = {}
        self.blocked_pairs_by_improvement: set[str] = set()
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

    def observe_portfolio_value(self, value):
        self.portfolio_history.append({"ts": datetime.now(timezone.utc), "value": float(value)})
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self.portfolio_history = [x for x in self.portfolio_history if x["ts"] >= cutoff]

    def apply_autonomy_profile(self, profile):
        if isinstance(profile, dict):
            self.autonomy_profile.update(profile)

    def _pair_trade_stats(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=AUTO_IMPROVEMENT_LOOKBACK_DAYS)
        by_pair: Dict[str, list[float]] = {}
        for t in self.trade_history:
            if t["ts"] < cutoff:
                continue
            pair = str(t.get("pair") or "").upper()
            if not pair:
                continue
            by_pair.setdefault(pair, []).append(float(t.get("pnl") or 0.0))

        out: Dict[str, dict] = {}
        for pair, pnls in by_pair.items():
            if not pnls:
                continue
            wins = [x for x in pnls if x > 0]
            losses = [x for x in pnls if x < 0]
            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
            out[pair] = {
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

        stats = self._pair_trade_stats()
        if not stats:
            self.last_improvement_rebalance_ts = now
            return ["Auto-improvement: not enough closed-trade history yet."]

        eligible = [
            (pair, s) for pair, s in stats.items()
            if int(s.get("trades", 0)) >= AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL
        ]
        if not eligible:
            self.last_improvement_rebalance_ts = now
            return ["Auto-improvement: waiting for more closes per pair before tuning."]

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
        for i, (pair, s) in enumerate(ranked):
            if i == 0:
                mult = 1.25
            elif i < max(1, n // 2):
                mult = 1.05
            elif i == n - 1:
                mult = 0.55
            else:
                mult = 0.80

            if float(s.get("expectancy", 0.0)) < 0 and float(s.get("profit_factor", 0.0)) < 0.9:
                new_blocked.add(pair)
                mult = 0.0
            new_multipliers[pair] = mult

        self.pair_risk_multipliers = new_multipliers
        self.blocked_pairs_by_improvement = new_blocked
        self.last_improvement_rebalance_ts = now

        top = ", ".join([f"{pair} x{new_multipliers.get(pair, 1.0):.2f}" for pair, _ in ranked[:4]]) or "none"
        blocked = ", ".join(sorted(new_blocked)) or "none"
        return [
            "Auto-improvement applied (daily return-per-risk rebalance).",
            f"Top allocations: {top}",
            f"Underperformer cap/block list: {blocked}",
        ]

    def evaluate_autonomy_profile(self, research_payload=None):
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=7)
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
            profile = {"allow_new_entries": True, "risk_multiplier": 1.25, "max_positions_multiplier": 1.1, "buy_threshold_multiplier": 0.85}
        elif score >= 14:
            mode = "normal"
            profile = {"allow_new_entries": True, "risk_multiplier": 1.0, "max_positions_multiplier": 1.0, "buy_threshold_multiplier": 1.0}
        elif score >= 4:
            mode = "cautious"
            profile = {"allow_new_entries": True, "risk_multiplier": 0.6, "max_positions_multiplier": 0.8, "buy_threshold_multiplier": 1.2}
        else:
            mode = "capital_preservation"
            profile = {"allow_new_entries": False, "risk_multiplier": 0.0, "max_positions_multiplier": 0.6, "buy_threshold_multiplier": 1.6}

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

        by_pair = {}
        for t in recent:
            by_pair.setdefault(t["pair"], []).append(t["pnl"])
        blocked = sorted([s for s, vals in by_pair.items() if vals and (sum(vals) / len(vals)) < 0])[:6]

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

    # ── Public interface ─────────────────────────────────────────────────────

    def analyse(self, pair: str) -> dict:
        """
        Fetch the latest bars, run indicators and the LSTM, and return a
        dict with keys: signal, predicted_close, current_price, confidence,
        ema_cross, rsi, atr.
        """
        df = fetch_bars(pair, lookback_bars=max(LOOKBACK_BARS + 50, INITIAL_TRAIN_BARS))
        if df.empty or len(df) < LOOKBACK_BARS:
            return {"signal": HOLD, "reason": "insufficient_data"}

        model = self._get_model(pair, df)
        self._maybe_retrain(pair, model, df)
        model.update(df)

        current_price = float(df["Close"].iloc[-1])
        predicted     = model.predict_next_close(df)

        ema_short_val = float(df[f"EMA_{EMA_SHORT}"].iloc[-1])
        ema_long_val  = float(df[f"EMA_{EMA_LONG}"].iloc[-1])
        rsi           = float(df["RSI"].iloc[-1])
        atr           = float(df["ATR"].iloc[-1])

        # Directional prediction
        if predicted is None:
            return {"signal": HOLD, "reason": "model_not_ready"}

        predicted_change_pct = (predicted - current_price) / current_price * 100.0
        ema_bullish  = ema_short_val > ema_long_val
        ema_bearish  = ema_short_val < ema_long_val
        rsi_ok_buy   = rsi < 70
        rsi_ok_sell  = rsi > 30

        profile = self.autonomy_profile
        external_research = fetch_external_research_sentiment()
        external_research_score = float(external_research.get("score", 0.0))
        blocked_pairs = set(profile.get("blocked_symbols", []) or []) | set(self.blocked_pairs_by_improvement)
        if pair in blocked_pairs:
            return {"signal": HOLD, "reason": "blocked_pair", "external_research_score": external_research_score}

        threshold = 0.05 * float(profile.get("buy_threshold_multiplier", 1.0))

        # Signal rules
        if predicted_change_pct > threshold and ema_bullish and rsi_ok_buy:
            signal = BUY
        elif predicted_change_pct < -threshold and ema_bearish and rsi_ok_sell:
            signal = SELL
        else:
            signal = HOLD

        if external_research_score <= -4 and signal == BUY:
            signal = HOLD

        return {
            "signal":            signal,
            "current_price":     current_price,
            "predicted_close":   predicted,
            "predicted_chg_pct": round(predicted_change_pct, 4),
            "ema_cross":         "bullish" if ema_bullish else "bearish",
            "rsi":               round(rsi, 2),
            "atr":               round(atr, 6),
            "external_research_score": external_research_score,
            "df":                df,
        }

    def execute(self, analysis: dict, pair: str, broker) -> Optional[dict]:
        """Place an order if conditions pass; returns trade dict or None."""
        signal = analysis.get("signal", HOLD)
        if signal == HOLD:
            return None

        # Cooldown check
        now = time.time()
        if now - self._last_trade.get(pair, 0) < TRADE_COOLDOWN_SECS:
            return None

        profile = self.autonomy_profile
        if signal == BUY and pair in self.blocked_pairs_by_improvement:
            return None
        if signal == BUY and not bool(profile.get("allow_new_entries", True)):
            return None

        # Max positions check
        effective_max_positions = max(1, int(MAX_POSITIONS * float(profile.get("max_positions_multiplier", 1.0))))
        if broker.get_open_positions_count() >= effective_max_positions:
            return None

        balance = broker.get_account_balance()
        atr     = analysis.get("atr", 0.0001)
        price   = analysis.get("current_price", 1.0)

        # ATR-based position sizing: risk RISK_PER_TRADE of balance per trade
        risk_amount = balance * RISK_PER_TRADE * float(profile.get("risk_multiplier", 1.0))
        risk_amount *= float(self.pair_risk_multipliers.get(pair, 1.0))
        stop_distance = max(atr * 1.5, price * STOP_LOSS_PCT)
        units = int(risk_amount / stop_distance) if stop_distance > 0 else 0
        if units <= 0:
            return None

        try:
            if signal == BUY:
                broker.buy(pair, units)
                self._entry_book[pair] = {"entry_price": price, "units": units, "ts": datetime.now(timezone.utc)}
            else:
                broker.sell(pair, units)
                entry = self._entry_book.get(pair, {})
                entry_price = float(entry.get("entry_price", price))
                pnl = (price - entry_price) * float(units)
                self.trade_history.append({"ts": datetime.now(timezone.utc), "pair": pair, "pnl": float(pnl)})
                self._entry_book.pop(pair, None)

            self._last_trade[pair] = now
            return {
                "action": signal,
                "pair":   pair,
                "units":  units,
                "price":  price,
                "atr":    atr,
            }
        except Exception as e:
            print(f"[Strategy] Order failed for {pair}: {e}")
            return None

    # ── Internals ────────────────────────────────────────────────────────────

    def _get_model(self, pair: str, df: pd.DataFrame) -> ForexModel:
        if pair not in self._models:
            m = ForexModel(pair)
            if not m.load():
                print(f"[Strategy] Training initial model for {pair}...")
                m.train(df)
            self._models[pair] = m
        return self._models[pair]

    def _maybe_retrain(self, pair: str, model: ForexModel, df: pd.DataFrame) -> None:
        self._bars_seen[pair] = self._bars_seen.get(pair, 0) + 1
        if self._bars_seen[pair] >= RETRAIN_EVERY_N_BARS:
            print(f"[Strategy] Scheduled retrain for {pair}...")
            model.train(df)
            self._bars_seen[pair] = 0
