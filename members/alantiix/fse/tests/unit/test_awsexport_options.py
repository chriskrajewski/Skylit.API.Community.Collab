"""Option checks of ``fse export-aws`` (design §7, review NIT 6).

Every directory is under ``tmp_path``, outside any git repository.

**Validates: aws-export design §14 test 1**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fse.awsexport import options as o
from fse.awsexport.errors import ExportInputError
from fse.data.path_guard import PathGuardError

PROJECT_DIR = Path(__file__).resolve().parents[2]


def build(tmp_path: Path, **kw: object) -> o.ExportOptions:
    kw.setdefault("cache_dir", tmp_path / "cache")
    kw.setdefault("staging_dir", tmp_path / "stage")
    kw.setdefault("mode", "stage_only")
    return o.build_options(**kw)  # type: ignore[arg-type]


def test_defaults(tmp_path: Path) -> None:
    opts = build(tmp_path)
    assert (opts.profile, opts.region, opts.prefix, opts.glue_database) == (
        "default",
        "us-east-1",
        "fse",
        "skylit_fse",
    )
    assert opts.bucket is None
    assert opts.datasets == o.DATASETS
    assert opts.symbols is None
    assert opts.months is None
    assert opts.cache_dir == (tmp_path / "cache").resolve()
    assert opts.staging_dir == (tmp_path / "stage").resolve()
    assert opts.stage_only
    assert not opts.dry_run
    assert opts.tables == (
        "heatmaps",
        "bars",
        "vix_bars",
        "vix_daily",
        "darkpool",
        "darkpool_fetched_dates",
        "regime_medians",
    )


def test_default_dirs_live_under_app_home(isolated_home: Path) -> None:
    assert o.default_cache_dir() == isolated_home / ".skylit-fse" / "cache"
    assert o.default_staging_dir() == isolated_home / ".skylit-fse" / "aws-export"


def test_modes(tmp_path: Path) -> None:
    assert build(tmp_path, mode="dry_run").dry_run
    assert not build(tmp_path, mode="full").dry_run
    with pytest.raises(ExportInputError):
        build(tmp_path, mode="both")


@pytest.mark.parametrize("value", ["", "a b", "x\ty"])
def test_profile_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--profile"):
        build(tmp_path, profile=value)


@pytest.mark.parametrize("value", ["us-east-1", "eu-west-2", "ap-southeast-10", "us-gov-west-1"])
def test_region_accepted(tmp_path: Path, value: str) -> None:
    assert build(tmp_path, region=value).region == value


@pytest.mark.parametrize("value", ["US-EAST-1", "useast1", "us-east", "us-east-123", ""])
def test_region_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--region"):
        build(tmp_path, region=value)


@pytest.mark.parametrize("value", ["abc", "my-bucket-1", "a" * 63])
def test_bucket_accepted(tmp_path: Path, value: str) -> None:
    assert build(tmp_path, bucket=value).bucket == value


@pytest.mark.parametrize(
    "value",
    [
        "ab",
        "a" * 64,
        "My-Bucket",
        "my.bucket",
        "-abc",
        "abc-",
        "my_bucket",
        "xn--a",
        "sthree-a",
        "a-s3alias",
        "a--ol-s3",
        "a--x-s3",
        "a--table-s3",
    ],
)
def test_bucket_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--bucket"):
        build(tmp_path, bucket=value)


@pytest.mark.parametrize(
    ("value", "normalized"),
    [("fse", "fse"), ("/fse/", "fse"), ("a/b.c/d_e-f", "a/b.c/d_e-f"), ("x/", "x")],
)
def test_prefix_normalized(tmp_path: Path, value: str, normalized: str) -> None:
    assert build(tmp_path, prefix=value).prefix == normalized


@pytest.mark.parametrize("value", ["", "/", "//", "a//b", "A", "a/../b", "..", "a b", "x" * 201])
def test_prefix_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--prefix"):
        build(tmp_path, prefix=value)


@pytest.mark.parametrize("value", ["", "1db", "Db", "a-b", "a" * 256])
def test_glue_database_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--glue-database"):
        build(tmp_path, glue_database=value)


def test_datasets_in_registry_order(tmp_path: Path) -> None:
    opts = build(tmp_path, datasets="regime_medians,darkpool, bars")
    assert opts.datasets == ("bars", "darkpool", "regime_medians")
    assert opts.tables == ("bars", "darkpool", "darkpool_fetched_dates", "regime_medians")


@pytest.mark.parametrize("value", ["", "bars,", "bars,bars", "nope"])
def test_datasets_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--datasets") as info:
        build(tmp_path, datasets=value)
    if value == "nope":
        assert "heatmaps" in str(info.value)  # lists the valid names


def test_symbols_and_months(tmp_path: Path) -> None:
    opts = build(tmp_path, symbols="SPX,SPY", months="2025-10,2026-01")
    assert opts.symbols == frozenset({"SPX", "SPY"})
    assert opts.months == frozenset({"2025-10", "2026-01"})


@pytest.mark.parametrize("value", ["SP X", "a/b", ".x", "SPX,SPX"])
def test_symbols_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--symbols"):
        build(tmp_path, symbols=value)


@pytest.mark.parametrize("value", ["2025-13", "2025-1", "202510", "2025-00", "x"])
def test_months_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExportInputError, match="--months"):
        build(tmp_path, months=value)


def test_staging_inside_cache_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExportInputError, match="overlaps"):
        build(tmp_path, staging_dir=tmp_path / "cache" / "stage")
    with pytest.raises(ExportInputError, match="overlaps"):
        build(tmp_path, cache_dir=tmp_path / "stage" / "cache", staging_dir=tmp_path / "stage")


def test_staging_in_the_repository_is_rejected_by_the_path_guard(tmp_path: Path) -> None:
    with pytest.raises(PathGuardError) as info:
        build(tmp_path, staging_dir=PROJECT_DIR / "aws-export-staging")
    assert info.value.exit_code == 2
    assert not (PROJECT_DIR / "aws-export-staging").exists()


def test_default_bucket() -> None:
    account = "1" * 12  # a stub, not a real account id
    assert o.default_bucket(account, "us-east-1") == f"skylit-fse-data-{account}-us-east-1"
    with pytest.raises(ExportInputError):
        o.default_bucket(account, "nowhere")
