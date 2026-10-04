"""The Experiment_Runner: one comparison of several Strategy_Configs (design §19-22).

Every experiment (ablation, frontier sweep, cadence comparison, walk-forward
window) goes through :func:`run_experiment`:

1. **Sessions** (Req 22.1-22.3). The Holdout_Period is computed from the
   Data_Cache with the base Strategy_Config
   (:func:`fse.backtest.runner.cache_holdout`). Every requested session in it
   is dropped, even inside the requested range; since it holds the newest
   sessions with data, what is left is the calendar sessions from the start
   of the range to the last session before it. Its first and last dates go
   in the experiment's Run_Manifest (``holdout``).
2. **Same sessions and seed** (Req 19.15). Every configuration runs on that
   one session range with one seed, used for the backtest and for the
   Monte_Carlo_Simulator. A seed is drawn when none is given and recorded in
   the Run_Manifest. The reference session list is the one most completed
   configurations evaluated (ties: the first in definition order). A completed
   configuration that evaluated another list is marked failed, so every
   compared figure covers the same session dates.
3. **Workers.** With ``workers`` above 1 the configurations run in a process
   pool; results are merged in definition order whatever order they finish in.
4. **Failures** (Req 19.16). Any error in one configuration marks it failed
   with a redacted description; the others still run.
5. **Labels and counts** (Req 22.15, 22.16). A completed configuration with
   fewer accepted trades than ``experiments.walkforward.min_trades`` of the
   base config (Shadow_Trades never count) is labeled "insufficient sample".
   The result gives the number of distinct configurations (distinct config
   hashes) evaluated, labeled ones and failed ones included.
6. **Ranking** (Req 20.14). With two or more configurations the result ranks
   them by ``ranking_objective`` (default: the base config's
   ``experiments.ranking_objective``) with the tie rules of
   :mod:`fse.experiments.ranking`.
7. **Bootstrap** (Req 20.12). With ``bootstrap_resamples`` set, every
   completed configuration also gets 95% percentile bootstrap intervals,
   drawn with the experiment seed; the Run_Manifest records the seed and
   resample count.

Outputs, through the Log_Writer, in the experiment directory: each
configuration's backtest and pass-estimate run under
``configs/<NNN>-<name>/``, ``comparison.json`` (the session list, seed,
Holdout_Period and per configuration its hash, status, metrics and pass
estimate), any file the caller adds (``outputs``) and ``run_manifest.json``.
Every input check runs before anything is written (:class:`ExperimentInputError`,
exit 2).
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Final, Literal

from fse.analytics.bootstrap import BootstrapIntervals, bootstrap_intervals
from fse.analytics.metrics import Metrics, MetricsCfg, metrics_to_jsonable, summarize
from fse.analytics.montecarlo import (
    PassEstimate,
    estimate_to_jsonable,
    resolve_seed,
    run_pass_estimate,
)
from fse.backtest.manifest import (
    MANIFEST_FILE_NAME,
    DataRange,
    RunSpec,
    SkippedSession,
    run_manifest,
)
from fse.backtest.runner import cache_holdout, has_first_target, run_backtest
from fse.calendars import load_calendars
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.bootstrap import BOOTSTRAP_RESAMPLES_MAX, BOOTSTRAP_RESAMPLES_MIN
from fse.config.schema.experiments import RANKING_OBJECTIVES, RankingObjective
from fse.data.cache import DataCache
from fse.data.path_guard import check_output_dir
from fse.experiments.holdout import HoldoutPeriod
from fse.experiments.ranking import objective_values, rank
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.logio.redact import Redactor
from fse.timekit import Instant, SessionTimes

__all__ = [
    "COMPARISON_FILE_NAME",
    "CONFIGS_DIR_NAME",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "ConfigOutcome",
    "ConfigResult",
    "ConfigStatus",
    "EvalTask",
    "Evaluator",
    "ExperimentConfig",
    "ExperimentInputError",
    "ExperimentResult",
    "OutputBuilder",
    "backtest_evaluator",
    "comparison_to_jsonable",
    "experiment_sessions",
    "metrics_cfg",
    "run_experiment",
]

COMPARISON_FILE_NAME: Final = "comparison.json"
CONFIGS_DIR_NAME: Final = "configs"
BACKTEST_DIR_NAME: Final = "backtest"
MONTECARLO_DIR_NAME: Final = "montecarlo"

type ConfigStatus = Literal["completed", "failed"]
STATUS_COMPLETED: Final[ConfigStatus] = "completed"
STATUS_FAILED: Final[ConfigStatus] = "failed"

_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}", re.ASCII)
_RUN_ID_HEX: Final = 16


class ExperimentInputError(ValueError):
    """An experiment that cannot start (exit 2); nothing was written."""

    exit_code: ClassVar[int] = 2


# ---------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """One configuration of an experiment: a path-safe ``name`` and its Strategy_Config."""

    name: str
    cfg: StrategyConfig


@dataclass(frozen=True, slots=True)
class EvalTask:
    """What one configuration's evaluation gets; picklable for a worker process.

    Every task of one experiment has the same ``data_range``, ``seed`` and
    ``bootstrap_resamples`` (``None``: no bootstrap intervals).
    """

    index: int
    name: str
    cfg: StrategyConfig
    data_range: DataRange
    seed: int
    out_dir: Path
    cache_dir: Path
    calendar_dir: Path
    code_version: str | None
    bootstrap_resamples: int | None = None


@dataclass(frozen=True, slots=True)
class ConfigResult:
    """A completed configuration: its sessions, metrics, pass estimate and intervals.

    ``intervals`` is ``None`` when the task asked for no bootstrap intervals.
    """

    run_id: str
    sessions: tuple[date, ...]
    skipped: tuple[SkippedSession, ...]
    metrics: Metrics
    pass_estimate: PassEstimate
    intervals: BootstrapIntervals | None = None


type Evaluator = Callable[[EvalTask, LogWriter], ConfigResult]
"""Runs one configuration; must be a module-level function to run in a worker process."""


@dataclass(frozen=True, slots=True)
class ConfigOutcome:
    """One configuration's outcome, completed (``result``) or failed (``error``)."""

    index: int
    name: str
    config_hash: str
    status: ConfigStatus
    error: str | None
    result: ConfigResult | None
    insufficient_sample: bool

    @property
    def completed(self) -> bool:
        return self.status == STATUS_COMPLETED


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """What :func:`run_experiment` compared, in definition order.

    ``sessions`` are the requested calendar sessions outside the
    Holdout_Period; ``data_range`` spans them and is every configuration's range.
    """

    kind: str
    run_id: str
    run_dir: Path
    requested: DataRange
    data_range: DataRange
    sessions: tuple[date, ...]
    seed: int
    holdout: HoldoutPeriod | None
    min_trades: int
    ranking_objective: RankingObjective
    outcomes: tuple[ConfigOutcome, ...]

    @property
    def compared_sessions(self) -> tuple[date, ...]:
        """The sessions every completed configuration evaluated (empty when none completed)."""
        return next((o.result.sessions for o in self.outcomes if o.result is not None), ())

    @property
    def distinct_configurations(self) -> int:
        """Distinct config hashes evaluated, "insufficient sample" and failed ones included."""
        return len({o.config_hash for o in self.outcomes})

    @property
    def ranking(self) -> tuple[int, ...]:
        """Outcome positions from first-ranked to last by ``ranking_objective`` (Req 20.14)."""
        values = [
            objective_values(
                None if o.result is None else o.result.metrics,
                None if o.result is None else o.result.pass_estimate,
            )
            for o in self.outcomes
        ]
        return rank(values, self.ranking_objective)


type OutputBuilder = Callable[[ExperimentResult], Mapping[str, JsonValue]]
"""Extra JSON files (name to content) written into the experiment directory."""


# ---------------------------------------------------------------- sessions


def experiment_sessions(
    run_sessions: Sequence[date], holdout: HoldoutPeriod | None
) -> tuple[date, ...]:
    """The requested sessions outside the Holdout_Period, in order (Req 22.2)."""
    if holdout is None:
        return tuple(run_sessions)
    return tuple(d for d in run_sessions if d < holdout.first)


# ---------------------------------------------------------------- the default evaluator


def metrics_cfg(cfg: StrategyConfig) -> MetricsCfg:
    """The :class:`MetricsCfg` of ``cfg``'s ``reporting`` section and Exit_Mode."""
    rep = cfg.reporting
    return MetricsCfg(
        scratch_tolerance_r=Decimal(repr(rep.scratch_tolerance_r)),
        min_sample_trades=rep.min_sample_trades,
        primary_win_rate=rep.primary_win_rate,
        has_first_target=has_first_target(cfg),
    )


def backtest_evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    """Backtest ``task.cfg``, summarize its trades and estimate its pass probability."""
    with DataCache(task.cache_dir) as cache:
        bt = run_backtest(
            task.cfg,
            task.data_range,
            cache,
            task.out_dir / BACKTEST_DIR_NAME,
            task.seed,
            writer=writer,
            calendar_dir=task.calendar_dir,
            code_version=task.code_version,
        )
    sessions = bt.manifest.sessions_evaluated
    mcfg = metrics_cfg(task.cfg)
    metrics = summarize(bt.trades, sessions, mcfg)
    intervals = (
        None
        if task.bootstrap_resamples is None
        else bootstrap_intervals(
            bt.trades, mcfg, seed=task.seed, resamples=task.bootstrap_resamples
        )
    )
    mc = run_pass_estimate(
        bt.run_dir,
        task.cfg,
        out_dir=task.out_dir / MONTECARLO_DIR_NAME,
        writer=writer,
        seed=task.seed,
        code_version=task.code_version,
    )
    return ConfigResult(
        run_id=bt.manifest.spec.run_id,
        sessions=sessions,
        skipped=bt.manifest.sessions_skipped,
        metrics=metrics,
        pass_estimate=mc.estimate,
        intervals=intervals,
    )


# ---------------------------------------------------------------- running


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _in_worker(evaluator: Evaluator, task: EvalTask, redactor: Redactor) -> ConfigResult:
    return evaluator(task, LogWriter(redactor))


type _Attempt = ConfigResult | str


def _attempt(evaluator: Evaluator, task: EvalTask, writer: LogWriter) -> _Attempt:
    try:
        return evaluator(task, writer)
    except Exception as exc:
        return writer.redact(_describe(exc))


def _evaluate_all(
    evaluator: Evaluator, tasks: Sequence[EvalTask], writer: LogWriter, workers: int
) -> list[_Attempt]:
    if workers == 1 or len(tasks) <= 1:
        return [_attempt(evaluator, t, writer) for t in tasks]
    out: list[_Attempt] = []
    with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
        futures: list[Future[ConfigResult]] = [
            pool.submit(_in_worker, evaluator, t, writer.redactor) for t in tasks
        ]
        for future in futures:  # definition order, whatever order they finish in
            try:
                out.append(future.result())
            except Exception as exc:
                out.append(writer.redact(_describe(exc)))
    return out


def _reference_sessions(attempts: Sequence[_Attempt]) -> tuple[date, ...] | None:
    """The session list most completed configurations evaluated; ties: definition order."""
    counts: dict[tuple[date, ...], int] = {}
    for a in attempts:
        if isinstance(a, ConfigResult):
            counts[a.sessions] = counts.get(a.sessions, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda s: counts[s])  # max keeps the first of equal counts


def _outcomes(
    configs: Sequence[ExperimentConfig],
    hashes: Sequence[str],
    attempts: Sequence[_Attempt],
    min_trades: int,
) -> tuple[ConfigOutcome, ...]:
    reference = _reference_sessions(attempts)
    out: list[ConfigOutcome] = []
    for i, (c, h, a) in enumerate(zip(configs, hashes, attempts, strict=True)):
        if isinstance(a, ConfigResult) and reference is not None and a.sessions != reference:
            a = (
                f"evaluated {len(a.sessions)} sessions, not the {len(reference)} sessions "
                "of the comparison"
            )
        if isinstance(a, str):
            out.append(ConfigOutcome(i, c.name, h, STATUS_FAILED, a, None, False))
            continue
        low = a.metrics.trade_count < min_trades
        out.append(ConfigOutcome(i, c.name, h, STATUS_COMPLETED, None, a, low))
    return tuple(out)


def _check_configs(configs: Sequence[ExperimentConfig]) -> None:
    if not configs:
        raise ExperimentInputError("an experiment needs at least one configuration")
    seen: set[str] = set()
    for c in configs:
        if not isinstance(c.name, str) or not _NAME.fullmatch(c.name):
            raise ExperimentInputError(
                f"configuration name {c.name!r} must be 1 to 64 of a-z, 0-9, _, . and -, "
                "starting with a letter or digit"
            )
        if c.name in seen:
            raise ExperimentInputError(f"configuration name {c.name!r} is used twice")
        seen.add(c.name)


def _check_resamples(resamples: int | None) -> None:
    if resamples is None:
        return
    if (
        isinstance(resamples, bool)
        or not isinstance(resamples, int)
        or not BOOTSTRAP_RESAMPLES_MIN <= resamples <= BOOTSTRAP_RESAMPLES_MAX
    ):
        raise ExperimentInputError(
            f"bootstrap resamples must be a whole number from {BOOTSTRAP_RESAMPLES_MIN:,} to "
            f"{BOOTSTRAP_RESAMPLES_MAX:,}: {resamples!r}"
        )


def _run_id(kind: str, base_hash: str, hashes: Sequence[str], rng: DataRange, seed: int) -> str:
    text = "|".join(
        (kind, base_hash, *hashes, rng.start.isoformat(), rng.end.isoformat(), str(seed))
    )
    return f"{kind}-{hashlib.sha256(text.encode()).hexdigest()[:_RUN_ID_HEX]}"


def run_experiment(
    kind: str,
    base: StrategyConfig,
    configs: Sequence[ExperimentConfig],
    requested: DataRange,
    *,
    cache_dir: Path,
    calendar_dir: Path,
    out_dir: Path,
    writer: LogWriter,
    seed: int | None = None,
    workers: int = 1,
    evaluator: Evaluator = backtest_evaluator,
    outputs: OutputBuilder | None = None,
    config_path: str | None = None,
    base_times: SessionTimes | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
    ranking_objective: RankingObjective | None = None,
    bootstrap_resamples: int | None = None,
) -> ExperimentResult:
    """Run every configuration of ``configs`` on the same sessions and seed (module notes).

    ``base`` sets the Holdout_Period, the "insufficient sample" minimum, the
    default ranking objective and the manifest's config hash. Raises
    :class:`ExperimentInputError` (or ``CalendarError``, ``PathGuardError``)
    before anything is written.
    """
    _check_configs(configs)
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ExperimentInputError(f"workers must be a whole number of at least 1: {workers!r}")
    objective = (
        base.experiments.ranking_objective if ranking_objective is None else ranking_objective
    )
    if objective not in RANKING_OBJECTIVES:
        raise ExperimentInputError(
            f"ranking objective {objective!r} is not one of {', '.join(RANKING_OBJECTIVES)}"
        )
    _check_resamples(bootstrap_resamples)
    target = check_output_dir(out_dir, label="experiment directory")
    taken = [n for n in (COMPARISON_FILE_NAME, MANIFEST_FILE_NAME) if (target / n).exists()]
    if taken:
        raise ExperimentInputError(
            f"the experiment directory {target} already holds {', '.join(taken)}; "
            "use a new directory"
        )
    used_seed = resolve_seed(seed)
    times = base.account.session_times(SessionTimes() if base_times is None else base_times)
    calendars = load_calendars(calendar_dir, requested.start, requested.end, times=times)
    with DataCache(cache_dir) as cache:
        holdout = cache_holdout(base, cache, calendars.exchange.sessions)
    sessions = experiment_sessions(calendars.run_sessions, holdout)
    if not sessions:
        where = "" if holdout is None else f" outside the Holdout_Period ({holdout.first} on)"
        raise ExperimentInputError(
            f"the range {requested.start} to {requested.end} holds no session{where}"
        )
    data_range = DataRange(sessions[0], sessions[-1])
    hashes = [config_hash(c.cfg) for c in configs]
    base_hash = config_hash(base)
    min_trades = base.experiments.walkforward.min_trades
    spec = RunSpec(
        run_id=_run_id(kind, base_hash, hashes, data_range, used_seed),
        kind=kind,
        config_hash=base_hash,
        data_range=requested,
        seed=used_seed,
        config_path=config_path,
        holdout=holdout,
    )
    with run_manifest(
        spec, writer=writer, out_dir=target, clock=clock, code_version=code_version
    ) as rec:
        run_dir = rec.out_dir
        tasks = [
            EvalTask(
                index=i,
                name=c.name,
                cfg=c.cfg,
                data_range=data_range,
                seed=used_seed,
                out_dir=run_dir / CONFIGS_DIR_NAME / f"{i:03d}-{c.name}",
                cache_dir=Path(cache_dir),
                calendar_dir=Path(calendar_dir),
                code_version=code_version,
                bootstrap_resamples=bootstrap_resamples,
            )
            for i, c in enumerate(configs)
        ]
        attempts = _evaluate_all(evaluator, tasks, writer, workers)
        outcomes = _outcomes(configs, hashes, attempts, min_trades)
        reference = next((o.result for o in outcomes if o.result is not None), None)
        if reference is not None:
            for d in reference.sessions:
                rec.evaluated(d)
            for s in reference.skipped:
                rec.skipped(s.session, s.missing, s.names)
        drawn = next(
            (o.result.intervals for o in outcomes if o.result and o.result.intervals), None
        )
        if drawn is not None:
            rec.record_bootstrap(drawn)
        result = ExperimentResult(
            kind=kind,
            run_id=spec.run_id,
            run_dir=run_dir,
            requested=requested,
            data_range=data_range,
            sessions=sessions,
            seed=used_seed,
            holdout=holdout,
            min_trades=min_trades,
            ranking_objective=objective,
            outcomes=outcomes,
        )
        for o in outcomes:
            if not o.completed:
                rec.warn(f"configuration {o.name} failed: {o.error}")
        files: dict[str, JsonValue] = {COMPARISON_FILE_NAME: comparison_to_jsonable(result)}
        if outputs is not None:
            files.update(outputs(result))
        for name, content in files.items():
            rec.output(writer.write_json(run_dir / name, content))
    return result


# ---------------------------------------------------------------- output


def _outcome_json(o: ConfigOutcome) -> dict[str, JsonValue]:
    r = o.result
    return {
        "index": o.index,
        "name": o.name,
        "config_hash": o.config_hash,
        "status": o.status,
        "error": o.error,
        "insufficient_sample": o.insufficient_sample,
        "dir": f"{CONFIGS_DIR_NAME}/{o.index:03d}-{o.name}",
        "run_id": None if r is None else r.run_id,
        "trade_count": None if r is None else r.metrics.trade_count,
        "metrics": None if r is None else metrics_to_jsonable(r.metrics),
        "pass_estimate": None if r is None else estimate_to_jsonable(r.pass_estimate),
    }


def comparison_to_jsonable(result: ExperimentResult) -> dict[str, JsonValue]:
    """``comparison.json``: sessions, seed, Holdout_Period and every configuration."""
    held = result.holdout
    return {
        "kind": result.kind,
        "run_id": result.run_id,
        "seed": result.seed,
        "requested_range": result.requested.to_json(),
        "data_range": result.data_range.to_json(),
        "calendar_sessions": [d.isoformat() for d in result.sessions],
        "sessions": [d.isoformat() for d in result.compared_sessions],
        "holdout": None if held is None else held.to_json(),
        "min_trades": result.min_trades,
        "distinct_configurations": result.distinct_configurations,
        "ranking": _ranking_json(result),
        "configurations": [_outcome_json(o) for o in result.outcomes],
    }


def _ranking_json(result: ExperimentResult) -> JsonValue:
    """The Req 20.14 ranking by name, or ``null`` for a single configuration."""
    if len(result.outcomes) < 2:
        return None
    return {
        "objective": result.ranking_objective,
        "order": [result.outcomes[i].name for i in result.ranking],
    }
