"""Property 64: Seeded analytics determinism.

*For any* trade list, session outcomes and seed, bootstrap confidence
intervals and Monte_Carlo_Simulator outputs are identical across reruns with
the same inputs, resample or path count and seed.

**Inputs.** 0 to 30 one-contract trades (nets in cents, some breakeven,
some shadow, partial exit times), a :class:`MetricsCfg`, 1,000 to 3,000
resamples; :mod:`tests.strategies.montecarlo_inputs` sessions and account
rules, 1,000 to 3,000 paths, 1 to 20 days; a seed.

**Checks.**

- Two :func:`bootstrap_intervals` calls give equal results, and a call on
  the trades in reverse order gives the same result (the draws follow the
  exit-time order, not the input order). The result records the seed and
  resample count.
- Two :func:`estimate` calls give equal results, recording the seed.

**Validates: Requirements 20.12, 21.9**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from fse.analytics.bootstrap import bootstrap_intervals
from fse.analytics.metrics import MetricsCfg
from fse.analytics.montecarlo import SessionOutcome, estimate
from fse.config.schema.account import AccountConfig
from fse.engine.types import Fill, SetupKey, Trade
from fse.timekit import NS_PER_MINUTE
from tests.strategies.montecarlo_inputs import account_configs, session_outcomes

SESSION = date(2026, 3, 2)
T0 = 1_767_600_000 * 1_000_000_000


@st.composite
def trade_lists(draw: st.DrawFn) -> list[Trade]:
    out: list[Trade] = []
    for i in range(draw(st.integers(0, 30), label="trades")):
        net = Decimal(draw(st.integers(-30_000, 30_000))).scaleb(-2)
        r = Decimal(draw(st.integers(1, 40))) * Decimal("1.25")
        key = SetupKey("MES", "gatekeeper_fade", 5800.0, "long", SESSION, i + 1)
        entry = Fill(f"e{i}", T0, 23120, 1, Decimal("0.00"))
        exit_fill = Fill(
            f"x{i}", T0 + draw(st.integers(0, 50)) * NS_PER_MINUTE, 23120, 1, Decimal("0.00")
        )
        out.append(
            Trade(
                setup_key=key,
                entry_fill=entry,
                initial_stop=23100,
                qty_at_entry=1,
                exits=(exit_fill,),
                net=net,
                r=r,
                r_multiple=net / r,
                reached_tp1=draw(st.booleans()),
                mae_r=Decimal(0),
                mfe_r=Decimal(0),
                missing_bars=0,
                shadow=draw(st.integers(0, 4)) == 0,
            )
        )
    return out


CFGS = st.builds(
    MetricsCfg,
    min_sample_trades=st.integers(1, 50),
    primary_win_rate=st.sampled_from(("a", "b", "c")),
    has_first_target=st.booleans(),
)


@given(
    trades=trade_lists(),
    cfg=CFGS,
    resamples=st.integers(1_000, 3_000),
    seed=st.integers(0, 2**63 - 1),
)
def test_bootstrap_determinism(
    trades: list[Trade], cfg: MetricsCfg, resamples: int, seed: int
) -> None:
    first = bootstrap_intervals(trades, cfg, seed=seed, resamples=resamples)
    again = bootstrap_intervals(list(trades), cfg, seed=seed, resamples=resamples)
    reordered = bootstrap_intervals(trades[::-1], cfg, seed=seed, resamples=resamples)
    assert first == again
    # Equal exit times keep their input order, so reversing is safe only with distinct times.
    exit_times = [max(f.bar_open_ns for f in t.exits) for t in trades if not t.shadow]
    if len(set(exit_times)) == len(exit_times):
        assert first == reordered
    assert (first.seed, first.resamples) == (seed, resamples)


@given(
    outcomes=session_outcomes(),
    acct=account_configs(),
    paths=st.integers(1_000, 3_000),
    max_days=st.integers(1, 20),
    seed=st.integers(0, 2**63 - 1),
)
def test_montecarlo_determinism(
    outcomes: tuple[SessionOutcome, ...],
    acct: AccountConfig,
    paths: int,
    max_days: int,
    seed: int,
) -> None:
    first = estimate(outcomes, acct, paths=paths, max_days=max_days, seed=seed, min_sessions=40)
    again = estimate(
        tuple(outcomes), acct, paths=paths, max_days=max_days, seed=seed, min_sessions=40
    )
    assert first == again
    assert first.seed == seed
