import time
from datetime import datetime, timezone

import os as _os
import sys as _sys

from config import (
	LONG_HORIZON_CASH_BUFFER_PCT,
	LONG_HORIZON_ENABLED,
	LONG_HORIZON_MAX_RISK_PER_TRADE,
	LONG_HORIZON_MONTHLY_CONTRIBUTION,
	LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT,
	MAX_POSITIONS,
	RISK_PER_TRADE,
	TECH_RESEARCH_FORCE_BUY_RISK_MULTIPLIER,
)

_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "shared"))
from execution_quality import ExecutionQualityTracker as _ExecQualTracker
from promotion_pipeline import PromotionPipeline as _PromotionPipeline

_EXEC_LOG_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs", "execution_quality.jsonl")
_exec_tracker = _ExecQualTracker(_EXEC_LOG_PATH)
_PIPELINE_STATE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
_pipeline = _PromotionPipeline("trading", _PIPELINE_STATE_DIR)


class ExecutionService:
	"""Handles broker I/O and order lifecycle. Strategy remains signal-only."""

	def execute_trade(self, strategy, signal, symbol, broker):
		symbol = str(symbol).upper()
		try:
			if signal == "BUY":
				if symbol in strategy.positions:
					return None
				if symbol in strategy.blocked_symbols_by_improvement:
					print(f"Skipping BUY for {symbol}: auto-improvement blocked symbol.")
					return None
				profile = strategy.autonomy_profile
				if not bool(profile.get("allow_new_entries", True)):
					print(f"Skipping BUY for {symbol}: autonomous gate disabled new entries.")
					return None

				effective_max_positions = max(
					1,
					int(MAX_POSITIONS * float(profile.get("max_positions_multiplier", 1.0))),
				)
				if broker.get_open_positions_count() >= effective_max_positions:
					print(f"Skipping BUY for {symbol}: already at max positions.")
					return None
				if hasattr(broker, "has_pending_buy_order") and broker.has_pending_buy_order(symbol):
					print(f"Skipping BUY for {symbol}: pending buy order already exists.")
					return None
				if hasattr(broker, "is_market_open") and not broker.is_market_open(symbol):
					print(f"Skipping BUY for {symbol}: regular market is closed for market orders.")
					return None

				capital = broker.get_account_balance()
				current_price = broker.get_current_price(symbol)
				if current_price <= 0 or capital <= 0:
					print(
						f"Skipping BUY for {symbol}: invalid capital or price "
						f"(capital={capital:.2f}, price={current_price:.4f})."
					)
					return None
				portfolio_value = broker.get_portfolio_value()
				if portfolio_value > 0:
					policy_state = strategy.long_term_policy.record_portfolio_value(portfolio_value)
					if policy_state.get("drawdown", 0.0) >= LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT:
						print(
							f"Skipping BUY for {symbol}: long-term drawdown guard active "
							f"({policy_state.get('drawdown', 0.0):.1%})."
						)
						return None

				entry_analysis = strategy.last_analysis.get(symbol, {})
				_rm_base = RISK_PER_TRADE
				_rm_autonomy = float(profile.get("risk_multiplier", 1.0))
				regime_risk = float(entry_analysis.get("regime_risk_multiplier", 1.0) or 1.0)
				_rm_symbol = float(strategy.symbol_risk_multipliers.get(symbol, 1.0))
				_rm_setup_rank = float(strategy.setup_rank_multipliers.get(symbol, 1.0))
				_rm_drift = max(0.25, min(1.0, float(strategy.drift_risk_multiplier)))
				_rm_confidence = max(0.25, min(1.2, float(strategy.confidence_risk_multiplier)))
				_rm_force = max(0.05, min(1.0, float(TECH_RESEARCH_FORCE_BUY_RISK_MULTIPLIER))) if bool(entry_analysis.get("research_force_buy_triggered", False)) else 1.0
				effective_risk_per_trade = _rm_base * _rm_autonomy * regime_risk * _rm_symbol * _rm_setup_rank * _rm_drift * _rm_confidence * _rm_force
				print(
					f"[risk-breakdown] {symbol}: base={_rm_base:.3f} "
					f"x autonomy={_rm_autonomy:.2f} "
					f"x regime={regime_risk:.2f} "
					f"x symbol={_rm_symbol:.2f} "
					f"x setup_rank={_rm_setup_rank:.2f} "
					f"x drift={_rm_drift:.2f} "
					f"x confidence={_rm_confidence:.2f} "
					f"x force={_rm_force:.2f} "
					f"= {effective_risk_per_trade:.4f} ({effective_risk_per_trade:.1%})"
				)
				deployable_capital = capital
				if LONG_HORIZON_ENABLED:
					deployable_capital = max(0.0, capital * max(0.0, 1.0 - LONG_HORIZON_CASH_BUFFER_PCT))
					effective_risk_per_trade = min(effective_risk_per_trade, float(LONG_HORIZON_MAX_RISK_PER_TRADE))
				target_qty = int((deployable_capital * effective_risk_per_trade) / current_price)
				max_affordable_qty = int(deployable_capital // current_price)
				qty = min(max_affordable_qty, max(1, target_qty)) if max_affordable_qty > 0 else 0
				if qty <= 0:
					print(
						f"Skipping BUY for {symbol}: insufficient buying power for one share "
						f"(capital={capital:.2f}, price={current_price:.4f}, "
						f"risk={effective_risk_per_trade:.4f}, target_qty={target_qty}, "
						f"max_affordable_qty={max_affordable_qty})."
					)
					return None

				proposed_notional = float(qty) * float(current_price)
				open_notional = broker.get_open_notional() if hasattr(broker, "get_open_notional") else 0.0
				allowed, reason = strategy.long_term_policy.can_open_position(
					symbol=symbol,
					proposed_notional=proposed_notional,
					portfolio_value=portfolio_value if portfolio_value > 0 else capital,
					open_notional=open_notional,
				)
				if not allowed:
					print(f"Skipping BUY for {symbol}: {reason}.")
					return None

				if _pipeline.stage == "shadow":
					_pipeline.log_shadow("BUY", symbol, qty, current_price)
					print(f"[shadow] Would BUY {symbol}: {qty} shares at ${current_price:.2f} - not submitted")
					return None
				if _pipeline.stage == "canary":
					qty = max(1, int(qty * _pipeline.canary_size_fraction))

				_eq_rec = _exec_tracker.start_record("BUY", symbol, qty, current_price)
				try:
					broker.buy(symbol, qty)
					_fill = _exec_tracker.poll_fill(broker, symbol, current_price)
					_exec_tracker.finish_record(_eq_rec, fill_price=_fill)
				except Exception as _eq_exc:
					_exec_tracker.finish_record(_eq_rec, rejected=True, reject_reason=str(_eq_exc))
					raise

				entry_context = strategy._build_adaptive_context(
					predicted_change=float(entry_analysis.get("effective_predicted_change_pct", 0.0)) / 100.0,
					trend_strength=float(entry_analysis.get("trend_strength_pct", 0.0)) / 100.0,
					sentiment=float(entry_analysis.get("sentiment", 0.0)),
					news_score=float(entry_analysis.get("news_score", 0.0)),
					sector_tailwind=bool(entry_analysis.get("sector_tailwind", False)),
					high_fear=str(entry_analysis.get("fear_level", "")).lower() in ("high", "extreme"),
					market_favorable=bool(entry_analysis.get("market_favorable", True)),
				)
				strategy.positions[symbol] = {
					"entry_price": current_price,
					"qty": qty,
					"entry_context": entry_context,
					"entry_ts": time.time(),
				}
				strategy.last_trade_times[symbol] = time.time()
				if bool(entry_analysis.get("research_force_buy_triggered", False)):
					print(
						f"BUY signal for {symbol}: {qty} shares at ${current_price:.2f} "
						f"[research_force_buy p={float(entry_analysis.get('research_force_buy_probability', 0.0))*100:.1f}% "
						f"impact={float(entry_analysis.get('research_force_buy_impact_score', 0.0)):.2f} "
						f"evidence={int(entry_analysis.get('research_force_buy_evidence_count', 0))}]"
					)
				else:
					print(f"BUY signal for {symbol}: {qty} shares at ${current_price:.2f}")
				if LONG_HORIZON_ENABLED:
					print(
						f"Long-horizon sizing active: monthly_contribution=${LONG_HORIZON_MONTHLY_CONTRIBUTION:.2f}, "
						f"cash_buffer={LONG_HORIZON_CASH_BUFFER_PCT:.0%}, risk_cap={LONG_HORIZON_MAX_RISK_PER_TRADE:.2%}"
					)
				return {"action": "BUY", "symbol": symbol, "qty": qty, "price": current_price}

			if signal == "SELL":
				local_position = strategy.positions.get(symbol, {})
				synced_position = strategy._sync_position(symbol, broker)
				qty = int(round(broker.get_position_size(symbol)))
				if qty <= 0:
					qty = int((synced_position or strategy.positions.get(symbol, {})).get("qty", 0))
				if qty <= 0:
					print(f"Skipping SELL for {symbol}: no open quantity found.")
					strategy.positions.pop(symbol, None)
					return None

				current_price = broker.get_current_price(symbol)
				entry_price_for_learning = float(
					local_position.get("entry_price")
					or (synced_position or {}).get("entry_price")
					or current_price
				)
				entry_context = local_position.get("entry_context")
				if not entry_context:
					analysis = strategy.last_analysis.get(symbol, {})
					entry_context = strategy._build_adaptive_context(
						predicted_change=float(analysis.get("effective_predicted_change_pct", 0.0)) / 100.0,
						trend_strength=float(analysis.get("trend_strength_pct", 0.0)) / 100.0,
						sentiment=float(analysis.get("sentiment", 0.0)),
						news_score=float(analysis.get("news_score", 0.0)),
						sector_tailwind=bool(analysis.get("sector_tailwind", False)),
						high_fear=str(analysis.get("fear_level", "")).lower() in ("high", "extreme"),
						market_favorable=bool(analysis.get("market_favorable", True)),
					)
				hold_minutes = 0.0
				if local_position.get("entry_ts"):
					hold_minutes = (time.time() - float(local_position["entry_ts"])) / 60.0

				if _pipeline.stage == "shadow":
					_pipeline.log_shadow("SELL", symbol, qty, current_price)
					print(f"[shadow] Would SELL {symbol}: {qty} shares at ${current_price:.2f} - not submitted")
					return None

				_eq_rec = _exec_tracker.start_record("SELL", symbol, qty, current_price)
				try:
					broker.sell(symbol, qty)
					_fill = _exec_tracker.poll_fill(broker, symbol, current_price)
					_exec_tracker.finish_record(_eq_rec, fill_price=_fill)
				except Exception as _eq_exc:
					_exec_tracker.finish_record(_eq_rec, rejected=True, reject_reason=str(_eq_exc))
					raise

				strategy.experience_policy.observe_trade(
					symbol=symbol,
					entry_context=entry_context,
					entry_price=entry_price_for_learning,
					exit_price=current_price,
					hold_minutes=hold_minutes,
				)
				pnl = (float(current_price) - float(entry_price_for_learning)) * float(qty)
				strategy.trade_history.append(
					{
						"ts": datetime.now(timezone.utc),
						"symbol": symbol,
						"pnl": float(pnl),
					}
				)
				strategy.positions.pop(symbol, None)
				strategy.last_trade_times[symbol] = time.time()
				print(f"SELL signal for {symbol}: {qty} shares at ${current_price:.2f}")
				return {"action": "SELL", "symbol": symbol, "qty": qty, "price": current_price}
		except Exception as e:
			print(f"Error executing trade for {symbol}: {e}")
		return None


execution_service = ExecutionService()
