"""Decision cards: a plugin log line's ``card`` -> ``decision_cards.jsonl`` (fse.live.host.cards).

Generic for every plugin; ``cards: false`` stops the file, never the plugin log.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date
from pathlib import Path
from typing import Any

from fse.live.host.cards import CARDS_FILE_NAME, MAX_FIELDS, MAX_TARGETS, clean_card
from fse.live.host.host import HOST_LOG_FILE_NAME, PLUGIN_LOG_FILE_NAME, BarsEvent
from fse.live.plugins import decision_card
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)


def bars_at(minute: int, price: int = 80_000) -> BarsEvent:
    bar = make_bar("MNQ", T0 + minute * NS_PER_MINUTE, price, price + 4, price - 4, price)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def at(minute: int, second: int = 2) -> int:
    return T0 + minute * NS_PER_MINUTE + second * NS_PER_SECOND


def run(tmp_path: Path, logged: list[Any], **entry: Any) -> Path:
    """Run one plugin that logs each of ``logged`` as a card on its first bar; its out dir."""

    def setup(plugin: Any) -> None:
        def on_bar(bar: Any, received: Any, catch_up: Any, ctx: Any) -> list[Any]:
            if bar.open_ns == T0:
                for item in logged:
                    ctx.log({"kind": "decision", "level": "info", "card": item})
            return []

        plugin.handlers["on_bar"] = on_bar

    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0))])
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1", **entry)], clock, plane)
    setup(host.plugin("p1"))
    asyncio.run(clock.run(host.run(until_ns=at(2))))
    return tmp_path / "run" / "plugins" / "p1"


def card(**over: Any) -> dict[str, Any]:
    base = decision_card(
        id="t1",
        title="Long MNQ",
        as_of_ns=T0,
        action="enter",
        instrument="MNQ",
        side="long",
        qty=1,
        entry=20_000.25,
        stop=19_990.0,
        targets=(20_010.0,),
        reason="test",
        tag="t1",
        fields=(("EMA", 21_000.5),),
    )
    base.update(over)
    return base


def test_a_valid_card_is_published_with_the_plugin_id(tmp_path: Path) -> None:
    out = run(tmp_path, [card()])
    [line] = read_jsonl(out / CARDS_FILE_NAME)
    assert line["plugin"] == "p1"
    assert line["kind"] == "decision"
    assert isinstance(line["t_ns"], int)
    assert line["card"]["id"] == "t1"
    assert line["card"]["action"] == "enter"
    assert line["card"]["targets"] == [20_010.0]
    assert line["card"]["fields"] == [["EMA", "21000.5"]]
    [log] = read_jsonl(out / PLUGIN_LOG_FILE_NAME)
    assert log["card"]["id"] == "t1"


def test_an_invalid_card_is_dropped_with_one_warning(tmp_path: Path) -> None:
    out = run(tmp_path, [card(action="buy"), card(id=""), card(as_of_ns="x")])
    assert not (out / CARDS_FILE_NAME).exists()
    assert len(read_jsonl(out / PLUGIN_LOG_FILE_NAME)) == 3
    host_log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    warnings = [e for e in host_log if e.get("kind") == "card_invalid"]
    assert len(warnings) == 1
    assert warnings[0]["plugin"] == "p1"
    assert warnings[0]["level"] == "warning"


def test_cards_off_keeps_the_plugin_log_and_writes_no_cards_file(tmp_path: Path) -> None:
    out = run(tmp_path, [card()], cards=False)
    assert not (out / CARDS_FILE_NAME).exists()
    [log] = read_jsonl(out / PLUGIN_LOG_FILE_NAME)
    assert log["card"]["id"] == "t1"


def test_a_log_line_without_a_card_writes_no_cards_file(tmp_path: Path) -> None:
    out = run(tmp_path, ["not a mapping"])
    assert not (out / CARDS_FILE_NAME).exists()


def test_clean_card_caps_and_drops_unknown_keys() -> None:
    raw = card(
        title="x" * 500,
        reason="r" * 500,
        id="i" * 200,
        ref="f" * 200,
        tag="  t1  ",
        side="sideways",
        qty=True,
        entry=math.inf,
        targets=[1.0, 2.0, "3", 4.0, 5.0, 6.0],
        fields=[["l" * 100, "v" * 500], ["bad"], [" ", 1]] + [[f"k{i}", i] for i in range(40)],
        result={"net": "-12.40", "exit_reason": "stop", "r": -1.0, "ticks": -8, "junk": 1},
        bars=[{"o": 1, "h": 2, "l": 0.5, "c": 1.5}, {"o": 1, "h": "x", "l": 1, "c": 1}],
        ladder=[{"kind": "king", "level": 5900, "strike": 5905, "value": 12}],
        dayPct=0.25,
        narration="n" * 900,
        extra="dropped",
    )
    out = clean_card(raw)
    assert out is not None
    assert "extra" not in out
    assert out["title"] == "x" * 120
    assert out["reason"] == "r" * 300
    assert out["id"] == "i" * 80
    assert out["ref"] == "f" * 80
    assert out["tag"] == "t1"
    assert out["side"] is None
    assert out["qty"] is None
    assert out["entry"] is None
    assert out["targets"] == [1.0, 2.0, 4.0]
    assert len(out["targets"]) <= MAX_TARGETS
    fields = out["fields"]
    assert isinstance(fields, list)
    assert fields[0] == ["l" * 60, "v" * 300]
    assert len(fields) <= MAX_FIELDS
    assert out["result"] == {"net": "-12.40", "exit_reason": "stop", "r": "-1.0", "ticks": -8}
    assert out["bars"] == [{"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}]
    assert out["ladder"] == [{"kind": "king", "level": 5900.0, "strike": 5905.0, "value": 12.0}]
    assert out["dayPct"] == 0.25
    narration = out["narration"]
    assert isinstance(narration, str)
    assert len(narration) == 800


def test_host_attaches_session_bars_when_store_has_data(tmp_path: Path) -> None:
    out = run(tmp_path, [card(action="skip", entry=None, stop=None, qty=None, targets=())])
    [line] = read_jsonl(out / CARDS_FILE_NAME)
    bars = line["card"]["bars"]
    assert isinstance(bars, list)
    assert len(bars) >= 1
    assert {"o", "h", "l", "c"} <= set(bars[-1])
    assert line["card"]["spot"] is not None
    assert line["card"]["dayPct"] is not None
    assert line["card"]["rangePct"] is not None


def test_clean_card_rejects_missing_required_keys() -> None:
    assert clean_card({"title": "t", "as_of_ns": 1, "action": "info"}) is None
    assert clean_card({"id": "a", "as_of_ns": 1, "action": "info"}) is None
    assert clean_card({"id": "a", "title": "t", "action": "info"}) is None
    assert clean_card({"id": "a", "title": "t", "as_of_ns": True, "action": "info"}) is None
    assert clean_card({"id": "a", "title": "t", "as_of_ns": 1, "action": "buy"}) is None
    ok = clean_card({"id": "a", "title": "t", "as_of_ns": 1, "action": "info"})
    assert ok is not None
    assert ok["targets"] == []
    assert ok["fields"] == []
    assert ok["result"] is None
    assert ok["bars"] == []
    assert ok["ladder"] == []
    assert ok["odds"] is None
    assert ok["narration"] is None
