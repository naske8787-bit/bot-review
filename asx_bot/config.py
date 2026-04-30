"""ASX Bot configuration — all tuneable parameters in one place."""
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


def _list(env_key: str, default: str) -> list[str]:
    raw = os.getenv(env_key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _int_list(env_key: str, default: str) -> list[int]:
    raw = os.getenv(env_key, default)
    out: list[int] = []
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
            if value > 0:
                out.append(value)
        except ValueError:
            continue
    return out


def _int_env(env_key: str, default: int) -> int:
    raw = str(os.getenv(env_key, str(default))).split("#", 1)[0].strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


# ── Watchlist ────────────────────────────────────────────────────────────────
# Liquid ASX stocks suitable for day trading (yfinance .AX suffix)
WATCHLIST: list[str] = _list(
    "ASX_WATCHLIST",
    "BHP.AX,CBA.AX,NAB.AX,WBC.AX,ANZ.AX,RIO.AX,WES.AX,WOW.AX,MQG.AX,CSL.AX",
)

# ── Broker mode ───────────────────────────────────────────────────────────────
# Supported:
#   - "paper": local simulator (default)
#   - "ibkr" : Interactive Brokers (TWS / IB Gateway)
BROKER_MODE = os.getenv("BROKER_MODE", "paper").strip().lower()
ALLOW_BROKER_FALLBACK = os.getenv("ALLOW_BROKER_FALLBACK", "true").strip().lower() == "true"

# IBKR connection settings (used when BROKER_MODE=ibkr)
IBKR_HOST       = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT       = _int_env("IBKR_PORT", 7497)    # paper: 7497, live: 7496
IBKR_CLIENT_ID  = _int_env("IBKR_CLIENT_ID", 7)
IBKR_ACCOUNT    = os.getenv("IBKR_ACCOUNT", "").strip()   # optional; auto-detect if blank
IBKR_EXCHANGE   = os.getenv("IBKR_EXCHANGE", "ASX")
IBKR_CURRENCY   = os.getenv("IBKR_CURRENCY", "AUD")

# ── Paper broker ─────────────────────────────────────────────────────────────
PAPER_CAPITAL        = float(os.getenv("PAPER_CAPITAL",      "100000"))   # AUD starting capital
PAPER_STATE_FILE     = os.path.join(BASE_DIR, "paper_state.json")
PAPER_TRADES_LOG     = os.path.join(BASE_DIR, "logs", "trades_log.csv")
SLIPPAGE_PCT         = float(os.getenv("SLIPPAGE_PCT",        "0.0005"))  # 0.05% market impact
BROKERAGE_FLAT       = float(os.getenv("BROKERAGE_FLAT",      "9.95"))    # $9.95 per order (CommSec style)

# ── Risk management ──────────────────────────────────────────────────────────
RISK_PER_TRADE       = float(os.getenv("RISK_PER_TRADE",     "0.02"))    # 2% of equity per trade
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT",      "0.015"))   # 1.5% stop
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT",    "0.03"))    # 3.0% target
MAX_POSITIONS        = int(os.getenv("MAX_POSITIONS",         "5"))
TRADE_COOLDOWN_SECS  = int(os.getenv("TRADE_COOLDOWN_SECS",  "300"))     # 5 min between trades per symbol

# ── ASX market hours (exchange-local time; DST handled by timezone) ─────────
ASX_TIMEZONE         = os.getenv("ASX_TIMEZONE",             "Australia/Sydney").strip()
ASX_OPEN_HOUR        = int(os.getenv("ASX_OPEN_HOUR",        "10"))
ASX_OPEN_MIN         = int(os.getenv("ASX_OPEN_MIN",         "0"))
ASX_CLOSE_HOUR       = int(os.getenv("ASX_CLOSE_HOUR",       "16"))
ASX_CLOSE_MIN        = int(os.getenv("ASX_CLOSE_MIN",        "0"))
EOD_CLOSE_HOUR       = int(os.getenv("EOD_CLOSE_HOUR",       "15"))
EOD_CLOSE_MIN        = int(os.getenv("EOD_CLOSE_MIN",        "50"))

# ── Loop timing ──────────────────────────────────────────────────────────────
LOOP_INTERVAL_SECS   = int(os.getenv("LOOP_INTERVAL_SECS",   "300"))     # evaluate every 5 min

# ── ML / model settings ──────────────────────────────────────────────────────
LOOKBACK_BARS        = int(os.getenv("LOOKBACK_BARS",         "60"))      # bars fed to LSTM
INITIAL_TRAIN_BARS   = int(os.getenv("INITIAL_TRAIN_BARS",    "500"))     # bars for first training
RETRAIN_EVERY_N_BARS = int(os.getenv("RETRAIN_EVERY_N_BARS",  "200"))     # full retrain frequency
MODEL_DIR            = os.path.join(BASE_DIR, "models")

# ── Technical indicator periods ──────────────────────────────────────────────
EMA_SHORT   = int(os.getenv("EMA_SHORT",  "9"))
EMA_LONG    = int(os.getenv("EMA_LONG",   "21"))
RSI_PERIOD  = int(os.getenv("RSI_PERIOD", "14"))
ATR_PERIOD  = int(os.getenv("ATR_PERIOD", "14"))
BB_PERIOD   = int(os.getenv("BB_PERIOD",  "20"))    # Bollinger Bands period
BB_STD      = float(os.getenv("BB_STD",   "2.0"))   # Bollinger Bands std multiplier
VWAP_RESET_DAILY = True                              # VWAP resets each trading day

# ── Event/news learning settings ────────────────────────────────────────────
EVENT_LEARNER_ALPHA = float(os.getenv("EVENT_LEARNER_ALPHA", "0.15"))
EVENT_MAX_EDGE_ADJUSTMENT_PCT = float(os.getenv("EVENT_MAX_EDGE_ADJUSTMENT_PCT", "0.8"))
EVENT_LEARNER_LAGS = _int_list("EVENT_LEARNER_LAGS", "1,3,6")
EVENT_BOOTSTRAP_ENABLED = os.getenv("EVENT_BOOTSTRAP_ENABLED", "true").lower() == "true"
EVENT_BOOTSTRAP_YEARS = int(os.getenv("EVENT_BOOTSTRAP_YEARS", "50"))
EVENT_BOOTSTRAP_INTERVAL = os.getenv("EVENT_BOOTSTRAP_INTERVAL", "1mo")
EVENT_BOOTSTRAP_MIN_OBSERVATIONS = int(os.getenv("EVENT_BOOTSTRAP_MIN_OBSERVATIONS", "120"))

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
