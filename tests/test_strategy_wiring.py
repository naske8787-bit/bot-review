import unittest
from unittest.mock import patch
import os
import sys
import types
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADING_BOT_DIR = os.path.join(ROOT, "trading_bot")
if TRADING_BOT_DIR not in sys.path:
    sys.path.insert(0, TRADING_BOT_DIR)

if "model" not in sys.modules:
    fake_model = types.ModuleType("model")
    fake_model.load_trained_model = lambda symbol=None: (None, None)
    fake_model.predict_price = lambda model, scaler, prices: float(prices[-1]) if len(prices) else 0.0
    sys.modules["model"] = fake_model

from strategy import TradingStrategy


class StrategyWiringTests(unittest.TestCase):
    def _base_context(self):
        return {
            "ok": True,
            "data": [1] * 120,
            "close": pd.Series([100.0] * 120),
            "predicted_price": 102.0,
            "current_price": 100.0,
            "predicted_change": 0.02,
            "short_trend": 101.0,
            "long_trend": 100.0,
            "recent_return": 0.01,
            "trend_strength": 0.01,
            "trades": [],
            "data_health": {"confidence": 1.0, "source": "test", "degraded": False},
            "vix": 18.0,
            "fear_level": "low",
            "symbol_news": {"score": 0.0, "topic_scores": {}},
            "symbol_news_score": 0.0,
            "symbol_news_topics": {},
            "global_news_score": 0.0,
            "global_news_topics": {},
            "external_research_score": 0.0,
            "external_research_topics": {},
            "sector_momentum": {"SPY": {"momentum_5d": 0.01}},
        }

    def test_etf_entry_uses_shared_model_decision_engine(self):
        strategy = TradingStrategy()
        strategy.signal_context_provider.build = lambda symbol, get_model_bundle: self._base_context()
        strategy._bootstrap_symbol_history_if_needed = lambda symbol: None
        strategy._get_market_regime = lambda: {
            "favorable": True,
            "label": "bull",
            "confidence": 0.8,
            "entry_threshold_multiplier": 1.0,
            "risk_multiplier": 1.0,
            "allow_new_entries": True,
        }
        strategy._calculate_sentiment = lambda trades, symbol: (2, 2, 0)
        strategy._sync_position = lambda symbol, broker=None: None
        strategy._in_cooldown = lambda symbol: (False, 0.0)
        strategy._research_force_buy_signal = lambda symbol: {"triggered": False}
        strategy.long_term_policy.drawdown_blocked = lambda: False
        strategy.event_learner.observe = lambda *args, **kwargs: None
        strategy.event_learner.get_edge_adjustment = lambda *args, **kwargs: 0.0
        strategy.experience_policy.edge_adjustment = lambda *args, **kwargs: 0.0
        strategy.experience_policy.diagnostic_score = lambda *args, **kwargs: 0.0

        with patch("strategy.evaluate_equity_setup", return_value={
            "setup": "etf_momentum",
            "passed": True,
            "sample_size": 100,
            "win_rate": 0.6,
            "expectancy": 0.01,
        }), patch("strategy.decide_model_trend_action", return_value="BUY") as mock_decider:
            signal = strategy.analyze_signal("SPY", broker=None)

        self.assertEqual(signal, "BUY")
        mock_decider.assert_called_once()

    def test_position_management_uses_shared_live_decision_engine(self):
        strategy = TradingStrategy()
        strategy.signal_context_provider.build = lambda symbol, get_model_bundle: self._base_context()
        strategy._bootstrap_symbol_history_if_needed = lambda symbol: None
        strategy._get_market_regime = lambda: {
            "favorable": True,
            "label": "bull",
            "confidence": 0.8,
            "entry_threshold_multiplier": 1.0,
            "risk_multiplier": 1.0,
            "allow_new_entries": True,
        }
        strategy._calculate_sentiment = lambda trades, symbol: (1, 1, 0)
        strategy._sync_position = lambda symbol, broker=None: {
            "entry_price": 100.0,
            "qty": 1,
            "entry_ts": 0.0,
        }
        strategy._in_cooldown = lambda symbol: (False, 0.0)
        strategy._research_force_buy_signal = lambda symbol: {"triggered": False}
        strategy.long_term_policy.drawdown_blocked = lambda: False
        strategy.event_learner.observe = lambda *args, **kwargs: None
        strategy.event_learner.get_edge_adjustment = lambda *args, **kwargs: 0.0
        strategy.experience_policy.edge_adjustment = lambda *args, **kwargs: 0.0
        strategy.experience_policy.diagnostic_score = lambda *args, **kwargs: 0.0

        with patch("strategy.decide_live_position_action", return_value="SELL") as mock_decider:
            signal = strategy.analyze_signal("SPY", broker=None)

        self.assertEqual(signal, "SELL")
        mock_decider.assert_called_once()


if __name__ == "__main__":
    unittest.main()
