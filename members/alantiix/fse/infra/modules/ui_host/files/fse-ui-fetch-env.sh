#!/usr/bin/env bash
# fse-ui.service / fse-edge.service ExecStartPre: write the container env.
#
#   fse-ui-fetch-env.sh ui|edge
#
# Writes $FSE_RUN_DIR/env (mode 0600) for `docker run --env-file` and
# $FSE_RUN_DIR/image_tag. Exits 1 when /fse/image_tag is none (no image
# deployed yet), when an aws call fails (only the command is named) or when a
# value is malformed. Never prints a value.
#   ui:   FSE_UI_OIDC_CLIENT_SECRET (Cognito app client), FSE_UI_RUNNER_INSTANCE_ID
#         (IMDSv2), FSE_UI_IMAGE_TAG
#   edge: ORIGIN_SECRET (/fse/ui/origin_secret), FSE_EDGE_SITE, FSE_EDGE_ZONE_ID,
#         FSE_EDGE_AWS_REGION
#
# Environment (default in brackets; systemd loads /etc/fse/host.env and
# /etc/fse/ui.env):
#   FSE_RUN_DIR      output directory [/run/fse-ui or /run/fse-edge]
#   FSE_SSM_PREFIX   parameter path prefix [/fse]
#   AWS_REGION       [us-east-1]
#   ui:   FSE_UI_COGNITO_POOL_ID, FSE_UI_OIDC_CLIENT_ID (required)
#   edge: FSE_EDGE_SITE, FSE_EDGE_ZONE_ID (required), FSE_EDGE_AWS_REGION [AWS_REGION]
set -euo pipefail

role="${1:-}"
case "$role" in
  ui | edge) ;;
  *)
    echo "usage: fse-ui-fetch-env.sh ui|edge" >&2
    exit 2
    ;;
esac

FSE_RUN_DIR="${FSE_RUN_DIR:-/run/fse-$role}"
FSE_SSM_PREFIX="${FSE_SSM_PREFIX:-/fse}"
AWS_REGION="${AWS_REGION:-us-east-1}"
imds="http://169.254.169.254/latest"

fail() {
  echo "fse-ui-fetch-env: $*" >&2
  exit 1
}

umask 077
mkdir -p "$FSE_RUN_DIR"
tmp="$FSE_RUN_DIR/env.tmp"
trap 'rm -f "$tmp"' EXIT

# Values are captured, never echoed; aws stderr is dropped so a failure names
# the command only.
tag="$(aws ssm get-parameter --region "$AWS_REGION" --name "$FSE_SSM_PREFIX/image_tag" \
  --query Parameter.Value --output text 2>/dev/null)" ||
  fail "aws ssm get-parameter $FSE_SSM_PREFIX/image_tag failed"
if [[ -z "$tag" || "$tag" == "none" ]]; then
  fail "no image deployed yet ($FSE_SSM_PREFIX/image_tag is none)"
fi
if [[ ! "$tag" =~ ^[A-Za-z0-9_.-]{1,128}$ ]]; then
  fail "$FSE_SSM_PREFIX/image_tag is not a valid image tag"
fi

if [[ "$role" == "ui" ]]; then
  pool="${FSE_UI_COGNITO_POOL_ID:?FSE_UI_COGNITO_POOL_ID is not set}"
  client="${FSE_UI_OIDC_CLIENT_ID:?FSE_UI_OIDC_CLIENT_ID is not set}"
  secret="$(aws cognito-idp describe-user-pool-client --region "$AWS_REGION" \
    --user-pool-id "$pool" --client-id "$client" \
    --query UserPoolClient.ClientSecret --output text 2>/dev/null)" ||
    fail "aws cognito-idp describe-user-pool-client failed"
  if [[ -z "$secret" || "$secret" == "None" || ! "$secret" =~ ^[A-Za-z0-9+/=_-]+$ ]]; then
    fail "the app client has no usable client secret"
  fi

  token="$(curl -sf -X PUT "$imds/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null)" ||
    fail "curl IMDSv2 token failed"
  instance_id="$(curl -sf -H "X-aws-ec2-metadata-token: $token" \
    "$imds/meta-data/instance-id" 2>/dev/null)" ||
    fail "curl IMDSv2 instance-id failed"
  if [[ ! "$instance_id" =~ ^i-[0-9a-f]{8,17}$ ]]; then
    fail "IMDS returned an invalid instance id"
  fi

  printf '%s=%s\n' \
    FSE_UI_OIDC_CLIENT_SECRET "$secret" \
    FSE_UI_RUNNER_INSTANCE_ID "$instance_id" \
    FSE_UI_IMAGE_TAG "$tag" >"$tmp"
else
  site="${FSE_EDGE_SITE:?FSE_EDGE_SITE is not set}"
  zone="${FSE_EDGE_ZONE_ID:?FSE_EDGE_ZONE_ID is not set}"
  edge_region="${FSE_EDGE_AWS_REGION:-$AWS_REGION}"
  secret="$(aws ssm get-parameter --region "$AWS_REGION" \
    --name "$FSE_SSM_PREFIX/ui/origin_secret" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null)" ||
    fail "aws ssm get-parameter $FSE_SSM_PREFIX/ui/origin_secret failed"
  if [[ ! "$secret" =~ ^[A-Za-z0-9]{32,}$ ]]; then
    fail "origin secret is not set"
  fi

  printf '%s=%s\n' \
    ORIGIN_SECRET "$secret" \
    FSE_EDGE_SITE "$site" \
    FSE_EDGE_ZONE_ID "$zone" \
    FSE_EDGE_AWS_REGION "$edge_region" >"$tmp"
fi

chmod 600 "$tmp"
mv -f "$tmp" "$FSE_RUN_DIR/env"
printf '%s\n' "$tag" >"$FSE_RUN_DIR/image_tag"
