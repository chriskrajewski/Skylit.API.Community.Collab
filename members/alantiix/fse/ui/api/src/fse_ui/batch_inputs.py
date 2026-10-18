"""Stage host inputs a Fargate ``fse backtest-plugin`` task cannot see.

The task downloads ``s3://bucket/cache/`` and reads recordings with
``--recordings-s3``. The host may hold a bar file or a closed recording day
that S3 does not yet have. This uploads only what is missing, under the pull
lock and a recordings lock, and releases both before RunTask. A held lock or
a denied call is a warning: the task still runs on whatever is already in S3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from fse_ui.aws.errors import AWS_ERRORS, error_code, is_access_denied
from fse_ui.aws.s3store import PULL_LOCK_KEY, RECORDINGS_LOCK_KEY, LockError, S3Lock

log = logging.getLogger(__name__)

MAX_CACHE_FILE = 64 * 1024 * 1024
_MIN_AGE_S = 30 * 60
_NY = ZoneInfo("America/New_York")
_CACHE_DIRS = ("bars", "equity_bars", "derived", "darkpool", "vix")
_RECORDING_NAME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\.(?:jsonl|quotes\.jsonl|part[A-Za-z0-9._-]*\.jsonl)\.gz$"
)
_MISSING = frozenset({"404", "NoSuchKey", "NotFound"})

Line = Callable[[str], None]


async def stage_backtest_inputs(
    s3: object,
    bucket: str,
    data_dir: Path,
    *,
    job_id: str,
    user: str,
    on_line: Line,
    locks: Mapping[str, S3Lock] | None = None,
    today: date | None = None,
    now: float | None = None,
) -> None:
    """Upload missing cache objects and closed recordings. Never raises."""
    found = locks or {}
    pull = found.get(PULL_LOCK_KEY) or S3Lock(s3, bucket, PULL_LOCK_KEY)
    recordings = found.get(RECORDINGS_LOCK_KEY) or S3Lock(s3, bucket, RECORDINGS_LOCK_KEY)
    await _under_lock(
        pull,
        "pull",
        job_id=job_id,
        user=user,
        on_line=on_line,
        body=lambda: _seed_cache(s3, bucket, data_dir / "cache", on_line),
    )
    ny_today = today or datetime.now(_NY).date()
    clock = time.time() if now is None else now
    folder = data_dir / "recordings" / "dataplane"
    # The hourly offload already uploads closed days. Taking the lock when this
    # host has nothing new made every overlapping card job log "recordings lock
    # is held" (the first IfNoneMatch wins; the rest only see the refusal).
    if not _eligible_recordings(folder, ny_today, clock):
        if folder.is_dir():
            on_line("recordings seed: nothing to upload")
        else:
            on_line("recordings seed: no local recordings")
        return
    await _under_lock(
        recordings,
        "recordings",
        job_id=job_id,
        user=user,
        on_line=on_line,
        body=lambda: _seed_recordings(s3, bucket, folder, on_line, ny_today, clock),
    )


async def _under_lock(
    lock: S3Lock,
    name: str,
    *,
    job_id: str,
    user: str,
    on_line: Line,
    body: Callable[[], None],
) -> None:
    acquired = False
    try:
        if not await lock.acquire(_lock_body(user, job_id)):
            try:
                holder = await lock.holder() or []
            except LockError as exc:
                holder = [f"(cannot read the lock: {exc.code})"]
            who = "; ".join(holder) if holder else "no holder recorded"
            on_line(f"warning: {name} lock is held ({who}); leaving s3 as it is")
            return
        acquired = True
        await asyncio.to_thread(body)
    except LockError as exc:
        log.warning("job %s: %s lock %s", job_id, name, exc.code)
        on_line(f"warning: cannot lock {name} ({exc.code}); leaving s3 as it is")
    except AWS_ERRORS as exc:
        log.warning("job %s: %s staging failed: %s", job_id, name, error_code(exc))
        on_line(f"warning: {name} staging failed ({error_code(exc)}); leaving s3 as it is")
    finally:
        if acquired:
            try:
                await lock.release_if(job_id)
            except LockError as exc:
                log.warning("job %s: cannot release %s: %s", job_id, name, exc.code)
                on_line(f"warning: could not release the {name} lock ({exc.code})")


def _lock_body(user: str, job_id: str) -> str:
    utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"user={user}\nhost=fse-console\nstarted={utc}\njob={job_id}\n"


def _seed_cache(s3: object, bucket: str, cache: Path, on_line: Line) -> None:
    if not cache.is_dir():
        on_line("cache seed: no local cache")
        return
    try:
        present = _list_keys(s3, bucket, "cache/bars/")
        for name in _CACHE_DIRS[1:]:
            present |= _list_keys(s3, bucket, f"cache/{name}/")
        present |= _list_keys(s3, bucket, "cache/catalog.sqlite")
    except AWS_ERRORS as exc:
        on_line(f"warning: cannot list cache/ ({error_code(exc)}); not uploading bars")
        return
    uploaded = 0
    for path, key in _cache_files(cache, on_line):
        if key in present:
            continue
        try:
            _upload(s3, bucket, key, path)
        except AWS_ERRORS as exc:
            on_line(f"warning: cannot upload {key} ({error_code(exc)})")
            if is_access_denied(exc):
                return
            continue
        uploaded += 1
    on_line(f"cache seed: uploaded {uploaded} missing object(s)")


def _cache_files(cache: Path, on_line: Line) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    catalog = cache / "catalog.sqlite"
    if _regular(catalog) and catalog.stat().st_size <= MAX_CACHE_FILE:
        out.append((catalog, "cache/catalog.sqlite"))
    for name in _CACHE_DIRS:
        root = cache / name
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*"):
            if path.name == ".DS_Store" or not _regular(path):
                continue
            try:
                rel = path.relative_to(cache).as_posix()
            except ValueError:
                continue
            if ".." in rel.split("/"):
                continue
            if path.stat().st_size > MAX_CACHE_FILE:
                on_line(f"warning: skip cache/{rel}: over 64 MiB")
                continue
            out.append((path, f"cache/{rel}"))
    return out


def _eligible_recordings(folder: Path, today: date, now: float) -> list[Path]:
    """Closed recording files old enough to upload. Empty when the folder is missing."""
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(folder.glob("*.jsonl.gz")):
        match = _RECORDING_NAME.fullmatch(path.name)
        if match is None or not _regular(path):
            continue
        day = date.fromisoformat(match.group(1))
        if day >= today:
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age < _MIN_AGE_S:
            continue
        out.append(path)
    return out


def _seed_recordings(
    s3: object,
    bucket: str,
    folder: Path,
    on_line: Line,
    today: date,
    now: float,
) -> None:
    if not folder.is_dir():
        on_line("recordings seed: no local recordings")
        return
    uploaded = 0
    for path in sorted(folder.glob("*.jsonl.gz")):
        name = path.name
        match = _RECORDING_NAME.fullmatch(name)
        if match is None or not _regular(path):
            continue
        day = date.fromisoformat(match.group(1))
        if day >= today:
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age < _MIN_AGE_S:
            on_line(f"recordings seed: skip {name}: written in the last 30 min")
            continue
        key = f"recordings/dataplane/{name}"
        try:
            size = path.stat().st_size
            remote = _remote_len(s3, bucket, key)
        except AWS_ERRORS as exc:
            on_line(f"warning: cannot read {key} ({error_code(exc)})")
            if is_access_denied(exc):
                return
            continue
        if remote is not None:
            if remote != size:
                on_line(f"recordings seed: skip {name}: s3 object already exists")
            continue
        try:
            _upload(s3, bucket, key, path)
        except AWS_ERRORS as exc:
            on_line(f"warning: cannot upload {key} ({error_code(exc)})")
            if is_access_denied(exc):
                return
            continue
        uploaded += 1
    on_line(f"recordings seed: uploaded {uploaded} closed day file(s)")


def _regular(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _list_keys(s3: object, bucket: str, prefix: str) -> set[str]:
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")  # type: ignore[attr-defined]
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            key = obj.get("Key")
            if isinstance(key, str):
                keys.add(key)
    return keys


def _remote_len(s3: object, bucket: str, key: str) -> int | None:
    try:
        resp = s3.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    except ClientError as exc:
        if error_code(exc) in _MISSING:
            return None
        raise
    length = resp.get("ContentLength")
    return int(length) if length is not None else None


def _upload(s3: object, bucket: str, key: str, path: Path) -> None:
    s3.upload_file(str(path), bucket, key)  # type: ignore[attr-defined]
