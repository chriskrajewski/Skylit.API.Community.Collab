#!/usr/bin/env bash
# fse-edge.service ExecStart: pull the deployed fse-edge image and run Caddy on :443.
#
#   fse-edge-start.sh
#
# Reads the tag fse-ui-fetch-env.sh left in $FSE_RUN_DIR/image_tag. Host
# networking; root inside the container holds only NET_BIND_SERVICE (a
# non-root user cannot bind 443 under no-new-privileges). ACME state lives in
# /var/lib/fse-edge. Logs go to CloudWatch ($FSE_UI_LOG_GROUP, stream edge).
#
# Environment (default in brackets; systemd loads /etc/fse/host.env and
# /etc/fse/ui.env):
#   FSE_EDGE_IMAGE_REPO  ECR repository URL of fse-edge (required)
#   FSE_RUN_DIR          [/run/fse-edge]
#   FSE_UI_LOG_GROUP     [/fse/ui]
#   AWS_REGION           [us-east-1]
set -euo pipefail

FSE_RUN_DIR="${FSE_RUN_DIR:-/run/fse-edge}"
FSE_UI_LOG_GROUP="${FSE_UI_LOG_GROUP:-/fse/ui}"
AWS_REGION="${AWS_REGION:-us-east-1}"
repo="${FSE_EDGE_IMAGE_REPO:?FSE_EDGE_IMAGE_REPO is not set}"

tag="$(cat "$FSE_RUN_DIR/image_tag")"
image="$repo:$tag"

docker pull --quiet "$image"

# exec: systemd tracks docker run as the main process.
exec docker run --rm --name fse-edge \
  --network host \
  --user 0:0 \
  --env-file "$FSE_RUN_DIR/env" \
  -v /var/lib/fse-edge:/data \
  --read-only \
  --tmpfs /tmp \
  --tmpfs /config \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --security-opt no-new-privileges \
  --memory 128m \
  --pids-limit 128 \
  --log-driver awslogs \
  --log-opt "awslogs-region=$AWS_REGION" \
  --log-opt "awslogs-group=$FSE_UI_LOG_GROUP" \
  --log-opt awslogs-stream=edge \
  "$image"
