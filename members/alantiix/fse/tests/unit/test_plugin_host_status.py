"""``status.json``: the host's plugin-agnostic status document (fse.live.host.status).

Every plugin gets one with no plugin code; ``StrategyPlugin.status()`` only adds
words. It never touches ``plugin.json`` and never faults the plugin.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from fse.live.host.host import HOST_LOG_FILE_NAME, PLUGIN_STATE_FILE_NAME, BarsEvent
from fse.live.host.status import STATUS_FILE_NAME
from fse.live.plugins import EnterMarket, TargetLeg
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import MinimalPlugin, RecordingPlugin

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)


def bars_at(minute: int, price: int = 80_000) -> BarsEvent:
    bar = make_bar("MNQ", T0 + minute * NS_PER_MINUTE, price, price + 4, price - 4, price)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def at(minute: int, second: int = 2) -> int:
    return T0 + minute * NS_PER_MINUTE + second * NS_PER_SECOND


def status_file(tmp_path: Path, plugin_id: str = "p1") -> Path:
    return tmp_path / "live-state" / "plugins" / plugin_id / STATUS_FILE_NAME


def run(
    tmp_path: Path,
    cls: type[Any],
    script: list[tuple[int, BarsEvent]],
    until: int,
    setup: Any = None,
) -> Any:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, script)
    host = make_host(tmp_path, {"p1": cls}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    if setup is not None:
        setup(plugin)
    asyncio.run(clock.run(host.run(until_ns=until)))
    return plugin


def test_a_plugin_with_no_status_hook_gets_a_full_status(tmp_path: Path) -> None:
    run(tmp_path, MinimalPlugin, [(at(1), bars_at(0))], at(2))
    doc = json.loads(status_file(tmp_path).read_text())
    assert doc["version"] == 1
    assert doc["plugin"] == "p1"
    assert doc["label"] == "MinimalPlugin"
    assert doc["brokerage"] == "host"
    assert doc["day"] == WED.isoformat()
    assert doc["phase"] == "waiting"
    assert doc["positions"] == []
    assert doc["working"] == []
    assert doc["today"] == {"net": "0", "entries": 0, "closed": 0, "trades": 0}
    assert doc["plugin_status"] is None
    assert doc["phase_text"]


def test_positions_and_recent_events_are_readable(tmp_path: Path) -> None:
    def setup(plugin: RecordingPlugin) -> None:
        def on_bar(bar: Any, *a: Any) -> list[EnterMarket]:
            if bar.open_ns == T0:
                return [
                    EnterMarket(
                        "t1", "MNQ", "long", 2, 40, (TargetLeg("tp1", 2, 80),), bar.close_ns, True
                    )
                ]
            return []

        plugin.handlers["on_bar"] = on_bar

    run(tmp_path, RecordingPlugin, [(at(1), bars_at(0)), (at(2), bars_at(1))], at(3), setup)
    doc = json.loads(status_file(tmp_path).read_text())
    assert doc["phase"] == "in_trade"
    [pos] = doc["positions"]
    assert pos["tag"] == "t1"
    assert pos["instrument"] == "MNQ"
    assert pos["side"] == "long"
    assert pos["qty"] == 2
    # entry at the open of the bar after the decision; stop 40 ticks (10 points) below
    assert pos["entry"] == 20_000.0
    assert pos["stop"] == 19_990.0
    assert pos["targets"] == [20_020.0]
    # what the console needs to value the position at a mark ($1.24 RT: $0.62 a side)
    assert pos["entry_t"] == 80_000
    assert pos["open_qty"] == 2
    assert pos["fees"] == "1.24"
    assert pos["realized"] == "-1.24"
    assert pos["tick_value"] == "0.50"
    assert pos["exit_fee"] == "0.62"
    assert doc["today"] == {"net": "0", "entries": 1, "closed": 0, "trades": 1}
    kinds = [r["kind"] for r in doc["recent"]]
    assert kinds[:2] == ["accepted", "filled_entry"]
    assert "Entered long MNQ: 2 @ 20,000.00 (t1)" in [r["text"] for r in doc["recent"]]


def test_the_plugin_status_hook_adds_words(tmp_path: Path) -> None:
    class Talking(RecordingPlugin):
        def status(self, now: int) -> dict[str, Any]:
            return {
                "summary": "Waiting for 11:40",
                "next": {"at_ns": now + NS_PER_MINUTE, "text": "T2 decision"},
                "schedule": [{"at_ns": now, "label": "T1 09:45", "state": "skipped"}, "junk"],
                "facts": [["EMA", 21_000.5], ["bad"]],
                "unknown_key": 1,
            }

    run(tmp_path, Talking, [(at(1), bars_at(0))], at(2))
    ps = json.loads(status_file(tmp_path).read_text())["plugin_status"]
    assert ps["summary"] == "Waiting for 11:40"
    assert ps["next"]["text"] == "T2 decision"
    assert ps["schedule"] == [
        {"at_ns": ps["schedule"][0]["at_ns"], "label": "T1 09:45", "state": "skipped"}
    ]
    assert ps["facts"] == [["EMA", "21000.5"]]
    assert "unknown_key" not in ps


def test_a_failing_status_hook_never_faults_the_plugin(tmp_path: Path) -> None:
    class Broken(RecordingPlugin):
        def status(self, now: int) -> dict[str, Any]:
            raise RuntimeError("boom")

    plugin = run(tmp_path, Broken, [(at(1), bars_at(0)), (at(2), bars_at(1))], at(3))
    assert plugin.hooks().count("on_bar") == 2
    host_log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    failed = [e for e in host_log if e["kind"] == "status_failed"]
    assert len(failed) == 1  # logged once
    assert not [e for e in host_log if e["kind"] == "plugin_faulted"]
    doc = json.loads(status_file(tmp_path).read_text())
    assert doc["phase"] == "waiting"
    assert doc["plugin_status"] is None


def test_a_non_json_status_is_ignored(tmp_path: Path) -> None:
    class Odd(RecordingPlugin):
        def status(self, now: int) -> Any:
            return {"summary": object()}

    run(tmp_path, Odd, [(at(1), bars_at(0))], at(2))
    host_log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    assert [e["kind"] for e in host_log].count("status_failed") == 1


def test_a_faulted_plugin_reports_faulted(tmp_path: Path) -> None:
    def setup(plugin: RecordingPlugin) -> None:
        def on_bar(*a: Any) -> None:
            raise ValueError("bad")

        plugin.handlers["on_bar"] = on_bar

    run(tmp_path, RecordingPlugin, [(at(1), bars_at(0))], at(2), setup)
    doc = json.loads(status_file(tmp_path).read_text())
    assert doc["phase"] == "faulted"
    assert "exception" in doc["phase_text"]
    assert doc["next_wake_ns"] is None


def test_the_next_wake_is_reported(tmp_path: Path) -> None:
    def setup(plugin: RecordingPlugin) -> None:
        plugin.wake = at(30)

    run(tmp_path, RecordingPlugin, [(at(1), bars_at(0))], at(2), setup)
    assert json.loads(status_file(tmp_path).read_text())["next_wake_ns"] == at(30)


def test_status_never_changes_the_state_file_and_is_not_rewritten(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0))])
    host = make_host(tmp_path, {"p1": MinimalPlugin}, [host_entry("p1")], clock, plane)
    asyncio.run(clock.run(host.run(until_ns=at(2))))
    state = tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME
    assert set(json.loads(state.read_text())) == {"version", "seq", "book", "plugin"}
    path = status_file(tmp_path)
    first = path.stat().st_mtime_ns
    # a restart with nothing new: same content (bar the clock), so no rewrite
    clock2 = FakeClock(at(3))
    plane2 = FakeDataPlane(clock2, recorded=plane.recorded)
    host2 = make_host(tmp_path, {"p1": MinimalPlugin}, [host_entry("p1")], clock2, plane2)
    asyncio.run(clock2.run(host2.run(until_ns=at(4))))
    assert path.stat().st_mtime_ns == first


def test_todays_recent_events_survive_a_restart(tmp_path: Path) -> None:
    def setup(plugin: RecordingPlugin) -> None:
        plugin.handlers["on_bar"] = lambda bar, *a: (
            [
                EnterMarket(
                    "t1", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 40),), bar.close_ns, True
                )
            ]
            if bar.open_ns == T0
            else []
        )

    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0)), (at(2), bars_at(1))])
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    setup(plugin)
    asyncio.run(clock.run(host.run(until_ns=at(3))))
    before = json.loads(status_file(tmp_path).read_text())["recent"]
    assert before
    clock2 = FakeClock(at(5))
    plane2 = FakeDataPlane(clock2, recorded=plane.recorded)
    host2 = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock2, plane2)
    asyncio.run(clock2.run(host2.run(until_ns=at(6))))
    after = json.loads(status_file(tmp_path).read_text())["recent"]
    assert after[: len(before)] == before


def test_today_counts_entries_and_closed_trades(tmp_path: Path) -> None:
    def setup(plugin: RecordingPlugin) -> None:
        def on_bar(bar: Any, *a: Any) -> list[EnterMarket]:
            if bar.open_ns == T0:
                return [
                    EnterMarket(
                        "t1", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 8),), bar.close_ns, True
                    )
                ]
            return []

        plugin.handlers["on_bar"] = on_bar

    # entry at 80,000 on bar 1; bar 2 trades through the 80,008 target
    script = [(at(1), bars_at(0)), (at(2), bars_at(1)), (at(3), bars_at(2, 80_020))]
    run(tmp_path, RecordingPlugin, script, at(4), setup)
    doc = json.loads(status_file(tmp_path).read_text())
    assert doc["positions"] == []
    today = doc["today"]
    assert (today["entries"], today["closed"], today["trades"]) == (1, 1, 1)
    assert today["net"] == "2.76"  # 8 ticks x $0.50 - $1.24
