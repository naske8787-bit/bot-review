import os
import re
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _validate_env_file(env_path):
    if not os.path.exists(env_path):
        return

    key_prefix_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
    embedded_key_re = re.compile(r"[A-Z][A-Z0-9_]{2,}\s*=")

    with open(env_path, "r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if not key_prefix_re.match(line):
                continue

            _, value = line.split("=", 1)
            for match in embedded_key_re.finditer(value):
                idx = match.start()
                prev = value[idx - 1] if idx > 0 else ""
                if prev.isalnum() or prev == "_":
                    token = match.group(0).strip()
                    raise RuntimeError(
                        f"Malformed .env at {env_path}:{line_no} - detected concatenated assignment before '{token}'. "
                        "Put each KEY=VALUE on its own line."
                    )


_ENV_PATH = os.path.join(BASE_DIR, ".env")
_validate_env_file(_ENV_PATH)
load_dotenv(_ENV_PATH, override=True)


def _parse_symbol_list(value, default):
    raw_value = value or default
    return [symbol.strip().upper() for symbol in raw_value.split(",") if symbol.strip()]


def _parse_int_list(value, default):
    raw_value = value or default
    parsed = []
    for part in str(raw_value).split(","):
        token = part.strip()
        if not token:
            continue
        try:
            val = int(token)
            if val > 0:
                parsed.append(val)
        except ValueError:
            continue
    return parsed


def _int_env(env_key, default):
    raw = str(os.getenv(env_key, str(default))).split("#", 1)[0].strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


def _parse_float_map(value):
    parsed = {}
    for part in str(value or "").split(","):
        token = part.strip()
        if not token or ":" not in token:
            continue
        key, raw_val = token.split(":", 1)
        key = key.strip().upper()
        try:
            parsed[key] = float(raw_val.strip())
        except Exception:
            continue
    return parsed


# API Keys
CAPITOL_TRADES_API_URL = os.getenv("CAPITOL_TRADES_API_URL", "https://www.capitoltrades.com")
CAPITOL_TRADES_MAX_PAGES = int(os.getenv("CAPITOL_TRADES_MAX_PAGES", "5"))
CAPITOL_TRADES_REQUEST_RETRIES = int(os.getenv("CAPITOL_TRADES_REQUEST_RETRIES", "3"))
CAPITOL_TRADES_RETRY_BACKOFF_SECONDS = float(os.getenv("CAPITOL_TRADES_RETRY_BACKOFF_SECONDS", "1.5"))
CAPITOL_TRADES_FAILURE_RETRY_SECONDS = int(os.getenv("CAPITOL_TRADES_FAILURE_RETRY_SECONDS", "90"))
CAPITOL_TRADES_PRIMARY_SOURCE = (os.getenv("CAPITOL_TRADES_PRIMARY_SOURCE", "quiver") or "quiver").strip().lower()
QUIVER_API_KEY = (os.getenv("QUIVER_API_KEY") or "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")  # Use paper trading for testing
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")  # "iex" (free) or "sip" (paid, full market)

# IBKR settings (used for international symbols)
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = _int_env("IBKR_PORT", 4002)          # 4002=IB Gateway paper, 4001=live, 7497=TWS paper
IBKR_CLIENT_ID = _int_env("IBKR_CLIENT_ID", 1)
IBKR_ENABLED = os.getenv("IBKR_ENABLED", "false").lower() == "true"

# Trading settings
INITIAL_CAPITAL = 10000
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.03"))        # 3% of capital per trade (was 5%)
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
BUY_THRESHOLD_PCT = float(os.getenv("BUY_THRESHOLD_PCT", "0.005"))  # 0.5% predicted upside required (was 0.1%)
SELL_THRESHOLD_PCT = float(os.getenv("SELL_THRESHOLD_PCT", "0.005")) # 0.5% predicted downside to sell (was 0.1%)
MIN_SENTIMENT_TO_BUY = int(os.getenv("MIN_SENTIMENT_TO_BUY", "1"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.03"))            # 3% stop loss (was 5%) — cut losses faster
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.12"))        # 12% take profit — 1:4 risk/reward
MIN_TREND_STRENGTH_PCT = float(os.getenv("MIN_TREND_STRENGTH_PCT", "0.005")) # 0.5% trend confirmation (was 0.1%)
TRADE_COOLDOWN_MINUTES = int(os.getenv("TRADE_COOLDOWN_MINUTES", "30")) # 30 min cooldown (was 15) — less overtrading
MARKET_REGIME_SYMBOL = os.getenv("MARKET_REGIME_SYMBOL", "SPY")
MARKET_REGIME_SHORT_WINDOW = int(os.getenv("MARKET_REGIME_SHORT_WINDOW", "50"))
MARKET_REGIME_LONG_WINDOW = int(os.getenv("MARKET_REGIME_LONG_WINDOW", "200"))
STOCK_DATA_CACHE_TTL_SECONDS = int(os.getenv("STOCK_DATA_CACHE_TTL_SECONDS", "900"))
CAPITOL_DATA_MIN_CONFIDENCE_TO_TRADE = float(os.getenv("CAPITOL_DATA_MIN_CONFIDENCE_TO_TRADE", "0.35"))
CAPITOL_DATA_LOW_CONFIDENCE_RISK_MULTIPLIER = float(
    os.getenv("CAPITOL_DATA_LOW_CONFIDENCE_RISK_MULTIPLIER", "0.55")
)
WATCHLIST = _parse_symbol_list(
    os.getenv("WATCHLIST"),
    "AAPL,MSFT,NVDA,GOOGL,TSLA,AMZN",
)
# International symbols for IBKR — use exchange suffix format: SYMBOL:EXCHANGE
# e.g. SHELL:AEB, ASML:AEB, BP:LSE, 7203:TSE
IBKR_WATCHLIST = _parse_symbol_list(
    os.getenv("IBKR_WATCHLIST"),
    "",
)
TRAINING_SYMBOLS = _parse_symbol_list(
    os.getenv("TRAINING_SYMBOLS"),
    ",".join(WATCHLIST),
)
ETF_SYMBOLS = set(_parse_symbol_list(
    os.getenv("ETF_SYMBOLS"),
    "GLD,SLV,GDX,USO,SPY,QQQ,IAU,CPER",
))

# Backtest execution cost model
BACKTEST_SPREAD_BPS_DEFAULT = float(os.getenv("BACKTEST_SPREAD_BPS_DEFAULT", "8"))
BACKTEST_SPREAD_BPS_BY_SYMBOL = _parse_float_map(os.getenv("BACKTEST_SPREAD_BPS_BY_SYMBOL", ""))
BACKTEST_SLIPPAGE_VOL_MULTIPLIER = float(os.getenv("BACKTEST_SLIPPAGE_VOL_MULTIPLIER", "0.25"))
BACKTEST_SLIPPAGE_BPS_MIN = float(os.getenv("BACKTEST_SLIPPAGE_BPS_MIN", "2"))
BACKTEST_SLIPPAGE_BPS_MAX = float(os.getenv("BACKTEST_SLIPPAGE_BPS_MAX", "80"))
BACKTEST_COMMISSION_PER_SHARE = float(os.getenv("BACKTEST_COMMISSION_PER_SHARE", "0.005"))
BACKTEST_MIN_COMMISSION = float(os.getenv("BACKTEST_MIN_COMMISSION", "1.00"))
BACKTEST_FILL_LATENCY_BARS = int(os.getenv("BACKTEST_FILL_LATENCY_BARS", "1"))

# Model settings
MODEL_PATH = os.path.join(BASE_DIR, "models", "trading_model.h5")

# Auto-retraining settings
AUTO_RETRAIN_ENABLED = os.getenv("AUTO_RETRAIN_ENABLED", "true").lower() == "true"
AUTO_RETRAIN_INTERVAL_HOURS = int(os.getenv("AUTO_RETRAIN_INTERVAL_HOURS", "24"))
RETRAIN_LOOKBACK_PERIOD = os.getenv("RETRAIN_LOOKBACK_PERIOD", "1y")

# Walk-forward validation
WALK_FORWARD_ENABLED = os.getenv("WALK_FORWARD_ENABLED", "true").lower() == "true"
WALK_FORWARD_INTERVAL_HOURS = int(os.getenv("WALK_FORWARD_INTERVAL_HOURS", "168"))  # weekly
WALK_FORWARD_TRAIN_MONTHS = int(os.getenv("WALK_FORWARD_TRAIN_MONTHS", "6"))
WALK_FORWARD_TEST_MONTHS = int(os.getenv("WALK_FORWARD_TEST_MONTHS", "1"))
WALK_FORWARD_MIN_FOLDS = int(os.getenv("WALK_FORWARD_MIN_FOLDS", "3"))
WALK_FORWARD_MAX_FOLDS = int(os.getenv("WALK_FORWARD_MAX_FOLDS", "6"))
WALK_FORWARD_FAIL_PROFIT_FACTOR = float(os.getenv("WALK_FORWARD_FAIL_PROFIT_FACTOR", "0.90"))
WALK_FORWARD_FAIL_SHARPE = float(os.getenv("WALK_FORWARD_FAIL_SHARPE", "-0.20"))
WALK_FORWARD_FAIL_MAX_DD_PCT = float(os.getenv("WALK_FORWARD_FAIL_MAX_DD_PCT", "30.0"))
WALK_FORWARD_CAUTIOUS_RISK_MULTIPLIER = float(os.getenv("WALK_FORWARD_CAUTIOUS_RISK_MULTIPLIER", "0.50"))
WALK_FORWARD_REPORT_PATH = os.path.join(BASE_DIR, "models", "walk_forward_report.json")

# Event/news learning settings
EVENT_LEARNER_ALPHA = float(os.getenv("EVENT_LEARNER_ALPHA", "0.15"))
EVENT_MAX_EDGE_ADJUSTMENT_PCT = float(os.getenv("EVENT_MAX_EDGE_ADJUSTMENT_PCT", "0.8"))
EVENT_LEARNER_LAGS = _parse_int_list(os.getenv("EVENT_LEARNER_LAGS"), "1,3,6")
EVENT_BOOTSTRAP_ENABLED = os.getenv("EVENT_BOOTSTRAP_ENABLED", "true").lower() == "true"
EVENT_BOOTSTRAP_YEARS = int(os.getenv("EVENT_BOOTSTRAP_YEARS", "50"))
EVENT_BOOTSTRAP_INTERVAL = os.getenv("EVENT_BOOTSTRAP_INTERVAL", "1mo")
EVENT_BOOTSTRAP_MIN_OBSERVATIONS = int(os.getenv("EVENT_BOOTSTRAP_MIN_OBSERVATIONS", "120"))
EVENT_INFLUENCE_REPORT_ENABLED = os.getenv("EVENT_INFLUENCE_REPORT_ENABLED", "true").lower() == "true"
EVENT_INFLUENCE_REPORT_INTERVAL_MINUTES = int(os.getenv("EVENT_INFLUENCE_REPORT_INTERVAL_MINUTES", "1440"))
EVENT_INFLUENCE_REPORT_TOPICS = int(os.getenv("EVENT_INFLUENCE_REPORT_TOPICS", "8"))
EVENT_INFLUENCE_REPORT_SYMBOLS = int(os.getenv("EVENT_INFLUENCE_REPORT_SYMBOLS", "6"))

# Adaptive experience policy settings
ADAPTIVE_POLICY_ENABLED = os.getenv("ADAPTIVE_POLICY_ENABLED", "true").lower() == "true"
ADAPTIVE_POLICY_LEARNING_RATE = float(os.getenv("ADAPTIVE_POLICY_LEARNING_RATE", "0.08"))
ADAPTIVE_POLICY_DECAY = float(os.getenv("ADAPTIVE_POLICY_DECAY", "0.999"))
ADAPTIVE_POLICY_MAX_ADJUSTMENT_PCT = float(os.getenv("ADAPTIVE_POLICY_MAX_ADJUSTMENT_PCT", "0.6"))

# Autonomous execution + experimentation controls
AUTONOMOUS_EXECUTION_ENABLED = os.getenv("AUTONOMOUS_EXECUTION_ENABLED", "true").lower() == "true"
AUTONOMOUS_MIN_CLOSED_TRADES = int(os.getenv("AUTONOMOUS_MIN_CLOSED_TRADES", "8"))
AUTONOMOUS_MIN_WIN_RATE = float(os.getenv("AUTONOMOUS_MIN_WIN_RATE", "0.52"))
AUTONOMOUS_MIN_PROFIT_FACTOR = float(os.getenv("AUTONOMOUS_MIN_PROFIT_FACTOR", "1.10"))
AUTONOMOUS_MIN_REALIZED_PNL_7D = float(os.getenv("AUTONOMOUS_MIN_REALIZED_PNL_7D", "0"))
AUTONOMOUS_MAX_DRAWDOWN_7D_PCT = float(os.getenv("AUTONOMOUS_MAX_DRAWDOWN_7D_PCT", "0.06"))
AUTONOMY_LEARNING_ENABLED = os.getenv("AUTONOMY_LEARNING_ENABLED", "true").lower() == "true"
AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE = float(os.getenv("AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE", "0.75"))
AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES = int(
    os.getenv("AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES", str(AUTONOMOUS_MIN_CLOSED_TRADES))
)
AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS = int(os.getenv("AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS", "24"))
AUTONOMY_LOSS_EVENT_MIN_PNL = float(os.getenv("AUTONOMY_LOSS_EVENT_MIN_PNL", "-25"))
AUTONOMY_RECOVERY_EVENT_MIN_PNL = float(os.getenv("AUTONOMY_RECOVERY_EVENT_MIN_PNL", "25"))

# Bounded autonomous tuning controls (guardrail layer).
AUTONOMY_DYNAMIC_TUNING_ENABLED = os.getenv("AUTONOMY_DYNAMIC_TUNING_ENABLED", "true").lower() == "true"
AUTONOMY_DYNAMIC_STEP = float(os.getenv("AUTONOMY_DYNAMIC_STEP", "0.05"))
AUTONOMY_RISK_MULT_MIN = float(os.getenv("AUTONOMY_RISK_MULT_MIN", "0.45"))
AUTONOMY_RISK_MULT_MAX = float(os.getenv("AUTONOMY_RISK_MULT_MAX", "1.35"))
AUTONOMY_BUY_THRESHOLD_MULT_MIN = float(os.getenv("AUTONOMY_BUY_THRESHOLD_MULT_MIN", "0.80"))
AUTONOMY_BUY_THRESHOLD_MULT_MAX = float(os.getenv("AUTONOMY_BUY_THRESHOLD_MULT_MAX", "1.35"))
AUTONOMY_MAX_POSITIONS_MULT_MIN = float(os.getenv("AUTONOMY_MAX_POSITIONS_MULT_MIN", "0.70"))
AUTONOMY_MAX_POSITIONS_MULT_MAX = float(os.getenv("AUTONOMY_MAX_POSITIONS_MULT_MAX", "1.25"))
AUTONOMY_FAILSAFE_DRAWDOWN_PCT = float(os.getenv("AUTONOMY_FAILSAFE_DRAWDOWN_PCT", "0.10"))

# External internet research settings
EXTERNAL_RESEARCH_ENABLED = os.getenv("EXTERNAL_RESEARCH_ENABLED", "true").lower() == "true"
EXTERNAL_RESEARCH_CACHE_TTL_SECONDS = int(os.getenv("EXTERNAL_RESEARCH_CACHE_TTL_SECONDS", "1800"))
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "serpapi").strip().lower()  # brave | serpapi
SEARCH_API_KEY = (os.getenv("SEARCH_API_KEY") or os.getenv("SERPAPI_API_KEY") or "").strip()
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "google").strip().lower()  # for serpapi
EXTERNAL_RESEARCH_MIN_HEADLINES = int(os.getenv("EXTERNAL_RESEARCH_MIN_HEADLINES", "12"))
EXTERNAL_RESEARCH_MIN_SOURCES = int(os.getenv("EXTERNAL_RESEARCH_MIN_SOURCES", "3"))
EXTERNAL_RESEARCH_MIN_FRESH_RATIO = float(os.getenv("EXTERNAL_RESEARCH_MIN_FRESH_RATIO", "0.25"))

# Research bot assisted force-buy controls
TECH_RESEARCH_FORCE_BUY_ENABLED = os.getenv("TECH_RESEARCH_FORCE_BUY_ENABLED", "true").lower() == "true"
TECH_RESEARCH_FORCE_BUY_MIN_PROBABILITY = float(os.getenv("TECH_RESEARCH_FORCE_BUY_MIN_PROBABILITY", "0.85"))
TECH_RESEARCH_FORCE_BUY_MIN_IMPACT_SCORE = float(os.getenv("TECH_RESEARCH_FORCE_BUY_MIN_IMPACT_SCORE", "6.5"))
TECH_RESEARCH_FORCE_BUY_MIN_EVIDENCE_COUNT = int(os.getenv("TECH_RESEARCH_FORCE_BUY_MIN_EVIDENCE_COUNT", "3"))
TECH_RESEARCH_FORCE_BUY_MAX_SIGNAL_AGE_HOURS = int(os.getenv("TECH_RESEARCH_FORCE_BUY_MAX_SIGNAL_AGE_HOURS", "48"))
TECH_RESEARCH_FORCE_BUY_MAX_CANDIDATES = int(os.getenv("TECH_RESEARCH_FORCE_BUY_MAX_CANDIDATES", "12"))
TECH_RESEARCH_FORCE_BUY_RISK_MULTIPLIER = float(os.getenv("TECH_RESEARCH_FORCE_BUY_RISK_MULTIPLIER", "0.4"))

# Automatic strategy improvement controls
AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED = os.getenv("AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED", "true").lower() == "true"
AUTO_IMPROVEMENT_REBALANCE_HOURS = int(os.getenv("AUTO_IMPROVEMENT_REBALANCE_HOURS", "24"))
AUTO_IMPROVEMENT_LOOKBACK_DAYS = int(os.getenv("AUTO_IMPROVEMENT_LOOKBACK_DAYS", "14"))
AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL = int(os.getenv("AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL", "3"))

# Long-term investing architecture controls
FUNDAMENTALS_GATE_ENABLED = os.getenv("FUNDAMENTALS_GATE_ENABLED", "true").lower() == "true"
FUNDAMENTALS_MIN_SCORE = float(os.getenv("FUNDAMENTALS_MIN_SCORE", "0.65"))
FUNDAMENTALS_MIN_MARKET_CAP_BILLION = float(os.getenv("FUNDAMENTALS_MIN_MARKET_CAP_BILLION", "10"))
FUNDAMENTALS_MAX_DEBT_TO_EQUITY = float(os.getenv("FUNDAMENTALS_MAX_DEBT_TO_EQUITY", "200"))
FUNDAMENTALS_REQUIRE_POSITIVE_FCF = os.getenv("FUNDAMENTALS_REQUIRE_POSITIVE_FCF", "true").lower() == "true"
LONG_TERM_MIN_HOLD_HOURS = int(os.getenv("LONG_TERM_MIN_HOLD_HOURS", "168"))
LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT = float(os.getenv("LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT", "0.25"))
LONG_TERM_MAX_TOTAL_EXPOSURE_PCT = float(os.getenv("LONG_TERM_MAX_TOTAL_EXPOSURE_PCT", "0.85"))
LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT = float(os.getenv("LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT", "0.12"))

# Growth-momentum buy pathway: buy strongly trending stocks independently of Capitol Trades signal.
# Requires a higher technical bar (2× trend, 2× momentum) + macro backing to compensate
# for the absence of political confirmation. Acts as a complement, not a replacement.
GROWTH_MOMENTUM_BUY_ENABLED = os.getenv("GROWTH_MOMENTUM_BUY_ENABLED", "true").lower() == "true"
GROWTH_MOMENTUM_MIN_TREND_MULTIPLIER = float(os.getenv("GROWTH_MOMENTUM_MIN_TREND_MULTIPLIER", "1.5"))
GROWTH_MOMENTUM_MIN_RETURN_MULTIPLIER = float(os.getenv("GROWTH_MOMENTUM_MIN_RETURN_MULTIPLIER", "2.0"))

# Long-horizon capital compounding controls
LONG_HORIZON_ENABLED = os.getenv("LONG_HORIZON_ENABLED", "true").lower() == "true"
LONG_HORIZON_MONTHLY_CONTRIBUTION = float(os.getenv("LONG_HORIZON_MONTHLY_CONTRIBUTION", "1000"))
LONG_HORIZON_MAX_RISK_PER_TRADE = float(os.getenv("LONG_HORIZON_MAX_RISK_PER_TRADE", "0.015"))
LONG_HORIZON_CASH_BUFFER_PCT = float(os.getenv("LONG_HORIZON_CASH_BUFFER_PCT", "0.10"))

# Cross-market historical + current regime overlay
MARKET_OVERLAY_ENABLED = os.getenv("MARKET_OVERLAY_ENABLED", "true").lower() == "true"
MARKET_OVERLAY_REFRESH_SECONDS = int(os.getenv("MARKET_OVERLAY_REFRESH_SECONDS", "1800"))
MARKET_OVERLAY_LOOKBACK_DAYS = int(os.getenv("MARKET_OVERLAY_LOOKBACK_DAYS", "365"))

# Observability and notifications
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
ALERT_MIN_INTERVAL_SECONDS = int(os.getenv("ALERT_MIN_INTERVAL_SECONDS", "900"))
ALERT_WEBHOOK_URL = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
ALERT_TELEGRAM_BOT_TOKEN = (os.getenv("ALERT_TELEGRAM_BOT_TOKEN") or "").strip()
ALERT_TELEGRAM_CHAT_ID = (os.getenv("ALERT_TELEGRAM_CHAT_ID") or "").strip()
ALERT_SOURCE_DEGRADED_ENABLED = os.getenv("ALERT_SOURCE_DEGRADED_ENABLED", "true").lower() == "true"
ALERT_KILL_SWITCH_ENABLED = os.getenv("ALERT_KILL_SWITCH_ENABLED", "true").lower() == "true"
ALERT_SOURCE_STALE_SECONDS = int(os.getenv("ALERT_SOURCE_STALE_SECONDS", "10800"))
ALERT_SELF_TEST_ON_START = os.getenv("ALERT_SELF_TEST_ON_START", "false").lower() == "true"
ALERT_SYMBOL_ERROR_THRESHOLD = int(os.getenv("ALERT_SYMBOL_ERROR_THRESHOLD", "3"))
ALERT_SYMBOL_ERROR_COOLDOWN_SECONDS = int(os.getenv("ALERT_SYMBOL_ERROR_COOLDOWN_SECONDS", "1800"))