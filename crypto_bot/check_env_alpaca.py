import os
import re

def check_env(file_path):
    print(f"--- Checking {file_path} ---")
    if not os.path.exists(file_path):
        print("File not found.")
        return
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    keys_found = {}
    for i, line in enumerate(lines):
        match = re.match(r'^\s*(ALPACA_API_KEY|ALPACA_API_SECRET)\s*=\s*(.*)', line)
        if match:
            name = match.group(1)
            val = match.group(2).strip().strip("'").strip('"')
            if name not in keys_found:
                keys_found[name] = []
            keys_found[name].append((i + 1, val))
            
    for name in ['ALPACA_API_KEY', 'ALPACA_API_SECRET']:
        instances = keys_found.get(name, [])
        if not instances:
            print(f"{name}: Not found")
        else:
            if len(instances) > 1:
                print(f"{name}: FOUND {len(instances)} TIMES (Duplicate check: FAIL)")
            else:
                print(f"{name}: Found (Duplicate check: PASS)")
            
            for line_no, val in instances:
                length = len(val)
                masked = val[:4] + "*" * (length - 8) + val[-4:] if length >= 8 else "****"
                print(f"  Line {line_no}: Length {length}, Format: {masked}")

def check_others(file_path):
    if not os.path.exists(file_path):
        return
    with open(file_path, 'r') as f:
        content = f.read()
        has_key = "ALPACA_API_KEY" in content
        has_secret = "ALPACA_API_SECRET" in content
        print(f"{file_path} contains Alpaca keys: {'Yes' if (has_key or has_secret) else 'No'}")

print("1 & 2) Checking trading_bot and crypto_bot .env")
check_env('/workspaces/Capitol_Trades_API/trading_bot/.env')
check_env('/workspaces/Capitol_Trades_API/crypto_bot/.env')

print("\n4) Checking other bots")
check_others('/workspaces/Capitol_Trades_API/forex_bot/.env')
check_others('/workspaces/Capitol_Trades_API/asx_bot/.env')
