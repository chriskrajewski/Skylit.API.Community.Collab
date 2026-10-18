"""The host trade journal ``trades.jsonl`` (paper-analytics design §4, B-1..B-6, B-F4).

Pure tests build engine ``Trade`` objects by hand and call
``fse.live.host.journal``; host tests drive ``PluginHost`` with ``FakeClock``
and ``FakeDataPlane`` as ``test_plugin_host_accounts.py`` does (MNQ, $0.50 a
tick, $0.62 a side per contract, slippage 0).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse.engine.types import Fill, SetupKey, Trade
from fse.live.host import host as host_module
from fse.live.host.host import PLUGIN_STATE_FILE_NAME, PluginHost
from fse.live.host.journal import (
    JournalDay,
    JournalSlot,
    _allocate,
    journal_day,
    journal_trades,
)
from fse.live.host.journal import journal_lines as _journal_lines
from fse.live.plugins import OrderRole, OrderUpdate, TargetLeg
from fse.live.plugins.api import EnterMarket
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.timekit import Instant
from tests.fakes.dataplane import host_entry, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin
from tests.unit.test_plugin_host_accounts import (
    FRI,
    SAT,
    SUN,
    WED,
    HoldPlugin,
    Run,
    bar,
    closed_net,
    enter,
    entries_on_bar,
    host_log,
    on,
    run_host,
)

ZERO = Decimal("0.00")
T0 = ny(WED, 10, 0)
T1 = ny(WED, 10, 5)
T2 = ny(WED, 10, 9)
KEY = SetupKey("MNQ", "p1:s1", 21240.0, "long", WED, 1)
DAY = JournalDay(WED, "open")
SLOT = JournalSlot("p1", "topstep_a", {})


def journal(tmp_path: Path, plugin_id: str = "p1") -> list[dict[str, Any]]:
    return read_jsonl(tmp_path / "run" / "plugins" / plugin_id / "trades.jsonl")


def kinds(lines: Sequence[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [ln for ln in lines if ln["kind"] == kind]


def journal_lines(
    slot: JournalSlot, u: OrderUpdate, day: JournalDay, key: SetupKey | None
) -> list[dict[str, Any]]:
    """``fse.live.host.journal.journal_lines`` with the lines typed for assertions."""
    return [dict(line) for line in _journal_lines(slot, u, day, key)]


ROLES: dict[str, OrderRole] = {"stop": "stop", "tp1": "tp1", "tp2": "tp2"}


def closed_update(trade: Trade, at: Instant = T2) -> OrderUpdate:
    """The ``closed`` update of ``trade`` (role of its last exit; a close_setup id is ``exit``)."""
    last = trade.exits[-1]
    role = ROLES.get(last.client_id.rsplit(":", 1)[1], "exit")
    return OrderUpdate("s1", "closed", None, last, role, trade, None, at)


def two_leg_trade() -> Trade:
    """2 lots long from 85012: tp1 1 lot +40 ticks, then the stop 1 lot at entry."""
    return Trade(
        setup_key=KEY,
        entry_fill=Fill("p1:s1:entry", T0, 85012, 2, Decimal("1.24")),
        initial_stop=84972,
        qty_at_entry=2,
        exits=(
            Fill("p1:s1:tp1", T1, 85052, 1, Decimal("0.62")),
            Fill("p1:s1:stop", T2, 85012, 1, Decimal("0.62")),
        ),
        net=Decimal("17.52"),  # 20.00 gross - 2.48 fees
        r=Decimal("40.00"),  # 40 ticks x 0.50 x 2
        r_multiple=Decimal("0.438"),
        reached_tp1=True,
        mae_r=Decimal("0.25"),
        mfe_r=Decimal("1.1"),
        missing_bars=0,
        shadow=False,
    )


def one_leg_trade(exit_id: str = "p1:s1:tp1", exit_t: int = 85052) -> Trade:
    gross = Decimal(exit_t - 85012) * Decimal("0.50")
    return Trade(
        setup_key=KEY,
        entry_fill=Fill("p1:s1:entry", T0, 85012, 1, Decimal("0.62")),
        initial_stop=84972,
        qty_at_entry=1,
        exits=(Fill(exit_id, T1, exit_t, 1, Decimal("0.62")),),
        net=gross - Decimal("1.24"),
        r=Decimal("20.00"),
        r_multiple=(gross - Decimal("1.24")) / Decimal("20.00"),
        reached_tp1=exit_t > 85012,
        mae_r=Decimal(0),
        mfe_r=Decimal("1"),
        missing_bars=2,
        shadow=False,
    )


# ---------------------------------------------------------------- pure: B-1, B-2, round trip


def test_one_leg_trade_line_has_every_field() -> None:
    """B-1: fees = the commission round turn, net = gross - fees."""
    trade = one_leg_trade()
    [line] = journal_lines(SLOT, closed_update(trade), DAY, None)
    assert line == {
        "v": 1,
        "kind": "trade",
        "line_id": f"p1:s1:tp1@{T1}",
        "trade_id": f"p1:s1:entry@{T0}",
        "plugin": "p1",
        "account": "topstep_a",
        "tag": "s1",
        "instrument": "MNQ",
        "side": "long",
        "qty": 1,
        "qty_at_entry": 1,
        "entry_ns": T0,
        "entry_t": 85012,
        "entry_price": "21253.00",
        "exit_ns": T1,
        "exit_t": 85052,
        "exit_price": "21263.00",
        "gross": "20.00",
        "fees": "1.24",
        "net": "18.76",
        "exit_reason": "target",
        "leg": "tp1",
        "initial_stop_t": 84972,
        "trade_r": "20.00",
        "r": "0.9380",
        "trade_net": "18.76",
        "r_multiple": format(trade.r_multiple, "f"),
        "mae_r": "0",
        "mfe_r": "1",
        "reached_tp1": True,
        "missing_bars": 2,
        "entry_day": "2026-10-14",
        "globex_day": "2026-10-14",
        "day_source": "open",
        "client_id": "p1:s1:tp1",
        "entry_fill": {
            "client_id": "p1:s1:entry",
            "bar_open_ns": T0,
            "price_t": 85012,
            "qty": 1,
            "fees": "0.62",
        },
        "exit_fill": {
            "client_id": "p1:s1:tp1",
            "bar_open_ns": T1,
            "price_t": 85052,
            "qty": 1,
            "fees": "0.62",
        },
        "setup_key": {
            "instrument": "MNQ",
            "pattern": "p1:s1",
            "source_strike": 21240.0,
            "direction": "long",
            "session": "2026-10-14",
            "tap_seq": 1,
        },
    }


def test_multi_leg_trade_sums_to_the_engine_trade() -> None:
    """B-2: leg qty sums to qty_at_entry; leg fees and nets sum to the trade's."""
    trade = two_leg_trade()
    lines = journal_lines(SLOT, closed_update(trade), DAY, None)
    assert [(ln["leg"], ln["exit_reason"], ln["qty"]) for ln in lines] == [
        ("tp1", "target", 1),
        ("stop", "stop", 1),
    ]
    assert sum(ln["qty"] for ln in lines) == trade.qty_at_entry
    assert sum(Decimal(ln["net"]) for ln in lines) == trade.net
    assert sum(Decimal(ln["fees"]) for ln in lines) == Decimal("2.48")
    assert [ln["net"] for ln in lines] == ["18.76", "-1.24"]
    assert [ln["r"] for ln in lines] == ["0.9380", "-0.0620"]
    assert {ln["trade_id"] for ln in lines} == {f"p1:s1:entry@{T0}"}
    assert len({ln["line_id"] for ln in lines}) == 2


def test_entry_fees_are_split_pro_rata_with_the_remainder_on_the_last_leg() -> None:
    assert _allocate(Decimal("1.00"), [1, 2], 3) == [Decimal("0.33"), Decimal("0.67")]
    assert _allocate(Decimal("1.00"), [1, 1, 1], 3) == [
        Decimal("0.33"),
        Decimal("0.33"),
        Decimal("0.34"),
    ]
    assert _allocate(Decimal("0.62"), [1], 1) == [Decimal("0.62")]


@pytest.mark.parametrize(
    "trade",
    [
        one_leg_trade(),
        two_leg_trade(),
        one_leg_trade("host_break_backstop:p1:s1:entry", 85000),
    ],
    ids=["one_leg", "two_legs", "close_setup"],
)
def test_round_trip_rebuilds_the_engine_trade(trade: Trade) -> None:
    lines = journal_lines(SLOT, closed_update(trade), DAY, None)
    text = [json.loads(json.dumps(ln)) for ln in lines]
    # a reader dedups a repeated line (a replay re-close after a crash)
    assert journal_trades([*text, text[0], {"kind": "fill"}]) == [trade]


def test_exit_reason_mapping() -> None:
    close_setup = one_leg_trade("restart_stale:p1:s1:entry", 85000)
    [line] = journal_lines(SLOT, closed_update(close_setup), DAY, None)
    assert (line["leg"], line["exit_reason"]) == ("exit", "restart_stale")
    market = one_leg_trade("p1:s1:exit", 85000)
    [line] = journal_lines(SLOT, closed_update(market), DAY, None)
    assert (line["leg"], line["exit_reason"]) == ("exit", "market")
    labelled = JournalSlot("p1", None, {"s1": "account_mll"})
    [line] = journal_lines(labelled, closed_update(market), DAY, None)
    assert (line["exit_reason"], line["account"]) == ("account_mll", None)
    # a stop or target keeps its own reason even with a pending flatten
    [line] = journal_lines(labelled, closed_update(one_leg_trade()), DAY, None)
    assert line["exit_reason"] == "target"


def test_fill_line_fields() -> None:
    fill = Fill("p1:s1:entry", T0, 85012, 2, Decimal("1.24"))
    detail = {"submit_quote_t": 85011, "fill_minus_quote_t": 1}
    u = OrderUpdate("s1", "filled_entry", None, fill, "entry", None, None, T0, detail)
    [line] = journal_lines(SLOT, u, DAY, KEY)
    assert line == {
        "v": 1,
        "kind": "fill",
        "plugin": "p1",
        "account": "topstep_a",
        "tag": "s1",
        "leg": "entry",
        "client_id": "p1:s1:entry",
        "instrument": "MNQ",
        "side": "buy",
        "qty": 2,
        "bar_open_ns": T0,
        "price_t": 85012,
        "price": "21253.00",
        "fees": "1.24",
        "globex_day": "2026-10-14",
        "day_source": "open",
        "submit_quote_t": 85011,
        "fill_minus_quote_t": 1,
    }
    exit_fill = Fill("p1:s1:stop", T1, 84972, 2, Decimal("1.24"))
    u = OrderUpdate("s1", "filled_exit", None, exit_fill, "stop", None, None, T1)
    [line] = journal_lines(SLOT, u, DAY, KEY)
    assert (line["side"], line["leg"], line["submit_quote_t"]) == ("sell", "stop", None)
    with pytest.raises(ValueError, match="no Setup_Key"):
        journal_lines(SLOT, u, DAY, None)
    accepted = OrderUpdate("s1", "accepted", None, None, "entry", None, None, T0)
    assert journal_lines(SLOT, accepted, DAY, KEY) == []


def test_journal_day_is_the_broker_day_else_the_last_closed_day() -> None:
    assert journal_day(WED, ny(WED, 10, 0), None) == JournalDay(WED, "open")
    assert journal_day(None, ny(SAT, 10, 0), None) == JournalDay(FRI, "after_close")
    assert journal_day(None, ny(SUN, 17, 59), None) == JournalDay(FRI, "after_close")
    assert journal_day(None, ny(FRI, 18, 30), None) == JournalDay(FRI, "after_close")


# ---------------------------------------------------------------- host: B-1, B-3, B-4


def _bars_up(d: date, hh: int, n: int, step: int = 4) -> list[Any]:
    return [
        bar(
            d,
            hh,
            i,
            80_000 + step * i,
            80_001 + step * i + step,
            79_999 + step * i,
            80_000 + step * (i + 1),
        )
        for i in range(n)
    ]


def test_startup_creates_the_journal_on_a_day_without_trades(tmp_path: Path) -> None:
    run_host(
        tmp_path,
        T0,
        ny(WED, 10, 6),
        bars=_bars_up(WED, 10, 5),
        entries=[host_entry("p1")],
        accounts=(),
    )
    path = tmp_path / "run" / "plugins" / "p1" / "trades.jsonl"
    assert path.exists()
    assert path.read_bytes() == b""


def test_host_journals_fills_and_the_trade_once(tmp_path: Path) -> None:
    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {T0: lambda b: [enter("t1", b.close_ns, 1, target=8)]})

    other = tmp_path
    run = run_host(
        other,
        T0,
        ny(WED, 10, 6),
        bars=_bars_up(WED, 10, 5),
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
    )
    lines = journal(other)
    assert [(ln["kind"], ln["leg"]) for ln in lines] == [
        ("fill", "entry"),
        ("fill", "tp1"),
        ("trade", "tp1"),
    ]
    [trade_line] = kinds(lines, "trade")
    [update] = [u for u in run.plugins["p1"].updates if u.status == "closed"]
    assert update.trade is not None
    assert trade_line["fees"] == "1.24"
    assert Decimal(trade_line["net"]) == update.trade.net == Decimal("2.76")
    assert Decimal(trade_line["gross"]) - Decimal(trade_line["fees"]) == Decimal(trade_line["net"])
    assert trade_line["account"] is None
    assert journal_trades(lines) == [update.trade]


def test_two_target_trade_through_the_host_round_trips(tmp_path: Path) -> None:
    def setup(run: Run) -> None:
        legs = (TargetLeg("tp1", 1, 8), TargetLeg("tp2", 2, 16))
        entries_on_bar(
            run.plugins["p1"],
            {T0: lambda b: [EnterMarket("t1", "MNQ", "long", 3, 40, legs, b.close_ns, True, None)]},
        )

    run = run_host(
        tmp_path,
        T0,
        ny(WED, 10, 9),
        bars=_bars_up(WED, 10, 8),
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
    )
    lines = kinds(journal(tmp_path), "trade")
    assert [ln["leg"] for ln in lines] == ["tp1", "tp2"]
    [update] = [u for u in run.plugins["p1"].updates if u.status == "closed"]
    assert update.trade is not None
    assert sum(ln["qty"] for ln in lines) == 3
    assert sum(Decimal(ln["net"]) for ln in lines) == update.trade.net
    assert journal_trades(lines) == [update.trade]


def test_journal_open_day_nets_equal_the_broker_day_net(tmp_path: Path) -> None:
    """B-3: three trades (two winners, one stop) on one Globex day."""

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                ny(WED, 10, 0): lambda b: [enter("a", b.close_ns, 1, target=8)],
                ny(WED, 10, 3): lambda b: [enter("b", b.close_ns, 2, target=8)],
                ny(WED, 10, 6): lambda b: [enter("c", b.close_ns, 1, stop=4, target=40)],
            },
        )

    bars = [
        *_bars_up(WED, 10, 6),
        *(bar(WED, 10, 6 + i, 80_024, 80_025, 80_010, 80_012) for i in range(3)),
    ]
    run = run_host(
        tmp_path,
        T0,
        ny(WED, 10, 12),
        bars=bars,
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
    )
    broker = run.host.broker("p1")
    assert broker is not None
    lines = kinds(journal(tmp_path), "trade")
    assert len({ln["trade_id"] for ln in lines}) == 3
    assert {ln["day_source"] for ln in lines} == {"open"}
    total = sum((Decimal(ln["net"]) for ln in lines if ln["day_source"] == "open"), ZERO)
    assert total == broker.state.day_net == closed_net(run.plugins["p1"].updates)
    assert "stop" in {ln["exit_reason"] for ln in lines}


def test_trade_open_across_a_restart_is_journaled_once(tmp_path: Path) -> None:
    """B-4: opened in run 1, closed in run 2 (after a replay of run 1's bars)."""

    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {T0: lambda b: [enter("t1", b.close_ns, 1, target=40)]})

    first = run_host(
        tmp_path,
        T0,
        ny(WED, 10, 4),
        bars=[bar(WED, 10, i, 80_000, 80_002, 79_998, 80_000) for i in range(3)],
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
    )
    assert [ln["leg"] for ln in journal(tmp_path)] == ["entry"]
    second = run_host(
        tmp_path,
        ny(WED, 10, 10),
        ny(WED, 10, 14),
        bars=[
            bar(WED, 10, 10 + i, 80_000 + 20 * i, 80_030 + 20 * i, 79_999 + 20 * i, 80_020 + 20 * i)
            for i in range(3)
        ],
        entries=[host_entry("p1")],
        accounts=(),
        recorded=first.plane.recorded,
    )
    lines = journal(tmp_path)
    assert [(ln["kind"], ln["leg"]) for ln in lines] == [
        ("fill", "entry"),
        ("fill", "tp1"),
        ("trade", "tp1"),
    ]
    [update] = [u for u in second.plugins["p1"].updates if u.status == "closed"]
    assert journal_trades(lines) == [update.trade]


# ---------------------------------------------------------------- host: B-5, B-6, B-F4


def test_plugin_raising_in_on_order_update_is_still_journaled(tmp_path: Path) -> None:
    """B-5: the fault on the entry fill; the bracket still closes and is journaled."""

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        entries_on_bar(p1, {T0: lambda b: [enter("t1", b.close_ns, 1, target=8)]})

        def boom(u: OrderUpdate, ctx: Any) -> None:
            if u.status == "filled_entry":
                raise RuntimeError("plugin bug")

        on(p1, "on_order_update", boom)

    run_host(
        tmp_path,
        T0,
        ny(WED, 10, 6),
        bars=_bars_up(WED, 10, 5),
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
        exit_code=1,  # a fault makes run() return 1
    )
    assert host_log(tmp_path, "plugin_faulted")
    lines = journal(tmp_path)
    assert [(ln["kind"], ln["leg"]) for ln in lines] == [
        ("fill", "entry"),
        ("fill", "tp1"),
        ("trade", "tp1"),
    ]


class CsvPlugin(RecordingPlugin):
    """Writes one CSV row per closed trade into its out dir (like a research plugin)."""

    def on_order_update(self, update: OrderUpdate, ctx: Any) -> Sequence[Any]:
        if update.status == "closed" and update.trade is not None:
            path = self.services.out_dir / "trades.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            buf = io.StringIO()
            t = update.trade
            csv.writer(buf).writerow([update.tag, t.entry_fill.price, t.exits[-1].price, t.net])
            with path.open("a", encoding="utf-8", newline="") as fh:
                fh.write(buf.getvalue())
        return super().on_order_update(update, ctx)


def _csv_run(tmp_path: Path) -> bytes:
    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                T0: lambda b: [enter("a", b.close_ns, 1, target=8)],
                ny(WED, 10, 3): lambda b: [enter("b", b.close_ns, 2, target=8)],
            },
        )

    run_host(
        tmp_path,
        T0,
        ny(WED, 10, 8),
        bars=_bars_up(WED, 10, 7),
        entries=[host_entry("p1")],
        accounts=(),
        classes={"p1": CsvPlugin},
        setup=setup,
    )
    return (tmp_path / "run" / "plugins" / "p1" / "trades.csv").read_bytes()


def test_plugin_csv_bytes_are_identical_with_and_without_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-6 / BC-4."""
    with_journal = _csv_run(tmp_path / "on")
    assert kinds(journal(tmp_path / "on"), "trade")
    monkeypatch.setattr(PluginHost, "_journal", lambda self, slot, u, batch: None)
    without = _csv_run(tmp_path / "off")
    assert journal(tmp_path / "off") == []
    assert with_journal == without
    assert with_journal.count(b"\n") == 2


class FailingJournalWriter(LogWriter):
    def __init__(self) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        self.fail = True

    def append_json(self, path: Any, obj: object, *, fsync: bool = False) -> None:
        if Path(path).name == "trades.jsonl" and self.fail:
            raise OSError("disk full")
        super().append_json(path, obj, fsync=fsync)


def test_journal_write_failure_is_logged_once_and_trading_continues(tmp_path: Path) -> None:
    """B-F4: two failing trades log one line; the third trade's lines log the recovery."""
    writer = FailingJournalWriter()

    def recover(b: Any) -> list[Any]:
        writer.fail = False
        return [enter("c", b.close_ns, 1, target=8)]

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                ny(WED, 10, 0): lambda b: [enter("a", b.close_ns, 1, target=8)],
                ny(WED, 10, 2): lambda b: [enter("b", b.close_ns, 1, target=8)],
                ny(WED, 10, 4): recover,
            },
        )

    run = run_host(
        tmp_path,
        T0,
        ny(WED, 10, 8),
        bars=_bars_up(WED, 10, 7),
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
        writer=writer,
    )
    [failed] = host_log(tmp_path, "journal_write_failed")
    assert (failed["level"], failed["plugin"], failed["detail"]) == ("error", "p1", "OSError")
    [recovered] = host_log(tmp_path, "journal_write_recovered")
    assert recovered["level"] == "info"
    closed = [u.tag for u in run.plugins["p1"].updates if u.status == "closed"]
    assert closed == ["a", "b", "c"]
    assert [ln["tag"] for ln in kinds(journal(tmp_path), "trade")] == ["c"]
    assert (tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME).exists()


# ---------------------------------------------------------------- _record guards


def _guard_run(tmp_path: Path) -> Run:
    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {T0: lambda b: [enter("t1", b.close_ns, 1, target=8)]})

    return run_host(
        tmp_path,
        T0,
        ny(WED, 10, 6),
        bars=_bars_up(WED, 10, 5),
        entries=[host_entry("p1", account="acct")],
        setup=setup,
    )


def _book_and_fills(tmp_path: Path, run: Run) -> tuple[Any, list[Any]]:
    doc = json.loads(
        (tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME).read_text("utf-8")
    )
    fills = [(u.tag, u.status, u.fill) for u in run.plugins["p1"].updates]
    return doc["book"], fills


def test_journal_lines_raising_never_stops_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b): a ``ValueError`` from building a line; the fills, the book and the account
    are those of a control run, and one ``journal_write_failed`` is logged."""
    control = _guard_run(tmp_path / "control")

    def broken(*args: Any) -> list[Any]:
        raise ValueError("bad line")

    monkeypatch.setattr(host_module, "journal_lines", broken)
    run = _guard_run(tmp_path / "broken")
    assert _book_and_fills(tmp_path / "broken", run) == _book_and_fills(
        tmp_path / "control", control
    )
    [failed] = host_log(tmp_path / "broken", "journal_write_failed")
    assert failed["detail"] == "ValueError"
    assert run.host.account("acct").state.realized == Decimal("2.76")
    assert run.host.account("acct").state == control.host.account("acct").state


class TouchFails(LogWriter):
    def __init__(self) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())

    def touch(self, path: Any) -> None:
        raise OSError("read-only file system")


def test_startup_touch_failure_never_stops_the_host(tmp_path: Path) -> None:
    """(c): the touch raises; the run trades as the control run, the journal recovers."""
    control = _guard_run(tmp_path / "control")

    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {T0: lambda b: [enter("t1", b.close_ns, 1, target=8)]})

    run = run_host(
        tmp_path / "touch",
        T0,
        ny(WED, 10, 6),
        bars=_bars_up(WED, 10, 5),
        entries=[host_entry("p1", account="acct")],
        setup=setup,
        writer=TouchFails(),
    )
    assert _book_and_fills(tmp_path / "touch", run) == _book_and_fills(
        tmp_path / "control", control
    )
    [failed] = host_log(tmp_path / "touch", "journal_write_failed")
    assert failed["detail"] == "OSError"
    assert len(host_log(tmp_path / "touch", "journal_write_recovered")) == 1
    assert run.host.account("acct").state.realized == Decimal("2.76")


# ---------------------------------------------------------------- weekend close


def test_weekend_restart_stale_close_is_dated_friday_after_close(tmp_path: Path) -> None:
    """A held Friday position closed by ``restart_stale`` on Saturday."""
    t0 = ny(FRI, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {t0: lambda b: [enter("t1", b.close_ns, 1, stop=200, target=200)]}
        )

    up = [bar(FRI, 10, i, 80_000 + i, 80_001 + i, 79_999 + i, 80_001 + i) for i in range(4)]
    run_host(tmp_path, t0, ny(FRI, 16, 30), bars=up, classes={"p1": HoldPlugin}, setup=setup)
    second = run_host(tmp_path, ny(SAT, 10, 0), ny(SUN, 18, 1), classes={"p1": HoldPlugin})
    [line] = kinds(journal(tmp_path), "trade")
    assert (line["globex_day"], line["day_source"]) == ("2026-10-16", "after_close")
    assert (line["exit_reason"], line["account"]) == ("restart_stale", "acct")
    assert Decimal(line["net"]) == Decimal("0.26")
    state = second.host.account("acct").state
    assert state.realized == Decimal("0.26")
    assert len(host_log(tmp_path, "account_close_outside_day")) == 1
    [fill] = [ln for ln in kinds(journal(tmp_path), "fill") if ln["leg"] == "exit"]
    assert fill["day_source"] == "after_close"
