"""Property 79: Risk pre-check.

*For any* order intent and Risk_Manager state, an order that opens or increases
a position is withheld when it would exceed the position cap or after the
internal daily loss stop, with the check, measured value and limit reported,
and an order that only reduces or closes a position always passes.

The oracle restates Req 24.23-24.25 and the Glossary:

- an order only reduces or closes a position when it is on the opposite side of
  the position held in its instrument, for at most that position's size;
- the position cap counts open contracts, working entry-order contracts and the
  order's own contracts in Micro_Equivalents (1 per MES or MNQ contract, 10 per
  ES or NQ), without regard to direction (Req 13.5);
- the internal daily loss stop has been reached when it fired in the order's
  session; its measured value and limit are the ones recorded when it fired.

Two tests:

- :func:`~fse.engine.risk.precheck` on any Risk_Manager state, positions,
  working entries, order and cap, with orders biased to reduce a held position
  and caps biased to the order's Micro_Equivalent total, against the oracle;
- the loss stop fired by :func:`~fse.engine.risk.on_minute_close` from the
  session's net plus the unrealized P&L at the bar's worst prices, then an
  opening order and a reducing order checked against the resulting state.

**Validates: Requirements 24.23, 24.24, 24.25**
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from fse.config.schema.kill_switches import KillSwitchesConfig
from fse.engine.risk import (
    FailedCheck,
    LossStopHit,
    RiskState,
    Withheld,
    on_minute_close,
    precheck,
)
from fse.engine.types import Order, OrderKind, OrderRole, Side
from fse.timekit import NS_PER_MINUTE, SessionCalendar, ny_instant

ME_PER_CONTRACT: Final[Mapping[str, int]] = {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}  # Glossary
INSTRUMENTS: Final = tuple(ME_PER_CONTRACT)
SIDES: Final[tuple[Side, ...]] = ("buy", "sell")
KINDS: Final[tuple[OrderKind, ...]] = ("limit", "stop", "market")
ROLES: Final[tuple[OrderRole, ...]] = ("entry", "stop", "tp1", "tp2", "exit")
CAP_RANGE: Final = (1, 1000)  # valid position caps (Req 13.4)

FIRST_DAY: Final = date(2026, 3, 2)
CAL: Final = SessionCalendar(FIRST_DAY, date(2026, 4, 30), holidays=[date(2026, 4, 3)])

# ---------------------------------------------------------------- oracle


def closing_side(held: int) -> Side:
    """The side that reduces a held position of ``held`` contracts (long above 0)."""
    return "sell" if held > 0 else "buy"


def only_reduces(order: Order, positions: Mapping[str, int]) -> bool:
    """``order`` only reduces or closes the position held in its instrument (Req 24.25)."""
    held = positions.get(order.instrument, 0)
    return held != 0 and order.side == closing_side(held) and order.qty <= abs(held)


def exposure(positions: Mapping[str, int], working: Mapping[str, int], order: Order) -> int:
    """Open plus working-entry plus the order's Micro_Equivalents, regardless of direction."""
    contracts = [*positions.items(), *working.items(), (order.instrument, order.qty)]
    return sum(ME_PER_CONTRACT[instrument] * abs(qty) for instrument, qty in contracts)


def expected_failures(case: Case) -> Counter[FailedCheck]:
    """The checks a non-reducing order fails, each with its measured value and limit (Req 24.24)."""
    failures: Counter[FailedCheck] = Counter()
    total = exposure(case.positions, case.working, case.order)
    if case.cap is not None and total > case.cap:
        failures[FailedCheck("position_cap", total, case.cap)] += 1
    stop = case.rs.loss_stop
    if case.rs.session == case.session and stop is not None:
        failures[FailedCheck("internal_daily_loss_stop", stop.measured, stop.limit)] += 1
    return failures


# ---------------------------------------------------------------- generators


@dataclass(frozen=True, slots=True)
class Case:
    rs: RiskState
    order: Order
    session: date
    positions: Mapping[str, int]
    working: Mapping[str, int]
    cap: int | None


def dollars(lo_cents: int, hi_cents: int) -> st.SearchStrategy[Decimal]:
    return st.integers(lo_cents, hi_cents).map(lambda c: Decimal(c) / 100)


@st.composite
def risk_states(draw: st.DrawFn) -> RiskState:
    """A Risk_Manager state, with the internal daily loss stop fired in about half of them."""
    streak = draw(st.integers(0, 5))
    if draw(st.integers(0, 9)) == 0:
        return RiskState(loss_streak=streak)  # before the first event
    session = FIRST_DAY + timedelta(days=draw(st.integers(0, 40)))
    stop = None
    if draw(st.booleans()):
        limit = draw(dollars(-200_000, -1))
        measured = limit - draw(dollars(0, 100_000))
        stop = LossStopHit(draw(st.integers(0, 2**62)), measured, limit)
    net = draw(dollars(-300_000, 300_000))
    return RiskState(session=session, loss_streak=streak, session_net=net, loss_stop=stop)


def make_order(
    instrument: str, side: Side, qty: int, kind: OrderKind, role: OrderRole, price: int
) -> Order:
    return Order(
        f"p79-{role}", None, instrument, side, kind, qty,
        None if kind == "market" else price, 0, role,
    )  # fmt: skip


@st.composite
def cases(draw: st.DrawFn) -> Case:
    rs = draw(risk_states())
    if rs.session is None:
        session = FIRST_DAY + timedelta(days=draw(st.integers(0, 40)))
    else:  # the state's own session most often, else a later one
        session = rs.session + timedelta(days=draw(st.sampled_from((0, 0, 0, 1, 2, 7))))
    positions = draw(st.dictionaries(st.sampled_from(INSTRUMENTS), st.integers(-40, 40)))
    working = draw(st.dictionaries(st.sampled_from(INSTRUMENTS), st.integers(0, 40)))

    instrument = draw(st.sampled_from(INSTRUMENTS))
    held = positions.get(instrument, 0)
    if held != 0 and draw(st.booleans()):  # against the held position: reduce, close or flip
        side = closing_side(held)
        qty = draw(st.integers(1, abs(held) + 2))
    else:
        side = draw(st.sampled_from(SIDES))
        qty = draw(st.integers(1, 60))
    kind, role = draw(st.sampled_from(KINDS)), draw(st.sampled_from(ROLES))
    order = make_order(instrument, side, qty, kind, role, draw(st.integers(1, 100_000)))

    total = exposure(positions, working, order)
    near_total = st.integers(-2, 2).map(lambda d: min(CAP_RANGE[1], max(CAP_RANGE[0], total + d)))
    cap = draw(st.one_of(st.none(), st.integers(*CAP_RANGE), near_total))
    return Case(rs, order, session, positions, working, cap)


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 79: Risk pre-check
@given(case=cases())
def test_precheck_withholds_exactly_the_opening_orders_that_fail_a_check(case: Case) -> None:
    result = precheck(case.rs, case.order, case.session, case.positions, case.working, case.cap)
    if only_reduces(case.order, case.positions):
        assert result is case.order, f"a reducing order was withheld: {result!r}"
        return
    expected = expected_failures(case)
    if not expected:
        assert result is case.order, f"an order that fails no check was withheld: {result!r}"
        return
    assert isinstance(result, Withheld), f"got {result!r}, want the order withheld"
    assert result.order is case.order, "the withheld order must be the order checked"
    assert Counter(result.failures) == expected


# Feature: skylit-futures-strategy-engine, Property 79: Risk pre-check
@given(
    session=st.sampled_from(CAL.sessions()),
    minute=st.integers(0, 359),  # 1-minute closes from 10:00 to 15:59
    net=dollars(-200_000, 200_000),
    unrealized=dollars(-200_000, 200_000),
    quarters=st.integers(1, 40),  # multiple = quarters / 4, from 0.25 to 10
    risk_usd=dollars(1, 100_000),
    held=st.integers(1, 20).flatmap(lambda n: st.sampled_from((n, -n))),
    entry_qty=st.integers(1, 20),
    data=st.data(),
)
def test_the_fired_loss_stop_withholds_opening_orders_but_not_reducing_ones(
    session: date,
    minute: int,
    net: Decimal,
    unrealized: Decimal,
    quarters: int,
    risk_usd: Decimal,
    held: int,
    entry_qty: int,
    data: st.DataObject,
) -> None:
    cfg = KillSwitchesConfig.model_validate(
        {"internal_daily_loss_stop": {"enabled": True, "multiple": quarters / 4}}
    )
    t = ny_instant(session, time(10, 0)) + minute * NS_PER_MINUTE
    rs, _ = on_minute_close(
        RiskState(session=session, session_net=net), t, {}, unrealized, risk_usd, cfg, CAL
    )
    measured, limit = net + unrealized, -(Decimal(quarters) / 4 * risk_usd)
    fired = measured <= limit  # Req 16.6

    entry = make_order("MES", "buy", entry_qty, "market", "entry", 0)
    result = precheck(rs, entry, session, {}, {}, None)
    if fired:
        assert result == Withheld(
            entry, (FailedCheck("internal_daily_loss_stop", measured, limit),)
        ), f"{measured} is at or below {limit}"
    else:
        assert result is entry, f"{measured} is above {limit}, yet got {result!r}"

    kind, role = data.draw(st.sampled_from(KINDS)), data.draw(st.sampled_from(ROLES))
    qty = data.draw(st.integers(1, abs(held)))
    reducing = make_order("ES", closing_side(held), qty, kind, role, 20_000)
    assert precheck(rs, reducing, session, {"ES": held}, {"ES": 5}, 1) is reducing
