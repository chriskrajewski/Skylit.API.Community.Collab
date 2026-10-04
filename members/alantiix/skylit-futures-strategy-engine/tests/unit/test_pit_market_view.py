"""Unit tests for the historical MarketView, Map_State, Snapshot_Age and Futures_Price.

Design §5 "Point-in-time layer", "Time model" and D8. All data is synthetic; the
last test round-trips it through a Data_Cache under ``tmp_path``.

**Validates: Requirements 5.1, 5.2, 5.3**
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest

from fse.config.schema.data import DataConfig
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.engine.types import Bar, DarkPoolPrint, EconomicEvent, Snapshot, Unavailable
from fse.pit.asof import Received
from fse.pit.market_view import HistoricalInputs, HistoricalMarketView, heatmap_view
from fse.pit.protocols import MapState, MarketView, futures_price, missing_snapshot
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, SessionCalendar, ny_instant
from tests.unit.test_engine_imports import engine_violations

SESSION = date(2026, 3, 5)  # a Thursday, Eastern Standard Time
PRIOR = date(2026, 3, 4)
OPEN = ny_instant(SESSION, time(9, 30))
S = NS_PER_SECOND
M = NS_PER_MINUTE
VIEW_ID = HeatmapView().view_id()
OTHER_VIEW_ID = HeatmapView(max_strikes=40).view_id()
SYMBOLS = ("SPX", "QQQ")
PROTOCOLS = Path(__file__).resolve().parents[2] / "src" / "fse" / "pit" / "protocols.py"


def snap(symbol: str, metric: str, as_of_ns: int, **overrides: Any) -> Snapshot:
    fields: dict[str, Any] = {
        "symbol": symbol,
        "metric": metric,
        "view_id": VIEW_ID,
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


def bar(instrument: str, open_ns: int, close_t: int, interval_s: int = 60) -> Bar:
    """A futures bar with tick prices; close in ticks is ``close_t``."""
    c = close_t / 4
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=interval_s,
        open_ns=open_ns,
        close_ns=open_ns + interval_s * S,
        o=c,
        h=c + 1.0,
        l=c - 1.0,
        c=c,
        v=100.0,
        o_t=close_t,
        h_t=close_t + 4,
        l_t=close_t - 4,
        c_t=close_t,
        source="atlas",
    )


def vix_bar(open_ns: int, close: float, interval_s: int = 60) -> Bar:
    return Bar(
        "VIX", "VIX", interval_s, open_ns, open_ns + interval_s * S,
        close, close, close, close, 0.0, None, None, None, None, "atlas",
    )  # fmt: skip


def inputs(**kw: Any) -> HistoricalInputs:
    kw.setdefault("symbols", SYMBOLS)
    kw.setdefault("view_id", VIEW_ID)
    return HistoricalInputs(**kw)


# ---------------------------------------------------------------- Map_State


def test_map_state_holds_latest_as_of_at_or_before_t() -> None:
    early, mid, late = OPEN - 60 * S, OPEN - 30 * S, OPEN + 30 * S
    data = inputs(
        snapshots=[snap("SPX", "gamma", t) for t in (late, early, mid)]
        + [snap(s, "vanna", early) for s in SYMBOLS]
        + [snap("QQQ", "gamma", early, spot=580.1)]
    )
    ms = data.view(OPEN).map_state()
    assert list(ms) == [("SPX", "gamma"), ("SPX", "vanna"), ("QQQ", "gamma"), ("QQQ", "vanna")]
    assert ms.get("SPX", "gamma") == snap("SPX", "gamma", mid)
    assert data.view(late).map_state().get("SPX", "gamma") == snap("SPX", "gamma", late)
    assert data.view(late - 1).map_state().get("SPX", "gamma") == snap("SPX", "gamma", mid)
    assert ms.missing() == ()
    assert ms.t == OPEN


def test_missing_symbol_and_metric_is_unavailable_never_a_later_snapshot() -> None:
    data = inputs(snapshots=[snap("SPX", "gamma", OPEN), snap("QQQ", "vanna", OPEN + 5 * S)])
    ms = data.view(OPEN).map_state()
    assert ms.get("QQQ", "vanna") == missing_snapshot("QQQ", "vanna")
    entry = ms.get("SPX", "vanna")
    assert isinstance(entry, Unavailable)
    assert "SPX vanna" in entry.reason
    assert ms.missing() == (("SPX", "vanna"), ("QQQ", "gamma"), ("QQQ", "vanna"))
    assert ms.snapshots() == (snap("SPX", "gamma", OPEN),)


def test_map_state_uses_only_the_configured_view_and_symbols() -> None:
    data = inputs(
        snapshots=[
            snap("SPX", "gamma", OPEN - 10 * S),
            snap("SPX", "gamma", OPEN - 1 * S, view_id=OTHER_VIEW_ID),
            snap("NDX", "gamma", OPEN - 1 * S),
        ]
    )
    ms = data.view(OPEN).map_state()
    assert ms.get("SPX", "gamma") == snap("SPX", "gamma", OPEN - 10 * S)
    assert ("NDX", "gamma") not in ms
    ndx = ms.get("NDX", "gamma")
    assert isinstance(ndx, Unavailable)
    assert "not a configured" in ndx.reason


def test_map_state_selects_by_returned_as_of_not_by_arrival() -> None:
    """Replay: a Snapshot is usable once received, and Map_State keeps the newest asOf."""
    a = snap("SPX", "gamma", OPEN - 5 * S)  # received late
    b = snap("SPX", "gamma", OPEN - 20 * S)  # received on time
    c = snap("SPX", "gamma", OPEN - 10 * S)  # received last, older asOf than a
    data = inputs(
        snapshots=[
            Received(a, received_ns=OPEN + 20 * S),
            Received(b, received_ns=OPEN - 19 * S),
            Received(c, received_ns=OPEN + 25 * S),
        ]
    )
    assert data.view(OPEN).map_state().get("SPX", "gamma") == b
    assert data.view(OPEN + 20 * S).map_state().get("SPX", "gamma") == a
    assert data.view(OPEN + 30 * S).map_state().get("SPX", "gamma") == a


def test_snapshot_age_is_t_minus_oldest_as_of_present() -> None:
    data = inputs(
        snapshots=[snap("SPX", "gamma", OPEN - 40 * S), snap("QQQ", "gamma", OPEN - 3 * S)]
    )
    assert data.view(OPEN).map_state().snapshot_age_ns() == 40 * S
    empty = inputs().view(OPEN).map_state()
    age = empty.snapshot_age_ns()
    assert isinstance(age, Unavailable)


def test_map_state_rejects_a_snapshot_after_t_or_under_another_key() -> None:
    with pytest.raises(ValueError, match="after t"):
        MapState(OPEN, {("SPX", "gamma"): snap("SPX", "gamma", OPEN + 1)})
    with pytest.raises(ValueError, match="holds a QQQ gamma"):
        MapState(OPEN, {("SPX", "gamma"): snap("QQQ", "gamma", OPEN)})


def test_map_state_is_cached_per_view() -> None:
    view = inputs(snapshots=[snap("SPX", "gamma", OPEN)]).view(OPEN)
    assert view.map_state() is view.map_state()


# ---------------------------------------------------------------- Snapshot lookups


def test_snapshot_at_or_before_and_between_never_see_past_t() -> None:
    times = [OPEN - 90 * S, OPEN - 60 * S, OPEN - 30 * S, OPEN, OPEN + 30 * S]
    data = inputs(snapshots=[snap("SPX", "gamma", t) for t in times])
    view = data.view(OPEN)
    window = 60 * S
    assert view.snapshot_at_or_before("SPX", "gamma", OPEN - window) == snap(
        "SPX", "gamma", OPEN - 60 * S
    )
    assert view.snapshot_at_or_before("SPX", "gamma", OPEN - 61 * S) == snap(
        "SPX", "gamma", OPEN - 90 * S
    )
    assert view.snapshot_at_or_before("SPX", "gamma", OPEN + 60 * S) == snap("SPX", "gamma", OPEN)
    assert view.snapshot_at_or_before("SPX", "gamma", OPEN - 91 * S) is None
    assert view.snapshot_at_or_before("SPX", "vanna", OPEN) is None
    assert view.snapshot_at_or_before("NDX", "gamma", OPEN) is None
    between = list(view.snapshots_between("SPX", "gamma", OPEN - 60 * S, OPEN + 60 * S))
    assert [s.as_of_ns for s in between] == [OPEN - 60 * S, OPEN - 30 * S, OPEN]
    assert list(view.snapshots_between("NDX", "gamma", OPEN - M, OPEN)) == []


# ---------------------------------------------------------------- bars and Futures_Price


def test_a_bar_is_first_usable_at_its_close() -> None:
    """Req 5.3 example: the bar that opens at 09:30 is first usable at 09:31:00."""
    data = inputs(bars=[bar("MES", OPEN + k * M, 23200 + k) for k in (-1, 0, 1)])
    assert data.view(OPEN + M - 1).bars("MES", 60, OPEN) == ()
    assert [b.open_ns for b in data.view(OPEN + M).bars("MES", 60, OPEN)] == [OPEN]
    assert [b.open_ns for b in data.view(OPEN + 2 * M).bars("MES", 60, OPEN - M)] == [
        OPEN - M,
        OPEN,
        OPEN + M,
    ]
    assert data.view(OPEN + 2 * M).bars("MES", 300, OPEN) == ()
    assert data.view(OPEN + 2 * M).bars("MNQ", 60, OPEN) == ()


def test_futures_price_is_close_of_latest_one_minute_bar_closed_by_t() -> None:
    data = inputs(
        bars=[bar("MES", OPEN + k * M, 23200 + k) for k in range(3)]
        + [bar("MES", OPEN + 2 * M, 23999, interval_s=5)]  # not a 1-minute bar
    )
    assert futures_price(data.view(OPEN + 2 * M), "MES") == 23201
    assert futures_price(data.view(OPEN + 3 * M - 1), "MES") == 23201
    assert futures_price(data.view(OPEN + 3 * M), "MES") == 23202
    missing = futures_price(data.view(OPEN + M - 1), "MES")
    assert isinstance(missing, Unavailable)
    assert "MES" in missing.reason
    assert isinstance(futures_price(data.view(OPEN + 3 * M), "MNQ"), Unavailable)
    view = data.view(OPEN + 3 * M)
    paired = view.last_bar_closed_at_or_before("MES", OPEN + 2 * M + 30 * S)
    assert paired is not None
    assert paired.close_ns == OPEN + 2 * M


# ---------------------------------------------------------------- VIX, dark pool, events


def test_vix_values_appear_at_their_observation_times() -> None:
    today = VixDailyRecord(SESSION, 18.5, OPEN + M, None, None, "atlas")
    prior = VixDailyRecord(
        PRIOR, 17.0, ny_instant(PRIOR, time(9, 31)), 17.25, ny_instant(PRIOR, time(16)), "atlas"
    )
    data = inputs(vix_today=today, vix_prior=prior, vix_bars=[vix_bar(OPEN, 18.75)])
    before = data.view(OPEN + M - 1).vix()
    assert isinstance(before.daily_open, Unavailable)
    assert isinstance(before.last_1m_close, Unavailable)
    assert before.prior_close == 17.25
    after = data.view(OPEN + M).vix()
    assert (after.daily_open, after.prior_close, after.last_1m_close) == (18.5, 17.25, 18.75)

    none = inputs(vix_today=replace(today, open=None, open_at_ns=None)).view(OPEN + M).vix()
    assert isinstance(none.daily_open, Unavailable)
    assert "missing" in none.daily_open.reason
    assert isinstance(none.prior_close, Unavailable)


def test_dark_pool_unfetched_ticker_is_unavailable() -> None:
    prints = [DarkPoolPrint("SPY", OPEN + k * S, 580.0, 5000, 2.9e6, "TRF") for k in (-5, 5, 50)]
    data = inputs(dark_pool={"SPY": prints, "QQQ": []})
    view = data.view(OPEN + 10 * S)
    assert view.dark_pool("SPY", OPEN) == (prints[1],)
    assert view.dark_pool("SPY", OPEN - M) == (prints[0], prints[1])
    assert view.dark_pool("QQQ", OPEN) == ()
    unfetched = view.dark_pool("IWM", OPEN)
    assert isinstance(unfetched, Unavailable)
    assert "not fetched" in unfetched.reason


def test_events_are_all_visible_in_release_order() -> None:
    cpi = EconomicEvent("CPI", SESSION, ny_instant(SESSION, time(8, 30)))
    fomc = EconomicEvent("FOMC", SESSION, ny_instant(SESSION, time(14, 0)))
    view = inputs(events=[fomc, cpi]).view(OPEN)
    assert view.events() == (cpi, fomc)


def test_historical_view_satisfies_the_protocol() -> None:
    view = inputs().view(OPEN)
    assert isinstance(view, MarketView)
    assert isinstance(view, HistoricalMarketView)
    assert view.t == OPEN


def test_protocols_module_stays_inside_the_engine_import_rules() -> None:
    """The engine imports ``fse.pit.protocols``, so it may only use engine-allowed modules."""
    source = PROTOCOLS.read_text(encoding="utf-8")
    assert engine_violations(source, "fse.pit.protocols") == []


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"symbols": ("SPX", "SPX")}, "more than once"),
        ({"symbols": ("",)}, "non-blank"),
        ({"metrics": ("delta",)}, "metrics entries"),
        ({"view_id": ""}, "view_id"),
        ({"snapshots": ["not a snapshot"]}, r"snapshots\[0\] must be Snapshot, got str"),
        ({"bars": [Received("x", 1)]}, r"bars\[0\] must be Bar, got str"),
        ({"vix_bars": [vix_bar(OPEN, 18.0, interval_s=5)]}, "not a 1-minute bar"),
        (
            {"dark_pool": {"SPY": [DarkPoolPrint("QQQ", OPEN, 1.0, 1, 1.0, "TRF")]}},
            "print for 'QQQ'",
        ),
        (
            {
                "vix_today": VixDailyRecord(PRIOR, 18.0, OPEN, None, None, "atlas"),
                "vix_prior": VixDailyRecord(SESSION, 18.0, OPEN, None, None, "atlas"),
            },
            "not before",
        ),
        ({"events": ["CPI"]}, r"events\[0\] must be EconomicEvent"),
    ],
)
def test_invalid_inputs_raise(kw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        inputs(**kw)


def test_view_instant_must_be_an_integer() -> None:
    with pytest.raises(ValueError, match="integer Instant"):
        inputs().view(1.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------- Data_Cache round trip

CALENDAR = SessionCalendar(date(2026, 1, 1), date(2026, 12, 31), holidays=[date(2026, 4, 3)])


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as c:
        yield c


def test_view_over_inputs_read_from_the_data_cache(cache: DataCache) -> None:
    cfg = DataConfig()
    view_cfg = heatmap_view(cfg.heatmap_view)
    assert view_cfg == HeatmapView()
    window = ny_instant(SESSION, time(9, 15))
    key = CacheWindowKey.for_view("SPX", "gamma", view_cfg, SESSION, window)
    stored = [snap("SPX", "gamma", window + k * 5 * M + 1) for k in range(3)]
    cache.write_window(key, stored, "range", view=view_cfg)
    mes = [bar("MES", OPEN + k * M, 23200 + k) for k in range(-2, 2)]
    cache.bars.write_session("MES", 60, SESSION, mes, contract="MESH6", source="atlas")
    cache.vix.write_daily([VixDailyRecord(SESSION, 18.5, OPEN + M, None, None, "atlas")])
    spy = [DarkPoolPrint("SPY", OPEN - 2 * S, 580.0, 5000, 2.9e6, "TRF")]
    cache.darkpool.write_trade_dates("SPY", [SESSION], spy)

    data = HistoricalInputs(
        symbols=cfg.symbols,
        view_id=view_cfg.view_id(),
        snapshots=cache.read_window(key),
        bars=cache.bars.read_session("MES", 60, SESSION),
        vix_today=cache.vix.read_daily()[SESSION],
        dark_pool={
            t: cache.darkpool.read(t, SESSION, SESSION)
            for t in ("SPY",)
            if SESSION in cache.darkpool.fetched_dates(t)
        },
    )
    view = data.view(OPEN)
    ms = view.map_state()
    assert len(ms) == 2 * len(cfg.symbols)
    assert ms.get("SPX", "gamma") == stored[-1]
    assert isinstance(ms.get("SPY", "gamma"), Unavailable)
    assert ms.snapshot_age_ns() == OPEN - stored[-1].as_of_ns
    assert futures_price(view, "MES") == 23199
    assert isinstance(view.vix().daily_open, Unavailable)
    assert data.view(OPEN + M).vix().daily_open == 18.5
    assert view.dark_pool("SPY", OPEN - M) == tuple(spy)
