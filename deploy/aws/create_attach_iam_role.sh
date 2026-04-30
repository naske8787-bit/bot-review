#!/usr/bin/env bash
set -euo pipefail

# Create policy + role for EC2 and attach to an instance profile.
# Requires AWS CLI configured with permissions.

ROLE_NAME="CapitolTradesEc2Role"
POLICY_NAME="CapitolTradesRuntimePolicy"
PROFILE_NAME="CapitolTradesEc2InstanceProfile"
POLICY_DOC="deploy/aws/iam-policy.json"
TRUST_DOC="$(mktemp)"
INSTANCE_ID=""

cleanup() {
  rm -f "$TRUST_DOC"
}
trap cleanup EXIT

usage() {
  cat <<'USAGE'
Usage:
  create_attach_iam_role.sh --instance-id <i-xxxx> [--role-name NAME] [--policy-name NAME] [--profile-name NAME] [--policy-doc PATH]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-id)
      INSTANCE_ID="$2"; shift 2 ;;
    --role-name)
      ROLE_NAME="$2"; shift 2 ;;
    --policy-name)
      POLICY_NAME="$2"; shift 2 ;;
    --profile-name)
      PROFILE_NAME="$2"; shift 2 ;;
    --policy-doc)
      POLICY_DOC="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$INSTANCE_ID" ]]; then
  echo "--instance-id is required." >&2
  usage
  exit 1
fi

if [[ ! -f "$POLICY_DOC" ]]; then
  echo "Policy document not found: $POLICY_DOC" >&2
  exit 1
fi

cat > "$TRUST_DOC" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "file://${TRUST_DOC}" >/dev/null
fi

if ! aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
  aws iam create-policy --policy-name "$POLICY_NAME" --policy-document "file://${POLICY_DOC}" >/dev/null
else
  aws iam create-policy-version \
    --policy-arn "$POLICY_ARN" \
    --policy-document "file://${POLICY_DOC}" \
    --set-as-default >/dev/null
fi

if ! aws iam list-attached-role-policies --role-name "$ROLE_NAME" --query "AttachedPolicies[?PolicyArn=='${POLICY_ARN}'] | length(@)" --output text | grep -q '^1$'; then
  aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$POLICY_ARN"
fi

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null
fi

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" --query "InstanceProfile.Roles[?RoleName=='${ROLE_NAME}'] | length(@)" --output text | grep -q '^1$'; then
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME" >/dev/null
fi

aws ec2 associate-iam-instance-profile \
  --instance-id "$INSTANCE_ID" \
  --iam-instance-profile Name="$PROFILE_NAME" >/dev/null || true

echo "Role/profile associated attempt complete."
echo "Role: $ROLE_NAME"
echo "Policy: $POLICY_NAME"
echo "Profile: $PROFILE_NAME"
echo "Instance: $INSTANCE_ID"
