"""Live P&L (fse_ui.runner.live_pnl): positions from status.json valued at marks.json.

``open_net = realized + ticks x tick_value x open_qty - exit_fee x open_qty``;
the day total is today's closed net plus every open net. Plugin-agnostic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fse_ui.runner import live_digest, live_pnl, live_snapshot

S = 1_000_000_000
NOW = 1_791_800_000 * S


def position(**over: Any) -> dict[str, Any]:
    pos: dict[str, Any] = {
        "tag": "t1",
        "instrument": "MNQ",
        "side": "long",
        "qty": 2,
        "entry": 20_000.0,
        "entry_t": 80_000,
        "open_qty": 2,
        "realized": "-1.24",  # the entry fee: 2 x $0.62
        "fees": "1.24",
        "tick_value": "0.50",
        "exit_fee": "0.62",
    }
    pos.update(over)
    return pos


def status(*positions: dict[str, Any], net: str = "0", entries: int = 1, closed: int = 0) -> dict:
    return {
        "positions": list(positions),
        "today": {"net": net, "entries": entries, "closed": closed, "trades": entries},
    }


def marks(
    inst: str = "MNQ",
    *,
    quote_t: int | None = 80_010,
    quote_at: int = NOW - 2 * S,
    bar_t: int | None = None,
    bar_at: int = NOW - 30 * S,
) -> dict[str, Any]:
    quote = None if quote_t is None else {"price_t": quote_t, "received_ns": quote_at}
    bar = None if bar_t is None else {"price_t": bar_t, "close_ns": bar_at}
    return {"version": 1, "updated_ns": NOW, "marks": {inst: {"quote": quote, "bar": bar}}}


def test_long_mnq_after_entry_and_exit_fees() -> None:
    pnl = live_pnl(status(position()), marks(), NOW)
    assert pnl is not None
    p = pnl["positions"]["t1"]
    assert p["ticks"] == 10
    assert p["mark"] == 20_002.5
    assert p["mark_source"] == "quote"
    assert p["open_gross"] == "10.00"  # 10 ticks x $0.50 x 2
    assert p["open_net"] == "7.52"  # -1.24 + 10.00 - 2 x 0.62
    assert p["stale"] is False
    assert p["age_s"] == 2.0
    assert pnl["day"] == {
        "closed": "0.00",
        "open": "7.52",
        "total": "7.52",
        "entries": 1,
        "closed_trades": 0,
    }
    assert pnl["as_of_ns"] == NOW


def test_short_mes_losing() -> None:
    pos = position(
        instrument="MES",
        side="short",
        entry_t=24_000,
        open_qty=1,
        realized="-0.62",
        tick_value="1.25",
        exit_fee="0.62",
    )
    pnl = live_pnl(status(pos), marks("MES", quote_t=24_008), NOW)
    assert pnl is not None
    p = pnl["positions"]["t1"]
    assert p["ticks"] == -8
    assert p["open_gross"] == "-10.00"
    assert p["open_net"] == "-11.24"


def test_short_mnq_winning_and_long_mes_winning() -> None:
    short = position(side="short", open_qty=1, realized="-0.62")
    pnl = live_pnl(status(short), marks(quote_t=79_990), NOW)
    assert pnl is not None
    assert pnl["positions"]["t1"]["open_net"] == "3.76"  # 10 x 0.50 - 0.62 - 0.62
    long_mes = position(instrument="MES", entry_t=24_000, open_qty=3, realized="-1.86")
    long_mes["tick_value"] = "1.25"
    pnl = live_pnl(status(long_mes), marks("MES", quote_t=24_004), NOW)
    assert pnl is not None
    assert pnl["positions"]["t1"]["open_net"] == "11.28"  # 4 x 1.25 x 3 - 1.86 - 1.86


def test_a_partial_exit_is_in_realized() -> None:
    # 2 long, tp1 sold 1 at +8 ticks: realized = 4.00 - 1.24 entry - 0.62 exit = 2.14
    pos = position(open_qty=1, realized="2.14", fees="1.86")
    pnl = live_pnl(status(pos), marks(quote_t=80_004), NOW)
    assert pnl is not None
    assert pnl["positions"]["t1"]["open_net"] == "3.52"  # 2.14 + 2.00 - 0.62


def test_no_exit_fee_counts_as_zero() -> None:
    pnl = live_pnl(status(position(exit_fee=None)), marks(), NOW)
    assert pnl is not None
    assert pnl["positions"]["t1"]["open_net"] == "8.76"


def test_a_quote_older_than_10_s_is_stale() -> None:
    fresh = live_pnl(status(position()), marks(quote_at=NOW - 9 * S), NOW)
    old = live_pnl(status(position()), marks(quote_at=NOW - 11 * S), NOW)
    assert fresh is not None and old is not None
    assert fresh["positions"]["t1"]["stale"] is False
    assert old["positions"]["t1"]["stale"] is True
    # stale still values the position
    assert old["positions"]["t1"]["open_net"] == "7.52"


def test_the_newer_1m_close_wins_over_an_older_quote() -> None:
    m = marks(quote_t=80_010, quote_at=NOW - 90 * S, bar_t=80_020, bar_at=NOW - 30 * S)
    pnl = live_pnl(status(position()), m, NOW)
    assert pnl is not None
    p = pnl["positions"]["t1"]
    assert p["mark_source"] == "1m_close"
    assert p["ticks"] == 20
    assert p["stale"] is False  # a 1m close is stale after 120 s
    assert p["mark_received_ns"] == NOW - 30 * S
    m = marks(quote_t=None, bar_t=80_000, bar_at=NOW - 121 * S)
    only_bar = live_pnl(status(position()), m, NOW)
    assert only_bar is not None
    assert only_bar["positions"]["t1"]["mark_source"] == "1m_close"
    assert only_bar["positions"]["t1"]["stale"] is True


def test_no_marks_is_stale_with_no_pnl() -> None:
    pnl = live_pnl(status(position(), net="-12.48", closed=1), None, NOW)
    assert pnl is not None
    p = pnl["positions"]["t1"]
    assert p["stale"] is True
    assert p["mark"] is None and p["ticks"] is None and p["open_net"] is None
    assert pnl["day"]["closed"] == "-12.48"
    assert pnl["day"]["open"] is None
    assert pnl["day"]["total"] is None
    other = live_pnl(status(position()), marks("MES"), NOW)
    assert other is not None and other["positions"]["t1"]["open_net"] is None


def test_day_total_is_closed_plus_every_open_position() -> None:
    a = position()
    b = position(tag="t2", side="short", open_qty=1, realized="-0.62")
    pnl = live_pnl(status(a, b, net="-12.48", entries=3, closed=1), marks(), NOW)
    assert pnl is not None
    assert pnl["positions"]["t2"]["open_net"] == "-6.24"  # -10 x 0.50 - 0.62 - 0.62
    assert pnl["day"] == {
        "closed": "-12.48",
        "open": "1.28",
        "total": "-11.20",
        "entries": 3,
        "closed_trades": 1,
    }


def test_flat_and_unbooked_plugins() -> None:
    flat = live_pnl(status(net="25.5", entries=2, closed=2), marks(), NOW)
    assert flat is not None
    assert flat["positions"] == {}
    assert flat["day"]["total"] == "25.50"
    # an older status.json: only the deprecated trades count
    old = live_pnl({"positions": [], "today": {"net": "0", "trades": 4}}, None, NOW)
    assert old is not None
    assert old["day"]["entries"] == 4 and old["day"]["closed_trades"] is None
    # a self-brokered plugin has no host book
    assert live_pnl({"positions": [], "today": None}, marks(), NOW) is None
    assert live_pnl(None, marks(), NOW) is None


def test_the_snapshot_adds_pnl_and_marks(tmp_path: Path) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    d = state / "plugins" / "p1"
    d.mkdir(parents=True)
    runs.mkdir()
    (d / "status.json").write_text(json.dumps(status(position())))
    (state / "marks.json").write_text(json.dumps(marks()))
    snap = live_snapshot(runs, state, now_ns=NOW)
    [item] = snap["plugins"]
    assert item["pnl"]["positions"]["t1"]["open_net"] == "7.52"
    assert snap["marks"]["marks"]["MNQ"]["quote"]["price_t"] == 80_010


def test_the_digest_ignores_the_clock_but_not_a_new_mark(tmp_path: Path) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    d = state / "plugins" / "p1"
    d.mkdir(parents=True)
    runs.mkdir()
    (d / "status.json").write_text(json.dumps(status(position())))
    (state / "marks.json").write_text(json.dumps(marks(quote_at=NOW - 8 * S)))
    first = live_digest(live_snapshot(runs, state, now_ns=NOW))
    # 5 s later: the quote went stale, but nothing changed on disk: no resend
    assert live_digest(live_snapshot(runs, state, now_ns=NOW + 5 * S)) == first
    (state / "marks.json").write_text(json.dumps(marks(quote_t=80_012)))
    assert live_digest(live_snapshot(runs, state, now_ns=NOW + 5 * S)) != first
