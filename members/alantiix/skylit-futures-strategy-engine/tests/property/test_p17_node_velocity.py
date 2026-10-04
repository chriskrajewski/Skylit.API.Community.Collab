"""Property 17: Node_Velocity.

*For any* sequence of Snapshots for one symbol, metric, view and session, any
Decision_Time and any velocity window, each strike's Node_Velocity equals
100 * (|v1| - |v0|) / |v0| with v0 taken from the latest Snapshot at or before
t - window, and is unavailable exactly when that Snapshot does not exist, lacks
the strike, or has value 0 there.

One session's inputs go through :class:`~fse.pit.market_view.HistoricalInputs`.
``v1`` comes from the Map_State Snapshot of the target (symbol, metric) at t.
Inputs mix plain Snapshots (available at their ``asOf``) and
:class:`~fse.pit.asof.Received` Snapshots (available at ``max(asOf, receipt)``,
design D8), so an older ``asOf`` often arrives after a newer one. Noise
Snapshots of the other metric, another configured symbol, another Heatmap_View
and an unconfigured symbol must never serve as ``v0``. Strikes come from a
small pool, so a strike is often absent from ``v0``; values include 0.0 and
-0.0. Each Snapshot may carry a live ``velocityPct`` in ``extra_json``, which
must never change the result. ``asOf`` values sit on a 1 s grid half the time,
which forces ties, and Decision_Times are biased to ``asOf + window`` and to
every availability instant by -1, 0 and +1 ns.

The oracle recomputes availability from D8 and scans every input. With equal
``asOf`` values any of the tied Snapshots is a valid ``v1`` or ``v0``.

**Validates: Requirements 5.9, 5.10**
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, time

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.data import VELOCITY_WINDOW_MAX_S
from fse.data.cache import HeatmapView
from fse.engine.nodes import (
    NO_PRIOR_SNAPSHOT,
    PRIOR_VALUE_ZERO,
    STRIKE_ABSENT,
    velocity,
)
from fse.engine.types import Metric, Snapshot, Unavailable
from fse.pit.asof import Received
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

type Item = Snapshot | Received[Snapshot]
type Source = tuple[str, Metric, str]
type Expected = Mapping[float, float | Unavailable]

S = NS_PER_SECOND
T0: Instant = ny_instant(date(2026, 3, 5), time(9, 30))
VIEW_ID = HeatmapView().view_id()
OTHER_VIEW_ID = HeatmapView(max_strikes=40).view_id()
SYMBOLS = ("SPX", "QQQ")  # configured; IWM is not
METRICS: tuple[Metric, ...] = ("gamma", "vanna")
TARGET: Source = ("SPX", "gamma", VIEW_ID)
NOISE: tuple[Source, ...] = (
    ("SPX", "vanna", VIEW_ID),  # the other metric
    ("QQQ", "gamma", VIEW_ID),  # another configured symbol
    ("SPX", "gamma", OTHER_VIEW_ID),  # another Heatmap_View
    ("IWM", "gamma", VIEW_ID),  # an unconfigured symbol
)
STRIKE_POOL = (5780.0, 5790.0, 5800.0, 5810.0, 5820.0, 5830.0)
NUDGES_NS = (-1, 0, 1)

# ---------------------------------------------------------------- inputs


def snap(
    as_of_ns: Instant,
    uid: int,
    strikes: tuple[float, ...],
    values: tuple[float, ...],
    source: Source = TARGET,
    extra_json: str = "{}",
) -> Snapshot:
    """A Snapshot made unique by ``uid`` (in ``as_of_raw``) so equality pins one input."""
    symbol, metric, view_id = source
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=view_id,
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{uid}",
        spot=5805.0,
        previous_close=None,
        strikes=strikes,
        values=values,
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json=extra_json,
    )


def unwrap(item: Item) -> tuple[Snapshot, Instant]:
    """The Snapshot and its availability per D8, computed without the code under test."""
    if isinstance(item, Received):
        return item.record, max(item.record.as_of_ns, item.received_ns)
    return item, item.as_of_ns


def expected(current: Snapshot, prior: Snapshot | None) -> Expected:
    """Req 5.9-5.10 for each strike of ``current`` (strikes are distinct)."""
    if prior is None:
        return dict.fromkeys(current.strikes, NO_PRIOR_SNAPSHOT)
    v0_by_strike = dict(zip(prior.strikes, prior.values, strict=True))
    out: dict[float, float | Unavailable] = {}
    for strike, v1 in zip(current.strikes, current.values, strict=True):
        if strike not in v0_by_strike:
            out[strike] = STRIKE_ABSENT
        elif v0_by_strike[strike] == 0.0:  # -0.0 too
            out[strike] = PRIOR_VALUE_ZERO
        else:
            v0 = abs(v0_by_strike[strike])
            out[strike] = 100.0 * (abs(v1) - v0) / v0
    return out


def latest(snapshots: list[Snapshot]) -> list[Snapshot]:
    """The Snapshots tied on the largest ``asOf`` (empty when there are none)."""
    if not snapshots:
        return []
    top = max(s.as_of_ns for s in snapshots)
    return [s for s in snapshots if s.as_of_ns == top]


@dataclass(frozen=True, slots=True)
class Case:
    """One session's Snapshot inputs and the (Decision_Time, window_s) pairs to evaluate."""

    items: tuple[Item, ...]
    queries: tuple[tuple[Instant, int], ...]

    def visible(self, t: Instant) -> list[Snapshot]:
        """Target Snapshots (symbol, metric and view) that are available at ``t``."""
        picked: list[Snapshot] = []
        for item in self.items:
            s, available = unwrap(item)
            if (s.symbol, s.metric, s.view_id) == TARGET and available <= t:
                picked.append(s)
        return picked


# ---------------------------------------------------------------- generators


def _at(seconds: int) -> Instant:
    return T0 + seconds * S


def _seconds(k: int) -> int:
    return k * S


def _velocity_pct_json(strikes: tuple[float, ...], pct: float) -> str:
    rows = [{"strike": k, "velocityPct": pct} for k in strikes]
    return json.dumps({"strikes": rows}, sort_keys=True, separators=(",", ":"))


AS_OF = st.one_of(st.integers(-5, 150).map(_at), st.integers(T0 - 5 * S, T0 + 150 * S))
# None: a plain historical record. Otherwise the receipt delay, which may be negative.
DELAYS = st.one_of(st.none(), st.none(), st.integers(-5, 90).map(_seconds), st.integers(-S, 90 * S))
SOURCES = st.sampled_from((TARGET, TARGET, TARGET, TARGET, *NOISE))
STRIKES = st.lists(st.sampled_from(STRIKE_POOL), max_size=len(STRIKE_POOL), unique=True).map(
    lambda ks: tuple(sorted(ks))
)
VALUES = st.one_of(
    st.sampled_from((0.0, -0.0)),
    st.floats(-5e9, 5e9, allow_nan=False),
    st.floats(allow_nan=False, allow_infinity=False),
)
LIVE_PCT = st.one_of(st.none(), st.floats(-100.0, 1000.0, allow_nan=False))
WINDOWS = st.one_of(
    st.sampled_from((1, 5, 15, 30, 60)),
    st.integers(1, 180),
    st.just(VELOCITY_WINDOW_MAX_S),
)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    specs = draw(st.lists(st.tuples(SOURCES, STRIKES, AS_OF, DELAYS, LIVE_PCT), max_size=20))
    items: list[Item] = []
    for uid, (source, strikes, as_of, delay, pct) in enumerate(specs):
        values = tuple(draw(st.lists(VALUES, min_size=len(strikes), max_size=len(strikes))))
        extra = "{}" if pct is None else _velocity_pct_json(strikes, pct)
        s = snap(as_of, uid, strikes, values, source, extra)
        items.append(s if delay is None else Received(s, as_of + delay))

    windows = draw(st.lists(WINDOWS, min_size=1, max_size=3, unique=True))
    any_time = st.integers(T0 - 10 * S, T0 + 300 * S)
    queries: list[tuple[Instant, int]] = []
    for window_s in windows:
        anchors: set[Instant] = set()
        for item in items:
            s, available = unwrap(item)
            for n in NUDGES_NS:
                anchors.update((s.as_of_ns + window_s * S + n, available + n))
        instants = st.one_of(st.sampled_from(sorted(anchors)), any_time) if anchors else any_time
        times = draw(st.lists(instants, min_size=1, max_size=4))
        queries.extend((t, window_s) for t in times)
    return Case(tuple(items), tuple(queries))


# ---------------------------------------------------------------- explicit examples

_BOUNDARY = Case(  # asOf == t - window is the window start; one ns earlier is not
    items=(
        snap(_at(0), 0, (5800.0, 5810.0), (40.0, -20.0)),
        snap(_at(60), 1, (5800.0, 5810.0), (50.0, -30.0)),
    ),
    queries=((_at(60), 60), (_at(60) - 1, 60), (_at(60), 61), (_at(61), 1)),
)
_LATE_RECEIPTS = Case(  # v0 must be visible at t and have the largest asOf, not the last arrival
    items=(
        snap(_at(0), 0, (5800.0,), (10.0,)),
        Received(snap(_at(20), 1, (5800.0,), (20.0,)), _at(100)),
        snap(_at(90), 2, (5800.0,), (40.0,)),
    ),
    queries=((_at(90), 60), (_at(100) - 1, 60), (_at(100), 60)),
)
_NOISE_AND_GAPS = Case(  # zero and absent strikes; newer noise inside the window is ignored
    items=(
        snap(_at(0), 0, (5790.0, 5800.0), (-0.0, -25.0)),
        snap(_at(60), 1, (5790.0, 5800.0, 5820.0), (7.0, 50.0, 3.0)),
        *(
            snap(at, uid, (5790.0, 5800.0, 5820.0), (1.0, 1.0, 1.0), source)
            for uid, source in enumerate(NOISE, start=2)
            for at in (_at(5), _at(-60))
        ),
    ),
    queries=((_at(65), 60), (_at(65), 120)),
)
_EMPTY = Case(  # no target Snapshot at all, then a target Snapshot with no strikes
    items=(snap(_at(0), 0, (), (), extra_json=_velocity_pct_json((5800.0,), 12.5)),),
    queries=((_at(-1), 60), (_at(0), 1), (_at(1), 1)),
)


def _with_examples[F: Callable[..., None]](test: F) -> F:
    for case in (_BOUNDARY, _LATE_RECEIPTS, _NOISE_AND_GAPS, _EMPTY):
        test = example(case=case)(test)
    return test


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 17: Node_Velocity
@_with_examples
@given(case=cases())
def test_node_velocity(case: Case) -> None:
    inputs = HistoricalInputs(
        symbols=SYMBOLS, view_id=VIEW_ID, metrics=METRICS, snapshots=case.items
    )
    symbol, metric, _ = TARGET
    for t, window_s in case.queries:
        view = inputs.view(t)
        visible = case.visible(t)
        current = view.map_state().get(symbol, metric)
        if not isinstance(current, Snapshot):
            assert not visible, f"t={t}: Map_State is {current!r} with target Snapshots visible"
            continue
        assert current in latest(visible), f"t={t}: Map_State Snapshot {current.as_of_raw}"

        result = velocity(view, current, window_s)

        # One entry per strike of the Map_State Snapshot, in its order.
        assert list(result) == list(current.strikes)
        # v0: the latest target Snapshot visible at t with asOf <= t - window (Req 5.9);
        # without one, every strike is unavailable (Req 5.10).
        start = t - window_s * S
        priors = latest([s for s in visible if s.as_of_ns <= start])
        options = [expected(current, p) for p in priors] or [expected(current, None)]
        assert dict(result) in options, (
            f"t={t} window={window_s}s current={current.as_of_raw} "
            f"priors={[p.as_of_raw for p in priors]}: got {dict(result)!r}, "
            f"want one of {options!r}"
        )
