"""Unit tests for the pull coverage report (``fse.data.coverage``).

Every cache and output directory is under ``tmp_path``, outside any git
repository, except the path-guard test, which makes its own repository.

**Validates: Requirements 3.11, 4.2, 4.6, 4.8, 4.11, 4.13**
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest

from fse.calendars import ContractPeriod, Covers, RollCalendar
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import CACHE_WINDOW_NS, CacheWindowKey, DataCache, HeatmapView
from fse.data.coverage import (
    CoverageCollector,
    CoverageRequest,
    TimeRange,
    coverage_report,
    merge_ranges,
    missing_minute_ranges,
)
from fse.data.path_guard import PathGuardError
from fse.engine.types import Bar, DarkPoolPrint, Resolution, Snapshot
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import dumps
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, SessionCalendar, SessionTimes, ny_instant

D_MON, D_TUE, D_WED, D_THU, D_FRI = (date(2026, 3, d) for d in (2, 3, 4, 5, 6))
SESSIONS = (D_TUE, D_WED, D_THU, D_FRI)
# A one-hour Pull_Window keeps each session at four Cache_Windows: 09:00 to 09:45.
CALENDAR = SessionCalendar(
    date(2026, 3, 2),
    date(2026, 3, 13),
    early_closes={D_FRI: time(13, 0)},
    times=SessionTimes(pull_start=time(9, 0), pull_end=time(10, 0)),
)
ROLL = RollCalendar(
    path=Path("roll_calendar.yaml"),
    covers=Covers(date(2026, 3, 2), date(2026, 3, 13)),
    periods={
        "ES": (ContractPeriod("ES", date(2026, 3, 2), date(2026, 3, 13), "ESH6"),),
        "NQ": (ContractPeriod("NQ", D_THU, date(2026, 3, 13), "NQH6"),),
    },
)
VIEW = HeatmapView()
PULL_ID = "pull-1"
STARTED = ny_instant(date(2026, 3, 9), time(8, 0))
ENDED = STARTED + 3_600 * NS_PER_SECOND
SECRET = "fake-skylit-key-0000"


def at(d: date, hh: int, mm: int) -> int:
    return ny_instant(d, time(hh, mm))


def request(**overrides: Any) -> CoverageRequest:
    fields: dict[str, Any] = {
        "pull_id": PULL_ID,
        "started_at": STARTED,
        "first": D_TUE,
        "last": D_FRI,
        "sessions": SESSIONS,
        "symbols": ("SPX", "QQQ"),
        "view": VIEW,
        "metrics": ("gamma",),
        "first_history": {"QQQ": D_WED},
        "instruments": ("ES", "NQ"),
        "vix_intraday": True,
        "dark_pool_tickers": ("SPY",),
    }
    fields.update(overrides)
    return CoverageRequest(**fields)


def snap(symbol: str, as_of_ns: int, resolution: Resolution) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id=VIEW.view_id(),
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}",
        spot=5800.25,
        previous_close=5790.5,
        strikes=(5780.0, 5800.0),
        values=(-1.5e9, 3.0e9),
        node_types=None,
        expirations=("2026-03-05",),
        resolution=resolution,
        source_endpoint="range",
        extra_json="{}",
    )


def key(symbol: str, d: date, hh: int, mm: int) -> CacheWindowKey:
    return CacheWindowKey.for_view(symbol, "gamma", VIEW, d, at(d, hh, mm))


def bar(open_ns: int, instrument: str = "ES", contract: str = "ESH6") -> Bar:
    futures = instrument != "VIX"
    return Bar(
        instrument=instrument,
        contract=contract,
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=5800.0,
        h=5801.0,
        l=5799.0,
        c=5800.5,
        v=10.0,
        o_t=23200 if futures else None,
        h_t=23204 if futures else None,
        l_t=23196 if futures else None,
        c_t=23202 if futures else None,
        source="atlas",
    )


def minutes(d: date, start: tuple[int, int], end: tuple[int, int]) -> range:
    return range(at(d, *start), at(d, *end), NS_PER_MINUTE)


def vix_daily(d: date, open_: float | None, close: float | None) -> VixDailyRecord:
    return VixDailyRecord(
        session=d,
        open=open_,
        open_at_ns=None if open_ is None else at(d, 9, 31),
        close=close,
        close_at_ns=None if close is None else at(d, 16, 0),
        source="atlas",
    )


def fill_cache(cache: DataCache) -> None:
    """The stored state the report reads; see each test for what it implies."""
    # SPX gamma. Tue: nothing stored. Wed: four complete 1m windows. Thu: 09:00
    # complete (1s, two Snapshots), 09:15 no_data, 09:30 incomplete, 09:45 absent.
    # Fri: 09:00 complete, the rest absent.
    for hh, mm in ((9, 0), (9, 15), (9, 30), (9, 45)):
        k = key("SPX", D_WED, hh, mm)
        cache.write_window(k, [snap("SPX", k.start_ns + NS_PER_SECOND, "1m")], "range")
    k = key("SPX", D_THU, 9, 0)
    cache.write_window(
        k, [snap("SPX", k.start_ns + s * NS_PER_SECOND, "1s") for s in (1, 2)], "range"
    )
    cache.write_window(key("SPX", D_THU, 9, 15), [], "range")
    cache.mark_incomplete(key("SPX", D_THU, 9, 30))
    k = key("SPX", D_FRI, 9, 0)
    cache.write_window(k, [snap("SPX", k.start_ns + NS_PER_SECOND, "1m")], "range")

    # ES: Wed no_data, Thu missing 10:01 and 10:02, Fri full to the 13:00 early close.
    cache.bars.write_session("ES", 60, D_WED, [], contract="ESH6", source="atlas")
    thu = [
        bar(t)
        for t in minutes(D_THU, (9, 30), (16, 0))
        if t not in minutes(D_THU, (10, 1), (10, 3))
    ]
    cache.bars.write_session("ES", 60, D_THU, thu, contract="ESH6", source="atlas")
    fri = [bar(t) for t in minutes(D_FRI, (9, 30), (13, 0))]
    cache.bars.write_session("ES", 60, D_FRI, fri, contract="ESH6", source="atlas")

    # VIX: daily values from Mon to Thu; Tue intraday bars miss 15:59.
    cache.vix.write_daily(
        [
            vix_daily(D_MON, 19.5, 20.0),
            vix_daily(D_TUE, 21.0, 22.0),
            vix_daily(D_WED, 22.5, None),
            vix_daily(D_THU, None, 19.0),
        ]
    )
    tue = [bar(t, "VIX", "VIX") for t in minutes(D_TUE, (9, 30), (15, 59))]
    cache.vix.bars.write_session("VIX", 60, D_TUE, tue, contract="VIX", source="atlas")

    # SPY prints fetched for Tue (one print) and Wed (none).
    print_ns = at(D_TUE, 10, 0)
    cache.darkpool.write_trade_dates(
        "SPY", [D_TUE, D_WED], [DarkPoolPrint("SPY", print_ns, 580.0, 2_000, 1.16e6, "TRF")]
    )


def fill_collector(cov: CoverageCollector) -> None:
    cov.record_resolution("SPX", "gamma", D_TUE, "1s")  # fetched, never stored
    cov.record_resolution("SPX", "gamma", D_FRI, "1m")
    cov.record_no_data("SPX", "gamma", D_FRI, at(D_FRI, 9, 0), at(D_FRI, 9, 1))
    cov.record_no_data("SPX", "gamma", D_FRI, at(D_FRI, 9, 1), at(D_FRI, 9, 2))
    cov.record_atlas_probe("ES", served=True)
    cov.record_atlas_probe("NQ", served=False, error_code="504")
    cov.record_bar_fetch("ES", D_TUE, contract="ESH6", source="atlas", error_code="gateway_timeout")
    cov.record_bar_fetch("NQ", D_TUE, contract=None, source=None)
    cov.record_off_grid_price("ES", D_THU, open_ns=at(D_THU, 10, 0), column="c", price=5800.1)
    cov.record_dark_pool_error("SPY", [D_THU], "gateway_timeout")


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as c:
        fill_cache(c)
        yield c


def writer(*secrets: str) -> tuple[LogWriter, io.StringIO]:
    stderr = io.StringIO()
    return LogWriter(Redactor(secrets), stdout=io.StringIO(), stderr=stderr), stderr


def run(cache: DataCache, out_dir: Path, *, roll: RollCalendar | None = ROLL) -> dict[str, Any]:
    log_writer, _ = writer()
    with coverage_report(
        request(),
        cache=cache,
        calendar=CALENDAR,
        writer=log_writer,
        out_dir=out_dir,
        roll=roll,
        clock=lambda: ENDED,
    ) as cov:
        fill_collector(cov)
    report: dict[str, Any] = json.loads((out_dir / f"coverage_{PULL_ID}.json").read_text())
    return report


def window(d: date, hh: int, mm: int, status: str) -> dict[str, Any]:
    start = at(d, hh, mm)
    return {"start": start, "end": start + CACHE_WINDOW_NS, "status": status}


def gap(start: int, end: int, cause: str, code: str | None = None) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "minutes": (end - start) // NS_PER_MINUTE,
        "cause": cause,
        "error_code": code,
    }


def strip_ny(value: Any) -> Any:
    """``value`` without the ``*_ny`` display strings, for compact comparisons."""
    if isinstance(value, dict):
        return {k: strip_ny(v) for k, v in value.items() if not k.endswith("_ny")}
    if isinstance(value, list):
        return [strip_ny(v) for v in value]
    return value


# ---------------------------------------------------------------- sections


def test_heatmap_coverage_per_symbol_and_metric(cache: DataCache, tmp_path: Path) -> None:
    report = strip_ny(run(cache, tmp_path / "out"))
    assert report["outcome"] == "completed"
    assert report["problems"] == []
    spx, qqq = report["heatmaps"]
    assert (spx["symbol"], spx["metric"], spx["first_history"]) == ("SPX", "gamma", None)
    assert spx["sessions_requested"] == 4
    assert spx["sessions_stored"] == 3
    assert spx["sessions_skipped"] == 0
    assert spx["no_data_gaps"] == 2
    assert spx["incomplete_windows"] == 9
    tue, wed, thu, fri = spx["sessions"]

    assert (tue["stored"], tue["resolution"]) == (False, ["1s"])  # from the collector
    assert tue["windows"] == {"total": 4, "complete": 0, "no_data": 0, "incomplete": 0, "absent": 4}
    assert [w["status"] for w in tue["incomplete_windows"]] == ["absent"] * 4

    assert (wed["stored"], wed["snapshots"], wed["resolution"]) == (True, 4, ["1m"])  # from cache
    assert wed["no_data_gaps"] == []
    assert wed["incomplete_windows"] == []

    assert (thu["snapshots"], thu["resolution"]) == (2, ["1s"])
    assert thu["windows"] == {"total": 4, "complete": 1, "no_data": 1, "incomplete": 1, "absent": 1}
    assert thu["no_data_gaps"] == [{"start": at(D_THU, 9, 15), "end": at(D_THU, 9, 30)}]
    assert thu["incomplete_windows"] == [
        window(D_THU, 9, 30, "incomplete"),
        window(D_THU, 9, 45, "absent"),
    ]

    # Two touching no_data samples merge into one gap.
    assert fri["no_data_gaps"] == [{"start": at(D_FRI, 9, 0), "end": at(D_FRI, 9, 2)}]
    assert fri["resolution"] == ["1m"]

    assert qqq["first_history"] == "2026-03-04"
    assert (qqq["sessions_skipped"], qqq["sessions_stored"], qqq["incomplete_windows"]) == (
        1,
        0,
        12,
    )
    assert qqq["sessions"][0]["skipped"] is True
    assert qqq["sessions"][0]["windows"]["total"] == 0


def test_futures_probe_contract_source_and_missing_minutes(
    cache: DataCache, tmp_path: Path
) -> None:
    es, nq = strip_ny(run(cache, tmp_path / "out"))["futures"]
    assert es["atlas_probe"] == {"served": True, "error_code": None}
    assert nq["atlas_probe"] == {"served": False, "error_code": "504"}
    rth = {d: (at(d, 9, 30), at(d, 16, 0)) for d in SESSIONS}
    rth[D_FRI] = (at(D_FRI, 9, 30), at(D_FRI, 13, 0))  # early close

    tue, wed, thu, fri = es["sessions"]
    assert (tue["status"], tue["contract"], tue["source"]) == ("absent", "ESH6", "atlas")
    assert tue["missing_rth"] == [gap(*rth[D_TUE], "error", "gateway_timeout")]
    assert (wed["status"], wed["bars"]) == ("no_data", 0)
    assert wed["missing_rth"] == [gap(*rth[D_WED], "no_bars")]
    assert (thu["status"], thu["bars"], thu["missing_rth_minutes"]) == ("complete", 388, 2)
    assert thu["missing_rth"] == [gap(at(D_THU, 10, 1), at(D_THU, 10, 3), "no_bars")]
    assert thu["off_grid_prices"] == [{"open": at(D_THU, 10, 0), "column": "c", "price": 5800.1}]
    assert (fri["contract"], fri["source"], fri["bars"], fri["missing_rth"]) == (
        "ESH6",
        "atlas",
        210,
        [],
    )

    causes = [s["missing_rth"][0]["cause"] for s in nq["sessions"]]
    # Tue: the Bar_Source found no contract; Wed: the roll calendar has none.
    assert causes == ["no_contract", "no_contract", "not_fetched", "not_fetched"]
    assert [s["contract"] for s in nq["sessions"]] == [None] * 4
    assert nq["sessions"][3]["missing_rth"][0]["minutes"] == 210


def test_without_roll_calendar_unreached_sessions_are_not_fetched(
    cache: DataCache, tmp_path: Path
) -> None:
    nq = run(cache, tmp_path / "out", roll=None)["futures"][1]
    assert [s["missing_rth"][0]["cause"] for s in nq["sessions"]] == [
        "no_contract",
        "not_fetched",
        "not_fetched",
        "not_fetched",
    ]


def test_vix_and_dark_pool_gaps(cache: DataCache, tmp_path: Path) -> None:
    report = strip_ny(run(cache, tmp_path / "out"))
    vix = report["vix"]
    assert (vix["instrument"], vix["intraday"]) == ("VIX", True)
    by_session = {g["session"]: g for g in vix["gaps"]}
    assert by_session["2026-03-03"]["items"] == ["intraday_bars"]
    assert by_session["2026-03-03"]["intraday_missing"] == [
        {"start": at(D_TUE, 15, 59), "end": at(D_TUE, 16, 0)}
    ]
    assert by_session["2026-03-04"]["items"] == ["intraday_bars"]
    assert by_session["2026-03-05"]["items"] == ["daily_open", "prior_close", "intraday_bars"]
    assert by_session["2026-03-06"]["items"] == ["daily_open", "intraday_bars"]
    assert by_session["2026-03-06"]["intraday_missing"] == [
        {"start": at(D_FRI, 9, 30), "end": at(D_FRI, 13, 0)}
    ]

    assert report["dark_pool"] == {
        "enabled": True,
        "tickers": [
            {
                "ticker": "SPY",
                "gaps": [
                    {"session": "2026-03-04", "cause": "no_prints", "error_code": None},
                    {"session": "2026-03-05", "cause": "error", "error_code": "gateway_timeout"},
                    {"session": "2026-03-06", "cause": "not_fetched", "error_code": None},
                ],
            }
        ],
    }


def test_markdown_lists_each_section(cache: DataCache, tmp_path: Path) -> None:
    run(cache, tmp_path / "out")
    md = (tmp_path / "out" / f"coverage_{PULL_ID}.md").read_text(encoding="utf-8")
    for line in (
        f"# Coverage report for pull {PULL_ID}",
        "- Outcome: completed",
        "| SPX | gamma | unknown | 4 | 3 | 0 | 2 | 9 |",
        "| QQQ | gamma | 2026-03-04 | 4 | 0 | 1 | 0 | 12 |",
        "- 2026-03-03: skipped, dated before the first history date",
        "- 2026-03-05 09:15 to 09:30",
        "- 2026-03-05: 09:30 to 09:45 incomplete (1 window); 09:45 to 10:00 absent (1 window)",
        "- 2026-03-04 to 2026-03-06 (3 sessions): every window absent",
        "| NQ | not served, last error code `504` | 4 | 0 | 1380 |",
        "- 2026-03-03 to 2026-03-04 (2 sessions): no contract in the roll calendar",
        "- 2026-03-05 10:01 to 10:03 (2 min): no bars returned",
        "- 2026-03-03 09:30 to 16:00 (390 min): failed request, last error code `gateway_timeout`",
        "- 2026-03-05: daily open, prior-session close, 1-minute bars 09:30 to 16:00 (390 min)",
        "- 2026-03-04: no prints",
        "- 2026-03-06: not fetched (the pull ended first)",
    ):
        assert line in md.splitlines(), line


def test_json_is_canonical_and_deterministic(cache: DataCache, tmp_path: Path) -> None:
    run(cache, tmp_path / "a")
    run(cache, tmp_path / "b")
    for suffix in ("json", "md"):
        a = (tmp_path / "a" / f"coverage_{PULL_ID}.{suffix}").read_bytes()
        b = (tmp_path / "b" / f"coverage_{PULL_ID}.{suffix}").read_bytes()
        assert a == b
    text = (tmp_path / "a" / f"coverage_{PULL_ID}.json").read_text(encoding="ascii")
    assert text == dumps(json.loads(text)) + "\n"


def test_unreadable_window_becomes_a_problem(cache: DataCache, tmp_path: Path) -> None:
    cache.window_path(key("SPX", D_THU, 9, 0)).write_bytes(b"not parquet")
    report = run(cache, tmp_path / "out")
    assert report["heatmaps"][0]["sessions"][2]["resolution"] == []
    assert len(report["problems"]) == 1
    assert "cannot read the stored resolution" in report["problems"][0]
    md = (tmp_path / "out" / f"coverage_{PULL_ID}.md").read_text(encoding="utf-8")
    assert "- 2026-03-05: resolution unknown" in md.splitlines()
    assert "## Problems" in md


# ---------------------------------------------------------------- the finally block


def open_report(
    out_dir: Path, log_writer: LogWriter, req: CoverageRequest | None = None
) -> AbstractContextManager[CoverageCollector]:
    return coverage_report(
        request() if req is None else req,
        cache=DataCache(out_dir.parent / "empty-cache", calendar=CALENDAR),
        calendar=CALENDAR,
        writer=log_writer,
        out_dir=out_dir,
        clock=lambda: ENDED,
    )


def read_report(out_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((out_dir / f"coverage_{PULL_ID}.json").read_text())
    return result


def probe_then_interrupt(cov: CoverageCollector) -> None:
    cov.record_atlas_probe("ES", served=True)
    raise KeyboardInterrupt


def test_interrupt_still_writes_the_report(tmp_path: Path) -> None:
    out = tmp_path / "out"
    log_writer, _ = writer()
    with pytest.raises(KeyboardInterrupt), open_report(out, log_writer) as cov:
        probe_then_interrupt(cov)
    report = read_report(out)
    assert (report["outcome"], report["error"], report["ended_at"]) == ("interrupted", None, ENDED)
    assert report["futures"][0]["atlas_probe"] == {"served": True, "error_code": None}
    # Nothing was stored: every window is absent and no bars were fetched.
    assert report["heatmaps"][0]["sessions"][0]["windows"]["absent"] == 4
    assert report["futures"][1]["atlas_probe"] is None
    assert (out / f"coverage_{PULL_ID}.md").is_file()


def test_error_is_recorded_redacted_and_still_raised(tmp_path: Path) -> None:
    out = tmp_path / "out"
    log_writer, _ = writer(SECRET)
    with pytest.raises(RuntimeError, match="boom"), open_report(out, log_writer):
        raise RuntimeError(f"boom with {SECRET}")
    report = read_report(out)
    assert report["outcome"] == "failed"
    assert report["error"] == "RuntimeError: boom with [REDACTED]"
    for suffix in ("json", "md"):
        assert SECRET not in (out / f"coverage_{PULL_ID}.{suffix}").read_text(encoding="utf-8")


def test_mark_stopped_sets_the_outcome(tmp_path: Path) -> None:
    out = tmp_path / "out"
    log_writer, _ = writer()
    with open_report(out, log_writer) as cov:
        cov.mark_stopped("failed", "Data_Cache write failed for SPX gamma")
    report = read_report(out)
    assert (report["outcome"], report["error"]) == (
        "failed",
        "Data_Cache write failed for SPX gamma",
    )


def test_exit_130_counts_as_an_interrupt(tmp_path: Path) -> None:
    out = tmp_path / "out"
    log_writer, _ = writer()
    with pytest.raises(SystemExit), open_report(out, log_writer):
        raise SystemExit(130)
    assert read_report(out)["outcome"] == "interrupted"


def test_write_failure_keeps_the_pull_error(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    log_writer, stderr = writer()
    with pytest.raises(RuntimeError, match="pull failed") as info, open_report(blocker, log_writer):
        raise RuntimeError("pull failed")
    assert any("could not be written" in note for note in info.value.__notes__)
    assert "error: the coverage report for pull pull-1 could not be written" in stderr.getvalue()


def test_write_failure_after_a_normal_exit_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    log_writer, _ = writer()
    with pytest.raises(FileExistsError), open_report(blocker, log_writer):
        pass


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_path_guard_runs_before_the_pull(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    log_writer, _ = writer()
    body_ran = False
    with pytest.raises(PathGuardError), open_report(repo / "reports", log_writer):
        body_ran = True
    assert not body_ran
    assert not (repo / "reports").exists()


def test_a_requested_date_that_is_not_a_session_is_rejected(tmp_path: Path) -> None:
    log_writer, _ = writer()
    weekend = request(last=date(2026, 3, 7), sessions=(D_TUE, date(2026, 3, 7)))
    with (
        pytest.raises(ValueError, match="not a session"),
        open_report(tmp_path / "out", log_writer, weekend),
    ):
        pass
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- inputs


def test_collector_rejects_what_was_not_requested() -> None:
    cov = CoverageCollector(request())
    with pytest.raises(ValueError, match="symbol"):
        cov.record_resolution("NDX", "gamma", D_TUE, "1s")
    with pytest.raises(ValueError, match="metric"):
        cov.record_resolution("SPX", "vanna", D_TUE, "1s")
    with pytest.raises(ValueError, match="not a requested session"):
        cov.record_no_data("SPX", "gamma", D_MON, at(D_MON, 9, 0), at(D_MON, 9, 1))
    with pytest.raises(ValueError, match="resolution"):
        cov.record_resolution("SPX", "gamma", D_TUE, "5s")
    with pytest.raises(ValueError, match="instrument"):
        cov.record_atlas_probe("MES", served=True)
    with pytest.raises(ValueError, match="ticker"):
        cov.record_dark_pool_error("QQQ", [D_TUE], "504")
    with pytest.raises(ValueError, match="start before"):
        cov.record_no_data("SPX", "gamma", D_TUE, at(D_TUE, 9, 1), at(D_TUE, 9, 1))


def test_request_validation() -> None:
    with pytest.raises(ValueError, match="ascending"):
        request(sessions=(D_WED, D_TUE))
    with pytest.raises(ValueError, match="outside"):
        request(sessions=(D_MON,))
    with pytest.raises(ValueError, match="not a symbol"):
        request(first_history={"NDX": D_TUE})
    with pytest.raises(ValueError, match="pull_id"):
        request(pull_id="../escape")
    assert request(symbols=["SPX", "QQQ"]).symbols == ("SPX", "QQQ")


# ---------------------------------------------------------------- range helpers

M = NS_PER_MINUTE


def test_missing_minute_ranges() -> None:
    assert missing_minute_ranges([], 0, 5 * M) == (TimeRange(0, 5 * M),)
    full = [(i * M, (i + 1) * M) for i in range(5)]
    assert missing_minute_ranges(full, 0, 5 * M) == ()
    holes = [full[0], full[3]]
    assert missing_minute_ranges(holes, 0, 5 * M) == (
        TimeRange(M, 3 * M),
        TimeRange(4 * M, 5 * M),
    )
    # A 5 s bar covers its minute; bars outside the range are ignored.
    five_s = [(2 * M + 10 * NS_PER_SECOND, 2 * M + 15 * NS_PER_SECOND), (-M, 0), (5 * M, 6 * M)]
    assert missing_minute_ranges(five_s, 0, 5 * M) == (
        TimeRange(0, 2 * M),
        TimeRange(3 * M, 5 * M),
    )
    assert missing_minute_ranges(full, 5 * M, 5 * M) == ()


def test_merge_ranges() -> None:
    merged = merge_ranges(
        [
            TimeRange(5 * M, 6 * M),
            TimeRange(0, 2 * M),
            TimeRange(1 * M, 3 * M),
            TimeRange(3 * M, 4 * M),
        ]
    )
    assert merged == (TimeRange(0, 4 * M), TimeRange(5 * M, 6 * M))
    assert merge_ranges([]) == ()
