"""Shared inputs for the experiment property tests (P54, P67, P68, P69).

**Light Data_Cache** (:func:`write_light_cache`). Per session with data:
one Snapshot per symbol and metric at 10:00 New York (``resolution`` and
the storage interval it is written with as given), and one bar per
instrument and stored bar interval. That is enough for the catalog checks
the Experiment_Runner makes (sessions with data, the Holdout_Period, stored
intervals); no test here runs the Backtester on it.

**Fake evaluator** (:func:`fake_evaluator`, module level so a worker process
can run it). It stands in for the backtest:

- it writes the task it got (range, seed, config hash, ``only_sessions``) to
  ``task.json`` in the configuration's directory;
- its sessions are the range's calendar sessions in ``only_sessions`` (when
  set) that the cache holds full data for, as the Backtester skips the others;
- its outcome is derived from the config hash and sessions alone
  (:func:`planned`): a failure (never with :func:`reliable_evaluator`), or
  a trade count, expectancy and win rate on a coarse grid (so ties happen),
  a pass count, a Setup_Key count and one session outcome per session.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from fse.analytics.metrics import Metrics, MetricsCfg, summarize
from fse.analytics.montecarlo import PassEstimate
from fse.backtest.runner import SessionOutcome, cache_sessions_with_data
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.data.cache import CacheWindowKey, DataCache
from fse.engine.types import Bar, NotApplicable, Resolution
from fse.experiments.runner import ConfigResult, EvalTask
from fse.logio import LogWriter
from fse.pit.market_view import MAP_METRICS
from fse.timekit import NS_PER_SECOND, ny_instant
from tests.fakes.configs import minimal_config_data
from tests.strategies.backtest_inputs import (
    CALENDAR,
    CONTRACTS,
    FAKE_SECRET,
    INSTRUMENTS,
    SPOT,
    STRIKES,
    SYMBOLS,
    VALUES,
    VIEW,
    _window_start,
    make_snapshot,
)

__all__ = [
    "ALL_SESSIONS",
    "PATHS",
    "TASK_FILE",
    "LightSession",
    "fake_evaluator",
    "light_config",
    "planned",
    "read_task",
    "reliable_evaluator",
    "write_light_cache",
]

ALL_SESSIONS: Final[tuple[date, ...]] = CALENDAR.sessions()
"""Every session of the test calendar (2026-02-02 to 2026-03-13, 29 sessions)."""
TASK_FILE: Final = "task.json"
PATHS: Final = 1_000
_NA: Final = NotApplicable()


# ---------------------------------------------------------------- the cache


@dataclass(frozen=True, slots=True)
class LightSession:
    """One session's stored data: Snapshot resolution, storage interval and bar intervals."""

    session: date
    resolution: Resolution = "1s"
    storage_s: int | None = None
    bar_intervals: tuple[int, ...] = (60,)
    instruments: tuple[str, ...] = INSTRUMENTS


def _bar(instrument: str, interval_s: int, open_ns: int) -> Bar:
    ticks = 5800 * 4 if instrument == "MES" else 20000 * 4
    return Bar(
        instrument, CONTRACTS[instrument], interval_s, open_ns,
        open_ns + interval_s * NS_PER_SECOND, ticks / 4, ticks / 4, ticks / 4, ticks / 4,
        100.0, ticks, ticks, ticks, ticks, "atlas",
    )  # fmt: skip


def write_light_cache(root: Path, sessions: Iterable[LightSession]) -> Path:
    """Store ``sessions`` in a new Data_Cache at ``root``; return ``root``."""
    for s in sessions:
        at = ny_instant(s.session, time(10, 0))
        with DataCache(root, calendar=CALENDAR, storage_interval_s=s.storage_s) as cache:
            for symbol in SYMBOLS:
                pairs = list(zip(STRIKES[symbol], VALUES, strict=False))
                for metric in MAP_METRICS:
                    snap = make_snapshot(s.session, symbol, metric, at, SPOT[symbol], pairs)
                    snap = dataclasses.replace(snap, resolution=s.resolution)
                    start = _window_start(s.session, at)
                    key = CacheWindowKey.for_view(symbol, metric, VIEW, s.session, start)
                    cache.write_window(key, [snap], "range", view=VIEW)
            for instrument in s.instruments:
                for interval in s.bar_intervals:
                    cache.bars.write_session(
                        instrument, interval, s.session, [_bar(instrument, interval, at)],
                        contract=CONTRACTS[instrument], source="atlas",
                    )  # fmt: skip
    return root


def light_config(**experiments: Any) -> StrategyConfig:
    """The minimal SPX and QQQ config with the given ``experiments`` section keys."""
    data = minimal_config_data()
    data.update(
        {
            "data": {"symbols": list(SYMBOLS), "nq_sources": ["QQQ"]},
            "experiments": experiments,
        }
    )
    return StrategyConfig.model_validate(data)


# ---------------------------------------------------------------- the fake evaluator


def planned(
    cfg_hash: str, sessions: Sequence[date], *, failures: bool = True
) -> dict[str, Any] | None:
    """The outcome of config ``cfg_hash`` on ``sessions``: ``None`` for a failure.

    With ``failures`` false no outcome is a failure.
    """
    text = "|".join((cfg_hash, *(d.isoformat() for d in sessions)))
    d = hashlib.sha256(text.encode()).digest()
    if failures and d[0] % 8 == 0:
        return None
    trades = d[1] % 12
    return {
        "trade_count": trades,
        "expectancy_r": _NA if trades == 0 else Fraction(d[2] % 5 - 2, 2),
        "win_rate": _NA if trades == 0 else Fraction(100 * (d[3] % 4), 3),
        "profit_factor": _NA if d[4] % 3 == 0 else Fraction(d[4] % 7, 2),
        "passed": d[5] % 4 * 250,
        "setup_keys": d[6] % 30,
        "nets": [Decimal(d[7 + i % 20] % 9 - 4) * 100 for i in range(len(sessions))],
    }


def _metrics(sessions: tuple[date, ...], plan: dict[str, Any]) -> Metrics:
    m = summarize([], sessions, MetricsCfg())
    return dataclasses.replace(
        m,
        trade_count=plan["trade_count"],
        trades_per_day=Fraction(plan["trade_count"], len(sessions)) if sessions else Fraction(0),
        primary_win_rate_pct=plan["win_rate"],
        win_rate_a_pct=plan["win_rate"],
        expectancy_r=plan["expectancy_r"],
        profit_factor=plan["profit_factor"],
    )


def fake_evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    """Record the task, then return (or raise) the planned outcome of its config."""
    return _evaluate(task, writer, failures=True)


def reliable_evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    """:func:`fake_evaluator` with no planned failure."""
    return _evaluate(task, writer, failures=False)


def _evaluate(task: EvalTask, writer: LogWriter, *, failures: bool) -> ConfigResult:
    cfg_hash = config_hash(task.cfg)
    task.out_dir.mkdir(parents=True)
    only = None if task.only_sessions is None else [d.isoformat() for d in task.only_sessions]
    writer.write_json(
        task.out_dir / TASK_FILE,
        {
            "range": task.data_range.to_json(),
            "seed": task.seed,
            "config_hash": cfg_hash,
            "only_sessions": only,
        },
    )
    in_range = CALENDAR.sessions(task.data_range.start, task.data_range.end)
    if task.only_sessions is not None:
        in_range = tuple(d for d in in_range if d in task.only_sessions)
    with DataCache(task.cache_dir) as cache:
        sessions = cache_sessions_with_data(task.cfg, cache, CALENDAR, in_range)
    plan = planned(cfg_hash, sessions, failures=failures)
    if plan is None:
        raise RuntimeError(f"planned failure near {FAKE_SECRET}")
    estimate = PassEstimate(
        seed=task.seed, paths=PATHS, max_days=60, min_sessions=40, sessions=len(sessions),
        truncated_by_run_account=0, passed=plan["passed"], failed=0,
        unresolved=PATHS - plan["passed"], median_days_to_pass=None, p90_days_to_pass=None,
    )  # fmt: skip
    outcomes = tuple(
        SessionOutcome(day, net, min(net, Decimal(0)), False)
        for day, net in zip(sessions, plan["nets"], strict=True)
    )
    return ConfigResult(
        run_id=f"fake-{cfg_hash[:8]}",
        sessions=sessions,
        skipped=(),
        metrics=_metrics(sessions, plan),
        pass_estimate=estimate,
        outcomes=outcomes,
        setup_keys=plan["setup_keys"],
    )


def read_task(config_dir: Path) -> dict[str, Any]:
    """The ``task.json`` that :func:`fake_evaluator` wrote in ``config_dir``."""
    data: dict[str, Any] = json.loads((config_dir / TASK_FILE).read_text(encoding="utf-8"))
    return data
