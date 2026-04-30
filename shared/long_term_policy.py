import json
import os
import time
from typing import Dict, Tuple


class LongTermPolicy:
    def __init__(
        self,
        bot_name: str,
        max_total_exposure_pct: float,
        max_symbol_exposure_pct: float,
        max_drawdown_pct: float,
    ):
        self.bot_name = str(bot_name or "unknown")
        self.max_total_exposure_pct = max(0.05, min(0.99, float(max_total_exposure_pct)))
        self.max_symbol_exposure_pct = max(0.01, min(0.50, float(max_symbol_exposure_pct)))
        self.max_drawdown_pct = max(0.01, min(0.90, float(max_drawdown_pct)))
        self._state_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "models",
            "long_term_policy_state.json",
        )
        self._cached_state = None
        self._cached_ts = 0.0

    def _default_state(self) -> Dict:
        return {
            "high_watermark": 0.0,
            "last_portfolio_value": 0.0,
            "last_drawdown": 0.0,
            "bots": {},
            "updated_at": 0.0,
        }

    def _load_state(self, force: bool = False) -> Dict:
        now = time.time()
        if not force and self._cached_state is not None and (now - self._cached_ts) < 5:
            return self._cached_state

        if not os.path.exists(self._state_path):
            state = self._default_state()
            self._cached_state = state
            self._cached_ts = now
            return state

        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                payload = self._default_state()
        except Exception:
            payload = self._default_state()

        self._cached_state = payload
        self._cached_ts = now
        return payload

    def _save_state(self, state: Dict) -> None:
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        state["updated_at"] = time.time()
        tmp_path = f"{self._state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp_path, self._state_path)
        self._cached_state = state
        self._cached_ts = time.time()

    def record_portfolio_value(self, portfolio_value: float) -> Dict:
        value = float(portfolio_value or 0.0)
        state = self._load_state(force=True)

        high_watermark = float(state.get("high_watermark", 0.0) or 0.0)
        if value > high_watermark:
            high_watermark = value

        drawdown = 0.0
        if high_watermark > 0:
            drawdown = max(0.0, (high_watermark - value) / high_watermark)

        state["high_watermark"] = high_watermark
        state["last_portfolio_value"] = value
        state["last_drawdown"] = drawdown
        bots = dict(state.get("bots") or {})
        bots[self.bot_name] = {
            "last_portfolio_value": value,
            "last_drawdown": drawdown,
            "updated_at": time.time(),
        }
        state["bots"] = bots
        self._save_state(state)
        return {"high_watermark": high_watermark, "portfolio_value": value, "drawdown": drawdown}

    def get_drawdown(self) -> float:
        state = self._load_state(force=True)
        return float(state.get("last_drawdown", 0.0) or 0.0)

    def drawdown_blocked(self) -> bool:
        return self.get_drawdown() >= self.max_drawdown_pct

    def can_open_position(
        self,
        symbol: str,
        proposed_notional: float,
        portfolio_value: float,
        open_notional: float,
    ) -> Tuple[bool, str]:
        sym = str(symbol or "").upper()
        proposed = max(0.0, float(proposed_notional or 0.0))
        portfolio = max(0.0, float(portfolio_value or 0.0))
        open_value = max(0.0, float(open_notional or 0.0))

        if portfolio <= 0:
            return False, "portfolio_value_unavailable"

        if self.drawdown_blocked():
            return False, "drawdown_guard_active"

        total_exposure_after = (open_value + proposed) / portfolio
        if total_exposure_after > self.max_total_exposure_pct:
            return False, (
                f"total_exposure_cap_exceeded ({total_exposure_after:.1%} > {self.max_total_exposure_pct:.1%})"
            )

        symbol_exposure = proposed / portfolio
        if symbol_exposure > self.max_symbol_exposure_pct:
            return False, (
                f"symbol_exposure_cap_exceeded for {sym} ({symbol_exposure:.1%} > {self.max_symbol_exposure_pct:.1%})"
            )

        return True, "ok"
