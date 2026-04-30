#!/usr/bin/env bash
set -euo pipefail

# One-time EC2 bootstrap for Capitol_Trades_API.
# Usage:
#   sudo bash deploy/aws/bootstrap_ec2.sh \
#     --repo-url https://github.com/naske8787-bit/Capitol_Trades_API.git \
#     --branch main \
#     --app-dir /opt/Capitol_Trades_API \
#     --app-user ubuntu

REPO_URL=""
BRANCH="main"
APP_DIR="/opt/Capitol_Trades_API"
APP_USER="ubuntu"
PYTHON_BIN="/usr/bin/python3"
IMPORT_ENV_FILES="true"

usage() {
  cat <<'USAGE'
Usage:
  bootstrap_ec2.sh --repo-url <git-url> [--branch <branch>] [--app-dir <dir>] [--app-user <user>] [--python-bin <path>] [--import-env-files true|false]

Example:
  sudo bash deploy/aws/bootstrap_ec2.sh \
    --repo-url https://github.com/naske8787-bit/Capitol_Trades_API.git \
    --branch main \
    --app-dir /opt/Capitol_Trades_API \
    --app-user ubuntu \
    --import-env-files true
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="$2"; shift 2 ;;
    --branch)
      BRANCH="$2"; shift 2 ;;
    --app-dir)
      APP_DIR="$2"; shift 2 ;;
    --app-user)
      APP_USER="$2"; shift 2 ;;
    --python-bin)
      PYTHON_BIN="$2"; shift 2 ;;
    --import-env-files)
      IMPORT_ENV_FILES="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$REPO_URL" ]]; then
  echo "--repo-url is required." >&2
  usage
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0 ..." >&2
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "User '$APP_USER' does not exist." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get -y install \
  git \
  tmux \
  python3 \
  python3-venv \
  python3-pip \
  build-essential \
  pkg-config \
  libopenblas-dev \
  liblapack-dev \
  gfortran

mkdir -p "$(dirname "$APP_DIR")"

if [[ ! -d "$APP_DIR/.git" ]]; then
  rm -rf "$APP_DIR"
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
fi

chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" bash -lc "
  set -euo pipefail
  cd '$APP_DIR'
  $PYTHON_BIN -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip wheel setuptools

  pip install -r requirements.txt
  pip install -r trading_bot/requirements.txt
  pip install -r crypto_bot/requirements.txt
  pip install -r asx_bot/requirements.txt
  pip install -r forex_bot/requirements.txt
  pip install -r dashboard/requirements.txt
"

# Create runtime env file if missing.
if [[ ! -f /etc/capitol-trades/capitol-trades.env ]]; then
  mkdir -p /etc/capitol-trades
  cp "$APP_DIR/deploy/aws/capitol-trades.env.example" /etc/capitol-trades/capitol-trades.env
  chmod 600 /etc/capitol-trades/capitol-trades.env
fi

if [[ "$IMPORT_ENV_FILES" == "true" ]]; then
  bash "$APP_DIR/deploy/aws/import_env_files.sh" \
    --app-dir "$APP_DIR" \
    --out-file /etc/capitol-trades/capitol-trades.env
fi

bash "$APP_DIR/deploy/aws/install_systemd_units.sh" \
  --app-dir "$APP_DIR" \
  --app-user "$APP_USER"

echo "Bootstrap complete."
echo "Next: edit /etc/capitol-trades/capitol-trades.env and set your secrets."
echo "Then: sudo systemctl restart capitol-api capitol-dashboard capitol-trading-bot capitol-crypto-bot capitol-asx-bot capitol-forex-bot capitol-tech-research-bot"
