#!/usr/bin/env bash
# The `fse run` wrapper of the paper forward test (launchd com.skylit.fse-run, or by hand).
#
#   scripts/run_paper.sh [fse run options, e.g. --until 2026-10-16T17:10:00-04:00]
#
# 1. Refuses to start (exit 78) unless .env exists with mode 600, .venv/bin/fse exists and
#    the config exists.
# 2. Exits 0 without starting when --until is already past (a KeepAlive restart or a stray
#    kickstart after the end must not start a new run).
# 3. Rotates the stdout/stderr logs under ~/.skylit-fse/logs/ by size at each start
#    (fse-run.out.log, fse-run.err.log; > FSE_LOG_MAX_BYTES, default 50 MiB, keep 5).
# 4. Sources .env (exported, nothing echoed), then
#    exec .venv/bin/fse run --config configs/runner.paper.yaml "$@".
#
# Environment: FSE_RUN_CONFIG (default configs/runner.paper.yaml), FSE_LOG_DIR,
# FSE_LOG_MAX_BYTES, FSE_LOG_KEEP, FSE_ENGINE_DIR (default: this script's engine dir).
set -euo pipefail
set +x  # never trace: .env values must not reach a log

ENGINE_DIR="${FSE_ENGINE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${FSE_LOG_DIR:-$HOME/.skylit-fse/logs}"
MAX_BYTES="${FSE_LOG_MAX_BYTES:-52428800}"
KEEP="${FSE_LOG_KEEP:-5}"
CONFIG="${FSE_RUN_CONFIG:-configs/runner.paper.yaml}"
EX_CONFIG=78

say() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) run_paper.sh: $*"; }
die() { local code=$1; shift; say "error: $*" >&2; exit "$code"; }

cd "$ENGINE_DIR" || die "$EX_CONFIG" "no engine dir $ENGINE_DIR"

file_mode() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"; }
file_size() { stat -f '%z' "$1" 2>/dev/null || stat -c '%s' "$1" 2>/dev/null || echo 0; }

[[ -f .env ]] || die "$EX_CONFIG" "no .env in $ENGINE_DIR (copy .env.example, chmod 600)"
mode="$(file_mode .env)"
[[ "$mode" == "600" ]] || die "$EX_CONFIG" ".env has mode $mode, not 600: run chmod 600 .env"
[[ -x .venv/bin/fse ]] || die "$EX_CONFIG" "no .venv/bin/fse in $ENGINE_DIR"
[[ -f "$CONFIG" ]] || die "$EX_CONFIG" "no config $CONFIG"

# --until already past: do not start (exit 0, so KeepAlive SuccessfulExit=false stops).
until_arg=""
prev=""
for a in "$@"; do
  if [[ "$prev" == "--until" ]]; then until_arg="$a"; fi
  if [[ "$a" == --until=* ]]; then until_arg="${a#--until=}"; fi
  prev="$a"
done
if [[ -n "$until_arg" ]]; then
  # 2026-10-16T17:10:00-04:00 -> 2026-10-16T17:10:00-0400 for BSD date; Z -> +0000
  norm="$(printf '%s' "$until_arg" | sed -E 's/\.[0-9]+//; s/Z$/+0000/; s/([+-][0-9]{2}):([0-9]{2})$/\1\2/')"
  if end_s="$(date -j -f '%Y-%m-%dT%H:%M:%S%z' "$norm" +%s 2>/dev/null)"; then
    if (( end_s <= $(date +%s) )); then
      say "--until $until_arg is past; not starting"
      exit 0
    fi
  fi  # unparsable here: fse run itself validates --until (exit 2)
fi

rotate() {
  local f=$1 i
  [[ -f "$f" ]] || return 0
  (( $(file_size "$f") > MAX_BYTES )) || return 0
  rm -f "$f.$KEEP"
  for (( i = KEEP - 1; i >= 1; i-- )); do
    if [[ -f "$f.$i" ]]; then mv -f "$f.$i" "$f.$((i + 1))"; fi
  done
  mv -f "$f" "$f.1"
}

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"
rotate "$LOG_DIR/fse-run.out.log"
rotate "$LOG_DIR/fse-run.err.log"
exec >>"$LOG_DIR/fse-run.out.log" 2>>"$LOG_DIR/fse-run.err.log"
say "starting fse run --config $CONFIG $*"

set -a
# shellcheck disable=SC1091
if ! . ./.env >/dev/null 2>&1; then
  set +a
  die "$EX_CONFIG" "cannot source .env as shell (quote values that hold spaces)"
fi
set +a

exec .venv/bin/fse run --config "$CONFIG" "$@"
