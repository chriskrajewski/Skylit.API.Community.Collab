"""The live map feed: Map_State refreshes for the Live_Runner (design §23, Req 23.2-23.4, 23.14).

:class:`MapFeed` delivers every Snapshot it receives to ``on_snapshots`` with
its receipt time (``clock.now()`` when the response or event arrived). The
Live_Runner appends them to :class:`~fse.pit.market_view.LiveInputs` (so each
is available at ``max(asOf, receipt_time)``) and to the recorder.

**Polling** (``live.mode: polling``, Req 23.2, 23.14). At each tick
``start + k * refresh_interval_s`` inside the run window, two multi-symbol
``GET /v1/heatmap`` requests are sent together, one per metric (``gamma`` and
``vanna``), each for every configured symbol. A request that fails or has no
response within 5 s (:data:`REFRESH_TIMEOUT_S`) is logged and abandoned:
nothing is delivered for that metric, so the prior Map_State stays in place,
and the next request goes at the next scheduled tick (a tick that passed
while a refresh was running is not made up).

**Stream** (``live.mode: stream``, Req 23.3-23.4). Two ``GET /v1/stream`` v2
connections, one per metric, each for every configured symbol; every
``snapshot`` event is delivered and no polling request is sent. A connection
is reopened with the id of the last event received (the ``Last-Event-ID``
header) when it closes (cleanly or by a transport error while the body is
read), fails to open, sends a ``reconnect`` or ``closed``
event, or sends no event for 60 s (:data:`STREAM_SILENCE_S`); comment lines do
not count as events. A reconnect is attempted at most once per 5 s
(:data:`RECONNECT_MIN_GAP_S`), until the stream resumes or the run window
closes. ``velocity`` and ``credits`` events are not used: the engine computes
its own Node_Velocity (Req 5.9).

Every failure, timeout and reconnect goes to ``log`` as a JSON object with an
``event`` name; no entry holds a header or the key. A 401, 402 or 403
(:class:`~fse.skylit.client.SkylitStoppedError`) and a blank key propagate.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Final, Protocol

from fse.clock import Clock
from fse.engine.types import Metric, Snapshot
from fse.live._wait import TIMED_OUT, FeedLog, next_tick, no_log, within
from fse.logio.canonical_json import JsonValue
from fse.pit.market_view import MAP_METRICS
from fse.skylit.client import Failed, SkylitStreamError
from fse.skylit.endpoints import ViewParams, stream_params
from fse.skylit.models import (
    HeatmapResponse,
    MalformedResponseError,
    StreamClosed,
    StreamConnected,
    StreamReconnect,
    StreamResumed,
    StreamSnapshot,
    StreamSymbolUnavailable,
    parse_stream_event,
)
from fse.skylit.sse import SseMessage
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "RECONNECT_MIN_GAP_S",
    "REFRESH_TIMEOUT_S",
    "STREAM_SILENCE_S",
    "MapClient",
    "MapFeed",
    "OnSnapshots",
]

REFRESH_TIMEOUT_S: Final = 5
"""No response within this many seconds counts as a failed refresh (Req 23.14)."""
STREAM_SILENCE_S: Final = 60
"""No stream event for this long reopens the connection (Req 23.4)."""
RECONNECT_MIN_GAP_S: Final = 5
"""At most one connection attempt per metric in this many seconds (Req 23.4)."""

type OnSnapshots = Callable[[Sequence[Snapshot], Instant], None]
"""Receives the Snapshots of one response or event and their receipt time."""


class MapClient(Protocol):
    """The part of :class:`~fse.skylit.client.SkylitClient` the map feed uses."""

    async def heatmap(
        self, symbols: Iterable[str], *, metric: str, view: ViewParams
    ) -> HeatmapResponse | Failed: ...

    def stream(
        self, params: Mapping[str, str], *, last_event_id: str | None = None
    ) -> AbstractAsyncContextManager[AsyncIterator[SseMessage]]: ...


async def _next(it: AsyncIterator[SseMessage]) -> SseMessage | None:
    """The next message, or ``None`` when the connection ended."""
    try:
        return await anext(it)
    except StopAsyncIteration:
        return None


class MapFeed:
    """Refreshes Map_State by polling or streaming (see the module notes)."""

    __slots__ = (
        "_client",
        "_clock",
        "_interval_ns",
        "_log",
        "_metrics",
        "_mode",
        "_on_snapshots",
        "_symbols",
        "_view",
        "_view_id",
    )

    def __init__(
        self,
        client: MapClient,
        clock: Clock,
        *,
        symbols: Iterable[str],
        view: ViewParams,
        view_id: str,
        mode: str,
        refresh_interval_s: int,
        on_snapshots: OnSnapshots,
        log: FeedLog = no_log,
        metrics: Iterable[Metric] = MAP_METRICS,
    ) -> None:
        if mode not in ("polling", "stream"):
            raise ValueError(f"mode must be polling or stream, got {mode!r}")
        if isinstance(refresh_interval_s, bool) or refresh_interval_s < REFRESH_TIMEOUT_S:
            raise ValueError(
                f"live.refresh_interval_s must be at least {REFRESH_TIMEOUT_S} s, "
                f"got {refresh_interval_s!r}"
            )
        self._client = client
        self._clock = clock
        self._symbols = tuple(symbols)
        if not self._symbols:
            raise ValueError("the map feed needs at least one symbol")
        self._view = view
        self._view_id = view_id
        self._mode = mode
        self._interval_ns = refresh_interval_s * NS_PER_SECOND
        self._on_snapshots = on_snapshots
        self._log = log
        self._metrics: tuple[Metric, ...] = tuple(metrics)

    @property
    def mode(self) -> str:
        return self._mode

    async def run(self, start_ns: Instant, end_ns: Instant) -> None:
        """Refresh from ``start_ns`` (inclusive) until ``end_ns`` (exclusive): the run window."""
        now = self._clock.now()
        if now < start_ns:
            await self._clock.sleep(start_ns - now)
        if self._mode == "polling":
            await self._poll(start_ns, end_ns)
        else:
            await asyncio.gather(*(self._stream(m, end_ns) for m in self._metrics))

    # ------------------------------------------------------------ polling

    async def refresh_once(self) -> None:
        """One polling refresh: one multi-symbol request per metric, sent together."""
        await asyncio.gather(*(self._refresh(m) for m in self._metrics))

    async def _poll(self, start_ns: Instant, end_ns: Instant) -> None:
        tick = next_tick(start_ns, self._interval_ns, start_ns - 1, self._clock.now())
        while tick < end_ns:
            now = self._clock.now()
            if now < tick:
                await self._clock.sleep(tick - now)
            await self.refresh_once()
            tick = next_tick(start_ns, self._interval_ns, tick, self._clock.now())

    async def _refresh(self, metric: Metric) -> None:
        sent = self._clock.now()
        result = await within(
            self._clock,
            self._client.heatmap(self._symbols, metric=metric, view=self._view),
            REFRESH_TIMEOUT_S * NS_PER_SECOND,
        )
        received = self._clock.now()
        if result is TIMED_OUT:
            self._failed(metric, sent, f"no response within {REFRESH_TIMEOUT_S} s")
            return
        if isinstance(result, Failed):
            self._failed(metric, sent, result.cause)
            return
        assert isinstance(result, HeatmapResponse)
        snapshots = result.snapshots(view_id=self._view_id, source_endpoint="heatmap")
        self._on_snapshots(snapshots, received)

    def _failed(self, metric: Metric, sent: Instant, cause: str) -> None:
        self._log(
            {
                "event": "map_refresh_failed",
                "metric": metric,
                "sent_ns": sent,
                "cause": cause,
                "kept": "prior Map_State",
            }
        )

    # ------------------------------------------------------------ stream

    async def _stream(self, metric: Metric, end_ns: Instant) -> None:
        last_id: str | None = None
        last_attempt: Instant | None = None
        gap = RECONNECT_MIN_GAP_S * NS_PER_SECOND
        while self._clock.now() < end_ns:
            if last_attempt is not None:
                wait = last_attempt + gap - self._clock.now()
                if wait > 0:
                    await self._clock.sleep(wait)
                if self._clock.now() >= end_ns:
                    break
            last_attempt = self._clock.now()
            reason, last_id = await self._read(metric, last_id, end_ns)
            if self._clock.now() < end_ns:
                self._log(
                    {
                        "event": "stream_reconnect",
                        "metric": metric,
                        "reason": reason,
                        "last_event_id": last_id,
                    }
                )

    async def _read(
        self, metric: Metric, last_id: str | None, end_ns: Instant
    ) -> tuple[str, str | None]:
        """Read one connection until it must be reopened; returns the reason and last id."""
        params = stream_params(self._symbols, metric=metric, view=self._view)
        silence = STREAM_SILENCE_S * NS_PER_SECOND
        try:
            async with self._client.stream(params, last_event_id=last_id) as messages:
                it = aiter(messages)
                while True:
                    deadline = min(self._clock.now() + silence, end_ns)
                    message = await within(
                        self._clock, _next(it), max(0, deadline - self._clock.now())
                    )
                    if message is None:
                        return "connection closed", last_id
                    if message is TIMED_OUT:
                        if self._clock.now() >= end_ns:
                            return "run window closed", last_id
                        return f"no event for {STREAM_SILENCE_S} s", last_id
                    assert isinstance(message, SseMessage)
                    if message.event_id:
                        last_id = message.event_id
                    reason = self._handle(metric, message)
                    if reason is not None:
                        return reason, last_id
        except SkylitStreamError as exc:
            if exc.dropped:
                return f"connection dropped: {exc.cause}", last_id
            return f"open failed: {exc.cause}", last_id

    def _handle(self, metric: Metric, message: SseMessage) -> str | None:
        """Deliver a snapshot event; a reason when the connection must be reopened."""
        received = self._clock.now()
        try:
            event = parse_stream_event(message.event, message.data, event_id=message.event_id)
        except MalformedResponseError as exc:
            self._log({"event": "stream_event_malformed", "metric": metric, "detail": str(exc)})
            return None
        if isinstance(event, StreamSnapshot):
            snapshot = event.to_snapshot(metric=metric, view_id=self._view_id)
            self._on_snapshots((snapshot,), received)
            return None
        if isinstance(event, StreamReconnect):
            return f"reconnect event ({event.reason or 'no reason'})"
        if isinstance(event, StreamClosed):
            return f"closed event ({event.reason or 'no reason'})"
        entry: dict[str, JsonValue] | None = None
        if isinstance(event, StreamConnected):
            entry = {"event": "stream_connected", "symbols": list(event.symbols)}
        elif isinstance(event, StreamResumed):
            entry = {"event": "stream_resumed", "gap_covered": event.gap_covered}
        elif isinstance(event, StreamSymbolUnavailable):
            entry = {
                "event": "stream_symbol_unavailable",
                "symbol": event.symbol,
                "reason": event.reason,
            }
        if entry is not None:
            entry["metric"] = metric
            self._log(entry)
        return None
