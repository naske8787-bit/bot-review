#!/usr/bin/env bash
set -euo pipefail

# Validate Alpaca credentials from a bot .env and perform a live /v2/account auth check.
# Usage: scripts/preflight_alpaca.sh <bot_env_path>

ENV_PATH="${1:-}"
if [[ -z "$ENV_PATH" ]]; then
  echo "Usage: $0 <bot_env_path>"
  exit 2
fi
if [[ ! -f "$ENV_PATH" ]]; then
  echo "Preflight failed: env file not found: $ENV_PATH"
  exit 2
fi

clean_value() {
  local value="$1"
  value="${value%%#*}"
  echo "$(echo "$value" | xargs)"
}

read_env() {
  local key="$1"
  local raw
  raw="$(grep -E "^${key}=" "$ENV_PATH" | head -n1 | cut -d= -f2- || true)"
  clean_value "$raw"
}

BASE_URL="$(read_env ALPACA_BASE_URL)"
if [[ -z "$BASE_URL" ]]; then
  BASE_URL="https://paper-api.alpaca.markets"
fi

API_KEY="$(read_env ALPACA_API_KEY)"
API_SECRET="$(read_env ALPACA_API_SECRET)"
if [[ -z "$API_KEY" ]]; then
  API_KEY="$(read_env APCA_API_KEY_ID)"
fi
if [[ -z "$API_SECRET" ]]; then
  API_SECRET="$(read_env APCA_API_SECRET_KEY)"
fi

if [[ -z "$API_KEY" || -z "$API_SECRET" ]]; then
  echo "Preflight failed: missing Alpaca key/secret in $ENV_PATH"
  exit 1
fi

http_code="$(curl -sS -o /tmp/alpaca_preflight_account.json -w "%{http_code}" \
  -H "APCA-API-KEY-ID: $API_KEY" \
  -H "APCA-API-SECRET-KEY: $API_SECRET" \
  "$BASE_URL/v2/account" || echo 000)"

if [[ "$http_code" != "200" ]]; then
  echo "Preflight failed: Alpaca auth returned HTTP $http_code (base=$BASE_URL, env=$ENV_PATH)"
  cat /tmp/alpaca_preflight_account.json 2>/dev/null || true
  exit 1
fi

echo "Preflight OK: Alpaca auth succeeded (base=$BASE_URL, env=$ENV_PATH)"
