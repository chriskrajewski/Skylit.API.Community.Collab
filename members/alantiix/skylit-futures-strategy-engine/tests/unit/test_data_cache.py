"""Unit tests for the heatmap Data_Cache (design §3, "Storage schemas").

Every cache directory is under ``tmp_path``, outside any git repository, except
in the path-guard tests, which build their own temporary repositories.

**Validates: Requirements 1.11, 3.6, 3.7, 3.8, 3.9, 3.10**
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from fse.data.cache import (
    CACHE_WINDOW_NS,
    CacheWindowKey,
    DataCache,
    HeatmapView,
    check_storage_interval,
    filter_storage_interval,
    storage_boundaries,
)
from fse.data.cache_io import CacheIntegrityError, CacheReadError, CacheWriteError
from fse.data.catalog import Catalog
from fse.data.path_guard import PathGuardError
from fse.engine.types import Snapshot
from fse.timekit import NS_PER_SECOND, SessionCalendar, ny_datetime, ny_instant

SESSION = date(2026, 3, 5)  # a Thursday, Eastern Standard Time
EARLY_CLOSE = date(2026, 11, 27)
CALENDAR = SessionCalendar(
    date(2026, 1, 1),
    date(2026, 12, 31),
    holidays=[date(2026, 4, 3)],
    early_closes={EARLY_CLOSE: time(13, 0)},
)
PULL_START = ny_instant(SESSION, time(9, 0))
WINDOW_0915 = PULL_START + CACHE_WINDOW_NS
VIEW = HeatmapView()
KEY = CacheWindowKey.for_view("SPX", "gamma", VIEW, SESSION, WINDOW_0915)
S = NS_PER_SECOND


def snap(as_of_ns: int, **overrides: Any) -> Snapshot:
    fields: dict[str, Any] = {
        "symbol": "SPX",
        "metric": "gamma",
        "view_id": VIEW.view_id(),
        "as_of_ns": as_of_ns,
        "as_of_raw": f"raw-{as_of_ns}",
        "spot": 5800.25,
        "previous_close": 5790.5,
        "strikes": (5780.0, 5800.0, 5825.0),
        "values": (-1.5e9, 3.0e9, 2.0e9),
        "node_types": None,
        "expirations": ("2026-03-05",),
        "resolution": "1s",
        "source_endpoint": "range",
        "extra_json": "{}",
    }
    fields.update(overrides)
    return Snapshot(**fields)


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as c:
        yield c


# ---------------------------------------------------------------- keys


def test_view_id_is_a_sha256_prefix_of_canonical_json() -> None:
    expected_json = (
        '{"expirations":null,"include_empty":false,"max_expirations":5,"max_strikes":92}'
    )
    assert VIEW.canonical_json() == expected_json
    assert VIEW.view_id() == hashlib.sha256(expected_json.encode()).hexdigest()[:16]


def test_view_id_differs_for_every_parameter() -> None:
    views = [
        VIEW,
        HeatmapView(max_strikes="all"),
        HeatmapView(max_strikes=50),
        HeatmapView(max_expirations="all"),
        HeatmapView(expirations=("2026-03-20",)),
        HeatmapView(include_empty=True),
    ]
    assert len({v.view_id() for v in views}) == len(views)
    assert HeatmapView(max_strikes=92).view_id() == VIEW.view_id()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_strikes": 0}, "max_strikes"),
        ({"max_strikes": True}, "max_strikes"),
        ({"max_strikes": "most"}, "max_strikes"),
        ({"max_expirations": -1}, "max_expirations"),
        ({"expirations": ()}, "non-empty tuple"),
        ({"expirations": ["2026-03-20"]}, "non-empty tuple"),
        ({"expirations": (" ",)}, "non-blank"),
        ({"include_empty": 1}, "include_empty"),
    ],
)
def test_view_rejects_invalid_parameters(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        HeatmapView(**kwargs)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"symbol": "SP/X"}, "symbol"),
        ({"symbol": ""}, "symbol"),
        ({"symbol": ".."}, "symbol"),
        ({"metric": "delta"}, "metric"),
        ({"view_id": "0123456789ABCDEF"}, "view_id"),
        ({"view_id": "abc"}, "view_id"),
        ({"session": datetime(2026, 3, 5, 9, 15)}, "session must be a date"),
        ({"start_ns": WINDOW_0915 + 1}, "whole minute"),
        ({"start_ns": ny_instant(date(2026, 3, 6), time(9, 15))}, "is not on session"),
    ],
)
def test_key_rejects_invalid_fields(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        replace(KEY, **overrides)


def test_window_path_layout(cache: DataCache) -> None:
    assert cache.window_path(KEY) == (
        cache.root / "heatmaps" / "SPX" / "gamma" / VIEW.view_id() / "2026-03-05" / "0915.parquet"
    )


@pytest.mark.parametrize(
    "start",
    [
        ny_instant(SESSION, time(9, 5)),  # not on the 15-minute tiling
        ny_instant(SESSION, time(16, 0)),  # the Pull_Window end
        ny_instant(SESSION, time(8, 45)),  # before the Pull_Window
        ny_instant(date(2026, 4, 3), time(9, 0)),  # an exchange holiday
        ny_instant(date(2026, 3, 7), time(9, 0)),  # a Saturday
    ],
)
def test_calendar_rejects_keys_that_are_not_window_starts(cache: DataCache, start: int) -> None:
    key = replace(KEY, session=ny_datetime(start).date(), start_ns=start)
    match = r"not a (Cache_Window start|session)"
    with pytest.raises(ValueError, match=match):
        cache.status(key)
    with pytest.raises(ValueError, match=match):
        cache.write_window(key, [], "range")
    assert not cache.root.exists()


def test_early_close_last_window_ends_at_the_close(cache: DataCache) -> None:
    last = replace(KEY, session=EARLY_CLOSE, start_ns=ny_instant(EARLY_CLOSE, time(12, 45)))
    assert cache.window_bounds(last) == (last.start_ns, ny_instant(EARLY_CLOSE, time(13, 0)))
    with pytest.raises(ValueError, match="not a Cache_Window start"):
        cache.status(replace(last, start_ns=ny_instant(EARLY_CLOSE, time(13, 0))))


# ---------------------------------------------------------------- status, write, read


def test_fresh_cache_creates_nothing(cache: DataCache) -> None:
    assert cache.status(KEY) == "absent"
    with pytest.raises(CacheReadError, match="absent"):
        cache.read_window(KEY)
    assert not cache.root.exists()


def test_round_trip_is_exact_in_every_field(cache: DataCache) -> None:
    other_axis = (-0.0, 5e-324, 1.7976931348623157e308)
    snaps = [
        snap(WINDOW_0915 + 3 * S, node_types=("king", None, "gatekeeper")),
        snap(
            WINDOW_0915 + 1 * S,
            as_of_raw="2026-03-05T14:15:01.123456789Z",
            previous_close=None,
            strikes=other_axis,
            values=(0.1 + 0.2, -0.0, float("inf")),
            expirations=(),
            resolution="1m",
            source_endpoint="historical",
            extra_json='{"note":"π ≥ 3","nested":{"a":[1,2.5]}}',
        ),
        snap(WINDOW_0915 + 2 * S, strikes=(), values=(), node_types=()),
        snap(WINDOW_0915 + 4 * S),  # shares the first Snapshot's axis
    ]
    cache.write_window(KEY, snaps, "range", view=VIEW)

    assert cache.status(KEY) == "complete"
    got = cache.read_window(KEY)
    assert got == sorted(snaps, key=lambda s: s.as_of_ns)
    assert str(got[0].strikes[0]) == "-0.0"
    path = cache.window_path(KEY)
    record = cache.record(KEY)
    assert record is not None
    assert (record.rows, record.source_endpoint) == (4, "range")
    assert record.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert [p.name for p in path.parent.iterdir()] == ["0915.parquet"]  # no temp file left


def test_empty_window_is_stored_as_no_data(cache: DataCache) -> None:
    cache.write_window(KEY, [], "historical")
    assert cache.status(KEY) == "no_data"
    assert cache.read_window(KEY) == []
    record = cache.record(KEY)
    assert record is not None
    assert (record.rows, record.source_endpoint) == (0, "historical")


@pytest.mark.parametrize(
    ("snapshots", "endpoint", "view", "match"),
    [
        ([snap(WINDOW_0915, symbol="SPY")], "range", None, r"snapshots\[0\] is SPY"),
        ([snap(WINDOW_0915, metric="vanna")], "range", None, r"snapshots\[0\] is SPX vanna"),
        ([snap(WINDOW_0915, view_id="f" * 16)], "range", None, r"snapshots\[0\]"),
        ([snap(WINDOW_0915)], "atlas", None, "source_endpoint must be"),
        ([snap(WINDOW_0915)], "range", HeatmapView(include_empty=True), "does not have view_id"),
    ],
)
def test_arguments_that_do_not_fit_the_key_write_nothing(
    cache: DataCache,
    snapshots: list[Snapshot],
    endpoint: str,
    view: HeatmapView | None,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        cache.write_window(KEY, snapshots, endpoint, view=view)
    assert cache.status(KEY) == "absent"
    assert not cache.root.exists()


def test_mark_incomplete_blocks_reads(cache: DataCache) -> None:
    cache.write_window(KEY, [snap(WINDOW_0915)], "range")
    cache.mark_incomplete(KEY)
    assert cache.status(KEY) == "incomplete"
    with pytest.raises(CacheReadError, match="incomplete"):
        cache.read_window(KEY)


def test_failed_rename_leaves_the_window_incomplete(
    cache: DataCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache.write_window(KEY, [snap(WINDOW_0915)], "range")  # complete before the refetch

    def fail(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("fse.data.cache_io.os.replace", fail)
    with pytest.raises(CacheWriteError) as info:
        cache.write_window(KEY, [snap(WINDOW_0915 + S)], "range")
    message = str(info.value)
    for part in ("SPX", "gamma", "2026-03-05", "09:15", "No space left on device"):
        assert part in message
    assert info.value.exit_code == 4
    assert cache.status(KEY) == "incomplete"
    assert not cache.window_path(KEY).with_name("0915.parquet.tmp").exists()


def test_failure_before_the_final_transaction_leaves_incomplete(
    cache: DataCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(self: Catalog, *args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt  # an Operator interrupt between steps 2 and 3

    monkeypatch.setattr(Catalog, "finish_window", crash)
    with pytest.raises(KeyboardInterrupt):
        cache.write_window(KEY, [snap(WINDOW_0915)], "range")
    assert cache.status(KEY) == "incomplete"
    assert cache.window_path(KEY).exists()  # the file is there; the catalog decides


def test_damaged_files_fail_the_integrity_check(cache: DataCache) -> None:
    cache.write_window(KEY, [snap(WINDOW_0915)], "range")
    path = cache.window_path(KEY)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(CacheIntegrityError, match="sha256"):
        cache.read_window(KEY)
    path.unlink()
    with pytest.raises(CacheIntegrityError, match="missing"):
        cache.read_window(KEY)


def test_windows_survive_a_new_cache_object(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as first:
        first.write_window(KEY, [snap(WINDOW_0915)], "range")
    with DataCache(tmp_path / "cache") as second:  # no calendar needed to read
        assert second.status(KEY) == "complete"
        assert second.read_window(KEY) == [snap(WINDOW_0915)]


def test_default_root_is_under_home(isolated_home: Path) -> None:
    with DataCache() as c:
        assert c.root == isolated_home.resolve() / ".skylit-fse" / "cache"
        assert c.status(KEY) == "absent"
    assert not (isolated_home / ".skylit-fse").exists()


# ---------------------------------------------------------------- storage interval


def test_storage_boundaries() -> None:
    assert list(storage_boundaries(0, 9, first_ns=10 * S, last_ns=36 * S)) == [
        18 * S,
        27 * S,
        36 * S,
    ]
    assert list(storage_boundaries(0, 5, last_ns=12 * S)) == [0, 5 * S, 10 * S]
    assert list(storage_boundaries(10 * S, 5, first_ns=0, last_ns=15 * S)) == [10 * S, 15 * S]


@pytest.mark.parametrize("bad", [0, 301, True, 1.5, "5"])
def test_storage_interval_must_be_1_to_300_whole_seconds(bad: object) -> None:
    with pytest.raises(ValueError, match="storage interval must be"):
        check_storage_interval(bad)


@pytest.mark.parametrize("bad", [7, 8, 120, 299])
def test_storage_interval_must_divide_the_cache_window(bad: int) -> None:
    with pytest.raises(ValueError, match="must divide the 900 s Cache_Window, got"):
        check_storage_interval(bad)


@pytest.mark.parametrize("good", [1, 5, 9, 225, 300])
def test_storage_interval_accepts_divisors_of_900(good: int) -> None:
    assert check_storage_interval(good) == good


def test_filter_keeps_the_latest_snapshot_per_boundary() -> None:
    # Boundaries at 0, 5, 10 and 15 s. Kept: -1 (for 0), 5 (for 5), 7 (for 10), 13 (for 15).
    seconds = [13, -1, 2, 4.5, 5, 7, 12.5, 13]  # one tie at 13 s
    snaps = [snap(int(x * S), as_of_raw=f"#{i}") for i, x in enumerate(seconds)]
    kept = filter_storage_interval(snaps, origin_ns=0, interval_s=5, last_ns=15 * S)
    assert [s.as_of_raw for s in kept] == ["#1", "#4", "#5", "#7"]  # the last 13 s one wins


def test_filter_keeps_one_snapshot_for_consecutive_boundaries() -> None:
    kept = filter_storage_interval([snap(S)], origin_ns=0, interval_s=5, last_ns=60 * S)
    assert kept == [snap(S)]
    assert filter_storage_interval([snap(61 * S)], origin_ns=0, interval_s=5, last_ns=60 * S) == []


def test_write_applies_the_storage_interval(tmp_path: Path) -> None:
    every_second = [snap(WINDOW_0915 + i * S) for i in range(-1, CACHE_WINDOW_NS // S)]
    with DataCache(tmp_path / "cache", calendar=CALENDAR, storage_interval_s=5) as c:
        c.write_window(KEY, every_second, "range")
        got = c.read_window(KEY)
    offsets = [(s.as_of_ns - WINDOW_0915) // S for s in got]
    # Boundaries 09:15:00 to 09:30:00 inclusive: one per 5 s, and the window's
    # last Snapshot (09:29:59) for the closing 09:30:00 boundary.
    assert offsets == [*range(0, 900, 5), 899]


def test_storage_interval_needs_a_calendar(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="calendar"):
        DataCache(tmp_path / "cache", storage_interval_s=5)
    with pytest.raises(ValueError, match="storage interval must be"):
        DataCache(tmp_path / "cache", calendar=CALENDAR, storage_interval_s=0)


# ---------------------------------------------------------------- path guard

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary repository that ignores ``ignored/``, free of the Operator's git config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    return root.resolve()


def _tree(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*")}


@needs_git
def test_cache_in_an_unignored_repo_dir_writes_nothing(git_repo: Path) -> None:
    before = _tree(git_repo)
    with DataCache(git_repo / "data" / "cache", calendar=CALENDAR) as c:
        assert c.status(KEY) == "absent"
        writes: list[Callable[[], object]] = [
            lambda: c.write_window(KEY, [snap(WINDOW_0915)], "range"),
            lambda: c.mark_incomplete(KEY),
            lambda: c.bars.write_session("MES", 60, SESSION, [], contract="MESH6", source="atlas"),
            lambda: c.vix.write_daily([]),
            lambda: c.darkpool.write_trade_dates("SPY", [SESSION], []),
            lambda: c.catalog.record_pull("p1", 1, "{}"),
        ]
        for write in writes:
            with pytest.raises(PathGuardError) as info:
                write()
            assert info.value.exit_code == 2
            assert str(git_repo / "data" / "cache") in str(info.value)
    assert _tree(git_repo) == before


@needs_git
def test_cache_in_an_ignored_repo_dir_is_written(git_repo: Path) -> None:
    with DataCache(git_repo / "ignored" / "cache", calendar=CALENDAR) as c:
        c.write_window(KEY, [snap(WINDOW_0915)], "range")
        assert c.read_window(KEY) == [snap(WINDOW_0915)]
