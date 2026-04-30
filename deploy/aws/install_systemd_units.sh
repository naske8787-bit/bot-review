#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/Capitol_Trades_API"
APP_USER="ubuntu"
ENV_FILE="/etc/capitol-trades/capitol-trades.env"

usage() {
  cat <<'USAGE'
Usage:
  install_systemd_units.sh [--app-dir <dir>] [--app-user <user>] [--env-file <path>]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir)
      APP_DIR="$2"; shift 2 ;;
    --app-user)
      APP_USER="$2"; shift 2 ;;
    --env-file)
      ENV_FILE="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0 ..." >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "App dir does not exist: $APP_DIR" >&2
  exit 1
fi

mkdir -p /etc/capitol-trades
if [[ ! -f "$ENV_FILE" ]]; then
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

cat >/etc/systemd/system/capitol-api.service <<EOF
[Unit]
Description=Capitol Trades API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec ./supervise_api.sh'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-dashboard.service <<EOF
[Unit]
Description=Capitol Mining Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec ./supervise_dashboard.sh'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-trading-bot.service <<EOF
[Unit]
Description=Capitol Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/trading_bot
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec ./supervise_bot.sh'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-crypto-bot.service <<EOF
[Unit]
Description=Capitol Crypto Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/crypto_bot
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec ./supervise_bot.sh'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-asx-bot.service <<EOF
[Unit]
Description=Capitol ASX Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/asx_bot
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec $APP_DIR/.venv/bin/python -u main.py'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-forex-bot.service <<EOF
[Unit]
Description=Capitol Forex Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/forex_bot
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec $APP_DIR/.venv/bin/python -u main.py'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-tech-research-bot.service <<EOF
[Unit]
Description=Capitol Tech Research Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/tech_research_bot
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHON_BIN=$APP_DIR/.venv/bin/python
ExecStart=/bin/bash -lc 'exec ./supervise_bot.sh'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/capitol-healthcheck.service <<EOF
[Unit]
Description=Capitol periodic health check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/bin/bash -lc './scripts/health_check.sh >> /tmp/capitol-healthcheck.log 2>&1'
EOF

cat >/etc/systemd/system/capitol-healthcheck.timer <<EOF
[Unit]
Description=Run Capitol health check every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Unit=capitol-healthcheck.service
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable \
  capitol-api \
  capitol-dashboard \
  capitol-trading-bot \
  capitol-crypto-bot \
  capitol-asx-bot \
  capitol-forex-bot \
  capitol-tech-research-bot \
  capitol-healthcheck.timer

echo "Installed and enabled systemd units:"
echo "  - capitol-api"
echo "  - capitol-dashboard"
echo "  - capitol-trading-bot"
echo "  - capitol-crypto-bot"
echo "  - capitol-asx-bot"
echo "  - capitol-forex-bot"
echo "  - capitol-tech-research-bot"
echo "  - capitol-healthcheck.timer"
