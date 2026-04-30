import os
import re
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)


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

for env_path in (
    os.path.join(BASE_DIR, ".env"),
    os.path.join(REPO_ROOT, ".env"),
):
    if os.path.exists(env_path):
        _validate_env_file(env_path)
        load_dotenv(env_path, override=True)


def _parse_symbol_list(value, default):
    raw_value = value or default
    symbols = []
    for part in raw_value.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if "/" not in symbol and symbol.endswith("USD") and len(symbol) > 3:
            symbol = f"{symbol[:-3]}/USD"
        symbols.append(symbol)
    return symbols


def _parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_env_value(value):
    if value is None:
        return ""
    return str(value).split("#", 1)[0].strip()


def _parse_int_env(name, default):
    raw = _clean_env_value(os.getenv(name, str(default)))
    try:
        return int(raw)
    except Exception:
        return int(default)


def _parse_float_env(name, default):
    raw = _clean_env_value(os.getenv(name, str(default)))
    try:
        return float(raw)
    except Exception:
        return float(default)


ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

CRYPTO_WATCHLIST = _parse_symbol_list(
    os.getenv("CRYPTO_WATCHLIST"),
    "BTC/USD,ETH/USD,SOL/USD",
)
CRYPTO_DATA_INTERVAL = os.getenv("CRYPTO_DATA_INTERVAL", "1h")
CRYPTO_LOOKBACK_PERIOD = os.getenv("CRYPTO_LOOKBACK_PERIOD", "90d")
CRYPTO_FAST_EMA_WINDOW = _parse_int_env("CRYPTO_FAST_EMA_WINDOW", 10)
CRYPTO_SLOW_EMA_WINDOW = _parse_int_env("CRYPTO_SLOW_EMA_WINDOW", 30)
CRYPTO_RSI_PERIOD = _parse_int_env("CRYPTO_RSI_PERIOD", 14)
CRYPTO_RSI_BUY_THRESHOLD = _parse_float_env("CRYPTO_RSI_BUY_THRESHOLD", 40)
CRYPTO_RSI_SELL_THRESHOLD = _parse_float_env("CRYPTO_RSI_SELL_THRESHOLD", 68)
CRYPTO_RISK_PER_TRADE = _parse_float_env("CRYPTO_RISK_PER_TRADE", 0.10)
CRYPTO_MAX_POSITIONS = _parse_int_env("CRYPTO_MAX_POSITIONS", 3)
CRYPTO_STOP_LOSS_PCT = _parse_float_env("CRYPTO_STOP_LOSS_PCT", 0.03)
CRYPTO_TAKE_PROFIT_PCT = _parse_float_env("CRYPTO_TAKE_PROFIT_PCT", 0.07)
CRYPTO_LOOP_INTERVAL_SECONDS = _parse_int_env("CRYPTO_LOOP_INTERVAL_SECONDS", 300)
CRYPTO_MIN_NOTIONAL_PER_TRADE = _parse_float_env("CRYPTO_MIN_NOTIONAL_PER_TRADE", 25)
CRYPTO_MIN_TREND_STRENGTH_PCT = _parse_float_env("CRYPTO_MIN_TREND_STRENGTH_PCT", 0.002)
CRYPTO_SELL_QTY_BUFFER_PCT = _parse_float_env("CRYPTO_SELL_QTY_BUFFER_PCT", 0.998)
CRYPTO_PAPER_ONLY = _parse_bool(os.getenv("CRYPTO_PAPER_ONLY"), True)

# MACD parameters
CRYPTO_MACD_FAST = _parse_int_env("CRYPTO_MACD_FAST", 12)
CRYPTO_MACD_SLOW = _parse_int_env("CRYPTO_MACD_SLOW", 26)
CRYPTO_MACD_SIGNAL = _parse_int_env("CRYPTO_MACD_SIGNAL", 9)

# ATR-based trailing stop
CRYPTO_ATR_PERIOD = _parse_int_env("CRYPTO_ATR_PERIOD", 14)
CRYPTO_ATR_STOP_MULTIPLIER = _parse_float_env("CRYPTO_ATR_STOP_MULTIPLIER", 2.0)

# Volume filter: require volume >= this percentile of recent history (0 = disabled)
CRYPTO_MIN_VOLUME_PERCENTILE = _parse_float_env("CRYPTO_MIN_VOLUME_PERCENTILE", 40)

# External research gating thresholds for entries.
# More negative values are more permissive (allow entries under bearish headlines).
CRYPTO_RESEARCH_HARD_BLOCK_SCORE = _parse_float_env("CRYPTO_RESEARCH_HARD_BLOCK_SCORE", -12)
CRYPTO_RESEARCH_SOFT_BLOCK_SCORE = _parse_float_env("CRYPTO_RESEARCH_SOFT_BLOCK_SCORE", -8)
CRYPTO_RESEARCH_ENTRY_GUARD_SCORE = _parse_float_env("CRYPTO_RESEARCH_ENTRY_GUARD_SCORE", -6)
CRYPTO_LOG_HOLD_REASONS = _parse_bool(os.getenv("CRYPTO_LOG_HOLD_REASONS"), True)

# Autonomous execution controls
AUTONOMOUS_EXECUTION_ENABLED = _parse_bool(os.getenv("AUTONOMOUS_EXECUTION_ENABLED"), True)
AUTONOMOUS_MIN_CLOSED_TRADES = _parse_int_env("AUTONOMOUS_MIN_CLOSED_TRADES", 6)
AUTONOMOUS_MIN_WIN_RATE = _parse_float_env("AUTONOMOUS_MIN_WIN_RATE", 0.5)
AUTONOMOUS_MIN_PROFIT_FACTOR = _parse_float_env("AUTONOMOUS_MIN_PROFIT_FACTOR", 1.05)
AUTONOMOUS_MIN_REALIZED_PNL_7D = _parse_float_env("AUTONOMOUS_MIN_REALIZED_PNL_7D", 0)
AUTONOMOUS_MAX_DRAWDOWN_7D_PCT = _parse_float_env("AUTONOMOUS_MAX_DRAWDOWN_7D_PCT", 0.08)
AUTONOMY_LEARNING_ENABLED = _parse_bool(os.getenv("AUTONOMY_LEARNING_ENABLED"), True)
AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE = _parse_float_env("AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE", 0.75)
AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES = _parse_int_env("AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES", AUTONOMOUS_MIN_CLOSED_TRADES)
AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS = _parse_int_env("AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS", 24)
AUTONOMY_LOSS_EVENT_MIN_PNL = _parse_float_env("AUTONOMY_LOSS_EVENT_MIN_PNL", -25)
AUTONOMY_RECOVERY_EVENT_MIN_PNL = _parse_float_env("AUTONOMY_RECOVERY_EVENT_MIN_PNL", 25)

# Bounded autonomous tuning controls (guardrail layer).
AUTONOMY_DYNAMIC_TUNING_ENABLED = _parse_bool(os.getenv("AUTONOMY_DYNAMIC_TUNING_ENABLED"), True)
AUTONOMY_DYNAMIC_STEP = _parse_float_env("AUTONOMY_DYNAMIC_STEP", 0.05)
AUTONOMY_RISK_MULT_MIN = _parse_float_env("AUTONOMY_RISK_MULT_MIN", 0.45)
AUTONOMY_RISK_MULT_MAX = _parse_float_env("AUTONOMY_RISK_MULT_MAX", 1.35)
AUTONOMY_BUY_THRESHOLD_MULT_MIN = _parse_float_env("AUTONOMY_BUY_THRESHOLD_MULT_MIN", 0.80)
AUTONOMY_BUY_THRESHOLD_MULT_MAX = _parse_float_env("AUTONOMY_BUY_THRESHOLD_MULT_MAX", 1.35)
AUTONOMY_MAX_POSITIONS_MULT_MIN = _parse_float_env("AUTONOMY_MAX_POSITIONS_MULT_MIN", 0.70)
AUTONOMY_MAX_POSITIONS_MULT_MAX = _parse_float_env("AUTONOMY_MAX_POSITIONS_MULT_MAX", 1.25)
AUTONOMY_FAILSAFE_DRAWDOWN_PCT = _parse_float_env("AUTONOMY_FAILSAFE_DRAWDOWN_PCT", 0.10)

# External internet research sentiment controls
EXTERNAL_RESEARCH_ENABLED = _parse_bool(os.getenv("EXTERNAL_RESEARCH_ENABLED"), True)
EXTERNAL_RESEARCH_CACHE_TTL_SECONDS = _parse_int_env("EXTERNAL_RESEARCH_CACHE_TTL_SECONDS", 1800)
SEARCH_PROVIDER = str(os.getenv("SEARCH_PROVIDER", "serpapi")).strip().lower()  # brave | serpapi
SEARCH_API_KEY = str(os.getenv("SEARCH_API_KEY") or os.getenv("SERPAPI_API_KEY") or "").strip()
SEARCH_ENGINE = str(os.getenv("SEARCH_ENGINE", "google")).strip().lower()  # for serpapi
EXTERNAL_RESEARCH_MIN_HEADLINES = _parse_int_env("EXTERNAL_RESEARCH_MIN_HEADLINES", 12)
EXTERNAL_RESEARCH_MIN_SOURCES = _parse_int_env("EXTERNAL_RESEARCH_MIN_SOURCES", 3)
EXTERNAL_RESEARCH_MIN_FRESH_RATIO = _parse_float_env("EXTERNAL_RESEARCH_MIN_FRESH_RATIO", 0.25)

# Automatic strategy improvement controls
AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED = _parse_bool(os.getenv("AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED"), True)
AUTO_IMPROVEMENT_REBALANCE_HOURS = _parse_int_env("AUTO_IMPROVEMENT_REBALANCE_HOURS", 24)
AUTO_IMPROVEMENT_LOOKBACK_DAYS = _parse_int_env("AUTO_IMPROVEMENT_LOOKBACK_DAYS", 14)
AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL = _parse_int_env("AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL", 3)

# Long-term investing architecture controls
LONG_TERM_MIN_HOLD_HOURS = _parse_int_env("LONG_TERM_MIN_HOLD_HOURS", 72)
LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT = _parse_float_env("LONG_TERM_MAX_PORTFOLIO_DRAWDOWN_PCT", 0.25)
LONG_TERM_MAX_TOTAL_EXPOSURE_PCT = _parse_float_env("LONG_TERM_MAX_TOTAL_EXPOSURE_PCT", 0.75)
LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT = _parse_float_env("LONG_TERM_MAX_SYMBOL_EXPOSURE_PCT", 0.10)

# Long-horizon capital compounding controls
LONG_HORIZON_ENABLED = _parse_bool(os.getenv("LONG_HORIZON_ENABLED"), True)
LONG_HORIZON_MONTHLY_CONTRIBUTION = _parse_float_env("LONG_HORIZON_MONTHLY_CONTRIBUTION", 1000)
LONG_HORIZON_MAX_RISK_PER_TRADE = _parse_float_env("LONG_HORIZON_MAX_RISK_PER_TRADE", 0.03)
LONG_HORIZON_CASH_BUFFER_PCT = _parse_float_env("LONG_HORIZON_CASH_BUFFER_PCT", 0.10)

# Market Regime Configuration
MARKET_REGIME_SYMBOL = os.getenv("MARKET_REGIME_SYMBOL", "BTC/USD")
MARKET_REGIME_SHORT_WINDOW = _parse_int_env("MARKET_REGIME_SHORT_WINDOW", 20)
MARKET_REGIME_LONG_WINDOW = _parse_int_env("MARKET_REGIME_LONG_WINDOW", 50)

# Crypto Influencer Monitor
# Tracks public statements from known market-moving influencers via Brave Search
# and generates manipulation signals that feed into entry/exit decisions.
INFLUENCER_MONITOR_ENABLED = _parse_bool(os.getenv("INFLUENCER_MONITOR_ENABLED"), True)
# How often to refresh influencer search results (seconds).  Default 15 min.
INFLUENCER_MONITOR_CACHE_TTL_SECONDS = _parse_int_env("INFLUENCER_MONITOR_CACHE_TTL_SECONDS", 900)
# net_signal threshold above which the bot treats this as a pump and boosts entries.
INFLUENCER_PUMP_TRADE_SCORE = _parse_float_env("INFLUENCER_PUMP_TRADE_SCORE", 3.0)
# net_signal threshold below which the bot treats this as a dump and forces exits.
INFLUENCER_DUMP_SELL_SCORE = _parse_float_env("INFLUENCER_DUMP_SELL_SCORE", -3.0)
# manipulation_score above this causes the bot to switch to a tighter take-profit
# "pump-ride" mode (exit sooner before the inevitable dump).
INFLUENCER_MANIPULATION_RIDE_SCORE = _parse_float_env("INFLUENCER_MANIPULATION_RIDE_SCORE", 5.0)
# manipulation_score below this forces an immediate sell of any open position.
INFLUENCER_MANIPULATION_DUMP_SCORE = _parse_float_env("INFLUENCER_MANIPULATION_DUMP_SCORE", -5.0)
# When coordination is detected (2+ influencers same direction) and this flag
# is True, apply an extra confirmation check before buying into the pump.
INFLUENCER_REQUIRE_TECHNICAL_CONFIRM = _parse_bool(
    os.getenv("INFLUENCER_REQUIRE_TECHNICAL_CONFIRM"), True
)

# Cross-market historical + current regime overlay
MARKET_OVERLAY_ENABLED = _parse_bool(os.getenv("MARKET_OVERLAY_ENABLED"), True)
MARKET_OVERLAY_REFRESH_SECONDS = _parse_int_env("MARKET_OVERLAY_REFRESH_SECONDS", 900)
MARKET_OVERLAY_LOOKBACK_DAYS = _parse_int_env("MARKET_OVERLAY_LOOKBACK_DAYS", 365)
