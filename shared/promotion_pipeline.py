"""
Promotion pipeline: manages strategy lifecycle through shadow → canary → live stages.

Stages
------
shadow  - Signals are computed but NO orders are submitted. Every signal is
          written to logs/shadow_signals.jsonl for observation.
canary  - Orders ARE submitted, but position size is scaled by
          `canary_size_fraction` (default 20 %).  Auto-promotes to live once
          execution-quality thresholds are met; auto-rolls-back to shadow if
          rejection or slippage thresholds are breached.
live    - Normal full-size production mode.  (Default on first run.)

State persistence
-----------------
{state_dir}/promotion_state_{bot_name}.json

Usage (in strategy)
-------------------
    from promotion_pipeline import PromotionPipeline
    _pipeline = PromotionPipeline("trading", "/path/to/logs")

    # In execute_trade BUY block, after qty is computed:
    if _pipeline.stage == "shadow":
        _pipeline.log_shadow("BUY", symbol, qty, current_price)
        return None
    if _pipeline.stage == "canary":
        qty = max(1, int(qty * _pipeline.canary_size_fraction))

Usage (in main loop)
--------------------
    events = pipeline.evaluate_auto_advance(exec_metrics)
    for ev in events:
        print(f"Promotion pipeline: {ev}")

API helpers
-----------
    from promotion_pipeline import load_promotion_state
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

_VALID_STAGES = ("shadow", "canary", "live")

# ---------------------------------------------------------------------------
# Default thresholds (all overridable via env vars)
# ---------------------------------------------------------------------------
_DEFAULT_CANARY_SIZE_FRACTION  = float(os.getenv("CANARY_SIZE_FRACTION",  "0.20"))   # 20 % of normal sizing
_DEFAULT_CANARY_MIN_TRADES     = int(os.getenv("CANARY_MIN_TRADES",       "20"))      # min canary fills before auto-promote
_DEFAULT_CANARY_MAX_REJECT_RATE = float(os.getenv("CANARY_MAX_REJECT_RATE", "0.10")) # promote only if rejection ≤ this
_DEFAULT_CANARY_MAX_AVG_SLIP   = float(os.getenv("CANARY_MAX_AVG_SLIP",   "30.0"))   # promote only if avg slippage ≤ bps
_DEFAULT_CANARY_MAX_P95_DELAY  = float(os.getenv("CANARY_MAX_P95_DELAY",  "15.0"))   # promote only if p95 fill delay ≤ s
_DEFAULT_ROLLBACK_REJECT_RATE  = float(os.getenv("CANARY_ROLLBACK_REJECT_RATE", "0.25"))  # canary→shadow if > this
_DEFAULT_ROLLBACK_SLIP_BPS     = float(os.getenv("CANARY_ROLLBACK_SLIP_BPS",    "100.0")) # canary→shadow if p95 > this


class PromotionPipeline:
    """State machine: shadow → canary → live, with auto-advance and auto-rollback."""

    def __init__(
        self,
        bot_name: str,
        state_dir: str,
        canary_size_fraction: float = _DEFAULT_CANARY_SIZE_FRACTION,
        canary_min_trades: int = _DEFAULT_CANARY_MIN_TRADES,
        canary_max_reject_rate: float = _DEFAULT_CANARY_MAX_REJECT_RATE,
        canary_max_avg_slippage_bps: float = _DEFAULT_CANARY_MAX_AVG_SLIP,
        canary_max_p95_fill_delay_s: float = _DEFAULT_CANARY_MAX_P95_DELAY,
        rollback_reject_rate: float = _DEFAULT_ROLLBACK_REJECT_RATE,
        rollback_p95_slippage_bps: float = _DEFAULT_ROLLBACK_SLIP_BPS,
    ):
        self.bot_name = bot_name
        self.state_dir = state_dir
        self.canary_size_fraction = max(0.01, min(1.0, canary_size_fraction))
        self.canary_min_trades = max(1, canary_min_trades)
        self.canary_max_reject_rate = canary_max_reject_rate
        self.canary_max_avg_slippage_bps = canary_max_avg_slippage_bps
        self.canary_max_p95_fill_delay_s = canary_max_p95_fill_delay_s
        self.rollback_reject_rate = rollback_reject_rate
        self.rollback_p95_slippage_bps = rollback_p95_slippage_bps

        # Shadow signal log
        os.makedirs(state_dir, exist_ok=True)
        self._shadow_log_path = os.path.join(state_dir, "shadow_signals.jsonl")
        self._state_path = os.path.join(state_dir, f"promotion_state_{bot_name}.json")

        # Internal state
        self._stage: str = "live"
        self._stage_entered_ts: float = time.time()
        self._canary_trades_at_stage_entry: int = 0
        self._last_rollback_ts: float = 0.0
        self._events: List[Dict] = []          # ring-buffer of recent pipeline events
        self._max_events = 50

        self._load()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def stage(self) -> str:
        return self._stage

    # ------------------------------------------------------------------
    # Shadow logging
    # ------------------------------------------------------------------

    def log_shadow(
        self,
        action: str,
        symbol: str,
        qty: float,
        signal_price: float,
        reason: str = "",
    ) -> None:
        """Append one shadow signal to the JSONL log (no order is placed)."""
        record = {
            "ts": time.time(),
            "action": action,
            "symbol": symbol,
            "qty": qty,
            "signal_price": signal_price,
            "reason": reason,
        }
        try:
            with open(self._shadow_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Auto-advance / auto-rollback evaluation
    # ------------------------------------------------------------------

    def evaluate_auto_advance(self, exec_metrics: Dict) -> List[str]:
        """
        Inspect current execution-quality metrics and advance or roll back.

        Parameters
        ----------
        exec_metrics : dict
            Output of ``ExecutionQualityTracker.get_metrics()`` (or
            ``load_execution_metrics()``).  Expected keys:
            ``sample_size``, ``rejection_rate``, ``avg_slippage_bps``,
            ``p95_slippage_bps``, ``p95_fill_delay_s``.

        Returns
        -------
        list[str]
            Human-readable events that occurred this evaluation
            (e.g. ``["promoted_to_live"]``).
        """
        if not isinstance(exec_metrics, dict):
            return []

        events: List[str] = []

        # --- canary rollback check ---
        if self._stage == "canary":
            rejection_rate = float(exec_metrics.get("rejection_rate") or 0.0)
            p95_slip = exec_metrics.get("p95_slippage_bps")
            p95_slip_val = float(p95_slip) if p95_slip is not None else 0.0
            sample_size = int(exec_metrics.get("sample_size") or 0)

            if sample_size >= 5:  # only evaluate once we have a meaningful sample
                if rejection_rate > self.rollback_reject_rate:
                    reason = f"rejection_rate={rejection_rate:.2%} > threshold={self.rollback_reject_rate:.2%}"
                    self._set_stage("shadow", reason=reason)
                    events.append(f"rolled_back_to_shadow: {reason}")
                    self._last_rollback_ts = time.time()
                    self.save()
                    return events

                if p95_slip_val > self.rollback_p95_slippage_bps:
                    reason = f"p95_slippage_bps={p95_slip_val:.1f} > threshold={self.rollback_p95_slippage_bps:.1f}"
                    self._set_stage("shadow", reason=reason)
                    events.append(f"rolled_back_to_shadow: {reason}")
                    self._last_rollback_ts = time.time()
                    self.save()
                    return events

            # --- canary promote check ---
            canary_sample = max(0, sample_size - self._canary_trades_at_stage_entry)
            if canary_sample >= self.canary_min_trades:
                avg_slip = exec_metrics.get("avg_slippage_bps")
                avg_slip_val = float(avg_slip) if avg_slip is not None else 0.0
                p95_delay = exec_metrics.get("p95_fill_delay_s")
                p95_delay_val = float(p95_delay) if p95_delay is not None else 0.0

                if (
                    rejection_rate <= self.canary_max_reject_rate
                    and avg_slip_val <= self.canary_max_avg_slippage_bps
                    and p95_delay_val <= self.canary_max_p95_fill_delay_s
                ):
                    reason = (
                        f"canary_trades={canary_sample}>={self.canary_min_trades}, "
                        f"reject={rejection_rate:.2%}, "
                        f"avg_slip={avg_slip_val:.1f}bps, "
                        f"p95_delay={p95_delay_val:.1f}s"
                    )
                    self._set_stage("live", reason=reason)
                    events.append(f"promoted_to_live: {reason}")
                    self.save()

        if events:
            self.save()
        return events

    # ------------------------------------------------------------------
    # Manual stage override
    # ------------------------------------------------------------------

    def set_stage(self, stage: str, reason: str = "manual") -> None:
        if stage not in _VALID_STAGES:
            raise ValueError(f"Invalid stage '{stage}'. Must be one of {_VALID_STAGES}")
        self._set_stage(stage, reason=reason)
        self.save()

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def get_state(self) -> Dict:
        return {
            "bot_name": self.bot_name,
            "stage": self._stage,
            "stage_entered_ts": self._stage_entered_ts,
            "canary_size_fraction": self.canary_size_fraction,
            "canary_min_trades": self.canary_min_trades,
            "canary_trades_at_stage_entry": self._canary_trades_at_stage_entry,
            "last_rollback_ts": self._last_rollback_ts,
            "thresholds": {
                "canary_max_reject_rate": self.canary_max_reject_rate,
                "canary_max_avg_slippage_bps": self.canary_max_avg_slippage_bps,
                "canary_max_p95_fill_delay_s": self.canary_max_p95_fill_delay_s,
                "rollback_reject_rate": self.rollback_reject_rate,
                "rollback_p95_slippage_bps": self.rollback_p95_slippage_bps,
            },
            "recent_events": self._events[-10:],
            "shadow_log_path": self._shadow_log_path,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.get_state(), fh)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _set_stage(self, stage: str, reason: str = "") -> None:
        prev = self._stage
        self._stage = stage
        self._stage_entered_ts = time.time()
        event = {
            "ts": self._stage_entered_ts,
            "from": prev,
            "to": stage,
            "reason": reason,
        }
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    def _load(self) -> None:
        if not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            stage = str(data.get("stage", "live"))
            if stage in _VALID_STAGES:
                self._stage = stage
            self._stage_entered_ts = float(data.get("stage_entered_ts") or time.time())
            self._canary_trades_at_stage_entry = int(data.get("canary_trades_at_stage_entry") or 0)
            self._last_rollback_ts = float(data.get("last_rollback_ts") or 0.0)
            events = data.get("recent_events")
            if isinstance(events, list):
                self._events = events[-self._max_events:]
        except (OSError, ValueError, KeyError):
            pass


# ---------------------------------------------------------------------------
# API helper  (consumed by app/main.py)
# ---------------------------------------------------------------------------

def load_promotion_state(state_dir: str, bot_name: str) -> Dict:
    """Read persisted promotion state without instantiating a live pipeline."""
    path = os.path.join(state_dir, f"promotion_state_{bot_name}.json")
    if not os.path.exists(path):
        return {
            "bot_name": bot_name,
            "stage": "live",
            "stage_entered_ts": None,
            "canary_size_fraction": _DEFAULT_CANARY_SIZE_FRACTION,
            "canary_min_trades": _DEFAULT_CANARY_MIN_TRADES,
            "last_rollback_ts": None,
            "recent_events": [],
        }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"bot_name": bot_name, "stage": "live", "error": "state_unreadable"}
