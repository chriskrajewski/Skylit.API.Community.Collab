"""The Holdout_Period: the newest sessions, reserved for out-of-sample evaluation.

Design §22 and Req 22.1. The Holdout_Period is the newest
``ceil(fraction * n)`` of the ``n`` sessions with data in the Data_Cache, with
the holdout fraction from 0.05 to 0.50 (default 0.20). The caller decides which
sessions have data and passes them in, so this module reads no file and no
clock. Sweeps, ablations, cadence comparisons and walk-forward tests drop every
session in the period (Req 22.2), and every relevant Run_Manifest records its
first and last dates as ``holdout: {first, last}`` (Req 22.3, :meth:`to_json`).

The rounding is exact. A float fraction is read through its shortest decimal
form, so ``0.07`` of 100 sessions is 7 sessions, not the 8 that
``math.ceil(0.07 * 100)`` gives after binary rounding.

**Holdout evaluation** (:func:`run_holdout_evaluation`, design §22, Req
22.3-22.8), in this order:

1. Load and validate the named Strategy_Config
   (:class:`~fse.config.loader.ConfigLoadError`, exit 2: missing file or
   failed validation).
2. Read the Holdout_Log (:func:`read_holdout_log`; :class:`HoldoutLogError`,
   exit 4, when it cannot be read). A missing file is an empty log.
3. Compute the Holdout_Period from the Data_Cache with that config
   (:class:`HoldoutRequestError`, exit 2, when no session has full data).
4. Count the earlier entries whose Holdout_Period shares a session with the
   current one. With one or more, warn on stderr that the Holdout_Period is
   no longer unseen, with the count, before the backtest starts, and record
   the warning (count included) in the evaluation's Run_Manifest.
5. Run the config, unchanged, on the Holdout_Period sessions only.
6. Append one entry to the Holdout_Log (one canonical JSON line, opened with
   ``O_APPEND`` and fsynced): the config hash, the evaluation time, the
   Holdout_Period's first and last dates, the trade count, expectancy, win
   rate and Combine_Pass probability.

Steps 1-3 run before anything is written. The log is never rewritten, and a
request that fails before step 6 leaves it byte-identical.

The evaluation imports the Backtester lazily: the Backtester and the
Run_Manifest import this module for :class:`HoldoutPeriod`.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final

from fse.analytics.metrics import Metrics, round_fraction
from fse.logio.canonical_json import JsonValue, instant_fields
from fse.timekit import Instant, SessionCalendar, SessionTimes

if TYPE_CHECKING:
    from fse.analytics.montecarlo import PassEstimate
    from fse.experiments.runner import ConfigResult, Evaluator
    from fse.logio import LogWriter

__all__ = [
    "HOLDOUT_FRACTION_DEFAULT",
    "HOLDOUT_FRACTION_MAX",
    "HOLDOUT_FRACTION_MIN",
    "HOLDOUT_KIND",
    "HOLDOUT_LOG_FILE_NAME",
    "HOLDOUT_RESULT_FILE_NAME",
    "HoldoutError",
    "HoldoutEvaluation",
    "HoldoutLogEntry",
    "HoldoutLogError",
    "HoldoutPeriod",
    "HoldoutRequestError",
    "compute_holdout_period",
    "holdout_log_entry",
    "holdout_session_count",
    "overlap_count",
    "overlap_warning",
    "read_holdout_log",
    "run_holdout_evaluation",
]

HOLDOUT_FRACTION_DEFAULT: Final = 0.20
HOLDOUT_FRACTION_MIN: Final = 0.05
HOLDOUT_FRACTION_MAX: Final = 0.50

HOLDOUT_KIND: Final = "holdout"
HOLDOUT_LOG_FILE_NAME: Final = "holdout_log.jsonl"
HOLDOUT_RESULT_FILE_NAME: Final = "holdout.json"
NOT_APPLICABLE_TEXT: Final = "not applicable"

_PLACES: Final = 6
_RUN_ID_HEX: Final = 16
_MIN_EXACT: Final = Fraction(1, 20)
_MAX_EXACT: Final = Fraction(1, 2)


class HoldoutError(ValueError):
    """The holdout fraction or the list of sessions with data is invalid."""


class HoldoutRequestError(ValueError):
    """A holdout evaluation that cannot start (exit 2); nothing was run or written."""

    exit_code: ClassVar[int] = 2


class HoldoutLogError(OSError):
    """The Holdout_Log cannot be read (exit 4); nothing was run or written."""

    exit_code: ClassVar[int] = 4


def _exact_fraction(fraction: float | Decimal | Fraction) -> Fraction:
    """``fraction`` as an exact rational, checked against 0.05 to 0.50 inclusive."""
    if isinstance(fraction, bool) or not isinstance(fraction, int | float | Decimal | Fraction):
        raise HoldoutError(f"holdout fraction must be a number, got {fraction!r}")
    if (isinstance(fraction, float) and not math.isfinite(fraction)) or (
        isinstance(fraction, Decimal) and not fraction.is_finite()
    ):
        raise HoldoutError(f"holdout fraction must be finite, got {fraction!r}")
    # repr() of a float is its shortest round-tripping decimal, e.g. "0.07".
    exact = Fraction(repr(fraction)) if isinstance(fraction, float) else Fraction(fraction)
    if not _MIN_EXACT <= exact <= _MAX_EXACT:
        raise HoldoutError(
            f"holdout fraction {fraction} is outside the allowed range "
            f"{HOLDOUT_FRACTION_MIN} to {HOLDOUT_FRACTION_MAX}"
        )
    return exact


def holdout_session_count(n_sessions_with_data: int, fraction: float | Decimal | Fraction) -> int:
    """``ceil(fraction * n)``: how many of ``n`` sessions the Holdout_Period holds."""
    if isinstance(n_sessions_with_data, bool) or not isinstance(n_sessions_with_data, int):
        raise HoldoutError(f"session count must be an integer, got {n_sessions_with_data!r}")
    if n_sessions_with_data < 0:
        raise HoldoutError(f"session count must not be negative, got {n_sessions_with_data}")
    return math.ceil(_exact_fraction(fraction) * n_sessions_with_data)


@dataclass(frozen=True, slots=True)
class HoldoutPeriod:
    """The Holdout_Period computed from ``n_sessions_with_data`` sessions.

    ``sessions`` holds the newest sessions with data, oldest first, and is never
    empty. ``fraction`` is the configured holdout fraction as given.
    """

    sessions: tuple[date, ...]
    fraction: float | Decimal | Fraction
    n_sessions_with_data: int

    def __post_init__(self) -> None:
        if not self.sessions:
            raise HoldoutError("a Holdout_Period holds at least one session")
        if any(a >= b for a, b in pairwise(self.sessions)):
            raise HoldoutError("Holdout_Period sessions must be distinct and oldest first")
        expected = holdout_session_count(self.n_sessions_with_data, self.fraction)
        if len(self.sessions) != expected:
            raise HoldoutError(
                f"a Holdout_Period of {self.n_sessions_with_data} sessions at fraction "
                f"{self.fraction} holds {expected} sessions, not {len(self.sessions)}"
            )

    @property
    def first(self) -> date:
        """The oldest session in the Holdout_Period."""
        return self.sessions[0]

    @property
    def last(self) -> date:
        """The newest session in the Holdout_Period (the newest session with data)."""
        return self.sessions[-1]

    def __len__(self) -> int:
        return len(self.sessions)

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and not isinstance(d, datetime) and d in self.sessions

    def to_json(self) -> dict[str, JsonValue]:
        """The Run_Manifest ``holdout`` field: first and last session as ``YYYY-MM-DD``."""
        return {"first": self.first.isoformat(), "last": self.last.isoformat()}


def compute_holdout_period(
    sessions_with_data: Iterable[date],
    fraction: float | Decimal | Fraction = HOLDOUT_FRACTION_DEFAULT,
    *,
    calendar: SessionCalendar | None = None,
) -> HoldoutPeriod:
    """The newest ``ceil(fraction * n)`` of the ``n`` sessions with data (Req 22.1).

    ``sessions_with_data`` may come in any order; repeated dates count once.
    With ``calendar`` every date must be a session of that calendar; a date
    outside its coverage raises ``OutsideCoverageError``.

    Raises ``HoldoutError`` for a fraction outside 0.05 to 0.50, a value that is
    not a date, a non-session date, or no sessions with data at all.
    """
    unique: set[date] = set()
    for d in sessions_with_data:
        if not isinstance(d, date) or isinstance(d, datetime):
            raise HoldoutError(f"sessions with data must be dates, got {d!r}")
        unique.add(d)
    ordered = sorted(unique)
    if calendar is not None:
        for d in ordered:
            if not calendar.is_session(d):
                raise HoldoutError(f"{d} is not a session (weekend or exchange holiday)")
    count = holdout_session_count(len(ordered), fraction)
    if count == 0:
        raise HoldoutError("no sessions with data, so there is no Holdout_Period")
    return HoldoutPeriod(
        sessions=tuple(ordered[-count:]),
        fraction=fraction,
        n_sessions_with_data=len(ordered),
    )


# ---------------------------------------------------------------- the Holdout_Log


@dataclass(frozen=True, slots=True)
class HoldoutLogEntry:
    """One Holdout_Log line: the fields the overlap check needs (``line`` is 1-based)."""

    line: int
    config_hash: str
    evaluated_at: Instant
    first: date
    last: date

    def shares_a_session(self, period: HoldoutPeriod) -> bool:
        """Whether a session of ``period`` lies in this entry's Holdout_Period (Req 22.7).

        An entry's Holdout_Period is the run of sessions with data from
        ``first`` to ``last``, so a session of ``period`` in that date span is
        a shared session.
        """
        return any(self.first <= d <= self.last for d in period.sessions)


def _entry(line: int, text: str) -> HoldoutLogEntry:
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise TypeError("an entry must be a JSON object")
    held = obj["holdout"]
    if not isinstance(held, dict):
        raise TypeError("holdout must be an object")
    cfg_hash, at = obj["config_hash"], obj["evaluated_at"]
    if not isinstance(cfg_hash, str) or not cfg_hash:
        raise TypeError("config_hash must be a non-empty string")
    if isinstance(at, bool) or not isinstance(at, int):
        raise TypeError("evaluated_at must be a whole number of nanoseconds")
    first, last = date.fromisoformat(held["first"]), date.fromisoformat(held["last"])
    if first > last:
        raise ValueError(f"holdout first {first} is after last {last}")
    return HoldoutLogEntry(line, cfg_hash, at, first, last)


def read_holdout_log(path: Path) -> tuple[HoldoutLogEntry, ...]:
    """Every entry of the Holdout_Log at ``path``, in file order; none when it does not exist.

    Raises :class:`HoldoutLogError` naming the cause when the file cannot be
    read, is not UTF-8, ends without a newline (a partial line) or holds a
    line that is not a valid entry (Req 22.6).
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise HoldoutLogError(f"the Holdout_Log {path} cannot be read: {reason}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HoldoutLogError(
            f"the Holdout_Log {path} is not UTF-8 text (byte {exc.start})"
        ) from None
    if text and not text.endswith("\n"):
        raise HoldoutLogError(f"the Holdout_Log {path} ends with a partial line")
    entries: list[HoldoutLogEntry] = []
    for i, line in enumerate(text.splitlines(), start=1):
        try:
            entries.append(_entry(i, line))
        except (KeyError, TypeError, ValueError) as exc:
            raise HoldoutLogError(
                f"the Holdout_Log {path} line {i} is not a valid entry: {type(exc).__name__}: {exc}"
            ) from None
    return tuple(entries)


def overlap_count(entries: Iterable[HoldoutLogEntry], period: HoldoutPeriod) -> int:
    """How many ``entries`` share at least one session with ``period`` (Req 22.7)."""
    return sum(1 for e in entries if e.shares_a_session(period))


def overlap_warning(count: int, period: HoldoutPeriod) -> str:
    """The Req 22.7 warning: the Holdout_Period is no longer unseen, with the entry count."""
    entries = "entry shares" if count == 1 else "entries share"
    return (
        f"the Holdout_Period ({period.first} to {period.last}) is no longer unseen: "
        f"{count} earlier Holdout_Log {entries} at least one session with it"
    )


def _shown(value: object) -> JsonValue:
    if isinstance(value, Fraction):
        return str(round_fraction(value, _PLACES))
    if isinstance(value, Decimal):
        return str(value)
    return NOT_APPLICABLE_TEXT


def holdout_log_entry(
    *,
    config_hash: str,
    evaluated_at: Instant,
    period: HoldoutPeriod,
    run_id: str,
    seed: int,
    metrics: Metrics,
    estimate: PassEstimate,
) -> dict[str, JsonValue]:
    """One Holdout_Log entry (Req 22.5; design "Storage schemas").

    Expectancy (mean R_Multiple per trade) and the Primary_Win_Rate (percent)
    are rounded to 6 places, or ``"not applicable"`` with zero trades; the
    Combine_Pass probability is the exact ``"a/b"`` share of passing paths.
    """
    return {
        "config_hash": config_hash,
        **instant_fields("evaluated_at", evaluated_at),
        "holdout": period.to_json(),
        "holdout_sessions": len(period),
        "run_id": run_id,
        "seed": seed,
        "trade_count": metrics.trade_count,
        "expectancy_r": _shown(metrics.expectancy_r),
        "primary_win_rate": metrics.primary_win_rate,
        "win_rate_pct": _shown(metrics.primary_win_rate_pct),
        "pass_probability": str(estimate.pass_probability),
        "pass_estimate_insufficient_sample": estimate.insufficient_sample,
    }


# ---------------------------------------------------------------- the evaluation


@dataclass(frozen=True, slots=True)
class HoldoutEvaluation:
    """What :func:`run_holdout_evaluation` ran, wrote and appended."""

    run_id: str
    run_dir: Path
    config_hash: str
    holdout: HoldoutPeriod
    overlapping_entries: int
    result: ConfigResult
    entry: dict[str, JsonValue]


def _run_id(cfg_hash: str, period: HoldoutPeriod, seed: int) -> str:
    text = f"{HOLDOUT_KIND}|{cfg_hash}|{period.first}|{period.last}|{len(period)}|{seed}"
    return f"{HOLDOUT_KIND}-{hashlib.sha256(text.encode()).hexdigest()[:_RUN_ID_HEX]}"


def run_holdout_evaluation(
    config_path: Path,
    *,
    log_path: Path,
    cache_dir: Path,
    calendar_dir: Path,
    out_dir: Path,
    writer: LogWriter,
    seed: int | None = None,
    evaluator: Evaluator | None = None,
    base_times: SessionTimes | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
) -> HoldoutEvaluation:
    """Evaluate the Strategy_Config at ``config_path`` on the Holdout_Period (module notes).

    ``evaluator`` defaults to the Experiment_Runner's backtest evaluator; the
    backtest and pass-estimate runs go under ``out_dir``, beside the
    evaluation's ``run_manifest.json`` and ``holdout.json``. Raises
    ``ConfigLoadError``, :class:`HoldoutLogError`, :class:`HoldoutRequestError`,
    ``CalendarError`` or ``PathGuardError`` before anything is written or run.
    """
    from fse.analytics.montecarlo import resolve_seed
    from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange, RunSpec, run_manifest
    from fse.backtest.runner import cache_holdout
    from fse.calendars import EXCHANGE_CALENDAR_FILE, load_exchange_calendar
    from fse.config.hashing import config_hash
    from fse.config.loader import load_or_raise
    from fse.data.cache import DataCache
    from fse.data.path_guard import check_output_dir
    from fse.experiments.runner import EvalTask, backtest_evaluator

    loaded = load_or_raise(config_path)
    for contradiction in loaded.warnings:
        writer.error(str(contradiction))
    cfg = loaded.config
    entries = read_holdout_log(log_path)
    target = check_output_dir(out_dir, label="holdout evaluation directory")
    taken = [n for n in (HOLDOUT_RESULT_FILE_NAME, MANIFEST_FILE_NAME) if (target / n).exists()]
    if taken:
        raise HoldoutRequestError(
            f"the holdout evaluation directory {target} already holds {', '.join(taken)}; "
            "use a new directory"
        )
    used_seed = resolve_seed(seed)
    times = cfg.account.session_times(SessionTimes() if base_times is None else base_times)
    exchange = load_exchange_calendar(Path(calendar_dir) / EXCHANGE_CALENDAR_FILE, times)
    with DataCache(cache_dir) as cache:
        period = cache_holdout(cfg, cache, exchange.sessions)
    if period is None:
        raise HoldoutRequestError(
            "the Data_Cache holds no session with full data, so there is no Holdout_Period"
        )
    cfg_hash = config_hash(cfg)
    overlapping = overlap_count(entries, period)
    warning = overlap_warning(overlapping, period) if overlapping else None
    if warning is not None:
        writer.error(f"warning: {warning}")
    spec = RunSpec(
        run_id=_run_id(cfg_hash, period, used_seed),
        kind=HOLDOUT_KIND,
        config_hash=cfg_hash,
        data_range=DataRange(period.first, period.last),
        seed=used_seed,
        config_path=str(config_path),
        holdout=period,
    )
    run = backtest_evaluator if evaluator is None else evaluator
    with run_manifest(
        spec, writer=writer, out_dir=target, clock=clock, code_version=code_version
    ) as rec:
        if warning is not None:
            rec.warn(warning)
        task = EvalTask(
            index=0,
            name=HOLDOUT_KIND,
            cfg=cfg,
            data_range=spec.data_range,
            seed=used_seed,
            out_dir=rec.out_dir,
            cache_dir=Path(cache_dir),
            calendar_dir=Path(calendar_dir),
            code_version=code_version,
            only_sessions=period.sessions,
        )
        result = run(task, writer)
        for d in result.sessions:
            rec.evaluated(d)
        for s in result.skipped:
            rec.skipped(s.session, s.missing, s.names)
        entry = holdout_log_entry(
            config_hash=cfg_hash,
            evaluated_at=clock(),
            period=period,
            run_id=spec.run_id,
            seed=used_seed,
            metrics=result.metrics,
            estimate=result.pass_estimate,
        )
        summary: dict[str, JsonValue] = {
            "entry": entry,
            "overlapping_entries": overlapping,
            "warning": warning,
            "sessions": [d.isoformat() for d in result.sessions],
            "log_path": str(log_path),
        }
        rec.output(writer.write_json(rec.out_dir / HOLDOUT_RESULT_FILE_NAME, summary))
        writer.append_json(log_path, entry, fsync=True)
    return HoldoutEvaluation(
        run_id=spec.run_id,
        run_dir=rec.out_dir,
        config_hash=cfg_hash,
        holdout=period,
        overlapping_entries=overlapping,
        result=result,
        entry=entry,
    )
