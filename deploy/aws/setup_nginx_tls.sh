#!/usr/bin/env bash
set -euo pipefail

API_DOMAIN=""
DASHBOARD_DOMAIN=""
EMAIL=""
API_PORT="8000"
DASHBOARD_PORT="5051"

usage() {
  cat <<'USAGE'
Usage:
  setup_nginx_tls.sh --api-domain <api.example.com> --dashboard-domain <dash.example.com> --email <you@example.com> [--api-port 8000] [--dashboard-port 5051]

Notes:
- DNS A records for both domains must point to this host first.
- Run as root.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-domain)
      API_DOMAIN="$2"; shift 2 ;;
    --dashboard-domain)
      DASHBOARD_DOMAIN="$2"; shift 2 ;;
    --email)
      EMAIL="$2"; shift 2 ;;
    --api-port)
      API_PORT="$2"; shift 2 ;;
    --dashboard-port)
      DASHBOARD_PORT="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$API_DOMAIN" || -z "$DASHBOARD_DOMAIN" || -z "$EMAIL" ]]; then
  echo "Missing required args." >&2
  usage
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0 ..." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y install nginx certbot python3-certbot-nginx ufw

cat >/etc/nginx/sites-available/capitol-trades.conf <<EOF
server {
    listen 80;
    server_name ${API_DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${API_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}

server {
    listen 80;
    server_name ${DASHBOARD_DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${DASHBOARD_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/capitol-trades.conf /etc/nginx/sites-enabled/capitol-trades.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl restart nginx
systemctl enable nginx

# Optional firewall hardening on Ubuntu.
ufw allow OpenSSH || true
ufw allow 'Nginx Full' || true
ufw --force enable || true

certbot --nginx \
  -d "$API_DOMAIN" \
  -d "$DASHBOARD_DOMAIN" \
  --agree-tos \
  --redirect \
  --non-interactive \
  --email "$EMAIL"

systemctl status nginx --no-pager

echo "Nginx + TLS setup complete."
echo "API URL: https://$API_DOMAIN"
echo "Dashboard URL: https://$DASHBOARD_DOMAIN"
