import time
from dataclasses import dataclass

import yfinance as yf


def _safe_series(symbol, period):
    try:
        data = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False)
        if data is None or len(data) == 0 or "Close" not in data:
            return None
        close = data["Close"].dropna().astype(float)
        if len(close) < 30:
            return None
        return close
    except Exception:
        return None


def _pct_change(close, periods):
    if close is None or len(close) <= periods:
        return 0.0
    base = float(close.iloc[-periods - 1])
    last = float(close.iloc[-1])
    if abs(base) <= 1e-12:
        return 0.0
    return (last / base) - 1.0


def _drawdown(close):
    if close is None or len(close) < 30:
        return 0.0
    peak = float(close.max())
    if peak <= 0:
        return 0.0
    return (float(close.iloc[-1]) / peak) - 1.0


def _volatility(close, lookback=20):
    if close is None or len(close) <= lookback + 1:
        return 0.0
    ret = close.pct_change().dropna().tail(lookback)
    if len(ret) == 0:
        return 0.0
    return float(ret.std())


@dataclass
class OverlayProfile:
    label: str
    score: float
    risk_multiplier: float
    entry_threshold_multiplier: float
    max_positions_multiplier: float
    allow_new_entries: bool
    confidence: float
    reasons: list

    def as_dict(self):
        return {
            "label": self.label,
            "score": float(self.score),
            "risk_multiplier": float(self.risk_multiplier),
            "entry_threshold_multiplier": float(self.entry_threshold_multiplier),
            "max_positions_multiplier": float(self.max_positions_multiplier),
            "allow_new_entries": bool(self.allow_new_entries),
            "confidence": float(self.confidence),
            "reasons": list(self.reasons),
        }


class MarketOverlay:
    def __init__(self, asset_class="equity", refresh_seconds=1800, lookback_days=365):
        self.asset_class = str(asset_class).strip().lower()
        self.refresh_seconds = max(60, int(refresh_seconds))
        self.lookback_days = max(120, int(lookback_days))
        self._last_ts = 0.0
        self._last_profile = self._neutral_profile(["overlay_init"])

    def _neutral_profile(self, reasons):
        return OverlayProfile(
            label="neutral",
            score=0.0,
            risk_multiplier=1.0,
            entry_threshold_multiplier=1.0,
            max_positions_multiplier=1.0,
            allow_new_entries=True,
            confidence=0.0,
            reasons=reasons,
        )

    def get(self):
        now = time.time()
        if now - self._last_ts < self.refresh_seconds:
            return self._last_profile.as_dict()

        period = f"{max(1, int(self.lookback_days / 30))}mo"
        try:
            if self.asset_class == "crypto":
                profile = self._build_crypto_profile(period)
            else:
                profile = self._build_equity_profile(period)
            self._last_profile = profile
            self._last_ts = now
            return profile.as_dict()
        except Exception:
            self._last_ts = now
            self._last_profile = self._neutral_profile(["overlay_error_fallback"])
            return self._last_profile.as_dict()

    def _build_equity_profile(self, period):
        spy = _safe_series("SPY", period)
        qqq = _safe_series("QQQ", period)
        vix = _safe_series("^VIX", period)
        oil = _safe_series("CL=F", period)

        score = 0.0
        reasons = []

        spy_ret_60 = _pct_change(spy, 60)
        qqq_ret_20 = _pct_change(qqq, 20)
        vix_last = float(vix.iloc[-1]) if vix is not None else 20.0
        oil_ret_20 = _pct_change(oil, 20)
        spy_dd = _drawdown(spy)
        spy_vol = _volatility(spy, 20)

        if spy_ret_60 > 0:
            score += 1.0
            reasons.append(f"spy_60d_up={spy_ret_60*100:.1f}%")
        else:
            score -= 1.0
            reasons.append(f"spy_60d_down={spy_ret_60*100:.1f}%")

        if qqq_ret_20 > 0:
            score += 1.0
            reasons.append(f"qqq_20d_up={qqq_ret_20*100:.1f}%")
        else:
            score -= 1.0
            reasons.append(f"qqq_20d_down={qqq_ret_20*100:.1f}%")

        if vix_last < 18:
            score += 1.0
            reasons.append(f"vix_low={vix_last:.2f}")
        elif vix_last > 26:
            score -= 2.0
            reasons.append(f"vix_high={vix_last:.2f}")
        elif vix_last > 20:
            score -= 1.0
            reasons.append(f"vix_elevated={vix_last:.2f}")

        if oil_ret_20 > 0.12:
            score -= 1.0
            reasons.append(f"oil_spike_20d={oil_ret_20*100:.1f}%")

        if spy_dd < -0.12:
            score -= 1.0
            reasons.append(f"spy_drawdown={spy_dd*100:.1f}%")

        if spy_vol > 0.02:
            score -= 0.5
            reasons.append(f"spy_vol_20d={spy_vol*100:.2f}%")

        if score >= 2.0:
            profile = OverlayProfile(
                label="risk_on",
                score=score,
                risk_multiplier=1.12,
                entry_threshold_multiplier=0.92,
                max_positions_multiplier=1.1,
                allow_new_entries=True,
                confidence=min(0.95, 0.35 + abs(score) * 0.12),
                reasons=reasons,
            )
        elif score >= 0.0:
            profile = OverlayProfile(
                label="balanced",
                score=score,
                risk_multiplier=1.0,
                entry_threshold_multiplier=1.0,
                max_positions_multiplier=1.0,
                allow_new_entries=True,
                confidence=min(0.9, 0.30 + abs(score) * 0.10),
                reasons=reasons,
            )
        elif score > -2.0:
            profile = OverlayProfile(
                label="cautious",
                score=score,
                risk_multiplier=0.72,
                entry_threshold_multiplier=1.22,
                max_positions_multiplier=0.85,
                allow_new_entries=True,
                confidence=min(0.9, 0.30 + abs(score) * 0.10),
                reasons=reasons,
            )
        else:
            profile = OverlayProfile(
                label="defensive",
                score=score,
                risk_multiplier=0.45,
                entry_threshold_multiplier=1.45,
                max_positions_multiplier=0.70,
                allow_new_entries=False,
                confidence=min(0.98, 0.45 + abs(score) * 0.10),
                reasons=reasons,
            )

        return profile

    def _build_crypto_profile(self, period):
        btc = _safe_series("BTC-USD", period)
        eth = _safe_series("ETH-USD", period)
        vix = _safe_series("^VIX", period)

        score = 0.0
        reasons = []

        btc_ret_60 = _pct_change(btc, 60)
        eth_ret_20 = _pct_change(eth, 20)
        btc_ret_5 = _pct_change(btc, 5)
        btc_dd = _drawdown(btc)
        btc_vol = _volatility(btc, 20)
        vix_last = float(vix.iloc[-1]) if vix is not None else 20.0

        if btc_ret_60 > 0:
            score += 1.0
            reasons.append(f"btc_60d_up={btc_ret_60*100:.1f}%")
        else:
            score -= 1.0
            reasons.append(f"btc_60d_down={btc_ret_60*100:.1f}%")

        if eth_ret_20 > 0:
            score += 1.0
            reasons.append(f"eth_20d_up={eth_ret_20*100:.1f}%")
        else:
            score -= 1.0
            reasons.append(f"eth_20d_down={eth_ret_20*100:.1f}%")

        if btc_ret_5 > 0:
            score += 0.5
        else:
            score -= 0.5

        if btc_vol > 0.05:
            score -= 1.0
            reasons.append(f"btc_vol_20d={btc_vol*100:.2f}%")

        if btc_dd < -0.25:
            score -= 1.0
            reasons.append(f"btc_drawdown={btc_dd*100:.1f}%")

        if vix_last > 24:
            score -= 1.0
            reasons.append(f"macro_risk_off_vix={vix_last:.2f}")

        if score >= 2.0:
            profile = OverlayProfile(
                label="risk_on",
                score=score,
                risk_multiplier=1.18,
                entry_threshold_multiplier=0.90,
                max_positions_multiplier=1.12,
                allow_new_entries=True,
                confidence=min(0.95, 0.35 + abs(score) * 0.12),
                reasons=reasons,
            )
        elif score >= 0.0:
            profile = OverlayProfile(
                label="balanced",
                score=score,
                risk_multiplier=1.0,
                entry_threshold_multiplier=1.0,
                max_positions_multiplier=1.0,
                allow_new_entries=True,
                confidence=min(0.9, 0.30 + abs(score) * 0.10),
                reasons=reasons,
            )
        elif score > -2.0:
            profile = OverlayProfile(
                label="cautious",
                score=score,
                risk_multiplier=0.70,
                entry_threshold_multiplier=1.22,
                max_positions_multiplier=0.85,
                allow_new_entries=True,
                confidence=min(0.9, 0.30 + abs(score) * 0.10),
                reasons=reasons,
            )
        else:
            profile = OverlayProfile(
                label="defensive",
                score=score,
                risk_multiplier=0.48,
                entry_threshold_multiplier=1.45,
                max_positions_multiplier=0.70,
                allow_new_entries=False,
                confidence=min(0.98, 0.45 + abs(score) * 0.10),
                reasons=reasons,
            )

        return profile
