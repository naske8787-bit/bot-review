#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

has_systemd_unit() {
  local unit="$1"
  local state
  state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null || echo "not-found")"
  [[ "$state" != "not-found" ]]
}

check_log_freshness() {
  local label="$1"
  local file_path="$2"
  local max_age_seconds="$3"

  if [[ ! -f "$file_path" ]]; then
    echo "$label log: missing ($file_path)"
    fail=1
    return
  fi

  local now ts age
  now="$(date +%s)"
  ts="$(stat -c %Y "$file_path" 2>/dev/null || echo 0)"
  age=$((now - ts))

  if (( age > max_age_seconds )); then
    echo "$label log: stale ${age}s ($file_path)"
    fail=1
  else
    echo "$label log: fresh ${age}s"
  fi
}

echo "=== HEALTH $(date -u +"%Y-%m-%dT%H:%M:%SZ") ==="

declare -A BOT_UNITS=(
  [trading_bot]="capitol-trading-bot.service"
  [crypto_bot]="capitol-crypto-bot.service"
  [tech_research_bot]="capitol-tech-research-bot.service"
)

for s in trading_bot crypto_bot tech_research_bot; do
  unit="${BOT_UNITS[$s]}"
  if tmux has-session -t "$s" 2>/dev/null; then
    echo "UP   tmux:$s"
  elif command -v systemctl >/dev/null 2>&1 && has_systemd_unit "$unit" && [[ "$(systemctl is-active "$unit" || true)" == "active" ]]; then
    echo "UP   systemd:$unit"
  else
    echo "DOWN $s"
    fail=1
  fi
done

if command -v systemctl >/dev/null 2>&1; then
  if has_systemd_unit "capitol-api.service"; then
    api_state="$(systemctl is-active capitol-api || true)"
    echo "api: $api_state (systemd)"
    [[ "$api_state" == "active" ]] || fail=1
  elif tmux has-session -t api_server 2>/dev/null; then
    echo "api: active (tmux)"
  else
    echo "api: inactive"
    fail=1
  fi

  if has_systemd_unit "capitol-dashboard.service"; then
    dash_state="$(systemctl is-active capitol-dashboard || true)"
    echo "dashboard: $dash_state (systemd)"
    [[ "$dash_state" == "active" ]] || fail=1
  elif tmux has-session -t mining_dashboard 2>/dev/null; then
    echo "dashboard: active (tmux)"
  else
    echo "dashboard: inactive"
    fail=1
  fi
fi

# Detect silent loops where a supervisor runs but the bot does not produce output.
check_log_freshness "crypto" "$ROOT_DIR/crypto_bot/bot.log" 10800
check_log_freshness "research" "$ROOT_DIR/tech_research_bot/bot.log" 10800

# Catch repeated Alpaca auth failures for crypto preflight.
if [[ -f "$ROOT_DIR/crypto_bot/supervisor.log" ]] && tail -n 40 "$ROOT_DIR/crypto_bot/supervisor.log" | grep -q "Preflight failed: Alpaca auth returned HTTP 401"; then
  echo "crypto preflight: AUTH 401"
  fail=1
fi

api_code="$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health || echo 000)"
dash_code="$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5051/ || echo 000)"

echo "API /health: $api_code"
echo "DASH /:      $dash_code"
[[ "$api_code" == "200" ]] || fail=1
[[ "$dash_code" == "200" || "$dash_code" == "301" || "$dash_code" == "302" ]] || fail=1

if [[ $fail -eq 0 ]]; then
  echo "HEALTH: PASS"
else
  echo "HEALTH: FAIL"
fi
exit $fail
