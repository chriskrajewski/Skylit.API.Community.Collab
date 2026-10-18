#!/usr/bin/env bash
# Mail an fse-runner alert through SNS, at most once per reason per hour.
#
#   fse-alert.sh            # fse-runner.service ExecStopPost: uses $EXIT_STATUS
#   fse-alert.sh gave-up    # fse-runner-alert.service (OnFailure): restarts exhausted
#
# ExecStopPost runs after every stop; EXIT_STATUS 0 (or unset) publishes nothing,
# and neither does EXIT_CODE=killed with TERM/INT/HUP/PIPE (a clean stop to systemd).
# Exit codes: 1 feed/plugin fault, 2 invalid config or runner lock held,
# 3 blank credential or auth failure, 130 second SIGINT.
#
# Environment (default in brackets; systemd loads /etc/fse/host.env):
#   FSE_ALERT_TOPIC_ARN  SNS topic (required)
#   AWS_REGION           [us-east-1]
#   FSE_STAMP_DIR        dedupe stamps [/run/fse]
#   FSE_ENV_DIR          where fse-fetch-secrets.sh leaves not-deployed [/run/fse]
set -euo pipefail

FSE_STAMP_DIR="${FSE_STAMP_DIR:-/run/fse}"
FSE_ENV_DIR="${FSE_ENV_DIR:-/run/fse}"
AWS_REGION="${AWS_REGION:-us-east-1}"

meaning() {
  case "$1" in
    1) echo "feed or plugin fault" ;;
    2) echo "invalid config or runner lock held" ;;
    3) echo "blank credential or auth failure" ;;
    130) echo "second SIGINT" ;;
    *) echo "unexpected exit" ;;
  esac
}

if [[ "${1:-}" == "gave-up" ]]; then
  key="gave-up"
  last="$(systemctl show -p ExecMainStatus --value fse-runner.service 2>/dev/null || echo unknown)"
  message="fse-runner gave up after repeated failures; last exit ${last:-unknown}. Check: journalctl -u fse-runner"
else
  status="${EXIT_STATUS:-0}"
  if [[ "$status" == "0" ]]; then
    exit 0
  fi
  # A deliberate stop (deploy restart, patch reboot): systemd's TERM after ExecStop
  # can reach `docker run` before it returns fse's 0. systemd counts these as clean.
  if [[ "${EXIT_CODE:-}" == "killed" ]]; then
    case "$status" in
      TERM | INT | HUP | PIPE) exit 0 ;;
    esac
  fi
  key="exit-$status"
  if [[ -e "$FSE_ENV_DIR/not-deployed" ]]; then
    message="fse-runner not started: /fse/image_tag is none (no image deployed yet)"
  else
    message="fse-runner exited $status ($(meaning "$status"))"
  fi
fi

mkdir -p "$FSE_STAMP_DIR"
stamp="$FSE_STAMP_DIR/alert-$key.stamp"
if [[ -n "$(find "$stamp" -mmin -60 2>/dev/null)" ]]; then
  echo "fse-alert: already alerted for $key within the hour: $message" >&2
  exit 0
fi

aws sns publish --region "$AWS_REGION" --topic-arn "${FSE_ALERT_TOPIC_ARN:?FSE_ALERT_TOPIC_ARN is not set}" \
  --subject "fse-runner alert" --message "$message" --output text >/dev/null
touch "$stamp"
echo "fse-alert: sent: $message" >&2
