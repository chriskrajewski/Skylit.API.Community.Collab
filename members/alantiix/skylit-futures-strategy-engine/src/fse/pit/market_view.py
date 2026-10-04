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
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
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
from fse.pit.asof import AsOfIndex, Received, observation_time
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
    "heatmap_view",
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
        if not isinstance(view_id, str) or not view_id:
            raise ValueError(f"view_id must be a non-blank string, got {view_id!r}")
        self._view_id = view_id
        self._keys: tuple[SymMetric, ...] = tuple((s, m) for s in syms for m in mets)

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

        for label, record in (("vix_today", vix_today), ("vix_prior", vix_prior)):
            if record is not None:
                _require(label, record, VixDailyRecord)
        if (
            vix_today is not None
            and vix_prior is not None
            and not vix_prior.session < vix_today.session
        ):
            raise ValueError(
                f"vix_prior is for {vix_prior.session}, not before vix_today's {vix_today.session}"
            )
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


class HistoricalMarketView:
    """A :class:`~fse.pit.protocols.MarketView` over :class:`HistoricalInputs` at ``t``."""

    __slots__ = ("_in", "_map_state", "_t")

    def __init__(self, inputs: HistoricalInputs, t: Instant) -> None:
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
