"""``plugin.json`` persistence and restart (design 12.1 steps 5-6, 12.2, 13).

A crash is a ``BaseException`` raised by the writer when it is about to write
``plugin.json``: the host run dies there, as a killed process would. The
restarted host gets a data plane holding the first run's recording, so the
repair replay sees the same bars.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fse.live.host.host import HOST_LOG_FILE_NAME, PLUGIN_STATE_FILE_NAME, BarsEvent
from fse.live.plugins import EnterMarket, TargetLeg
from fse.live.state_store import BlockStore
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)


class Crash(BaseException):
    """The process dies here."""


class CrashingWriter(LogWriter):
    __slots__ = ("crash_when", "writes")

    def __init__(self, crash_when: Callable[[int], bool]) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        self.crash_when = crash_when
        self.writes = 0

    def write_text(self, path: Any, text: str) -> Path:
        if Path(path).name == PLUGIN_STATE_FILE_NAME:
            self.writes += 1
            if self.crash_when(self.writes):
                raise Crash
        return super().write_text(path, text)


def bars_at(minute: int, price: int = 80_000) -> BarsEvent:
    bar = make_bar("MNQ", T0 + minute * NS_PER_MINUTE, price, price + 4, price - 4, price)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def at(minute: int, second: int = 2) -> int:
    return T0 + minute * NS_PER_MINUTE + second * NS_PER_SECOND


def enter(tag: str, placed_at: int) -> EnterMarket:
    return EnterMarket(tag, "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 40),), placed_at, True)


def state_file(tmp_path: Path, plugin_id: str = "p1") -> Path:
    return tmp_path / "live-state" / "plugins" / plugin_id / PLUGIN_STATE_FILE_NAME


def counting(plugin: RecordingPlugin, entry_on: int | None = T0) -> None:
    """Count bars in the plugin state; enter ``t1`` on the bar opening at ``entry_on``."""

    def on_bar(bar: Any, *a: Any) -> list[EnterMarket]:
        assert isinstance(plugin.state, dict)
        n = plugin.state["n"]
        assert isinstance(n, int)
        plugin.state = {"n": n + 1}
        return [enter("t1", bar.close_ns)] if bar.open_ns == entry_on else []

    plugin.handlers["on_bar"] = on_bar


def run_first(
    tmp_path: Path, script: list[tuple[int, BarsEvent]], until: int, writer: LogWriter | None = None
) -> tuple[FakeDataPlane, RecordingPlugin, int | BaseException]:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, script)
    host = make_host(
        tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane, writer=writer
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    counting(plugin)

    async def go() -> int:
        return await clock.run(host.run(until_ns=until))

    try:
        result: int | BaseException = asyncio.run(go())
    except Crash as exc:
        result = exc
    return plane, plugin, result


def restart(
    tmp_path: Path, plane: FakeDataPlane, start: int, until: int, entries: tuple[str, ...] = ()
) -> tuple[RecordingPlugin, Any]:
    clock = FakeClock(start)
    plane2 = FakeDataPlane(clock, recorded=plane.recorded)
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane2)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    counting(plugin, entry_on=None)
    if entries:
        plugin.handlers["on_start"] = lambda ctx: [enter(t, ctx.now) for t in entries]
    asyncio.run(clock.run(host.run(until_ns=until)))
    return plugin, host


def test_round_trip_restores_both_halves_and_seq_continues(tmp_path: Path) -> None:
    plane, _plugin, result = run_first(tmp_path, [(at(1), bars_at(0)), (at(2), bars_at(1))], at(3))
    assert result == 0
    doc = json.loads(state_file(tmp_path).read_text())
    assert set(doc) == {"version", "seq", "book", "plugin"}
    assert doc["version"] == 1
    assert doc["plugin"] == {"n": 2}
    seq = doc["seq"]
    plugin2, host2 = restart(tmp_path, plane, at(5), at(6))
    assert ("restore", 0, {"n": 2}) in plugin2.calls
    broker = host2.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    # nothing changed after the restore: the file is not rewritten
    assert json.loads(state_file(tmp_path).read_text())["seq"] == seq


def test_unchanged_state_is_not_rewritten(tmp_path: Path) -> None:
    script = [(at(1), bars_at(0))]
    writer = CrashingWriter(lambda n: False)
    _, plugin, _ = run_first(tmp_path, script, at(30), writer)
    # writes: on_start (fresh state) and the bar step; on_day_open, ~1,700 gate ticks
    # while the entry works, and the graceful stop change nothing, so nothing more is written
    assert writer.writes == 2
    assert json.loads(state_file(tmp_path).read_text())["seq"] == 2
    assert plugin.state == {"n": 1}


def test_crash_before_the_fill_write_redelivers_the_fill(tmp_path: Path) -> None:
    script = [(at(1), bars_at(0)), (at(2), bars_at(1)), (at(3), bars_at(2))]
    seqs: list[int] = []

    def crash(n: int) -> bool:
        if state_file(tmp_path).exists():
            seqs.append(json.loads(state_file(tmp_path).read_text())["seq"])
        # write 1: on_start, 2: the 10:00 bar (accepted), 3: the 10:01 bar (filled): die there
        return n == 3

    plane, plugin, result = run_first(tmp_path, script, at(4), CrashingWriter(crash))
    assert isinstance(result, Crash)
    assert [u.status for u in plugin.updates] == ["accepted", "filled_entry"]
    plugin2, host2 = restart(tmp_path, plane, at(1, 30), at(2, 30))
    assert ("restore", 0, {"n": 1}) in plugin2.calls
    # the repair replays the 10:01 bar (catch_up) and redelivers its fill once
    assert [u.status for u in plugin2.updates] == ["filled_entry"]
    assert [d for h, _, d in plugin2.calls if h == "on_bar"] == [("MNQ", T0 + NS_PER_MINUTE, True)]
    broker = host2.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)


def test_crash_after_the_fill_write_does_not_redeliver(tmp_path: Path) -> None:
    script = [(at(1), bars_at(0)), (at(2), bars_at(1)), (at(3), bars_at(2))]
    plane, _plugin, result = run_first(tmp_path, script, at(4), CrashingWriter(lambda n: n == 4))
    assert isinstance(result, Crash)
    plugin2, _ = restart(tmp_path, plane, at(3, 30), at(4))
    assert ("restore", 0, {"n": 2}) in plugin2.calls
    assert plugin2.updates == []
    assert [d for h, _, d in plugin2.calls if h == "on_bar"] == [
        ("MNQ", T0 + 2 * NS_PER_MINUTE, True)
    ]


class Entering(RecordingPlugin):
    """Enters ``<id>x`` on every bar (set at construction, so a rebuilt plugin does too)."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.handlers["on_bar"] = lambda bar, *a: [enter(f"{self.plugin_id}x", bar.close_ns)]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"version": 1, "seq": 3, "book": {"$type": "nope.Nope"}, "plugin": None}),
        json.dumps({"version": 2, "seq": 3, "book": None, "plugin": None}),
        "RESTORE_RAISES",
    ],
    ids=["unreadable", "undecodable", "version", "restore_value_error"],
)
def test_bad_state_discards_both_halves_and_blocks_only_that_plugin(
    tmp_path: Path, content: str
) -> None:
    if content == "RESTORE_RAISES":
        _plane, _, _ = run_first(tmp_path, [(at(1), bars_at(0))], at(2))
        doc = json.loads(state_file(tmp_path).read_text())
        doc["plugin"] = {"unusable": True}
        content = json.dumps(doc)
    path = state_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    clock = FakeClock(at(5))
    plane2 = FakeDataPlane(clock, [(at(6), bars_at(5))])
    host = make_host(
        tmp_path,
        {"p1": Entering, "p2": Entering},
        [host_entry("p1"), host_entry("p2")],
        clock,
        plane2,
    )
    asyncio.run(clock.run(host.run(until_ns=at(7))))
    p1, p2 = host.plugin("p1"), host.plugin("p2")
    assert isinstance(p1, RecordingPlugin)
    assert isinstance(p2, RecordingPlugin)
    # p1 is a fresh plugin with a fresh book
    assert p1.state == {"n": 0}
    assert not any(h == "restore" for h in p1.hooks())
    broker = host.broker("p1")
    assert broker is not None
    assert broker.book.trades == {}
    assert [(u.status, u.reason) for u in p1.updates] == [("rejected", "restore_failed")]
    assert [u.status for u in p2.updates] == ["accepted"]
    bad = list(path.parent.glob(f"{PLUGIN_STATE_FILE_NAME}.bad-*"))
    assert len(bad) == 1
    found = BlockStore(LogWriter(Redactor()), path.parent).read()
    assert isinstance(found, tuple)
    assert [b.kind for b in found] == ["restore_failed"]
    assert not (tmp_path / "live-state" / "blocks.json").exists()
    assert any(
        e["kind"] == "restore_failed" and e["plugin"] == "p1"
        for e in read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    )


def test_tags_stay_used_across_a_restart(tmp_path: Path) -> None:
    plane, _, result = run_first(tmp_path, [(at(1), bars_at(0))], at(2))
    assert result == 0
    plugin2, _ = restart(tmp_path, plane, at(1, 30), at(2), entries=("t1", "t2"))
    assert [(u.tag, u.status, u.reason) for u in plugin2.updates] == [
        ("t1", "rejected", "duplicate_tag"),
        ("t2", "accepted", None),
    ]


class FailingWriter(CrashingWriter):
    __slots__ = ("fail_on",)

    def __init__(self, fail_on: set[int]) -> None:
        super().__init__(lambda n: False)
        self.fail_on = fail_on

    def write_text(self, path: Any, text: str) -> Path:
        if Path(path).name == PLUGIN_STATE_FILE_NAME and self.writes + 1 in self.fail_on:
            self.writes += 1
            raise OSError(28, "No space left on device")
        return super().write_text(path, text)


def test_three_write_failures_block_entries_until_a_write_succeeds(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    script = [(at(m + 1), bars_at(m)) for m in range(5)]
    writer = FailingWriter({2, 3, 4})  # write 1 is on_start; the 10:00-10:02 bars fail
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin},
        [host_entry("p1")],
        clock,
        FakeDataPlane(clock, script),
        writer=writer,
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    counting(plugin, entry_on=None)
    blocked: list[tuple[str, ...]] = []

    def on_bar(bar: Any, received: int, catch_up: bool, ctx: Any) -> list[EnterMarket]:
        assert isinstance(plugin.state, dict)
        plugin.state = {"n": int(str(plugin.state["n"])) + 1}
        blocked.append(ctx.entries_blocked())
        if bar.open_ns >= T0 + 3 * NS_PER_MINUTE:
            return [enter(f"t{bar.open_ns}", bar.close_ns)]
        return []

    plugin.handlers["on_bar"] = on_bar
    asyncio.run(clock.run(host.run(until_ns=at(6))))
    assert blocked == [(), (), (), ("state_write_failed",), ()]
    assert [(u.status, u.reason) for u in plugin.updates][:1] == [
        ("rejected", "state_write_failed")
    ]
    assert ("accepted", None) in [(u.status, u.reason) for u in plugin.updates]
    failures = [
        e["failures"]
        for e in read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
        if e["kind"] == "state_write_failed"
    ]
    assert failures == [1, 2, 3]


def test_an_older_state_without_day_closed_still_restores(tmp_path: Path) -> None:
    plane, _plugin, result = run_first(tmp_path, [(at(1), bars_at(0)), (at(2), bars_at(1))], at(3))
    assert result == 0
    doc = json.loads(state_file(tmp_path).read_text())
    assert doc["book"]["day_closed"] == 0
    del doc["book"]["day_closed"]  # a plugin.json written before the field existed
    state_file(tmp_path).write_text(json.dumps(doc))
    plugin2, host2 = restart(tmp_path, plane, at(5), at(6))
    assert ("restore", 0, {"n": 2}) in plugin2.calls
    broker = host2.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    assert broker.state.day_closed == 0
    host_log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    assert not [e for e in host_log if e["kind"] == "restore_failed"]
