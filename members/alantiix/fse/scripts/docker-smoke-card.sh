#!/usr/bin/env bash
# Build the fse image and run one card backtest as uid 10001.
#
# Catches the Fargate failure where ECS creates /home/fse/.skylit-fse as root
# for the cache mount, so the entrypoint cannot mkdir runs/ and the task exits 1.
# The container uses FSE_CACHE_BACKEND=s3, a one-session MNQ fixture, and a stub
# aws. backtest-plugin must exit 0 and the runs/ upload must contain trades.json
# and summary.json (the objects the API reads back from S3 by run name).
#
#   scripts/docker-smoke-card.sh
#
# SMOKE_IMAGE [fse:smoke]   SMOKE_SKIP_BUILD=1 skips docker build
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [[ -x "$root/.venv/bin/python" ]]; then
  python="$root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python="python3"
else
  echo "error: python3 is required to write the fixture" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is required" >&2
  exit 1
fi

work="$(mktemp -d)"
# shellcheck disable=SC2329  # runs from the EXIT trap
cleanup() {
  local rc=$?
  # The container writes the upload as uid 10001. chmod needs to own the tree;
  # sudo covers a host user that does not.
  if [[ -n "${work:-}" && -d "$work" ]]; then
    chmod -R a+rwx "$work" 2>/dev/null || true
    rm -rf "$work" 2>/dev/null || sudo rm -rf "$work" 2>/dev/null || true
  fi
  exit "$rc"
}
trap cleanup EXIT

fixture="$work/fixture"
upload="$work/upload"
mkdir -p "$fixture" "$upload" "$work/bin"
chmod 777 "$upload"

"$python" - "$fixture" <<'PY'
import sys
from datetime import date, time, timedelta
from pathlib import Path

import yaml
from fse.data.cache import DataCache
from fse.engine.types import Bar
from fse.live.host.dayclock import globex_day
from fse.strategy.spec import example_spec
from fse.timekit import NS_PER_MINUTE, ny_datetime

WED = date(2026, 10, 14)


def make_bar(instrument: str, open_ns: int, o: int, h: int, low: int, c: int) -> Bar:
    hh, ll = max(h, o, c), min(low, o, c)
    return Bar(
        instrument=instrument,
        contract=f"{instrument}Z6",
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=o / 4,
        h=hh / 4,
        l=ll / 4,
        c=c / 4,
        v=100.0,
        o_t=o,
        h_t=hh,
        l_t=ll,
        c_t=c,
        source="projectx",
    )


def session_bars(day: date) -> list[Bar]:
    out: list[Bar] = []
    t = globex_day(day).open_ns
    end = globex_day(day).rotate_ns - 60 * NS_PER_MINUTE
    i = 0
    while t < end:
        p = 80000 + (i % 40) - 20
        out.append(make_bar("MNQ", t, p, p + 2, p - 2, p + 1))
        t += NS_PER_MINUTE
        i += 1
    return out


def history_days(first: date, n: int) -> list[date]:
    out: list[date] = []
    day = first - timedelta(days=1)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out


dest = Path(sys.argv[1])
cache = dest / "cache"
days = [*history_days(WED, 2), WED]
script = [(80000, 80000, 80000, 80000)] * 30
script.append((80000, 80120, 79990, 80080))
script.append((80040, 80050, 80005, 80030))
with DataCache(cache) as store:
    for day in days:
        bars: list[Bar] = []
        rth = 0
        for bar in session_bars(day):
            ny = ny_datetime(bar.open_ns)
            if day == WED and time(9, 30) <= ny.time() < time(16, 0) and rth < len(script):
                o, h, low, c = script[rth]
                rth += 1
                bars.append(make_bar("MNQ", bar.open_ns, o, h, low, c))
            else:
                bars.append(bar)
        store.bars.write_session("MNQ", 60, day, bars, contract="MNQZ6", source="projectx")
spec = example_spec()
(dest / "runner.yaml").write_text(
    yaml.safe_dump(
        {
            "version": 1,
            "data_plane": {"instruments": ["MNQ"], "skylit": {"enabled": False}},
            "plugins": [
                {
                    "id": "rules",
                    "module": "fse.live.plugins.rules:RulesPlugin",
                    "enabled": True,
                    "config": spec.model_dump(mode="json"),
                    "fills": {
                        "trade_through_ticks": 0,
                        "slippage_ticks": 0,
                        "stop_fill": "at_stop",
                        "commission_rt": {"MNQ": "0.00"},
                    },
                    "risk": {"max_contracts": 8, "max_open_trades": 4},
                    "hold_through_break": False,
                }
            ],
        },
        sort_keys=False,
    ),
    encoding="utf-8",
)
print(f"fixture sessions: {', '.join(day.isoformat() for day in sorted(days))}")
PY

chmod -R a+rX "$fixture"

cat >"$work/bin/aws" <<'PY'
#!/usr/bin/env python3
"""Stand-in for aws(1): copy the smoke fixture and capture the runs/ upload."""
import os
import shutil
import sys
from pathlib import Path

FLAGS = {"--only-show-errors", "--recursive", "--summarize"}


def mapped(uri: str) -> Path:
    rest = uri.removeprefix("s3://")
    _bucket, _, key = rest.partition("/")
    if key == "runs" or key.startswith("runs/"):
        rel = key.removeprefix("runs").lstrip("/")
        return Path(os.environ["SMOKE_UPLOAD"]) / rel
    return Path(os.environ["SMOKE_FIXTURE"]) / key


def copy_path(src: Path, dest: Path) -> None:
    if not src.exists():
        sys.stderr.write(f"smoke aws: missing {src}\n")
        sys.exit(1)
    if src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = dest / src.name if dest.is_dir() else dest
        if str(dest).endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / src.name
        shutil.copy2(src, target)
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        target = dest / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


args = [arg for arg in sys.argv[1:] if arg not in FLAGS]
if args[:1] == ["s3"] and args[1:2] == ["ls"]:
    sys.stdout.write("Total Objects: 1\n   Total Size: 10\n")
    sys.exit(0)
if args[:2] in (["s3", "sync"], ["s3", "cp"]) and len(args) >= 4:
    src, dest = args[2], args[3]
    src_path = mapped(src) if src.startswith("s3://") else Path(src)
    dest_path = mapped(dest) if dest.startswith("s3://") else Path(dest)
    if dest.startswith("s3://") and (dest.endswith("/") or args[1] == "sync"):
        dest_path.mkdir(parents=True, exist_ok=True)
    copy_path(src_path, dest_path if not str(dest).endswith("/") else dest_path)
    sys.exit(0)
if args[:2] == ["s3api", "head-object"]:
    import hashlib
    import json
    key = ""
    if "--key" in args:
        key = args[args.index("--key") + 1]
    path = mapped(f"s3://fixture/{key}") if key else None
    if path is not None and path.is_file():
        payload = path.read_bytes()
        digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
        sys.stdout.write(json.dumps({
            "ContentLength": len(payload),
            "ETag": '"' + digest + '"',
        }) + "\n")
        sys.exit(0)
    sys.stderr.write(f"smoke aws: head-object missing {key}\n")
    sys.exit(1)
if args[:2] in (["s3api", "put-object"], ["s3api", "delete-object"]):
    sys.exit(0)
sys.stderr.write("smoke aws: unhandled " + " ".join(sys.argv[1:]) + "\n")
sys.exit(1)
PY
chmod 755 "$work/bin/aws"

image="${SMOKE_IMAGE:-fse:smoke}"
if [[ "${SMOKE_SKIP_BUILD:-}" != 1 ]]; then
  # The Dockerfile's syntax directive needs BuildKit. Hosted runners have it;
  # a plain dockerd does not turn it on by itself.
  DOCKER_BUILDKIT=1 docker build -t "$image" "$root"
fi

echo "probe: uid 10001 can write runs, configs, cache, uploads, research"
docker run --rm --user 10001:10001 "$image" /bin/sh -c '
set -eu
id
for path in cache runs configs ui/uploads research; do
  touch "/home/fse/.skylit-fse/$path/.probe"
  rm -f "/home/fse/.skylit-fse/$path/.probe"
done
echo probe-ok
'

echo "backtest-plugin as uid 10001 with FSE_CACHE_BACKEND=s3"
docker run --rm --user 10001:10001 \
  -e HOME=/home/fse \
  -e FSE_CACHE_BACKEND=s3 \
  -e FSE_CACHE_PREFIXES=bars,catalog.sqlite \
  -e FSE_SYNC_PROGRESS_S=0 \
  -e DATA_BUCKET=fixture \
  -e AWS_REGION=us-east-1 \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=testing \
  -e AWS_SECRET_ACCESS_KEY=testing \
  -e AWS_EC2_METADATA_DISABLED=true \
  -e PATH=/opt/smoke/bin:/usr/local/bin:/usr/bin:/bin \
  -e SMOKE_FIXTURE=/fixture \
  -e SMOKE_UPLOAD=/upload \
  -v "$fixture:/fixture:ro" \
  -v "$upload:/upload" \
  -v "$work/bin/aws:/opt/smoke/bin/aws:ro" \
  "$image" \
  batch backtest-plugin \
  --config=/fixture/runner.yaml \
  --plugin=rules \
  --first=2026-10-14 \
  --last=2026-10-14 \
  --out=/home/fse/.skylit-fse/runs/card-smoke \
  --history=2 \
  --lag-s=2

test -s "$upload/card-smoke/trades.json"
test -s "$upload/card-smoke/summary.json"
echo "smoke ok: $upload/card-smoke/trades.json"
