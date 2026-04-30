#!/usr/bin/env bash
set -euo pipefail

# Render iam-policy-template.json by replacing placeholders.
# Usage:
#   bash deploy/aws/render_iam_policy.sh \
#     --region us-east-1 \
#     --account-id 123456789012 \
#     --bucket my-capitol-data \
#     --kms-key-id 11111111-2222-3333-4444-555555555555 \
#     --out deploy/aws/iam-policy.json

TEMPLATE="deploy/aws/iam-policy-template.json"
OUT="deploy/aws/iam-policy.json"
REGION=""
ACCOUNT_ID=""
KMS_KEY_ID=""
BUCKET=""

usage() {
  cat <<'USAGE'
Usage:
  render_iam_policy.sh --region <region> --account-id <12-digit> [--kms-key-id <key-id-or-arn>] [--bucket <name>] [--template <path>] [--out <path>]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)
      REGION="$2"; shift 2 ;;
    --account-id)
      ACCOUNT_ID="$2"; shift 2 ;;
    --kms-key-id)
      KMS_KEY_ID="$2"; shift 2 ;;
    --bucket)
      BUCKET="$2"; shift 2 ;;
    --template)
      TEMPLATE="$2"; shift 2 ;;
    --out)
      OUT="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$REGION" || -z "$ACCOUNT_ID" ]]; then
  echo "--region and --account-id are required." >&2
  usage
  exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template not found: $TEMPLATE" >&2
  exit 1
fi

if [[ -z "$BUCKET" ]]; then
  BUCKET="capitol-placeholder-bucket"
fi

mkdir -p "$(dirname "$OUT")"

sed \
  -e "s/REGION/${REGION}/g" \
  -e "s/ACCOUNT_ID/${ACCOUNT_ID}/g" \
  -e "s/CAPITOL_BUCKET_NAME/${BUCKET}/g" \
  "$TEMPLATE" > "$OUT"

if [[ -n "$KMS_KEY_ID" ]]; then
  python3 - "$OUT" "$REGION" "$ACCOUNT_ID" "$KMS_KEY_ID" <<'PY'
import json
import sys

path, region, account_id, kms_key_id = sys.argv[1:]

with open(path, encoding="utf-8") as f:
  doc = json.load(f)

statement = {
  "Sid": "AllowKMSDecryptForSecrets",
  "Effect": "Allow",
  "Action": ["kms:Decrypt"],
  "Resource": f"arn:aws:kms:{region}:{account_id}:key/{kms_key_id}",
  "Condition": {
    "StringEquals": {
      "kms:ViaService": [
        f"secretsmanager.{region}.amazonaws.com",
        f"ssm.{region}.amazonaws.com",
      ]
    }
  },
}

doc.setdefault("Statement", []).insert(3, statement)

with open(path, "w", encoding="utf-8") as f:
  json.dump(doc, f, indent=2)
  f.write("\n")
PY
fi

echo "Rendered policy: $OUT"
