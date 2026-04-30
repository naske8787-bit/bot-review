#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

LOG_FILE="bot.log"
PID_FILE="bot.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Bot is already running with PID $(cat "$PID_FILE")."
  exit 1
fi

nohup python -u main.py > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "Bot started in background with PID $PID. Logs: $LOG_FILE"