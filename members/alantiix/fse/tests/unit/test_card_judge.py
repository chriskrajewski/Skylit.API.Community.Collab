"""Rule 9.1: day-averaged lift, breadth, permutation, and an outside group."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fse.analytics.card_judge import (
    BaselineObservation,
    JudgeThresholds,
    Observation,
    judge,
)


def _baseline(
    symbol: str, clock: str, side: Literal["long", "short"], hit: bool, n: int = 12
) -> list[BaselineObservation]:
    start = date(2025, 3, 3)
    return [
        BaselineObservation(
            symbol=symbol, day=start + timedelta(days=i), clock=clock, side=side, hit=hit
        )
        for i in range(n)
    ]


def test_pass_when_every_rule_91_check_holds() -> None:
    trades: list[Observation] = []
    baseline: list[BaselineObservation] = []
    start = date(2026, 1, 5)
    for i in range(20):
        symbol = "MNQ" if i % 2 == 0 else "MES"
        day = start + timedelta(days=i)
        trades.append(
            Observation(
                symbol=symbol, day=day, clock="10:15", side="long", hit=True, group="design"
            )
        )
        baseline.extend(_baseline(symbol, "10:15", "long", False))
    trades.append(
        Observation(
            symbol="ES",
            day=date(2026, 4, 1),
            clock="10:15",
            side="long",
            hit=True,
            group="outside",
        )
    )
    baseline.extend(_baseline("ES", "10:15", "long", False))
    report = judge(
        trades,
        baseline,
        design_symbols=["MNQ", "MES"],
        thresholds=JudgeThresholds(permutations=300),
    )
    failed = [item.name for item in report.checks if not item.passed]
    assert report.grade == "PASS", failed
    assert report.judged_at == "2R"
    assert report.k == 2
    assert report.days >= 15


def test_missing_baseline_and_missing_outside_group_fail() -> None:
    trades = [
        Observation(symbol="MNQ", day=date(2026, 2, 2), clock="10:00", side="long", hit=True)
    ]
    report = judge(trades, [], design_symbols=["MNQ"])
    by_name = {item.name: item.passed for item in report.checks}
    assert report.grade == "FAIL"
    assert by_name["lift_vs_baseline"] is False
    assert by_name["outside_group"] is False
    assert by_name["permutation_p"] is False


def test_thresholds_are_configurable() -> None:
    day = date(2026, 5, 4)
    trades = [
        Observation(symbol="MNQ", day=day, clock="11:00", side="long", mfe_r=Decimal("3"))
    ]
    baseline = _baseline("MNQ", "11:00", "long", True)
    outside = Observation(
        symbol="ES", day=date(2026, 5, 5), clock="11:00", side="long", hit=True, group="outside"
    )
    loose = JudgeThresholds(
        min_lift=Decimal("0"),
        min_days=1,
        min_symbol_positive_frac=Decimal("0"),
        permutations=20,
        k=1,
        max_permutation_p=Decimal("1"),
    )
    passed = judge(
        [trades[0], outside],
        [*baseline, *_baseline("ES", "11:00", "long", False)],
        design_symbols=["MNQ"],
        thresholds=loose,
    )
    assert passed.checks[0].passed is True
    tight = JudgeThresholds(min_lift=Decimal("0.99"), min_days=1, permutations=20)
    failed = judge(
        [trades[0], outside],
        [*baseline, *_baseline("ES", "11:00", "long", False)],
        design_symbols=["MNQ"],
        thresholds=tight,
    )
    assert failed.grade == "FAIL"
    assert failed.checks[0].passed is False
