#!/usr/bin/env bash
set -euo pipefail

SESSION="asx_dashboard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(which python3)}"
PORT="${ASX_DASHBOARD_PORT:-5052}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Dashboard session '$SESSION' already running."
    exit 0
fi

cd "$SCRIPT_DIR"
tmux new-session -d -s "$SESSION" \
  "cd '$SCRIPT_DIR' && ASX_DASHBOARD_PORT='$PORT' $PYTHON_BIN -u dashboard_app.py 2>&1 | tee dashboard_output.log"

echo "ASX dashboard started in tmux session '$SESSION' on port $PORT"
echo "Open: http://127.0.0.1:$PORT"
