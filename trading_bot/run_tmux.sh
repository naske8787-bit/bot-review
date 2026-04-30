#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

SESSION_NAME="trading_bot"
LOG_FILE="bot.log"
SUPERVISOR_LOG="supervisor.log"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed. Install tmux and try again."
  exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Session '$SESSION_NAME' already exists. Attach with: tmux attach -t $SESSION_NAME"
  exit 1
fi

TMUX_CMD="cd \"$(pwd)\" && bash ./supervise_bot.sh"

tmux new-session -d -s "$SESSION_NAME" "$TMUX_CMD"
echo "Started bot in tmux session '$SESSION_NAME' with auto-restart enabled."
echo "Attach with: tmux attach -t $SESSION_NAME"
echo "Bot logs: $LOG_FILE"
echo "Supervisor logs: $SUPERVISOR_LOG"