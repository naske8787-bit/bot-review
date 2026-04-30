from config import TRAINING_SYMBOLS, RETRAIN_LOOKBACK_PERIOD
from data_fetcher import fetch_stock_data, preprocess_data
from model import train_model


def retrain_models(symbols=None, period=None):
    """Retrain models for the given symbols. Returns a dict of symbol -> success bool."""
    if symbols is None:
        symbols = TRAINING_SYMBOLS
    if period is None:
        period = RETRAIN_LOOKBACK_PERIOD

    results = {}
    for symbol in symbols:
        try:
            print(f"Retraining model for {symbol} (period={period})...")
            data = fetch_stock_data(symbol, period=period, use_cache=False)
            data = preprocess_data(data)
            if len(data) < 60:
                print(f"Skipping {symbol}: not enough historical data.")
                results[symbol] = False
                continue
            train_model(data, symbol=symbol)
            print(f"Model retrained and saved for {symbol}.")
            results[symbol] = True
        except Exception as e:
            print(f"Error retraining {symbol}: {e}")
            results[symbol] = False

    return results


def main():
    retrain_models()


if __name__ == "__main__":
    main()