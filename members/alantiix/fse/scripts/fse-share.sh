#!/usr/bin/env bash
# fse-share.sh — share the Skylit fse cache (or any folders) as a password-protected 7z via S3.
#
# Usage:
#   ./fse-share.sh                         Share the fse cache (same as: build cache)
#   ./fse-share.sh build [PATH ...]        Share one or more folders/files in ONE archive
#   ./fse-share.sh PATH [PATH ...]         Shorthand for: build PATH ...
#   ./fse-share.sh presign <name.7z>       Print a fresh download link for an uploaded archive
#   ./fse-share.sh cleanup <name.7z>       Delete the archive from S3 and from this Mac
#
#   PATH can be any folder or file. The word "cache" means $FSE_HOME/cache.
#   Example: ./fse-share.sh cache ../some-other-folder
#
# Settings (override with environment variables):
#   AWS_PROFILE   AWS CLI profile            (default: default)
#   FSE_HOME      fse home folder            (default: ~/.skylit-fse)
#   BUCKET        S3 bucket                  (default: fse-share-<account id>)
#   REGION        AWS region                 (default: profile region, else us-east-1)
#   EXPIRES       Link lifetime in seconds   (default: 604800 = 7 days, the maximum)
#   KEEP_DAYS     Auto-delete shares/ after  (default: 14 days)

set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-default}"
FSE_HOME="${FSE_HOME:-$HOME/.skylit-fse}"
CACHE_DIR_NAME="cache"
SHARES_DIR="$FSE_HOME/shares"
EXPIRES="${EXPIRES:-604800}"
KEEP_DAYS="${KEEP_DAYS:-14}"
S3_PREFIX="shares"

# ---------- helpers ----------
log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_tools() {
  command -v aws >/dev/null 2>&1 || die "AWS CLI not found. Install with: brew install awscli"
  command -v openssl >/dev/null 2>&1 || die "openssl not found."
  if ! command -v 7zz >/dev/null 2>&1; then
    command -v brew >/dev/null 2>&1 || die "7zz not found and Homebrew is missing. Install 7-Zip manually."
    log "Installing 7-Zip (brew install sevenzip)"
    brew install sevenzip
  fi
}

aws_init() {
  log "Checking AWS profile '$AWS_PROFILE'"
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
    || die "AWS profile '$AWS_PROFILE' is not signed in. For SSO run: aws sso login --profile $AWS_PROFILE"
  REGION="${REGION:-$(aws configure get region 2>/dev/null || true)}"
  REGION="${REGION:-us-east-1}"
  BUCKET="${BUCKET:-fse-share-$ACCOUNT_ID}"
  echo "Account: $ACCOUNT_ID   Region: $REGION   Bucket: $BUCKET"

  if [ -n "$(aws configure get sso_start_url 2>/dev/null || true)" ] || \
     [ -n "$(aws configure get sso_session 2>/dev/null || true)" ]; then
    warn "Profile '$AWS_PROFILE' uses SSO. Download links stop working when your SSO session expires (often hours, not 7 days). Use an IAM user's access keys for a full 7-day link."
  fi
}

presign() {
  local name="$1"
  aws s3 presign "s3://$BUCKET/$S3_PREFIX/$name" --expires-in "$EXPIRES" --region "$REGION"
}

ensure_bucket() {
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "Bucket exists."
  else
    log "Creating bucket $BUCKET"
    aws s3 mb "s3://$BUCKET" --region "$REGION"
  fi

  log "Blocking all public access"
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

  log "Setting lifecycle: delete $S3_PREFIX/ after $KEEP_DAYS days, clean up failed uploads after 1 day"
  aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration "{
    \"Rules\": [{
      \"ID\": \"expire-shares\",
      \"Filter\": {\"Prefix\": \"$S3_PREFIX/\"},
      \"Status\": \"Enabled\",
      \"Expiration\": {\"Days\": $KEEP_DAYS},
      \"AbortIncompleteMultipartUpload\": {\"DaysAfterInitiation\": 1}
    }]
  }"
}

# Resolve a user-supplied path (or the keyword "cache") to an absolute path.
resolve_source() {
  local p="$1"
  [ "$p" = "cache" ] && p="$FSE_HOME/$CACHE_DIR_NAME"
  [ -e "$p" ] || die "Not found: $p"
  local parent base
  parent="$(cd "$(dirname "$p")" && pwd)" || die "Cannot access parent of $p"
  base="$(basename "$p")"
  [ "$base" != "/" ] && [ "$base" != "." ] && [ "$base" != ".." ] || die "Pass a named folder or file, not '$1'"
  printf '%s/%s\n' "${parent%/}" "$base"
}

# ---------- commands ----------
cmd_build() {
  [ "$#" -gt 0 ] || set -- cache

  # Resolve sources and reject duplicate top-level names (they would collide in the archive).
  local sources=() names=() src base n
  for arg in "$@"; do
    src="$(resolve_source "$arg")"
    base="$(basename "$src")"
    for n in ${names[@]+"${names[@]}"}; do
      [ "$n" != "$base" ] || die "Two sources share the name '$base'. Rename one or share them separately."
    done
    sources+=("$src")
    names+=("$base")
  done

  require_tools
  aws_init

  for src in "${sources[@]}"; do
    if [ "$src" = "$FSE_HOME/$CACHE_DIR_NAME" ] && [ ! -f "$src/catalog.sqlite" ]; then
      warn "catalog.sqlite not found in the cache."
    fi
  done
  mkdir -p "$SHARES_DIR"

  # Archive name: fse-<source name>-<timestamp>.7z, or fse-bundle-... for several sources.
  local label
  if [ "${#sources[@]}" -eq 1 ]; then
    label="$(printf '%s' "${names[0]}" | tr -c 'A-Za-z0-9._-' '-')"
  else
    label="bundle"
  fi
  local name
  name="fse-$label-$(date +%Y-%m-%d-%H%M).7z"
  local archive="$SHARES_DIR/$name"
  [ ! -e "$archive" ] || die "$archive already exists. Wait a minute or delete it."

  # Disk space check: archive is uncompressed (-mx=0), so it is about the size of the sources.
  local need_kb=0 free_kb source_files=0 kb count
  for src in "${sources[@]}"; do
    kb="$(du -sk "$src" | awk '{print $1}')"
    count="$(find "$src" -type f | wc -l | tr -d ' ')"
    echo "Source: $src   ($((kb / 1024)) MB, $count files)"
    need_kb=$((need_kb + kb))
    source_files=$((source_files + count))
  done
  free_kb="$(df -k "$SHARES_DIR" | awk 'NR==2 {print $4}')"
  echo "Total size: $((need_kb / 1024)) MB   Free space: $((free_kb / 1024)) MB"
  [ "$free_kb" -gt $((need_kb + need_kb / 10)) ] || die "Not enough free disk space for the archive."

  log "Generating password"
  local pw
  pw="$(openssl rand -base64 24)"

  log "Building $name (AES-256, encrypted file names, no compression)"
  # Add each source from its parent folder so it lands at the archive root under its own name.
  for src in "${sources[@]}"; do
    ( cd "$(dirname "$src")" && 7zz a -t7z -mx=0 -mhe=on -p"$pw" "$archive" "$(basename "$src")" -bb0 ) \
      || { rm -f "$archive"; die "7-Zip failed while adding $src"; }
  done

  log "Verifying archive"
  local test_out archived_files
  test_out="$(7zz t -p"$pw" "$archive")" || die "Archive test FAILED. Do not share $archive."
  echo "$test_out" | grep -q "Everything is Ok" || die "Archive test did not report 'Everything is Ok'."
  archived_files="$(echo "$test_out" | awk -F': *' '/^Files:/ {print $2}')"
  # 7-Zip omits the "Files:" line when the archive holds exactly one file.
  if [ -z "$archived_files" ] && [ "$source_files" = "1" ]; then archived_files=1; fi
  echo "Files in sources: $source_files   Files in archive: ${archived_files:-?}"
  [ "${archived_files:-0}" = "$source_files" ] || die "File count mismatch. Do not share $archive."

  ensure_bucket

  log "Uploading to s3://$BUCKET/$S3_PREFIX/$name"
  aws s3 cp "$archive" "s3://$BUCKET/$S3_PREFIX/$name" --region "$REGION"

  log "Creating download link (valid $((EXPIRES / 3600)) hours)"
  local url
  url="$(presign "$name")"

  if command -v pbcopy >/dev/null 2>&1; then
    printf '%s' "$pw" | pbcopy
    local clip_note="(also copied to your clipboard)"
  else
    local clip_note=""
  fi

  cat <<EOF

=====================================================================
 DONE
=====================================================================
Archive:  $archive
Contains: ${names[*]}
S3:       s3://$BUCKET/$S3_PREFIX/$name
Deletes:  automatically from S3 after $KEEP_DAYS days

PASSWORD $clip_note
  $pw

  -> Save it in your password manager now. It is not stored anywhere else.
  -> Send it over Signal (or similar), separately from the link.

DOWNLOAD LINK
  $url

RECIPIENT INSTRUCTIONS
  curl -C - -o fse.7z "<link>"
  Windows: open with 7-Zip.   Mac: Keka, or: 7zz x fse.7z

New link later:   $0 presign $name
Clean up after:   $0 cleanup $name
=====================================================================
EOF
}

cmd_presign() {
  local name="${1:-}"
  [ -n "$name" ] || die "Usage: $0 presign <name.7z>"
  aws_init
  presign "$name"
}

cmd_cleanup() {
  local name="${1:-}"
  [ -n "$name" ] || die "Usage: $0 cleanup <name.7z>"
  aws_init
  log "Deleting s3://$BUCKET/$S3_PREFIX/$name"
  aws s3 rm "s3://$BUCKET/$S3_PREFIX/$name" --region "$REGION"
  if [ -f "$SHARES_DIR/$name" ]; then
    log "Deleting local $SHARES_DIR/$name"
    rm -f "$SHARES_DIR/$name"
  fi
  echo "Cleanup complete."
}

case "${1:-build}" in
  build)   [ "$#" -gt 0 ] && shift; cmd_build "$@" ;;
  presign) shift; cmd_presign "$@" ;;
  cleanup) shift; cmd_cleanup "$@" ;;
  -h|--help|help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//' ;;
  -*) die "Unknown option '$1'. Use: build [PATH ...] | presign <name> | cleanup <name>" ;;
  *) cmd_build "$@" ;;
esac
