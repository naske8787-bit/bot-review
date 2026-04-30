"""Execution quality tracker.

Records per-order execution records (slippage, fill delay, rejection) to an
append-only JSONL log and computes rolling summary metrics for the API.

Metrics tracked
---------------
- slippage_bps   : (fill_price - signal_price) / signal_price * 10_000
                   negative = favourable (fill better than signal), positive = adverse
- fill_delay_s   : seconds from signal_ts to fill_ts (broker-confirmed or polled)
- rejected       : True when broker raised an exception on submit_order
- signal_price   : pre-order market price used for the trading decision
- fill_price     : avg_entry_price from broker position after fill (None if poll timed out)

Rolling window metrics (last N closed records):
  avg_slippage_bps, p95_slippage_bps, avg_fill_delay_s, rejection_rate,
  fill_confirmed_rate, sample_size
"""

import json
import math
import os
import time
from typing import Any, Dict, List, Optional

_WINDOW = 100        # rolling window for metric summaries
_POLL_ATTEMPTS = 3   # number of times to poll broker for fill confirmation
_POLL_DELAY_S  = 1.5 # seconds between poll attempts


class ExecutionQualityTracker:
    """Append-only JSONL logger + rolling metric computer.

    Usage:
        tracker = ExecutionQualityTracker(log_path)

        # Before submitting the order:
        rec = tracker.start_record(action, symbol, qty, signal_price)

        try:
            broker.buy(symbol, qty)
            fill = tracker.poll_fill(broker, symbol, signal_price)
            tracker.finish_record(rec, fill_price=fill)
        except Exception as exc:
            tracker.finish_record(rec, rejected=True, reject_reason=str(exc))
            raise  # re-raise so existing error handling still works
    """

    def __init__(self, log_path: str) -> None:
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def start_record(
        self,
        action: str,
        symbol: str,
        qty: float,
        signal_price: float,
    ) -> Dict[str, Any]:
        """Return a mutable in-flight record.  Call finish_record() to persist."""
        return {
            "action":       str(action).upper(),
            "symbol":       str(symbol).upper(),
            "qty":          float(qty),
            "signal_price": float(signal_price),
            "signal_ts":    time.time(),
            "fill_price":   None,
            "fill_ts":      None,
            "fill_delay_s": None,
            "slippage_bps": None,
            "rejected":     False,
            "reject_reason": None,
        }

    def finish_record(
        self,
        record: Dict[str, Any],
        fill_price: Optional[float] = None,
        rejected: bool = False,
        reject_reason: Optional[str] = None,
    ) -> None:
        """Complete and persist the record."""
        if rejected:
            record["rejected"]     = True
            record["reject_reason"] = str(reject_reason or "unknown")
        elif fill_price is not None and fill_price > 0:
            record["fill_price"] = round(float(fill_price), 6)
            record["fill_ts"]    = time.time()
            record["fill_delay_s"] = round(record["fill_ts"] - record["signal_ts"], 3)
            sig = record["signal_price"]
            if sig and sig > 0:
                record["slippage_bps"] = round(
                    (float(fill_price) - float(sig)) / float(sig) * 10_000, 2
                )
        self._append(record)

    def poll_fill(
        self,
        broker: Any,
        symbol: str,
        signal_price: float,
    ) -> Optional[float]:
        """Poll broker for fill confirmation; return fill price or None.

        Tries `_POLL_ATTEMPTS` times with `_POLL_DELAY_S` sleep between each.
        Uses broker.get_position(symbol) → avg_entry_price as fill proxy.
        Falls back to signal_price (zero slippage assumption) on timeout.
        """
        for _ in range(_POLL_ATTEMPTS):
            time.sleep(_POLL_DELAY_S)
            try:
                pos = broker.get_position(symbol) if hasattr(broker, "get_position") else None
                if pos:
                    fill = float(pos.get("entry_price") or pos.get("avg_entry_price") or 0.0)
                    if fill > 0:
                        return fill
            except Exception:
                pass
        return None   # fill not confirmed within poll window

    # ── Rolling metrics ─────────────────────────────────────────────────────

    def get_metrics(self, window: int = _WINDOW) -> Dict[str, Any]:
        """Read last `window` records from log and compute rolling metrics."""
        records = self._tail(window)
        if not records:
            return _empty_metrics()

        total        = len(records)
        rejected     = [r for r in records if r.get("rejected")]
        confirmed    = [r for r in records if r.get("fill_price") is not None and not r.get("rejected")]
        slippages    = [float(r["slippage_bps"]) for r in confirmed if r.get("slippage_bps") is not None]
        delays       = [float(r["fill_delay_s"]) for r in confirmed if r.get("fill_delay_s") is not None]

        return {
            "sample_size":          total,
            "rejection_rate":       round(len(rejected) / total, 4) if total else 0.0,
            "fill_confirmed_rate":  round(len(confirmed) / total, 4) if total else 0.0,
            "avg_slippage_bps":     _safe_mean(slippages),
            "p95_slippage_bps":     _safe_percentile(slippages, 95),
            "avg_fill_delay_s":     _safe_mean(delays),
            "p95_fill_delay_s":     _safe_percentile(delays, 95),
            "slippage_sample_size": len(slippages),
            "window":               window,
        }

    def get_recent_records(self, n: int = 20) -> List[Dict[str, Any]]:
        return self._tail(n)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _append(self, record: Dict[str, Any]) -> None:
        try:
            line = json.dumps(record, default=str)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _tail(self, n: int) -> List[Dict[str, Any]]:
        """Read last `n` lines from the JSONL log efficiently."""
        try:
            if not os.path.exists(self.log_path):
                return []
            chunk = 8192
            records: List[str] = []
            with open(self.log_path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                pos  = size
                buf  = b""
                while pos > 0:
                    read = min(chunk, pos)
                    pos -= read
                    fh.seek(pos)
                    buf = fh.read(read) + buf
                    lines = buf.split(b"\n")
                    # Keep first (possibly incomplete) fragment for next iteration.
                    buf = lines[0]
                    records = [l.decode("utf-8", errors="replace") for l in lines[1:] if l.strip()] + records
                    if len(records) >= n:
                        break
                # Don't forget remaining buf when pos == 0
                if buf.strip():
                    records = [buf.decode("utf-8", errors="replace")] + records
            parsed: List[Dict[str, Any]] = []
            for line in records[-n:]:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        parsed.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass
            return parsed
        except Exception:
            return []


def _empty_metrics() -> Dict[str, Any]:
    return {
        "sample_size":          0,
        "rejection_rate":       0.0,
        "fill_confirmed_rate":  0.0,
        "avg_slippage_bps":     None,
        "p95_slippage_bps":     None,
        "avg_fill_delay_s":     None,
        "p95_fill_delay_s":     None,
        "slippage_sample_size": 0,
        "window":               _WINDOW,
    }


def _safe_mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _safe_percentile(values: List[float], pct: int) -> Optional[float]:
    if not values:
        return None
    sorted_v = sorted(values)
    idx = max(0, min(len(sorted_v) - 1, int(math.ceil(pct / 100.0 * len(sorted_v))) - 1))
    return round(sorted_v[idx], 3)


def load_execution_metrics(log_path: str, window: int = _WINDOW) -> Dict[str, Any]:
    """Convenience for API consumption — reads log without instantiating a tracker."""
    t = ExecutionQualityTracker(log_path)
    m = t.get_metrics(window)
    m["log_path"] = log_path
    return m
