"""Sync of staged parts to S3 (design §8.5) and the ``DeleteObjects`` check (review finding 2).

The sync cases run under moto against a bucket that passes the required
checks. ``DeleteObjects`` per-key failures, which moto never returns, are
exercised on :func:`fse.awsexport.sync.delete_superseded` with
``botocore.stub.Stubber``.

**Validates: aws-export design §14 test 8; design review round 3 finding 2**
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto import mock_aws

from fse.awsexport import source, staging, sync
from fse.awsexport.errors import ExportAccessError, ExportDataError
from fse.data.cache import CacheWindowKey, DataCache
from fse.logio import LogWriter, Redactor
from tests.fakes.aws import compliant_bucket
from tests.fakes.export_cache import build_cache, opts, snapshot

BUCKET = "skylit-fse-data-sync-us-east-1"
HEAT_OCT = "fse/heatmaps/symbol=SPX/metric=gamma/month=2025-10/"
ACCOUNT = "1" * 12  # Stubber cases only


class Calls:
    """``(operation, Key or Prefix)`` of every S3 call, in order."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def __call__(self, params: dict[str, Any], model: Any, **_: Any) -> None:
        self.items.append((model.name, str(params.get("Key", params.get("Prefix", "")))))

    def ops(self, name: str) -> list[str]:
        return [k for op, k in self.items if op == name]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Path]:
    build_cache(tmp_path / "cache")
    with mock_aws():
        compliant_bucket(BUCKET)
        yield tmp_path


def _stage(tmp_path: Path) -> staging.Manifest:
    plan = source.plan(opts(tmp_path / "cache", tmp_path / "stage"))
    return staging.stage(plan, tmp_path / "stage", writer=_writer()[0]).manifest


def _writer() -> tuple[LogWriter, io.StringIO]:
    out = io.StringIO()
    return LogWriter(Redactor(()), stdout=out), out


def _s3(calls: Calls | None = None) -> Any:
    s3 = boto3.client("s3", region_name="us-east-1")
    if calls is not None:
        s3.meta.events.register("provide-client-params.s3.*", calls)
    return s3


def _account() -> str:
    return str(boto3.client("sts", region_name="us-east-1").get_caller_identity()["Account"])


def _sync(
    tmp_path: Path, manifest: staging.Manifest, s3: Any | None = None, **kw: object
) -> tuple[sync.SyncReport, str]:
    writer, out = _writer()
    report = sync.sync_tables(
        _s3() if s3 is None else s3,
        options=opts(tmp_path / "cache", tmp_path / "stage", mode="full", **kw),
        account=_account(),
        bucket=BUCKET,
        manifest=manifest,
        staging_dir=tmp_path / "stage",
        writer=writer,
    )
    return report, out.getvalue()


def _keys() -> dict[str, int]:
    resp = boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=BUCKET)
    return {o["Key"]: o["Size"] for o in resp.get("Contents", [])}


def _put(key: str) -> None:
    boto3.client("s3", region_name="us-east-1").put_object(Bucket=BUCKET, Key=key, Body=b"x")


# ---------------------------------------------------------------- moto


def test_first_run_uploads_everything_with_metadata(env: Path) -> None:
    manifest = _stage(env)
    report, out = _sync(env, manifest)
    assert len(report.uploaded) == 14
    assert report.skipped == ()
    assert report.deleted == ()
    keys = _keys()
    assert set(keys) == set(report.uploaded)
    assert all(k.startswith("fse/") and k.endswith("/part-00000.parquet") for k in keys)
    assert "fse/darkpool_fetched_dates/part-00000.parquet" in keys
    entry = manifest.partitions["heatmaps/symbol=SPX/metric=gamma/month=2025-10/"]
    head = _s3().head_object(Bucket=BUCKET, Key=HEAT_OCT + "part-00000.parquet")
    assert head["Metadata"] == {
        "sha256": entry.files[0].sha256,
        "fse-export-format": "fse.aws-export/1",
    }
    assert head["ContentType"] == "application/vnd.apache.parquet"
    assert report.bytes_uploaded == sum(
        f.size for p in manifest.partitions.values() for f in p.files
    )
    assert "warning" not in out


def test_rerun_uploads_nothing(env: Path) -> None:
    manifest = _stage(env)
    _sync(env, manifest)
    calls = Calls()
    report, _ = _sync(env, manifest, _s3(calls))
    assert report.uploaded == ()
    assert len(report.skipped) == 14
    assert "fse/darkpool_fetched_dates/part-00000.parquet" in report.skipped
    assert calls.ops("PutObject") == []
    assert calls.ops("DeleteObjects") == []


def test_changed_file_uploads_only_itself(env: Path) -> None:
    _sync(env, _stage(env))
    key = build_cache_key(env)
    with DataCache(env / "cache") as cache:
        cache.write_window(key, [snapshot(key, 1)], "range")
    report, _ = _sync(env, _stage(env))
    assert report.uploaded == (HEAT_OCT + "part-00000.parquet",)
    assert len(report.skipped) == 13


def build_cache_key(env: Path) -> CacheWindowKey:
    """The first SPX gamma 2025-10 window of the synthetic cache."""
    plan = source.plan(opts(env / "cache", env / "stage", datasets="heatmaps", symbols="SPX"))
    w = plan.partitions[0].inputs[0]
    assert isinstance(w, source.WindowInput)
    return CacheWindowKey(w.symbol, w.metric, w.view_id, w.session, w.start_ns)  # type: ignore[arg-type]


def test_shrunk_partition_deletes_the_superseded_part_after_uploads(env: Path) -> None:
    manifest = _stage(env)
    stale = HEAT_OCT + "part-00001.parquet"
    _put(stale)  # left over from when the partition had two parts
    calls = Calls()
    report, out = _sync(env, manifest, _s3(calls))
    assert report.deleted == (stale,)
    assert stale not in _keys()
    assert f"deleted superseded s3://{BUCKET}/{stale}" in out
    order = [(op, k) for op, k in calls.items if op in ("PutObject", "DeleteObjects")]
    delete_at = order.index(("DeleteObjects", ""))
    heat_puts = [i for i, (op, k) in enumerate(order) if op == "PutObject" and "heatmaps/" in k]
    assert len(heat_puts) == 8
    assert max(heat_puts) < delete_at  # every heatmaps upload came first
    later = [k for op, k in order[delete_at:] if op == "PutObject"]
    assert later
    assert all("heatmaps/" not in k for k in later)


def test_foreign_nested_and_orphaned_keys_are_kept(env: Path) -> None:
    manifest = _stage(env)
    nested = HEAT_OCT + "x/part-00000.parquet"
    notes = "fse/heatmaps/notes.txt"
    orphan = "fse/heatmaps/symbol=NDX/metric=gamma/month=2024-01/part-00000.parquet"
    comma = "fse/heatmaps/symbol=a,b/metric=gamma/month=2025-10/part-00000.parquet"
    for k in (nested, notes, orphan, comma):
        _put(k)
    report, out = _sync(env, manifest)
    keys = _keys()
    for k in (nested, notes, orphan, comma):
        assert k in keys, k
    assert report.deleted == ()
    assert f"warning: foreign object s3://{BUCKET}/{nested} ignored" in out
    assert f"warning: foreign object s3://{BUCKET}/{notes} ignored" in out
    assert f"warning: foreign object s3://{BUCKET}/{comma} ignored" in out
    assert (
        "warning: remote partition heatmaps/symbol=NDX/metric=gamma/month=2024-01/ has no "
        "staged source; kept"
    ) in out
    remote = report.remote["heatmaps"]
    symbols = {dict(v)["symbol"] for v in remote}
    assert symbols == {"SPX", "SPY", "NDX"}  # the comma key contributes nothing
    assert "a,b" not in symbols


def test_darkpool_tables_never_see_each_others_keys(env: Path) -> None:
    manifest = _stage(env)
    dp_stale = "fse/darkpool/ticker=SPY/part-00001.parquet"
    fd_stale = "fse/darkpool_fetched_dates/part-00001.parquet"
    _put(dp_stale)
    _put(fd_stale)
    calls = Calls()
    report, out = _sync(env, manifest, _s3(calls), datasets="darkpool")
    assert calls.ops("ListObjectsV2") == ["fse/darkpool/", "fse/darkpool_fetched_dates/"]
    assert set(report.deleted) == {dp_stale, fd_stale}
    assert "warning" not in out


def test_changed_staged_file_is_exit_4(env: Path) -> None:
    manifest = _stage(env)
    part = env / "stage/data/vix_daily/part-00000.parquet"
    part.write_bytes(part.read_bytes() + b"x")
    with pytest.raises(ExportDataError, match="staged file changed; rerun to restage") as info:
        _sync(env, manifest, datasets="vix_daily")
    assert info.value.exit_code == 4
    reloaded, _ = staging.load_manifest(env / "stage")
    assert "vix_daily/" not in reloaded.partitions


def _failing_upload(inner: Exception | None) -> Any:
    def upload_file(*args: Any, **kwargs: Any) -> None:
        if inner is None:
            raise S3UploadFailedError("Failed to upload x")
        try:
            raise inner
        except Exception:
            raise S3UploadFailedError("Failed to upload x: wrapped") from None

    return upload_file


@pytest.mark.parametrize(
    ("inner", "code"),
    [
        (ClientError({"Error": {"Code": "ExpiredToken", "Message": "x"}}, "PutObject"), 3),
        (ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "PutObject"), 4),
        (None, 4),
    ],
)
def test_upload_failures(
    env: Path, monkeypatch: pytest.MonkeyPatch, inner: Exception | None, code: int
) -> None:
    manifest = _stage(env)
    s3 = _s3()
    upload = _failing_upload(inner)

    def keep_context(*args: Any, **kwargs: Any) -> None:
        try:
            upload(*args, **kwargs)
        except S3UploadFailedError as exc:
            exc.__suppress_context__ = False
            raise

    monkeypatch.setattr(s3, "upload_file", keep_context)
    with pytest.raises((ExportAccessError, ExportDataError)) as info:
        _sync(env, manifest, s3)
    assert info.value.exit_code == code
    if code == 3:
        assert "aws sts get-caller-identity --profile default" in str(info.value)


# ---------------------------------------------------------------- finding 2: DeleteObjects Errors


def _delete_stub(errors: list[dict[str, str]] | None, keys: list[str]) -> tuple[Any, Stubber]:
    s3 = boto3.client("s3", region_name="us-east-1")
    stub = Stubber(s3)
    response: dict[str, Any] = {} if errors is None else {"Errors": errors}
    stub.add_response(
        "delete_objects",
        response,
        {
            "Bucket": BUCKET,
            "Delete": {"Objects": [{"Key": k} for k in keys], "Quiet": True},
            "ExpectedBucketOwner": ACCOUNT,
        },
    )
    return s3, stub


def _delete(s3: Any, keys: list[str]) -> list[str]:
    return sync.delete_superseded(
        s3, bucket=BUCKET, account=ACCOUNT, keys=keys, profile="myprofile"
    )


KEY = HEAT_OCT + "part-00001.parquet"


def test_delete_access_denied_is_exit_3_naming_the_key() -> None:
    s3, stub = _delete_stub([{"Key": KEY, "Code": "AccessDenied", "Message": "x"}], [KEY])
    with stub, pytest.raises(ExportAccessError) as info:
        _delete(s3, [KEY])
    assert info.value.exit_code == 3
    assert KEY in str(info.value)
    assert "duplicate rows" in str(info.value)


def test_delete_other_error_is_exit_4() -> None:
    s3, stub = _delete_stub([{"Key": KEY, "Code": "InternalError", "Message": "x"}], [KEY])
    with stub, pytest.raises(ExportDataError) as info:
        _delete(s3, [KEY])
    assert info.value.exit_code == 4
    assert "InternalError" in str(info.value)
    assert "duplicate rows" in str(info.value)
    assert KEY in str(info.value)


def test_delete_mixed_errors_are_exit_3() -> None:
    other = HEAT_OCT + "part-00002.parquet"
    errors = [
        {"Key": KEY, "Code": "InternalError", "Message": "x"},
        {"Key": other, "Code": "AccessDenied", "Message": "y"},
    ]
    s3, stub = _delete_stub(errors, [KEY, other])
    with stub, pytest.raises(ExportAccessError, match="2 superseded keys"):
        _delete(s3, [KEY, other])


def test_delete_without_errors_returns_the_keys() -> None:
    s3, stub = _delete_stub(None, [KEY])
    with stub:
        assert _delete(s3, [KEY]) == [KEY]
    s3, stub = _delete_stub([], [KEY])
    with stub:
        assert _delete(s3, [KEY]) == [KEY]


def test_delete_batches_of_1000() -> None:
    keys = [f"{HEAT_OCT}part-{i:05d}.parquet" for i in range(1001)]
    s3 = boto3.client("s3", region_name="us-east-1")
    stub = Stubber(s3)
    for batch in (keys[:1000], keys[1000:]):
        stub.add_response(
            "delete_objects",
            {},
            {
                "Bucket": BUCKET,
                "Delete": {"Objects": [{"Key": k} for k in batch], "Quiet": True},
                "ExpectedBucketOwner": ACCOUNT,
            },
        )
    with stub:
        assert _delete(s3, keys) == keys
    stub.assert_no_pending_responses()


def test_first_failing_batch_stops_the_run() -> None:
    keys = [f"{HEAT_OCT}part-{i:05d}.parquet" for i in range(1001)]
    s3, stub = _delete_stub([{"Key": keys[0], "Code": "AccessDenied", "Message": "x"}], keys[:1000])
    with stub, pytest.raises(ExportAccessError):
        _delete(s3, keys)  # the second batch is never sent (Stubber has no response for it)


def test_non_part_key_is_refused_before_any_call() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    with Stubber(s3), pytest.raises(AssertionError, match="not a part key"):
        _delete(s3, [KEY, "fse/heatmaps/notes.txt"])
