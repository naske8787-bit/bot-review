set -e
files=(trading_bot/.env crypto_bot/.env asx_bot/.env forex_bot/.env)

echo '=== before ==='
for f in "${files[@]}"; do
  provider=$(grep -E '^SEARCH_PROVIDER=' "$f" | tail -n1 | cut -d= -f2- || true)
  engine=$(grep -E '^SEARCH_ENGINE=' "$f" | tail -n1 | cut -d= -f2- || true)
  key=$(grep -E '^SEARCH_API_KEY=' "$f" | tail -n1 | cut -d= -f2- || true)
  echo "$f provider=${provider:-<missing>} engine=${engine:-<missing>} key_set=$([[ -n "$key" ]] && echo true || echo false) key_len=${#key}"
done

donor=''
for f in "${files[@]}"; do
  key=$(grep -E '^SEARCH_API_KEY=' "$f" | tail -n1 | cut -d= -f2- || true)
  if [[ -n "$key" ]]; then donor="$key"; break; fi
done

if [[ -z "$donor" ]]; then
  echo 'No non-empty SEARCH_API_KEY found in any bot .env file. Cannot auto-activate without a key.'
  exit 3
fi

for f in "${files[@]}"; do
  tmp=$(mktemp)
  awk -v key="$donor" '
    BEGIN { p=0; e=0; k=0 }
    {
      if ($0 ~ /^SEARCH_PROVIDER=/) { print "SEARCH_PROVIDER=brave"; p=1; next }
      if ($0 ~ /^SEARCH_ENGINE=/) { print "SEARCH_ENGINE=web"; e=1; next }
      if ($0 ~ /^SEARCH_API_KEY=/) { print "SEARCH_API_KEY=" key; k=1; next }
      print $0
    }
    END {
      if (!p) print "SEARCH_PROVIDER=brave"
      if (!e) print "SEARCH_ENGINE=web"
      if (!k) print "SEARCH_API_KEY=" key
    }
  ' "$f" > "$tmp"
  mv "$tmp" "$f"
done

echo '=== after env sync ==='
for f in "${files[@]}"; do
  provider=$(grep -E '^SEARCH_PROVIDER=' "$f" | tail -n1 | cut -d= -f2- || true)
  engine=$(grep -E '^SEARCH_ENGINE=' "$f" | tail -n1 | cut -d= -f2- || true)
  key=$(grep -E '^SEARCH_API_KEY=' "$f" | tail -n1 | cut -d= -f2- || true)
  echo "$f provider=${provider:-<missing>} engine=${engine:-<missing>} key_set=$([[ -n "$key" ]] && echo true || echo false) key_len=${#key}"
done

echo '=== restarting bot sessions ==='
for s in trading_bot crypto_bot asx_bot forex_bot; do
  tmux has-session -t "$s" 2>/dev/null && tmux kill-session -t "$s" || true
  echo "killed(if existed): $s"
done
(cd trading_bot && PYTHON_BIN=/home/codespace/.python/current/bin/python bash ./run_tmux.sh)
(cd crypto_bot && PYTHON_BIN=/home/codespace/.python/current/bin/python bash ./run_tmux.sh)
(cd asx_bot && PYTHON_BIN=/home/codespace/.python/current/bin/python bash ./run_tmux.sh)
(cd forex_bot && PYTHON_BIN=/home/codespace/.python/current/bin/python bash ./run_tmux.sh)

tmux ls | grep -E 'trading_bot|crypto_bot|asx_bot|forex_bot' || true

echo '=== runtime config check ==='
/usr/bin/python3 - <<'PY'
import importlib.util
for b in ['trading_bot','crypto_bot','asx_bot','forex_bot']:
    p=f'/workspaces/Capitol_Trades_API/{b}/config.py'
    spec=importlib.util.spec_from_file_location(f'{b}_config', p)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    key=getattr(mod,'SEARCH_API_KEY','') or ''
    provider=getattr(mod,'SEARCH_PROVIDER','')
    engine=getattr(mod,'SEARCH_ENGINE','')
    print(f"{b}: provider={provider} engine={engine} key_set={bool(key)} key_len={len(key)}")
PY

echo '=== dashboard payload key_set check ==='
/usr/bin/python3 - <<'PY'
import json, urllib.request
raw = urllib.request.urlopen('http://localhost:8000/bot_dashboard_data', timeout=15).read()
d = json.loads(raw.decode('utf-8'))
for b in ['trading_bot','crypto_bot','asx_bot','forex_bot']:
    r = (((d.get(b) or {}).get('metrics') or {}).get('research') or {})
    print(f"{b}: payload_key_set={r.get('key_set')} provider={r.get('provider')} engine={r.get('engine')} mentions={r.get('mentions')}")
PY
