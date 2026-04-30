import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass

load_dotenv('/workspaces/Capitol_Trades_API/forex_bot/.env', override=True)
key=os.getenv('ALPACA_API_KEY')
sec=os.getenv('ALPACA_API_SECRET')
paper=str(os.getenv('ALPACA_PAPER','true')).lower()=='true'

client = TradingClient(key, sec, paper=paper)

try:
    print("Listing AssetClass members...")
    for name in dir(AssetClass):
        if not name.startswith('_'):
            print(f"AssetClass: {name}")

    print("\nFetching US_EQUITY assets (limited)...")
    try:
        # Try a limited fetch first
        assets = client.get_all_assets(GetAssetsRequest(asset_class=AssetClass.US_EQUITY))
        print(f"Total US_EQUITY assets: {len(assets)}")
        print(f"Sample symbols: {[a.symbol for a in assets[:5]]}")
    except Exception as e:
        print('err US_EQUITY', type(e).__name__, str(e))

    # Check for FOREX if it exists
    if hasattr(AssetClass, 'FOREX'):
        print("\nFetching FOREX assets...")
        try:
            assets = client.get_all_assets(GetAssetsRequest(asset_class=AssetClass.FOREX))
            print(f"Total FOREX assets: {len(assets)}")
            print(f"Sample symbols: {[a.symbol for a in assets[:10]]}")
        except Exception as e:
            print('err FOREX', type(e).__name__, str(e))
    else:
        print("\nAssetClass has no FOREX member.")

except Exception as e:
    print('err main', type(e).__name__, str(e))
