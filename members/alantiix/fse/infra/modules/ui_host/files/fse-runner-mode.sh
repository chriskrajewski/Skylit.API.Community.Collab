#!/usr/bin/env bash
# fse-runner.service ExecStartPre (20-console.conf): runner mode, config and
# the plugin-orders switch.
#
#   fse-runner-mode.sh
#
# Reads /fse/runner_mode and /fse/runner_config (written by the console) and
# /fse/control/plugin_orders (written by Terraform only) in one get-parameters
# call. Missing parameters default to run and configs/runner.yaml. The mode
# must be run or paper; the config must match ^[A-Za-z0-9_./~-]{1,300}$ with
# no .. segment. Anything else exits 1, so the unit start fails and alerts.
# Rewrites $FSE_ENV_DIR/env (left by fse-fetch-secrets.sh) atomically with
# FSE_RUNNER_MODE= and FSE_RUNNER_CONFIG= appended and every
# FSE_RUN_PLUGIN_ORDERS= line dropped (the runner image ignores that variable).
# Writes $FSE_CONTROL_DIR/plugin_orders atomically (dir 0755, file 0644):
# `enabled` only when the parameter is exactly enabled, else `disabled` (a
# missing parameter silently; any other value with a line on stderr).
# fse-console-start.sh mounts the directory read-only at /run/fse-control.
#
# Environment (default in brackets; systemd loads /etc/fse/host.env):
#   FSE_ENV_DIR      [/run/fse]
#   FSE_CONTROL_DIR  [/run/fse-control]
#   FSE_SSM_PREFIX   parameter path prefix [/fse]
#   AWS_REGION       [us-east-1]
set -euo pipefail

FSE_ENV_DIR="${FSE_ENV_DIR:-/run/fse}"
FSE_CONTROL_DIR="${FSE_CONTROL_DIR:-/run/fse-control}"
FSE_SSM_PREFIX="${FSE_SSM_PREFIX:-/fse}"
AWS_REGION="${AWS_REGION:-us-east-1}"

fail() {
  echo "fse-runner-mode: $*" >&2
  exit 1
}

env_file="$FSE_ENV_DIR/env"
[[ -f "$env_file" ]] || fail "$env_file is missing (fse-fetch-secrets.sh runs first)"

json="$(aws ssm get-parameters --region "$AWS_REGION" \
  --names "$FSE_SSM_PREFIX/runner_mode" "$FSE_SSM_PREFIX/runner_config" \
  "$FSE_SSM_PREFIX/control/plugin_orders" \
  --output json 2>/dev/null)" ||
  fail "aws ssm get-parameters failed"

# Three lines: mode, config (empty when the parameter is missing) and the
# switch as enabled, disabled, missing or other (never the raw value).
values="$(printf '%s' "$json" | python3 -c '
import json, sys
prefix = sys.argv[1]
try:
    params = {p["Name"]: p.get("Value", "") for p in json.load(sys.stdin).get("Parameters", [])}
except (ValueError, KeyError, TypeError, AttributeError):
    sys.exit("fse-runner-mode: cannot parse the get-parameters output")
out = [params.get(prefix + "/runner_mode", ""), params.get(prefix + "/runner_config", "")]
if any("\n" in v or "\r" in v for v in out):
    sys.exit("fse-runner-mode: a parameter holds a newline")
switch = params.get(prefix + "/control/plugin_orders")
if switch is None:
    state = "missing"
elif switch in ("enabled", "disabled"):
    state = switch
else:
    state = "other"
sys.stdout.write(out[0] + "\n" + out[1] + "\n" + state + "\n")
' "$FSE_SSM_PREFIX")" || exit 1

mode="$(printf '%s\n' "$values" | sed -n 1p)"
config="$(printf '%s\n' "$values" | sed -n 2p)"
switch="$(printf '%s\n' "$values" | sed -n 3p)"
mode="${mode:-run}"
config="${config:-configs/runner.yaml}"

case "$mode" in
  run | paper) ;;
  *) fail "$FSE_SSM_PREFIX/runner_mode must be run or paper" ;;
esac
# Length checked apart: BSD regex caps {m,n} at 255.
if ((${#config} > 300)) || [[ ! "$config" =~ ^[A-Za-z0-9_./~-]+$ ]]; then
  fail "$FSE_SSM_PREFIX/runner_config is not a valid config path"
fi
case "/$config/" in
  */../*) fail "$FSE_SSM_PREFIX/runner_config must not contain a .. segment" ;;
esac

plugin_orders=disabled
case "$switch" in
  enabled) plugin_orders=enabled ;;
  disabled | missing) ;;
  *) echo "fse-runner-mode: plugin_orders is not enabled|disabled; plugin orders off" >&2 ;;
esac

umask 077
tmp="$FSE_ENV_DIR/env.mode.tmp"
control_tmp="$FSE_CONTROL_DIR/plugin_orders.tmp"
trap 'rm -f "$tmp" "$control_tmp"' EXIT
# Keep every other line; drop earlier mode lines so a re-run stays idempotent.
# grep exits 1 when no line is left (fine) and 2 on a read error (fatal).
rc=0
grep -v -e '^FSE_RUNNER_MODE=' -e '^FSE_RUNNER_CONFIG=' -e '^FSE_RUN_PLUGIN_ORDERS=' \
  "$env_file" >"$tmp" || rc=$?
((rc <= 1)) || fail "cannot read $env_file"
printf 'FSE_RUNNER_MODE=%s\nFSE_RUNNER_CONFIG=%s\n' "$mode" "$config" >>"$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$env_file"

# The control file lives outside /data (which the console mounts read-write).
mkdir -p -m 0755 "$FSE_CONTROL_DIR"
chmod 0755 "$FSE_CONTROL_DIR"
printf '%s\n' "$plugin_orders" >"$control_tmp"
chmod 0644 "$control_tmp"
mv -f "$control_tmp" "$FSE_CONTROL_DIR/plugin_orders"
