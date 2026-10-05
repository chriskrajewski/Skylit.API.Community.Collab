"""The historical MarketView (design §5 "Point-in-time layer", "Time model", D8).

:class:`HistoricalInputs` indexes one session's inputs once: Snapshots per
configured (symbol, metric) of the configured Heatmap_View, bars per
(instrument, interval), the VIX daily values and 1-minute VIX bars, dark-pool
prints per fetched ticker, and the economic events. ``inputs.view(t)`` is a
cheap :class:`HistoricalMarketView` that sees each index truncated at ``t``.

Availability (D8): a plain record is available at its Observation_Time
(historical backtest). A :class:`~fse.pit.asof.Received` record, as read from a
live recording in replay mode, is available at ``max(Observation_Time,
receipt_time)``. VIX daily values are available at their recorded
Observation_Times (09:31 for the open, the prior session's close).

Snapshots of another Heatmap_View or of a symbol or metric that is not
configured are left out, so Map_State is always built from the configured view
(Req 5.1). The Backtester builds one ``HistoricalInputs`` per session, so
"same session" lookups such as Node_Velocity's ``v0`` need no extra filter.

**Live** (design "Time model", Req 5.1-5.3, 23.9). :class:`LiveInputs` holds
the same series as append-only :class:`~fse.pit.asof.RingIndex` buffers. The
live feeds append each input with its receipt time, so it is available at
``max(Observation_Time, receipt_time)``; ``inputs.view(t)`` is a
:class:`LiveMarketView` that sees each buffer truncated at ``t``. The view code
is shared with :class:`HistoricalMarketView`, and a ring answers every query as
an :class:`~fse.pit.asof.AsOfIndex` of the same records in the same order does,
so a replay of the recording (``HistoricalInputs`` of ``Received`` records)
sees what the live view saw. VIX daily values become available at
``max(Observation_Time, receipt_time)`` too (:func:`vix_daily_received`). The
Live_Runner starts a new ``LiveInputs`` for each session.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Final, cast

from fse.config.schema.data import HeatmapViewConfig
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import HeatmapView
from fse.engine.types import (
    METRICS,
    Bar,
    DarkPoolPrint,
    EconomicEvent,
    Metric,
    Snapshot,
    Unavailable,
    VixState,
)
from fse.pit.asof import (
    DEFAULT_RING_CAPACITY,
    AsOfIndex,
    Received,
    RingIndex,
    available_at,
    observation_time,
)
from fse.pit.protocols import (
    FUTURES_PRICE_INTERVAL_S,
    MapState,
    SymMetric,
    missing_snapshot,
)
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "MAP_METRICS",
    "VIX_BAR_INTERVAL_S",
    "HistoricalInputs",
    "HistoricalMarketView",
    "LiveInputs",
    "LiveMarketView",
    "heatmap_view",
    "vix_daily_received",
    "vix_daily_slot",
]

MAP_METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
"""The metrics of every configured symbol in Map_State (Req 3.3-3.4)."""

VIX_BAR_INTERVAL_S: Final = 60


def heatmap_view(cfg: HeatmapViewConfig) -> HeatmapView:
    """The Data_Cache Heatmap_View for the ``data.heatmap_view`` config section."""
    return HeatmapView(
        max_strikes=cfg.max_strikes,
        max_expirations=cfg.max_expirations,
        expirations=cfg.expirations,
        include_empty=cfg.include_empty,
    )


def _record[T](item: T | Received[T]) -> T:
    return cast("T", item.record) if isinstance(item, Received) else item


def _require[T](label: str, item: object, kind: type[T]) -> T:
    if not isinstance(item, kind):
        raise ValueError(f"{label} must be {kind.__name__}, got {type(item).__name__}")
    return item


def _map_keys(symbols: Iterable[str], metrics: Iterable[Metric]) -> tuple[SymMetric, ...]:
    syms = tuple(symbols)
    mets = tuple(metrics)
    for s in syms:
        if not isinstance(s, str) or not s.strip():
            raise ValueError(f"symbols entries must be non-blank strings, got {s!r}")
    for m in mets:
        if m not in METRICS:
            raise ValueError(f"metrics entries must be one of {sorted(METRICS)}, got {m!r}")
    for name, values in (("symbols", syms), ("metrics", mets)):
        if len(set(values)) != len(values):
            raise ValueError(f"{name} lists an entry more than once: {values!r}")
    return tuple((s, m) for s in syms for m in mets)


def _check_view_id(view_id: object) -> str:
    if not isinstance(view_id, str) or not view_id:
        raise ValueError(f"view_id must be a non-blank string, got {view_id!r}")
    return view_id


def _check_vix_daily(today: VixDailyRecord | None, prior: VixDailyRecord | None) -> None:
    for label, record in (("vix_today", today), ("vix_prior", prior)):
        if record is not None:
            _require(label, record, VixDailyRecord)
    if today is not None and prior is not None and not prior.session < today.session:
        raise ValueError(
            f"vix_prior is for {prior.session}, not before vix_today's {today.session}"
        )


def vix_daily_received(record: VixDailyRecord, received_ns: Instant) -> VixDailyRecord:
    """``record`` with each value's instant moved to ``max(Observation_Time, receipt_time)`` (D8).

    The live view and the replay of a recording both use it, so a VIX daily
    value received live becomes usable at the same instant in both.
    """
    _require("record", record, VixDailyRecord)
    return replace(
        record,
        open_at_ns=None
        if record.open_at_ns is None
        else available_at(record.open_at_ns, received_ns),
        close_at_ns=(
            None if record.close_at_ns is None else available_at(record.close_at_ns, received_ns)
        ),
    )


class HistoricalInputs:
    """One session's inputs, indexed once and viewed at each Decision_Time.

    ``symbols`` and ``metrics`` fix the Map_State keys and their order;
    ``view_id`` is the configured Heatmap_View's id. ``dark_pool`` maps each
    ticker whose prints were fetched for the session to those prints; a ticker
    that is absent was not fetched. Raises ``ValueError`` for inputs of the
    wrong type, repeated keys, VIX bars that are not 1-minute bars, dark-pool
    prints filed under another ticker, or a prior VIX record that is not
    before the session's record.
    """

    __slots__ = (
        "_bars",
        "_dark_pool",
        "_events",
        "_keys",
        "_snapshots",
        "_view_id",
        "_vix_bars",
        "_vix_prior",
        "_vix_today",
    )

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        view_id: str,
        metrics: Iterable[Metric] = MAP_METRICS,
        snapshots: Iterable[Snapshot | Received[Snapshot]] = (),
        bars: Iterable[Bar | Received[Bar]] = (),
        vix_today: VixDailyRecord | None = None,
        vix_prior: VixDailyRecord | None = None,
        vix_bars: Iterable[Bar | Received[Bar]] = (),
        dark_pool: Mapping[str, Iterable[DarkPoolPrint | Received[DarkPoolPrint]]] | None = None,
        events: Iterable[EconomicEvent] = (),
    ) -> None:
        self._keys: tuple[SymMetric, ...] = _map_keys(symbols, metrics)
        self._view_id = _check_view_id(view_id)

        by_key: dict[SymMetric, list[Snapshot | Received[Snapshot]]] = {k: [] for k in self._keys}
        for i, snap_item in enumerate(snapshots):
            snap = _require(f"snapshots[{i}]", _record(snap_item), Snapshot)
            group = by_key.get((snap.symbol, snap.metric))
            if group is not None and snap.view_id == view_id:
                group.append(snap_item)
        self._snapshots: dict[SymMetric, AsOfIndex[Snapshot]] = {
            k: AsOfIndex.of(v, observation_time) for k, v in by_key.items()
        }

        by_series: dict[tuple[str, int], list[Bar | Received[Bar]]] = {}
        for i, bar_item in enumerate(bars):
            bar = _require(f"bars[{i}]", _record(bar_item), Bar)
            by_series.setdefault((bar.instrument, bar.interval_s), []).append(bar_item)
        self._bars: dict[tuple[str, int], AsOfIndex[Bar]] = {
            k: AsOfIndex.of(v, observation_time) for k, v in by_series.items()
        }

        vix_items = list(vix_bars)
        for i, vix_item in enumerate(vix_items):
            vix_bar = _require(f"vix_bars[{i}]", _record(vix_item), Bar)
            if vix_bar.interval_s != VIX_BAR_INTERVAL_S:
                raise ValueError(
                    f"vix_bars[{i}] is a {vix_bar.interval_s}s bar, not a 1-minute bar"
                )
        self._vix_bars: AsOfIndex[Bar] = AsOfIndex.of(vix_items, observation_time)

        _check_vix_daily(vix_today, vix_prior)
        self._vix_today = vix_today
        self._vix_prior = vix_prior

        self._dark_pool: dict[str, AsOfIndex[DarkPoolPrint]] = {}
        for ticker, prints in (dark_pool or {}).items():
            items = list(prints)
            for i, print_item in enumerate(items):
                p = _require(f"dark_pool[{ticker!r}][{i}]", _record(print_item), DarkPoolPrint)
                if p.ticker != ticker:
                    raise ValueError(
                        f"dark_pool[{ticker!r}][{i}] is a print for {p.ticker!r}, not {ticker!r}"
                    )
            self._dark_pool[ticker] = AsOfIndex.of(items, observation_time)

        evs = [_require(f"events[{i}]", e, EconomicEvent) for i, e in enumerate(events)]
        self._events: tuple[EconomicEvent, ...] = tuple(
            sorted(evs, key=lambda e: (e.release_ns, e.event_type))
        )

    @property
    def keys(self) -> tuple[SymMetric, ...]:
        """The Map_State keys in configured order: each symbol, then each metric."""
        return self._keys

    @property
    def view_id(self) -> str:
        return self._view_id

    def view(self, t: Instant) -> HistoricalMarketView:
        """The MarketView at Decision_Time ``t``."""
        return HistoricalMarketView(self, t)


class _IndexedView:
    """The :class:`~fse.pit.protocols.MarketView` queries over indexed inputs at ``t``."""

    __slots__ = ("_in", "_map_state", "_t")

    def __init__(self, inputs: HistoricalInputs | LiveInputs, t: Instant) -> None:
        if not isinstance(t, int) or isinstance(t, bool):
            raise ValueError(f"t must be an integer Instant, got {t!r}")
        self._in = inputs
        self._t = t
        self._map_state: MapState | None = None

    @property
    def t(self) -> Instant:
        return self._t

    def map_state(self) -> MapState:
        if self._map_state is None:
            entries: dict[SymMetric, Snapshot | Unavailable] = {}
            for key, index in self._in._snapshots.items():
                snap = index.latest(self._t)
                entries[key] = snap if snap is not None else missing_snapshot(*key)
            self._map_state = MapState(self._t, entries)
        return self._map_state

    def snapshot_at_or_before(self, symbol: str, metric: Metric, at: Instant) -> Snapshot | None:
        index = self._in._snapshots.get((symbol, metric))
        return None if index is None else index.latest_at_or_before(self._t, at)

    def snapshots_between(
        self, symbol: str, metric: Metric, lo: Instant, hi: Instant
    ) -> Iterator[Snapshot]:
        index = self._in._snapshots.get((symbol, metric))
        return iter(() if index is None else index.between(self._t, lo, hi))

    def bars(self, instrument: str, interval_s: int, since: Instant) -> Sequence[Bar]:
        index = self._in._bars.get((instrument, interval_s))
        if index is None:
            return ()
        # open_ns >= since  <=>  close_ns >= since + interval (the index is by close).
        return tuple(index.between(self._t, since + interval_s * NS_PER_SECOND, self._t))

    def last_bar_closed_at_or_before(self, instrument: str, at: Instant) -> Bar | None:
        index = self._in._bars.get((instrument, FUTURES_PRICE_INTERVAL_S))
        return None if index is None else index.latest_at_or_before(self._t, at)

    def vix(self) -> VixState:
        today, prior = self._in._vix_today, self._in._vix_prior
        if today is None:
            daily_open: float | Unavailable = Unavailable("no VIX daily record for the session")
        else:
            daily_open = self._daily(today.open, today.open_at_ns, "the session's VIX daily open")
        if prior is None:
            prior_close: float | Unavailable = Unavailable(
                "no VIX daily record for the prior session"
            )
        else:
            prior_close = self._daily(prior.close, prior.close_at_ns, "the prior VIX close")
        bar = self._in._vix_bars.latest(self._t)
        last: float | Unavailable = (
            bar.c
            if bar is not None
            else Unavailable("no VIX 1-minute bar closed at or before the Decision_Time")
        )
        return VixState(daily_open=daily_open, prior_close=prior_close, last_1m_close=last)

    def _daily(self, value: float | None, at_ns: Instant | None, what: str) -> float | Unavailable:
        if value is None or at_ns is None:
            return Unavailable(f"{what} is missing")
        if at_ns > self._t:
            return Unavailable(f"{what} is not observed until after the Decision_Time")
        return value

    def dark_pool(self, ticker: str, since: Instant) -> Sequence[DarkPoolPrint] | Unavailable:
        index = self._in._dark_pool.get(ticker)
        if index is None:
            return Unavailable(f"{ticker} dark-pool prints were not fetched for the session")
        return tuple(index.between(self._t, since, self._t))

    def events(self) -> Sequence[EconomicEvent]:
        return self._in._events


def vix_daily_slot(
    current: VixDailyRecord | None,
    raw: VixDailyRecord | None,
    new: VixDailyRecord | None,
    received_ns: Instant,
) -> tuple[VixDailyRecord | None, VixDailyRecord | None]:
    """The (available, as received) VIX record of a slot after setting ``new`` at ``received_ns``.

    An unchanged record keeps its earlier receipt time; ``LiveInputs`` and replay share this.
    """
    if new is None:
        return None, None
    if new == raw and current is not None:
        return current, raw
    return vix_daily_received(new, received_ns), new


class HistoricalMarketView(_IndexedView):
    """A :class:`~fse.pit.protocols.MarketView` over :class:`HistoricalInputs` at ``t``."""

    __slots__ = ()

    def __init__(self, inputs: HistoricalInputs, t: Instant) -> None:
        super().__init__(inputs, t)


# ---------------------------------------------------------------- live


class LiveInputs:
    """One live session's inputs in append-only ring buffers (design "Time model").

    The same keys and filters as :class:`HistoricalInputs`: Snapshots of the
    configured Heatmap_View per configured (symbol, metric), bars per
    (instrument, interval), 1-minute VIX bars, dark-pool prints per fetched
    ticker, and the economic events. Every ``add_*`` call takes the receipt
    time; the input is available at ``max(Observation_Time, receipt_time)``.
    ``capacity`` bounds each series (:class:`~fse.pit.asof.RingIndex`).

    The Live_Runner appends inputs in receipt order and builds one view per
    Decision_Time with :meth:`view`. A view sees only inputs available at its
    ``t``, so inputs appended after it was built stay invisible to it unless
    they were available by ``t``.
    """

    __slots__ = (
        "_bars",
        "_capacity",
        "_dark_pool",
        "_events",
        "_keys",
        "_raw_prior",
        "_raw_today",
        "_snapshots",
        "_view_id",
        "_vix_bars",
        "_vix_prior",
        "_vix_today",
    )

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        view_id: str,
        metrics: Iterable[Metric] = MAP_METRICS,
        events: Iterable[EconomicEvent] = (),
        capacity: int = DEFAULT_RING_CAPACITY,
    ) -> None:
        self._keys: tuple[SymMetric, ...] = _map_keys(symbols, metrics)
        self._view_id = _check_view_id(view_id)
        self._capacity = capacity
        self._snapshots: dict[SymMetric, RingIndex[Snapshot]] = {
            k: RingIndex(capacity) for k in self._keys
        }
        self._bars: dict[tuple[str, int], RingIndex[Bar]] = {}
        self._vix_bars: RingIndex[Bar] = RingIndex(capacity)
        self._dark_pool: dict[str, RingIndex[DarkPoolPrint]] = {}
        evs = [_require(f"events[{i}]", e, EconomicEvent) for i, e in enumerate(events)]
        self._events: tuple[EconomicEvent, ...] = tuple(
            sorted(evs, key=lambda e: (e.release_ns, e.event_type))
        )
        self._vix_today: VixDailyRecord | None = None
        self._vix_prior: VixDailyRecord | None = None
        self._raw_today: VixDailyRecord | None = None
        self._raw_prior: VixDailyRecord | None = None

    @property
    def keys(self) -> tuple[SymMetric, ...]:
        """The Map_State keys in configured order: each symbol, then each metric."""
        return self._keys

    @property
    def view_id(self) -> str:
        return self._view_id

    def add_snapshot(self, snapshot: Snapshot, received_ns: Instant) -> bool:
        """Append ``snapshot``; ``False`` (nothing kept) for another view or an unconfigured key."""
        snap = _require("snapshot", snapshot, Snapshot)
        ring = self._snapshots.get((snap.symbol, snap.metric))
        if ring is None or snap.view_id != self._view_id:
            return False
        ring.append(snap, observation_time(snap), received_ns)
        return True

    def add_bar(self, bar: Bar, received_ns: Instant) -> None:
        """Append a futures bar (any interval) to its (instrument, interval) series."""
        b = _require("bar", bar, Bar)
        ring = self._bars.get((b.instrument, b.interval_s))
        if ring is None:
            ring = self._bars[(b.instrument, b.interval_s)] = RingIndex(self._capacity)
        ring.append(b, observation_time(b), received_ns)

    def add_vix_bar(self, bar: Bar, received_ns: Instant) -> None:
        """Append a 1-minute VIX bar; ``ValueError`` for another interval."""
        b = _require("bar", bar, Bar)
        if b.interval_s != VIX_BAR_INTERVAL_S:
            raise ValueError(f"a VIX bar must be a 1-minute bar, not a {b.interval_s}s bar")
        self._vix_bars.append(b, observation_time(b), received_ns)

    def set_vix_daily(
        self,
        today: VixDailyRecord | None,
        prior: VixDailyRecord | None,
        received_ns: Instant,
    ) -> None:
        """Set the session's and prior session's VIX daily records (:func:`vix_daily_received`).

        A record equal to the one already set keeps its earlier receipt time.
        """
        _check_vix_daily(today, prior)
        self._vix_today, self._raw_today = vix_daily_slot(
            self._vix_today, self._raw_today, today, received_ns
        )
        self._vix_prior, self._raw_prior = vix_daily_slot(
            self._vix_prior, self._raw_prior, prior, received_ns
        )

    def add_dark_pool(
        self, ticker: str, prints: Iterable[DarkPoolPrint], received_ns: Instant
    ) -> None:
        """Append one fetch of ``ticker``'s prints; the ticker counts as fetched from now on."""
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError(f"ticker must be a non-blank string, got {ticker!r}")
        items = list(prints)
        for i, item in enumerate(items):
            p = _require(f"prints[{i}]", item, DarkPoolPrint)
            if p.ticker != ticker:
                raise ValueError(f"prints[{i}] is a print for {p.ticker!r}, not {ticker!r}")
        ring = self._dark_pool.get(ticker)
        if ring is None:
            ring = self._dark_pool[ticker] = RingIndex(self._capacity)
        for p in items:
            ring.append(p, observation_time(p), received_ns)

    def view(self, t: Instant) -> LiveMarketView:
        """The MarketView at Decision_Time ``t``."""
        return LiveMarketView(self, t)


class LiveMarketView(_IndexedView):
    """A :class:`~fse.pit.protocols.MarketView` over :class:`LiveInputs` at ``t``."""

    __slots__ = ()

    def __init__(self, inputs: LiveInputs, t: Instant) -> None:
        super().__init__(inputs, t)
