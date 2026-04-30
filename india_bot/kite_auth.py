"""Zerodha Kite Connect daily authentication helper.

Kite access tokens expire every day. Run this script once each morning
before starting the bot to get a fresh access token.

Usage:
    python kite_auth.py

The script will:
  1. Print the Zerodha login URL.
  2. Ask you to paste the request_token from the redirect URL.
  3. Exchange it for an access token and write it to .env.
"""

import os
import re
import sys

from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

if not API_KEY or not API_SECRET:
    print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in india_bot/.env")
    sys.exit(1)

kite = KiteConnect(api_key=API_KEY)
login_url = kite.login_url()

print("=" * 60)
print("Open this URL in your browser to log in to Zerodha:")
print(login_url)
print("=" * 60)
print("\nAfter logging in, you will be redirected to your callback URL.")
print("Copy the full redirect URL or just the 'request_token' parameter.\n")

raw = input("Paste the redirect URL or request_token here: ").strip()

# Extract request_token whether user pasted the full URL or just the token
match = re.search(r"request_token=([A-Za-z0-9]+)", raw)
request_token = match.group(1) if match else raw

try:
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
except Exception as e:
    print(f"Failed to generate session: {e}")
    sys.exit(1)

# Persist the new token in .env
set_key(ENV_FILE, "KITE_ACCESS_TOKEN", access_token)
print(f"\nSuccess! Access token saved to {ENV_FILE}")
print("You can now start the bot: python main.py")
