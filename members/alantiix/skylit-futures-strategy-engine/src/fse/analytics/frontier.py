"""The win-rate vs reward:risk frontier table (design §20, Req 20.9-20.11, 20.13).

:class:`FrontierRow` is one configuration of a frontier sweep (Req 20.9): its
Exit_Mode, swept R multiple (``None`` for an Exit_Mode without one), applied
min_reward_risk threshold, trade count, Primary_Win_Rate (percent),
expectancy in R, profit factor, maximum drawdown in dollars, trades per day
and Combine_Pass probability (0 to 1), plus its bootstrap intervals and its
labels.

Values are exact. ``NotApplicable`` marks a metric that is undefined for a
completed configuration; ``None`` marks every value of a failed
configuration ("not available").

- :func:`pareto_statuses` (Req 20.10): a row with any of expectancy in R,
  Primary_Win_Rate and Combine_Pass probability not a number is
  ``excluded``. Among the others, a row is a ``member`` when no other row is
  at least equal on all three and higher on at least one, else
  ``dominated``. Two rows with equal values are both members.
- :func:`reference_rows` (Req 20.11): the rows whose Primary_Win_Rate is at
  or above the reference win rate, in table order. An empty result means no
  configuration met the reference.
- ``low_sample`` (Req 20.13): the configuration has fewer accepted trades
  than ``reporting.min_sample_trades``. Every report and table row then
  labels its Primary_Win_Rate, expectancy and confidence intervals
  (:data:`LOW_SAMPLE_COLUMNS`) low-sample, the reference list included.

:func:`frontier_to_jsonable` writes the rows, the Pareto labels, the
reference list and the ranking as JSON (``frontier.json``); the Markdown and
CSV renderings are the Report_Generator's (task 28.1).

The module is pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal

from fse.analytics.bootstrap import BootstrapIntervals, Interval
from fse.analytics.metrics import round_fraction
from fse.engine.types import NotApplicable
from fse.logio.canonical_json import JsonValue

__all__ = [
    "LOW_SAMPLE_COLUMNS",
    "NO_REFERENCE_MET",
    "PARETO_DOMINATED",
    "PARETO_EXCLUDED",
    "PARETO_MEMBER",
    "FrontierRow",
    "FrontierValue",
    "ParetoStatus",
    "frontier_to_jsonable",
    "interval_to_jsonable",
    "pareto_statuses",
    "reference_pct",
    "reference_rows",
    "value_to_jsonable",
]

type FrontierValue = Fraction | NotApplicable | None
"""A number, undefined (``NotApplicable``) or not available (``None``, a failed config)."""

type ParetoStatus = Literal["member", "dominated", "excluded"]
PARETO_MEMBER: Final[ParetoStatus] = "member"
PARETO_DOMINATED: Final[ParetoStatus] = "dominated"
PARETO_EXCLUDED: Final[ParetoStatus] = "excluded"

LOW_SAMPLE_COLUMNS: Final[tuple[str, ...]] = (
    "primary_win_rate_pct",
    "expectancy_r",
    "intervals",
)
"""The row values a low-sample row labels (Req 20.13)."""

NO_REFERENCE_MET: Final = "no configuration met the reference win rate"
"""The Req 20.11 statement when the reference list is empty."""

_PLACES: Final = 6
_NA_TEXT: Final = "not applicable"

type ParetoPoint = tuple[FrontierValue, FrontierValue, FrontierValue]


@dataclass(frozen=True, slots=True)
class FrontierRow:
    """One configuration of a frontier sweep (see the module notes)."""

    index: int
    name: str
    exit_mode: str
    r_multiple: float | None
    min_reward_risk: float
    config_hash: str
    status: str
    error: str | None
    trade_count: int | None
    primary_win_rate_pct: FrontierValue
    expectancy_r: FrontierValue
    profit_factor: FrontierValue
    max_drawdown_usd: FrontierValue
    trades_per_day: FrontierValue
    pass_probability: FrontierValue
    intervals: BootstrapIntervals | None
    low_sample: bool
    insufficient_sample: bool

    @property
    def pareto_point(self) -> ParetoPoint:
        """Expectancy in R, Primary_Win_Rate and Combine_Pass probability (Req 20.10)."""
        return self.expectancy_r, self.primary_win_rate_pct, self.pass_probability


def pareto_statuses(points: Sequence[ParetoPoint]) -> tuple[ParetoStatus, ...]:
    """Each point's Pareto label, in order (see the module notes)."""
    numeric: list[tuple[Fraction, ...] | None] = [
        tuple(v for v in p if isinstance(v, Fraction)) if _all_numbers(p) else None for p in points
    ]
    out: list[ParetoStatus] = []
    for i, p in enumerate(numeric):
        if p is None:
            out.append(PARETO_EXCLUDED)
            continue
        dominated = any(
            q is not None and j != i and _dominates(q, p) for j, q in enumerate(numeric)
        )
        out.append(PARETO_DOMINATED if dominated else PARETO_MEMBER)
    return tuple(out)


def reference_pct(reference_win_rate: float) -> Fraction:
    """``reporting.reference_win_rate`` as the exact decimal it is written as."""
    return Fraction(Decimal(repr(reference_win_rate)))


def reference_rows(
    rows: Sequence[FrontierRow], reference_win_rate: float
) -> tuple[FrontierRow, ...]:
    """The rows with a Primary_Win_Rate at or above ``reference_win_rate`` percent."""
    floor = reference_pct(reference_win_rate)
    return tuple(
        r
        for r in rows
        if isinstance(r.primary_win_rate_pct, Fraction) and r.primary_win_rate_pct >= floor
    )


# ---------------------------------------------------------------- JSON


def value_to_jsonable(value: FrontierValue) -> JsonValue:
    """A rounded decimal string, ``"not applicable"``, or ``null`` when not available."""
    if value is None:
        return None
    if isinstance(value, NotApplicable):
        return _NA_TEXT
    return str(round_fraction(value, _PLACES))


def interval_to_jsonable(interval: Interval | NotApplicable) -> JsonValue:
    if isinstance(interval, NotApplicable):
        return _NA_TEXT
    return {"lower": repr(interval.lower), "upper": repr(interval.upper)}


def _intervals_json(intervals: BootstrapIntervals | None) -> JsonValue:
    if intervals is None:
        return None
    return {
        "confidence_pct": intervals.confidence_pct,
        "seed": intervals.seed,
        "resamples": intervals.resamples,
        "primary_win_rate_pct": interval_to_jsonable(intervals.primary_win_rate_pct),
        "expectancy_r": interval_to_jsonable(intervals.expectancy_r),
    }


def _row_json(row: FrontierRow, pareto: ParetoStatus) -> dict[str, JsonValue]:
    return {
        "index": row.index,
        "name": row.name,
        "exit_mode": row.exit_mode,
        "r_multiple": None if row.r_multiple is None else repr(row.r_multiple),
        "min_reward_risk": repr(row.min_reward_risk),
        "config_hash": row.config_hash,
        "status": row.status,
        "error": row.error,
        "trade_count": row.trade_count,
        "primary_win_rate_pct": value_to_jsonable(row.primary_win_rate_pct),
        "expectancy_r": value_to_jsonable(row.expectancy_r),
        "profit_factor": value_to_jsonable(row.profit_factor),
        "max_drawdown_usd": value_to_jsonable(row.max_drawdown_usd),
        "trades_per_day": value_to_jsonable(row.trades_per_day),
        "pass_probability": value_to_jsonable(row.pass_probability),
        "intervals": _intervals_json(row.intervals),
        "low_sample": row.low_sample,
        "insufficient_sample": row.insufficient_sample,
        "pareto": pareto,
    }


def frontier_to_jsonable(
    rows: Sequence[FrontierRow],
    *,
    reference_win_rate: float,
    ranking_objective: str,
    ranking: Sequence[int],
) -> dict[str, JsonValue]:
    """``frontier.json``: the rows with Pareto labels, the reference list and the ranking.

    ``ranking`` holds row positions from first-ranked to last. The
    ``ranking`` entry is ``null`` below two rows, as in ``comparison.json``
    (Req 20.14 ranks two or more configurations).
    """
    statuses = pareto_statuses([r.pareto_point for r in rows])
    met = reference_rows(rows, reference_win_rate)
    return {
        "columns": [
            "exit_mode",
            "r_multiple",
            "min_reward_risk",
            "trade_count",
            "primary_win_rate_pct",
            "expectancy_r",
            "profit_factor",
            "max_drawdown_usd",
            "trades_per_day",
            "pass_probability",
        ],
        "low_sample_columns": list(LOW_SAMPLE_COLUMNS),
        "rows": [_row_json(r, s) for r, s in zip(rows, statuses, strict=True)],
        "pareto_set": [r.name for r, s in zip(rows, statuses, strict=True) if s == PARETO_MEMBER],
        "pareto_excluded": [
            r.name for r, s in zip(rows, statuses, strict=True) if s == PARETO_EXCLUDED
        ],
        "reference": {
            "win_rate_pct": repr(reference_win_rate),
            "met": bool(met),
            "statement": None if met else NO_REFERENCE_MET,
            "rows": [
                {
                    "name": r.name,
                    "primary_win_rate_pct": value_to_jsonable(r.primary_win_rate_pct),
                    "expectancy_r": value_to_jsonable(r.expectancy_r),
                    "profit_factor": value_to_jsonable(r.profit_factor),
                    "pass_probability": value_to_jsonable(r.pass_probability),
                    "low_sample": r.low_sample,
                }
                for r in met
            ],
        },
        "ranking": None
        if len(rows) < 2
        else {
            "objective": ranking_objective,
            "order": [rows[i].name for i in ranking],
        },
    }


# ---------------------------------------------------------------- internals


def _all_numbers(point: ParetoPoint) -> bool:
    return all(isinstance(v, Fraction) for v in point)


def _dominates(q: tuple[Fraction, ...], p: tuple[Fraction, ...]) -> bool:
    """``q`` is at least ``p`` on every value and above it on one."""
    return all(a >= b for a, b in zip(q, p, strict=True)) and q != p
