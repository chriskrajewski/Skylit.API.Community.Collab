"""The pure paper-account rule engine (design 3.1-3.4, A-4, A-6, A-8, A-F5, A-F7, A-F8)."""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from fse.config.schema.account import PaperAccountRules
from fse.engine.types import Fill, SetupKey, Trade
from fse.live.host.accounts import (
    ACCOUNT_CODEC,
    FlattenMembers,
    Logged,
    PaperAccount,
    PaperAccountState,
    StatusChanged,
    mll_fingerprint,
    rules_fingerprint,
    trade_id,
)
from fse.sim.account import mll_floor_for, next_mll_floor

D = Decimal
START = D("50000.00")
ZERO = D("0.00")
TUE, WED, THU, FRI = (date(2026, 10, d) for d in (13, 14, 15, 16))
MON = date(2026, 10, 19)
NOW = 1_800_000_000_000_000_000
_BAR = itertools.count(1)


def rules(**over: Any) -> PaperAccountRules:
    return PaperAccountRules.model_validate(over)


def fresh(**over: Any) -> PaperAccount:
    return PaperAccount.fresh("a", rules(**over), ("p1", "p2"))


def opened(day: date = TUE, **over: Any) -> PaperAccount:
    acct = fresh(**over)
    acct.open_day(day)
    return acct


def trade(net: str, tag: str = "t1", bar_ns: int = 1, plugin: str = "p1") -> Trade:
    key = SetupKey("MNQ", "test", 0.0, "long", TUE, 1)
    return Trade(
        setup_key=key,
        entry_fill=Fill(f"{plugin}:{tag}:entry", bar_ns, 100, 1, ZERO),
        initial_stop=90,
        qty_at_entry=1,
        exits=(Fill(f"{plugin}:{tag}:exit", bar_ns + 60, 100, 1, ZERO),),
        net=D(net),
        r=D("5.00"),
        r_multiple=D("0"),
        reached_tp1=False,
        mae_r=D("0"),
        mfe_r=D("0"),
        missing_bars=0,
        shadow=False,
    )


_TRADES = itertools.count(1)


def close(acct: PaperAccount, net: str) -> list[Any]:
    """Book a closed trade with a unique id."""
    return acct.on_closed(trade(net, tag=f"t{next(_TRADES)}"), NOW)


def bar(
    acct: PaperAccount,
    worst: str = "0",
    best: str | None = None,
    at_close: str | None = None,
    instrument: str = "MNQ",
    close_t: int = 100,
) -> list[Any]:
    """Evaluate one new bar; open-P&L sums default to ``worst``."""
    w = D(worst)
    b = w if best is None else D(best)
    c = w if at_close is None else D(at_close)
    ns = next(_BAR) * 60_000_000_000
    return acct.on_bar(instrument, ns, w, b, c, NOW + ns, close_t=close_t, close_ns=ns + 60)


def end_day(acct: PaperAccount, new_day: date | None) -> list[Any]:
    return acct.roll(new_day, NOW)


# ---------------------------------------------------------------- A-4: floors


def test_fresh_account_starts_at_the_starting_balance() -> None:
    s = fresh().state
    assert (s.balance, s.hwm, s.eod_high, s.mll_floor) == (START, START, START, D("48000.00"))
    assert (s.status, s.day, s.trading_days, s.profit_target) == ("active", None, 0, D("3000.00"))
    assert s.plugins == ("p1", "p2")
    assert fresh(maximum_loss_limit={"enabled": False}).state.mll_floor is None


def test_static_floor_never_moves() -> None:
    acct = opened(maximum_loss_limit={"mode": "static"})
    bar(acct, "0", best="4000")
    close(acct, "2500")
    end_day(acct, WED)
    bar(acct, "0", best="1000")
    close(acct, "1000")
    end_day(acct, THU)
    assert acct.state.balance == D("53500.00")
    assert acct.state.mll_floor == D("48000.00")


def test_eod_trailing_moves_only_at_day_end_and_never_down() -> None:
    acct = opened()
    bar(acct, "0", best="1500", at_close="1500")
    assert acct.state.mll_floor == D("48000.00")  # an intraday high does not move it
    acct.revalue(ZERO)
    close(acct, "1000")
    assert acct.state.mll_floor == D("48000.00")
    end_day(acct, WED)  # eod 51000 -> min(50000, max(48000, 49000))
    assert (acct.state.eod_high, acct.state.mll_floor) == (D("51000.00"), D("49000.00"))
    close(acct, "-500")
    end_day(acct, THU)  # eod 50500: the floor does not move down
    assert acct.state.mll_floor == D("49000.00")
    close(acct, "2500")
    end_day(acct, FRI)  # eod 53000 -> 51000, locked at the starting balance
    assert acct.state.mll_floor == START


def test_eod_trailing_without_trail_lock_rises_above_the_start() -> None:
    acct = opened(maximum_loss_limit={"trail_lock": False})
    close(acct, "3000")
    end_day(acct, WED)
    assert acct.state.mll_floor == D("51000.00")


def test_eod_high_counts_open_pnl_at_the_last_mark() -> None:
    acct = opened()
    close(acct, "500")
    bar(acct, "-100", best="900", at_close="800")
    end_day(acct, WED)  # equity 50500 + 800
    assert acct.state.eod_high == D("51300.00")
    assert acct.state.mll_floor == D("49300.00")
    assert acct.state.open_pnl_day_start == D("800")


def test_intraday_trailing_follows_the_best_price_hwm_and_never_down() -> None:
    acct = opened(maximum_loss_limit={"mode": "intraday_trailing"})
    bar(acct, "-200", best="1500")  # unrealized at the bar's best price
    assert (acct.state.hwm, acct.state.mll_floor) == (D("51500.00"), D("49500.00"))
    bar(acct, "-300", best="500")
    assert (acct.state.hwm, acct.state.mll_floor) == (D("51500.00"), D("49500.00"))
    bar(acct, "0", best="3000")
    assert acct.state.mll_floor == START  # trail lock


def test_intraday_trailing_without_trail_lock() -> None:
    acct = opened(maximum_loss_limit={"mode": "intraday_trailing", "trail_lock": False})
    bar(acct, "0", best="3000")
    assert acct.state.mll_floor == D("51000.00")


def test_intraday_breach_is_judged_before_the_hwm_moves() -> None:
    acct = opened(maximum_loss_limit={"mode": "intraday_trailing"})
    # Equity at the worst price touches the floor; the same bar's high is ignored.
    actions = bar(acct, "-2000", best="3000")
    assert actions == [
        StatusChanged("active", "breached", "account_mll"),
        FlattenMembers("account_mll"),
    ]
    assert (acct.state.hwm, acct.state.mll_floor) == (START, D("48000.00"))
    # Above the floor at its worst, the bar raises the HWM; the raised floor is
    # first applied to the next bar.
    acct = opened(
        maximum_loss_limit={"mode": "intraday_trailing"}, daily_loss_limit={"enabled": False}
    )
    assert bar(acct, "-1900", best="3000") == []
    assert acct.state.mll_floor == START
    assert bar(acct, "-100")[0] == StatusChanged("active", "breached", "account_mll")


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.integers(min_value=-300_000, max_value=300_000), min_size=1, max_size=25),
    st.integers(min_value=1, max_value=4_999_999),
)
def test_eod_trailing_with_trail_lock_equals_iterating_next_mll_floor(
    day_nets: list[int], mll_cents: int
) -> None:
    mll = D(mll_cents) / 100
    acct = opened(maximum_loss_limit={"value": str(mll)})
    floor = START - mll
    for k, cents in enumerate(day_nets):
        acct.on_closed(trade(str(D(cents) / 100), tag=f"d{k}", bar_ns=k + 1), NOW)
        acct.roll(date.fromordinal(TUE.toordinal() + k + 1), NOW)
        floor = next_mll_floor(floor, acct.state.balance, mll, START)
        assert acct.state.mll_floor == floor
    m = acct.rules.maximum_loss_limit
    assert mll_floor_for(m, START, acct.state.eod_high, acct.state.hwm) == floor


def test_mll_floor_for_is_none_when_disabled() -> None:
    m = rules(maximum_loss_limit={"enabled": False}).maximum_loss_limit
    assert mll_floor_for(m, START, START, START) is None


# ---------------------------------------------------------------- A-F5: rule changes


def _restore(acct: PaperAccount, **over: Any) -> tuple[PaperAccount, list[Any]]:
    return PaperAccount.restore(acct.state, rules(**over), ("p1",), NOW + 1)


def test_unchanged_rules_restore_as_is() -> None:
    acct = opened()
    close(acct, "700")
    again, actions = PaperAccount.restore(acct.state, acct.rules, ("p1", "p2"), NOW)
    assert actions == []
    assert again.state == acct.state


def test_static_value_change_moves_the_floor() -> None:
    acct = opened(maximum_loss_limit={"mode": "static"})
    close(acct, "400")
    again, actions = _restore(acct, maximum_loss_limit={"mode": "static", "value": "3000.00"})
    assert again.state.mll_floor == D("47000.00")
    assert again.state.balance == D("50400.00")
    assert again.state.rules_changed_at == NOW + 1
    assert again.state.plugins == ("p1",)
    [logged] = actions
    assert isinstance(logged, Logged)
    assert logged.kind == "account_rules_changed"
    assert logged.level == "warning"
    assert (logged.detail["old_floor"], logged.detail["new_floor"]) == ("48000.00", "47000.00")
    assert logged.detail["new_fingerprint"] == rules_fingerprint(again.rules)
    assert again.state.mll_fingerprint == mll_fingerprint(again.rules)


def _peaks(**over: Any) -> PaperAccount:
    """An account with hwm 51500 and eod_high 50800 (both below the trail lock)."""
    acct = opened(**over)
    bar(acct, "0", best="1500")
    close(acct, "800")
    acct.revalue(ZERO)
    end_day(acct, WED)
    assert (acct.state.hwm, acct.state.eod_high) == (D("51500.00"), D("50800.00"))
    return acct


def test_enable_from_disabled_uses_the_peaks_tracked_while_off() -> None:
    acct = _peaks(maximum_loss_limit={"enabled": False})
    assert acct.state.mll_floor is None
    eod, actions = _restore(acct)
    assert eod.state.mll_floor == D("48800.00")
    assert actions[0].detail["old_floor"] is None
    intraday, _ = _restore(acct, maximum_loss_limit={"mode": "intraday_trailing"})
    assert intraday.state.mll_floor == D("49500.00")


def test_mode_change_to_intraday_uses_the_hwm_and_disabling_clears_the_floor() -> None:
    acct = _peaks()
    assert acct.state.mll_floor == D("48800.00")
    intraday, actions = _restore(acct, maximum_loss_limit={"mode": "intraday_trailing"})
    assert intraday.state.mll_floor == D("49500.00")
    assert (actions[0].detail["old_floor"], actions[0].detail["new_floor"]) == (
        "48800.00",
        "49500.00",
    )
    off, actions = _restore(acct, maximum_loss_limit={"enabled": False})
    assert off.state.mll_floor is None
    assert actions[0].detail["new_floor"] is None


def test_a_non_mll_change_keeps_the_floor_and_every_balance() -> None:
    acct = _peaks()
    again, [logged] = _restore(acct, daily_loss_limit={"value": "800.00"})
    assert "old_floor" not in logged.detail
    keep = ("balance", "realized", "hwm", "eod_high", "status", "day", "mll_floor")
    for name in keep:
        assert getattr(again.state, name) == getattr(acct.state, name), name


def test_a_new_starting_balance_is_logged_and_waits_for_a_reset() -> None:
    acct = opened()
    again, actions = _restore(acct, starting_balance={"value": "100000.00"})
    assert again.state.starting_balance == START
    assert [a.kind for a in actions] == ["account_rules_changed", "account_start_changed"]
    assert actions[1].detail["configured"] == "100000.00"


def test_restore_keeps_a_breach() -> None:
    acct = opened()
    bar(acct, "-2000")
    again, _ = _restore(acct, daily_loss_limit={"value": "900.00"})
    assert again.state.status == "breached"
    assert again.entry_blocks() == ("account_breached",)


# ---------------------------------------------------------------- A-6: DLL and MLL


def test_dll_flat_locks_and_clears_at_the_rotation() -> None:
    acct = opened()
    close(acct, "-600")
    actions = bar(acct, "-400")
    assert actions == [
        StatusChanged("active", "flat_locked", "account_dll"),
        FlattenMembers("account_dll"),
    ]
    assert acct.entry_blocks() == ("account_daily_loss",)
    assert end_day(acct, WED) == [StatusChanged("flat_locked", "active", "account_day_end")]
    s = acct.state
    assert (s.status, s.day, s.day_net, s.trading_days) == ("active", WED, ZERO, 1)
    assert acct.entry_blocks() == ()


def test_dll_is_not_checked_without_a_day_or_when_disabled() -> None:
    acct = fresh()
    assert bar(acct, "-1500") == []
    acct = opened(daily_loss_limit={"enabled": False})
    assert bar(acct, "-1500") == []


def test_dll_counts_only_the_change_of_carried_trades_since_the_day_start() -> None:
    acct = opened()
    bar(acct, "-300")
    end_day(acct, WED)  # the trade is held over the break at -300
    assert acct.state.open_pnl_day_start == D("-300")
    assert bar(acct, "-1200") == []  # -900 today
    assert bar(acct, "-1300")[0] == StatusChanged("active", "flat_locked", "account_dll")


def test_mll_breach_never_clears_on_the_rotation() -> None:
    acct = opened()
    bar(acct, "-2000")
    assert acct.state.breach_reason == "account_mll"
    assert end_day(acct, WED) == []
    end_day(acct, THU)
    assert acct.state.status == "breached"
    assert acct.entry_blocks() == ("account_breached",)


def test_breach_balances_are_fill_model_values_not_clamped() -> None:
    acct = opened()
    bar(acct, "-2000")
    close(acct, "-2350")  # the next_open exit filled past the limit
    assert acct.state.balance == D("47650.00")
    assert acct.state.balance < acct.state.mll_floor  # type: ignore[operator]


# ---------------------------------------------------------------- transition table


def test_three_more_bars_after_an_mll_breach_return_nothing() -> None:
    acct = opened()
    assert len(bar(acct, "-2100")) == 2
    for worst in ("-2200", "-2500", "-1500"):
        assert bar(acct, worst) == []


def test_three_more_bars_after_a_dll_lock_return_nothing() -> None:
    acct = opened()
    assert len(bar(acct, "-1000")) == 2
    for worst in ("-1100", "-1200", "-1000"):
        assert bar(acct, worst) == []
    assert acct.state.status == "flat_locked"


def test_a_flat_locked_account_crossing_the_mll_returns_exactly_one_pair() -> None:
    acct = opened()
    bar(acct, "-1000")
    assert bar(acct, "-2000") == [
        StatusChanged("flat_locked", "breached", "account_mll"),
        FlattenMembers("account_mll"),
    ]
    assert bar(acct, "-2100") == []


def test_a_breached_account_crossing_the_dll_returns_nothing() -> None:
    acct = PaperAccount.restore_failed("a", rules(), ("p1",), NOW)
    acct.open_day(TUE)
    assert bar(acct, "-1500") == []
    assert bar(acct, "-5000") == []


def test_passed_is_checked_for_both_limits() -> None:
    acct = opened(consistency_target={"enabled": False})
    close(acct, "3000")
    assert end_day(acct, WED) == [StatusChanged("active", "passed", "account_profit_target")]
    assert acct.state.passed_at == NOW
    assert bar(acct, "-1000") == [
        StatusChanged("passed", "flat_locked", "account_dll"),
        FlattenMembers("account_dll"),
    ]
    assert end_day(acct, THU) == [StatusChanged("flat_locked", "passed", "account_day_end")]
    floor = acct.state.mll_floor
    assert floor == START
    assert bar(acct, "-3000") == [
        StatusChanged("passed", "breached", "account_mll"),
        FlattenMembers("account_mll"),
    ]


def test_bookkeeping_runs_in_every_status() -> None:
    acct = PaperAccount.restore_failed("a", rules(), ("p1",), NOW)
    bar(acct, "-10", best="700", at_close="650", close_t=4321)
    s = acct.state
    assert (s.marks["MNQ"], s.unrealized, s.hwm) == (4321, D("650"), D("50700.00"))
    assert acct.view()["equity"] == "50650.00"


def test_a_replayed_bar_is_ignored() -> None:
    acct = opened()
    acct.on_bar("MNQ", 10, ZERO, ZERO, ZERO, NOW, close_t=1, close_ns=70)
    before = acct.state
    assert acct.on_bar("MNQ", 10, D("-5000"), ZERO, ZERO, NOW, close_t=2, close_ns=70) == []
    assert acct.on_bar("MNQ", 5, D("-5000"), ZERO, ZERO, NOW, close_t=2, close_ns=70) == []
    assert acct.state == before
    # Another instrument has its own watermark.
    acct.on_bar("MES", 10, ZERO, ZERO, ZERO, NOW, close_t=9, close_ns=70)
    assert acct.state.last_bar_open == {"MNQ": 10, "MES": 10}


# ---------------------------------------------------------------- pass, consistency, days


def test_consistency_raises_the_target_before_the_pass() -> None:
    acct = opened()
    close(acct, "2000")
    assert end_day(acct, WED) == []
    assert acct.state.profit_target == D("3636.37")  # 2000 / 0.55 rounded up to a cent
    close(acct, "1100")
    assert end_day(acct, THU) == []  # 53100 < 53636.37
    close(acct, "600")
    assert end_day(acct, FRI) == [StatusChanged("active", "passed", "account_profit_target")]
    assert acct.state.best_day == D("2000")


def test_min_trading_days_delays_the_pass() -> None:
    acct = opened(consistency_target={"enabled": False}, min_trading_days=3)
    close(acct, "3500")
    assert end_day(acct, WED) == []
    end_day(acct, THU)  # no trade: not a trading day
    assert acct.state.trading_days == 1
    close(acct, "10")
    assert end_day(acct, FRI) == []
    close(acct, "10")
    assert end_day(acct, MON) == [StatusChanged("active", "passed", "account_profit_target")]
    assert acct.state.trading_days == 3


def test_a_disabled_profit_target_never_passes() -> None:
    acct = opened(profit_target={"enabled": False})
    close(acct, "9000")
    assert end_day(acct, WED) == []
    assert acct.state.status == "active"


# ---------------------------------------------------------------- closes and days


def test_applied_dedupes_a_trade_across_one_rotation() -> None:
    acct = opened()
    t = trade("120.00", tag="dup", bar_ns=77)
    assert acct.on_closed(t, NOW) == []
    acct.on_closed(t, NOW)
    assert (acct.state.balance, acct.state.day_net) == (D("50120.00"), D("120.00"))
    assert acct.state.applied == {trade_id(t)} == {"p1:dup:entry@77"}
    end_day(acct, WED)
    acct.on_closed(t, NOW)  # applied_prev still remembers it
    assert acct.state.balance == D("50120.00")
    assert acct.state.applied_prev == {trade_id(t)}
    assert acct.state.applied == frozenset()


def test_close_outside_a_day_books_to_the_ledger_only() -> None:
    acct = opened(TUE)
    close(acct, "100")
    end_day(acct, None)  # the Friday rotate in the weekend gap
    before = acct.state
    t = trade("-250.00", tag="stale", bar_ns=999)
    actions = acct.on_closed(t, NOW)
    assert actions == [
        Logged(
            "account_close_outside_day",
            "info",
            {"account": "a", "trade_id": "p1:stale:entry@999", "net": "-250.00"},
        )
    ]
    after = acct.state
    assert after == replace(
        before,
        realized=before.realized - D("250.00"),
        balance=before.balance - D("250.00"),
        applied=before.applied | {trade_id(t)},
    )
    acct.open_day(MON)
    s = acct.state
    assert (s.day, s.day_net, s.traded_today, s.trading_days) == (MON, ZERO, False, 1)


def test_roll_twice_is_a_no_op_and_open_day_on_a_set_day_too() -> None:
    acct = opened(TUE)
    close(acct, "50")
    end_day(acct, WED)
    first = acct.state
    assert end_day(acct, WED) == []
    assert acct.state == first
    acct.open_day(THU)
    assert acct.state == first


def test_weekend_roll_closes_friday_once() -> None:
    acct = opened(FRI)
    close(acct, "100")
    end_day(acct, None)
    assert (acct.state.day, acct.state.trading_days) == (None, 1)
    assert end_day(acct, None) == []  # no day open: no rollover
    assert end_day(acct, MON) == []
    acct.open_day(MON)
    assert (acct.state.day, acct.state.trading_days) == (MON, 1)


# ---------------------------------------------------------------- entry checks (A-8)


def test_shared_position_cap_refuses_the_entry_past_it() -> None:
    acct = opened()
    assert acct.check_entry("MNQ", 25, 30, time(10, 0)) == "account_max_position"
    assert acct.check_entry("MNQ", 20, 30, time(10, 0)) is None
    assert acct.check_entry("ES", 2, 30, time(10, 0)) is None  # 30 + 20 = 50
    assert acct.check_entry("ES", 3, 30, time(10, 0)) == "account_max_position"
    assert opened(position_cap={"enabled": False}).check_entry("ES", 9, 45, time(10)) is None


def test_no_trade_window_is_checked_before_the_cap() -> None:
    acct = opened(no_trade_windows=[{"start": "09:25", "end": "09:35"}])
    assert acct.check_entry("MNQ", 99, 0, time(9, 30)) == "account_no_trade_window"
    assert acct.check_entry("MNQ", 99, 0, time(9, 35)) == "account_max_position"
    assert acct.check_entry("MNQ", 1, 0, time(9, 35)) is None


def test_flat_by_flattens_once_per_day_and_blocks_until_the_rotation() -> None:
    acct = opened()
    assert acct.flat_by(TUE) == [FlattenMembers("account_flat_by")]
    assert acct.flat_by(TUE) == []
    assert acct.entry_blocks() == ("account_flat_by",)
    end_day(acct, WED)
    assert acct.entry_blocks() == ()
    assert opened(flat_deadline_enabled=False).flat_by(TUE) == []


def test_restore_failed_starts_breached_and_blocks() -> None:
    acct = PaperAccount.restore_failed("a", rules(), ("p1",), NOW)
    s = acct.state
    assert (s.status, s.breach_reason, s.breached_at) == ("breached", "restore_failed", NOW)
    assert acct.entry_blocks() == ("account_breached",)


# ---------------------------------------------------------------- view and codec


def test_view_reports_distances_to_every_limit() -> None:
    acct = opened()
    close(acct, "-200")
    bar(acct, "-150", best="100", at_close="-50")
    v = acct.view(exposure_micros=12, flat_by_ns=123)
    assert v["equity"] == "49750.00"
    assert v["drawdown"] == "250.00"  # hwm stays at the start (49800 + 100 is below it)
    assert v["distance"] == {
        "mll": "1750.00",
        "dll": "750.00",  # 1000 - 200 - 50
        "profit_target": "3200.00",
        "position": 38,
        "flat_by_ns": 123,
    }
    assert v["blocks"] == []
    assert v["exposure_micros"] == 12
    assert v["disabled_rules"] == ["no_trade_windows", "min_trading_days"]
    none = PaperAccount.fresh("n", rules(maximum_loss_limit={"enabled": False}), ())
    distance = none.view()["distance"]
    assert isinstance(distance, dict)
    assert distance["mll"] is None


def test_state_round_trips_through_the_codec_with_tagged_money() -> None:
    acct = opened()
    close(acct, "12.50")
    bar(acct, "-3.25", best="4.00", at_close="1.75", close_t=4000)
    acct.flat_by(TUE)
    state = acct.state
    encoded = ACCOUNT_CODEC.encode(state)
    text = json.dumps(encoded)
    back = ACCOUNT_CODEC.decode(json.loads(text))
    assert back == state
    assert isinstance(back, PaperAccountState)
    assert encoded["balance"] == {"$decimal": "50012.50"}  # type: ignore[index,call-overload]
    assert encoded["status"] == "active"  # type: ignore[index,call-overload]
    assert encoded["day"] == {"$date": "2026-10-13"}  # type: ignore[index,call-overload]
