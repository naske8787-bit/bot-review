#!/usr/bin/env bash
SESSION="asx_dashboard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "ASX dashboard is RUNNING"
    tail -20 "$SCRIPT_DIR/dashboard_output.log" 2>/dev/null || echo "(no dashboard log yet)"
else
    echo "ASX dashboard is NOT running"
fi
