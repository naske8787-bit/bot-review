def passes_model_trend_entry(predicted_change, trend_confirmation, momentum, edge_threshold, momentum_threshold):
    """Shared core entry condition for model+trend pathways used in live and backtest."""
    return (
        bool(trend_confirmation)
        and float(predicted_change) >= float(edge_threshold)
        and float(momentum) >= float(momentum_threshold)
    )


def decide_model_trend_action(context):
    """Pure model+trend decision engine for BUY/SELL/HOLD using a context dict."""
    has_position = bool(context.get("has_position", False))
    predicted_change = float(context.get("predicted_change", 0.0))
    trend_confirmation = bool(context.get("trend_confirmation", False))
    momentum = float(context.get("momentum", 0.0))
    buy_threshold = float(context.get("buy_threshold", 0.0))

    if not has_position:
        should_buy = passes_model_trend_entry(
            predicted_change=predicted_change,
            trend_confirmation=trend_confirmation,
            momentum=momentum,
            edge_threshold=buy_threshold,
            momentum_threshold=buy_threshold,
        )
        return "BUY" if should_buy else "HOLD"

    current_price = float(context.get("current_price", 0.0))
    entry_price = float(context.get("entry_price", 0.0))
    stop_loss_pct = float(context.get("stop_loss_pct", 0.0))
    take_profit_pct = float(context.get("take_profit_pct", 0.0))
    sell_threshold = float(context.get("sell_threshold", 0.0))

    if entry_price > 0 and current_price <= entry_price * (1.0 - stop_loss_pct):
        return "SELL"
    if entry_price > 0 and current_price >= entry_price * (1.0 + take_profit_pct):
        return "SELL"
    if predicted_change <= -sell_threshold and ((not trend_confirmation) or momentum < 0):
        return "SELL"
    return "HOLD"


def decide_live_position_action(context):
    """Pure live position manager for current holdings using a context dict."""
    current_price = float(context.get("current_price", 0.0))
    stop_loss_price = float(context.get("stop_loss_price", 0.0))
    take_profit_price = float(context.get("take_profit_price", 0.0))
    min_hold_reached = bool(context.get("min_hold_reached", False))
    effective_predicted_change = float(context.get("effective_predicted_change", 0.0))
    buy_threshold = float(context.get("buy_threshold", 0.0))
    sell_threshold = float(context.get("sell_threshold", 0.0))
    sentiment = float(context.get("sentiment", 0.0))
    trend_strength = float(context.get("trend_strength", 0.0))
    recent_return = float(context.get("recent_return", 0.0))
    short_trend = float(context.get("short_trend", 0.0))
    long_trend = float(context.get("long_trend", 0.0))
    news_score = float(context.get("news_score", 0.0))

    if current_price <= stop_loss_price:
        return "SELL"
    if not min_hold_reached:
        return "HOLD"
    if current_price >= take_profit_price and (
        effective_predicted_change <= buy_threshold or sentiment <= 0 or trend_strength < 0
    ):
        return "SELL"
    if effective_predicted_change <= -sell_threshold and (
        sentiment < 0 or trend_strength < 0 or recent_return < 0
    ):
        return "SELL"
    if sentiment <= -2 and recent_return < 0:
        return "SELL"
    if short_trend < long_trend and recent_return <= -sell_threshold:
        return "SELL"
    if news_score <= -3 and recent_return < 0:
        return "SELL"
    return "HOLD"
