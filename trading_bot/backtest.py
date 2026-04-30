from config import (
    BUY_THRESHOLD_PCT,
    SELL_THRESHOLD_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    BACKTEST_SPREAD_BPS_DEFAULT,
    BACKTEST_SPREAD_BPS_BY_SYMBOL,
    BACKTEST_SLIPPAGE_VOL_MULTIPLIER,
    BACKTEST_SLIPPAGE_BPS_MIN,
    BACKTEST_SLIPPAGE_BPS_MAX,
    BACKTEST_COMMISSION_PER_SHARE,
    BACKTEST_MIN_COMMISSION,
    BACKTEST_FILL_LATENCY_BARS,
)
from data_fetcher import fetch_stock_data, preprocess_data
from model import load_trained_model, predict_price


def _max_drawdown(values):
    peak = None
    max_drawdown = 0.0
    for value in values:
        peak = value if peak is None else max(peak, value)
        if peak:
            drawdown = (value - peak) / peak
            max_drawdown = min(max_drawdown, drawdown)
    return abs(max_drawdown) * 100


def _spread_bps_for_symbol(symbol):
    sym = str(symbol or "").upper()
    by_symbol = BACKTEST_SPREAD_BPS_BY_SYMBOL or {}
    return float(by_symbol.get(sym, BACKTEST_SPREAD_BPS_DEFAULT))


def _slippage_bps_from_volatility(rolling_vol):
    vol = max(0.0, float(rolling_vol or 0.0))
    est_bps = vol * float(BACKTEST_SLIPPAGE_VOL_MULTIPLIER) * 10000.0
    return max(float(BACKTEST_SLIPPAGE_BPS_MIN), min(float(BACKTEST_SLIPPAGE_BPS_MAX), est_bps))


def _estimate_fill_price(side, reference_price, spread_bps, slippage_bps):
    ref = float(reference_price)
    spread_frac = float(spread_bps) / 10000.0
    slippage_frac = float(slippage_bps) / 10000.0
    if side == "BUY":
        return ref * (1.0 + (spread_frac / 2.0) + slippage_frac)
    return ref * (1.0 - (spread_frac / 2.0) - slippage_frac)


def _commission_for_shares(shares):
    qty = abs(float(shares or 0.0))
    if qty <= 0:
        return 0.0
    raw = qty * float(BACKTEST_COMMISSION_PER_SHARE)
    return max(float(BACKTEST_MIN_COMMISSION), raw)


def backtest_strategy(symbol, start_date, end_date, initial_capital=10000.0):
    """Backtest the current model + trend-filter strategy on historical data."""
    data = preprocess_data(fetch_stock_data(symbol, start=start_date, end=end_date, use_cache=False))
    if len(data) < 60:
        raise ValueError("Not enough historical data to run the backtest.")

    model, scaler = load_trained_model(symbol=symbol)
    close = data["Close"].astype(float)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    momentum_5d = close.pct_change(5).fillna(0.0)
    rolling_vol_20 = close.pct_change().rolling(20).std().fillna(0.0)

    capital = float(initial_capital)
    shares = 0.0
    entry_price = None
    total_fees = 0.0
    total_slippage_bps = 0.0
    filled_orders = 0
    equity_curve = []
    completed_trades = []

    spread_bps = _spread_bps_for_symbol(symbol)
    latency_bars = max(0, int(BACKTEST_FILL_LATENCY_BARS))

    for i in range(60, len(data)):
        current_price = float(close.iloc[i])
        fill_i = min(len(data) - 1, i + latency_bars)
        delayed_price = float(close.iloc[fill_i])
        vol_for_fill = float(rolling_vol_20.iloc[i])
        slippage_bps = _slippage_bps_from_volatility(vol_for_fill)
        recent_data = close.iloc[i - 60:i].to_numpy()
        predicted_price = predict_price(model, scaler, recent_data)
        predicted_change = (predicted_price - current_price) / current_price
        trend_confirmation = bool(sma20.iloc[i] > sma50.iloc[i] and current_price > sma20.iloc[i])
        positive_momentum = bool(momentum_5d.iloc[i] >= BUY_THRESHOLD_PCT)

        if shares > 0:
            should_sell = (
                current_price <= entry_price * (1 - STOP_LOSS_PCT)
                or current_price >= entry_price * (1 + TAKE_PROFIT_PCT)
                or (
                    predicted_change <= -SELL_THRESHOLD_PCT
                    and (not trend_confirmation or momentum_5d.iloc[i] < 0)
                )
            )
            if should_sell:
                fill_price = _estimate_fill_price("SELL", delayed_price, spread_bps, slippage_bps)
                commission = _commission_for_shares(shares)
                gross_proceeds = shares * fill_price
                capital = max(0.0, gross_proceeds - commission)
                total_fees += commission
                total_slippage_bps += slippage_bps
                filled_orders += 1
                pnl = capital - initial_capital if not completed_trades else capital - completed_trades[-1]["equity_after_trade"]
                completed_trades.append(
                    {
                        "action": "SELL",
                        "price": fill_price,
                        "reference_price": delayed_price,
                        "spread_bps": spread_bps,
                        "slippage_bps": slippage_bps,
                        "commission": commission,
                        "latency_bars": latency_bars,
                        "predicted_change_pct": predicted_change * 100,
                        "equity_after_trade": capital,
                        "pnl": pnl,
                    }
                )
                shares = 0.0
                entry_price = None
        else:
            should_buy = predicted_change >= BUY_THRESHOLD_PCT and trend_confirmation and positive_momentum
            if should_buy:
                fill_price = _estimate_fill_price("BUY", delayed_price, spread_bps, slippage_bps)
                if fill_price <= 0:
                    continue
                tentative_shares = capital / fill_price
                commission = _commission_for_shares(tentative_shares)
                deployable_capital = max(0.0, capital - commission)
                shares = deployable_capital / fill_price if fill_price > 0 else 0.0
                if shares <= 0:
                    continue
                entry_price = fill_price
                total_fees += commission
                total_slippage_bps += slippage_bps
                filled_orders += 1
                completed_trades.append(
                    {
                        "action": "BUY",
                        "price": fill_price,
                        "reference_price": delayed_price,
                        "spread_bps": spread_bps,
                        "slippage_bps": slippage_bps,
                        "commission": commission,
                        "latency_bars": latency_bars,
                        "predicted_change_pct": predicted_change * 100,
                        "equity_after_trade": capital,
                        "pnl": 0.0,
                    }
                )
                capital = 0.0

        equity_curve.append(capital + shares * current_price)

    final_price = float(close.iloc[-1])
    final_value = capital + shares * final_price
    buy_and_hold_value = initial_capital * (final_price / float(close.iloc[60]))
    avg_slippage_bps = (total_slippage_bps / filled_orders) if filled_orders > 0 else 0.0

    return {
        "symbol": symbol.upper(),
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "total_return_pct": round(((final_value - initial_capital) / initial_capital) * 100, 2),
        "buy_and_hold_value": round(buy_and_hold_value, 2),
        "buy_and_hold_return_pct": round(((buy_and_hold_value - initial_capital) / initial_capital) * 100, 2),
        "max_drawdown_pct": round(_max_drawdown(equity_curve), 2),
        "signals_executed": len(completed_trades),
        "execution_costs": {
            "spread_bps": round(float(spread_bps), 2),
            "avg_slippage_bps": round(float(avg_slippage_bps), 2),
            "commission_total": round(float(total_fees), 2),
            "latency_bars": int(latency_bars),
            "fills": int(filled_orders),
        },
    }


if __name__ == "__main__":
    result = backtest_strategy("AAPL", "2020-01-01", "2023-01-01")
    print("=== Strategy Backtest Summary ===")
    print(f"Symbol:                 {result['symbol']}")
    print(f"Period:                 {result['start_date']} to {result['end_date']}")
    print(f"Initial capital:        ${result['initial_capital']:.2f}")
    print(f"Final portfolio value:  ${result['final_value']:.2f}")
    print(f"Strategy return:        {result['total_return_pct']:.2f}%")
    print(f"Buy & hold return:      {result['buy_and_hold_return_pct']:.2f}%")
    print(f"Max drawdown:           {result['max_drawdown_pct']:.2f}%")
    print(f"Signals executed:       {result['signals_executed']}")