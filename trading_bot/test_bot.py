from broker import Broker
from strategy import TradingStrategy


def main():
    """Run a safe smoke test without submitting any orders."""
    print("Testing bot initialization (no live orders)...")
    broker = Broker()
    strategy = TradingStrategy()

    print("Bot initialized successfully")
    print(f"Account balance: ${broker.get_account_balance()}")

    signal = strategy.analyze_signal("AAPL", broker=broker)
    print(f"AAPL signal: {signal}")
    print("Smoke test completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()