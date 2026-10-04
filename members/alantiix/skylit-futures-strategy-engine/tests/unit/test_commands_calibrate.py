"""Unit tests for ``fse calibrate regime-min-abs`` (``fse.commands.calibrate``).

Each run uses the Project's real ``calendars/exchange_calendar.yaml`` and a
Data_Cache under ``tmp_path`` filled with fake Snapshots. No network is used.

**Validates: Requirements 7.4**
"""

from __future__ import annotations

import io
from datetime import date, time
from pathlib import Path

import numpy as np
import pytest

from fse import cli
from fse.calendars import load_exchange_calendar
from fse.commands import calibrate
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.engine.types import Metric, Snapshot
from fse.timekit import ny_instant

PROJECT_DIR = Path(__file__).resolve().parents[2]
CALENDARS = PROJECT_DIR / "calendars"
CALENDAR = load_exchange_calendar(CALENDARS / "exchange_calendar.yaml").sessions

VIEW = HeatmapView()
OTHER_VIEW = HeatmapView(max_strikes="all")
WED, THU, FRI = date(2026, 3, 4), date(2026, 3, 5), date(2026, 3, 6)
NEXT_MON = date(2026, 3, 9)
EARLY_CLOSE = date(2025, 11, 28)  # 13:15 in exchange_calendar.yaml
RANGE = ("--start", "2026-03-02", "--end", "2026-03-06")  # five exchange sessions
HUGE = 1e12  # the King of every Snapshot that must be left out


def at(session: date, hh: int, mm: int, ss: int = 0) -> int:
    return ny_instant(session, time(hh, mm, ss))


def snap(
    as_of_ns: int,
    values: tuple[float, ...],
    *,
    symbol: str = "SPX",
    metric: Metric = "gamma",
    view: HeatmapView = VIEW,
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=view.view_id(),
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}",
        spot=5800.0,
        previous_close=5790.0,
        strikes=tuple(5790.0 + 10.0 * i for i in range(len(values))),
        values=values,
        node_types=None,
        expirations=("2026-03-06",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def store(
    cache: DataCache,
    session: date,
    hh: int,
    mm: int,
    snaps: list[Snapshot],
    *,
    symbol: str = "SPX",
    metric: Metric = "gamma",
    view: HeatmapView = VIEW,
) -> None:
    key = CacheWindowKey.for_view(symbol, metric, view, session, at(session, hh, mm))
    cache.write_window(key, snaps, "range", view=view)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """SPX gamma Kings 4, 3, 5 (Thursday) and 1, 2 (Friday) in RTH; every other value is HUGE."""
    root = tmp_path / "cache"
    with DataCache(root) as cache:
        store(cache, THU, 9, 15, [snap(at(THU, 9, 29, 59), (HUGE, 1.0))])  # before 09:30
        store(
            cache,
            THU,
            9,
            30,
            [snap(at(THU, 9, 30), (-4.0, 1.0)), snap(at(THU, 9, 30, 5), (2.0, -3.0))],
        )
        store(
            cache,
            THU,
            15,
            45,
            [snap(at(THU, 15, 59, 59), (5.0,)), snap(at(THU, 16, 0), (HUGE,))],  # 16:00 is out
        )
        store(cache, FRI, 9, 30, [snap(at(FRI, 9, 31), (1.0,)), snap(at(FRI, 9, 32), (-2.0,))])
        cache.mark_incomplete(CacheWindowKey.for_view("SPX", "gamma", VIEW, FRI, at(FRI, 9, 45)))
        store(cache, WED, 9, 30, [])  # no_data
        store(cache, THU, 9, 30, [snap(at(THU, 9, 31), (HUGE,), symbol="SPY")], symbol="SPY")
        store(cache, THU, 9, 30, [snap(at(THU, 9, 31), (HUGE,), metric="vanna")], metric="vanna")
        store(cache, THU, 9, 30, [snap(at(THU, 9, 31), (HUGE,), view=OTHER_VIEW)], view=OTHER_VIEW)
        store(cache, NEXT_MON, 9, 30, [snap(at(NEXT_MON, 9, 31), (HUGE,))])  # after --end
    return root


def run(*argv: str, cache_dir: Path | None = None) -> tuple[int, str, str]:
    args = ["calibrate", "regime-min-abs", *argv, "--calendar-dir", str(CALENDARS)]
    if cache_dir is not None:
        args += ["--cache-dir", str(cache_dir)]
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(args, project_dir=None, environ={}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- the command


def test_command_is_discovered_and_documented() -> None:
    assert calibrate.register in cli.discover_commands()
    out = io.StringIO()
    code = cli.run(
        ["calibrate", "regime-min-abs", "--help"],
        project_dir=None,
        environ={},
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == 0
    for text in ("--start", "--end", "--percentile", "--symbol", "regime.min_abs_value"):
        assert text in out.getvalue()


def test_calibrate_without_a_calibration_is_a_usage_error() -> None:
    err = io.StringIO()
    code = cli.run(["calibrate"], project_dir=None, environ={}, stdout=io.StringIO(), stderr=err)
    assert code == 2
    assert "<calibration>" in err.getvalue()


@pytest.mark.parametrize(
    ("p", "expected"), [("0", 1.0), ("50", 3.0), ("100", 5.0), ("10", None), ("62.5", None)]
)
def test_prints_the_percentile_of_rth_kings_and_the_session_count(
    cache_dir: Path, p: str, expected: float | None
) -> None:
    before = tree(cache_dir)

    code, out, err = run(*RANGE, "--percentile", p, cache_dir=cache_dir)

    assert (code, err) == (0, "")
    reference = float(np.percentile([4.0, 3.0, 5.0, 1.0, 2.0], float(p)))
    assert expected is None or reference == expected
    lines = out.splitlines()
    assert f"Heatmap_View {VIEW.view_id()}" in lines[0]
    assert "sessions: 2 of the 5 exchange sessions in the range" in lines
    assert "snapshots: 5" in lines
    assert any(line.startswith("incomplete windows skipped: 1;") for line in lines)
    assert f"percentile {float(p):g}: {reference!r}" in lines
    assert "No file was written" in lines[-1]
    assert tree(cache_dir) == before  # read only


def test_the_symbol_and_view_flags_select_other_windows(cache_dir: Path) -> None:
    code, out, _ = run(*RANGE, "--percentile", "50", "--symbol", "SPY", cache_dir=cache_dir)
    assert code == 0
    assert f"percentile 50: {HUGE!r}" in out.splitlines()

    code, out, _ = run(*RANGE, "--percentile", "50", "--max-strikes", "all", cache_dir=cache_dir)
    assert code == 0
    assert f"Heatmap_View {OTHER_VIEW.view_id()}" in out
    assert "sessions: 1 of the 5" in out


def test_no_matching_snapshot_exits_4_and_names_the_cached_views(cache_dir: Path) -> None:
    code, out, err = run(*RANGE, "--percentile", "50", "--max-strikes", "50", cache_dir=cache_dir)

    assert (code, out) == (4, "")
    assert "holds no RTH SPX gamma Snapshot" in err
    assert VIEW.view_id() in err
    assert OTHER_VIEW.view_id() in err


def test_an_empty_cache_exits_4_and_creates_nothing(tmp_path: Path) -> None:
    absent = tmp_path / "absent"
    code, _, err = run(*RANGE, "--percentile", "50", cache_dir=absent)

    assert code == 4
    assert "run fse pull for the range first" in err
    assert not absent.exists()


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (("--start", "2026-03-06", "--end", "2026-03-02", "--percentile", "5"), "is after --end"),
        (("--start", "2030-01-02", "--end", "2030-01-03", "--percentile", "5"), "outside"),
        ((*RANGE, "--percentile", "101"), "is not a percentile from 0 to 100"),
        ((*RANGE, "--percentile", "nan"), "is not a percentile from 0 to 100"),
        ((*RANGE, "--percentile", "-1"), "is not a percentile from 0 to 100"),
        (("--start", "03/02/2026", "--end", "2026-03-06", "--percentile", "5"), "YYYY-MM-DD"),
        ((*RANGE,), "--percentile"),
    ],
)
def test_invalid_input_exits_2(cache_dir: Path, argv: tuple[str, ...], reason: str) -> None:
    code, out, err = run(*argv, cache_dir=cache_dir)

    assert (code, out) == (2, "")
    assert reason in err


def test_the_calendar_dir_is_needed_without_a_project_folder(cache_dir: Path) -> None:
    err = io.StringIO()
    code = cli.run(
        ["calibrate", "regime-min-abs", *RANGE, "--percentile", "5", "--cache-dir", str(cache_dir)],
        project_dir=None,
        environ={},
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 2
    assert "pass --calendar-dir DIR" in err.getvalue()


# ---------------------------------------------------------------- the functions


def test_rth_ends_at_the_early_close(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache") as cache:
        store(
            cache,
            EARLY_CLOSE,
            13,
            0,
            [snap(at(EARLY_CLOSE, 13, 14, 59), (7.0,)), snap(at(EARLY_CLOSE, 13, 15), (HUGE,))],
        )
        sample = calibrate.collect_king_values(
            cache,
            CALENDAR,
            symbol="SPX",
            view_id=VIEW.view_id(),
            start=EARLY_CLOSE,
            end=EARLY_CLOSE,
        )
    assert sample == calibrate.KingSample(
        values=(7.0,), sessions=1, sessions_in_range=1, incomplete_windows=0
    )


def test_percentile_interpolates_between_ranks() -> None:
    values = [10.0, 30.0, 20.0, 40.0]
    assert calibrate.percentile(values, 0) == 10.0
    assert calibrate.percentile(values, 100) == 40.0
    assert calibrate.percentile(values, 50) == 25.0
    assert calibrate.percentile([3.5], 37.0) == 3.5
    with pytest.raises(ValueError, match="no values"):
        calibrate.percentile([], 50)
    with pytest.raises(ValueError, match="from 0 to 100"):
        calibrate.percentile(values, 100.5)
