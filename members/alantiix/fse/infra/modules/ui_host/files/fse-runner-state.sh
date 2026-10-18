#!/usr/bin/env bash
# fse-runner.service ExecStartPost/ExecStopPost (20-console.conf): runner state.
#
#   fse-runner-state.sh started|stopped
#
# Writes $FSE_STATE_FILE atomically (mode 0644; the console reads it
# read-only): {"state": "running"|"stopped", "at": <epoch>, "exit_status": ...,
# "result": ...}. EXIT_STATUS and SERVICE_RESULT come from systemd; a value
# outside ^[A-Za-z0-9_-]{0,32}$ is written as an empty string. `started` also
# removes the console hold marker (runner-held next to the state file): a
# runner that starts (for example after a reboot) is no longer held.
#
# Environment (default in brackets):
#   FSE_STATE_FILE  [/var/lib/fse-host/runner-state.json]
set -euo pipefail

FSE_STATE_FILE="${FSE_STATE_FILE:-/var/lib/fse-host/runner-state.json}"

case "${1:-}" in
  started) state=running ;;
  stopped) state=stopped ;;
  *)
    echo "usage: fse-runner-state.sh started|stopped" >&2
    exit 2
    ;;
esac

clean() {
  if [[ "$1" =~ ^[A-Za-z0-9_-]{0,32}$ ]]; then
    printf '%s' "$1"
  fi
}

exit_status="$(clean "${EXIT_STATUS:-}")"
result="$(clean "${SERVICE_RESULT:-}")"
dir="$(dirname "$FSE_STATE_FILE")"
mkdir -p "$dir"

if [[ "$state" == "running" ]]; then
  rm -f "$dir/runner-held"
fi

tmp="$FSE_STATE_FILE.tmp"
trap 'rm -f "$tmp"' EXIT
umask 022
printf '{"state": "%s", "at": %s, "exit_status": "%s", "result": "%s"}\n' \
  "$state" "$(date +%s)" "$exit_status" "$result" >"$tmp"
chmod 644 "$tmp"
mv -f "$tmp" "$FSE_STATE_FILE"
