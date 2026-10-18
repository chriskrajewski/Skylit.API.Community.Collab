"""Property 63: Ranking and reference list.

*For any* set of configuration results, the ranking is descending by the
configured objective, ties broken by the other objectives in the order
Combine_Pass probability, expectancy in R, profit factor, then definition
order, with not-applicable objectives last; the reference list holds exactly
the configurations with Primary_Win_Rate at or above the reference; and every
value from fewer than the minimum trades carries the low-sample label.

**Inputs.** 2 to 8 configurations of one frontier sweep. Each is failed, or
completed with 0 to 8 one-contract trades whose nets come from a small set
(so ties are common), a pass count out of 1,000 paths, real
:func:`summarize` metrics and real bootstrap intervals. A ranking objective,
a reference win rate and a sample minimum.

**Model.** A pairwise comparison written from Req 20.14: per objective in
the order (configured, then the others in Req order), a number beats a
non-number, a higher number beats a lower one; then definition order.

**Checks.** ``ExperimentResult.ranking`` and ``frontier.json``'s ranking
equal the model's order. The reference rows are exactly the completed rows
with Primary_Win_Rate >= the reference, in table order, with their
expectancy, profit factor and pass probability; when none qualifies,
``frontier.json`` says no configuration met the reference. A row, its
intervals and its reference entry are low-sample exactly when its trade
count is below the minimum.

**Validates: Requirements 20.11, 20.13, 20.14**
"""

from __future__ import annotations

import functools
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from fse.analytics.bootstrap import bootstrap_intervals
from fse.analytics.frontier import NO_REFERENCE_MET, reference_pct, reference_rows
from fse.analytics.metrics import MetricsCfg, summarize
from fse.analytics.montecarlo import PassEstimate
from fse.backtest.manifest import DataRange
from fse.config.schema import StrategyConfig
from fse.config.schema.experiments import RANKING_OBJECTIVES, RankingObjective
from fse.engine.types import Fill, SetupKey, Trade
from fse.experiments.ranking import ObjectiveValues, objective_values
from fse.experiments.runner import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    ConfigOutcome,
    ConfigResult,
    ExperimentResult,
)
from fse.experiments.sweep import (
    SweepDefinition,
    SweepPoint,
    frontier_json,
    frontier_rows,
)
from fse.timekit import NS_PER_MINUTE
from tests.fakes.configs import minimal_config_data

SESSION: Final = date(2026, 3, 2)
T0: Final = 1_767_600_000 * 1_000_000_000
PATHS: Final = 1_000
NETS: Final = ("100.00", "-100.00", "50.00", "0.00", "-25.00", "200.00")
REFERENCES: Final = (25.0, 33.3, 50.0, 66.7, 80.0, 100.0)
OBJECTIVES: Final[tuple[RankingObjective, ...]] = (
    "combine_pass_probability",
    "expectancy",
    "profit_factor",
)
BASE: Final = StrategyConfig.model_validate(minimal_config_data())


def trade(i: int, net: str) -> Trade:
    key = SetupKey("MES", "gatekeeper_fade", 5800.0, "long", SESSION, i + 1)
    entry = Fill(f"e{i}", T0, 23120, 1, Decimal("0.00"))
    exit_fill = Fill(f"x{i}", T0 + (i + 1) * NS_PER_MINUTE, 23120, 1, Decimal("0.00"))
    r = Decimal(100)
    return Trade(
        setup_key=key, entry_fill=entry, initial_stop=23100, qty_at_entry=1, exits=(exit_fill,),
        net=Decimal(net), r=r, r_multiple=Decimal(net) / r, reached_tp1=False,
        mae_r=Decimal(0), mfe_r=Decimal(0), missing_bars=0, shadow=False,
    )  # fmt: skip


@st.composite
def outcomes(draw: st.DrawFn, i: int, min_sample: int, seed: int) -> ConfigOutcome:
    name = f"fixed_r-r{i}.0"
    if draw(st.integers(0, 5), label=f"fail {i}") == 0:
        return ConfigOutcome(i, name, f"h{i}", STATUS_FAILED, "boom", None, False)
    nets = draw(st.lists(st.sampled_from(NETS), max_size=8), label=f"nets {i}")
    trades = [trade(k, n) for k, n in enumerate(nets)]
    cfg = MetricsCfg(min_sample_trades=min_sample)
    passed = draw(st.sampled_from((0, 100, 250, 500)), label=f"passed {i}")
    estimate = PassEstimate(
        seed=seed, paths=PATHS, max_days=60, min_sessions=40, sessions=1,
        truncated_by_run_account=0, passed=passed, failed=0, unresolved=PATHS - passed,
        median_days_to_pass=None, p90_days_to_pass=None,
    )  # fmt: skip
    result = ConfigResult(
        run_id=f"run-{i}",
        sessions=(SESSION,),
        skipped=(),
        metrics=summarize(trades, (SESSION,), cfg),
        pass_estimate=estimate,
        intervals=bootstrap_intervals(trades, cfg, seed=seed, resamples=1_000),
    )
    return ConfigOutcome(i, name, f"h{i}", STATUS_COMPLETED, None, result, len(trades) < 3)


def model_order(values: list[ObjectiveValues], objective: RankingObjective) -> list[int]:
    keys = [objective, *(o for o in RANKING_OBJECTIVES if o != objective)]

    def compare(i: int, j: int) -> int:
        for k in keys:
            a, b = values[i][k], values[j][k]
            a_num, b_num = isinstance(a, Fraction), isinstance(b, Fraction)
            if a_num and not b_num:
                return -1
            if b_num and not a_num:
                return 1
            if a_num and b_num and a != b:
                return -1 if a > b else 1  # type: ignore[operator]
        return -1 if i < j else (1 if i > j else 0)

    return sorted(range(len(values)), key=functools.cmp_to_key(compare))


@st.composite
def cases(draw: st.DrawFn) -> tuple[ExperimentResult, list[SweepPoint], int, float]:
    n = draw(st.integers(2, 8), label="configs")
    min_sample = draw(st.integers(1, 10), label="min sample")
    seed = draw(st.integers(0, 2**63 - 1), label="seed")
    objective: RankingObjective = draw(st.sampled_from(OBJECTIVES), label="objective")
    reference = draw(st.sampled_from(REFERENCES), label="reference")
    outs = tuple(draw(outcomes(i, min_sample, seed)) for i in range(n))
    result = ExperimentResult(
        kind="sweep", run_id="sweep-test", run_dir=Path("unused"),
        requested=DataRange(SESSION, SESSION), data_range=DataRange(SESSION, SESSION),
        sessions=(SESSION,), seed=seed, holdout=None, min_trades=3,
        ranking_objective=objective, outcomes=outs,
    )  # fmt: skip
    points = [SweepPoint(o.name, "fixed_r", float(o.index + 1), 1.0, BASE) for o in outs]
    return result, points, min_sample, reference


@given(case=cases())
def test_ranking_and_reference_list(
    case: tuple[ExperimentResult, list[SweepPoint], int, float],
) -> None:
    result, points, min_sample, reference = case
    values = [
        objective_values(
            None if o.result is None else o.result.metrics,
            None if o.result is None else o.result.pass_estimate,
        )
        for o in result.outcomes
    ]
    order = model_order(values, result.ranking_objective)
    assert list(result.ranking) == order

    base = BASE.model_copy(
        update={"reporting": BASE.reporting.model_copy(update={"reference_win_rate": reference})}
    )
    defn = SweepDefinition(("fixed_r",), (1.0,), result.ranking_objective, 1_000)
    frontier = frontier_json(base, defn, points, result)
    ranking = frontier["ranking"]
    assert isinstance(ranking, dict)
    assert ranking["order"] == [result.outcomes[i].name for i in order]

    # The reference list: exactly the rows at or above the reference win rate.
    rows = frontier_rows(points, result)
    floor = reference_pct(reference)
    expected = [
        r
        for r in rows
        if isinstance(r.primary_win_rate_pct, Fraction) and r.primary_win_rate_pct >= floor
    ]
    assert list(reference_rows(rows, reference)) == expected
    ref = frontier["reference"]
    assert isinstance(ref, dict)
    assert ref["met"] is bool(expected)
    assert ref["statement"] == (None if expected else NO_REFERENCE_MET)
    ref_rows = ref["rows"]
    assert isinstance(ref_rows, list)
    assert [e["name"] for e in ref_rows] == [r.name for r in expected]  # type: ignore[index,call-overload]
    for entry, r in zip(ref_rows, expected, strict=True):
        assert isinstance(entry, dict)
        assert entry["low_sample"] is r.low_sample
        for key in ("expectancy_r", "profit_factor", "pass_probability"):
            assert key in entry

    # Low-sample labels: every row (and its intervals) below the minimum trades.
    rows_json = frontier["rows"]
    assert isinstance(rows_json, list)
    for r, rj, o in zip(rows, rows_json, result.outcomes, strict=True):
        assert isinstance(rj, dict)
        if o.result is None:
            assert r.trade_count is None
            assert rj["intervals"] is None
            continue
        low = o.result.metrics.trade_count < min_sample
        assert r.low_sample is low
        assert rj["low_sample"] is low
        assert r.intervals is not None
        assert r.intervals.low_sample is low
