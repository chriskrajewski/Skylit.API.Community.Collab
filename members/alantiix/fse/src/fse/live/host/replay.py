"""Backtest any runner plugin through the live host (``fse backtest-plugin``): core, generic.

The same :class:`~fse.live.host.host.PluginHost`, paper broker, fills, risk caps,
gate, wakes and day rotation as ``fse run`` drive the plugins; only the data plane
and the clock differ. :class:`ReplayDataPlane` plays, in receipt order, exactly the
inputs the enabled plugins declare in ``needs()`` (with the runner ``data_plane``
block for the Skylit view, layout and window):

- **bars** of every needed instrument: the data-plane recording's ``bar`` lines when
  the day was recorded, else the Data_Cache 1m session (received ``lag`` after close);
- **Skylit snapshots** of the needed symbols: the recording's ``snapshot`` lines with
  their receipt times (the grid of ``layout: matrix`` included), else the
  Data_Cache windows of the configured view and layout (received ``lag`` after asOf);
- **dark-pool prints** and **feed status** from the recording; **quotes** from the
  day's quote file (``needs().quotes`` only).

Nothing here names a plugin. :func:`coverage` reports, per Globex day and need,
where the input comes from or that it is missing; :func:`run_backtest` refuses a
range with a missing need unless ``allow_gaps``.

**No lookahead.** The host's ``cache_dir`` is a fresh history-only Data_Cache (1m
bars of the needed instruments for the ``history`` sessions before ``first``); the
replayed days reach the plugins only through the data plane and its bar store.

**Isolation.** ``HOME`` points into the work folder for the whole run, so every
plugin's ``~`` default (outputs, private caches) lands in the run folder and never
touches the Operator's files. Absolute paths in a plugin ``config`` are listed as
warnings in the coverage report.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import heapq
import itertools
import json
import os
from collections.abc import (
    Callable,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Final, cast

from fse.calendars import Calendars, load_calendars
from fse.data.cache import CacheWindowKey, DataCache, cache_window_starts
from fse.data.cache_io import CacheReadError
from fse.engine.types import Bar, Snapshot, Trade
from fse.live.host.barstore import LiveBarStore
from fse.live.host.config import RunnerConfig
from fse.live.host.dataplane import (
    DATAPLANE_DIR_NAME,
    dataplane_quotes_path,
    dataplane_recording_files,
)
from fse.live.host.dayclock import GlobexDay, globex_day
from fse.live.host.host import (
    BarsEvent,
    DarkPoolEvent,
    FeedStatusEvent,
    HostPaths,
    HostResources,
    InlineBlockingRunner,
    MarketEvent,
    PluginHost,
    QuoteEvent,
    SnapshotsEvent,
)
from fse.live.host.recordings_s3 import S3Recordings, is_quotes_name
from fse.live.plugins.api import DataNeeds, FeedName, Quote, StrategyPlugin
from fse.live.recorder import (
    RECORDINGS_DIR_NAME,
    RecordedInput,
    RecordingError,
    RecordingKind,
    decode_bar,
    decode_dark_pool,
    decode_snapshot,
    iter_recording,
)
from fse.logio import LogWriter
from fse.pit.market_view import MAP_METRICS, heatmap_view
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND, Instant, ny_datetime, ny_instant

__all__ = [
    "DEFAULT_HISTORY",
    "DEFAULT_LAG_S",
    "RECORDING_S3",
    "REORDER_NS",
    "Coverage",
    "DayCoverage",
    "ReplayDataPlane",
    "ReplayNeeds",
    "VirtualClock",
    "coverage",
    "day_events",
    "replay_needs",
    "sandbox_home",
    "scan_day",
]

DEFAULT_HISTORY: Final = 30
"""1m sessions before ``first`` in the history-only cache (enough for every shipped plugin)."""
DEFAULT_LAG_S: Final = 2
"""Seconds from a bar's close (or a snapshot's asOf) to its simulated receipt.

Added to the virtual receipt instant only. ``VirtualClock.sleep`` jumps that
clock; it does not wait ``lag_s`` in real time.
"""
_SETTLE: Final = 200
_QUIET: Final = 8
"""Loop turns with an unchanged sleeper set before virtual time may jump.

``_SETTLE`` is still the cap, so a task that needs many turns reaches its next
sleep. Production replays park in a handful of turns, and waiting out the cap
on every 1-minute bar was the cost of ~60 bars/s.
"""


# ---------------------------------------------------------------- virtual time


class VirtualClock:
    """A :class:`~fse.clock.Clock` whose time moves only when every task is asleep.

    ``run(main)`` runs ``main`` and every task it starts; when all are parked in
    ``sleep()`` time jumps to the earliest wake. A replay never waits in real time.
    """

    def __init__(self, start: Instant) -> None:
        self._now = start
        self._sleepers: list[tuple[Instant, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()
        self._live = 0
        self._generation = 0

    def now(self) -> Instant:
        return self._now

    async def sleep(self, delay_ns: int) -> None:
        if delay_ns <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._now + delay_ns, next(self._seq), fut))
        self._live += 1
        self._generation += 1
        await fut

    async def run[T](self, main: Coroutine[Any, Any, T], *, max_steps: int = 50_000_000) -> T:
        task = asyncio.get_running_loop().create_task(main)
        try:
            for _ in range(max_steps):
                quiet = 0
                seen = self._generation
                for _ in range(_SETTLE):
                    if task.done():
                        return task.result()
                    await asyncio.sleep(0)
                    gen = self._generation
                    # An empty sleeper set is not "quiet": a task may still be on its
                    # way to sleep(). A cancelled sleep leaves its future pending, so
                    # it still counts, matching a scan of not-done futures.
                    if self._live and gen == seen:
                        quiet += 1
                        if quiet >= _QUIET:
                            break
                    else:
                        quiet = 0
                        seen = gen
                if task.done():
                    return task.result()
                if not self._wake_next():
                    raise RuntimeError("replay deadlock: tasks blocked and nothing asleep")
            raise RuntimeError(f"replay did not finish in {max_steps} time steps")
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _wake_next(self) -> bool:
        while self._sleepers and self._sleepers[0][2].done():
            heapq.heappop(self._sleepers)
        if not self._sleepers:
            return False
        wake = self._sleepers[0][0]
        self._now = max(self._now, wake)
        woke = False
        while self._sleepers and self._sleepers[0][0] == wake:
            _, _, fut = heapq.heappop(self._sleepers)
            if not fut.done():
                fut.set_result(None)
                self._live -= 1
                woke = True
        if woke:
            self._generation += 1
        return True


# ---------------------------------------------------------------- needs


@dataclass(frozen=True, slots=True)
class ReplayNeeds:
    """The union of the enabled plugins' ``needs()`` (and the data plane's Skylit view)."""

    instruments: tuple[str, ...]
    skylit_symbols: tuple[str, ...]
    layout: str
    view_id: str | None
    window: tuple[str, str] | None
    dark_pool_tickers: tuple[str, ...]
    quotes: bool
    matrix_view_id: str | None = None
    """The Data_Cache view id of the same view pulled with ``layout: matrix``."""

    def kinds(self) -> list[str]:
        out = [f"bars {i}" for i in self.instruments]
        out += [f"skylit {s} ({self.layout})" for s in self.skylit_symbols]
        out += [f"dark pool {t}" for t in self.dark_pool_tickers]
        if self.quotes:
            out += [f"quotes {i}" for i in self.instruments]
        return out


def replay_needs(cfg: RunnerConfig, needs: Mapping[str, DataNeeds]) -> ReplayNeeds:
    inst: dict[str, None] = {}
    syms: dict[str, None] = {}
    dark: dict[str, None] = {}
    quotes = False
    layout = "net"
    for n in needs.values():
        for b in n.bars:
            inst[b.instrument] = None
        if n.skylit is not None:
            for s in n.skylit.symbols:
                syms[s] = None
            for t in n.skylit.dark_pool_tickers:
                dark[t] = None
            if n.skylit.layout == "matrix":
                layout = "matrix"
        quotes = quotes or n.quotes
    sky = cfg.data_plane.skylit
    has_sky = bool(syms) and sky.enabled
    return ReplayNeeds(
        instruments=tuple(inst),
        skylit_symbols=tuple(syms) if has_sky else (),
        layout=layout,
        view_id=heatmap_view(sky.heatmap_view).view_id() if has_sky else None,
        window=(f"{sky.window.start:%H:%M}", f"{sky.window.end:%H:%M}") if has_sky else None,
        dark_pool_tickers=tuple(dark),
        quotes=quotes,
        matrix_view_id=(
            replace(heatmap_view(sky.heatmap_view), layout="matrix").view_id() if has_sky else None
        ),
    )


# ---------------------------------------------------------------- inputs per day

REORDER_NS: Final = 120 * NS_PER_SECOND
"""A file's line order is only roughly receipt order: events wait this long to be sorted."""
RECORDING_S3: Final = "recording (s3)"
"""The coverage source of a day found only in ``--recordings-s3`` (downloaded at replay)."""
_RANK_RECORDED: Final = 0  # dark-pool prints and feed status (ties: as the old full sort)
_RANK_BARS: Final = 1
_RANK_SNAPS: Final = 2
_RANK_QUOTES: Final = 3

type _Key = tuple[Instant, int, int, int]
"""``(received_ns, rank, sub, seq)``: receipt order, then the input kind, then file order."""
type _Keyed = tuple[_Key, MarketEvent]


def _recording_entries(
    files: Sequence[Path], kinds: Sequence[RecordingKind]
) -> Generator[RecordedInput]:
    """The lines of ``kinds`` in the day's recording files, streamed; a bad file ends early."""
    for path in files:
        try:
            yield from iter_recording(path, kinds)
        except RecordingError, OSError:
            continue  # the lines read before the problem are kept


def _quote_lines(path: Path | None) -> Generator[tuple[str, Any]]:
    """``(kind, payload)`` of a gzip JSON Lines file (a quote file), streamed; a cut-off tail
    is dropped."""
    if path is None or not path.is_file():
        return
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("kind"), str):
                    yield obj["kind"], obj.get("payload")
    except OSError, EOFError:
        return


def _reorder(events: Iterable[tuple[Instant, int, int, MarketEvent]]) -> Iterator[_Keyed]:
    """``events`` in key order, given that none is more than :data:`REORDER_NS` late."""
    heap: list[_Keyed] = []
    seq = itertools.count()
    newest: Instant | None = None
    for t, rank, sub, event in events:
        heapq.heappush(heap, ((t, rank, sub, next(seq)), event))
        newest = t if newest is None or t > newest else newest
        while heap[0][0][0] < newest - REORDER_NS:
            yield heapq.heappop(heap)
    while heap:
        yield heapq.heappop(heap)


@dataclass(slots=True)
class _Day:
    """One Globex day: where each need comes from, and how to play its events."""

    gday: GlobexDay
    sources: dict[str, str]
    needs: ReplayNeeds
    recordings_root: Path | None
    lag_ns: int
    cache_view_ids: CacheViewIds
    s3: S3Recordings | None = None

    def events(self, cache: DataCache, scratch: Path) -> Generator[tuple[Instant, MarketEvent]]:
        """The day's events in receipt order, lazily (see :func:`day_events`)."""
        return day_events(
            self.gday,
            self.needs,
            self.sources,
            cache=cache,
            recordings_root=self.recordings_root,
            lag_ns=self.lag_ns,
            cache_view_ids=self.cache_view_ids,
            s3=self.s3,
            scratch=scratch,
        )


def _in_window(t: Instant, window: tuple[str, str] | None) -> bool:
    if window is None:
        return False
    hhmm = ny_datetime(t).strftime("%H:%M")
    return window[0] <= hhmm < window[1]


def _quote(payload: Any) -> Quote | None:
    if not isinstance(payload, dict):
        return None
    try:
        return Quote(
            instrument=str(payload["instrument"]),
            contract_id=str(payload["contract_id"]),
            t_ns=int(payload["t_ns"]),
            received_ns=int(payload["received_ns"]),
            bid_t=None if payload.get("bid_t") is None else int(payload["bid_t"]),
            ask_t=None if payload.get("ask_t") is None else int(payload["ask_t"]),
            last_t=None if payload.get("last_t") is None else int(payload["last_t"]),
        )
    except KeyError, TypeError, ValueError:
        return None


def _cache_bars(cache: DataCache, inst: str, d: date) -> list[Bar]:
    try:
        return cache.bars.read_session(inst, 60, d)
    except CacheReadError:
        return []


def _cache_snaps(
    cache: DataCache, view_ids: Sequence[str], symbol: str, d: date, window: tuple[str, str]
) -> list[Snapshot]:
    start = ny_instant(d, time.fromisoformat(window[0]))
    end = ny_instant(d, time.fromisoformat(window[1]))
    out: list[Snapshot] = []
    for vid in view_ids:
        for metric in MAP_METRICS:
            for t in cache_window_starts(start - start % (15 * 60 * NS_PER_SECOND), end):
                try:
                    key = CacheWindowKey(symbol, metric, vid, d, t)
                    out.extend(cache.read_window(key))
                except CacheReadError, ValueError:
                    continue
        if out:
            return out
    return out


CacheViewIds = Callable[[ReplayNeeds], Sequence[str]]
"""The Data_Cache view ids holding snapshots of the needed view and layout."""


def _default_view_ids(needs: ReplayNeeds) -> Sequence[str]:
    """The cache view of the needed layout (``fse pull --layout matrix`` stores its own)."""
    if needs.view_id is None:
        return ()
    if needs.layout == "matrix":
        return () if needs.matrix_view_id is None else (needs.matrix_view_id,)
    return (needs.view_id,)


def _skylit_key(needs: ReplayNeeds, symbol: str) -> str:
    return f"skylit {symbol} ({needs.layout})"


def _scan_recording(
    gday: GlobexDay, needs: ReplayNeeds, files: Sequence[Path]
) -> tuple[set[str], set[str], set[str]]:
    """The needed instruments, symbols and tickers the recording holds (no payload is kept)."""
    want_bars, want_syms = set(needs.instruments), set(needs.skylit_symbols)
    want_dark = set(needs.dark_pool_tickers)
    bars: set[str] = set()
    syms: set[str] = set()
    dark: set[str] = set()
    kinds: list[RecordingKind] = []
    if want_bars:
        kinds.append("bar")
    if want_syms:
        kinds.append("snapshot")
    if want_dark:
        kinds.append("dark_pool")
    if not files or not kinds:
        return bars, syms, dark
    with contextlib.closing(_recording_entries(files, kinds)) as entries:
        for e in entries:
            try:
                if e.kind == "bar":
                    bar = decode_bar(e.payload)
                    if bar.instrument in want_bars and (
                        gday.open_ns <= bar.open_ns < gday.rotate_ns
                    ):
                        bars.add(bar.instrument)
                elif e.kind == "snapshot":
                    p = e.payload
                    if (
                        isinstance(p, dict)
                        and p.get("symbol") in want_syms - syms
                        and p.get("view_id") == needs.view_id
                        and _in_window(e.received_ns, needs.window)
                    ):
                        syms.add(decode_snapshot(p).symbol)
                elif e.kind == "dark_pool":
                    ticker, _ = decode_dark_pool(e.payload)
                    if ticker in want_dark:
                        dark.add(ticker)
            except RecordingError:
                continue
            if bars >= want_bars and syms >= want_syms and dark >= want_dark:
                break  # every need found: the rest of the file is not read
    return bars, syms, dark


def scan_day(
    gday: GlobexDay,
    needs: ReplayNeeds,
    *,
    cache: DataCache,
    recordings_root: Path | None,
    cache_view_ids: CacheViewIds = _default_view_ids,
    s3: S3Recordings | None = None,
) -> dict[str, str]:
    """Where each need of one Globex day comes from (one streaming pass; no payload kept).

    A day with no local recording file whose files are in ``s3`` is reported
    :data:`RECORDING_S3` for every need such a recording serves, without downloading.
    """
    files = [] if recordings_root is None else dataplane_recording_files(recordings_root, gday.day)
    qpath = None if recordings_root is None else dataplane_quotes_path(recordings_root, gday.day)
    label = "recording"
    rec_quotes: set[str] = set()
    names = (
        s3.names(gday.day)
        if s3 is not None and not files and (qpath is None or not qpath.is_file())
        else []
    )
    if names:
        label = RECORDING_S3
        main = any(not is_quotes_name(n) for n in names)
        sky = main and gday.exchange_session and needs.window is not None
        rec_bars = set(needs.instruments) if main else set()
        rec_syms = set(needs.skylit_symbols) if sky else set()
        rec_dark = set(needs.dark_pool_tickers) if main else set()
        if any(is_quotes_name(n) for n in names):
            rec_quotes = set(needs.instruments)
    else:
        rec_bars, rec_syms, rec_dark = _scan_recording(gday, needs, files)
        if needs.quotes:
            want = set(needs.instruments)
            with contextlib.closing(_quote_lines(qpath)) as lines:
                for kind, payload in lines:
                    q = _quote(payload) if kind == "quote" else None
                    if q is not None and q.instrument in want:
                        rec_quotes.add(q.instrument)
                        if rec_quotes >= want:
                            break
    sources: dict[str, str] = {}
    for inst in needs.instruments:
        if inst in rec_bars:
            sources[f"bars {inst}"] = label
        else:
            cached = bool(_cache_bars(cache, inst, gday.day))
            sources[f"bars {inst}"] = "cache" if cached else "missing"
    for sym in needs.skylit_symbols:
        key = _skylit_key(needs, sym)
        if sym in rec_syms:
            sources[key] = label
        elif not gday.exchange_session or needs.window is None:
            sources[key] = "no session"
        else:
            snaps = _cache_snaps(cache, cache_view_ids(needs), sym, gday.day, needs.window)
            sources[key] = "cache" if snaps else "missing"
    for t in needs.dark_pool_tickers:
        sources[f"dark pool {t}"] = label if t in rec_dark else "missing"
    if needs.quotes:
        for inst in needs.instruments:
            sources[f"quotes {inst}"] = label if inst in rec_quotes else "missing"
    return sources


def _recorded_events(
    gday: GlobexDay,
    needs: ReplayNeeds,
    files: Sequence[Path],
    bar_index: Mapping[str, int],
    symbols: set[str],
) -> Iterator[tuple[Instant, int, int, MarketEvent]]:
    """The recording's events of the needs it serves, in file order (snapshot batches grouped)."""
    kinds: list[RecordingKind] = ["operator_event"]
    if bar_index:
        kinds.append("bar")
    if symbols:
        kinds.append("snapshot")
    if needs.dark_pool_tickers:
        kinds.append("dark_pool")
    batch: list[Snapshot] = []
    batch_t: Instant = 0
    with contextlib.closing(_recording_entries(files, kinds)) as entries:
        for e in entries:
            at = e.received_ns
            if batch and at != batch_t:  # the batch of one receipt is complete
                yield batch_t, _RANK_SNAPS, 0, SnapshotsEvent(tuple(batch), batch_t)
                batch = []
            try:
                if e.kind == "snapshot":
                    snap = decode_snapshot(e.payload)
                    if (
                        snap.symbol in symbols
                        and snap.view_id == needs.view_id
                        and _in_window(at, needs.window)
                    ):
                        batch.append(snap)
                        batch_t = at
                elif e.kind == "bar":
                    bar = decode_bar(e.payload)
                    idx = bar_index.get(bar.instrument)
                    if idx is not None and gday.open_ns <= bar.open_ns < gday.rotate_ns:
                        yield at, _RANK_BARS, idx, BarsEvent((bar,), at)
                elif e.kind == "dark_pool":
                    ticker, prints = decode_dark_pool(e.payload)
                    if ticker in needs.dark_pool_tickers:
                        yield at, _RANK_RECORDED, 0, DarkPoolEvent(ticker, prints, at)
                elif e.kind == "operator_event" and isinstance(e.payload, dict):
                    p = e.payload
                    if p.get("event") == "feed_status" and isinstance(p.get("feed"), str):
                        status = FeedStatusEvent(
                            cast("FeedName", p["feed"]),
                            bool(p.get("up")),
                            str(p.get("detail") or ""),
                        )
                        yield at, _RANK_RECORDED, 0, status
            except RecordingError:
                continue
    if batch:
        yield batch_t, _RANK_SNAPS, 0, SnapshotsEvent(tuple(batch), batch_t)


def _keyed(events: Iterable[tuple[Instant, int, int, MarketEvent]]) -> Iterator[_Keyed]:
    """Already ordered events with their merge key."""
    seq = itertools.count()
    for t, rank, sub, event in events:
        yield (t, rank, sub, next(seq)), event


def _cache_bar_events(
    cache: DataCache, inst: str, idx: int, d: date, lag_ns: int
) -> Iterator[tuple[Instant, int, int, MarketEvent]]:
    for bar in sorted(_cache_bars(cache, inst, d), key=lambda b: b.close_ns):
        live = replace(bar, source="projectx")
        received = bar.close_ns + lag_ns
        yield received, _RANK_BARS, idx, BarsEvent((live,), received)


def _cache_snap_events(
    cache: DataCache,
    needs: ReplayNeeds,
    view_ids: Sequence[str],
    sym: str,
    idx: int,
    d: date,
    lag_ns: int,
) -> Iterator[tuple[Instant, int, int, MarketEvent]]:
    assert needs.window is not None
    out: list[tuple[Instant, Snapshot]] = []
    for s in _cache_snaps(cache, view_ids, sym, d, needs.window):
        if s.view_id != needs.view_id:  # a matrix-layout cache view: the live view id
            s = replace(s, view_id=needs.view_id or s.view_id)
        received = s.as_of_ns + lag_ns
        if _in_window(received, needs.window):
            out.append((received, s))
    out.sort(key=lambda x: x[0])
    for received, s in out:
        yield received, _RANK_SNAPS, idx, SnapshotsEvent((s,), received)


def _quote_events(
    path: Path | None, needs: ReplayNeeds
) -> Iterator[tuple[Instant, int, int, MarketEvent]]:
    for kind, payload in _quote_lines(path):
        q = _quote(payload) if kind == "quote" else None
        if q is not None and q.instrument in needs.instruments:
            yield q.received_ns, _RANK_QUOTES, 0, QuoteEvent(q)


def _local_events(
    gday: GlobexDay,
    needs: ReplayNeeds,
    sources: Mapping[str, str],
    *,
    cache: DataCache,
    recordings_root: Path | None,
    lag_ns: int,
    cache_view_ids: CacheViewIds,
) -> Generator[tuple[Instant, MarketEvent]]:
    day = gday.day
    files = [] if recordings_root is None else dataplane_recording_files(recordings_root, day)
    bar_index = {
        inst: i
        for i, inst in enumerate(needs.instruments)
        if sources.get(f"bars {inst}") == "recording"
    }
    symbols = {s for s in needs.skylit_symbols if sources.get(_skylit_key(needs, s)) == "recording"}
    streams: list[Iterator[_Keyed]] = []
    if files:
        streams.append(_reorder(_recorded_events(gday, needs, files, bar_index, symbols)))
    for i, inst in enumerate(needs.instruments):
        if sources.get(f"bars {inst}") == "cache":
            streams.append(_keyed(_cache_bar_events(cache, inst, i, day, lag_ns)))
    for i, sym in enumerate(needs.skylit_symbols, start=1):
        if sources.get(_skylit_key(needs, sym)) == "cache":
            view_ids = cache_view_ids(needs)
            streams.append(_keyed(_cache_snap_events(cache, needs, view_ids, sym, i, day, lag_ns)))
    if needs.quotes and recordings_root is not None:
        qpath = dataplane_quotes_path(recordings_root, day)
        streams.append(_reorder(_quote_events(qpath, needs)))
    try:
        for key, event in heapq.merge(*streams, key=lambda x: x[0]):
            yield key[0], event
    finally:
        for s in streams:
            close = getattr(s, "close", None)
            if close is not None:
                close()


def day_events(
    gday: GlobexDay,
    needs: ReplayNeeds,
    sources: Mapping[str, str],
    *,
    cache: DataCache,
    recordings_root: Path | None,
    lag_ns: int,
    cache_view_ids: CacheViewIds = _default_view_ids,
    s3: S3Recordings | None = None,
    scratch: Path | None = None,
) -> Generator[tuple[Instant, MarketEvent]]:
    """One Globex day's events in receipt order, produced lazily (bounded memory).

    The recording and quote files are streamed through a :data:`REORDER_NS` buffer
    and merged with the Data_Cache inputs of the needs ``sources`` assigns to it. A
    :data:`RECORDING_S3` day is downloaded into ``<scratch>/recordings/dataplane/``
    when its first event is asked for, scanned again (a need the file lacks falls
    back to the Data_Cache) and deleted after its last event.
    """
    if s3 is None or scratch is None or RECORDING_S3 not in sources.values():
        yield from _local_events(
            gday,
            needs,
            sources,
            cache=cache,
            recordings_root=recordings_root,
            lag_ns=lag_ns,
            cache_view_ids=cache_view_ids,
        )
        return
    folder = scratch / RECORDINGS_DIR_NAME / DATAPLANE_DIR_NAME
    try:
        s3.fetch(gday.day, scratch)
        real = scan_day(
            gday, needs, cache=cache, recordings_root=scratch, cache_view_ids=cache_view_ids
        )
        yield from _local_events(
            gday,
            needs,
            real,
            cache=cache,
            recordings_root=scratch,
            lag_ns=lag_ns,
            cache_view_ids=cache_view_ids,
        )
    finally:
        for p in folder.glob(f"{gday.day.isoformat()}.*") if folder.is_dir() else ():
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------- coverage


@dataclass(frozen=True, slots=True)
class DayCoverage:
    day: date
    exchange_session: bool
    sources: Mapping[str, str]

    @property
    def missing(self) -> list[str]:
        return [k for k, v in self.sources.items() if v == "missing"]


@dataclass(frozen=True, slots=True)
class Coverage:
    needs: ReplayNeeds
    days: tuple[DayCoverage, ...]
    warnings: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return all(not d.missing for d in self.days if d.exchange_session)

    def to_json(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "needs": self.needs.kinds(),
            "layout": self.needs.layout,
            "warnings": list(self.warnings),
            "days": [
                {
                    "day": d.day.isoformat(),
                    "exchange_session": d.exchange_session,
                    "sources": dict(d.sources),
                    "missing": d.missing,
                }
                for d in self.days
            ],
        }

    def lines(self) -> list[str]:
        out = [f"needs: {', '.join(self.needs.kinds()) or 'nothing'}"]
        out.extend(collapsed_day_lines(self.days))
        out += [f"warning: {w}" for w in self.warnings]
        out.append("coverage: complete" if self.complete else "coverage: INCOMPLETE")
        return out


def collapsed_day_lines(days: Sequence[DayCoverage]) -> list[str]:
    """One line per run of days that share a source map.

    A year of ``bars MNQ=missing`` is one range, not one line per session.
    ``coverage.json`` still lists every day.
    """
    out: list[str] = []
    i = 0
    count = len(days)
    while i < count:
        day = days[i]
        if not day.exchange_session:
            out.append(f"{day.day}: no exchange session")
            i += 1
            continue
        signature = tuple(day.sources.items())
        j = i + 1
        while (
            j < count
            and days[j].exchange_session
            and tuple(days[j].sources.items()) == signature
        ):
            j += 1
        parts = "; ".join(f"{key}={value}" for key, value in day.sources.items())
        if j == i + 1:
            out.append(f"{day.day}: {parts}")
        else:
            last = days[j - 1].day
            out.append(f"{day.day}..{last} ({j - i} sessions): {parts}")
        i = j
    return out


def globex_days(first: date, last: date, calendars: Calendars) -> list[GlobexDay]:
    cal = calendars.exchange.sessions
    out: list[GlobexDay] = []
    d = first
    while d <= last:
        if cal.covers(d) and cal.is_session(d):
            out.append(globex_day(d, cal))
        d += timedelta(days=1)
    return out


def coverage(
    needs: ReplayNeeds,
    days: Sequence[GlobexDay],
    *,
    cache: DataCache,
    recordings_root: Path | None,
    lag_ns: int,
    warnings: Sequence[str] = (),
    cache_view_ids: CacheViewIds = _default_view_ids,
    s3: S3Recordings | None = None,
    progress: LogWriter | None = None,
) -> tuple[Coverage, list[_Day]]:
    """Each day's sources (:func:`scan_day`) and the days to replay (events made lazily)."""
    built: list[_Day] = []
    total = len(days)
    prev_sig: tuple[tuple[str, str], ...] | None = None
    run_start: date | None = None
    run_end: date | None = None
    run_shown = ""
    run_count = 0
    for i, g in enumerate(days, start=1):
        sources = scan_day(
            g,
            needs,
            cache=cache,
            recordings_root=recordings_root,
            cache_view_ids=cache_view_ids,
            s3=s3,
        )
        if progress is not None:
            sig = tuple(sources.items())
            shown = ", ".join(f"{key}={value}" for key, value in sources.items()) or "none"
            if prev_sig is None or sig != prev_sig:
                if run_count > 1 and run_start is not None and run_end is not None:
                    progress.echo(
                        f"coverage {i - 1}/{total} {run_start}..{run_end} "
                        f"({run_count} sessions): {run_shown}"
                    )
                progress.echo(f"coverage {i}/{total} {g.day} {shown}")
                prev_sig, run_start, run_shown, run_count = sig, g.day, shown, 1
            else:
                run_count += 1
            run_end = g.day
        built.append(_Day(g, sources, needs, recordings_root, lag_ns, cache_view_ids, s3))
    if progress is not None and run_count > 1 and run_start is not None and run_end is not None:
        progress.echo(
            f"coverage {total}/{total} {run_start}..{run_end} ({run_count} sessions): {run_shown}"
        )
    cov = Coverage(
        needs,
        tuple(DayCoverage(b.gday.day, b.gday.exchange_session, b.sources) for b in built),
        (*warnings, *([] if s3 is None else s3.errors)),
    )
    return cov, built


# ---------------------------------------------------------------- the data plane


class ReplayDataPlane:
    """A :class:`~fse.live.host.host.HostDataPlane` that plays prepared events in time."""

    def __init__(
        self, clock: VirtualClock, bar_root: Path, *, defer_bar_files: bool = True
    ) -> None:
        self._clock = clock
        self._store = LiveBarStore(bar_root)
        self._defer_bar_files = defer_bar_files
        self.bar_store = self._store.view()
        self._script: Iterable[tuple[Instant, MarketEvent]] = ()
        self._recorded: list[tuple[Bar, Instant]] = []
        self.on_error: Callable[[str, str], None] | None = None
        """``(kind, detail)`` when producing the events fails (e.g. an S3 download);
        unset, the error is raised."""

    def load(self, events: Iterable[tuple[Instant, MarketEvent]]) -> None:
        """The events to play, in receipt order; consumed lazily by :meth:`run`."""
        self._script = events

    async def run(self, submit: Callable[[MarketEvent], None]) -> None:
        script = iter(self._script)
        while True:
            try:
                item = next(script, None)
            except Exception as exc:
                if self.on_error is None:
                    raise
                self.on_error("replay_input_failed", f"{type(exc).__name__}: {exc}")
                break
            if item is None:
                break
            t, event = item
            delay = t - self._clock.now()
            if delay > 0:
                await self._clock.sleep(delay)
            submit(event)
        await asyncio.get_running_loop().create_future()  # idle until cancelled

    def record(self, event: MarketEvent) -> None:
        if isinstance(event, BarsEvent):
            self._recorded.extend((b, event.received_ns) for b in event.bars)

    def store_bars(self, bars: Sequence[Bar], received_ns: Instant) -> None:
        self._store.add(bars, received_ns, flush=not self._defer_bar_files)

    def recorded_bars(self, day: GlobexDay) -> list[tuple[Bar, Instant]]:
        return [(b, r) for b, r in self._recorded if day.open_ns <= b.open_ns < day.rotate_ns]

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None:
        return None

    def rotate(self, closing: GlobexDay, opening: GlobexDay | None) -> None:
        self._store.forget(closing.day)

    def close(self) -> None:
        self._store.flush()


# ---------------------------------------------------------------- history and sandbox


def build_history(
    src: DataCache, dst_root: Path, instruments: Sequence[str], first: date, sessions: int
) -> dict[str, int]:
    """1m bars of ``instruments`` for the ``sessions`` cached sessions before ``first``.

    Writes ``warmup.json`` (oldest session first) when any session was copied, so
    the rules plugin can replay those bars with entries suppressed.
    """
    counts: dict[str, int] = {}
    copied: dict[str, list[str]] = {}
    with DataCache(dst_root) as dst:
        for inst in instruments:
            found: list[date] = []
            d = first - timedelta(days=1)
            stop = first - timedelta(days=max(14, 3 * sessions))
            while len(found) < sessions and d > stop:
                bars = _cache_bars(src, inst, d)
                if bars:
                    dst.bars.write_session(
                        inst, 60, d, bars, contract=bars[0].contract, source=bars[0].source
                    )
                    found.append(d)
                d -= timedelta(days=1)
            counts[inst] = len(found)
            copied[inst] = [item.isoformat() for item in reversed(found)]
    if any(copied.values()):
        (dst_root / "warmup.json").write_text(json.dumps(copied), encoding="utf-8")
    return counts


@contextlib.contextmanager
def sandbox_home(home: Path) -> Iterator[Path]:
    """``HOME`` (and ``~``) inside the run folder for the duration of a replay."""
    home.mkdir(parents=True, exist_ok=True)
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


def absolute_paths(config: Mapping[str, Any], where: str) -> list[str]:
    """Absolute or ``~`` paths in a plugin ``config`` (they escape the run folder)."""
    out: list[str] = []

    def walk(v: Any, path: str) -> None:
        if isinstance(v, Mapping):
            for k, x in v.items():
                walk(x, f"{path}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")
        elif isinstance(v, str) and v.startswith("/"):
            out.append(f"{where}{path} is an absolute path ({v}); it is used as given")

    walk(config, "")
    return out


# ---------------------------------------------------------------- the run


@dataclass(slots=True)
class ReplayResult:
    exit_code: int
    faults: dict[str, str]
    trades: list[tuple[str, Trade]]
    coverage: Coverage
    plugin_ids: tuple[str, ...]


def config_digest(text: str, plugin_ids: Sequence[str]) -> str:
    h = hashlib.sha256(text.encode("utf-8"))
    h.update(json.dumps(sorted(plugin_ids)).encode())
    return h.hexdigest()


def run_replay(
    cfg: RunnerConfig,
    classes: Mapping[str, type[StrategyPlugin]],
    *,
    first: date,
    last: date,
    work: Path,
    source_cache: Path,
    recordings_root: Path | None,
    calendar_dir: Path,
    project_dir: Path | None,
    writer: LogWriter,
    history: int = DEFAULT_HISTORY,
    lag_s: int = DEFAULT_LAG_S,
    allow_gaps: bool = False,
    coverage_only: bool = False,
    cache_view_ids: CacheViewIds = _default_view_ids,
    recordings_s3: str | S3Recordings | None = None,
    progress: LogWriter | None = None,
    coalesce_state: bool = True,
    defer_bar_files: bool = True,
) -> ReplayResult:
    """Replay ``cfg``'s enabled plugins over ``first..last`` (see the module notes).

    ``recordings_s3`` (``s3://bucket[/prefix]``) serves days missing under
    ``recordings_root``: each is downloaded into ``<work>/recordings-s3/`` when its
    replay starts and deleted after it.

    ``lag_s`` is added to the virtual receipt time. The clock jumps; it does not
    sleep. ``coalesce_state`` skips the per-bar ``plugin.json`` encode (the bar
    ring changes every minute) and writes the final book at shutdown.
    ``defer_bar_files`` rewrites each day's bar parquet once per day. Hook
    calls, fills and that final book match the per-bar path.
    """
    s3 = S3Recordings(recordings_s3) if isinstance(recordings_s3, str) else recordings_s3
    calendars = load_calendars(calendar_dir, first - timedelta(days=60), last + timedelta(10))
    days = globex_days(first, last, calendars)
    if not days:
        raise ValueError(f"no exchange session between {first} and {last}")
    start = days[0].open_ns
    until = days[-1].rotate_ns + 10 * 60 * NS_PER_SECOND
    clock = VirtualClock(start)
    plane = ReplayDataPlane(clock, work / "bars", defer_bar_files=defer_bar_files)
    trades: list[tuple[str, Trade]] = []
    lag_ns = lag_s * NS_PER_SECOND
    with sandbox_home(work / "home"), DataCache(source_cache) as src:
        host = PluginHost(
            cfg,
            classes,
            dataplane=plane,
            clock=clock,
            writer=writer,
            paths=HostPaths(
                state_root=work / "live-state",
                run_dir=work / "run",
                cache_dir=work / "cache",
                calendar_dir=calendar_dir,
                project_dir=project_dir,
                recordings_root=work,
            ),
            calendars=calendars,
            resources=HostResources(env=EnvView({}, {})),
            calendar_loader=lambda a, b: calendars,
            blocking=InlineBlockingRunner(),
            trade_sink=lambda pid, t: trades.append((pid, t)),
            write_marks=False,
            coalesce_state=coalesce_state,
        )
        ids = host.plugin_ids
        needs = replay_needs(cfg, {pid: host.needs_of(pid) for pid in ids})
        warnings = [
            w for e in cfg.enabled_plugins for w in absolute_paths(e.config, f"{e.id}.config")
        ]
        cov, built = coverage(
            needs,
            days,
            cache=src,
            recordings_root=recordings_root,
            lag_ns=lag_ns,
            warnings=warnings,
            cache_view_ids=cache_view_ids,
            s3=s3,
            progress=progress,
        )
        if coverage_only or (not cov.complete and not allow_gaps):
            return ReplayResult(0 if coverage_only else 3, {}, [], cov, ids)
        build_history(src, work / "cache", needs.instruments, first, history)
    plane.on_error = host.fail

    async def go() -> int:
        return await clock.run(host.run(until_ns=until))

    with sandbox_home(work / "home"), DataCache(source_cache) as src:
        script = _script(
            built, src, work / "recordings-s3", start, until, progress=progress, trades=trades
        )
        plane.load(script)
        try:
            code = asyncio.run(go())
        finally:
            script.close()  # deletes a downloaded day the run stopped inside
    return ReplayResult(code, dict(host.faults()), trades, cov, ids)


def _covered_bar_sources(day: _Day) -> str:
    """Bar sources coverage already called present. Empty when every bar need is missing."""
    parts = [
        f"{key}={source}"
        for key, source in day.sources.items()
        if key.startswith("bars ") and source not in {"missing", "no session"}
    ]
    return ", ".join(parts)


def _script(
    days: Sequence[_Day],
    cache: DataCache,
    scratch: Path,
    start: Instant,
    until: Instant,
    *,
    progress: LogWriter | None = None,
    trades: list[tuple[str, Trade]] | None = None,
) -> Generator[tuple[Instant, MarketEvent]]:
    """Every day's events in order, made lazily; only ``start <= t <= until``.

    The day line is logged before that day's events are built, so an S3 download
    is not a silent gap, and again in ``finally`` so the last day still reports
    when the consumer closes the generator. A session coverage called complete
    that then yields no bars is a fault: the line starts with ``error:`` so the
    job log shows it, and the run exits 1 instead of publishing a hole.
    """
    total = len(days)
    started = monotonic()
    seen = 0
    for i, day in enumerate(days, start=1):
        if progress is not None:
            progress.echo(f"replay {i}/{total} {day.gday.day} starting")
        day_bars = 0
        try:
            with contextlib.closing(day.events(cache, scratch)) as events:
                for t, event in events:
                    if isinstance(event, BarsEvent):
                        n = len(event.bars)
                        day_bars += n
                        seen += n
                    if start <= t <= until:
                        yield t, event
        except Exception as exc:
            covered = _covered_bar_sources(day)
            if day_bars == 0 and day.gday.exchange_session and covered and progress is not None:
                progress.echo(
                    f"error: {day.gday.day} replayed 0 bars but coverage said {covered}: "
                    f"{type(exc).__name__}: {exc}"
                )
            raise
        else:
            covered = _covered_bar_sources(day)
            if day_bars == 0 and day.gday.exchange_session and covered:
                message = f"error: {day.gday.day} replayed 0 bars but coverage said {covered}"
                if progress is not None:
                    progress.echo(message)
                raise RuntimeError(message)
        finally:
            if progress is not None:
                elapsed = max(monotonic() - started, 1e-9)
                ntrades = 0 if trades is None else len(trades)
                progress.echo(
                    f"replay {i}/{total} {day.gday.day} bars={day_bars} "
                    f"total_bars={seen} trades={ntrades} {seen / elapsed:.0f} bars/s"
                )
