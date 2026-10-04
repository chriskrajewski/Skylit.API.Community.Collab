"""The Backtester: :func:`run_backtest` (design §18 "Backtester", "Decision loop", Req 18).

A run, in this order:

1. **Validate before acting** (Req 18.2, 4.16). The date range must not end
   before it starts, the calendar files must load and cover it, and it must
   hold at least one exchange session. A failure raises
   :class:`BacktestInputError` (or :class:`~fse.calendars.CalendarError`),
   both exit 2, before any Decision_Time and before any output file.
2. **Check the cache** (catalog reads only, :func:`check_sessions`). Per
   session: the Cache_Windows of every configured symbol and metric over the
   session's Pull_Window, and the 1-minute bars of every configured
   instrument. A session with no stored Snapshot for a configured symbol and
   metric, or no bars for a configured instrument, is skipped and recorded in
   the Run_Manifest with the missing data types (Req 18.3).
3. **Offline mode** (Req 1.8, 3.12-3.13). This module never imports the
   Skylit_Client, so no run builds one, reads ``SKYLIT_API_KEY`` or opens a
   connection. With ``offline=True`` every requested session with absent or
   incomplete Cache_Windows is printed (symbol, metric and count) before the
   first Decision_Time.
4. **Run** inside :func:`~fse.backtest.manifest.run_manifest`, so a completed,
   failed or interrupted run leaves one Run_Manifest (Req 18.4). Sessions run
   in date order; each is loaded alone into :class:`~fse.pit.asof.AsOfIndex`
   objects (:class:`~fse.pit.market_view.HistoricalInputs`). With
   ``workers > 1`` the next sessions are loaded on worker threads while the
   current one runs; results are consumed in session order, so the output
   does not depend on ``workers``.

**Per Decision_Time** ``t`` (design "Decision loop"), every 1-minute bar that
closed in ``(t_prev, t]`` goes through the bar phase, in close-time order with
the bars of one interval together (instruments in name order):

1. the Fill_Simulator (``fse.sim.fills.on_bar``) fills working orders;
2. the Account_Simulator books each fill, then checks the Maximum and Daily
   Loss Limits on the bar's worst prices. A liquidation closes every open
   trade at its instrument's worst price on that bar and cancels every
   working order (Req 15.8-15.12);
3. the Risk_Manager counts every fill (:meth:`Engine.count_fill`);
4. the Order_Planner applies the fills and moves stops at the bar close
   (:meth:`Engine.update_stops`), then the internal daily loss stop is
   checked at the 1-minute close (:meth:`Engine.check_loss_stop`), the order
   the engine's bar-phase hooks document;
5. the Tap tracker and Chart_Feature_Builder consume the bar
   (:meth:`Engine.consume_bar`).

Then :meth:`Engine.step` runs with the bar-phase events, its intents go to the
Fill_Simulator, and its payload becomes one decision-log line (Req 18.6).

**Intent routing.** ``PlaceBracket`` goes to ``SimBook.submit_bracket`` once
:meth:`AccountSim.check_order` accepts its entry; a refusal becomes an
:class:`~fse.engine.step.EntryRejected` event for the next step.
``ModifyOrder``, ``CancelOrder`` and ``SubmitExit`` go to ``SimBook.modify``,
``cancel`` and ``submit_exit``; one that refers to an order or trade the
Fill_Simulator no longer holds (it filled, or an account rule closed it)
changes nothing. Every order fills only on bars that open at or after its
placement or change (Req 5.5-5.6).

**Session end.** After the last Decision_Time, the bars up to the
Flat_Deadline run through the bar phase too. At the Flat_Deadline (at the
first Decision_Time at or after it on an early-close day) the
Account_Simulator closes every position at the open of the bar that opens at
the deadline, cancels every working order and applies the day-end rules. A
Combine_Attempt that passes or fails ends, and the next trading day starts a
new one (Req 15.9, 15.17); the one still running when the range ends is
recorded as incomplete (Req 15.18). The closing fills reach the
Risk_Manager, the Order_Planner and the next step only once that bar has
closed (Req 5.3): on an early-close day at the first Decision_Time at or after
the deadline plus 60 s, otherwise at the next session's first Decision_Time.

**Shadow trades and the Gate_Funnel** (design §19). A
:class:`~fse.backtest.shadow.ShadowBook` gets every bar after the account's
fills, those fills, and each Decision_Time's payload and planner context in
the log phase. It simulates the Shadow_Trades of rejected Setup_Keys on its
own books, closes them at the Flat_Deadline with the account, and gives each
Setup_Key one final status per session. Nothing it does reaches the engine
state, the account or the trade list (Req 19.9). The Gate_Funnel
(:func:`fse.analytics.funnel.gate_funnel`) is computed from those records and
the accepted trades.

**Report inputs.** Per evaluated session, the Tap count and the inter-decision
Tap count on the 1-minute bars (Req 18.11), and the King and Gatekeeper
agreement with Skylit's ``nodeType`` labels (Req 6.22-6.23)
(:mod:`fse.backtest.stats`). Before the run, the Holdout_Period is computed
from every calendar session the Data_Cache holds full data for (a Snapshot
for each configured symbol and metric, 1-minute bars for each instrument), at
``experiments.holdout_fraction`` (Req 22.1), so the report can say whether
the run includes a Holdout_Period session (Req 20.15). After the last
session, the 95% percentile bootstrap intervals of the Primary_Win_Rate and
expectancy in R (:func:`fse.analytics.bootstrap.bootstrap_intervals`) are
drawn from the accepted trades with the run's seed and
``reporting.bootstrap_resamples``; the Run_Manifest records the seed and
resample count, and ``report_inputs.json`` the bounds (Req 20.12).

**Monte Carlo inputs.** Each evaluated session gets a :class:`SessionOutcome`:
the trading day's net P&L, its intraday equity low (the lowest day P&L plus
unrealized P&L at the worst prices, never above 0), and whether the run's own
Combine_Attempt failed mid-session (design §21).

**Outputs** in the run directory, each through the Log_Writer (Req 1.9):
``decision_log.jsonl``, ``trades.csv`` and ``trades.json`` (the trade list,
MAE and MFE included), ``combine_attempts.json``, ``session_outcomes.json``,
``setups.json`` (every Setup_Key's final status, with its Shadow_Trade),
``shadow_trades.csv``, ``gate_funnel.json``, ``report_inputs.json`` and
``run_manifest.json``.

**Determinism** (Req 18.5). Nothing in the loop reads a clock or draws a
random number; the manifest clock is injected. Equal inputs and seed give
byte-identical decision logs, trade lists, Gate_Funnels and manifests, apart
from the manifest's ``started_at`` and ``ended_at``. A missing seed is drawn with
``secrets.randbits(63)`` and recorded (design §21); the seed feeds the seeded
analytics that read the run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
import time
from collections import deque
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Final, Literal

from fse.analytics.bootstrap import BootstrapIntervals, bootstrap_intervals
from fse.analytics.frontier import interval_to_jsonable
from fse.analytics.funnel import GateFunnel, funnel_to_jsonable, gate_funnel
from fse.analytics.metrics import MetricsCfg
from fse.backtest.decision_log import DECISION_LOG_FILE_NAME, DecisionLog, map_key
from fse.backtest.manifest import (
    MANIFEST_FILE_NAME,
    DataRange,
    MissingData,
    RunManifest,
    RunSpec,
    SkippedSession,
    run_manifest,
)
from fse.backtest.shadow import SetupRecord, ShadowBook, ShadowMode
from fse.backtest.stats import NodeAgreement, TapCounts, node_agreement, tap_counts
from fse.calendars import load_calendars, project_calendar_dir
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.exits import EXIT_REGIMES
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import CacheWindowKey, DataCache, cache_window_starts
from fse.data.catalog import BarsCoverageRecord
from fse.data.medians import MedianParams, RegimeMedianStore
from fse.data.vix import VIX_INSTRUMENT, VIX_INTERVAL_S, prior_session
from fse.engine.nodes import NodeParams
from fse.engine.planner import CancelOrder, ModifyOrder, OrderFill, OrderIntent, PlaceBracket
from fse.engine.risk import RiskFill
from fse.engine.state import EngineState
from fse.engine.step import BarPhaseResult, Engine, EngineParams, EntryRejected, StepEvent
from fse.engine.taps import BASE_INTERVAL_S
from fse.engine.types import (
    Bar,
    DarkPoolPrint,
    Direction,
    EconomicEvent,
    Metric,
    Money,
    Snapshot,
    Ticks,
    Trade,
)
from fse.experiments.holdout import HoldoutError, HoldoutPeriod, compute_holdout_period
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue, dumps, ny_iso, to_jsonable
from fse.pit.market_view import MAP_METRICS, HistoricalInputs, HistoricalMarketView, heatmap_view
from fse.settings import project_dir
from fse.sim.account import (
    AccountRejection,
    AccountSim,
    AttemptResult,
    FlatDeadlineClose,
    Liquidation,
)
from fse.sim.fills import FillEvent, SimBook, close_all
from fse.sim.fills import on_bar as fill_bar
from fse.timekit import NS_PER_SECOND, Instant, SessionCalendar, SessionTimes

__all__ = [
    "ATTEMPTS_FILE_NAME",
    "BACKTEST_KIND",
    "BACKTEST_OUTPUT_FILES",
    "FUNNEL_FILE_NAME",
    "REPORT_INPUTS_FILE_NAME",
    "SESSION_OUTCOMES_FILE_NAME",
    "SETUPS_FILE_NAME",
    "SHADOW_TRADES_FILE_NAME",
    "TRADES_CSV_FILE_NAME",
    "TRADES_JSON_FILE_NAME",
    "TRADE_COLUMNS",
    "BacktestInputError",
    "BacktestMode",
    "BacktestResult",
    "SessionCheck",
    "SessionOutcome",
    "WindowGap",
    "backtest_range",
    "cache_holdout",
    "check_sessions",
    "default_run_id",
    "has_first_target",
    "intervals_to_jsonable",
    "metrics_cfg",
    "offline_gap_lines",
    "resolve_seed",
    "run_backtest",
    "trades_csv",
]

BACKTEST_KIND: Final = "backtest"
TRADES_CSV_FILE_NAME: Final = "trades.csv"
TRADES_JSON_FILE_NAME: Final = "trades.json"
ATTEMPTS_FILE_NAME: Final = "combine_attempts.json"
SESSION_OUTCOMES_FILE_NAME: Final = "session_outcomes.json"
SETUPS_FILE_NAME: Final = "setups.json"
SHADOW_TRADES_FILE_NAME: Final = "shadow_trades.csv"
FUNNEL_FILE_NAME: Final = "gate_funnel.json"
REPORT_INPUTS_FILE_NAME: Final = "report_inputs.json"
BACKTEST_OUTPUT_FILES: Final[tuple[str, ...]] = (
    DECISION_LOG_FILE_NAME,
    TRADES_CSV_FILE_NAME,
    TRADES_JSON_FILE_NAME,
    ATTEMPTS_FILE_NAME,
    SESSION_OUTCOMES_FILE_NAME,
    SETUPS_FILE_NAME,
    SHADOW_TRADES_FILE_NAME,
    FUNNEL_FILE_NAME,
    REPORT_INPUTS_FILE_NAME,
    MANIFEST_FILE_NAME,
)
"""Every file a backtest writes in its run directory; none may exist when it starts."""

type BacktestMode = Literal["historical", "replay"]

SEED_BITS: Final = 63
_ZERO: Final = Decimal("0.00")
_ONE_DAY: Final = timedelta(days=1)
_RUN_ID_HEX: Final = 16


class BacktestInputError(ValueError):
    """Invalid backtest input (date range, run directory, config): exit 2, nothing written."""

    exit_code: ClassVar[int] = 2


def backtest_range(start: date, end: date) -> DataRange:
    """The inclusive range ``start`` to ``end``; :class:`BacktestInputError` if it ends first."""
    if end < start:
        raise BacktestInputError(
            f"invalid date range: the end date {end} is before the start date {start}"
        )
    return DataRange(start, end)


# ---------------------------------------------------------------- seed and run id


def resolve_seed(seed: int | None) -> int:
    """``seed``, or a fresh ``secrets.randbits(63)`` when it is ``None`` (design §21)."""
    if seed is None:
        return secrets.randbits(SEED_BITS)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**SEED_BITS:
        raise BacktestInputError(f"the seed must be a whole number from 0 to 2**63 - 1: {seed!r}")
    return seed


def default_run_id(cfg_hash: str, data_range: DataRange, seed: int, mode: BacktestMode) -> str:
    """``bt-`` and 16 hex characters of a hash of the run inputs: equal inputs, equal id."""
    text = f"{cfg_hash}|{data_range.start}|{data_range.end}|{seed}|{mode}"
    return f"bt-{hashlib.sha256(text.encode()).hexdigest()[:_RUN_ID_HEX]}"


# ---------------------------------------------------------------- cache checks


@dataclass(frozen=True, slots=True)
class WindowGap:
    """Absent or incomplete Cache_Windows of one session, symbol and metric (Req 3.13)."""

    session: date
    symbol: str
    metric: Metric
    absent: int
    incomplete: int

    @property
    def count(self) -> int:
        return self.absent + self.incomplete


@dataclass(frozen=True, slots=True)
class SessionCheck:
    """What the Data_Cache holds for one session, from the catalog alone.

    ``windows`` lists the readable (``complete`` or ``no_data``) Cache_Windows
    per configured (symbol, metric); ``missing_snapshots`` the
    ``SYMBOL/metric`` pairs with no stored Snapshot, and ``missing_bars`` the
    configured instruments with no stored 1-minute bar (Req 18.3).
    """

    session: date
    gaps: tuple[WindowGap, ...]
    windows: tuple[CacheWindowKey, ...]
    missing_snapshots: tuple[str, ...]
    missing_bars: tuple[str, ...]

    @property
    def missing(self) -> tuple[MissingData, ...]:
        """The missing data types, in the Run_Manifest's order."""
        kinds: list[MissingData] = []
        if self.missing_snapshots:
            kinds.append("snapshots")
        if self.missing_bars:
            kinds.append("bars")
        return tuple(kinds)

    @property
    def skip(self) -> bool:
        return bool(self.missing)


def check_sessions(
    cache: DataCache,
    calendar: SessionCalendar,
    sessions: Iterable[date],
    *,
    symbols: Sequence[str],
    view_id: str,
    instruments: Sequence[str],
) -> tuple[SessionCheck, ...]:
    """One :class:`SessionCheck` per session, in the given order. Reads the catalog only.

    The Cache_Windows of a session tile its Pull_Window (from ``calendar``);
    windows the catalog holds outside that tiling are not read.
    """
    out: list[SessionCheck] = []
    for session in sessions:
        records = {
            (r.symbol, r.metric, r.start_ns): r
            for r in cache.catalog.windows(view_id=view_id, session=session)
        }
        starts = cache_window_starts(*calendar.pull_window(session))
        gaps: list[WindowGap] = []
        readable: list[CacheWindowKey] = []
        no_snapshots: list[str] = []
        for symbol in symbols:
            for metric in MAP_METRICS:
                absent = incomplete = rows = 0
                for start in starts:
                    record = records.get((symbol, metric, start))
                    if record is None:
                        absent += 1
                    elif record.status == "incomplete":
                        incomplete += 1
                    else:
                        readable.append(CacheWindowKey(symbol, metric, view_id, session, start))
                        rows += record.rows or 0
                if absent or incomplete:
                    gaps.append(WindowGap(session, symbol, metric, absent, incomplete))
                if rows == 0:
                    no_snapshots.append(map_key(symbol, metric))
        no_bars = [
            instrument
            for instrument in instruments
            if not _has_bars(cache.bars.record(instrument, BASE_INTERVAL_S, session))
        ]
        out.append(
            SessionCheck(session, tuple(gaps), tuple(readable), tuple(no_snapshots), tuple(no_bars))
        )
    return tuple(out)


def _has_bars(record: BarsCoverageRecord | None) -> bool:
    return record is not None and record.status == "complete" and (record.rows or 0) > 0


def offline_gap_lines(checks: Iterable[SessionCheck]) -> list[str]:
    """The offline-mode gap printout: one line per session, symbol and metric with a gap."""
    lines: list[str] = []
    for check in checks:
        for gap in check.gaps:
            lines.append(
                f"offline: {gap.session.isoformat()} {gap.symbol} {gap.metric}: "
                f"{gap.count} absent or incomplete Cache_Window(s) "
                f"({gap.absent} absent, {gap.incomplete} incomplete)"
            )
    return lines


# ---------------------------------------------------------------- session inputs


@dataclass(frozen=True, slots=True)
class _LoadSpec:
    """What every session load needs, fixed for the run."""

    calendar: SessionCalendar
    symbols: tuple[str, ...]
    view_id: str
    instruments: tuple[str, ...]
    vix_daily: Mapping[date, VixDailyRecord]
    events: tuple[EconomicEvent, ...]
    dark_pool_tickers: tuple[str, ...]
    dark_pool_fetched: Mapping[str, frozenset[date]]
    dark_pool_lookback: int
    node_params: NodeParams


@dataclass(frozen=True, slots=True)
class _Session:
    """One session's indexed inputs, its futures bars in open-time order and its agreement."""

    session: date
    inputs: HistoricalInputs
    bars: tuple[Bar, ...]
    agreement: NodeAgreement


def _lookback_first(calendar: SessionCalendar, session: date, sessions: int) -> date:
    first = session
    for _ in range(sessions - 1):
        prev = prior_session(calendar, first)
        if prev is None:
            break
        first = prev
    return first


def _load_session(cache: DataCache, check: SessionCheck, spec: _LoadSpec) -> _Session:
    """Read one session from the cache and index it (design §18 step 3)."""
    session = check.session
    seen: set[tuple[str, Metric, Instant]] = set()
    snapshots: list[Snapshot] = []
    for key in check.windows:
        for snap in cache.read_window(key):
            ident = (snap.symbol, snap.metric, snap.as_of_ns)
            if ident not in seen:  # a window boundary Snapshot is stored twice
                seen.add(ident)
                snapshots.append(snap)
    bars: list[Bar] = []
    for instrument in spec.instruments:
        bars.extend(cache.bars.read_session(instrument, BASE_INTERVAL_S, session))
    bars.sort(key=lambda b: (b.open_ns, b.instrument))
    vix_bars: list[Bar] = []
    if cache.vix.bars.status(VIX_INSTRUMENT, VIX_INTERVAL_S, session) == "complete":
        vix_bars = cache.vix.bars.read_session(VIX_INSTRUMENT, VIX_INTERVAL_S, session)
    prior = prior_session(spec.calendar, session)
    dark_pool: dict[str, list[DarkPoolPrint]] = {}
    if spec.dark_pool_tickers:
        first = _lookback_first(spec.calendar, session, spec.dark_pool_lookback) - _ONE_DAY
        for ticker in spec.dark_pool_tickers:
            if session in spec.dark_pool_fetched.get(ticker, frozenset()):
                dark_pool[ticker] = cache.darkpool.read(ticker, first, session)
    events = tuple(
        e for e in spec.events if session - _ONE_DAY <= e.release_date <= session + _ONE_DAY
    )
    inputs = HistoricalInputs(
        symbols=spec.symbols,
        view_id=spec.view_id,
        snapshots=snapshots,
        bars=bars,
        vix_today=spec.vix_daily.get(session),
        vix_prior=None if prior is None else spec.vix_daily.get(prior),
        vix_bars=vix_bars,
        dark_pool=dark_pool,
        events=events,
    )
    agreement = node_agreement(snapshots, spec.node_params)
    return _Session(session, inputs, tuple(bars), agreement)


def _prefetch(
    cache: DataCache, checks: Sequence[SessionCheck], spec: _LoadSpec, workers: int
) -> Generator[_Session]:
    """Load each check's session, in the given order; ``workers`` sessions at most in flight.

    With one worker the given cache is read on this thread. With more, each
    load opens its own read-only :class:`DataCache` on the same root, because a
    catalog connection belongs to one thread.
    """
    if workers <= 1:
        for check in checks:
            yield _load_session(cache, check, spec)
        return

    def load(check: SessionCheck) -> _Session:
        with DataCache(cache.root, calendar=spec.calendar) as own:
            return _load_session(own, check, spec)

    pending: deque[Future[_Session]] = deque()
    todo = iter(checks)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fse-load") as pool:
        try:
            for check in todo:
                pending.append(pool.submit(load, check))
                if len(pending) >= workers:
                    break
            while pending:
                ready = pending.popleft().result()
                nxt = next(todo, None)
                if nxt is not None:
                    pending.append(pool.submit(load, nxt))
                yield ready
        finally:
            for future in pending:
                future.cancel()


# ---------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    """One evaluated session for the Monte_Carlo_Simulator (design §21).

    ``net`` is the trading day's net P&L on the run's account, ``intraday_low``
    the lowest value of the day's P&L plus the unrealized P&L at each bar's
    worst prices (0 at the day's start, so never above 0), and
    ``truncated_by_run_account`` whether the run's Combine_Attempt failed
    during the session.
    """

    session: date
    net: Money
    intraday_low: Money
    truncated_by_run_account: bool


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """What a completed run produced; the files hold the same values."""

    manifest: RunManifest
    run_dir: Path
    seed: int
    decision_times: int
    trades: tuple[Trade, ...]
    attempts: tuple[AttemptResult, ...]
    outcomes: tuple[SessionOutcome, ...]
    gaps: tuple[WindowGap, ...]
    skipped: tuple[SkippedSession, ...]
    setups: tuple[SetupRecord, ...]
    shadow_trades: tuple[Trade, ...]
    funnel: GateFunnel
    tap_counts: tuple[TapCounts, ...]
    agreement: NodeAgreement
    holdout: HoldoutPeriod | None
    intervals: BootstrapIntervals


# ---------------------------------------------------------------- the trade list

TRADE_COLUMNS: Final[tuple[str, ...]] = (
    "session",
    "instrument",
    "pattern",
    "source_strike",
    "direction",
    "tap_seq",
    "entry_client_id",
    "entry_bar_open_ns",
    "entry_bar_open_ny",
    "entry_price_ticks",
    "qty_at_entry",
    "initial_stop_ticks",
    "exit_count",
    "exit_bar_open_ns",
    "exit_bar_open_ny",
    "exits",
    "net",
    "r",
    "r_multiple",
    "reached_tp1",
    "mae_r",
    "mfe_r",
    "missing_bars",
    "shadow",
)
"""The trade-list CSV header: the Trade fields, with MAE and MFE in R (Req 20.6)."""


def _trade_row(trade: Trade) -> list[str]:
    key, entry, last = trade.setup_key, trade.entry_fill, trade.exits[-1]
    return [
        key.session.isoformat(),
        key.instrument,
        key.pattern,
        repr(key.source_strike),
        key.direction,
        str(key.tap_seq),
        entry.client_id,
        str(entry.bar_open_ns),
        ny_iso(entry.bar_open_ns),
        str(entry.price),
        str(trade.qty_at_entry),
        str(trade.initial_stop),
        str(len(trade.exits)),
        str(last.bar_open_ns),
        ny_iso(last.bar_open_ns),
        dumps(trade.exits),
        str(trade.net),
        str(trade.r),
        str(trade.r_multiple),
        "true" if trade.reached_tp1 else "false",
        str(trade.mae_r),
        str(trade.mfe_r),
        str(trade.missing_bars),
        "true" if trade.shadow else "false",
    ]


def trades_csv(trades: Iterable[Trade]) -> str:
    """The trade list as CSV text: :data:`TRADE_COLUMNS`, then one row per trade."""
    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(TRADE_COLUMNS)
    for trade in trades:
        out.writerow(_trade_row(trade))
    return buf.getvalue()


# ---------------------------------------------------------------- the decision loop


@dataclass(frozen=True, slots=True)
class _Group:
    """The bars of one 1-minute interval, one per instrument, in instrument order."""

    open_ns: Instant
    close_ns: Instant
    bars: tuple[Bar, ...]

    def by_instrument(self) -> dict[str, Bar]:
        return {b.instrument: b for b in self.bars}


def _groups(bars: Sequence[Bar], first_open: Instant, last_close: Instant) -> list[_Group]:
    out: list[_Group] = []
    for bar in bars:
        if bar.open_ns < first_open or bar.close_ns > last_close:
            continue
        if out and out[-1].open_ns == bar.open_ns:
            last = out[-1]
            out[-1] = _Group(last.open_ns, last.close_ns, (*last.bars, bar))
        else:
            out.append(_Group(bar.open_ns, bar.close_ns, (bar,)))
    return out


class _Loop:
    """The mutable state of one run: engine state, Fill_Simulator book and account."""

    def __init__(
        self,
        cfg: StrategyConfig,
        engine: Engine,
        account: AccountSim,
        calendar: SessionCalendar,
        log: DecisionLog,
        shadow_mode: ShadowMode,
    ) -> None:
        self._cfg = cfg
        self._engine = engine
        self._account = account
        self._calendar = calendar
        self._log = log
        self._instruments = engine.params.instruments
        self._cadence_s = cfg.time.decision_cadence_s
        self.state: EngineState = engine.initial_state()
        self.book = SimBook()
        self.shadows = ShadowBook(cfg.fills, engine.params.planner, cfg.sizing, shadow_mode)
        self.pending: list[StepEvent] = []
        self.trades: list[Trade] = []
        self.outcomes: list[SessionOutcome] = []
        self.tap_counts: list[TapCounts] = []
        self.agreement = NodeAgreement()
        self.decision_times = 0
        self._last_bar: dict[str, Bar] = {}
        self._day_low: Money = _ZERO
        self._truncated = False

    # ------------------------------------------------------------ sessions

    def run_session(self, data: _Session) -> None:
        cal = self._calendar
        session = data.session
        self._account.start_trading_day(session)
        self._day_low = _ZERO
        self._truncated = False
        deadline = cal.flat_deadline(session)
        rth = (cal.rth_open(session), cal.rth_close(session))
        self.shadows.start_session(session, rth)
        # The closing fills are stamped on the bar that opens at the deadline, so
        # the engine first sees them when that bar has closed (Req 5.3).
        observed = deadline + BASE_INTERVAL_S * NS_PER_SECOND
        groups = _groups(data.bars, cal.trading_day_start(session), deadline)
        i = 0
        ended = False
        held: list[tuple[Bar, FillEvent]] | None = None
        for t in cal.decision_times(session, self._cadence_s):
            view = data.inputs.view(t)
            while i < len(groups) and groups[i].close_ns <= t:
                self._bar_phase(groups[i], view, rth)
                i += 1
            if not ended and t >= deadline:
                held = self._end_day(data, deadline)
                ended = True
            if held is not None and t >= observed:
                self._deliver(held)
                held = None
            self._decide(view, t)
        if not ended:
            view = data.inputs.view(deadline)
            for group in groups[i:]:
                self._bar_phase(group, view, rth)
            held = self._end_day(data, deadline)
        if held is not None:
            self._deliver(held)
        net = self._account.day_pnl
        self.outcomes.append(SessionOutcome(session, net, min(self._day_low, net), self._truncated))
        self.shadows.finish_session(self.state.taps)
        self.tap_counts.append(tap_counts(self.state.taps, session, cal))
        self.agreement += data.agreement

    def _decide(self, view: HistoricalMarketView, t: Instant) -> None:
        events = tuple(self.pending)
        self.pending.clear()
        result = self._engine.step(self.state, view, t, events)
        self.state = result.state
        self._route(result.intents)
        self._log.write(result.payload)
        self.shadows.on_decision(t, result.payload, result.context, self.state.taps)
        self.decision_times += 1

    # ------------------------------------------------------------ the bar phase

    def _bar_phase(
        self, group: _Group, view: HistoricalMarketView, rth: tuple[Instant, Instant]
    ) -> None:
        eng = self._engine
        bars = group.by_instrument()
        fills: list[tuple[Bar, FillEvent]] = []
        for bar in group.bars:
            self.book, events = fill_bar(self.book, bar, self._cfg.fills, rth=rth)
            for event in events:
                self._account.on_fill(event.fill, event.order)
                fills.append((bar, event))
        self.shadows.on_main_fills(event for _, event in fills)
        for bar in group.bars:
            self.shadows.on_bar(bar)
        self._day_low = min(self._day_low, self._account.day_pnl + self._unrealized(bars))
        for account_event in self._account.on_bar(bars):
            if isinstance(account_event, Liquidation):
                fills.extend(self._liquidate(account_event, bars))
                if account_event.rule == "maximum_loss_limit":
                    self._truncated = True
                self._day_low = min(self._day_low, self._account.day_pnl)
        self._count(event for _, event in fills)
        # Every bar of the interval, then any older bar a liquidation was stamped on.
        anchors = list(group.bars)
        for bar, _ in fills:
            if bar not in anchors:
                anchors.append(bar)
        for bar in anchors:
            self._update_stops(bar, fills)
        positions = self.book.positions()
        self._apply(
            eng.check_loss_stop(
                self.state,
                group.close_ns,
                {i: positions.get(i, 0) for i in self._instruments},
                self._unrealized(bars),
            )
        )
        for bar in group.bars:
            self.state = eng.consume_bar(self.state, bar, view)
            self._last_bar[bar.instrument] = bar

    def _count(self, events: Iterable[FillEvent]) -> None:
        """Bar phase 3: every fill to the Risk_Manager, and each closed trade to the list."""
        for event in events:
            rf = RiskFill(event.fill, event.order.role, event.gross)
            self.state = self._engine.count_fill(self.state, rf, event.closed)
            if event.closed is not None:
                self.trades.append(event.closed)

    def _update_stops(self, bar: Bar, fills: Sequence[tuple[Bar, FillEvent]]) -> None:
        """Bar phase 4: the fills stamped on ``bar`` into the planner, then its stop moves."""
        mine = [OrderFill(e.order, e.fill) for b, e in fills if b == bar]
        self._apply(self._engine.update_stops(self.state, bar, mine))

    def _apply(self, result: BarPhaseResult) -> None:
        self.state = result.state
        self._route(result.intents)
        self.pending.extend(result.events)

    def _unrealized(self, bars: Mapping[str, Bar]) -> Money:
        """The open trades at their worst price on ``bars`` (the last close without a bar)."""
        total = _ZERO
        for trade in self.book.trades.values():
            bar = bars.get(trade.instrument)
            if bar is not None:
                price = trade.worst_price(bar)
            else:
                last = self._last_bar.get(trade.instrument)
                if last is None or last.c_t is None:
                    raise ValueError(f"no {trade.instrument} bar to value an open trade")
                price = last.c_t
            total += trade.unrealized(price)
        return total

    # ------------------------------------------------------------ account closes

    def _close(
        self, priced: Mapping[tuple[str, Direction], tuple[Bar, Ticks]], reason: str, at: Instant
    ) -> list[tuple[Bar, FillEvent]]:
        """Close every open trade and cancel every working order (an account rule fired).

        ``priced`` maps each (instrument, direction) with open trades to the
        bar the closing fills are stamped with and the closing price. Resting
        plans lose their orders, so each gets an ``EntryRejected`` for the
        next step.
        """
        out: list[tuple[Bar, FillEvent]] = []
        cfg = self._cfg.fills
        for (instrument, direction), (bar, price) in sorted(priced.items()):
            keys = [
                k
                for k, t in self.book.trades.items()
                if (t.instrument, t.direction) == (instrument, direction)
            ]
            part = SimBook(
                brackets={k: self.book.brackets[k] for k in keys},
                trades={k: self.book.trades[k] for k in keys},
                last_bar_open=dict(self.book.last_bar_open),
            )
            _, events = close_all(part, bar.open_ns, {instrument: price}, cfg, reason=reason)
            out.extend((bar, e) for e in events)
        message = f"{reason}: the Account_Simulator cancelled every working order"
        for plan in self.state.book.resting:
            self.pending.append(EntryRejected(plan.key, at, message))
        self.book = SimBook(last_bar_open=dict(self.book.last_bar_open))
        return out

    def _liquidate(
        self, event: Liquidation, bars: Mapping[str, Bar]
    ) -> list[tuple[Bar, FillEvent]]:
        """An MLL or DLL liquidation: each trade closes at its worst price on the breach bar.

        An instrument without a bar on the breach interval is closed on its
        last bar, the bar the Account_Simulator valued it with.
        """
        priced: dict[tuple[str, Direction], tuple[Bar, Ticks]] = {}
        for trade in self.book.trades.values():
            bar = bars.get(trade.instrument) or self._last_bar[trade.instrument]
            priced[(trade.instrument, trade.direction)] = (bar, trade.worst_price(bar))
        return self._close(priced, event.rule, event.bar_open_ns)

    def _end_day(self, data: _Session, deadline: Instant) -> list[tuple[Bar, FillEvent]]:
        """The Flat_Deadline: the account's flat close and day-end rules (Req 15.7, 15.16).

        Returns the closing fills; :meth:`_deliver` hands them to the engine.
        """
        closing: dict[str, Bar] = {}
        for bar in data.bars:
            if bar.open_ns >= deadline and bar.instrument not in closing:
                closing[bar.instrument] = bar
        for trade in self.book.trades.values():
            found = closing.get(trade.instrument)
            if found is None or found.open_ns != deadline:
                raise ValueError(
                    f"{data.session}: the open {trade.instrument} position needs a bar that "
                    f"opens at the Flat_Deadline {ny_iso(deadline)}"
                )
        flat: list[tuple[Bar, FillEvent]] = []
        for event in self._account.end_trading_day(closing):
            if isinstance(event, FlatDeadlineClose):
                # Every trade closes at the open of its instrument's deadline bar (Req 15.16),
                # the price the account closes its net position at. A long and a short of one
                # instrument can net to no account position, so the bar gives the price.
                priced: dict[tuple[str, Direction], tuple[Bar, Ticks]] = {}
                for t in self.book.trades.values():
                    bar = closing[t.instrument]
                    assert bar.o_t is not None  # futures bars carry tick prices
                    priced[(t.instrument, t.direction)] = (bar, bar.o_t)
                flat = self._close(priced, "flat_deadline", deadline)
        if self.book.trades:
            raise ValueError(f"{data.session}: open trades remain after the Flat_Deadline")
        if self.book.orders:
            self._close({}, "flat_deadline", deadline)
        self.shadows.flat_close(closing, deadline)
        return flat

    def _deliver(self, flat: Sequence[tuple[Bar, FillEvent]]) -> None:
        """The Flat_Deadline fills to the Risk_Manager and the Order_Planner (bar phase 3-4)."""
        self._count(e for _, e in flat)
        for bar in dict.fromkeys(b for b, _ in flat):
            self._update_stops(bar, flat)

    # ------------------------------------------------------------ intents

    def _route(self, intents: Iterable[OrderIntent]) -> None:
        """Send planner intents to the Fill_Simulator, each entry through the account first."""
        for intent in intents:
            book = self.book
            if isinstance(intent, PlaceBracket):
                entry = intent.entry
                working = [o for o in book.working_orders() if o.role == "entry"]
                checked = self._account.check_order(entry, working)
                if isinstance(checked, AccountRejection):
                    assert entry.setup_key is not None  # planner entries carry their key
                    self.pending.append(
                        EntryRejected(entry.setup_key, entry.placed_at, checked.message)
                    )
                    continue
                self.book = book.submit_bracket(entry, intent.stop, intent.targets)
            elif isinstance(intent, ModifyOrder):
                if book.order(intent.client_id) is not None:
                    self.book = book.modify(intent.client_id, intent.price, intent.at)
            elif isinstance(intent, CancelOrder):
                if intent.client_id in book.orders:
                    self.book = book.cancel(intent.client_id, intent.at)
            else:
                order = intent.order
                if order.setup_key in book.trades and order.client_id not in book.orders:
                    self.book = book.submit_exit(order)


# ---------------------------------------------------------------- run_backtest


def _require_new_run_dir(out_dir: Path) -> None:
    taken = [name for name in BACKTEST_OUTPUT_FILES if (out_dir / name).exists()]
    if taken:
        raise BacktestInputError(
            f"the run directory {out_dir} already holds {', '.join(taken)}; use a new run directory"
        )


def _fees(cfg: StrategyConfig, instruments: Sequence[str]) -> dict[str, Money]:
    out: dict[str, Money] = {}
    missing: list[str] = []
    for instrument in instruments:
        costs = cfg.fills.costs_for(instrument)
        if costs is None:
            missing.append(f"fills.costs.{instrument}")
        else:
            out[instrument] = costs.per_contract
    if missing:
        raise BacktestInputError(
            "commission and exchange_fee are required for every traded instrument; "
            f"not set: {', '.join(missing)}"
        )
    return out


def run_backtest(
    cfg: StrategyConfig,
    sessions: DataRange,
    cache: DataCache,
    out_dir: str | Path,
    seed: int | None,
    mode: BacktestMode = "historical",
    *,
    writer: LogWriter,
    calendar_dir: Path | None = None,
    offline: bool = False,
    base_times: SessionTimes | None = None,
    config_path: str | None = None,
    run_id: str | None = None,
    workers: int = 1,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
    shadow_mode: ShadowMode = "rejected",
) -> BacktestResult:
    """Run ``cfg`` over every session of ``sessions`` from ``cache`` (see the module notes).

    - ``sessions``: the inclusive date range (:func:`backtest_range` checks it).
    - ``out_dir``: the run directory; it must not hold a backtest output yet.
    - ``seed``: recorded in the Run_Manifest; ``None`` draws one.
    - ``calendar_dir``: the three calendar files, default the Project's
      ``calendars/`` folder.
    - ``offline``: print the absent and incomplete Cache_Windows first.
    - ``base_times``: the Pull_Window (default 09:00-16:00); the ``account``
      section sets the Flat_Deadline.
    - ``workers``: sessions loaded ahead on worker threads (1: none).
    - ``clock`` and ``code_version``: for the Run_Manifest only.
    - ``shadow_mode``: :class:`~fse.backtest.shadow.ShadowBook` mode; tests
      only change it.

    Raises :class:`BacktestInputError` or :class:`~fse.calendars.CalendarError`
    (exit 2) before any Decision_Time and before any output file. After that,
    any error ends the run with an ``aborted`` Run_Manifest and propagates.
    """
    if mode != "historical":
        raise NotImplementedError("replay mode reads a live recording; it is not available yet")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise BacktestInputError(f"workers must be a whole number of at least 1: {workers!r}")
    if calendar_dir is None:
        folder = project_dir()
        if folder is None:
            raise BacktestInputError("there is no Project folder; pass the calendar directory")
        calendar_dir = project_calendar_dir(folder)
    times = cfg.account.session_times(SessionTimes() if base_times is None else base_times)
    calendars = load_calendars(calendar_dir, sessions.start, sessions.end, times=times)
    run_sessions = calendars.run_sessions
    if not run_sessions:
        raise BacktestInputError(
            f"invalid date range: {sessions.start} to {sessions.end} contains no session"
        )
    calendar = calendars.exchange.sessions
    params = EngineParams.from_sections(cfg)
    instruments = params.instruments
    fees = _fees(cfg, instruments)
    target = Path(out_dir).expanduser()
    _require_new_run_dir(target)
    resolved_seed = resolve_seed(seed)

    view_id = heatmap_view(cfg.data.heatmap_view).view_id()
    symbols = tuple(cfg.data.symbols)
    checks = check_sessions(
        cache, calendar, run_sessions, symbols=symbols, view_id=view_id, instruments=instruments
    )
    gaps = tuple(g for c in checks for g in c.gaps)
    if offline:
        writer.echo(
            f"offline: {len({g.session for g in gaps})} of {len(checks)} requested sessions "
            "have absent or incomplete Cache_Windows"
        )
        for line in offline_gap_lines(checks):
            writer.echo(line)

    cfg_hash = config_hash(cfg)
    spec = RunSpec(
        run_id=run_id or default_run_id(cfg_hash, sessions, resolved_seed, mode),
        kind=BACKTEST_KIND,
        config_hash=cfg_hash,
        data_range=sessions,
        seed=resolved_seed,
        config_path=config_path,
    )
    with run_manifest(
        spec, writer=writer, out_dir=target, clock=clock, code_version=code_version
    ) as rec:
        run_dir = rec.out_dir
        medians = RegimeMedianStore(cache).load_or_build(
            calendar, view_id=view_id, params=MedianParams.from_config(cfg.regime, cfg.data)
        )
        engine = Engine(params, calendar, medians.by_session)
        account = AccountSim(cfg.account, calendar, fees)
        dp = cfg.gates.dark_pool_confluence
        tickers = tuple(dict.fromkeys((dp.es_ticker, dp.nq_ticker))) if dp.enabled else ()
        load_spec = _LoadSpec(
            calendar=calendar,
            symbols=symbols,
            view_id=view_id,
            instruments=instruments,
            vix_daily=cache.vix.read_daily(),
            events=calendars.events.events,
            dark_pool_tickers=tickers,
            dark_pool_fetched={t: cache.darkpool.fetched_dates(t) for t in tickers},
            dark_pool_lookback=dp.lookback_sessions,
            node_params=params.node_params,
        )
        holdout = _holdout(
            cfg,
            cache,
            calendar,
            symbols=load_spec.symbols,
            view_id=load_spec.view_id,
            instruments=load_spec.instruments,
        )
        log_path = run_dir / DECISION_LOG_FILE_NAME
        with DecisionLog(writer, log_path) as log:
            rec.output(log_path)
            loop = _Loop(cfg, engine, account, calendar, log, shadow_mode)
            loaded = _prefetch(cache, [c for c in checks if not c.skip], load_spec, workers)
            evaluated: list[date] = []
            try:
                for check in checks:
                    if check.skip:
                        names = (*check.missing_snapshots, *check.missing_bars)
                        rec.skipped(check.session, check.missing, names)
                        continue
                    loop.run_session(next(loaded))
                    rec.evaluated(check.session)
                    evaluated.append(check.session)
            finally:
                loaded.close()
        attempts = account.finish()
        trades = tuple(loop.trades)
        outcomes = tuple(loop.outcomes)
        setups = loop.shadows.records
        shadow_trades = loop.shadows.shadow_trades
        enabled = tuple(g for g in cfg.gates.order if cfg.gates.enabled(g))
        funnel = gate_funnel(
            setups,
            trades,
            enabled,
            min_sample=cfg.reporting.shadow_min_sample,
            sessions=evaluated,
        )
        intervals = bootstrap_intervals(
            trades,
            metrics_cfg(cfg),
            seed=resolved_seed,
            resamples=cfg.reporting.bootstrap_resamples,
        )
        rec.record_bootstrap(intervals)
        inputs = _report_inputs(cfg, instruments, enabled, holdout, evaluated, loop, intervals)
        rec.output(writer.write_text(run_dir / TRADES_CSV_FILE_NAME, trades_csv(trades)))
        rec.output(writer.write_json(run_dir / TRADES_JSON_FILE_NAME, to_jsonable(list(trades))))
        rec.output(writer.write_json(run_dir / ATTEMPTS_FILE_NAME, to_jsonable(list(attempts))))
        rec.output(
            writer.write_json(run_dir / SESSION_OUTCOMES_FILE_NAME, to_jsonable(list(outcomes)))
        )
        rec.output(writer.write_json(run_dir / SETUPS_FILE_NAME, to_jsonable(list(setups))))
        rec.output(writer.write_text(run_dir / SHADOW_TRADES_FILE_NAME, trades_csv(shadow_trades)))
        rec.output(writer.write_json(run_dir / FUNNEL_FILE_NAME, funnel_to_jsonable(funnel)))
        rec.output(writer.write_json(run_dir / REPORT_INPUTS_FILE_NAME, inputs))
    manifest = rec.manifest
    assert manifest is not None  # run_manifest sets it on a normal exit
    return BacktestResult(
        manifest=manifest,
        run_dir=run_dir,
        seed=resolved_seed,
        decision_times=loop.decision_times,
        trades=trades,
        attempts=attempts,
        outcomes=outcomes,
        gaps=gaps,
        skipped=manifest.sessions_skipped,
        setups=setups,
        shadow_trades=shadow_trades,
        funnel=funnel,
        tap_counts=tuple(loop.tap_counts),
        agreement=loop.agreement,
        holdout=holdout,
        intervals=intervals,
    )


def cache_holdout(
    cfg: StrategyConfig, cache: DataCache, calendar: SessionCalendar
) -> HoldoutPeriod | None:
    """The Holdout_Period of the calendar sessions the cache holds full data for (Req 22.1).

    Full data means a Snapshot for each of ``cfg``'s symbols and metrics and
    bars for each of its instruments. ``None`` when no session has full data.
    """
    return _holdout(
        cfg,
        cache,
        calendar,
        symbols=tuple(cfg.data.symbols),
        view_id=heatmap_view(cfg.data.heatmap_view).view_id(),
        instruments=EngineParams.from_sections(cfg).instruments,
    )


def _holdout(
    cfg: StrategyConfig,
    cache: DataCache,
    calendar: SessionCalendar,
    *,
    symbols: Sequence[str],
    view_id: str,
    instruments: Sequence[str],
) -> HoldoutPeriod | None:
    checks = check_sessions(
        cache,
        calendar,
        calendar.sessions(),
        symbols=symbols,
        view_id=view_id,
        instruments=instruments,
    )
    with_data = [c.session for c in checks if not c.skip]
    if not with_data:
        return None
    try:
        return compute_holdout_period(with_data, cfg.experiments.holdout_fraction)
    except HoldoutError:
        return None


def has_first_target(cfg: StrategyConfig) -> bool:
    """Whether an Exit_Mode the config can select sets a first target (not Trailing)."""
    modes = {cfg.exits.setting_for(regime).mode for regime in (None, *EXIT_REGIMES)}
    return modes != {"trailing"}


def metrics_cfg(cfg: StrategyConfig) -> MetricsCfg:
    """The :class:`MetricsCfg` of ``cfg``'s ``reporting`` section and Exit_Mode."""
    rep = cfg.reporting
    return MetricsCfg(
        scratch_tolerance_r=Decimal(repr(rep.scratch_tolerance_r)),
        min_sample_trades=rep.min_sample_trades,
        primary_win_rate=rep.primary_win_rate,
        has_first_target=has_first_target(cfg),
    )


def intervals_to_jsonable(intervals: BootstrapIntervals) -> JsonValue:
    """A run's bootstrap intervals as ``report_inputs.json`` stores them (Req 20.12-20.13).

    Each bound is the ``repr`` of its float64 value, so it reads back exactly;
    an undefined interval is ``"not applicable"`` (Req 20.16).
    """
    return {
        "confidence_pct": intervals.confidence_pct,
        "seed": intervals.seed,
        "resamples": intervals.resamples,
        "trade_count": intervals.trade_count,
        "low_sample": intervals.low_sample,
        "primary_win_rate": intervals.primary_win_rate,
        "primary_win_rate_pct": interval_to_jsonable(intervals.primary_win_rate_pct),
        "expectancy_r": interval_to_jsonable(intervals.expectancy_r),
    }


def _report_inputs(
    cfg: StrategyConfig,
    instruments: Sequence[str],
    enabled: Sequence[str],
    holdout: HoldoutPeriod | None,
    evaluated: Sequence[date],
    loop: _Loop,
    intervals: BootstrapIntervals,
) -> JsonValue:
    """What ``fse report`` reads besides the trades and the Gate_Funnel (Req 20.15, 18.11)."""
    costs: dict[str, object] = {}
    for instrument in instruments:
        found = cfg.fills.costs_for(instrument)
        if found is not None:
            costs[instrument] = {"commission": found.commission, "exchange_fee": found.exchange_fee}
    held: dict[str, object] | None = None
    if holdout is not None:
        held = {
            "fraction": holdout.fraction,
            "first": holdout.first,
            "last": holdout.last,
            "sessions": len(holdout),
            "included": [d for d in evaluated if d in holdout],
        }
    rep = cfg.reporting
    a = loop.agreement
    bootstrap: object = intervals_to_jsonable(intervals)
    return to_jsonable(
        {
            "decision_cadence_s": cfg.time.decision_cadence_s,
            "bar_interval_s": BASE_INTERVAL_S,
            "instruments": list(instruments),
            "fills": {
                "trade_through_ticks": cfg.fills.trade_through_ticks,
                "slippage_ticks": cfg.fills.slippage_ticks,
                "costs": costs,
            },
            "metrics": {
                "scratch_tolerance_r": Decimal(repr(rep.scratch_tolerance_r)),
                "min_sample_trades": rep.min_sample_trades,
                "primary_win_rate": rep.primary_win_rate,
                "has_first_target": has_first_target(cfg),
            },
            "shadow_min_sample": rep.shadow_min_sample,
            "enabled_gates": list(enabled),
            "holdout": held,
            "bootstrap": bootstrap,
            "node_agreement": {
                "compared": a.compared,
                "without_labels": a.without_labels,
                "king_agree": a.king_agree,
                "gatekeeper_both": a.gatekeeper_both,
                "gatekeeper_either": a.gatekeeper_either,
            },
            "tap_counts": [
                {
                    "session": c.session,
                    "bar_interval_s": c.bar_interval_s,
                    "taps": c.taps,
                    "inter_decision": c.inter_decision,
                }
                for c in loop.tap_counts
            ],
        }
    )
