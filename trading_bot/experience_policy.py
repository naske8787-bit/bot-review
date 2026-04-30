import json
import math
import os
from typing import Dict


class ExperiencePolicy:
    """Lightweight online learner that nudges entry decisions from realized outcomes."""

    def __init__(
        self,
        storage_path,
        enabled=True,
        learning_rate=0.08,
        decay=0.999,
        max_adjustment_abs=0.006,
    ):
        self.storage_path = storage_path
        self.enabled = bool(enabled)
        self.learning_rate = max(0.0001, float(learning_rate))
        self.decay = min(1.0, max(0.9, float(decay)))
        self.max_adjustment_abs = max(0.0005, float(max_adjustment_abs))
        self.feature_weights = {
            "bias": 0.0,
            "model_edge": 0.0,
            "trend": 0.0,
            "sentiment": 0.0,
            "news": 0.0,
            "sector_tailwind": 0.0,
            "fear": 0.0,
            "market_regime": 0.0,
        }
        self.symbol_bias: Dict[str, float] = {}
        self.total_updates = 0
        self._load_state()
        if self.enabled and not os.path.exists(self.storage_path):
            # Materialize learner state so health checks can confirm persistence.
            self._persist_state()

    @staticmethod
    def _clip(value, lo, hi):
        return max(lo, min(hi, float(value)))

    def _load_state(self):
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            for key in self.feature_weights:
                if key in payload.get("feature_weights", {}):
                    self.feature_weights[key] = float(payload["feature_weights"][key])
            self.symbol_bias = {
                str(k).upper(): float(v)
                for k, v in (payload.get("symbol_bias") or {}).items()
            }
            self.total_updates = int(payload.get("total_updates", 0))
        except Exception:
            # Corrupt state should not block trading.
            return

    def _persist_state(self):
        if not self.enabled:
            return
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        payload = {
            "feature_weights": self.feature_weights,
            "symbol_bias": self.symbol_bias,
            "total_updates": self.total_updates,
        }
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

    def _features(self, context):
        predicted = float(context.get("predicted_change", 0.0))
        trend_strength = float(context.get("trend_strength", 0.0))
        sentiment = float(context.get("sentiment", 0.0))
        news_score = float(context.get("news_score", 0.0))
        sector_tailwind = 1.0 if context.get("sector_tailwind") else -1.0
        fear = -1.0 if context.get("high_fear") else 0.0
        market_regime = 1.0 if context.get("market_favorable", True) else -1.0

        return {
            "bias": 1.0,
            "model_edge": self._clip(predicted / 0.03, -3.0, 3.0),
            "trend": self._clip(trend_strength / 0.02, -3.0, 3.0),
            "sentiment": self._clip(sentiment / 4.0, -3.0, 3.0),
            "news": self._clip(news_score / 3.0, -3.0, 3.0),
            "sector_tailwind": sector_tailwind,
            "fear": fear,
            "market_regime": market_regime,
        }

    def _raw_score(self, symbol, context):
        symbol = symbol.upper()
        features = self._features(context)
        score = 0.0
        for key, value in features.items():
            score += float(self.feature_weights.get(key, 0.0)) * float(value)
        score += float(self.symbol_bias.get(symbol, 0.0))
        return score

    def edge_adjustment(self, symbol, context):
        """Convert policy score into a bounded edge adjustment percentage."""
        if not self.enabled:
            return 0.0
        score = self._raw_score(symbol, context)
        # tanh keeps adjustment bounded while allowing smooth updates.
        return math.tanh(score) * self.max_adjustment_abs

    def diagnostic_score(self, symbol, context):
        if not self.enabled:
            return 0.0
        return float(self._raw_score(symbol, context))

    def observe_trade(self, symbol, entry_context, entry_price, exit_price, hold_minutes=0.0):
        """Update policy weights from realized returns."""
        if not self.enabled:
            return

        symbol = symbol.upper()
        entry_price = float(entry_price or 0.0)
        exit_price = float(exit_price or 0.0)
        if entry_price <= 0 or exit_price <= 0:
            return

        raw_return = (exit_price - entry_price) / entry_price
        hold_minutes = max(0.0, float(hold_minutes or 0.0))

        # Reward favors positive returns while lightly penalizing very quick churn.
        churn_penalty = 0.0015 if hold_minutes < 10 else 0.0
        reward = raw_return - churn_penalty

        # Map reward into a stable training target.
        target = self._clip(reward / 0.03, -1.0, 1.0)
        prediction = math.tanh(self._raw_score(symbol, entry_context))
        error = target - prediction

        features = self._features(entry_context)
        for key, value in features.items():
            current = float(self.feature_weights.get(key, 0.0))
            updated = (current * self.decay) + (self.learning_rate * error * float(value))
            self.feature_weights[key] = self._clip(updated, -3.0, 3.0)

        current_symbol_bias = float(self.symbol_bias.get(symbol, 0.0))
        updated_symbol_bias = (current_symbol_bias * self.decay) + (self.learning_rate * error * 0.5)
        self.symbol_bias[symbol] = self._clip(updated_symbol_bias, -2.0, 2.0)

        self.total_updates += 1
        self._persist_state()
