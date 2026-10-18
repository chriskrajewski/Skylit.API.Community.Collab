#!/usr/bin/env bash
# fse-runner.service ExecStartPre: fetch secrets and the image tag from SSM.
#
#   fse-fetch-secrets.sh
#
# Writes $FSE_ENV_DIR/env (NAME=value per /fse/secrets/<NAME>, mode 0600) for
# `docker run --env-file`, and $FSE_ENV_DIR/image_tag. A value starting with
# PLACEHOLDER is written blank, so fse sees an unset credential (exit 3).
# Exits 1 when /fse/image_tag is still `none` (no image deployed yet) and
# leaves $FSE_ENV_DIR/not-deployed so fse-alert.sh can say so.
#
# Environment (default in brackets; systemd loads /etc/fse/host.env):
#   FSE_ENV_DIR     output directory [/run/fse]
#   FSE_SSM_PREFIX  parameter path prefix [/fse]
#   AWS_REGION      region of the parameters [us-east-1]
set -euo pipefail

FSE_ENV_DIR="${FSE_ENV_DIR:-/run/fse}"
FSE_SSM_PREFIX="${FSE_SSM_PREFIX:-/fse}"
AWS_REGION="${AWS_REGION:-us-east-1}"

umask 077
mkdir -p "$FSE_ENV_DIR"
tmp="$FSE_ENV_DIR/env.tmp"
trap 'rm -f "$tmp"' EXIT

aws ssm get-parameters-by-path --region "$AWS_REGION" \
  --path "$FSE_SSM_PREFIX/secrets/" --with-decryption --output json |
  python3 -c '
import json, re, sys
params = json.load(sys.stdin).get("Parameters", [])
lines = []
for p in sorted(params, key=lambda p: p["Name"]):
    full = p["Name"]
    name = full.rsplit("/", 1)[-1]
    value = p.get("Value", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        sys.exit("fse-fetch-secrets: bad parameter name " + full)
    if "\n" in value or "\r" in value:
        sys.exit("fse-fetch-secrets: " + full + " holds a newline; --env-file cannot carry it")
    if value.startswith("PLACEHOLDER"):
        value = ""
    lines.append(name + "=" + value + "\n")
sys.stdout.write("".join(lines))
' >"$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$FSE_ENV_DIR/env"

tag="$(aws ssm get-parameter --region "$AWS_REGION" \
  --name "$FSE_SSM_PREFIX/image_tag" --query Parameter.Value --output text)"
if [[ -z "$tag" || "$tag" == "none" ]]; then
  touch "$FSE_ENV_DIR/not-deployed"
  echo "fse-fetch-secrets: $FSE_SSM_PREFIX/image_tag is none; no image deployed yet" >&2
  exit 1
fi
rm -f "$FSE_ENV_DIR/not-deployed"
printf '%s\n' "$tag" >"$FSE_ENV_DIR/image_tag"
echo "$tag"
