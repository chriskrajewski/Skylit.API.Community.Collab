"""Unit tests for the Account_Simulator (design §15, Req 15).

Synthetic sessions in March 2026 (Monday 2 March onward). MES is 1.25 dollars
per tick and 1 Micro_Equivalent; ES is 12.50 dollars per tick and 10. Prices
are ticks: 23200 is 5800.00 points.

**Validates: Requirements 15.3, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11,
15.12, 15.13, 15.14, 15.15, 15.16, 15.17, 15.18**
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest

from fse.config.schema.account import AccountConfig
from fse.engine.types import Bar, Fill, Order, OrderRole, Side
from fse.sim.account import (
    MICRO_EQUIVALENTS,
    TICK_VALUE_USD,
    AccountRejection,
    AccountSim,
    AttemptEnded,
    AttemptResult,
    AttemptStarted,
    FlatDeadlineClose,
    ForcedExit,
    Liquidation,
    consistency_profit_target,
    next_mll_floor,
)
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, SessionTimes, ny_instant

MON, TUE, WED, THU, FRI = (date(2026, 3, d) for d in (2, 3, 4, 5, 6))
NEXT_MON = date(2026, 3, 9)
EARLY = date(2026, 3, 13)  # a synthetic 13:00 early close: Flat_Deadline 12:45
P = 23200  # a reference price in ticks
FEES = {"MES": Decimal("0"), "MNQ": Decimal("0"), "ES": Decimal("0"), "NQ": Decimal("0")}
D = Decimal
ZERO = Decimal("0")


def config(**sections: Any) -> AccountConfig:
    return AccountConfig.model_validate(sections)


def make_sim(
    cfg: AccountConfig | None = None,
    fees: dict[str, Decimal] | None = None,
    holidays: tuple[date, ...] = (),
) -> AccountSim:
    cfg = cfg or AccountConfig()
    calendar = SessionCalendar(
        date(2026, 3, 1),
        date(2026, 3, 31),
        holidays=holidays,
        early_closes={EARLY: time(13, 0)},
        times=cfg.session_times(SessionTimes()),
    )
    return AccountSim(cfg, calendar, FEES if fees is None else fees)


def at(session: date, hh: int, mm: int) -> Instant:
    return ny_instant(session, time(hh, mm))


def bar(t: Instant, o: int, h: int, low: int, c: int, instrument: str = "MES") -> Bar:
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=60,
        open_ns=t,
        close_ns=t + NS_PER_MINUTE,
        o=o / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=100.0,
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        source="projectx",
    )


def flat_bar(t: Instant, price: int, instrument: str = "MES") -> Bar:
    return bar(t, price, price, price, price, instrument)


def order(
    cid: str,
    side: Side = "buy",
    qty: int = 1,
    role: OrderRole = "entry",
    instrument: str = "MES",
) -> Order:
    return Order(cid, None, instrument, side, "market", qty, None, 0, role)


def fill_on(
    sim: AccountSim,
    o: Order,
    b: Bar,
    price: int,
    fees: Decimal = ZERO,
) -> list[Any]:
    """Book a fill of ``o`` at ``price`` on bar ``b``, then run the bar checks."""
    sim.on_fill(Fill(o.client_id, b.open_ns, price, o.qty, fees), o)
    return check_bar(sim, b)


def check_bar(sim: AccountSim, b: Bar) -> list[Any]:
    """Run the bar checks on ``b`` alone, with no fill."""
    return sim.on_bar({b.instrument: b})


def day_result(sim: AccountSim, session: date, pnl_ticks: int, qty: int = 4) -> list[Any]:
    """One trading day with a single MES round trip of ``pnl_ticks`` per contract."""
    events: list[Any] = sim.start_trading_day(session)
    entry = flat_bar(at(session, 10, 0), P)
    exit_ = flat_bar(at(session, 10, 1), P + pnl_ticks)
    events += fill_on(sim, order(f"{session}-in", "buy", qty), entry, P)
    events += fill_on(sim, order(f"{session}-out", "sell", qty, "exit"), exit_, P + pnl_ticks)
    events += sim.end_trading_day({})
    return events


def ended(events: list[Any]) -> AttemptResult:
    [result] = [e.result for e in events if isinstance(e, AttemptEnded)]
    return result


# ---------------------------------------------------------------- rule arithmetic


def test_contract_table() -> None:
    assert dict(TICK_VALUE_USD) == {
        "MES": D("1.25"),
        "MNQ": D("0.50"),
        "ES": D("12.50"),
        "NQ": D("5.00"),
    }
    assert dict(MICRO_EQUIVALENTS) == {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}


def test_next_mll_floor() -> None:
    start, mll = D("50000.00"), D("2000.00")
    assert next_mll_floor(D("48000.00"), D("51200.00"), mll, start) == D("49200.00")
    assert next_mll_floor(D("49200.00"), D("52000.00"), mll, start) == D("50000.00")
    assert next_mll_floor(D("50000.00"), D("55000.00"), mll, start) == D("50000.00")
    assert next_mll_floor(D("49200.00"), D("50100.00"), mll, start) == D("49200.00")


@pytest.mark.parametrize(
    ("best", "pct", "expected"),
    [
        (None, 55.0, "3000.00"),
        ("-400.00", 55.0, "3000.00"),
        ("1650.00", 55.0, "3000.00"),
        ("2000.00", 55.0, "3636.37"),
        ("1650.01", 55.0, "3000.02"),
        ("3000.00", 100.0, "3000.00"),
        ("1000.00", 30.0, "3333.34"),
    ],
)
def test_consistency_profit_target(best: str | None, pct: float, expected: str) -> None:
    best_day = None if best is None else D(best)
    target = consistency_profit_target(D("3000.00"), best_day, pct)
    assert str(target) == expected


# ---------------------------------------------------------------- attempts and the floor


def test_an_attempt_starts_at_the_starting_balance() -> None:
    sim = make_sim()
    assert sim.blocked_by == "no_session"
    assert sim.start_trading_day(MON) == [AttemptStarted(1, MON, D("50000.00"), D("48000.00"))]
    assert (sim.balance, sim.mll_floor, sim.profit_target) == (
        D("50000.00"),
        D("48000.00"),
        D("3000.00"),
    )
    assert sim.active
    assert sim.blocked_by is None


def test_the_mll_floor_trails_day_end_balances_only() -> None:
    sim = make_sim()
    day_result(sim, MON, 240)  # 4 x 240 ticks x 1.25 = +1,200
    assert (sim.balance, sim.mll_floor) == (D("51200.00"), D("49200.00"))
    sim.start_trading_day(TUE)
    entry = bar(at(TUE, 10, 0), P, P, P - 100, P - 100)
    fill_on(sim, order("in", "buy", 4), entry, P)  # -500 unrealized at the low
    assert sim.mll_floor == D("49200.00")  # unchanged intraday
    fill_on(sim, order("out", "sell", 4, "exit"), flat_bar(at(TUE, 10, 1), P + 160), P + 160)
    sim.end_trading_day({})
    assert (sim.balance, sim.mll_floor) == (D("52000.00"), D("50000.00"))
    day_result(sim, WED, -100)  # -500
    assert (sim.balance, sim.mll_floor) == (D("51500.00"), D("50000.00"))


# ---------------------------------------------------------------- loss limits


def test_mll_breach_on_the_worst_price_liquidates_and_fails_the_attempt() -> None:
    sim = make_sim(config(daily_loss_limit={"enabled": False}))
    sim.start_trading_day(MON)
    fill_on(sim, order("in", "buy", 10), flat_bar(at(MON, 10, 0), P), P)
    breach = bar(at(MON, 10, 1), P - 10, P - 5, P - 160, P - 20)  # low: 10 x -160 x 1.25
    events = check_bar(sim, breach)
    [liq, end] = events
    assert liq == Liquidation(
        "maximum_loss_limit",
        MON,
        breach.open_ns,
        (ForcedExit("MES", "sell", 10, P - 160, D("0.00")),),
        D("48000.00"),
        D("-2000.00"),
    )
    assert isinstance(end, AttemptEnded)
    assert end.result == AttemptResult(
        number=1,
        outcome="failed",
        first_session=MON,
        last_session=MON,
        trading_days=1,
        ended_ns=breach.open_ns,
        final_balance=D("48000.00"),
        mll_floor=D("48000.00"),
        profit_target=D("3000.00"),
        best_day=None,
        disabled_rules=("daily_loss_limit",),
    )
    assert sim.positions == {}
    for o in (order("again"), order("stop", "sell", 1, "stop")):
        rejection = sim.check_order(o)
        assert isinstance(rejection, AccountRejection)
        assert rejection.rule == "maximum_loss_limit"
    assert sim.on_bar({"MES": flat_bar(at(MON, 10, 2), P - 400)}) == []
    with pytest.raises(ValueError, match="after the Combine_Attempt ended"):
        sim.on_fill(Fill("late", at(MON, 10, 3), P, 1, D("0")), order("late"))


def test_mll_gap_through_the_floor_keeps_the_open_price_value() -> None:
    sim = make_sim(config(daily_loss_limit={"enabled": False}))
    sim.start_trading_day(MON)
    fill_on(sim, order("in", "buy", 10), flat_bar(at(MON, 10, 0), P), P)
    gap = bar(at(MON, 10, 1), P - 200, P - 190, P - 210, P - 200)  # open: -2,500
    [liq, _] = check_bar(sim, gap)
    assert isinstance(liq, Liquidation)
    assert liq.balance == D("47500.00")
    assert sim.balance == D("47500.00")


def test_a_short_is_valued_at_the_bar_high() -> None:
    sim = make_sim(config(daily_loss_limit={"enabled": False}))
    sim.start_trading_day(MON)
    fill_on(sim, order("in", "sell", 10), flat_bar(at(MON, 10, 0), P), P)
    assert check_bar(sim, bar(at(MON, 10, 1), P, P + 159, P - 300, P)) == []
    [liq, _] = check_bar(sim, bar(at(MON, 10, 2), P, P + 160, P, P))
    assert isinstance(liq, Liquidation)
    assert liq.exits == (ForcedExit("MES", "buy", 10, P + 160, D("0.00")),)


def test_dll_liquidates_and_blocks_entries_until_the_next_trading_day() -> None:
    sim = make_sim()
    sim.start_trading_day(MON)
    fill_on(sim, order("in", "buy", 5), flat_bar(at(MON, 10, 0), P), P)
    hit = bar(at(MON, 10, 1), P - 20, P - 10, P - 160, P - 100)  # low: 5 x -160 x 1.25
    assert check_bar(sim, hit) == [
        Liquidation(
            "daily_loss_limit",
            MON,
            hit.open_ns,
            (ForcedExit("MES", "sell", 5, P - 160, D("0.00")),),
            D("49000.00"),
            D("-1000.00"),
        )
    ]
    assert sim.active
    assert sim.blocked_by == "daily_loss_limit"
    rejection = sim.check_order(order("entry"))
    assert isinstance(rejection, AccountRejection)
    assert rejection.rule == "daily_loss_limit"
    exit_order = order("exit", "sell", 1, "exit")
    assert sim.check_order(exit_order) is exit_order
    assert check_bar(sim, flat_bar(at(MON, 10, 2), P - 300)) == []  # fires once
    [close] = sim.end_trading_day({})
    assert close == FlatDeadlineClose(MON, at(MON, 16, 10), ())
    assert sim.mll_floor == D("48000.00")
    sim.start_trading_day(TUE)
    entry = order("tuesday")
    assert sim.check_order(entry) is entry


def test_dll_gap_keeps_the_open_price_value() -> None:
    sim = make_sim()
    sim.start_trading_day(MON)
    fill_on(sim, order("in", "buy", 5), flat_bar(at(MON, 10, 0), P), P)
    [liq] = check_bar(sim, bar(at(MON, 10, 1), P - 200, P - 200, P - 220, P - 210))
    assert isinstance(liq, Liquidation)
    assert (liq.day_pnl, liq.balance) == (D("-1250.00"), D("48750.00"))


def test_mll_wins_when_mll_and_dll_fire_on_the_same_bar() -> None:
    sim = make_sim()
    day_result(sim, MON, -144, qty=5)  # 5 x -144 x 1.25 = -900
    assert (sim.balance, sim.mll_floor) == (D("49100.00"), D("48000.00"))
    sim.start_trading_day(TUE)
    fill_on(sim, order("in", "buy", 5), flat_bar(at(TUE, 10, 0), P), P)
    # Low: -1,100 for the day (past the DLL) and equity 48,000 (at the floor).
    events = check_bar(sim, bar(at(TUE, 10, 1), P, P, P - 176, P - 176))
    assert [type(e) for e in events] == [Liquidation, AttemptEnded]
    assert isinstance(events[0], Liquidation)
    assert events[0].rule == "maximum_loss_limit"
    assert ended(events).outcome == "failed"
    assert ended(events).trading_days == 2


# ---------------------------------------------------------------- position cap


def test_position_cap_counts_open_and_working_entries_in_any_direction() -> None:
    sim = make_sim()
    sim.start_trading_day(MON)
    fill_on(sim, order("open", "sell", 40), flat_bar(at(MON, 10, 0), P), P)
    assert sim.open_micro_equivalents() == 40
    working = [order("es", "buy", 1, "entry", "ES"), order("tp", "buy", 40, "tp1")]
    new = order("new", "buy", 1)
    rejection = sim.check_order(new, working)
    assert rejection == AccountRejection(
        "position_cap",
        "new",
        "position_cap: 51 Micro_Equivalents would exceed the cap of 50",
        total=51,
        cap=50,
    )
    assert sim.check_order(new, working[1:]) is new  # 41
    assert sim.check_order(new, [*working[1:], new]) is new  # itself is not counted twice
    at_cap = order("ten", "buy", 10)
    assert sim.check_order(at_cap) is at_cap  # 50, at the cap
    stop = order("stop", "buy", 40, "stop")
    assert sim.check_order(stop, working) is stop  # exits are never capped
    assert sim.positions == {"MES": -40}


def test_a_disabled_position_cap_is_skipped() -> None:
    sim = make_sim(config(position_cap={"enabled": False}))
    sim.start_trading_day(MON)
    big = order("big", "buy", 20, "entry", "NQ")  # 200 Micro_Equivalents
    assert sim.check_order(big) is big


# ---------------------------------------------------------------- Flat_Deadline


def test_flat_deadline_closes_at_the_next_bar_open_with_fees() -> None:
    sim = make_sim(fees={**FEES, "MES": D("1.00")})
    sim.start_trading_day(MON)
    fill_on(sim, order("in", "buy", 2), flat_bar(at(MON, 15, 0), P), P, fees=D("2.00"))
    deadline = at(MON, 16, 10)
    with pytest.raises(ValueError, match="Flat_Deadline go to end_trading_day"):
        sim.on_bar({"MES": flat_bar(deadline, P)})
    with pytest.raises(ValueError, match="no closing bar for open MES"):
        sim.end_trading_day({})
    with pytest.raises(ValueError, match="opens before the Flat_Deadline"):
        sim.end_trading_day({"MES": flat_bar(at(MON, 16, 9), P)})
    events = sim.end_trading_day({"MES": bar(deadline, P + 40, P + 50, P - 50, P)})
    assert events == [
        FlatDeadlineClose(MON, deadline, (ForcedExit("MES", "sell", 2, P + 40, D("2.00")),))
    ]
    # 2 x 40 ticks x 1.25 = 100, minus 2.00 entry and 2.00 exit fees.
    assert (sim.balance, sim.day_pnl, sim.positions) == (D("50096.00"), D("96.00"), {})
    assert sim.session is None
    assert sim.blocked_by == "flat_deadline"
    rejection = sim.check_order(order("late"))
    assert isinstance(rejection, AccountRejection)
    assert rejection.rule == "flat_deadline"


def test_early_close_flat_deadline_is_the_offset_before_the_close() -> None:
    sim = make_sim()
    sim.start_trading_day(EARLY)
    fill_on(sim, order("in", "buy", 1), flat_bar(at(EARLY, 12, 0), P), P)
    with pytest.raises(ValueError, match="end_trading_day"):
        sim.on_bar({"MES": flat_bar(at(EARLY, 12, 45), P)})
    [close] = sim.end_trading_day({"MES": flat_bar(at(EARLY, 12, 45), P + 4)})
    assert isinstance(close, FlatDeadlineClose)
    assert close.deadline_ns == at(EARLY, 12, 45)
    assert sim.balance == D("50005.00")


# ---------------------------------------------------------------- trading days


def test_fills_belong_to_the_trading_day_from_18_00_the_day_before() -> None:
    sim = make_sim()
    sim.start_trading_day(TUE)
    evening = flat_bar(at(MON, 18, 30), P)  # Monday evening is Tuesday's trading day
    fill_on(sim, order("in", "buy", 1), evening, P)
    fill_on(sim, order("out", "sell", 1, "exit"), flat_bar(at(TUE, 9, 45), P + 8), P + 8)
    assert sim.day_pnl == D("10.00")
    with pytest.raises(ValueError, match="outside trading day"):
        sim.on_fill(Fill("x", at(MON, 17, 0), P, 1, D("0")), order("x"))
    with pytest.raises(ValueError, match="outside trading day"):
        sim.on_bar({"MES": flat_bar(at(WED, 10, 0), P)})


# ---------------------------------------------------------------- passes


def test_a_pass_ends_the_attempt_and_the_next_day_starts_a_new_one() -> None:
    sim = make_sim()
    assert not any(isinstance(e, AttemptEnded) for e in day_result(sim, MON, 300))
    events = day_result(sim, TUE, 300)  # +1,500 twice: 53,000 with a 1,500 best day
    result = ended(events)
    assert result == AttemptResult(
        number=1,
        outcome="passed",
        first_session=MON,
        last_session=TUE,
        trading_days=2,
        ended_ns=at(TUE, 16, 10),
        final_balance=D("53000.00"),
        mll_floor=D("50000.00"),
        profit_target=D("3000.00"),
        best_day=D("1500.00"),
        disabled_rules=(),
    )
    rejection = sim.check_order(order("after"))
    assert isinstance(rejection, AccountRejection)
    assert rejection.rule == "profit_target"
    assert sim.start_trading_day(WED) == [AttemptStarted(2, WED, D("50000.00"), D("48000.00"))]
    assert sim.results == (result,)


def test_the_consistency_target_raises_the_profit_target() -> None:
    sim = make_sim()
    day_result(sim, MON, 400)  # +2,000: target 2,000 / 0.55 = 3,636.37
    assert sim.profit_target == D("3636.37")
    assert day_result(sim, TUE, 300) == [FlatDeadlineClose(TUE, at(TUE, 16, 10), ())]
    assert sim.balance == D("53500.00")  # below 50,000 + 3,636.37
    assert sim.active


def test_disabled_rules_are_skipped_and_label_every_result() -> None:
    cfg = config(
        profit_target={"enabled": False},
        maximum_loss_limit={"enabled": False},
        consistency_target={"enabled": False},
    )
    sim = make_sim(cfg)
    assert sim.start_trading_day(MON) == [AttemptStarted(1, MON, D("50000.00"), None)]
    sim.end_trading_day({})
    day_result(sim, TUE, 1000)  # +5,000: no pass, no consistency raise
    assert (sim.profit_target, sim.mll_floor) == (D("3000.00"), None)
    sim.start_trading_day(WED)
    # 1 x -900 ticks x 1.25 = -1,125 at the low: the DLL still applies, the MLL does not.
    [liq] = fill_on(sim, order("in", "buy", 1), bar(at(WED, 10, 0), P, P, P - 900, P - 900), P)
    assert isinstance(liq, Liquidation)
    assert liq.rule == "daily_loss_limit"
    assert sim.balance == D("54000.00")
    sim.end_trading_day({})
    [result] = sim.finish()
    assert result.outcome == "incomplete"
    assert result.disabled_rules == ("profit_target", "maximum_loss_limit", "consistency_target")


def test_finish_records_an_incomplete_attempt() -> None:
    sim = make_sim(holidays=(WED,))
    day_result(sim, MON, 100)  # +500
    day_result(sim, THU, -40)  # -200; Tuesday was skipped and Wednesday is a holiday
    [result] = sim.finish()
    assert result == AttemptResult(
        number=1,
        outcome="incomplete",
        first_session=MON,
        last_session=THU,
        trading_days=3,  # Monday, Tuesday and Thursday
        ended_ns=None,
        final_balance=D("50300.00"),
        mll_floor=D("48500.00"),
        profit_target=D("3000.00"),
        best_day=D("500.00"),
        disabled_rules=(),
    )
    with pytest.raises(ValueError, match="finished"):
        sim.start_trading_day(FRI)


def test_finish_after_a_pass_adds_no_incomplete_attempt() -> None:
    sim = make_sim()
    day_result(sim, MON, 300)
    day_result(sim, TUE, 300)
    assert [r.outcome for r in sim.finish()] == ["passed"]


# ---------------------------------------------------------------- misuse


def test_the_calendar_must_use_the_account_flat_deadline() -> None:
    cfg = config(flat_deadline="15:00")
    calendar = SessionCalendar(date(2026, 3, 1), date(2026, 3, 31))
    with pytest.raises(ValueError, match="session_times"):
        AccountSim(cfg, calendar, FEES)
    with pytest.raises(ValueError, match="not one of the contracts"):
        make_sim(fees={"MGC": D("1.00")})


def test_out_of_order_calls_raise() -> None:
    sim = make_sim()
    with pytest.raises(ValueError, match="start_trading_day"):
        sim.on_bar({"MES": flat_bar(at(MON, 10, 0), P)})
    sim.start_trading_day(TUE)
    with pytest.raises(ValueError, match="has not ended"):
        sim.start_trading_day(WED)
    with pytest.raises(ValueError, match="same instant"):
        sim.on_bar({"MES": flat_bar(at(TUE, 10, 0), P), "MNQ": flat_bar(at(TUE, 10, 1), P, "MNQ")})
    with pytest.raises(ValueError, match="keyed as"):
        sim.on_bar({"MNQ": flat_bar(at(TUE, 10, 0), P)})
    sim.end_trading_day({})
    with pytest.raises(ValueError, match="not after"):
        sim.start_trading_day(MON)
    with pytest.raises(ValueError, match="not a session"):
        sim.start_trading_day(date(2026, 3, 7))
