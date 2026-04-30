#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

LOG_FILE="api_server.log"
RESTART_DELAY="${RESTART_DELAY:-5}"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  _PYTHON_BIN="$PYTHON_BIN"
elif [[ -x "$(dirname "$0")/.venv/bin/python" ]]; then
  _PYTHON_BIN="$(dirname "$0")/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  _PYTHON_BIN="$(command -v python3)"
else
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ERROR: No Python interpreter found (set PYTHON_BIN or create .venv)." >> "$LOG_FILE"
  exit 1
fi

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

echo "[$(timestamp)] API server supervisor started." >> "$LOG_FILE"

while true; do
  echo "[$(timestamp)] Launching API server..." >> "$LOG_FILE"
  "$_PYTHON_BIN" -u run.py >> "$LOG_FILE" 2>&1
  exit_code=$?
  echo "[$(timestamp)] API server exited with code $exit_code. Restarting in ${RESTART_DELAY}s..." >> "$LOG_FILE"
  sleep "$RESTART_DELAY"
done
