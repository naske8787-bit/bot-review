"""Main trading loop for the forex bot."""
from __future__ import annotations

import signal
import sys
import time

from broker import ForexBroker
from config import AUTONOMOUS_EXECUTION_ENABLED, LOOP_INTERVAL_SECS, WATCHLIST
from data_fetcher import fetch_external_research_sentiment
from strategy import ForexStrategy

_running = True


def _handle_stop(signum, frame):
    global _running
    print("\n[Main] Shutdown signal received — stopping after current cycle.")
    _running = False


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT,  _handle_stop)


def main() -> int:
    print("=" * 60)
    print("  Forex Bot  (paper trading)")
    print(f"  Pairs   : {', '.join(WATCHLIST)}")
    print(f"  Interval: {LOOP_INTERVAL_SECS}s")
    print("=" * 60)

    broker   = ForexBroker()
    strategy = ForexStrategy()

    try:
        balance = broker.get_account_balance()
        print(f"[Main] Starting balance: ${balance:,.2f}")
    except Exception as e:
        print(f"[Main] Cannot connect to Alpaca: {e}")
        print("[Main] Check ALPACA_API_KEY / ALPACA_API_SECRET in .env")
        return 1

    cycle = 0
    while _running:
        cycle += 1
        print(f"\n── Cycle {cycle}  {time.strftime('%Y-%m-%d %H:%M:%S')} ──")

        strategy.observe_portfolio_value(broker.get_portfolio_value())
        if AUTONOMOUS_EXECUTION_ENABLED:
            research = fetch_external_research_sentiment()
            profile = strategy.evaluate_autonomy_profile(research_payload=research)
            strategy.apply_autonomy_profile(profile)
            metrics = profile.get("metrics", {})
            print(
                "  Autonomy profile: "
                f"mode={profile.get('mode')} score={profile.get('score')} "
                f"allow_entries={profile.get('allow_new_entries')} risk_mult={profile.get('risk_multiplier')} "
                f"blocked={','.join(profile.get('blocked_symbols', [])) or 'none'} "
                f"closed_7d={metrics.get('closed_trades_7d', 0)} win_7d={float(metrics.get('win_rate_7d', 0.0)):.1%} "
                f"pf_7d={float(metrics.get('profit_factor_7d', 0.0)):.2f} pnl_7d={float(metrics.get('realized_pnl_7d', 0.0)):.2f} "
                f"dd_7d={float(metrics.get('max_drawdown_7d', 0.0)):.2%}"
            )
            for update in strategy.auto_apply_improvements():
                print(f"  Auto-improvement: {update}")

        for pair in WATCHLIST:
            if not _running:
                break
            try:
                analysis = strategy.analyse(pair)
                signal_val = analysis.get("signal", "HOLD")
                chg        = analysis.get("predicted_chg_pct", 0.0)
                rsi        = analysis.get("rsi", 0.0)
                ema_cross  = analysis.get("ema_cross", "—")

                print(
                    f"  {pair:10s} | {signal_val:4s} | "
                    f"pred_chg={chg:+.4f}%  RSI={rsi:.1f}  EMA={ema_cross}"
                    f"  EXT={analysis.get('external_research_score', 0.0):+.2f}"
                    f"  AUTO={strategy.autonomy_profile.get('mode', 'normal')}"
                )

                trade = strategy.execute(analysis, pair, broker)
                if trade:
                    print(
                        f"  >>> {trade['action']} {trade['units']} units of {pair} "
                        f"@ {trade['price']:.5f}  ATR={trade['atr']:.6f}"
                    )

            except Exception as e:
                print(f"  [Main] Error on {pair}: {e}")

        # Equity snapshot
        try:
            pv = broker.get_portfolio_value()
            print(f"  Portfolio value: ${pv:,.2f}")
        except Exception:
            pass

        print(f"  Sleeping {LOOP_INTERVAL_SECS}s...")
        for _ in range(LOOP_INTERVAL_SECS):
            if not _running:
                break
            time.sleep(1)

    print("[Main] Forex bot stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
