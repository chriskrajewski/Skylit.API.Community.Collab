"""The live bar feed: ProjectX closed 1-minute bars (design §23 "Bar feed", Req 23.6).

:class:`BarFeed` polls ``POST /api/History/retrieveBars`` through
:class:`~fse.projectx.bars.ProjectXBars` (paced under the documented 50
requests per 30 s, retried, with token renewal) for each configured
instrument, every ``poll_interval_s`` (default 5 s).

Each poll asks, per instrument, for the bars from its cursor to the last whole
minute at or before ``clock.now()``, so only closed bars are requested. The
cursor starts at ``start_ns`` (the trading-day start, so the chart sees the
overnight bars) and moves to the close of the last bar delivered. Bars are
delivered to ``on_bars`` oldest first, with the receipt time of the response,
and never twice; a minute without a bar is not synthesized (Req 4.8). A
request window that failed is logged; its minutes are asked for again at the
next poll. A ProjectX 401 after renewal, 402 or 403
(:class:`~fse.projectx.session.ProjectXAuthError`) propagates.

Read-only: the feed calls only the bar history endpoint.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from fse.clock import Clock
from fse.engine.types import Bar
from fse.live._wait import FeedLog, next_tick, no_log
from fse.projectx.models import BarHistory
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "BAR_INTERVAL_S",
    "DEFAULT_POLL_INTERVAL_S",
    "BarFeed",
    "BarRetriever",
    "FeedInstrument",
    "OnBars",
]

BAR_INTERVAL_S: Final = 60
DEFAULT_POLL_INTERVAL_S: Final = 5

type OnBars = Callable[[Sequence[Bar], Instant], None]
"""Receives one instrument's new closed bars, oldest first, and their receipt time."""


class BarRetriever(Protocol):
    """The part of :class:`~fse.projectx.bars.ProjectXBars` the feed uses."""

    async def retrieve(
        self,
        *,
        instrument: str,
        contract: str,
        contract_id: str,
        start_ns: Instant,
        end_ns: Instant,
        interval_s: int,
    ) -> BarHistory: ...


@dataclass(frozen=True, slots=True)
class FeedInstrument:
    """One traded instrument: root (``MES``), contract (``MESZ6``) and ProjectX contract id."""

    instrument: str
    contract: str
    contract_id: str

    def __post_init__(self) -> None:
        for name in ("instrument", "contract", "contract_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"FeedInstrument.{name} must be a non-blank string")


class BarFeed:
    """Delivers each instrument's closed 1-minute bars once, in order (see the module notes)."""

    __slots__ = ("_bars", "_clock", "_cursor", "_instruments", "_interval_ns", "_log", "_on_bars")

    def __init__(
        self,
        bars: BarRetriever,
        clock: Clock,
        *,
        instruments: Iterable[FeedInstrument],
        start_ns: Instant,
        on_bars: OnBars,
        log: FeedLog = no_log,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        if isinstance(poll_interval_s, bool) or poll_interval_s < 1:
            raise ValueError(f"poll_interval_s must be at least 1, got {poll_interval_s!r}")
        grid = BAR_INTERVAL_S * NS_PER_SECOND
        if start_ns % grid:
            raise ValueError("start_ns must lie on the 1-minute bar grid")
        self._bars = bars
        self._clock = clock
        self._instruments = tuple(instruments)
        names = [i.instrument for i in self._instruments]
        if not names or len(set(names)) != len(names):
            raise ValueError(f"instruments must name each instrument once: {names!r}")
        self._cursor: dict[str, Instant] = dict.fromkeys(names, start_ns)
        self._on_bars = on_bars
        self._log = log
        self._interval_ns = poll_interval_s * NS_PER_SECOND

    def cursor(self, instrument: str) -> Instant:
        """The close of the last bar delivered for ``instrument`` (or the start)."""
        return self._cursor[instrument]

    async def poll_once(self) -> None:
        """Fetch and deliver every instrument's bars that closed since its cursor."""
        grid = BAR_INTERVAL_S * NS_PER_SECOND
        for item in self._instruments:
            end = self._clock.now() // grid * grid
            start = self._cursor[item.instrument]
            if end <= start:
                continue
            history = await self._bars.retrieve(
                instrument=item.instrument,
                contract=item.contract,
                contract_id=item.contract_id,
                start_ns=start,
                end_ns=end,
                interval_s=BAR_INTERVAL_S,
            )
            received = self._clock.now()
            for failure in history.failures:
                self._log(
                    {
                        "event": "bar_fetch_failed",
                        "instrument": item.instrument,
                        "window_start_ns": failure.window_start_ns,
                        "window_end_ns": failure.window_end_ns,
                        "cause": failure.cause,
                    }
                )
            fresh = sorted(
                (b for b in history.bars if b.open_ns >= start and b.close_ns <= end),
                key=lambda b: b.open_ns,
            )
            unique: list[Bar] = []
            for bar in fresh:
                if not unique or bar.open_ns > unique[-1].open_ns:
                    unique.append(bar)
            if unique:
                self._cursor[item.instrument] = unique[-1].close_ns
                self._on_bars(tuple(unique), received)

    async def run(self, end_ns: Instant) -> None:
        """Poll every ``poll_interval_s`` until ``end_ns`` (the Flat_Deadline bar's close)."""
        start = self._clock.now()
        tick = start
        while tick < end_ns:
            now = self._clock.now()
            if now < tick:
                await self._clock.sleep(tick - now)
            await self.poll_once()
            tick = next_tick(start, self._interval_ns, tick, self._clock.now())
