#!/usr/bin/env bash
# Supervisor — restarts the forex bot if it crashes.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG="$SCRIPT_DIR/supervisor.log"
RESTART_DELAY=10

echo "[supervisor] Forex bot supervisor started." | tee -a "$LOG"

while true; do
    echo "[supervisor] $(date)  Starting forex bot..." | tee -a "$LOG"
    cd "$SCRIPT_DIR" && $PYTHON_BIN main.py 2>&1 | tee -a "$SCRIPT_DIR/output.log"
    EXIT_CODE=$?
    echo "[supervisor] $(date)  Bot exited with code $EXIT_CODE. Restarting in ${RESTART_DELAY}s..." | tee -a "$LOG"
    sleep $RESTART_DELAY
done
