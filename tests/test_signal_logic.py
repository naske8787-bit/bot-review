import unittest

from trading_bot.signal_logic import decide_live_position_action, decide_model_trend_action


class SignalLogicTests(unittest.TestCase):
    def test_model_trend_buy_when_thresholds_pass(self):
        action = decide_model_trend_action(
            {
                "has_position": False,
                "predicted_change": 0.012,
                "trend_confirmation": True,
                "momentum": 0.008,
                "buy_threshold": 0.005,
            }
        )
        self.assertEqual(action, "BUY")

    def test_model_trend_hold_when_trend_fails(self):
        action = decide_model_trend_action(
            {
                "has_position": False,
                "predicted_change": 0.02,
                "trend_confirmation": False,
                "momentum": 0.02,
                "buy_threshold": 0.005,
            }
        )
        self.assertEqual(action, "HOLD")

    def test_model_trend_sell_on_stop_loss(self):
        action = decide_model_trend_action(
            {
                "has_position": True,
                "current_price": 96.0,
                "entry_price": 100.0,
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.10,
                "predicted_change": 0.001,
                "trend_confirmation": True,
                "momentum": 0.01,
                "sell_threshold": 0.005,
            }
        )
        self.assertEqual(action, "SELL")

    def test_live_position_hold_before_min_hold(self):
        action = decide_live_position_action(
            {
                "current_price": 104.0,
                "stop_loss_price": 97.0,
                "take_profit_price": 112.0,
                "min_hold_reached": False,
                "effective_predicted_change": 0.02,
                "buy_threshold": 0.005,
                "sell_threshold": 0.005,
                "sentiment": 2,
                "trend_strength": 0.01,
                "recent_return": 0.01,
                "short_trend": 105.0,
                "long_trend": 103.0,
                "news_score": 0.5,
            }
        )
        self.assertEqual(action, "HOLD")

    def test_live_position_sell_on_negative_news_momentum_combo(self):
        action = decide_live_position_action(
            {
                "current_price": 101.0,
                "stop_loss_price": 97.0,
                "take_profit_price": 112.0,
                "min_hold_reached": True,
                "effective_predicted_change": 0.004,
                "buy_threshold": 0.005,
                "sell_threshold": 0.005,
                "sentiment": 0,
                "trend_strength": 0.002,
                "recent_return": -0.01,
                "short_trend": 103.0,
                "long_trend": 102.0,
                "news_score": -3.2,
            }
        )
        self.assertEqual(action, "SELL")


if __name__ == "__main__":
    unittest.main()
