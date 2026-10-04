"""Property 13: Map_State selection.

*For any* set of Snapshots and any Decision_Time t, Map_State holds for each
configured symbol and metric the Snapshot of the configured view with the
largest returned ``asOf`` that is at or before t, or an unavailable marker when
none exists; it never holds a Snapshot with ``asOf`` after t.

Inputs mix plain Snapshots (historical: available at their ``asOf``) and
:class:`~fse.pit.asof.Received` Snapshots (replay: available at
``max(asOf, receipt)``, design D8). Receipt delays are random, so a Snapshot
with an older ``asOf`` often arrives after a newer one; Map_State must still
pick the largest ``asOf``, never the latest arrival. Noise Snapshots of another
Heatmap_View or of a symbol that is not configured must never be chosen.
``asOf`` values sit on a 1 s grid half the time, which forces ties, and
Decision_Times are biased to every ``asOf`` and availability instant by -1, 0
and +1 ns.

The oracle scans every input: it keeps the Snapshots of the configured view and
(symbol, metric) whose availability, recomputed here from D8, is at or before
t, and expects the largest ``asOf`` among them. With equal ``asOf`` values any
of the tied Snapshots satisfies the property.

**Validates: Requirements 5.1, 5.2**
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time

from hypothesis import example, given
from hypothesis import strategies as st

from fse.data.cache import HeatmapView
from fse.engine.types import Metric, Snapshot
from fse.pit.asof import Received
from fse.pit.market_view import HistoricalInputs
from fse.pit.protocols import MarketView, SymMetric, missing_snapshot
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

type Item = Snapshot | Received[Snapshot]

S = NS_PER_SECOND
T0: Instant = ny_instant(date(2026, 3, 5), time(9, 30))
VIEW_ID = HeatmapView().view_id()
OTHER_VIEW_ID = HeatmapView(max_strikes=40).view_id()
CONFIG_SYMBOLS = ("SPX", "QQQ", "SPY", "NDX")
NOISE_SYMBOLS = ("IWM", "RUT")  # never configured
ALL_METRICS: tuple[Metric, ...] = ("gamma", "vanna")
METRIC_ORDERS: tuple[tuple[Metric, ...], ...] = (
    ("gamma", "vanna"),
    ("vanna", "gamma"),
    ("gamma",),
    ("vanna",),
)
NUDGES_NS = (-1, 0, 1)

# ---------------------------------------------------------------- inputs


def snap(
    symbol: str, metric: Metric, as_of_ns: Instant, uid: int, view_id: str = VIEW_ID
) -> Snapshot:
    """A Snapshot made unique by ``uid`` (in ``as_of_raw``) so equality pins one input."""
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=view_id,
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{uid}",
        spot=5800.25,
        previous_close=5790.5,
        strikes=(5780.0, 5800.0, 5825.0),
        values=(-1.5e9, 3.0e9, float(uid)),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def unwrap(item: Item) -> tuple[Snapshot, Instant]:
    """The Snapshot and its availability per D8, computed without the code under test."""
    if isinstance(item, Received):
        return item.record, max(item.record.as_of_ns, item.received_ns)
    return item, item.as_of_ns


@dataclass(frozen=True, slots=True)
class Case:
    """Configured keys, the session's Snapshot inputs and the Decision_Times to view."""

    symbols: tuple[str, ...]
    metrics: tuple[Metric, ...]
    items: tuple[Item, ...]
    times: tuple[Instant, ...]

    def keys(self) -> tuple[SymMetric, ...]:
        return tuple((s, m) for s in self.symbols for m in self.metrics)

    def candidates(self, key: SymMetric, t: Instant) -> list[Snapshot]:
        """Snapshots of the configured view and ``key`` that are available at ``t``."""
        picked: list[Snapshot] = []
        for item in self.items:
            s, available = unwrap(item)
            if (s.symbol, s.metric, s.view_id) == (*key, VIEW_ID) and available <= t:
                picked.append(s)
        return picked


# ---------------------------------------------------------------- generators


def _at(seconds: int) -> Instant:
    return T0 + seconds * S


def _seconds(k: int) -> int:
    return k * S


AS_OF = st.one_of(st.integers(-5, 60).map(_at), st.integers(T0 - 5 * S, T0 + 60 * S))
# None: a plain historical record. Otherwise the receipt delay, which may be negative.
DELAYS = st.one_of(st.none(), st.integers(-5, 90).map(_seconds), st.integers(-S, 90 * S))
VIEWS = st.sampled_from((VIEW_ID, VIEW_ID, VIEW_ID, OTHER_VIEW_ID))


@st.composite
def cases(draw: st.DrawFn) -> Case:
    symbols = tuple(
        draw(st.lists(st.sampled_from(CONFIG_SYMBOLS), min_size=1, max_size=3, unique=True))
    )
    metrics = draw(st.sampled_from(METRIC_ORDERS))
    record_symbols = st.one_of(
        st.sampled_from(symbols), st.sampled_from(CONFIG_SYMBOLS + NOISE_SYMBOLS)
    )
    specs = draw(
        st.lists(
            st.tuples(record_symbols, st.sampled_from(ALL_METRICS), VIEWS, AS_OF, DELAYS),
            max_size=30,
        )
    )
    items: list[Item] = []
    for uid, (symbol, metric, view_id, as_of, delay) in enumerate(specs):
        s = snap(symbol, metric, as_of, uid, view_id)
        items.append(s if delay is None else Received(s, as_of + delay))
    anchors: set[Instant] = set()
    for item in items:
        s, available = unwrap(item)
        anchors.update(at + n for at in (s.as_of_ns, available) for n in NUDGES_NS)
    any_time = st.integers(T0 - 10 * S, T0 + 160 * S)
    instants = st.one_of(st.sampled_from(sorted(anchors)), any_time) if anchors else any_time
    times = draw(st.lists(instants, min_size=1, max_size=8))
    return Case(symbols, metrics, tuple(items), tuple(times))


# ---------------------------------------------------------------- explicit examples

_OLDER_ARRIVES_LAST = Case(  # the latest arrival (asOf +5 s, received +20 s) must lose
    symbols=("SPX",),
    metrics=("gamma",),
    items=(
        snap("SPX", "gamma", _at(10), 0),
        Received(snap("SPX", "gamma", _at(5), 1), _at(20)),
    ),
    times=(_at(7), _at(10) - 1, _at(10), _at(15), _at(20), _at(30)),
)
_NEWER_NOT_YET_RECEIVED = Case(  # asOf +10 s is at or before t = +15 s but unseen until +20 s
    symbols=("SPX", "QQQ"),
    metrics=("gamma", "vanna"),
    items=(
        snap("SPX", "vanna", _at(5), 0),
        Received(snap("SPX", "vanna", _at(10), 1), _at(20)),
        snap("QQQ", "gamma", _at(12), 2),
    ),
    times=(_at(15), _at(20) - 1, _at(20)),
)
_BOUNDARY_AND_NOISE = Case(  # asOf == t counts; other views and symbols never do
    symbols=("SPX",),
    metrics=("gamma", "vanna"),
    items=(
        snap("SPX", "gamma", T0, 0),
        snap("SPX", "gamma", T0 - 10 * S, 1, OTHER_VIEW_ID),
        snap("IWM", "gamma", T0 - 10 * S, 2),
        snap("SPX", "vanna", T0 + 30 * S, 3, OTHER_VIEW_ID),
    ),
    times=(T0 - 1, T0, T0 + 1, T0 + 60 * S),
)
_NO_SNAPSHOTS = Case(symbols=("SPX", "NDX"), metrics=("gamma", "vanna"), items=(), times=(T0,))


def _with_examples[F: Callable[..., None]](test: F) -> F:
    for case in (_OLDER_ARRIVES_LAST, _NEWER_NOT_YET_RECEIVED, _BOUNDARY_AND_NOISE, _NO_SNAPSHOTS):
        test = example(case=case)(test)
    return test


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 13: Map_State selection
@_with_examples
@given(case=cases())
def test_map_state_selection(case: Case) -> None:
    inputs = HistoricalInputs(
        symbols=case.symbols, view_id=VIEW_ID, metrics=case.metrics, snapshots=case.items
    )
    keys = case.keys()
    assert inputs.keys == keys

    for t in case.times:
        view = inputs.view(t)
        assert isinstance(view, MarketView)
        assert view.t == t
        ms = view.map_state()
        assert ms.t == t
        assert tuple(ms) == keys  # one entry per configured (symbol, metric), in order

        missing: list[SymMetric] = []
        for key in keys:
            entry = ms.get(*key)
            candidates = case.candidates(key, t)
            if not candidates:
                # Req 5.2: unavailable, never a Snapshot with a later asOf.
                assert entry == missing_snapshot(*key), f"t={t} {key}: {entry!r}"
                missing.append(key)
                continue
            # Req 5.1: the largest returned asOf at or before t, not the latest arrival.
            top = max(c.as_of_ns for c in candidates)
            assert isinstance(entry, Snapshot), f"t={t} {key}: {entry!r}"
            assert entry.as_of_ns == top <= t, f"t={t} {key}: asOf {entry.as_of_ns}, want {top}"
            assert entry in [c for c in candidates if c.as_of_ns == top]

        assert ms.missing() == tuple(missing)
        assert all(s.as_of_ns <= t and s.view_id == VIEW_ID for s in ms.snapshots())
