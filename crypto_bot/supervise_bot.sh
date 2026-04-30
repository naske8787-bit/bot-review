#!/usr/bin/env bash
set -u
set -o pipefail
cd "$(dirname "$0")"

LOG_FILE="bot.log"
SUPERVISOR_LOG="supervisor.log"
RESTART_DELAY="${RESTART_DELAY:-10}"
PYTHON_BIN="${PYTHON_BIN:-python}"
PREFLIGHT_SCRIPT="$(cd .. && pwd)/scripts/preflight_alpaca.sh"
ENV_FILE="$(pwd)/.env"
LOCK_FILE="${LOCK_FILE:-/tmp/capitol-crypto-bot.supervisor.lock}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

timestamp_log_stream() {
  while IFS= read -r line; do
    printf '[%s] %s\n' "$(timestamp)" "$line"
  done
}

acquire_lock() {
  exec 9>"$LOCK_FILE"
  if flock -n 9; then
    return 0
  fi

  echo "[$(timestamp)] Another supervisor instance detected. Waiting for lock: $LOCK_FILE" >> "$SUPERVISOR_LOG"
  flock 9
  echo "[$(timestamp)] Lock acquired after wait: $LOCK_FILE" >> "$SUPERVISOR_LOG"
}

echo "[$(timestamp)] Supervisor started. Auto-restart is enabled." >> "$SUPERVISOR_LOG"
acquire_lock

while true; do
  if [[ -x "$PREFLIGHT_SCRIPT" ]]; then
    if ! "$PREFLIGHT_SCRIPT" "$ENV_FILE" >> "$SUPERVISOR_LOG" 2>&1; then
      echo "[$(timestamp)] Alpaca preflight failed. Retrying in ${RESTART_DELAY}s..." >> "$SUPERVISOR_LOG"
      sleep "$RESTART_DELAY"
      continue
    fi
  fi
  echo "[$(timestamp)] Launching crypto bot..." >> "$SUPERVISOR_LOG"
  if "$PYTHON_BIN" -u main.py 2>&1 | timestamp_log_stream >> "$LOG_FILE"; then
    exit_code=0
  else
    exit_code=$?
  fi
  echo "[$(timestamp)] Crypto bot exited with code $exit_code. Restarting in ${RESTART_DELAY}s..." >> "$SUPERVISOR_LOG"
  sleep "$RESTART_DELAY"
done
