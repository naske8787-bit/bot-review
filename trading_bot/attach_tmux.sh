#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

SESSION_NAME="trading_bot"

tmux attach -t "$SESSION_NAME"