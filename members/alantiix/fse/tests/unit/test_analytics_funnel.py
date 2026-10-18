"""Unit tests for filled keys in the Gate_Funnel (``fse.analytics.funnel``).

A key whose account entry filled is ``filled`` even when its governing
evaluation failed a Gate; its ``governing_failing`` and ``superseded_shadow``
are audit fields that never reach the per-Gate counts or the only-rejected
Shadow_Trades (Amended 2026-10-05 by Operator decision, Req 19.3/19.4/19.8).
A record whose fields contradict its status is refused.

**Validates: Requirements 19.3, 19.4, 19.6, 19.8, 19.10**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from fse.analytics.funnel import FunnelError, GateFunnel, funnel_to_jsonable, gate_funnel
from fse.backtest.shadow import FinalStatus, SetupRecord, ShadowOutcome
from fse.config.schema.gates import GateId
from fse.engine.types import Fill, SetupKey, Trade

SESSION = date(2026, 3, 4)
GATES: tuple[GateId, ...] = ("map_grade", "candle_color")


def key(i: int) -> SetupKey:
    return SetupKey("MES", "gatekeeper_fade", 5800.0 + i, "long", SESSION, 1)


def trade(k: SetupKey, r_multiple: Decimal, *, shadow: bool) -> Trade:
    entry = Fill("e", 0, 100, 1, Decimal(0))
    exit_ = Fill("x", 60, 104, 1, Decimal(0))
    r = Decimal(5)
    return Trade(
        k, entry, 96, 1, (exit_,), r_multiple * r, r, r_multiple, False, Decimal(0),
        Decimal(0), 0, shadow,
    )  # fmt: skip


def record(
    k: SetupKey,
    status: FinalStatus,
    failing: tuple[GateId, ...] = (),
    shadow: ShadowOutcome | None = None,
    *,
    governing_failing: tuple[GateId, ...] = (),
    superseded_shadow: ShadowOutcome | None = None,
) -> SetupRecord:
    cause = ("max_open",) if status == "cancelled" else ()
    return SetupRecord(
        k, status, failing, cause, 0, 0, 0, shadow,
        governing_failing=governing_failing, superseded_shadow=superseded_shadow,
    )  # fmt: skip


def funnel(records: list[SetupRecord]) -> GateFunnel:
    return gate_funnel(records, (), GATES, min_sample=1, sessions=[SESSION])


def test_a_record_that_is_not_rejected_with_failing_gates_or_a_shadow_is_refused() -> None:
    with pytest.raises(FunnelError, match="is filled but lists failing Gates"):
        funnel([record(key(0), "filled", ("map_grade",))])
    outcome = ShadowOutcome(0, None, "size_zero")
    with pytest.raises(FunnelError, match="is cancelled but lists failing Gates"):
        funnel([record(key(0), "cancelled", (), outcome)])


def test_a_record_that_is_not_filled_with_a_superseded_evaluation_is_refused() -> None:
    with pytest.raises(FunnelError, match="is rejected but keeps a superseded"):
        funnel([record(key(0), "rejected", ("map_grade",), governing_failing=("map_grade",))])
    outcome = ShadowOutcome(0, None, None)
    with pytest.raises(FunnelError, match="is cancelled but keeps a superseded"):
        funnel([record(key(0), "cancelled", superseded_shadow=outcome)])


def test_a_superseded_failing_gate_must_be_enabled() -> None:
    with pytest.raises(FunnelError, match="fails Gates that are not enabled"):
        funnel([record(key(0), "filled", governing_failing=("tap_count",))])


def test_a_filled_key_with_a_failing_governing_evaluation_counts_only_as_accepted() -> None:
    late = key(0)
    superseded = ShadowOutcome(0, trade(late, Decimal(4), shadow=True), None)
    rejected = key(1)
    rejected_shadow = ShadowOutcome(0, trade(rejected, Decimal(-1), shadow=True), None)
    records = [
        record(late, "filled", governing_failing=("map_grade",), superseded_shadow=superseded),
        record(rejected, "rejected", ("candle_color",), rejected_shadow),
    ]
    result = gate_funnel(
        records,
        [trade(late, Decimal(2), shadow=False)],
        GATES,
        min_sample=1,
        sessions=[SESSION],
    )
    funnel_to_jsonable(result)

    assert dict(result.status_counts)["filled"] == 1
    assert dict(result.status_counts)["rejected"] == 1
    map_grade = result.gate("map_grade")
    assert (map_grade.failing, map_grade.only_failing, map_grade.first_failing) == (0, 0, 0)
    assert (map_grade.shadow.filled, map_grade.shadow.not_filled) == (0, 0)
    candle = result.gate("candle_color")
    assert (candle.failing, candle.only_failing, candle.first_failing) == (1, 1, 1)
    assert candle.shadow.filled == 1
    assert sum(result.gate(g).first_failing for g in GATES) == 1  # Req 19.6
    assert result.co_rejection == ((0, 0), (0, 1))
    assert (result.accepted.filled, result.accepted.mean_r) == (1, Fraction(2))
    assert result.sessions[0].top == (("candle_color", 1),)
