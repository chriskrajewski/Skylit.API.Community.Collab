"""Session outcomes and account rules for the Monte_Carlo_Simulator tests (Properties 65, 66).

- :func:`session_outcomes`: 1 to 12 sessions in date order. About one in four
  has no trades (net and low 0). Others draw a net in whole cents, often a
  round value near the default rules' boundaries, and an intraday low of
  ``min(0, net)`` less an excursion, often one that lands the low on ``-$1,000``
  (the default DLL) or ``-$2,000`` (the default MLL distance). Some are
  flagged ``truncated_by_run_account``.
- :func:`account_configs`: the 50K defaults or random values, each of the
  four rules the estimate reads (profit target, MLL, DLL, consistency) on or
  off, and consistency percentages that do not divide evenly.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from hypothesis import strategies as st

from fse.backtest.runner import SessionOutcome
from fse.config.schema.account import AccountConfig

FIRST_SESSION: Final = date(2026, 3, 2)

_ROUND_NETS: Final = (0, 50_000, 100_000, 150_000, 165_000, 300_000, -100_000, -50_000)
_ROUND_LOWS: Final = (-100_000, -200_000, -99_999, -100_001, -199_999, -200_001)


def _usd(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


@st.composite
def session_outcome(draw: st.DrawFn, session: date) -> SessionOutcome:
    truncated = draw(st.booleans()) and draw(st.booleans())
    if draw(st.integers(0, 3)) == 0:
        return SessionOutcome(session, _usd(0), _usd(0), truncated)
    net = draw(st.one_of(st.sampled_from(_ROUND_NETS), st.integers(-300_000, 400_000)))
    ceiling = min(0, net)
    low = draw(
        st.one_of(
            st.integers(0, 250_000).map(lambda extra: ceiling - extra),
            st.sampled_from(_ROUND_LOWS).filter(lambda v: v <= ceiling),
            st.just(ceiling),
        )
    )
    return SessionOutcome(session, _usd(net), _usd(low), truncated)


@st.composite
def session_outcomes(draw: st.DrawFn, max_sessions: int = 12) -> tuple[SessionOutcome, ...]:
    n = draw(st.integers(1, max_sessions))
    return tuple(draw(session_outcome(FIRST_SESSION + timedelta(days=i))) for i in range(n))


def _money(lo: int, hi: int) -> st.SearchStrategy[str]:
    return st.integers(lo, hi).map(lambda c: str(_usd(c)))


@st.composite
def account_configs(draw: st.DrawFn) -> AccountConfig:
    if draw(st.booleans()):
        start, mll, dll, target = "50000.00", "2000.00", "1000.00", "3000.00"
    else:
        start_c = draw(st.integers(1_000_00, 300_000_00))
        start = str(_usd(start_c))
        mll = draw(_money(1, min(start_c - 1, 5_000_00)))
        dll = draw(_money(1, 3_000_00))
        target = draw(_money(1, 6_000_00))
    pct = draw(st.one_of(st.sampled_from([55.0, 50.0, 100.0, 33.3]), st.floats(1.0, 100.0)))
    return AccountConfig.model_validate(
        {
            "starting_balance": {"value": start},
            "profit_target": {"enabled": draw(st.integers(0, 4)) > 0, "value": target},
            "maximum_loss_limit": {"enabled": draw(st.integers(0, 4)) > 0, "value": mll},
            "daily_loss_limit": {"enabled": draw(st.integers(0, 4)) > 0, "value": dll},
            "consistency_target": {"enabled": draw(st.booleans()), "pct": pct},
        }
    )
