import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv('/workspaces/Capitol_Trades_API/forex_bot/.env', override=True)
key=os.getenv('ALPACA_API_KEY')
sec=os.getenv('ALPACA_API_SECRET')
paper=str(os.getenv('ALPACA_PAPER','true')).lower()=='true'

print(f"Key: {key[:5]}...")
print(f"Paper: {paper}")

client = TradingClient(key, sec, paper=paper)
try:
    acc = client.get_account()
    print('account_status', acc.status)
except Exception as e:
    print('err', type(e).__name__, str(e))
