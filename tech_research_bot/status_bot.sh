#!/usr/bin/env bash
set -euo pipefail
SESSION_NAME="tech_research_bot"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed."
  exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "tech_research_bot is running in tmux session '$SESSION_NAME'."
else
  echo "tech_research_bot is not running."
fi
