"""``PluginHost`` and ``ModifyStop`` (plugin-api-r1, plan decision 3, test items 16-18).

Routing from a hook, not gated by the halt file, persistence across a restart, the
``Flatten`` interactions and the 18:00 break backstop. Bars are received 2 s after
their close, so ``ctx.now`` is past the next bar's open when the plugin modifies.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from fse.live.host.broker import BROKER_CODEC, PluginBrokerState
from fse.live.host.host import HOST_LOG_FILE_NAME, PLUGIN_STATE_FILE_NAME, BarsEvent
from fse.live.plugins import EnterMarket, Flatten, ModifyStop, OrderUpdate, TargetLeg
from fse.logio.canonical_json import dumps
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)
M = NS_PER_MINUTE
STOP = "p1:t1:stop"


def ev(start: int, minute: int, low: int = 79_996, price: int = 80_000) -> BarsEvent:
    """The bar opening ``minute`` minutes after ``start`` (o = c = price), received 2 s late."""
    bar = make_bar("MNQ", start + minute * M, price, price + 4, low, price)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def at(minute: int, second: int = 2, start: int = T0) -> int:
    return start + minute * M + second * NS_PER_SECOND


def enter(placed_at: int) -> EnterMarket:
    """``t1`` long 1 filling at the 80_000 open: stop 79_960, tp1 80_040 (slip 0)."""
    return EnterMarket("t1", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 40),), placed_at, True)


def statuses(updates: list[OrderUpdate]) -> list[tuple[str, str, str | None, str | None]]:
    return [(u.tag, u.status, u.reason, u.role) for u in updates]


def exits(updates: list[OrderUpdate]) -> list[tuple[str | None, int]]:
    return [(u.role, u.fill.price) for u in updates if u.status == "filled_exit" and u.fill]


def host_with(
    tmp_path: Path, script: list[tuple[int, BarsEvent]], start: int = T0, **entry: Any
) -> tuple[Any, RecordingPlugin, FakeClock]:
    clock = FakeClock(start)
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin},
        [host_entry("p1", **entry)],
        clock,
        FakeDataPlane(clock, script),
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    return host, plugin, clock


def on_bars(plugin: RecordingPlugin, by_minute: dict[int, list[Any]], start: int = T0) -> None:
    """``on_bar`` returns ``by_minute[m]`` for the bar opening at ``start + m``; enters on m=0."""

    def on_bar(bar: Any, received: int, catch_up: bool, ctx: Any) -> list[Any]:
        minute = (bar.open_ns - start) // M
        if minute == 0:
            return [enter(bar.close_ns)]
        out = by_minute.get(minute, [])
        return out(ctx) if callable(out) else out

    plugin.handlers["on_bar"] = on_bar


# ---------------------------------------------------------------- 16: routing


def test_modify_from_on_bar_is_routed_and_the_next_bar_exits_there(tmp_path: Path) -> None:
    script = [(at(m + 1), ev(T0, m)) for m in range(2)] + [(at(3), ev(T0, 2, low=79_990))]
    host, plugin, clock = host_with(tmp_path, script)
    on_bars(plugin, {1: [ModifyStop("t1", 79_990)]})
    assert asyncio.run(clock.run(host.run(until_ns=at(4)))) == 0
    assert statuses(plugin.updates) == [
        ("t1", "accepted", None, "entry"),
        ("t1", "filled_entry", None, "entry"),
        ("t1", "accepted", None, "stop"),
        ("t1", "filled_exit", None, "stop"),
        ("t1", "closed", None, "stop"),
    ]
    accepted = plugin.updates[2]
    assert accepted.at == at(2)  # ctx.now: after the next bar's open ...
    assert accepted.detail == {  # ... which is still the effective time
        "stop_price": 79_990,
        "previous_stop": 79_960,
        "effective_ns": T0 + 2 * M,
        "unchanged": False,
    }
    assert exits(plugin.updates) == [("stop", 79_990)]
    closed = plugin.updates[-1].trade
    assert closed is not None
    assert closed.initial_stop == 79_960
    log = read_jsonl(tmp_path / "run" / "plugins" / "p1" / "plugin_log.jsonl")
    assert any(e["kind"] == "intent_accepted" and e["intent"] == "ModifyStop" for e in log)


def test_a_loosen_is_logged_as_a_warning(tmp_path: Path) -> None:
    script = [(at(m + 1), ev(T0, m)) for m in range(2)]
    host, plugin, clock = host_with(tmp_path, script)
    on_bars(plugin, {1: [ModifyStop("t1", 79_950)]})
    asyncio.run(clock.run(host.run(until_ns=at(3))))
    assert statuses(plugin.updates)[-1] == ("t1", "rejected", "stop_loosen", "stop")
    log = read_jsonl(tmp_path / "run" / "plugins" / "p1" / "plugin_log.jsonl")
    (rej,) = [e for e in log if e["kind"] == "intent_rejected"]
    assert (rej["level"], rej["reason"], rej["intent"]) == ("warning", "stop_loosen", "ModifyStop")


def test_a_halt_file_does_not_refuse_a_modify(tmp_path: Path) -> None:
    script = [(at(m + 1), ev(T0, m)) for m in range(3)] + [(at(4), ev(T0, 3, low=79_990))]
    host, plugin, clock = host_with(tmp_path, script)
    blocked: list[tuple[str, ...]] = []

    def halt(ctx: Any) -> list[Any]:
        (tmp_path / "live-state" / "HALT").write_text("")
        return []

    def modify(ctx: Any) -> list[Any]:
        blocked.append(ctx.entries_blocked())
        return [ModifyStop("t1", 79_990)]

    on_bars(plugin, {1: halt, 2: modify})
    asyncio.run(clock.run(host.run(until_ns=at(5))))
    assert blocked == [("halt_file",)]
    assert statuses(plugin.updates)[2:] == [
        ("t1", "accepted", None, "stop"),
        ("t1", "filled_exit", None, "stop"),
        ("t1", "closed", None, "stop"),
    ]


def test_a_non_intent_still_faults(tmp_path: Path) -> None:
    script = [(at(m + 1), ev(T0, m)) for m in range(2)]
    host, plugin, clock = host_with(tmp_path, script)
    on_bars(plugin, {1: [("ModifyStop", "t1", 79_990)]})
    assert asyncio.run(clock.run(host.run(until_ns=at(3)))) == 1
    log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    assert any(e["kind"] == "plugin_faulted" and e["reason"] == "bad_intents" for e in log)
    broker = host.broker("p1")
    assert broker is not None
    stop = broker.book.orders[STOP].latest
    assert stop is not None
    assert stop.price == 79_960


# ---------------------------------------------------------------- 17: persistence


def test_a_modified_stop_survives_a_restart(tmp_path: Path) -> None:
    script = [(at(m + 1), ev(T0, m)) for m in range(2)]
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, script)
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    on_bars(plugin, {1: [ModifyStop("t1", 79_990)]})
    asyncio.run(clock.run(host.run(until_ns=at(2, 30))))
    path = tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME
    doc = json.loads(path.read_text())
    state = BROKER_CODEC.decode(doc["book"])
    assert isinstance(state, PluginBrokerState)
    assert BROKER_CODEC.decode(json.loads(dumps(BROKER_CODEC.encode(state)))) == state
    versions = state.book.orders[STOP].versions
    assert [(t, o.price if o else None) for t, o in versions] == [
        (T0 + M, 79_960),
        (T0 + 2 * M, 79_990),
    ]
    # restart on the same state dir; the next bar touches the modified stop only
    clock2 = FakeClock(at(2, 40))
    plane2 = FakeDataPlane(clock2, [(at(3), ev(T0, 2, low=79_990))], recorded=plane.recorded)
    host2 = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock2, plane2)
    plugin2 = host2.plugin("p1")
    assert isinstance(plugin2, RecordingPlugin)
    asyncio.run(clock2.run(host2.run(until_ns=at(4))))
    assert any(c[0] == "restore" for c in plugin2.calls)
    assert statuses(plugin2.updates) == [
        ("t1", "filled_exit", None, "stop"),
        ("t1", "closed", None, "stop"),
    ]
    assert exits(plugin2.updates) == [("stop", 79_990)]


# ---------------------------------------------------------------- 18: flatten, backstop


def test_flatten_last_close_after_a_modify_then_modify_is_not_open(tmp_path: Path) -> None:
    script = [(at(m + 1), ev(T0, m)) for m in range(3)]
    host, plugin, clock = host_with(tmp_path, script)
    on_bars(
        plugin,
        {
            1: [ModifyStop("t1", 79_990), Flatten("t1", "session_close", "last_close")],
            2: [ModifyStop("t1", 79_995)],
        },
    )
    asyncio.run(clock.run(host.run(until_ns=at(4))))
    assert statuses(plugin.updates)[2:] == [
        ("t1", "accepted", None, "stop"),
        ("t1", "filled_exit", None, "exit"),
        ("t1", "closed", None, "exit"),
        ("t1", "rejected", "not_open", "stop"),
    ]
    assert exits(plugin.updates) == [("exit", 80_000)]  # the 10:01 bar's close


def test_with_a_next_open_flatten_pending_the_market_exit_fills_first(tmp_path: Path) -> None:
    # The next_open exit is placed at ctx.now (10:02:02), so it first applies to the
    # 10:03 bar; the modify from the 10:02 bar's hook applies from 10:03 too.
    script = [(at(m + 1), ev(T0, m)) for m in range(3)] + [(at(4), ev(T0, 3, low=79_990))]
    host, plugin, clock = host_with(tmp_path, script)
    pending: list[bool] = []

    def modify(ctx: Any) -> list[Any]:
        broker = host.broker("p1")
        assert broker is not None
        pending.append("p1:t1:exit" in broker.book.orders)
        return [ModifyStop("t1", 79_990)]

    on_bars(plugin, {1: [Flatten("t1", "exit_now", "next_open")], 2: modify})
    asyncio.run(clock.run(host.run(until_ns=at(5))))
    assert pending == [True]
    assert statuses(plugin.updates)[2:] == [
        ("t1", "accepted", None, "stop"),
        ("t1", "filled_exit", None, "exit"),
        ("t1", "closed", None, "exit"),
    ]
    assert exits(plugin.updates) == [("exit", 80_000)]  # the 10:03 open, not the stop


def test_the_break_backstop_flattens_a_modified_trade(tmp_path: Path) -> None:
    start = ny(WED, 16, 50)
    script = [(at(m + 1, start=start), ev(start, m)) for m in range(2)]
    host, plugin, clock = host_with(tmp_path, script, start, hold_through_break=False)
    on_bars(plugin, {1: [ModifyStop("t1", 79_970)]}, start)
    asyncio.run(clock.run(host.run(until_ns=ny(WED, 18, 1))))
    assert statuses(plugin.updates) == [
        ("t1", "accepted", None, "entry"),
        ("t1", "filled_entry", None, "entry"),
        ("t1", "accepted", None, "stop"),
        ("t1", "filled_exit", None, "exit"),
        ("t1", "closed", None, "exit"),
    ]
    closed = plugin.updates[-1]
    assert closed.fill is not None
    assert closed.fill.client_id == "host_break_backstop:p1:t1:entry"
    assert closed.fill.price == 80_000
    broker = host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ()
    assert broker.book.orders == {}
