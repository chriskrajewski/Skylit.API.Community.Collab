"""Daily rotation, weekends and the restart rules of ``PluginHost`` (design 5.3, 12.1 step 7).

The break backstop truth table: the effective ``hold_through_break`` is
``needs().hold_through_break AND plugins[].hold_through_break``; only an
effective ``false`` flattens at 18:00, Friday 18:00 included.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fse.calendars import CalendarError, CalendarProblem, Calendars, load_calendars
from fse.live.host.host import HOST_LOG_FILE_NAME, BarsEvent
from fse.live.plugins import BarNeed, DataNeeds, EnterMarket, TargetLeg
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import (
    CALENDAR_DIR,
    FakeDataPlane,
    host_entry,
    make_bar,
    make_host,
    ny,
    read_jsonl,
)
from tests.fakes.plugins import RecordingPlugin

WED, THU, FRI, MON = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19)


def bar_event(t: int, price: int = 80_000) -> BarsEvent:
    bar = make_bar("MNQ", t, price, price + 4, price - 4, price)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def enter(tag: str, placed_at: int) -> EnterMarket:
    return EnterMarket(tag, "MNQ", "long", 1, 400, (TargetLeg("tp1", 1, 400),), placed_at, True)


class Holder(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(
        bars=(BarNeed("MNQ"),), hours="eth", hold_through_break=True
    )


def trade_into_rotation(
    tmp_path: Path, day: date, cls: type[RecordingPlugin], yaml_hold: bool
) -> tuple[Any, RecordingPlugin, FakeDataPlane]:
    """Enter at ``day`` 16:50, fill at 16:51, run past the 18:00 rotation."""
    start = ny(day, 16, 50)
    clock = FakeClock(start)
    plane = FakeDataPlane(
        clock,
        [
            (start + NS_PER_MINUTE + 2 * NS_PER_SECOND, bar_event(start)),
            (start + 2 * NS_PER_MINUTE + 2 * NS_PER_SECOND, bar_event(start + NS_PER_MINUTE)),
        ],
    )
    host = make_host(
        tmp_path,
        {"p1": cls},
        [host_entry("p1", hold_through_break=yaml_hold)],
        clock,
        plane,
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    plugin.handlers["on_bar"] = lambda bar, *a: (
        [enter("t1", bar.close_ns)] if bar.open_ns == start else []
    )
    asyncio.run(clock.run(host.run(until_ns=ny(day, 18, 1))))
    return host, plugin, plane


@pytest.mark.parametrize("day", [WED, FRI], ids=["wednesday", "friday"])
@pytest.mark.parametrize(
    ("needs_hold", "yaml_hold", "flattened"),
    [(False, False, True), (False, True, True), (True, False, True), (True, True, False)],
)
def test_break_backstop_truth_table(
    tmp_path: Path, day: date, needs_hold: bool, yaml_hold: bool, flattened: bool
) -> None:
    cls = Holder if needs_hold else RecordingPlugin
    host, plugin, plane = trade_into_rotation(tmp_path, day, cls, yaml_hold)
    assert host.hold_through_break("p1") is (needs_hold and yaml_hold)
    statuses = [(u.status, u.reason) for u in plugin.updates]
    assert statuses[:2] == [("accepted", None), ("filled_entry", None)]
    broker = host.broker("p1")
    assert broker is not None
    log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    backstops = [e for e in log if e["kind"] == "host_break_backstop"]
    if flattened:
        assert statuses[2:] == [("filled_exit", None), ("closed", None)]
        closed = plugin.updates[-1]
        assert closed.fill is not None
        assert closed.fill.client_id == "host_break_backstop:p1:t1:entry"
        assert closed.fill.price == 80_000  # the newest bar's close
        assert broker.open_tags() == ()
        assert [b["level"] for b in backstops] == ["error"]
    else:
        assert statuses[2:] == []
        assert broker.open_tags() == ("t1",)
        assert backstops == []
    # the hooks run in the 5.3 order
    hooks = [h for h in plugin.hooks() if h in ("on_day_close", "on_order_update", "on_day_open")]
    close_at = hooks.index("on_day_close")
    assert close_at >= 2
    assert plane.rotations == [(day, None if day == FRI else THU)]
    if day == WED:
        assert hooks[-1] == "on_day_open"
        assert [d for h, _, d in plugin.calls if h == "on_day_open"] == [WED, THU]
    else:
        assert [d for h, _, d in plugin.calls if h == "on_day_open"] == [FRI]


def test_used_tags_reset_at_rotation(tmp_path: Path) -> None:
    clock = FakeClock(ny(WED, 17, 50))
    plane = FakeDataPlane(clock)
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    plugin.handlers["on_start"] = lambda ctx: [enter("t1", ctx.now)]
    plugin.handlers["on_day_open"] = lambda day, ctx: (
        [enter("t1", ctx.now)] if day.day == THU else []
    )
    asyncio.run(clock.run(host.run(until_ns=ny(WED, 18, 1))))
    assert [(u.tag, u.status, u.reason) for u in plugin.updates] == [
        ("t1", "accepted", None),
        ("t1", "cancelled", "host_break_backstop"),  # the working entry at 18:00
        ("t1", "accepted", None),  # the same tag on Thursday
    ]


def test_weekend_sleep_then_monday_opens_at_sunday_1800(tmp_path: Path) -> None:
    clock = FakeClock(ny(FRI, 17, 30))
    plane = FakeDataPlane(clock)
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    sunday_open = ny(date(2026, 10, 18), 18, 0)
    asyncio.run(clock.run(host.run(until_ns=sunday_open + NS_PER_MINUTE)))
    events = [(h, t, d) for h, t, d in plugin.calls if h in ("on_day_open", "on_day_close")]
    assert events == [
        ("on_day_open", ny(FRI, 17, 30), FRI),
        ("on_day_close", ny(FRI, 18, 0), FRI),
        ("on_day_open", sunday_open, MON),
    ]
    assert plane.rotations == [(FRI, None)]
    # the weekend was a few big sleeps, not a busy loop
    assert len(clock.sleeps) < 20
    assert plane.urgent[-1] == [("MNQ", ny(MON, 17, 0))]


def test_calendar_failure_at_rotation_blocks_entries(tmp_path: Path) -> None:
    clock = FakeClock(ny(WED, 17, 59))
    plane = FakeDataPlane(clock)
    calls: list[tuple[date, date]] = []

    def loader(first: date, last: date) -> Calendars:
        calls.append((first, last))
        raise CalendarError([CalendarProblem(CALENDAR_DIR / "x.yaml", None, None, "broken")])

    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin},
        [host_entry("p1")],
        clock,
        plane,
        calendars=load_calendars(CALENDAR_DIR, WED, FRI),
        calendar_loader=loader,
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    plugin.handlers["on_day_open"] = lambda day, ctx: (
        [enter("t1", ctx.now)] if day.day == THU else []
    )
    asyncio.run(clock.run(host.run(until_ns=ny(WED, 19, 30))))
    assert [(u.status, u.reason) for u in plugin.updates] == [("rejected", "calendar_stale")]
    assert calls[0] == (THU, date(2026, 10, 22))
    assert len(calls) == 2  # the hourly retry at 19:00
    kinds = [e["kind"] for e in read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)]
    assert kinds.count("calendar_stale") == 2


def test_restart_flattens_a_trade_from_an_earlier_day(tmp_path: Path) -> None:
    start = ny(WED, 15, 0)
    clock = FakeClock(start)
    plane = FakeDataPlane(
        clock,
        [
            (start + NS_PER_MINUTE + 2 * NS_PER_SECOND, bar_event(start)),
            (
                start + 2 * NS_PER_MINUTE + 2 * NS_PER_SECOND,
                bar_event(start + NS_PER_MINUTE, 80_010),
            ),
        ],
    )
    host = make_host(tmp_path, {"p1": Holder}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    plugin.handlers["on_bar"] = lambda bar, *a: (
        [enter("t1", bar.close_ns)] if bar.open_ns == start else []
    )
    asyncio.run(clock.run(host.run(until_ns=ny(WED, 15, 5))))
    broker = host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    # restart on Thursday's Globex day
    clock2 = FakeClock(ny(THU, 10, 0))
    host2 = make_host(tmp_path, {"p1": Holder}, [host_entry("p1")], clock2, FakeDataPlane(clock2))
    asyncio.run(clock2.run(host2.run(until_ns=ny(THU, 10, 1))))
    plugin2 = host2.plugin("p1")
    assert isinstance(plugin2, RecordingPlugin)
    assert [(u.status, u.reason) for u in plugin2.updates] == [
        ("filled_exit", None),
        ("closed", None),
    ]
    closed = plugin2.updates[-1]
    assert closed.fill is not None
    assert closed.fill.client_id == "restart_stale:p1:t1:entry"
    assert closed.fill.price == 80_010
    log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    assert any(e["kind"] == "restart_stale" and e["level"] == "error" for e in log)
    # the restart happens before on_day_open(today)
    hooks = plugin2.hooks()
    assert hooks.index("on_order_update") < hooks.index("on_day_open")
