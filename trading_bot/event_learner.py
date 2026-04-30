import json
import os
import threading
from typing import Dict, List


class EventImpactLearner:
    """Online learner for topic-to-return cause/effect relationships.

    The learner stores two levels of topic impact:
    1) global impact across all symbols
    2) symbol-specific impact for each ticker

    Each cycle, it observes current topic exposure and updates impacts based on
    realized next-cycle return. Impacts are persisted to disk so learning
    survives restarts.
    """

    def __init__(
        self,
        storage_path: str,
        alpha: float = 0.15,
        max_adjustment_abs: float = 0.01,
        lags=(1, 3, 6),
    ):
        self.storage_path = storage_path
        self.alpha = max(0.01, min(0.5, float(alpha)))
        self.max_adjustment_abs = max(0.001, float(max_adjustment_abs))
        self.lags = tuple(sorted({int(l) for l in (lags or (1, 3, 6)) if int(l) > 0}))
        if not self.lags:
            self.lags = (1,)
        self.lock = threading.Lock()

        self.state = {
            "global_topic_impacts": {},
            "symbol_topic_impacts": {},
            "last_observation": {},
            "observation_history": {},
            "bootstrap_completed_symbols": [],
        }
        self._load()
        # Materialize state file on startup so health checks can verify learner readiness.
        if not os.path.exists(self.storage_path):
            self._save()

    def _load(self):
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                    if isinstance(payload, dict):
                        self.state.update(payload)
        except Exception:
            # Keep bot running even if state file is corrupted.
            self.state = {
                "global_topic_impacts": {},
                "symbol_topic_impacts": {},
                "last_observation": {},
                "observation_history": {},
                "bootstrap_completed_symbols": [],
            }

    def _update_impacts(self, symbol: str, topic_scores: Dict[str, float], realized_return: float, lag: int):
        global_impacts = self.state["global_topic_impacts"]
        symbol_impacts = self.state["symbol_topic_impacts"].setdefault(symbol, {})
        lag_weight = 1.0 / (float(lag) ** 0.5)

        for topic, raw_exposure in topic_scores.items():
            exposure = self._normalize_topic_score(raw_exposure)
            if exposure == 0:
                continue

            signal = realized_return * exposure * lag_weight

            old_global = float(global_impacts.get(topic, 0.0))
            new_global = old_global + self.alpha * (signal - old_global)
            global_impacts[topic] = new_global

            old_symbol = float(symbol_impacts.get(topic, 0.0))
            new_symbol = old_symbol + self.alpha * (signal - old_symbol)
            symbol_impacts[topic] = new_symbol

    def _save(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        tmp_path = f"{self.storage_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp_path, self.storage_path)

    def is_bootstrap_completed(self, symbol: str) -> bool:
        symbol = str(symbol or "").upper()
        done = set(str(s).upper() for s in self.state.get("bootstrap_completed_symbols", []))
        return symbol in done

    def _mark_bootstrap_completed(self, symbol: str):
        symbol = str(symbol or "").upper()
        if not symbol:
            return
        done = set(str(s).upper() for s in self.state.get("bootstrap_completed_symbols", []))
        done.add(symbol)
        self.state["bootstrap_completed_symbols"] = sorted(done)

    def bootstrap_symbol_history(self, symbol: str, observations: List[Dict[str, object]]) -> int:
        """Replay historical observations to warm-start impacts.

        observations entries must be chronological and contain:
        - price: float
        - topic_scores: Dict[str, float]

        Returns number of observations consumed.
        """
        symbol = str(symbol or "").upper()
        if not symbol or not observations:
            return 0

        consumed = 0
        with self.lock:
            history = self.state["observation_history"].setdefault(symbol, [])
            for item in observations:
                price = float(item.get("price", 0.0))
                if price <= 0:
                    continue
                topic_scores = item.get("topic_scores", {}) or {}

                history.append(
                    {
                        "price": price,
                        "topic_scores": {k: float(v) for k, v in topic_scores.items()},
                    }
                )

                for lag in self.lags:
                    if len(history) <= lag:
                        continue
                    previous = history[-(lag + 1)]
                    prev_price = float(previous.get("price", 0.0))
                    prev_topics = previous.get("topic_scores", {}) or {}
                    if prev_price <= 0 or not prev_topics:
                        continue

                    realized_return = (price - prev_price) / prev_price
                    self._update_impacts(symbol, prev_topics, realized_return, lag)

                keep = max(self.lags) + 1
                if len(history) > keep:
                    del history[:-keep]

                self.state["last_observation"][symbol] = {
                    "price": price,
                    "topic_scores": {k: float(v) for k, v in topic_scores.items()},
                }
                consumed += 1

            if consumed > 0:
                self._mark_bootstrap_completed(symbol)
                self._save()

        return consumed

    @staticmethod
    def _normalize_topic_score(value: float) -> float:
        # Clamp topic exposure to [-1, 1] to reduce headline outlier impact.
        v = float(value)
        if v > 3.0:
            return 1.0
        if v < -3.0:
            return -1.0
        return v / 3.0

    def observe(self, symbol: str, current_price: float, topic_scores: Dict[str, float]):
        """Update topic impacts using multiple lag horizons.

        This learns both immediate and delayed cause/effect (e.g., 1, 3, 6
        observation steps), which better reflects how macro events can take time
        to flow through markets.
        """
        symbol = str(symbol or "").upper()
        if not symbol or current_price <= 0:
            return

        topic_scores = topic_scores or {}

        with self.lock:
            history = self.state["observation_history"].setdefault(symbol, [])
            history.append(
                {
                    "price": float(current_price),
                    "topic_scores": {k: float(v) for k, v in topic_scores.items()},
                }
            )

            # Learn from configured lag horizons.
            for lag in self.lags:
                if len(history) <= lag:
                    continue
                previous = history[-(lag + 1)]
                prev_price = float(previous.get("price", 0.0))
                prev_topics = previous.get("topic_scores", {}) or {}
                if prev_price <= 0 or not prev_topics:
                    continue

                realized_return = (float(current_price) - prev_price) / prev_price
                self._update_impacts(symbol, prev_topics, realized_return, lag)

            # Keep only the needed trailing history.
            keep = max(self.lags) + 1
            if len(history) > keep:
                del history[:-keep]

            # Store latest observation for next-cycle learning.
            self.state["last_observation"][symbol] = {
                "price": float(current_price),
                "topic_scores": {k: float(v) for k, v in topic_scores.items()},
            }

            # Persist periodically every observation; file is small.
            self._save()

    def get_edge_adjustment(self, symbol: str, topic_scores: Dict[str, float]) -> float:
        """Return model edge adjustment based on learned topic impacts.

        Positive values increase predicted upside confidence.
        Negative values reduce confidence.
        """
        symbol = str(symbol or "").upper()
        topic_scores = topic_scores or {}

        with self.lock:
            global_impacts = self.state["global_topic_impacts"]
            symbol_impacts = self.state["symbol_topic_impacts"].get(symbol, {})

            adjustment = 0.0
            for topic, raw_exposure in topic_scores.items():
                exposure = self._normalize_topic_score(raw_exposure)
                if exposure == 0:
                    continue

                global_component = float(global_impacts.get(topic, 0.0))
                symbol_component = float(symbol_impacts.get(topic, 0.0))
                combined_impact = (0.6 * global_component) + (0.4 * symbol_component)
                adjustment += combined_impact * exposure

            if adjustment > self.max_adjustment_abs:
                return self.max_adjustment_abs
            if adjustment < -self.max_adjustment_abs:
                return -self.max_adjustment_abs
            return float(adjustment)
