#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

LOG_FILE="bot.log"
PID_FILE="bot.pid"
PYTHON_BIN="${PYTHON_BIN:-python}"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Crypto bot is already running with PID $(cat "$PID_FILE")."
  exit 1
fi

nohup "$PYTHON_BIN" -u main.py > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "Crypto bot started in background with PID $PID. Logs: $LOG_FILE"
