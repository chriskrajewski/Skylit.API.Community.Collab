"""Unit tests for the trailing regime medians (``fse.data.medians``).

Each Data_Cache lives under ``tmp_path`` and holds fake Snapshots; the
calendar is a synthetic six-week range with no holidays.

**Validates: Requirements 7.2, 7.12**
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date, time
from pathlib import Path

import pytest

from fse.config.schema.regime import RegimeConfig
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.data.cache_io import CacheIntegrityError
from fse.data.medians import (
    MedianParams,
    RegimeMedians,
    RegimeMedianStore,
    no_history,
    trailing_medians,
)
from fse.engine.regime import (
    TRAILING_SESSIONS,
    RegimeParams,
    RegimeResult,
    TrailingMedian,
    TrailingMedians,
    regime,
)
from fse.engine.types import Metric, MissingInput, Snapshot, Unavailable, VixState
from fse.pit.protocols import MapState
from fse.timekit import SessionCalendar, ny_instant

CALENDAR = SessionCalendar(date(2026, 1, 5), date(2026, 2, 13))  # 30 weekday sessions
SESSIONS = CALENDAR.sessions()
D0, D1, D2, D3 = SESSIONS[:4]
VIEW = HeatmapView()
OTHER_VIEW = HeatmapView(max_strikes="all")
VIEW_ID = VIEW.view_id()
PARAMS = MedianParams("SPX", 1.0)
HUGE = 1e12  # a magnitude that must never reach a median


def at(session: date, hh: int, mm: int, ss: int = 0) -> int:
    return ny_instant(session, time(hh, mm, ss))


def snap(
    as_of_ns: int,
    a: float,
    *,
    symbol: str = "SPX",
    metric: Metric = "gamma",
    view: HeatmapView = VIEW,
) -> Snapshot:
    """Raw magnitude ``a`` at 1% of spot 5800; the 5900 strike lies outside the distance."""
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=view.view_id(),
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}",
        spot=5800.0,
        previous_close=None,
        strikes=(5790.0, 5800.0, 5900.0),
        values=(-a, a / 2, HUGE),
        node_types=None,
        expirations=("2026-01-30",),
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


def full(median: float) -> TrailingMedian:
    return TrailingMedian(median, TRAILING_SESSIONS)


# ---------------------------------------------------------------- the pure computation


def test_trailing_window_holds_the_20_most_recent_earlier_sessions_with_values() -> None:
    days = SESSIONS[:23]
    daily = [(d, [] if i == 3 else [float(i)]) for i, d in enumerate(days)]
    out = dict(trailing_medians(daily, symbol="SPX", metric="gamma"))
    assert out[days[0]] == no_history("SPX", "gamma", days[0])
    assert out[days[1]] == TrailingMedian(0.0, 1)
    assert out[days[2]] == TrailingMedian(0.5, 2)
    assert out[days[4]] == TrailingMedian(1.0, 3)  # day 3 has no values and is not counted
    assert out[days[21]] == full(10.5)  # 0, 1, 2, 4..20
    assert out[days[22]] == full(11.5)  # day 0 left the window: 1, 2, 4..21


def test_a_session_pools_every_value_and_its_own_values_are_excluded() -> None:
    daily = [(D0, [1.0, 2.0, 9.0]), (D1, [3.0]), (D2, [HUGE])]
    out = dict(trailing_medians(daily, symbol="SPX", metric="vanna"))
    assert out[D1] == TrailingMedian(2.0, 1)
    assert out[D2] == TrailingMedian(2.5, 2)


def test_a_zero_median_covers_20_sessions_but_is_not_usable() -> None:
    daily = [(d, [0.0]) for d in SESSIONS[:21]]
    last = dict(trailing_medians(daily, symbol="SPX", metric="gamma"))[SESSIONS[20]]
    assert last == full(0.0)
    assert isinstance(last, TrailingMedian)
    assert not last.usable


@pytest.mark.parametrize(
    "daily",
    [[(D1, [1.0]), (D0, [1.0])], [(D0, [1.0]), (D0, [1.0])], [(D0, [-1.0]), (D1, [1.0])]],
)
def test_out_of_order_sessions_and_negative_magnitudes_are_rejected(
    daily: list[tuple[date, list[float]]],
) -> None:
    with pytest.raises(ValueError, match=r"sessions must strictly increase|finite and >= 0"):
        list(trailing_medians(daily, symbol="SPX", metric="gamma"))


# ---------------------------------------------------------------- parameters


def test_params_hash_covers_only_the_symbol_and_the_regime_distance() -> None:
    h = PARAMS.params_hash()
    assert len(h) == 16
    assert set(h) <= set("0123456789abcdef")
    assert MedianParams("SPX", 1.0).params_hash() == h
    assert MedianParams("NDX", 1.0).params_hash() != h
    assert MedianParams("SPX", 0.5).params_hash() != h
    a = RegimeParams(RegimeConfig(min_abs_value=1.0, vanna_multiple=3.0))
    b = RegimeParams(RegimeConfig(min_abs_value=9.0, regime_distance_pct=1.0))
    assert MedianParams.from_regime(a) == MedianParams.from_regime(b) == PARAMS
    for bad in (0.0, -1.0, 100.5, float("nan")):
        with pytest.raises(ValueError, match="regime_distance_pct"):
            MedianParams("SPX", bad)


# ---------------------------------------------------------------- the cache


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    """SPX RTH gamma 4, 2, 6 (D0) and 8 (D1); vanna 10 (D0). Everything else is left out."""
    c = DataCache(tmp_path / "cache")
    store(c, D0, 9, 15, [snap(at(D0, 9, 29, 59), HUGE), snap(at(D0, 9, 30), 4.0)])
    store(c, D0, 9, 30, [snap(at(D0, 9, 30), 4.0), snap(at(D0, 9, 31), 2.0)])  # 09:30 again
    store(c, D0, 15, 45, [snap(at(D0, 15, 59, 59), 6.0), snap(at(D0, 16, 0), HUGE)])
    store(c, D0, 9, 30, [snap(at(D0, 9, 31), 10.0, metric="vanna")], metric="vanna")
    c.mark_incomplete(CacheWindowKey.for_view("SPX", "gamma", VIEW, D1, at(D1, 9, 45)))
    store(c, D1, 9, 30, [snap(at(D1, 9, 35), 8.0)])
    store(c, D1, 9, 30, [snap(at(D1, 9, 35), HUGE, symbol="SPY")], symbol="SPY")
    store(c, D1, 9, 30, [snap(at(D1, 9, 35), HUGE, view=OTHER_VIEW)], view=OTHER_VIEW)
    yield c
    c.close()


def test_build_pools_rth_snapshots_of_the_regime_symbol_and_view(cache: DataCache) -> None:
    built = RegimeMedianStore(cache).build(CALENDAR, view_id=VIEW_ID, params=PARAMS)
    assert tuple(built.by_session) == SESSIONS
    assert built.incomplete_windows == 1
    assert built.for_session(D0) == TrailingMedians(
        gamma=no_history("SPX", "gamma", D0), vanna=no_history("SPX", "vanna", D0)
    )
    assert built.for_session(D1) == TrailingMedians(
        gamma=TrailingMedian(4.0, 1), vanna=TrailingMedian(10.0, 1)
    )
    # D1 has no vanna Snapshot, so the vanna window still holds D0 only.
    expected = TrailingMedians(gamma=TrailingMedian(5.0, 2), vanna=TrailingMedian(10.0, 1))
    assert built.for_session(D2) == built.for_session(D3) == expected
    with pytest.raises(ValueError, match="not a session"):
        built.for_session(date(2026, 1, 10))  # a Saturday


def test_short_medians_make_the_regime_missing_and_full_ones_feed_it(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache") as c:
        for d in SESSIONS[:TRAILING_SESSIONS]:
            store(c, d, 9, 30, [snap(at(d, 9, 31), 100.0)])
            store(c, d, 9, 30, [snap(at(d, 9, 31), 50.0, metric="vanna")], metric="vanna")
        built = RegimeMedianStore(c).build(CALENDAR, view_id=VIEW_ID, params=PARAMS)
    p = RegimeParams(
        RegimeConfig.model_validate({"min_abs_value": 1.0, "vix_condition": {"enabled": False}})
    )
    gamma, vanna = snap(0, 100.0), snap(0, 50.0, metric="vanna")
    ms = MapState(t=0, entries={("SPX", "gamma"): gamma, ("SPX", "vanna"): vanna})
    vix = VixState(daily_open=Unavailable("x"), prior_close=20.0, last_1m_close=Unavailable("x"))

    short = built.for_session(SESSIONS[TRAILING_SESSIONS - 1])
    assert short == TrailingMedians(gamma=TrailingMedian(100.0, 19), vanna=TrailingMedian(50.0, 19))
    assert regime(ms, short, vix, p) == MissingInput(
        ("SPX gamma trailing median", "SPX vanna trailing median")
    )
    ready = built.for_session(SESSIONS[TRAILING_SESSIONS])
    assert ready == TrailingMedians(gamma=full(100.0), vanna=full(50.0))
    result = regime(ms, ready, vix, p)
    assert isinstance(result, RegimeResult)
    assert (result.normalized_gex, result.normalized_vex) == (1.0, 1.0)


# ---------------------------------------------------------------- files


def test_write_and_read_round_trip_under_the_derived_layout(cache: DataCache) -> None:
    s = RegimeMedianStore(cache)
    built = s.build(CALENDAR, view_id=VIEW_ID, params=PARAMS)
    assert s.read(VIEW_ID, PARAMS) is None
    path = s.write(built)
    assert path == cache.root / "derived" / "regime_medians" / VIEW_ID / (
        f"{PARAMS.params_hash()}.parquet"
    )
    assert s.read(VIEW_ID, PARAMS) == built
    other = MedianParams("SPX", 2.0)
    assert s.read(VIEW_ID, other) is None
    shutil.copy(path, s.path(VIEW_ID, other))  # a file of other parameters
    with pytest.raises(CacheIntegrityError, match=r"fse\.params_hash"):
        s.read(VIEW_ID, other)


def test_load_or_build_reuses_the_file_until_the_inputs_change(cache: DataCache) -> None:
    s = RegimeMedianStore(cache)
    first = s.load_or_build(CALENDAR, view_id=VIEW_ID, params=PARAMS)
    path = s.path(VIEW_ID, PARAMS)
    stamp = (path.stat().st_ino, path.stat().st_mtime_ns)
    assert s.load_or_build(CALENDAR, view_id=VIEW_ID, params=PARAMS) == first
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == stamp  # not rewritten

    store(cache, D2, 9, 30, [snap(at(D2, 9, 40), 30.0)])  # a new pull
    assert s.inputs_sha256(CALENDAR, view_id=VIEW_ID, params=PARAMS) != first.inputs_sha256
    second = s.load_or_build(CALENDAR, view_id=VIEW_ID, params=PARAMS)
    assert second.for_session(D3).gamma == TrailingMedian(6.0, 3)  # 4, 2, 6, 8, 30
    assert s.read(VIEW_ID, PARAMS) == second

    path.write_bytes(b"not parquet")  # a damaged derived file is rebuilt
    assert s.load_or_build(CALENDAR, view_id=VIEW_ID, params=PARAMS) == second
    assert s.read(VIEW_ID, PARAMS) == second


def test_regime_medians_validate_their_fields() -> None:
    ok = TrailingMedians(gamma=full(1.0), vanna=full(1.0))
    with pytest.raises(ValueError, match="inputs_sha256"):
        RegimeMedians(VIEW_ID, PARAMS, "abc", {D0: ok})
    with pytest.raises(ValueError, match="TrailingMedians"):
        RegimeMedians(VIEW_ID, PARAMS, "0" * 64, {D0: full(1.0)})  # type: ignore[dict-item]
