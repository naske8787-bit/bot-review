#!/usr/bin/env bash
# Start the ASX bot in a detached tmux session.
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-$(which python3)}"
SESSION="asx_bot"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already running. Use ./stop_bot.sh to stop it first."
    exit 0
fi

cd "$SCRIPT_DIR"
tmux new-session -d -s "$SESSION" \
    "cd '$SCRIPT_DIR' && $PYTHON_BIN -u main.py 2>&1 | tee output.log"

echo "ASX bot started in tmux session '$SESSION'."
echo "  Watch output : tmux attach -t $SESSION"
echo "  View log     : tail -f $SCRIPT_DIR/output.log"
echo "  Stop bot     : bash $SCRIPT_DIR/stop_bot.sh"
