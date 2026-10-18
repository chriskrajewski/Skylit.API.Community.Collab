"""The Monte_Carlo_Simulator: a Combine pass estimate for one run (design §21, Req 21).

:func:`estimate` resamples a completed Backtester run's sessions into account
paths and counts how many pass the Combine, fail it or are still running
after the configured number of trading days.

**Inputs.** The run's :class:`~fse.backtest.runner.SessionOutcome` records
(``session_outcomes.json``), one per evaluated session, with sessions without
trades included: the trading day's net P&L and its intraday equity low,
measured from the day's start (never above 0 and never above the net). The
account rules are the run's ``account`` section (Req 21.3).

**Draws** (Req 21.1, 21.9). Session indices come from
``numpy.random.Generator(numpy.random.PCG64(seed))``. They equal one
``rng.integers(0, n, size=(paths, max_days))`` call, where row ``i`` is path
``i`` and column ``d`` its trading day ``d + 1``; they are drawn in blocks of
rows only to bound memory (:func:`draw_sessions`). Each row is used up to the
day its path stops.

**One trading day of a path** (Req 21.2, 21.4, 21.5), from the path's
balance at the start of the day:

1. With the Maximum Loss Limit enabled, ``start + low <= MLL_Floor`` fails
   the path on that day, whatever the session's close.
2. Else, with the Daily Loss Limit enabled, ``low <= -DLL`` makes the day's
   P&L exactly ``-DLL``.
3. Else the day's P&L is the session's net.
4. At the day's end, as the Account_Simulator does: the MLL_Floor becomes
   ``min(start balance, max(floor, balance - MLL))``; with the consistency
   target enabled the profit target becomes ``max(target, best day / share)``
   rounded up to a cent; with the profit target enabled the path passes when
   the balance reaches the starting balance plus the profit target.
5. The path stops at a pass, a fail or ``max_days``; a path still running
   then is unresolved.

Money is exact: whole cents in ``int64``. The consistency target of a best
day depends only on that day's P&L and does not decrease as it grows, so the
target after each day is the running maximum of the per-session targets,
computed once per session with :func:`fse.sim.account.consistency_profit_target`.

**Outputs** (:class:`PassEstimate`, Req 21.6-21.8, 21.11): the pass, fail and
unresolved counts (summing to the path count) and their shares as exact
fractions, the median and nearest-rank 90th-percentile trading days to pass
over passing paths (``None``, "not available", when none pass), the
"insufficient sample" flag for a run with fewer sessions than
``min_sessions``, and the count of sessions in which the run's own
Combine_Attempt ended mid-session (``truncated_by_run_account``, a known
approximation of resampling path-dependent sessions).

**Validation** (Req 21.12). The path count (1,000 to 1,000,000), maximum
trading days (1 to 250), minimum sessions (1 to 1,000) and a run with zero
sessions raise :class:`MonteCarloError` naming every failed check, before any
draw. :func:`load_run_outcomes` raises :class:`MonteCarloRunError` for a run
directory with no Run_Manifest, one that is not a completed backtest, or no
readable session outcomes.

**A run** (:func:`run_pass_estimate`): loads the run, checks that the given
Strategy_Config is the run's (same config hash), draws a seed with
``secrets.randbits(63)`` when none is given (Req 21.10), and writes
``pass_estimate.json`` and a Run_Manifest of kind ``montecarlo`` that records
the seed, through the Log_Writer.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import ClassVar, Final

import numpy as np
import numpy.typing as npt

from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange, RunSpec, run_manifest
from fse.backtest.runner import BACKTEST_KIND, SESSION_OUTCOMES_FILE_NAME, SessionOutcome
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.account import AccountConfig
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.sim.account import consistency_profit_target
from fse.timekit import Instant

__all__ = [
    "MAX_DAYS_MAX",
    "MAX_DAYS_MIN",
    "MIN_SESSIONS_MAX",
    "MIN_SESSIONS_MIN",
    "MONTECARLO_KIND",
    "PASS_ESTIMATE_FILE_NAME",
    "PATHS_MAX",
    "PATHS_MIN",
    "SEED_BITS",
    "STATUS_FAILED",
    "STATUS_PASSED",
    "STATUS_UNRESOLVED",
    "MonteCarloError",
    "MonteCarloRunError",
    "PassEstimate",
    "PassEstimateRun",
    "PathResults",
    "RunOutcomes",
    "SessionOutcome",
    "draw_sessions",
    "estimate",
    "estimate_to_jsonable",
    "load_run_outcomes",
    "pass_estimate_run_id",
    "resolve_seed",
    "run_pass_estimate",
    "simulate_paths",
    "validate_settings",
]

MONTECARLO_KIND: Final = "montecarlo"
PASS_ESTIMATE_FILE_NAME: Final = "pass_estimate.json"

PATHS_MIN: Final = 1_000
PATHS_MAX: Final = 1_000_000
MAX_DAYS_MIN: Final = 1
MAX_DAYS_MAX: Final = 250
MIN_SESSIONS_MIN: Final = 1
MIN_SESSIONS_MAX: Final = 1_000
SEED_BITS: Final = 63

STATUS_UNRESOLVED: Final = 0
STATUS_PASSED: Final = 1
STATUS_FAILED: Final = 2

# Index cells drawn per block: 8 MiB of int64 indices, whatever the path length.
_BLOCK_CELLS: Final = 1 << 20
_RUN_ID_HEX: Final = 16


class MonteCarloError(ValueError):
    """An invalid pass-estimate request (exit 2); the message names every failed check."""

    exit_code: ClassVar[int] = 2


class MonteCarloRunError(MonteCarloError):
    """The run has no Run_Manifest, did not complete, or has no readable session outcomes."""

    exit_code: ClassVar[int] = 4


# ---------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class PassEstimate:
    """One Combine pass estimate (Req 21.6-21.8, 21.11).

    The shares are exact fractions of ``paths``. ``median_days_to_pass`` and
    ``p90_days_to_pass`` are over passing paths only, ``None`` (not
    available) when no path passed. ``insufficient_sample`` is true when the
    run has fewer than ``min_sessions`` sessions; every report output that
    shows the estimate labels it.
    """

    seed: int
    paths: int
    max_days: int
    min_sessions: int
    sessions: int
    truncated_by_run_account: int
    passed: int
    failed: int
    unresolved: int
    median_days_to_pass: Fraction | None
    p90_days_to_pass: int | None

    @property
    def pass_probability(self) -> Fraction:
        return Fraction(self.passed, self.paths)

    @property
    def failure_probability(self) -> Fraction:
        return Fraction(self.failed, self.paths)

    @property
    def unresolved_share(self) -> Fraction:
        return Fraction(self.unresolved, self.paths)

    @property
    def insufficient_sample(self) -> bool:
        return self.sessions < self.min_sessions


@dataclass(frozen=True, slots=True)
class PathResults:
    """Per path: ``status`` (``STATUS_*``) and ``days``, the trading days it ran."""

    status: npt.NDArray[np.int8]
    days: npt.NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class RunOutcomes:
    """A completed backtest's identity and its session outcomes, in session order."""

    run_dir: Path
    run_id: str
    config_hash: str
    data_range: DataRange
    outcomes: tuple[SessionOutcome, ...]


@dataclass(frozen=True, slots=True)
class PassEstimateRun:
    """What :func:`run_pass_estimate` wrote: the estimate, its run id and directory."""

    run_id: str
    run_dir: Path
    source_run_id: str
    estimate: PassEstimate


# ---------------------------------------------------------------- validation


def _in_range(value: object, lo: int, hi: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def _setting_problems(paths: object, max_days: object, min_sessions: object) -> list[str]:
    checks = (
        ("paths", paths, PATHS_MIN, PATHS_MAX),
        ("max_days", max_days, MAX_DAYS_MIN, MAX_DAYS_MAX),
        ("min_sessions", min_sessions, MIN_SESSIONS_MIN, MIN_SESSIONS_MAX),
    )
    return [
        f"{name} must be a whole number from {lo:,} to {hi:,}, got {value!r}"
        for name, value, lo, hi in checks
        if not _in_range(value, lo, hi)
    ]


def _seed_problem(seed: object) -> str | None:
    if _in_range(seed, 0, 2**SEED_BITS - 1):
        return None
    return f"seed must be a whole number from 0 to 2**63 - 1, got {seed!r}"


def validate_settings(paths: object, max_days: object, min_sessions: object) -> None:
    """Raise :class:`MonteCarloError` naming each setting outside its range (Req 21.12)."""
    problems = _setting_problems(paths, max_days, min_sessions)
    if problems:
        raise MonteCarloError("; ".join(problems))


def resolve_seed(seed: int | None) -> int:
    """``seed``, or a fresh ``secrets.randbits(63)`` when it is ``None`` (Req 21.10)."""
    if seed is None:
        return secrets.randbits(SEED_BITS)
    problem = _seed_problem(seed)
    if problem is not None:
        raise MonteCarloError(problem)
    return seed


# ---------------------------------------------------------------- draws and paths


def draw_sessions(seed: int, n: int, paths: int, max_days: int) -> Iterator[npt.NDArray[np.int64]]:
    """The session indices of every path, in blocks of rows (see the module notes)."""
    rng = np.random.Generator(np.random.PCG64(seed))
    rows = max(1, _BLOCK_CELLS // max_days)
    for start in range(0, paths, rows):
        yield rng.integers(0, n, size=(min(rows, paths - start), max_days))


def _cents(value: Decimal, what: str) -> int:
    cents = value * 100
    if not value.is_finite() or cents != cents.to_integral_value():
        raise MonteCarloError(f"{what} {value} is not a whole number of cents")
    return int(cents)


@dataclass(frozen=True, slots=True)
class _Table:
    """The session outcomes and account rules in whole cents."""

    net: npt.NDArray[np.int64]
    low: npt.NDArray[np.int64]
    target: npt.NDArray[np.int64]  # the consistency profit target of each session as best day
    start: int
    mll: int | None
    dll: int | None
    profit_target: int | None
    configured_target: int


def _table(outcomes: Sequence[SessionOutcome], acct: AccountConfig) -> _Table:
    for o in outcomes:
        if not (o.net.is_finite() and o.intraday_low.is_finite()):
            raise MonteCarloError(
                f"session {o.session}: the net {o.net} and intraday low {o.intraday_low} "
                "must be finite"
            )
        if o.intraday_low > 0 or o.intraday_low > o.net:
            raise MonteCarloError(
                f"session {o.session}: the intraday low {o.intraday_low} is above 0 or above "
                f"the net {o.net}"
            )
    configured = acct.profit_target.value
    consistency = acct.consistency_target
    targets = [
        consistency_profit_target(configured, o.net, consistency.pct)
        if consistency.enabled
        else configured
        for o in outcomes
    ]
    return _Table(
        net=np.array([_cents(o.net, f"{o.session} net") for o in outcomes], dtype=np.int64),
        low=np.array(
            [_cents(o.intraday_low, f"{o.session} intraday low") for o in outcomes],
            dtype=np.int64,
        ),
        target=np.array([_cents(t, "profit target") for t in targets], dtype=np.int64),
        start=_cents(acct.starting_balance.value, "starting balance"),
        mll=_cents(acct.maximum_loss_limit.value, "MLL")
        if acct.maximum_loss_limit.enabled
        else None,
        dll=_cents(acct.daily_loss_limit.value, "DLL") if acct.daily_loss_limit.enabled else None,
        profit_target=_cents(configured, "profit target") if acct.profit_target.enabled else None,
        configured_target=_cents(configured, "profit target"),
    )


def _simulate(table: _Table, draws: npt.NDArray[np.int64]) -> PathResults:
    rows, max_days = draws.shape
    status = np.zeros(rows, dtype=np.int8)
    days = np.full(rows, max_days, dtype=np.int64)
    balance = np.full(rows, table.start, dtype=np.int64)
    floor = np.full(rows, table.start - (table.mll or 0), dtype=np.int64)
    target = np.full(rows, table.configured_target, dtype=np.int64)
    active = np.arange(rows)
    for d in range(max_days):
        if active.size == 0:
            break
        idx = draws[active, d]
        low = table.low[idx]
        bal = balance[active]
        if table.mll is not None:
            fails = bal + low <= floor[active]
            status[active[fails]] = STATUS_FAILED
            days[active[fails]] = d + 1
            keep = ~fails
            active, idx, low, bal = active[keep], idx[keep], low[keep], bal[keep]
        pnl = table.net[idx]
        day_target = table.target[idx]
        if table.dll is not None:
            hit = low <= -table.dll
            pnl = np.where(hit, -table.dll, pnl)
            # A -DLL best day is negative, so its consistency target is the configured one.
            day_target = np.where(hit, table.configured_target, day_target)
        bal = bal + pnl
        balance[active] = bal
        if table.mll is not None:
            floor[active] = np.minimum(table.start, np.maximum(floor[active], bal - table.mll))
        tgt = np.maximum(target[active], day_target)
        target[active] = tgt
        if table.profit_target is not None:
            passes = bal >= table.start + tgt
            status[active[passes]] = STATUS_PASSED
            days[active[passes]] = d + 1
            active = active[~passes]
    return PathResults(status, days)


def simulate_paths(
    outcomes: Sequence[SessionOutcome], acct: AccountConfig, draws: npt.NDArray[np.int64]
) -> PathResults:
    """Run every path of ``draws`` (rows of session indices into ``outcomes``)."""
    return _simulate(_table(outcomes, acct), draws)


# ---------------------------------------------------------------- the estimate


def _median(sorted_days: npt.NDArray[np.int64]) -> Fraction:
    k = sorted_days.size
    mid = k // 2
    if k % 2:
        return Fraction(int(sorted_days[mid]))
    return Fraction(int(sorted_days[mid - 1]) + int(sorted_days[mid]), 2)


def _nearest_rank_p90(sorted_days: npt.NDArray[np.int64]) -> int:
    k = sorted_days.size
    return int(sorted_days[-(-9 * k // 10) - 1])


def estimate(
    outcomes: Sequence[SessionOutcome],
    acct: AccountConfig,
    *,
    paths: int,
    max_days: int,
    seed: int,
    min_sessions: int,
) -> PassEstimate:
    """The Combine pass estimate of one run's sessions (see the module notes).

    Raises :class:`MonteCarloError` for zero sessions, a setting out of range
    or a bad seed, before any draw (Req 21.12).
    """
    problems = _setting_problems(paths, max_days, min_sessions)
    seed_problem = _seed_problem(seed)
    if seed_problem is not None:
        problems.append(seed_problem)
    if not outcomes:
        problems.append("the run has zero sessions")
    if problems:
        raise MonteCarloError("; ".join(problems))
    table = _table(outcomes, acct)
    counts = [0, 0, 0]
    passing: list[npt.NDArray[np.int64]] = []
    for draws in draw_sessions(seed, len(outcomes), paths, max_days):
        result = _simulate(table, draws)
        for code in (STATUS_UNRESOLVED, STATUS_PASSED, STATUS_FAILED):
            counts[code] += int(np.count_nonzero(result.status == code))
        passing.append(result.days[result.status == STATUS_PASSED])
    days = np.sort(np.concatenate(passing))
    return PassEstimate(
        seed=seed,
        paths=paths,
        max_days=max_days,
        min_sessions=min_sessions,
        sessions=len(outcomes),
        truncated_by_run_account=sum(o.truncated_by_run_account for o in outcomes),
        passed=counts[STATUS_PASSED],
        failed=counts[STATUS_FAILED],
        unresolved=counts[STATUS_UNRESOLVED],
        median_days_to_pass=_median(days) if days.size else None,
        p90_days_to_pass=_nearest_rank_p90(days) if days.size else None,
    )


def estimate_to_jsonable(est: PassEstimate) -> dict[str, JsonValue]:
    """``pass_estimate.json``: counts, shares as exact ``"a/b"`` strings, days to pass."""
    median = est.median_days_to_pass
    return {
        "seed": est.seed,
        "paths": est.paths,
        "max_days": est.max_days,
        "min_sessions": est.min_sessions,
        "sessions": est.sessions,
        "insufficient_sample": est.insufficient_sample,
        "truncated_by_run_account": est.truncated_by_run_account,
        "passed": est.passed,
        "failed": est.failed,
        "unresolved": est.unresolved,
        "pass_probability": str(est.pass_probability),
        "failure_probability": str(est.failure_probability),
        "unresolved_share": str(est.unresolved_share),
        "median_days_to_pass": None if median is None else str(median),
        "p90_days_to_pass": est.p90_days_to_pass,
    }


# ---------------------------------------------------------------- a run


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MonteCarloRunError(f"{path} does not exist") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MonteCarloRunError(f"{path} cannot be read: {exc}") from None


def _outcome(obj: object) -> SessionOutcome:
    if not isinstance(obj, dict):
        raise TypeError("a session outcome must be an object")
    truncated = obj["truncated_by_run_account"]
    if not isinstance(truncated, bool):
        raise TypeError("truncated_by_run_account must be true or false")
    net, low = Decimal(obj["net"]), Decimal(obj["intraday_low"])
    if not (net.is_finite() and low.is_finite()):
        raise ValueError(f"net {obj['net']!r} and intraday low {obj['intraday_low']!r}: not finite")
    return SessionOutcome(
        session=date.fromisoformat(obj["session"]),
        net=net,
        intraday_low=low,
        truncated_by_run_account=truncated,
    )


def load_run_outcomes(run_dir: Path) -> RunOutcomes:
    """Read a completed backtest's Run_Manifest and session outcomes (Req 21.1, 21.12)."""
    manifest = _read_json(run_dir / MANIFEST_FILE_NAME)
    if not isinstance(manifest, dict) or manifest.get("kind") != BACKTEST_KIND:
        raise MonteCarloRunError(f"{run_dir / MANIFEST_FILE_NAME} is not a backtest Run_Manifest")
    if manifest.get("status") != "completed":
        raise MonteCarloRunError(
            f"backtest {manifest.get('run_id')} ended with status {manifest.get('status')}; "
            "only a completed run has a pass estimate"
        )
    path = run_dir / SESSION_OUTCOMES_FILE_NAME
    raw = _read_json(path)
    try:
        if not isinstance(raw, list):
            raise TypeError("the file must hold a list")
        outcomes = tuple(_outcome(o) for o in raw)
        rng = manifest["data_range"]
        data_range = DataRange(date.fromisoformat(rng["start"]), date.fromisoformat(rng["end"]))
        run_id = str(manifest["run_id"])
        cfg_hash = str(manifest["config_hash"])
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise MonteCarloRunError(
            f"{run_dir} holds a bad Run_Manifest or {path.name}: {exc}"
        ) from None
    return RunOutcomes(run_dir, run_id, cfg_hash, data_range, outcomes)


def pass_estimate_run_id(
    source: str, paths: int, max_days: int, min_sessions: int, seed: int
) -> str:
    """The run id of a pass estimate: ``mc-`` and a hash of its source run, settings and seed."""
    text = f"{source}|{paths}|{max_days}|{min_sessions}|{seed}"
    return f"mc-{hashlib.sha256(text.encode()).hexdigest()[:_RUN_ID_HEX]}"


def run_pass_estimate(
    run_dir: Path,
    cfg: StrategyConfig,
    *,
    out_dir: Path,
    writer: LogWriter,
    seed: int | None = None,
    config_path: str | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
) -> PassEstimateRun:
    """Estimate the pass probability of the run in ``run_dir`` and write it to ``out_dir``.

    ``cfg`` must be the run's Strategy_Config (its config hash is checked);
    its ``account`` section sets the rules and ``experiments.montecarlo`` the
    path count, maximum trading days and minimum sessions. Every check runs
    before anything is written: the settings, the seed, the session values and
    an ``out_dir`` that already holds a pass estimate or Run_Manifest.
    """
    source = load_run_outcomes(run_dir)
    mc = cfg.experiments.montecarlo
    validate_settings(mc.paths, mc.max_days, mc.min_sessions)
    if not source.outcomes:
        raise MonteCarloError(f"the run {source.run_id} has zero sessions")
    cfg_hash = config_hash(cfg)
    if cfg_hash != source.config_hash:
        raise MonteCarloError(
            f"the Strategy_Config (hash {cfg_hash}) is not the run's (hash {source.config_hash})"
        )
    used = resolve_seed(seed)
    _table(source.outcomes, cfg.account)  # the session values, before the manifest opens
    taken = [n for n in (PASS_ESTIMATE_FILE_NAME, MANIFEST_FILE_NAME) if (out_dir / n).exists()]
    if taken:
        raise MonteCarloError(
            f"the pass-estimate directory {out_dir} already holds {', '.join(taken)}; "
            "use a new directory"
        )
    run_id = pass_estimate_run_id(source.run_id, mc.paths, mc.max_days, mc.min_sessions, used)
    spec = RunSpec(
        run_id=run_id,
        kind=MONTECARLO_KIND,
        config_hash=cfg_hash,
        data_range=source.data_range,
        seed=used,
        config_path=config_path,
    )
    with run_manifest(
        spec, writer=writer, out_dir=out_dir, clock=clock, code_version=code_version
    ) as rec:
        for o in source.outcomes:
            rec.evaluated(o.session)
        est = estimate(
            source.outcomes,
            cfg.account,
            paths=mc.paths,
            max_days=mc.max_days,
            seed=used,
            min_sessions=mc.min_sessions,
        )
        out = rec.out_dir / PASS_ESTIMATE_FILE_NAME
        writer.write_json(out, {"source_run_id": source.run_id, **estimate_to_jsonable(est)})
        rec.output(out)
    return PassEstimateRun(run_id, rec.out_dir, source.run_id, est)
