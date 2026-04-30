import time
import os
import sys
import json
import urllib.error
import urllib.request

from broker import Broker
from config import (
    AUTONOMOUS_EXECUTION_ENABLED,
    CRYPTO_LOOP_INTERVAL_SECONDS,
    CRYPTO_WATCHLIST,
    INFLUENCER_MONITOR_ENABLED,
    INFLUENCER_MONITOR_CACHE_TTL_SECONDS,
    MARKET_OVERLAY_ENABLED,
    MARKET_OVERLAY_LOOKBACK_DAYS,
    MARKET_OVERLAY_REFRESH_SECONDS,
    SEARCH_API_KEY,
)
from data_fetcher import fetch_external_research_sentiment
from influencer_monitor import monitor_influencers
from strategy import TradingStrategy
from strategy import _pipeline as _promotion_pipeline, _exec_tracker as _exec_quality_tracker

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "shared"))
from scorecard_runtime import build_or_load_setup_scorecard, select_active_candidates, candidate_symbol_set
from market_overlay import MarketOverlay
from drift_detector import DriftDetector
from confidence_pacer import ConfidenceCapitalPacer

_DRIFT_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


_PORTFOLIO_GUARDRAILS_URL = os.getenv("PORTFOLIO_GUARDRAILS_URL", "http://127.0.0.1:8000/portfolio_guardrails")
_PORTFOLIO_GUARDRAILS_CACHE_TTL_SECONDS = int(os.getenv("PORTFOLIO_GUARDRAILS_CACHE_TTL_SECONDS", "90"))
_PORTFOLIO_GUARDRAILS_LAST = {"ts": 0.0, "data": {}}


def _pacing_regime(multiplier):
    val = float(multiplier or 1.0)
    if val <= 0.50:
        return "defensive"
    if val <= 0.85:
        return "cautious"
    return "normal"


def _fetch_portfolio_guardrails():
    now = time.time()
    cached = _PORTFOLIO_GUARDRAILS_LAST.get("data") or {}
    ttl_seconds = max(15, _PORTFOLIO_GUARDRAILS_CACHE_TTL_SECONDS)
    last_ts = float(_PORTFOLIO_GUARDRAILS_LAST.get("ts", 0.0) or 0.0)
    age = now - last_ts
    if cached and age < ttl_seconds:
        return cached

    try:
        req = urllib.request.Request(_PORTFOLIO_GUARDRAILS_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict):
                _PORTFOLIO_GUARDRAILS_LAST["ts"] = now
                _PORTFOLIO_GUARDRAILS_LAST["data"] = payload
                return payload
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        # Use short-lived cache only; stale values should not keep bots paused.
        if cached and age < ttl_seconds:
            return cached
    return {}


def _rank_multipliers(rows):
    multipliers = {}
    for idx, row in enumerate(rows or []):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        expectancy = float(row.get("expectancy", 0.0) or 0.0)
        sample_size = int(row.get("sample_size", 0) or 0)
        if expectancy < 0.005 or sample_size < 100:
            continue
        if idx == 0:
            mult = 1.80
        elif idx == 1:
            mult = 1.25
        else:
            mult = 1.00
        multipliers[symbol] = mult
    return multipliers


def wait_for_account_ready(max_retries=10):
    """Wait for Alpaca account to fully initialize (handles ACCOUNT_CLOSED_PENDING status)."""
    for attempt in range(max_retries):
        try:
            broker = Broker()
            balance = broker.get_account_balance()
            portfolio = broker.get_portfolio_value()
            print(f"✓ Account ready: balance=${balance:.2f}, portfolio=${portfolio:.2f}")
            return broker
        except Exception as e:
            if "ACCOUNT_CLOSED_PENDING" in str(e) or "Pydantic" in str(e.__class__.__name__):
                wait_time = 10 + (attempt * 5)
                print(f"Account still initializing (attempt {attempt + 1}/{max_retries}). Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                # Other error, raise immediately
                raise
    
    raise RuntimeError("Account failed to initialize after maximum retries. Check Alpaca account status.")


def main():
    broker = wait_for_account_ready()
    strategy = TradingStrategy()
    drift_detector = DriftDetector("crypto", _DRIFT_STATE_DIR)
    capital_pacer = ConfidenceCapitalPacer("crypto", _DRIFT_STATE_DIR)
    last_pacing_mult = float(capital_pacer.multiplier)
    last_pacing_regime = _pacing_regime(last_pacing_mult)
    last_setup_scorecard_ts = 0.0
    market_overlay = None
    if MARKET_OVERLAY_ENABLED:
        market_overlay = MarketOverlay(
            asset_class="crypto",
            refresh_seconds=MARKET_OVERLAY_REFRESH_SECONDS,
            lookback_days=MARKET_OVERLAY_LOOKBACK_DAYS,
        )

    print("Crypto bot started in paper-trading mode. Press Ctrl+C to stop.")
    print(f"Watching: {', '.join(CRYPTO_WATCHLIST)}")
    portfolio_guardrail_kill_switch = False

    while True:
        now = time.time()
        if now - last_setup_scorecard_ts >= 1800:
            try:
                setup_payload = build_or_load_setup_scorecard(force=True, max_age_seconds=1800)
                active_crypto_rows = select_active_candidates(setup_payload, asset_class="crypto", limit=3, min_score=0.0)
                qualified_crypto_rows = [
                    row for row in active_crypto_rows
                    if bool(row.get("passed", False))
                    and float(row.get("expectancy", 0.0) or 0.0) >= 0.005
                    and int(row.get("sample_size", 0) or 0) >= 100
                ]
                active_crypto_symbols = candidate_symbol_set(qualified_crypto_rows)
                strategy.apply_setup_candidates(active_crypto_symbols)
                strategy.apply_setup_rank_multipliers(_rank_multipliers(qualified_crypto_rows))
                if qualified_crypto_rows:
                    ranked = ", ".join(
                        f"{row['symbol']}({row['setup']} exp={float(row.get('expectancy', 0.0))*100:.2f}% n={int(row.get('sample_size', 0))})"
                        for row in qualified_crypto_rows[:3]
                    )
                    print(f"Active validated crypto candidates: {ranked}")
                else:
                    # Force strict no-entry posture until a qualified validated
                    # setup appears in the scorecard.
                    strategy.apply_setup_candidates({"__NO_VALID_CRYPTO_SETUP__"})
                    strategy.apply_setup_rank_multipliers({})
                    print("Active validated crypto candidates: none")
            except Exception as e:
                print(f"Setup scorecard refresh failed: {e}")
            last_setup_scorecard_ts = now

        portfolio_value = broker.get_portfolio_value()
        strategy.observe_portfolio_value(portfolio_value)
        if AUTONOMOUS_EXECUTION_ENABLED:
            research = fetch_external_research_sentiment()
            profile = strategy.evaluate_autonomy_profile(research_payload=research)

            overlay = None
            if market_overlay is not None:
                overlay = market_overlay.get()
                profile["allow_new_entries"] = bool(profile.get("allow_new_entries", True)) and bool(
                    overlay.get("allow_new_entries", True)
                )
                profile["risk_multiplier"] = max(
                    0.0,
                    min(
                        1.7,
                        float(profile.get("risk_multiplier", 1.0))
                        * float(overlay.get("risk_multiplier", 1.0)),
                    ),
                )
                profile["buy_threshold_multiplier"] = max(
                    0.7,
                    min(
                        2.8,
                        float(profile.get("buy_threshold_multiplier", 1.0))
                        * float(overlay.get("entry_threshold_multiplier", 1.0)),
                    ),
                )
                profile["max_positions_multiplier"] = max(
                    0.5,
                    min(
                        1.6,
                        float(profile.get("max_positions_multiplier", 1.0))
                        * float(overlay.get("max_positions_multiplier", 1.0)),
                    ),
                )
                profile["market_overlay"] = overlay

            strategy.apply_autonomy_profile(profile)

            guardrails = _fetch_portfolio_guardrails() or {}
            kill_switch_by_bot = guardrails.get("kill_switch_by_bot") or {}
            bot_reasons = guardrails.get("bot_reasons") or {}
            global_hard_stop_active = bool(guardrails.get("global_hard_stop_active", guardrails.get("kill_switch_active", False)))
            global_hard_stop_reasons = guardrails.get("global_hard_stop_reasons") or guardrails.get("reasons") or []
            if isinstance(kill_switch_by_bot, dict) and ("crypto_bot" in kill_switch_by_bot):
                guardrail_kill_switch = bool(kill_switch_by_bot.get("crypto_bot", False))
                reasons = bot_reasons.get("crypto_bot") or []
            else:
                guardrail_kill_switch = bool(guardrails.get("kill_switch_active", False))
                reasons = guardrails.get("reasons") or []
            effective_kill_switch = bool(guardrail_kill_switch or global_hard_stop_active)
            if effective_kill_switch:
                portfolio_guardrail_kill_switch = True
                merged_reasons = list(reasons)
                if global_hard_stop_active:
                    for r in global_hard_stop_reasons:
                        if r not in merged_reasons:
                            merged_reasons.append(r)
                strategy.apply_autonomy_profile({
                    "allow_new_entries": False,
                    "risk_multiplier": 0.0,
                    "mode": "capital_preservation",
                })
                print(
                    "Crypto portfolio guardrail kill-switch active: "
                    + ("; ".join(str(r) for r in merged_reasons) or "risk thresholds breached")
                )
            elif portfolio_guardrail_kill_switch:
                portfolio_guardrail_kill_switch = False
                print("Crypto portfolio guardrail recovered: autonomous entry gating restored.")

            for line in strategy.auto_apply_improvements():
                print(line)
            metrics = profile.get("metrics", {})
            print(
                "Autonomy profile: "
                f"mode={profile.get('mode')} score={profile.get('score')} "
                f"allow_entries={profile.get('allow_new_entries')} risk_mult={profile.get('risk_multiplier')} "
                f"blocked={','.join(profile.get('blocked_symbols', [])) or 'none'} "
                f"closed_7d={metrics.get('closed_trades_7d', 0)} win_7d={float(metrics.get('win_rate_7d', 0.0)):.1%} "
                f"pf_7d={float(metrics.get('profit_factor_7d', 0.0)):.2f} pnl_7d={float(metrics.get('realized_pnl_7d', 0.0)):.2f} "
                f"dd_7d={float(metrics.get('max_drawdown_7d', 0.0)):.2%}"
            )
            if overlay is not None:
                print(
                    "Market overlay: "
                    f"label={overlay.get('label')} "
                    f"score={float(overlay.get('score', 0.0)):.2f} "
                    f"allow_entries={overlay.get('allow_new_entries')} "
                    f"risk_mult={float(overlay.get('risk_multiplier', 1.0)):.2f} "
                    f"entry_mult={float(overlay.get('entry_threshold_multiplier', 1.0)):.2f} "
                    f"confidence={float(overlay.get('confidence', 0.0)):.2f}"
                )

            # Live drift kill-switch: pause new entries when quality degrades.
            closed_7d = int(metrics.get("closed_trades_7d", 0) or 0)
            pf_7d = float(metrics.get("profit_factor_7d", 0.0) or 0.0)
            dd_7d = float(metrics.get("max_drawdown_7d", 0.0) or 0.0)
            if (closed_7d >= 8 and pf_7d < 0.95) or dd_7d > 0.10:
                strategy.apply_autonomy_profile({
                    "allow_new_entries": False,
                    "risk_multiplier": 0.0,
                    "mode": "capital_preservation",
                })
                print(
                    "Risk kill-switch active: pausing new entries "
                    f"(closed_7d={closed_7d}, pf_7d={pf_7d:.2f}, dd_7d={dd_7d:.2%})."
                )
            dominant_topics = ",".join((research.get("dominant_topics") or [])[:4]) or "none"
            strategy_notes = " | ".join((research.get("strategy_notes") or [])[:2]) or "none"
            print(
                "Research regime: "
                f"score={float(research.get('score', 0.0)):.2f} "
                f"provider={research.get('search_provider', 'n/a')} "
                f"headlines={int(research.get('headline_count', 0))} "
                f"topics={dominant_topics}"
            )
            print(f"Research strategy notes: {strategy_notes}")

            # Write influencer data to disk for the dashboard
            if INFLUENCER_MONITOR_ENABLED and SEARCH_API_KEY:
                try:
                    inf_data = monitor_influencers(
                        api_key=SEARCH_API_KEY,
                        cache_ttl_seconds=INFLUENCER_MONITOR_CACHE_TTL_SECONDS,
                    )
                    _inf_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
                    os.makedirs(_inf_log_dir, exist_ok=True)
                    _inf_path = os.path.join(_inf_log_dir, "influencer_analysis.json")
                    with open(_inf_path, "w", encoding="utf-8") as _f:
                        json.dump(inf_data, _f, indent=2)
                    g = inf_data.get("global", {})
                    print(
                        f"Influencer monitor: signal={g.get('dominant_signal','?')} "
                        f"manip={g.get('manipulation_detected',False)} "
                        f"coordination={g.get('coordination_count',0)} "
                        f"actors={g.get('influencer_count',0)}"
                    )
                except Exception as _ie:
                    print(f"Influencer monitor write failed: {_ie}")

        for symbol in CRYPTO_WATCHLIST:
            try:
                signal = strategy.analyze_signal(symbol)
                analysis = strategy.last_analysis.get(symbol, {})

                # Feed drift detector with signal features and current regime.
                drift_detector.update_features({
                    "trend_strength":  float(analysis.get("trend_strength_pct", 0.0) or 0.0) / 100.0,
                    "momentum":        float(analysis.get("momentum_pct", 0.0) or 0.0) / 100.0,
                    "rsi_norm":        (float(analysis.get("rsi", 50.0) or 50.0) - 50.0) / 50.0,
                    "ext_research":    float(analysis.get("external_research_score", 0.0) or 0.0),
                })
                drift_detector.update_regime(str(analysis.get("market_regime", "unknown") or "unknown"))

                print(
                    f"{symbol}: {signal} | "
                    f"trend={analysis.get('trend_strength_pct', 0.0):.2f}% | "
                    f"rsi={analysis.get('rsi', 0.0):.1f} | "
                    f"macd_hist={analysis.get('macd_hist', 0.0):.4f} | "
                    f"atr={analysis.get('atr', 0.0):.4f} | "
                    f"ext={analysis.get('external_research_score', 0.0):.2f} | "
                    f"vol_ok={analysis.get('volume_ok', True)} | "
                    f"momentum={analysis.get('momentum_pct', 0.0):.2f}% | "
                    f"setup={analysis.get('validated_setup', 'none')} "
                    f"pass={analysis.get('setup_passed', False)} "
                    f"exp={analysis.get('setup_expectancy_pct', 0.0):.2f}% "
                    f"n={analysis.get('setup_sample_size', 0)} | "
                    f"pattern_score={analysis.get('pattern_score', 0.0):.2f} "
                    f"({','.join(analysis.get('pattern_hits', [])[:2]) or 'none'}) | "
                    f"autonomy={strategy.autonomy_profile.get('mode', 'normal')}"
                )
                strategy.execute_trade(signal, symbol, broker)
                time.sleep(1)
            except Exception as e:
                print(f"Error processing {symbol}: {e}")

        # Drift detection: apply de-risk multiplier and persist state.
        drift_mult = drift_detector.get_risk_multiplier()
        strategy.drift_risk_multiplier = drift_mult
        drift_detector.save()
        drift_state = drift_detector.get_state()

        # Promotion pipeline: auto-advance canary→live or roll back canary→shadow.
        _exec_metrics = _exec_quality_tracker.get_metrics()
        _promo_events = _promotion_pipeline.evaluate_auto_advance(_exec_metrics)
        for _ev in _promo_events:
            print(f"Promotion pipeline [{_promotion_pipeline.stage}]: {_ev}")

        # Confidence pacing: reliability-driven capital deployment multiplier.
        _pace_mult, _pace_reasons = capital_pacer.update(
            exec_metrics=_exec_metrics,
            drift_state=drift_state,
            pipeline_stage=_promotion_pipeline.stage,
        )
        strategy.confidence_risk_multiplier = _pace_mult
        capital_pacer.save()
        if _pace_reasons:
            print(
                "Capital pacing active: "
                f"mult={_pace_mult:.2f} reasons={_pace_reasons}"
            )

        current_pacing_regime = _pacing_regime(_pace_mult)
        if current_pacing_regime != last_pacing_regime:
            print(
                "CRYPTO ALERT: capital pacing regime changed "
                f"{last_pacing_regime} -> {current_pacing_regime} "
                f"(mult={_pace_mult:.2f}, reasons={_pace_reasons})"
            )
        if abs(float(_pace_mult) - float(last_pacing_mult)) >= 0.15:
            print(
                "CRYPTO ALERT: capital pacing multiplier step-change "
                f"{float(last_pacing_mult):.2f} -> {float(_pace_mult):.2f} "
                f"(regime={current_pacing_regime})"
            )
        last_pacing_mult = float(_pace_mult)
        last_pacing_regime = current_pacing_regime

        if drift_state.get("drift_active"):
            print(
                f"Crypto drift de-risk active: sizing multiplier={drift_mult:.2f} "
                f"flags={drift_state.get('flags', [])}"
            )

        print(
            f"Portfolio snapshot: cash=${broker.get_account_balance():.2f}, "
            f"portfolio=${broker.get_portfolio_value():.2f}"
        )
        print(f"Waiting {CRYPTO_LOOP_INTERVAL_SECONDS} seconds before next cycle...")
        time.sleep(CRYPTO_LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
