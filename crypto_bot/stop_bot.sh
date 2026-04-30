#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE="bot.pid"
SESSION_NAME="crypto_bot"
stopped_any=0

if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  tmux kill-session -t "$SESSION_NAME"
  echo "Stopped tmux session '$SESSION_NAME'."
  stopped_any=1
fi

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Stopped crypto bot with PID $PID."
    stopped_any=1
  else
    echo "Process $PID is not running. Removing stale PID file."
  fi
  rm -f "$PID_FILE"
fi

if [ "$stopped_any" -eq 0 ]; then
  echo "No running crypto bot process found."
fi
