"""Host behaviors the Range plugin relies on (range api-request R3.1-R3.3).

- R3.1: one hook's intents are routed in order and their updates are delivered
  only after all of them are routed, so a ``last_close`` Flatten frees the caps
  for an ``EnterMarket`` in the same return (Range's reverse).
- R3.2: per bar, the book processes the bar and its updates are delivered before
  ``on_bar`` (pinned by ``test_plugin_host_dispatch.py::
  test_order_record_store_book_update_hook``); a ``Flatten(last_close,
  stamp_ns=bar.close_ns)`` from ``on_bar`` prices at that bar's ``c_t``.
- R3.3: startup order: restore, ``on_start`` (intents routed), repair replay
  (``catch_up=True``, only bars after the book's ``last_bar_open``),
  ``restart_stale``, ``on_day_open``.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fse.live.host.host import HOST_LOG_FILE_NAME, BarsEvent
from fse.live.plugins import (
    BarNeed,
    DataNeeds,
    EnterMarket,
    Flatten,
    Intent,
    ModifyStop,
    TargetLeg,
)
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin

WED, THU = date(2026, 10, 14), date(2026, 10, 15)
T0 = ny(WED, 10, 0)
M = NS_PER_MINUTE


def event(open_ns: int, o: int, h: int, low: int, c: int) -> BarsEvent:
    """The bar opening at ``open_ns``, received 2 s after its close."""
    bar = make_bar("MNQ", open_ns, o, h, low, c)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def enter(tag: str, placed_at: int, direction: str = "long", qty: int = 1) -> EnterMarket:
    return EnterMarket(
        tag=tag,
        instrument="MNQ",
        direction=direction,  # type: ignore[arg-type]
        qty=qty,
        stop_ticks=400,
        targets=(TargetLeg("tp1", qty, 400),),
        placed_at=placed_at,
        exact_bar_open=True,
    )


@pytest.mark.asyncio
async def test_reverse_in_one_return_closes_then_enters_and_delivers_after(
    tmp_path: Path,
) -> None:
    clock = FakeClock(T0)
    bar0 = event(T0, 80_000, 80_004, 79_996, 80_000)
    bar1 = event(T0 + M, 80_000, 80_008, 79_998, 80_006)  # c_t differs from o_t
    plane = FakeDataPlane(clock, [(bar0.received_ns, bar0), (bar1.received_ns, bar1)])
    entry = host_entry("p1", risk={"max_contracts": 3, "max_open_trades": 1})
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [entry], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    broker = host.broker("p1")
    assert broker is not None
    working_at_update: list[tuple[str, str, tuple[str, ...]]] = []

    def on_bar(bar: Any, received: int, catch_up: bool, ctx: Any) -> list[Intent]:
        if bar.open_ns == T0:
            return [enter("a", bar.close_ns)]
        return [
            Flatten("a", "range_rev", "last_close", stamp_ns=bar.close_ns),
            enter("b", bar.close_ns, "short", qty=3),
        ]

    def on_update(update: Any, ctx: Any) -> list[Intent]:
        working_at_update.append((update.tag, update.status, broker.working_tags()))
        return []

    plugin.handlers["on_bar"] = on_bar
    plugin.handlers["on_order_update"] = on_update
    assert await clock.run(host.run(until_ns=bar1.received_ns + NS_PER_SECOND)) == 0
    assert [(u.tag, u.status, u.reason) for u in plugin.updates] == [
        ("a", "accepted", None),
        ("a", "filled_entry", None),  # R3.2: the book's update comes before on_bar(10:01)
        ("a", "filled_exit", None),
        ("a", "closed", None),
        ("b", "accepted", None),  # not max_open_trades, not max_contracts (1 + 3 > 3)
    ]
    # R3.2 order for the 10:01 bar: book update -> on_order_update -> on_bar
    hooks = [(h, d) for h, _, d in plugin.calls if h in ("on_bar", "on_order_update")]
    assert hooks[2:] == [
        ("on_order_update", ("a", "filled_entry")),
        ("on_bar", ("MNQ", T0 + M, False)),
        ("on_order_update", ("a", "filled_exit")),
        ("on_order_update", ("a", "closed")),
        ("on_order_update", ("b", "accepted")),
    ]
    # R3.1: 'a''s updates reach the plugin only after 'b' was routed too
    assert working_at_update[2:] == [
        ("a", "filled_exit", ("b",)),
        ("a", "closed", ("b",)),
        ("b", "accepted", ("b",)),
    ]
    # R3.2: Flatten(last_close, stamp_ns=bar.close_ns) prices at that bar's c_t
    exit_fill = plugin.updates[2].fill
    assert exit_fill is not None
    assert exit_fill.price == bar1.bars[0].c_t == 80_006
    assert exit_fill.bar_open_ns == T0 + M
    closed = plugin.updates[3].trade
    assert closed is not None
    assert broker.open_tags() == ()
    assert broker.working_tags() == ("b",)


class Holder(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(
        bars=(BarNeed("MNQ"),), hours="eth", hold_through_break=True
    )


def test_startup_order_restore_on_start_replay_restart_stale_day_open(tmp_path: Path) -> None:
    entry = host_entry("p1", hold_through_break=True)
    # run 1: enter on Wednesday, hold through the break into Thursday's Globex day
    wed = ny(WED, 16, 50)
    thu_open = ny(WED, 18, 0)  # Thursday's Globex day opens Wednesday 18:00
    first = [
        event(wed, 80_000, 80_004, 79_996, 80_000),
        event(wed + M, 80_000, 80_004, 79_996, 80_000),
        event(thu_open, 80_000, 80_004, 79_996, 80_000),
        event(thu_open + M, 80_000, 80_004, 79_996, 80_000),
    ]
    clock = FakeClock(wed)
    plane = FakeDataPlane(clock, [(e.received_ns, e) for e in first])
    host = make_host(tmp_path, {"p1": Holder}, [entry], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    plugin.handlers["on_bar"] = lambda bar, *a: (
        [enter("t1", bar.close_ns)] if bar.open_ns == wed else []
    )
    asyncio.run(clock.run(host.run(until_ns=thu_open + 3 * M)))
    broker = host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    assert broker.book.last_bar_open["MNQ"] == thu_open + M

    # run 2: today's recording holds two bars the book already processed and two it did not
    later = [
        event(thu_open + 2 * M, 80_000, 80_004, 79_996, 80_000),
        event(thu_open + 3 * M, 80_010, 80_024, 80_006, 80_020),
    ]
    clock2 = FakeClock(thu_open + 10 * M)
    plane2 = FakeDataPlane(clock2, recorded=[*plane.recorded, *later])
    host2 = make_host(tmp_path, {"p1": Holder}, [entry], clock2, plane2)
    plugin2 = host2.plugin("p1")
    assert isinstance(plugin2, RecordingPlugin)
    plugin2.handlers["on_start"] = lambda ctx: [ModifyStop("t1", 79_700)]  # stop was 79_600
    asyncio.run(clock2.run(host2.run(until_ns=thu_open + 11 * M)))
    calls = [(h, d) for h, _, d in plugin2.calls if h not in ("restore", "on_stop")]
    assert plugin2.calls[0][0] == "restore"
    assert calls == [
        ("on_start", None),
        ("on_order_update", ("t1", "accepted")),  # on_start's intents are routed first
        ("on_bar", ("MNQ", thu_open + 2 * M, True)),  # only bars after last_bar_open
        ("on_bar", ("MNQ", thu_open + 3 * M, True)),
        ("on_order_update", ("t1", "filled_exit")),  # restart_stale, after the replay
        ("on_order_update", ("t1", "closed")),
        ("on_day_open", THU),
    ]
    closed = plugin2.updates[-1]
    assert closed.fill is not None
    assert closed.fill.client_id == "restart_stale:p1:t1:entry"
    assert closed.fill.price == 80_020  # the replayed 18:03 bar's close
    log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    (stale,) = [e for e in log if e["kind"] == "restart_stale"]
    assert (stale["plugin"], stale["level"], stale["trade_session"]) == (
        "p1",
        "error",
        WED.isoformat(),
    )
