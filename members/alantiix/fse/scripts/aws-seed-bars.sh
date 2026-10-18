#!/usr/bin/env bash
# Copy 1-minute futures bars and catalog.sqlite into the data bucket.
#
# Card backtests read s3://$DATA_BUCKET/cache/bars/<SYMBOL>/60s/<session>.parquet
# and cache/catalog.sqlite. They do not read heatmaps or darkpool. This script
# uploads only those two things. It does not upload heatmaps/, darkpool/,
# equity_bars/, derived/, vix/ or plugin_books/.
#
# Run it on the machine that already has the Data_Cache (the Mac that ran
# `fse pull` while Atlas futures were entitled), from the engine folder:
#
#   scripts/aws-seed-bars.sh --yes
#
# Where the bars are:
#   Atlas continuous symbols MES1 and MNQ1. Intraday history starts at the
#   2024-01-02 session. Skylit stopped serving futures after the 2026-10-06
#   session (GET /v1/history returns 403 not_entitled). A new `fse pull` cannot
#   refill them: that 403 stops the Skylit client, and the roll calendar has no
#   ProjectX contract id before the 2026 contracts. This engine has no Databento
#   source.
#   What that earlier pull left on disk (your own notes on the earlier pull):
#     ~/.skylit-fse/cache/bars/MNQ/60s/   2025-01-02 .. 2026-10-07
#     ~/.skylit-fse/cache/bars/MES/60s/   from 2025-10-06 only
#   2024-01-02 .. 2024-12-31 was never pulled. MES before 2025-10-06 was not
#   pulled. Those sessions stay missing until a copy of the parquet files exists.
#
# The runner host's /data/cache is a different disk. "cache seed: uploaded 0"
# means that host had no bar file S3 was missing. Run this script on the Mac,
# not on the runner.
#
# Environment (default in brackets):
#   AWS_PROFILE [default]  AWS_REGION [us-east-1]
#   DATA_BUCKET     [skylit-fse-data-123456789012-us-east-1]
#   FSE_CACHE_DIR   [~/.skylit-fse/cache]
set -euo pipefail

if [[ "${1:-}" != --yes ]]; then
  sed -n -e '/^set -euo/q' -e '2,$p' "$0"
  exit 2
fi

export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
cache_dir="${FSE_CACHE_DIR:-$HOME/.skylit-fse/cache}"
bucket="${DATA_BUCKET:-skylit-fse-data-123456789012-us-east-1}"

if [[ ! -d "$cache_dir/bars" ]]; then
  echo "error: no bars at $cache_dir/bars" >&2
  exit 1
fi
if [[ ! -f "$cache_dir/catalog.sqlite" ]]; then
  echo "error: no catalog at $cache_dir/catalog.sqlite" >&2
  exit 1
fi

echo "MES sessions on disk: $(find "$cache_dir/bars/MES/60s" -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')"
echo "MNQ sessions on disk: $(find "$cache_dir/bars/MNQ/60s" -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')"
echo "Uploading bars/ and catalog.sqlite to s3://$bucket/cache/ (not heatmaps, not darkpool) ..."
aws s3 sync "$cache_dir/bars/" "s3://$bucket/cache/bars/" --only-show-errors
aws s3 cp "$cache_dir/catalog.sqlite" "s3://$bucket/cache/catalog.sqlite" --only-show-errors
echo "Done. POST /api/cards/{id}/backtest lists any sessions that are still missing."
