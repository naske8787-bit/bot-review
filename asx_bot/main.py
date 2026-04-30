"""ASX day-trading bot — main loop."""
from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    ALLOW_BROKER_FALLBACK,
    ASX_CLOSE_HOUR,
    ASX_CLOSE_MIN,
    ASX_OPEN_HOUR,
    ASX_OPEN_MIN,
    ASX_TIMEZONE,
    AUTONOMOUS_EXECUTION_ENABLED,
    BROKER_MODE,
    LOOP_INTERVAL_SECS,
    WATCHLIST,
)
from data_fetcher import fetch_external_research_sentiment
from strategy import ASXStrategy

_running = True
_ASX_TZ = ZoneInfo(ASX_TIMEZONE)


def _handle_stop(signum, frame):
    global _running
    print("\n[Main] Shutdown signal received — closing positions and stopping.")
    _running = False


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT,  _handle_stop)


def _market_open_bounds(now_utc: datetime) -> tuple[datetime, datetime]:
    local_now = now_utc.astimezone(_ASX_TZ)
    local_open = local_now.replace(
        hour=ASX_OPEN_HOUR,
        minute=ASX_OPEN_MIN,
        second=0,
        microsecond=0,
    )
    local_close = local_now.replace(
        hour=ASX_CLOSE_HOUR,
        minute=ASX_CLOSE_MIN,
        second=0,
        microsecond=0,
    )
    return local_open, local_close


def _is_market_open(now_utc: datetime | None = None) -> bool:
    """Return True during ASX trading hours in exchange-local time."""
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    local_now = now_utc.astimezone(_ASX_TZ)
    if local_now.weekday() >= 5:
        return False
    local_open, local_close = _market_open_bounds(now_utc)
    return local_open <= local_now < local_close


def _next_market_open(now_utc: datetime | None = None) -> datetime:
    """Return the next ASX market-open timestamp in UTC."""
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    local_now = now_utc.astimezone(_ASX_TZ)
    days_ahead = 0

    if local_now.weekday() >= 5:
        days_ahead = (7 - local_now.weekday()) % 7
    else:
        local_open, _ = _market_open_bounds(now_utc)
        if local_now >= local_open:
            days_ahead = 1

    next_open = local_now.replace(
        hour=ASX_OPEN_HOUR,
        minute=ASX_OPEN_MIN,
        second=0,
        microsecond=0,
    ) + timedelta(days=days_ahead)

    while next_open.weekday() >= 5:
        next_open += timedelta(days=1)

    return next_open.astimezone(timezone.utc)


def _fmt_positions(positions: dict) -> str:
    if not positions:
        return "  (none)"
    lines = []
    for sym, pos in positions.items():
        lines.append(f"  {sym}: {pos['qty']} shares @ ${pos['avg_cost']:.3f}")
    return "\n".join(lines)


def _build_broker():
    """Create broker instance from config, with optional fallback to paper."""
    if BROKER_MODE == "paper":
        from broker import PaperBroker
        return PaperBroker(), "paper"

    if BROKER_MODE == "ibkr":
        try:
            from ibkr_broker import IBKRBroker
            return IBKRBroker(), "ibkr"
        except Exception as e:
            if not ALLOW_BROKER_FALLBACK:
                raise
            print(f"[Main] IBKR broker unavailable: {e}")
            print("[Main] Falling back to local paper broker.")
            from broker import PaperBroker
            return PaperBroker(), "paper"

    raise ValueError(f"Unsupported BROKER_MODE={BROKER_MODE!r}; expected 'paper' or 'ibkr'")


def main() -> int:
    print("=" * 60)
    print("  ASX Day-Trading Bot")
    print(f"  Watchlist : {', '.join(WATCHLIST)}")
    print(f"  Interval  : {LOOP_INTERVAL_SECS}s")
    print(
        f"  Market hrs: {ASX_OPEN_HOUR:02d}:{ASX_OPEN_MIN:02d} – "
        f"{ASX_CLOSE_HOUR:02d}:{ASX_CLOSE_MIN:02d} {ASX_TIMEZONE}"
    )
    print(f"  Broker cfg: {BROKER_MODE}")
    print("=" * 60)

    broker, active_broker = _build_broker()
    print(f"[Main] Active broker: {active_broker}")
    strategy = ASXStrategy()

    try:
        balance = broker.get_account_balance()
        pv      = broker.get_portfolio_value()
        print(f"[Main] Cash: ${balance:,.2f}  |  Portfolio: ${pv:,.2f}")
    except Exception as e:
        print(f"[Main] Broker init failed: {e}")
        return 1

    cycle         = 0
    eod_closed    = False   # guard: only close once per day

    while _running:
        now   = datetime.now(tz=timezone.utc)
        cycle += 1
        print(f"\n── Cycle {cycle}  {now.strftime('%Y-%m-%d %H:%M:%S UTC')} ──")

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

        # ── Stop/take-profit check (always, even outside hours) ───────────────
        triggered = broker.check_stop_take_profit()
        for t in triggered:
            print(f"  AUTO-EXIT {t['symbol']} {t['reason']}: "
                  f"{t['qty']} shares @ ${t['fill_price']:.3f}  P&L: ${t['pnl']:+.2f}")

        if not _is_market_open(now):
            next_open = _next_market_open(now)
            sleep_for = max(1, min(LOOP_INTERVAL_SECS, int((next_open - now).total_seconds())))
            status = "weekend" if now.astimezone(_ASX_TZ).weekday() >= 5 else "outside hours"
            print(f"  Market {status} — sleeping {sleep_for}s until next check")
            eod_closed = False   # reset for next trading day
            for _ in range(sleep_for):
                if not _running:
                    break
                time.sleep(1)
            continue

        # ── EOD forced close ──────────────────────────────────────────────────
        if strategy.is_eod_close_time() and not eod_closed:
            print("  *** EOD: closing all intraday positions ***")
            closed = strategy.close_all_positions(broker)
            for c in closed:
                print(f"  EOD CLOSE {c['symbol']}: {c['qty']} shares @ ${c['price']:.3f}")
            eod_closed = True

        # ── Main trading loop ─────────────────────────────────────────────────
        for symbol in WATCHLIST:
            if not _running:
                break
            if eod_closed:
                break    # no new entries after EOD close

            try:
                analysis   = strategy.analyse(symbol)
                sig        = analysis.get("signal", "HOLD")
                chg        = analysis.get("predicted_chg_pct", 0.0)
                learned    = analysis.get("learned_edge_adjustment_pct", 0.0)
                eff        = analysis.get("effective_predicted_chg_pct", chg)
                rsi        = analysis.get("rsi", 0.0)
                ema_cross  = analysis.get("ema_cross", "—")
                vwap_pos   = analysis.get("vwap_position", "—")
                vol_ratio  = analysis.get("volume_ratio", 0.0)
                bb_pos     = analysis.get("bb_position", "—")

                print(
                    f"  {symbol:<10} | {sig:4s} | "
                    f"pred={chg:+.3f}%  learned={learned:+.3f}%  eff={eff:+.3f}%  RSI={rsi:.1f}  "
                    f"EMA={ema_cross}  VWAP={vwap_pos}  "
                    f"BB={bb_pos}  Vol×{vol_ratio:.2f}  "
                    f"EXT={analysis.get('external_research_score', 0.0):+.2f}  "
                    f"AUTO={strategy.autonomy_profile.get('mode', 'normal')}"
                )

                trade = strategy.execute(analysis, symbol, broker)
                if trade:
                    action = trade["action"]
                    qty    = trade["qty"]
                    price  = trade["price"]
                    if action == "BUY":
                        print(
                            f"  >>> BUY  {qty} × {symbol} @ ${price:.3f}  "
                            f"stop=${trade['stop']:.3f}  target=${trade['target']:.3f}"
                        )
                    else:
                        print(f"  >>> SELL {qty} × {symbol} @ ${price:.3f}")

            except Exception as e:
                import traceback
                print(f"  [Main] Error on {symbol}: {e}")
                traceback.print_exc()

        # ── Snapshot ──────────────────────────────────────────────────────────
        try:
            pv   = broker.get_portfolio_value()
            cash = broker.get_account_balance()
            print(f"\n  Portfolio: ${pv:,.2f}  |  Cash: ${cash:,.2f}")
            positions = broker.get_positions()
            if positions:
                print("  Open positions:")
                print(_fmt_positions(positions))
        except Exception:
            pass

        # ── Sleep ─────────────────────────────────────────────────────────────
        print(f"  Sleeping {LOOP_INTERVAL_SECS}s…")
        for _ in range(LOOP_INTERVAL_SECS):
            if not _running:
                break
            time.sleep(1)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    print("\n[Main] Shutting down — closing all positions…")
    closed = strategy.close_all_positions(broker)
    for c in closed:
        print(f"  Closed {c['symbol']}: {c['qty']} shares @ ${c['price']:.3f}")
    pv = broker.get_portfolio_value()
    print(f"[Main] Final portfolio value: ${pv:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
