"""Unit tests for the Tap tracker (design §6 "Tap tracker").

Bars are synthetic 1-minute futures bars on 0.25-point ticks. ``node_bands``
reads Snapshots and bars through the real historical MarketView.

**Validates: Requirements 6.14**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time

import pytest

from fse.config.schema.nodes import NodesConfig
from fse.engine.levels import ConvertedMap, LevelParams, convert_map, round_to_tick
from fse.engine.nodes import NodeParams, classify
from fse.engine.taps import NodeBand, NodeId, TapState, is_rth_bar, node_bands, session_of
from fse.engine.types import Bar, Metric, Snapshot
from fse.pit.market_view import HistoricalInputs, HistoricalMarketView
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, ny_instant

S = NS_PER_SECOND
THU = date(2026, 3, 5)
FRI = date(2026, 3, 6)
MON = date(2026, 3, 9)
TUE = date(2026, 3, 10)

SPX_NODE: NodeId = ("SPX", "gamma", 5800.0)
BAND = NodeBand("SPX", "gamma", 5800.0, "MES", 5805.0, 5815.0)
IN = (5810.0, 5811.0)  # a bar range inside BAND
OUT = (5790.0, 5791.0)  # a bar range below BAND


def at(d: date, h: int, m: int) -> int:
    return ny_instant(d, time(h, m))


def mbar(instrument: str, open_ns: int, low: float, high: float, close: float | None = None) -> Bar:
    """A 1-minute futures bar opening at ``open_ns``."""
    c = high if close is None else close
    lo_t, hi_t, c_t = round_to_tick(low), round_to_tick(high), round_to_tick(c)
    return Bar(
        instrument, f"{instrument}H6", 60, open_ns, open_ns + 60 * S,
        low, high, low, c, 1.0, lo_t, hi_t, lo_t, c_t, "atlas",
    )  # fmt: skip


def feed(
    ranges: Sequence[tuple[float, float]],
    d: date = THU,
    state: TapState | None = None,
    bands: Sequence[NodeBand] = (BAND,),
) -> TapState:
    """MES bars from 09:30 on ``d``, one per range, each with ``bands``."""
    s = TapState.initial() if state is None else state
    start = at(d, 9, 30)
    for i, (low, high) in enumerate(ranges):
        s = s.on_bar(mbar("MES", start + i * NS_PER_MINUTE, low, high), bands)
    return s


# ---------------------------------------------------------------- sessions


@pytest.mark.parametrize(
    ("d", "h", "m", "rth"),
    [
        (THU, 9, 29, False),
        (THU, 9, 30, True),
        (THU, 15, 59, True),
        (THU, 16, 0, False),
        (date(2026, 1, 6), 9, 30, True),  # standard time: 14:30 UTC
        (date(2026, 1, 6), 9, 29, False),
        (date(2026, 3, 7), 10, 0, False),  # Saturday
    ],
)
def test_is_rth_bar_uses_new_york_09_30_to_16_00_on_weekdays(
    d: date, h: int, m: int, rth: bool
) -> None:
    assert is_rth_bar(mbar("MES", at(d, h, m), *IN)) is rth


def test_session_of_rolls_to_the_next_date_at_18_00() -> None:
    assert session_of(at(THU, 9, 30)) == THU
    assert session_of(at(THU, 17, 59)) == THU
    assert session_of(at(THU, 18, 0)) == FRI


# ---------------------------------------------------------------- counting


def test_each_run_of_overlapping_bars_is_one_tap() -> None:
    state = feed([IN, IN, OUT, IN, OUT, OUT, IN])
    view = state.view(at(THU, 9, 37))
    taps = view.session_taps(SPX_NODE)
    assert [tap.seq for tap in taps] == [1, 2, 3]
    assert [(tap.first_open_ns, tap.end_ns) for tap in taps] == [
        (at(THU, 9, 30), at(THU, 9, 32)),  # ends at the close of its last overlapping bar
        (at(THU, 9, 33), at(THU, 9, 34)),
        (at(THU, 9, 36), at(THU, 9, 37)),
    ]
    assert taps[0].first_close_ns == at(THU, 9, 31)
    assert [tap.ended for tap in taps] == [True, True, False]
    assert view.session_count(SPX_NODE) == view.weekly_count(SPX_NODE) == 3
    assert view.ended_count(SPX_NODE) == 2
    assert view.tap_seq(SPX_NODE) == 3
    assert view.latest_tap(SPX_NODE) == taps[-1]
    assert view.session_count(("SPX", "gamma", 5825.0)) == 0


@pytest.mark.parametrize(
    ("low", "high", "overlaps"),
    [
        (5800.0, 5805.0, True),  # high touches the lower edge
        (5815.0, 5820.0, True),  # low touches the upper edge
        (5795.0, 5825.0, True),  # the bar spans the band
        (5800.0, 5804.75, False),  # one tick below
        (5815.25, 5820.0, False),  # one tick above
    ],
)
def test_overlap_includes_the_band_edges(low: float, high: float, overlaps: bool) -> None:
    view = feed([(low, high)]).view(at(THU, 9, 31))
    assert view.session_count(SPX_NODE) == int(overlaps)


def test_only_rth_bars_count_and_the_first_rth_bar_starts_a_tap() -> None:
    pre = mbar("MES", at(THU, 9, 29), *IN)
    state = TapState.initial().on_bar(pre, (BAND,))
    assert state.view(at(THU, 9, 30)).session_count(SPX_NODE) == 0
    state = feed([IN], state=state)
    tap = state.view(at(THU, 9, 31)).session_taps(SPX_NODE)[0]
    assert tap.first_open_ns == at(THU, 9, 30)
    late = feed([IN] * 2, state=TapState.initial())
    after_close = late.on_bar(mbar("MES", at(THU, 16, 0), *OUT), (BAND,))
    # A bar after 16:00 neither counts nor ends the open Tap.
    assert after_close.view(at(THU, 16, 1)).session_taps(SPX_NODE)[0].ended is False


def test_a_node_missing_from_the_map_state_does_not_overlap() -> None:
    state = feed([IN])
    state = state.on_bar(mbar("MES", at(THU, 9, 31), *IN), ())  # no band at this close
    state = state.on_bar(mbar("MES", at(THU, 9, 32), *IN), (BAND,))
    taps = state.view(at(THU, 9, 33)).session_taps(SPX_NODE)
    assert [(tap.seq, tap.ended) for tap in taps] == [(1, True), (2, False)]


def test_bars_of_another_instrument_neither_tap_nor_end_a_tap() -> None:
    state = feed([IN])
    state = state.on_bar(mbar("MNQ", at(THU, 9, 31), *OUT), (BAND,))
    state = state.on_bar(mbar("MNQ", at(THU, 9, 32), *IN), (BAND,))
    state = state.on_bar(mbar("MES", at(THU, 9, 31), *IN), (BAND,))
    view = state.view(at(THU, 9, 33))
    (tap,) = view.session_taps(SPX_NODE)
    assert (tap.ended, tap.end_ns) == (False, at(THU, 9, 32))


def test_counts_are_kept_per_node() -> None:
    other = NodeBand("SPX", "vanna", 5800.0, "MES", 5805.0, 5815.0)
    wide = NodeBand("SPX", "gamma", 5790.0, "MES", 5785.0, 5795.0)
    state = feed([IN, OUT, IN], bands=(BAND, other, wide))
    view = state.view(at(THU, 9, 33))
    assert view.session_count(SPX_NODE) == 2
    assert view.session_count(other.node) == 2
    assert view.session_count(wide.node) == 1


# ---------------------------------------------------------------- resets


def test_session_counts_reset_daily_and_weekly_counts_reset_weekly() -> None:
    thu = feed([IN, OUT, IN])  # 2 Taps, the second still open at the end of the session
    fri_open = thu.view(at(FRI, 9, 30))
    assert fri_open.session == FRI
    assert fri_open.session_count(SPX_NODE) == 0
    assert fri_open.weekly_count(SPX_NODE) == 2
    latest = fri_open.latest_tap(SPX_NODE)
    assert latest is not None
    assert (latest.session, latest.seq, latest.ended) == (THU, 2, True)

    fri = feed([IN], d=FRI, state=thu)
    view = fri.view(at(FRI, 9, 31))
    assert view.session_count(SPX_NODE) == 1
    assert view.weekly_count(SPX_NODE) == 3
    assert view.tap_seq(SPX_NODE) == 1
    fri_tap = view.latest_tap(SPX_NODE)
    assert fri_tap is not None
    assert fri_tap.session == FRI

    mon = fri.view(at(MON, 9, 30))
    assert mon.weekly_count(SPX_NODE) == 0
    assert mon.latest_tap(SPX_NODE) is None
    # Monday holiday: Tuesday is the week's first session.
    tue = feed([IN], d=TUE, state=fri).view(at(TUE, 9, 31))
    assert (tue.session_count(SPX_NODE), tue.weekly_count(SPX_NODE)) == (1, 1)


# ---------------------------------------------------------------- point in time and errors


def test_view_raises_before_a_consumed_bar_close() -> None:
    state = feed([IN])
    with pytest.raises(ValueError, match="requested after consuming MES bars"):
        state.view(at(THU, 9, 31) - 1)


def test_states_are_values() -> None:
    first = feed([IN])
    second = first.on_bar(mbar("MES", at(THU, 9, 31), *OUT), (BAND,))
    # on_bar leaves ``first`` unchanged.
    assert len(first.open_taps) == 1
    assert first.ended_taps == ()
    bars = [mbar("MES", at(THU, 9, 30 + i), *r) for i, r in enumerate([IN, OUT])]
    assert TapState.initial().on_bars((b, (BAND,)) for b in bars) == second


def test_bad_bars_and_bands_are_rejected() -> None:
    state = feed([IN])
    with pytest.raises(ValueError, match="takes 60 s bars"):
        state.on_bar(
            Bar("MES", "MESH6", 300, at(THU, 9, 35), at(THU, 9, 40),
                1.0, 1.0, 1.0, 1.0, 1.0, 4, 4, 4, 4, "atlas"),
            (),
        )  # fmt: skip
    with pytest.raises(ValueError, match="no tick prices"):
        state.on_bar(
            Bar("VIX", "VIX", 60, at(THU, 9, 31), at(THU, 9, 32),
                1.0, 1.0, 1.0, 1.0, 0.0, None, None, None, None, "atlas"),
            (),
        )  # fmt: skip
    with pytest.raises(ValueError, match="out of order"):
        state.on_bar(mbar("MES", at(THU, 9, 30), *IN), (BAND,))
    with pytest.raises(ValueError, match="two Deflection_Bands"):
        state.on_bar(mbar("MES", at(THU, 9, 31), *IN), (BAND, BAND))
    with pytest.raises(ValueError, match="lo <= hi"):
        NodeBand("SPX", "gamma", 5800.0, "MES", 5815.0, 5805.0)


# ---------------------------------------------------------------- bands at a bar's close

VIEW_ID = "view-test"
NP = NodeParams.from_config(NodesConfig())
P = LevelParams()
T0 = at(THU, 10, 0)


def snap(
    symbol: str,
    as_of_ns: int,
    spot: float,
    strikes: tuple[float, ...],
    values: tuple[float, ...],
    metric: Metric = "gamma",
) -> Snapshot:
    return Snapshot(
        symbol=symbol, metric=metric, view_id=VIEW_ID, as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}", spot=spot, previous_close=None, strikes=strikes,
        values=values, node_types=None, expirations=("2026-03-05",), resolution="1s",
        source_endpoint="range", extra_json="{}",
    )  # fmt: skip


# Nodes 5780 and 5825 (5800 is below 20% of the King); MES 5810 gives an offset of +10.
SPX = snap("SPX", T0 - 30 * S, 5800.0, (5780.0, 5800.0, 5825.0), (1e9, 1e8, 5e8))
QQQ = snap("QQQ", T0 - 30 * S, 500.0, (490.0, 500.0), (1e9, 1e9))  # ratio 40
NDX = snap("NDX", T0 - 30 * S, 16000.0, (16000.0,), (1e9,))  # ratio 1.25
BARS = [
    mbar("MES", T0 - 120 * S, 5805.0, 5812.0, 5810.0),
    mbar("MNQ", T0 - 120 * S, 19990.0, 20005.0, 20000.0),
]


def hview(snapshots: list[Snapshot], bars: list[Bar], t: int = T0) -> HistoricalMarketView:
    inputs = HistoricalInputs(
        symbols=("SPX", "QQQ", "NDX"), view_id=VIEW_ID, snapshots=snapshots, bars=bars
    )
    return inputs.view(t)


def test_node_bands_at_t_match_the_level_converter() -> None:
    view = hview([SPX, QQQ, NDX], BARS)
    bands = node_bands(view, T0, NP, P)
    converted = convert_map(view, P)
    for b in bands:
        cm = converted[(b.symbol, b.metric)]
        assert isinstance(cm, ConvertedMap)
        assert (b.lo, b.hi) == cm.band_of(b.strike)
    by_symbol = {s.symbol: [b.strike for b in bands if b.symbol == s.symbol] for s in (SPX, QQQ)}
    assert by_symbol == {"SPX": list(classify(SPX, NP).nodes), "QQQ": [490.0, 500.0]}
    spx = [(b.instrument, b.lo, b.hi) for b in bands if b.symbol == "SPX"]
    assert spx == [("MES", 5785.0, 5795.0), ("MES", 5830.0, 5840.0)]
    ndx = [(b.instrument, b.lo, b.hi) for b in bands if b.symbol == "NDX"]
    assert ndx == [("MNQ", 19980.0, 20020.0)]  # 20000 +/- 0.50 x 40


def test_node_bands_use_the_map_state_at_the_given_instant() -> None:
    later = snap("SPX", T0, 5800.0, (5900.0,), (1e9,))
    bars = [*BARS, mbar("MES", T0 - 60 * S, 5815.0, 5822.0, 5820.0)]
    view = hview([SPX, later], bars)
    early = node_bands(view, T0 - 30 * S, NP, P)
    assert [(b.strike, b.lo, b.hi) for b in early] == [
        (5780.0, 5785.0, 5795.0),
        (5825.0, 5830.0, 5840.0),
    ]
    now = node_bands(view, T0, NP, P)
    assert [(b.strike, b.lo, b.hi) for b in now] == [(5900.0, 5915.0, 5925.0)]  # offset +20
    assert node_bands(view, T0 - 31 * S, NP, P) == ()  # no Snapshot yet
    with pytest.raises(ValueError, match="requested from a MarketView"):
        node_bands(view, T0 + 1, NP, P)


def test_node_bands_skip_missing_prices() -> None:
    view = hview([SPX, NDX], [])  # no futures bars; NDX also lacks the QQQ Snapshot
    assert node_bands(view, T0, NP, P) == ()
    no_qqq = hview([NDX], BARS)
    assert node_bands(no_qqq, T0, NP, P) == ()


def test_node_bands_feed_the_tracker() -> None:
    view = hview([SPX], BARS)
    bar = mbar("MES", T0 - 60 * S, 5794.0, 5799.0)  # inside the 5780 Node's band only
    state = TapState.initial().on_bar(bar, node_bands(view, bar.close_ns, NP, P))
    taps = state.view(T0)
    assert taps.session_count(("SPX", "gamma", 5780.0)) == 1
    assert taps.session_count(("SPX", "gamma", 5825.0)) == 0
