"""A scalar reference Monte Carlo path (Property 65).

One path at a time, in exact ``Decimal`` dollars, written from Req 21.2-21.5
and the Account_Simulator's day-end rules, apart from the vectorized
production code:

- the path starts at the starting balance, with the MLL_Floor at
  ``start - MLL`` and the configured profit target (Req 21.3);
- day ``d`` uses session ``row[d]``; a start balance plus intraday low at or
  below the MLL_Floor fails the path that day (Req 21.5); else a low at or
  below ``-DLL`` makes the day's P&L ``-DLL``; else the day's P&L is the net;
- at the day's end: the best day is the largest day P&L so far, the floor
  becomes :func:`~fse.sim.account.next_mll_floor`, the profit target
  :func:`~fse.sim.account.consistency_profit_target` of the best day, and
  the path passes when the balance reaches start plus target (Req 15.7,
  15.13, 15.17);
- the path stops at a pass, a fail or the end of the row.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from fse.backtest.runner import SessionOutcome
from fse.config.schema.account import AccountConfig
from fse.sim.account import consistency_profit_target, next_mll_floor

type PathStatus = Literal["passed", "failed", "unresolved"]


def reference_path(
    outcomes: Sequence[SessionOutcome], acct: AccountConfig, row: Sequence[int]
) -> tuple[PathStatus, int]:
    """The status of one path and the trading days it ran."""
    start = acct.starting_balance.value
    mll = acct.maximum_loss_limit
    dll = acct.daily_loss_limit
    balance = start
    floor = start - mll.value if mll.enabled else None
    target = acct.profit_target.value
    best: Decimal | None = None
    for day, index in enumerate(row, start=1):
        session = outcomes[index]
        if floor is not None and balance + session.intraday_low <= floor:
            return "failed", day
        dll_hit = dll.enabled and session.intraday_low <= -dll.value
        pnl = -dll.value if dll_hit else session.net
        balance += pnl
        best = pnl if best is None else max(best, pnl)
        if floor is not None:
            floor = next_mll_floor(floor, balance, mll.value, start)
        if acct.consistency_target.enabled:
            target = consistency_profit_target(
                acct.profit_target.value, best, acct.consistency_target.pct
            )
        if acct.profit_target.enabled and balance >= start + target:
            return "passed", day
    return "unresolved", len(row)
