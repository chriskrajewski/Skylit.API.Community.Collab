"""The point-in-time interface the engine sees (design §5 "Point-in-time layer").

``MarketView`` is the only way the Strategy_Engine reads data. A view is fixed
at one Decision_Time ``t`` and returns only inputs whose ``available_at`` is at
or before ``t`` (Req 5.3, D8). The Backtester builds historical views
(``fse.pit.market_view``); the Live_Runner builds views over ring buffers.

This module is part of the engine's allowed imports, so it depends only on
``fse.engine.types``, ``fse.timekit`` and the standard library.

- :class:`MapState`: per configured (symbol, metric), the Snapshot with the
  latest returned ``asOf`` at or before ``t``, or an ``Unavailable`` marker
  (Req 5.1-5.2).
- Snapshot_Age: ``t`` minus the oldest ``asOf`` in Map_State
  (:meth:`MapState.snapshot_age_ns`).
- Futures_Price: the close of the latest 1-minute bar of an instrument that
  closed at or before ``t`` (:func:`futures_price`).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from fse.engine.types import (
    Bar,
    DarkPoolPrint,
    EconomicEvent,
    Metric,
    Snapshot,
    Ticks,
    Unavailable,
    VixState,
)
from fse.timekit import Instant

__all__ = [
    "FUTURES_PRICE_INTERVAL_S",
    "MapState",
    "MarketView",
    "SymMetric",
    "futures_price",
    "missing_snapshot",
]

type SymMetric = tuple[str, Metric]
"""A Map_State key: (symbol, metric)."""

FUTURES_PRICE_INTERVAL_S: Final = 60
"""Futures_Price and Level_Converter pairing read 1-minute bars (Glossary, Req 8.4)."""


def missing_snapshot(symbol: str, metric: Metric) -> Unavailable:
    """The Map_State marker for a configured (symbol, metric) with no Snapshot yet (Req 5.2)."""
    return Unavailable(f"no {symbol} {metric} Snapshot with asOf at or before the Decision_Time")


@dataclass(frozen=True, slots=True)
class MapState:
    """Map_State at ``t``: one entry per configured (symbol, metric), in configured order.

    Each entry is the Snapshot with the latest returned ``asOf`` at or before
    ``t``, or an :class:`Unavailable` marker. Construction raises ``ValueError``
    if a Snapshot is filed under another (symbol, metric) or has an ``asOf``
    after ``t``, so a Map_State can never hold a later Snapshot.
    """

    t: Instant
    entries: Mapping[SymMetric, Snapshot | Unavailable]
    _snapshots: tuple[Snapshot, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        frozen = MappingProxyType(dict(self.entries))
        snapshots: list[Snapshot] = []
        for (symbol, metric), entry in frozen.items():
            if isinstance(entry, Snapshot):
                if (entry.symbol, entry.metric) != (symbol, metric):
                    raise ValueError(
                        f"Map_State entry {symbol} {metric} holds a {entry.symbol} "
                        f"{entry.metric} Snapshot"
                    )
                if entry.as_of_ns > self.t:
                    raise ValueError(
                        f"Map_State entry {symbol} {metric} has asOf {entry.as_of_ns}, "
                        f"after t {self.t}"
                    )
                snapshots.append(entry)
            elif not isinstance(entry, Unavailable):
                raise ValueError(
                    f"Map_State entry {symbol} {metric} must be a Snapshot or Unavailable"
                )
        object.__setattr__(self, "entries", frozen)
        object.__setattr__(self, "_snapshots", tuple(snapshots))

    def __contains__(self, key: object) -> bool:
        return key in self.entries

    def __iter__(self) -> Iterator[SymMetric]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, symbol: str, metric: Metric) -> Snapshot | Unavailable:
        """The entry for (symbol, metric); ``Unavailable`` when it is not configured."""
        entry = self.entries.get((symbol, metric))
        if entry is None:
            return Unavailable(f"{symbol} {metric} is not a configured symbol and metric")
        return entry

    def snapshots(self) -> tuple[Snapshot, ...]:
        """The Snapshots present, in configured order."""
        return self._snapshots

    def missing(self) -> tuple[SymMetric, ...]:
        """The configured (symbol, metric) pairs marked unavailable, in configured order."""
        return tuple(k for k, v in self.entries.items() if isinstance(v, Unavailable))

    def snapshot_age_ns(self) -> int | Unavailable:
        """Snapshot_Age: ``t`` minus the oldest ``asOf`` among the Snapshots present."""
        if not self._snapshots:
            return Unavailable("Map_State holds no Snapshot, so Snapshot_Age is undefined")
        return self.t - min(s.as_of_ns for s in self._snapshots)


@runtime_checkable
class MarketView(Protocol):
    """Every input available at the Decision_Time ``t``, and nothing later.

    Each accessor returns only records with ``available_at <= t``. "Latest"
    means the largest Observation_Time among those records.
    """

    @property
    def t(self) -> Instant: ...

    def map_state(self) -> MapState:
        """Latest returned ``asOf`` at or before ``t`` per configured (symbol, metric)."""
        ...

    def snapshot_at_or_before(self, symbol: str, metric: Metric, at: Instant) -> Snapshot | None:
        """The Snapshot of the configured view with the latest ``asOf <= at``."""
        ...

    def snapshots_between(
        self, symbol: str, metric: Metric, lo: Instant, hi: Instant
    ) -> Iterator[Snapshot]:
        """Snapshots with ``lo <= asOf <= hi``, in ``asOf`` order."""
        ...

    def bars(self, instrument: str, interval_s: int, since: Instant) -> Sequence[Bar]:
        """Bars that open at or after ``since`` and closed at or before ``t``, in time order."""
        ...

    def last_bar_closed_at_or_before(self, instrument: str, at: Instant) -> Bar | None:
        """The latest 1-minute bar of ``instrument`` with ``close_ns <= at``."""
        ...

    def vix(self) -> VixState:
        """VIX daily open, prior close and latest 1-minute close, each possibly unavailable."""
        ...

    def dark_pool(self, ticker: str, since: Instant) -> Sequence[DarkPoolPrint] | Unavailable:
        """Prints with ``since <= ts_ns``; ``Unavailable`` when the ticker was not fetched."""
        ...

    def events(self) -> Sequence[EconomicEvent]:
        """Calendar events; the schedule is known in advance, so all are visible."""
        ...


def futures_price(view: MarketView, instrument: str) -> Ticks | Unavailable:
    """Futures_Price: the tick close of the latest 1-minute bar with ``close_ns <= t``."""
    bar = view.last_bar_closed_at_or_before(instrument, view.t)
    if bar is None:
        return Unavailable(f"no {instrument} 1-minute bar closed at or before the Decision_Time")
    if bar.c_t is None:
        return Unavailable(f"{instrument} bar closing at {bar.close_ns} has no tick prices")
    return bar.c_t
