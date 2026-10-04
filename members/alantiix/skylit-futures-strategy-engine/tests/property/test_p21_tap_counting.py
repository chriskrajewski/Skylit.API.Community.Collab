"""Property 21: Tap counting.

*For any* sequence of RTH 1-minute bars and Deflection_Bands, the session and
weekly Tap counts of each Node equal the number of maximal runs of consecutive
overlapping bars (the first RTH bar starting a run when it overlaps), reset at
session start and at the first session of each week.

Sessions are weekdays from 2026-03-02 to 2026-03-20: three ISO weeks across the
DST change of 2026-03-08, with random gaps, so a missing Monday makes Tuesday
the week's first session. Each session holds MES and MNQ 1-minute bars, each
instrument's bars in time order, merged in a random order. Bars open from
09:00 to 16:30, so some open outside RTH and must change no count, and minutes
may be missing (a gap does not split a run). Each bar carries the
Deflection_Bands of the Map_State at its close: a random subset of five Nodes
in random order (two share a strike and differ only by metric), with edges on
the tick grid or at arbitrary floats. Bar and band prices sit in a narrow
range, so overlaps, edge touches and long runs are common.

The reference model recounts from scratch: per session and Node, the maximal
runs of overlapping bars among the session's RTH bars of the Node's
instrument, in feed order. RTH and the session come from the generator, not
from the code under test. Views are checked at 09:30 of each session before
its bars and after every bar.

**Validates: Requirements 6.14**
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from fractions import Fraction

from hypothesis import example, given
from hypothesis import strategies as st

from fse.engine.taps import NodeBand, NodeId, TapState, TapView
from fse.engine.types import Bar, Ticks
from fse.timekit import NS_PER_MINUTE, Instant, ny_instant

type Session = tuple[date, tuple[Fed, ...]]

FIRST_DAY = date(2026, 3, 2)  # a Monday; DST starts on Sunday 2026-03-08
WEEKDAYS: tuple[date, ...] = tuple(
    d for d in (FIRST_DAY + timedelta(days=i) for i in range(19)) if d.weekday() < 5
)
NODES: Mapping[NodeId, str] = {  # Node -> the instrument its symbol maps to
    ("SPX", "gamma", 5800.0): "MES",
    ("SPX", "vanna", 5800.0): "MES",  # same strike, other metric: another Node
    ("SPY", "gamma", 580.0): "MES",
    ("NDX", "gamma", 20000.0): "MNQ",
    ("QQQ", "gamma", 500.0): "MNQ",
}
CONTRACTS: Mapping[str, str] = {"MES": "MESH6", "MNQ": "MNQH6"}
BASE_TICKS: Mapping[str, Ticks] = {"MES": 5800 * 4, "MNQ": 20000 * 4}
SPAN_TICKS = 24
RTH_MINUTES = range(390)  # minute offsets from 09:30 of bars that open within RTH
# Minute offsets from 09:30: 09:00 to 16:30, biased to the RTH edges.
MINUTES = st.one_of(st.integers(-30, 420), st.sampled_from((-1, 0, 1, 389, 390)))


# ---------------------------------------------------------------- inputs


def mbar(instrument: str, open_ns: Instant, low_t: Ticks, high_t: Ticks) -> Bar:
    """A 1-minute futures bar opening at ``open_ns`` with tick low and high."""
    low, high = low_t / 4, high_t / 4
    return Bar(
        instrument=instrument,
        contract=CONTRACTS[instrument],
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=low,
        h=high,
        l=low,
        c=high,
        v=1.0,
        o_t=low_t,
        h_t=high_t,
        l_t=low_t,
        c_t=high_t,
        source="atlas",
    )


@dataclass(frozen=True, slots=True)
class Fed:
    """One bar with the bands of the Map_State at its close, plus its ground truth."""

    bar: Bar
    bands: tuple[NodeBand, ...]
    session: date
    rth: bool


@dataclass(frozen=True, slots=True)
class Case:
    """Sessions in date order, each with its bars in feed order."""

    sessions: tuple[Session, ...]


# ---------------------------------------------------------------- reference model


def overlaps(bar: Bar, b: NodeBand) -> bool:
    """``bar.low <= band.hi and bar.high >= band.lo``, exactly, in points."""
    assert bar.l_t is not None
    assert bar.h_t is not None
    return Fraction(bar.l_t, 4) <= Fraction(b.hi) and Fraction(bar.h_t, 4) >= Fraction(b.lo)


def recount(fed: Sequence[Fed]) -> Counter[tuple[date, NodeId]]:
    """Taps per (session, Node): maximal runs of overlapping RTH bars of the Node's instrument."""
    runs: Counter[tuple[date, NodeId]] = Counter()
    inside: set[tuple[date, NodeId]] = set()  # the latest RTH bar overlapped
    for f in fed:
        if not f.rth:
            continue
        bands = {b.node: b for b in f.bands}
        for node, instrument in NODES.items():
            if instrument != f.bar.instrument:
                continue
            key = (f.session, node)  # a new session starts with no open run
            b = bands.get(node)
            if b is not None and overlaps(f.bar, b):
                if key not in inside:
                    runs[key] += 1
                    inside.add(key)
            else:
                inside.discard(key)
    return runs


def _week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)


def check(view: TapView, fed: Sequence[Fed], session: date, where: str) -> None:
    """The view's counts equal the recount over every bar fed so far."""
    runs = recount(fed)
    assert view.session == session, f"{where}: view session {view.session}, want {session}"
    for node in NODES:
        want_session = runs[(session, node)]
        want_week = sum(n for (d, k), n in runs.items() if k == node and _week(d) == _week(session))
        got = (view.session_count(node), view.weekly_count(node))
        assert got == (want_session, want_week), (
            f"{where}: {node} (session, weekly) counts {got}, want {(want_session, want_week)}"
        )
    want_total = sum(runs[(session, node)] for node in NODES)
    got_total = len(view.taps)
    assert got_total == want_total, f"{where}: {got_total} session Taps, want {want_total}"


# ---------------------------------------------------------------- generators


@st.composite
def band_edges(draw: st.DrawFn, instrument: str) -> tuple[float, float]:
    base = BASE_TICKS[instrument]
    if draw(st.booleans()):  # on the tick grid, so edges often touch bar extremes
        lo_t = base + draw(st.integers(0, SPAN_TICKS))
        return lo_t / 4, (lo_t + draw(st.integers(0, 12))) / 4
    lo = draw(st.floats(base / 4, (base + SPAN_TICKS) / 4))
    return lo, lo + draw(st.floats(0.0, 3.0))


@st.composite
def bands_at_close(draw: st.DrawFn) -> tuple[NodeBand, ...]:
    out: list[NodeBand] = []
    for (symbol, metric, strike), instrument in NODES.items():
        if draw(st.integers(0, 3)) == 0:  # not a Node, or no price, at this close
            continue
        lo, hi = draw(band_edges(instrument))
        out.append(NodeBand(symbol, metric, strike, instrument, lo, hi))
    return tuple(draw(st.permutations(out)))


@st.composite
def instrument_bars(draw: st.DrawFn, d: date, instrument: str) -> list[Fed]:
    minutes = sorted(draw(st.lists(MINUTES, unique=True, max_size=10)))
    open0 = ny_instant(d, time(9, 30))
    out: list[Fed] = []
    for m in minutes:
        low_t = BASE_TICKS[instrument] + draw(st.integers(0, SPAN_TICKS))
        high_t = low_t + draw(st.integers(0, 8))
        bar = mbar(instrument, open0 + m * NS_PER_MINUTE, low_t, high_t)
        out.append(Fed(bar, draw(bands_at_close()), d, m in RTH_MINUTES))
    return out


@st.composite
def cases(draw: st.DrawFn) -> Case:
    days = sorted(draw(st.lists(st.sampled_from(WEEKDAYS), unique=True, min_size=1, max_size=5)))
    sessions: list[Session] = []
    for d in days:
        es = draw(instrument_bars(d, "MES"))
        nq = draw(instrument_bars(d, "MNQ"))
        merged: list[Fed] = []
        while es or nq:  # keeps each instrument's time order
            take_es = bool(es) and (not nq or draw(st.booleans()))
            merged.append((es if take_es else nq).pop(0))
        sessions.append((d, tuple(merged)))
    return Case(tuple(sessions))


# ---------------------------------------------------------------- explicit example

SPX_GAMMA: NodeId = ("SPX", "gamma", 5800.0)
BAND = NodeBand("SPX", "gamma", 5800.0, "MES", 5805.0, 5815.0)
IN = (5810 * 4, 5811 * 4)  # a tick range inside BAND
OUT = (5790 * 4, 5791 * 4)  # a tick range below BAND


def fixed_session(d: date, spec: Sequence[tuple[int, tuple[Ticks, Ticks], bool]]) -> Session:
    """MES bars at minute offsets from 09:30, each with BAND or with no band."""
    open0 = ny_instant(d, time(9, 30))
    items = tuple(
        Fed(
            mbar("MES", open0 + m * NS_PER_MINUTE, *r),
            (BAND,) if banded else (),
            d,
            m in RTH_MINUTES,
        )
        for m, r, banded in spec
    )
    return (d, items)


THU, FRI, TUE = date(2026, 3, 12), date(2026, 3, 13), date(2026, 3, 17)
_HAND_COUNTED = Case(
    (
        # Pre-open overlap, then runs {09:30} and {09:32, 09:34}: the 09:31 bar has
        # no band and splits them, the missing 09:33 does not. 16:00 is not RTH.
        fixed_session(
            THU,
            [
                (-1, IN, True),
                (0, IN, True),
                (1, IN, False),
                (2, IN, True),
                (4, IN, True),
                (389, OUT, True),
                (390, IN, True),
            ],
        ),
        fixed_session(FRI, [(0, IN, True)]),  # session 1, week 3
        fixed_session(TUE, [(0, IN, True)]),  # Monday missing: session 1, week 1
    )
)


def test_reference_model_on_a_hand_counted_case() -> None:
    fed = [f for _, items in _HAND_COUNTED.sessions for f in items]
    assert recount(fed) == Counter({(THU, SPX_GAMMA): 2, (FRI, SPX_GAMMA): 1, (TUE, SPX_GAMMA): 1})


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 21: Tap counting
@example(case=_HAND_COUNTED)
@given(case=cases())
def test_tap_counting(case: Case) -> None:
    state = TapState.initial()
    fed: list[Fed] = []
    horizon: Instant = 0
    for d, items in case.sessions:
        # Before the session's first bar: no session Taps, the week's earlier ones kept.
        check(state.view(ny_instant(d, time(9, 30))), fed, d, f"{d} 09:30 before its bars")
        for item in items:
            state = state.on_bar(item.bar, item.bands)
            fed.append(item)
            horizon = max(horizon, item.bar.close_ns)
            where = f"{d} after the {item.bar.instrument} bar opening at {item.bar.open_ns}"
            check(state.view(horizon), fed, d, where)
