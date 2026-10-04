"""Unit tests for the Risk_Manager (``fse.engine.risk``, design §16).

Kill_Switches and Lockouts (Req 16.1-16.8), the internal daily loss stop at
1-minute closes (Req 16.6) and the live order pre-check (Req 24.23-24.25).
Fills feed the Risk_Manager in order, as the engine's bar phase does.

**Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.8, 24.23, 24.24, 24.25**
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest

from fse.config.schema.kill_switches import KILL_SWITCH_IDS, KillSwitchesConfig
from fse.engine.risk import (
    CloseIntent,
    FailedCheck,
    Lockout,
    LossStopHit,
    RiskFill,
    RiskState,
    Withheld,
    active_lockouts,
    entries_blocked,
    entries_to_cancel,
    is_losing_trade,
    is_reducing,
    loss_stop_hit,
    loss_stop_limit,
    micro_equivalents,
    next_weekday_session,
    on_fill,
    on_minute_close,
    precheck,
    roll_session,
)
from fse.engine.types import Fill, Order, OrderKind, OrderRole, SetupKey, Side, Trade
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, ny_instant

GOOD_FRIDAY = date(2026, 4, 3)
CAL = SessionCalendar(date(2026, 3, 2), date(2026, 4, 30), holidays=[GOOD_FRIDAY])
TUE, WED, THU = date(2026, 3, 31), date(2026, 4, 1), date(2026, 4, 2)
MON = date(2026, 4, 6)

DEFAULTS = KillSwitchesConfig()
RISK_USD = Decimal("375")
ZERO = Decimal(0)


def only(*rules: str, **overrides: Any) -> KillSwitchesConfig:
    """A config with just ``rules`` enabled; ``overrides`` merge into the sections."""
    data: dict[str, Any] = {rule: {"enabled": rule in rules} for rule in KILL_SWITCH_IDS}
    for name, value in overrides.items():
        data[name] = {**data[name], **value} if isinstance(value, dict) else value
    return KillSwitchesConfig.model_validate(data)


def at(d: date, hh: int, mm: int) -> Instant:
    return ny_instant(d, time(hh, mm))


def fill(client_id: str, t: Instant, qty: int = 1, fees: str = "0") -> Fill:
    return Fill(client_id, t, 20000, qty, Decimal(fees))


def trade(entry: Fill, exits: tuple[Fill, ...], net: Decimal, shadow: bool = False) -> Trade:
    r = Decimal("100")
    session = CAL.trading_day_of(entry.bar_open_ns)
    assert session is not None
    key = SetupKey("MES", "gatekeeper_fade", 5800.0, "long", session, 1)
    return Trade(key, entry, 19980, entry.qty, exits, net, r, net / r, False, ZERO, ZERO, 0, shadow)


def round_trip(
    rs: RiskState, cfg: KillSwitchesConfig, cid: str, d: date, minute: int, gross: str
) -> RiskState:
    """One 1-contract trade: entry at 10:00 + ``minute``, full exit one minute later."""
    t = at(d, 10, 0) + minute * NS_PER_MINUTE
    entry, exit_ = fill(f"{cid}-e", t), fill(f"{cid}-x", t + NS_PER_MINUTE)
    rs = on_fill(rs, RiskFill(entry, "entry"), None, cfg, CAL)
    closed = trade(entry, (exit_,), Decimal(gross))
    return on_fill(rs, RiskFill(exit_, "stop", Decimal(gross)), closed, cfg, CAL)


def order(
    side: Side,
    qty: int,
    instrument: str = "MES",
    role: OrderRole = "entry",
    kind: OrderKind = "limit",
) -> Order:
    price = None if kind == "market" else 20000
    return Order(f"{role}-{side}-{qty}", None, instrument, side, kind, qty, price, 0, role)


# ---------------------------------------------------------------- kill switches


def test_max_trades_locks_at_the_first_fill_of_the_last_allowed_entry() -> None:
    cfg = only("max_trades")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "50")
    rs = round_trip(rs, cfg, "b", TUE, 10, "50")
    assert (rs.session_entries, rs.lockouts) == (2, ())
    t = at(TUE, 11, 0)
    rs = on_fill(rs, RiskFill(fill("c-e", t), "entry"), None, cfg, CAL)
    assert rs.lockouts == (Lockout("max_trades", t, TUE),)
    # A second partial fill of the same entry order is not another entry.
    rs = on_fill(rs, RiskFill(fill("c-e", t + NS_PER_MINUTE), "entry"), None, cfg, CAL)
    assert (rs.session_entries, len(rs.lockouts)) == (3, 1)


def test_max_losers_locks_when_the_session_count_reaches_the_limit() -> None:
    cfg = only("max_losers")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "-100")
    assert (rs.session_losers, rs.lockouts) == (1, ())
    rs = round_trip(rs, cfg, "b", TUE, 10, "-100")
    assert rs.session_losers == 2
    assert rs.lockouts == (Lockout("max_losers", at(TUE, 10, 11), TUE),)


def test_losing_trade_tolerance_sets_the_losing_line() -> None:
    cfg = only("max_losers", max_losers={"limit": 1}, losing_trade_tolerance_usd="10")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "-10")
    assert (rs.session_losers, rs.loss_streak, rs.lockouts) == (0, 0, ())
    rs = round_trip(rs, cfg, "b", TUE, 10, "-10.01")
    assert rs.session_losers == 1
    assert [lo.rule for lo in rs.lockouts] == ["max_losers"]
    tiny_loss = trade(fill("x", at(TUE, 10, 0)), (fill("y", at(TUE, 10, 1)),), Decimal("-0.01"))
    assert is_losing_trade(tiny_loss, DEFAULTS)


def test_consecutive_losers_count_across_sessions_and_skip_weekend_and_holiday() -> None:
    cfg = only("consecutive_losers")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "-100")
    rs = round_trip(rs, cfg, "b", WED, 0, "-100")
    assert (rs.loss_streak, rs.session_losers) == (2, 1)
    rs = round_trip(rs, cfg, "c", THU, 0, "-100")
    # Good Friday is a holiday, so the Lockout covers Thursday and Monday.
    assert rs.lockouts == (Lockout("consecutive_losers", at(THU, 10, 1), MON),)
    assert rs.loss_streak == 0
    assert entries_blocked(rs, MON)
    assert not entries_blocked(rs, date(2026, 4, 7))


def test_a_non_losing_close_resets_the_loss_streak() -> None:
    cfg = only("consecutive_losers")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "-100")
    rs = round_trip(rs, cfg, "b", TUE, 10, "-100")
    assert rs.loss_streak == 2
    rs = round_trip(rs, cfg, "c", TUE, 20, "0")
    assert rs.loss_streak == 0
    rs = round_trip(rs, cfg, "d", TUE, 30, "-100")
    assert (rs.loss_streak, rs.lockouts) == (1, ())


def test_next_weekday_session_skips_weekends_and_holidays() -> None:
    assert next_weekday_session(date(2026, 3, 27), CAL) == date(2026, 3, 30)
    assert next_weekday_session(THU, CAL) == MON
    # Past the calendar's last date, only weekends are skipped.
    assert next_weekday_session(date(2026, 4, 30), CAL) == date(2026, 5, 1)


def test_red_day_locks_on_a_close_below_the_threshold_only() -> None:
    cfg = only("red_day")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "0")
    assert rs.lockouts == ()
    rs = round_trip(rs, cfg, "b", TUE, 10, "-0.01")
    assert rs.lockouts == (Lockout("red_day", at(TUE, 10, 11), TUE),)
    # A partial exit leaves the trade open, so red_day waits for the last exit.
    t = at(WED, 10, 0)
    rs = on_fill(rs, RiskFill(fill("p-e", t, qty=2), "entry"), None, cfg, CAL)
    rs = on_fill(
        rs, RiskFill(fill("p-1", t + NS_PER_MINUTE), "tp1", Decimal("-50")), None, cfg, CAL
    )
    assert (rs.session_net, rs.lockouts) == (Decimal("-50"), ())


def test_daily_profit_cap_locks_on_the_exit_fill_that_reaches_it() -> None:
    cfg = only("daily_profit_cap")
    entry = fill("a-e", at(TUE, 10, 0), qty=2)
    rs = on_fill(RiskState(), RiskFill(entry, "entry"), None, cfg, CAL)
    tp1 = fill("a-1", at(TUE, 10, 5))
    rs = on_fill(rs, RiskFill(tp1, "tp1", Decimal("1200")), None, cfg, CAL)
    assert rs.lockouts == (Lockout("daily_profit_cap", at(TUE, 10, 5), TUE),)
    rest = fill("a-2", at(TUE, 10, 9))
    closed = trade(entry, (tp1, rest), Decimal("1210"))
    rs = on_fill(rs, RiskFill(rest, "stop", Decimal("10")), closed, cfg, CAL)
    assert (rs.session_net, len(rs.lockouts)) == (Decimal("1210"), 1)


def test_session_net_counts_every_fee_and_ends_at_the_trade_net() -> None:
    cfg = only()
    t = at(TUE, 10, 0)
    entry = fill("a-e", t, qty=2, fees="1.24")
    rs = on_fill(RiskState(), RiskFill(entry, "entry"), None, cfg, CAL)
    assert rs.session_net == Decimal("-1.24")
    tp1 = fill("a-1", t + NS_PER_MINUTE, fees="0.62")
    rs = on_fill(rs, RiskFill(tp1, "tp1", Decimal("25.00")), None, cfg, CAL)
    assert rs.session_net == Decimal("23.14")
    rest = fill("a-2", t + 2 * NS_PER_MINUTE, fees="0.62")
    closed = trade(entry, (tp1, rest), Decimal("22.52"))
    rs = on_fill(rs, RiskFill(rest, "stop"), closed, cfg, CAL)
    assert rs.session_net == closed.net


def test_disabled_rules_start_nothing_but_counters_still_count() -> None:
    cfg = only()
    rs = RiskState()
    for i in range(4):
        rs = round_trip(rs, cfg, f"t{i}", TUE, 10 * i, "-100")
    assert (rs.session_entries, rs.session_losers, rs.loss_streak) == (4, 4, 4)
    assert rs.lockouts == ()


def test_one_fill_can_start_several_lockouts_in_rule_order() -> None:
    rs = round_trip(RiskState(), DEFAULTS, "a", TUE, 0, "-100")
    assert [lo.rule for lo in rs.lockouts] == ["red_day"]
    rs = round_trip(rs, DEFAULTS, "b", TUE, 10, "-100")
    assert [lo.rule for lo in rs.lockouts] == ["red_day", "max_losers", "red_day"]
    assert active_lockouts(rs, TUE) == rs.lockouts


# ---------------------------------------------------------------- sessions


def test_a_new_session_resets_counters_and_drops_ended_lockouts() -> None:
    rs = round_trip(RiskState(), DEFAULTS, "a", TUE, 0, "-100")
    assert rs.session == TUE
    rolled = roll_session(rs, WED)
    assert rolled == RiskState(session=WED, loss_streak=1)
    assert roll_session(rolled, WED) is rolled
    with pytest.raises(ValueError, match="before the Risk_Manager's session"):
        roll_session(rolled, TUE)


def test_an_evening_fill_belongs_to_the_next_session() -> None:
    rs = on_fill(RiskState(), RiskFill(fill("e", at(TUE, 18, 30)), "entry"), None, DEFAULTS, CAL)
    assert rs.session == WED


def test_malformed_fill_events_raise() -> None:
    rs = round_trip(RiskState(), DEFAULTS, "a", WED, 0, "50")
    after_deadline = fill("late", at(WED, 17, 0))
    with pytest.raises(ValueError, match="outside every trading day"):
        on_fill(rs, RiskFill(after_deadline, "entry"), None, DEFAULTS, CAL)
    with pytest.raises(ValueError, match="before the Risk_Manager's session"):
        on_fill(rs, RiskFill(fill("old", at(TUE, 10, 0)), "entry"), None, DEFAULTS, CAL)
    entry, exit_ = fill("b-e", at(WED, 11, 0)), fill("b-x", at(WED, 11, 1))
    shadow = trade(entry, (exit_,), Decimal("10"), shadow=True)
    with pytest.raises(ValueError, match="Shadow_Trade"):
        on_fill(rs, RiskFill(exit_, "tp1", Decimal("10")), shadow, DEFAULTS, CAL)
    other = trade(entry, (fill("b-y", at(WED, 11, 2)),), Decimal("10"))
    with pytest.raises(ValueError, match="must end with the fill"):
        on_fill(rs, RiskFill(exit_, "tp1", Decimal("10")), other, DEFAULTS, CAL)
    with pytest.raises(ValueError, match="entry fill cannot close"):
        on_fill(rs, RiskFill(entry, "entry"), trade(entry, (exit_,), ZERO), DEFAULTS, CAL)


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: RiskFill(fill("e", 0), "entry", Decimal("5")), "entry RiskFill has no gross"),
        (lambda: RiskFill(fill("e", 0), "close", ZERO), "RiskFill.role"),  # type: ignore[arg-type]
        (lambda: RiskFill(fill("e", 0), "stop", Decimal("NaN")), "finite Decimal"),
        (lambda: Lockout("halt", 0, TUE), "Lockout.rule"),  # type: ignore[arg-type]
        (lambda: RiskState(session_losers=1), "no session"),
        (lambda: RiskState(session=TUE, filled_entries=("a", "a")), "repeat"),
        (lambda: RiskState(loss_streak=-1), "at least 0"),
        (lambda: LossStopHit(0, Decimal("-700"), Decimal("-750")), "at or below"),
        (lambda: Withheld(order("buy", 1), ()), "at least one failed check"),
        (lambda: CloseIntent("MES", "sell", 0), "at least 1"),
    ],
)
def test_risk_values_reject_malformed_input(build: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build()


# ---------------------------------------------------------------- internal daily loss stop


def test_loss_stop_limit_is_minus_multiple_times_risk_dollars() -> None:
    assert loss_stop_limit(RISK_USD, DEFAULTS) == Decimal("-750")
    cfg = only(internal_daily_loss_stop={"multiple": 1.5})
    assert loss_stop_limit(RISK_USD, cfg) == Decimal("-562.5")
    with pytest.raises(ValueError, match="above 0"):
        loss_stop_limit(ZERO, DEFAULTS)


def test_the_internal_loss_stop_closes_every_position_then_locks_out() -> None:
    cfg = only("internal_daily_loss_stop")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "-500")
    t = at(TUE, 10, 30)
    positions = {"MNQ": -2, "MES": 3, "ES": 0}
    same, closes = on_minute_close(rs, t, positions, Decimal("-249.99"), RISK_USD, cfg, CAL)
    assert (same, closes) == (rs, ())
    t2 = t + NS_PER_MINUTE
    rs, closes = on_minute_close(rs, t2, positions, Decimal("-250"), RISK_USD, cfg, CAL)
    assert closes == (CloseIntent("MES", "sell", 3), CloseIntent("MNQ", "buy", 2))
    assert rs.lockouts == (Lockout("internal_daily_loss_stop", t2, TUE),)
    assert rs.loss_stop == LossStopHit(t2, Decimal("-750"), Decimal("-750"))
    # It fires once per session, so the next close issues no second market close.
    again, closes = on_minute_close(rs, t2 + NS_PER_MINUTE, {}, ZERO, RISK_USD, cfg, CAL)
    assert (again, closes) == (rs, ())
    # A new session clears it; with no open position a fire closes nothing.
    rs = round_trip(rs, cfg, "b", WED, 0, "-800")
    rs, closes = on_minute_close(rs, at(WED, 10, 5), {"MES": 0}, ZERO, RISK_USD, cfg, CAL)
    assert closes == ()
    assert active_lockouts(rs, WED) == (Lockout("internal_daily_loss_stop", at(WED, 10, 5), WED),)


def test_the_internal_loss_stop_is_off_by_default() -> None:
    rs = round_trip(RiskState(), only(), "a", TUE, 0, "-5000")
    after, closes = on_minute_close(
        rs, at(TUE, 10, 5), {"MES": 1}, Decimal("-100"), RISK_USD, DEFAULTS, CAL
    )
    assert (after, closes) == (rs, ())


def test_minute_closes_outside_a_trading_day_change_nothing_and_later_ones_roll() -> None:
    cfg = only("internal_daily_loss_stop")
    rs = round_trip(RiskState(), cfg, "a", TUE, 0, "-100")
    late = at(TUE, 17, 0)
    assert on_minute_close(rs, late, {}, Decimal("-9999"), RISK_USD, cfg, CAL) == (rs, ())
    rolled, _ = on_minute_close(rs, at(WED, 9, 31), {}, ZERO, RISK_USD, cfg, CAL)
    assert rolled == RiskState(session=WED, loss_streak=1)


# ---------------------------------------------------------------- lockout queries


def test_a_lockout_cancels_resting_entries_and_keeps_exit_orders() -> None:
    working = (
        order("buy", 1, role="entry"),
        order("sell", 1, role="stop", kind="stop"),
        order("sell", 1, role="tp1"),
    )
    rs = RiskState(session=TUE)
    assert not entries_blocked(rs, TUE)
    assert entries_to_cancel(rs, TUE, working) == ()
    locked = replace(rs, lockouts=(Lockout("max_trades", at(TUE, 10, 0), TUE),))
    assert entries_blocked(locked, TUE)
    assert entries_to_cancel(locked, TUE, working) == (working[0],)
    assert entries_to_cancel(locked, WED, working) == ()


# ---------------------------------------------------------------- pre-check

HIT = LossStopHit(at(TUE, 11, 0), Decimal("-760"), Decimal("-750"))
STOPPED = RiskState(
    session=TUE,
    session_net=Decimal("-760"),
    lockouts=(Lockout("internal_daily_loss_stop", HIT.at, TUE),),
    loss_stop=HIT,
)


def test_micro_equivalents_count_minis_as_ten() -> None:
    assert micro_equivalents("MES", 3) == 3
    assert micro_equivalents("MNQ", -2) == 2
    assert micro_equivalents("NQ", 1) == 10
    with pytest.raises(ValueError, match="no Micro_Equivalent size"):
        micro_equivalents("MGC", 1)


def test_only_opposite_side_orders_within_the_position_reduce() -> None:
    positions = {"MES": 2, "MNQ": -1}
    assert is_reducing(order("sell", 2), positions)
    assert is_reducing(order("buy", 1, "MNQ"), positions)
    assert not is_reducing(order("sell", 3), positions)  # would flip short
    assert not is_reducing(order("buy", 1), positions)  # adds to the long
    assert not is_reducing(order("sell", 1, "ES"), positions)  # flat instrument


@pytest.mark.parametrize(
    "reducing",
    [
        order("sell", 5, role="exit", kind="market"),
        order("sell", 3, role="stop", kind="stop"),
        order("buy", 2, "MNQ", role="tp1"),
    ],
)
def test_reducing_orders_always_pass(reducing: Order) -> None:
    positions = {"MES": 5, "MNQ": -2}
    assert precheck(STOPPED, reducing, TUE, positions, {"MES": 40}, 1) is reducing


def test_an_opening_order_past_the_position_cap_is_withheld() -> None:
    positions, working = {"MES": 5, "MNQ": -2}, {"MNQ": 1}
    entry = order("buy", 1, "ES")  # 5 + 2 + 1 + 10 = 18 Micro_Equivalents
    state = RiskState(session=TUE)
    assert precheck(state, entry, TUE, positions, working, 18) is entry
    assert precheck(state, entry, TUE, positions, working, 17) == Withheld(
        entry, (FailedCheck("position_cap", 18, 17),)
    )
    assert precheck(state, entry, TUE, positions, working, None) is entry


def test_an_opening_order_after_the_internal_loss_stop_is_withheld() -> None:
    entry = order("buy", 1)
    failed = FailedCheck("internal_daily_loss_stop", Decimal("-760"), Decimal("-750"))
    assert precheck(STOPPED, entry, TUE, {}, {}, 50) == Withheld(entry, (failed,))
    assert loss_stop_hit(STOPPED, WED) is None
    assert precheck(STOPPED, entry, WED, {}, {}, 50) is entry
    with pytest.raises(ValueError, match="before the Risk_Manager's session"):
        precheck(STOPPED, entry, date(2026, 3, 30), {}, {}, 50)


def test_every_failed_check_is_reported_in_order() -> None:
    flip = order("sell", 4)  # the long is 2, so this opens a short
    result = precheck(STOPPED, flip, TUE, {"MES": 2}, {}, 5)
    assert isinstance(result, Withheld)
    assert [(f.check, f.measured, f.limit) for f in result.failures] == [
        ("position_cap", 6, 5),
        ("internal_daily_loss_stop", Decimal("-760"), Decimal("-750")),
    ]


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"working_entries": {"MES": -1}}, "must not be negative"),
        ({"position_cap": 0}, "at least 1"),
        ({"positions": {"MES": 1.5}}, "must be an integer"),
        ({"order": order("buy", 1, "MGC")}, "no Micro_Equivalent size"),
    ],
)
def test_precheck_rejects_malformed_input(args: dict[str, Any], match: str) -> None:
    call: dict[str, Any] = {
        "rs": RiskState(session=TUE),
        "order": order("buy", 1),
        "session": TUE,
        "positions": {},
        "working_entries": {},
        "position_cap": 50,
        **args,
    }
    with pytest.raises(ValueError, match=match):
        precheck(**call)
