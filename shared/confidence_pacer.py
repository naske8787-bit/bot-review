"""Reliability-driven capital pacing for position sizing.

This module adjusts a multiplicative sizing factor based on recent execution
quality, drift status, and promotion stage. It does not alter core signal
generation; it only scales capital deployment.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Tuple


_DEFAULT_MIN_SAMPLE = int(os.getenv("CAPITAL_PACING_MIN_SAMPLE", "10"))
_DEFAULT_EMA_ALPHA = float(os.getenv("CAPITAL_PACING_EMA_ALPHA", "0.35"))
_DEFAULT_MIN_MULT = float(os.getenv("CAPITAL_PACING_MIN_MULT", "0.25"))
_DEFAULT_MAX_MULT = float(os.getenv("CAPITAL_PACING_MAX_MULT", "1.15"))
_DEFAULT_WARMUP_MULT = float(os.getenv("CAPITAL_PACING_WARMUP_MULT", "0.85"))


class ConfidenceCapitalPacer:
    def __init__(self, bot_name: str, state_dir: str):
        self.bot_name = str(bot_name)
        self.state_dir = state_dir
        os.makedirs(self.state_dir, exist_ok=True)
        self.state_path = os.path.join(self.state_dir, f"capital_pacing_state_{self.bot_name}.json")

        self.multiplier = 1.0
        self.target_multiplier = 1.0
        self.last_update_ts = 0.0
        self.last_reasons: List[str] = []
        self.last_metrics: Dict = {}
        self.events: List[Dict] = []
        self._max_events = 40
        self._load()

    def update(self, exec_metrics: Dict, drift_state: Dict, pipeline_stage: str = "live") -> Tuple[float, List[str]]:
        now = time.time()
        metrics = exec_metrics or {}
        sample_size = int(metrics.get("sample_size") or 0)
        rejection_rate = float(metrics.get("rejection_rate") or 0.0)
        avg_slippage = metrics.get("avg_slippage_bps")
        avg_slippage_bps = float(avg_slippage) if avg_slippage is not None else 0.0
        p95_slippage = metrics.get("p95_slippage_bps")
        p95_slippage_bps = float(p95_slippage) if p95_slippage is not None else 0.0
        p95_delay = metrics.get("p95_fill_delay_s")
        p95_fill_delay_s = float(p95_delay) if p95_delay is not None else 0.0
        fill_confirmed_rate = float(metrics.get("fill_confirmed_rate") or 0.0)

        min_sample = max(1, _DEFAULT_MIN_SAMPLE)
        reasons: List[str] = []

        if sample_size < min_sample:
            target = _DEFAULT_WARMUP_MULT
            reasons.append(f"warmup_sample<{min_sample}")
        else:
            target = 1.0

            if rejection_rate > 0.25:
                target = min(target, 0.35)
                reasons.append("reject_rate_critical")
            elif rejection_rate > 0.10:
                target = min(target, 0.65)
                reasons.append("reject_rate_elevated")

            if p95_slippage_bps > 100.0:
                target = min(target, 0.40)
                reasons.append("p95_slippage_critical")
            elif avg_slippage_bps > 30.0:
                target = min(target, 0.75)
                reasons.append("avg_slippage_elevated")

            if p95_fill_delay_s > 20.0:
                target = min(target, 0.50)
                reasons.append("fill_delay_critical")
            elif p95_fill_delay_s > 12.0:
                target = min(target, 0.80)
                reasons.append("fill_delay_elevated")

            if fill_confirmed_rate < 0.60:
                target = min(target, 0.70)
                reasons.append("fill_confirm_low")

        if bool((drift_state or {}).get("drift_active", False)):
            target *= 0.85
            reasons.append("drift_penalty")

        stage = str(pipeline_stage or "live").lower()
        if stage == "canary":
            target = min(target, 0.85)
            reasons.append("canary_cap")
        elif stage == "shadow":
            target = min(target, 0.25)
            reasons.append("shadow_cap")

        target = max(_DEFAULT_MIN_MULT, min(_DEFAULT_MAX_MULT, target))
        alpha = max(0.05, min(1.0, _DEFAULT_EMA_ALPHA))
        prev = float(self.multiplier)
        new_mult = (alpha * target) + ((1.0 - alpha) * prev)
        new_mult = max(_DEFAULT_MIN_MULT, min(_DEFAULT_MAX_MULT, new_mult))

        if abs(new_mult - prev) >= 0.10:
            self.events.append(
                {
                    "ts": now,
                    "prev_multiplier": round(prev, 4),
                    "new_multiplier": round(new_mult, 4),
                    "target_multiplier": round(target, 4),
                    "reasons": reasons,
                }
            )
            if len(self.events) > self._max_events:
                self.events = self.events[-self._max_events :]

        self.multiplier = float(new_mult)
        self.target_multiplier = float(target)
        self.last_update_ts = now
        self.last_reasons = reasons
        self.last_metrics = {
            "sample_size": sample_size,
            "rejection_rate": rejection_rate,
            "avg_slippage_bps": avg_slippage if avg_slippage is not None else None,
            "p95_slippage_bps": p95_slippage if p95_slippage is not None else None,
            "p95_fill_delay_s": p95_delay if p95_delay is not None else None,
            "fill_confirmed_rate": fill_confirmed_rate,
            "pipeline_stage": stage,
        }
        return self.multiplier, self.last_reasons

    def get_state(self) -> Dict:
        return {
            "bot_name": self.bot_name,
            "multiplier": round(float(self.multiplier), 6),
            "target_multiplier": round(float(self.target_multiplier), 6),
            "last_update_ts": self.last_update_ts,
            "last_reasons": list(self.last_reasons),
            "last_metrics": dict(self.last_metrics),
            "events": self.events[-10:],
            "state_path": self.state_path,
        }

    def save(self) -> None:
        try:
            tmp_path = self.state_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.get_state(), f)
            os.replace(tmp_path, self.state_path)
        except OSError:
            pass

    def _load(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.multiplier = float(payload.get("multiplier") or self.multiplier)
            self.target_multiplier = float(payload.get("target_multiplier") or self.target_multiplier)
            self.last_update_ts = float(payload.get("last_update_ts") or 0.0)
            if isinstance(payload.get("last_reasons"), list):
                self.last_reasons = payload.get("last_reasons")
            if isinstance(payload.get("last_metrics"), dict):
                self.last_metrics = payload.get("last_metrics")
            if isinstance(payload.get("events"), list):
                self.events = payload.get("events")[-self._max_events :]
        except (OSError, ValueError, TypeError):
            pass


def load_capital_pacing_state(state_dir: str, bot_name: str) -> Dict:
    path = os.path.join(state_dir, f"capital_pacing_state_{bot_name}.json")
    if not os.path.exists(path):
        return {
            "bot_name": bot_name,
            "multiplier": 1.0,
            "target_multiplier": 1.0,
            "last_update_ts": None,
            "last_reasons": [],
            "last_metrics": {},
            "events": [],
            "state_path": path,
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {
            "bot_name": bot_name,
            "multiplier": 1.0,
            "target_multiplier": 1.0,
            "error": "state_unreadable",
            "state_path": path,
        }
