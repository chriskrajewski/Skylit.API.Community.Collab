"""Synthetic Data_Cache inputs and Strategy_Configs for the Backtester tests (tasks 19.3-19.7).

**Calendar.** Three small calendar files (:func:`write_calendars`) cover
2026-02-02 to 2026-03-13: a test holiday on Monday 2026-03-09, a 13:15 early
close on Friday 2026-03-06 (Flat_Deadline 13:00) and three economic events.
:data:`CALENDAR` is the same exchange calendar with the default account times.

**History.** The 20 February sessions hold only SPX gamma and vanna Snapshots
(:data:`HISTORY_SESSIONS`, fixed), so every March session has usable trailing Regime
medians (20 sessions, Req 7.2) and the Regime is measured. They are never in a
run's date range.

**Sessions** (:func:`market_inputs`): one to three consecutive March sessions.
Per session:

- **Snapshots** of SPX and QQQ gamma and vanna in the default Heatmap_View: a
  base Snapshot at 09:29:30 (sometimes left out, so early Decision_Times have
  a missing marker) and up to three more inside the Pull_Window. Strikes are a
  random subset of a 10-point SPX grid (5700-5900) or a 1-point QQQ grid
  (490-510); values come from a set with zeros, ties and both signs. A subset
  can be empty.
- **Bars**: MES and MNQ 1-minute bars as random walks in ticks over one to
  two segments (the first from 09:20, so 09:30 has a Futures_Price), with gap
  opens between segments, plus the bar that opens at the Flat_Deadline. Both
  instruments share their bar minutes.
- **VIX**: usually a daily record (open observed 09:31, close at the RTH
  close) and sometimes 1-minute VIX bars from 09:30.
- **Dark pool**: zero to three SPY and QQQ prints, all fetched dates stored.
- With ``gaps=True``, a session can lose every Snapshot of one symbol and
  metric or every bar of one instrument, so the Backtester skips it.

Bulk numbers (walks, map values, spots) come from a seeded
:class:`random.Random`; the structure (session count, segments, which
Snapshots exist, gaps) is drawn by Hypothesis.

**Configs** (:func:`strategy_configs`): the minimal valid config with SPX and
QQQ, a 600-3600 s Decision_Cadence, every Gate off (so priced setups are
A_Plus) or a random subset or all on, and random exits, sizing, Cancel_Trigger,
Kill_Switch and account settings.

**Running** (:func:`run`): :func:`fse.backtest.runner.run_backtest` on a cache
written by :func:`write_cache`, with a constant manifest clock and a fixed
code version, so equal inputs give byte-identical output files.
"""

from __future__ import annotations

import io
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from hypothesis import strategies as st

from fse.backtest.manifest import DataRange
from fse.backtest.runner import BacktestResult, run_backtest
from fse.calendars import ECONOMIC_EVENTS_FILE, EXCHANGE_CALENDAR_FILE, ROLL_CALENDAR_FILE
from fse.config.schema import StrategyConfig
from fse.config.schema.account import AccountConfig
from fse.config.schema.exits import EXIT_MODES
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.orders import CANCEL_TRIGGERS
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import CACHE_WINDOW_NS, CacheWindowKey, DataCache, HeatmapView
from fse.data.vix import VIX_CONTRACT, VIX_INSTRUMENT, VIX_INTERVAL_S
from fse.engine.types import Bar, DarkPoolPrint, Metric, Snapshot, Ticks
from fse.logio import LogWriter, Redactor
from fse.pit.market_view import MAP_METRICS
from fse.timekit import NS_PER_SECOND, Instant, SessionCalendar, SessionTimes, ny_instant
from tests.fakes.configs import minimal_config_data

__all__ = [
    "CALENDAR",
    "CODE_VERSION",
    "DARK_POOL_TICKERS",
    "FAKE_SECRET",
    "INSTRUMENTS",
    "KEYS",
    "MINUTE",
    "SEED",
    "SYMBOLS",
    "VIEW",
    "MarketInputs",
    "SessionInputs",
    "make_bar",
    "make_snapshot",
    "market_inputs",
    "random_bar",
    "random_pairs",
    "random_spot",
    "run",
    "strategy_configs",
    "vix_bar",
    "write_cache",
    "write_calendars",
]

MINUTE: Final = 60 * NS_PER_SECOND
SECOND: Final = NS_PER_SECOND
SEED: Final = 11
CODE_VERSION: Final = "test-version"
FAKE_SECRET: Final = "fake-backtest-secret-0000"
STARTED: Final = 1_772_000_000_000_000_000

FIRST: Final = date(2026, 2, 2)
RUN_FIRST: Final = date(2026, 3, 2)
LAST: Final = date(2026, 3, 13)
HOLIDAY: Final = date(2026, 3, 9)
EARLY_CLOSE_DAY: Final = date(2026, 3, 6)
EARLY_CLOSE: Final = time(13, 15)
CALENDAR: Final = SessionCalendar(
    FIRST,
    LAST,
    holidays=(HOLIDAY,),
    early_closes={EARLY_CLOSE_DAY: EARLY_CLOSE},
    times=AccountConfig().session_times(SessionTimes()),
)
SESSIONS: Final[tuple[date, ...]] = CALENDAR.sessions(RUN_FIRST, LAST)
"""The sessions a run's date range is drawn from."""
HISTORY_SESSIONS: Final[tuple[date, ...]] = CALENDAR.sessions(FIRST, date(2026, 2, 27))

CALENDAR_FILES: Final[Mapping[str, str]] = MappingProxyType(
    {
        EXCHANGE_CALENDAR_FILE: """\
covers: {first: 2026-02-02, last: 2026-03-13}
holidays:
  - {date: 2026-03-09, name: Test holiday}
early_closes:
  - {date: 2026-03-06, time: "13:15", name: Test early close}
""",
        ECONOMIC_EVENTS_FILE: """\
covers: {first: 2026-02-02, last: 2026-03-13}
events:
  - {type: FOMC, date: 2026-03-04, time: "14:00"}
  - {type: NFP, date: 2026-03-06, time: "08:30"}
  - {type: CPI, date: 2026-03-11, time: "10:00"}
""",
        ROLL_CALENDAR_FILE: """\
covers: {first: 2026-02-02, last: 2026-03-13}
instruments:
  ES:
    - {first: 2026-02-02, last: 2026-03-13, contract: ESH6}
  NQ:
    - {first: 2026-02-02, last: 2026-03-13, contract: NQH6}
""",
    }
)

VIEW: Final = HeatmapView()
SYMBOLS: Final[tuple[str, ...]] = ("SPX", "QQQ")
KEYS: Final[tuple[tuple[str, Metric], ...]] = tuple((s, m) for s in SYMBOLS for m in MAP_METRICS)
"""The configured (symbol, metric) pairs in Map_State order."""
INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ")
CONTRACTS: Final[Mapping[str, str]] = MappingProxyType({"MES": "MESH6", "MNQ": "MNQH6"})
DARK_POOL_TICKERS: Final[tuple[str, ...]] = ("SPY", "QQQ")

STRIKES: Final[Mapping[str, tuple[float, ...]]] = MappingProxyType(
    {
        "SPX": tuple(5700.0 + 10 * i for i in range(21)),
        "QQQ": tuple(490.0 + i for i in range(21)),
    }
)
SPOT: Final[Mapping[str, float]] = MappingProxyType({"SPX": 5800.0, "QQQ": 500.0})
VALUES: Final[tuple[float, ...]] = (
    -3.0e9, -2.0e9, -1.0e9, -0.5e9, 0.0, 0.0, 0.5e9, 0.9e9, 1.0e9, 2.0e9, 3.0e9,
)  # fmt: skip
START_TICKS: Final[Mapping[str, int]] = MappingProxyType({"MES": 5800 * 4, "MNQ": 20000 * 4})
STEP_TICKS: Final[Mapping[str, int]] = MappingProxyType({"MES": 12, "MNQ": 40})
"""The largest one-minute move of each walk, in ticks."""


HISTORY_PAIRS: Final[Mapping[Metric, tuple[tuple[float, float], ...]]] = MappingProxyType(
    {
        "gamma": ((5750.0, 2.0e9), (5800.0, -1.0e9), (5850.0, 1.5e9)),
        "vanna": ((5750.0, -1.0e9), (5800.0, 0.5e9), (5850.0, 1.0e9)),
    }
)


def write_calendars(directory: Path) -> Path:
    """Write the three test calendar files into ``directory`` (created) and return it."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in CALENDAR_FILES.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


# ---------------------------------------------------------------- records


def make_snapshot(
    session: date,
    symbol: str,
    metric: Metric,
    as_of_ns: Instant,
    spot: float,
    pairs: Sequence[tuple[float, float]],
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=VIEW.view_id(),
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}",
        spot=spot,
        previous_close=None,
        strikes=tuple(k for k, _ in pairs),
        values=tuple(v for _, v in pairs),
        node_types=None,
        expirations=(session.isoformat(),),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def make_bar(instrument: str, open_ns: Instant, o: int, h: int, low: int, c: int) -> Bar:
    """A 1-minute futures bar from tick prices."""
    return Bar(
        instrument, CONTRACTS[instrument], 60, open_ns, open_ns + MINUTE,
        o / 4, h / 4, low / 4, c / 4, 100.0, o, h, low, c, "atlas",
    )  # fmt: skip


def vix_bar(open_ns: Instant, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        VIX_INSTRUMENT, VIX_CONTRACT, VIX_INTERVAL_S, open_ns, open_ns + MINUTE,
        o, h, low, c, 0.0, None, None, None, None, "atlas",
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class SessionInputs:
    """One session's synthetic inputs, every list in Observation_Time order."""

    session: date
    snapshots: tuple[Snapshot, ...]
    bars: tuple[Bar, ...]
    vix: VixDailyRecord | None
    vix_bars: tuple[Bar, ...]
    prints: tuple[DarkPoolPrint, ...]

    def snapshots_of(self, symbol: str, metric: Metric) -> tuple[Snapshot, ...]:
        return tuple(s for s in self.snapshots if (s.symbol, s.metric) == (symbol, metric))

    def bars_of(self, instrument: str) -> tuple[Bar, ...]:
        return tuple(b for b in self.bars if b.instrument == instrument)

    @property
    def missing_snapshots(self) -> tuple[str, ...]:
        """``SYMBOL/metric`` of each configured pair without a Snapshot, in Map_State order."""
        return tuple(f"{s}/{m}" for s, m in KEYS if not self.snapshots_of(s, m))

    @property
    def missing_bars(self) -> tuple[str, ...]:
        return tuple(i for i in INSTRUMENTS if not self.bars_of(i))

    @property
    def skipped(self) -> bool:
        return bool(self.missing_snapshots or self.missing_bars)


@dataclass(frozen=True, slots=True)
class MarketInputs:
    """Consecutive sessions of synthetic inputs."""

    sessions: tuple[SessionInputs, ...]

    @property
    def data_range(self) -> DataRange:
        return DataRange(self.sessions[0].session, self.sessions[-1].session)

    @property
    def evaluated(self) -> tuple[SessionInputs, ...]:
        return tuple(s for s in self.sessions if not s.skipped)


# ---------------------------------------------------------------- generators


def _walk(
    rng: random.Random, instrument: str, opens: Sequence[Instant], gap_after: frozenset[Instant]
) -> list[Bar]:
    """A tick random walk with a bar at each of ``opens``; a gap open after each segment."""
    step = STEP_TICKS[instrument]
    price = START_TICKS[instrument] + rng.randint(-4 * step, 4 * step)
    out: list[Bar] = []
    for open_ns in opens:
        if open_ns in gap_after:
            price += rng.randint(-6 * step, 6 * step)
        o = price
        c = o + rng.randint(-step, step)
        h = max(o, c) + rng.randint(0, step // 3)
        low = min(o, c) - rng.randint(0, step // 3)
        out.append(make_bar(instrument, open_ns, o, h, low, c))
        price = c
    return out


@st.composite
def _bar_opens(draw: st.DrawFn, session: date) -> tuple[tuple[Instant, ...], frozenset[Instant]]:
    """Bar opens of one session: one or two segments, then the Flat_Deadline bar."""
    rth_open = CALENDAR.rth_open(session)
    deadline = CALENDAR.flat_deadline(session)
    first = ny_instant(session, time(9, 20))
    opens = [first + k * MINUTE for k in range(draw(st.integers(12, 75), label="segment 1"))]
    gaps: set[Instant] = set()
    latest = (deadline - rth_open) // MINUTE - 50
    if draw(st.booleans(), label="segment 2"):
        start = rth_open + draw(st.integers(70, latest), label="segment 2 start") * MINUTE
        length = draw(st.integers(5, 45), label="segment 2 length")
        opens.extend(start + k * MINUTE for k in range(length))
        gaps.add(start)
    opens.append(deadline)
    gaps.add(deadline)
    return tuple(opens), frozenset(gaps)


def random_pairs(rng: random.Random, symbol: str) -> list[tuple[float, float]]:
    """Zero to eight strikes of the symbol's grid, each with a value from :data:`VALUES`."""
    strikes = sorted(rng.sample(STRIKES[symbol], rng.randint(0, 8)))
    return [(k, rng.choice(VALUES)) for k in strikes]


def _changed(rng: random.Random, pairs: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(k, rng.choice(VALUES) if rng.random() < 0.3 else v) for k, v in pairs]


def random_spot(rng: random.Random, symbol: str) -> float:
    """A spot near the symbol's base spot."""
    spread = 15.0 if symbol == "SPX" else 2.0
    return round(SPOT[symbol] + rng.uniform(-spread, spread), 2)


def random_bar(rng: random.Random, instrument: str, open_ns: Instant, near: Ticks) -> Bar:
    """A 1-minute bar opening at ``open_ns`` within a few steps of ``near``."""
    step = STEP_TICKS[instrument]
    o = near + rng.randint(-2 * step, 2 * step)
    c = o + rng.randint(-step, step)
    h = max(o, c) + rng.randint(0, step)
    low = min(o, c) - rng.randint(0, step)
    return make_bar(instrument, open_ns, o, h, low, c)


@st.composite
def session_inputs(draw: st.DrawFn, session: date, *, gaps: bool = False) -> SessionInputs:
    """One session of inputs (see the module notes)."""
    rng = random.Random(draw(st.integers(0, 2**32 - 1), label="seed"))
    pull_start, pull_end = CALENDAR.pull_window(session)
    rth_open = CALENDAR.rth_open(session)
    opens, gap_after = draw(_bar_opens(session))

    extra_times = sorted(
        set(
            draw(
                st.lists(
                    st.sampled_from(opens[:-1]).map(lambda o: o + 30 * SECOND)
                    | st.integers(rth_open, pull_end - SECOND),
                    max_size=3,
                ),
                label="extra Snapshot times",
            )
        )
    )
    extra_times = [t for t in extra_times if pull_start <= t < pull_end]
    base_at = ny_instant(session, time(9, 29, 30))
    snapshots: list[Snapshot] = []
    for symbol, metric in KEYS:
        pairs = random_pairs(rng, symbol)
        times = [t for t in extra_times if rng.random() < 0.6]
        if not times or rng.random() < 0.85:
            times.insert(0, base_at)
        for t in times:
            spot = random_spot(rng, symbol)
            snapshots.append(make_snapshot(session, symbol, metric, t, spot, pairs))
            pairs = _changed(rng, pairs)

    bars = [b for i in INSTRUMENTS for b in _walk(rng, i, opens, gap_after)]
    if gaps and rng.random() < 0.25:
        if rng.random() < 0.5:
            symbol, metric = rng.choice(KEYS)
            snapshots = [s for s in snapshots if (s.symbol, s.metric) != (symbol, metric)]
        else:
            instrument = rng.choice(INSTRUMENTS)
            bars = [b for b in bars if b.instrument != instrument]

    vix: VixDailyRecord | None = None
    vix_bars: list[Bar] = []
    if rng.random() < 0.8:
        vix = VixDailyRecord(
            session=session,
            open=round(rng.uniform(12.0, 30.0), 2),
            open_at_ns=rth_open + MINUTE,
            close=round(rng.uniform(12.0, 30.0), 2),
            close_at_ns=CALENDAR.rth_close(session),
            source="import",
        )
        level = vix.open or 20.0
        for k in range(rng.choice((0, 5, 20))):
            o = level
            level = round(max(9.0, level + rng.uniform(-0.3, 0.3)), 2)
            vix_bars.append(vix_bar(rth_open + k * MINUTE, o, max(o, level), min(o, level), level))

    prints: list[DarkPoolPrint] = []
    for ticker in DARK_POOL_TICKERS:
        base = SPOT["SPX"] / 10 if ticker == "SPY" else SPOT["QQQ"]
        for _ in range(rng.randint(0, 3)):
            price = round(base + rng.uniform(-2.0, 2.0), 2)
            size = rng.randint(1_000, 20_000)
            ts = rng.randrange(rth_open, pull_end)
            prints.append(DarkPoolPrint(ticker, ts, price, size, round(price * size, 2), "D"))
    prints.sort(key=lambda p: (p.ts_ns, p.ticker))

    snapshots.sort(key=lambda s: (s.as_of_ns, s.symbol, s.metric))
    return SessionInputs(
        session, tuple(snapshots), tuple(bars), vix, tuple(vix_bars), tuple(prints)
    )


@st.composite
def market_inputs(draw: st.DrawFn, *, gaps: bool = False, max_sessions: int = 3) -> MarketInputs:
    """One to ``max_sessions`` consecutive sessions of the test calendar."""
    count = draw(st.integers(1, max_sessions), label="sessions")
    first = draw(st.integers(0, len(SESSIONS) - count), label="first session")
    return MarketInputs(
        tuple(draw(session_inputs(d, gaps=gaps)) for d in SESSIONS[first : first + count])
    )


@st.composite
def strategy_configs(draw: st.DrawFn) -> StrategyConfig:
    """A valid Strategy_Config for SPX and QQQ (see the module notes)."""
    data = minimal_config_data()
    mode = draw(st.integers(0, 5), label="gates")
    if mode <= 2:
        gates: dict[str, Any] = {g: {"enabled": False} for g in GATE_IDS}
    elif mode <= 4:
        gates = {g: {"enabled": draw(st.booleans(), label=g)} for g in GATE_IDS}
    else:
        gates = {g: {"enabled": True} for g in GATE_IDS}
    data.update(
        {
            "time": {"decision_cadence_s": draw(st.sampled_from((900, 1800, 3600, 600)))},
            "data": {"symbols": list(SYMBOLS), "nq_sources": ["QQQ"]},
            "gates": gates,
            "exits": {"global": {"mode": draw(st.sampled_from(EXIT_MODES), label="exit")}},
            "sizing": {
                "trinity_size_down": {"enabled": draw(st.booleans(), label="trinity size")},
                "vix_gap": {"enabled": draw(st.booleans(), label="vix size")},
            },
            "orders": {
                "max_open": draw(st.integers(1, 3), label="max_open"),
                "cancel_triggers": dict.fromkeys(
                    CANCEL_TRIGGERS, draw(st.booleans(), label="cancel triggers")
                ),
            },
            "kill_switches": {
                "max_trades": {"max": draw(st.integers(1, 3), label="max trades")},
                "consecutive_losers": {"limit": draw(st.integers(1, 3), label="streak")},
                "internal_daily_loss_stop": {"enabled": draw(st.booleans(), label="loss stop")},
            },
            "account": {
                "profit_target": {"value": draw(st.sampled_from(("3000.00", "300.00")))},
                "maximum_loss_limit": {"value": draw(st.sampled_from(("2000.00", "150.00")))},
                "daily_loss_limit": {"value": draw(st.sampled_from(("1000.00", "100.00")))},
                "consistency_target": {"enabled": draw(st.booleans(), label="consistency")},
            },
        }
    )
    return StrategyConfig.model_validate(data)


# ---------------------------------------------------------------- the cache


def _window_start(session: date, at: Instant) -> Instant:
    start, _ = CALENDAR.pull_window(session)
    return start + (at - start) // CACHE_WINDOW_NS * CACHE_WINDOW_NS


def write_cache(root: Path, market: MarketInputs | Iterable[SessionInputs]) -> Path:
    """Store the history and ``market`` in a new Data_Cache at ``root``; return ``root``."""
    sessions = tuple(market.sessions if isinstance(market, MarketInputs) else market)
    with DataCache(root, calendar=CALENDAR) as cache:
        for day in HISTORY_SESSIONS:
            at = ny_instant(day, time(10, 0))
            for metric, pairs in HISTORY_PAIRS.items():
                snap = make_snapshot(day, "SPX", metric, at, SPOT["SPX"], pairs)
                key = CacheWindowKey.for_view("SPX", metric, VIEW, day, _window_start(day, at))
                cache.write_window(key, [snap], "range", view=VIEW)
        for s in sessions:
            windows: dict[CacheWindowKey, list[Snapshot]] = {}
            for snap in s.snapshots:
                start = _window_start(s.session, snap.as_of_ns)
                key = CacheWindowKey.for_view(snap.symbol, snap.metric, VIEW, s.session, start)
                windows.setdefault(key, []).append(snap)
            for key, snaps in windows.items():
                cache.write_window(key, snaps, "range", view=VIEW)
            for instrument in INSTRUMENTS:
                bars = s.bars_of(instrument)
                if bars:
                    cache.bars.write_session(
                        instrument, 60, s.session, bars,
                        contract=CONTRACTS[instrument], source="atlas",
                    )  # fmt: skip
            if s.vix_bars:
                cache.vix.bars.write_session(
                    VIX_INSTRUMENT, VIX_INTERVAL_S, s.session, s.vix_bars,
                    contract=VIX_CONTRACT, source="atlas",
                )  # fmt: skip
        daily = [s.vix for s in sessions if s.vix is not None]
        if daily:
            cache.vix.write_daily(daily)
        for ticker in DARK_POOL_TICKERS:
            prints = [p for s in sessions for p in s.prints if p.ticker == ticker]
            cache.darkpool.write_trade_dates(ticker, [s.session for s in sessions], prints)
    return root


def run(
    cache_root: Path,
    calendar_dir: Path,
    cfg: StrategyConfig,
    data_range: DataRange,
    out_dir: Path,
    *,
    workers: int = 1,
    offline: bool = False,
    stdout: io.StringIO | None = None,
    stderr: io.StringIO | None = None,
) -> BacktestResult:
    """:func:`run_backtest` with :data:`SEED`, a constant manifest clock and a fake secret."""
    writer = LogWriter(
        Redactor([FAKE_SECRET]),
        stdout=io.StringIO() if stdout is None else stdout,
        stderr=io.StringIO() if stderr is None else stderr,
    )
    with DataCache(cache_root, calendar=CALENDAR) as cache:
        return run_backtest(
            cfg,
            data_range,
            cache,
            out_dir,
            SEED,
            writer=writer,
            calendar_dir=calendar_dir,
            offline=offline,
            workers=workers,
            clock=lambda: STARTED,
            code_version=CODE_VERSION,
        )
