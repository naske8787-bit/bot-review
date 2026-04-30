#!/usr/bin/env bash
set -euo pipefail
SESSION_NAME="tech_research_bot"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed."
  exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  tmux kill-session -t "$SESSION_NAME"
  echo "Stopped session '$SESSION_NAME'."
else
  echo "Session '$SESSION_NAME' is not running."
fi
