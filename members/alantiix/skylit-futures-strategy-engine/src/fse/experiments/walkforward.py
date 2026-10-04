"""Walk-forward tests (design §22, Req 22.9-22.16).

**Sessions.** The requested calendar sessions the Data_Cache holds full data
for (a Snapshot for each configured symbol and metric, bars for each
instrument, as the Holdout_Period counts them), before the Holdout_Period
(Req 22.2), oldest first.

**Window pairs** (:func:`window_pairs`, Req 22.9). A training window of
``experiments.walkforward.train`` sessions, then directly the test window of
``test`` sessions. Each next pair starts ``test`` sessions later, so test
windows never overlap. A final test window shorter than ``test`` is dropped.
With fewer sessions than one training plus one test window the test is
rejected before anything is written (:class:`WalkForwardError`, Req 22.13).

**Per pair:**

1. Every configuration runs on the training window through
   :func:`fse.experiments.runner.run_experiment` (same seed, "insufficient
   sample" labels from ``experiments.walkforward.min_trades``).
2. :func:`select_configuration` picks the configuration with the highest
   walk-forward objective (``experiments.walkforward.objective``, default
   expectancy) among the completed configurations not labeled "insufficient
   sample" whose objective is a number; ties go to the configuration listed
   first (Req 22.10, 22.16).
3. The selected configuration runs, unchanged, on the test window (Req 22.11).
   With no selection the pair is "no selection" and its test sessions count
   as zero-trade sessions in the concatenated test results (Req 22.12).

**Results** (Req 22.14-22.15, ``walkforward.json``): per pair the windows,
the configurations evaluated on the training window and the selection; the
concatenated test-window results beside the in-sample results (each selected
configuration's trades on its own training window, pooled across pairs):
trade count, expectancy, Primary_Win_Rate and Combine_Pass probability; the
count of "no selection" pairs; and the distinct configurations evaluated.

Pooling is exact: the pooled expectancy and win rate are the trade-weighted
means of the windows' exact values. The pooled Combine_Pass probability is a
Monte_Carlo_Simulator estimate over the pooled session outcomes, with the
base config's account rules and ``experiments.montecarlo`` settings and the
experiment seed; it is not available (``None``) when an evaluator gives no
session outcomes. Every pooled value is not available when a contributing run
failed.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import ClassVar, Final

from fse.analytics.metrics import Metrics, round_fraction, summarize
from fse.analytics.montecarlo import (
    PassEstimate,
    estimate,
    estimate_to_jsonable,
    resolve_seed,
)
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange, RunSpec, run_manifest
from fse.backtest.runner import (
    SessionOutcome,
    cache_holdout,
    cache_sessions_with_data,
    metrics_cfg,
)
from fse.calendars import load_calendars
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.experiments import RANKING_OBJECTIVES, RankingObjective
from fse.data.cache import DataCache
from fse.data.path_guard import check_output_dir
from fse.engine.types import NotApplicable
from fse.experiments.holdout import HoldoutPeriod
from fse.experiments.ranking import ObjectiveValue, objective_values
from fse.experiments.runner import (
    ConfigOutcome,
    Evaluator,
    ExperimentConfig,
    ExperimentInputError,
    ExperimentResult,
    backtest_evaluator,
    check_configs,
    experiment_sessions,
    run_experiment,
)
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant, SessionTimes

__all__ = [
    "NO_SELECTION",
    "WALKFORWARD_FILE_NAME",
    "WALKFORWARD_KIND",
    "PairResult",
    "Pooled",
    "WalkForwardError",
    "WalkForwardResult",
    "WindowPair",
    "run_walkforward",
    "select_configuration",
    "walkforward_to_jsonable",
    "window_pairs",
]

WALKFORWARD_KIND: Final = "walkforward"
TRAIN_KIND: Final = "walkforward_train"
TEST_KIND: Final = "walkforward_test"
WALKFORWARD_FILE_NAME: Final = "walkforward.json"
PAIRS_DIR_NAME: Final = "pairs"
NO_SELECTION: Final = "no selection"
NOT_APPLICABLE_TEXT: Final = "not applicable"

_PLACES: Final = 6
_RUN_ID_HEX: Final = 16
_NA: Final = NotApplicable()


class WalkForwardError(ExperimentInputError):
    """A walk-forward test that cannot start (exit 2); nothing was written."""

    exit_code: ClassVar[int] = 2


# ---------------------------------------------------------------- windows


@dataclass(frozen=True, slots=True)
class WindowPair:
    """One training window and the test window directly after it, oldest first."""

    index: int
    train: tuple[date, ...]
    test: tuple[date, ...]


def window_pairs(sessions: Sequence[date], train: int, test: int) -> tuple[WindowPair, ...]:
    """The window pairs of ``sessions`` (oldest first): step ``test``, short last test dropped.

    Raises :class:`WalkForwardError` when a length is not a positive whole
    number or ``sessions`` holds fewer than ``train + test`` sessions (Req 22.13).
    """
    for name, value in (("train", train), ("test", test)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise WalkForwardError(f"the {name} window length must be a positive whole number")
    days = tuple(sessions)
    need = train + test
    if len(days) < need:
        raise WalkForwardError(
            f"a walk-forward test needs {need} sessions outside the Holdout_Period "
            f"({train} training plus {test} test); {len(days)} are available"
        )
    pairs: list[WindowPair] = []
    start = 0
    while start + need <= len(days):
        pairs.append(
            WindowPair(
                len(pairs),
                days[start : start + train],
                days[start + train : start + need],
            )
        )
        start += test
    return tuple(pairs)


# ---------------------------------------------------------------- selection


def _objective(o: ConfigOutcome, objective: RankingObjective) -> ObjectiveValue:
    r = o.result
    values = objective_values(
        None if r is None else r.metrics, None if r is None else r.pass_estimate
    )
    return values[objective]


def select_configuration(
    outcomes: Sequence[ConfigOutcome], objective: RankingObjective
) -> int | None:
    """The position of the configuration selected on a training window (Req 22.10, 22.16).

    The highest ``objective`` value among completed configurations not labeled
    "insufficient sample" whose value is a number; the first listed on ties.
    ``None`` ("no selection") when no configuration qualifies.
    """
    if objective not in RANKING_OBJECTIVES:
        raise WalkForwardError(
            f"walk-forward objective {objective!r} is not one of {', '.join(RANKING_OBJECTIVES)}"
        )
    best: int | None = None
    best_value: Fraction | None = None
    for i, o in enumerate(outcomes):
        if not o.completed or o.insufficient_sample:
            continue
        value = _objective(o, objective)
        if not isinstance(value, Fraction):
            continue
        if best_value is None or value > best_value:
            best, best_value = i, value
    return best


# ---------------------------------------------------------------- pooling


@dataclass(frozen=True, slots=True)
class Pooled:
    """Pooled results of several runs (Req 22.14).

    ``sessions`` counts session slots (a session in two training windows
    counts twice). ``None`` is not available (a contributing run failed, or
    no session outcomes were given for the pass estimate);
    ``NotApplicable`` is undefined (zero trades).
    """

    runs: int
    sessions: int
    trade_count: int | None
    expectancy_r: Fraction | NotApplicable | None
    win_rate_pct: Fraction | NotApplicable | None
    pass_estimate: PassEstimate | None


@dataclass(frozen=True, slots=True)
class _Part:
    metrics: Metrics | None  # None: the run failed
    sessions: int
    outcomes: tuple[SessionOutcome, ...] | None


def _weighted(
    parts: Sequence[tuple[int, Fraction | NotApplicable]],
) -> Fraction | NotApplicable:
    total = sum(n for n, _ in parts)
    if total == 0:
        return _NA
    acc = Fraction(0)
    for n, value in parts:
        if n == 0:
            continue
        if not isinstance(value, Fraction):
            return _NA
        acc += n * value
    return acc / total


def _pool(parts: Sequence[_Part], base: StrategyConfig, seed: int) -> Pooled:
    sessions = sum(p.sessions for p in parts)
    if any(p.metrics is None for p in parts):
        return Pooled(len(parts), sessions, None, None, None, None)
    metrics = [p.metrics for p in parts if p.metrics is not None]
    trades = sum(m.trade_count for m in metrics)
    expectancy = _weighted([(m.trade_count, m.expectancy_r) for m in metrics])
    win_rate = _weighted([(m.trade_count, m.primary_win_rate_pct) for m in metrics])
    est: PassEstimate | None = None
    if parts and all(p.outcomes is not None for p in parts):
        outcomes = [o for p in parts for o in (p.outcomes or ())]
        if outcomes:
            mc = base.experiments.montecarlo
            est = estimate(
                outcomes,
                base.account,
                paths=mc.paths,
                max_days=mc.max_days,
                seed=seed,
                min_sessions=mc.min_sessions,
            )
    return Pooled(len(parts), sessions, trades, expectancy, win_rate, est)


def _zero_outcomes(days: Sequence[date]) -> tuple[SessionOutcome, ...]:
    zero = Decimal("0.00")
    return tuple(SessionOutcome(d, zero, zero, False) for d in days)


# ---------------------------------------------------------------- the test


@dataclass(frozen=True, slots=True)
class PairResult:
    """One window pair: its training experiment, the selection and its test run."""

    pair: WindowPair
    train: ExperimentResult
    selected: int | None
    test: ExperimentResult | None

    @property
    def configurations_evaluated(self) -> int:
        return self.train.distinct_configurations


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """What :func:`run_walkforward` ran (Req 22.14-22.15)."""

    run_id: str
    run_dir: Path
    seed: int
    sessions: tuple[date, ...]
    holdout: HoldoutPeriod | None
    objective: RankingObjective
    configs: tuple[ExperimentConfig, ...]
    pairs: tuple[PairResult, ...]
    in_sample: Pooled
    out_of_sample: Pooled

    @property
    def no_selection_count(self) -> int:
        return sum(1 for p in self.pairs if p.selected is None)

    @property
    def distinct_configurations(self) -> int:
        """Distinct config hashes evaluated, "insufficient sample" ones included."""
        return len({o.config_hash for p in self.pairs for o in p.train.outcomes})


def _part(result: ExperimentResult, position: int) -> _Part:
    o = result.outcomes[position]
    r = o.result
    if r is None:
        return _Part(None, len(result.sessions), None)
    return _Part(r.metrics, len(r.sessions), r.outcomes)


def _run_id(base_hash: str, hashes: Sequence[str], rng: DataRange, seed: int) -> str:
    text = "|".join(
        (WALKFORWARD_KIND, base_hash, *hashes, rng.start.isoformat(), rng.end.isoformat())
    )
    digest = hashlib.sha256(f"{text}|{seed}".encode()).hexdigest()[:_RUN_ID_HEX]
    return f"{WALKFORWARD_KIND}-{digest}"


def run_walkforward(
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
    config_path: str | None = None,
    base_times: SessionTimes | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
    objective: RankingObjective | None = None,
) -> WalkForwardResult:
    """Run a walk-forward test of ``configs`` over ``requested`` (see the module notes).

    ``base`` sets the Holdout_Period, the window lengths, the "insufficient
    sample" minimum, the default objective and the pooled pass estimate's
    account rules. Every input check runs before anything is written.
    """
    check_configs(configs)
    wf = base.experiments.walkforward
    chosen = wf.objective if objective is None else objective
    if chosen not in RANKING_OBJECTIVES:
        raise WalkForwardError(
            f"walk-forward objective {chosen!r} is not one of {', '.join(RANKING_OBJECTIVES)}"
        )
    target = check_output_dir(out_dir, label="walk-forward directory")
    taken = [n for n in (WALKFORWARD_FILE_NAME, MANIFEST_FILE_NAME) if (target / n).exists()]
    if taken:
        raise WalkForwardError(
            f"the walk-forward directory {target} already holds {', '.join(taken)}; "
            "use a new directory"
        )
    used_seed = resolve_seed(seed)
    times = base.account.session_times(SessionTimes() if base_times is None else base_times)
    calendars = load_calendars(calendar_dir, requested.start, requested.end, times=times)
    calendar = calendars.exchange.sessions
    with DataCache(cache_dir) as cache:
        holdout = cache_holdout(base, cache, calendar)
        outside = experiment_sessions(calendars.run_sessions, holdout)
        sessions = cache_sessions_with_data(base, cache, calendar, outside)
    pairs = window_pairs(sessions, wf.train, wf.test)
    hashes = [config_hash(c.cfg) for c in configs]
    spec = RunSpec(
        run_id=_run_id(config_hash(base), hashes, requested, used_seed),
        kind=WALKFORWARD_KIND,
        config_hash=config_hash(base),
        data_range=requested,
        seed=used_seed,
        config_path=config_path,
        holdout=holdout,
    )

    def sub(kind: str, chosen_configs: Sequence[ExperimentConfig], days: Sequence[date],
            directory: Path) -> ExperimentResult:  # fmt: skip
        return run_experiment(
            kind,
            base,
            chosen_configs,
            DataRange(days[0], days[-1]),
            cache_dir=cache_dir,
            calendar_dir=calendar_dir,
            out_dir=directory,
            writer=writer,
            seed=used_seed,
            workers=workers,
            evaluator=evaluator,
            base_times=base_times,
            clock=clock,
            code_version=code_version,
        )

    with run_manifest(
        spec, writer=writer, out_dir=target, clock=clock, code_version=code_version
    ) as rec:
        run_dir = rec.out_dir
        results: list[PairResult] = []
        for pair in pairs:
            pair_dir = run_dir / PAIRS_DIR_NAME / f"{pair.index:03d}"
            train = sub(TRAIN_KIND, configs, pair.train, pair_dir / "train")
            selected = select_configuration(train.outcomes, chosen)
            test = None
            if selected is None:
                rec.warn(f"window pair {pair.index}: {NO_SELECTION}")
            else:
                test = sub(TEST_KIND, [configs[selected]], pair.test, pair_dir / "test")
            results.append(PairResult(pair, train, selected, test))
        for d in sorted({d for p in pairs for d in (*p.train, *p.test)}):
            rec.evaluated(d)
        mcfg = metrics_cfg(base)
        in_parts = [_part(p.train, p.selected) for p in results if p.selected is not None]
        out_parts = [
            _Part(summarize([], p.pair.test, mcfg), len(p.pair.test), _zero_outcomes(p.pair.test))
            if p.test is None
            else _part(p.test, 0)
            for p in results
        ]
        result = WalkForwardResult(
            run_id=spec.run_id,
            run_dir=run_dir,
            seed=used_seed,
            sessions=sessions,
            holdout=holdout,
            objective=chosen,
            configs=tuple(configs),
            pairs=tuple(results),
            in_sample=_pool(in_parts, base, used_seed),
            out_of_sample=_pool(out_parts, base, used_seed),
        )
        rec.output(
            writer.write_json(run_dir / WALKFORWARD_FILE_NAME, walkforward_to_jsonable(result))
        )
    return result


# ---------------------------------------------------------------- output


def _shown(value: object) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, Fraction):
        return str(round_fraction(value, _PLACES))
    if isinstance(value, NotApplicable):
        return NOT_APPLICABLE_TEXT
    raise TypeError(f"not a pooled value: {value!r}")


def _pooled_json(p: Pooled) -> dict[str, JsonValue]:
    return {
        "runs": p.runs,
        "sessions": p.sessions,
        "trade_count": p.trade_count,
        "expectancy_r": _shown(p.expectancy_r),
        "win_rate_pct": _shown(p.win_rate_pct),
        "pass_probability": None
        if p.pass_estimate is None
        else str(p.pass_estimate.pass_probability),
        "pass_estimate": None if p.pass_estimate is None else estimate_to_jsonable(p.pass_estimate),
    }


def _days(days: Sequence[date]) -> dict[str, JsonValue]:
    return {"first": days[0].isoformat(), "last": days[-1].isoformat(), "sessions": len(days)}


def _pair_json(p: PairResult, configs: Sequence[ExperimentConfig]) -> dict[str, JsonValue]:
    chosen = None if p.selected is None else configs[p.selected].name
    return {
        "index": p.pair.index,
        "train": _days(p.pair.train),
        "test": _days(p.pair.test),
        "train_dir": f"{PAIRS_DIR_NAME}/{p.pair.index:03d}/train",
        "test_dir": None if p.test is None else f"{PAIRS_DIR_NAME}/{p.pair.index:03d}/test",
        "configurations_evaluated": p.configurations_evaluated,
        "insufficient_sample": [o.name for o in p.train.outcomes if o.insufficient_sample],
        "failed": [o.name for o in p.train.outcomes if not o.completed],
        "selection": NO_SELECTION if chosen is None else "selected",
        "selected": chosen,
    }


def walkforward_to_jsonable(result: WalkForwardResult) -> dict[str, JsonValue]:
    """``walkforward.json``: the window pairs and the pooled results (Req 22.14-22.15)."""
    held = result.holdout
    return {
        "kind": WALKFORWARD_KIND,
        "run_id": result.run_id,
        "seed": result.seed,
        "objective": result.objective,
        "holdout": None if held is None else held.to_json(),
        "sessions": [d.isoformat() for d in result.sessions],
        "distinct_configurations": result.distinct_configurations,
        "no_selection_pairs": result.no_selection_count,
        "pairs": [_pair_json(p, result.configs) for p in result.pairs],
        "in_sample": _pooled_json(result.in_sample),
        "out_of_sample": _pooled_json(result.out_of_sample),
    }
