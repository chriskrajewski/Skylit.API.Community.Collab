#!/usr/bin/env bash
# Offload closed days of the data-plane recordings to S3 and prune them locally.
#
#   fse-offload-recordings.sh
#
# The runner records every snapshot (about 2.5 GB gz a day), so the 30 GB /data
# volume would fill in about 11 days. Run hourly (fse-offload-recordings.timer).
# Each file of a closed day (YYYY-MM-DD.jsonl.gz, YYYY-MM-DD.quotes.jsonl.gz,
# YYYY-MM-DD.part*.jsonl.gz with day < today in New York) that has not changed
# for FSE_OFFLOAD_MIN_AGE_MIN goes to s3://<bucket>/recordings/dataplane/<name>
# (skipped when S3 already has the same ContentLength) and is then verified
# with head-object. fse backtest-plugin --recordings-s3 reads it from there.
#
# Local copies are deleted only when verified: first every file of the days
# beyond the newest FSE_RECORDINGS_KEEP_DAYS closed days, then, while /data use
# is above FSE_OFFLOAD_DISK_PCT, the kept days oldest first. Today's file, a
# file written recently and an unverified file are never deleted. Nothing in
# S3 is deleted.
#
# Environment (default in brackets):
#   FSE_DATA_BUCKET          bucket name [FSE_UI_DATA_BUCKET from $FSE_UI_ENV_FILE]
#   FSE_UI_ENV_FILE          [/etc/fse/ui.env]
#   FSE_RECORDINGS_DIR       [/data/recordings/dataplane]
#   FSE_RECORDINGS_KEEP_DAYS closed days kept locally [2]
#   FSE_OFFLOAD_DISK_PCT     disk use (%) above which kept days go too [70]
#   FSE_OFFLOAD_MIN_AGE_MIN  minutes since the last write before a file is offloaded [30]
#   FSE_TODAY                today as YYYY-MM-DD [TZ=America/New_York date +%F] (tests)
#   FSE_DISK_USE_PCT         fixed disk use (%) [df -P $FSE_RECORDINGS_DIR] (tests)
#   FSE_SYNC_TIMEOUT_S       upper bound for one aws call [3600]
#   FSE_LOCK_FILE            [/run/fse-offload-recordings.lock]
#   AWS_REGION               [us-east-1]
# Exit 1 when the bucket is unknown or an upload or verify failed.
set -euo pipefail
FSE_UI_ENV_FILE="${FSE_UI_ENV_FILE:-/etc/fse/ui.env}"
FSE_RECORDINGS_DIR="${FSE_RECORDINGS_DIR:-/data/recordings/dataplane}"
FSE_RECORDINGS_KEEP_DAYS="${FSE_RECORDINGS_KEEP_DAYS:-2}"
FSE_OFFLOAD_DISK_PCT="${FSE_OFFLOAD_DISK_PCT:-70}"
FSE_OFFLOAD_MIN_AGE_MIN="${FSE_OFFLOAD_MIN_AGE_MIN:-30}"
FSE_TODAY="${FSE_TODAY:-$(TZ=America/New_York date +%F)}"
FSE_DISK_USE_PCT="${FSE_DISK_USE_PCT:-}"
FSE_SYNC_TIMEOUT_S="${FSE_SYNC_TIMEOUT_S:-3600}"
FSE_LOCK_FILE="${FSE_LOCK_FILE:-/run/fse-offload-recordings.lock}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION
log() { echo "fse-offload-recordings: $*"; }
bucket="${FSE_DATA_BUCKET:-}"
if [[ -z "$bucket" && -r "$FSE_UI_ENV_FILE" ]]; then
  bucket="$(sed -n 's/^FSE_UI_DATA_BUCKET=//p' "$FSE_UI_ENV_FILE" | tail -n 1)"
fi
if [[ ! "$bucket" =~ ^[a-z0-9][a-z0-9.-]{2,62}$ ]]; then
  log "no data bucket (set FSE_DATA_BUCKET or FSE_UI_DATA_BUCKET in $FSE_UI_ENV_FILE)"
  exit 1
fi
if [[ ! "$FSE_TODAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  log "FSE_TODAY is not YYYY-MM-DD: $FSE_TODAY"
  exit 1
fi
for n in "$FSE_RECORDINGS_KEEP_DAYS" "$FSE_OFFLOAD_DISK_PCT" "$FSE_OFFLOAD_MIN_AGE_MIN" "${FSE_DISK_USE_PCT:-0}"; do
  if [[ ! "$n" =~ ^[0-9]+$ ]]; then
    log "not a whole number: $n"
    exit 1
  fi
done
if [[ ! -d "$FSE_RECORDINGS_DIR" ]]; then
  log "no $FSE_RECORDINGS_DIR; nothing to offload"
  exit 0
fi
# One run at a time: an upload can outlast the hour.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$FSE_LOCK_FILE" || exit 1
  if ! flock -n 9; then
    log "another offload is running; skipping"
    exit 0
  fi
fi
limit=()
if command -v timeout >/dev/null 2>&1; then
  limit=(timeout "$FSE_SYNC_TIMEOUT_S")
fi
run_aws() { ${limit[@]+"${limit[@]}"} aws "$@"; }
prefix="recordings/dataplane"
# S3 ContentLength of a key, empty when absent or unreadable.
remote_size() {
  run_aws s3api head-object --bucket "$bucket" --key "$prefix/$1" \
    --query ContentLength --output text 2>/dev/null || true
}
disk_use() {
  if [[ -n "$FSE_DISK_USE_PCT" ]]; then
    echo "$FSE_DISK_USE_PCT"
  else
    df -P "$FSE_RECORDINGS_DIR" | awk 'NR == 2 { sub("%", "", $5); print $5 }'
  fi
}

name_re='^([0-9]{4}-[0-9]{2}-[0-9]{2})\.(jsonl|quotes\.jsonl|part[A-Za-z0-9._-]*\.jsonl)\.gz$'
closed=""   # "<day> <name>" lines, every closed-day file on disk
verified="|" # |name|name|... uploaded and ContentLength-checked in this run
failures=0
shopt -s nullglob
for path in "$FSE_RECORDINGS_DIR"/*.jsonl.gz; do
  name="${path##*/}"
  [[ -f "$path" && "$name" =~ $name_re ]] || continue
  day="${BASH_REMATCH[1]}"
  [[ "$day" < "$FSE_TODAY" ]] || continue
  closed+="$day $name"$'\n'
  if [[ -z "$(find "$path" -prune -mmin "+$FSE_OFFLOAD_MIN_AGE_MIN")" ]]; then
    log "skip $name: written in the last $FSE_OFFLOAD_MIN_AGE_MIN min"
    continue
  fi
  size="$(wc -c <"$path" | tr -d ' ')"
  if [[ "$(remote_size "$name")" == "$size" ]]; then
    log "skip upload of $name: in s3 with the same size ($size bytes)"
    verified+="$name|"
    continue
  fi
  if ! run_aws s3 cp "$path" "s3://$bucket/$prefix/$name" --only-show-errors; then
    log "upload of $name failed; keeping it"
    failures=$((failures + 1))
    continue
  fi
  got="$(remote_size "$name")"
  if [[ "$got" != "$size" ]]; then
    log "verify of $name failed (local $size bytes, s3 ${got:-none}); keeping it"
    failures=$((failures + 1))
    continue
  fi
  log "uploaded $name ($size bytes) to s3://$bucket/$prefix/"
  verified+="$name|"
done

delete_day() { # delete_day <day>: delete the day's verified files
  local line name
  while IFS= read -r line; do
    [[ "${line%% *}" == "$1" ]] || continue
    name="${line#* }"
    if [[ "$verified" == *"|$name|"* ]]; then
      rm -f "$FSE_RECORDINGS_DIR/$name"
      log "deleted $name (in s3)"
    else
      log "keep $name: not verified in s3"
    fi
  done <<<"$closed"
}

days="$(printf '%s' "$closed" | cut -d ' ' -f 1 | LC_ALL=C sort -r -u)"
kept=""
i=0
for day in $days; do
  if ((i < FSE_RECORDINGS_KEEP_DAYS)); then
    kept="$day $kept" # oldest first
  else
    delete_day "$day"
  fi
  i=$((i + 1))
done
for day in $kept; do
  use="$(disk_use || true)"
  [[ "$use" =~ ^[0-9]+$ ]] || use=0
  ((use > FSE_OFFLOAD_DISK_PCT)) || break
  log "disk use ${use}% > ${FSE_OFFLOAD_DISK_PCT}%: pruning kept day $day"
  delete_day "$day"
done
if ((failures > 0)); then
  log "$failures file(s) not offloaded; they stay on disk"
  exit 1
fi
log "done"
