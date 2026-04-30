import csv
import os
from collections import defaultdict, deque
from datetime import datetime

from performance_tracker import EQUITY_LOG_PATH, TRADE_LOG_PATH


def _read_rows(path):
    if not os.path.exists(path):
        return []

    with open(path, "r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _format_timestamp(value):
    if not value:
        return "n/a"

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def _max_drawdown(equity_rows):
    peak = None
    max_drawdown = 0.0
    for row in equity_rows:
        value = _to_float(row.get("portfolio_value"))
        if value <= 0:
            continue
        peak = value if peak is None else max(peak, value)
        drawdown = (value - peak) / peak if peak else 0.0
        max_drawdown = min(max_drawdown, drawdown)
    return abs(max_drawdown) * 100


def _analyze_closed_trades(trades):
    open_lots = defaultdict(deque)
    realized_pnl = 0.0
    winning_closes = 0
    losing_closes = 0
    closed_count = 0
    per_symbol_pnl = defaultdict(float)

    for row in trades:
        action = str(row.get("action") or "").upper()
        symbol = str(row.get("symbol") or "").upper()
        qty = _to_int(row.get("qty"))
        price = _to_float(row.get("price"))
        if qty <= 0 or price <= 0:
            continue

        if action == "BUY":
            open_lots[symbol].append({"qty": qty, "price": price})
            continue

        if action != "SELL":
            continue

        remaining_qty = qty
        close_pnl = 0.0
        matched_qty = 0
        while remaining_qty > 0 and open_lots[symbol]:
            lot = open_lots[symbol][0]
            fill_qty = min(remaining_qty, lot["qty"])
            close_pnl += (price - lot["price"]) * fill_qty
            matched_qty += fill_qty
            lot["qty"] -= fill_qty
            remaining_qty -= fill_qty
            if lot["qty"] <= 0:
                open_lots[symbol].popleft()

        if matched_qty > 0:
            realized_pnl += close_pnl
            per_symbol_pnl[symbol] += close_pnl
            closed_count += 1
            if close_pnl >= 0:
                winning_closes += 1
            else:
                losing_closes += 1

    win_rate = (winning_closes / closed_count * 100) if closed_count else 0.0
    avg_realized_pnl = (realized_pnl / closed_count) if closed_count else 0.0
    return {
        "closed_count": closed_count,
        "win_rate": win_rate,
        "realized_pnl": realized_pnl,
        "avg_realized_pnl": avg_realized_pnl,
        "winning_closes": winning_closes,
        "losing_closes": losing_closes,
        "per_symbol_pnl": dict(sorted(per_symbol_pnl.items())),
    }


def main():
    trades = _read_rows(TRADE_LOG_PATH)
    equity = _read_rows(EQUITY_LOG_PATH)
    closed_trade_stats = _analyze_closed_trades(trades)

    print("=== Trading Bot Performance Report ===")
    print(f"Trade log:  {TRADE_LOG_PATH}")
    print(f"Equity log: {EQUITY_LOG_PATH}")
    print()

    buy_count = sum(1 for row in trades if str(row.get("action") or "").upper() == "BUY")
    sell_count = sum(1 for row in trades if str(row.get("action") or "").upper() == "SELL")
    buy_notional = sum(_to_float(row.get("notional")) for row in trades if str(row.get("action") or "").upper() == "BUY")
    sell_notional = sum(_to_float(row.get("notional")) for row in trades if str(row.get("action") or "").upper() == "SELL")

    print(f"Executed trades:        {len(trades)}")
    print(f"Buys:                   {buy_count} | Sells: {sell_count}")
    print(f"Bought notional:        ${buy_notional:.2f}")
    print(f"Sold notional:          ${sell_notional:.2f}")
    print(f"Closed trade count:     {closed_trade_stats['closed_count']}")
    print(f"Win rate:               {closed_trade_stats['win_rate']:.2f}%")
    print(f"Realized P&L:           ${closed_trade_stats['realized_pnl']:.2f}")
    print(f"Average realized P&L:   ${closed_trade_stats['avg_realized_pnl']:.2f}")
    print()

    if equity:
        start_value = _to_float(equity[0].get("portfolio_value"))
        latest_value = _to_float(equity[-1].get("portfolio_value"))
        change_value = latest_value - start_value
        change_pct = (change_value / start_value * 100) if start_value else 0.0
        max_drawdown_pct = _max_drawdown(equity)

        print(f"Start portfolio value:  ${start_value:.2f}")
        print(f"Latest portfolio value: ${latest_value:.2f}")
        print(f"Net change:             ${change_value:.2f} ({change_pct:.2f}%)")
        print(f"Max drawdown:           {max_drawdown_pct:.2f}%")
        print(f"Last snapshot time:     {_format_timestamp(equity[-1].get('timestamp'))}")
        print(f"Open positions:         {equity[-1].get('open_positions', '0')}")
    else:
        print("No equity snapshots recorded yet.")

    if not trades and equity and _to_int(equity[-1].get("open_positions")) > 0:
        print()
        print("Note: open positions exist, but no tracked trades are in the CSV logs yet.")
        print("They may have been opened before performance tracking or before the latest bot restart.")

    print()
    if closed_trade_stats["per_symbol_pnl"]:
        print("Realized P&L by symbol:")
        for symbol, pnl in closed_trade_stats["per_symbol_pnl"].items():
            print(f"- {symbol}: ${pnl:.2f}")
        print()

    if trades:
        print("Recent trades:")
        for row in trades[-5:]:
            print(
                f"- {_format_timestamp(row.get('timestamp'))} | {row.get('action')} {row.get('qty')} "
                f"{row.get('symbol')} @ ${_to_float(row.get('price')):.2f} | "
                f"sentiment={row.get('sentiment')} | predicted_change={_to_float(row.get('predicted_change_pct')):.2f}%"
            )
    else:
        print("No trades recorded yet. Let the paper bot run and check back later.")


if __name__ == "__main__":
    main()
