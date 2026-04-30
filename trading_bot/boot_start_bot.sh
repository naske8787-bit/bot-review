#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

SESSION_NAME="trading_bot"
STARTUP_LOG="startup.log"
STARTUP_DELAY="${STARTUP_DELAY:-15}"
PYTHON_BIN="${PYTHON_BIN:-/home/codespace/.python/current/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python}"
fi

{
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] boot_start_bot.sh invoked"
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] waiting ${STARTUP_DELAY}s for network/services"
  sleep "$STARTUP_DELAY"

  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] tmux session '$SESSION_NAME' already running; nothing to do"
    exit 0
  fi

  if command -v tmux >/dev/null 2>&1; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] starting bot via run_tmux.sh"
    PYTHON_BIN="$PYTHON_BIN" bash ./run_tmux.sh
  else
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] tmux not found; falling back to run_bot.sh"
    PYTHON_BIN="$PYTHON_BIN" bash ./run_bot.sh
  fi
} >> "$STARTUP_LOG" 2>&1
