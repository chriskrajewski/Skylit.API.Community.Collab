"""Unit tests for the Data_Cache SQLite catalog (design §3, "Storage schemas").

**Validates: Requirements 3.6, 3.8, 3.9**
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fse.data.catalog import (
    CATALOG_FILE_NAME,
    SCHEMA_VERSION,
    BarsKey,
    Catalog,
    CatalogError,
)
from fse.timekit import ny_instant

SESSION = date(2026, 3, 5)
SHA_A = "a" * 64
SHA_B = "b" * 64


def key(start: time = time(9, 0), symbol: str = "SPX", metric: str = "gamma") -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        metric=metric,
        view_id="0123456789abcdef",
        session=SESSION,
        start_ns=ny_instant(SESSION, start),
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        self.now += 1
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def catalog(tmp_path: Path, clock: FakeClock) -> Iterator[Catalog]:
    with Catalog(tmp_path / CATALOG_FILE_NAME, clock=clock) as cat:
        yield cat


def test_reads_before_any_write_create_nothing(tmp_path: Path) -> None:
    path = tmp_path / CATALOG_FILE_NAME
    with Catalog(path) as cat:
        assert cat.window_status(key()) == "absent"
        assert cat.window(key()) is None
        assert cat.windows() == []
        assert cat.bars_status(BarsKey("bars", "MES", 60, SESSION)) == "absent"
        assert cat.bars_coverage() == []
        assert cat.pulls() == []
        assert cat.pull("p1") is None
    assert not path.exists()


def test_schema_matches_the_design(catalog: Catalog) -> None:
    catalog.mark_window_incomplete(key())
    conn = sqlite3.connect(catalog.path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"windows", "bars_coverage", "pulls"}
        info = {t: conn.execute(f"PRAGMA table_info({t})").fetchall() for t in tables}
        assert [c[1] for c in info["windows"]] == [
            *("symbol", "metric", "view_id", "session", "start_ns", "status", "rows"),
            *("source_endpoint", "sha256", "updated_at"),
        ]
        pk = [c[1] for c in sorted(info["windows"], key=lambda c: c[5]) if c[5]]
        assert pk == ["symbol", "metric", "view_id", "session", "start_ns"]
        assert [c[1] for c in info["pulls"]] == ["pull_id", "started_at", "args_json"]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_window_lifecycle_sets_every_field(catalog: Catalog) -> None:
    k = key()
    catalog.mark_window_incomplete(k)
    record = catalog.window(k)
    assert record is not None
    assert (record.status, record.rows, record.source_endpoint, record.sha256) == (
        "incomplete",
        None,
        None,
        None,
    )
    assert (record.session, record.start_ns, record.updated_at) == (SESSION, k.start_ns, 1)

    catalog.finish_window(k, status="complete", rows=3, source_endpoint="range", sha256=SHA_A)
    record = catalog.window(k)
    assert record is not None
    assert (record.status, record.rows, record.source_endpoint, record.sha256) == (
        "complete",
        3,
        "range",
        SHA_A,
    )
    assert record.updated_at == 2

    # A refetch starts by marking the window incomplete again, which clears the result.
    catalog.mark_window_incomplete(k)
    assert catalog.window_status(k) == "incomplete"
    record = catalog.window(k)
    assert record is not None
    assert (record.rows, record.sha256) == (None, None)


@pytest.mark.parametrize(
    ("status", "rows", "sha", "match"),
    [
        ("incomplete", 0, SHA_A, "final status"),
        ("absent", 0, SHA_A, "final status"),
        ("no_data", 1, SHA_A, "no_data entry has 0 rows"),
        ("complete", -1, SHA_A, "rows must be"),
        ("complete", 1, "A" * 64, "sha256 must be"),
        ("complete", 1, "a" * 63, "sha256 must be"),
    ],
)
def test_finish_rejects_invalid_results(
    catalog: Catalog, status: str, rows: int, sha: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        catalog.finish_window(
            key(),
            status=status,  # type: ignore[arg-type]
            rows=rows,
            source_endpoint="range",
            sha256=sha,
        )
    assert catalog.window_status(key()) == "absent"


def test_failed_statement_leaves_no_row(catalog: Catalog) -> None:
    catalog.mark_window_incomplete(key())  # creates the schema
    with pytest.raises(sqlite3.IntegrityError):
        catalog.mark_window_incomplete(key(metric="delta"))  # CHECK constraint
    assert [r.metric for r in catalog.windows()] == ["gamma"]


def test_windows_filters_and_orders_by_key(catalog: Catalog) -> None:
    for k in (key(time(9, 15)), key(time(9, 0)), key(time(9, 0), symbol="QQQ")):
        catalog.mark_window_incomplete(k)
    assert [(r.symbol, r.start_ns) for r in catalog.windows()] == [
        ("QQQ", key(time(9, 0)).start_ns),
        ("SPX", key(time(9, 0)).start_ns),
        ("SPX", key(time(9, 15)).start_ns),
    ]
    assert len(catalog.windows(symbol="SPX", metric="gamma", session=SESSION)) == 2
    assert catalog.windows(session=date(2026, 3, 6)) == []


def test_bars_coverage_lifecycle(catalog: Catalog) -> None:
    k = BarsKey("bars", "MES", 60, SESSION)
    catalog.mark_bars_incomplete(k, contract="MESH6", source="atlas")
    assert catalog.bars_status(k) == "incomplete"
    catalog.finish_bars(
        k, status="complete", rows=5, contract="MESH6", source="atlas", sha256=SHA_B
    )
    vix = BarsKey("vix", "MES", 60, SESSION)  # same instrument, other dataset: separate row
    catalog.finish_bars(vix, status="no_data", rows=0, contract="VIX", source="atlas", sha256=SHA_A)
    [record] = catalog.bars_coverage(dataset="bars")
    assert (record.instrument, record.contract, record.source, record.rows) == (
        "MES",
        "MESH6",
        "atlas",
        5,
    )
    assert (record.status, record.sha256, record.session) == ("complete", SHA_B, SESSION)
    assert catalog.bars_status(vix) == "no_data"
    assert len(catalog.bars_coverage(instrument="MES", interval_s=60)) == 2


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        (("ticks", "MES", 60, SESSION), "dataset"),
        (("bars", "ME/S", 60, SESSION), "instrument"),
        (("bars", "MES", 0, SESSION), "interval_s"),
        (("bars", "MES", True, SESSION), "interval_s"),
        (("bars", "MES", 60, datetime(2026, 3, 5)), "session"),
    ],
)
def test_bars_key_validation(fields: tuple[object, ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        BarsKey(*fields)  # type: ignore[arg-type]


def test_pulls(catalog: Catalog) -> None:
    catalog.record_pull("pull-b", 20, '{"start":"2026-03-02"}')
    catalog.record_pull("pull-a", 10, "{}")
    assert [p.pull_id for p in catalog.pulls()] == ["pull-a", "pull-b"]
    record = catalog.pull("pull-b")
    assert record is not None
    assert (record.started_at, record.args_json) == (20, '{"start":"2026-03-02"}')
    with pytest.raises(ValueError, match="already recorded"):
        catalog.record_pull("pull-a", 30, "{}")
    with pytest.raises(ValueError, match="not valid JSON"):
        catalog.record_pull("pull-c", 30, "{not json")
    assert len(catalog.pulls()) == 2


def test_before_write_runs_once_before_the_file_exists(tmp_path: Path) -> None:
    path = tmp_path / CATALOG_FILE_NAME
    calls: list[bool] = []
    with Catalog(path, before_write=lambda: calls.append(path.exists())) as cat:
        cat.mark_window_incomplete(key())
        cat.mark_window_incomplete(key(time(9, 15)))
        cat.record_pull("p", 1, "{}")
    assert calls == [False]
    # A new object guards again, also when a read opened the existing file first.
    with Catalog(path, before_write=lambda: calls.append(path.exists())) as cat:
        assert cat.window_status(key()) == "incomplete"
        cat.mark_window_incomplete(key(time(9, 30)))
    assert calls == [False, True]


def test_state_persists_across_connections(tmp_path: Path) -> None:
    path = tmp_path / CATALOG_FILE_NAME
    with Catalog(path) as cat:
        cat.finish_window(
            key(), status="no_data", rows=0, source_endpoint="historical", sha256=SHA_A
        )
    with Catalog(path) as cat:
        assert cat.window_status(key()) == "no_data"


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / CATALOG_FILE_NAME
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with Catalog(path) as cat, pytest.raises(CatalogError, match="version 99"):
        cat.window_status(key())


def test_non_database_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / CATALOG_FILE_NAME
    path.write_bytes(b"not a sqlite database, just some bytes " * 4)
    with Catalog(path) as cat, pytest.raises(CatalogError, match="not a Data_Cache catalog"):
        cat.window_status(key())
