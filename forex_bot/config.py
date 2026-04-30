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


def _parse_list(value, default):
    raw = value or default
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


# Alpaca credentials (shared with trading_bot or separate)
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_PAPER      = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# Execution backend:
# - "alpaca": submit real/paper orders to Alpaca trading API
# - "sim": local paper simulator (no broker dependency for fills)
FOREX_BROKER_MODE = os.getenv("FOREX_BROKER_MODE", "alpaca").strip().lower()
FOREX_SIM_START_BALANCE = float(os.getenv("FOREX_SIM_START_BALANCE", "100000"))
FOREX_SIM_STATE_FILE = os.path.join(BASE_DIR, "paper_state.json")

# Forex pairs to trade  (Alpaca uses format "EUR/USD")
WATCHLIST = _parse_list(
    os.getenv("FOREX_WATCHLIST"),
    "EUR/USD,GBP/USD,AUD/USD,USD/JPY,USD/CAD",
)

# Risk management
RISK_PER_TRADE        = float(os.getenv("RISK_PER_TRADE", "0.01"))      # 1% of balance per trade
STOP_LOSS_PCT         = float(os.getenv("STOP_LOSS_PCT", "0.005"))      # 0.5%
TAKE_PROFIT_PCT       = float(os.getenv("TAKE_PROFIT_PCT", "0.01"))     # 1.0%
MAX_POSITIONS         = int(os.getenv("MAX_POSITIONS", "3"))
TRADE_COOLDOWN_SECS   = int(os.getenv("TRADE_COOLDOWN_SECS", "300"))    # 5 min between trades per pair

# Loop timing
LOOP_INTERVAL_SECS    = int(os.getenv("LOOP_INTERVAL_SECS", "60"))      # evaluate every 60 s

# Model / learning settings
LOOKBACK_BARS         = int(os.getenv("LOOKBACK_BARS", "60"))           # bars fed into LSTM
RETRAIN_EVERY_N_BARS  = int(os.getenv("RETRAIN_EVERY_N_BARS", "500"))   # online retrain frequency
INITIAL_TRAIN_BARS    = int(os.getenv("INITIAL_TRAIN_BARS", "500"))     # minimum bars for first train
MODEL_DIR             = os.path.join(BASE_DIR, "models")

# Feature engineering windows
EMA_SHORT   = int(os.getenv("EMA_SHORT", "9"))
EMA_LONG    = int(os.getenv("EMA_LONG", "21"))
RSI_PERIOD  = int(os.getenv("RSI_PERIOD", "14"))
ATR_PERIOD  = int(os.getenv("ATR_PERIOD", "14"))

# Autonomous execution controls
AUTONOMOUS_EXECUTION_ENABLED = os.getenv("AUTONOMOUS_EXECUTION_ENABLED", "true").lower() == "true"
AUTONOMOUS_MIN_CLOSED_TRADES = 0
AUTONOMOUS_MIN_WIN_RATE = 0.0
AUTONOMOUS_MIN_PROFIT_FACTOR = 0.0
AUTONOMOUS_MIN_REALIZED_PNL_7D = float(os.getenv("AUTONOMOUS_MIN_REALIZED_PNL_7D", "0"))
AUTONOMOUS_MAX_DRAWDOWN_7D_PCT = float(os.getenv("AUTONOMOUS_MAX_DRAWDOWN_7D_PCT", "0.08"))
AUTONOMY_LEARNING_ENABLED = os.getenv("AUTONOMY_LEARNING_ENABLED", "true").lower() == "true"
AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE = float(os.getenv("AUTONOMY_AGGRESSIVE_MIN_CONFIDENCE", "0.75"))
AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES = int(
    os.getenv("AUTONOMY_AGGRESSIVE_MIN_CLOSED_TRADES", str(AUTONOMOUS_MIN_CLOSED_TRADES))
)
AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS = int(os.getenv("AUTONOMY_AGGRESSIVE_COOLDOWN_HOURS", "24"))
AUTONOMY_LOSS_EVENT_MIN_PNL = float(os.getenv("AUTONOMY_LOSS_EVENT_MIN_PNL", "-25"))
AUTONOMY_RECOVERY_EVENT_MIN_PNL = float(os.getenv("AUTONOMY_RECOVERY_EVENT_MIN_PNL", "25"))

# External internet research sentiment controls
EXTERNAL_RESEARCH_ENABLED = os.getenv("EXTERNAL_RESEARCH_ENABLED", "true").lower() == "true"
EXTERNAL_RESEARCH_CACHE_TTL_SECONDS = int(os.getenv("EXTERNAL_RESEARCH_CACHE_TTL_SECONDS", "1800"))
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "serpapi").strip().lower()  # brave | serpapi
SEARCH_API_KEY = (os.getenv("SEARCH_API_KEY") or os.getenv("SERPAPI_API_KEY") or "").strip()
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "google").strip().lower()  # for serpapi

# Automatic strategy improvement controls
AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED = os.getenv("AUTO_IMPLEMENT_IMPROVEMENTS_ENABLED", "true").lower() == "true"
AUTO_IMPROVEMENT_REBALANCE_HOURS = int(os.getenv("AUTO_IMPROVEMENT_REBALANCE_HOURS", "24"))
AUTO_IMPROVEMENT_LOOKBACK_DAYS = int(os.getenv("AUTO_IMPROVEMENT_LOOKBACK_DAYS", "14"))
AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL = int(os.getenv("AUTO_IMPROVEMENT_MIN_TRADES_PER_SYMBOL", "3"))
