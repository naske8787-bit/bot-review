#!/usr/bin/env bash
SESSION="asx_bot"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "=== ASX bot is RUNNING ==="
    tail -20 "$(dirname "$0")/output.log" 2>/dev/null || echo "(no log yet)"
else
    echo "ASX bot is NOT running."
fi
