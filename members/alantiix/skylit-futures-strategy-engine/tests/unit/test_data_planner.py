"""Unit tests for the pull planner (design §3 "Planning", D11, OQ3).

The cache is a real :class:`DataCache` under ``tmp_path``. Nothing here makes
a network request or reads the clock: the ``GET /v1/symbols`` result and the
pull start instant are plain inputs.

**Validates: Requirements 3.3, 3.4, 3.5, 3.8, 3.14**
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest

from fse.data.cache import CACHE_WINDOW_NS, CacheWindowKey, DataCache, HeatmapView
from fse.data.planner import (
    DEFAULT_SYMBOLS,
    PullPlan,
    PullPlanError,
    PullSpec,
    ReplayRequest,
    plan_pull,
    pull_date,
)
from fse.engine.types import Snapshot
from fse.timekit import NS_PER_MINUTE, SessionCalendar, SessionTimes, ny_instant

TODAY = date(2026, 3, 6)  # a Friday
STARTED = ny_instant(TODAY, time(17, 0))  # after the day's Pull_Window
HOLIDAY = date(2026, 2, 16)  # Presidents' Day, a Monday
EARLY_CLOSE = date(2025, 11, 28)
CALENDAR = SessionCalendar(
    date(2024, 1, 1),
    date(2026, 12, 31),
    holidays=[HOLIDAY],
    early_closes={EARLY_CLOSE: time(13, 0)},
)
FIRST_HISTORY = date(2023, 3, 28)
LISTED: Mapping[str, date] = {s: FIRST_HISTORY for s in (*DEFAULT_SYMBOLS, "AAA", "BBB")}
VIEW = HeatmapView()
RECENT = date(2026, 3, 5)  # 1 day old: range path
OLD = date(2025, 3, 6)  # exactly 365 days old: historical path
MIN = NS_PER_MINUTE


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as c:
        yield c


def plan(
    spec: PullSpec,
    cache: DataCache,
    *,
    listed: Mapping[str, date] = LISTED,
    started_ns: int = STARTED,
    calendar: SessionCalendar = CALENDAR,
) -> PullPlan:
    return plan_pull(spec, listed=listed, started_ns=started_ns, calendar=calendar, cache=cache)


def one_day(d: date, **kw: Any) -> PullSpec:
    return PullSpec(d, d, **kw)


def at(request: ReplayRequest) -> int:
    """The ``/v1/historical`` instant of a request that must have one."""
    assert request.at_ns is not None
    return request.at_ns


def snap(key: CacheWindowKey) -> Snapshot:
    return Snapshot(
        symbol=key.symbol,
        metric=key.metric,
        view_id=key.view_id,
        as_of_ns=key.start_ns,
        as_of_raw="raw",
        spot=5800.25,
        previous_close=None,
        strikes=(5800.0,),
        values=(1.0e9,),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


# ---------------------------------------------------------------- validation (Req 3.14)


def test_invalid_range_and_unlisted_symbol_are_reported_together(cache: DataCache) -> None:
    spec = PullSpec(date(2026, 3, 5), date(2026, 3, 2), symbols=("SPX", "XYZ"))
    with pytest.raises(PullPlanError) as info:
        plan(spec, cache)
    assert info.value.exit_code == 2
    assert info.value.problems == (
        "pull start date 2026-03-05 is after the end date 2026-03-02",
        "symbol XYZ is not listed by GET /v1/symbols",
    )


def test_end_after_the_pull_date_is_rejected(cache: DataCache) -> None:
    with pytest.raises(
        PullPlanError, match="end date 2026-03-09 is after the current date 2026-03-06"
    ):
        plan(PullSpec(date(2026, 3, 2), date(2026, 3, 9)), cache)


def test_pull_date_is_the_new_york_date_of_the_start() -> None:
    # 2026-03-07 01:30 UTC is still 2026-03-06 in New York.
    assert pull_date(ny_instant(TODAY, time(20, 30))) == TODAY


@pytest.mark.parametrize(
    "fields",
    [
        {"sample_interval_minutes": 0},
        {"sample_interval_minutes": 16},
        {"sample_interval_minutes": True},
        {"label_sample_minutes": 0},
        {"label_sample_minutes": 61},
        {"symbols": ()},
        {"symbols": ("SPX", "SPX")},
        {"symbols": ("S/PX",)},
        {"metrics": ("delta",)},
    ],
)
def test_invalid_fields_are_rejected(cache: DataCache, fields: dict[str, Any]) -> None:
    with pytest.raises(PullPlanError) as info:
        plan(one_day(RECENT, **fields), cache)
    assert info.value.exit_code == 2
    assert info.value.problems


def test_calendar_must_cover_every_weekday_of_the_range(tmp_path: Path) -> None:
    calendar = SessionCalendar(date(2026, 1, 5), date(2026, 12, 31))
    with DataCache(tmp_path / "c", calendar=calendar) as c:
        with pytest.raises(PullPlanError, match="not 2026-01-02, a weekday"):
            plan(PullSpec(date(2026, 1, 2), date(2026, 1, 6)), c, calendar=calendar)
        # A weekend outside the calendar holds no session, so it is fine.
        p = plan(PullSpec(date(2026, 1, 3), date(2026, 1, 6)), c, calendar=calendar)
    assert [s.session for s in p.sessions] == [date(2026, 1, 6), date(2026, 1, 5)]


# ---------------------------------------------------------------- sessions and windows


def test_sessions_come_from_the_calendar_newest_first(cache: DataCache) -> None:
    p = plan(PullSpec(date(2026, 2, 12), date(2026, 2, 18)), cache)
    assert [s.session for s in p.sessions] == [
        date(2026, 2, 18),
        date(2026, 2, 17),
        date(2026, 2, 13),
        date(2026, 2, 12),
    ]
    sends = [r.session for r in p.requests()]
    assert sends == sorted(sends, reverse=True)


def test_pull_window_is_tiled_into_15_minute_windows(cache: DataCache, tmp_path: Path) -> None:
    (full,) = plan(one_day(RECENT), cache).sessions
    assert len(full.windows) == 28
    assert full.windows[0].start_ns == ny_instant(RECENT, time(9, 0))
    assert all(w.end_ns - w.start_ns == CACHE_WINDOW_NS for w in full.windows)
    assert full.windows[-1].end_ns == ny_instant(RECENT, time(16, 0))

    (early,) = plan(one_day(EARLY_CLOSE), cache).sessions
    assert len(early.windows) == 16
    assert early.windows[-1].end_ns == ny_instant(EARLY_CLOSE, time(13, 0))

    calendar = SessionCalendar(date(2026, 1, 1), date(2026, 12, 31), times=SessionTimes(time(9, 5)))
    with DataCache(tmp_path / "c", calendar=calendar) as c:
        (short,) = plan(one_day(RECENT), c, calendar=calendar).sessions
    assert len(short.windows) == 28
    last = short.windows[-1]
    assert (last.start_ns, last.end_ns) == (
        ny_instant(RECENT, time(15, 50)),
        ny_instant(RECENT, time(16, 0)),
    )


# ---------------------------------------------------------------- endpoints (Req 3.3, 3.4)


def test_recent_session_uses_one_range_request_per_window_metric_and_5_symbols(
    cache: DataCache,
) -> None:
    (session,) = plan(one_day(RECENT), cache).sessions
    assert (session.endpoint, session.age_days) == ("range", 1)
    requests = list(session.requests())
    assert len(requests) == 28 * 2  # the design's per-session budget
    for r in requests:
        assert (r.endpoint, r.path, r.at_ns, r.label_sample) == (
            "range",
            "/v1/historical/range",
            None,
            False,
        )
        assert r.symbols == DEFAULT_SYMBOLS
        assert r.window_end_ns - r.window_start_ns == CACHE_WINDOW_NS

    seven = (*DEFAULT_SYMBOLS, "AAA", "BBB")
    (session,) = plan(one_day(RECENT, symbols=seven), cache).sessions
    first = session.windows[0].requests
    assert [(r.metric, r.symbols) for r in first] == [
        ("gamma", DEFAULT_SYMBOLS),
        ("gamma", ("AAA", "BBB")),
        ("vanna", DEFAULT_SYMBOLS),
        ("vanna", ("AAA", "BBB")),
    ]


def test_sessions_365_days_old_switch_to_historical(cache: DataCache) -> None:
    p = plan(PullSpec(OLD, date(2025, 3, 7)), cache)
    assert [(s.session, s.age_days, s.endpoint) for s in p.sessions] == [
        (date(2025, 3, 7), 364, "range"),
        (OLD, 365, "historical"),
    ]


def test_old_session_samples_historical_on_the_interval_grid(cache: DataCache) -> None:
    (session,) = plan(one_day(OLD), cache).sessions
    assert len(list(session.requests())) == 420 * 2  # 60 s default: the design's budget

    symbols = (*DEFAULT_SYMBOLS, "AAA", "BBB", "SPX2", "QQQ2", "NDX2", "VIX2")
    listed = {**LISTED, **{s: FIRST_HISTORY for s in symbols}}
    (session,) = plan(
        one_day(OLD, symbols=symbols, sample_interval_minutes=7), cache, listed=listed
    ).sessions
    pull_start, pull_end = session.pull_start_ns, session.pull_end_ns
    grid = list(range(pull_start, pull_end, 7 * MIN))
    for metric in ("gamma", "vanna"):
        requests = [r for r in session.requests() if r.metric == metric]
        assert all(r.endpoint == "historical" and r.path == "/v1/historical" for r in requests)
        assert all(r.window_start_ns <= at(r) < r.window_end_ns for r in requests)
        assert sorted({at(r) for r in requests}) == grid
        assert [r.symbols for r in requests if r.at_ns == grid[0]] == [symbols[:10], symbols[10:]]


# ---------------------------------------------------------------- skipping (Req 3.5)


def test_sessions_before_first_history_are_skipped(cache: DataCache) -> None:
    listed = {**LISTED, "NDXP": RECENT}
    p = plan(PullSpec(date(2026, 3, 4), RECENT), cache, listed=listed)
    newest, older = p.sessions
    assert ("NDXP" in newest.symbols, newest.skipped_symbols) == (True, ())
    assert older.skipped_symbols == ("NDXP",)
    assert all("NDXP" not in r.symbols for r in older.requests())
    assert all(k.symbol != "NDXP" for w in older.windows for k in (*w.fetch, *w.served))
    assert list(p.skipped()) == [("NDXP", date(2026, 3, 4))]


# ---------------------------------------------------------------- cache (Req 3.8)


def test_completed_session_serves_complete_and_no_data_windows(cache: DataCache) -> None:
    start = ny_instant(RECENT, time(9, 15))

    def key(symbol: str) -> CacheWindowKey:
        return CacheWindowKey.for_view(symbol, "gamma", VIEW, RECENT, start)

    cache.write_window(key("SPX"), [snap(key("SPX"))], "range")  # complete
    cache.write_window(key("SPY"), [], "range")  # no_data
    cache.mark_incomplete(key("QQQ"))

    (session,) = plan(one_day(RECENT), cache).sessions
    assert session.completed
    window = session.windows[1]
    assert window.start_ns == start
    assert window.served == (key("SPX"), key("SPY"))
    assert key("QQQ") in window.fetch
    assert [(r.metric, r.symbols) for r in window.requests] == [
        ("gamma", ("QQQ", "NDX", "NDXP")),
        ("vanna", DEFAULT_SYMBOLS),
    ]
    p = plan(one_day(RECENT), cache)
    assert sum(1 for _ in p.served_keys()) == 2
    assert p.request_count == 56


def test_unfinished_session_fetches_even_complete_windows(cache: DataCache) -> None:
    key = CacheWindowKey.for_view("SPX", "gamma", VIEW, TODAY, ny_instant(TODAY, time(9, 0)))
    cache.write_window(key, [snap(key)], "range")
    (session,) = plan(one_day(TODAY), cache, started_ns=ny_instant(TODAY, time(10, 0))).sessions
    assert not session.completed
    assert session.windows[0].served == ()
    assert "SPX" in session.windows[0].requests[0].symbols


# ---------------------------------------------------------------- label samples (OQ3)


def test_label_samples_are_added_to_range_sessions_only(cache: DataCache) -> None:
    p = plan(PullSpec(OLD, date(2025, 3, 7), label_sample_minutes=30), cache)
    recent, old = p.sessions
    labels = [r for r in recent.requests() if r.label_sample]
    expected = list(range(recent.pull_start_ns, recent.pull_end_ns, 30 * MIN))
    assert len(expected) == 14
    for metric in ("gamma", "vanna"):
        mine = [r for r in labels if r.metric == metric]
        assert [r.at_ns for r in mine] == expected
        assert all(r.endpoint == "historical" and r.symbols == DEFAULT_SYMBOLS for r in mine)
        assert all(r.window_start_ns <= at(r) < r.window_end_ns for r in mine)
    assert sum(1 for r in recent.requests() if r.endpoint == "range") == 56
    assert not any(r.label_sample for r in old.requests())
