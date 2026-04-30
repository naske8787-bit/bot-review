#!/usr/bin/env bash
SESSION="asx_bot"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    echo "ASX bot stopped."
else
    echo "No session '$SESSION' found."
fi
