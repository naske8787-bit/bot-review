from broker import Broker
from strategy import TradingStrategy


def main():
    print("Testing crypto bot initialization (paper trading only)...")
    broker = Broker()
    strategy = TradingStrategy()

    print("Crypto bot initialized successfully")
    print(f"Account balance: ${broker.get_account_balance():.2f}")

    symbol = "BTC/USD"
    signal = strategy.analyze_signal(symbol)
    print(f"{symbol} signal: {signal}")
    print(f"Last analysis: {strategy.last_analysis.get(symbol, {})}")
    print("Smoke test completed successfully.")


if __name__ == "__main__":
    main()
