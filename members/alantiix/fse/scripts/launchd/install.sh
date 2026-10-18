#!/usr/bin/env bash
# Render and (with --apply) load the paper forward test launchd jobs.
#
#   scripts/launchd/install.sh [--apply] [--until ISO_INSTANT] [--log-dir DIR]
#
#   (default)      dry run: render the plists to a temp dir, lint them, print them and the
#                  launchctl commands, change nothing
#   --apply        write ~/.skylit-fse/launchd/<label>.plist and
#                  `launchctl bootstrap gui/$UID <plist>`: this STARTS the run (see the
#                  com.skylit.fse-run template comment)
#   --until X      passed to fse run (e.g. 2026-10-16T17:10:00-04:00); without it the run
#                  goes on until scripts/launchd/uninstall.sh --apply
#   --log-dir DIR  default ~/.skylit-fse/logs
#
# The plists are never copied to ~/Library/LaunchAgents, so nothing starts at login.
set -euo pipefail
shopt -u patsub_replacement 2>/dev/null || true  # bash >= 5.2: '&' stays literal below

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$(cd "$HERE/../.." && pwd)"
RUN_LABEL="com.skylit.fse-run"
DEST_DIR="$HOME/.skylit-fse/launchd"
LOG_DIR="$HOME/.skylit-fse/logs"
DOMAIN="gui/$(id -u)"

apply=0
until_arg=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=1 ;;
    --dry-run) apply=0 ;;
    --until) until_arg="${2:?--until needs a value}"; shift ;;
    --until=*) until_arg="${1#--until=}" ;;
    --log-dir) LOG_DIR="${2:?--log-dir needs a value}"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "install.sh: error: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ -n "$until_arg" && ! "$until_arg" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$ ]]; then
  echo "install.sh: error: --until $until_arg is not an ISO 8601 instant with an offset" >&2
  exit 2
fi
for p in "$ENGINE_DIR" "$LOG_DIR" "$DEST_DIR"; do
  if [[ "$p" == *[\<\>\&\"]* ]]; then
    echo "install.sh: error: path $p holds an XML special character" >&2
    exit 2
  fi
done

render() {  # template label run_args -> stdout
  local text
  text="$(cat "$1")"
  text="${text//@LABEL@/$2}"
  text="${text//@ENGINE_DIR@/$ENGINE_DIR}"
  text="${text//@LOG_DIR@/$LOG_DIR}"
  text="${text//$'\n'@RUN_ARGS@/$3}"  # the whole line; none left without args
  printf '%s\n' "$text"
}

run_args=""
if [[ -n "$until_arg" ]]; then
  run_args=$'\n'"    <string>--until</string>"$'\n'"    <string>$until_arg</string>"
fi

jobs=("$RUN_LABEL")

template_of() { echo "$HERE/com.skylit.fse-run.plist.template"; }
args_of() { if [[ "$1" == "$RUN_LABEL" ]]; then printf '%s' "$run_args"; fi; }

# preflight (reported in a dry run, enforced with --apply)
problems=()
if [[ ! -f "$ENGINE_DIR/.env" ]]; then
  problems+=("no $ENGINE_DIR/.env")
else
  mode="$(stat -f '%Lp' "$ENGINE_DIR/.env" 2>/dev/null || stat -c '%a' "$ENGINE_DIR/.env")"
  [[ "$mode" == "600" ]] || problems+=(".env has mode $mode, not 600 (chmod 600 .env)")
fi
[[ -x "$ENGINE_DIR/.venv/bin/fse" ]] || problems+=("no $ENGINE_DIR/.venv/bin/fse")
for label in "${jobs[@]}"; do
  if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
    problems+=("$label is already loaded (scripts/launchd/uninstall.sh --apply first)")
  fi
done

if (( ! apply )); then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  echo "# dry run (nothing is written or loaded; add --apply to do it)"
  echo "# engine dir: $ENGINE_DIR"
  echo "# log dir:    $LOG_DIR"
  for label in "${jobs[@]}"; do
    render "$(template_of "$label")" "$label" "$(args_of "$label")" > "$tmp/$label.plist"
    plutil -lint "$tmp/$label.plist" | sed 's|^.*: |# plutil: |'
    echo "# ---- $DEST_DIR/$label.plist"
    cat "$tmp/$label.plist"
  done
  echo "# commands --apply would run:"
  echo "mkdir -p '$DEST_DIR' '$LOG_DIR'"
  for label in "${jobs[@]}"; do
    echo "launchctl bootstrap $DOMAIN '$DEST_DIR/$label.plist'"
  done
  if [[ ${#problems[@]} -gt 0 ]]; then
    for p in "${problems[@]}"; do echo "# preflight: $p"; done
  else
    echo "# preflight: ok"
  fi
  exit 0
fi

if [[ ${#problems[@]} -gt 0 ]]; then
  for p in "${problems[@]}"; do echo "install.sh: error: $p" >&2; done
  exit 2
fi
mkdir -p "$DEST_DIR" "$LOG_DIR"
chmod 700 "$DEST_DIR" "$LOG_DIR"
for label in "${jobs[@]}"; do
  dest="$DEST_DIR/$label.plist"
  render "$(template_of "$label")" "$label" "$(args_of "$label")" > "$dest"
  chmod 600 "$dest"
  plutil -lint "$dest" >/dev/null
  launchctl bootstrap "$DOMAIN" "$dest"
  echo "loaded $label ($dest)"
done
echo "status: launchctl print $DOMAIN/$RUN_LABEL | head -30; logs in $LOG_DIR"
