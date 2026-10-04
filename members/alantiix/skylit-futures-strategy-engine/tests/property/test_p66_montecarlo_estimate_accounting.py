"""Property 66: Estimate accounting.

*For any* estimate, the pass, fail and unresolved counts sum to the path
count, each share lies in [0, 1] and the three shares sum to 1, and the
median and nearest-rank 90th-percentile days to pass are computed over
passing paths only (not available when none pass).

**Inputs.** :mod:`tests.strategies.montecarlo_inputs` sessions and account
rules; 1,000 to 3,000 paths, 1 to 20 days, 1 to 1,000 minimum sessions and a
seed.

**Model.** The same draws (:func:`draw_sessions`) through
:func:`simulate_paths` give each path's status and days. The model counts
them, sorts the passing paths' days, takes the median (mean of the two middle
values for an even count) and the nearest-rank P90 (the value at rank
``ceil(0.9 x k)``), or neither when no path passed.

**Checks.** Counts, shares and days to pass equal the model's; the counts sum
to the path count and the shares, exact fractions in [0, 1], to 1; the
estimate records the seed, settings, session count, the
``truncated_by_run_account`` count and the "insufficient sample" flag.

**Validates: Requirements 21.6, 21.7, 21.8**
"""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from fse.analytics.montecarlo import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_UNRESOLVED,
    SessionOutcome,
    draw_sessions,
    estimate,
    simulate_paths,
)
from fse.config.schema.account import AccountConfig
from tests.strategies.montecarlo_inputs import account_configs, session_outcomes


@given(
    outcomes=session_outcomes(),
    acct=account_configs(),
    paths=st.integers(1_000, 3_000),
    max_days=st.integers(1, 20),
    min_sessions=st.integers(1, 1_000),
    seed=st.integers(0, 2**63 - 1),
)
def test_estimate_accounting(
    outcomes: tuple[SessionOutcome, ...],
    acct: AccountConfig,
    paths: int,
    max_days: int,
    min_sessions: int,
    seed: int,
) -> None:
    est = estimate(
        outcomes, acct, paths=paths, max_days=max_days, seed=seed, min_sessions=min_sessions
    )

    draws = np.concatenate(list(draw_sessions(seed, len(outcomes), paths, max_days)))
    model = simulate_paths(outcomes, acct, draws)
    counts = {
        code: int(np.count_nonzero(model.status == code))
        for code in (STATUS_UNRESOLVED, STATUS_PASSED, STATUS_FAILED)
    }
    assert (est.passed, est.failed, est.unresolved) == (
        counts[STATUS_PASSED],
        counts[STATUS_FAILED],
        counts[STATUS_UNRESOLVED],
    )
    assert est.passed + est.failed + est.unresolved == paths
    shares = (est.pass_probability, est.failure_probability, est.unresolved_share)
    assert all(isinstance(s, Fraction) and 0 <= s <= 1 for s in shares)
    assert sum(shares) == 1
    assert est.pass_probability == Fraction(est.passed, paths)

    days = sorted(int(d) for d in model.days[model.status == STATUS_PASSED])
    if days:
        k = len(days)
        median = Fraction(days[k // 2]) if k % 2 else Fraction(days[k // 2 - 1] + days[k // 2], 2)
        assert est.median_days_to_pass == median
        assert est.p90_days_to_pass == days[math.ceil(Fraction(9, 10) * k) - 1]
        assert 1 <= est.median_days_to_pass <= max_days
    else:
        assert est.median_days_to_pass is None
        assert est.p90_days_to_pass is None

    assert (est.seed, est.paths, est.max_days, est.min_sessions) == (
        seed,
        paths,
        max_days,
        min_sessions,
    )
    assert est.sessions == len(outcomes)
    assert est.truncated_by_run_account == sum(o.truncated_by_run_account for o in outcomes)
    assert est.insufficient_sample == (len(outcomes) < min_sessions)
