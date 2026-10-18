"""Node lifecycle, Sloppy_Seconds and Dormant labels (design §6, Req 6.15-6.18).

A Node is identified by (symbol, metric, strike), as in the Tap tracker
(:mod:`fse.engine.taps`). ``a`` is a strike's absolute value; a strike listed
twice in a Snapshot reads its first value.

**Lifecycle** (Req 6.15). At each Decision_Time ``t``, every Node of the
Map_State Snapshots (:func:`fse.engine.nodes.classify`) gets the first state
that holds (:func:`lifecycle_of`):

1. Decaying: ``a_now <= (1 - decay_fraction) * peak``, where ``a_now`` is from
   the Map_State Snapshot and ``peak`` is the strike's highest ``a`` over the
   session's Snapshots with ``asOf`` from 09:30 New York time to ``t``. A
   strike with no such Snapshot has no peak and is not Decaying.
2. Delivered: the Node's latest Tap this week has ended, and a 1-minute RTH
   bar opening at or after that Tap's end overlapped the Deflection_Band of
   another Node of the same symbol and metric whose band does not overlap this
   Node's band. Both bands are the ones in the Map_State at that bar's close,
   the same bands the Tap tracker reads, so this Node must have a band then.
3. Tested: the Node's weekly Tap count is at least 1.
4. Fresh.

**Sloppy_Seconds** (Req 6.16). For each Tap of the session, ``ref`` is the
strike's ``a`` in the latest Snapshot with ``asOf`` at or before the close of
the Tap's first bar (no such Snapshot or strike: the Tap sets no label). The
first Snapshot with ``asOf`` in ``(close, close + window]`` and
``a <= (1 - fraction) * ref`` labels the Node from that ``asOf`` until the
session ends, in addition to its lifecycle state.

**Dormant** (Req 6.17-6.18). :func:`dormant_nodes` needs the Regime, so it
runs after the Regime_Classifier. Unless the Regime is Vanna_Dominant, the
Dormant Nodes are those with ``|strike - spot| > dormant_distance_pct / 100 *
spot``. A ``MissingInput`` Regime is not Vanna_Dominant.

**State.** :class:`LifecycleState` is a frozen value. :meth:`~LifecycleState.on_bar`
consumes the same (bar, bands) stream as :class:`~fse.engine.taps.TapState`
and keeps, per Node, the open of the latest bar that delivered it.
:meth:`~LifecycleState.evaluate` takes the MarketView and the Tap counts at
``t`` and folds only the Snapshots that are new since the last Decision_Time:
the session peaks resume from the latest ``asOf`` already folded, and each
Sloppy_Seconds window resumes where it stopped. Peaks, windows and labels
reset when a new session starts; deliveries reset at the first RTH bar of
each Monday-Friday (ISO) week. Every comparison is float64 arithmetic in the
order written above.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.nodes import FRACTION_MIN, PCT_MAX, SLOPPY_WINDOW_MAX_MIN, NodesConfig
from fse.engine.levels import ticks_to_points
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import RegimeResult
from fse.engine.taps import BASE_INTERVAL_S, NodeBand, NodeId, TapView, is_rth_bar, session_of
from fse.engine.types import Bar, Metric, MissingInput, Regime, Snapshot
from fse.pit.protocols import MapState, MarketView, SymMetric
from fse.timekit import NS_PER_MINUTE, RTH_OPEN, Instant, ny_datetime, ny_instant

__all__ = [
    "LIFECYCLES",
    "Lifecycle",
    "LifecycleParams",
    "LifecycleState",
    "NodeState",
    "NodeStates",
    "SessionPeaks",
    "SloppyWatch",
    "dormant_nodes",
    "lifecycle_of",
]

type Lifecycle = Literal["Fresh", "Tested", "Delivered", "Decaying"]
LIFECYCLES: Final[frozenset[str]] = frozenset({"Fresh", "Tested", "Delivered", "Decaying"})


def _require_range(name: str, value: object, lo: float, hi: float, *, lo_open: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"LifecycleParams.{name} must be a finite number, got {value!r}")
    above_lo = value > lo if lo_open else value >= lo
    if not (above_lo and value <= hi):
        bound = f"above {lo}" if lo_open else f"at least {lo}"
        raise ValueError(f"LifecycleParams.{name} must be {bound} and at most {hi}, got {value!r}")


@dataclass(frozen=True, slots=True)
class LifecycleParams:
    """The ``nodes`` keys read by the lifecycle, Sloppy_Seconds and Dormant rules.

    Ranges match :class:`~fse.config.schema.nodes.NodesConfig`. ``nodes``
    decides which strikes are Nodes.
    """

    decay_fraction: float
    sloppy_window_min: int
    sloppy_fraction: float
    dormant_distance_pct: float
    nodes: NodeParams

    def __post_init__(self) -> None:
        _require_range("decay_fraction", self.decay_fraction, FRACTION_MIN, 1.0, lo_open=False)
        _require_range("sloppy_fraction", self.sloppy_fraction, FRACTION_MIN, 1.0, lo_open=False)
        _require_range(
            "dormant_distance_pct", self.dormant_distance_pct, 0.0, PCT_MAX, lo_open=True
        )
        window = self.sloppy_window_min
        if isinstance(window, bool) or not isinstance(window, int):
            raise ValueError(f"LifecycleParams.sloppy_window_min must be whole minutes: {window!r}")
        _require_range("sloppy_window_min", window, 1, SLOPPY_WINDOW_MAX_MIN, lo_open=False)
        if not isinstance(self.nodes, NodeParams):
            raise ValueError(f"LifecycleParams.nodes must be NodeParams, got {self.nodes!r}")

    @classmethod
    def from_config(cls, cfg: NodesConfig) -> LifecycleParams:
        return cls(
            decay_fraction=cfg.decay_fraction,
            sloppy_window_min=cfg.sloppy_seconds.window_min,
            sloppy_fraction=cfg.sloppy_seconds.fraction,
            dormant_distance_pct=cfg.dormant_distance_pct,
            nodes=NodeParams.from_config(cfg),
        )


@dataclass(frozen=True, slots=True)
class NodeState:
    """One Node's lifecycle state and, when labeled, the ``asOf`` Sloppy_Seconds holds from."""

    lifecycle: Lifecycle
    sloppy_since: Instant | None = None

    def __post_init__(self) -> None:
        if self.lifecycle not in LIFECYCLES:
            raise ValueError(f"NodeState.lifecycle must be one of {sorted(LIFECYCLES)}")

    @property
    def sloppy(self) -> bool:
        return self.sloppy_since is not None


type NodeStates = Mapping[NodeId, NodeState]
"""Per Map_State Node, in Map_State order and then ascending strike."""


def lifecycle_of(
    a_now: float,
    peak: float | None,
    *,
    delivered: bool,
    weekly_taps: int,
    decay_fraction: float,
) -> Lifecycle:
    """The first lifecycle rule that holds: Decaying, Delivered, Tested, Fresh (Req 6.15)."""
    if peak is not None and a_now <= (1.0 - decay_fraction) * peak:
        return "Decaying"
    if delivered:
        return "Delivered"
    if weekly_taps >= 1:
        return "Tested"
    return "Fresh"


def _abs_at(snapshot: Snapshot, strike: float) -> float | None:
    """The strike's absolute value in ``snapshot`` (first listing), or ``None`` when absent."""
    try:
        i = snapshot.strikes.index(strike)
    except ValueError:
        return None
    return abs(snapshot.values[i])


def _iso_week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)


# ---------------------------------------------------------------- state parts


@dataclass(frozen=True, slots=True)
class SessionPeaks:
    """Per strike, the highest ``a`` of one (symbol, metric) since 09:30 of the session.

    ``strikes`` are ascending and distinct, ``peaks`` parallel. ``folded_to``
    is the latest ``asOf`` folded in.
    """

    symbol: str
    metric: Metric
    folded_to: Instant
    strikes: tuple[float, ...]
    peaks: tuple[float, ...]

    def peak(self, strike: float) -> float | None:
        i = bisect_left(self.strikes, strike)
        return self.peaks[i] if i < len(self.strikes) and self.strikes[i] == strike else None


def _fold(
    prev: SessionPeaks | None, key: SymMetric, snapshots: Iterable[Snapshot]
) -> SessionPeaks | None:
    strikes, peaks = (prev.strikes, prev.peaks) if prev is not None else ((), ())
    folded_to = None if prev is None else prev.folded_to
    for s in snapshots:
        folded_to = s.as_of_ns if folded_to is None else max(folded_to, s.as_of_ns)
        if s.strikes == strikes:
            peaks = tuple(max(p, abs(v)) for p, v in zip(peaks, s.values, strict=True))
            continue
        merged = dict(zip(strikes, peaks, strict=True))
        for k, v in zip(s.strikes, s.values, strict=True):
            a = abs(v)
            if a > merged.get(k, -1.0):
                merged[k] = a
        strikes = tuple(sorted(merged))
        peaks = tuple(merged[k] for k in strikes)
    if folded_to is None:
        return prev
    return SessionPeaks(key[0], key[1], folded_to, strikes, peaks)


@dataclass(frozen=True, slots=True)
class SloppyWatch:
    """One session Tap whose Sloppy_Seconds window is not fully scanned yet.

    ``ref_abs`` is the strike's ``a`` at or before ``first_close_ns``;
    Snapshots with ``asOf`` up to ``scanned_to`` have been checked.
    """

    symbol: str
    metric: Metric
    strike: float
    seq: int
    first_close_ns: Instant
    ref_abs: float
    scanned_to: Instant

    @property
    def node(self) -> NodeId:
        return (self.symbol, self.metric, self.strike)


# ---------------------------------------------------------------- the state


@dataclass(frozen=True, slots=True)
class LifecycleState:
    """Lifecycle maxima, deliveries and Sloppy_Seconds labels; every method returns a new state.

    ``t`` is the latest Decision_Time evaluated and ``session`` its session.
    ``horizons`` holds, per instrument, the close of the latest bar consumed.
    ``deliveries`` maps each Node to the open of the latest RTH bar of
    ``delivery_week`` that delivered it. ``tap_seqs`` is the highest Tap
    ``seq`` per Node already given a Sloppy_Seconds watch this session, and
    ``sloppy`` maps each labeled Node to the ``asOf`` its label holds from.
    """

    t: Instant | None = None
    session: date | None = None
    horizons: tuple[tuple[str, Instant], ...] = ()
    delivery_week: tuple[int, int] | None = None
    deliveries: tuple[tuple[NodeId, Instant], ...] = ()
    peaks: tuple[SessionPeaks, ...] = ()
    watches: tuple[SloppyWatch, ...] = ()
    tap_seqs: tuple[tuple[NodeId, int], ...] = ()
    sloppy: tuple[tuple[NodeId, Instant], ...] = ()

    @classmethod
    def initial(cls) -> LifecycleState:
        return cls()

    def on_bar(self, bar: Bar, bands: Iterable[NodeBand]) -> LifecycleState:
        """Consume one closed 1-minute bar with the Node bands of the Map_State at its close.

        Feed the same stream as :meth:`fse.engine.taps.TapState.on_bar`.
        Raises ``ValueError`` for a bar of another interval, a bar without
        tick prices, or a bar that opens before its instrument's previous bar
        closed.
        """
        if bar.interval_s != BASE_INTERVAL_S:
            raise ValueError(
                f"the lifecycle takes {BASE_INTERVAL_S} s bars, got a {bar.interval_s} s bar"
            )
        if bar.l_t is None or bar.h_t is None:
            raise ValueError(f"{bar.instrument} bar opening at {bar.open_ns} has no tick prices")
        horizons = dict(self.horizons)
        last = horizons.get(bar.instrument)
        if last is not None and bar.open_ns < last:
            raise ValueError(
                f"{bar.instrument} bar opening at {bar.open_ns} is out of order: bars up to "
                f"{last} were already consumed"
            )
        horizons[bar.instrument] = bar.close_ns
        state = replace(self, horizons=tuple(sorted(horizons.items())))
        if not is_rth_bar(bar):
            return state
        week = _iso_week(ny_datetime(bar.open_ns).date())
        deliveries = dict(self.deliveries) if week == self.delivery_week else {}
        low, high = ticks_to_points(bar.l_t), ticks_to_points(bar.h_t)
        groups: dict[SymMetric, list[NodeBand]] = {}
        for b in bands:
            if b.instrument == bar.instrument:
                groups.setdefault((b.symbol, b.metric), []).append(b)
        for group in groups.values():
            hit = [b for b in group if b.overlaps(low, high)]
            for x in group:
                if any(y.strike != x.strike and not x.overlaps(y.lo, y.hi) for y in hit):
                    deliveries[x.node] = bar.open_ns
        return replace(state, delivery_week=week, deliveries=tuple(sorted(deliveries.items())))

    def on_bars(self, items: Iterable[tuple[Bar, Iterable[NodeBand]]]) -> LifecycleState:
        """Consume (bar, bands) pairs in order; equal to calling :meth:`on_bar` for each."""
        state = self
        for bar, bands in items:
            state = state.on_bar(bar, bands)
        return state

    def evaluate(
        self, view: MarketView, taps: TapView, params: LifecycleParams
    ) -> tuple[LifecycleState, NodeStates]:
        """The lifecycle state and Sloppy_Seconds label of every Map_State Node at ``view.t``.

        ``taps`` is the Tap tracker's view at the same instant. Returns the
        next state with the labels. Raises ``ValueError`` when ``taps`` is
        for another instant, ``view.t`` is before the previous Decision_Time
        or before the close of a bar already consumed.
        """
        t = view.t
        if taps.t != t:
            raise ValueError(f"Tap counts at {taps.t} given for the Decision_Time {t}")
        if self.t is not None and t < self.t:
            raise ValueError(f"lifecycle at {t} requested after evaluating {self.t}")
        for instrument, close_ns in self.horizons:
            if t < close_ns:
                raise ValueError(
                    f"lifecycle at {t} requested after consuming {instrument} bars up to {close_ns}"
                )
        session = session_of(t)
        state = self
        if session != self.session:
            state = replace(self, session=session, peaks=(), watches=(), tap_seqs=(), sloppy=())
        ms = view.map_state()
        peaks = state._folded(view, ms, session)
        watches, tap_seqs, sloppy = state._scanned(view, taps, params)
        nxt = replace(
            state,
            t=t,
            peaks=peaks,
            watches=watches,
            tap_seqs=tuple(sorted(tap_seqs.items())),
            sloppy=tuple(sorted(sloppy.items())),
        )

        by_key = {(p.symbol, p.metric): p for p in peaks}
        deliveries = dict(self.deliveries)
        out: dict[NodeId, NodeState] = {}
        for s in ms.snapshots():
            sp = by_key.get((s.symbol, s.metric))
            for strike in classify(s, params.nodes).nodes:
                node: NodeId = (s.symbol, s.metric, strike)
                stage = lifecycle_of(
                    abs(s.values[s.strikes.index(strike)]),  # a Node is a strike of its Snapshot
                    None if sp is None else sp.peak(strike),
                    delivered=_delivered(taps, node, deliveries.get(node)),
                    weekly_taps=taps.weekly_count(node),
                    decay_fraction=params.decay_fraction,
                )
                out[node] = NodeState(stage, sloppy.get(node))
        return nxt, MappingProxyType(out)

    # ---------------------------------------------------------------- internals

    def _folded(self, view: MarketView, ms: MapState, session: date) -> tuple[SessionPeaks, ...]:
        """The session peaks with the Snapshots new since the last fold."""
        rth_open = ny_instant(session, RTH_OPEN)
        prev = {(p.symbol, p.metric): p for p in self.peaks}
        out: list[SessionPeaks] = []
        for key in ms:
            p = prev.get(key)
            # Re-reading the latest folded asOf is harmless: a maximum is idempotent.
            lo = rth_open if p is None else p.folded_to
            folded = p
            if lo <= view.t:
                folded = _fold(p, key, view.snapshots_between(key[0], key[1], lo, view.t))
            if folded is not None:
                out.append(folded)
        return tuple(out)

    def _scanned(
        self, view: MarketView, taps: TapView, params: LifecycleParams
    ) -> tuple[tuple[SloppyWatch, ...], dict[NodeId, int], dict[NodeId, Instant]]:
        """Watch the session's new Taps and scan every open Sloppy_Seconds window up to ``t``."""
        t = view.t
        seqs = dict(self.tap_seqs)
        labeled = dict(self.sloppy)
        watches = list(self.watches)
        for tap in taps.taps:
            node = tap.node
            if tap.seq <= seqs.get(node, 0):
                continue
            seqs[node] = tap.seq
            ref = view.snapshot_at_or_before(tap.symbol, tap.metric, tap.first_close_ns)
            ref_abs = None if ref is None else _abs_at(ref, tap.strike)
            if ref_abs is not None:
                watches.append(
                    SloppyWatch(
                        tap.symbol, tap.metric, tap.strike, tap.seq,
                        tap.first_close_ns, ref_abs, tap.first_close_ns,
                    )
                )  # fmt: skip
        window_ns = params.sloppy_window_min * NS_PER_MINUTE
        sloppy = dict(labeled)
        still: list[SloppyWatch] = []
        for w in watches:
            if w.node in labeled:
                continue
            end = w.first_close_ns + window_ns
            hi = min(end, t)
            lo = max(w.first_close_ns + 1, w.scanned_to)
            found: Instant | None = None
            if lo <= hi:
                limit = (1.0 - params.sloppy_fraction) * w.ref_abs
                for s in view.snapshots_between(w.symbol, w.metric, lo, hi):
                    a = _abs_at(s, w.strike)
                    if a is not None and a <= limit:
                        found = s.as_of_ns
                        break
            if found is not None:
                sloppy[w.node] = min(found, sloppy.get(w.node, found))
            elif hi < end:
                still.append(replace(w, scanned_to=hi))
        return tuple(w for w in still if w.node not in sloppy), seqs, sloppy


def _delivered(taps: TapView, node: NodeId, delivered_at: Instant | None) -> bool:
    """Whether a delivery bar opened at or after the end of the Node's latest Tap this week."""
    latest = taps.latest_tap(node)
    return (
        latest is not None
        and latest.ended
        and delivered_at is not None
        and delivered_at >= latest.end_ns
    )


# ---------------------------------------------------------------- Dormant


def dormant_nodes(
    ms: MapState, regime: Regime | RegimeResult | MissingInput, params: LifecycleParams
) -> tuple[NodeId, ...]:
    """The Dormant Nodes of ``ms``, in Map_State order and ascending strike (Req 6.17-6.18).

    None when the Regime is Vanna_Dominant. Otherwise, including a
    ``MissingInput`` Regime, each Node with ``|strike - spot| >
    dormant_distance_pct / 100 * spot``, with ``spot`` from its Snapshot.
    """
    label = regime.regime if isinstance(regime, RegimeResult) else regime
    if label == "Vanna_Dominant":
        return ()
    out: list[NodeId] = []
    for s in ms.snapshots():
        limit = params.dormant_distance_pct / 100.0 * s.spot
        out.extend(
            (s.symbol, s.metric, strike)
            for strike in classify(s, params.nodes).nodes
            if abs(strike - s.spot) > limit
        )
    return tuple(out)
