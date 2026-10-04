"""Property 65: Path model matches the scalar reference.

*For any* session outcomes (net P&L and intraday equity low), account
configuration, path count and maximum days, each vectorized path equals a
scalar reference simulation: draws are uniform indices into all sessions
(zero-trade sessions included), a day whose start balance plus intraday low
reaches the MLL_Floor fails the path even if its close would pass, a DLL
breach sets the day P&L to -DLL, end-of-day updates follow the
Account_Simulator rules, and the path stops at pass, fail or the maximum days.

**Inputs.** :mod:`tests.strategies.montecarlo_inputs`: 1 to 12 sessions and
a default or random ``AccountConfig``; 1 to 40 paths, 1 to 30 days, a seed,
and the draw block size (the production 2**20 cells, or 1 to 50 cells so the
draws cross block boundaries).

**Reference model.** :func:`tests.reference.montecarlo.reference_path`, one
path at a time in exact ``Decimal`` dollars with the Account_Simulator's
``next_mll_floor`` and ``consistency_profit_target``.

**Checks.** The blocks of :func:`draw_sessions` concatenate to one
``rng.integers(0, n, size=(paths, max_days))`` call of a fresh
``PCG64(seed)`` generator, so every session, zero-trade ones included, is a
uniform draw. Each row's status and trading days from
:func:`simulate_paths` equal the reference's.

**Validates: Requirements 21.1, 21.2, 21.4, 21.5**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final
from unittest import mock

import numpy as np
from hypothesis import example, given
from hypothesis import strategies as st

from fse.analytics import montecarlo
from fse.analytics.montecarlo import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_UNRESOLVED,
    SessionOutcome,
    draw_sessions,
    simulate_paths,
)
from fse.config.schema.account import AccountConfig
from tests.reference.montecarlo import reference_path
from tests.strategies.montecarlo_inputs import account_configs, session_outcomes

NAMES: Final = {STATUS_PASSED: "passed", STATUS_FAILED: "failed", STATUS_UNRESOLVED: "unresolved"}
DEFAULTS: Final = AccountConfig()

# Day 1 low reaches the $48,000 floor exactly while the close would pass (Req 21.5);
# a DLL hit at exactly -$1,000 with a positive net; a quiet day.
BOUNDARY: Final = (
    SessionOutcome(date(2026, 3, 2), Decimal("3000.00"), Decimal("-2000.00"), False),
    SessionOutcome(date(2026, 3, 3), Decimal("500.00"), Decimal("-1000.00"), True),
    SessionOutcome(date(2026, 3, 4), Decimal("0.00"), Decimal("0.00"), False),
)


@given(
    outcomes=session_outcomes(),
    acct=account_configs(),
    paths=st.integers(1, 40),
    max_days=st.integers(1, 30),
    seed=st.integers(0, 2**63 - 1),
    block_cells=st.one_of(st.just(1 << 20), st.integers(1, 50)),
)
@example(outcomes=BOUNDARY, acct=DEFAULTS, paths=40, max_days=30, seed=7, block_cells=7)
def test_paths_match_the_scalar_reference(
    outcomes: tuple[SessionOutcome, ...],
    acct: AccountConfig,
    paths: int,
    max_days: int,
    seed: int,
    block_cells: int,
) -> None:
    n = len(outcomes)
    with mock.patch.object(montecarlo, "_BLOCK_CELLS", block_cells):
        draws = np.concatenate(list(draw_sessions(seed, n, paths, max_days)))
    expected = np.random.Generator(np.random.PCG64(seed)).integers(0, n, size=(paths, max_days))
    np.testing.assert_array_equal(draws, expected)

    result = simulate_paths(outcomes, acct, draws)
    assert result.status.shape == result.days.shape == (paths,)
    for i in range(paths):
        status, days = reference_path(outcomes, acct, [int(j) for j in draws[i]])
        assert (NAMES[int(result.status[i])], int(result.days[i])) == (status, days), i
