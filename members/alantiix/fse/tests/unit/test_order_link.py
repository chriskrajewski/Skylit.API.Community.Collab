"""``OrderLink`` and ``BrokeredPaperBroker`` (design D4, round-4 A2, T23d, T26 unit part).

A real :class:`~fse.live.broker_safety.BrokerSafety` runs against
:class:`tests.fakes.broker.FakeAdapter` (OCO legs on); no host, no network.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from fse.config.schema.fills import CostsConfig, FillsConfig, InstrumentCosts
from fse.live.bar_feed import FeedInstrument
from fse.live.broker_safety import _LOOKUP_MARGIN_NS, ClientIds, OrderJournal, _Pending
from fse.live.host.broker import RiskCaps, client_id
from fse.live.host.dayclock import globex_day
from fse.live.host.live_orders import (
    BrokeredPaperBroker,
    BrokerEvent,
    ModeDecision,
    OrderLink,
    PluginOrderSetup,
    make_safety_factory,
)
from fse.live.order_router import ExitAction, ModifyAction, PlaceAction
from fse.live.plugins import EnterMarket, Flatten, ModifyStop, OrderUpdate, TargetLeg
from fse.live.state_store import BlockStore
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter, EntryIntent, OrderRequest
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.broker import FakeAdapter
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import make_bar, ny

WED = date(2026, 10, 14)
DAY = globex_day(WED)
NEXT = globex_day(date(2026, 10, 15))
T0 = ny(WED, 10, 0)
M = NS_PER_MINUTE
S = NS_PER_SECOND
MNQ = "CON.F.US.MNQ.Z26"
FILLS = FillsConfig(
    trade_through_ticks=1,
    slippage_ticks=1,
    costs=CostsConfig(MNQ=InstrumentCosts(commission=Decimal("0.62"), exchange_fee=Decimal("0"))),
)


async def _forever() -> None:
    await asyncio.Event().wait()


class _Plane:
    def contracts(self, day: date) -> tuple[tuple[FeedInstrument, ...], list[str]]:
        return (FeedInstrument("MNQ", "MNQZ6", MNQ),), []


@dataclass
class H:
    clock: FakeClock
    fake: FakeAdapter
    broker: BrokeredPaperBroker
    link: OrderLink
    events: list[BrokerEvent] = field(default_factory=list)
    logs: list[Any] = field(default_factory=list)

    def route(self, intent: Any, at: int) -> list[OrderUpdate]:
        return self.broker.route(intent, at)

    async def flush_step(self, at: int) -> None:
        self.clock.advance_to(max(at, self.clock.now()))
        self.link.flush(at)
        assert self.link.safety is not None
        await self.clock.run(self.link.safety.step())

    def places(self) -> list[Any]:
        return [r for n, r in self.fake.calls if n == "place"]

    def brackets(self) -> list[Any]:
        return [r for n, r in self.fake.calls if n == "place_bracketed"]

    def kinds(self, kind: str) -> list[Any]:
        return [e for e in self.logs if e.get("kind") == kind]


@pytest_asyncio.fixture
async def h(tmp_path: Path) -> AsyncIterator[H]:
    clock = FakeClock(T0)
    fake = FakeAdapter(clock, oco_legs=True, contracts={"MNQ": MNQ})
    writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
    journal = OrderJournal(writer, tmp_path)
    setup = PluginOrderSetup(
        "mx",
        ModeDecision("practice", "practice"),
        adapter=cast(BrokerAdapter, fake),
        journal=journal,
        ids=ClientIds(writer, tmp_path, journal, "0a1b2c3d"),
        blocks=BlockStore(writer, tmp_path),
    )
    logs: list[Any] = []
    broker = BrokeredPaperBroker(
        "mx",
        fills=FILLS,
        stop_fill="at_stop",
        caps=RiskCaps(10, 3, 5, Decimal("500")),
        declared=frozenset({"MNQ"}),
        rth_for=lambda bar: (bar.open_ns, bar.close_ns),
        log=logs.append,
    )
    broker.start_day(WED)
    events: list[BrokerEvent] = []
    factory = make_safety_factory(setup, _Plane(), clock, lambda _i: None, frozenset({"MNQ"}))
    link = OrderLink(
        setup,
        broker,
        instruments=frozenset({"MNQ"}),
        factory=factory,
        post=events.append,
        log=logs.append,
        clock=clock,
    )
    broker.link = link
    safety = link.open_day(DAY, T0)
    assert safety is not None
    link.task = asyncio.create_task(_forever())
    await clock.run(safety.start())
    yield H(clock, fake, broker, link, events, logs)
    link.task.cancel()


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
    return EnterMarket(**(args | kw))


async def opened(h: H) -> None:
    """t1 long 2: sent, filled at the broker, then filled in paper on the T0 bar."""
    (acc,) = h.route(market(), T0 + 5 * S)
    assert acc.status == "accepted"
    await h.flush_step(T0 + 5 * S)
    ups = h.broker.on_bar(make_bar("MNQ", T0, 80_000, 80_004, 79_996, 80_002))
    assert [u.status for u in ups] == ["filled_entry"]
    await h.flush_step(T0 + M + S)


def test_unpriced_legs_are_market_placeholders() -> None:
    from fse.engine.types import Order, SetupKey
    from fse.live.host.live_orders import _unpriced

    key = SetupKey("MNQ", "mx:t1", 0.0, "long", WED, 1)
    leg = Order("mx:t1:stop", key, "MNQ", "sell", "stop", 2, 79_990, T0, "stop")
    view = _unpriced(leg)
    assert (view.kind, view.price, view.role, view.qty) == ("market", None, "stop", 2)


@pytest.mark.asyncio
async def test_a_market_entry_becomes_a_tick_bracket_with_synthetic_legs(h: H) -> None:
    h.route(market(), T0 + 5 * S)
    (action,) = h.link._actions
    assert isinstance(action, PlaceAction)
    assert (action.stop_ticks, action.target_ticks) == (8, 12)
    assert action.stop.price is None
    assert [t.client_id for t in action.targets] == ["mx:t1:tp1"]
    assert "t1" in h.link.market_tags
    await h.flush_step(T0 + 5 * S)
    (intent,) = h.brackets()
    assert intent.kind == "market"
    assert intent.bracket_ticks == (8, 12)
    assert h.link.safety is not None
    assert h.link.safety._last_diffs == ()


@pytest.mark.asyncio
async def test_flush_submits_even_with_nothing_routed(h: H) -> None:
    calls: list[tuple[int, list[Any]]] = []
    safety = h.link.safety
    assert safety is not None
    original = safety.submit

    def record(t: int, actions: Any, expectation: Any) -> None:
        calls.append((t, list(actions)))
        original(t, actions, expectation)

    safety.submit = record  # type: ignore[method-assign]
    h.link.flush(T0 + S)
    assert calls == [(T0 + S, [])]


@pytest.mark.asyncio
async def test_withdraw_cancels_at_placement_time_so_the_fill_bar_never_fills(h: H) -> None:
    h.route(market(), T0 + 5 * S)
    ups = h.broker.withdraw("t1", T0 + 10 * S, "broker_withheld")
    assert [(u.status, u.reason) for u in ups] == [("cancelled", "broker_withheld")]
    assert "t1" not in h.link.market_tags
    assert h.broker.on_bar(make_bar("MNQ", T0, 80_000, 80_004, 79_996, 80_002)) == []
    assert not h.broker.book.trades
    assert not h.broker.book.market_brackets


@pytest.mark.asyncio
async def test_a_session_close_flatten_holds_a_closing_record_until_the_exit_fills(h: H) -> None:
    await opened(h)
    ups = h.route(Flatten("t1", "session_close", "last_close"), T0 + 2 * M)
    assert "closed" in [u.status for u in ups]
    assert not h.broker.book.trades
    assert "t1" in h.link._closing
    (action,) = h.link._actions
    assert isinstance(action, ExitAction)
    assert action.qty == 2
    assert action.legs == ("mx:t1:stop", "mx:t1:tp1")
    assert action.entry_id == "mx:t1:entry"
    await h.flush_step(T0 + 2 * M)
    assert [r.qty for r in h.places()] == [2]
    assert len([c for c in h.fake.calls if c[0] == "cancel"]) == 2
    assert h.fake.positions["MNQ"] == 0
    assert h.link.safety is not None
    assert h.link.safety._last_diffs == ()
    h.link.flush(T0 + 2 * M + S)
    assert "t1" not in h.link._closing
    assert not h.link.exits_open


@pytest.mark.parametrize(
    ("entry", "dropped"),
    [
        ({"event": "broker_skip", "reason": "exit: the broker holds nothing to close"}, True),
        ({"event": "broker_skip", "reason": "no checked contract"}, True),
        ({"event": "order_sent", "outcome": "rejected"}, True),
        ({"event": "order_sent", "outcome": "no_response"}, False),
        ({"event": "resubmit_dropped"}, True),
        ({"event": "broker_exit_deferred"}, False),
    ],
)
@pytest.mark.asyncio
async def test_closing_drop_rules(h: H, entry: dict[str, Any], dropped: bool) -> None:
    await opened(h)
    h.route(Flatten("t1", "session_close", "last_close"), T0 + 2 * M)
    h.link.safety_log({**entry, "engine_id": "mx:t1:exit"})
    assert ("t1" not in h.link._closing) is dropped
    notes = [e.text for e in h.events if e.kind == "note"]
    if entry.get("reason") == "no checked contract":
        assert notes == ["exit for MNQ not sent: no contract id"]
    else:
        assert notes == []


@pytest.mark.asyncio
async def test_an_account_flatten_then_a_session_close_send_one_exit(h: H) -> None:
    await opened(h)
    h.route(Flatten(None, "account_flat", "next_open"), T0 + 2 * M)
    h.route(Flatten("t1", "session_close", "last_close"), T0 + 2 * M)
    exits = [a for a in h.link._actions if isinstance(a, ExitAction)]
    assert [a.order.client_id for a in exits] == ["mx:t1:exit"]
    assert len(h.kinds("broker_exit_dup_skipped")) == 1
    assert "t1" in h.link._closing
    await h.flush_step(T0 + 2 * M)
    assert [r.qty for r in h.places()] == [2]
    assert h.fake.positions["MNQ"] == 0


@pytest.mark.asyncio
async def test_a_flatten_in_the_same_dispatch_retracts_the_entry(h: H) -> None:
    h.route(market(), T0 + 5 * S)
    ups = h.route(Flatten("t1", "session_close", "last_close"), T0 + 5 * S)
    assert ("t1", "cancelled") in [(u.tag, u.status) for u in ups]
    assert h.link._actions == []
    assert not h.link._closing
    await h.flush_step(T0 + 5 * S)
    assert not h.brackets()
    assert not h.places()


@pytest.mark.asyncio
async def test_a_flatten_after_a_flushed_entry_withdraws_it_and_closes_the_broker(h: H) -> None:
    h.route(market(), T0 + 5 * S)
    await h.flush_step(T0 + 5 * S)
    h.route(Flatten(None, "account_flat", "next_open"), T0 + 15 * S)
    assert "t1" in h.link._closing
    assert h.link._closing["t1"].entry is not None
    await h.flush_step(T0 + 15 * S)
    assert [r.qty for r in h.places()] == [2]
    assert h.broker.on_bar(make_bar("MNQ", T0, 80_000, 80_004, 79_996, 80_002)) == []
    assert not h.broker.book.trades
    assert h.fake.positions["MNQ"] == 0
    assert h.link.safety is not None
    assert h.link.safety._last_diffs == ()


@pytest.mark.asyncio
async def test_broker_only_remainders_get_distinct_ids(h: H) -> None:
    h.fake.positions["MNQ"] = 2
    assert h.link.safety is not None
    await h.clock.run(h.link.safety.sync())
    h.route(Flatten(None, "restart_stale", "last_close"), T0 + S)
    h.route(Flatten(None, "restart_stale", "last_close"), T0 + S)
    ids = [a.order.client_id for a in h.link._actions if isinstance(a, ExitAction)]
    assert ids == ["mx:*0:exit", "mx:*1:exit"]
    assert all(
        a.order.setup_key is None for a in h.link._actions if isinstance(a, ExitAction)
    )
    await h.flush_step(T0 + S)
    assert [r.qty for r in h.places()] == [2]  # the second is sized 0 on its fresh read
    assert h.fake.positions["MNQ"] == 0


@pytest.mark.asyncio
async def test_rejections_before_paper(h: H) -> None:
    def reason(intent: Any) -> str | None:
        (u,) = h.route(intent, T0 + 5 * S)
        return u.reason

    assert reason(market("a", targets=())) == "broker_one_target"
    assert reason(market("b", targets=(TargetLeg("tp1", 1, 12),))) == "broker_one_target"
    assert reason(market("c", reference_t=80_000)) == "broker_reference_t"
    h.route(market("d"), T0 + 5 * S)
    from fse.live.plugins import Cancel

    assert reason(Cancel("d")) == "broker_entry_sent"
    h.link.catching_up = True
    assert reason(market("e")) == "broker_catch_up"
    h.link.catching_up = False
    h.link.stopping = True
    assert reason(market("f")) == "broker_stopping"


@pytest.mark.asyncio
async def test_a_bad_reason_flatten_records_nothing(h: H) -> None:
    h.route(market(), T0 + 5 * S)
    await h.flush_step(T0 + 5 * S)
    (u,) = h.route(Flatten("t1", "Bad Reason!", "last_close"), T0 + 6 * S)
    assert (u.status, u.reason) == ("rejected", "bad_reason")
    assert "t1" in h.link.market_tags
    assert not h.link._closing
    assert h.link._actions == []


@pytest.mark.asyncio
async def test_a_modify_stop_sends_a_modify_for_the_adopted_stop_leg(h: H) -> None:
    await opened(h)
    (u,) = h.route(ModifyStop("t1", 80_001), T0 + 2 * M)
    assert u.status == "accepted"
    (action,) = h.link._actions
    assert isinstance(action, ModifyAction)
    assert (action.client_id, action.kind, action.price) == ("mx:t1:stop", "stop", 80_001)
    await h.flush_step(T0 + 2 * M)
    stop_oid = h.link.journal.order_for_engine("mx:t1:stop")
    assert stop_oid is not None
    assert [c[1] for c in h.fake.calls if c[0] == "modify"] == [
        (stop_oid, {"stop_price": 80_001})
    ]


@pytest.mark.asyncio
async def test_a_no_price_flatten_records_nothing_and_keeps_both_open(h: H) -> None:
    await opened(h)
    ups = h.route(Flatten("t1", "session_close", "last_close", stamp_ns=T0), T0 + 2 * M)
    assert [(u.status, u.reason) for u in ups] == [("rejected", "no_price")]
    assert not h.link._closing
    assert h.link._actions == []
    (skipped,) = h.kinds("broker_flatten_skipped")
    assert (skipped["tag"], skipped["reason"]) == ("t1", "no_price")
    assert h.broker.book.trades
    await h.flush_step(T0 + 2 * M)
    assert not h.places()
    assert h.fake.positions["MNQ"] == 2
    assert h.link.safety is not None
    assert h.link.safety._last_diffs == ()


# ---------------------------------------------------------------- T15 day rollover carry


@pytest.mark.asyncio
async def test_a_no_response_exit_at_rollover_is_found_by_the_next_days_first_sync(h: H) -> None:
    await opened(h)
    late = DAY.rotate_ns - S  # 17:59:59
    h.route(Flatten("t1", "session_close", "last_close"), late)
    h.fake.place_outcomes.append("lost")  # the exit fills; no answer arrives
    h.fake.lookup_modes.append("fail")  # and the same-day lookup fails
    await h.flush_step(late)
    old = h.link.safety
    assert old is not None
    (p,) = old.pending.values()
    assert p.sent_at == late
    h.clock.advance_to(NEXT.open_ns)
    before = len(h.logs)
    new = h.link.open_day(NEXT, h.clock.now())
    assert new is not None
    assert new is not old
    assert new._session_start == late - _LOOKUP_MARGIN_NS
    assert p.client_id in new.pending
    (roll,) = h.kinds("broker_rollover")
    assert roll["carried_pending"] == 1
    await h.clock.run(new.start())
    assert [r.qty for r in h.places()] == [2]  # found by the lookup: not sent again
    assert h.fake.positions["MNQ"] == 0
    assert not new.pending
    assert new._last_diffs == ()
    assert not [e for e in h.logs[before:] if e.get("event") == "entry_filled"]
    h.link.flush(h.clock.now())
    assert "t1" not in h.link._closing


@pytest.mark.asyncio
async def test_rollover_withdraws_an_unanswered_entry_and_drops_an_unanswered_tp2(h: H) -> None:
    h.route(market(), T0 + 5 * S)
    h.fake.place_outcomes.append("dropped")
    h.fake.lookup_modes.append("fail")
    await h.flush_step(T0 + 5 * S)
    old = h.link.safety
    assert old is not None
    (entry_p,) = old.pending.values()
    assert isinstance(entry_p.request, EntryIntent)
    tp2 = OrderRequest("fse-tp2", MNQ, "sell", 1, "limit", 80_020)
    old.pending["fse-tp2"] = _Pending("fse-tp2", "mx:t9:tp2", "MNQ", tp2, T0)
    h.clock.advance_to(NEXT.open_ns)
    new = h.link.open_day(NEXT, h.clock.now())
    assert new is not None
    assert not new.pending
    assert new._session_start == NEXT.open_ns  # nothing carried
    reasons = {e["engine_id"]: e["reason"] for e in h.kinds("broker_skip")}
    assert reasons == {
        "mx:t1:entry": "entry unanswered at rollover",
        "mx:t9:tp2": "tp2 unanswered at rollover",
    }
    assert [e.tag for e in h.events if e.kind == "withdraw"] == ["t1"]
    (roll,) = h.kinds("broker_rollover")
    assert (roll["withdrawn"], roll["dropped"], roll["carried_pending"]) == (1, 1, 0)


@pytest.mark.asyncio
async def test_no_session_withdraws_entries_and_carries_exits(h: H) -> None:
    await opened(h)
    assert h.link.task is not None
    h.link.task.cancel()
    await asyncio.sleep(0)
    h.route(market("t2", placed=T0 + 2 * M), T0 + 2 * M + S)
    h.route(Flatten("t1", "session_close", "last_close"), T0 + 2 * M + S)
    h.link.flush(T0 + 2 * M + S)
    assert [(e.kind, e.tag, e.text) for e in h.events] == [
        ("withdraw", "t2", "broker_no_session")
    ]
    assert [type(a).__name__ for a in h.link._carry] == ["ExitAction"]
    assert h.link.entry_blocks() == ("broker_not_synced",)
    # the carried exit is hidden from the expectation until it is released
    assert client_id("mx", "t1", "exit") not in {o.client_id for o in h.link.working_orders()}
