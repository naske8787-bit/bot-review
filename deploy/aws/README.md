# AWS deployment helper

This folder contains scripts to bootstrap a fresh Ubuntu EC2 host for this repository and run services with systemd.

## What gets installed

- OS packages: git, tmux, python3, python3-venv, python3-pip, build tools
- Project checkout/update at `/opt/Capitol_Trades_API` (configurable)
- Python virtual environment at `/opt/Capitol_Trades_API/.venv`
- Dependencies from:
  - `requirements.txt`
  - `trading_bot/requirements.txt`
  - `crypto_bot/requirements.txt`
  - `asx_bot/requirements.txt`
  - `forex_bot/requirements.txt`
  - `dashboard/requirements.txt`
- systemd units:
  - `capitol-api`
  - `capitol-dashboard`
  - `capitol-trading-bot`
  - `capitol-crypto-bot`
  - `capitol-asx-bot`
  - `capitol-forex-bot`
  - `capitol-tech-research-bot`

## Quick start (on EC2)

Run as root once after instance creation:

```bash
sudo bash deploy/aws/bootstrap_ec2.sh \
  --repo-url https://github.com/naske8787-bit/Capitol_Trades_API.git \
  --branch main \
  --app-dir /opt/Capitol_Trades_API \
  --app-user ubuntu \
  --import-env-files true
```

When `--import-env-files true` is set, bootstrap merges values from detected files like `trading_bot/.env`, `crypto_bot/.env`, and `asx_bot/.env` into `/etc/capitol-trades/capitol-trades.env`.

## One-shot first boot with cloud-init

Use the contents of `cloud-init.user-data.yaml` as EC2 User data when launching a new Ubuntu instance.

This automatically:

- Clones the repository to `/opt/Capitol_Trades_API`
- Runs `bootstrap_ec2.sh`
- Enables and starts systemd services

File:

- `deploy/aws/cloud-init.user-data.yaml`

## Configure secrets

Edit:

```bash
sudo nano /etc/capitol-trades/capitol-trades.env
```

You can re-run import at any time:

```bash
sudo bash deploy/aws/import_env_files.sh --app-dir /opt/Capitol_Trades_API --out-file /etc/capitol-trades/capitol-trades.env
```

Then restart services:

```bash
sudo systemctl restart capitol-api capitol-dashboard capitol-trading-bot capitol-crypto-bot capitol-asx-bot
sudo systemctl restart capitol-forex-bot capitol-tech-research-bot
```

## Health checks

```bash
sudo systemctl status capitol-api --no-pager
sudo systemctl status capitol-dashboard --no-pager
sudo systemctl status capitol-trading-bot --no-pager
sudo systemctl status capitol-crypto-bot --no-pager
sudo systemctl status capitol-asx-bot --no-pager
sudo systemctl status capitol-forex-bot --no-pager
sudo systemctl status capitol-tech-research-bot --no-pager

curl -I http://127.0.0.1:8000/health
ss -tulpen | grep -E '8000|5051'
```

## Nginx + HTTPS (Let's Encrypt)

After DNS is pointed at your EC2 public IP, run:

```bash
sudo bash deploy/aws/setup_nginx_tls.sh \
  --api-domain api.example.com \
  --dashboard-domain dashboard.example.com \
  --email you@example.com
```

File:

- `deploy/aws/setup_nginx_tls.sh`

This script installs Nginx + Certbot, configures reverse proxy to local ports 8000/5051, requests certs, and enables HTTPS redirects.

## IAM role template

Starter least-privilege policy template:

- `deploy/aws/iam-policy-template.json`

Before use, replace placeholders:

- `REGION`
- `ACCOUNT_ID`
- `CAPITOL_BUCKET_NAME` (if S3 is needed)

`KMS_KEY_ID` is optional now and can be skipped when using AWS-managed keys.

Attach the final policy to an EC2 IAM role and attach the role to your instance.

### Fast path with scripts

Render a concrete policy from placeholders:

```bash
bash deploy/aws/render_iam_policy.sh \
  --region us-east-1 \
  --account-id 123456789012 \
  --bucket my-capitol-data \
  --out deploy/aws/iam-policy.json
```

If you use a customer-managed KMS key, include:

```bash
--kms-key-id 11111111-2222-3333-4444-555555555555
```

Create/attach IAM role + instance profile (requires AWS CLI auth):

```bash
bash deploy/aws/create_attach_iam_role.sh \
  --instance-id i-0123456789abcdef0 \
  --role-name CapitolTradesEc2Role \
  --policy-name CapitolTradesRuntimePolicy \
  --profile-name CapitolTradesEc2InstanceProfile \
  --policy-doc deploy/aws/iam-policy.json
```

## Logs

```bash
# service logs
sudo journalctl -u capitol-api -f
sudo journalctl -u capitol-dashboard -f
sudo journalctl -u capitol-trading-bot -f
sudo journalctl -u capitol-crypto-bot -f
sudo journalctl -u capitol-asx-bot -f
sudo journalctl -u capitol-forex-bot -f
sudo journalctl -u capitol-tech-research-bot -f

# existing app logs
tail -f /opt/Capitol_Trades_API/api_server.log
tail -f /tmp/dashboard.log
tail -f /opt/Capitol_Trades_API/trading_bot/bot.log
tail -f /opt/Capitol_Trades_API/crypto_bot/bot.log
tail -f /opt/Capitol_Trades_API/asx_bot/output.log
tail -f /opt/Capitol_Trades_API/forex_bot/output.log
tail -f /opt/Capitol_Trades_API/tech_research_bot/bot.log
```

## Notes

- This setup keeps your existing supervisor scripts as the runtime source of truth.
- If your workloads need GPU, use a GPU-capable instance and matching ML stack.
- For production internet exposure, use Nginx + TLS and tight security group rules.
