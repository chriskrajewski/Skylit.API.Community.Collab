"""Property 62: Pareto set.

*For any* frontier table, a configuration is in the Pareto set if and only if
all three of its values are applicable and no other configuration is at
least equal on expectancy in R, Primary_Win_Rate and Combine_Pass
probability and higher on at least one; configurations with a
not-applicable value are excluded and labeled.

**Inputs.** 0 to 12 points of (expectancy in R, Primary_Win_Rate, Combine_Pass
probability). Each value is a small fraction (so ties and equal points are
common), ``NotApplicable`` or ``None`` (a failed configuration).

**Model.** A direct transcription of Req 20.10 over every ordered pair.

**Checks.** :func:`pareto_statuses` gives ``excluded`` exactly for points
with a non-number, ``member`` exactly for the model's members, ``dominated``
otherwise; ``frontier.json`` lists the members as ``pareto_set`` and the
excluded rows as ``pareto_excluded``, in table order, and labels every row.
At least one member exists whenever a point has three numbers.

**Validates: Requirements 20.10**
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from fse.analytics.frontier import FrontierRow, frontier_to_jsonable, pareto_statuses
from fse.engine.types import NotApplicable

NA: Final = NotApplicable()

VALUES: Final = st.one_of(
    st.integers(-4, 4).map(lambda n: Fraction(n, 2)),
    st.just(NA),
    st.none(),
)
POINT_VALUES: Final = st.one_of(
    st.tuples(VALUES, VALUES, VALUES),
    st.tuples(*(st.integers(-4, 4).map(lambda n: Fraction(n, 2)) for _ in range(3))),
)

type Value = Fraction | NotApplicable | None
type Point = tuple[Value, Value, Value]


def numbers(p: Point) -> bool:
    return all(isinstance(v, Fraction) for v in p)


def model_member(i: int, points: list[Point]) -> bool:
    p = points[i]
    if not numbers(p):
        return False
    for j, q in enumerate(points):
        if j == i or not numbers(q):
            continue
        at_least = all(a >= b for a, b in zip(q, p, strict=True))  # type: ignore[operator]
        higher = any(a > b for a, b in zip(q, p, strict=True))  # type: ignore[operator]
        if at_least and higher:
            return False
    return True


def row(i: int, p: Point) -> FrontierRow:
    return FrontierRow(
        index=i, name=f"c{i}", exit_mode="fixed_r", r_multiple=1.0, min_reward_risk=1.0,
        config_hash=f"h{i}", status="failed" if None in p else "completed", error=None,
        trade_count=None if None in p else 3, primary_win_rate_pct=p[1], expectancy_r=p[0],
        profit_factor=NA, max_drawdown_usd=NA, trades_per_day=Fraction(0), pass_probability=p[2],
        intervals=None, low_sample=True, insufficient_sample=True,
    )  # fmt: skip


@given(points=st.lists(POINT_VALUES, max_size=12))
def test_pareto_set(points: list[Point]) -> None:
    statuses = pareto_statuses(points)
    assert len(statuses) == len(points)
    for i, (p, s) in enumerate(zip(points, statuses, strict=True)):
        if not numbers(p):
            assert s == "excluded"
        elif model_member(i, points):
            assert s == "member"
        else:
            assert s == "dominated"
    if any(numbers(p) for p in points):
        assert "member" in statuses

    rows = [row(i, p) for i, p in enumerate(points)]
    out = frontier_to_jsonable(
        rows, reference_win_rate=80.0, ranking_objective="expectancy", ranking=range(len(rows))
    )
    assert out["pareto_set"] == [
        r.name for r, s in zip(rows, statuses, strict=True) if s == "member"
    ]
    assert out["pareto_excluded"] == [
        r.name for r, s in zip(rows, statuses, strict=True) if s == "excluded"
    ]
    rows_json = out["rows"]
    assert isinstance(rows_json, list)
    assert [r["pareto"] for r in rows_json] == list(statuses)  # type: ignore[index,call-overload]
