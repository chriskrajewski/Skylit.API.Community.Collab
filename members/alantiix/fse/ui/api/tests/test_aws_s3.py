"""S3 run store, config mirror, batch locks and the Unlock API (design.md 10.5, 10.6). moto only."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from fse_ui.aws import s3store
from fse_ui.aws.s3store import (
    PULL_LOCK_KEY,
    CompositeArtifactStore,
    LockError,
    S3ArtifactStore,
    S3ConfigMirror,
    S3Lock,
)
from fse_ui.batch_inputs import stage_backtest_inputs
from fse_ui.configs import ConfigStore
from fse_ui.runs import LocalArtifactStore, RunError
from tests.aws_support import BUCKET, Aws, Harness, insert_row
from tests.conftest import PROJECT, Console


def _put(aws: Aws, key: str, body: bytes = b"{}") -> None:
    aws.s3.put_object(Bucket=BUCKET, Key=key, Body=body)


# ---------------------------------------------------------------- S3ArtifactStore


def test_s3_store_lists_runs_files_sizes_and_mtime(aws: Aws) -> None:
    _put(aws, "runs/backtest-1/trades.json", b"[]")
    _put(aws, "runs/backtest-1/sub/report.json", b'{"a": 1}')
    _put(aws, "runs/bad name/x.json")  # not a valid run name
    _put(aws, "runs/stray-object")  # no file part
    store = S3ArtifactStore(aws.s3, BUCKET)
    runs = dict(store.runs())
    assert set(runs) == {"backtest-1"}
    assert abs(runs["backtest-1"] - time.time()) < 120
    assert store.files("backtest-1") == [
        {"name": "sub/report.json", "size": 8},
        {"name": "trades.json", "size": 2},
    ]
    assert store.exists("backtest-1", "trades.json")
    assert not store.exists("backtest-1", "nope.json")
    assert store.read_bytes("backtest-1", "sub/report.json") == b'{"a": 1}'
    with pytest.raises(RunError, match="no run"):
        store.files("other")
    with pytest.raises(RunError, match="invalid"):
        store.files("../x")
    with pytest.raises(RunError, match="invalid"):
        store.read_bytes("backtest-1", "../trades.json")
    with pytest.raises(RunError, match="no file"):
        store.read_bytes("backtest-1", "gone.json")


def test_s3_store_size_cap_and_stale_index(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s3store, "MAX_READ_BYTES", 10)
    _put(aws, "runs/r1/big.json", b"x" * 11)
    _put(aws, "runs/r1/small.json", b"x" * 3)
    store = S3ArtifactStore(aws.s3, BUCKET, ttl_s=3600)
    with pytest.raises(RunError, match="larger than 10 bytes"):
        store.read_bytes("r1", "big.json")
    _put(aws, "runs/r1/small.json", b"x" * 20)  # grew after the listing
    with pytest.raises(RunError, match="larger than 10 bytes"):
        store.read_bytes("r1", "small.json")


def test_s3_store_truncates_above_max_keys_and_refreshes_on_invalidate(aws: Aws) -> None:
    for i in range(3):
        _put(aws, f"runs/r{i}/a.json")
    store = S3ArtifactStore(aws.s3, BUCKET, max_keys=2, ttl_s=3600)
    assert len(store.runs()) == 2 and store.truncated
    big = S3ArtifactStore(aws.s3, BUCKET, ttl_s=3600)
    assert len(big.runs()) == 3 and not big.truncated
    _put(aws, "runs/r9/a.json")
    assert len(big.runs()) == 3  # cached
    big.invalidate()
    assert len(big.runs()) == 4


def test_composite_store_prefers_local_runs(aws: Aws, tmp_path: Path) -> None:
    local_root = tmp_path / "runs"
    (local_root / "run-1").mkdir(parents=True)
    (local_root / "run-1" / "a.json").write_text('"local"')
    _put(aws, "runs/run-1/a.json", b'"remote"')
    _put(aws, "runs/backtest-2/a.json", b'"remote2"')
    store = CompositeArtifactStore(LocalArtifactStore(local_root), S3ArtifactStore(aws.s3, BUCKET))
    names = dict(store.runs())
    assert set(names) == {"run-1", "backtest-2"}
    assert names["run-1"] == (local_root / "run-1").stat().st_mtime
    assert store.read_bytes("run-1", "a.json") == b'"local"'
    assert store.read_bytes("backtest-2", "a.json") == b'"remote2"'
    assert store.files("backtest-2") == [{"name": "a.json", "size": 9}]
    assert store.exists("run-1", "a.json") and not store.exists("backtest-2", "b.json")


# ---------------------------------------------------------------- config mirror


def test_config_mirror_puts_every_current_yaml(aws: Aws, tmp_path: Path) -> None:
    data = tmp_path / ".skylit-fse"
    store = ConfigStore(data / "configs", mirror=S3ConfigMirror(aws.s3, BUCKET, data))
    assert store.seed(PROJECT) > 0
    baseline = aws.s3.get_object(
        Bucket=BUCKET, Key="configs/strategy/playbook_baseline/current.yaml"
    )
    assert baseline["Body"].read().decode() == store.get("strategy", "playbook_baseline")["text"]
    text = store.get("strategy", "playbook_baseline")["text"]
    ok, saved = store.save("strategy", "copy", text, author="op", create=True)
    assert ok.ok and saved is not None
    assert aws.s3.head_object(Bucket=BUCKET, Key="configs/strategy/copy/current.yaml")


def test_config_mirror_failure_never_fails_the_save(
    aws: Aws, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / ".skylit-fse"
    store = ConfigStore(data / "configs", mirror=S3ConfigMirror(aws.s3, "no-such-bucket-x", data))
    with caplog.at_level(logging.WARNING, logger="fse_ui.configs"):
        assert store.seed(PROJECT) > 0
    assert store.get("strategy", "playbook_baseline")["text"]
    assert any(
        "config mirror to s3 failed for strategy/playbook_baseline: NoSuchBucket" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------- S3Lock


@pytest.mark.asyncio
async def test_lock_acquire_holder_release_if_and_force_release(aws: Aws) -> None:
    lock = S3Lock(aws.s3, BUCKET, PULL_LOCK_KEY)
    assert await lock.holder() is None
    assert await lock.acquire("user=a\nhost=fse-console\nstarted=x\njob=j1\n")
    assert not await lock.acquire("job=j2\n")  # 412: held
    assert await lock.holder() == ["user=a", "host=fse-console", "started=x", "job=j1"]
    assert not await lock.release_if("j2")  # names another job: left alone
    assert await lock.holder() is not None
    assert await lock.release_if("j1")
    assert await lock.holder() is None
    assert not await lock.force_release()
    aws.put_lock("j3")
    assert await lock.force_release()
    with pytest.raises(LockError) as exc:
        await S3Lock(aws.s3, "no-such-bucket-x", PULL_LOCK_KEY).holder()
    assert exc.value.code == "NoSuchBucket"


@pytest.mark.asyncio
async def test_an_expired_lock_is_replaced(aws: Aws) -> None:
    lock = S3Lock(aws.s3, BUCKET, PULL_LOCK_KEY)
    aws.s3.put_object(
        Bucket=BUCKET,
        Key=PULL_LOCK_KEY,
        Body=b"user=seed-efs-cache\nhost=old\nstarted=2020-01-01T00:00:00Z\n",
    )
    assert await lock.acquire("user=new\nhost=fse-console\nstarted=2026-10-15T12:00:00Z\njob=j9\n")
    holder = await lock.holder()
    assert holder is not None
    assert "user=new" in holder
    assert "job=j9" in holder


@pytest.mark.asyncio
async def test_a_lock_without_a_started_time_is_not_replaced(aws: Aws) -> None:
    lock = S3Lock(aws.s3, BUCKET, PULL_LOCK_KEY)
    aws.s3.put_object(
        Bucket=BUCKET,
        Key=PULL_LOCK_KEY,
        Body=b"user=seed-efs-cache\nhost=old\nstarted=not-a-time\n",
    )
    assert not await lock.acquire("user=new\nhost=fse-console\nstarted=2026-10-15T12:00:00Z\n")
    holder = await lock.holder()
    assert holder is not None
    assert "user=seed-efs-cache" in holder


# ---------------------------------------------------------------- the lock API


def test_locks_api_lists_both_locks(aws_console: Callable[..., Console], aws: Aws) -> None:
    console = aws_console()
    body = console.get("/api/batch/locks").json()
    assert body == {
        "enabled": True,
        "locks": [
            {"name": "pull", "held": False, "holder": None, "job": None},
            {"name": "holdout", "held": False, "holder": None, "job": None},
            {"name": "recordings", "held": False, "holder": None, "job": None},
        ],
    }
    aws.put_lock("a000000000000007")
    pull = console.get("/api/batch/locks").json()["locks"][0]
    assert pull["held"] and pull["job"] == "a000000000000007"
    assert "job=a000000000000007" in pull["holder"]


def test_locks_api_reports_a_read_error_per_lock(
    aws_console: Callable[..., Console], aws: Aws
) -> None:
    console = aws_console(data_bucket="no-such-bucket-x")
    r = console.get("/api/batch/locks")
    assert r.status_code == 200
    assert r.json()["locks"][0]["error"] == "cannot read the lock: NoSuchBucket"


def test_unlock_deletes_and_is_audited(aws_console: Callable[..., Console], aws: Aws) -> None:
    console = aws_console()
    aws.put_lock("a000000000000007")
    r = console.post("/api/batch/unlock", json={"lock": "pull", "confirm": "unlock"})
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert aws.lock_body() is None
    entry = console.get("/api/audit").json()[0]
    assert entry["action"] == "POST /api/batch/unlock" and entry["detail"]["lock"] == "pull"
    assert "job=a000000000000007" in entry["detail"]["holder"]
    again = console.post("/api/batch/unlock", json={"lock": "holdout", "confirm": "unlock"})
    assert again.json() == {"deleted": False}


def test_unlock_refuses_while_a_job_using_the_lock_runs(
    aws_console: Callable[..., Console], aws: Aws
) -> None:
    console = aws_console()
    pull = Harness.request(("pull",), ("--start=2026-10-06",))
    insert_row(console.app.state.db, "a000000000000005", pull, status="running")
    aws.put_lock("a000000000000005")
    r = console.post("/api/batch/unlock", json={"lock": "pull", "confirm": "unlock"})
    assert r.status_code == 409
    assert r.json()["detail"] == "a pull job is running (a000000000000005): cancel it first"
    assert aws.lock_body() is not None
    holdout = Harness.request(("experiment", "holdout"), ("--config=x",))
    insert_row(console.app.state.db, "a000000000000006", holdout, status="queued")
    r = console.post("/api/batch/unlock", json={"lock": "holdout", "confirm": "unlock"})
    assert r.status_code == 409 and "a000000000000006" in r.json()["detail"]


def test_unlock_validation_local_executor_and_s3_errors(
    aws_console: Callable[..., Console],
    console: Console,
    aws: Aws,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert console.get("/api/batch/locks").json() == {"enabled": False, "locks": []}
    r = console.post("/api/batch/unlock", json={"lock": "pull", "confirm": "unlock"})
    assert r.status_code == 404
    on_aws = aws_console()
    assert (
        on_aws.post("/api/batch/unlock", json={"lock": "nope", "confirm": "unlock"}).status_code
        == 422
    )
    assert (
        on_aws.post("/api/batch/unlock", json={"lock": "pull", "confirm": "yes"}).status_code == 422
    )

    async def broken(self: S3Lock) -> bool:
        raise LockError("AccessDenied")

    monkeypatch.setattr(S3Lock, "force_release", broken)
    r = on_aws.post("/api/batch/unlock", json={"lock": "pull", "confirm": "unlock"})
    assert r.status_code == 502 and r.json()["detail"] == "cannot delete the lock: AccessDenied"


class _S3Proxy:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.uploads: list[str] = []

    def put_object(self, **kw: Any) -> Any:
        return self.real.put_object(**kw)

    def get_object(self, **kw: Any) -> Any:
        return self.real.get_object(**kw)

    def delete_object(self, **kw: Any) -> Any:
        return self.real.delete_object(**kw)

    def head_object(self, **kw: Any) -> Any:
        return self.real.head_object(**kw)

    def get_paginator(self, name: str) -> Any:
        return self.real.get_paginator(name)

    def upload_file(self, filename: str, bucket: str, key: str, **kw: Any) -> Any:
        self.uploads.append(key)
        return self.real.upload_file(filename, bucket, key, **kw)


def _touch(path: Path, body: bytes, mtime: float) -> None:
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_stage_uploads_only_what_s3_is_missing(aws: Aws, tmp_path: Path) -> None:
    data = tmp_path / "data"
    bars = data / "cache" / "bars" / "MNQ"
    bars.mkdir(parents=True)
    (bars / "session.bin").write_bytes(b"bars")
    (bars / ".DS_Store").write_bytes(b"junk")
    vix = data / "cache" / "vix"
    vix.mkdir()
    (vix / "keep.bin").write_bytes(b"local-new")
    aws.s3.put_object(Bucket=BUCKET, Key="cache/vix/keep.bin", Body=b"remote-old")
    rec = data / "recordings" / "dataplane"
    rec.mkdir(parents=True)
    now = 1_800_000_000.0
    _touch(rec / "2024-01-02.jsonl.gz", b"closed", now - 3600)
    _touch(rec / "2024-01-03.jsonl.gz", b"same", now - 3600)
    _touch(rec / "2024-01-04.jsonl.gz", b"local-longer-body", now - 3600)
    _touch(rec / "2026-10-14.jsonl.gz", b"young", now - 60)
    _touch(rec / "2026-10-15.jsonl.gz", b"today", now - 3600)
    aws.s3.put_object(Bucket=BUCKET, Key="recordings/dataplane/2024-01-03.jsonl.gz", Body=b"same")
    aws.s3.put_object(Bucket=BUCKET, Key="recordings/dataplane/2024-01-04.jsonl.gz", Body=b"short")
    proxy = _S3Proxy(aws.s3)
    lines: list[str] = []
    await stage_backtest_inputs(
        proxy,
        BUCKET,
        data,
        job_id="job1",
        user="op@example.com",
        on_line=lines.append,
        today=date(2026, 10, 15),
        now=now,
    )
    got = aws.s3.get_object(Bucket=BUCKET, Key="cache/bars/MNQ/session.bin")
    assert got["Body"].read() == b"bars"
    kept = aws.s3.get_object(Bucket=BUCKET, Key="cache/vix/keep.bin")
    assert kept["Body"].read() == b"remote-old"
    assert "cache/.DS_Store" not in proxy.uploads
    assert "cache/vix/keep.bin" not in proxy.uploads
    closed = aws.s3.get_object(Bucket=BUCKET, Key="recordings/dataplane/2024-01-02.jsonl.gz")
    assert closed["Body"].read() == b"closed"
    assert "recordings/dataplane/2024-01-03.jsonl.gz" not in proxy.uploads
    replaced = aws.s3.get_object(Bucket=BUCKET, Key="recordings/dataplane/2024-01-04.jsonl.gz")
    assert replaced["Body"].read() == b"short"
    assert "recordings/dataplane/2024-01-04.jsonl.gz" not in proxy.uploads
    assert any(
        "2024-01-04.jsonl.gz" in line and "s3 object already exists" in line for line in lines
    )
    assert "recordings/dataplane/2026-10-14.jsonl.gz" not in proxy.uploads
    assert "recordings/dataplane/2026-10-15.jsonl.gz" not in proxy.uploads
    assert any("skip 2026-10-14" in line for line in lines)
    pull = S3Lock(aws.s3, BUCKET, PULL_LOCK_KEY)
    assert await pull.holder() is None


@pytest.mark.asyncio
async def test_stage_warns_and_continues_when_a_lock_is_held_or_denied(
    aws: Aws, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    bars = data / "cache" / "bars"
    bars.mkdir(parents=True)
    (bars / "x.bin").write_bytes(b"bars")
    held = S3Lock(aws.s3, BUCKET, PULL_LOCK_KEY)
    assert await held.acquire("user=other\nhost=fse-console\nstarted=x\njob=other\n")
    lines: list[str] = []
    await stage_backtest_inputs(
        aws.s3,
        BUCKET,
        data,
        job_id="job2",
        user="op@example.com",
        on_line=lines.append,
        today=date(2026, 10, 15),
        now=1_800_000_000.0,
    )
    assert any("pull lock is held" in line for line in lines)
    with pytest.raises(ClientError):
        aws.s3.get_object(Bucket=BUCKET, Key="cache/bars/x.bin")
    assert await held.release_if("other")

    class DenyList(_S3Proxy):
        def get_paginator(self, name: str) -> Any:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListObjectsV2"
            )

    fresh = tmp_path / "fresh"
    (fresh / "cache" / "bars").mkdir(parents=True)
    (fresh / "cache" / "bars" / "y.bin").write_bytes(b"y")
    denied: list[str] = []
    await stage_backtest_inputs(
        DenyList(aws.s3),
        BUCKET,
        fresh,
        job_id="job3",
        user="op@example.com",
        on_line=denied.append,
        today=date(2026, 10, 15),
        now=1_800_000_000.0,
    )
    assert any("cannot list cache/" in line for line in denied)
    assert any("no local recordings" in line for line in denied)
