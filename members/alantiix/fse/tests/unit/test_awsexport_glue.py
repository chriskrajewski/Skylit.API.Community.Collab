"""Glue database and tables with partition projection (design §8.6).

Runs under moto. Calls are recorded with a ``before-call`` handler on the
Glue client.

**Validates: aws-export design §14 test 9**
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from fse.awsexport import glue, source, staging, sync
from fse.awsexport.errors import ExportInputError
from fse.awsexport.tables import TABLES
from fse.logio import LogWriter, Redactor
from tests.fakes.aws import Recorder, compliant_bucket
from tests.fakes.export_cache import build_cache, opts

BUCKET = "skylit-fse-data-glue-us-east-1"
DB = "skylit_fse"


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[Path, staging.Manifest]]:
    build_cache(tmp_path / "cache")
    plan = source.plan(opts(tmp_path / "cache", tmp_path / "stage"))
    manifest = staging.stage(
        plan, tmp_path / "stage", writer=LogWriter(Redactor(()), stdout=io.StringIO())
    ).manifest
    with mock_aws():
        yield tmp_path, manifest


def _glue(rec: Recorder | None = None) -> Any:
    g = boto3.client("glue", region_name="us-east-1")
    if rec is not None:
        g.meta.events.register("before-call.*.*", rec)
    return g


def _account() -> str:
    return str(boto3.client("sts", region_name="us-east-1").get_caller_identity()["Account"])


def _apply(
    tmp_path: Path,
    manifest: staging.Manifest,
    remote: dict[str, frozenset[Any]] | None = None,
    rec: Recorder | None = None,
    bucket: str = BUCKET,
) -> str:
    out = io.StringIO()
    glue.apply_glue(
        _glue(rec),
        options=opts(tmp_path / "cache", tmp_path / "stage", mode="full"),
        account=_account(),
        bucket=bucket,
        manifest=manifest,
        remote=remote or {},
        writer=LogWriter(Redactor(()), stdout=out),
    )
    return out.getvalue()


def _table(name: str) -> dict[str, Any]:
    return dict(_glue().get_table(DatabaseName=DB, Name=name)["Table"])


def test_database_and_tables_created_as_specified(env: tuple[Path, staging.Manifest]) -> None:
    tmp_path, manifest = env
    out = _apply(tmp_path, manifest, bucket="b")
    db = _glue().get_database(Name=DB)["Database"]
    assert db["Description"] == glue.DATABASE_DESCRIPTION
    assert db["Parameters"] == {"fse.export.format": "fse.aws-export/1"}
    names = {t["Name"] for t in _glue().get_tables(DatabaseName=DB)["TableList"]}
    assert names == {n for n in TABLES if n != "vix_bars"}  # no data, no table
    for name in names:
        assert f"created table {DB}.{name}" in out
    heat = _table("heatmaps")
    sd = heat["StorageDescriptor"]
    spec = TABLES["heatmaps"]
    assert [c["Name"] for c in sd["Columns"]] == spec.schema.names
    types = {c["Name"]: c["Type"] for c in sd["Columns"]}
    assert types["strikes"] == "array<double>"
    assert types["expirations"] == "array<string>"
    assert types["session"] == "date"
    assert types["window_row"] == "int"
    assert types["as_of_ns"] == "bigint"
    comments = {c["Name"]: c.get("Comment") for c in sd["Columns"]}
    assert "cache column 'values'" in comments["strike_values"]
    assert sd["Location"] == "s3://b/fse/heatmaps/"
    assert sd["SerdeInfo"]["SerializationLibrary"].endswith("ParquetHiveSerDe")
    assert heat["PartitionKeys"] == [
        {"Name": "symbol", "Type": "string"},
        {"Name": "metric", "Type": "string"},
        {"Name": "month", "Type": "string"},
    ]
    p = heat["Parameters"]
    assert p["EXTERNAL"] == "TRUE"
    assert p["classification"] == "parquet"
    assert p["parquet.compression"] == "ZSTD"
    assert p["projection.enabled"] == "true"
    assert p["projection.symbol.type"] == "enum"
    assert p["projection.symbol.values"] == "SPX,SPY"
    assert p["projection.metric.values"] == "gamma,vanna"
    assert p["projection.month.type"] == "date"
    assert p["projection.month.format"] == "yyyy-MM"
    assert p["projection.month.range"] == "2025-10,NOW"
    assert p["projection.month.interval"] == "1"
    assert p["projection.month.interval.unit"] == "MONTHS"
    assert (
        p["storage.location.template"]
        == "s3://b/fse/heatmaps/symbol=${symbol}/metric=${metric}/month=${month}/"
    )
    fetched = _table("darkpool_fetched_dates")
    assert fetched["StorageDescriptor"]["Location"].endswith("/darkpool_fetched_dates/")
    assert fetched["PartitionKeys"] == []
    assert not any(k.startswith("projection.") for k in fetched["Parameters"])
    assert _table("darkpool")["StorageDescriptor"]["Location"].endswith("/fse/darkpool/")


def test_second_identical_run_makes_no_update(env: tuple[Path, staging.Manifest]) -> None:
    tmp_path, manifest = env
    _apply(tmp_path, manifest)
    rec = Recorder()
    out = _apply(tmp_path, manifest, rec=rec)
    ops = [op for _, op in rec.calls]
    assert "UpdateTable" not in ops
    assert "CreateDatabase" not in ops
    assert "CreateTable" not in ops
    assert f"plan: table {DB}.heatmaps unchanged" in out


def test_changed_projection_makes_exactly_one_update(env: tuple[Path, staging.Manifest]) -> None:
    tmp_path, manifest = env
    _apply(tmp_path, manifest)
    rec = Recorder()
    remote = {"darkpool": frozenset({(("ticker", "IWM"),)})}
    out = _apply(tmp_path, manifest, remote=remote, rec=rec)
    assert [op for _, op in rec.calls].count("UpdateTable") == 1
    assert f"updated table {DB}.darkpool" in out
    assert _table("darkpool")["Parameters"]["projection.ticker.values"] == "IWM,QQQ,SPY"


def test_remote_only_earlier_month_lowers_the_range(env: tuple[Path, staging.Manifest]) -> None:
    tmp_path, manifest = env
    remote = {
        "heatmaps": frozenset({(("symbol", "NDX"), ("metric", "gamma"), ("month", "2024-03"))})
    }
    _apply(tmp_path, manifest, remote=remote)
    p = _table("heatmaps")["Parameters"]
    assert p["projection.month.range"] == "2024-03,NOW"
    assert p["projection.symbol.values"] == "NDX,SPX,SPY"


def test_foreign_remote_keys_leave_the_table_input_unchanged(
    env: tuple[Path, staging.Manifest],
) -> None:
    tmp_path, manifest = env
    compliant_bucket(BUCKET)
    options = opts(tmp_path / "cache", tmp_path / "stage", mode="full", datasets="heatmaps")
    s3 = boto3.client("s3", region_name="us-east-1")

    def remote() -> Any:
        return sync.sync_tables(
            s3,
            options=options,
            account=_account(),
            bucket=BUCKET,
            manifest=manifest,
            staging_dir=tmp_path / "stage",
            writer=LogWriter(Redactor(()), stdout=io.StringIO()),
        ).remote

    clean = remote()
    for key in (
        "fse/heatmaps/symbol=SPX/metric=gamma/month=2025-10/x=y/part-00000.parquet",
        "fse/heatmaps/symbol=a,b/metric=gamma/month=2025-10/part-00000.parquet",
        "fse/heatmaps/symbol=SPX/metric=gamma/month=2025-13/part-00000.parquet",
        "fse/heatmaps/metric=gamma/symbol=SPX/month=2025-10/part-00000.parquet",
    ):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    dirty = remote()
    spec = TABLES["heatmaps"]

    def inp(r: Any) -> dict[str, Any]:
        values = {p.values for p in manifest.partitions.values() if p.table == "heatmaps"}
        return glue.table_input(
            spec, bucket=BUCKET, prefix="fse", partitions=values | set(r["heatmaps"])
        )

    assert inp(dirty) == inp(clean)


def test_table_without_marker_is_exit_2(env: tuple[Path, staging.Manifest]) -> None:
    tmp_path, manifest = env
    g = _glue()
    g.create_database(DatabaseInput={"Name": DB})
    g.create_table(
        DatabaseName=DB,
        TableInput={"Name": "heatmaps", "Parameters": {"owner": "someone"}},
    )
    with pytest.raises(ExportInputError, match="not created by fse export-aws") as info:
        _apply(tmp_path, manifest)
    assert info.value.exit_code == 2


def test_existing_database_is_used_as_is(env: tuple[Path, staging.Manifest]) -> None:
    tmp_path, manifest = env
    _glue().create_database(DatabaseInput={"Name": DB, "Description": "mine"})
    rec = Recorder()
    _apply(tmp_path, manifest, rec=rec)
    assert "CreateDatabase" not in [op for _, op in rec.calls]
    assert "UpdateDatabase" not in [op for _, op in rec.calls]
    assert _glue().get_database(Name=DB)["Database"]["Description"] == "mine"


def test_managed_parameters_filter() -> None:
    params = {
        "EXTERNAL": "TRUE",
        "transient_lastDdlTime": "1",
        "projection.enabled": "true",
        "fse.export.format": "x",
        "storage.location.template": "s3://b/",
        "other": "y",
    }
    assert set(glue.managed_parameters(params)) == {
        "EXTERNAL",
        "projection.enabled",
        "fse.export.format",
        "storage.location.template",
    }


def test_projection_values_reject_unsafe_values() -> None:
    with pytest.raises(AssertionError):
        glue.projection_values(TABLES["darkpool"], [(("ticker", "a,b"),)])
