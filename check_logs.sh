set -e
cd /workspaces/Capitol_Trades_API
for f in crypto_bot/bot.log trading_bot/bot.log; do
  echo "=== $f sample ==="
  if [ -f "$f" ]; then
    tail -n 25 "$f" | cat
    echo "timestamp-like lines in tail:"
    tail -n 200 "$f" | grep -E "[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}:[0-9]{2}:[0-9]{2}" | tail -n 5 || true
  else
    echo "$f not found"
  fi
  echo
done
