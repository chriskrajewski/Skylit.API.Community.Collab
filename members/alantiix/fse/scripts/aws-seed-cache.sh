#!/usr/bin/env bash
# Copy this Mac's Data_Cache to the data bucket once, so cloud batch tasks start warm.
#
# Run from this Mac, in the project folder, after the main stack is applied:
#
#   scripts/aws-seed-cache.sh --yes
#
# It refuses while an `fse pull` runs on this Mac (the cache would be copied mid-write),
# prints the cache size, then runs `aws s3 sync <cache>/ s3://$DATA_BUCKET/cache/`.
# A rerun only uploads what changed. Batch tasks then sync the cache down and back up.
#
# Environment (default in brackets):
#   AWS_PROFILE [default]  AWS_REGION [us-east-1]
#   DATA_BUCKET     data bucket name [terraform output data_bucket in infra/]
#   FSE_CACHE_DIR   local cache [~/.skylit-fse/cache]
set -euo pipefail

if [[ "${1:-}" != --yes ]]; then
  sed -n -e '/^set -euo/q' -e '2,$p' "$0"
  exit 2
fi

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
cache_dir="${FSE_CACHE_DIR:-$HOME/.skylit-fse/cache}"

if pgrep -f "fse pull" >/dev/null; then
  echo "error: an fse pull is running on this Mac. Let it finish or stop it first." >&2
  exit 1
fi
if [[ ! -d "$cache_dir" ]]; then
  echo "error: no cache at $cache_dir" >&2
  exit 1
fi
if [[ -z "${DATA_BUCKET:-}" ]]; then
  if ! DATA_BUCKET="$(terraform -chdir="$project_dir/infra" output -raw data_bucket 2>/dev/null)" ||
    [[ -z "$DATA_BUCKET" ]]; then
    echo "error: set DATA_BUCKET, or run 'terraform init' in infra/ first" >&2
    exit 2
  fi
fi

echo "Uploading $cache_dir ($(du -sh "$cache_dir" | cut -f1)) to s3://$DATA_BUCKET/cache/ ..."
aws s3 sync "$cache_dir/" "s3://$DATA_BUCKET/cache/" --only-show-errors
echo "Done."
