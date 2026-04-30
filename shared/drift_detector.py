"""Drift detection for trading bots.

Three components:
  1. Feature PSI  — Population Stability Index on model input scalars.
  2. Calibration  — Rolling direction-prediction accuracy from closed trades.
  3. Regime decay — Detects dominant market regime change vs reference period.

De-risk multiplier returned by get_risk_multiplier():
  - No drift:                    1.00
  - Moderate PSI  (0.10 – 0.25): 0.65  (reduce sizing ~35%)
  - Severe   PSI  (>0.25):       0.35  (reduce sizing ~65%)
  - Calibration accuracy 0.40-0.48: 0.65
  - Calibration accuracy < 0.40:    0.35
  - Regime flip detected:        0.50
  - Combined floor:              0.25
"""

import csv
import json
import math
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

# ── PSI thresholds ──────────────────────────────────────────────────────────
_MODERATE_PSI = 0.10
_SEVERE_PSI   = 0.25

# ── Calibration thresholds ──────────────────────────────────────────────────
_CALIB_MOD_THRESHOLD = 0.48   # below this → moderate de-risk
_CALIB_SEV_THRESHOLD = 0.40   # below this → severe de-risk

# ── Window sizes ────────────────────────────────────────────────────────────
_REFERENCE_MIN_OBS = 40   # minimum observations before PSI is evaluated
_REFERENCE_MAX_OBS = 80   # reference window is frozen once full
_CURRENT_WINDOW    = 20   # rolling current-distribution window
_CALIB_WINDOW      = 20   # rolling calibration accuracy window
_REGIME_WINDOW     = 10   # rolling regime-label window

# ── PSI implementation ───────────────────────────────────────────────────────
_PSI_BUCKETS = 10
_PSI_EPSILON = 1e-6

# ── De-risk multipliers ──────────────────────────────────────────────────────
_MULT_MODERATE = 0.65
_MULT_SEVERE   = 0.35
_MULT_REGIME   = 0.50
_MULT_FLOOR    = 0.25


class DriftDetector:
    """Tracks feature drift, calibration drift, and regime decay.

    Usage (per main-loop iteration):
        detector.update_features({"predicted_change": 0.012, "trend_strength": 0.03, ...})
        detector.update_regime("bull")
        # periodically:
        detector.update_calibration_from_log("/path/to/trade_log.csv")
        strategy.drift_risk_multiplier = detector.get_risk_multiplier()
        detector.save()
    """

    def __init__(self, bot_name: str, state_dir: str) -> None:
        self.bot_name   = bot_name
        self.state_path = os.path.join(state_dir, f"drift_state_{bot_name}.json")

        # Feature windows
        self._reference: List[Dict[str, float]] = []
        self._current:   deque = deque(maxlen=_CURRENT_WINDOW)

        # Calibration pairs: (predicted_up: bool, actual_up: bool)
        self._calib: deque = deque(maxlen=_CALIB_WINDOW)

        # Regime labels
        self._regime_ref:     List[str] = []
        self._regime_current: deque     = deque(maxlen=_REGIME_WINDOW)

        # Computed state (invalidated on each update)
        self._state: Dict[str, Any] = _blank_state()
        self._dirty: bool = False

        self._load()

    # ── Public API ───────────────────────────────────────────────────────────

    def update_features(self, features: Dict[str, float]) -> None:
        """Call after each model prediction with derived scalar features."""
        clean = {
            k: float(v)
            for k, v in features.items()
            if isinstance(v, (int, float)) and math.isfinite(float(v))
        }
        if not clean:
            return
        self._current.append(clean)
        if len(self._reference) < _REFERENCE_MAX_OBS:
            self._reference.append(clean)
        self._dirty = True

    def update_calibration(self, predicted_up: bool, actual_up: bool) -> None:
        """Call when a trade closes to record direction prediction accuracy."""
        self._calib.append((bool(predicted_up), bool(actual_up)))
        self._dirty = True

    def update_calibration_from_log(self, trade_log_path: str) -> None:
        """Re-derive calibration window by pairing BUYs/SELLs in trade log CSV."""
        try:
            pairs = _extract_trade_pairs(trade_log_path)
        except Exception:
            return
        if not pairs:
            return
        recent = pairs[-_CALIB_WINDOW:]
        self._calib.clear()
        for pred_up, actual_up in recent:
            self._calib.append((pred_up, actual_up))
        self._dirty = True

    def update_regime(self, regime_label: str) -> None:
        """Call with the current market-regime label each loop iteration."""
        label = str(regime_label or "unknown")
        self._regime_current.append(label)
        if len(self._regime_ref) < _REFERENCE_MAX_OBS:
            self._regime_ref.append(label)
        self._dirty = True

    def get_risk_multiplier(self) -> float:
        """Return combined de-risk multiplier in [0.25, 1.0]."""
        if self._dirty:
            self._recompute()
        return float(self._state.get("combined_multiplier", 1.0))

    def get_state(self) -> Dict[str, Any]:
        """Return full drift-state dict for API reporting."""
        if self._dirty:
            self._recompute()
        return dict(self._state)

    def save(self) -> None:
        """Atomically persist state to JSON (safe to call every loop)."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(
                    {
                        "bot_name":        self.bot_name,
                        "state":           self._state,
                        "reference":       self._reference[-_REFERENCE_MAX_OBS:],
                        "calib":           list(self._calib),
                        "regime_ref":      self._regime_ref[-_REFERENCE_MAX_OBS:],
                        "regime_current":  list(self._regime_current),
                        "saved_at":        time.time(),
                    },
                    fh,
                )
            os.replace(tmp, self.state_path)
        except Exception:
            pass

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.state_path) as fh:
                payload = json.load(fh)
            self._reference       = payload.get("reference", [])
            raw_calib             = payload.get("calib", [])
            self._calib           = deque(
                [(bool(a), bool(b)) for a, b in raw_calib],
                maxlen=_CALIB_WINDOW,
            )
            self._regime_ref      = payload.get("regime_ref", [])
            self._regime_current  = deque(
                payload.get("regime_current", []), maxlen=_REGIME_WINDOW
            )
            self._state           = payload.get("state", _blank_state())
            self._dirty           = False
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            pass

    def _recompute(self) -> None:
        state = _blank_state()
        state["updated_at"]    = time.time()
        state["reference_obs"] = len(self._reference)
        state["current_obs"]   = len(self._current)

        # ── 1. Feature PSI ────────────────────────────────────────────────
        if (
            len(self._reference) >= _REFERENCE_MIN_OBS
            and len(self._current) >= max(5, _CURRENT_WINDOW // 2)
        ):
            all_keys = set(self._reference[0].keys()) if self._reference else set()
            psi_by_feature: Dict[str, float] = {}
            for key in all_keys:
                ref_vals = [obs[key] for obs in self._reference if key in obs]
                cur_vals = [obs[key] for obs in self._current  if key in obs]
                if len(ref_vals) < 5 or len(cur_vals) < 5:
                    continue
                psi = _compute_psi(ref_vals, cur_vals)
                psi_by_feature[key] = round(psi, 4)
            if psi_by_feature:
                max_psi = max(psi_by_feature.values())
                state["psi_max"]        = round(max_psi, 4)
                state["psi_by_feature"] = psi_by_feature
                if max_psi > _SEVERE_PSI:
                    state["psi_multiplier"] = _MULT_SEVERE
                    state["flags"].append(f"psi_severe({max_psi:.3f})")
                elif max_psi > _MODERATE_PSI:
                    state["psi_multiplier"] = _MULT_MODERATE
                    state["flags"].append(f"psi_moderate({max_psi:.3f})")

        # ── 2. Calibration accuracy ───────────────────────────────────────
        if len(self._calib) >= max(5, _CALIB_WINDOW // 2):
            correct = sum(1 for p, a in self._calib if p == a)
            acc = correct / len(self._calib)
            state["calibration_accuracy"] = round(acc, 4)
            if acc < _CALIB_SEV_THRESHOLD:
                state["calibration_multiplier"] = _MULT_SEVERE
                state["flags"].append(f"calib_severe(acc={acc:.2f})")
            elif acc < _CALIB_MOD_THRESHOLD:
                state["calibration_multiplier"] = _MULT_MODERATE
                state["flags"].append(f"calib_moderate(acc={acc:.2f})")

        # ── 3. Regime decay ───────────────────────────────────────────────
        if (
            len(self._regime_ref) >= 10
            and len(self._regime_current) >= max(3, _REGIME_WINDOW // 2)
        ):
            ref_dom = _dominant(list(self._regime_ref))
            cur_dom = _dominant(list(self._regime_current))
            if ref_dom and cur_dom and ref_dom != cur_dom:
                state["regime_ref_dominant"]     = ref_dom
                state["regime_current_dominant"] = cur_dom
                state["regime_flip"]             = True
                state["regime_multiplier"]       = _MULT_REGIME
                state["flags"].append(f"regime_flip({ref_dom}->{cur_dom})")

        # ── Combined multiplier ───────────────────────────────────────────
        combined = min(
            state["psi_multiplier"],
            state["calibration_multiplier"],
            state["regime_multiplier"],
        )
        combined = max(_MULT_FLOOR, combined)
        state["combined_multiplier"] = round(combined, 4)
        state["drift_active"]        = combined < 1.0

        self._state = state
        self._dirty = False


# ── Module-level helpers ─────────────────────────────────────────────────────

def _blank_state() -> Dict[str, Any]:
    return {
        "psi_max":                  0.0,
        "psi_by_feature":           {},
        "psi_multiplier":           1.0,
        "calibration_accuracy":     None,
        "calibration_multiplier":   1.0,
        "regime_flip":              False,
        "regime_ref_dominant":      None,
        "regime_current_dominant":  None,
        "regime_multiplier":        1.0,
        "combined_multiplier":      1.0,
        "drift_active":             False,
        "flags":                    [],
        "reference_obs":            0,
        "current_obs":              0,
        "updated_at":               None,
    }


def _compute_psi(
    reference: List[float],
    current:   List[float],
    buckets:   int = _PSI_BUCKETS,
) -> float:
    """Population Stability Index between two samples using equal-width bins."""
    all_vals = reference + current
    mn, mx   = min(all_vals), max(all_vals)
    if mx == mn:
        return 0.0
    width = (mx - mn) / buckets

    def bucket_fracs(vals: List[float]) -> List[float]:
        counts = [0] * buckets
        for v in vals:
            idx = min(buckets - 1, int((v - mn) / width))
            counts[idx] += 1
        n = len(vals)
        return [max(_PSI_EPSILON, c / n) for c in counts]

    ref_f = bucket_fracs(reference)
    cur_f = bucket_fracs(current)
    return sum((a - e) * math.log(a / e) for a, e in zip(cur_f, ref_f))


def _dominant(labels: List[str]) -> Optional[str]:
    if not labels:
        return None
    counts: Dict[str, int] = {}
    for lbl in labels:
        counts[lbl] = counts.get(lbl, 0) + 1
    return max(counts, key=lambda k: counts[k])


def _extract_trade_pairs(log_path: str) -> List[Tuple[bool, bool]]:
    """Pair BUYs with their subsequent SELL for the same symbol.

    Returns list of (predicted_up, actual_up) tuples for calibration.
    """
    rows: List[Tuple[str, str, str, float, float]] = []
    try:
        with open(log_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                action = str(row.get("action", "")).upper()
                symbol = str(row.get("symbol", "")).upper()
                price  = float(row.get("price", 0) or 0)
                pred   = float(row.get("predicted_change_pct", 0) or 0)
                ts     = str(row.get("timestamp", ""))
                if action in ("BUY", "SELL") and symbol and price > 0:
                    rows.append((ts, action, symbol, price, pred))
    except FileNotFoundError:
        return []

    open_buys: Dict[str, Tuple[float, float]] = {}   # symbol -> (buy_price, pred_pct)
    pairs: List[Tuple[bool, bool]] = []
    for _ts, action, symbol, price, pred in rows:
        if action == "BUY":
            open_buys[symbol] = (price, pred)
        elif action == "SELL" and symbol in open_buys:
            buy_price, buy_pred = open_buys.pop(symbol)
            actual_return = (price - buy_price) / buy_price if buy_price > 0 else 0.0
            pairs.append((buy_pred > 0, actual_return > 0))
    return pairs


def load_drift_state(state_dir: str, bot_name: str) -> Dict[str, Any]:
    """Read persisted drift state from disk (for API consumption)."""
    path = os.path.join(state_dir, f"drift_state_{bot_name}.json")
    try:
        with open(path) as fh:
            payload = json.load(fh)
        state = payload.get("state", _blank_state())
        state["saved_at"] = payload.get("saved_at")
        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return _blank_state()
