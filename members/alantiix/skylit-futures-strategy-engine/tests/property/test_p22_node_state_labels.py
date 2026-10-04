"""Property 22: Node state labels match the reference model.

*For any* session history of Snapshots and bars, each Node's lifecycle state
equals the first matching rule of Decaying, Delivered, Tested, Fresh; the
Sloppy_Seconds label is set from the first qualifying Snapshot inside the
window and kept to session end; and Dormant labels are exactly the Nodes beyond
the Dormant distance when the Regime is not Vanna_Dominant, and none when it is.

Sessions are one to three weekdays out of Thu 2026-03-05 to Tue 2026-03-10: two
ISO weeks across the DST change of 2026-03-08. Each session has its own
:class:`~fse.pit.market_view.HistoricalInputs` with SPX gamma and SPX vanna
Snapshots (distinct ``asOf`` per metric, some before 09:30, strikes from a
5-point pool, a strike sometimes absent, values from a pool of round numbers
so the decay and Sloppy_Seconds limits are often hit exactly) and MES 1-minute
bars, some outside RTH. Each bar carries the Deflection_Bands of the Map_State
at its close: every Node of the latest Snapshot at or before the close, at
``strike + 10`` points with one half-width per case, so adjacent bands are
apart, touching or overlapping. A bar sometimes carries no band (no price).
The Tap tracker and the lifecycle state persist across sessions and are fed
every bar that closed by each Decision_Time, as the engine does.

The reference model recomputes everything at each Decision_Time from the full
Snapshot list and the bars fed so far, with no incremental state: the Taps as
maximal runs of overlapping RTH bars per session, the peak as a maximum over
the session's Snapshots since 09:30, deliveries by scanning every bar after
the latest Tap's end, and each Sloppy_Seconds window by scanning every
Snapshot in it. Sessions, RTH and weeks come from the generator.

**Validates: Requirements 6.15, 6.16, 6.17, 6.18**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, time

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.nodes import NodesConfig
from fse.engine.lifecycle import (
    Lifecycle,
    LifecycleParams,
    LifecycleState,
    NodeState,
    dormant_nodes,
)
from fse.engine.nodes import NodeParams
from fse.engine.regime import RegimeResult
from fse.engine.taps import NodeBand, NodeId, TapState
from fse.engine.types import Bar, Metric, MissingInput, Regime, Snapshot, Ticks
from fse.pit.market_view import HistoricalInputs
from fse.pit.protocols import SymMetric
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

type RegimeInput = Regime | RegimeResult | MissingInput
type Expected = dict[NodeId, NodeState]

S = NS_PER_SECOND
VIEW_ID = "view-p22"
SYMBOL = "SPX"
INSTRUMENT = "MES"
METRICS: tuple[Metric, ...] = ("gamma", "vanna")
KEYS: tuple[SymMetric, ...] = tuple((SYMBOL, m) for m in METRICS)
STRIKES = (5790.0, 5795.0, 5800.0, 5805.0, 5810.0, 5815.0)
ALL_NODES: tuple[NodeId, ...] = tuple((SYMBOL, m, k) for m in METRICS for k in STRIKES)
LEVEL_OFFSET = 10.0  # SPX strike -> MES level, in points
HALF_WIDTHS = (1.0, 2.5, 4.0)  # adjacent bands 5 points apart: apart, touching, overlapping
DAYS = (date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 9), date(2026, 3, 10))
RTH_MINUTES = range(390)  # minute offsets from 09:30 of bars that open within RTH
LAST_MINUTE = 450  # Decision_Times stay before 17:00, inside the session of their date
BASE_NODES = NodeParams.from_config(NodesConfig())


def rth_open(d: date) -> Instant:
    return ny_instant(d, time(9, 30))


def iso_week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)


# ---------------------------------------------------------------- inputs


def mbar(open_ns: Instant, low_t: Ticks, high_t: Ticks) -> Bar:
    """A 1-minute MES bar opening at ``open_ns`` with tick low and high."""
    low, high = low_t / 4, high_t / 4
    return Bar(
        instrument=INSTRUMENT,
        contract="MESH6",
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


def snap(
    metric: Metric,
    as_of_ns: Instant,
    strikes: Sequence[float],
    values: Sequence[float],
    spot: float,
) -> Snapshot:
    return Snapshot(
        symbol=SYMBOL,
        metric=metric,
        view_id=VIEW_ID,
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{metric}-{as_of_ns}",
        spot=spot,
        previous_close=None,
        strikes=tuple(strikes),
        values=tuple(values),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


@dataclass(frozen=True, slots=True)
class Fed:
    """One bar with the bands of the Map_State at its close, plus its ground truth."""

    bar: Bar
    bands: tuple[NodeBand, ...]
    session: date
    rth: bool


@dataclass(frozen=True, slots=True)
class Day:
    """One session's Snapshots and its Decision_Times, each with the Regime given there."""

    session: date
    snapshots: tuple[Snapshot, ...]
    times: tuple[Instant, ...]
    regimes: tuple[RegimeInput, ...]


@dataclass(frozen=True, slots=True)
class Case:
    """Sessions in date order and every bar in feed (time) order."""

    params: LifecycleParams
    days: tuple[Day, ...]
    bars: tuple[Fed, ...]


# ---------------------------------------------------------------- reference model


def abs_at(s: Snapshot, strike: float) -> float | None:
    for k, v in zip(s.strikes, s.values, strict=True):
        if k == strike:
            return abs(v)
    return None


def ref_nodes(s: Snapshot, node_fraction: float) -> tuple[float, ...]:
    """Strikes with ``a >= node_fraction * max(a)``, ascending; none when ``max(a)`` is 0."""
    a = [abs(v) for v in s.values]
    if not a or max(a) == 0.0:
        return ()
    top = max(a)
    return tuple(sorted(k for k, x in zip(s.strikes, a, strict=True) if x >= node_fraction * top))


def latest_at_or_before(snaps: Sequence[Snapshot], at: Instant) -> Snapshot | None:
    found = [s for s in snaps if s.as_of_ns <= at]
    return max(found, key=lambda s: s.as_of_ns) if found else None


def of_key(snaps: Sequence[Snapshot], key: SymMetric) -> list[Snapshot]:
    return [s for s in snaps if (s.symbol, s.metric) == key]


def map_state(snaps: Sequence[Snapshot], t: Instant) -> list[Snapshot]:
    """The Map_State Snapshots at ``t``, in configured order."""
    out: list[Snapshot] = []
    for key in KEYS:
        s = latest_at_or_before(of_key(snaps, key), t)
        if s is not None:
            out.append(s)
    return out


def bands_at(
    snaps: Sequence[Snapshot], c: Instant, node_fraction: float, hw: float
) -> list[NodeBand]:
    """The bands of every Node in the Map_State at a bar's close ``c``."""
    out: list[NodeBand] = []
    for s in map_state(snaps, c):
        for k in ref_nodes(s, node_fraction):
            level = k + LEVEL_OFFSET
            out.append(NodeBand(s.symbol, s.metric, k, INSTRUMENT, level - hw, level + hw))
    return out


def band_of(f: Fed, node: NodeId) -> NodeBand | None:
    return next((b for b in f.bands if b.node == node), None)


def touches(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    """Whether ``[lo_a, hi_a]`` meets ``[lo_b, hi_b]``; touching an edge counts."""
    return lo_a <= hi_b and hi_a >= lo_b


def hit(f: Fed, node: NodeId) -> bool:
    """Whether the bar's tick range, in points, overlaps the Node's band at its close."""
    b = band_of(f, node)
    low_t, high_t = f.bar.l_t, f.bar.h_t
    assert low_t is not None
    assert high_t is not None
    return b is not None and touches(low_t / 4, high_t / 4, b.lo, b.hi)


@dataclass(frozen=True, slots=True)
class RefTap:
    node: NodeId
    session: date
    first_open: Instant
    first_close: Instant
    end: Instant
    followed: bool  # a later RTH bar of the session did not overlap


def ref_taps(fed: Sequence[Fed]) -> list[RefTap]:
    """Maximal runs of consecutive overlapping RTH bars, per session and Node."""
    by_session: dict[date, list[Fed]] = {}
    for f in fed:
        if f.rth:
            by_session.setdefault(f.session, []).append(f)
    out: list[RefTap] = []
    for d, bars in by_session.items():
        for node in ALL_NODES:
            hits = [hit(f, node) for f in bars]
            i = 0
            while i < len(bars):
                if not hits[i]:
                    i += 1
                    continue
                j = i
                while j + 1 < len(bars) and hits[j + 1]:
                    j += 1
                first, last = bars[i].bar, bars[j].bar
                out.append(
                    RefTap(node, d, first.open_ns, first.close_ns, last.close_ns, j + 1 < len(bars))
                )
                i = j + 1
    return out


def delivers(f: Fed, node: NodeId) -> bool:
    """``f`` traded in the band of another same-metric Node whose band misses ``node``'s band."""
    own = band_of(f, node)
    if own is None:
        return False
    return any(
        (b.symbol, b.metric) == (node[0], node[1])
        and b.strike != node[2]
        and hit(f, b.node)
        and not touches(own.lo, own.hi, b.lo, b.hi)
        for b in f.bands
    )


def reference(
    params: LifecycleParams, day: Day, fed: Sequence[Fed], t: Instant
) -> tuple[Expected, tuple[NodeId, ...]]:
    """Every Map_State Node's expected state at ``t`` and the non-Vanna Dormant Nodes."""
    d = day.session
    visible = [s for s in day.snapshots if s.as_of_ns <= t]
    taps = ref_taps([f for f in fed if f.bar.close_ns <= t])
    week = [tp for tp in taps if iso_week(tp.session) == iso_week(d)]
    window = params.sloppy_window_min * NS_PER_MINUTE
    expected: Expected = {}
    dormant: list[NodeId] = []
    for s in map_state(visible, t):
        key = (s.symbol, s.metric)
        since_open = [x for x in of_key(visible, key) if x.as_of_ns >= rth_open(d)]
        for k in ref_nodes(s, params.nodes.node_fraction):
            node: NodeId = (s.symbol, s.metric, k)
            a_now = abs_at(s, k)
            assert a_now is not None
            # Decaying: the peak covers the session's Snapshots from 09:30 to t.
            seen = [a for a in (abs_at(x, k) for x in since_open) if a is not None]
            peak = max(seen) if seen else None
            # Delivered: after the latest Tap this week ended, a bar reached another band.
            mine = [tp for tp in week if tp.node == node]
            latest = max(mine, key=lambda tp: tp.first_open) if mine else None
            delivered = (
                latest is not None
                and (latest.followed or latest.session < d)
                and any(
                    f.rth and f.bar.open_ns >= latest.end and delivers(f, node)
                    for f in fed
                    if f.bar.close_ns <= t
                )
            )
            lifecycle: Lifecycle
            if peak is not None and a_now <= (1.0 - params.decay_fraction) * peak:
                lifecycle = "Decaying"
            elif delivered:
                lifecycle = "Delivered"
            elif mine:
                lifecycle = "Tested"
            else:
                lifecycle = "Fresh"
            # Sloppy_Seconds: the first qualifying asOf of any session Tap's window, up to t.
            found: list[Instant] = []
            for tp in mine:
                if tp.session != d:
                    continue
                ref_snap = latest_at_or_before(of_key(visible, key), tp.first_close)
                ref = None if ref_snap is None else abs_at(ref_snap, k)
                if ref is None:
                    continue
                limit = (1.0 - params.sloppy_fraction) * ref
                for x in of_key(visible, key):
                    a = abs_at(x, k)
                    in_window = tp.first_close < x.as_of_ns <= tp.first_close + window
                    if in_window and a is not None and a <= limit:
                        found.append(x.as_of_ns)
            expected[node] = NodeState(lifecycle, min(found) if found else None)
            if abs(k - s.spot) > params.dormant_distance_pct / 100.0 * s.spot:
                dormant.append(node)
    return expected, tuple(dormant)


def regime_label(regime: RegimeInput) -> Regime | None:
    if isinstance(regime, MissingInput):
        return None
    return regime.regime if isinstance(regime, RegimeResult) else regime


# ---------------------------------------------------------------- generators

NUDGES = (0, 0, 0, 30 * S, -1, 1)  # on the minute grid (bar closes) most often


def offsets(minutes: st.SearchStrategy[int]) -> st.SearchStrategy[int]:
    """Nanosecond offsets from 09:30: a minute plus a nudge."""
    return st.tuples(minutes, st.sampled_from(NUDGES)).map(lambda p: p[0] * NS_PER_MINUTE + p[1])


def signed(pair: tuple[float, bool]) -> float:
    return -pair[0] if pair[1] else pair[0]


AS_OF_OFFSETS = offsets(st.integers(-5, 45))
TIME_OFFSETS = offsets(st.integers(-5, 60))
BAR_MINUTES = st.one_of(st.integers(-5, 45), st.sampled_from((-1, 0, 389, 390)))
VALUE_POOL = (0.0, 50.0, 75.0, 90.0, 100.0, 150.0, 200.0, 225.0, 300.0, 400.0)
VALUES = st.tuples(
    st.one_of(st.sampled_from(VALUE_POOL), st.floats(0.0, 500.0)), st.booleans()
).map(signed)
STRIKE_SETS = st.one_of(
    st.just(STRIKES),
    st.lists(st.sampled_from(STRIKES), unique=True).map(lambda ks: tuple(sorted(ks))),
)
SPOTS = st.one_of(st.sampled_from((5800.0, 5802.5, 5807.5)), st.floats(5780.0, 5825.0))
FRACTIONS = st.one_of(st.sampled_from((0.01, 0.1, 0.2, 0.25, 0.5, 1.0)), st.floats(0.01, 1.0))
PARAMS = st.builds(
    LifecycleParams,
    decay_fraction=FRACTIONS,
    sloppy_window_min=st.one_of(st.sampled_from((1, 2, 5, 15, 390)), st.integers(1, 390)),
    sloppy_fraction=FRACTIONS,
    dormant_distance_pct=st.one_of(
        st.sampled_from((0.05, 0.1, 0.2, 3.5, 100.0)), st.floats(0.001, 0.5)
    ),
    nodes=st.sampled_from((0.2, 0.5, 1.0)).map(lambda f: replace(BASE_NODES, node_fraction=f)),
)
REGIMES: tuple[Regime, ...] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
REGIME_INPUTS: tuple[RegimeInput, ...] = (
    *REGIMES,
    *(RegimeResult(r, 1.0, 1.0, 1.0, 1.0, 1.0, vanna_withheld=False) for r in REGIMES),
    MissingInput(("SPX vanna Snapshot",)),
)


@st.composite
def day_snapshots(draw: st.DrawFn, d: date, metric: Metric) -> list[Snapshot]:
    offsets = sorted(draw(st.lists(AS_OF_OFFSETS, unique=True, max_size=8)))
    out: list[Snapshot] = []
    for offset in offsets:
        strikes = draw(STRIKE_SETS)
        values = draw(st.lists(VALUES, min_size=len(strikes), max_size=len(strikes)))
        out.append(snap(metric, rth_open(d) + offset, strikes, values, draw(SPOTS)))
    return out


@st.composite
def day_bars(
    draw: st.DrawFn, d: date, snaps: Sequence[Snapshot], node_fraction: float, hw: float
) -> list[Fed]:
    out: list[Fed] = []
    for m in sorted(draw(st.lists(BAR_MINUTES, unique=True, max_size=12))):
        low_t = draw(st.integers(5797 * 4, 5828 * 4))
        bar = mbar(rth_open(d) + m * NS_PER_MINUTE, low_t, low_t + draw(st.integers(0, 8)))
        priced = draw(st.integers(0, 7)) != 0
        bands = bands_at(snaps, bar.close_ns, node_fraction, hw) if priced else []
        out.append(Fed(bar, tuple(draw(st.permutations(bands))), d, m in RTH_MINUTES))
    return out


@st.composite
def cases(draw: st.DrawFn) -> Case:
    params = draw(PARAMS)
    hw = draw(st.sampled_from(HALF_WIDTHS))
    window = params.sloppy_window_min * NS_PER_MINUTE
    days: list[Day] = []
    bars: list[Fed] = []
    for d in sorted(draw(st.lists(st.sampled_from(DAYS), unique=True, min_size=1, max_size=3))):
        open0 = rth_open(d)
        snaps = [s for _, m in KEYS for s in draw(day_snapshots(d, m))]
        fed = draw(day_bars(d, snaps, params.nodes.node_fraction, hw))
        # Decision_Times: biased to every asOf, bar close and Sloppy_Seconds window end.
        lo, hi = open0 - 30 * NS_PER_MINUTE, open0 + LAST_MINUTE * NS_PER_MINUTE
        anchors = {s.as_of_ns for s in snaps} | {f.bar.close_ns for f in fed}
        anchors |= {f.bar.close_ns + window for f in fed}
        nudged = sorted({a + n for a in anchors for n in (-1, 0, 1) if lo <= a + n < hi})
        grid = [open0 + x for x in draw(st.lists(TIME_OFFSETS, max_size=3))]
        picked = draw(st.lists(st.sampled_from(nudged), max_size=4)) if nudged else []
        times = sorted([*grid, *picked]) or [open0 + draw(TIME_OFFSETS)]
        regimes = draw(
            st.lists(st.sampled_from(REGIME_INPUTS), min_size=len(times), max_size=len(times))
        )
        days.append(Day(d, tuple(snaps), tuple(times), tuple(regimes)))
        bars.extend(fed)
    return Case(params, tuple(days), tuple(bars))


# ---------------------------------------------------------------- explicit example

THU = DAYS[0]
X: NodeId = ("SPX", "gamma", 5800.0)
Y: NodeId = ("SPX", "gamma", 5815.0)
Z: NodeId = ("SPX", "gamma", 5790.0)


def at(h: int, m: int) -> Instant:
    return ny_instant(THU, time(h, m))


def _hand_case() -> Case:
    """A Tap on X at 10:00, price reaching Y at 10:02, and X 25% below its reference at 10:16."""
    params = LifecycleParams(
        decay_fraction=0.5,
        sloppy_window_min=15,
        sloppy_fraction=0.25,
        dormant_distance_pct=0.1,  # 5.8 points at spot 5800
        nodes=BASE_NODES,
    )
    snaps = tuple(
        snap("gamma", as_of, (5790.0, 5800.0, 5815.0), (1000.0, x, -1000.0), 5800.0)
        for as_of, x in ((at(9, 30), 400.0), (at(10, 1), 300.0), (at(10, 16), 225.0))
    )
    ranges = ((5810 * 4, 5811 * 4), (5816 * 4, 5817 * 4), (5825 * 4, 5826 * 4))  # X, gap, Y
    fed = tuple(
        Fed(bar, tuple(bands_at(snaps, bar.close_ns, 0.2, 2.5)), THU, True)
        for bar in (mbar(at(10, i), *r) for i, r in enumerate(ranges))
    )
    times = (at(10, 16), at(10, 30))
    return Case(params, (Day(THU, snaps, times, ("Positive_Gamma", "Vanna_Dominant")),), fed)


HAND = _hand_case()


def test_reference_model_on_a_hand_built_case() -> None:
    (day,) = HAND.days
    expected, dormant = reference(HAND.params, day, HAND.bars, at(10, 30))
    assert expected == {
        Z: NodeState("Fresh"),
        X: NodeState("Delivered", at(10, 16)),
        Y: NodeState("Tested"),  # its Tap is still open
    }
    assert dormant == (Z, Y)


# ---------------------------------------------------------------- property


def _check(
    got: Mapping[NodeId, NodeState],
    dormant: tuple[NodeId, ...],
    case: Case,
    day: Day,
    t: Instant,
    regime: RegimeInput,
    fed: Sequence[Fed],
) -> None:
    expected, beyond = reference(case.params, day, fed, t)
    where = f"{day.session} t={t}"
    assert list(got) == list(expected), f"{where}: Nodes {list(got)}, want {list(expected)}"
    for node, want in expected.items():
        assert got[node] == want, f"{where}: {node} is {got[node]}, want {want}"
    want_dormant = () if regime_label(regime) == "Vanna_Dominant" else beyond
    assert dormant == want_dormant, f"{where} {regime!r}: Dormant {dormant}, want {want_dormant}"


# Feature: skylit-futures-strategy-engine, Property 22: Node state labels match the reference model
@example(case=HAND)
@given(case=cases())
def test_node_state_labels(case: Case) -> None:
    life, taps = LifecycleState.initial(), TapState.initial()
    pending = list(case.bars)
    fed: list[Fed] = []
    for day in case.days:
        inputs = HistoricalInputs(
            symbols=(SYMBOL,), metrics=METRICS, view_id=VIEW_ID, snapshots=day.snapshots
        )
        for t, regime in zip(day.times, day.regimes, strict=True):
            while pending and pending[0].bar.close_ns <= t:
                f = pending.pop(0)
                taps = taps.on_bar(f.bar, f.bands)
                life = life.on_bar(f.bar, f.bands)
                fed.append(f)
            view = inputs.view(t)
            life, states = life.evaluate(view, taps.view(t), case.params)
            dormant = dormant_nodes(view.map_state(), regime, case.params)
            _check(states, dormant, case, day, t, regime, fed)
