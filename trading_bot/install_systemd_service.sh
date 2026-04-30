#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

SERVICE_NAME="${SERVICE_NAME:-trading-bot}"
BOT_DIR="$(pwd)"
BOT_USER="${BOT_USER:-${SUDO_USER:-$USER}}"
PYTHON_BIN="${PYTHON_BIN:-$BOT_DIR/.venv/bin/python}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
DRY_RUN="${DRY_RUN:-0}"

render_service() {
  cat <<EOF
[Unit]
Description=Capitol Trades Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
Environment=PYTHON_BIN=$PYTHON_BIN
ExecStart=/usr/bin/bash $BOT_DIR/supervise_bot.sh
Restart=always
RestartSec=10
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF
}

if [[ "$DRY_RUN" == "1" ]]; then
  render_service
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo ./install_systemd_service.sh"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found at: $PYTHON_BIN"
  echo "Create the environment first, for example:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

render_service > "$SERVICE_PATH"
chmod 644 "$SERVICE_PATH"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Installed service at: $SERVICE_PATH"
echo "Useful checks:"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -n 50 --no-pager"
