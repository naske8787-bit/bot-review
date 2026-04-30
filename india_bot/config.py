import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _parse_symbol_list(value, default):
    raw = value or default
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _parse_int_list(value, default):
    raw = value or default
    out = []
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        try:
            val = int(token)
            if val > 0:
                out.append(val)
        except ValueError:
            continue
    return out


def _int_env(env_key, default):
    raw = str(os.getenv(env_key, str(default))).split("#", 1)[0].strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


# Interactive Brokers (IBKR) connection settings
# IBKR uses TWS (Trader Workstation) or IB Gateway running locally.
# No API key needed — the bot connects via socket to your running TWS/Gateway.
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = _int_env("IBKR_PORT", 7497)   # 7497 = TWS paper, 7496 = TWS live, 4002 = Gateway live
IBKR_CLIENT_ID = _int_env("IBKR_CLIENT_ID", 1)
IBKR_ACCOUNT = os.getenv("IBKR_ACCOUNT", "")      # Leave blank to use default account

# Paper trading mode — uses IBKR paper account (port 7497) and no real orders
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

# Exchange: "NSE" for Indian stocks via IBKR
DEFAULT_EXCHANGE = os.getenv("DEFAULT_EXCHANGE", "NSE")

# yfinance suffix map (used for historical data fallback)
EXCHANGE_YF_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}
YF_SUFFIX = EXCHANGE_YF_SUFFIX.get(DEFAULT_EXCHANGE.upper(), ".NS")

# IBKR currency for Indian stocks
IBKR_CURRENCY = os.getenv("IBKR_CURRENCY", "INR")

# Watchlist — top NSE large-caps by default
WATCHLIST = _parse_symbol_list(
    os.getenv("WATCHLIST"),
    "RELIANCE,TCS,HDFCBANK,INFY,ICICIBANK,KOTAKBANK,HINDUNILVR,AXISBANK,WIPRO,TATAMOTORS",
)

# Market regime symbol (NIFTY 50 index in yfinance)
MARKET_REGIME_SYMBOL = os.getenv("MARKET_REGIME_SYMBOL", "^NSEI")
MARKET_REGIME_SHORT_WINDOW = _int_env("MARKET_REGIME_SHORT_WINDOW", 50)
MARKET_REGIME_LONG_WINDOW = _int_env("MARKET_REGIME_LONG_WINDOW", 200)

# Market hours (IST) — NSE/BSE: 9:15 AM – 3:30 PM
MARKET_OPEN_TIME = os.getenv("MARKET_OPEN_TIME", "09:15")
MARKET_CLOSE_TIME = os.getenv("MARKET_CLOSE_TIME", "15:30")
MARKET_TIMEZONE = os.getenv("MARKET_TIMEZONE", "Asia/Kolkata")

# Trading parameters
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100000"))  # INR
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.05"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.05"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.12"))
TRADE_COOLDOWN_MINUTES = int(os.getenv("TRADE_COOLDOWN_MINUTES", "30"))

# Signal thresholds
BUY_THRESHOLD_PCT = float(os.getenv("BUY_THRESHOLD_PCT", "0.001"))
SELL_THRESHOLD_PCT = float(os.getenv("SELL_THRESHOLD_PCT", "0.001"))

# Cache
STOCK_DATA_CACHE_TTL_SECONDS = int(os.getenv("STOCK_DATA_CACHE_TTL_SECONDS", "900"))

# Loop interval
LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", "3600"))

# Event/news learning settings
EVENT_LEARNER_ALPHA = float(os.getenv("EVENT_LEARNER_ALPHA", "0.15"))
EVENT_MAX_EDGE_ADJUSTMENT_PCT = float(os.getenv("EVENT_MAX_EDGE_ADJUSTMENT_PCT", "0.8"))
EVENT_LEARNER_LAGS = _parse_int_list(os.getenv("EVENT_LEARNER_LAGS"), "1,3,6")
EVENT_BOOTSTRAP_ENABLED = os.getenv("EVENT_BOOTSTRAP_ENABLED", "true").lower() == "true"
EVENT_BOOTSTRAP_YEARS = int(os.getenv("EVENT_BOOTSTRAP_YEARS", "50"))
EVENT_BOOTSTRAP_INTERVAL = os.getenv("EVENT_BOOTSTRAP_INTERVAL", "1mo")
EVENT_BOOTSTRAP_MIN_OBSERVATIONS = int(os.getenv("EVENT_BOOTSTRAP_MIN_OBSERVATIONS", "120"))
