"""``PluginPaperBroker`` (design 7.2-7.4): placement, caps, fills, flatten, freeze, codec."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from fse.config.schema.fills import CostsConfig, FillsConfig, InstrumentCosts
from fse.engine.types import Bar
from fse.live.host.broker import (
    BROKER_CODEC,
    PluginBrokerState,
    PluginPaperBroker,
    RiskCaps,
    client_id,
)
from fse.live.plugins import (
    Cancel,
    EnterMarket,
    EnterResting,
    Flatten,
    OrderUpdate,
    Quote,
    TargetLeg,
)
from fse.logio.canonical_json import dumps
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.dataplane import make_bar, ny

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)
M = NS_PER_MINUTE
FILLS = FillsConfig(
    trade_through_ticks=1,
    slippage_ticks=1,
    costs=CostsConfig(MNQ=InstrumentCosts(commission=Decimal("0.62"), exchange_fee=Decimal("0"))),
)


def broker(caps: RiskCaps | None = None, **kw: Any) -> tuple[PluginPaperBroker, list[Any]]:
    logs: list[Any] = []
    b = PluginPaperBroker(
        "mx",
        fills=FILLS,
        stop_fill=kw.pop("stop_fill", "at_stop"),
        caps=caps or RiskCaps(max_contracts=10, max_open_trades=3),
        declared=frozenset({"MNQ"}),
        rth_for=lambda bar: (bar.open_ns, bar.close_ns),
        log=logs.append,
    )
    b.start_day(WED)
    return b, logs


def bar(
    minute: int, o: int, h: int | None = None, low: int | None = None, c: int | None = None
) -> Bar:
    return make_bar("MNQ", T0 + minute * M, o, h, low, c)


def market(tag: str = "t1", placed: int = T0, **kw: Any) -> EnterMarket:
    args: dict[str, Any] = {
        "tag": tag,
        "instrument": "MNQ",
        "direction": "long",
        "qty": 2,
        "stop_ticks": 8,
        "targets": (TargetLeg("tp1", 2, 12),),
        "placed_at": placed,
        "exact_bar_open": True,
    }
    args.update(kw)
    return EnterMarket(**args)


def statuses(updates: list[OrderUpdate]) -> list[tuple[str, str, str | None]]:
    return [(u.tag, u.status, u.reason) for u in updates]


def test_market_bracket_fills_at_open_plus_slip_with_legs_from_the_open() -> None:
    b, _ = broker()
    (acc,) = b.route(market(), T0 + 5 * NS_PER_SECOND)
    assert (acc.status, acc.detail) == ("accepted", {"submit_quote_t": None})
    ups = b.on_bar(bar(0, 80_000, 80_004, 79_996, 80_002))
    assert statuses(ups) == [("t1", "filled_entry", None)]
    assert ups[0].fill is not None
    assert ups[0].fill.price == 80_001
    assert ups[0].fill.client_id == "mx:t1:entry"
    orders = {o.client_id: o.price for o in b.book.working_orders()}
    assert orders == {"mx:t1:stop": 80_000 - 8, "mx:t1:tp1": 80_000 + 12}
    # target trades through on a later bar
    ups = b.on_bar(bar(1, 80_002, 80_014, 80_000, 80_010))
    assert statuses(ups) == [("t1", "filled_exit", None), ("t1", "closed", None)]
    assert ups[0].role == "tp1"
    assert ups[0].fill is not None
    assert ups[0].fill.price == 80_012
    closed = ups[1].trade
    assert closed is not None
    assert closed.setup_key.pattern == "mx:t1"
    # (80_012 - 80_001) ticks * $0.50 * 2 - fees 4 * 0.62
    assert closed.net == Decimal("11") - Decimal("2.48")
    assert b.state.day_net == closed.net
    assert b.state.day_trades == 1


def test_client_id_splits_into_three_parts() -> None:
    assert client_id("mx", "20261012T0945", "tp1").split(":") == ["mx", "20261012T0945", "tp1"]


def test_exact_bar_open_expires_when_the_fill_bar_is_missing() -> None:
    b, _ = broker()
    b.route(market(), T0)
    ups = b.on_bar(bar(1, 80_000))  # the 10:00 bar never came
    assert statuses(ups) == [("t1", "expired", "fill_bar_missing")]
    assert b.book.orders == {}
    assert b.book.market_brackets == {}


@pytest.mark.parametrize(
    ("placed", "now", "reason"),
    [
        (T0, T0 + M + 1, "stale_placement"),  # older than 60 s
        (T0 + 1, T0, "stale_placement"),  # in the future
    ],
)
def test_placement_window(placed: int, now: int, reason: str) -> None:
    b, _ = broker()
    assert statuses(b.route(market(placed=placed), now)) == [("t1", "rejected", reason)]


def test_placement_after_a_processed_bar_is_stale() -> None:
    b, _ = broker()
    b.on_bar(bar(0, 80_000))
    assert statuses(b.route(market(placed=T0), T0 + 30 * NS_PER_SECOND)) == [
        ("t1", "rejected", "stale_placement")
    ]


def test_bad_freeze_boundary_accepts_a_fill_bar_ending_at_the_close() -> None:
    b, _ = broker()
    assert statuses(b.route(market("a", freeze_from_ns=T0 + M - 1), T0)) == [
        ("a", "rejected", "bad_freeze")
    ]
    assert statuses(b.route(market("b", freeze_from_ns=T0 + M), T0)) == [("b", "accepted", None)]
    ups = b.on_bar(bar(0, 80_000))
    assert statuses(ups) == [("b", "filled_entry", None)]


def test_tags_bad_duplicate_and_reused_next_day() -> None:
    b, _ = broker()
    assert statuses(b.route(market("bad:tag"), T0)) == [("bad:tag", "rejected", "bad_tag")]
    assert statuses(b.route(market("t1"), T0)) == [("t1", "accepted", None)]
    assert statuses(b.route(market("t1"), T0)) == [("t1", "rejected", "duplicate_tag")]
    b.on_bar(bar(1, 80_000))  # expires t1: the tag stays used today
    assert statuses(b.route(market("t1", placed=T0 + 2 * M), T0 + 2 * M)) == [
        ("t1", "rejected", "duplicate_tag")
    ]
    b.start_day(date(2026, 10, 15))
    assert statuses(b.route(market("t1", placed=T0 + 2 * M), T0 + 2 * M)) == [
        ("t1", "accepted", None)
    ]


def test_instrument_not_declared_and_blocks() -> None:
    b, _ = broker()
    assert statuses(b.route(market(instrument="MES"), T0)) == [
        ("t1", "rejected", "instrument_not_declared")
    ]
    assert statuses(b.route(market(), T0, blocks=("halt_file",))) == [
        ("t1", "rejected", "halt_file")
    ]


def test_caps() -> None:
    b, _ = broker(RiskCaps(max_contracts=3, max_open_trades=2, max_trades_per_day=2))
    assert statuses(b.route(market("a", qty=4, targets=()), T0)) == [
        ("a", "rejected", "max_contracts")
    ]
    assert statuses(b.route(market("b", qty=2, targets=()), T0)) == [("b", "accepted", None)]
    assert statuses(b.route(market("c", qty=2, targets=()), T0)) == [
        ("c", "rejected", "max_contracts")
    ]
    assert statuses(b.route(market("d", qty=1, targets=()), T0)) == [("d", "accepted", None)]
    assert statuses(b.route(market("e", qty=1, targets=()), T0)) == [
        ("e", "rejected", "max_trades_per_day")
    ]
    b2, _ = broker(RiskCaps(max_contracts=10, max_open_trades=1))
    b2.route(market("a"), T0)
    assert statuses(b2.route(market("b"), T0)) == [("b", "rejected", "max_open_trades")]


def test_max_daily_loss_blocks_until_the_next_day() -> None:
    # Cap must clear the entry's stop risk (8 ticks * $0.50 * 2 = $8) so the trade
    # can arm; the day halt still fires after the stop takes ~$10.
    b, _ = broker(RiskCaps(max_contracts=10, max_open_trades=3, max_daily_loss=Decimal("9")))
    assert statuses(b.route(market("a"), T0)) == [("a", "accepted", None)]
    b.on_bar(bar(0, 80_000))
    b.on_bar(bar(1, 79_995, 79_996, 79_980, 79_990))  # stop at 79_992 - 1 slip
    assert b.state.day_net <= Decimal("-9")
    assert b.cap_blocks() == ("max_daily_loss",)
    assert statuses(b.route(market("b", placed=T0 + 2 * M), T0 + 2 * M)) == [
        ("b", "rejected", "max_daily_loss")
    ]
    b.start_day(date(2026, 10, 15))
    assert b.cap_blocks() == ()


def test_reset_day_net_clears_max_daily_loss_block_without_start_day() -> None:
    b, _ = broker(RiskCaps(max_contracts=10, max_open_trades=3, max_daily_loss=Decimal("9")))
    assert statuses(b.route(market("a"), T0)) == [("a", "accepted", None)]
    b.on_bar(bar(0, 80_000))
    b.on_bar(bar(1, 79_995, 79_996, 79_980, 79_990))
    assert b.state.day_net <= Decimal("-9")
    assert b.cap_blocks() == ("max_daily_loss",)
    day_before, trades_before = b.state.day, b.state.day_trades
    assert b.reset_day_net() is True
    assert b.state.day_net == Decimal(0)
    assert b.state.day == day_before
    assert b.state.day_trades == trades_before
    assert b.cap_blocks() == ()
    # Full stop-risk budget again (8 ticks * $0.50 * 2 = $8 under a $9 cap).
    assert statuses(b.route(market("b", placed=T0 + 2 * M), T0 + 2 * M)) == [
        ("b", "accepted", None)
    ]
    assert b.reset_day_net() is False


def test_max_daily_loss_rejects_enter_when_stop_risk_exceeds_budget() -> None:
    # MNQ $0.50/tick: 438 ticks * 5 = $1,095 stop risk under an $800 daily cap.
    b, _ = broker(RiskCaps(max_contracts=16, max_open_trades=1, max_daily_loss=Decimal("800")))
    assert statuses(
        b.route(market("wide", qty=5, stop_ticks=438, targets=(TargetLeg("tp1", 5, 110),)), T0)
    ) == [("wide", "rejected", "max_daily_loss")]
    # Sized to remaining budget: floor(800 / 219) = 3 → accepted.
    assert statuses(
        b.route(market("ok", qty=3, stop_ticks=438, targets=(TargetLeg("tp1", 3, 110),)), T0)
    ) == [("ok", "accepted", None)]


def test_max_daily_loss_rejects_even_one_contract_over_budget() -> None:
    b, _ = broker(RiskCaps(max_contracts=16, max_open_trades=1, max_daily_loss=Decimal("200")))
    assert statuses(
        b.route(market("one", qty=1, stop_ticks=438, targets=(TargetLeg("tp1", 1, 110),)), T0)
    ) == [("one", "rejected", "max_daily_loss")]


def test_at_stop_fills_at_the_stop_on_a_gap() -> None:
    b, _ = broker(stop_fill="at_stop")
    b.route(market(), T0)
    b.on_bar(bar(0, 80_000))
    ups = b.on_bar(bar(1, 79_900, 79_910, 79_890, 79_900))  # gaps through the stop
    assert ups[0].role == "stop"
    assert ups[0].fill is not None
    assert ups[0].fill.price == 80_000 - 8 - 1
    b2, _ = broker(stop_fill="gap_open")
    b2.route(market(), T0)
    b2.on_bar(bar(0, 80_000))
    ups = b2.on_bar(bar(1, 79_900, 79_910, 79_890, 79_900))
    assert ups[0].fill is not None
    assert ups[0].fill.price == 79_900 - 1


def test_flatten_last_close_uses_the_ring_bar_at_the_stamp() -> None:
    b, _ = broker()
    b.route(market(targets=()), T0)
    b.on_bar(bar(0, 80_000))
    b.on_bar(bar(1, 80_004, 80_006, 80_002, 80_005))
    b.on_bar(bar(2, 80_006, 80_008, 80_004, 80_007))
    ups = b.flatten(Flatten("t1", "session_close", "last_close", stamp_ns=T0 + 2 * M), T0 + 3 * M)
    assert statuses(ups) == [("t1", "filled_exit", None), ("t1", "closed", None)]
    fill = ups[0].fill
    assert fill is not None
    assert fill.price == 80_005
    assert fill.bar_open_ns == T0 + M
    assert fill.client_id == "session_close:mx:t1:entry"
    assert ups[0].bar is None
    assert b.book.orders == {}
    assert b.book.trades == {}


def test_flatten_without_a_bar_is_no_price_and_next_open_exits_on_the_next_bar() -> None:
    b, logs = broker()
    b.route(market(), T0)
    b.on_bar(bar(0, 80_000))
    ups = b.flatten(Flatten("t1", "x", "last_close", stamp_ns=T0 - M), T0 + M)
    assert statuses(ups) == [("t1", "rejected", "no_price")]
    assert logs[-1]["kind"] == "flatten_no_price"
    assert logs[-1]["level"] == "error"
    assert b.flatten(Flatten(None, "eod", "next_open"), T0 + M) == []
    ups = b.on_bar(bar(1, 80_003))
    assert statuses(ups) == [("t1", "filled_exit", None), ("t1", "closed", None)]
    assert ups[0].role == "exit"
    assert ups[0].fill is not None
    assert ups[0].fill.price == 80_002


def test_flatten_cancels_a_pending_entry() -> None:
    b, _ = broker()
    b.route(market(), T0)
    ups = b.flatten(Flatten(None, "host_break_backstop", "last_close"), T0 + 10)
    assert statuses(ups) == [("t1", "cancelled", "host_break_backstop")]
    assert b.book.market_brackets == {}
    assert b.book.orders == {}
    assert statuses(b.flatten(Flatten(None, "Bad Reason", "last_close"), T0)) == [
        ("*", "rejected", "bad_reason")
    ]


def test_cancel_and_cancel_entries() -> None:
    b, _ = broker()
    resting = EnterResting("r1", "MNQ", "long", 1, "limit", 79_990, 79_980, (("tp1", 1, 80_010),))
    assert statuses(b.route(resting, T0)) == [("r1", "accepted", None)]
    assert statuses(b.route(Cancel("r1"), T0)) == [("r1", "cancelled", None)]  # at placement
    b.route(resting.__class__("r2", "MNQ", "long", 1, "limit", 79_990, 79_980, ()), T0)
    assert b.working_tags() == ("r2",)
    assert b.cancel_entries(T0 + M, "halt_file") == []  # takes effect from bars at T0 + M
    assert b.working_tags() == ()
    ups = b.on_bar(bar(1, 80_000))
    assert statuses(ups) == [("r2", "cancelled", "halt_file")]


def test_post_close_bar_does_not_fill_a_frozen_setup() -> None:
    b, logs = broker()
    b.route(market(freeze_from_ns=T0 + M, targets=()), T0)
    b.on_bar(bar(0, 80_000))
    ups = b.on_bar(bar(1, 79_000, 79_001, 78_000, 79_000))  # far through the stop
    assert ups == []
    assert b.open_tags() == ("t1",)
    b.on_bar(bar(2, 79_000, 79_001, 78_000, 79_000))
    assert [e["kind"] for e in logs] == ["post_close_bar"]  # once per tag
    ups = b.flatten(Flatten("t1", "session_close", "last_close", stamp_ns=T0 + M), T0 + 3 * M)
    assert ups[0].fill is not None
    assert ups[0].fill.price == 80_000


def test_submit_quote_detail_and_fill_distance() -> None:
    b, _ = broker()
    quote = Quote("MNQ", "CON.MNQ", T0 - NS_PER_SECOND, T0 - NS_PER_SECOND, 79_998, 80_002, 80_000)
    (acc,) = b.route(market(), T0 + 3 * NS_PER_SECOND, quote=quote)
    assert acc.detail == {"submit_quote_t": 80_002, "quote_age_ms": 4000}
    (fill,) = b.on_bar(bar(0, 80_000))
    assert fill.detail == {"submit_quote_t": 80_002, "fill_minus_quote_t": -1}
    stale = Quote("MNQ", "CON.MNQ", T0 - 9 * NS_PER_SECOND, T0 - 9 * NS_PER_SECOND, 1, 2, 1)
    (acc,) = b.route(market("t2", placed=T0 + M), T0 + M, quote=stale)
    assert acc.detail == {"submit_quote_t": None}


def test_state_codec_round_trip() -> None:
    b, _ = broker()
    b.route(market(freeze_from_ns=T0 + 5 * M), T0)
    b.on_bar(bar(0, 80_000))
    encoded = BROKER_CODEC.encode(b.state)
    text = dumps(encoded)
    decoded = BROKER_CODEC.decode(json.loads(text))
    assert decoded == b.state
    assert isinstance(decoded, PluginBrokerState)
    assert decoded.used_tags == frozenset({"t1"})
    assert decoded.freeze == {"t1": T0 + 5 * M}
    # an older document without the newer fields decodes with defaults
    old = {"$type": "broker.PluginBrokerState", "book": encoded["book"]}  # type: ignore[index,call-overload]
    restored = BROKER_CODEC.decode(old)
    assert isinstance(restored, PluginBrokerState)
    assert restored.used_tags == frozenset()
