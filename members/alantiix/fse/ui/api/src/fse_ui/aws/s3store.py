"""S3: the batch locks, batch run outputs (``runs/``) and the config mirror (``configs/``).

``S3Lock`` is the ``scripts/aws-batch.sh`` lock protocol: an object created with
``IfNoneMatch="*"`` whose body names the holder (``user``, ``host``, ``started``
and, from the console, ``job=<id>``). A lock whose ``started`` time is at least
``FSE_LOCK_TTL_S`` seconds ago (default 7200) is replaced on the next acquire.
A missing or unreadable ``started`` line is left alone. The console only
deletes a lock whose body names its own job, except through the operator's
explicit Unlock.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from fse_ui.aws.errors import AWS_ERRORS, error_code, http_status
from fse_ui.runs import FILE_RE, MAX_READ_BYTES, RUN_RE, ArtifactStore, RunError, check_names

log = logging.getLogger(__name__)

PULL_LOCK_KEY = "locks/pull.lock"
HOLDOUT_LOCK_KEY = "locks/holdout.lock"
RECORDINGS_LOCK_KEY = "locks/recordings.lock"
_HELD_CODES = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})
DEFAULT_LOCK_TTL_S = 7200.0


def lock_ttl_s() -> float:
    """Seconds before a lock with a parseable ``started=`` line may be replaced."""
    raw = os.environ.get("FSE_LOCK_TTL_S", "").strip()
    if not raw:
        return DEFAULT_LOCK_TTL_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LOCK_TTL_S
    return value if value > 0 else DEFAULT_LOCK_TTL_S


def started_epoch(lines: list[str] | None) -> float | None:
    """UTC epoch of the first ``started=`` line, or None when it cannot be read."""
    for line in lines or []:
        if not line.startswith("started="):
            continue
        raw = line.removeprefix("started=").strip()
        if not raw:
            return None
        text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            return None
        return stamp.timestamp()
    return None


def lock_is_expired(
    lines: list[str] | None, *, now: float | None = None, ttl_s: float | None = None
) -> bool:
    """True when ``started`` is present and at least ``ttl_s`` seconds old."""
    started = started_epoch(lines)
    if started is None:
        return False
    ttl = lock_ttl_s() if ttl_s is None else ttl_s
    current = time.time() if now is None else now
    return current - started >= ttl


class LockError(Exception):
    """The lock object could not be read, created or deleted (``code`` is the AWS code)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class S3Lock:
    def __init__(self, s3: Any, bucket: str, key: str) -> None:
        self._s3 = s3
        self.bucket = bucket
        self.key = key

    @property
    def url(self) -> str:
        return f"s3://{self.bucket}/{self.key}"

    def _holder(self) -> list[str] | None:
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=self.key)
            text = obj["Body"].read(64 * 1024).decode("utf-8", "replace")
        except ClientError as exc:
            if error_code(exc) in ("NoSuchKey", "404"):
                return None
            raise LockError(error_code(exc)) from None
        except AWS_ERRORS as exc:
            raise LockError(error_code(exc)) from None
        return [line for line in text.splitlines() if line.strip()]

    async def holder(self) -> list[str] | None:
        """The body lines, or None when nobody holds the lock."""
        return await asyncio.to_thread(self._holder)

    def _acquire(self, body: str, *, allow_steal: bool = True) -> bool:
        try:
            self._s3.put_object(
                Bucket=self.bucket, Key=self.key, Body=body.encode("utf-8"), IfNoneMatch="*"
            )
        except ClientError as exc:
            if error_code(exc) in _HELD_CODES or http_status(exc) in (409, 412):
                # A checksum-vs-If-None-Match refusal also returns 409 when the
                # key does not exist. That is not a holder. Say so.
                lines = self._holder()
                if lines is None:
                    raise LockError(error_code(exc)) from None
                if allow_steal and lock_is_expired(lines):
                    log.warning("lock %s is past its TTL; replacing it", self.url)
                    self._delete()
                    return self._acquire(body, allow_steal=False)
                return False
            raise LockError(error_code(exc)) from None
        except AWS_ERRORS as exc:
            raise LockError(error_code(exc)) from None
        return True

    async def acquire(self, body: str) -> bool:
        """True when created; False when someone else holds it."""
        return await asyncio.to_thread(self._acquire, body)

    def _delete(self) -> None:
        try:
            self._s3.delete_object(Bucket=self.bucket, Key=self.key)
        except AWS_ERRORS as exc:
            raise LockError(error_code(exc)) from None

    async def release_if(self, job_id: str) -> bool:
        """Delete the lock only when its body names ``job_id``; True when deleted."""

        def run() -> bool:
            lines = self._holder()
            if lines is None or f"job={job_id}" not in lines:
                return False
            self._delete()
            return True

        return await asyncio.to_thread(run)

    async def force_release(self) -> bool:
        """The operator's Unlock: delete whoever holds it; False when there was no lock."""

        def run() -> bool:
            if self._holder() is None:
                return False
            self._delete()
            return True

        return await asyncio.to_thread(run)


def job_of(lines: list[str] | None) -> str | None:
    """The ``job=<id>`` a lock body names, if any."""
    for line in lines or []:
        if line.startswith("job="):
            return line.removeprefix("job=").strip() or None
    return None


# ---------------------------------------------------------------- runs


class S3ArtifactStore:
    """Run folders under ``s3://<bucket>/runs/``, listed into a short-lived index."""

    def __init__(
        self,
        s3: Any,
        bucket: str,
        prefix: str = "runs/",
        *,
        ttl_s: float = 30.0,
        max_keys: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._s3 = s3
        self._bucket = bucket
        self._prefix = prefix
        self._ttl = ttl_s
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, tuple[int, float]]] = {}
        self._at: float | None = None
        self.truncated = False

    def invalidate(self) -> None:
        with self._lock:
            self._at = None

    def _refresh(self) -> dict[str, dict[str, tuple[int, float]]]:
        with self._lock:
            if self._at is not None and self._clock() - self._at < self._ttl:
                return self._index
            index: dict[str, dict[str, tuple[int, float]]] = {}
            seen = 0
            truncated = False
            try:
                pages = self._s3.get_paginator("list_objects_v2").paginate(
                    Bucket=self._bucket, Prefix=self._prefix
                )
                for page in pages:
                    for obj in page.get("Contents", []):
                        seen += 1
                        if seen > self._max_keys:
                            truncated = True
                            break
                        run, _, rel = str(obj["Key"]).removeprefix(self._prefix).partition("/")
                        if not run or not rel:
                            continue
                        modified = obj.get("LastModified")
                        mtime = modified.timestamp() if modified is not None else 0.0
                        index.setdefault(run, {})[rel] = (int(obj.get("Size", 0)), mtime)
                    if truncated:
                        break
            except AWS_ERRORS as exc:
                log.warning(
                    "cannot list s3://%s/%s: %s", self._bucket, self._prefix, error_code(exc)
                )
                return self._index  # keep serving the last listing
            if truncated:
                log.warning(
                    "s3://%s/%s holds more than %d keys; the run list is truncated",
                    self._bucket,
                    self._prefix,
                    self._max_keys,
                )
            self._index, self._at, self.truncated = index, self._clock(), truncated
            return index

    def runs(self) -> list[tuple[str, float]]:
        return [
            (name, max((m for _, m in files.values()), default=0.0))
            for name, files in self._refresh().items()
            if RUN_RE.fullmatch(name) and name not in (".", "..")
        ]

    def files(self, run: str) -> list[dict[str, Any]]:
        check_names(run)
        entries = self._refresh().get(run)
        if entries is None:
            raise RunError(f"no run {run!r}")
        return [
            {"name": rel, "size": size}
            for rel, (size, _) in sorted(entries.items())
            if FILE_RE.fullmatch(rel) and ".." not in rel.split("/")
        ]

    def exists(self, run: str, name: str) -> bool:
        try:
            check_names(run, name)
        except RunError:
            return False
        return name in self._refresh().get(run, {})

    def read_bytes(self, run: str, name: str) -> bytes:
        check_names(run, name)
        size = self._refresh().get(run, {}).get(name, (0, 0.0))[0]
        if size > MAX_READ_BYTES:
            raise RunError(f"{name} is larger than {MAX_READ_BYTES} bytes")
        key = f"{self._prefix}{run}/{name}"
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=key)
            data: bytes = obj["Body"].read(MAX_READ_BYTES + 1)
        except ClientError as exc:
            if error_code(exc) in ("NoSuchKey", "404"):
                raise RunError(f"no file {name!r} in run {run!r}") from None
            log.warning("cannot read s3://%s/%s: %s", self._bucket, key, error_code(exc))
            raise RunError(f"cannot read s3 {run}/{name}: {error_code(exc)}") from None
        except AWS_ERRORS as exc:
            log.warning("cannot read s3://%s/%s: %s", self._bucket, key, error_code(exc))
            raise RunError(f"cannot read s3 {run}/{name}: {error_code(exc)}") from None
        if len(data) > MAX_READ_BYTES:  # the index was stale
            raise RunError(f"{name} is larger than {MAX_READ_BYTES} bytes")
        return data


class CompositeArtifactStore:
    """Local runs (the runner's ``run-*`` dirs on EBS) first, then S3 batch runs."""

    def __init__(self, local: Any, remote: S3ArtifactStore) -> None:
        self._local: ArtifactStore = local
        self._remote = remote

    def _has_local(self, run: str) -> bool:
        has_run: Callable[[str], bool] | None = getattr(self._local, "has_run", None)
        return bool(has_run(run)) if has_run is not None else False

    def _pick(self, run: str) -> ArtifactStore:
        return self._local if self._has_local(run) else self._remote

    def runs(self) -> list[tuple[str, float]]:
        found = dict(self._remote.runs())
        found.update(self._local.runs())  # a name in both: the local entry wins
        return list(found.items())

    def files(self, run: str) -> list[dict[str, Any]]:
        return self._pick(run).files(run)

    def read_bytes(self, run: str, name: str) -> bytes:
        return self._pick(run).read_bytes(run, name)

    def exists(self, run: str, name: str) -> bool:
        return self._pick(run).exists(run, name)

    def invalidate(self) -> None:
        self._remote.invalidate()


# ---------------------------------------------------------------- configs


class S3ConfigMirror:
    """Copies each ``current.yaml`` to ``s3://<bucket>/configs/<kind>/<name>/current.yaml``."""

    def __init__(self, s3: Any, bucket: str, data_dir: Path) -> None:
        self._s3 = s3
        self._bucket = bucket
        self._root = data_dir / "configs"

    def put(self, path: Path) -> None:
        rel = path.relative_to(self._root).as_posix()
        self._s3.put_object(Bucket=self._bucket, Key=f"configs/{rel}", Body=path.read_bytes())
