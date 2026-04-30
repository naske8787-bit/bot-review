from typing import Dict

import yfinance as yf


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def evaluate_company_fundamentals(
    symbol: str,
    min_score: float,
    min_market_cap_billion: float,
    max_debt_to_equity: float,
    require_positive_fcf: bool,
) -> Dict:
    """
    Fundamentals-first viability gate for long-horizon investing.
    Returns a normalized score and a boolean pass/fail decision.
    """
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}

    market_cap = _safe_float(info.get("marketCap"), 0.0)
    revenue_growth = _safe_float(info.get("revenueGrowth"), 0.0)
    earnings_growth = _safe_float(info.get("earningsGrowth"), 0.0)
    return_on_equity = _safe_float(info.get("returnOnEquity"), 0.0)
    debt_to_equity = _safe_float(info.get("debtToEquity"), 0.0)
    trailing_pe = _safe_float(info.get("trailingPE"), 0.0)
    forward_pe = _safe_float(info.get("forwardPE"), 0.0)
    free_cashflow = _safe_float(info.get("freeCashflow"), 0.0)
    operating_margin = _safe_float(info.get("operatingMargins"), 0.0)

    checks = []
    checks.append(market_cap >= (min_market_cap_billion * 1_000_000_000))
    checks.append(revenue_growth >= 0.03)
    checks.append(earnings_growth >= 0.03)
    checks.append(return_on_equity >= 0.10)
    checks.append(operating_margin >= 0.10)

    # Debt threshold can be relaxed for cash-rich mega-caps with strong margins.
    debt_pass = (debt_to_equity <= max_debt_to_equity) or (market_cap >= 150_000_000_000 and operating_margin >= 0.18)
    checks.append(debt_pass)

    # Valuation sanity check: prefer avoiding extreme multiples unless growth is strong.
    effective_pe = trailing_pe if trailing_pe > 0 else forward_pe
    pe_pass = (effective_pe <= 45.0) or (revenue_growth >= 0.12 and earnings_growth >= 0.12)
    checks.append(pe_pass)

    if require_positive_fcf:
        checks.append(free_cashflow > 0)

    passed_checks = sum(1 for ok in checks if ok)
    score = passed_checks / max(1, len(checks))

    return {
        "symbol": symbol.upper(),
        "passed": bool(score >= float(min_score)),
        "score": round(score, 4),
        "min_score": float(min_score),
        "metrics": {
            "market_cap": market_cap,
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "return_on_equity": return_on_equity,
            "operating_margin": operating_margin,
            "debt_to_equity": debt_to_equity,
            "effective_pe": effective_pe,
            "free_cashflow": free_cashflow,
        },
    }
