"""The puller: one ``fse pull`` run from plan to coverage report (design §3-4).

Requirements 3.1-3.4, 3.9, 3.11, 3.14-3.15, 4.1-4.3 and 4.12. :func:`run_pull`
wires the planner, the estimate, the Data_Cache, the Bar_Source, the VIX and
dark-pool fetches and the coverage report together:

1. ``GET /v1/symbols`` (free). A failed call stops the pull before any
   Replay_Request (:class:`SymbolsUnavailableError`, exit 4, Req 3.14).
2. :func:`~fse.data.planner.plan_pull` validates the range and plans every
   Replay_Request, newest session first. A Cache_Window that had not ended
   when the pull started gets no request (:func:`drop_unfinished_windows`):
   storing it now would store a partial window as final, and the next pull
   fetches it once it has ended.
3. :func:`~fse.data.estimate.announce_pull` prints the estimate and applies
   the size limit before the first Replay_Request (Req 3.1-3.2, exit 2).
4. Inside :func:`~fse.data.coverage.coverage_report` (written from a
   ``finally`` block, Req 3.11), in this order:

   - the Atlas probe of each futures instrument on the first and last session
     (Req 4.2-4.3), with the OQ10 timestamp check printed, because the
     coverage report has no field for it. A whole-bar shift stops the pull
     (:class:`~fse.data.bar_source.BarTimestampError`, exit 4);
   - dark-pool prints when tickers are configured (Req 4.12), over the
     pull's sessions plus the :data:`DARK_POOL_LOOKBACK_SESSIONS` sessions
     before the first one, because the ``dark_pool_confluence`` Gate reads
     the trailing 5 sessions. Errors on those earlier sessions are printed;
     the report covers the pull's own sessions;
   - VIX daily values for sessions whose open, close or prior close is not
     stored yet (Req 4.9);
   - futures bars for each instrument and session not yet stored, skipping
     sessions whose bar range had not ended at pull start (Req 4.1, 4.5);
   - the heatmap Cache_Windows, newest session first (D11).

   The probe, bars, VIX and dark pool are a few dozen requests and run first,
   so an interrupted multi-hour heatmap pull still leaves them stored.

Heatmap windows follow the window write protocol (Req 3.9-3.10): every key a
request will fill is marked ``incomplete`` before the window's first
Replay_Request; the window's requests then run concurrently (the client caps
replays in flight and paces every send); then each key is written ``complete``
or ``no_data``. A key whose request failed for any reason other than
``404 no_data`` stays ``incomplete`` and the pull continues (Req 2.8). A
Data_Cache write error stops the pull before the next window's first
Replay_Request (Req 3.15, exit 4).

What a window stores:

- ``/v1/historical/range`` frames with ``asOf`` in ``[window start, window
  end)``. The range ``to`` is inclusive, so a frame exactly at the window end
  belongs to the next window.
- ``/v1/historical`` answers (the latest Snapshot at or before the sample
  instant), as returned. ``--label-sample-minutes`` samples follow the range
  frames, so on an equal ``asOf`` the labelled Snapshot is the one the
  storage-interval filter keeps. Exact duplicates are stored once.
- A symbol missing from an answer, like ``404 no_data``, adds nothing; a
  missing ``/v1/historical`` sample is recorded as a ``no_data`` gap of one
  sample interval (label samples are not, since range frames fill the window).

An Operator interrupt (asyncio cancels the pull task on Ctrl-C) marks the
coverage report ``interrupted``, leaves every unfinished window
``incomplete`` and propagates; ``asyncio.run`` then raises
``KeyboardInterrupt`` and the CLI exits 130.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final, cast

from fse.calendars import RollCalendar
from fse.data.aux_stores import VixDailyRecord
from fse.data.bar_source import (
    AtlasBars,
    BarSource,
    ProbeResult,
    SessionBars,
    pull_bars,
    session_bar_range,
)
from fse.data.cache import CacheWindowKey, DataCache, check_storage_interval
from fse.data.cache_io import CacheWriteError, check_component
from fse.data.catalog import FINAL_STATUSES
from fse.data.coverage import (
    OUTPUT_DIR_LABEL,
    CoverageCollector,
    CoverageFiles,
    CoverageRequest,
    coverage_paths,
    coverage_report,
)
from fse.data.darkpool import TickerFetch, fetch_dark_pool
from fse.data.estimate import (
    DEFAULT_HISTORICAL_CREDITS,
    DEFAULT_RANGE_CREDITS,
    DEFAULT_SIZE_LIMIT_GB,
    EstimateConfig,
    PullEstimate,
    announce_pull,
    check_size_limit,
)
from fse.data.path_guard import check_output_dir
from fse.data.planner import (
    PullPlan,
    PullSpec,
    ReplayRequest,
    SessionPlan,
    WindowPlan,
    plan_pull,
)
from fse.data.vix import VIX_INSTRUMENT, VixPull, prior_session, pull_vix
from fse.engine.types import Snapshot
from fse.logio import LogWriter, canonical_json
from fse.logio.canonical_json import JsonValue
from fse.projectx.bars import ProjectXBars
from fse.skylit.client import Failed, SkylitClient
from fse.skylit.models import HeatmapResponse, RangeResponse, SymbolCatalog
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar

__all__ = [
    "BAR_INTERVALS_S",
    "DARK_POOL_LOOKBACK_SESSIONS",
    "DEFAULT_BAR_INTERVAL_S",
    "DEFAULT_INSTRUMENTS",
    "EXIT_DATA_IO",
    "HeatmapTally",
    "PullContext",
    "PullOptions",
    "PullSummary",
    "SymbolsUnavailableError",
    "drop_unfinished_windows",
    "listed_symbols",
    "run_pull",
]

# Design "Exit codes": 4 = data or I/O failure, which includes a /v1/symbols failure.
EXIT_DATA_IO: Final = 4

DEFAULT_INSTRUMENTS: Final[tuple[str, ...]] = ("ES", "NQ")
DEFAULT_BAR_INTERVAL_S: Final = 60
BAR_INTERVALS_S: Final[tuple[int, ...]] = tuple(s for s in range(1, 61) if 60 % s == 0)
"""Bar sizes that tile a minute: 60 s from Atlas or ProjectX, the rest from ProjectX (OQ9)."""

DARK_POOL_LOOKBACK_SESSIONS: Final = 5
"""Sessions before the pull's first session whose prints the Gate's trailing window reads."""
_LOOKBACK_SCAN_DAYS: Final = 21  # holds 5 sessions across any holiday cluster

_ONE_DAY: Final = timedelta(days=1)


class SymbolsUnavailableError(Exception):
    """``GET /v1/symbols`` failed; the pull stopped before any Replay_Request (exit 4)."""

    exit_code: ClassVar[int] = EXIT_DATA_IO


# ---------------------------------------------------------------- options


def _names(label: str, values: object, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ValueError(f"{label} must be a tuple naming at least one entry, got {values!r}")
    for value in values:
        check_component(label, value)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not repeat an entry: {list(values)}")
    return values


@dataclass(frozen=True, slots=True)
class PullOptions:
    """Everything ``fse pull`` was asked for besides the calendars and paths.

    Defaults are the requirement defaults. ``dark_pool_tickers`` is empty when
    dark-pool prints are not fetched. ``storage_interval_s`` is ``None`` to
    store every Snapshot.
    """

    spec: PullSpec
    instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS
    bar_interval_s: int = DEFAULT_BAR_INTERVAL_S
    storage_interval_s: int | None = None
    size_limit_gb: float = DEFAULT_SIZE_LIMIT_GB
    range_credits: int = DEFAULT_RANGE_CREDITS
    historical_credits: int = DEFAULT_HISTORICAL_CREDITS
    dark_pool_tickers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _names("instrument", self.instruments, allow_empty=False)
        _names("dark-pool ticker", self.dark_pool_tickers, allow_empty=True)
        if isinstance(self.bar_interval_s, bool) or self.bar_interval_s not in BAR_INTERVALS_S:
            raise ValueError(
                f"bar interval must be one of {', '.join(map(str, BAR_INTERVALS_S))} seconds, "
                f"got {self.bar_interval_s!r}"
            )
        if self.storage_interval_s is not None:
            check_storage_interval(self.storage_interval_s)
        check_size_limit(self.size_limit_gb)
        self.estimate_config()  # checks the credits

    def estimate_config(self) -> EstimateConfig:
        return EstimateConfig(
            range_credits=self.range_credits,
            historical_credits=self.historical_credits,
            storage_interval_s=self.storage_interval_s,
        )

    def to_json(self, calendar: SessionCalendar) -> dict[str, JsonValue]:
        """The pull's arguments for the catalog ``pulls`` row."""
        spec = self.spec
        times = calendar.times
        return {
            "start": spec.start.isoformat(),
            "end": spec.end.isoformat(),
            "symbols": list(spec.symbols),
            "metrics": list(spec.metrics),
            "view": canonical_json.to_jsonable(spec.view.to_json_obj()),
            "view_id": spec.view.view_id(),
            "pull_window": {
                "start": times.pull_start.strftime("%H:%M"),
                "end": times.pull_end.strftime("%H:%M"),
            },
            "sample_interval_minutes": spec.sample_interval_minutes,
            "label_sample_minutes": spec.label_sample_minutes,
            "storage_interval_s": self.storage_interval_s,
            "size_limit_gb": float(self.size_limit_gb),
            "range_credits": self.range_credits,
            "historical_credits": self.historical_credits,
            "instruments": list(self.instruments),
            "bar_interval_s": self.bar_interval_s,
            "dark_pool_tickers": list(self.dark_pool_tickers),
        }


@dataclass(frozen=True, slots=True)
class PullContext:
    """What the pull runs with: clients, stores, calendars, output and the start instant.

    ``out_dir`` receives ``coverage_{pull_id}.md`` and ``.json``. ``projectx``
    is the alternate bar source, ``None`` when not configured. ``interactive``
    and ``read_line`` drive the size prompt (default: detect a terminal, read
    stdin); ``clock`` stamps the report's end.
    """

    client: SkylitClient
    cache: DataCache
    calendar: SessionCalendar
    roll: RollCalendar
    writer: LogWriter
    out_dir: Path
    pull_id: str
    started_ns: Instant
    projectx: ProjectXBars | None = None
    interactive: bool | None = None
    read_line: Callable[[], str] = input
    clock: Callable[[], Instant] = time.time_ns


# ---------------------------------------------------------------- results


@dataclass(slots=True)
class HeatmapTally:
    """Heatmap Replay_Requests and Cache_Windows of one pull (one window = symbol and metric)."""

    requests: int = 0
    failed_requests: int = 0
    last_failure: str | None = None
    complete: int = 0
    no_data: int = 0
    incomplete: int = 0
    unfinished: int = 0  # windows that had not ended at pull start


@dataclass(frozen=True, slots=True)
class PullSummary:
    """What :func:`run_pull` did, for the console and for tests."""

    pull_id: str
    plan: PullPlan
    estimate: PullEstimate
    coverage: CoverageFiles
    heatmaps: HeatmapTally
    probes: Mapping[str, ProbeResult]
    bars: Mapping[str, tuple[SessionBars, ...]]
    vix: VixPull | None
    dark_pool: tuple[TickerFetch, ...]


# ---------------------------------------------------------------- planning helpers


async def listed_symbols(client: SkylitClient) -> Mapping[str, date]:
    """``GET /v1/symbols`` as symbol to first history date (Req 3.5, 3.14).

    A listed symbol whose history Skylit could not read maps to ``None``, so
    the planner names it ("no first history date") with the other problems.
    """
    result = await client.symbols()
    if isinstance(result, Failed):
        raise SymbolsUnavailableError(
            f"GET /v1/symbols failed ({result.cause}); the pull stopped before the first "
            "Replay_Request"
        )
    return _first_history(result)


def _first_history(catalog: SymbolCatalog) -> Mapping[str, date]:
    listed: dict[str, date | None] = {
        info.symbol: None if info.history is None else info.history.first
        for info in catalog.symbols
    }
    # The planner reports a non-date value as a problem before it reads any.
    return cast(Mapping[str, date], MappingProxyType(listed))


def drop_unfinished_windows(plan: PullPlan) -> tuple[PullPlan, int]:
    """``plan`` with no request for a Cache_Window that ends after the pull started.

    Returns the new plan and the number of (symbol, metric) windows dropped.
    Such a window is neither served nor fetched, so the coverage report lists
    it as absent and the next pull plans it again.
    """
    dropped = 0
    sessions: list[SessionPlan] = []
    for session in plan.sessions:
        windows: list[WindowPlan] = []
        for window in session.windows:
            if window.fetch and window.end_ns > plan.started_ns:
                dropped += len(window.fetch)
                window = replace(window, fetch=(), requests=())
            windows.append(window)
        sessions.append(replace(session, windows=tuple(windows)))
    return replace(plan, sessions=tuple(sessions)), dropped


def _coverage_request(
    options: PullOptions, plan: PullPlan, listed: Mapping[str, date], ctx: PullContext
) -> CoverageRequest:
    spec = options.spec
    return CoverageRequest(
        pull_id=ctx.pull_id,
        started_at=ctx.started_ns,
        first=spec.start,
        last=spec.end,
        sessions=tuple(s.session for s in reversed(plan.sessions)),
        symbols=spec.symbols,
        view=spec.view,
        metrics=spec.metrics,
        first_history={s: listed[s] for s in spec.symbols},
        instruments=options.instruments,
        bar_interval_s=options.bar_interval_s,
        vix_instrument=VIX_INSTRUMENT,
        vix_intraday=False,
        dark_pool_tickers=options.dark_pool_tickers,
    )


def _record_pull(cache: DataCache, pull_id: str, started_ns: Instant, args_json: str) -> None:
    try:
        cache.catalog.record_pull(pull_id, started_ns, args_json)
    except (sqlite3.Error, OSError) as exc:
        raise CacheWriteError(f"the pulls row of pull {pull_id}", exc) from exc


# ---------------------------------------------------------------- the pull


async def run_pull(options: PullOptions, ctx: PullContext) -> PullSummary:
    """Plan, announce and run one pull; write its coverage report.

    Raises (each with an ``exit_code``): ``PullPlanError`` and
    ``PullSizeLimitError`` (2) before any Replay_Request;
    :class:`SymbolsUnavailableError` (4); ``SkylitKeyMissingError`` and
    ``SkylitStoppedError`` (3); ``CacheWriteError`` and ``BarTimestampError``
    (4). An interrupt propagates as ``asyncio.CancelledError`` or
    ``KeyboardInterrupt`` after the report is written.
    """
    writer = ctx.writer
    target = check_output_dir(ctx.out_dir, label=OUTPUT_DIR_LABEL)
    listed = await listed_symbols(ctx.client)
    plan = plan_pull(
        options.spec,
        listed=listed,
        started_ns=ctx.started_ns,
        calendar=ctx.calendar,
        cache=ctx.cache,
    )
    plan, unfinished = drop_unfinished_windows(plan)
    if unfinished:
        writer.echo(
            f"{unfinished:,} Cache_Windows had not ended when the pull started; they get no "
            "request now and a later pull fetches them."
        )
    estimate = announce_pull(
        plan,
        writer=writer,
        config=options.estimate_config(),
        cache=ctx.cache,
        limit_gb=options.size_limit_gb,
        interactive=ctx.interactive,
        read_line=ctx.read_line,
    )
    args_json = canonical_json.dumps(options.to_json(ctx.calendar))
    _record_pull(ctx.cache, ctx.pull_id, ctx.started_ns, args_json)

    request = _coverage_request(options, plan, listed, ctx)
    files = coverage_paths(target, ctx.pull_id)
    puller = _Puller(options, ctx, plan, request)
    puller.tally.unfinished = unfinished
    try:
        with coverage_report(
            request,
            cache=ctx.cache,
            calendar=ctx.calendar,
            writer=writer,
            out_dir=target,
            roll=ctx.roll,
            clock=ctx.clock,
        ) as coverage:
            try:
                await puller.run(coverage)
            except asyncio.CancelledError:
                coverage.mark_stopped("interrupted", "Operator interrupt")
                raise
    finally:
        if files.json.exists():
            writer.echo(f"Coverage report: {files.markdown} (and {files.json.name})")
    return PullSummary(
        pull_id=ctx.pull_id,
        plan=plan,
        estimate=estimate,
        coverage=files,
        heatmaps=puller.tally,
        probes=MappingProxyType(puller.probes),
        bars=MappingProxyType(puller.bars),
        vix=puller.vix,
        dark_pool=puller.dark_pool,
    )


@dataclass(slots=True)
class _KeyResult:
    """What one Cache_Window key collected from the window's answers."""

    frames: list[Snapshot] = field(default_factory=list)
    labels: list[Snapshot] = field(default_factory=list)
    failed: bool = False

    def snapshots(self) -> list[Snapshot]:
        """Frames, then label samples, each exact duplicate once."""
        seen: set[Snapshot] = set()
        out: list[Snapshot] = []
        for snap in (*self.frames, *self.labels):
            if snap not in seen:
                seen.add(snap)
                out.append(snap)
        return out


type _Answer = RangeResponse | HeatmapResponse | Failed


class _Puller:
    """One pull's fetch steps, with what they did."""

    __slots__ = (
        "_atlas",
        "_ctx",
        "_options",
        "_plan",
        "_request",
        "_source",
        "bars",
        "dark_pool",
        "probes",
        "tally",
        "vix",
    )

    def __init__(
        self, options: PullOptions, ctx: PullContext, plan: PullPlan, request: CoverageRequest
    ) -> None:
        self._options = options
        self._ctx = ctx
        self._plan = plan
        self._request = request
        self._atlas = AtlasBars(ctx.client)
        self._source = BarSource(
            calendar=ctx.calendar, roll=ctx.roll, atlas=self._atlas, projectx=ctx.projectx
        )
        self.tally = HeatmapTally()
        self.probes: dict[str, ProbeResult] = {}
        self.bars: dict[str, tuple[SessionBars, ...]] = {}
        self.vix: VixPull | None = None
        self.dark_pool: tuple[TickerFetch, ...] = ()

    async def run(self, coverage: CoverageCollector) -> None:
        if self._request.sessions:
            await self._probe(coverage)
            await self._dark_pool(coverage)
            await self._vix()
            await self._bars(coverage)
        else:
            self._ctx.writer.echo("The range holds no exchange session; nothing to fetch.")
        await self._heatmaps(coverage)
        self._print_heatmap_totals()

    # ---------------------------------------------------------------- futures bars

    async def _probe(self, coverage: CoverageCollector) -> None:
        sessions = self._request.sessions
        first, last = sessions[0], sessions[-1]
        for instrument in self._options.instruments:
            found = await self._source.probe([instrument], first, last, coverage=coverage)
            result = found[instrument]
            self.probes[instrument] = result
            served = "served" if result.served else "not served"
            code = "" if result.error_code is None else f", last error {result.error_code}"
            check = (
                "not run (Atlas does not serve the instrument)"
                if result.timestamp_check is None
                else result.timestamp_check.describe()
            )
            self._ctx.writer.echo(
                f"Atlas probe {instrument}: {served} ({result.bars_first:,} bars on {first}, "
                f"{result.bars_last:,} on {last}{code}); OQ10 timestamp check: {check}"
            )

    async def _bars(self, coverage: CoverageCollector) -> None:
        ctx, interval = self._ctx, self._options.bar_interval_s
        for instrument in self._options.instruments:
            todo: list[date] = []
            cached = unfinished = 0
            for d in self._request.sessions:
                if session_bar_range(ctx.calendar, d, interval)[1] > ctx.started_ns:
                    unfinished += 1
                elif ctx.cache.bars.status(instrument, interval, d) in FINAL_STATUSES:
                    cached += 1
                else:
                    todo.append(d)
            source = self._source.source_for(instrument, interval)
            if todo and source == "projectx" and ctx.projectx is None:
                ctx.writer.echo(
                    f"Bars {instrument}: ProjectX is the bar source but is not configured "
                    "(PROJECTX_USERNAME or PROJECTX_API_KEY is blank); the sessions are "
                    "recorded with no_alternate_source."
                )
            results = (
                await pull_bars(
                    self._source,
                    ctx.cache.bars,
                    instrument,
                    todo,
                    interval_s=interval,
                    coverage=coverage,
                )
                if todo
                else ()
            )
            self.bars[instrument] = results
            failed = [r for r in results if r.error_code is not None]
            stored = sum(1 for r in results if r.usable)
            last = f", last error {failed[-1].error_code}" if failed else ""
            ctx.writer.echo(
                f"Bars {instrument} ({interval} s, source {source}): {len(todo):,} sessions "
                f"fetched, {stored:,} stored, {len(failed):,} failed{last}; {cached:,} served "
                f"from the Data_Cache; {unfinished:,} not finished at pull start."
            )

    # ---------------------------------------------------------------- VIX

    async def _vix(self) -> None:
        ctx = self._ctx
        stored = ctx.cache.vix.read_daily()
        todo = [d for d in self._request.sessions if self._vix_needed(d, stored)]
        if not todo:
            ctx.writer.echo("VIX daily values: every session served from the Data_Cache.")
            return
        result = await pull_vix(self._atlas, ctx.cache.vix, ctx.calendar, todo)
        self.vix = result
        failed = [s for s in result.fetched if s.error_code is not None]
        last = f", last error {failed[-1].error_code}" if failed else ""
        line = (
            f"VIX daily values: {len(todo):,} sessions requested, {len(result.written):,} "
            f"records written, {len(failed):,} sessions failed{last}; "
            f"{len(result.gaps):,} sessions still miss a VIX item."
        )
        if result.gaps:
            line += " `fse import-vix` can load them from a CSV."
        ctx.writer.echo(line)

    def _vix_needed(self, session: date, stored: Mapping[date, VixDailyRecord]) -> bool:
        record = stored.get(session)
        if record is None or record.open is None or record.close is None:
            return True
        prior = prior_session(self._ctx.calendar, session)
        if prior is None:
            return False
        before = stored.get(prior)
        return before is None or before.close is None

    # ---------------------------------------------------------------- dark pool

    def _lookback_sessions(self) -> tuple[date, ...]:
        calendar = self._ctx.calendar
        first = self._request.sessions[0]
        lo = max(calendar.first, first - timedelta(days=_LOOKBACK_SCAN_DAYS))
        if lo >= first:
            return ()
        return calendar.sessions(lo, first - _ONE_DAY)[-DARK_POOL_LOOKBACK_SESSIONS:]

    async def _dark_pool(self, coverage: CoverageCollector) -> None:
        if not self._request.dark_pool_tickers:
            return
        lookback = self._lookback_sessions()
        wider = replace(
            self._request,
            first=lookback[0] if lookback else self._request.first,
            sessions=lookback + self._request.sessions,
        )
        # The wider collector sees the lookback sessions; only the pull's own
        # sessions go to the report, which is built over those.
        collector = CoverageCollector(wider)
        self.dark_pool = await fetch_dark_pool(
            self._ctx.client, self._ctx.cache.darkpool, collector
        )
        own = frozenset(self._request.sessions)
        for fetch in self.dark_pool:
            earlier: list[str] = []
            for d, code in fetch.errors.items():
                if d in own:
                    coverage.record_dark_pool_error(fetch.ticker, (d,), code)
                else:
                    earlier.append(f"{d} ({code})")
            self._ctx.writer.echo(
                f"Dark pool {fetch.ticker}: {len(fetch.stored):,} sessions fetched, "
                f"{len(fetch.cached):,} served from the Data_Cache, {fetch.prints:,} prints "
                f"stored, {len(fetch.errors):,} sessions with an error; includes "
                f"{len(lookback)} sessions before {self._request.sessions[0]} for the "
                f"{DARK_POOL_LOOKBACK_SESSIONS}-session lookback."
            )
            if earlier:
                self._ctx.writer.echo(
                    f"Dark pool {fetch.ticker} lookback sessions with an error (not in the "
                    f"coverage report): {', '.join(earlier)}"
                )

    # ---------------------------------------------------------------- heatmaps

    async def _heatmaps(self, coverage: CoverageCollector) -> None:
        for session in self._plan.sessions:
            before = replace(self.tally)
            for window in session.windows:
                if window.requests:
                    await self._window(session, window, coverage)
            sent = self.tally.requests - before.requests
            if sent:
                stored = self.tally.complete - before.complete
                no_data = self.tally.no_data - before.no_data
                incomplete = self.tally.incomplete - before.incomplete
                self._ctx.writer.echo(
                    f"{session.session} ({session.endpoint}): {sent:,} Replay_Requests, "
                    f"{stored:,} Cache_Windows stored, {no_data:,} no_data, "
                    f"{incomplete:,} left incomplete."
                )

    async def _window(
        self, session: SessionPlan, window: WindowPlan, coverage: CoverageCollector
    ) -> None:
        cache = self._ctx.cache
        results: dict[CacheWindowKey, _KeyResult] = {}
        for request in window.requests:
            filled = request.keys()
            for key in filled:
                results.setdefault(key, _KeyResult())
        for key in window.fetch:
            if key in results:
                cache.mark_incomplete(key)  # step 1, before the window's first request
        answers = await _gather([self._send(r) for r in window.requests])
        self.tally.requests += len(window.requests)
        for request, answer in zip(window.requests, answers, strict=True):
            if request.endpoint == "range":
                self._apply_range(request, answer, results, coverage)
            else:
                self._apply_historical(request, answer, results, coverage)
        for key in window.fetch:
            got = results.get(key)
            if got is None:
                continue  # no request covers it: leave it as it is
            if got.failed:
                self.tally.incomplete += 1
                continue
            snaps = got.snapshots()
            cache.write_window(key, snaps, session.endpoint, view=self._options.spec.view)
            if snaps:
                self.tally.complete += 1
            else:
                self.tally.no_data += 1

    async def _send(self, request: ReplayRequest) -> _Answer:
        client, view = self._ctx.client, self._options.spec.view
        if request.endpoint == "range":
            return await client.historical_range(
                request.symbols,
                from_ns=request.window_start_ns,
                to_ns=request.window_end_ns,
                metric=request.metric,
                view=view,
            )
        assert request.at_ns is not None  # ReplayRequest checks it
        return await client.historical(
            request.symbols, at_ns=request.at_ns, metric=request.metric, view=view
        )

    def _fail(
        self, request: ReplayRequest, cause: str, results: Mapping[CacheWindowKey, _KeyResult]
    ) -> None:
        self.tally.failed_requests += 1
        self.tally.last_failure = f"{request.path} {cause}"
        filled = request.keys()
        for key in filled:
            results[key].failed = True

    def _apply_range(
        self,
        request: ReplayRequest,
        answer: _Answer,
        results: Mapping[CacheWindowKey, _KeyResult],
        coverage: CoverageCollector,
    ) -> None:
        if isinstance(answer, Failed):
            if not answer.no_data:
                self._fail(request, answer.cause, results)
            return
        if not isinstance(answer, RangeResponse) or answer.meta.metric != request.metric:
            self._fail(request, "answer for another metric", results)
            return
        by_symbol = answer.snapshots(view_id=request.view_id)
        lo, hi = request.window_start_ns, request.window_end_ns
        filled = request.keys()
        for key in filled:
            snaps = by_symbol.get(key.symbol)
            if snaps is None:
                continue  # not in the answer: no data for this symbol
            coverage.record_resolution(key.symbol, key.metric, key.session, answer.meta.resolution)
            results[key].frames.extend(s for s in snaps if lo <= s.as_of_ns < hi)

    def _apply_historical(
        self,
        request: ReplayRequest,
        answer: _Answer,
        results: Mapping[CacheWindowKey, _KeyResult],
        coverage: CoverageCollector,
    ) -> None:
        filled = request.keys()
        if isinstance(answer, Failed):
            if not answer.no_data:
                self._fail(request, answer.cause, results)
            elif not request.label_sample:
                for key in filled:
                    self._sample_gap(key, request, coverage)
            return
        if not isinstance(answer, HeatmapResponse) or answer.meta.metric != request.metric:
            self._fail(request, "answer for another metric", results)
            return
        by_symbol: dict[str, Snapshot] = {}
        for board in answer.snapshots(view_id=request.view_id, source_endpoint="historical"):
            by_symbol.setdefault(board.symbol, board)
        for key in filled:
            snap = by_symbol.get(key.symbol)
            if request.label_sample:
                if snap is not None:
                    results[key].labels.append(snap)
            elif snap is None:
                self._sample_gap(key, request, coverage)
            else:
                coverage.record_resolution(
                    key.symbol, key.metric, key.session, answer.meta.resolution
                )
                results[key].frames.append(snap)

    def _sample_gap(
        self, key: CacheWindowKey, request: ReplayRequest, coverage: CoverageCollector
    ) -> None:
        """One ``/v1/historical`` sample without a Snapshot: a gap of one sample interval."""
        assert request.at_ns is not None
        step = self._options.spec.sample_interval_minutes * NS_PER_MINUTE
        end = min(request.at_ns + step, request.window_end_ns)
        coverage.record_no_data(key.symbol, key.metric, key.session, request.at_ns, end)

    def _print_heatmap_totals(self) -> None:
        t = self.tally
        failure = "" if t.last_failure is None else f", last failure {t.last_failure}"
        line = (
            f"Heatmaps: {t.requests:,} Replay_Requests sent, {t.failed_requests:,} failed"
            f"{failure}; {t.complete:,} Cache_Windows stored, {t.no_data:,} stored as no_data, "
            f"{t.incomplete:,} left incomplete, {t.unfinished:,} not ended at pull start."
        )
        if t.incomplete or t.unfinished:
            line += " Rerun the same command to fetch them."
        self._ctx.writer.echo(line)


async def _gather[T](coros: Sequence[Coroutine[Any, Any, T]]) -> list[T]:
    """Run ``coros`` concurrently; results in order. The first failure cancels the rest.

    A failure is raised as itself, not inside an exception group, so its
    ``exit_code`` reaches the CLI.
    """
    if len(coros) == 1:
        return [await coros[0]]
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(c) for c in coros]
    except BaseExceptionGroup as failures:
        raise _first_leaf(failures) from None
    return [task.result() for task in tasks]


def _first_leaf(group: BaseExceptionGroup[BaseException]) -> BaseException:
    first = group.exceptions[0]
    return _first_leaf(first) if isinstance(first, BaseExceptionGroup) else first
