"""Main loop for the India bot (NSE/BSE via Zerodha Kite Connect)."""

import time
from datetime import datetime

import pytz

from broker import Broker
from config import LOOP_INTERVAL_SECONDS, MARKET_CLOSE_TIME, MARKET_OPEN_TIME, MARKET_TIMEZONE, WATCHLIST
from strategy import TradingStrategy


def _is_market_open() -> bool:
    """Return True if the current time is within NSE/BSE trading hours."""
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)
    # Skip weekends
    if now.weekday() >= 5:
        return False
    open_h, open_m = map(int, MARKET_OPEN_TIME.split(":"))
    close_h, close_m = map(int, MARKET_CLOSE_TIME.split(":"))
    market_open = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    market_close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return market_open <= now <= market_close


def main():
    broker = Broker()
    strategy = TradingStrategy()

    print("India trading bot started. Press Ctrl+C to stop.")
    print(f"Watchlist: {', '.join(WATCHLIST)}")
    print(f"Market hours: {MARKET_OPEN_TIME}–{MARKET_CLOSE_TIME} IST")

    try:
        while True:
            if not _is_market_open():
                print("[Main] Market is closed. Waiting 5 minutes...")
                time.sleep(300)
                continue

            print(f"\n[Main] --- Cycle start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
            print(f"[Main] Portfolio value: ₹{broker.get_portfolio_value():,.2f} | Cash: ₹{broker.get_account_balance():,.2f}")

            for symbol in WATCHLIST:
                try:
                    signal = strategy.analyze_signal(symbol, broker=broker)
                    analysis = strategy.last_analysis.get(symbol, {})
                    print(
                        f"  {symbol}: {signal} | "
                        f"EMA9/21={analysis.get('ema_short', 0):.2f}/{analysis.get('ema_long', 0):.2f} | "
                        f"RSI={analysis.get('rsi', 0):.1f} | "
                        f"MACD hist={analysis.get('macd_histogram', 0):.4f} | "
                        f"learned={analysis.get('learned_edge_adjustment_pct', 0.0):+.3f}% | "
                        f"eff={analysis.get('effective_edge_pct', 0.0):+.3f}% | "
                        f"market={'OK' if analysis.get('market_favorable', True) else 'WEAK'}"
                    )

                    result = strategy.execute_trade(signal, symbol, broker)
                    if result:
                        print(
                            f"  >> {result['action']} {result['qty']} x {result['symbol']} @ ₹{result['price']:.2f}"
                        )

                    time.sleep(1)
                except Exception as e:
                    print(f"  [Error] {symbol}: {e}")

            print(f"[Main] Cycle complete. Waiting {LOOP_INTERVAL_SECONDS // 60} minutes...")
            time.sleep(LOOP_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n[Main] Interrupted. Disconnecting from broker...")
        broker.disconnect()

if __name__ == "__main__":
    main()
