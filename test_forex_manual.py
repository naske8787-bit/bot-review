import requests
import os
from dotenv import load_dotenv

load_dotenv('/workspaces/Capitol_Trades_API/forex_bot/.env', override=True)
key = os.getenv('ALPACA_API_KEY')
sec = os.getenv('ALPACA_API_SECRET')
base_url = 'https://paper-api.alpaca.markets/v2'

headers = {
    'APCA-API-KEY-ID': key,
    'APCA-API-SECRET-KEY': sec
}

# Try to find Forex assets via raw REST
print("Requesting FOREX assets...")
resp = requests.get(f"{base_url}/assets?asset_class=forex", headers=headers)
if resp.status_code == 200:
    assets = resp.json()
    print(f"Found {len(assets)} forex assets.")
    for a in assets[:5]:
        print(f"Symbol: {a['symbol']}, Name: {a['name']}, Class: {a['class']}")
else:
    print(f"Error fetching forex assets: {resp.status_code} {resp.text}")

# Try searching for a specific common forex pair if asset_class filter failed
print("\nRequesting EUR/USD asset directly...")
resp = requests.get(f"{base_url}/assets/EURUSD", headers=headers)
if resp.status_code == 200:
    print(f"EURUSD found: {resp.json()}")
else:
    print(f"EURUSD not found: {resp.status_code}")
