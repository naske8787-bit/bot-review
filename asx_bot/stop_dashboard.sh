#!/usr/bin/env bash
SESSION="asx_dashboard"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    echo "ASX dashboard stopped."
else
    echo "No dashboard session '$SESSION' found."
fi
