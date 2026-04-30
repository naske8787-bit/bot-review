import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv('/workspaces/Capitol_Trades_API/forex_bot/.env', override=True)
key=os.getenv('ALPACA_API_KEY')
sec=os.getenv('ALPACA_API_SECRET')
paper=str(os.getenv('ALPACA_PAPER','true')).lower()=='true'

client = TradingClient(key, sec, paper=paper)

symbols = ['EURUSD','EUR/USD','GBPUSD','GBP/USD','USD/JPY','USDJPY']
for s in symbols:
    try:
        req=MarketOrderRequest(symbol=s, qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.IOC)
        o=client.submit_order(req)
        print(f'ok {s} id {getattr(o,"id",None)} status {getattr(o,"status",None)}')
    except Exception as e:
        print(f'err {s}: {type(e).__name__} {str(e)[:200]}')
