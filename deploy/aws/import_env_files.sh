#!/usr/bin/env bash
set -euo pipefail

# Import KEY=VALUE entries from repo .env files into a system env file.
# Last file wins for duplicate keys.

APP_DIR="/opt/Capitol_Trades_API"
OUT_FILE="/etc/capitol-trades/capitol-trades.env"

usage() {
  cat <<'USAGE'
Usage:
  import_env_files.sh [--app-dir <dir>] [--out-file <path>]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir)
      APP_DIR="$2"; shift 2 ;;
    --out-file)
      OUT_FILE="$2"; shift 2 ;;
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

mkdir -p "$(dirname "$OUT_FILE")"
touch "$OUT_FILE"
chmod 600 "$OUT_FILE"

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

# Keep existing file first, then layer discovered .env files over it.
cat "$OUT_FILE" > "$tmp_file"

declare -a candidates=(
  "$APP_DIR/.env"
  "$APP_DIR/trading_bot/.env"
  "$APP_DIR/crypto_bot/.env"
  "$APP_DIR/asx_bot/.env"
  "$APP_DIR/forex_bot/.env"
  "$APP_DIR/india_bot/.env"
)

import_count=0
for f in "${candidates[@]}"; do
  if [[ -f "$f" ]]; then
    echo "Importing: $f"
    # Keep assignment lines only; supports 'export KEY=VAL' and plain 'KEY=VAL'.
    awk '
      /^[[:space:]]*#/ { next }
      /^[[:space:]]*$/ { next }
      {
        line=$0
        sub(/^[[:space:]]*export[[:space:]]+/, "", line)
        if (line ~ /^[A-Za-z_][A-Za-z0-9_]*=/) print line
      }
    ' "$f" >> "$tmp_file"
    import_count=$((import_count + 1))
  fi
done

if [[ "$import_count" -eq 0 ]]; then
  echo "No .env files found to import under $APP_DIR."
fi

# Deduplicate by key with last-one-wins while preserving full value (including '=' in value).
awk -F= '
  /^[[:space:]]*#/ { next }
  /^[[:space:]]*$/ { next }
  {
    key=$1
    sub(/^[[:space:]]+/, "", key)
    sub(/[[:space:]]+$/, "", key)
    if (key ~ /^[A-Za-z_][A-Za-z0-9_]*$/) {
      val=substr($0, index($0, "=")+1)
      data[key]=val
      order[++n]=key
    }
  }
  END {
    for (i=1; i<=n; i++) {
      k=order[i]
      if (!(k in seen)) seen[k]=1
    }
    for (i=1; i<=n; i++) {
      k=order[i]
      if (seen[k] == 1) {
        print k "=" data[k]
        seen[k]=2
      }
    }
  }
' "$tmp_file" > "$OUT_FILE"

chmod 600 "$OUT_FILE"
echo "Wrote merged env to $OUT_FILE"
