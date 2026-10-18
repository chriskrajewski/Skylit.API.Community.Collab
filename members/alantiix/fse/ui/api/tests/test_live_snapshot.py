"""``live_snapshot``'s additive ``paper``/``accounts`` maps (design §6.6, C-4, C-F5, NF-3).

Since the merge with main the snapshot is main's (``status.json`` per plugin, live
P&L over ``marks.json``, no raw ``plugin.json``: ``/api/live/plugins/{id}/raw``
serves that); ``paper`` and ``accounts`` are added on top.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse_ui import paper
from fse_ui.runner import MAX_RAW_BYTES, live_snapshot, plugin_raw
from tests.conftest import ORIGIN, Console
from tests.paper_support import (
    WED,
    account_state,
    bar,
    day_open,
    host_log,
    journal,
    ny,
    open_trade,
    simple,
    stop_order,
    write_account,
    write_book,
    write_marks,
    write_status,
)

MAIN_KEYS = {
    "run_dir",
    "now_ns",
    "host_log",
    "heartbeat",
    "skylit",
    "faults",
    "plugins",
    "marks",
    "blocks",
}
"""The keys of main's live snapshot; ``paper`` and ``accounts`` are the branch's."""


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    paper._clear_caches()
    yield
    paper._clear_caches()


def seed(runs: Path, live_state: Path, plugins: tuple[str, ...] = ("alpha", "bravo")) -> None:
    host_log(runs, "run-a", [day_open(WED), {"t_ns": ny(WED, 9), "kind": "heartbeat"}])
    for i, pid in enumerate(plugins):
        journal(
            runs, "run-a", [simple(pid, "s1", WED, 10 + i), simple(pid, "s2", WED, -6, hour=11)]
        )
        write_book(
            live_state,
            pid,
            day_net="1.00",
            trades=[open_trade(pid, "s3", WED, ny(WED, 12), 80_000, 1)],
            orders=[stop_order(pid, "s3", WED, 79_960, 1, ny(WED, 12))],
            ring=[bar(ny(WED, 12, 1) + k * 60 * 10**9, 80_004 + k) for k in range(30)],
        )
    write_account(
        live_state,
        account_state("topstep_a", [plugins[0]], day=WED, unrealized=Decimal("1.50")),
        {"equity": "50001.50", "blocks": [], "distance": {"mll": "2001.50"}},
    )


def test_paper_and_accounts_are_keyed_maps_without_a_clock(tmp_path: Path) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    seed(runs, state)
    snap = live_snapshot(runs, state, now_ns=ny(WED, 13))
    assert set(snap) == MAIN_KEYS | {"paper", "accounts"}
    assert set(snap["paper"]) == {"day", "plugins", "total"}
    assert snap["paper"]["day"] == "2026-10-14"
    assert set(snap["paper"]["plugins"]) == {"alpha", "bravo"}
    assert snap["paper"]["plugins"]["bravo"]["id"] == "bravo"
    assert set(snap["accounts"]) == {"topstep_a", "implicit:bravo"}
    assert snap["paper"]["plugins"]["alpha"]["account"] == "topstep_a"
    ours = {"paper": snap["paper"], "accounts": snap["accounts"]}
    assert "as_of_ns" not in json.dumps(ours)
    again = live_snapshot(runs, state, now_ns=ny(WED, 14))
    assert json.dumps(ours, sort_keys=True, default=str) == json.dumps(
        {"paper": again["paper"], "accounts": again["accounts"]}, sort_keys=True, default=str
    )


def test_the_old_positional_call_still_works(tmp_path: Path) -> None:
    snap = live_snapshot(tmp_path / "runs", tmp_path / "live-state")
    assert snap["paper"] == {
        "day": None,
        "plugins": {},
        "total": {
            "realized": "0.00",
            "unrealized": "0.00",
            "day_trades": 0,
            "win_rate_today": None,
            "cumulative_net": "0.00",
        },
    }
    assert snap["accounts"] == {}


def test_paper_open_pnl_is_the_plugin_cards_live_pnl(tmp_path: Path) -> None:
    """One P&L computation: ``paper`` values open positions with the same ``live_pnl``
    over ``marks.json`` as the plugin's card (``plugins[].pnl``)."""
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    seed(runs, state, ("alpha",))
    write_status(state, "alpha")
    write_marks(state, quote=(80_012, ny(WED, 13)))
    snap = live_snapshot(runs, state, now_ns=ny(WED, 13))
    today = snap["paper"]["plugins"]["alpha"]
    card = _book_item(snap, "alpha")["pnl"]
    assert today["open_positions"][0]["open_pnl"] == card["positions"]["s3"]["open_net"]
    # 1 lot: -0.62 entry fee + 12 ticks x $0.50 - 0.62 exit fee
    assert today["unrealized"] == card["day"]["open"] == "4.76"
    assert today["price_source"]["MNQ"] == {"source": "quote", "t_ns": ny(WED, 13)}


def _book_item(snap: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in snap["plugins"] if p["id"] == pid)


def test_a_book_over_64_kib_because_of_its_ring_is_never_dropped(tmp_path: Path) -> None:
    """E-1 under main's Live page: the snapshot carries no raw book at all, so no book
    is dropped for its size; the raw state (ring included) comes from ``plugin_raw``,
    and today's numbers still read the book."""
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    ring = [bar(ny(WED, 1) + k * 60 * 10**9, 80_000 + k % 50) for k in range(900)]
    path = write_book(
        state,
        "alpha",
        day_net="1.00",
        trades=[open_trade("alpha", "s3", WED, ny(WED, 12), 80_000, 1)],
        ring=ring,
        seq=7,
    )
    assert path.stat().st_size > 64 * 1024
    snap = live_snapshot(runs, state)
    item = _book_item(snap, "alpha")
    assert "book" not in item and "book_dropped" not in item and item["has_raw"] is True
    assert snap["paper"]["plugins"]["alpha"]["realized"] == "1.00"
    assert len(snap["paper"]["plugins"]["alpha"]["open_positions"]) == 1
    raw = plugin_raw(state, "alpha")
    assert raw is not None and raw["state"] == json.loads(path.read_text())


def test_a_plugin_json_over_the_raw_limit_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fse_ui import runner

    state = tmp_path / "live-state"
    path = write_book(state, "alpha", day_net="1.00")
    assert path.stat().st_size <= MAX_RAW_BYTES
    monkeypatch.setattr(runner, "MAX_RAW_BYTES", path.stat().st_size - 1)
    raw = plugin_raw(state, "alpha")
    assert raw is not None and raw["state"] is None


def test_a_plugin_json_over_8_mib_is_an_error_in_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    path = write_book(state, "alpha", day_net="1.00")
    size = path.stat().st_size
    monkeypatch.setattr(paper, "MAX_STATE_READ_BYTES", size - 1)
    item = live_snapshot(runs, state)["paper"]["plugins"]["alpha"]
    assert item["error"] == f"plugin.json is {size} bytes"


def test_three_plugins_add_at_most_10_kb(tmp_path: Path) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    seed(runs, state, ("alpha", "bravo", "echo"))
    snap = live_snapshot(runs, state)
    added = len(json.dumps({"paper": snap["paper"], "accounts": snap["accounts"]}))
    assert added <= 10 * 1024, added


def test_ws_payload_has_the_new_keys_and_keeps_the_old_ones(console: Console) -> None:
    s = console.app.state.settings
    seed(s.runs_dir, s.live_state_dir)
    rest = console.get("/api/live").json()
    cookie = f"fse_session={console.client.cookies.get('fse_session')}"
    with console.client.websocket_connect(
        "/api/ws/live", headers={"origin": ORIGIN, "cookie": cookie}
    ) as ws:
        snap: dict[str, Any] = ws.receive_json()
    assert set(snap) == MAIN_KEYS | {"paper", "accounts", "runner"}
    for k in MAIN_KEYS:
        assert type(snap[k]) is type(rest[k]), k
    assert snap["paper"]["plugins"]["alpha"]["realized"] == "1.00"
    assert snap["accounts"]["topstep_a"]["unrealized"] == "1.50"
    assert snap["paper"] == rest["paper"] and snap["accounts"] == rest["accounts"]
    old = live_snapshot(s.runs_dir, s.live_state_dir)
    assert [p["id"] for p in snap["plugins"]] == [p["id"] for p in old["plugins"]]


def _order_mode_line(path: Path, mode: str, *, filler: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "t_ns": 7,
        "kind": "order_mode",
        "level": "warning",
        "plugin": path.parent.name,
        "requested": "practice",
        "mode": mode,
        "failures": ["the start command has no --plugin-orders flag"],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
        for i in range(filler):
            fh.write(json.dumps({"t_ns": 8 + i, "kind": "bar", "plugin": "alpha"}) + "\n")


def test_order_mode_comes_from_the_plugin_log(tmp_path: Path) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    seed(runs, state)
    log = runs / "run-a" / "plugins" / "alpha" / "plugin_log.jsonl"
    _order_mode_line(log, "paper", filler=200)
    snap = live_snapshot(runs, state)
    by_id = {p["id"]: p for p in snap["plugins"]}
    assert by_id["alpha"]["order_mode"] == {
        "requested": "practice",
        "mode": "paper",
        "failures": ["the start command has no --plugin-orders flag"],
        "t_ns": 7,
    }
    assert all(e.get("kind") != "order_mode" for e in by_id["alpha"]["log"])  # the tail misses it
    assert by_id["bravo"]["order_mode"] is None


def test_the_order_mode_scan_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    seed(runs, state)
    log = runs / "run-a" / "plugins" / "alpha" / "plugin_log.jsonl"
    _order_mode_line(log, "practice", filler=100)
    monkeypatch.setattr("fse_ui.runner.MAX_RAW_BYTES", 2048)
    assert log.stat().st_size > 2048
    by_id = {p["id"]: p for p in live_snapshot(runs, state)["plugins"]}
    assert by_id["alpha"]["order_mode"] is None
    _order_mode_line(log, "practice", filler=3)
    by_id = {p["id"]: p for p in live_snapshot(runs, state)["plugins"]}
    assert by_id["alpha"]["order_mode"]["mode"] == "practice"
