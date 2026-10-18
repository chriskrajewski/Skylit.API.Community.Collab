"""Market data sources of the data plane (design 4.2): polling and streaming.

Both implementations deliver **the same bars**: closed 1-minute bars from
ProjectX ``retrieveBars`` (:class:`~fse.live.bar_feed.BarFeed`), each once,
oldest first per instrument, never synthesized, with ``received_ns`` taken
after the fetch returns. Only the trigger and the quote stream differ.
Callbacks are fixed at construction: ``on_bars(bars, received_ns)``,
``on_quote(quote)``, ``on_status(feed, up, detail)``.

- :class:`PollingMarketData`: every ``poll_interval_s`` (default 5) it polls
  each instrument, on the grid ``phase_ns + k * poll_interval_s`` where
  ``phase_ns`` is the instant :meth:`~PollingMarketData.run` starts (the
  host start on the first day, ``open_ns`` on later days), the rule
  ``BarFeed.run`` uses. For each urgent ``(instrument, close_ns)``
  (:meth:`~PollingMarketData.set_urgent`) it polls only that instrument every
  ``hot_poll_interval_s`` (default 1) from ``close_ns`` until the bar arrives
  or ``close_ns + 20 s``. Hot polls are extra requests: they never shift the
  normal grid. With MNQ hot and MES normal that is 30 + 6 = 36 requests per
  30 s, under the shared limiter's 45.
- :class:`StreamingMarketData` (``transport: stream`` or ``auto``): a
  :class:`~fse.projectx.realtime.MarketHubClient` subscribes quotes and trades.
  While the hub is connected, the first trade at or after a minute boundary
  ``m`` (or ``m + 1.5 s`` without one) fetches that instrument; if the bar
  closing at ``m`` is not in the response it retries every 1 s up to
  ``m + 10 s``. The polling grid keeps running underneath as the safety poll,
  so a hub drop loses no bar: the status goes ``quotes: down``, the transport
  becomes ``polling`` (hot polls resume) and the hub reconnects with backoff.
  Every switch is reported through ``on_status("quotes", up, detail)``.

Every delivered bar's receipt lag (``received_ns - close_ns``) is tracked per
instrument; :attr:`status` reports p50/p95/max for the heartbeat.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from fse.clock import Clock
from fse.engine.types import Bar
from fse.live._wait import FeedLog, next_tick, no_log, within
from fse.live.bar_feed import BarFeed, BarRetriever, FeedInstrument
from fse.live.host.dayclock import GlobexDay
from fse.live.plugins.api import FeedName, Quote
from fse.logio.canonical_json import JsonValue
from fse.projectx.realtime import HubQuote, HubTrade
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant

__all__ = [
    "HOT_POLL_WINDOW_NS",
    "STREAM_RETRY_UNTIL_NS",
    "STREAM_TRIGGER_DELAY_NS",
    "HubClient",
    "HubFactory",
    "LagStats",
    "MarketDataSource",
    "OnQuote",
    "OnSourceBars",
    "OnSourceStatus",
    "OnTrade",
    "PollingMarketData",
    "SourceStatus",
    "StreamingMarketData",
    "Transport",
]

HOT_POLL_WINDOW_NS: Final = 20 * NS_PER_SECOND
STREAM_TRIGGER_DELAY_NS: Final = 1_500_000_000
STREAM_RETRY_NS: Final = NS_PER_SECOND
STREAM_RETRY_UNTIL_NS: Final = 10 * NS_PER_SECOND

type Transport = Literal["polling", "stream"]
type OnSourceBars = Callable[[Sequence[Bar], Instant], None]
type OnQuote = Callable[[Quote], None]
type OnSourceStatus = Callable[[FeedName, bool, str], None]
type OnTrade = Callable[[str, HubTrade, Instant], None]
"""``(instrument, trade, received_ns)``: every validated trade print (recording only)."""


def _percentile(sorted_values: Sequence[int], q: float) -> int:
    rank = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[min(rank, len(sorted_values) - 1)]


class LagStats:
    """Receipt lags per instrument since the last :meth:`reset` (heartbeat window)."""

    __slots__ = ("_lags",)

    def __init__(self) -> None:
        self._lags: dict[str, list[int]] = {}

    def add(self, instrument: str, lag_ns: int) -> None:
        self._lags.setdefault(instrument, []).append(lag_ns)

    def summary(self) -> dict[str, JsonValue]:
        """``{instrument: {n, p50_ms, p95_ms, max_ms}}``."""
        out: dict[str, JsonValue] = {}
        for instrument in sorted(self._lags):
            values = sorted(self._lags[instrument])
            out[instrument] = {
                "n": len(values),
                "p50_ms": _percentile(values, 0.50) // 1_000_000,
                "p95_ms": _percentile(values, 0.95) // 1_000_000,
                "max_ms": values[-1] // 1_000_000,
            }
        return out

    def reset(self) -> None:
        self._lags.clear()


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """The source's state for the heartbeat."""

    transport: Transport
    connected: bool
    """The market hub is connected (always ``False`` for polling)."""
    subscription: str
    """``live`` or ``sim`` (the ``retrieveBars`` data subscription in use)."""
    lag: Mapping[str, JsonValue] = field(default_factory=dict)
    rt_dropped: int = 0


class MarketDataSource(Protocol):
    """One Globex day of closed bars (and quotes, when streaming)."""

    async def run(
        self,
        day: GlobexDay,
        instruments: tuple[FeedInstrument, ...],
        start_ns: Instant,
        until_ns: Instant,
        *,
        seed: Mapping[str, Instant] | None = None,
    ) -> None:
        """Deliver the day's bars from ``start_ns`` until ``until_ns``.

        ``seed`` moves an instrument's cursor to the close of its newest
        already-recorded bar (restart).
        """
        ...

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None: ...

    @property
    def status(self) -> SourceStatus: ...

    def take_lag(self) -> dict[str, JsonValue]:
        """The lag summary since the last call, then reset (heartbeat)."""
        ...


class PollingMarketData:
    """REST polling with hot polls for urgent closes (see the module notes)."""

    def __init__(
        self,
        bars: BarRetriever,
        clock: Clock,
        *,
        on_bars: OnSourceBars,
        on_status: OnSourceStatus,
        log: FeedLog = no_log,
        poll_interval_s: int = 5,
        hot_poll_interval_s: int = 1,
        subscription: str = "sim",
        hot_enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        if not 1 <= hot_poll_interval_s < poll_interval_s:
            raise ValueError("hot_poll_interval_s must be at least 1 and below poll_interval_s")
        self._bars = bars
        self._clock = clock
        self._on_bars = on_bars
        self._on_status = on_status
        self._log = log
        self._interval_ns = poll_interval_s * NS_PER_SECOND
        self._hot_ns = hot_poll_interval_s * NS_PER_SECOND
        self._subscription = subscription
        self._hot_enabled = hot_enabled
        self._feed: BarFeed | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._urgent: tuple[tuple[str, Instant], ...] = ()
        self._urgent_done: set[tuple[str, Instant]] = set()
        self._changed = asyncio.Event()
        self._lag = LagStats()

    # ------------------------------------------------------------ public

    @property
    def status(self) -> SourceStatus:
        return SourceStatus("polling", False, self._subscription, self._lag.summary(), 0)

    def take_lag(self) -> dict[str, JsonValue]:
        out = self._lag.summary()
        self._lag.reset()
        return out

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None:
        self._urgent = tuple(sorted(set(closes), key=lambda x: (x[1], x[0])))
        self._changed.set()

    def cursor(self, instrument: str) -> Instant | None:
        feed = self._feed
        if feed is None or instrument not in {i.instrument for i in feed.instruments}:
            return None
        return feed.cursor(instrument)

    async def poll(self, instrument: str) -> tuple[Bar, ...]:
        """Fetch ``instrument`` now (serialized with every other poll of it)."""
        feed = self._feed
        if feed is None:
            return ()
        async with self._locks[instrument]:
            return await feed.poll_instrument(instrument)

    async def run(
        self,
        day: GlobexDay,
        instruments: tuple[FeedInstrument, ...],
        start_ns: Instant,
        until_ns: Instant,
        *,
        seed: Mapping[str, Instant] | None = None,
    ) -> None:
        feed = BarFeed(
            self._bars,
            self._clock,
            instruments=instruments,
            start_ns=start_ns,
            on_bars=self._deliver,
            log=self._log,
            poll_interval_s=self._interval_ns // NS_PER_SECOND,
        )
        for instrument, close_ns in (seed or {}).items():
            if instrument in {i.instrument for i in instruments}:
                feed.advance_cursor(instrument, close_ns)
        self._feed = feed
        self._locks = {i.instrument: asyncio.Lock() for i in instruments}
        self._urgent_done = set()
        hot = asyncio.ensure_future(self._hot(until_ns))
        try:
            await self._normal(self._clock.now(), until_ns, instruments)
            now = self._clock.now()
            if now < until_ns:  # the last grid tick is before until_ns: hot polls may still run
                await self._clock.sleep(until_ns - now)
        finally:
            hot.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hot
            self._feed = None

    # ------------------------------------------------------------ internals

    def _deliver(self, bars: Sequence[Bar], received_ns: Instant) -> None:
        for bar in bars:
            self._lag.add(bar.instrument, received_ns - bar.close_ns)
        self._on_bars(bars, received_ns)

    async def _normal(
        self, phase_ns: Instant, until_ns: Instant, instruments: Iterable[FeedInstrument]
    ) -> None:
        items = tuple(instruments)
        tick = phase_ns
        while tick < until_ns:
            now = self._clock.now()
            if now < tick:
                await self._clock.sleep(tick - now)
            for item in items:
                await self.poll(item.instrument)
            tick = next_tick(phase_ns, self._interval_ns, tick, self._clock.now())

    def _pending(self, now: Instant, until_ns: Instant) -> list[tuple[str, Instant]]:
        out: list[tuple[str, Instant]] = []
        for instrument, close_ns in self._urgent:
            if (instrument, close_ns) in self._urgent_done or close_ns > until_ns:
                continue
            cursor = self.cursor(instrument)
            if cursor is None or cursor >= close_ns or now > close_ns + HOT_POLL_WINDOW_NS:
                continue
            out.append((instrument, close_ns))
        return out

    async def _hot(self, until_ns: Instant) -> None:
        while True:
            self._changed.clear()
            now = self._clock.now()
            pending = self._pending(now, until_ns)
            if not pending:
                await self._changed.wait()
                continue
            close_ns = min(c for _, c in pending)
            if now < close_ns:
                await within(self._clock, self._changed.wait(), close_ns - now)
                continue
            due = [i for i, c in pending if c == close_ns]
            await self._hot_poll(due, close_ns)
            self._urgent_done.update((i, close_ns) for i in due)

    async def _hot_poll(self, instruments: Sequence[str], close_ns: Instant) -> None:
        waiting = list(instruments)
        while waiting and self._clock.now() <= close_ns + HOT_POLL_WINDOW_NS:
            if self._hot_enabled():
                for instrument in waiting:
                    await self.poll(instrument)
            waiting = [i for i in waiting if (self.cursor(i) or 0) < close_ns]
            if waiting:
                await self._clock.sleep(self._hot_ns)


class HubClient(Protocol):
    """The part of :class:`~fse.projectx.realtime.MarketHubClient` the source uses."""

    async def run(self) -> None: ...

    @property
    def dropped(self) -> int: ...


type HubFactory = Callable[
    [
        tuple[str, ...],
        Callable[[HubQuote, Instant], None],
        Callable[[HubTrade, Instant], None],
        Callable[[bool, str], None],
    ],
    HubClient,
]
"""``(contract_ids, on_quote, on_trade, on_state) -> client`` for one Globex day."""


class StreamingMarketData:
    """Hub-triggered bar fetches over the polling grid (see the module notes)."""

    def __init__(
        self,
        bars: BarRetriever,
        clock: Clock,
        *,
        hub: HubFactory,
        on_bars: OnSourceBars,
        on_quote: OnQuote,
        on_status: OnSourceStatus,
        on_trade: OnTrade | None = None,
        log: FeedLog = no_log,
        poll_interval_s: int = 5,
        hot_poll_interval_s: int = 1,
        subscription: str = "sim",
        transport: Literal["stream", "auto"] = "auto",
    ) -> None:
        self._clock = clock
        self._hub_factory = hub
        self._on_quote = on_quote
        self._on_status = on_status
        self._on_trade = on_trade
        self._log = log
        self._transport = transport
        self._hub_up = False
        self._hub: HubClient | None = None
        self._dropped_before = 0
        self._by_contract: dict[str, str] = {}
        self._triggered: set[tuple[str, Instant]] = set()
        self._fetches: set[asyncio.Task[None]] = set()
        self._until_ns: Instant = 0
        self._poller = PollingMarketData(
            bars,
            clock,
            on_bars=on_bars,
            on_status=on_status,
            log=log,
            poll_interval_s=poll_interval_s,
            hot_poll_interval_s=hot_poll_interval_s,
            subscription=subscription,
            hot_enabled=lambda: not self._hub_up,
        )

    @property
    def status(self) -> SourceStatus:
        inner = self._poller.status
        dropped = self._dropped_before + (0 if self._hub is None else self._hub.dropped)
        return SourceStatus(
            "stream" if self._hub_up else "polling",
            self._hub_up,
            inner.subscription,
            inner.lag,
            dropped,
        )

    def take_lag(self) -> dict[str, JsonValue]:
        return self._poller.take_lag()

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None:
        self._poller.set_urgent(closes)

    async def run(
        self,
        day: GlobexDay,
        instruments: tuple[FeedInstrument, ...],
        start_ns: Instant,
        until_ns: Instant,
        *,
        seed: Mapping[str, Instant] | None = None,
    ) -> None:
        self._by_contract = {i.contract_id: i.instrument for i in instruments}
        self._triggered = set()
        self._until_ns = until_ns
        hub = self._hub_factory(tuple(self._by_contract), self._quote, self._trade, self._hub_state)
        self._hub = hub
        poller = asyncio.ensure_future(
            self._poller.run(day, instruments, start_ns, until_ns, seed=seed)
        )
        helpers = [
            asyncio.ensure_future(self._hub_run(hub)),
            asyncio.ensure_future(self._minutes(start_ns, until_ns)),
        ]
        try:
            await poller
        finally:
            for task in (poller, *helpers, *self._fetches):
                task.cancel()
            for task in (poller, *helpers, *list(self._fetches)):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._fetches.clear()
            self._dropped_before += hub.dropped
            self._hub = None
            if self._hub_up:
                self._hub_state(False, "day ended")

    # ------------------------------------------------------------ hub callbacks

    def _hub_state(self, up: bool, detail: str) -> None:
        changed = up != self._hub_up
        self._hub_up = up
        if changed or not up:
            transport = "stream" if up else "polling"
            self._on_status("quotes", up, f"transport {transport}: {detail}")

    def _quote(self, quote: HubQuote, received_ns: Instant) -> None:
        instrument = self._by_contract.get(quote.contract_id)
        if instrument is None:
            return
        self._on_quote(
            Quote(
                instrument=instrument,
                contract_id=quote.contract_id,
                t_ns=quote.t_ns,
                received_ns=received_ns,
                bid_t=quote.bid_t,
                ask_t=quote.ask_t,
                last_t=quote.last_t,
            )
        )

    def _trade(self, trade: HubTrade, received_ns: Instant) -> None:
        instrument = self._by_contract.get(trade.contract_id)
        if instrument is None:
            return
        if self._on_trade is not None:
            self._on_trade(instrument, trade, received_ns)
        minute = trade.t_ns // NS_PER_MINUTE * NS_PER_MINUTE
        self._trigger(instrument, minute)

    # ------------------------------------------------------------ triggers

    async def _hub_run(self, hub: HubClient) -> None:
        try:
            await hub.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log({"event": "market_hub_failed", "level": "error", "cause": type(exc).__name__})
            self._hub_state(False, f"hub client failed ({type(exc).__name__})")

    def _trigger(self, instrument: str, minute: Instant) -> None:
        key = (instrument, minute)
        cursor = self._poller.cursor(instrument)
        if (
            not self._hub_up
            or key in self._triggered
            or cursor is None
            or cursor >= minute
            or minute > self._until_ns
        ):
            return
        self._triggered.add(key)
        task = asyncio.ensure_future(self._fetch_closing(instrument, minute))
        self._fetches.add(task)
        task.add_done_callback(self._fetches.discard)

    async def _fetch_closing(self, instrument: str, minute: Instant) -> None:
        """Fetch until the bar closing at ``minute`` arrives, at most up to ``minute + 10 s``."""
        now = self._clock.now()
        if now < minute:
            await self._clock.sleep(minute - now)
        while True:
            await self._poller.poll(instrument)
            cursor = self._poller.cursor(instrument)
            if cursor is None or cursor >= minute:
                return
            if self._clock.now() + STREAM_RETRY_NS > minute + STREAM_RETRY_UNTIL_NS:
                return  # the safety poll takes over
            await self._clock.sleep(STREAM_RETRY_NS)

    async def _minutes(self, start_ns: Instant, until_ns: Instant) -> None:
        """At ``m + 1.5 s`` trigger every instrument whose bar closing at ``m`` has no trade."""
        minute = (self._clock.now() // NS_PER_MINUTE + 1) * NS_PER_MINUTE
        minute = max(minute, start_ns + NS_PER_MINUTE)
        while minute <= until_ns:
            wake = minute + STREAM_TRIGGER_DELAY_NS
            now = self._clock.now()
            if now < wake:
                await self._clock.sleep(wake - now)
            for instrument in self._by_contract.values():
                self._trigger(instrument, minute)
            minute += NS_PER_MINUTE
