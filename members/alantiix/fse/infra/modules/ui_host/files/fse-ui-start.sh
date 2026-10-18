#!/usr/bin/env bash
# fse-ui.service ExecStart: pull the deployed fse-ui image and run the console API.
#
#   fse-ui-start.sh
#
# Reads the tag fse-ui-fetch-env.sh left in $FSE_RUN_DIR/image_tag. Host
# networking (binds 127.0.0.1:8080), uid 10001, read-only root, no Linux
# capabilities. /data is the fse data root; the root-owned host files in
# /var/lib/fse-host are mounted read-only on top of it at the settings paths.
# Logs go to CloudWatch ($FSE_UI_LOG_GROUP, stream ui).
#
# Environment (default in brackets; systemd loads /etc/fse/host.env and
# /etc/fse/ui.env):
#   FSE_UI_IMAGE_REPO  ECR repository URL of fse-ui (required)
#   FSE_RUN_DIR        [/run/fse-ui]
#   FSE_UI_LOG_GROUP   [/fse/ui]
#   AWS_REGION         [us-east-1]
set -euo pipefail

FSE_RUN_DIR="${FSE_RUN_DIR:-/run/fse-ui}"
FSE_UI_LOG_GROUP="${FSE_UI_LOG_GROUP:-/fse/ui}"
AWS_REGION="${AWS_REGION:-us-east-1}"
repo="${FSE_UI_IMAGE_REPO:?FSE_UI_IMAGE_REPO is not set}"

tag="$(cat "$FSE_RUN_DIR/image_tag")"
image="$repo:$tag"

docker pull --quiet "$image"

# exec: systemd tracks docker run as the main process.
exec docker run --rm --name fse-ui \
  --network host \
  --user 10001:10001 \
  --env-file /etc/fse/ui.env \
  --env-file "$FSE_RUN_DIR/env" \
  -v /data:/data \
  -v /var/lib/fse-host:/data/host:ro \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 768m \
  --pids-limit 256 \
  --log-driver awslogs \
  --log-opt "awslogs-region=$AWS_REGION" \
  --log-opt "awslogs-group=$FSE_UI_LOG_GROUP" \
  --log-opt awslogs-stream=ui \
  "$image"
