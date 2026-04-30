import csv
import json
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from config import (
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
)


def _parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


class AutonomousDecisionEngine:
    """Scores recent performance and returns an execution profile for autonomous trading."""

    def __init__(self, trade_log_path, equity_log_path):
        self.trade_log_path = trade_log_path
        self.equity_log_path = equity_log_path
        self.learning_enabled = bool(AUTONOMY_LEARNING_ENABLED)
        self.state_path = os.path.join(os.path.dirname(trade_log_path), "..", "models", "autonomy_state.json")
        self.state = {
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
            "dynamic_offsets": {
                "risk": 0.0,
                "buy_threshold": 0.0,
                "max_positions": 0.0,
            },
        }
        self._load_state()

    @staticmethod
    def _clamp(value, lo, hi):
        return max(lo, min(hi, float(value)))

    def _load_state(self):
        if not self.learning_enabled or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                self.state.update(payload)
        except Exception:
            return

    def _persist_state(self):
        if not self.learning_enabled:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, sort_keys=True)
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

        last_mode = str(self.state.get("last_mode") or "normal")
        prev_pnl = _to_float(self.state.get("last_realized_pnl_7d"), 0.0)
        prev_dd = _to_float(self.state.get("last_drawdown_7d"), 0.0)
        delta_pnl = realized_pnl_7d - prev_pnl
        delta_dd = drawdown_7d - prev_dd

        mode_stats = dict(self.state.get("mode_stats") or {})
        stats = self._safe_mode_stats(mode_stats, last_mode)

        if delta_pnl <= float(AUTONOMY_LOSS_EVENT_MIN_PNL) or (delta_pnl < 0 and delta_dd > 0.005):
            stats["losses"] += 1
            stats["pnl_sum"] += delta_pnl
            reasons.append(
                f"learning update: {last_mode} underperformed (delta_pnl={delta_pnl:.2f}, delta_dd={delta_dd:.2%})"
            )
            if last_mode == "aggressive":
                cooldown_until = now + timedelta(hours=max(1, AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS))
                self.state["aggressive_cooldown_until"] = cooldown_until.isoformat()
                reasons.append(
                    f"aggressive cooldown enabled for {AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS}h after loss event"
                )
        elif delta_pnl >= float(AUTONOMY_RECOVERY_EVENT_MIN_PNL):
            stats["wins"] += 1
            stats["pnl_sum"] += delta_pnl
            reasons.append(f"learning update: {last_mode} delivered positive outcome (delta_pnl={delta_pnl:.2f})")

        mode_stats[last_mode] = stats
        self.state["mode_stats"] = mode_stats
        return reasons

    def _mode_confidence_penalty(self, mode):
        mode_stats = dict(self.state.get("mode_stats") or {})
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

    def _read_csv(self, path):
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8", newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            return []

    def _closed_trade_pnls(self, trades, since_ts):
        buys = defaultdict(deque)
        pnls = []
        pnls_by_symbol = defaultdict(list)

        for row in trades:
            ts = _parse_ts(row.get("timestamp"))
            if not ts or ts < since_ts:
                continue
            action = str(row.get("action", "")).upper()
            symbol = str(row.get("symbol", "")).upper()
            qty = max(0.0, _to_float(row.get("qty"), 0.0))
            price = _to_float(row.get("price"), 0.0)
            if not symbol or qty <= 0 or price <= 0:
                continue

            if action == "BUY":
                buys[symbol].append({"qty": qty, "price": price})
                continue

            if action != "SELL":
                continue

            remaining = qty
            pnl = 0.0
            while remaining > 0 and buys[symbol]:
                lot = buys[symbol][0]
                matched = min(remaining, lot["qty"])
                pnl += (price - lot["price"]) * matched
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= 1e-9:
                    buys[symbol].popleft()

            if pnl != 0.0:
                pnls.append(pnl)
                pnls_by_symbol[symbol].append(pnl)

        return pnls, pnls_by_symbol

    @staticmethod
    def _drawdown_7d(equity_rows, since_ts):
        values = []
        for row in equity_rows:
            ts = _parse_ts(row.get("timestamp"))
            if not ts or ts < since_ts:
                continue
            v = _to_float(row.get("portfolio_value"), 0.0)
            if v > 0:
                values.append(v)
        if len(values) < 2:
            return 0.0

        peak = values[0]
        max_dd = 0.0
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd

    def evaluate(self, research_payload=None):
        now = datetime.now(timezone.utc)
        since_ts = now - timedelta(days=7)
        trades = self._read_csv(self.trade_log_path)
        equity = self._read_csv(self.equity_log_path)
        pnls, pnls_by_symbol = self._closed_trade_pnls(trades, since_ts)

        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        closed = len(pnls)
        win_rate = (len(wins) / closed) if closed else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
        realized_pnl_7d = sum(pnls)
        drawdown_7d = self._drawdown_7d(equity, since_ts)

        # External research score contributes but does not override poor execution metrics.
        research_score = _to_float((research_payload or {}).get("score"), 0.0)

        score = 0
        reasons = []

        if closed >= AUTONOMOUS_MIN_CLOSED_TRADES:
            score += 12
            reasons.append(f"closed trades in 7d={closed} (min {AUTONOMOUS_MIN_CLOSED_TRADES})")
        else:
            score -= 8
            reasons.append(f"insufficient closed trades in 7d={closed}")

        # Cold-start grace: when there are zero closed trades the win_rate and
        # profit_factor are mathematically undefined (0/0 = NaN, not "0%").
        # Penalising undefined metrics would permanently lock the bot in
        # capital_preservation before it has had a chance to trade at all.
        # Instead, treat both as neutral (0 points) when closed == 0.
        if closed == 0:
            reasons.append("win rate / profit factor undefined (no closed trades yet) — neutral")
        else:
            if win_rate >= AUTONOMOUS_MIN_WIN_RATE:
                score += 10
                reasons.append(f"win rate {win_rate:.1%} >= {AUTONOMOUS_MIN_WIN_RATE:.1%}")
            else:
                score -= 8
                reasons.append(f"win rate {win_rate:.1%} below target")

            if profit_factor >= AUTONOMOUS_MIN_PROFIT_FACTOR:
                score += 10
                reasons.append(f"profit factor {profit_factor:.2f} >= {AUTONOMOUS_MIN_PROFIT_FACTOR:.2f}")
            else:
                score -= 8
                reasons.append(f"profit factor {profit_factor:.2f} below target")

        if realized_pnl_7d >= AUTONOMOUS_MIN_REALIZED_PNL_7D:
            score += 8
            reasons.append(f"realized pnl 7d {realized_pnl_7d:.2f} meets target")
        else:
            score -= 8
            reasons.append(f"realized pnl 7d {realized_pnl_7d:.2f} below target")

        if drawdown_7d <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT:
            score += 8
            reasons.append(f"drawdown 7d {drawdown_7d:.2%} within cap")
        else:
            score -= 12
            reasons.append(f"drawdown 7d {drawdown_7d:.2%} above cap")

        if research_score > 3:
            score += 4
            reasons.append("external macro/policy/technology research is supportive")
        elif research_score < -3:
            score -= 4
            reasons.append("external research indicates elevated macro headwinds")

        learning_reasons = self._update_mode_learning(now, realized_pnl_7d, drawdown_7d)
        reasons.extend(learning_reasons)

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
        passed_checks += 1 if drawdown_7d <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT else 0
        confidence = passed_checks / 5.0

        cooldown_until = self._parse_state_ts(self.state.get("aggressive_cooldown_until"))
        aggressive_cooldown_active = bool(cooldown_until and cooldown_until > now)

        if score >= 28:
            mode = "aggressive"
            allow_new_entries = True
            risk_multiplier = 1.25
            buy_threshold_multiplier = 0.85
            max_positions_multiplier = 1.10
        elif score >= 14:
            mode = "normal"
            allow_new_entries = True
            risk_multiplier = 1.00
            buy_threshold_multiplier = 1.00
            max_positions_multiplier = 1.00
        elif score >= 4:
            mode = "cautious"
            allow_new_entries = True
            risk_multiplier = 0.60
            buy_threshold_multiplier = 1.10
            max_positions_multiplier = 0.80
        else:
            mode = "capital_preservation"
            allow_new_entries = False
            risk_multiplier = 0.0
            buy_threshold_multiplier = 10.0
            max_positions_multiplier = 0.50

        if mode == "aggressive":
            if aggressive_cooldown_active:
                mode = "cautious"
                allow_new_entries = True
                risk_multiplier = 0.60
                buy_threshold_multiplier = 1.10
                max_positions_multiplier = 0.80
                reasons.append("aggressive blocked during cooldown after recent underperformance")
            elif closed < max(1, AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES):
                mode = "normal"
                allow_new_entries = True
                risk_multiplier = 1.00
                buy_threshold_multiplier = 1.00
                max_positions_multiplier = 1.00
                reasons.append(
                    f"aggressive held back until closed trades >= {AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES}"
                )
            elif confidence < max(0.0, min(1.0, AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE)):
                mode = "normal"
                allow_new_entries = True
                risk_multiplier = 1.00
                buy_threshold_multiplier = 1.00
                max_positions_multiplier = 1.00
                reasons.append(
                    f"aggressive held back: confidence {confidence:.0%} below {AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE:.0%}"
                )

        # Autonomous guardrail tuning: bounded and stateful for smoother adaptation.
        offsets = dict(self.state.get("dynamic_offsets") or {})
        risk_offset = _to_float(offsets.get("risk"), 0.0)
        buy_offset = _to_float(offsets.get("buy_threshold"), 0.0)
        max_pos_offset = _to_float(offsets.get("max_positions"), 0.0)

        if AUTONOMY_DYNAMIC_TUNING_ENABLED:
            step = max(0.01, float(AUTONOMY_DYNAMIC_STEP))
            robust_sample = closed >= max(4, AUTONOMOUS_MIN_CLOSED_TRADES)
            quality_good = (
                robust_sample
                and profit_factor >= max(1.0, AUTONOMOUS_MIN_PROFIT_FACTOR)
                and drawdown_7d <= AUTONOMOUS_MAX_DRAWDOWN_7D_PCT
                and win_rate >= max(0.45, AUTONOMOUS_MIN_WIN_RATE - 0.03)
                and research_score > -2.0
            )
            quality_weak = (
                robust_sample
                and (
                    profit_factor < 0.95
                    or drawdown_7d > AUTONOMOUS_MAX_DRAWDOWN_7D_PCT * 1.15
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

            # Hard failsafe rollback on sustained deterioration.
            if closed >= 8 and (profit_factor < 0.90 or drawdown_7d > AUTONOMY_FAILSAFE_DRAWDOWN_PCT):
                allow_new_entries = False
                mode = "capital_preservation"
                risk_multiplier = 0.0
                buy_threshold_multiplier = 10.0
                max_positions_multiplier = 0.50
                risk_offset = min(risk_offset, -0.20)
                buy_offset = max(buy_offset, 0.25)
                max_pos_offset = min(max_pos_offset, -0.20)
                reasons.append("failsafe rollback: pausing new entries after weak realized quality")

        risk_multiplier = self._clamp(risk_multiplier + risk_offset, AUTONOMY_RISK_MULT_MIN, AUTONOMY_RISK_MULT_MAX)
        buy_threshold_multiplier = self._clamp(
            buy_threshold_multiplier + buy_offset,
            AUTONOMY_BUY_THRESHOLD_MULT_MIN,
            AUTONOMY_BUY_THRESHOLD_MULT_MAX,
        )
        max_positions_multiplier = self._clamp(
            max_positions_multiplier + max_pos_offset,
            AUTONOMY_MAX_POSITIONS_MULT_MIN,
            AUTONOMY_MAX_POSITIONS_MULT_MAX,
        )

        if not allow_new_entries:
            risk_multiplier = 0.0

        self.state["dynamic_offsets"] = {
            "risk": risk_offset,
            "buy_threshold": buy_offset,
            "max_positions": max_pos_offset,
        }

        weak_symbols = []
        for symbol, vals in pnls_by_symbol.items():
            if vals and (sum(vals) / len(vals)) < 0:
                weak_symbols.append(symbol)

        self.state["last_mode"] = mode
        self.state["last_realized_pnl_7d"] = realized_pnl_7d
        self.state["last_drawdown_7d"] = drawdown_7d
        self._persist_state()

        return {
            "mode": mode,
            "score": score,
            "allow_new_entries": allow_new_entries,
            "risk_multiplier": risk_multiplier,
            "buy_threshold_multiplier": buy_threshold_multiplier,
            "max_positions_multiplier": max_positions_multiplier,
            "blocked_symbols": sorted(weak_symbols)[:6],
            "metrics": {
                "closed_trades_7d": closed,
                "win_rate_7d": win_rate,
                "profit_factor_7d": profit_factor,
                "realized_pnl_7d": realized_pnl_7d,
                "max_drawdown_7d": drawdown_7d,
                "research_score": research_score,
                "confidence": confidence,
            },
            "reasons": reasons,
        }
