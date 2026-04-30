"""
Walk-forward validation for the trading bot.

Each fold:
  - Train window: WALK_FORWARD_TRAIN_MONTHS of history
  - Test window:  WALK_FORWARD_TEST_MONTHS of OOS data immediately following

The engine trains a fresh model on each fold's training window, then runs
the backtest (with full execution-cost model) on the OOS window.  It
aggregates OOS Sharpe, profit factor, max drawdown, and trade count across
all folds and writes a JSON report to models/walk_forward_report.json.

If the median OOS profit factor is below WF_FAIL_PROFIT_FACTOR or median
OOS Sharpe is below WF_FAIL_SHARPE the function returns verdict="fail".
The caller (main.py) can then apply a cautious autonomy profile for the
next trading period.
"""

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from config import (
    WALK_FORWARD_ENABLED,
    WALK_FORWARD_TRAIN_MONTHS,
    WALK_FORWARD_TEST_MONTHS,
    WALK_FORWARD_MIN_FOLDS,
    WALK_FORWARD_MAX_FOLDS,
    WALK_FORWARD_FAIL_PROFIT_FACTOR,
    WALK_FORWARD_FAIL_SHARPE,
    WALK_FORWARD_FAIL_MAX_DD_PCT,
    WALK_FORWARD_REPORT_PATH,
    WALK_FORWARD_CAUTIOUS_RISK_MULTIPLIER,
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
from model import train_model, predict_price, create_model
from sklearn.preprocessing import MinMaxScaler


# ---------------------------------------------------------------------------
# Internal helpers (duplicated from backtest.py to avoid circular deps)
# ---------------------------------------------------------------------------

def _spread_bps(symbol):
    sym = str(symbol or "").upper()
    by_sym = BACKTEST_SPREAD_BPS_BY_SYMBOL or {}
    return float(by_sym.get(sym, BACKTEST_SPREAD_BPS_DEFAULT))


def _slippage_bps(vol):
    v = max(0.0, float(vol or 0.0))
    est = v * float(BACKTEST_SLIPPAGE_VOL_MULTIPLIER) * 10_000.0
    return max(float(BACKTEST_SLIPPAGE_BPS_MIN), min(float(BACKTEST_SLIPPAGE_BPS_MAX), est))


def _fill_price(side, ref, sp_bps, sl_bps):
    r = float(ref)
    s = float(sp_bps) / 10_000.0
    sl = float(sl_bps) / 10_000.0
    return r * (1.0 + s / 2.0 + sl) if side == "BUY" else r * (1.0 - s / 2.0 - sl)


def _commission(shares):
    qty = abs(float(shares or 0.0))
    if qty <= 0:
        return 0.0
    return max(float(BACKTEST_MIN_COMMISSION), qty * float(BACKTEST_COMMISSION_PER_SHARE))


# ---------------------------------------------------------------------------
# OOS simulation on a pre-trained model + scaler pair
# ---------------------------------------------------------------------------

def _run_oos_fold(model, scaler, oos_data, symbol, initial_capital=10_000.0):
    """Run OOS simulation using an already-trained model.  Returns metrics dict."""
    close = oos_data["Close"].astype(float)
    if len(close) < 62:
        return None

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    mom5 = close.pct_change(5).fillna(0.0)
    vol20 = close.pct_change().rolling(20).std().fillna(0.0)

    sp_bps = _spread_bps(symbol)
    lat = max(0, int(BACKTEST_FILL_LATENCY_BARS))
    capital = float(initial_capital)
    shares = 0.0
    entry_price = None
    equity_curve = []
    daily_returns = []
    wins = losses = 0
    gross_profit = gross_loss = 0.0
    prev_equity = initial_capital

    for i in range(60, len(close)):
        cur_price = float(close.iloc[i])
        fill_i = min(len(close) - 1, i + lat)
        del_price = float(close.iloc[fill_i])
        sl_bps = _slippage_bps(float(vol20.iloc[i]))

        recent = close.iloc[i - 60:i].to_numpy()
        scaled = scaler.transform(recent.reshape(-1, 1))
        x = np.reshape(scaled, (1, 60, 1))
        pred_scaled = float(model.predict(x, verbose=0)[0][0])
        pred_price = float(scaler.inverse_transform([[pred_scaled]])[0][0])
        pred_change = (pred_price - cur_price) / cur_price if cur_price else 0.0

        trend_ok = bool(sma20.iloc[i] > sma50.iloc[i] and cur_price > sma20.iloc[i])
        pos_mom = bool(mom5.iloc[i] >= BUY_THRESHOLD_PCT)

        equity_now = capital + shares * cur_price
        equity_curve.append(equity_now)
        ret = (equity_now - prev_equity) / prev_equity if prev_equity else 0.0
        daily_returns.append(ret)
        prev_equity = equity_now

        if shares > 0 and entry_price is not None:
            should_sell = (
                cur_price <= entry_price * (1 - STOP_LOSS_PCT)
                or cur_price >= entry_price * (1 + TAKE_PROFIT_PCT)
                or (pred_change <= -SELL_THRESHOLD_PCT and (not trend_ok or mom5.iloc[i] < 0))
            )
            if should_sell:
                fp = _fill_price("SELL", del_price, sp_bps, sl_bps)
                comm = _commission(shares)
                proceeds = max(0.0, shares * fp - comm)
                trade_pnl = proceeds - (entry_price * shares)
                if trade_pnl >= 0:
                    wins += 1
                    gross_profit += trade_pnl
                else:
                    losses += 1
                    gross_loss += abs(trade_pnl)
                capital = proceeds
                shares = 0.0
                entry_price = None
        elif shares == 0 and pred_change >= BUY_THRESHOLD_PCT and trend_ok and pos_mom:
            fp = _fill_price("BUY", del_price, sp_bps, sl_bps)
            if fp > 0:
                comm = _commission(capital / fp)
                deployable = max(0.0, capital - comm)
                shares = deployable / fp if fp > 0 else 0.0
                if shares > 0:
                    entry_price = fp
                    capital = 0.0

    # Liquidate any open position at the last price for OOS accounting
    if shares > 0:
        last_price = float(close.iloc[-1])
        comm = _commission(shares)
        capital = max(0.0, shares * last_price - comm)
        shares = 0.0

    final_equity = capital
    total_trades = wins + losses
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    # Sharpe (annualised, daily returns, 252 trading days)
    rets = np.array(daily_returns)
    if len(rets) > 1 and rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * math.sqrt(252))
    else:
        sharpe = 0.0

    # Max drawdown
    peak = initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    total_return_pct = (final_equity - initial_capital) / initial_capital * 100.0 if initial_capital > 0 else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / total_trades if total_trades > 0 else 0.0,
        "profit_factor": round(profit_factor, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "total_return_pct": round(total_return_pct, 2),
        "oos_bars": len(close) - 60,
    }


# ---------------------------------------------------------------------------
# Train a model in-memory on a slice of data (no file I/O)
# ---------------------------------------------------------------------------

def _train_in_memory(train_data):
    """Fit model + scaler on train_data, return (model, scaler) without saving."""
    close_vals = train_data["Close"].astype(float).values
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled = scaler.fit_transform(close_vals.reshape(-1, 1))

    x, y = [], []
    for i in range(60, len(scaled)):
        x.append(scaled[i - 60:i, 0])
        y.append(scaled[i, 0])

    if not x:
        return None, None

    x = np.array(x)
    y = np.array(y)
    x = np.reshape(x, (x.shape[0], x.shape[1], 1))

    model = create_model((x.shape[1], 1))
    # Fewer epochs for WF to keep total runtime manageable
    model.fit(x, y, epochs=20, batch_size=32, verbose=0)
    return model, scaler


# ---------------------------------------------------------------------------
# Date-slice helper
# ---------------------------------------------------------------------------

def _slice_by_months(df, anchor_date, train_months, test_months):
    """Return (train_df, oos_df) sliced from df around anchor_date."""
    train_start = anchor_date - timedelta(days=train_months * 30)
    train_end = anchor_date
    oos_start = anchor_date
    oos_end = anchor_date + timedelta(days=test_months * 30)

    df_indexed = df.copy()
    if not hasattr(df_indexed.index, "date"):
        return None, None

    train_df = df_indexed.loc[
        (df_indexed.index >= str(train_start.date()))
        & (df_indexed.index < str(train_end.date()))
    ]
    oos_df = df_indexed.loc[
        (df_indexed.index >= str(oos_start.date()))
        & (df_indexed.index < str(oos_end.date()))
    ]
    return train_df, oos_df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_walk_forward(symbols, initial_capital=10_000.0):
    """
    Run walk-forward validation across symbols and folds.

    Returns:
        {
            "verdict":  "pass" | "fail" | "insufficient_data",
            "folds":    [...per-fold results...],
            "summary":  {median_sharpe, median_pf, median_max_dd, ...},
            "generated_at": ISO timestamp,
            "cautious_risk_multiplier": float,
        }
    """
    if not WALK_FORWARD_ENABLED:
        return {"verdict": "disabled", "folds": [], "summary": {}, "generated_at": _now_iso()}

    train_months = max(3, int(WALK_FORWARD_TRAIN_MONTHS))
    test_months = max(1, int(WALK_FORWARD_TEST_MONTHS))
    min_folds = max(1, int(WALK_FORWARD_MIN_FOLDS))
    max_folds = max(min_folds, int(WALK_FORWARD_MAX_FOLDS))

    all_fold_results = []

    for symbol in symbols:
        try:
            # Fetch enough history: (max_folds * test_months + train_months) months
            lookback_months = train_months + max_folds * test_months + 2
            period_str = f"{lookback_months}mo"
            raw = fetch_stock_data(symbol, period=period_str, use_cache=False)
            data = preprocess_data(raw)
            if len(data) < 60:
                print(f"[walk_forward] {symbol}: insufficient data ({len(data)} rows), skipping.")
                continue

            # Build fold anchor dates walking forward through time
            earliest = data.index[0]
            latest = data.index[-1]
            if not hasattr(earliest, "year"):
                print(f"[walk_forward] {symbol}: unexpected index type, skipping.")
                continue

            # First anchor: train_months after the start of the dataset
            anchor = earliest + timedelta(days=train_months * 30)
            fold_num = 0

            while fold_num < max_folds:
                oos_end = anchor + timedelta(days=test_months * 30)
                if oos_end > latest:
                    break

                train_df, oos_df = _slice_by_months(data, anchor, train_months, test_months)
                if train_df is None or len(train_df) < 62 or len(oos_df) < 62:
                    anchor += timedelta(days=test_months * 30)
                    fold_num += 1
                    continue

                print(
                    f"[walk_forward] {symbol} fold {fold_num + 1}: "
                    f"train {train_df.index[0].date()}→{train_df.index[-1].date()} "
                    f"| OOS {oos_df.index[0].date()}→{oos_df.index[-1].date()}"
                )

                model, scaler = _train_in_memory(train_df)
                if model is None:
                    anchor += timedelta(days=test_months * 30)
                    fold_num += 1
                    continue

                metrics = _run_oos_fold(model, scaler, oos_df, symbol, initial_capital)
                if metrics is not None:
                    metrics["symbol"] = symbol.upper()
                    metrics["fold"] = fold_num + 1
                    metrics["train_start"] = str(train_df.index[0].date())
                    metrics["train_end"] = str(train_df.index[-1].date())
                    metrics["oos_start"] = str(oos_df.index[0].date())
                    metrics["oos_end"] = str(oos_df.index[-1].date())
                    all_fold_results.append(metrics)
                    print(
                        f"[walk_forward] {symbol} fold {fold_num + 1}: "
                        f"PF={metrics['profit_factor']:.2f} Sharpe={metrics['sharpe']:.2f} "
                        f"DD={metrics['max_drawdown_pct']:.1f}% trades={metrics['total_trades']}"
                    )

                anchor += timedelta(days=test_months * 30)
                fold_num += 1

        except Exception as exc:
            print(f"[walk_forward] {symbol}: error — {exc}")
            continue

    if len(all_fold_results) < min_folds:
        result = {
            "verdict": "insufficient_data",
            "folds": all_fold_results,
            "summary": {},
            "generated_at": _now_iso(),
            "cautious_risk_multiplier": float(WALK_FORWARD_CAUTIOUS_RISK_MULTIPLIER),
        }
        _save_report(result)
        return result

    pfs = [f["profit_factor"] for f in all_fold_results if math.isfinite(f["profit_factor"])]
    sharpes = [f["sharpe"] for f in all_fold_results]
    dds = [f["max_drawdown_pct"] for f in all_fold_results]
    returns = [f["total_return_pct"] for f in all_fold_results]

    def _median(vals):
        s = sorted(vals)
        n = len(s)
        return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0) if s else 0.0

    med_pf = _median(pfs)
    med_sharpe = _median(sharpes)
    med_dd = _median(dds)
    med_return = _median(returns)

    # Parameter stability: coefficient of variation of PF across folds
    pf_cv = (float(np.std(pfs)) / float(np.mean(pfs))) if pfs and float(np.mean(pfs)) > 0 else 0.0

    fail_reasons = []
    if med_pf < float(WALK_FORWARD_FAIL_PROFIT_FACTOR):
        fail_reasons.append(
            f"median_pf={med_pf:.2f} < threshold={WALK_FORWARD_FAIL_PROFIT_FACTOR}"
        )
    if med_sharpe < float(WALK_FORWARD_FAIL_SHARPE):
        fail_reasons.append(
            f"median_sharpe={med_sharpe:.2f} < threshold={WALK_FORWARD_FAIL_SHARPE}"
        )
    if med_dd > float(WALK_FORWARD_FAIL_MAX_DD_PCT):
        fail_reasons.append(
            f"median_max_dd={med_dd:.1f}% > threshold={WALK_FORWARD_FAIL_MAX_DD_PCT}%"
        )

    verdict = "fail" if fail_reasons else "pass"

    summary = {
        "folds_evaluated": len(all_fold_results),
        "symbols_evaluated": len(set(f["symbol"] for f in all_fold_results)),
        "median_profit_factor": round(med_pf, 4),
        "median_sharpe": round(med_sharpe, 4),
        "median_max_drawdown_pct": round(med_dd, 2),
        "median_total_return_pct": round(med_return, 2),
        "pf_stability_cv": round(pf_cv, 4),
        "fail_reasons": fail_reasons,
    }

    result = {
        "verdict": verdict,
        "folds": all_fold_results,
        "summary": summary,
        "generated_at": _now_iso(),
        "cautious_risk_multiplier": float(WALK_FORWARD_CAUTIOUS_RISK_MULTIPLIER),
    }

    _save_report(result)
    print(
        f"[walk_forward] verdict={verdict} "
        f"median_pf={med_pf:.2f} median_sharpe={med_sharpe:.2f} "
        f"median_dd={med_dd:.1f}% folds={len(all_fold_results)}"
        + (f" | fail_reasons: {'; '.join(fail_reasons)}" if fail_reasons else "")
    )
    return result


def load_latest_report():
    """Load the most recent walk-forward report from disk, or return None."""
    path = str(WALK_FORWARD_REPORT_PATH)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _save_report(report):
    path = str(WALK_FORWARD_REPORT_PATH)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception as exc:
        print(f"[walk_forward] could not save report: {exc}")


if __name__ == "__main__":
    from config import WATCHLIST
    report = run_walk_forward(WATCHLIST[:3])
    print(json.dumps(report["summary"], indent=2))
    print(f"Verdict: {report['verdict']}")
