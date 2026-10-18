#!/usr/bin/env bash
# fse-runner.service ExecStart: pull the deployed image and run the runner.
#
#   fse-start.sh
#
# Reads the tag fse-fetch-secrets.sh left in $FSE_ENV_DIR/image_tag. The
# container gets the secrets from $FSE_ENV_DIR/env, the /data volume, no Linux
# capabilities, and logs to CloudWatch ($FSE_LOG_GROUP, stream runner).
#
# Environment (default in brackets; systemd loads /etc/fse/host.env):
#   FSE_IMAGE_REPO       ECR repository URL (required)
#   FSE_ENV_DIR          [/run/fse]
#   FSE_FAULT_BACKOFF_S  [900]
#   FSE_LOG_GROUP        [/fse/runner]
#   AWS_REGION           [us-east-1]
set -euo pipefail

FSE_ENV_DIR="${FSE_ENV_DIR:-/run/fse}"
FSE_FAULT_BACKOFF_S="${FSE_FAULT_BACKOFF_S:-900}"
FSE_LOG_GROUP="${FSE_LOG_GROUP:-/fse/runner}"
AWS_REGION="${AWS_REGION:-us-east-1}"
repo="${FSE_IMAGE_REPO:?FSE_IMAGE_REPO is not set}"

tag="$(cat "$FSE_ENV_DIR/image_tag")"
image="$repo:$tag"

docker pull --quiet "$image"

# exec: systemd tracks docker run as the main process; its exit code is fse's.
exec docker run --rm --name fse-runner \
  --env-file "$FSE_ENV_DIR/env" \
  -e "FSE_FAULT_BACKOFF_S=$FSE_FAULT_BACKOFF_S" \
  -v /data:/data \
  --log-driver awslogs \
  --log-opt "awslogs-region=$AWS_REGION" \
  --log-opt "awslogs-group=$FSE_LOG_GROUP" \
  --log-opt awslogs-stream=runner \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 1600m \
  "$image" runner
