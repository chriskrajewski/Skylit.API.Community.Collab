"""Paper analytics API (design §6, C-1 to C-3, C-F7, reset route, journal health, NF-3)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fse.analytics.metrics import MetricsCfg, MetricsError, metrics_to_jsonable, summarize
from fse.live.host.journal import journal_trades
from fse.live.host.lock import RunnerLock
from fse.logio.canonical_json import to_jsonable

from fse_ui import api as api_mod
from fse_ui import paper
from fse_ui.runs import equity_curve
from tests.conftest import Console
from tests.paper_support import (
    MON,
    TUE,
    WED,
    account_state,
    bar,
    day_open,
    host_log,
    journal,
    lines_of,
    mk_trade,
    ny,
    open_trade,
    simple,
    stop_order,
    write_account,
    write_book,
    write_journal,
    write_marks,
    write_status,
)

INTERNAL = (
    "applied",
    "applied_prev",
    "marks",
    "last_bar_open",
    "open_pnl_day_start",
    "fingerprint",
    "mll_fingerprint",
)


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    paper._clear_caches()
    api_mod._RUNNER_CFG.clear()
    yield
    paper._clear_caches()


def dirs(console: Console) -> tuple[Path, Path]:
    s = console.app.state.settings
    return s.runs_dir, s.live_state_dir


def seed(console: Console) -> dict[str, Any]:
    """alpha on topstep_a (2 closed trades Wed, 1 open 2-lot), range implicit (1 trade)."""
    runs, ls = dirs(console)
    m1, m2 = simple("alpha", "s1", WED, 20), simple("alpha", "s2", WED, -10, hour=11)
    r1 = simple("bravo", "r1", WED, 8)
    lines = journal(runs, "run-a", [m1, m2], account="topstep_a")
    lines += journal(runs, "run-a", [r1])
    host_log(runs, "run-a", [day_open(MON), day_open(TUE), day_open(WED)])
    write_book(
        ls,
        "alpha",
        day_net="2.52",
        trades=[open_trade("alpha", "s3", WED, ny(WED, 12), 80_000, 2)],
        orders=[stop_order("alpha", "s3", WED, 79_960, 2, ny(WED, 12))],
        ring=[bar(ny(WED, 12, 5), 80_010)],
    )
    write_status(ls, "alpha")  # the host's exit fee: $0.62 per contract
    write_marks(ls, quote=(80_010, ny(WED, 12, 6)))
    write_book(ls, "bravo", day_net="2.76")
    view = {"equity": "50002.52", "blocks": ["account_no_trade_window"], "distance": {}}
    write_account(
        ls,
        account_state(
            "topstep_a",
            ["alpha"],
            day=WED,
            realized=Decimal("2.52"),
            balance=Decimal("50002.52"),
            unrealized=Decimal("8.76"),
            day_net=Decimal("2.52"),
            hwm=Decimal("50015.00"),
        ),
        view,
    )
    return {"alpha": [m1, m2], "bravo": [r1], "lines": lines}


# ---------------------------------------------------------------- C-1 today


def test_today_per_plugin_total_and_account(console: Console) -> None:
    seed(console)
    r = console.get("/api/paper/plugins")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"as_of_ns", "day", "plugins", "total", "skipped_lines"}
    assert body["day"] == "2026-10-14" and isinstance(body["as_of_ns"], int)
    m = body["plugins"]["alpha"]
    assert m["account"] == "topstep_a"
    # open P&L is main's live P&L: realized (-1.24 entry fees) + 10 ticks x 2 x $0.50
    # at the quote - 2 x $0.62 exit fees
    assert (m["realized"], m["unrealized"], m["cumulative_net"]) == ("2.52", "7.52", "2.52")
    assert m["open_positions"] == [
        {
            "tag": "s3",
            "instrument": "MNQ",
            "side": "long",
            "qty": 2,
            "entry_price": "20000.00",
            "open_pnl": "7.52",
        }
    ]
    assert m["working_orders"] == [
        {"tag": "s3", "leg": "stop", "type": "stop", "side": "sell", "qty": 2, "price": "19990.00"}
    ]
    assert (m["day_trades"], m["win_rate_today"]) == (2, 0.5)
    # runner.yaml (the console default) caps alpha at 16 contracts.
    assert m["caps"] == {"max_contracts": 16, "used": 2, "max_daily_loss": None, "day_net": "2.52"}
    assert m["blocks"] == ["account_no_trade_window"]
    assert m["price_source"] == {"MNQ": {"source": "quote", "t_ns": ny(WED, 12, 6)}}
    assert m["journal_failed"] is False
    rg = body["plugins"]["bravo"]
    assert (rg["account"], rg["realized"], rg["cumulative_net"]) == (
        "implicit:bravo",
        "2.76",
        "2.76",
    )
    assert body["total"] == {
        "realized": "5.28",
        "unrealized": "7.52",
        "day_trades": 3,
        "win_rate_today": 0.6667,
        "cumulative_net": "5.28",
    }
    accts = console.get("/api/paper/accounts").json()["accounts"]
    a = accts["topstep_a"]
    assert (a["implicit"], a["plugins"], a["status"]) == (False, ["alpha"], "active")
    # the account's figures are its rule engine's (account.json, at the 1-minute close)
    assert (a["balance"], a["equity"], a["drawdown"]) == ("50002.52", "50011.28", "3.72")
    assert a["starting_balance"] == "50000.00" and a["mll_floor"] == "48000.00"
    assert a["view"]["blocks"] == ["account_no_trade_window"] and a["nexus"] is None
    imp = accts["implicit:bravo"]
    assert imp["implicit"] is True and imp["starting_balance"] == "0.00"
    assert (imp["balance"], imp["status"], imp["mll_floor"], imp["view"]) == (
        "2.76",
        "active",
        None,
        None,
    )


def test_account_today_has_no_internal_fields(console: Console) -> None:
    seed(console)
    for a in console.get("/api/paper/accounts").json()["accounts"].values():
        assert not set(INTERNAL) & set(a), a


def test_nexus_status_is_shown_without_history(console: Console) -> None:
    seed(console)
    _, ls = dirs(console)
    status = {"version": 1, "enabled": True, "queue_depth": 0, "local": [1], "nexus": [2]}
    (ls / "accounts" / "topstep_a" / "nexus_status.json").write_text(json.dumps(status))
    nexus = console.get("/api/paper/accounts").json()["accounts"]["topstep_a"]["nexus"]
    assert nexus == {"version": 1, "enabled": True, "queue_depth": 0}


def test_open_pnl_is_the_live_pages_number(console: Console) -> None:
    """One computation: /api/paper and /live value a position with ``live_pnl``."""
    from fse_ui.runner import live_snapshot

    seed(console)
    runs, ls = dirs(console)
    m = console.get("/api/paper/plugins").json()["plugins"]["alpha"]
    live = next(p for p in live_snapshot(runs, ls)["plugins"] if p["id"] == "alpha")
    assert m["open_positions"][0]["open_pnl"] == live["pnl"]["positions"]["s3"]["open_net"]
    assert m["unrealized"] == live["pnl"]["day"]["open"] == "7.52"


def test_a_newer_1m_close_wins_over_the_quote(console: Console) -> None:
    seed(console)
    _, ls = dirs(console)
    write_marks(ls, quote=(80_010, ny(WED, 12, 6)), bar_close=(80_020, ny(WED, 12, 31)))
    m = console.get("/api/paper/plugins").json()["plugins"]["alpha"]
    assert m["price_source"]["MNQ"] == {"source": "1m_close", "t_ns": ny(WED, 12, 31)}
    assert m["open_positions"][0]["open_pnl"] == "17.52"  # 20 ticks x 2 x $0.50 - 2.48 fees


def test_a_position_without_a_mark_has_no_open_pnl(console: Console) -> None:
    seed(console)
    _, ls = dirs(console)
    (ls / "marks.json").unlink()
    body = console.get("/api/paper/plugins").json()
    m = body["plugins"]["alpha"]
    assert m["open_positions"][0]["open_pnl"] is None and m["unrealized"] is None
    assert m["price_source"] == {}
    assert body["total"]["unrealized"] is None
    assert body["plugins"]["bravo"]["unrealized"] == "0.00"  # flat: nothing to value


def test_a_book_on_an_earlier_day_realized_nothing_today(console: Console) -> None:
    seed(console)
    _, ls = dirs(console)
    # alpha's book was last rotated on Tuesday (its runner stopped); "old" is a removed
    # plugin whose book sits in the weekend-gap form. Both day_nets belong to past days.
    write_book(ls, "alpha", day=TUE, day_net="2.52", seq=2)
    write_book(ls, "old", day=None, day_net="-40.00")
    body = console.get("/api/paper/plugins").json()
    assert body["day"] == "2026-10-14"
    m, old, rg = (body["plugins"][p] for p in ("alpha", "old", "bravo"))
    assert (m["realized"], m["caps"]["day_net"], m["cumulative_net"]) == ("0.00", "0.00", "2.52")
    assert (old["realized"], old["caps"]) == ("0.00", None)
    assert rg["realized"] == "2.76"
    assert body["total"]["realized"] == "2.76"
    assert body["total"]["cumulative_net"] == "5.28"
    accts = console.get("/api/paper/accounts").json()["accounts"]
    assert accts["implicit:old"]["day_net"] == "0.00"
    assert accts["implicit:bravo"]["day_net"] == "2.76"


def test_as_of_ns_follows_the_marks_status_and_blocks_files(console: Console) -> None:
    import io
    import os

    from fse.live.state_store import BlockStore
    from fse.logio.log_writer import LogWriter
    from fse.secrets.env import EnvView

    seed(console)
    s = console.app.state.settings
    base = console.get("/api/paper/plugins").json()["as_of_ns"]
    later = base + 5_000_000_000
    os.utime(s.live_state_dir / "marks.json", ns=(later, later))
    assert console.get("/api/paper/plugins").json()["as_of_ns"] == later
    status = later + 5_000_000_000
    os.utime(s.live_state_dir / "plugins" / "alpha" / "status.json", ns=(status, status))
    assert console.get("/api/paper/plugins").json()["as_of_ns"] == status
    writer = LogWriter.from_env(EnvView({}, {}), stdout=io.StringIO(), stderr=io.StringIO())
    blocks = BlockStore(writer, s.live_state_dir / "plugins" / "bravo")
    blocks.clear()
    latest = status + 5_000_000_000
    os.utime(blocks.path, ns=(latest, latest))
    assert console.get("/api/paper/plugins").json()["as_of_ns"] == latest


def test_two_leg_trade_counts_once_in_win_rate(console: Console) -> None:
    runs, ls = dirs(console)
    t0 = ny(WED, 10)
    two = mk_trade(
        "alpha",
        "s1",
        WED,
        t0,
        80_000,
        [("tp1", t0 + 60 * 10**9, 80_020, 1), ("stop", t0 + 120 * 10**9, 79_970, 1)],
    )
    assert two.net < 0
    journal(runs, "run-a", [two, simple("alpha", "s2", WED, 12, hour=11)])
    write_book(ls, "alpha")
    m = console.get("/api/paper/plugins").json()["plugins"]["alpha"]
    assert (m["day_trades"], m["win_rate_today"]) == (2, 0.5)


# ---------------------------------------------------------------- C-1 history


def test_history_metrics_and_equity_equal_the_engine(console: Console) -> None:
    data = seed(console)
    rebuilt = journal_trades([x for x in data["lines"] if x["plugin"] == "alpha"])
    assert rebuilt == data["alpha"]  # journal -> Trade mapping, field by field
    q = "from=2026-10-12&to=2026-10-14"
    got = console.get(f"/api/paper/plugins/alpha/metrics?{q}").json()
    want = summarize(data["alpha"], [MON, TUE, WED], MetricsCfg())
    assert got["source"] == "journal" and got["skipped_lines"] == 0
    assert got["metrics"] == metrics_to_jsonable(want, places=6)
    eq = console.get(f"/api/paper/plugins/alpha/equity?{q}").json()
    assert {k: v for k, v in eq.items() if k != "skipped_lines"} == equity_curve(
        [to_jsonable(t) for t in data["alpha"]]  # type: ignore[misc]
    )
    total = console.get(f"/api/paper/total/metrics?{q}").json()["metrics"]
    every = summarize(data["alpha"] + data["bravo"], [MON, TUE, WED], MetricsCfg())
    assert total == metrics_to_jsonable(every, places=6)
    acct = console.get(f"/api/paper/accounts/topstep_a/metrics?{q}").json()["metrics"]
    assert acct == got["metrics"]
    imp = console.get(f"/api/paper/accounts/implicit:bravo/equity?{q}").json()
    assert imp["net_usd"] == str(data["bravo"][0].net)


def test_a_trade_entered_before_from_and_held_over_the_break(console: Console) -> None:
    runs, _ = dirs(console)
    held = simple("alpha", "h1", MON, 30, hour=17, minute=30)  # session Mon, closed on Tue
    write_journal(runs, "run-b", "alpha", lines_of(held, day=TUE))
    host_log(runs, "run-b", [day_open(TUE)])
    r = console.get("/api/paper/plugins/alpha/metrics?from=2026-10-13&to=2026-10-13")
    assert r.status_code == 200 and r.json()["metrics"]["trade_count"] == 1


def test_metrics_error_is_a_200_with_the_message(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fse.analytics.metrics as metrics_mod

    seed(console)

    def boom(*_: Any, **__: Any) -> Any:
        raise MetricsError("session 2026-10-14 is repeated")

    monkeypatch.setattr(metrics_mod, "summarize", boom)
    r = console.get("/api/paper/total/metrics?from=2026-10-12&to=2026-10-14")
    assert r.status_code == 200
    assert r.json() == {
        "source": "journal",
        "skipped_lines": 0,
        "metrics": None,
        "error": "session 2026-10-14 is repeated",
    }


def test_total_trades_merge_dedup_newest_first_and_limit(console: Console) -> None:
    runs, ls = dirs(console)
    trades = [simple("alpha", f"m{i}", WED, 10 + i, hour=9, minute=i * 3) for i in range(6)]
    trades += [simple("bravo", f"r{i}", WED, -5 - i, hour=9, minute=1 + i * 3) for i in range(6)]
    lines = journal(runs, "run-a", trades[:6], account="topstep_a")
    lines += journal(runs, "run-a", trades[6:])
    write_journal(runs, "run-b", "alpha", lines[:1])  # a replayed line: same line_id
    write_book(ls, "alpha")
    r = console.get("/api/paper/total/trades?from=2026-10-14&to=2026-10-14&limit=5").json()
    assert (r["total"], r["truncated"], len(r["trades"])) == (12, True, 5)
    exits = [t["exit_ns"] for t in r["trades"]]
    assert exits == sorted(exits, reverse=True)
    newest = sorted(lines, key=lambda x: (x["exit_ns"], x["line_id"]), reverse=True)[:5]
    assert [t["line_id"] for t in r["trades"]] == [x["line_id"] for x in newest]
    one = console.get("/api/paper/plugins/alpha/trades?from=2026-10-14&to=2026-10-14").json()
    assert (one["total"], one["truncated"], len(one["trades"])) == (6, False, 6)
    acct = console.get("/api/paper/accounts/topstep_a/trades?from=2026-10-14&to=2026-10-14")
    assert acct.json()["total"] == 6
    imp = console.get("/api/paper/accounts/implicit:bravo/trades?from=2026-10-14&to=2026-10-14")
    assert imp.json()["total"] == 6


# ---------------------------------------------------------------- C-3 validation


@pytest.mark.parametrize(
    "url",
    [
        "/api/paper/plugins/Bad/trades",
        "/api/paper/plugins/implicit:alpha/metrics",
        "/api/paper/accounts/Bad/equity",
        "/api/paper/plugins/alpha/trades?from=2026-13-01",
        "/api/paper/plugins/alpha/trades?to=20261014",
        "/api/paper/plugins/alpha/trades?from=2026-10-14&to=2026-10-13",
        "/api/paper/total/metrics?from=2025-10-12&to=2026-10-14",
        "/api/paper/total/trades?limit=0",
        "/api/paper/total/trades?limit=5001",
        "/api/paper/accounts/topstep_a/trades?limit=0",
    ],
)
def test_bad_input_is_422(console: Console, url: str) -> None:
    seed(console)
    assert console.get(url).status_code == 422, url


def test_a_366_day_span_is_allowed(console: Console) -> None:
    seed(console)
    r = console.get("/api/paper/total/trades?from=2025-10-13&to=2026-10-14")
    assert r.status_code == 200 and r.json()["total"] == 3


@pytest.mark.parametrize(
    "url",
    [
        "/api/paper/plugins/nosuch/trades",
        "/api/paper/accounts/nosuch/metrics",
        "/api/paper/accounts/implicit:nosuch/equity",
    ],
)
def test_unknown_ids_are_404(console: Console, url: str) -> None:
    seed(console)
    assert console.get(url).status_code == 404, url


# ---------------------------------------------------------------- C-F7 bad files


def test_corrupt_lines_are_counted_and_bad_files_never_500(console: Console) -> None:
    runs, ls = dirs(console)
    good = lines_of(simple("alpha", "s1", WED, 20))
    broken = {**good[0], "line_id": "x@1", "net": "not money"}
    write_journal(runs, "run-a", "alpha", [*good, broken], raw='not json\n{"kind": "trade"')
    write_book(ls, "alpha")
    (ls / "plugins" / "bravo").mkdir(parents=True)
    (ls / "plugins" / "bravo" / "plugin.json").write_text("{nope")
    (ls / "accounts" / "bad_acct").mkdir(parents=True)
    (ls / "accounts" / "bad_acct" / "account.json").write_text('{"account": 5}')
    body = console.get("/api/paper/plugins").json()
    assert body["skipped_lines"] == 3
    assert body["plugins"]["alpha"]["cumulative_net"] == "8.76"
    assert body["plugins"]["bravo"]["error"].startswith("plugin.json is unreadable")
    bad = console.get("/api/paper/accounts").json()["accounts"]["bad_acct"]
    assert bad["error"].startswith("account.json is unreadable") and bad["balance"] is None
    hist = console.get("/api/paper/plugins/alpha/trades?from=2026-10-14&to=2026-10-14").json()
    assert (hist["total"], hist["skipped_lines"]) == (1, 3)
    assert console.get("/api/paper/total/metrics").status_code == 200


def test_a_group_that_does_not_rebuild_is_skipped(console: Console) -> None:
    runs, ls = dirs(console)
    good = lines_of(simple("alpha", "s1", WED, 20))
    bad = lines_of(simple("alpha", "s2", WED, 10, hour=11))
    bad[0]["qty_at_entry"] = 7  # exits no longer sum to the entry: Trade refuses it
    write_journal(runs, "run-a", "alpha", good + bad)
    write_book(ls, "alpha")
    r = console.get("/api/paper/plugins/alpha/metrics?from=2026-10-14&to=2026-10-14").json()
    assert (r["skipped_lines"], r["metrics"]["trade_count"]) == (1, 1)


def test_missing_state_and_runs_dirs_are_empty(console: Console) -> None:
    body = console.get("/api/paper/plugins").json()
    # The console's runner.yaml lists host-brokered plugins; none has a book or a journal.
    assert all(
        p["realized"] == "0.00" and p["open_positions"] == [] for p in body["plugins"].values()
    )
    assert body["skipped_lines"] == 0 and body["total"]["day_trades"] == 0
    assert body["day"] is None
    accounts = console.get("/api/paper/accounts").json()["accounts"]
    assert all(a["implicit"] for a in accounts.values())
    r = console.get("/api/paper/total/trades").json()
    assert (r["total"], r["trades"]) == (0, [])


# ---------------------------------------------------------------- journal health


def failed(plugin: str) -> dict[str, Any]:
    return {"t_ns": 7, "kind": "journal_write_failed", "level": "error", "plugin": plugin}


def test_journal_failure_survives_500_heartbeats_until_recovered(console: Console) -> None:
    runs, ls = dirs(console)
    write_book(ls, "bravo")
    log = host_log(runs, "run-a", [failed("bravo")])
    host_log(runs, "run-a", [{"t_ns": i, "kind": "heartbeat"} for i in range(500)])
    rest = console.get("/api/paper/plugins").json()["plugins"]["bravo"]
    snap = console.get("/api/live").json()
    assert rest["journal_failed"] is True
    assert snap["paper"]["plugins"]["bravo"]["journal_failed"] is True
    assert not any(e.get("kind") == "journal_write_failed" for e in snap["host_log"])
    host_log(runs, "run-a", [{"t_ns": 9, "kind": "journal_write_recovered", "plugin": "bravo"}])
    assert console.get("/api/paper/plugins").json()["plugins"]["bravo"]["journal_failed"] is False
    assert log.is_file()


def test_journal_health_reads_only_new_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    log = host_log(runs, "run-a", [{"kind": "heartbeat"}] * 50)
    read: list[int] = []
    real = paper._read_from

    def counting(path: Path, offset: int) -> bytes:
        data = real(path, offset)
        read.append(len(data))
        return data

    monkeypatch.setattr(paper, "_read_from", counting)
    assert paper.journal_health(runs / "run-a") == {}
    assert read == [log.stat().st_size]
    line = (json.dumps(failed("bravo")) + "\n").encode()
    with log.open("ab") as fh:
        fh.write(line[:10])  # torn: waits
    assert paper.journal_health(runs / "run-a") == {}
    with log.open("ab") as fh:
        fh.write(line[10:])
    assert paper.journal_health(runs / "run-a") == {"bravo": {"failed": True, "failed_at_ns": 7}}
    assert read[1:] == [10, len(line)]  # the torn bytes are read again once complete
    assert paper.journal_health(runs / "run-a")["bravo"]["failed"] is True
    assert read[3:] == []  # unchanged file: nothing read
    log.write_text(json.dumps({"kind": "heartbeat"}) + "\n")  # truncated: rescan from 0
    assert paper.journal_health(runs / "run-a") == {}
    assert read[-1] == log.stat().st_size
    newer = host_log(runs, "run-b", [failed("alpha")])
    assert paper.journal_health(runs / "run-b") == {"alpha": {"failed": True, "failed_at_ns": 7}}
    assert read[-1] == newer.stat().st_size


# ---------------------------------------------------------------- reset route


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    real = asyncio.create_subprocess_exec

    async def record(*argv: str, **kw: Any) -> Any:
        calls.append(tuple(argv))
        return await real(*argv, **kw)

    monkeypatch.setattr(api_mod.asyncio, "create_subprocess_exec", record)
    return calls


def reset(console: Console, account: str, reason: str = "new combine") -> Any:
    return console.post(f"/api/paper/accounts/{account}/reset", json={"reason": reason})


@pytest.mark.parametrize(
    ("account", "reason"),
    [("Bad", "x"), ("implicit:alpha", "x"), ("topstep_a", ""), ("topstep_a", "x" * 201)],
)
def test_reset_bad_input_is_422_with_no_spawn(
    console: Console, spawned: list[tuple[str, ...]], account: str, reason: str
) -> None:
    seed(console)
    assert reset(console, account, reason).status_code == 422
    assert spawned == []


def test_reset_missing_account_dir_is_404(console: Console, spawned: list[Any]) -> None:
    assert reset(console, "topstep_a").status_code == 404
    assert spawned == []


@pytest.mark.parametrize("state", ["running", "starting"])
def test_reset_while_the_runner_runs_is_409_before_the_probe(
    console: Console, spawned: list[Any], monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    seed(console)
    probes: list[Path] = []
    monkeypatch.setattr(api_mod, "_lock_held", lambda p: probes.append(p) or False)

    async def status() -> dict[str, Any]:
        return {"state": state}

    monkeypatch.setattr(console.app.state.runner, "status", status)
    r = reset(console, "topstep_a")
    assert r.status_code == 409 and r.json()["detail"] == "Stop the runner first"
    assert probes == [] and spawned == []


def test_reset_while_the_lock_is_held_is_409(console: Console, spawned: list[Any]) -> None:
    seed(console)
    _, ls = dirs(console)
    lock = RunnerLock(ls)
    lock.acquire()
    try:
        r = reset(console, "topstep_a")
    finally:
        lock.release()
    assert r.status_code == 409 and spawned == []


def test_reset_cli_exit_3_is_409_and_exit_0_is_200(
    console: Console, spawned: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(console)
    _, ls = dirs(console)
    monkeypatch.setenv("STUB_EXIT", "3")
    r = reset(console, "topstep_a")
    assert r.status_code == 409 and r.json()["detail"]["exit_code"] == 3
    monkeypatch.setenv("STUB_EXIT", "0")
    r = reset(console, "topstep_a", "new combine")
    assert r.status_code == 200 and r.json()["exit_code"] == 0
    argv = spawned[-1]
    assert argv[1:] == (
        "account",
        "reset",
        "topstep_a",
        "--state-dir",
        str(ls),
        "--reason=new combine",
    )
    actions = [a["action"] for a in console.get("/api/audit").json()]
    assert actions[0] == "POST /api/paper/accounts/topstep_a/reset"


def test_existing_quick_callers_still_500_on_exit_1(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "1")
    assert console.post("/api/halt", json={"reason": "x"}).status_code == 500
    monkeypatch.setenv("STUB_EXIT", "3")
    assert console.post("/api/clear", json={}).status_code == 500


# ---------------------------------------------------------------- NF-3


def test_six_weeks_of_journals_answer_in_time(console: Console) -> None:
    runs, ls = dirs(console)
    days: list[date] = []
    d = date(2026, 8, 31)
    while len(days) < 30:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    for i, day in enumerate(days):
        run = f"run-{i:02d}"
        host_log(runs, run, [day_open(day)])
        for plugin in ("alpha", "bravo", "echo"):
            trades = [
                simple(
                    plugin, f"t{k}", day, (k % 7) - 3 or 5, hour=9 + k // 10, minute=(k % 10) * 5
                )
                for k in range(40)
            ]
            journal(runs, run, trades)
    write_book(ls, "alpha")
    url = f"/api/paper/total/metrics?from={days[0]}&to={days[-1]}"
    t0 = time.perf_counter()
    first = console.get(url)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    second = console.get(url)
    warm = time.perf_counter() - t0
    assert first.status_code == 200 and first.json()["metrics"]["trade_count"] == 3600
    assert second.json() == first.json()
    assert cold < 3.0, cold
    assert warm < 1.0, warm
