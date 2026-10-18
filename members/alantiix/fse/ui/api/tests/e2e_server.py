"""The API for the Playwright smoke suite (``ui/web/e2e``), started by its webServer.

A temp ``FSE_UI_HOME`` with two backtest runs (one with its report under
``report/``), the toy ``e2e_plugin`` state folder (waiting), ``e2e_live`` in a
trade valued at ``marks.json`` (which a thread rewrites every second as the host
does), paper books for ``plugin_a`` and ``plugin_b`` with their ``status.json``,
journals and the ``topstep_a`` account (real codecs), one runner run folder
(host log, journals, plugin logs and decision cards in ``decision_cards.jsonl``),
two daily reports (one ``.partial``), a stub ``fse`` (prints its argv; ``halt``/``clear``
write and remove the engine's halt file), dev login, and the built SPA from
``../web/dist``. The temp folder is removed on exit. Not a pytest module (no
``test_`` prefix).

    python tests/e2e_server.py --port 18765 [--root-file /tmp/fse-e2e-root.txt]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import textwrap
import threading
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # for ``fixtures`` when run as a script

import uvicorn  # noqa: E402
from fse.engine.types import Trade  # noqa: E402
from fse.live.host.accounts import PaperAccount  # noqa: E402
from fse.live.host.config import AccountEntry  # noqa: E402

from fixtures import backtest_run  # noqa: E402
from fse_ui.app import create_app  # noqa: E402
from fse_ui.runs import LocalArtifactStore, run_metrics  # noqa: E402
from fse_ui.settings import Settings  # noqa: E402
from paper_support import (  # noqa: E402
    HALF_RT,
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
)

PLUGIN_ID = "e2e_plugin"
LIVE_PLUGIN_ID = "e2e_live"
MIN_NS = 60 * 1_000_000_000
RUN_NAME = "run-20261012T130000Z-0a1b2c3d"
"""The one runner run folder: the newest run, so /live, /analytics and Cards all read it."""

STUB_FSE = textwrap.dedent(
    """\
    #!{python}
    import sys
    from pathlib import Path
    from fse.live.state_store import halt_file

    args = sys.argv[1:]
    print("stub argv:", args, flush=True)

    def state_dir():
        return Path(args[args.index("--state-dir") + 1])

    if args[:1] == ["halt"]:
        f = halt_file(state_dir())
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("halted from the e2e stub\\n")
    elif args[:1] == ["clear"]:
        halt_file(state_dir()).unlink(missing_ok=True)
    elif args[:1] == ["accounts"]:
        print(
            '{{"kind":"fse_accounts","accounts":['
            '{{"id":"101","name":"PRAC-FAKE-1","can_trade":true,"role":""}},'
            '{{"id":"202","name":"50KTC-FAKE-2","can_trade":true,"role":""}}],'
            '"diagnostics":[]}}',
            flush=True,
        )
    else:
        for i in range(3):
            print(f"stub line {{i}}", flush=True)
    sys.exit(0)
    """
)


def _globex_today() -> date:
    from fse_ui.paper import globex_today

    return globex_today(time.time_ns())


def _seed_paper(data: Path, now_ns: int) -> None:
    """Paper books, an account, a host log and journals written with the engine's own
    codecs and journal writer (design §13), so /live and /analytics show real numbers.

    Days are the current Globex day (the last closed one in the weekend gap) and the two
    before it, so the default history range holds every seeded trade.
    """
    from fse.live.host.dayclock import previous_globex_day

    from fse_ui.paper import globex_today

    runs, live_state = data / "runs", data / "live-state"
    today = globex_today(now_ns)
    days = [today]
    for _ in range(2):
        days.insert(0, previous_globex_day(days[0]).day)
    ticks = (24, -12, 16, -8, 30, -20, 10, 6, -16, 40)
    trades: dict[str, list[Trade]] = {"plugin_a": [], "plugin_b": []}
    for pid, offset in (("plugin_a", 0), ("plugin_b", 3)):
        for i in range(40):
            day = days[i % 3]
            hour, minute = 9 + (i // 3) % 7, (i * 7) % 50
            t = ticks[(i + offset) % len(ticks)]
            trades[pid].append(simple(pid, f"s{i:02d}", day, t, hour=hour, minute=minute))
    journal(runs, RUN_NAME, trades["plugin_a"], account="topstep_a")
    journal(runs, RUN_NAME, trades["plugin_b"])
    nets = {p: [t.net for t in ts if t.setup_key.session == today] for p, ts in trades.items()}
    host_log(
        runs,
        RUN_NAME,
        [
            *(
                {
                    "t_ns": now_ns - 60 * 10**9,
                    "kind": "plugin_loaded",
                    "level": "info",
                    "plugin": p,
                    "module": f"e2e.{p}",
                }
                for p in ("plugin_a", "plugin_b")
            ),
            {**day_open(today), "t_ns": now_ns - 30 * 10**9},
            {
                "t_ns": now_ns,
                "kind": "heartbeat",
                "level": "info",
                "connected": True,
                "transport": "stub",
                "subscription": "e2e",
            },
        ],
    )
    entry_ns = ny(today, 12)
    write_book(
        live_state,
        "plugin_a",
        day=today,
        day_net=format(sum(nets["plugin_a"], Decimal(0)), ".2f"),
        trades=[open_trade("plugin_a", "s99", today, entry_ns, 80_000, 2)],
        orders=[stop_order("plugin_a", "s99", today, 79_960, 2, entry_ns)],
        ring=[bar(entry_ns + k * 60 * 10**9, 80_004 + k) for k in range(5)],
    )
    write_book(
        live_state, "plugin_b", day=today, day_net=format(sum(nets["plugin_b"], Decimal(0)), ".2f")
    )
    entries = {p: sum(1 for t in ts if t.setup_key.session == today) for p, ts in trades.items()}
    for pid, label in (("plugin_a", "Plugin A"), ("plugin_b", "Plugin B")):
        _write_status(live_state, pid, label, now_ns, entries[pid])
    realized = sum((t.net for t in trades["plugin_a"]), Decimal(0))
    state = account_state(
        "topstep_a",
        ["plugin_a"],
        day=today,
        realized=realized,
        balance=Decimal("50000.00") + realized,
        day_net=sum(nets["plugin_a"], Decimal(0)),
        # the rule engine's open P&L at the last 1-minute close (8 ticks x 2 x $0.50 - 1.24
        # entry fees): what MLL/DLL are evaluated on, not the live mark
        unrealized=Decimal("6.76"),
        hwm=Decimal("50000.00") + max(realized, Decimal(0)),
        eod_high=Decimal("50000.00") + max(realized, Decimal(0)),
        trading_days=2,
    )
    rules = AccountEntry.model_validate({"id": "topstep_a", "preset": "topstep_50k_combine"}).rules
    view = {**PaperAccount(state, rules).view(exposure_micros=20), "blocks": []}
    write_account(live_state, state, view)


def _write_status(live_state: Path, pid: str, label: str, now_ns: int, entries: int) -> None:
    """The host's ``status.json`` of a seeded book, built by the host's own ``build_status``
    from the decoded ``plugin.json`` (main's Live card and live P&L read it)."""
    from dataclasses import replace

    from fse.live.host.broker import BROKER_CODEC
    from fse.live.host.status import build_status

    d = live_state / "plugins" / pid
    state = BROKER_CODEC.decode(json.loads((d / "plugin.json").read_text())["book"])
    state = replace(state, day_trades=entries, day_closed=entries)
    doc = build_status(
        plugin_id=pid,
        label=label,
        module=f"e2e.{pid}",
        brokerage="host",
        now=now_ns,
        faulted=None,
        blocks=(),
        state=state,
        day=None if state.day is None else state.day.isoformat(),
        next_wake_ns=None,
        recent=[],
        last_log=None,
        plugin_status=None,
        exit_fee=lambda _instrument: HALF_RT,
    )
    (d / "status.json").write_text(json.dumps(doc))


def _daily_report(day: str, *, partial: bool) -> tuple[dict[str, Any], str]:
    """A daily-check report in its own format (sections, cumulative, PARTIAL marker)."""
    later = "SKIPPED" if partial else "OK"
    cumulative = {
        "status": "OK",
        "since": "2026-10-12",
        "so_far": partial,
        "plugins": {
            "plugin_a": {
                "net_cents": 4126,
                "trades": 14,
                "wins": 8,
                "win_rate": 0.5714,
                "max_dd_cents": 2124,
                "sources": {"journal": 1, "csv": 0},
            },
            "plugin_b": {
                "net_cents": 1876,
                "trades": 13,
                "wins": 7,
                "win_rate": 0.5385,
                "max_dd_cents": 1062,
                "sources": {"journal": 1, "csv": 0},
            },
        },
        "accounts": {"topstep_a": {"plugins": ["plugin_a"], "status": "active"}},
        "go_no_go": {"net_cents": 4126, "line_cents": -500000, "status": "OK"},
    }
    sections: dict[str, Any] = {
        "Host health": {"status": "OK"},
        "Plugin A": {"status": later},
        "Plugin B": {"status": later},
        "Plugin C": {"status": later},
        "Per-fill slippage": {"status": "OK"},
        "Cumulative since forward-test start": cumulative,
    }
    summary: dict[str, Any] = {"day": day, "exit": 0, "sections": sections}
    if partial:
        summary |= {"partial": True, "window_end_ns": ny(date.fromisoformat(day), 11)}
    so_far = " so far" if partial else ""
    md = [
        f"# FSE paper daily check {day}" + (" (PARTIAL)" if partial else ""),
        "",
        "Result: **OK** (exit 0). Seeded by the e2e server.",
        "",
        *(["**PARTIAL day**: the day is still in progress.", ""] if partial else []),
        "| section | status |",
        "|---|---|",
        *(f"| {title} | {s['status']} |" for title, s in sections.items()),
        "",
        "## Cumulative since forward-test start: OK",
        "",
        f"Since Globex day 2026-10-12 through {day}{so_far}.",
        "",
        "| plugin | net | trades | win rate | max drawdown | days from journal / csv |",
        "|---|---|---|---|---|---|",
        "| plugin_a | $41.26 | 14 | 57.14% | $21.24 | 1 / 0 |",
        "| plugin_b | $18.76 | 13 | 53.85% | $10.62 | 1 / 0 |",
        "",
        f"Go/no-go: cumulative net $41.26{so_far} is above the -$5,000.00 line: OK.",
    ]
    return summary, "\n".join(md) + "\n"


def prepare(root: Path, port: int) -> None:
    home = root / "home"
    data = home / ".skylit-fse"
    runs = data / "runs"
    backtest_run(runs, "backtest-e2e", ["2", "-1", "0.5"])
    # fse report writes report/report.{json,md}: the Runs Report tab must find it (G-2).
    sub = backtest_run(runs, "backtest-e2e-sub", ["1", "-0.5", "2", "-1"])
    metrics = run_metrics(LocalArtifactStore(runs), "backtest-e2e-sub")["metrics"]
    (sub / "report").mkdir()
    (sub / "report" / "report.json").write_text(json.dumps({"metrics": metrics}))
    (sub / "report" / "report.md").write_text(
        "# Report backtest-e2e-sub\n\nWritten under report/ by fse report.\n"
    )
    plugin = data / "live-state" / "plugins" / PLUGIN_ID
    plugin.mkdir(parents=True)
    # real BROKER_CODEC books, so the paper analytics read every plugin (no "unreadable")
    write_book(data / "live-state", PLUGIN_ID, day=_globex_today())
    status = {
        "version": 1,
        "plugin": PLUGIN_ID,
        "label": "E2E plugin",
        "module": "tests:E2E",
        "brokerage": "host",
        "updated_ns": 1,
        "day": "2026-10-12",
        "phase": "waiting",
        "phase_text": "Waiting for its next setup",
        "blocks": [],
        "next_wake_ns": None,
        "positions": [],
        "working": [],
        "today": {"net": "0", "trades": 0},
        "recent": [],
        "last_log": None,
        "plugin_status": {
            "summary": "Waiting for the 10:00 entry",
            "next": {"at_ns": None, "text": "Enter long at 10:00 ET"},
        },
    }
    (plugin / "status.json").write_text(json.dumps(status))
    live_plugin(data / "live-state")
    runner_run(data / "runs")
    _seed_paper(data, time.time_ns())
    daily = data / "daily"
    daily.mkdir()
    for stem, partial in (("2026-10-12", False), ("2026-10-13.partial", True)):
        summary, md = _daily_report(stem.removesuffix(".partial"), partial=partial)
        (daily / f"{stem}.json").write_text(json.dumps(summary, indent=1) + "\n")
        (daily / f"{stem}.md").write_text(md)
    stub = root / "bin" / "fse"
    stub.parent.mkdir()
    stub.write_text(STUB_FSE.format(python=sys.executable))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    os.environ.update(
        {
            "FSE_UI_HOME": str(home),
            "FSE_UI_DEV_AUTH": "1",
            "FSE_UI_ENV": "local",
            "FSE_UI_BIND_HOST": "127.0.0.1",
            "FSE_UI_PORT": str(port),
            "FSE_UI_PUBLIC_URL": f"http://127.0.0.1:{port}",
            "FSE_UI_STATIC_DIR": str((HERE / ".." / ".." / "web" / "dist").resolve()),
            "FSE_UI_FSE_BIN": str(stub),
        }
    )


def live_plugin(state: Path) -> None:
    """A host-brokered plugin long 2 MNQ at 20,000.00, with a plan and recent orders."""
    now = time.time_ns()
    d = state / "plugins" / LIVE_PLUGIN_ID
    d.mkdir(parents=True)
    write_book(
        state,
        LIVE_PLUGIN_ID,
        day=_globex_today(),
        day_net="-12.48",
        trades=[open_trade(LIVE_PLUGIN_ID, "e2e-1", _globex_today(), now - 12 * MIN_NS, 80_000, 2)],
    )
    position = {
        "tag": "e2e-1",
        "instrument": "MNQ",
        "side": "long",
        "qty": 2,
        "entry": 20_000.0,
        "stop": 19_990.0,
        "targets": [20_020.0],
        "opened_ns": now - 12 * MIN_NS,
        "entry_t": 80_000,
        "open_qty": 2,
        "realized": "-1.24",
        "fees": "1.24",
        "tick_value": "0.50",
        "exit_fee": "0.62",
    }
    status = {
        "version": 1,
        "plugin": LIVE_PLUGIN_ID,
        "label": "E2E live P&L",
        "module": "tests:E2ELive",
        "brokerage": "host",
        "updated_ns": now,
        "day": time.strftime("%Y-%m-%d"),
        "phase": "in_trade",
        "phase_text": "In a trade",
        "blocks": [],
        "next_wake_ns": None,
        "positions": [position],
        "working": [],
        "today": {"net": "-12.48", "entries": 2, "closed": 1, "trades": 2},
        "recent": [
            {
                "at_ns": now - 40 * MIN_NS,
                "kind": "closed",
                "tag": "e2e-0",
                "text": "Trade closed long MNQ: net $-12.48, -1.00 R (e2e-0)",
            },
            {
                "at_ns": now - 12 * MIN_NS,
                "kind": "filled_entry",
                "tag": "e2e-1",
                "text": "Entered long MNQ: 2 @ 20,000.00 (e2e-1)",
            },
        ],
        "last_log": None,
        "plugin_status": {
            "summary": "In a trade from slot T2",
            "schedule": [
                {"at_ns": now - 40 * MIN_NS, "label": "T1: entered, stopped out", "state": "done"},
                {"at_ns": now - 12 * MIN_NS, "label": "T2: entered long", "state": "entered"},
                {"at_ns": now + 30 * MIN_NS, "label": "T3: planned", "state": "planned"},
                {"at_ns": None, "label": "Flat before the close", "state": "planned"},
            ],
        },
    }
    (d / "status.json").write_text(json.dumps(status))
    threading.Thread(target=_marks_loop, args=(state / "marks.json",), daemon=True).start()


def _card(card_id: str, title: str, at: int, action: str, **over: object) -> dict[str, object]:
    card: dict[str, object] = {
        "id": card_id,
        "title": title,
        "as_of_ns": at,
        "action": action,
        "instrument": "MNQ",
        "symbol": None,
        "side": None,
        "qty": None,
        "entry": None,
        "stop": None,
        "targets": [],
        "reason": None,
        "ref": None,
        "tag": None,
        "result": None,
        "fields": [],
    }
    card.update(over)
    return card


def runner_run(runs: Path) -> None:
    """A runner run folder: a host log and decision cards from both plugins."""
    now = time.time_ns()
    run = runs / RUN_NAME
    host = [{"t_ns": now - 60 * MIN_NS, "kind": "run_started", "level": "info"}]
    cards: dict[str, list[tuple[int, str, dict[str, object]]]] = {
        LIVE_PLUGIN_ID: [
            (
                now - 40 * MIN_NS,
                "entry",
                _card(
                    "e2e-0",
                    "T1: long MNQ",
                    now - 40 * MIN_NS,
                    "enter",
                    side="long",
                    qty=1,
                    entry=20_010.0,
                    stop=20_000.0,
                    targets=[20_030.0],
                    reason="Price above the EMA at the T1 slot",
                    tag="e2e-0",
                    fields=[["EMA", "20005.25"], ["Slot", "T1"]],
                ),
            ),
            (
                now - 30 * MIN_NS,
                "result",
                _card(
                    "e2e-0-result",
                    "T1: stopped out",
                    now - 30 * MIN_NS,
                    "result",
                    ref="e2e-0",
                    result={"net": "-12.48", "exit_reason": "stop", "r": "-1.00", "ticks": -40},
                ),
            ),
            (
                now - 12 * MIN_NS,
                "entry",
                _card(
                    "e2e-1",
                    "T2: long MNQ",
                    now - 12 * MIN_NS,
                    "enter",
                    side="long",
                    qty=2,
                    entry=20_000.0,
                    stop=19_990.0,
                    targets=[20_020.0],
                    reason="Price above the EMA at the T2 slot",
                    tag="e2e-1",
                ),
            ),
        ],
        PLUGIN_ID: [
            (
                now - 20 * MIN_NS,
                "skip",
                _card(
                    "e2e-skip-1",
                    "09:45 slot skipped",
                    now - 20 * MIN_NS,
                    "skip",
                    reason="Opening range wider than 40 ticks",
                ),
            ),
        ],
    }
    for pid, items in cards.items():
        out = run / "plugins" / pid
        out.mkdir(parents=True)
        with (
            (out / "plugin_log.jsonl").open("w") as log,
            (out / "decision_cards.jsonl").open("w") as dec,
        ):
            for t_ns, kind, card in items:
                line = {"t_ns": t_ns, "kind": kind, "level": "info", "plugin": pid, "card": card}
                log.write(json.dumps(line) + "\n")
                dec.write(json.dumps({"t_ns": t_ns, "plugin": pid, "kind": kind, "card": card}))
                dec.write("\n")
    (run / "host_log.jsonl").write_text("".join(json.dumps(e) + "\n" for e in host))


def _marks_loop(path: Path) -> None:
    """Rewrite ``marks.json`` every second: MNQ quotes around 20,002.50."""
    i = 0
    while True:
        now = time.time_ns()
        price = 80_010 + (i % 3)
        doc = {
            "version": 1,
            "updated_ns": now,
            "marks": {
                "MNQ": {
                    "quote": {
                        "price_t": price,
                        "price": price / 4,
                        "basis": "last",
                        "t_ns": now,
                        "received_ns": now,
                        "contract_id": "CON.F.US.MNQ.Z26",
                    },
                    "bar": {
                        "price_t": 80_008,
                        "price": 20_002.0,
                        "close_ns": now - MIN_NS,
                        "contract": "MNQZ6",
                    },
                }
            },
        }
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(doc))
            tmp.replace(path)
        except OSError:
            return  # the temp home was removed: the server is stopping
        i += 1
        time.sleep(1)


def _exit(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument(
        "--root-file",
        type=Path,
        help="write the temp root's path here after seeding (the spec edits seeded files)",
    )
    args = parser.parse_args()
    port = args.port
    root = Path(tempfile.mkdtemp(prefix="fse-e2e-"))
    # uvicorn re-raises SIGTERM/SIGINT after its shutdown with the handler that was
    # installed before it started; exiting through SystemExit runs the cleanup below.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _exit)
    try:
        prepare(root, port)
        if args.root_file is not None:
            args.root_file.write_text(str(root), encoding="utf-8")
        settings = Settings.from_env()
        settings.check()
        uvicorn.run(
            create_app(settings),
            host="127.0.0.1",
            port=port,
            ws="websockets-sansio",
            log_level="warning",
            # /api/ws/live only notices a closed socket on its next send, so an
            # idle stream would hold the shutdown open; cancel it after 2 s.
            timeout_graceful_shutdown=2,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if args.root_file is not None:
            args.root_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
