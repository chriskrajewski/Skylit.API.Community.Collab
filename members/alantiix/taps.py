"""The Tap tracker (design §6 "Tap tracker", Req 6.14).

A Tap is futures price trading inside a Node's Deflection_Band. :class:`TapState`
consumes closed 1-minute bars in time order, one at a time, with the Node bands
of the Map_State at each bar's close, and answers :meth:`TapState.view` at a
Decision_Time. Every state is a frozen value that depends only on the bars and
bands fed, in order.

**Bands.** For a bar closing at ``c``, the Nodes are those of the Map_State at
``c``: per configured (symbol, metric), the Snapshot with the latest ``asOf <=
c`` and its Nodes (:func:`fse.engine.nodes.classify`). Their bands follow the
Level_Converter rules (:mod:`fse.engine.levels`): each Snapshot paired with its
own futures bar, ES half-width ``es_half_width_pts``, NQ half-width
``qqq_half_width_usd x`` the QQQ ratio of the same metric. :func:`node_bands`
builds them from a MarketView whose ``t`` is at or after ``c``. A Node whose
Snapshot or price is missing at ``c`` has no band, so it cannot overlap.

**Counting.** A bar counts only for Nodes whose symbol maps to the bar's
instrument (``data.instruments``), and only when it opens within RTH: 09:30 to
16:00 New York time on a weekday (:func:`is_rth_bar`). Overlap is
``bar.low <= band.hi and bar.high >= band.lo``, on the bar's tick prices in
points. A Tap is added when the bar overlaps and either it is the session's
first RTH bar of its instrument or the previous RTH bar of that instrument did
not overlap that Node's band. "Previous" is the previous bar consumed, so a
missing minute does not split a Tap. Nodes are identified by (symbol, metric,
strike).

**Ends.** A Tap ends at the close of its last consecutive overlapping bar. It is
marked ``ended`` once a later RTH bar of its instrument in the same session does
not overlap the band, or when the next session starts.

**Resets.** An RTH bar belongs to the session of its New York date. The session
counts reset at each session's start, the weekly counts at the first session of
each Monday-Friday (ISO) week. :meth:`TapState.view` applies the same rollover
for its Decision_Time, so a view at 09:30 of a new session shows no session
Taps before any of that session's bars arrive.

**Point in time** (Req 5.3). ``view(t)`` raises when ``t`` is before the close
of a bar already consumed, so a state never answers for an earlier instant.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final

from fse.engine.levels import (
    NQ_BAND_SYMBOL,
    Conversion,
    LevelParams,
    band,
    level_family,
    pair,
    ticks_to_points,
)
from fse.engine.nodes import NodeParams, classify
from fse.engine.types import Bar, Metric, MissingPrice
from fse.pit.protocols import MarketView, SymMetric
from fse.timekit import RTH_CLOSE, RTH_OPEN, TRADING_DAY_START, Instant, ny_datetime

__all__ = [
    "BASE_INTERVAL_S",
    "NodeBand",
    "NodeId",
    "Tap",
    "TapState",
    "TapView",
    "is_rth_bar",
    "node_bands",
    "session_of",
]

BASE_INTERVAL_S: Final = 60
"""Taps are counted on 1-minute bars (Req 6.14)."""

type NodeId = tuple[str, Metric, float]
"""A Node's identity across Snapshots: (symbol, metric, strike)."""

_ONE_DAY: Final = timedelta(days=1)


# ---------------------------------------------------------------- sessions


def is_rth_bar(bar: Bar) -> bool:
    """Whether ``bar`` opens within RTH: 09:30 to 16:00 New York time on a weekday."""
    local = ny_datetime(bar.open_ns)
    return local.weekday() < 5 and RTH_OPEN <= local.time() < RTH_CLOSE


def session_of(t: Instant) -> date:
    """The session whose trading day holds ``t``: its New York date, the next date from 18:00."""
    local = ny_datetime(t)
    day = local.date()
    return day + _ONE_DAY if local.time() >= TRADING_DAY_START else day


def _week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)


# ---------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class NodeBand:
    """One Node's Deflection_Band ``[lo, hi]`` in points, in the Map_State at a bar's close.

    ``instrument`` is the futures instrument whose bars can tap it.
    """

    symbol: str
    metric: Metric
    strike: float
    instrument: str
    lo: float
    hi: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lo) and math.isfinite(self.hi)) or self.lo > self.hi:
            raise ValueError(
                f"a Deflection_Band needs finite edges with lo <= hi, got ({self.lo}, {self.hi})"
            )

    @property
    def node(self) -> NodeId:
        return (self.symbol, self.metric, self.strike)

    def overlaps(self, low: float, high: float) -> bool:
        """Whether the range ``[low, high]`` meets the band; touching an edge counts."""
        return low <= self.hi and high >= self.lo


@dataclass(frozen=True, slots=True)
class Tap:
    """One Tap: a maximal run of consecutive RTH bars overlapping a Node's band.

    ``seq`` is 1 for the Node's first Tap of ``session``. ``first_open_ns`` and
    ``first_close_ns`` bound the run's first bar. ``end_ns`` is the close of
    the run's latest overlapping bar; it is final once ``ended``.
    """

    symbol: str
    metric: Metric
    strike: float
    instrument: str
    session: date
    seq: int
    first_open_ns: Instant
    first_close_ns: Instant
    end_ns: Instant
    ended: bool = False

    @property
    def node(self) -> NodeId:
        return (self.symbol, self.metric, self.strike)


@dataclass(frozen=True, slots=True)
class TapView:
    """The Tap counts at Decision_Time ``t``, read-only.

    ``taps`` holds the session's Taps in start order. ``prior_week_counts``
    and ``prior_week_latest`` cover the earlier sessions of the session's week.
    """

    t: Instant
    session: date
    taps: tuple[Tap, ...]
    prior_week_counts: Mapping[NodeId, int]
    prior_week_latest: Mapping[NodeId, Tap]
    _by_node: Mapping[NodeId, tuple[Tap, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_node: dict[NodeId, list[Tap]] = {}
        for tap in self.taps:
            by_node.setdefault(tap.node, []).append(tap)
        frozen = MappingProxyType({k: tuple(v) for k, v in by_node.items()})
        counts = MappingProxyType(dict(self.prior_week_counts))
        latest = MappingProxyType(dict(self.prior_week_latest))
        object.__setattr__(self, "_by_node", frozen)
        object.__setattr__(self, "prior_week_counts", counts)
        object.__setattr__(self, "prior_week_latest", latest)

    def session_taps(self, node: NodeId) -> tuple[Tap, ...]:
        """The Node's Taps this session, in order (``seq`` 1, 2, ...)."""
        return self._by_node.get(node, ())

    def session_count(self, node: NodeId) -> int:
        """The Node's session Tap count (Req 6.14)."""
        return len(self.session_taps(node))

    def weekly_count(self, node: NodeId) -> int:
        """The Node's weekly Tap count: this session's plus the week's earlier sessions'."""
        return self.prior_week_counts.get(node, 0) + self.session_count(node)

    def latest_tap(self, node: NodeId) -> Tap | None:
        """The Node's latest Tap this week, or ``None`` when it has none."""
        taps = self.session_taps(node)
        return taps[-1] if taps else self.prior_week_latest.get(node)

    def ended_count(self, node: NodeId) -> int:
        """The Node's Taps this session that ended before ``t``."""
        return sum(1 for tap in self.session_taps(node) if tap.ended)

    def tap_seq(self, node: NodeId) -> int:
        """The Setup_Key Tap sequence number: 1 plus :meth:`ended_count` (Req 10.11)."""
        return 1 + self.ended_count(node)


# ---------------------------------------------------------------- the state machine


@dataclass(frozen=True, slots=True)
class TapState:
    """The Tap tracker state.

    ``session`` is the session of the latest RTH bar consumed. ``horizons``
    holds, per instrument, the close of its latest 1-minute bar consumed.
    ``open_taps`` are the session's Taps whose latest bar overlapped;
    ``ended_taps`` the session's ended Taps, in the order they ended.
    ``week_counts`` and ``week_latest`` cover the earlier sessions of the
    session's week. Every method returns a new state and leaves this one
    unchanged.
    """

    session: date | None = None
    horizons: tuple[tuple[str, Instant], ...] = ()
    open_taps: tuple[Tap, ...] = ()
    ended_taps: tuple[Tap, ...] = ()
    week_counts: tuple[tuple[NodeId, int], ...] = ()
    week_latest: tuple[Tap, ...] = ()

    @classmethod
    def initial(cls) -> TapState:
        return cls()

    def on_bar(self, bar: Bar, bands: Iterable[NodeBand]) -> TapState:
        """Consume one closed 1-minute bar with the Node bands of the Map_State at its close.

        Bars of every instrument may be fed; bands of other instruments and
        bars that do not open within RTH change no count. Raises
        ``ValueError`` for a bar of another interval, a bar without tick
        prices, a bar that opens before its instrument's previous bar closed,
        or two bands of one Node for the bar's instrument.
        """
        if bar.interval_s != BASE_INTERVAL_S:
            raise ValueError(
                f"the Tap tracker takes {BASE_INTERVAL_S} s bars, got a {bar.interval_s} s bar"
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
        session = ny_datetime(bar.open_ns).date()
        low, high = ticks_to_points(bar.l_t), ticks_to_points(bar.h_t)
        return state._rolled(session)._count(bar, session, low, high, bands)

    def on_bars(self, items: Iterable[tuple[Bar, Iterable[NodeBand]]]) -> TapState:
        """Consume (bar, bands) pairs in order; equal to calling :meth:`on_bar` for each."""
        state = self
        for bar, bands in items:
            state = state.on_bar(bar, bands)
        return state

    def view(self, t: Instant) -> TapView:
        """The Tap counts at Decision_Time ``t`` from the bars consumed so far.

        Raises ``ValueError`` when ``t`` is before the close of a bar already
        consumed (a count at ``t`` uses only bars with ``close_ns <= t``).
        """
        for instrument, close_ns in self.horizons:
            if t < close_ns:
                raise ValueError(
                    f"Taps at {t} requested after consuming {instrument} bars up to {close_ns}"
                )
        session = session_of(t)
        state = self._rolled(session)
        taps = sorted(
            (*state.ended_taps, *state.open_taps), key=lambda tap: (tap.first_open_ns, tap.node)
        )
        return TapView(
            t=t,
            session=session,
            taps=tuple(taps),
            prior_week_counts=dict(state.week_counts),
            prior_week_latest={tap.node: tap for tap in state.week_latest},
        )

    # ---------------------------------------------------------------- internals

    def _rolled(self, session: date) -> TapState:
        """This state at the start of ``session``; ``self`` when it is the current one."""
        if self.session == session:
            return self
        if self.session is not None and session < self.session:
            raise ValueError(f"session {session} is before the current session {self.session}")
        week_counts: tuple[tuple[NodeId, int], ...] = ()
        week_latest: tuple[Tap, ...] = ()
        if self.session is not None and _week(session) == _week(self.session):
            counts = Counter(dict(self.week_counts))
            latest = {tap.node: tap for tap in self.week_latest}
            ended = (*self.ended_taps, *(replace(tap, ended=True) for tap in self.open_taps))
            for tap in sorted(ended, key=lambda tap: tap.seq):
                counts[tap.node] += 1
                latest[tap.node] = tap
            week_counts = tuple(sorted(counts.items()))
            week_latest = tuple(sorted(latest.values(), key=lambda tap: tap.node))
        return replace(
            self,
            session=session,
            open_taps=(),
            ended_taps=(),
            week_counts=week_counts,
            week_latest=week_latest,
        )

    def _count(
        self, bar: Bar, session: date, low: float, high: float, bands: Iterable[NodeBand]
    ) -> TapState:
        """Count one RTH bar of the current session."""
        hit: dict[NodeId, NodeBand] = {}
        seen: set[NodeId] = set()
        for b in bands:
            if b.instrument != bar.instrument:
                continue
            node = b.node
            if node in seen:
                raise ValueError(f"two Deflection_Bands given for Node {node}")
            seen.add(node)
            if b.overlaps(low, high):
                hit[node] = b
        still_open: list[Tap] = []
        ended = list(self.ended_taps)
        for tap in self.open_taps:
            if tap.instrument != bar.instrument:
                still_open.append(tap)
            elif hit.pop(tap.node, None) is not None:
                still_open.append(replace(tap, end_ns=bar.close_ns))
            else:
                ended.append(replace(tap, ended=True))
        if hit:
            done = Counter(tap.node for tap in ended)
            for node, b in hit.items():
                still_open.append(
                    Tap(
                        symbol=b.symbol,
                        metric=b.metric,
                        strike=b.strike,
                        instrument=b.instrument,
                        session=session,
                        seq=done[node] + 1,
                        first_open_ns=bar.open_ns,
                        first_close_ns=bar.close_ns,
                        end_ns=bar.close_ns,
                    )
                )
        return replace(self, open_taps=tuple(still_open), ended_taps=tuple(ended))


# ---------------------------------------------------------------- bands at a bar's close


def node_bands(
    view: MarketView, at: Instant, nodes: NodeParams, levels: LevelParams
) -> tuple[NodeBand, ...]:
    """The Nodes of the Map_State at ``at`` (a bar's close) with their Deflection_Bands.

    For each configured (symbol, metric), in Map_State order, the Snapshot
    with the latest ``asOf <= at``, its Nodes in strike order, and their bands
    by the Level_Converter rules (Req 8.6-8.7). A (symbol, metric) with no
    Snapshot, no Nodes or a ``MissingPrice`` contributes no band. At
    ``at == view.t`` the bands equal those of
    :func:`fse.engine.levels.convert_map`. Raises ``ValueError`` when ``at``
    is after ``view.t`` or a configured symbol has no futures family.
    """
    if at > view.t:
        raise ValueError(f"bands at {at} requested from a MarketView at {view.t}")
    keys = tuple(view.map_state().entries)
    snapshots = {key: view.snapshot_at_or_before(*key, at) for key in keys}
    pairings: dict[SymMetric, Conversion | MissingPrice] = {}

    def pairing(key: SymMetric) -> Conversion | MissingPrice:
        found = pairings.get(key)
        if found is None:
            snapshot = snapshots.get(key)
            found = MissingPrice(key[0]) if snapshot is None else pair(snapshot, view, levels)
            pairings[key] = found
        return found

    out: list[NodeBand] = []
    for key in keys:
        symbol, metric = key
        family = level_family(symbol)
        snapshot = snapshots[key]
        if snapshot is None:
            continue
        strikes = classify(snapshot, nodes).nodes
        if not strikes:
            continue
        conv = pairing(key)
        if isinstance(conv, MissingPrice):
            continue
        if family == "ES":
            half_width = levels.levels.es_half_width_pts
        else:
            band_conv = pairing((NQ_BAND_SYMBOL, metric))
            if isinstance(band_conv, MissingPrice):
                continue
            half_width = levels.levels.qqq_half_width_usd * band_conv.factor
        for strike in strikes:
            lo, hi = band(conv.level(strike), half_width)
            out.append(NodeBand(symbol, metric, strike, conv.instrument, lo, hi))
    return tuple(out)
