#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

BOOT_SCRIPT="$(pwd)/boot_start_bot.sh"
CRON_LINE="@reboot /usr/bin/env bash \"$BOOT_SCRIPT\""

if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab is not installed in this environment."
  echo "The VS Code workspace auto-start task is already configured for this repo."
  echo "For a full Linux VM/server, install cron and rerun this script."
  exit 1
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

(crontab -l 2>/dev/null | grep -Fv "$BOOT_SCRIPT" || true) > "$TMP_FILE"
printf '%s\n' "$CRON_LINE" >> "$TMP_FILE"
crontab "$TMP_FILE"

echo "Installed @reboot startup entry:"
crontab -l | grep -F "$BOOT_SCRIPT"
