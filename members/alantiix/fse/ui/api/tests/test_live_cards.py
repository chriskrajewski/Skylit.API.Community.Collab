"""The Cards page (fse_ui.runner.live_cards, GET /api/live/cards): every plugin's
decision cards plus fse.notify Finding_Cards, newest first, filterable, with
the plugins whose runner ``cards`` switch is off hidden at once. Plugin-agnostic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from fse_ui.runner import card_publish, live_cards
from tests.conftest import ORIGIN, Console

S = 1_000_000_000
T0 = 1_791_800_000 * S


def card(card_id: str, action: str = "enter", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": card_id,
        "title": f"card {card_id}",
        "as_of_ns": T0,
        "action": action,
        "instrument": "MNQ",
        "symbol": None,
        "side": "long",
        "qty": 1,
        "entry": 20_000.25,
        "stop": 19_990.0,
        "targets": [20_010.0],
        "reason": "test",
        "ref": None,
        "tag": card_id,
        "result": None,
        "fields": [["EMA", "21000.5"]],
    }
    body.update(over)
    return body


def write_lines(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def make_run(runs_dir: Path) -> Path:
    run = runs_dir / "run-20261012T130000Z-0a1b2c3d"
    write_lines(run / "host_log.jsonl", [{"t_ns": T0, "kind": "heartbeat"}])
    write_lines(
        run / "plugins" / "alpha" / "decision_cards.jsonl",
        [
            {"t_ns": T0 + 1 * S, "plugin": "alpha", "kind": "entry", "card": card("a1")},
            {"t_ns": T0 + 3 * S, "plugin": "alpha", "kind": "skip", "card": card("a2", "skip")},
            {
                "t_ns": T0 + 5 * S,
                "plugin": "alpha",
                "kind": "result",
                "card": card(
                    "a1-result",
                    "result",
                    ref="a1",
                    result={"net": "-12.40", "exit_reason": "stop", "r": "-1.0", "ticks": -8},
                ),
            },
            {
                "t_ns": T0 + 6 * S,
                "plugin": "alpha",
                "kind": "result",
                "card": card("orphan", "result", ref="nope"),
            },
        ],
    )
    write_lines(
        run / "plugins" / "beta" / "decision_cards.jsonl",
        [{"t_ns": T0 + 2 * S, "plugin": "beta", "kind": "entry", "card": card("b1")}],
    )
    write_lines(
        run / "plugins" / "gamma" / "cards.jsonl",
        [
            {"kind": "premarket", "decision_time": T0, "symbol": "QQQ"},
            {"narration": "Prose.", "card": {"kind": "decision", "decision_time": T0 + 4 * S}},
        ],
    )
    return run


def test_every_plugins_cards_newest_first_with_results_joined(tmp_path: Path) -> None:
    make_run(tmp_path)
    out = live_cards(tmp_path, publish={})
    assert out["run_dir"] == "run-20261012T130000Z-0a1b2c3d"
    times = [c["t_ns"] for c in out["cards"]]
    assert times == sorted(times, reverse=True)
    ids = [(c["plugin"], c.get("card", {}).get("id") or c["title"]) for c in out["cards"]]
    assert ids == [
        ("alpha", "orphan"),
        ("gamma", "Finding_Card"),
        ("alpha", "a2"),
        ("beta", "b1"),
        ("alpha", "a1"),
        ("gamma", "Premarket card"),
    ]
    a1 = next(c for c in out["cards"] if c.get("card", {}).get("id") == "a1")
    assert a1["result"]["card"]["result"]["net"] == "-12.40"
    assert a1["result"]["t_ns"] == T0 + 5 * S
    assert a1["raw"]["card"]["id"] == "a1"
    finding = next(c for c in out["cards"] if c.get("title") == "Finding_Card")
    assert finding["format"] == "finding_card"
    assert finding["raw"]["narration"] == "Prose."
    assert {p["id"]: p["count"] for p in out["plugins"]} == {"alpha": 3, "beta": 1, "gamma": 2}


def test_filter_by_plugin_and_limit(tmp_path: Path) -> None:
    make_run(tmp_path)
    out = live_cards(tmp_path, publish={}, plugin="beta")
    assert [c["plugin"] for c in out["cards"]] == ["beta"]
    assert len(live_cards(tmp_path, publish={}, limit=2)["cards"]) == 2


def test_publish_false_hides_a_plugin_and_reports_it(tmp_path: Path) -> None:
    make_run(tmp_path)
    out = live_cards(tmp_path, publish={"alpha": False, "delta": False})
    assert {c["plugin"] for c in out["cards"]} == {"beta", "gamma"}
    plugins = {p["id"]: p for p in out["plugins"]}
    assert plugins["alpha"] == {"id": "alpha", "publish": False, "count": 3, "hidden": True}
    assert plugins["delta"]["hidden"] is True
    assert plugins["beta"]["hidden"] is False


def test_a_finding_card_file_outside_the_run_still_shows(tmp_path: Path) -> None:
    make_run(tmp_path)
    write_lines(
        tmp_path / "live-2026-10-12-deadbeef" / "cards.jsonl",
        [{"kind": "decision", "decision_time": T0 + 9 * S}],
    )
    out = live_cards(tmp_path, publish={})
    top = out["cards"][0]
    assert top["plugin"] is None
    assert top["format"] == "finding_card"
    assert top["source"] == "live-2026-10-12-deadbeef/cards.jsonl"


def test_no_run_is_empty(tmp_path: Path) -> None:
    assert live_cards(tmp_path, publish={}) == {
        "run_dir": None,
        "runner_config": None,
        "plugins": [],
        "cards": [],
    }


def test_same_utc_day_runs_merge_and_dedupe(tmp_path: Path) -> None:
    """A runner restart must not hide earlier plugins' cards from the same UTC day."""
    older = tmp_path / "run-20261015T120000Z-aaaaaaaa"
    newer = tmp_path / "run-20261015T135202Z-15d931b2"
    write_lines(older / "host_log.jsonl", [{"t_ns": T0, "kind": "heartbeat"}])
    write_lines(
        older / "plugins" / "alpha" / "decision_cards.jsonl",
        [{"t_ns": T0 + 1 * S, "plugin": "alpha", "kind": "entry", "card": card("m1")}],
    )
    write_lines(
        older / "plugins" / "bravo" / "decision_cards.jsonl",
        [{"t_ns": T0 + 2 * S, "plugin": "bravo", "kind": "skip", "card": card("r1", "skip")}],
    )
    write_lines(newer / "host_log.jsonl", [{"t_ns": T0 + 100 * S, "kind": "heartbeat"}])
    write_lines(
        newer / "plugins" / "charlie" / "decision_cards.jsonl",
        [{"t_ns": T0 + 3 * S, "plugin": "charlie", "kind": "entry", "card": card("s1")}],
    )
    # Same decision id after restart: keep the newer copy only.
    write_lines(
        newer / "plugins" / "alpha" / "decision_cards.jsonl",
        [
            {
                "t_ns": T0 + 4 * S,
                "plugin": "alpha",
                "kind": "info",
                "card": card("m1", "info", title="Order_Mode combine in use"),
            }
        ],
    )
    # Newest host_log mtime wins _latest (sub-second FS clocks can tie otherwise).
    newer_host = newer / "host_log.jsonl"
    newer_host.write_text(newer_host.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    out = live_cards(
        tmp_path,
        publish={"alpha": True, "bravo": True, "charlie": True, "delta": True, "playbook": True},
    )
    assert out["run_dir"] == newer.name
    by_plugin = {c["plugin"] for c in out["cards"]}
    assert by_plugin == {"alpha", "bravo", "charlie"}
    alpha_ids = [c["card"]["id"] for c in out["cards"] if c["plugin"] == "alpha"]
    assert alpha_ids == ["m1"]
    assert next(c for c in out["cards"] if c["card"]["id"] == "m1")["card"]["title"] == (
        "Order_Mode combine in use"
    )
    plugins = {p["id"]: p for p in out["plugins"]}
    assert plugins["delta"] == {
        "id": "delta",
        "publish": True,
        "count": 0,
        "hidden": False,
    }
    assert plugins["playbook"]["count"] == 0
    assert plugins["charlie"]["count"] == 1
    assert plugins["bravo"]["count"] == 1


def test_publish_lists_zero_count_plugins(tmp_path: Path) -> None:
    make_run(tmp_path)
    out = live_cards(tmp_path, publish={"alpha": True, "beta": True, "gamma": True, "idle": True})
    plugins = {p["id"]: p for p in out["plugins"]}
    assert plugins["idle"] == {"id": "idle", "publish": True, "count": 0, "hidden": False}


def _runner_yaml(console: Console) -> dict[str, Any]:
    text = console.get("/api/configs/runner/runner").json()["text"]
    data: dict[str, Any] = yaml.safe_load(text)
    return data


def test_the_endpoint_reads_the_cards_switch_from_the_runner_config(console: Console) -> None:
    settings = console.app.state.settings
    make_run(settings.runs_dir)
    data = _runner_yaml(console)
    first = data["plugins"][0]["id"]
    write_lines(
        settings.runs_dir
        / "run-20261012T130000Z-0a1b2c3d"
        / "plugins"
        / first
        / "decision_cards.jsonl",
        [{"t_ns": T0 + 7 * S, "plugin": first, "kind": "entry", "card": card("x1")}],
    )
    shown = console.get("/api/live/cards").json()
    assert shown["runner_config"] == "runner"
    assert first in {c["plugin"] for c in shown["cards"]}
    data["plugins"][0]["cards"] = False
    saved = console.post(
        "/api/configs/runner/runner",
        json={"text": yaml.safe_dump(data, sort_keys=False), "message": "cards off"},
    )
    assert saved.status_code == 200, saved.text
    hidden = console.get("/api/live/cards").json()
    assert first not in {c["plugin"] for c in hidden["cards"]}
    assert next(p for p in hidden["plugins"] if p["id"] == first)["hidden"] is True
    only = console.get("/api/live/cards", params={"plugin": "beta"}).json()
    assert [c["plugin"] for c in only["cards"]] == ["beta"]
    assert console.get("/api/live/cards", params={"plugin": "Bad-Id"}).status_code == 422
    name, publish = card_publish(console.app.state.db, console.app.state.configs)
    assert name == "runner"
    assert publish[first] is False


def test_the_endpoint_needs_a_session(console: Console) -> None:
    anon = TestClient(console.app, base_url=ORIGIN)
    assert anon.get("/api/live/cards").status_code == 401
