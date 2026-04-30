#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE="bot.pid"
LOG_FILE="bot.log"
SUPERVISOR_LOG="supervisor.log"
SESSION_NAME="crypto_bot"

if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Crypto bot is running in tmux session '$SESSION_NAME'."
  echo "Last 20 bot log lines:"
  [ -f "$LOG_FILE" ] && tail -20 "$LOG_FILE"
  echo
  echo "Last 10 supervisor log lines:"
  [ -f "$SUPERVISOR_LOG" ] && tail -10 "$SUPERVISOR_LOG"
  exit 0
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Crypto bot is running with PID $(cat "$PID_FILE")."
  echo "Last 20 log lines:"
  [ -f "$LOG_FILE" ] && tail -20 "$LOG_FILE"
  exit 0
fi

echo "Crypto bot is not running."
if [ -f "$LOG_FILE" ]; then
  echo "Last 20 log lines:"
  tail -20 "$LOG_FILE"
fi
