#!/usr/bin/env bash
set -euo pipefail

# Canonical service controller:
# - API/dashboard owned by systemd units when present.
# - Bots owned by tmux sessions.
# Usage: scripts/service_ctl.sh {start|stop|restart|status|health}

ACTION="${1:-status}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

has_systemd_unit() {
  local unit="$1"
  local state
  state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null || echo "not-found")"
  [[ "$state" != "not-found" ]]
}

start_tmux_bot() {
  local session="$1"
  local workdir="$2"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "UP   $session"
    return 0
  fi
  tmux new-session -d -s "$session" "cd $workdir && PYTHON_BIN=$VENV_PY bash ./supervise_bot.sh"
  echo "STARTED $session"
}

stop_tmux_bot() {
  local session="$1"
  tmux kill-session -t "$session" 2>/dev/null || true
  echo "STOPPED $session"
}

status_tmux_bot() {
  local session="$1"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "UP   $session"
  else
    echo "DOWN $session"
  fi
}

start_api_dashboard() {
  if has_systemd_unit "capitol-api.service"; then
    sudo systemctl start capitol-api
    echo "STARTED systemd:capitol-api"
  else
    if ! tmux has-session -t api_server 2>/dev/null; then
      tmux new-session -d -s api_server "cd $ROOT_DIR && bash supervise_api.sh"
    fi
    echo "UP   tmux:api_server"
  fi

  if has_systemd_unit "capitol-dashboard.service"; then
    sudo systemctl start capitol-dashboard
    echo "STARTED systemd:capitol-dashboard"
  else
    if ! tmux has-session -t mining_dashboard 2>/dev/null; then
      tmux new-session -d -s mining_dashboard "cd $ROOT_DIR && bash supervise_dashboard.sh"
    fi
    echo "UP   tmux:mining_dashboard"
  fi
}

stop_api_dashboard() {
  if has_systemd_unit "capitol-api.service"; then
    sudo systemctl stop capitol-api || true
    echo "STOPPED systemd:capitol-api"
  fi
  if has_systemd_unit "capitol-dashboard.service"; then
    sudo systemctl stop capitol-dashboard || true
    echo "STOPPED systemd:capitol-dashboard"
  fi
  tmux kill-session -t api_server 2>/dev/null || true
  tmux kill-session -t mining_dashboard 2>/dev/null || true
}

status_api_dashboard() {
  if has_systemd_unit "capitol-api.service"; then
    echo "api: $(systemctl is-active capitol-api || true) (systemd)"
  else
    if tmux has-session -t api_server 2>/dev/null; then echo "api: active (tmux)"; else echo "api: inactive"; fi
  fi

  if has_systemd_unit "capitol-dashboard.service"; then
    echo "dashboard: $(systemctl is-active capitol-dashboard || true) (systemd)"
  else
    if tmux has-session -t mining_dashboard 2>/dev/null; then echo "dashboard: active (tmux)"; else echo "dashboard: inactive"; fi
  fi
}

run_health() {
  "$ROOT_DIR/scripts/health_check.sh"
}

case "$ACTION" in
  start)
    start_api_dashboard
    start_tmux_bot trading_bot "$ROOT_DIR/trading_bot"
    start_tmux_bot crypto_bot "$ROOT_DIR/crypto_bot"
    ;;
  stop)
    stop_tmux_bot trading_bot
    stop_tmux_bot crypto_bot
    stop_api_dashboard
    ;;
  restart)
    "$0" stop
    sleep 2
    "$0" start
    ;;
  status)
    status_api_dashboard
    status_tmux_bot trading_bot
    status_tmux_bot crypto_bot
    ;;
  health)
    run_health
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|health}"
    exit 2
    ;;
esac
