"""The cadence comparison (design §19-22, Req 18.8-18.10).

One Strategy_Config runs once per compared Decision_Cadence (default 5 s,
60 s and 300 s) through :func:`fse.experiments.runner.run_experiment`, so
every cadence gets the same sessions and seed, the Holdout_Period is
excluded (Req 22.2-22.3) and the distinct configuration count is reported
(Req 22.15).

**Eligible sessions** (Req 18.8). A session outside the Holdout_Period is
compared when its stored Snapshot interval and its stored bar interval are
each no longer than the shortest compared cadence:

- the stored Snapshot interval of a session is the longest interval of its
  stored Cache_Windows for the configured symbols and metrics (only windows
  with rows count). A window's interval is the longer of its Snapshots'
  ``resolution`` (``1s`` or ``1m``) and the storage interval it was written
  with (``fse.storage_interval_s`` in the file metadata; none: every Snapshot
  kept);
- the stored bar interval of a session is, over the configured instruments,
  the longest of each instrument's shortest stored bar interval (complete,
  with rows);
- a session with no stored Snapshot or no stored bars for a configured
  instrument has no interval and is not eligible.

Every other session of the range outside the Holdout_Period is listed as
excluded with its intervals. With no eligible session nothing runs or is
written, and :class:`CadenceError` (exit 2) says that no session supports the
shortest compared cadence (Req 18.9).

**Results** (Req 18.10, ``cadence.json``). Per cadence: the count of distinct
Setup_Keys among Candidate_Setups, the count of entry fills (accepted trades,
each opened by one entry fill), the mean trades per session and the
expectancy (mean R_Multiple per trade; not available with zero trades). For
every pair of cadences, each of those four values of the shorter cadence
minus the longer cadence's, not available when either value is. Plus the
included and excluded session dates.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import ClassVar, Final, cast

import pyarrow.parquet as pq

from fse.analytics.metrics import round_fraction
from fse.backtest.manifest import DataRange
from fse.config.schema import StrategyConfig
from fse.config.schema.time import DECISION_CADENCE_MAX_S
from fse.data.cache import CacheWindowKey, DataCache
from fse.engine.step import EngineParams
from fse.engine.types import Metric
from fse.experiments.runner import (
    ConfigOutcome,
    Evaluator,
    ExperimentConfig,
    ExperimentInputError,
    ExperimentResult,
    backtest_evaluator,
    run_experiment,
)
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.pit.market_view import MAP_METRICS, heatmap_view
from fse.timekit import Instant, SessionCalendar, SessionTimes

__all__ = [
    "CADENCES_DEFAULT",
    "CADENCE_FILE_NAME",
    "CADENCE_KIND",
    "CADENCE_METRICS",
    "CadenceDiff",
    "CadenceError",
    "CadenceRow",
    "SessionIntervals",
    "cadence_configs",
    "cadence_diffs",
    "cadence_rows",
    "cadence_to_jsonable",
    "check_cadences",
    "eligible",
    "run_cadence_comparison",
    "stored_intervals",
]

CADENCE_KIND: Final = "cadence"
CADENCE_FILE_NAME: Final = "cadence.json"
CADENCES_DEFAULT: Final[tuple[int, ...]] = (5, 60, 300)
CADENCE_METRICS: Final[tuple[str, ...]] = (
    "setup_keys",
    "entry_fills",
    "trades_per_session",
    "expectancy_r",
)
NOT_AVAILABLE_TEXT: Final = "not available"

_RESOLUTION_S: Final[Mapping[str, int]] = {"1s": 1, "1m": 60}
_STORAGE_KEY: Final = b"fse.storage_interval_s"
_PLACES: Final = 6

type CadenceValue = Fraction | None
"""A cadence metric: an exact number, or ``None`` (not available)."""


class CadenceError(ExperimentInputError):
    """A cadence comparison that cannot start (exit 2); nothing was run or written."""

    exit_code: ClassVar[int] = 2


# ---------------------------------------------------------------- cadences


def check_cadences(cadences: Sequence[int]) -> tuple[int, ...]:
    """``cadences`` sorted shortest first; raises :class:`CadenceError` listing each problem.

    Each must be a whole number of seconds from 1 to 3600 (the Decision_Cadence
    range), listed once, and there must be at least two.
    """
    problems: list[str] = []
    values = tuple(cadences)
    for c in values:
        if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= DECISION_CADENCE_MAX_S:
            problems.append(
                f"cadence {c!r} must be a whole number of seconds from 1 to "
                f"{DECISION_CADENCE_MAX_S}"
            )
    good = [c for c in values if isinstance(c, int) and not isinstance(c, bool)]
    repeated = sorted({c for c in good if good.count(c) > 1})
    if repeated:
        problems.append(f"cadences listed more than once: {', '.join(map(str, repeated))}")
    if len(set(good)) < 2:
        problems.append("a cadence comparison needs at least two different cadences")
    if problems:
        raise CadenceError("; ".join(problems))
    return tuple(sorted(values))


def _name(cadence_s: int) -> str:
    return f"cadence-{cadence_s}s"


def cadence_configs(base: StrategyConfig, cadences: Sequence[int]) -> list[ExperimentConfig]:
    """One configuration per cadence, shortest first: ``base`` with that Decision_Cadence."""
    out: list[ExperimentConfig] = []
    for c in check_cadences(cadences):
        data = base.model_dump(mode="python", by_alias=True)
        data["time"]["decision_cadence_s"] = c
        out.append(ExperimentConfig(_name(c), StrategyConfig.model_validate(data)))
    return out


# ---------------------------------------------------------------- stored intervals


@dataclass(frozen=True, slots=True)
class SessionIntervals:
    """A session's stored Snapshot and bar intervals in seconds (``None``: none stored)."""

    session: date
    snapshot_s: int | None
    bar_s: int | None

    def supports(self, cadence_s: int) -> bool:
        """Whether both intervals exist and are no longer than ``cadence_s`` (Req 18.8)."""
        return (
            self.snapshot_s is not None
            and self.bar_s is not None
            and self.snapshot_s <= cadence_s
            and self.bar_s <= cadence_s
        )


def _window_interval(cache: DataCache, key: CacheWindowKey) -> int:
    with pq.ParquetFile(cache.window_path(key)) as parquet:
        metadata = parquet.schema_arrow.metadata or {}
        stored = metadata.get(_STORAGE_KEY, b"")
        resolutions = parquet.read(columns=["resolution"]).column(0).to_pylist()
    longest = max((_RESOLUTION_S[str(r)] for r in resolutions), default=1)
    return max(longest, int(stored)) if stored else longest


def stored_intervals(
    cfg: StrategyConfig,
    cache: DataCache,
    sessions: Sequence[date],
) -> tuple[SessionIntervals, ...]:
    """The stored Snapshot and bar intervals of each session (see the module notes)."""
    view_id = heatmap_view(cfg.data.heatmap_view).view_id()
    instruments = EngineParams.from_sections(cfg).instruments
    keys: set[tuple[str, str]] = {(s, m) for s in cfg.data.symbols for m in MAP_METRICS}
    shortest_bars: dict[tuple[str, date], int] = {}
    for instrument in instruments:
        for b in cache.catalog.bars_coverage(dataset="bars", instrument=instrument):
            if b.status != "complete" or not b.rows:
                continue
            bk = (instrument, b.session)
            shortest_bars[bk] = min(shortest_bars.get(bk, b.interval_s), b.interval_s)
    out: list[SessionIntervals] = []
    for session in sessions:
        per_key: dict[tuple[str, str], int] = {}
        for w in cache.catalog.windows(view_id=view_id, session=session):
            wk = (w.symbol, w.metric)
            if wk not in keys or w.status != "complete" or not w.rows:
                continue
            key = CacheWindowKey(w.symbol, cast(Metric, w.metric), view_id, session, w.start_ns)
            per_key[wk] = max(per_key.get(wk, 0), _window_interval(cache, key))
        snapshot_s = max(per_key.values()) if len(per_key) == len(keys) else None
        bars = [shortest_bars.get((i, session)) for i in instruments]
        bar_s = None if any(b is None for b in bars) else max(b for b in bars if b is not None)
        out.append(SessionIntervals(session, snapshot_s, bar_s))
    return tuple(out)


def eligible(intervals: Sequence[SessionIntervals], cadences: Sequence[int]) -> tuple[date, ...]:
    """The sessions whose stored intervals support the shortest cadence (Req 18.8)."""
    shortest = min(cadences)
    return tuple(i.session for i in intervals if i.supports(shortest))


# ---------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class CadenceRow:
    """One cadence's Req 18.10 values (``None``: not available)."""

    cadence_s: int
    name: str
    status: str
    values: Mapping[str, CadenceValue]


@dataclass(frozen=True, slots=True)
class CadenceDiff:
    """Shorter minus longer cadence for each metric (``None``: not available)."""

    shorter_s: int
    longer_s: int
    values: Mapping[str, CadenceValue]


def _values(o: ConfigOutcome) -> dict[str, CadenceValue]:
    r = o.result
    if r is None:
        return dict.fromkeys(CADENCE_METRICS)
    expectancy = r.metrics.expectancy_r
    return {
        "setup_keys": None if r.setup_keys is None else Fraction(r.setup_keys),
        "entry_fills": Fraction(r.metrics.trade_count),
        "trades_per_session": Fraction(r.metrics.trades_per_day),
        "expectancy_r": expectancy if isinstance(expectancy, Fraction) else None,
    }


def cadence_rows(result: ExperimentResult, cadences: Sequence[int]) -> tuple[CadenceRow, ...]:
    """One row per cadence, shortest first (the experiment's configuration order)."""
    ordered = check_cadences(cadences)
    return tuple(
        CadenceRow(c, o.name, o.status, _values(o))
        for c, o in zip(ordered, result.outcomes, strict=True)
    )


def cadence_diffs(rows: Sequence[CadenceRow]) -> tuple[CadenceDiff, ...]:
    """Every pair of cadences: the shorter cadence's value minus the longer's (Req 18.10)."""
    out: list[CadenceDiff] = []
    for a, b in combinations(sorted(rows, key=lambda r: r.cadence_s), 2):
        values: dict[str, CadenceValue] = {}
        for m in CADENCE_METRICS:
            x, y = a.values[m], b.values[m]
            values[m] = None if x is None or y is None else x - y
        out.append(CadenceDiff(a.cadence_s, b.cadence_s, values))
    return tuple(out)


_COUNTS: Final = frozenset({"setup_keys", "entry_fills"})


def _shown(metric: str, value: CadenceValue) -> JsonValue:
    """A count (or a difference of counts) as a whole number, a mean rounded to 6 places."""
    if value is None:
        return NOT_AVAILABLE_TEXT
    if metric in _COUNTS:
        return int(value)
    return str(round_fraction(value, _PLACES))


def cadence_to_jsonable(
    result: ExperimentResult,
    cadences: Sequence[int],
    intervals: Mapping[date, SessionIntervals],
) -> dict[str, JsonValue]:
    """``cadence.json``: sessions, per-cadence values and pairwise differences."""
    rows = cadence_rows(result, cadences)

    def interval_json(d: date) -> dict[str, JsonValue]:
        i = intervals.get(d)
        return {
            "session": d.isoformat(),
            "snapshot_interval_s": None if i is None else i.snapshot_s,
            "bar_interval_s": None if i is None else i.bar_s,
        }

    held = result.holdout
    return {
        "kind": CADENCE_KIND,
        "run_id": result.run_id,
        "seed": result.seed,
        "cadences_s": list(check_cadences(cadences)),
        "holdout": None if held is None else held.to_json(),
        "distinct_configurations": result.distinct_configurations,
        "included_sessions": [d.isoformat() for d in result.sessions],
        "excluded_sessions": [interval_json(d) for d in result.excluded],
        "cadences": [
            {
                "cadence_s": r.cadence_s,
                "name": r.name,
                "status": r.status,
                **{m: _shown(m, r.values[m]) for m in CADENCE_METRICS},
            }
            for r in rows
        ],
        "differences": [
            {
                "shorter_s": d.shorter_s,
                "longer_s": d.longer_s,
                **{m: _shown(m, d.values[m]) for m in CADENCE_METRICS},
            }
            for d in cadence_diffs(rows)
        ],
    }


# ---------------------------------------------------------------- the run


def run_cadence_comparison(
    base: StrategyConfig,
    requested: DataRange,
    *,
    cadences: Sequence[int] = CADENCES_DEFAULT,
    cache_dir: Path,
    calendar_dir: Path,
    out_dir: Path,
    writer: LogWriter,
    seed: int | None = None,
    workers: int = 1,
    evaluator: Evaluator = backtest_evaluator,
    config_path: str | None = None,
    base_times: SessionTimes | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
) -> ExperimentResult:
    """Compare ``base`` at each of ``cadences`` on the eligible sessions (module notes)."""
    configs = cadence_configs(base, cadences)
    shortest = min(cadences)
    found: dict[date, SessionIntervals] = {}

    def keep(
        cache: DataCache, calendar: SessionCalendar, sessions: tuple[date, ...]
    ) -> tuple[date, ...]:
        del calendar
        intervals = stored_intervals(base, cache, sessions)
        found.update((i.session, i) for i in intervals)
        kept = eligible(intervals, cadences)
        if not kept:
            raise CadenceError(
                f"no session of {requested.start} to {requested.end} outside the "
                f"Holdout_Period supports the shortest compared cadence of {shortest} s: "
                f"none stores both Snapshots and bars at {shortest} s or shorter"
            )
        return kept

    return run_experiment(
        CADENCE_KIND,
        base,
        configs,
        requested,
        cache_dir=cache_dir,
        calendar_dir=calendar_dir,
        out_dir=out_dir,
        writer=writer,
        seed=seed,
        workers=workers,
        evaluator=evaluator,
        outputs=lambda r: {CADENCE_FILE_NAME: cadence_to_jsonable(r, cadences, found)},
        config_path=config_path,
        base_times=base_times,
        clock=clock,
        code_version=code_version,
        session_filter=keep,
    )
