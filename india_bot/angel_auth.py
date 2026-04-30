"""Angel One SmartAPI authentication helper.

Angel One sessions don't expire like Kite tokens — the SmartAPI auto-handles
auth on each bot start using your credentials + TOTP.

Run this script to verify your credentials are correct before starting the bot.

Usage:
    python angel_auth.py
"""

import os
import sys

import pyotp
from SmartApi import SmartConnect
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

API_KEY = os.getenv("ANGEL_API_KEY")
CLIENT_ID = os.getenv("ANGEL_CLIENT_ID")
PASSWORD = os.getenv("ANGEL_PASSWORD")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

if not all([API_KEY, CLIENT_ID, PASSWORD]):
    print("ERROR: ANGEL_API_KEY, ANGEL_CLIENT_ID, and ANGEL_PASSWORD must be set in india_bot/.env")
    sys.exit(1)

print(f"Connecting to Angel One as {CLIENT_ID}...")

totp = pyotp.TOTP(TOTP_SECRET).now() if TOTP_SECRET else ""
angel = SmartConnect(api_key=API_KEY)
session = angel.generateSession(CLIENT_ID, PASSWORD, totp)

if not session.get("status"):
    print(f"Login FAILED: {session.get('message')}")
    sys.exit(1)

profile = angel.getProfile(session["data"]["refreshToken"])
print("Login SUCCESSFUL!")
print(f"  Name   : {profile['data'].get('name', 'N/A')}")
print(f"  Email  : {profile['data'].get('email', 'N/A')}")
print(f"  Broker : Angel One")
print("\nYou can now start the bot: python main.py")
