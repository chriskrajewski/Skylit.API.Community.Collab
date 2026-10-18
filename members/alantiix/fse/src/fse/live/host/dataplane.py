"""``DataPlane``: the always-on, strategy-agnostic feeds of ``fse run`` (design 4).

The data plane runs whether or not a plugin needs a feed; a feed is skipped
only when its ``enabled`` flag is false. It implements
:class:`~fse.live.host.host.HostDataPlane`:

- **Bars** (every Globex day, ``open_ns`` to 17:02 NY): one
  :class:`~fse.live.host.sources.MarketDataSource` per day (polling, or
  streaming with the market hub), contracts from the roll calendar
  (``feed_instruments_for``) with ``data_plane.projectx.expected_contracts`` as
  the fallback id. An instrument without a contract id is skipped for the day
  with an error log and a ``FeedStatusEvent("bars", False, ...)``.
- **Skylit** (exchange sessions only, inside ``skylit.window``): a
  :class:`~fse.live.map_feed.MapFeed` (gamma + vanna) and, when enabled, a
  :class:`~fse.live.darkpool_feed.DarkPoolFeed`, through one
  :class:`~fse.skylit.client.SkylitClient` per Globex day whose Fetch_Log is
  ``<run_dir>/dataplane/<day>/fetch_log.jsonl``. :attr:`DataPlane.levels`
  is the :class:`DataPlaneLevels` proxy that forwards ``gex_levels`` to the
  current day's client. A Skylit 401/402/403 stops the Skylit plane for the
  run (``FeedStatusEvent("skylit", False, ...)``).
- **Recording**: one :class:`~fse.live.recorder.Recorder` per Globex day at
  ``<recordings_root>/recordings/dataplane/<day>.jsonl.gz`` (kinds
  ``bar``, ``snapshot``, ``dark_pool``, ``operator_event`` for feed status),
  flushed every 60 s and closed at rotation and shutdown; quotes go to
  ``<day>.quotes.jsonl.gz`` (``record_quotes``: ``sampled_1s`` = the last
  quote per instrument per second, ``all`` = every quote and trade, ``off``).
- **Bar store**: :class:`~fse.live.host.barstore.LiveBarStore`.
- **Credits**: at each day open a ``credit_projection`` host-log line with the
  ``credit_projection`` formula of ``fse.live.runner``.
- **Subscription probe** (:meth:`DataPlane.probe`, ``auto|live|sim``) and
  **backfill** (:meth:`DataPlane.backfill`), both run by ``fse run`` before
  the plugins start. A heartbeat is written when the bar source starts and
  every 5 minutes after that: transport, subscription, per-instrument receipt
  lag (p50/p95/max) and ``rt_dropped``.

Fatal conditions (a ProjectX auth failure that stops the bar feed: exit 3;
any other bar source failure: exit 1) go to ``on_fatal``; :meth:`run` never
raises, so the host can stop gracefully. The data plane's log lines go to the
host log through ``log`` (``{"kind": ..., "level": ...}``); none holds a URL,
header or key.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Final, Protocol

from fse.calendars import CalendarError, Calendars
from fse.clock import Clock
from fse.data.darkpool import DarkPoolTradesClient
from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.live.bar_feed import BarRetriever, FeedInstrument
from fse.live.darkpool_feed import DEFAULT_INTERVAL_S as DARK_POOL_INTERVAL_S
from fse.live.darkpool_feed import DarkPoolFeed
from fse.live.host.barstore import LiveBarStore, LiveBarStoreView, StoredBar
from fse.live.host.config import DataPlaneConfig
from fse.live.host.dayclock import DATA_PAUSE, GlobexDay, globex_day, globex_day_at, next_open
from fse.live.host.host import (
    BarsEvent,
    DarkPoolEvent,
    FeedStatusEvent,
    MarketEvent,
    QuoteEvent,
    SnapshotsEvent,
)
from fse.live.host.sources import (
    HubFactory,
    MarketDataSource,
    PollingMarketData,
    StreamingMarketData,
)
from fse.live.levels_compare import LevelsClient
from fse.live.map_feed import MapClient, MapFeed
from fse.live.plugins.api import FeedName, Quote
from fse.live.recorder import (
    RECORDINGS_DIR_NAME,
    Recorder,
    RecordingError,
    decode_bar,
    iter_recording,
)
from fse.live.runner import feed_instruments_for
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.logio.log_writer import LineSink
from fse.pit.market_view import MAP_METRICS, heatmap_view
from fse.projectx.realtime import HubTrade
from fse.projectx.session import ProjectXAuthError
from fse.skylit.client import Failed, SkylitStoppedError
from fse.skylit.endpoints import DARK_POOL_TRADES, GEX_LEVELS, HEATMAP, STREAM, ViewParams
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.skylit.models import LevelsResponse
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

__all__ = [
    "DATAPLANE_DIR_NAME",
    "FLUSH_INTERVAL_NS",
    "HEARTBEAT_INTERVAL_NS",
    "PROBE_MINUTES",
    "DataPlane",
    "DataPlaneLevels",
    "DataPlanePaths",
    "SkylitPlaneClient",
    "SubscriptionProbeError",
    "credit_projection_for",
    "dataplane_quotes_path",
    "dataplane_recording_files",
    "dataplane_recording_path",
]

DATAPLANE_DIR_NAME: Final = "dataplane"
FLUSH_INTERVAL_NS: Final = 60 * NS_PER_SECOND
HEARTBEAT_INTERVAL_NS: Final = 300 * NS_PER_SECOND
PROBE_MINUTES: Final = 5
BACKFILL_CLOSE: Final = time(17, 0)
EXIT_FEED_STOPPED: Final = 1
EXIT_CREDENTIALS: Final = 3
_QUOTES_SUFFIX: Final = ".quotes.jsonl.gz"


class SubscriptionProbeError(Exception):
    """``subscription: live`` and the live probe failed (exit 3)."""

    exit_code = EXIT_CREDENTIALS


class SkylitPlaneClient(MapClient, DarkPoolTradesClient, LevelsClient, Protocol):
    """The parts of :class:`~fse.skylit.client.SkylitClient` the Skylit plane uses."""


type DataPlaneLog = Callable[[Mapping[str, JsonValue]], None]
type BarsFactory = Callable[[bool], BarRetriever]
"""``live -> retriever``: a ``ProjectXBars`` on the live (``True``) or sim data subscription."""
type SkylitFactory = Callable[[FetchLog], SkylitPlaneClient]
type CachedDay = Callable[[str, date], bool]
"""Whether the Data_Cache holds a final session for ``(instrument, day)`` (backfill)."""
type FatalHandler = Callable[[int, str], None]


def dataplane_recording_path(recordings_root: Path, day: date) -> Path:
    """``<recordings_root>/recordings/dataplane/<day>.jsonl.gz``."""
    return Path(recordings_root) / RECORDINGS_DIR_NAME / DATAPLANE_DIR_NAME / f"{day}.jsonl.gz"


def dataplane_recording_files(recordings_root: Path, day: date) -> list[Path]:
    """The day's recording files in reading order: every ``<day>.part*.jsonl.gz``
    (by name), then ``<day>.jsonl.gz``; only files that exist."""
    main = dataplane_recording_path(recordings_root, day)
    parts = sorted(
        (p for p in main.parent.glob(f"{day.isoformat()}.part*.jsonl.gz") if p.is_file()),
        key=lambda p: p.name,
    )
    return [*parts, main] if main.is_file() else parts


def dataplane_quotes_path(recordings_root: Path, day: date) -> Path:
    """``<recordings_root>/recordings/dataplane/<day>.quotes.jsonl.gz``."""
    return (
        Path(recordings_root)
        / RECORDINGS_DIR_NAME
        / DATAPLANE_DIR_NAME
        / f"{day.isoformat()}{_QUOTES_SUFFIX}"
    )


@dataclass(frozen=True, slots=True)
class DataPlanePaths:
    recordings_root: Path
    """Recordings go to ``<recordings_root>/recordings/dataplane/``."""
    run_dir: Path
    """Fetch_Logs go to ``<run_dir>/dataplane/<day>/``."""
    bar_store: Path


def credit_projection_for(cfg: DataPlaneConfig, day: GlobexDay) -> dict[str, JsonValue]:
    """The day's projected Skylit credits (same formula as ``fse.live.runner.credit_projection``).

    Stream: ``symbols x 2 metrics x (window minutes + 1)``; polling:
    ``ceil(window_s / refresh) x 2``; dark pool ``ceil(window_s / 60) x
    credits x tickers``. Zero outside exchange sessions or with Skylit off.
    """
    sky = cfg.skylit
    start, end = sky.window.pair()
    seconds = max(0, ny_instant(day.day, end) - ny_instant(day.day, start)) / NS_PER_SECOND
    active = sky.enabled and day.exchange_session
    metrics = len(MAP_METRICS)
    if not active:
        map_credits = 0
    elif sky.mode == "polling":
        map_credits = math.ceil(seconds / sky.refresh_interval_s) * metrics * HEATMAP.credits
    else:
        minutes = math.ceil(seconds / 60)
        map_credits = len(sky.symbols) * metrics * STREAM.credits * (minutes + 1)
    dark_pool = 0
    if active and sky.dark_pool.enabled:
        polls = math.ceil(seconds / DARK_POOL_INTERVAL_S)
        dark_pool = polls * DARK_POOL_TRADES.credits * max(1, len(sky.dark_pool.tickers))
    return {
        "day": day.day.isoformat(),
        "exchange_session": day.exchange_session,
        "refresh_mode": sky.mode,
        "refresh_interval_s": sky.refresh_interval_s,
        "window_start_ny": start.strftime("%H:%M"),
        "window_end_ny": end.strftime("%H:%M"),
        "symbols": list(sky.symbols),
        "metrics": list(MAP_METRICS),
        "map_credits": map_credits,
        "dark_pool_credits": dark_pool,
        "total_credits": map_credits + dark_pool,
    }


class DataPlaneLevels:
    """``LevelsClient`` proxy over the current Globex day's Skylit client (design 4.5)."""

    __slots__ = ("_plane",)

    def __init__(self, plane: DataPlane) -> None:
        self._plane = plane

    async def gex_levels(
        self, symbols: Iterable[str], *, metric: str, view: ViewParams
    ) -> LevelsResponse | Failed:
        client = self._plane.current_skylit
        if client is None:
            return Failed(GEX_LEVELS.host, GEX_LEVELS.path, {}, "failed", 0, error_type="no_client")
        return await client.gex_levels(symbols, metric=metric, view=view)


@dataclass(slots=True)
class _SkylitDay:
    day: date
    fetch_log: FetchLog
    client: SkylitPlaneClient


class _QuoteFile:
    """One day's quote file with the ``sampled_1s`` / ``all`` rule."""

    __slots__ = ("_buffer", "_mode", "_sink")

    def __init__(self, sink: LineSink, mode: str) -> None:
        self._sink = sink
        self._mode = mode
        self._buffer: dict[str, Quote] = {}

    def quote(self, quote: Quote) -> None:
        if self._mode == "all":
            self._line("quote", quote.received_ns, quote)
            return
        held = self._buffer.get(quote.instrument)
        if held is not None and held.received_ns // NS_PER_SECOND < (
            quote.received_ns // NS_PER_SECOND
        ):
            self._line("quote", held.received_ns, held)
        self._buffer[quote.instrument] = quote

    def trade(self, instrument: str, trade: HubTrade, received_ns: Instant) -> None:
        if self._mode == "all":
            payload = {
                "instrument": instrument,
                "contract_id": trade.contract_id,
                "t_ns": trade.t_ns,
                "price_t": trade.price_t,
            }
            self._line("trade", received_ns, payload)

    def flush(self, now: Instant | None) -> None:
        """Write held quotes whose second has ended (all of them when ``now`` is ``None``)."""
        for instrument in sorted(self._buffer):
            held = self._buffer[instrument]
            if now is None or held.received_ns // NS_PER_SECOND < now // NS_PER_SECOND:
                self._line("quote", held.received_ns, held)
                del self._buffer[instrument]
        self._sink.flush()

    def close(self) -> None:
        try:
            self.flush(None)
        finally:
            self._sink.close()

    def _line(self, kind: str, received_ns: Instant, payload: object) -> None:
        self._sink.write_json({"kind": kind, "received_ns": received_ns, "payload": payload})


class DataPlane:
    """The data plane of one ``fse run`` process (see the module notes)."""

    def __init__(
        self,
        cfg: DataPlaneConfig,
        *,
        clock: Clock,
        writer: LogWriter,
        paths: DataPlanePaths,
        calendars: Calendars,
        bars: BarsFactory,
        log: DataPlaneLog,
        on_fatal: FatalHandler,
        hub: HubFactory | None = None,
        skylit: SkylitFactory | None = None,
        calendar_loader: Callable[[date, date], Calendars] | None = None,
    ) -> None:
        self._cfg = cfg
        self._clock = clock
        self._writer = writer
        self._paths = paths
        self._calendars = calendars
        self._calendar_loader = calendar_loader
        self._bars_factory = bars
        self._log = log
        self._on_fatal = on_fatal
        self._hub = hub
        self._skylit_factory = skylit if cfg.skylit.enabled else None
        px = cfg.projectx
        self._live = px.subscription == "live"
        self._subscription = "live" if self._live else "sim"
        self._store = LiveBarStore(paths.bar_store, log=log)
        self._view = self._store.view()
        self._recorders: dict[date, Recorder] = {}
        self._quotes: dict[date, _QuoteFile] = {}
        self._skylit: _SkylitDay | None = None
        self._skylit_stopped = False
        self._source: MarketDataSource | None = None
        self._urgent: tuple[tuple[str, Instant], ...] = ()
        self._submit: Callable[[MarketEvent], None] | None = None
        self._last_flush: Instant = clock.now()
        self._last_day: date | None = None
        self._levels = DataPlaneLevels(self) if cfg.skylit.enabled else None
        self._fatal = False

    # ------------------------------------------------------------ properties

    @property
    def bar_store(self) -> LiveBarStoreView:
        return self._view

    @property
    def store(self) -> LiveBarStore:
        return self._store

    @property
    def levels(self) -> DataPlaneLevels | None:
        """``services.skylit_levels`` (``None`` with Skylit off)."""
        return self._levels

    @property
    def current_skylit(self) -> SkylitPlaneClient | None:
        return None if self._skylit is None else self._skylit.client

    @property
    def subscription(self) -> str:
        return self._subscription

    @property
    def source(self) -> MarketDataSource | None:
        return self._source

    # ------------------------------------------------------------ logs

    def _entry(self, kind: str, level: str, **fields: JsonValue) -> None:
        entry: dict[str, JsonValue] = {"kind": kind, "level": level, "source": "dataplane"}
        entry.update(fields)
        self._log(entry)

    def feed_log(self, entry: Mapping[str, JsonValue]) -> None:
        """A feed's own log entry (``{"event": ...}``) as a host-log line."""
        event = entry.get("event")
        level = entry.get("level")
        out: dict[str, JsonValue] = {
            "kind": event if isinstance(event, str) else "feed_event",
            "level": level if isinstance(level, str) else "warning",
            "source": "dataplane",
        }
        out.update({k: v for k, v in entry.items() if k not in ("event", "level")})
        self._log(out)

    # ------------------------------------------------------------ calendars and contracts

    def _day(self, d: date) -> GlobexDay:
        return globex_day(d, self._calendars.exchange.sessions)

    def _reload_calendars(self, d: date) -> None:
        if self._calendar_loader is None:
            return
        try:
            self._calendars = self._calendar_loader(d, d + timedelta(days=7))
        except CalendarError as exc:
            self._entry("calendar_stale", "error", detail=self._writer.redact(str(exc)))

    def contracts(self, day: date) -> tuple[tuple[FeedInstrument, ...], list[str]]:
        """The day's feed instruments and one problem line per instrument without a contract."""
        expected = self._cfg.projectx.expected_contracts
        return feed_instruments_for(
            [(i, self._calendars.roll.contract_for(i, day)) for i in self._cfg.instruments],
            expected.get,
        )

    # ------------------------------------------------------------ probe and backfill

    async def probe(self) -> str:
        """The subscription probe (design 4.4); returns ``live`` or ``sim``."""
        mode = self._cfg.projectx.subscription
        if mode == "sim":
            self._live, self._subscription = False, "sim"
            self._entry("subscription", "info", subscription="sim", mode=mode)
            return "sim"
        now = self._clock.now()
        end = now // NS_PER_MINUTE * NS_PER_MINUTE
        start = end - PROBE_MINUTES * NS_PER_MINUTE
        gday = globex_day_at(start) or next_open(start)
        found, _ = self.contracts(gday.day)
        item = next((i for i in found if i.instrument == "MNQ"), found[0] if found else None)
        results: dict[bool, tuple[bool, int]] = {}
        for live in (True, False) if mode == "auto" else (True,):
            ok, n, lag = False, 0, None
            if item is not None:
                try:
                    history = await self._bars_factory(live).retrieve(
                        instrument=item.instrument,
                        contract=item.contract,
                        contract_id=item.contract_id,
                        start_ns=start,
                        end_ns=end,
                        interval_s=60,
                    )
                except ProjectXAuthError:
                    raise
                received = self._clock.now()
                ok = not history.failures
                n = len(history.bars)
                if history.bars:
                    lag = received - max(b.close_ns for b in history.bars)
            results[live] = (ok, n)
            self._entry(
                "subscription_probe",
                "info",
                live=live,
                succeeded=ok,
                bars=n,
                newest_lag_ms=None if lag is None else lag // 1_000_000,
                instrument=None if item is None else item.instrument,
            )
        live_ok, live_bars = results[True]
        if mode == "live":
            if not live_ok or item is None:
                raise SubscriptionProbeError(
                    "subscription: live, but the live retrieveBars probe failed"
                )
            chosen = "live"
        else:
            sim_bars = results[False][1]
            # Live wins when it returned bars, or when it succeeded and neither had a
            # bar to show (market closed: nothing says the live subscription is missing).
            chosen = "live" if live_bars or (live_ok and not sim_bars) else "sim"
            if chosen == "sim":
                self._entry(
                    "projectx_sim_subscription",
                    "warning",
                    detail=(
                        "the live retrieveBars probe returned no bars; using the sim subscription"
                    ),
                )
        self._live = chosen == "live"
        self._subscription = chosen
        self._entry("subscription", "info", subscription=chosen, mode=mode)
        return chosen

    async def backfill(self, cached: CachedDay | None = None) -> int:
        """Fetch missing past Globex days into the bar store (design 4.6); days written."""
        n = self._cfg.projectx.backfill_sessions
        if n <= 0:
            return 0
        now = self._clock.now()
        current = globex_day_at(now) or next_open(now)
        cal = self._calendars.exchange.sessions
        days: list[date] = []
        d = current.day
        while len(days) < n:
            d -= timedelta(days=1)
            if not cal.covers(d):
                break
            if d.weekday() < 5 and cal.is_session(d):
                days.append(d)
        retriever = self._bars_factory(self._live)
        written = 0
        for instrument in self._cfg.instruments:
            for d in reversed(days):
                if self._store.has_day(instrument, d):
                    continue
                if cached is not None and cached(instrument, d):
                    continue
                period = self._calendars.roll.contract_for(instrument, d)
                if period is None or period.projectx_contract_id is None:
                    self._entry(
                        "backfill_skipped",
                        "warning",
                        instrument=instrument,
                        day=d.isoformat(),
                        reason="no ProjectX contract id",
                    )
                    continue
                gday = self._day(d)
                try:
                    history = await retriever.retrieve(
                        instrument=instrument,
                        contract=period.contract,
                        contract_id=period.projectx_contract_id,
                        start_ns=gday.open_ns,
                        end_ns=ny_instant(d, BACKFILL_CLOSE),
                        interval_s=60,
                    )
                except ProjectXAuthError:
                    raise
                received = self._clock.now()
                if history.failures or not history.bars:
                    self._entry(
                        "backfill_failed",
                        "warning",
                        instrument=instrument,
                        day=d.isoformat(),
                        failures=len(history.failures),
                        bars=len(history.bars),
                    )
                    continue
                self._store.write_day(
                    instrument,
                    d,
                    period.contract,
                    (StoredBar(b, received) for b in history.bars),
                )
                written += 1
                self._entry(
                    "backfill_day",
                    "info",
                    instrument=instrument,
                    day=d.isoformat(),
                    bars=len(history.bars),
                )
        return written

    # ------------------------------------------------------------ HostDataPlane

    def _recorder(self, day: date) -> Recorder:
        rec = self._recorders.get(day)
        if rec is None:
            rec = Recorder(self._writer, dataplane_recording_path(self._paths.recordings_root, day))
            self._recorders[day] = rec
            self._last_day = day
        return rec

    def _event_day(self, t: Instant) -> date:
        gday = globex_day_at(t)
        if gday is not None:
            return gday.day
        if self._last_day is not None:
            return self._last_day
        return next_open(t).day

    def _quote_file(self, day: date) -> _QuoteFile | None:
        mode = self._cfg.projectx.record_quotes
        if mode == "off":
            return None
        qf = self._quotes.get(day)
        if qf is None:
            sink = self._writer.open_lines(
                dataplane_quotes_path(self._paths.recordings_root, day), compress=True
            )
            qf = _QuoteFile(sink, mode)
            self._quotes[day] = qf
        return qf

    def record(self, event: MarketEvent) -> None:
        if isinstance(event, BarsEvent):
            for bar in event.bars:
                self._recorder(self._event_day(bar.open_ns)).bar(bar, event.received_ns)
        elif isinstance(event, SnapshotsEvent):
            rec = self._recorder(self._event_day(event.received_ns))
            for snap in event.snaps:
                rec.snapshot(snap, event.received_ns)
        elif isinstance(event, DarkPoolEvent):
            rec = self._recorder(self._event_day(event.received_ns))
            rec.dark_pool(event.ticker, event.prints, event.received_ns)
        elif isinstance(event, QuoteEvent):
            qf = self._quote_file(self._event_day(event.quote.received_ns))
            if qf is not None:
                qf.quote(event.quote)
        else:
            now = self._clock.now()
            self._recorder(self._event_day(now)).operator_event(
                {
                    "event": "feed_status",
                    "feed": event.feed,
                    "up": event.up,
                    "detail": event.detail,
                },
                now,
            )
        now = self._clock.now()
        if now - self._last_flush >= FLUSH_INTERVAL_NS:
            self.flush(now)

    def flush(self, now: Instant | None = None) -> None:
        """Flush every open recording (the 60 s timer, rotation, shutdown)."""
        at = self._clock.now() if now is None else now
        self._last_flush = at
        for rec in self._recorders.values():
            rec.flush()
        for qf in self._quotes.values():
            qf.flush(at)

    def store_bars(self, bars: Sequence[Bar], received_ns: Instant) -> None:
        self._store.add(bars, received_ns)

    def recorded_bars(self, day: GlobexDay) -> list[tuple[Bar, Instant]]:
        rec = self._recorders.get(day.day)
        if rec is not None:
            rec.flush()
        out: list[tuple[Bar, Instant]] = []
        for path in dataplane_recording_files(self._paths.recordings_root, day.day):
            try:  # streamed: a day's recording reaches gigabytes of snapshots
                for entry in iter_recording(path, kinds=("bar",)):
                    try:
                        bar = decode_bar(entry.payload)
                    except RecordingError:
                        continue
                    if day.open_ns <= bar.open_ns < day.rotate_ns:
                        out.append((bar, entry.received_ns))
            except RecordingError as exc:  # keep the bars read so far
                self._entry(
                    "recording_unreadable",
                    "warning",
                    day=day.day.isoformat(),
                    detail=self._writer.redact(str(exc)),
                )
        return out

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None:
        self._urgent = tuple(closes)
        if self._source is not None:
            self._source.set_urgent(self._urgent)

    def rotate(self, closing: GlobexDay, opening: GlobexDay | None) -> None:
        self._close_day(closing.day)
        self._store.forget(closing.day)

    def close(self) -> None:
        for day in sorted({*self._recorders, *self._quotes}):
            self._close_day(day)
        self._close_skylit()

    def _close_day(self, day: date) -> None:
        rec = self._recorders.pop(day, None)
        if rec is not None:
            rec.close()
        qf = self._quotes.pop(day, None)
        if qf is not None:
            qf.close()
        if self._skylit is not None and self._skylit.day == day:
            self._close_skylit()

    def _close_skylit(self) -> None:
        sky, self._skylit = self._skylit, None
        if sky is not None:
            sky.fetch_log.close()

    # ------------------------------------------------------------ the loop

    def _emit(self, event: MarketEvent) -> None:
        if self._submit is not None:
            self._submit(event)

    def _status(self, feed: FeedName, up: bool, detail: str) -> None:
        self._entry("feed_status", "info" if up else "warning", feed=feed, up=up, detail=detail)
        self._emit(FeedStatusEvent(feed, up, detail))

    def _on_bars(self, bars: Sequence[Bar], received_ns: Instant) -> None:
        if bars:
            self._emit(BarsEvent(tuple(bars), received_ns))

    def _on_quote(self, quote: Quote) -> None:
        self._emit(QuoteEvent(quote))

    def _on_trade(self, instrument: str, trade: HubTrade, received_ns: Instant) -> None:
        qf = self._quote_file(self._event_day(received_ns))
        if qf is not None:
            qf.trade(instrument, trade, received_ns)

    def _on_snapshots(self, snaps: Sequence[Snapshot], received_ns: Instant) -> None:
        if snaps:
            self._emit(SnapshotsEvent(tuple(snaps), received_ns))

    def _on_prints(
        self, ticker: str, prints: Sequence[DarkPoolPrint], received_ns: Instant
    ) -> None:
        self._emit(DarkPoolEvent(ticker, tuple(prints), received_ns))

    def _make_source(self) -> MarketDataSource:
        px = self._cfg.projectx
        bars = self._bars_factory(self._live)
        if px.transport == "polling" or self._hub is None:
            return PollingMarketData(
                bars,
                self._clock,
                on_bars=self._on_bars,
                on_status=self._status,
                log=self.feed_log,
                poll_interval_s=px.poll_interval_s,
                hot_poll_interval_s=px.hot_poll_interval_s,
                subscription=self._subscription,
            )
        return StreamingMarketData(
            bars,
            self._clock,
            hub=self._hub,
            on_bars=self._on_bars,
            on_quote=self._on_quote,
            on_status=self._status,
            on_trade=self._on_trade,
            log=self.feed_log,
            poll_interval_s=px.poll_interval_s,
            hot_poll_interval_s=px.hot_poll_interval_s,
            subscription=self._subscription,
            transport=px.transport,
        )

    async def run(self, submit: Callable[[MarketEvent], None]) -> None:
        """Run the feeds day after day until cancelled (see the module notes)."""
        self._submit = submit
        helpers = [
            asyncio.ensure_future(self._flush_loop()),
            asyncio.ensure_future(self._heartbeat_loop()),
        ]
        first = True
        try:
            while True:
                now = self._clock.now()
                gday = globex_day_at(now)
                if gday is None:
                    await self._clock.sleep(next_open(now).open_ns - now)
                    continue
                if not first:
                    self._reload_calendars(gday.day)
                day = self._day(gday.day)
                await self._run_day(day, first=first)
                first = False
                now = self._clock.now()
                if now < day.rotate_ns:
                    await self._clock.sleep(day.rotate_ns - now)
        finally:
            for task in helpers:
                task.cancel()
            for task in helpers:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _run_day(self, day: GlobexDay, *, first: bool) -> None:
        self._entry("credit_projection", "info", **credit_projection_for(self._cfg, day))
        instruments, missing = self.contracts(day.day)
        for problem in missing:
            self._entry("bar_feed_skipped", "error", day=day.day.isoformat(), detail=problem)
            self._status("bars", False, f"{day.day}: {problem}")
        seed: dict[str, Instant] = {}
        if first:
            for bar, _ in self.recorded_bars(day):
                seed[bar.instrument] = max(seed.get(bar.instrument, 0), bar.close_ns)
        until = ny_instant(day.day, DATA_PAUSE)
        tasks: list[asyncio.Future[None]] = []
        bars_task: asyncio.Future[None] | None = None
        if instruments and self._clock.now() < until:
            source = self._make_source()
            self._source = source
            source.set_urgent(self._urgent)
            # First heartbeat as soon as the bar source exists (the loop still
            # sleeps HEARTBEAT_INTERVAL_NS before each later beat). Live's ProjectX
            # tile keys off kind=heartbeat; waiting a full interval looked "unavailable"
            # even after market_hub_connected / quotes feed_status.
            self._emit_heartbeat(source)
            bars_task = asyncio.ensure_future(
                source.run(day, instruments, day.open_ns, until, seed=seed)
            )
            tasks.append(bars_task)
        tasks.extend(self._skylit_tasks(day))
        try:
            if bars_task is not None:
                await self._guard_bars(bars_task)
            rest = [t for t in tasks if t is not bars_task]
            if rest:
                await asyncio.gather(*rest, return_exceptions=True)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await task
            self._source = None
        if self._fatal:
            await asyncio.get_running_loop().create_future()  # idle until the host stops

    async def _guard_bars(self, task: asyncio.Future[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            raise
        except ProjectXAuthError as exc:
            self._fatal_stop(EXIT_CREDENTIALS, f"bar feed stopped: {type(exc).__name__}: {exc}")
        except Exception as exc:
            self._fatal_stop(EXIT_FEED_STOPPED, f"bar feed stopped: {type(exc).__name__}: {exc}")

    def _fatal_stop(self, code: int, text: str) -> None:
        detail = self._writer.redact(text)
        self._fatal = True
        self._entry("feed_stopped", "error", feed="bars", detail=detail)
        self._status("bars", False, detail)
        self._on_fatal(code, detail)

    def _skylit_tasks(self, day: GlobexDay) -> list[asyncio.Future[None]]:
        factory = self._skylit_factory
        sky = self._cfg.skylit
        if factory is None or self._skylit_stopped or not day.exchange_session:
            return []
        start_t, end_t = sky.window.pair()
        start, end = ny_instant(day.day, start_t), ny_instant(day.day, end_t)
        if self._clock.now() >= end:
            return []
        if self._skylit is None or self._skylit.day != day.day:
            self._close_skylit()
            fetch_log = FetchLog(
                self._writer,
                self._paths.run_dir
                / DATAPLANE_DIR_NAME
                / day.day.isoformat()
                / FETCH_LOG_FILE_NAME,
            )
            self._skylit = _SkylitDay(day.day, fetch_log, factory(fetch_log))
        client = self._skylit.client
        view = heatmap_view(sky.heatmap_view)
        feed = MapFeed(
            client,
            self._clock,
            symbols=sky.symbols,
            view=view,
            view_id=view.view_id(),
            mode=sky.mode,
            refresh_interval_s=sky.refresh_interval_s,
            on_snapshots=self._on_snapshots,
            log=self.feed_log,
            layout=sky.layout,
        )
        out: list[asyncio.Future[None]] = [
            asyncio.ensure_future(self._guard_skylit("skylit", feed.run(start, end)))
        ]
        if sky.dark_pool.enabled and sky.dark_pool.tickers:
            dark = DarkPoolFeed(
                client,
                self._clock,
                tickers=sky.dark_pool.tickers,
                session=day.day,
                on_prints=self._on_prints,
                log=self.feed_log,
            )
            out.append(
                asyncio.ensure_future(self._guard_skylit("dark_pool", self._dark(dark, start, end)))
            )
        return out

    async def _dark(self, feed: DarkPoolFeed, start: Instant, end: Instant) -> None:
        now = self._clock.now()
        if now < start:
            await self._clock.sleep(start - now)
        await feed.run(end)

    async def _guard_skylit(self, name: FeedName, work: Coroutine[Any, Any, None]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except SkylitStoppedError as exc:
            self._skylit_stopped = True
            detail = self._writer.redact(f"{name} stopped: {type(exc).__name__}: {exc}")
            self._entry("feed_stopped", "error", feed=name, detail=detail)
            self._status(name, False, detail)
        except Exception as exc:
            detail = self._writer.redact(f"{name} failed: {type(exc).__name__}: {exc}")
            self._entry("feed_stopped", "error", feed=name, detail=detail)
            self._status(name, False, detail)

    async def _flush_loop(self) -> None:
        while True:
            await self._clock.sleep(FLUSH_INTERVAL_NS)
            try:
                self.flush()
            except OSError as exc:
                self._entry("recording_flush_failed", "error", detail=type(exc).__name__)

    def _emit_heartbeat(self, source: MarketDataSource) -> None:
        status = source.status
        self._entry(
            "heartbeat",
            "info",
            transport=status.transport,
            connected=status.connected,
            subscription=status.subscription,
            lag=source.take_lag(),
            rt_dropped=status.rt_dropped,
        )

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._clock.sleep(HEARTBEAT_INTERVAL_NS)
            source = self._source
            if source is None:
                continue
            self._emit_heartbeat(source)
