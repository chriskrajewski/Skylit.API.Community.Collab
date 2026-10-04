"""Ranking the configurations of an experiment (design §20, Req 20.14).

:func:`rank` orders the configurations of an experiment with two or more
configurations:

1. Descending by the configured objective (``experiments.ranking_objective``,
   or the sweep definition's): ``combine_pass_probability`` (the default),
   ``expectancy`` (in R) or ``profit_factor``.
2. Ties are broken by the other two objectives, in the order
   Combine_Pass probability, expectancy in R, profit factor, each descending.
3. Then by definition order.

A configuration whose objective value is not applicable (``NotApplicable``,
or ``None`` for a failed configuration) is placed after every configuration
with a number, and the same tie rules order those configurations among
themselves. A tie-breaker value that is not a number sorts after every
number for that tie-breaker.

The module is pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Final

from fse.analytics.metrics import Metrics
from fse.analytics.montecarlo import PassEstimate
from fse.config.schema.experiments import RANKING_OBJECTIVES, RankingObjective
from fse.engine.types import NotApplicable

__all__ = [
    "RANKING_OBJECTIVES",
    "ObjectiveValue",
    "ObjectiveValues",
    "RankingError",
    "objective_values",
    "rank",
    "tie_breakers",
]

type ObjectiveValue = Fraction | NotApplicable | None
"""An objective's value: a number, undefined (``NotApplicable``) or not available (``None``)."""

type ObjectiveValues = Mapping[str, ObjectiveValue]
"""One configuration's value for each of :data:`RANKING_OBJECTIVES`."""

_MISSING: Final = (1, Fraction(0))


class RankingError(ValueError):
    """An unknown ranking objective, or values without every objective."""


def objective_values(metrics: Metrics | None, estimate: PassEstimate | None) -> ObjectiveValues:
    """The three objective values of one configuration; ``None`` each when it failed."""
    if metrics is None or estimate is None:
        return dict.fromkeys(RANKING_OBJECTIVES)
    return {
        "combine_pass_probability": estimate.pass_probability,
        "expectancy": _number(metrics.expectancy_r),
        "profit_factor": _number(metrics.profit_factor),
    }


def tie_breakers(objective: RankingObjective) -> tuple[str, ...]:
    """The objectives that break ties of ``objective``, in Req 20.14 order."""
    _check_objective(objective)
    return tuple(o for o in RANKING_OBJECTIVES if o != objective)


def rank(values: Sequence[ObjectiveValues], objective: RankingObjective) -> tuple[int, ...]:
    """The indices of ``values`` (in definition order) from first-ranked to last."""
    keys = (objective, *tie_breakers(objective))
    for i, v in enumerate(values):
        missing = [k for k in RANKING_OBJECTIVES if k not in v]
        if missing:
            raise RankingError(f"configuration {i} has no value for {', '.join(missing)}")
    return tuple(sorted(range(len(values)), key=lambda i: (*(_key(values[i][k]) for k in keys), i)))


def _check_objective(objective: str) -> None:
    if objective not in RANKING_OBJECTIVES:
        raise RankingError(f"ranking objective {objective!r} is not one of {RANKING_OBJECTIVES}")


def _key(value: ObjectiveValue) -> tuple[int, Fraction]:
    if isinstance(value, Fraction):
        return 0, -value
    return _MISSING


def _number(value: Fraction | Decimal | NotApplicable) -> Fraction | NotApplicable:
    return value if isinstance(value, NotApplicable) else Fraction(value)
