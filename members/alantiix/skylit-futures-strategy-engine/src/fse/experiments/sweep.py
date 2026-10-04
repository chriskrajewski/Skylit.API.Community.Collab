"""The win-rate vs reward:risk frontier sweep (design §20, Req 20.7-20.11, 20.14).

A sweep definition is a mapping (the command reads it from YAML):

- ``exit_modes``: the Exit_Modes to evaluate, at least one, each listed once;
- ``r_multiples``: the swept R multiples, each above 0 and at most 10 and
  listed once (default 0.5, 1, 1.5, 2 and 3);
- ``ranking_objective``: ``combine_pass_probability``, ``expectancy`` or
  ``profit_factor`` (default: the base config's
  ``experiments.ranking_objective``);
- ``bootstrap_resamples``: 1,000 to 100,000 (default: the base config's
  ``reporting.bootstrap_resamples``).

:func:`parse_sweep` checks it and raises :class:`SweepError` naming every
invalid value (Req 20.8), before any configuration is built or evaluated.

:func:`sweep_configs` builds the configurations in definition order: for
each listed Exit_Mode, one per swept R multiple when the mode takes one
(:data:`R_EXIT_MODES`), else one. Each is the base with only these keys
changed (Req 20.7):

- the Exit_Mode selection: ``exits.global.mode`` and the ``mode`` of every
  configured ``exits.per_regime`` setting become the swept Exit_Mode (stop
  rules unchanged), so every trade of the configuration uses it;
- ``exits.modes.<mode>.r_multiple``: the swept R multiple;
- ``gates.min_reward_risk.min``: the lower of the swept R multiple and the
  base threshold; the base threshold for a mode without an R multiple.

The copies go through ``model_copy``, so nothing else is re-parsed or
re-defaulted. :func:`run_sweep` runs them through
:func:`fse.experiments.runner.run_experiment` (same sessions, data range,
cost settings and seed; Holdout_Period excluded), with the sweep's ranking
objective and bootstrap resample count, and writes ``frontier.json``
(:func:`fse.analytics.frontier.frontier_to_jsonable`) beside
``comparison.json``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import ClassVar, Final, cast

from fse.analytics.frontier import FrontierRow, frontier_to_jsonable
from fse.backtest.manifest import DataRange
from fse.config.schema import StrategyConfig
from fse.config.schema.bootstrap import BOOTSTRAP_RESAMPLES_MAX, BOOTSTRAP_RESAMPLES_MIN
from fse.config.schema.exits import EXIT_MODES, EXIT_REGIMES, ExitModeName
from fse.config.schema.experiments import RANKING_OBJECTIVES, RankingObjective
from fse.engine.types import NotApplicable
from fse.experiments.runner import (
    ConfigOutcome,
    Evaluator,
    ExperimentConfig,
    ExperimentResult,
    backtest_evaluator,
    run_experiment,
)
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant, SessionTimes

__all__ = [
    "DEFAULT_R_MULTIPLES",
    "FRONTIER_FILE_NAME",
    "R_EXIT_MODES",
    "SWEEP_KEYS",
    "SWEEP_KIND",
    "SWEEP_R_MAX",
    "SweepDefinition",
    "SweepError",
    "SweepPoint",
    "SweepResult",
    "frontier_json",
    "frontier_rows",
    "parse_sweep",
    "point_name",
    "run_sweep",
    "sweep_configs",
]

SWEEP_KIND: Final = "sweep"
FRONTIER_FILE_NAME: Final = "frontier.json"
DEFAULT_R_MULTIPLES: Final[tuple[float, ...]] = (0.5, 1.0, 1.5, 2.0, 3.0)
SWEEP_R_MAX: Final = 10.0
R_EXIT_MODES: Final[tuple[ExitModeName, ...]] = ("fixed_r", "opposition_or_fixed_r")
"""The Exit_Modes with an R-multiple parameter (``exits.modes.<mode>.r_multiple``)."""
SWEEP_KEYS: Final[tuple[str, ...]] = (
    "exit_modes",
    "r_multiples",
    "ranking_objective",
    "bootstrap_resamples",
)


class SweepError(ValueError):
    """An invalid sweep definition (exit 2); nothing was evaluated or written."""

    exit_code: ClassVar[int] = 2

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid frontier sweep definition: " + "; ".join(self.problems))


@dataclass(frozen=True, slots=True)
class SweepDefinition:
    """A checked sweep definition, defaults filled in from the base config."""

    exit_modes: tuple[ExitModeName, ...]
    r_multiples: tuple[float, ...]
    ranking_objective: RankingObjective
    bootstrap_resamples: int

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "exit_modes": list(self.exit_modes),
            "r_multiples": [repr(r) for r in self.r_multiples],
            "ranking_objective": self.ranking_objective,
            "bootstrap_resamples": self.bootstrap_resamples,
        }


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One configuration of a sweep: its Exit_Mode, R multiple and applied threshold."""

    name: str
    exit_mode: ExitModeName
    r_multiple: float | None
    min_reward_risk: float
    cfg: StrategyConfig


@dataclass(frozen=True, slots=True)
class SweepResult:
    """A completed sweep: its definition, configurations, experiment and frontier rows."""

    definition: SweepDefinition
    points: tuple[SweepPoint, ...]
    experiment: ExperimentResult
    rows: tuple[FrontierRow, ...]


# ---------------------------------------------------------------- the definition


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _exit_modes(raw: Mapping[str, object], problems: list[str]) -> tuple[ExitModeName, ...]:
    listed = raw.get("exit_modes")
    if not isinstance(listed, list | tuple):
        problems.append(
            "the sweep lists no Exit_Mode"
            if listed is None
            else f"exit_modes must be a list of Exit_Modes, got {listed!r}"
        )
        return ()
    if not listed:
        problems.append("the sweep lists no Exit_Mode")
    out: list[ExitModeName] = []
    for i, mode in enumerate(listed):
        if not isinstance(mode, str) or mode not in EXIT_MODES:
            problems.append(
                f"exit_modes[{i}] {mode!r} is not an Exit_Mode (one of {', '.join(EXIT_MODES)})"
            )
        elif mode in out:
            problems.append(f"exit_modes[{i}] {mode!r} is listed twice")
        else:
            out.append(cast("ExitModeName", mode))
    return tuple(out)


def _r_multiples(raw: Mapping[str, object], problems: list[str]) -> tuple[float, ...]:
    if "r_multiples" not in raw:
        return DEFAULT_R_MULTIPLES
    listed = raw["r_multiples"]
    if not isinstance(listed, list | tuple):
        problems.append(f"r_multiples must be a list of numbers, got {listed!r}")
        return ()
    if not listed:
        problems.append("the sweep lists no R multiple")
    out: list[float] = []
    for i, r in enumerate(listed):
        if not _is_number(r) or (isinstance(r, float) and not math.isfinite(r)):
            problems.append(f"r_multiples[{i}] {r!r} is not a finite number")
            continue
        in_range = 0 < cast("float", r) <= SWEEP_R_MAX  # compared before float(): no overflow
        value = float(cast("float", r)) if in_range else math.nan
        if not in_range:
            problems.append(f"r_multiples[{i}] {r!r} must be above 0 and at most {SWEEP_R_MAX:g}")
        elif value in out:
            problems.append(f"r_multiples[{i}] {r!r} is listed twice")
        else:
            out.append(value)
    return tuple(out)


def _objective(
    raw: Mapping[str, object], base: StrategyConfig, problems: list[str]
) -> RankingObjective:
    value = raw.get("ranking_objective", base.experiments.ranking_objective)
    if not isinstance(value, str) or value not in RANKING_OBJECTIVES:
        problems.append(
            f"ranking_objective {value!r} is not one of {', '.join(RANKING_OBJECTIVES)}"
        )
        return base.experiments.ranking_objective
    return cast("RankingObjective", value)


def _resamples(raw: Mapping[str, object], base: StrategyConfig, problems: list[str]) -> int:
    value = raw.get("bootstrap_resamples", base.reporting.bootstrap_resamples)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not BOOTSTRAP_RESAMPLES_MIN <= value <= BOOTSTRAP_RESAMPLES_MAX
    ):
        problems.append(
            f"bootstrap_resamples {value!r} must be a whole number from "
            f"{BOOTSTRAP_RESAMPLES_MIN:,} to {BOOTSTRAP_RESAMPLES_MAX:,}"
        )
        return base.reporting.bootstrap_resamples
    return value


def parse_sweep(raw: object, base: StrategyConfig) -> SweepDefinition:
    """Check a sweep definition (module notes); :class:`SweepError` names every invalid value."""
    if not isinstance(raw, Mapping):
        raise SweepError([f"a sweep definition must be a mapping, got {type(raw).__name__}"])
    problems: list[str] = [
        f"unknown key {k!r} (allowed: {', '.join(SWEEP_KEYS)})" for k in raw if k not in SWEEP_KEYS
    ]
    defn = cast("Mapping[str, object]", raw)
    modes = _exit_modes(defn, problems)
    r_multiples = _r_multiples(defn, problems)
    objective = _objective(defn, base, problems)
    resamples = _resamples(defn, base, problems)
    if problems:
        raise SweepError(problems)
    return SweepDefinition(modes, r_multiples, objective, resamples)


# ---------------------------------------------------------------- configurations


def point_name(mode: str, r_multiple: float | None) -> str:
    """``<mode>`` or ``<mode>-r<R>``, for example ``fixed_r-r1.5``."""
    return mode if r_multiple is None else f"{mode}-r{r_multiple!r}"


def _variant(base: StrategyConfig, mode: ExitModeName, r: float | None) -> SweepPoint:
    exits = base.exits
    glob = exits.global_.model_copy(update={"mode": mode})
    regimes = {
        regime: setting.model_copy(update={"mode": mode})
        for regime in EXIT_REGIMES
        if (setting := exits.per_regime.get(regime)) is not None
    }
    per_regime = exits.per_regime.model_copy(update=regimes)
    modes = exits.modes
    if r is not None:
        entry = getattr(modes, mode)
        modes = modes.model_copy(update={mode: entry.model_copy(update={"r_multiple": r})})
    gate = base.gates.min_reward_risk
    threshold = gate.min if r is None else min(r, gate.min)
    gates = base.gates.model_copy(
        update={"min_reward_risk": gate.model_copy(update={"min": threshold})}
    )
    cfg = base.model_copy(
        update={
            "exits": exits.model_copy(
                update={"global_": glob, "per_regime": per_regime, "modes": modes}
            ),
            "gates": gates,
        }
    )
    return SweepPoint(point_name(mode, r), mode, r, threshold, cfg)


def sweep_configs(base: StrategyConfig, definition: SweepDefinition) -> tuple[SweepPoint, ...]:
    """The sweep's configurations in definition order (module notes)."""
    points: list[SweepPoint] = []
    for mode in definition.exit_modes:
        if mode in R_EXIT_MODES:
            points.extend(_variant(base, mode, r) for r in definition.r_multiples)
        else:
            points.append(_variant(base, mode, None))
    return tuple(points)


# ---------------------------------------------------------------- the frontier


def _number(value: object) -> Fraction | NotApplicable:
    return value if isinstance(value, NotApplicable) else Fraction(cast("Fraction", value))


def _row(point: SweepPoint, outcome: ConfigOutcome) -> FrontierRow:
    r = outcome.result
    m = None if r is None else r.metrics
    return FrontierRow(
        index=outcome.index,
        name=outcome.name,
        exit_mode=point.exit_mode,
        r_multiple=point.r_multiple,
        min_reward_risk=point.min_reward_risk,
        config_hash=outcome.config_hash,
        status=outcome.status,
        error=outcome.error,
        trade_count=None if m is None else m.trade_count,
        primary_win_rate_pct=None if m is None else _number(m.primary_win_rate_pct),
        expectancy_r=None if m is None else _number(m.expectancy_r),
        profit_factor=None if m is None else _number(m.profit_factor),
        max_drawdown_usd=None if m is None else _number(m.max_drawdown_usd),
        trades_per_day=None if m is None else _number(m.trades_per_day),
        pass_probability=None if r is None else r.pass_estimate.pass_probability,
        intervals=None if r is None else r.intervals,
        low_sample=False if m is None else m.low_sample,
        insufficient_sample=outcome.insufficient_sample,
    )


def frontier_rows(
    points: Sequence[SweepPoint], result: ExperimentResult
) -> tuple[FrontierRow, ...]:
    """One frontier row per configuration, in definition order (Req 20.9)."""
    return tuple(_row(p, o) for p, o in zip(points, result.outcomes, strict=True))


def frontier_json(
    base: StrategyConfig,
    definition: SweepDefinition,
    points: Sequence[SweepPoint],
    result: ExperimentResult,
) -> dict[str, JsonValue]:
    """``frontier.json``: the definition, the rows, Pareto set, reference list and ranking."""
    return {
        "definition": definition.to_json(),
        "distinct_configurations": result.distinct_configurations,
        **frontier_to_jsonable(
            frontier_rows(points, result),
            reference_win_rate=base.reporting.reference_win_rate,
            ranking_objective=result.ranking_objective,
            ranking=result.ranking,
        ),
    }


def run_sweep(
    base: StrategyConfig,
    definition: object,
    requested: DataRange,
    *,
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
) -> SweepResult:
    """Check ``definition``, run its configurations and write ``frontier.json``.

    Raises :class:`SweepError` (or an ``ExperimentInputError``) before
    anything is evaluated or written.
    """
    sweep = parse_sweep(definition, base)
    points = sweep_configs(base, sweep)
    result = run_experiment(
        SWEEP_KIND,
        base,
        [ExperimentConfig(p.name, p.cfg) for p in points],
        requested,
        cache_dir=cache_dir,
        calendar_dir=calendar_dir,
        out_dir=out_dir,
        writer=writer,
        seed=seed,
        workers=workers,
        evaluator=evaluator,
        outputs=lambda r: {FRONTIER_FILE_NAME: frontier_json(base, sweep, points, r)},
        config_path=config_path,
        base_times=base_times,
        clock=clock,
        code_version=code_version,
        ranking_objective=sweep.ranking_objective,
        bootstrap_resamples=sweep.bootstrap_resamples,
    )
    return SweepResult(sweep, points, result, frontier_rows(points, result))
