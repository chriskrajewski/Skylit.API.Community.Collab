"""Opt-in broker orders in the plugin host (design D4/D6, T10-T30 host cases, round-4 A1-A3).

The broker plugin runs against :class:`tests.fakes.broker.FakeAdapter`
(OCO legs on) through a real per-day BrokerSafety; the other plugins stay on
the paper book. No network.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from fse.engine.types import Bar
from fse.live.bar_feed import FeedInstrument
from fse.live.broker_safety import BrokerSafety, ClientIds, OrderJournal
from fse.live.host.broker import BROKER_CODEC, PluginPaperBroker
from fse.live.host.dayclock import GlobexDay
from fse.live.host.gate import plugin_dir
from fse.live.host.host import HOST_LOG_FILE_NAME, BarsEvent, HostResources, PluginHost
from fse.live.host.live_orders import (
    BrokeredPaperBroker,
    ModeDecision,
    PluginOrderSetup,
    make_safety_factory,
)
from fse.live.host.nexus import NexusMirror
from fse.live.plugins import (
    BarNeed,
    DataNeeds,
    EnterMarket,
    EnterResting,
    Flatten,
    Intent,
    TargetLeg,
)
from fse.live.plugins.loader import PluginLoadError
from fse.live.state_store import BlockStore
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter, OrderRequest
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant
from tests.fakes.broker import FakeAdapter
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin
from tests.unit.test_nexus_mirror import FakeNexus, Hanging, Raising

WED = date(2026, 10, 14)
THU = date(2026, 10, 15)
S = NS_PER_SECOND
M = NS_PER_MINUTE
MNQ = "CON.F.US.MNQ.Z26"


class BrokerPlugin(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth", broker_orders=True)


class GoldPlugin(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(
        bars=(BarNeed("MNQ"), BarNeed("MGC")), hours="eth", broker_orders=True
    )


class SelfPlugin(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(
        bars=(BarNeed("MNQ"),), hours="eth", brokerage="self", broker_orders=True
    )


class _Plane:
    def contracts(self, day: date) -> tuple[tuple[FeedInstrument, ...], list[str]]:
        return (FeedInstrument("MNQ", "MNQZ6", MNQ),), []


def entry(pid: str = "mx", mode: str = "practice", **kw: Any) -> dict[str, Any]:
    risk = {
        "max_contracts": 10,
        "max_open_trades": 3,
        "max_daily_loss": "500.00",
        "max_trades_per_day": 5,
    } | kw.pop("risk", {})
    return host_entry(
        pid, risk=risk, hold_through_break=False, execution={"order_mode": mode}, **kw
    )


def flat(d: date, hh: int, mm: int, n: int, price: int = 80_000) -> list[Bar]:
    return [
        make_bar("MNQ", ny(d, hh, mm + i), price, price + 1, price - 1, price) for i in range(n)
    ]


def enter(tag: str, placed_at: Instant, qty: int = 2, **kw: Any) -> EnterMarket:
    args: dict[str, Any] = {
        "tag": tag,
        "instrument": "MNQ",
        "direction": "long",
        "qty": qty,
        "stop_ticks": 40,
        "targets": (TargetLeg("tp1", qty, 40),),
        "placed_at": placed_at,
        "exact_bar_open": True,
    }
    return EnterMarket(**(args | kw))


@dataclass
class Run:
    tmp: Path
    host: PluginHost
    clock: FakeClock
    fake: FakeAdapter
    plugin: RecordingPlugin
    safeties: list[BrokerSafety] = field(default_factory=list)
    code: int = 0
    nexus: Any = None

    def log(self, kind: str) -> list[dict[str, Any]]:
        lines = read_jsonl(self.tmp / "run" / HOST_LOG_FILE_NAME)
        return [ln for ln in lines if ln.get("kind") == kind]

    def broker(self, event: str) -> list[dict[str, Any]]:
        return [ln for ln in self.log("broker") if ln.get("event") == event]

    def cards(self, pid: str = "mx") -> list[dict[str, Any]]:
        return [
            ln["card"]
            for ln in read_jsonl(self.tmp / "run" / "plugins" / pid / "decision_cards.jsonl")
        ]

    def plugin_log(self, pid: str = "mx") -> list[dict[str, Any]]:
        return read_jsonl(self.tmp / "run" / "plugins" / pid / "plugin_log.jsonl")

    def rejected(self, pid: str = "mx") -> dict[str, str]:
        return {
            ln["tag"]: ln["reason"]
            for ln in self.plugin_log(pid)
            if ln.get("kind") == "intent_rejected"
        }

    def brackets(self) -> list[Any]:
        return [r for n, r in self.fake.calls if n == "place_bracketed"]

    def places(self) -> list[OrderRequest]:
        return [r for n, r in self.fake.calls if n == "place"]

    def blocks(self) -> list[str]:
        writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        found = BlockStore(writer, plugin_dir(self.tmp / "live-state", "mx")).read()
        assert isinstance(found, tuple | list)
        return [b.kind for b in found]

    def updates(self) -> list[tuple[str, str, str | None]]:
        return [(u.tag, u.status, u.reason) for u in self.plugin.updates]


NEXUS = {"enabled": True, "account": "practice"}


type BarFn = Callable[[Bar, bool, Run], Sequence[Intent] | None]


def run(
    tmp: Path,
    start: Instant,
    until: Instant,
    *,
    bars: Sequence[Bar] = (),
    on_bar: BarFn | None = None,
    entries: Sequence[Mapping[str, Any]] | None = None,
    classes: Mapping[str, type[RecordingPlugin]] | None = None,
    attach: str = "broker",
    fake: Callable[[FakeClock], FakeAdapter] | None = None,
    before: Callable[[Run], None] | None = None,
    recorded: Sequence[BarsEvent] = (),
    exit_code: int = 0,
    nexus: Callable[[FakeClock], Any] | None = None,
) -> Run:
    """``nexus`` (a transport factory) puts ``mx`` on a Nexus-mirrored paper account."""
    clock = FakeClock(start)
    script = [(b.close_ns + 2 * S, BarsEvent((b,), b.close_ns + 2 * S)) for b in bars]
    plane = FakeDataPlane(clock, script, recorded=recorded)
    entries = list(entries) if entries is not None else [entry()]
    cls: dict[str, type[RecordingPlugin]] = dict(classes or {})
    for e in entries:
        cls.setdefault(e["id"], BrokerPlugin)
    transport = nexus(clock) if nexus is not None else None
    accounts: list[dict[str, Any]] = []
    resources: HostResources | None = None
    if transport is not None:
        accounts = [{"id": "acct", "preset": "none", "nexus": NEXUS}]
        entries = [dict(e, account="acct") if e["id"] == "mx" else e for e in entries]
        resources = HostResources(
            env=EnvView({}, {}), nexus=transport, nexus_resolved={"acct": "practice"}
        )
    host = make_host(tmp, cls, entries, clock, plane, accounts=accounts, resources=resources)
    adapter = fake(clock) if fake is not None else FakeAdapter(clock, oco_legs=True)
    adapter.contracts = {"MNQ": MNQ}
    plugin = host.plugin("mx")
    assert isinstance(plugin, RecordingPlugin)
    r = Run(tmp, host, clock, adapter, plugin, nexus=transport)
    if on_bar is not None:

        def handler(b: Bar, received: Instant, catch_up: bool, ctx: Any) -> Any:
            return on_bar(b, catch_up, r)

        plugin.handlers["on_bar"] = handler

    if attach == "broker":
        writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        state = plugin_dir(tmp / "live-state", "mx")
        journal = OrderJournal(writer, state)
        setup = PluginOrderSetup(
            "mx",
            ModeDecision("practice", "practice"),
            adapter=cast(BrokerAdapter, adapter),
            journal=journal,
            ids=ClientIds(writer, state, journal, "0a1b2c3d"),
            blocks=BlockStore(writer, state),
        )
        inner = make_safety_factory(
            setup, _Plane(), clock, host.broker_entry_gate("mx"), host.broker_instruments("mx")
        )

        def factory(day: GlobexDay, begin: Instant) -> BrokerSafety:
            safety = inner(day, begin)
            r.safeties.append(safety)
            return safety

        host.attach_order_mode(setup, factory, plugin_orders=True)
    elif attach == "paper":
        decision = ModeDecision(
            "practice", "paper", ("the start command has no --plugin-orders flag",)
        )
        host.attach_order_mode(PluginOrderSetup("mx", decision), None)
    if before is not None:
        before(r)
    r.code = asyncio.run(clock.run(host.run(until_ns=until), max_steps=2_000_000))
    assert r.code == exit_code
    return r


def at(d: date, hh: int, mm: int) -> Callable[[Bar], bool]:
    return lambda b: b.open_ns == ny(d, hh, mm)


def script(steps: Mapping[Instant, Callable[[Bar, Run], Sequence[Intent] | None]]) -> BarFn:
    def fn(b: Bar, catch_up: bool, r: Run) -> Sequence[Intent] | None:
        step = steps.get(b.open_ns)
        return None if step is None or catch_up else step(b, r)

    return fn


def no_differences(r: Run) -> bool:
    return all(not ln["differences"] for ln in r.broker("reconciliation"))


# ---------------------------------------------------------------- T13 Broker-plugin-shaped flow


def matrix_flow(**extra: Callable[[Bar, Run], Sequence[Intent] | None]) -> BarFn:
    steps: dict[Instant, Callable[[Bar, Run], Sequence[Intent] | None]] = {
        ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)],
        ny(WED, 10, 3): lambda b, r: [Flatten("t1", "session_close", "last_close")],
    }
    for k, v in extra.items():
        steps[int(k)] = v
    return script(steps)


def test_matrix_flow_sends_one_bracket_and_one_exit_and_ends_flat(tmp_path: Path) -> None:
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 8), bars=flat(WED, 10, 0, 6), on_bar=matrix_flow()
    )
    (intent,) = r.brackets()
    assert intent.kind == "market"
    assert intent.bracket_ticks == (40, 40)
    assert [p.qty for p in r.places()] == [2]
    assert len([c for c in r.fake.calls if c[0] == "cancel"]) == 2
    assert not [c for c in r.fake.calls if c[0] == "modify"]
    assert r.fake.positions["MNQ"] == 0
    assert ("t1", "filled_entry", None) in r.updates()
    assert no_differences(r)
    assert r.blocks() == []
    assert len(r.safeties) == 1  # startup on a trading day builds exactly one


def test_the_first_card_says_practice_is_in_use(tmp_path: Path) -> None:
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 1))
    first = r.cards()[0]
    assert first["title"] == "Order_Mode practice in use"
    assert first["id"] == "order-mode-0a1b2c3d"
    assert "PRACTICE_ACCOUNT_ID" in first["reason"]


# ---------------------------------------------------------------- T10 only the broker plugin


def test_only_the_broker_plugin_reaches_the_adapter(tmp_path: Path) -> None:
    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)]})
    paper_steps: dict[str, Any] = {}

    def before(r: Run) -> None:
        other = r.host.plugin("pp")
        assert isinstance(other, RecordingPlugin)
        other.handlers["on_bar"] = lambda b, rec, cu, ctx: (
            [enter("t1", b.close_ns)] if b.open_ns == ny(WED, 10, 0) and not cu else []
        )
        paper_steps["plugin"] = other

    r = run(
        tmp_path,
        ny(WED, 9, 59),
        ny(WED, 10, 4),
        bars=flat(WED, 10, 0, 3),
        on_bar=fn,
        entries=[entry(), entry("pp", "paper")],
        before=before,
    )
    assert len(r.brackets()) == 1
    cid = r.brackets()[0].client_id
    # Entry plus ProjectX Auto OCO children tagged {entry}-SL / {entry}-TP.
    assert {o.tag for o in r.fake.orders.values() if o.tag} == {cid, f"{cid}-SL", f"{cid}-TP"}
    paper = r.host.broker("pp")
    assert type(paper) is PluginPaperBroker
    assert paper is not None
    assert paper.open_tags() == ("t1",)
    assert isinstance(r.host.broker("mx"), BrokeredPaperBroker)


# ---------------------------------------------------------------- T11 gating fallback card


@pytest.mark.parametrize(
    ("attach", "text"),
    [
        ("paper", "the start command has no --plugin-orders flag"),
        ("none", "the Broker_Adapter did not resolve the accounts"),
    ],
)
def test_a_paper_fallback_trades_paper_and_names_the_reason(
    tmp_path: Path, attach: str, text: str
) -> None:
    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)]})
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 3), bars=flat(WED, 10, 0, 2), on_bar=fn, attach=attach
    )
    first = r.cards()[0]
    assert first["title"] == "Order_Mode practice was not used, running Paper"
    assert text in first["reason"]
    assert not r.fake.calls
    broker = r.host.broker("mx")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    assert any(ln["detail"] == text for ln in r.log("order_mode"))


# ---------------------------------------------------------------- T12 kill switches


def test_a_root_halt_stops_new_broker_entries_but_not_exits(tmp_path: Path) -> None:
    def halt(b: Bar, r: Run) -> list[Intent]:
        (r.tmp / "live-state" / "HALT").write_text("halt\n", encoding="utf-8")
        return [enter("t2", b.close_ns), Flatten("t1", "session_close", "last_close")]

    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)], ny(WED, 10, 2): halt})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 6), bars=flat(WED, 10, 0, 5), on_bar=fn)
    assert len(r.brackets()) == 1
    assert r.rejected()["t2"] != ""
    assert [p.qty for p in r.places()] == [2]


def test_a_persistent_block_refuses_the_next_entry_at_route_time(tmp_path: Path) -> None:
    def before(r: Run) -> None:
        writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        from fse.live.state_store import BlockRecord

        BlockStore(writer, plugin_dir(r.tmp / "live-state", "mx")).add(
            BlockRecord("reconciliation", None, "seeded", ny(WED, 9, 0))
        )

    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)]})
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 3), bars=flat(WED, 10, 0, 2), on_bar=fn, before=before
    )
    assert not r.brackets()
    assert "t1" in r.rejected()


def _store(r: Run, root: bool) -> BlockStore:
    writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
    where = r.tmp / "live-state" if root else plugin_dir(r.tmp / "live-state", "mx")
    return BlockStore(writer, where)


@pytest.mark.parametrize("root", [True, False])
def test_a_halt_command_stops_new_broker_entries_but_not_exits(tmp_path: Path, root: bool) -> None:
    from fse.live.state_store import BlockRecord

    def halt(b: Bar, r: Run) -> list[Intent]:
        _store(r, root).add(BlockRecord("halt_command", None, "fse halt", b.close_ns))
        return [enter("t2", b.close_ns), Flatten("t1", "session_close", "last_close")]

    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)], ny(WED, 10, 2): halt})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 6), bars=flat(WED, 10, 0, 5), on_bar=fn)
    assert len(r.brackets()) == 1
    assert r.rejected()["t2"] == "halt_command"
    assert [p.qty for p in r.places()] == [2]
    assert r.fake.positions["MNQ"] == 0


def test_fse_clear_on_the_plugin_dir_lets_the_next_entry_send(tmp_path: Path) -> None:
    from fse.live.state_store import BlockRecord

    def before(r: Run) -> None:
        _store(r, False).add(BlockRecord("reconciliation", None, "seeded", ny(WED, 9, 0)))

    def clear(b: Bar, r: Run) -> list[Intent]:
        _store(r, False).clear()  # what `fse clear --state-dir <plugin dir>` does
        return [enter("t2", b.close_ns)]

    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)], ny(WED, 10, 1): clear})
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 4), bars=flat(WED, 10, 0, 3), on_bar=fn, before=before
    )
    assert "t1" in r.rejected()
    assert "t2" not in r.rejected()
    (intent,) = r.brackets()
    assert intent.qty == 2


def test_a_halt_after_a_flushed_market_entry_does_not_cancel_paper(tmp_path: Path) -> None:
    def before(r: Run) -> None:
        send = r.fake.place_bracketed

        async def then_halt(o: Any) -> Any:
            result = await send(o)
            (r.tmp / "live-state" / "HALT").write_text("halt\n", encoding="utf-8")
            return result

        r.fake.place_bracketed = then_halt  # type: ignore[method-assign]
        r.fake.show_fill_after(10**6)  # the broker entry stays working: the tick sees it

    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)]})
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 4), bars=flat(WED, 10, 0, 3), on_bar=fn, before=before
    )
    assert len(r.brackets()) == 1
    assert len(r.log("broker_cancel_ignored")) == 1
    assert ("t1", "filled_entry", None) in r.updates()
    assert not [u for u in r.updates() if u[0] == "t1" and u[1] == "cancelled"]


# ---------------------------------------------------------------- T14 pre-checks and caps


def test_broker_rejections_and_the_trades_per_day_cap(tmp_path: Path) -> None:
    def many(b: Bar, r: Run) -> list[Intent]:
        return [
            enter("two", b.close_ns, targets=(TargetLeg("tp1", 1, 40), TargetLeg("tp2", 1, 60))),
            enter("ref", b.close_ns, reference_t=80_000),
            enter("ok", b.close_ns),
            enter("second", b.close_ns),
        ]

    fn = script({ny(WED, 10, 0): many})
    r = run(
        tmp_path,
        ny(WED, 9, 59),
        ny(WED, 10, 3),
        bars=flat(WED, 10, 0, 2),
        on_bar=fn,
        entries=[entry(risk={"max_trades_per_day": 1})],
    )
    rej = r.rejected()
    assert rej["two"] == "broker_one_target"
    assert rej["ref"] == "broker_reference_t"
    assert rej["second"] == "max_trades_per_day"
    assert len(r.brackets()) == 1


def test_an_invalid_bracket_withdraws_the_paper_entry_once(tmp_path: Path) -> None:
    # stop_ticks 4001 is rejected by the broker adapter; daily-loss must clear the
    # stop-risk gate (4001 * $0.50 * 2 = $4,001) so the withhold path still runs.
    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns, stop_ticks=4_001)]})
    r = run(
        tmp_path,
        ny(WED, 9, 59),
        ny(WED, 10, 4),
        bars=flat(WED, 10, 0, 3),
        on_bar=fn,
        entries=[entry(risk={"max_daily_loss": "5000.00"})],
    )
    assert not r.brackets()
    assert ("t1", "cancelled", "broker_withheld") in r.updates()
    assert not r.log("broker_withdraw_late")
    broker = r.host.broker("mx")
    assert broker is not None
    assert not broker.book.trades
    assert not broker.book.market_brackets


# ---------------------------------------------------------------- T16 persistence


def test_plugin_json_keeps_its_format_and_an_old_one_restores(tmp_path: Path) -> None:
    fn = script({ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)]})
    run(
        tmp_path,
        ny(WED, 9, 59),
        ny(WED, 10, 3),
        bars=flat(WED, 10, 0, 2),
        on_bar=fn,
        entries=[entry(mode="paper")],
        attach="off",
    )
    path = plugin_dir(tmp_path / "live-state", "mx") / "plugin.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    BROKER_CODEC.decode(doc["book"])
    r = run(tmp_path, ny(WED, 10, 4), ny(WED, 10, 5))
    broker = r.host.broker("mx")
    assert isinstance(broker, BrokeredPaperBroker)
    assert broker.open_tags() == ("t1",)
    BROKER_CODEC.decode(json.loads(path.read_text(encoding="utf-8"))["book"])


# ---------------------------------------------------------------- T17 brokerage problems


@pytest.mark.parametrize(
    ("cls", "text"),
    [
        (RecordingPlugin, "needs().broker_orders is false"),
        (SelfPlugin, "is self-brokered"),
        (GoldPlugin, "declares MGC"),
    ],
)
def test_a_plugin_that_cannot_send_broker_orders_does_not_load(
    tmp_path: Path, cls: type[RecordingPlugin], text: str
) -> None:
    clock = FakeClock(ny(WED, 10, 0))
    with pytest.raises(PluginLoadError) as info:
        make_host(tmp_path, {"mx": cls}, [entry()], clock, FakeDataPlane(clock))
    assert any(text in p for p in info.value.problems)
    if cls is RecordingPlugin:  # the same plugin on paper loads
        make_host(tmp_path, {"mx": cls}, [entry(mode="paper")], clock, FakeDataPlane(clock))


# ---------------------------------------------------------------- T15 day rollover, catch-up


def test_each_globex_day_gets_its_own_safety(tmp_path: Path) -> None:
    r = run(tmp_path, ny(WED, 17, 58), ny(WED, 18, 3))
    assert len(r.safeties) == 2
    assert r.safeties[0]._session_start == ny(date(2026, 10, 13), 18, 0)
    assert r.safeties[1]._session_start == ny(WED, 18, 0)


def test_a_resting_entry_in_the_startup_replay_is_refused(tmp_path: Path) -> None:
    replay = flat(WED, 9, 50, 2)

    def fn(b: Bar, catch_up: bool, r: Run) -> list[Intent]:
        if catch_up and b.open_ns == ny(WED, 9, 50):
            return [
                EnterResting(
                    "rest", "MNQ", "long", 1, "limit", 79_000, 78_900, (("tp1", 1, 79_200),)
                )
            ]
        return []

    r = run(
        tmp_path,
        ny(WED, 9, 59),
        ny(WED, 10, 1),
        on_bar=fn,
        recorded=[BarsEvent((b,), b.close_ns) for b in replay],
    )
    assert r.rejected()["rest"] == "broker_catch_up"
    assert not r.brackets()


# ---------------------------------------------------------------- T21 the break backstop


def test_the_break_backstop_exit_is_sent_after_the_next_days_first_sync(tmp_path: Path) -> None:
    fn = script({ny(WED, 17, 50): lambda b, r: [enter("t1", b.close_ns)]})
    r = run(tmp_path, ny(WED, 17, 49), ny(WED, 18, 4), bars=flat(WED, 17, 50, 3), on_bar=fn)
    assert len(r.brackets()) == 1
    assert r.log("host_break_backstop")
    assert [p.qty for p in r.places()] == [2]
    assert r.fake.positions["MNQ"] == 0
    assert len(r.safeties) == 2
    assert r.log("broker_carried")


# ---------------------------------------------------------------- T22 broker-only exposure


def test_a_broker_fill_paper_never_saw_is_closed_by_the_backstop(tmp_path: Path) -> None:
    # paper's fill bar is missing (exact_bar_open at 17:51, the 17:51 bar never comes)
    bars = [*flat(WED, 17, 50, 1), *flat(WED, 17, 52, 1)]
    fn = script({ny(WED, 17, 50): lambda b, r: [enter("t1", b.close_ns)]})
    r = run(tmp_path, ny(WED, 17, 49), ny(WED, 18, 4), bars=bars, on_bar=fn)
    assert ("t1", "expired", "fill_bar_missing") in r.updates()
    assert "reconciliation" in r.blocks()
    assert r.log("host_break_backstop")
    assert [p.qty for p in r.places()] == [2]
    assert r.fake.positions["MNQ"] == 0


# ---------------------------------------------------------------- T23 exits


def test_a_failed_pre_read_keeps_the_exit_pending_then_sends_it(tmp_path: Path) -> None:
    def down(b: Bar, r: Run) -> list[Intent]:
        r.fake.down = True
        return [Flatten("t1", "session_close", "last_close")]

    def up(b: Bar, r: Run) -> list[Intent]:
        r.fake.down = False
        return []

    fn = matrix_flow(**{str(ny(WED, 10, 3)): down, str(ny(WED, 10, 4)): up})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 8), bars=flat(WED, 10, 0, 6), on_bar=fn)
    assert [p.qty for p in r.places()] == [2]
    assert r.broker("resubmitted")
    assert r.fake.positions["MNQ"] == 0
    assert len([c for c in r.fake.calls if c[0] == "cancel"]) == 2


def test_a_lagging_read_defers_the_exit_to_the_next_sync(tmp_path: Path) -> None:
    def lag(b: Bar, r: Run) -> list[Intent]:
        r.fake.show_fill_after(10**6)  # the broker shows the entry working for now
        return [enter("t1", b.close_ns)]

    def flatten(b: Bar, r: Run) -> list[Intent]:
        # paper filled on this bar; the broker fill shows one read later (A5): it is
        # hidden from the exit's fresh read and seen by the sync right after it
        for oid in r.fake.lagged:
            r.fake.lagged[oid] = 1
        return [Flatten("t1", "account_flat", "next_open")]

    fn = script({ny(WED, 10, 0): lag, ny(WED, 10, 1): flatten})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 6), bars=flat(WED, 10, 0, 5), on_bar=fn)
    assert r.broker("broker_exit_deferred")
    assert [p.qty for p in r.places()] == [2]
    assert r.fake.positions["MNQ"] == 0
    assert "reconciliation" not in r.blocks()


def test_an_account_flatten_then_a_session_close_send_one_exit(tmp_path: Path) -> None:
    both = [
        Flatten(None, "account_flat", "next_open"),
        Flatten("t1", "session_close", "last_close"),
    ]
    fn = matrix_flow(**{str(ny(WED, 10, 3)): lambda b, r: both})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 8), bars=flat(WED, 10, 0, 6), on_bar=fn)
    assert [p.qty for p in r.places()] == [2]
    assert r.log("broker_exit_dup_skipped")
    assert r.fake.positions["MNQ"] == 0


# ---------------------------------------------------------------- T24 stop


def test_an_exit_routed_in_the_final_dispatch_is_sent_at_stop(tmp_path: Path) -> None:
    fn = matrix_flow()
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 4) + 3 * S, bars=flat(WED, 10, 0, 4), on_bar=fn)
    assert [p.qty for p in r.places()] == [2]


def test_a_hanging_adapter_is_bounded_at_stop(tmp_path: Path) -> None:
    def hang(b: Bar, r: Run) -> list[Intent]:
        async def slow(o: OrderRequest) -> Any:
            await r.clock.sleep(60 * S)
            raise OSError("late")

        r.fake.place = slow  # type: ignore[method-assign]
        return [Flatten("t1", "session_close", "last_close")]

    fn = matrix_flow(**{str(ny(WED, 10, 3)): hang})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 4) + 3 * S, bars=flat(WED, 10, 0, 4), on_bar=fn)
    assert not r.places()
    assert r.log("host_stopped")


def test_a_note_posted_while_stopping_becomes_a_card(tmp_path: Path) -> None:
    def reject(b: Bar, r: Run) -> list[Intent]:
        r.fake.place_outcomes.append("rejected")
        return [Flatten("t1", "session_close", "last_close")]

    fn = matrix_flow(**{str(ny(WED, 10, 3)): reject})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 4) + 3 * S, bars=flat(WED, 10, 0, 4), on_bar=fn)
    assert len(r.places()) == 1  # the final-dispatch exit, sent by the stop drain
    assert any("rejected" in c["title"] + str(c.get("reason")) for c in r.cards())


# ---------------------------------------------------------------- T25 foreign orders


def test_a_foreign_working_order_gives_a_dedicated_account_note(tmp_path: Path) -> None:
    from fse.projectx.broker import BrokerOrder
    from fse.projectx.models import OrderStatus

    def fake(clock: FakeClock) -> FakeAdapter:
        f = FakeAdapter(clock, oco_legs=True)
        f.orders[7] = BrokerOrder(7, MNQ, None, "buy", 1, "limit", 70_000, OrderStatus.OPEN)
        return f

    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 1), fake=fake)
    assert any(
        "must be dedicated to this plugin" in c["title"] + str(c.get("reason")) for c in r.cards()
    )
    assert "reconciliation" in r.blocks()


def test_the_broker_entry_gate_refuses_inside_an_account_no_trade_window(tmp_path: Path) -> None:
    clock = FakeClock(ny(WED, 10, 7))
    acct = {
        "id": "acct",
        "preset": "none",
        "rules": {"no_trade_windows": [{"start": "10:05", "end": "10:10"}]},
    }
    host = make_host(
        tmp_path,
        {"mx": BrokerPlugin},
        [entry(account="acct")],
        clock,
        FakeDataPlane(clock),
        accounts=[acct],
    )
    gate = host.broker_entry_gate("mx")  # the BrokerSafety resend check (T9 covers resends)
    assert gate("MNQ") == "account_no_trade_window"
    clock.advance_to(ny(WED, 10, 11))
    assert gate("MNQ") is None


# ---------------------------------------------------------------- T26 divergence


def test_a_paper_stop_the_broker_did_not_hit_blocks_the_next_entry(tmp_path: Path) -> None:
    bars = [
        *flat(WED, 10, 0, 2),
        make_bar("MNQ", ny(WED, 10, 2), 80_000, 80_001, 79_900, 79_950),  # paper stop hit
        *flat(WED, 10, 3, 3),
    ]
    fn = script(
        {
            ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns)],
            ny(WED, 10, 4): lambda b, r: [enter("t2", b.close_ns)],
        }
    )
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 7), bars=bars, on_bar=fn)
    assert ("t1", "closed", None) in r.updates()
    assert "reconciliation" in r.blocks()
    assert any(c["title"].startswith("Broker: reconciliation") for c in r.cards())
    assert "t2" in r.rejected()
    assert len(r.brackets()) == 1


# ---------------------------------------------------------------- T27 broker-only at start


def test_a_broker_only_position_at_start_is_closed_once(tmp_path: Path) -> None:
    def fake(clock: FakeClock) -> FakeAdapter:
        f = FakeAdapter(clock, oco_legs=True)
        f.positions["MNQ"] = 2
        return f

    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 1), fake=fake)
    (stale,) = r.log("restart_stale")
    assert stale["source"] == "broker"
    assert [(p.qty, p.side) for p in r.places()] == [(2, "sell")]
    sent = [ln for ln in r.broker("order_sent") if ln["engine_id"] == "mx:*0:exit"]
    assert len(sent) == 1
    assert r.fake.positions["MNQ"] == 0


# ---------------------------------------------------------------- T28 broker task restart (A3)


def two_open_then_flatten(b: Bar, r: Run) -> list[Intent]:
    r.fake.raise_on_place = 1
    return [Flatten(None, "account_flat", "next_open")]


def test_a_failed_broker_task_is_replaced_once_and_sends_the_whole_batch(tmp_path: Path) -> None:
    fn = script(
        {  # one bracket at a time: the legs of each fill are adopted unambiguously
            ny(WED, 10, 0): lambda b, r: [enter("t1", b.close_ns, qty=1)],
            ny(WED, 10, 1): lambda b, r: [enter("t2", b.close_ns, qty=1)],
            ny(WED, 10, 3): two_open_then_flatten,
            ny(WED, 10, 5): lambda b, r: [enter("t3", b.close_ns, qty=1)],
        }
    )
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 8), bars=flat(WED, 10, 0, 7), on_bar=fn, exit_code=1
    )
    assert r.log("broker_task_failed")
    assert len(r.safeties) == 2
    assert sorted(p.qty for p in r.places()) == [1, 1]
    assert r.fake.positions["MNQ"] == 0
    assert r.rejected()["t3"] == "broker_task_stopped"
    assert "reconciliation" not in r.blocks()


def test_after_a_task_failure_entries_send_again_on_the_next_globex_day(tmp_path: Path) -> None:
    fn = script(
        {
            ny(WED, 17, 50): lambda b, r: [enter("t1", b.close_ns, qty=1)],
            ny(WED, 17, 52): two_open_then_flatten,
            ny(WED, 17, 54): lambda b, r: [enter("t2", b.close_ns, qty=1)],
            ny(WED, 18, 0): lambda b, r: [enter("t3", b.close_ns, qty=1)],
        }
    )
    bars = [*flat(WED, 17, 50, 6), *flat(WED, 18, 0, 3)]
    r = run(tmp_path, ny(WED, 17, 49), ny(WED, 18, 4), bars=bars, on_bar=fn, exit_code=1)
    assert r.log("broker_task_failed")
    assert r.rejected()["t2"] == "broker_task_stopped"
    assert "t3" not in r.rejected()
    assert len(r.brackets()) == 2
    assert len(r.safeties) == 3  # WED, its replacement, THU


def test_a_second_failure_asks_for_a_manual_flatten(tmp_path: Path) -> None:
    def twice(b: Bar, r: Run) -> list[Intent]:
        r.fake.raise_on_place = 2
        return [Flatten(None, "account_flat", "next_open")]

    fn = matrix_flow(**{str(ny(WED, 10, 3)): twice})
    r = run(
        tmp_path, ny(WED, 9, 59), ny(WED, 10, 8), bars=flat(WED, 10, 0, 6), on_bar=fn, exit_code=1
    )
    assert len(r.safeties) == 2
    assert any("flatten the account manually" in c["title"] for c in r.cards())


# ---------------------------------------------------------------- T30 gate tick


def test_link_blocks_never_cancel_a_resting_entry_but_a_halt_does(tmp_path: Path) -> None:
    rest = EnterResting("rest", "MNQ", "long", 1, "limit", 79_000, 78_900, (("tp1", 1, 79_200),))

    def outage(b: Bar, r: Run) -> list[Intent]:
        r.fake.down = True
        return []

    def halt(b: Bar, r: Run) -> list[Intent]:
        (r.tmp / "live-state" / "HALT").write_text("halt\n", encoding="utf-8")
        return []

    bars = flat(WED, 10, 0, 8)  # the HALT's cancel shows on the bar after the next
    fn = script({ny(WED, 10, 0): lambda b, r: [rest], ny(WED, 10, 1): outage, ny(WED, 10, 4): halt})
    r = run(tmp_path, ny(WED, 9, 59), ny(WED, 10, 9), bars=bars, on_bar=fn)
    assert len(r.brackets()) == 1
    cancelled = [ln for ln in r.log("broker_skip") if ln.get("reason") == "outage"]
    assert cancelled == []
    statuses = r.updates()
    cancels = [u for u in statuses if u[0] == "rest" and u[1] == "cancelled"]
    assert len(cancels) == 1  # by the HALT only, not by the outage before it
    # the CancelAction reached the safety, which skips it during the outage (as fse live)
    assert [ln["reason"] for ln in r.broker("broker_skip")] == ["outage: no modify or cancel"]


# ---------------------------------------------------------------- Practice + Nexus mirror


def _requests(r: Run) -> list[tuple[str, Any]]:
    return [(n, getattr(req, "qty", None)) for n, req in r.fake.calls]


def _nexus_run(tmp: Path, nexus: Callable[[FakeClock], Any] | None) -> Run:
    return run(
        tmp,
        ny(WED, 9, 59),
        ny(WED, 10, 8),
        bars=flat(WED, 10, 0, 6),
        on_bar=matrix_flow(),
        nexus=nexus,
    )


def test_practice_entry_and_exit_reach_projectx_and_nexus(tmp_path: Path) -> None:
    control = _nexus_run(tmp_path / "control", None)
    r = _nexus_run(tmp_path / "nexus", FakeNexus)
    (intent,) = r.brackets()
    assert intent.kind == "market"
    assert [p.qty for p in r.places()] == [2]
    assert r.fake.positions["MNQ"] == 0
    assert _requests(r) == _requests(control)
    assert r.updates() == control.updates()
    # Nexus gets the paper book's fills: the entry, then the exit
    assert [p for _, p, _ in r.nexus.calls] == ["/trades", "/trading/accounts/practice/close"]
    assert r.nexus.ids[0] == "fse:20261014:mx:t1:entry"
    assert r.blocks() == []
    assert no_differences(r)


@pytest.mark.parametrize("transport", [Raising, Hanging])
def test_a_failing_nexus_never_stops_a_broker_exit(tmp_path: Path, transport: type) -> None:
    control = _nexus_run(tmp_path / "control", None)
    r = _nexus_run(tmp_path / "nexus", lambda clock: transport())
    assert r.nexus.calls >= 1
    assert [p.qty for p in r.places()] == [2]
    assert _requests(r) == _requests(control)
    assert r.updates() == control.updates()
    assert r.fake.positions["MNQ"] == 0
    assert r.blocks() == []
    assert r.rejected() == {}


def test_a_raising_enqueue_never_blocks_the_broker_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: NexusMirror, *args: Any) -> None:
        raise RuntimeError("enqueue bug")

    monkeypatch.setattr(NexusMirror, "enqueue_fills", boom)
    control = _nexus_run(tmp_path / "control", None)
    r = _nexus_run(tmp_path / "nexus", FakeNexus)
    assert len(r.brackets()) == 1
    assert [p.qty for p in r.places()] == [2]
    assert _requests(r) == _requests(control)
    assert r.nexus.calls == []
    (line,) = r.log("nexus_enqueue_failed")
    assert line["detail"] == "RuntimeError"


def test_broker_exits_are_sent_before_the_nexus_drain_at_shutdown(tmp_path: Path) -> None:
    sent: list[Instant] = []

    def track(r: Run) -> None:
        place = r.fake.place

        async def timed(o: OrderRequest) -> Any:
            sent.append(r.clock.now())
            return await place(o)

        r.fake.place = timed  # type: ignore[method-assign]

    until = ny(WED, 10, 4) + 2 * S  # the 10:03 bar's Flatten is routed in the final dispatch
    r = run(
        tmp_path,
        ny(WED, 9, 59),
        until,
        bars=flat(WED, 10, 0, 4),
        on_bar=matrix_flow(),
        before=track,
        nexus=lambda clock: Hanging(),
    )
    assert [p.qty for p in r.places()] == [2]
    assert r.nexus.calls == 1
    # the hung mirror drains for 2 s at stop; the exit goes out at stop, not after it
    # (stop sends broker exits before the drain, so carried ones are not delayed either)
    (at_,) = sent
    assert until <= at_ < until + 2 * S
