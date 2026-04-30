import os, sys
sys.path.insert(0, '/workspaces/Capitol_Trades_API/crypto_bot')
from dotenv import load_dotenv
load_dotenv('/workspaces/Capitol_Trades_API/trading_bot/.env')

from alpaca.trading.client import TradingClient

key = os.getenv('ALPACA_API_KEY')
secret = os.getenv('ALPACA_API_SECRET')

api = TradingClient(key, secret, paper=True)
account = api.get_account()
print(f"Cash: ${account.cash}")
print(f"Portfolio value: ${account.portfolio_value}")
print(f"Buying power: ${account.buying_power}")

positions = api.get_all_positions()
print(f"\nOpen positions ({len(positions)}):")
for p in positions:
    print(f"  {p.symbol}: qty={p.qty}, market_value=${p.market_value}, unrealized_pl=${p.unrealized_pl}")

orders = api.get_orders()
crypto_orders = [o for o in orders if '/' in str(o.symbol) or any(c in str(o.symbol) for c in ['BTC','ETH','SOL','DOGE','ADA','XRP','AVAX','LINK','MATIC','DOT'])]
print(f"\nRecent crypto orders ({len(crypto_orders)}):")
for o in crypto_orders[:10]:
    print(f"  {o.symbol} {o.side} {o.qty} - status={o.status} submitted={o.submitted_at}")
