#!/usr/bin/env bash

# Update Alpaca API keys for crypto_bot and trading_bot
# Usage: bash update_keys_crypto_trading.sh <API_KEY> <API_SECRET> [BASE_URL]

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: bash update_keys_crypto_trading.sh <API_KEY> <API_SECRET> [BASE_URL]"
    echo
    echo "Example:"
    echo "  bash update_keys_crypto_trading.sh 'YOUR_API_KEY' 'YOUR_SECRET' 'https://paper-api.alpaca.markets'"
    exit 1
fi

API_KEY="$1"
API_SECRET="$2"
BASE_URL="${3:-https://paper-api.alpaca.markets}"

BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"

update_or_append() {
    local file="$1"
    local key="$2"
    local value="$3"
    if grep -q "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$file"
    fi
}

mask() {
    local s="$1"
    local n=${#s}
    if (( n <= 8 )); then
        echo "********"
    else
        echo "${s:0:4}...${s:n-4:4}"
    fi
}

echo "Updating Alpaca credentials for crypto_bot and trading_bot..."
echo "API key: $(mask "$API_KEY")"
echo "Base URL: $BASE_URL"

for bot in crypto_bot trading_bot; do
    env_file="$BASE_DIR/$bot/.env"
    if [[ ! -f "$env_file" ]]; then
        echo "Skipping $bot (.env not found)"
        continue
    fi

    update_or_append "$env_file" "ALPACA_API_KEY" "$API_KEY"
    update_or_append "$env_file" "ALPACA_API_SECRET" "$API_SECRET"
    update_or_append "$env_file" "ALPACA_BASE_URL" "$BASE_URL"
    echo "Updated $bot/.env"
done

echo "Done. Restart bot sessions to apply new credentials."
