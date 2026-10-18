#!/usr/bin/env bash
# Keep the runner's Data_Cache (/data/cache) in step with s3://<bucket>/cache/.
#
#   fse-sync-cache.sh
#
# The runner reads only its own disk; pulls and seeding land in S3 (batch tasks,
# the Mac). Run at every fse-runner start (ExecStartPre=-, 20-console.conf) and
# every 30 minutes (fse-cache-sync.timer). Plugins reopen the cache at each
# Globex day open (18:00 ET), so a sync before then is what they see.
#
# Copies only what the plugins read, never the 20 GB of heatmaps or plugin_books:
#   bars/ equity_bars/ derived/ darkpool/ vix/ and catalog.sqlite.
# One sync per prefix: a filtered sync of cache/ would still list all 167,000
# objects (2 minutes of CPU) every time. The data folders go first and
# catalog.sqlite last, so the catalog never lists a session whose file has not
# arrived; the catalog is fetched only when its ETag changed. aws s3 downloads
# to a temporary name and renames, so a reader never sees a half-written file.
# Nothing is deleted.
#
# Environment (default in brackets):
#   FSE_DATA_BUCKET    bucket name [FSE_UI_DATA_BUCKET from $FSE_UI_ENV_FILE]
#   FSE_UI_ENV_FILE    [/etc/fse/ui.env]
#   FSE_CACHE_DIR      [/data/cache]
#   FSE_CACHE_OWNER    chown target for the container user [10001:10001]; empty skips
#   FSE_SYNC_TIMEOUT_S upper bound for one aws call [300]
#   FSE_LOCK_FILE      [/run/fse-cache-sync.lock]
#   FSE_CATALOG_ETAG_FILE  last catalog ETag [<parent of FSE_CACHE_DIR>/.fse-cache-catalog.etag]
#   AWS_REGION         [us-east-1]
# Exit 1 when the bucket is unknown or a sync fails (the unit start ignores it).
set -euo pipefail
FSE_UI_ENV_FILE="${FSE_UI_ENV_FILE:-/etc/fse/ui.env}"
FSE_CACHE_DIR="${FSE_CACHE_DIR:-/data/cache}"
FSE_CACHE_OWNER="${FSE_CACHE_OWNER-10001:10001}"
FSE_SYNC_TIMEOUT_S="${FSE_SYNC_TIMEOUT_S:-300}"
FSE_LOCK_FILE="${FSE_LOCK_FILE:-/run/fse-cache-sync.lock}"
FSE_CATALOG_ETAG_FILE="${FSE_CATALOG_ETAG_FILE:-$(dirname "$FSE_CACHE_DIR")/.fse-cache-catalog.etag}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION
log() { echo "fse-sync-cache: $*"; }
bucket="${FSE_DATA_BUCKET:-}"
if [[ -z "$bucket" && -r "$FSE_UI_ENV_FILE" ]]; then
  bucket="$(sed -n 's/^FSE_UI_DATA_BUCKET=//p' "$FSE_UI_ENV_FILE" | tail -n 1)"
fi
if [[ ! "$bucket" =~ ^[a-z0-9][a-z0-9.-]{2,62}$ ]]; then
  log "no data bucket (set FSE_DATA_BUCKET or FSE_UI_DATA_BUCKET in $FSE_UI_ENV_FILE)"
  exit 1
fi
# One sync at a time: the timer and a runner start can overlap.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$FSE_LOCK_FILE" || exit 1
  if ! flock -n 9; then
    log "another sync is running; skipping"
    exit 0
  fi
fi
limit=()
if command -v timeout >/dev/null 2>&1; then
  limit=(timeout "$FSE_SYNC_TIMEOUT_S")
fi
run_aws() { ${limit[@]+"${limit[@]}"} aws "$@"; }
mkdir -p "$FSE_CACHE_DIR" || exit 1
src="s3://$bucket/cache/"
rc=0
for sub in bars equity_bars derived darkpool vix; do
  mkdir -p "$FSE_CACHE_DIR/$sub" || exit 1
  run_aws s3 sync "$src$sub/" "$FSE_CACHE_DIR/$sub/" \
    --exclude "*.DS_Store" --only-show-errors || rc=$?
done
if ((rc == 0)); then
  etag=""
  run_aws s3api head-object --bucket "$bucket" --key cache/catalog.sqlite \
    --query ETag --output text >"$FSE_CATALOG_ETAG_FILE.new" || rc=$?
  if ((rc == 0)); then
    etag="$(cat "$FSE_CATALOG_ETAG_FILE.new")"
    last=""
    [[ -f "$FSE_CATALOG_ETAG_FILE" ]] && last="$(cat "$FSE_CATALOG_ETAG_FILE")"
    if [[ ! -f "$FSE_CACHE_DIR/catalog.sqlite" || "$etag" != "$last" ]]; then
      part="$FSE_CACHE_DIR/.catalog.sqlite.part"
      if run_aws s3 cp "${src}catalog.sqlite" "$part" --only-show-errors \
        && mv -f "$part" "$FSE_CACHE_DIR/catalog.sqlite"; then
        printf '%s\n' "$etag" >"$FSE_CATALOG_ETAG_FILE"
      else
        rc=1
        rm -f "$part"
      fi
    fi
  fi
  rm -f "$FSE_CATALOG_ETAG_FILE.new"
fi
if [[ -n "$FSE_CACHE_OWNER" ]]; then
  chown -R "$FSE_CACHE_OWNER" "$FSE_CACHE_DIR" || log "chown $FSE_CACHE_OWNER failed"
fi
if ((rc != 0)); then
  log "sync from $src failed (exit $rc); the cache on disk is unchanged or partly updated"
  exit 1
fi
log "synced $src to $FSE_CACHE_DIR"
