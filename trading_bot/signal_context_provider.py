from data_fetcher import (
    fetch_capitol_trades,
    fetch_external_research_sentiment,
    fetch_global_macro_sentiment,
    fetch_news_sentiment,
    fetch_sector_momentum,
    fetch_stock_data,
    fetch_vix_level,
    get_capitol_data_health,
    preprocess_data,
)
from model import predict_price


class SignalContextProvider:
    """Builds market/model context for a symbol so strategy can stay decision-focused."""

    def build(self, symbol, get_model_bundle):
        symbol = str(symbol).upper()
        data = preprocess_data(fetch_stock_data(symbol, period="1y"))
        if len(data) < 60:
            return {
                "ok": False,
                "reason": "not_enough_data",
            }

        model, scaler = get_model_bundle(symbol)
        close = data["Close"].astype(float)
        recent_prices = close.tail(60).to_numpy()

        current_price = float(close.iloc[-1])
        predicted_price = float(predict_price(model, scaler, recent_prices))
        predicted_change = (predicted_price - current_price) / current_price
        short_trend = float(close.tail(min(20, len(close))).mean())
        long_trend = float(close.tail(min(50, len(close))).mean())
        recent_return = float(close.pct_change(5).fillna(0.0).iloc[-1])
        trend_strength = (short_trend - long_trend) / max(abs(long_trend), 1e-9)

        trades = fetch_capitol_trades()
        data_health = get_capitol_data_health()

        vix_data = fetch_vix_level()
        vix = vix_data["vix"] if vix_data else 20.0
        fear_level = vix_data["fear_level"] if vix_data else "moderate"

        symbol_news = fetch_news_sentiment(symbol)
        symbol_news_score = float(symbol_news.get("score", 0.0))
        symbol_news_topics = symbol_news.get("topic_scores", {}) or {}

        global_news = fetch_global_macro_sentiment()
        global_news_score = float(global_news.get("score", 0.0))
        global_news_topics = global_news.get("topic_scores", {}) or {}

        external_research = fetch_external_research_sentiment()
        external_research_score = float(external_research.get("score", 0.0))
        external_research_topics = external_research.get("topic_scores", {}) or {}

        sector_momentum = fetch_sector_momentum()

        return {
            "ok": True,
            "data": data,
            "close": close,
            "predicted_price": predicted_price,
            "current_price": current_price,
            "predicted_change": predicted_change,
            "short_trend": short_trend,
            "long_trend": long_trend,
            "recent_return": recent_return,
            "trend_strength": trend_strength,
            "trades": trades,
            "data_health": data_health,
            "vix": vix,
            "fear_level": fear_level,
            "symbol_news": symbol_news,
            "symbol_news_score": symbol_news_score,
            "symbol_news_topics": symbol_news_topics,
            "global_news_score": global_news_score,
            "global_news_topics": global_news_topics,
            "external_research_score": external_research_score,
            "external_research_topics": external_research_topics,
            "sector_momentum": sector_momentum,
        }
