"""Property 58: Shadow edge flags.

*For any* set of shadow and accepted trades and any minimum sample, each
Gate's only-rejected shadow statistics equal a reference computation, the
Gate is flagged "no measured edge" exactly when its filled only-rejected count
is at least the minimum and their mean R_Multiple is at least the accepted
mean, and is marked "insufficient sample" exactly when the count is below the
minimum or there are no filled accepted trades.

The generator draws enabled Gates, Setup_Key records with every final status
(rejected keys fail one or more Gates and carry a Shadow_Trade that filled or
not), accepted trades for some keys (only those of ``filled`` keys count) and
a minimum sample. :func:`fse.analytics.funnel.gate_funnel` must match a
reference written with plain sums, exactly (``Fraction``), and the funnel must
encode as JSON.

**Validates: Requirements 19.10, 19.11, 19.12**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

from hypothesis import event, given
from hypothesis import strategies as st

from fse.analytics.funnel import funnel_to_jsonable, gate_funnel
from fse.backtest.shadow import FINAL_STATUSES, SetupRecord, ShadowOutcome
from fse.config.schema.gates import GATE_IDS, GateId
from fse.engine.types import Fill, NotApplicable, SetupKey, Trade

SESSION = date(2026, 3, 4)
R_VALUES = st.decimals(min_value=-3, max_value=5, places=2, allow_nan=False, allow_infinity=False)


def make_trade(key: SetupKey, r_multiple: Decimal, *, shadow: bool) -> Trade:
    entry = Fill("e", 0, 100, 1, Decimal(0))
    exit_ = Fill("x", 60, 104, 1, Decimal(0))
    r = Decimal(5)
    return Trade(
        key, entry, 96, 1, (exit_,), r_multiple * r, r, r_multiple, False, Decimal(0),
        Decimal(0), 0, shadow,
    )  # fmt: skip


type Inputs = tuple[list[GateId], list[SetupRecord], list[Trade], int]


@st.composite
def funnel_inputs(draw: st.DrawFn) -> Inputs:
    gates = draw(
        st.lists(st.sampled_from(GATE_IDS), min_size=1, max_size=5, unique=True), label="gates"
    )
    gates.sort(key=lambda g: GATE_IDS.index(g))
    records: list[SetupRecord] = []
    trades: list[Trade] = []
    for i in range(draw(st.integers(0, 40), label="keys")):
        key = SetupKey("MES", "gatekeeper_fade", 5800.0 + i, "long", SESSION, 1)
        status = draw(st.sampled_from(FINAL_STATUSES))
        failing: tuple[GateId, ...] = ()
        shadow = None
        if status == "rejected":
            subset = draw(st.lists(st.sampled_from(gates), min_size=1, unique=True))
            # Only-rejected keys are the interesting ones: favour a single Gate.
            failing = tuple(sorted(subset if draw(st.booleans()) else subset[:1], key=gates.index))
            filled = draw(st.booleans())
            trade = make_trade(key, draw(R_VALUES), shadow=True) if filled else None
            shadow = ShadowOutcome(0, trade, None if filled else draw(st.sampled_from((None, "x"))))
        tapped = status != "untapped"
        records.append(
            SetupRecord(
                key, status, failing, ("max_open",) if status == "cancelled" else (), 0,
                0 if tapped else None, 0 if tapped else None, shadow,
            )
        )  # fmt: skip
        if status in ("filled", "rejected", "cancelled") and draw(st.booleans()):
            trades.append(make_trade(key, draw(R_VALUES), shadow=False))
    return gates, records, trades, draw(st.integers(1, 6), label="min sample")


def mean(values: list[Decimal]) -> Fraction | NotApplicable:
    return sum(map(Fraction, values), Fraction(0)) / len(values) if values else NotApplicable()


# Feature: skylit-futures-strategy-engine, Property 58: Shadow edge flags
@given(inputs=funnel_inputs())
def test_only_rejected_shadow_statistics_and_flags_match_the_reference(inputs: Inputs) -> None:
    gates, records, trades, min_sample = inputs
    funnel = gate_funnel(records, trades, gates, min_sample=min_sample, sessions=[SESSION])
    funnel_to_jsonable(funnel)

    filled_keys = {r.key for r in records if r.status == "filled"}
    accepted = [t.r_multiple for t in trades if t.setup_key in filled_keys]
    assert funnel.accepted.filled == len(accepted)
    assert funnel.accepted.mean_r == mean(accepted)
    assert funnel.accepted.wins == sum(1 for r in accepted if r > 0)

    flags = []
    for g in gates:
        row = funnel.gate(g)
        only = [r for r in records if r.status == "rejected" and r.failing == (g,)]
        shadows = [r.shadow for r in only]
        rs = [s.trade.r_multiple for s in shadows if s is not None and s.trade is not None]
        stats = row.shadow
        assert stats.filled == len(rs)
        assert stats.not_filled == len(only) - len(rs)
        assert stats.wins == sum(1 for r in rs if r > 0)
        assert stats.mean_r == mean(rs)
        want_rate = Fraction(100 * stats.wins, len(rs)) if rs else NotApplicable()
        assert stats.win_rate_pct == want_rate
        shadow_mean, accepted_mean = mean(rs), mean(accepted)
        insufficient = len(rs) < min_sample or not accepted
        assert (row.flag == "insufficient_sample") == insufficient
        edge = (
            not insufficient
            and not isinstance(shadow_mean, NotApplicable)
            and not isinstance(accepted_mean, NotApplicable)
            and shadow_mean >= accepted_mean
        )
        assert (row.flag == "no_measured_edge") == edge
        flags.append(row.flag)
    event(f"flags: {sorted({str(f) for f in flags})}")
