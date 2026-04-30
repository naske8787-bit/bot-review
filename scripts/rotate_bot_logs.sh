#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
ARCHIVE_DIR="$ROOT_DIR/archive/log_reset_$STAMP"

mkdir -p "$ARCHIVE_DIR"

rotate_one() {
  local src="$1"
  local rel="$2"
  local dst_dir="$ARCHIVE_DIR/$(dirname "$rel")"

  if [[ ! -f "$src" ]]; then
    echo "skip (missing): $rel"
    return
  fi

  mkdir -p "$dst_dir"
  mv "$src" "$ARCHIVE_DIR/$rel"
  : > "$src"
  echo "rotated: $rel -> archive/log_reset_$STAMP/$rel"
}

rotate_one "$ROOT_DIR/trading_bot/bot.log" "trading_bot/bot.log"
rotate_one "$ROOT_DIR/trading_bot/supervisor.log" "trading_bot/supervisor.log"
rotate_one "$ROOT_DIR/crypto_bot/bot.log" "crypto_bot/bot.log"
rotate_one "$ROOT_DIR/crypto_bot/supervisor.log" "crypto_bot/supervisor.log"

echo "done: archived logs under $ARCHIVE_DIR"
