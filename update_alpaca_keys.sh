#!/usr/bin/env bash

# Update Alpaca API keys across all bot directories
# Usage: bash update_alpaca_keys.sh <API_KEY> <API_SECRET>

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if credentials are provided
if [[ $# -lt 2 ]]; then
    echo -e "${YELLOW}Usage: bash update_alpaca_keys.sh <API_KEY> <API_SECRET>${NC}"
    echo ""
    echo "Example:"
    echo "  bash update_alpaca_keys.sh 'YOUR_API_KEY' 'YOUR_API_SECRET'"
    echo ""
    echo "To get your keys:"
    echo "  1. Go to app.alpaca.markets"
    echo "  2. Settings → API Keys"
    echo "  3. Copy your API Key ID and Secret Key"
    exit 1
fi

API_KEY="$1"
API_SECRET="$2"

# Array of bot directories
BOTS=(
    "trading_bot"
    "crypto_bot"
    "asx_bot"
    "forex_bot"
    "india_bot"
)

BASE_DIR="/workspaces/Capitol_Trades_API"

echo -e "${YELLOW}Updating Alpaca API keys...${NC}"
echo "API Key: ${API_KEY:0:10}...${API_KEY: -5}"
echo ""

UPDATED=0
FAILED=0

for bot in "${BOTS[@]}"; do
    ENV_FILE="$BASE_DIR/$bot/.env"
    
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${YELLOW}⊘ Skipped${NC} $bot/.env (file not found)"
        continue
    fi
    
    # Create backup
    cp "$ENV_FILE" "$ENV_FILE.backup"
    
    # Update API key and secret using sed
    sed -i.tmp "s/^ALPACA_API_KEY=.*/ALPACA_API_KEY=$API_KEY/" "$ENV_FILE"
    sed -i.tmp "s/^ALPACA_API_SECRET=.*/ALPACA_API_SECRET=$API_SECRET/" "$ENV_FILE"
    rm -f "$ENV_FILE.tmp"
    
    echo -e "${GREEN}✓ Updated${NC} $bot/.env"
    ((UPDATED++))
done

echo ""
echo -e "${GREEN}=== Update Complete ===${NC}"
echo "Updated: $UPDATED bot(s)"
echo ""
echo "Backups created as .env.backup in each directory"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Restart the trading bots:"
echo "   bash stop_all_bots.sh && bash start_all_bots.sh"
echo ""
echo "2. Or restart individual bots:"
echo "   cd trading_bot && bash stop_bot.sh && bash run_tmux.sh"
echo "   cd crypto_bot && bash stop_bot.sh && bash run_tmux.sh"
