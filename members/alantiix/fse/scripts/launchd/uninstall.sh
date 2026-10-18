#!/usr/bin/env bash
# Stop and unload the paper forward test launchd jobs.
#
#   scripts/launchd/uninstall.sh [--apply]
#
#   (default)  dry run: print what is loaded and the commands, change nothing
#   --apply    `launchctl bootout gui/$UID/<label>` for each loaded job (fse run gets
#              SIGTERM, saves its state and exits 0), then remove the rendered plists
#              from ~/.skylit-fse/launchd/
set -euo pipefail

DEST_DIR="$HOME/.skylit-fse/launchd"
DOMAIN="gui/$(id -u)"
LABELS=("com.skylit.fse-run")

apply=0
case "${1:-}" in
  --apply) apply=1 ;;
  ""|--dry-run) apply=0 ;;
  -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  *) echo "uninstall.sh: error: unknown option $1" >&2; exit 2 ;;
esac

for label in "${LABELS[@]}"; do
  if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
    loaded=1
  else
    loaded=0
  fi
  if (( ! apply )); then
    if (( loaded )); then
      echo "launchctl bootout $DOMAIN/$label   # loaded"
    else
      echo "# $label is not loaded"
    fi
    if [[ -f "$DEST_DIR/$label.plist" ]]; then echo "rm -f '$DEST_DIR/$label.plist'"; fi
    continue
  fi
  if (( loaded )); then
    launchctl bootout "$DOMAIN/$label"
    echo "unloaded $label"
  fi
  rm -f "$DEST_DIR/$label.plist"
done
if (( ! apply )); then echo "# dry run: add --apply to do it"; fi
