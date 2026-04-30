"""IBKR connection test script.

Run this before starting the bot to verify TWS/IB Gateway is running
and your connection settings are correct.

Usage:
    python ibkr_check.py
"""

import os
import sys

from dotenv import load_dotenv
from ib_insync import IB, Stock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _int_env(env_key, default):
    raw = str(os.getenv(env_key, str(default))).split("#", 1)[0].strip()
    try:
        return int(raw)
    except Exception:
        return int(default)

HOST = os.getenv("IBKR_HOST", "127.0.0.1")
PORT = _int_env("IBKR_PORT", 7497)
CLIENT_ID = _int_env("IBKR_CLIENT_ID", 1)
CURRENCY = os.getenv("IBKR_CURRENCY", "INR")
EXCHANGE = os.getenv("DEFAULT_EXCHANGE", "NSE")

print(f"Connecting to IBKR at {HOST}:{PORT} (clientId={CLIENT_ID})...")
ib = IB()

try:
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
except Exception as e:
    print(f"\nCONNECTION FAILED: {e}")
    print("\nMake sure:")
    print("  1. TWS or IB Gateway is running and logged in")
    print("  2. API is enabled: Edit > Global Configuration > API > Settings")
    print(f"  3. Port is set to {PORT} in TWS (7497=paper, 7496=live)")
    print("  4. 127.0.0.1 is in trusted IP addresses")
    sys.exit(1)

accounts = ib.managedAccounts()
print(f"\nConnected successfully!")
print(f"  Accounts : {accounts}")

# Show account balance
for v in ib.accountValues(accounts[0] if accounts else ""):
    if v.tag == "NetLiquidation":
        print(f"  Net Liquidation ({v.currency}): {float(v.value):,.2f}")
    if v.tag == "AvailableFunds":
        print(f"  Available Funds ({v.currency}): {float(v.value):,.2f}")

# Test fetching a price
test_symbol = "RELIANCE"
print(f"\nFetching test price for {test_symbol} on {EXCHANGE}...")
try:
    contract = Stock(test_symbol, EXCHANGE, CURRENCY)
    ib.qualifyContracts(contract)
    ticker = ib.reqMktData(contract, snapshot=True)
    ib.sleep(2)
    price = ticker.last or ticker.close or ticker.bid
    ib.cancelMktData(contract)
    if price:
        print(f"  {test_symbol} last price: ₹{float(price):.2f}")
    else:
        print(f"  Price returned empty — market may be closed or data subscription needed")
except Exception as e:
    print(f"  Price fetch failed: {e}")

ib.disconnect()
print("\nAll checks complete. You can now start the bot: python main.py")
