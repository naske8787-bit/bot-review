#!/usr/bin/env bash
# Run the forex bot inside a tmux session so it survives terminal closes.
SESSION="forex_bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Forex bot is already running in tmux session '$SESSION'."
    echo "Attach with:  tmux attach -t $SESSION"
    exit 0
fi

tmux new-session -d -s "$SESSION" \
    "cd '$SCRIPT_DIR' && set -a && source '$SCRIPT_DIR/.env' && set +a && PYTHONUNBUFFERED=1 $PYTHON_BIN -u main.py 2>&1 | tee -a output.log"

echo "Forex bot started in tmux session '$SESSION'."
echo "Attach with:  tmux attach -t $SESSION"
echo "Stop with:    tmux kill-session -t $SESSION"
