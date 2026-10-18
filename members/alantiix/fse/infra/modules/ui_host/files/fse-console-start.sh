#!/usr/bin/env bash
# fse-runner.service ExecStart with the console (20-console.conf replaces
# Phase 1's fse-start.sh with this script through ExecStart= + ExecStart=).
#
#   fse-console-start.sh
#
# The same docker run as modules/runner/files/fse-start.sh plus one read-only
# mount: $FSE_CONTROL_DIR (written by fse-runner-mode.sh from
# /fse/control/plugin_orders) at /run/fse-control. The runner image enables
# plugin orders only when /run/fse-control/plugin_orders holds `enabled`; the
# Phase 1 script never mounts it, so without this drop-in plugin orders stay off.
#
# Environment (default in brackets; systemd loads /etc/fse/host.env):
#   FSE_IMAGE_REPO       ECR repository URL (required)
#   FSE_ENV_DIR          [/run/fse]
#   FSE_CONTROL_DIR      [/run/fse-control]
#   FSE_FAULT_BACKOFF_S  [900]
#   FSE_LOG_GROUP        [/fse/runner]
#   AWS_REGION           [us-east-1]
set -euo pipefail

FSE_ENV_DIR="${FSE_ENV_DIR:-/run/fse}"
FSE_CONTROL_DIR="${FSE_CONTROL_DIR:-/run/fse-control}"
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
  -v "$FSE_CONTROL_DIR:/run/fse-control:ro" \
  --log-driver awslogs \
  --log-opt "awslogs-region=$AWS_REGION" \
  --log-opt "awslogs-group=$FSE_LOG_GROUP" \
  --log-opt awslogs-stream=runner \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 1600m \
  "$image" runner
