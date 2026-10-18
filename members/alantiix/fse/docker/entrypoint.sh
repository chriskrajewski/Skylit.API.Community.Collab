#!/usr/bin/env bash
# Entrypoint of the fse container image (tini is PID 1 and runs this script).
#
#   fse-entrypoint runner                       # the plugin runner (fse run or fse paper) on the /data volume
#   fse-entrypoint batch <fse args...>          # one fse command with its data synced from/to S3
#   fse-entrypoint batch-py <module> [args...]  # one allowlisted research module, synced from/to S3
#   fse-entrypoint seed-efs-cache               # S3→EFS full cache sync under the pull lock (reconcile/seed)
#   fse-entrypoint <command...>                 # anything else runs as given, e.g. `fse --help`
#
# Batch mode: with FSE_CACHE_BACKEND=efs (set by the fse-batch task def once EFS
# is applied) the Data_Cache lives on the EFS mount at ~/.skylit-fse/cache —
# download from S3 is skipped; writers still dual-write their prefixes to S3.
# Unset or FSE_CACHE_BACKEND=s3 uses scoped S3 sync (rollback / local / unwired).
# Plus runs/<id>/ for --run <id>, then uploads runs/. Only pull, pull-equity-bars
# and import-vix (which write cache, under the S3 pull lock) upload the prefixes
# they write. batch-py downloads the bars set and research/ (s3 mode), then
# uploads research/ and runs/ (never cache/). plugin_books/ is never synced unless
# FSE_CACHE_PREFIXES names it.
#
# Cache prefix sets (per-prefix sync, like fse-sync-cache.sh on the runner):
#   BARS_SET  bars equity_bars derived darkpool vix + catalog.sqlite (~70 MB)
#   HEAT_SET  BARS_SET + heatmaps
#   NONE      no cache download (accounts)
# Command map: accounts→NONE; report and batch-py→BARS_SET; backtest, calibrate,
# experiment, export-aws, pull→HEAT_SET; pull-equity-bars→equity_bars+catalog;
# import-vix→vix+catalog; unknown→HEAT_SET. A strategy-card task sets
# FSE_CACHE_BACKEND=s3 and FSE_CACHE_PREFIXES=bars,catalog.sqlite so it reads the
# seeded bars from S3 and does not download heatmaps or darkpool.
#
# Environment (default in brackets):
#   FSE_DATA_DIR          runner data volume [/data]
#   FSE_RUNNER_CONFIG     runner YAML, relative to /app [configs/runner.yaml]
#   FSE_RUNNER_MODE       runner only: run or paper [run]
#   FSE_RUN_PLUGIN_ORDERS runner only: ignored and unset. Plugin orders (fse run
#                         --plugin-orders and --live-orders) come only from the
#                         read-only host file /run/fse-control/plugin_orders holding
#                         exactly `enabled` (fse-console-start.sh mounts it;
#                         Terraform owns the switch). --live-orders is required for
#                         Combine; Practice ignores it.
#   FSE_LOCK_WAIT_S       seconds to wait for another runner to release its lock [180]
#   FSE_FAULT_BACKOFF_S   seconds to wait before exiting 2 or 3, so restarts stay slow [900]
#   FSE_PAPER_RETRY_S     paper only: seconds between fse paper sessions [300]
#   FSE_LOCAL_ROOT        the fse default root [$HOME/.skylit-fse]
#   DATA_BUCKET           batch only, required: the S3 bucket that holds cache/ and runs/
#   FSE_SYNC_CONFIGS      batch only: 1 downloads configs/ (then read-only) before the command
#   FSE_SYNC_UPLOADS      batch only: space-separated upload ids to download to ui/uploads/<id>/
#   FSE_SYNC_STATE        batch only: 1 copies state/holdout_log.jsonl down and back up
#   FSE_SYNC_PROGRESS_S   batch and batch-py: seconds between sync progress lines; 0 turns them off [30]
#   FSE_CACHE_PREFIXES    batch only: comma-separated cache prefixes to download
#                         instead of the command map (e.g. bars,heatmaps,catalog.sqlite);
#                         empty string skips cache download
#   FSE_CACHE_BACKEND     batch only: efs (skip cache download; writers still upload
#                         prefixes to S3) or s3 (scoped download+upload). Default s3
#                         when unset (local / pre-EFS). Task def sets efs once wired.
#                         Missing EFS mount with backend=efs exits 76.
#   FSE_LOCK_TTL_S        seed-efs-cache: replace locks/pull.lock when its started=
#                         time is at least this many seconds old [7200]. A missing
#                         or unreadable started= line is not replaced. Ignored when
#                         FSE_SEED_LOCK_EXTERNAL=1 (the caller already holds the lock).
#   FSE_SEED_LOCK_EXTERNAL  seed-efs-cache only: 1 means the caller (aws-seed-efs-cache.sh)
#                         already holds locks/pull.lock — skip acquire/release here.
#
# A secret variable whose value starts with PLACEHOLDER is exported blank, so a
# Terraform placeholder behaves like an unset credential (fse exits 3).
# SIGTERM and SIGINT are forwarded to fse as SIGTERM; batch mode still uploads afterwards.
set -euo pipefail

FSE_DATA_DIR="${FSE_DATA_DIR:-/data}"
FSE_RUNNER_CONFIG="${FSE_RUNNER_CONFIG:-configs/runner.yaml}"
FSE_LOCK_WAIT_S="${FSE_LOCK_WAIT_S:-180}"
FSE_FAULT_BACKOFF_S="${FSE_FAULT_BACKOFF_S:-900}"
: "${FSE_PAPER_RETRY_S:=300}"
FSE_LOCAL_ROOT="${FSE_LOCAL_ROOT:-$HOME/.skylit-fse}"
FSE_SYNC_PROGRESS_S="${FSE_SYNC_PROGRESS_S:-30}"
FSE_LOCK_TTL_S="${FSE_LOCK_TTL_S:-7200}"
# Plugin-orders switch: a literal path, never taken from the environment.
readonly FSE_CONTROL_FILE=/run/fse-control/plugin_orders

SECRET_NAMES="SKYLIT_API_KEY PROJECTX_USERNAME PROJECTX_API_KEY PRACTICE_ACCOUNT_ID
COMBINE_ACCOUNT_ID NOTIFIER_WEBHOOK_URL NARRATOR_API_KEY"

child=""     # pid of the running child (fse or the backoff sleep)
caught=0     # a signal arrived while waiting for the child
stopping=0   # a signal arrived at any time
run_id=""    # batch: the run a --run argument names (set by run_arg)
pull_lock_held=0  # seed-efs-cache: 1 after this process created locks/pull.lock
placed_args=()    # batch: argv after --out is placed under $FSE_LOCAL_ROOT/runs

log() {
  echo "fse-entrypoint: $*" >&2
}

on_signal() {
  caught=1
  stopping=1
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
  fi
}

# Run "$@" as a child so signals reach it; sets rc to its exit code.
run_child() {
  "$@" &
  child=$!
  rc=0
  wait "$child" || rc=$?
  # wait returns early (128+n) when a trapped signal arrives: wait again for the real code.
  while [[ "$caught" == 1 ]] && ((rc > 128)); do
    caught=0
    local again=0
    wait "$child" || again=$?
    if ((again != 127)); then # 127: already reaped, keep the code we have
      rc=$again
    fi
  done
  child=""
}

blank_placeholders() {
  local name value
  for name in $SECRET_NAMES; do
    value="${!name-}"
    case "$value" in
      PLACEHOLDER*) export "$name=" ;;
    esac
  done
}

fault_exit() {
  local code="$1" reason="$2"
  log "$reason; waiting ${FSE_FAULT_BACKOFF_S}s before exiting $code"
  if [[ "$stopping" == 0 ]]; then
    run_child sleep "$FSE_FAULT_BACKOFF_S"
  fi
  exit "$code"
}

# paper: fse paper runs one session; between sessions wait and retry. Never returns.
# rc 2 also covers "no session now"; the console validated the config (runner_target),
# and fse-runner-mode.sh validated the path before the container started.
paper_loop() {
  local d="$1"
  while :; do
    run_child fse paper --config "$FSE_RUNNER_CONFIG" --state-dir "$d/live-state" \
      --recordings-root "$d" --cache-dir "$d/cache"
    # docker stop during a session: keep fse's own code, as `fse run` does.
    if [[ "$stopping" == 1 ]]; then
      exit "$rc"
    fi
    case "$rc" in
      0 | 2)
        log "fse paper exited $rc (session ended or no session now); retrying in ${FSE_PAPER_RETRY_S}s"
        run_child sleep "$FSE_PAPER_RETRY_S"
        if [[ "$stopping" == 1 ]]; then
          exit 0
        fi
        ;;
      3) fault_exit 3 "fse paper exited 3 (blank credential or auth failure)" ;;
      *) exit "$rc" ;;
    esac
  done
}

# True when $1 is the empty directory tree baked into the engine image
# (cache, runs, configs, ui/uploads, research) and nothing else. A mount, a
# symlink, an empty directory, or any real file is not that tree.
image_placeholder_root() {
  local root="$1" path rel ok
  [[ -d "$root" && ! -L "$root" ]] || return 1
  if [[ -r /proc/mounts ]] && grep -F -q " ${root} " /proc/mounts; then
    return 1
  fi
  [[ -d "$root/cache" && -d "$root/runs" && -d "$root/configs" \
    && -d "$root/ui/uploads" && -d "$root/research" ]] || return 1
  ok=1
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    rel="${path#"$root"/}"
    case "$rel" in
      cache | runs | configs | research | ui | ui/uploads) ;;
      *) ok=0 ;;
    esac
    if [[ ! -d "$path" ]]; then
      ok=0
    fi
  done <<EOF
$(find "$root" -mindepth 1)
EOF
  [[ "$ok" == 1 ]]
}

runner() {
  local d="$FSE_DATA_DIR" sub mode="${FSE_RUNNER_MODE:-run}"
  # No inherited value can enable plugin orders: only the control file does (below).
  unset FSE_RUN_PLUGIN_ORDERS
  # Only run and paper: live (real orders) is never started by this image's runner.
  case "$mode" in
    run | paper) ;;
    *) fault_exit 2 "FSE_RUNNER_MODE=$mode is not allowed (run or paper)" ;;
  esac
  if [[ "$mode" == paper && ! "$FSE_PAPER_RETRY_S" =~ ^[0-9]+$ ]]; then
    fault_exit 2 "FSE_PAPER_RETRY_S=$FSE_PAPER_RETRY_S is not a whole number of seconds"
  fi
  for sub in live-state live-bars recordings runs logs cache; do
    mkdir -p "$d/$sub"
  done
  # fse defaults (and `docker exec ... fse halt`) use ~/.skylit-fse: point it at the volume.
  # The image's empty placeholder tree is not a data root; any other directory is.
  if image_placeholder_root "$FSE_LOCAL_ROOT"; then
    log "replacing image placeholder $FSE_LOCAL_ROOT with a symlink to $d"
    rm -rf "$FSE_LOCAL_ROOT"
  fi
  if [[ ! -e "$FSE_LOCAL_ROOT" && ! -L "$FSE_LOCAL_ROOT" ]]; then
    mkdir -p "$(dirname "$FSE_LOCAL_ROOT")"
    ln -sfn "$d" "$FSE_LOCAL_ROOT"
  fi
  local lock="$d/live-state/runner.lock"
  if command -v flock >/dev/null 2>&1; then
    # Acquire and release at once: wait for an old runner to let go, never hold it here.
    # Run as a child so a docker stop during the wait ends it at once.
    run_child flock -w "$FSE_LOCK_WAIT_S" "$lock" true
    if [[ "$stopping" == 0 ]] && ((rc != 0)); then
      fault_exit 2 "runner lock still held after ${FSE_LOCK_WAIT_S}s: $lock"
    fi
  else
    log "flock not found; skipping the runner lock wait (fse run still takes the lock)"
  fi
  if [[ "$stopping" == 1 ]]; then
    log "stopped before fse started"
    exit 143
  fi
  if [[ "$mode" == paper ]]; then
    paper_loop "$d"
  fi
  # --out names the run directory itself, so it is not passed: runs go to
  # <out_root>/run-<stamp>-<hex8>, and out_root is ~/.skylit-fse/runs on the volume.
  # Plugin orders: only an explicit run mode with a regular (non-symlink) control
  # file holding exactly `enabled`. The flags and the variable go to fse run only.
  # --live-orders travels with --plugin-orders so Combine can resolve; Practice
  # does not require it and ignores it.
  local plugin_args=() child_env=()
  if [[ "$mode" == run && -n "${FSE_RUNNER_MODE:-}" && -f "$FSE_CONTROL_FILE" && ! -L "$FSE_CONTROL_FILE" ]] &&
    [[ "$(head -c 16 "$FSE_CONTROL_FILE" 2>/dev/null)" == enabled ]]; then
    plugin_args=(--plugin-orders --live-orders)
    child_env=(FSE_RUN_PLUGIN_ORDERS=enabled)
    log "plugin orders: enabled by control/plugin_orders (live orders on for Combine)"
  fi
  run_child env ${child_env[@]+"${child_env[@]}"} fse run --config "$FSE_RUNNER_CONFIG" \
    --state-dir "$d/live-state" \
    --recordings-root "$d" \
    --bar-store "$d/live-bars" \
    --cache-dir "$d/cache" \
    ${plugin_args[@]+"${plugin_args[@]}"}
  case "$rc" in
    2) fault_exit 2 "fse run exited 2 (invalid config or runner lock held)" ;;
    3) fault_exit 3 "fse run exited 3 (blank credential or auth failure)" ;;
  esac
  exit "$rc"
}

# Set run_id to the bare run id given as --run ID or --run=ID, else empty.
# A path or a --runs-dir is left to fse: only ids under the default runs root are fetched.
run_arg() {
  local a prev="" value="" runs_dir=0
  run_id=""
  for a in "$@"; do
    case "$a" in
      --run=*) value="${a#--run=}" ;;
      --runs-dir | --runs-dir=*) runs_dir=1 ;;
    esac
    if [[ "$prev" == --run ]]; then
      value="$a"
    fi
    prev="$a"
  done
  case "$value" in
    "" | . | .. | -* | */*) return 0 ;;
  esac
  if [[ "$runs_dir" == 0 ]]; then
    run_id="$value"
  fi
}

require_bucket() {
  if [[ -z "${DATA_BUCKET:-}" ]]; then
    log "DATA_BUCKET is not set; batch mode needs the data bucket name"
    exit 2
  fi
}

# A profile with no credentials lets the default chain reach the ECS container
# provider, so `fse export-aws` (default --profile default) works in Fargate.
aws_profile_shim() {
  local file="$HOME/.aws/config"
  if [[ -n "${AWS_CONFIG_FILE+set}" || -e "$file" ]]; then
    return 0
  fi
  mkdir -p "$HOME/.aws"
  (
    umask 077
    printf '[profile default]\nregion = %s\n' "${AWS_REGION:-us-east-1}" >"$file"
  )
  chmod 600 "$file"
}

# An invalid FSE_SYNC_PROGRESS_S only costs the progress lines: warn and use 30.
check_progress_interval() {
  if [[ ! "$FSE_SYNC_PROGRESS_S" =~ ^[0-9]+$ ]]; then
    log "FSE_SYNC_PROGRESS_S=$FSE_SYNC_PROGRESS_S is not a whole number of seconds; using 30"
    FSE_SYNC_PROGRESS_S=30
  fi
}

# 7 -> 7s, 245 -> 4m05s, 3723 -> 1h02m03s
fmt_s() {
  local s="$1"
  if ((s < 60)); then
    echo "${s}s"
  elif ((s < 3600)); then
    printf '%dm%02ds\n' $((s / 60)) $((s % 60))
  else
    printf '%dh%02dm%02ds\n' $((s / 3600)) $((s % 3600 / 60)) $((s % 60))
  fi
}

# KiB -> "512 KB", "12.3 MB", "22.4 GB" (one decimal, integer arithmetic)
fmt_kb() {
  local kb="$1" unit=1024 name=MB
  if ((kb < 1024)); then
    echo "$kb KB"
    return 0
  fi
  if ((kb >= 1048576)); then
    unit=1048576
    name=GB
  fi
  local tenths=$((kb * 10 / unit))
  echo "$((tenths / 10)).$((tenths % 10)) $name"
}

# Size (KiB) and file count of directory $1, as "KB FILES"; vanishing temp files are ignored.
dir_usage() {
  local kb files
  kb=$(du -sk "$1" 2>/dev/null | { read -r k _ && echo "$k"; }) || true
  files=$(find "$1" -type f 2>/dev/null | wc -l) || true
  echo "${kb:-0} $((${files:-0}))"
}

# Read "OBJECTS BYTES" from an `aws s3 ls --summarize` output file once it is complete.
listed_totals() {
  local line objects="" bytes=""
  [[ -s "$1" ]] || return 1
  while read -r line; do
    case "$line" in
      "Total Objects: "*) objects="${line#Total Objects: }" ;;
      "Total Size: "*) bytes="${line#Total Size: }" ;;
    esac
  done < <(tail -n 2 "$1" 2>/dev/null)
  [[ "$objects" =~ ^[0-9]+$ && "$bytes" =~ ^[0-9]+$ ]] || return 1
  echo "$objects $bytes"
}

# Background progress printer for s3_sync: one line every FSE_SYNC_PROGRESS_S seconds.
# Never touches the caller's exit status; s3_sync kills and reaps it.
sync_ticker() {
  local dir="$1" label="$2" src="$3" start="$4" totals="$5" n="$FSE_SYNC_PROGRESS_S" s="" known=""
  local objects bytes kb files pct elapsed
  set +e
  trap 'if [[ -n "$s" ]]; then kill "$s" 2>/dev/null; fi; exit 0' TERM
  while :; do
    sleep "$n" &
    s=$!
    wait "$s"
    s=""
    elapsed=$(fmt_s $((SECONDS - start)))
    if [[ "$dir" == up ]]; then
      log "still uploading $label, $elapsed elapsed"
      continue
    fi
    read -r kb files < <(dir_usage "$dir")
    if [[ -z "$known" && -n "$totals" ]] && known=$(listed_totals "$totals"); then
      read -r objects bytes <<<"$known"
      log "$src holds $objects objects ($(fmt_kb $((bytes / 1024))))"
    fi
    if [[ -n "$known" ]]; then
      pct=0
      if ((bytes > 0)); then
        pct=$((kb * 1024 * 100 / bytes))
      fi
      if ((pct > 99)); then
        pct=99 # du counts blocks, so it can pass 100% before the sync ends
      fi
      log "downloading $label: $(fmt_kb "$kb") of $(fmt_kb $((bytes / 1024))) ($pct%)," \
        "$files of $objects files, $elapsed elapsed"
    else
      log "downloading $label: $(fmt_kb "$kb"), $files files, $elapsed elapsed"
    fi
  done
}

# aws s3 sync SRC DEST --only-show-errors, with start/done lines and periodic progress.
# Returns the sync's exit code; the caller decides what a failure means.
s3_sync() {
  local label="$1" src="$2" dest="$3" start="$SECONDS" rc=0 ticker="" lister="" totals=""
  local n="$FSE_SYNC_PROGRESS_S" kb files
  if [[ "$dest" == s3://* ]]; then
    log "uploading $label to $dest ..."
    if ((n > 0)); then
      sync_ticker up "$label" "$src" "$start" "" </dev/null &
      ticker=$!
    fi
  else
    log "downloading $label from $src ..."
    if ((n > 0)); then
      # Totals for the % come from a parallel listing; the sync never waits for it.
      if totals=$(mktemp "${TMPDIR:-/tmp}/fse-sync.XXXXXX" 2>/dev/null); then
        aws s3 ls "$src" --recursive --summarize >"$totals" 2>/dev/null </dev/null &
        lister=$!
      else
        totals=""
      fi
      sync_ticker "$dest" "$label" "$src" "$start" "$totals" </dev/null &
      ticker=$!
    fi
  fi
  aws s3 sync "$src" "$dest" --only-show-errors || rc=$?
  local pid
  for pid in $ticker $lister; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  if [[ -n "$totals" ]]; then
    rm -f "$totals"
  fi
  local elapsed
  elapsed=$(fmt_s $((SECONDS - start)))
  if ((rc != 0)); then
    if [[ "$dest" == s3://* ]]; then
      log "uploading $label failed after $elapsed (aws exit $rc)"
    else
      log "downloading $label failed after $elapsed (aws exit $rc)"
    fi
  elif [[ "$dest" == s3://* ]]; then
    log "uploaded $label in $elapsed"
  elif ((n > 0)); then
    read -r kb files < <(dir_usage "$dest")
    log "downloaded $label in $elapsed ($(fmt_kb "$kb"), $files files)"
  else
    log "downloaded $label in $elapsed"
  fi
  return "$rc"
}

# Download s3://$DATA_BUCKET/$1 to $2, or exit 1.
sync_down() {
  if ! s3_sync "$1" "s3://$DATA_BUCKET/$1" "$2"; then
    log "cannot download s3://$DATA_BUCKET/$1"
    exit 1
  fi
}

# Bars subset (~70 MB): same prefixes as fse-sync-cache.sh on the runner host.
# Heat set adds heatmaps/. Never include plugin_books/ in these defaults.
CACHE_BARS_DIRS="bars equity_bars derived darkpool vix"
CACHE_HEAT_DIRS="$CACHE_BARS_DIRS heatmaps"

# Parse FSE_CACHE_PREFIXES (comma-separated) into cache_dirs (space-separated) and
# cache_catalog (0/1). Empty override means none. Invalid names exit 2.
# Sets: cache_dirs, cache_catalog.
parse_cache_prefixes_env() {
  local raw="${FSE_CACHE_PREFIXES-}" item
  cache_dirs=""
  cache_catalog=0
  # Unset → caller uses the command map. Empty → none.
  if [[ -z "${FSE_CACHE_PREFIXES+x}" ]]; then
    return 1
  fi
  if [[ -z "$raw" ]]; then
    return 0
  fi
  raw="${raw//,/ }"
  for item in $raw; do
    case "$item" in
      catalog.sqlite | catalog)
        cache_catalog=1
        ;;
      bars | equity_bars | derived | darkpool | vix | heatmaps | plugin_books)
        cache_dirs="${cache_dirs:+$cache_dirs }$item"
        ;;
      *)
        log "invalid FSE_CACHE_PREFIXES entry: $item"
        exit 2
        ;;
    esac
  done
  return 0
}

# Set cache_dirs / cache_catalog for a batch fse command (download side).
# Sets cache_up_dirs / cache_up_catalog for writer uploads (empty when read-only).
cache_plan_for_cmd() {
  local cmd="$1"
  cache_up_dirs=""
  cache_up_catalog=0
  if parse_cache_prefixes_env; then
    # Override applies to download only; writers still upload their own prefixes.
    case "$cmd" in
      pull)
        cache_up_dirs="bars darkpool vix heatmaps"
        cache_up_catalog=1
        ;;
      pull-equity-bars)
        cache_up_dirs="equity_bars"
        cache_up_catalog=1
        ;;
      import-vix)
        cache_up_dirs="vix"
        cache_up_catalog=1
        ;;
    esac
    return 0
  fi
  case "$cmd" in
    accounts)
      cache_dirs=""
      cache_catalog=0
      ;;
    report)
      cache_dirs="$CACHE_BARS_DIRS"
      cache_catalog=1
      ;;
    pull-equity-bars)
      cache_dirs="equity_bars"
      cache_catalog=1
      cache_up_dirs="equity_bars"
      cache_up_catalog=1
      ;;
    import-vix)
      cache_dirs="vix"
      cache_catalog=1
      cache_up_dirs="vix"
      cache_up_catalog=1
      ;;
    pull)
      cache_dirs="$CACHE_HEAT_DIRS"
      cache_catalog=1
      cache_up_dirs="bars darkpool vix heatmaps"
      cache_up_catalog=1
      ;;
    backtest | calibrate | experiment | export-aws)
      cache_dirs="$CACHE_HEAT_DIRS"
      cache_catalog=1
      ;;
    *)
      # Unknown commands: heatmaps + bars, still skip plugin_books/.
      cache_dirs="$CACHE_HEAT_DIRS"
      cache_catalog=1
      ;;
  esac
}

# Bars set for every allowlisted batch-py module (no heatmaps).
cache_plan_for_batch_py() {
  cache_up_dirs=""
  cache_up_catalog=0
  if parse_cache_prefixes_env; then
    return 0
  fi
  cache_dirs="$CACHE_BARS_DIRS"
  cache_catalog=1
}

# ECS creates a missing mount-point parent as root. The image already owns
# ~/.skylit-fse as uid 10001, so a cache mount does not hide runs/. If that
# parent is still root-owned, say so and exit 1 (a mkdir failure is not a
# scan-secrets result).
ensure_work_dirs() {
  local root="$1" path err
  for path in "$root/cache" "$root/runs" "$root/configs" "$root/ui/uploads" "$root/research"; do
    if ! err=$(mkdir -p "$path" 2>&1); then
      log "error: cannot create $path (${err:-permission denied})"
      exit 1
    fi
  done
  if ! err=$(touch "$root/runs/.write-probe" 2>&1); then
    log "error: cannot write $root/runs (uid $(id -u): ${err:-permission denied})"
    exit 1
  fi
  rm -f "$root/runs/.write-probe"
}

# --out must live under $root/runs so the runs/ upload is the folder the API
# reads by directory name. A host path such as /data/runs/<name> is moved.
canonicalize_out() {
  local root="$1" path="$2" base dest
  case "$path" in
    "$root/runs" | "$root/runs"/*)
      printf '%s\n' "$path"
      return
      ;;
  esac
  base="${path##*/}"
  if [[ -z "$base" || "$base" == "." || "$base" == ".." ]]; then
    log "error: cannot place --out $path under $root/runs"
    exit 2
  fi
  dest="$root/runs/$base"
  log "placing --out $path at $dest"
  printf '%s\n' "$dest"
}

place_out() {
  local root="$1" arg path
  shift
  placed_args=()
  while (($#)); do
    arg="$1"
    shift
    if [[ "$arg" == --out ]]; then
      if (($# == 0)); then
        log "error: --out needs a path"
        exit 2
      fi
      path="$(canonicalize_out "$root" "$1")"
      shift
      placed_args+=(--out "$path")
    elif [[ "$arg" == --out=* ]]; then
      path="$(canonicalize_out "$root" "${arg#--out=}")"
      placed_args+=("--out=$path")
    else
      placed_args+=("$arg")
    fi
  done
}

# True when the lock body's started= line is at least FSE_LOCK_TTL_S old.
# A missing or unreadable started= line is not expired.
lock_body_expired() {
  local file="$1" raw started now ttl
  ttl="${FSE_LOCK_TTL_S:-7200}"
  if ! [[ "$ttl" =~ ^[0-9]+$ ]] || ((ttl <= 0)); then
    ttl=7200
  fi
  raw="$(sed -n 's/^started=//p' "$file" | head -n 1 | tr -d '\r')"
  if [[ -z "$raw" ]]; then
    return 1
  fi
  if ! started="$(date -u -d "$raw" +%s 2>/dev/null)"; then
    return 1
  fi
  now="$(date -u +%s)"
  ((now - started >= ttl))
}

# Drop locks/pull.lock only when this process created it (EXIT of seed-efs-cache).
# shellcheck disable=SC2329  # invoked from the EXIT/TERM traps
release_seed_lock() {
  if [[ "$pull_lock_held" != 1 ]]; then
    return 0
  fi
  pull_lock_held=0
  aws s3api delete-object --bucket "$DATA_BUCKET" --key "locks/pull.lock" >/dev/null ||
    log "warning: could not delete s3://$DATA_BUCKET/locks/pull.lock"
}

# True when cache should use S3 sync paths (download + the existing up map).
# FSE_CACHE_BACKEND=efs requires ~/.skylit-fse/cache to be a mountpoint (exit 76).
# Unset defaults to s3 so an unwired task def (no EFS volume yet) still works.
cache_uses_s3() {
  case "${FSE_CACHE_BACKEND:-s3}" in
    s3) return 0 ;;
    efs)
      cache_require_mount
      return 1
      ;;
    *)
      log "invalid FSE_CACHE_BACKEND=${FSE_CACHE_BACKEND} (efs|s3)"
      exit 2
      ;;
  esac
}

# Exit 76 when the EFS cache is not actually mounted. The container did start,
# so ECS never attached a volume here (a failed mount stops the task first).
cache_require_mount() {
  if cache_efs_mounted; then
    return 0
  fi
  log "error: FSE_CACHE_BACKEND=efs but $FSE_LOCAL_ROOT/cache is not a mountpoint"
  log "error: this task has no EFS volume at that path. Card backtests set FSE_CACHE_BACKEND=s3. Other batch tasks need the fse-cache volume from fse-infra, or the same s3 backend."
  exit 76
}

# EFS access-point mount at $FSE_LOCAL_ROOT/cache. Prefer mountpoint(1); fall
# back to /proc/self/mountinfo when util-linux is absent (slim image).
cache_efs_mounted() {
  local path="$FSE_LOCAL_ROOT/cache"
  if [[ ! -d "$path" ]]; then
    return 1
  fi
  if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q "$path"
    return $?
  fi
  awk -v p="$path" '$5 == p { found = 1 } END { exit !found }' /proc/self/mountinfo 2>/dev/null
}

# True when the fse argv already names a cache directory.
argv_has_cache_dir() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --cache-dir | --cache-dir=*) return 0 ;;
    esac
  done
  return 1
}

# A truncated catalog.sqlite is "disk I/O error" inside sqlite and exit 4, while
# another task's copy of the same object opens fine. Check size, the single-part
# ETag (a multipart ETag is not an MD5), and PRAGMA integrity_check.
catalog_matches_s3() {
  local file="$1" meta
  if ! meta=$(aws s3api head-object --bucket "$DATA_BUCKET" \
    --key "cache/catalog.sqlite" --output json); then
    log "cannot read catalog.sqlite object metadata"
    return 1
  fi
  CATALOG_META="$meta" python3 - "$file" <<'PY'
import hashlib
import json
import os
import sqlite3
import sys

path = sys.argv[1]
meta = json.loads(os.environ["CATALOG_META"])
size = os.path.getsize(path)
remote = int(meta["ContentLength"])
if size != remote:
    print(f"size {size} != s3 {remote}", file=sys.stderr)
    sys.exit(1)
etag = str(meta["ETag"]).strip('"')
if "-" not in etag:
    with open(path, "rb") as handle:
        digest = hashlib.md5(handle.read(), usedforsecurity=False).hexdigest()
    if digest != etag:
        print(f"etag {digest} != s3 {etag}", file=sys.stderr)
        sys.exit(1)
try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
except sqlite3.DatabaseError as exc:
    print(f"integrity_check: {exc}", file=sys.stderr)
    sys.exit(1)
if row is None or row[0] != "ok":
    detail = row[0] if row else "empty"
    print(f"integrity_check: {detail}", file=sys.stderr)
    sys.exit(1)
PY
}

# S3 cache download. A card task sets FSE_CACHE_BACKEND=s3 but the batch task
# definition still mounts EFS at $root/cache. Downloading into that mount lets
# the next card job replace catalog.sqlite and bar files under a replay that
# is already open. A mounted cache is left untouched; this task reads a private
# copy and the caller passes it as --cache-dir.
# Sets private_cache_dir when the download did not use the shared mount.
private_cache_dir=""
prepare_readonly_cache() {
  local root="$1"
  private_cache_dir=""
  if ! cache_uses_s3; then
    log "FSE_CACHE_BACKEND=efs; skipping cache S3 download"
    return 0
  fi
  if cache_efs_mounted; then
    private_cache_dir="$root/job-cache/cache"
    log "FSE_CACHE_BACKEND=s3 and $root/cache is mounted; downloading a private cache at $private_cache_dir"
    sync_cache_down "$root/job-cache"
    return 0
  fi
  sync_cache_down "$root"
}

# Download the planned cache prefixes one at a time (LIST stays scoped to each
# prefix). catalog.sqlite is a single-object cp, last, after the data dirs.
# A bad copy is deleted and retried; three failed checks exit 4 before fse.
sync_cache_down() {
  local root="$1" s3="s3://$DATA_BUCKET" sub
  for sub in $cache_dirs; do
    mkdir -p "$root/cache/$sub"
    if ! s3_sync "cache/$sub" "$s3/cache/$sub/" "$root/cache/$sub/"; then
      log "cannot download $s3/cache/$sub/"
      exit 1
    fi
  done
  if [[ "$cache_catalog" == 1 ]]; then
    log "downloading cache/catalog.sqlite from $s3/cache/catalog.sqlite ..."
    local start="$SECONDS" part="$root/cache/catalog.sqlite.part" dest="$root/cache/catalog.sqlite"
    local attempt=1 ok=0 downloaded=0 rc=0 detail
    while (( attempt <= 3 )); do
      rm -f "$part"
      rc=0
      aws s3 cp "$s3/cache/catalog.sqlite" "$part" --only-show-errors || rc=$?
      if (( rc != 0 )); then
        log "downloading cache/catalog.sqlite failed (attempt $attempt/3, aws exit $rc)"
        attempt=$((attempt + 1))
        continue
      fi
      downloaded=1
      detail=""
      if detail=$(catalog_matches_s3 "$part" 2>&1); then
        mv -f "$part" "$dest"
        ok=1
        break
      fi
      log "catalog.sqlite failed verification (attempt $attempt/3): ${detail}"
      attempt=$((attempt + 1))
    done
    rm -f "$part"
    if (( ok != 1 )); then
      if (( downloaded == 0 )); then
        log "downloading cache/catalog.sqlite failed after $(fmt_s $((SECONDS - start)))"
        log "cannot download $s3/cache/catalog.sqlite"
        exit 1
      fi
      log "error: catalog.sqlite is not a usable Data_Cache catalog after 3 attempts"
      exit 4
    fi
    log "downloaded cache/catalog.sqlite in $(fmt_s $((SECONDS - start)))"
  fi
}

# Upload only the prefixes a writer command may have changed (no --delete).
sync_cache_up() {
  local root="$1" s3="s3://$DATA_BUCKET" sub up_rc=0
  for sub in $cache_up_dirs; do
    if [[ ! -d "$root/cache/$sub" ]]; then
      continue
    fi
    s3_sync "cache/$sub" "$root/cache/$sub/" "$s3/cache/$sub/" || up_rc=$?
  done
  if [[ "$cache_up_catalog" == 1 && -f "$root/cache/catalog.sqlite" ]]; then
    log "uploading cache/catalog.sqlite to $s3/cache/catalog.sqlite ..."
    local start="$SECONDS" cp_rc=0
    aws s3 cp "$root/cache/catalog.sqlite" "$s3/cache/catalog.sqlite" --only-show-errors || cp_rc=$?
    if ((cp_rc != 0)); then
      log "uploading cache/catalog.sqlite failed after $(fmt_s $((SECONDS - start))) (aws exit $cp_rc)"
      up_rc=$cp_rc
    else
      log "uploaded cache/catalog.sqlite in $(fmt_s $((SECONDS - start)))"
    fi
  fi
  return "$up_rc"
}

# Console inputs: configs/ (read-only once down) and the uploads named in FSE_SYNC_UPLOADS.
sync_inputs() {
  local root="$1" id
  for id in ${FSE_SYNC_UPLOADS:-}; do
    if [[ ! "$id" =~ ^[a-f0-9]{16}$ ]]; then
      log "invalid upload id: $id"
      exit 2
    fi
  done
  if [[ "${FSE_SYNC_CONFIGS:-}" == 1 ]]; then
    mkdir -p "$root/configs"
    sync_down configs/ "$root/configs/"
    chmod -R a-w "$root/configs"
  fi
  for id in ${FSE_SYNC_UPLOADS:-}; do
    mkdir -p "$root/ui/uploads/$id"
    sync_down "uploads/$id/" "$root/ui/uploads/$id/"
  done
}

HOLDOUT_KEY="state/holdout_log.jsonl"

# FSE_SYNC_STATE=1: fetch the holdout log; a missing object (404) means none yet.
sync_state_down() {
  local root="$1" err
  if [[ "${FSE_SYNC_STATE:-}" != 1 ]]; then
    return 0
  fi
  if err=$(aws s3api head-object --bucket "$DATA_BUCKET" --key "$HOLDOUT_KEY" 2>&1 >/dev/null); then
    if aws s3 cp "s3://$DATA_BUCKET/$HOLDOUT_KEY" "$root/holdout_log.jsonl" --only-show-errors; then
      return 0
    fi
  elif [[ "$err" == *"(404)"* || "$err" == *"Not Found"* ]]; then
    log "no $HOLDOUT_KEY yet"
    return 0
  fi
  log "cannot download $HOLDOUT_KEY"
  exit 1
}

batch() {
  require_bucket
  if (($# == 0)); then
    log "usage: fse-entrypoint batch <fse command> [args...]"
    exit 2
  fi
  local root="$FSE_LOCAL_ROOT" s3="s3://$DATA_BUCKET" cmd="$1"
  local cache_dirs="" cache_catalog=0 cache_up_dirs="" cache_up_catalog=0
  check_progress_interval
  aws_profile_shim
  ensure_work_dirs "$root"
  cache_plan_for_cmd "$cmd"
  prepare_readonly_cache "$root"
  sync_inputs "$root"
  sync_state_down "$root"
  place_out "$root" "$@"
  set -- "${placed_args[@]}"
  if [[ -n "$private_cache_dir" ]] && ! argv_has_cache_dir "$@"; then
    set -- "$@" --cache-dir "$private_cache_dir"
  fi
  # report and experiment --run <id> read ~/.skylit-fse/runs/<id>: fetch that run.
  run_arg "$@"
  if [[ -n "$run_id" ]]; then
    if ! s3_sync "runs/$run_id/" "$s3/runs/$run_id/" "$root/runs/$run_id/"; then
      log "cannot download $s3/runs/$run_id/"
      exit 1
    fi
  fi
  if [[ "$stopping" == 1 ]]; then
    log "stopped before fse started"
    exit 143
  fi
  log "running: fse $*"
  local started="$SECONDS"
  run_child fse "$@"
  local fse_rc="$rc" sync_rc=0
  log "fse $cmd exited $fse_rc after $(fmt_s $((SECONDS - started)))"
  # Upload only (no --delete): always, after a normal exit and after SIGTERM.
  # Only pull, pull-equity-bars and import-vix upload the prefixes they write
  # (dual-write to S3 even on efs, so the runner bars sync and S3 mirror stay warm).
  # Other commands only read cache; re-uploading their copy could replace a
  # concurrent pull's files.
  if [[ -n "$cache_up_dirs" || "$cache_up_catalog" == 1 ]]; then
    sync_cache_up "$root" || sync_rc=$?
  fi
  s3_sync runs "$root/runs/" "$s3/runs/" || sync_rc=$?
  if [[ "${FSE_SYNC_STATE:-}" == 1 && -f "$root/holdout_log.jsonl" ]]; then
    aws s3 cp "$root/holdout_log.jsonl" "$s3/$HOLDOUT_KEY" --only-show-errors || sync_rc=$?
  fi
  if ((sync_rc != 0)); then
    log "upload to $s3 failed (aws exit $sync_rc)"
    if ((fse_rc == 0)); then
      exit 1
    fi
  fi
  exit "$fse_rc"
}

# S3→EFS full-tree reconcile (manual seed script + EventBridge every 6h).
# Holds locks/pull.lock so a concurrent pull cannot write mid-sync; if the lock
# is already held, exit 0 (skip) so the schedule never fights a live pull.
# When FSE_SEED_LOCK_EXTERNAL=1 the caller already holds the lock (manual seed
# script owns acquire/release so Spot/SIGTERM cannot leave it stuck).
seed_efs_cache() {
  require_bucket
  local root="$FSE_LOCAL_ROOT" s3="s3://$DATA_BUCKET"
  local lock_key="locks/pull.lock" lock_file="" sync_rc=0
  local external_lock=0
  case "${FSE_SEED_LOCK_EXTERNAL:-0}" in
    1 | true | yes) external_lock=1 ;;
  esac
  check_progress_interval
  aws_profile_shim
  ensure_work_dirs "$root"
  case "${FSE_CACHE_BACKEND:-s3}" in
    efs)
      cache_require_mount
      ;;
    s3)
      log "seed-efs-cache requires FSE_CACHE_BACKEND=efs (got ${FSE_CACHE_BACKEND:-s3}); apply EFS infra and re-register fse-batch"
      exit 2
      ;;
    *)
      log "invalid FSE_CACHE_BACKEND=${FSE_CACHE_BACKEND} (efs|s3)"
      exit 2
      ;;
  esac
  if [[ "$external_lock" == 1 ]]; then
    log "pull lock owned by caller (FSE_SEED_LOCK_EXTERNAL=1); skipping acquire"
  else
    # Release only the lock this process created, including when the task is killed.
    if ! lock_file=$(mktemp "${TMPDIR:-/tmp}/fse-pull-lock.XXXXXX" 2>/dev/null); then
      log "cannot create temp file for the pull lock"
      exit 1
    fi
    local lock_err="${lock_file}.err" holder="${lock_file}.holder"
    printf 'user=seed-efs-cache\nhost=%s\nstarted=%s\n' "$(hostname 2>/dev/null || echo unknown)" \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$lock_file"
    if aws s3api put-object --bucket "$DATA_BUCKET" --key "$lock_key" --if-none-match '*' \
      --body "$lock_file" >/dev/null 2>"$lock_err"; then
      pull_lock_held=1
    elif grep -Eq 'PreconditionFailed|ConditionalRequestConflict|412' "$lock_err"; then
      if aws s3api get-object --bucket "$DATA_BUCKET" --key "$lock_key" "$holder" >/dev/null 2>"$lock_err" \
        && lock_body_expired "$holder"; then
        log "pull lock s3://$DATA_BUCKET/$lock_key is past its ${FSE_LOCK_TTL_S}s TTL; replacing it"
        if ! aws s3api delete-object --bucket "$DATA_BUCKET" --key "$lock_key" >/dev/null 2>"$lock_err"; then
          cat "$lock_err" >&2 || true
          log "cannot delete expired pull lock s3://$DATA_BUCKET/$lock_key"
          rm -f "$lock_file" "$lock_err" "$holder"
          exit 1
        fi
        if aws s3api put-object --bucket "$DATA_BUCKET" --key "$lock_key" --if-none-match '*' \
          --body "$lock_file" >/dev/null 2>"$lock_err"; then
          pull_lock_held=1
        else
          cat "$lock_err" >&2 || true
          log "cannot acquire pull lock s3://$DATA_BUCKET/$lock_key after replacing an expired one"
          rm -f "$lock_file" "$lock_err" "$holder"
          exit 1
        fi
      else
        log "pull lock s3://$DATA_BUCKET/$lock_key is held; skipping S3→EFS reconcile"
        rm -f "$lock_file" "$lock_err" "$holder"
        exit 0
      fi
    else
      cat "$lock_err" >&2 || true
      log "cannot acquire pull lock s3://$DATA_BUCKET/$lock_key"
      rm -f "$lock_file" "$lock_err" "$holder"
      exit 1
    fi
    rm -f "$lock_file" "$lock_err" "$holder"
    # EXIT releases the lock; TERM/INT release it and still stop the child.
    trap 'release_seed_lock; on_signal' TERM INT
    trap release_seed_lock EXIT
  fi
  log "reconciling S3 cache → EFS at $root/cache ..."
  s3_sync cache "$s3/cache/" "$root/cache/" || sync_rc=$?
  if ((sync_rc != 0)); then
    log "S3→EFS cache reconcile failed (aws exit $sync_rc)"
    exit 1
  fi
  log "S3→EFS cache reconcile done"
  exit 0
}

# Allowlist of research modules batch-py may run: list yours here, space separated.
BATCH_PY_MODULES="research.example.run"

batch_py() {
  require_bucket
  if (($# == 0)); then
    log "usage: fse-entrypoint batch-py <module> [args...]"
    exit 2
  fi
  local module="$1" m allowed=0
  shift
  for m in $BATCH_PY_MODULES; do
    if [[ "$module" == "$m" ]]; then
      allowed=1
    fi
  done
  if [[ "$allowed" == 0 ]]; then
    log "unknown module: $module (allowed: $BATCH_PY_MODULES)"
    exit 2
  fi
  local root="$FSE_LOCAL_ROOT" s3="s3://$DATA_BUCKET"
  local cache_dirs="" cache_catalog=0 cache_up_dirs="" cache_up_catalog=0
  check_progress_interval
  aws_profile_shim
  ensure_work_dirs "$root"
  cache_plan_for_batch_py
  if cache_uses_s3; then
    sync_cache_down "$root"
  else
    log "FSE_CACHE_BACKEND=efs; skipping cache S3 download"
  fi
  sync_inputs "$root"
  sync_down research/ "$root/research/"
  if [[ "$stopping" == 1 ]]; then
    log "stopped before $module started"
    exit 143
  fi
  log "running: python -m $module${1+ $*}"
  local started="$SECONDS"
  run_child python -m "$module" ${1+"$@"}
  local py_rc="$rc" sync_rc=0
  log "$module exited $py_rc after $(fmt_s $((SECONDS - started)))"
  # Upload only (no --delete), always; cache/ is never uploaded from here.
  s3_sync research "$root/research/" "$s3/research/" || sync_rc=$?
  s3_sync runs "$root/runs/" "$s3/runs/" || sync_rc=$?
  if ((sync_rc != 0)); then
    log "upload to $s3 failed (aws exit $sync_rc)"
    if ((py_rc == 0)); then
      exit 1
    fi
  fi
  exit "$py_rc"
}

trap on_signal TERM INT
blank_placeholders

mode="${1:-runner}"
if (($# > 0)); then
  shift
fi
# ${1+"$@"}: bash 3.2 treats an empty "$@" as unbound under set -u.
case "$mode" in
  runner) runner ;;
  batch) batch ${1+"$@"} ;;
  batch-py) batch_py ${1+"$@"} ;;
  seed-efs-cache) seed_efs_cache ;;
  *)
    trap - TERM INT
    exec "$mode" ${1+"$@"}
    ;;
esac
