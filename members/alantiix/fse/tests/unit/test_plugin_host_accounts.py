"""Paper accounts in ``PluginHost`` (paper-analytics design 3.3-3.6, A-5..A-8, BC-3).

Every test drives the host with ``FakeClock`` and the in-memory
``FakeDataPlane`` (no socket). Prices are MNQ ticks ($0.50 a tick), fees
$0.62 a side per contract (``host_entry``'s $1.24 round turn), slippage 0.
A restart is a second host on the same ``tmp_path`` whose data plane holds
the first run's recording, as in ``test_plugin_host_persistence.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from time import perf_counter_ns as time_ns
from typing import Any, ClassVar

from fse.engine.types import Bar
from fse.live.host.accounts import ACCOUNT_CODEC, PaperAccountState
from fse.live.host.broker import BROKER_CODEC
from fse.live.host.host import (
    HOST_LOG_FILE_NAME,
    PLUGIN_LOG_FILE_NAME,
    PLUGIN_STATE_FILE_NAME,
    BarsEvent,
    HostResources,
    MarketEvent,
    PluginHost,
)
from fse.live.host.nexus import NexusResponse
from fse.live.plugins import (
    BarNeed,
    Cancel,
    DataNeeds,
    EnterMarket,
    EnterResting,
    Flatten,
    Intent,
    ModifyStop,
    OrderUpdate,
    TargetLeg,
)
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin
from tests.unit.fixtures.make_plugin_json_v1 import FIXTURE_PATH
from tests.unit.fixtures.make_replay_v1_golden import load_bars

MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
WED = date(2026, 10, 14)
THU = date(2026, 10, 15)
FRI = date(2026, 10, 16)
SAT = date(2026, 10, 17)
SUN = date(2026, 10, 18)
NEXT_MON = date(2026, 10, 19)
START = Decimal("50000.00")
ZERO = Decimal("0.00")


class HoldPlugin(RecordingPlugin):
    """A Broker-like plugin that may hold a position across the 18:00 rotation."""

    NEEDS: ClassVar[DataNeeds] = DataNeeds(
        bars=(BarNeed("MNQ"),), hours="eth", hold_through_break=True
    )


# ---------------------------------------------------------------- helpers


def bar(d: date, hh: int, mm: int, o: int, h: int, low: int, c: int) -> Bar:
    return make_bar("MNQ", ny(d, hh, mm), o, h, low, c)


def flat_bars(d: date, hh: int, mm: int, n: int, price: int = 80_000) -> list[Bar]:
    return [bar(d, hh, mm + i, price, price + 1, price - 1, price) for i in range(n)]


def enter(
    tag: str,
    placed_at: Instant,
    qty: int = 1,
    *,
    stop: int = 40,
    target: int = 40,
    freeze: Instant | None = None,
) -> EnterMarket:
    return EnterMarket(
        tag, "MNQ", "long", qty, stop, (TargetLeg("tp1", qty, target),), placed_at, True, freeze
    )


def account(rules: Mapping[str, Any] | None = None, acct_id: str = "acct") -> dict[str, Any]:
    out: dict[str, Any] = {"id": acct_id, "preset": "none"}
    if rules:
        out["rules"] = dict(rules)
    return out


def on(plugin: RecordingPlugin, hook: str, fn: Callable[..., Sequence[Intent] | None]) -> None:
    plugin.handlers[hook] = fn


def entries_on_bar(
    plugin: RecordingPlugin, by_open: Mapping[Instant, Callable[[Bar], list[Any]]]
) -> None:
    """``on_bar`` returns ``by_open[bar.open_ns](bar)`` (live bars only)."""

    def handler(b: Bar, received: Instant, catch_up: bool, ctx: Any) -> list[Any]:
        fn = by_open.get(b.open_ns)
        return [] if fn is None or catch_up else fn(b)

    on(plugin, "on_bar", handler)


def wake_at(plugin: RecordingPlugin, t: Instant, fn: Callable[[Instant, Any], list[Any]]) -> None:
    """One ``on_wake`` at ``t`` returning ``fn(now, ctx)``."""
    plugin.wake = t

    def handler(now: Instant, ctx: Any) -> list[Any]:
        plugin.wake = None
        return fn(now, ctx)

    on(plugin, "on_wake", handler)


@dataclass
class Run:
    host: PluginHost
    plane: FakeDataPlane
    plugins: dict[str, RecordingPlugin]


def run_host(
    tmp_path: Path,
    start: Instant,
    until: Instant,
    *,
    bars: Sequence[Bar] = (),
    entries: Sequence[Mapping[str, Any]] | None = None,
    accounts: Sequence[Mapping[str, Any]] = (account(),),
    classes: Mapping[str, type[RecordingPlugin]] | None = None,
    recorded: Sequence[MarketEvent] = (),
    setup: Callable[[Run], None] | None = None,
    writer: LogWriter | None = None,
    exit_code: int = 0,
    resources: HostResources | None = None,
) -> Run:
    clock = FakeClock(start)
    script = [
        (b.close_ns + 2 * NS_PER_SECOND, BarsEvent((b,), b.close_ns + 2 * NS_PER_SECOND))
        for b in bars
    ]
    plane = FakeDataPlane(clock, script, recorded=recorded)
    entries = list(entries) if entries is not None else [host_entry("p1", account="acct")]
    cls = dict(classes) if classes is not None else {}
    for e in entries:
        cls.setdefault(e["id"], RecordingPlugin)
    host = make_host(
        tmp_path,
        cls,
        entries,
        clock,
        plane,
        accounts=list(accounts),
        writer=writer,
        resources=resources,
    )
    plugins: dict[str, RecordingPlugin] = {}
    for e in entries:
        p = host.plugin(e["id"])
        assert isinstance(p, RecordingPlugin)
        plugins[e["id"]] = p
    run = Run(host, plane, plugins)
    if setup is not None:
        setup(run)
    code = asyncio.run(clock.run(host.run(until_ns=until), max_steps=1_000_000))
    assert code == exit_code
    return run


def host_log(tmp_path: Path, kind: str | None = None, since: Instant = 0) -> list[dict[str, Any]]:
    lines = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    return [ln for ln in lines if (kind is None or ln["kind"] == kind) and ln["t_ns"] >= since]


def plugin_log(tmp_path: Path, plugin_id: str = "p1") -> list[dict[str, Any]]:
    return read_jsonl(tmp_path / "run" / "plugins" / plugin_id / PLUGIN_LOG_FILE_NAME)


def rejected(tmp_path: Path, plugin_id: str = "p1") -> dict[str, str]:
    """``{tag: reason}`` of every ``intent_rejected`` line."""
    return {
        ln["tag"]: ln["reason"]
        for ln in plugin_log(tmp_path, plugin_id)
        if ln["kind"] == "intent_rejected"
    }


def statuses(tmp_path: Path, since: Instant = 0) -> list[tuple[str, str, str]]:
    return [
        (ln["old"], ln["new"], ln["reason"]) for ln in host_log(tmp_path, "account_status", since)
    ]


def trade_lines(tmp_path: Path, plugin_id: str = "p1") -> list[dict[str, Any]]:
    """The ``trade`` lines of the plugin's journal (``run/plugins/<id>/trades.jsonl``)."""
    lines = read_jsonl(tmp_path / "run" / "plugins" / plugin_id / "trades.jsonl")
    return [ln for ln in lines if ln["kind"] == "trade"]


def closed_net(updates: Sequence[OrderUpdate]) -> Decimal:
    return sum((u.trade.net for u in updates if u.status == "closed" and u.trade), ZERO)


def account_file(tmp_path: Path, acct_id: str = "acct") -> dict[str, Any]:
    path = tmp_path / "live-state" / "accounts" / acct_id / "account.json"
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def account_state(tmp_path: Path, acct_id: str = "acct") -> PaperAccountState:
    state = ACCOUNT_CODEC.decode(account_file(tmp_path, acct_id)["account"])
    assert isinstance(state, PaperAccountState)
    return state


# ---------------------------------------------------------------- A-5: breaches


MLL_100 = {"maximum_loss_limit": {"enabled": True, "value": "100.00", "mode": "static"}}


def test_mll_breach_blocks_every_member_and_flattens_once(tmp_path: Path) -> None:
    """Two members, one MLL breach, three more losing bars: one line pair, one exit each."""
    bars = [
        bar(WED, 10, 0, 80_000, 80_001, 79_999, 80_000),
        bar(WED, 10, 1, 80_000, 80_002, 79_975, 79_980),  # fills; worst -131.20 < -100
        # the flatten exits, placed 10:02:02, fill on the first bar opening after
        bar(WED, 10, 2, 79_978, 79_980, 79_970, 79_972),
        bar(WED, 10, 3, 79_960, 79_962, 79_950, 79_955),  # the exits fill at this open
        bar(WED, 10, 4, 79_950, 79_952, 79_940, 79_945),
        bar(WED, 10, 5, 79_940, 79_942, 79_930, 79_935),
    ]
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        p1, p2 = run.plugins["p1"], run.plugins["p2"]
        entries_on_bar(p1, {t0: lambda b: [enter("t1", b.close_ns, 5)]})
        entries_on_bar(p2, {t0: lambda b: [enter("u1", b.close_ns, 5)]})
        # between the breach (10:02:02) and the exit fill (10:04:02)
        wake_at(
            p1,
            ny(WED, 10, 2, 30),
            # qty 60 is also past max_contracts 50: the account block comes first
            lambda now, ctx: [enter("t2", now, 60), ModifyStop("t1", 79_965)],
        )
        wake_at(p2, ny(WED, 10, 2, 30), lambda now, ctx: [enter("u2", now, 1)])

    run = run_host(
        tmp_path,
        t0,
        ny(WED, 10, 10),
        bars=bars,
        entries=[host_entry("p1", account="acct"), host_entry("p2", account="acct")],
        accounts=[account(MLL_100)],
        setup=setup,
    )
    assert statuses(tmp_path) == [("active", "breached", "account_mll")]
    [status] = host_log(tmp_path, "account_status")
    assert status["level"] == "warning"
    assert status["account"] == "acct"
    assert status["mll_floor"] == "49900.00"
    [flatten] = host_log(tmp_path, "account_flatten")
    assert (flatten["reason"], flatten["plugins"]) == ("account_mll", ["p1", "p2"])
    for pid in ("p1", "p2"):
        exits = [
            u
            for u in run.plugins[pid].updates
            if u.status == "filled_exit"
            and u.fill is not None
            and u.fill.client_id.endswith(":exit")
        ]
        assert len(exits) == 1, pid
        fill = exits[0].fill
        assert fill is not None
        assert fill.price == 79_960
    assert rejected(tmp_path, "p1") == {"t2": "account_breached"}
    assert rejected(tmp_path, "p2") == {"u2": "account_breached"}
    refusal = next(ln for ln in plugin_log(tmp_path) if ln["kind"] == "intent_rejected")
    assert refusal["level"] == "info"
    # stop modifies still go through on a breached account
    assert any(
        u.status == "accepted" and u.role == "stop" and u.tag == "t1"
        for u in run.plugins["p1"].updates
    )
    state = run.host.account("acct").state
    assert (state.status, state.breach_reason) == ("breached", "account_mll")
    net = closed_net(run.plugins["p1"].updates) + closed_net(run.plugins["p2"].updates)
    # fill-model balances, not clamped to the floor: 2 x (5 x -40 ticks x 0.50 - 6.20 fees)
    assert net == Decimal("-212.40")
    assert state.balance == START + net
    assert state.realized == net
    assert account_state(tmp_path) == state
    # A-5 journaled: each member's flatten exit carries the breach reason
    for pid in ("p1", "p2"):
        [line] = trade_lines(tmp_path, pid)
        assert (line["leg"], line["exit_reason"], line["account"]) == (
            "exit",
            "account_mll",
            "acct",
        )


def test_dll_lock_then_mll_breach_gives_two_line_pairs_and_one_exit_order(tmp_path: Path) -> None:
    """The book is frozen from 10:03 (``freeze_from_ns``), so the DLL flatten's exit stays
    working while the account keeps valuing the trade at each bar."""
    t0 = ny(WED, 10, 0)
    rules = {
        "daily_loss_limit": {"enabled": True, "value": "50.00"},
        "maximum_loss_limit": {"enabled": True, "value": "150.00", "mode": "static"},
    }
    bars = [
        bar(WED, 10, 0, 80_000, 80_001, 79_999, 80_000),
        bar(WED, 10, 1, 80_000, 80_001, 79_999, 80_000),  # fill
        bar(WED, 10, 2, 80_000, 80_001, 79_999, 80_000),
        bar(WED, 10, 3, 80_000, 80_000, 79_988, 79_990),  # worst -66.20: DLL
        bar(WED, 10, 4, 79_990, 79_990, 79_980, 79_982),  # -106.20: above the floor
        bar(WED, 10, 5, 79_982, 79_982, 79_970, 79_972),  # -156.20: MLL floor 49850
        bar(WED, 10, 6, 79_972, 79_972, 79_960, 79_962),
    ]

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {t0: lambda b: [enter("t1", b.close_ns, 10, freeze=b.close_ns + 2 * NS_PER_MINUTE)]},
        )

    run = run_host(tmp_path, t0, ny(WED, 10, 10), bars=bars, accounts=[account(rules)], setup=setup)
    assert statuses(tmp_path) == [
        ("active", "flat_locked", "account_dll"),
        ("flat_locked", "breached", "account_mll"),
    ]
    assert [ln["reason"] for ln in host_log(tmp_path, "account_flatten")] == [
        "account_dll",
        "account_mll",
    ]
    broker = run.host.broker("p1")
    assert broker is not None
    exits = [w for w in broker.book.orders.values() if w.placed.role == "exit"]
    assert len(exits) == 1
    assert len(exits[0].versions) == 1
    assert exits[0].placed.placed_at == ny(WED, 10, 4, 2)


def test_flat_by_flattens_at_the_deadline_with_no_bar_and_blocks_entries(tmp_path: Path) -> None:
    t0 = ny(WED, 16, 0)

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        entries_on_bar(p1, {t0: lambda b: [enter("t1", b.close_ns, 1, stop=200, target=200)]})
        wake_at(p1, ny(WED, 16, 11), lambda now, ctx: [enter("t2", now, 1)])

    run = run_host(
        tmp_path,
        t0,
        ny(WED, 16, 15),
        bars=flat_bars(WED, 16, 0, 9),  # the last bar opens 16:08, arrives 16:09:02
        accounts=[account({"flat_deadline_enabled": True})],
        setup=setup,
    )
    [flatten] = host_log(tmp_path, "account_flatten")
    assert flatten["t_ns"] == ny(WED, 16, 10)
    assert flatten["reason"] == "account_flat_by"
    assert host_log(tmp_path, "account_status") == []  # flat_by is not a status change
    broker = run.host.broker("p1")
    assert broker is not None
    [exit_order] = [w for w in broker.book.orders.values() if w.placed.role == "exit"]
    assert exit_order.placed.placed_at == ny(WED, 16, 10)
    assert rejected(tmp_path) == {"t2": "account_flat_by"}
    assert run.host.account("acct").state.flat_by_done == WED


def test_flat_by_uses_the_early_close_offset(tmp_path: Path) -> None:
    run = run_host(
        tmp_path,
        ny(WED, 10, 0),
        ny(WED, 10, 1),
        accounts=[account({"flat_deadline_enabled": True})],
    )
    host = run.host
    slot = host._accounts["acct"]
    day = host.day
    assert day is not None
    assert host._account_deadline_ns(slot, day) == ny(WED, 16, 10)
    early = replace(day, early_close=time(13, 0))
    assert host._account_deadline_ns(slot, early) == ny(WED, 12, 45)


def test_no_trade_window_refuses_entries_only(tmp_path: Path) -> None:
    """Inside a window: new entries refused; cancels, stop modifies and positions untouched."""
    t0 = ny(WED, 10, 0)
    seen: list[tuple[str, ...]] = []

    def in_window(now: Instant, ctx: Any) -> list[Any]:
        seen.append(ctx.entries_blocked())
        return [enter("t2", now, 1), Cancel("r1"), ModifyStop("t1", 79_965)]

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        entries_on_bar(
            p1,
            {
                t0: lambda b: [
                    enter("t1", b.close_ns, 1),
                    EnterResting("r1", "MNQ", "long", 1, "limit", 79_900, 79_860, ()),
                ]
            },
        )
        wake_at(p1, ny(WED, 10, 6, 30), in_window)

    run = run_host(
        tmp_path,
        t0,
        ny(WED, 10, 9),
        bars=flat_bars(WED, 10, 0, 8),
        accounts=[account({"no_trade_windows": [{"start": "10:05", "end": "10:10"}]})],
        setup=setup,
    )
    assert seen == [("account_no_trade_window",)]
    assert rejected(tmp_path) == {"t2": "account_no_trade_window"}
    updates = run.plugins["p1"].updates
    assert any(u.tag == "r1" and u.status == "cancelled" for u in updates)
    assert any(u.tag == "t1" and u.status == "accepted" and u.role == "stop" for u in updates)
    broker = run.host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    assert host_log(tmp_path, "account_flatten") == []
    assert host_log(tmp_path, "account_status") == []


def test_shared_position_cap_sums_both_members(tmp_path: Path) -> None:
    """A-8: 30 micros open on p1, then 25 more on p2 is past the 50-micro cap; 20 fits."""
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {t0: lambda b: [enter("t1", b.close_ns, 30)]})
        entries_on_bar(
            run.plugins["p2"],
            {
                t0 + NS_PER_MINUTE: lambda b: [
                    enter("u1", b.close_ns, 25),
                    enter("u2", b.close_ns, 20),
                ]
            },
        )

    run_host(
        tmp_path,
        t0,
        ny(WED, 10, 4),
        bars=flat_bars(WED, 10, 0, 3),
        entries=[host_entry("p1", account="acct"), host_entry("p2", account="acct")],
        accounts=[account({"position_cap": {"enabled": True, "micro_equivalents": 50}})],
        setup=setup,
    )
    assert rejected(tmp_path, "p2") == {"u1": "account_max_position"}
    accepted = [ln["tag"] for ln in plugin_log(tmp_path, "p2") if ln["kind"] == "intent_accepted"]
    assert accepted == ["u2"]
    refusal = next(ln for ln in plugin_log(tmp_path, "p2") if ln["kind"] == "intent_rejected")
    assert refusal["level"] == "info"
    assert host_log(tmp_path, "account_flatten") == []


# ---------------------------------------------------------------- equity and exit reasons


def test_equity_after_a_close_between_bars_counts_only_the_open_trade(tmp_path: Path) -> None:
    """A plugin ``Flatten`` at the last close (no bar): the account revalues at the marks."""
    t0 = ny(WED, 10, 0)
    rising = [
        bar(WED, 10, i, 80_000 + 2 * i, 80_001 + 2 * i, 79_999 + 2 * i, 80_002 + 2 * i)
        for i in range(5)
    ]

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        entries_on_bar(p1, {t0: lambda b: [enter("t1", b.close_ns), enter("t2", b.close_ns)]})
        wake_at(
            p1, ny(WED, 10, 5, 30), lambda now, ctx: [Flatten("t1", "plugin_exit", "last_close")]
        )

    run = run_host(tmp_path, t0, ny(WED, 10, 7), bars=rising, setup=setup)
    broker = run.host.broker("p1")
    assert broker is not None
    [t2] = broker.book.trades.values()
    state = run.host.account("acct").state
    mark = state.marks["MNQ"]
    assert mark == 80_010
    open_pnl = t2.realized + t2.unrealized(mark)
    assert state.unrealized == open_pnl
    closed = closed_net(run.plugins["p1"].updates)
    assert state.balance == START + closed
    assert Decimal(account_file(tmp_path)["view"]["equity"]) == state.balance + open_pnl


def test_a_tagged_flatten_names_only_its_tag_and_the_close_pops_it(tmp_path: Path) -> None:
    t0 = ny(WED, 10, 0)
    seen: list[dict[str, str]] = []

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        slot = run.host._by_id["p1"]
        entries_on_bar(
            p1,
            {
                t0: lambda b: [enter("s1", b.close_ns), enter("s2", b.close_ns)],
                ny(WED, 10, 2): lambda b: [Flatten("s1", "news_exit", "next_open")],
            },
        )

        def look(now: Instant, ctx: Any) -> list[Any]:
            seen.append(dict(slot.exit_reasons))
            return []

        wake_at(p1, ny(WED, 10, 3, 30), look)

    run = run_host(tmp_path, t0, ny(WED, 10, 6), bars=flat_bars(WED, 10, 0, 5), setup=setup)
    # set when the flatten was routed (10:03:02), popped when s1's exit filled on the
    # 10:04 bar (10:05:02); the wake at 10:03:30 sits in between
    assert seen == [{"s1": "news_exit"}]
    assert run.host._by_id["p1"].exit_reasons == {}
    assert [u.tag for u in run.plugins["p1"].updates if u.status == "closed"] == ["s1"]
    [line] = trade_lines(tmp_path)
    assert (line["tag"], line["exit_reason"]) == ("s1", "news_exit")


def test_exit_reason_is_set_for_every_open_and_working_tag(tmp_path: Path) -> None:
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        entries_on_bar(
            p1,
            {
                t0: lambda b: [
                    enter("s1", b.close_ns),
                    EnterResting("r1", "MNQ", "long", 1, "limit", 79_900, 79_860, ()),
                ],
            },
        )
        wake_at(p1, ny(WED, 10, 2, 30), lambda now, ctx: [Flatten(None, "news_exit", "next_open")])

    run = run_host(tmp_path, t0, ny(WED, 10, 2, 40), bars=flat_bars(WED, 10, 0, 2), setup=setup)
    assert run.host._by_id["p1"].exit_reasons == {"s1": "news_exit", "r1": "news_exit"}


def test_an_untagged_plugin_flatten_labels_every_open_trade_in_the_journal(
    tmp_path: Path,
) -> None:
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        p1 = run.plugins["p1"]
        entries_on_bar(p1, {t0: lambda b: [enter("s1", b.close_ns), enter("s2", b.close_ns)]})
        wake_at(p1, ny(WED, 10, 2, 30), lambda now, ctx: [Flatten(None, "news_exit", "next_open")])

    run_host(tmp_path, t0, ny(WED, 10, 6), bars=flat_bars(WED, 10, 0, 5), setup=setup)
    lines = trade_lines(tmp_path)
    assert sorted((ln["tag"], ln["leg"], ln["exit_reason"]) for ln in lines) == [
        ("s1", "exit", "news_exit"),
        ("s2", "exit", "news_exit"),
    ]


def test_flat_by_exit_is_journaled_with_its_reason(tmp_path: Path) -> None:
    t0 = ny(WED, 16, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {t0: lambda b: [enter("t1", b.close_ns, 1, stop=200, target=200)]},
        )

    run_host(
        tmp_path,
        t0,
        ny(WED, 16, 14),
        bars=flat_bars(WED, 16, 0, 13),  # bars after the 16:10 deadline fill the exit
        accounts=[account({"flat_deadline_enabled": True})],
        setup=setup,
    )
    [line] = trade_lines(tmp_path)
    assert (line["leg"], line["exit_reason"], line["exit_ns"]) == (
        "exit",
        "account_flat_by",
        ny(WED, 16, 10),  # placed at the 16:10:00 deadline, filled at that bar's open
    )


# ---------------------------------------------------------------- exit reasons across a restart


MLL_BARS = [
    bar(WED, 10, 0, 80_000, 80_001, 79_999, 80_000),
    bar(WED, 10, 1, 80_000, 80_002, 79_975, 79_980),  # fills; worst -131.20 < -100
]
"""10 lots: the MLL breach routes a ``next_open`` exit at 10:02:02; run 1 stops before it fills."""


def _breach_then_stop(tmp_path: Path) -> Run:
    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {ny(WED, 10, 0): lambda b: [enter("t1", b.close_ns, 10)]})

    run = run_host(
        tmp_path,
        ny(WED, 10, 0),
        ny(WED, 10, 2, 30),
        bars=MLL_BARS,
        accounts=[account(MLL_100)],
        setup=setup,
    )
    broker = run.host.broker("p1")
    assert broker is not None
    assert [w.placed.role for w in broker.book.orders.values() if w.placed.role == "exit"]
    return run


def _restart(tmp_path: Path, first: Run, seen: list[dict[str, str]] | None = None) -> Run:
    def setup(run: Run) -> None:
        if seen is not None:
            slot = run.host._by_id["p1"]
            on(run.plugins["p1"], "on_start", lambda ctx: seen.append(dict(slot.exit_reasons)))

    return run_host(
        tmp_path,
        ny(WED, 10, 5),
        ny(WED, 10, 7),
        bars=flat_bars(WED, 10, 5, 1, 79_970),
        accounts=[account(MLL_100)],
        recorded=first.plane.recorded,
        setup=setup,
    )


def sidecar(tmp_path: Path) -> Path:
    return tmp_path / "live-state" / "plugins" / "p1" / "exit_reasons.json"


def test_a_pending_breach_exit_keeps_its_reason_across_a_restart(tmp_path: Path) -> None:
    first = _breach_then_stop(tmp_path)
    doc = json.loads(sidecar(tmp_path).read_text(encoding="utf-8"))
    assert doc == {"version": 1, "exit_reasons": {"t1": "account_mll"}}
    seen: list[dict[str, str]] = []
    _restart(tmp_path, first, seen)
    assert seen == [{"t1": "account_mll"}]
    [line] = trade_lines(tmp_path)
    assert (line["leg"], line["exit_reason"]) == ("exit", "account_mll")
    # popped at the close, and the empty map is written once
    assert json.loads(sidecar(tmp_path).read_text(encoding="utf-8"))["exit_reasons"] == {}


def test_a_lost_sidecar_labels_the_pending_exit_pending_exit_restart(tmp_path: Path) -> None:
    first = _breach_then_stop(tmp_path)
    sidecar(tmp_path).unlink()
    _restart(tmp_path, first)
    [line] = trade_lines(tmp_path)
    assert line["exit_reason"] == "pending_exit_restart"
    assert host_log(tmp_path, "exit_reasons_restore_failed") == []


def test_an_unreadable_sidecar_is_set_aside_and_logged(tmp_path: Path) -> None:
    first = _breach_then_stop(tmp_path)
    sidecar(tmp_path).write_text("{not json", encoding="utf-8")
    _restart(tmp_path, first)
    [failed] = host_log(tmp_path, "exit_reasons_restore_failed")
    assert (failed["level"], failed["plugin"]) == ("warning", "p1")
    assert list(sidecar(tmp_path).parent.glob("exit_reasons.json.bad-*"))
    [line] = trade_lines(tmp_path)
    assert line["exit_reason"] == "pending_exit_restart"


def test_a_stale_sidecar_tag_is_dropped_at_restore(tmp_path: Path) -> None:
    first = _breach_then_stop(tmp_path)
    sidecar(tmp_path).write_text(
        json.dumps({"version": 1, "exit_reasons": {"t1": "account_mll", "gone": "news_exit"}}),
        encoding="utf-8",
    )
    seen: list[dict[str, str]] = []
    _restart(tmp_path, first, seen)
    assert seen == [{"t1": "account_mll"}]


class FailingSidecarWriter(LogWriter):
    def __init__(self) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())

    def write_text(self, path: Any, text: str) -> Path:
        if Path(path).name == "exit_reasons.json":
            raise OSError("disk full")
        return super().write_text(path, text)


def test_sidecar_write_failures_warn_once_and_block_nothing(tmp_path: Path) -> None:
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                t0: lambda b: [enter("t1", b.close_ns, 1, target=8)],
                t0 + 2 * NS_PER_MINUTE: lambda b: [enter("t2", b.close_ns, 1, target=8)],
            },
        )

    rising = [
        bar(WED, 10, i, 80_000 + 4 * i, 80_005 + 4 * i, 79_999 + 4 * i, 80_004 + 4 * i)
        for i in range(6)
    ]
    run = run_host(
        tmp_path, t0, ny(WED, 10, 7), bars=rising, setup=setup, writer=FailingSidecarWriter()
    )
    [warning] = host_log(tmp_path, "exit_reasons_write_failed")
    assert warning["level"] == "warning"
    assert [u.tag for u in run.plugins["p1"].updates if u.status == "closed"] == ["t1", "t2"]
    assert rejected(tmp_path) == {}
    assert (tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME).exists()


# ---------------------------------------------------------------- _record guard (a): Nexus outbox


class OkNexus:
    """A Nexus transport that fills everything (no network)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, body: Mapping[str, Any]) -> NexusResponse:
        self.calls.append((path, dict(body)))
        return NexusResponse(200, {"data": {"fillPrice": "20000.00"}})


MIRRORED = {**account(), "nexus": {"enabled": True, "account": "practice"}}


def mirrored(transport: OkNexus) -> HostResources:
    return HostResources(env=EnvView({}, {}), nexus=transport, nexus_resolved={"acct": "practice"})


class FailingOutboxWriter(LogWriter):
    def __init__(self) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())

    def append_json(self, path: Any, obj: object, *, fsync: bool = False) -> None:
        if Path(path).name == "nexus_outbox.jsonl":
            raise OSError("disk full")
        super().append_json(path, obj, fsync=fsync)


def test_nexus_outbox_write_failure_is_logged_once_and_trading_is_unchanged(
    tmp_path: Path,
) -> None:
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                t0: lambda b: [enter("t1", b.close_ns, 1, target=8)],
                t0 + 2 * NS_PER_MINUTE: lambda b: [enter("t2", b.close_ns, 1, target=8)],
            },
        )

    rising = [
        bar(WED, 10, i, 80_000 + 4 * i, 80_005 + 4 * i, 79_999 + 4 * i, 80_004 + 4 * i)
        for i in range(6)
    ]
    runs: dict[str, Run] = {}
    sent: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for name, writer in (("control", None), ("failing", FailingOutboxWriter())):
        transport = OkNexus()
        runs[name] = run_host(
            tmp_path / name,
            t0,
            ny(WED, 10, 7),
            bars=rising,
            setup=setup,
            accounts=[MIRRORED],
            writer=writer,
            resources=mirrored(transport),
        )
        sent[name] = transport.calls
    [line] = host_log(tmp_path / "failing", "nexus_outbox_write_failed")
    assert (line["level"], line["account"], line["detail"]) == ("error", "acct", "OSError")
    assert host_log(tmp_path / "control", "nexus_outbox_write_failed") == []
    control, failing = runs["control"], runs["failing"]
    assert failing.plugins["p1"].updates == control.plugins["p1"].updates
    b_control, b_failing = control.host.broker("p1"), failing.host.broker("p1")
    assert b_control is not None
    assert b_failing is not None
    assert b_failing.state == b_control.state
    assert failing.host.account("acct").state == control.host.account("acct").state
    # the items stayed in memory and were still sent
    assert len(sent["control"]) == 4
    assert sent["failing"] == sent["control"]


# ---------------------------------------------------------------- NF-3: dispatch latency


REPLAY_MNQ = [b for b in load_bars() if b.instrument == "MNQ"]
"""The 240 MNQ bars of the BC-2 replay fixture (Tue 09:30 to Wed 11:00, one rotation)."""


def run_three_members(
    tmp_path: Path, steps: list[int] | None = None, *, with_account: bool = True
) -> Run:
    """3 plugins on one account over the replay bars, an entry every 15 bars each.

    With ``steps``, the wall time of every dispatch step (``PluginHost._event``) is
    appended to it. The host keeps its real ``perf_ns``, so ``hook_latency`` lines
    are the production ones. ``with_account=False``: the same plugins without
    ``account:`` (the pre-feature host path).
    """

    def entry_at(i: int) -> Callable[[Bar], list[Any]]:
        return lambda b: [enter(f"e{i}", b.close_ns, 1, stop=8, target=8)]

    def setup(run: Run) -> None:
        for p in run.plugins.values():
            entries_on_bar(
                p, {b.open_ns: entry_at(i) for i, b in enumerate(REPLAY_MNQ) if i % 15 == 0}
            )
        if steps is None:
            return
        dispatch = run.host._event

        def timed(event: Any) -> None:
            t = time_ns()
            dispatch(event)
            steps.append(time_ns() - t)

        run.host._event = timed  # type: ignore[method-assign]

    return run_host(
        tmp_path,
        REPLAY_MNQ[0].open_ns,
        REPLAY_MNQ[-1].close_ns + NS_PER_MINUTE,
        bars=REPLAY_MNQ,
        entries=[
            host_entry(pid, account="acct") if with_account else host_entry(pid)
            for pid in ("p1", "p2", "p3")
        ],
        accounts=(account(),) if with_account else (),
        setup=setup,
    )


def test_three_members_on_one_account_log_no_hook_latency(tmp_path: Path) -> None:
    """NF-3: the account checks and journal appends run outside the hooks, so the
    existing ``hook_latency`` budget sees nothing new. The wall-clock budget of a
    whole dispatch step is ``tests/perf/test_plugin_host_accounts_perf.py``."""
    run = run_three_members(tmp_path)
    assert host_log(tmp_path, "hook_latency") == []
    for pid in ("p1", "p2", "p3"):
        assert trade_lines(tmp_path, pid), pid
    assert run.host.account("acct").state.realized != ZERO


# ---------------------------------------------------------------- persistence and restart (A-7)


DLL_50 = {
    "daily_loss_limit": {"enabled": True, "value": "50.00"},
    "maximum_loss_limit": {"enabled": True, "value": "500.00", "mode": "eod_trailing"},
}


def dll_bars(d: date, hh: int) -> list[Bar]:
    return [
        bar(d, hh, 0, 80_000, 80_001, 79_999, 80_000),
        bar(d, hh, 1, 80_000, 80_001, 79_988, 79_990),  # fill; worst -66.20: DLL lock
        bar(d, hh, 2, 79_990, 79_991, 79_989, 79_990),  # the flatten exit fills
        bar(d, hh, 3, 79_990, 79_991, 79_989, 79_990),
    ]


def dll_setup(d: date, hh: int) -> Callable[[Run], None]:
    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {ny(d, hh, 0): lambda b: [enter("t1", b.close_ns, 10)]})

    return setup


def test_restart_mid_day_restores_every_field_and_keeps_the_block(tmp_path: Path) -> None:
    first = run_host(
        tmp_path,
        ny(WED, 10, 0),
        ny(WED, 10, 10),
        bars=dll_bars(WED, 10),
        accounts=[account(DLL_50)],
        setup=dll_setup(WED, 10),
    )
    before = first.host.account("acct").state
    assert before.status == "flat_locked"
    [line] = trade_lines(tmp_path)
    assert (line["leg"], line["exit_reason"]) == ("exit", "account_dll")
    assert before.day_net < ZERO
    assert before.mll_floor == Decimal("49500.00")

    def setup(run: Run) -> None:
        on(run.plugins["p1"], "on_start", lambda ctx: [enter("t9", ctx.now)])

    second = run_host(
        tmp_path,
        ny(WED, 10, 20),
        ny(WED, 10, 21),
        accounts=[account(DLL_50)],
        recorded=first.plane.recorded,
        setup=setup,
    )
    assert second.host.account("acct").state == before
    assert rejected(tmp_path) == {"t9": "account_daily_loss"}
    [restored] = host_log(tmp_path, "account_restored")
    assert restored["status"] == "flat_locked"
    assert host_log(tmp_path, "account_flatten", since=ny(WED, 10, 20)) == []


class CrashAfterAccountWrite(LogWriter):
    """Dies on the ``plugin.json`` write that follows the account's booking of a close."""

    def __init__(self, account_path: Path) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        self.account_path = account_path
        self.crashed = False

    def write_text(self, path: Any, text: str) -> Path:
        if (
            Path(path).name == PLUGIN_STATE_FILE_NAME
            and not self.crashed
            and self.account_path.exists()
        ):
            doc = json.loads(self.account_path.read_text(encoding="utf-8"))
            state = ACCOUNT_CODEC.decode(doc["account"])
            assert isinstance(state, PaperAccountState)
            if state.applied:
                self.crashed = True
                raise Crash
        return super().write_text(path, text)


class Crash(BaseException):
    """The process dies here."""


def test_crash_between_account_and_plugin_writes_books_the_close_once(tmp_path: Path) -> None:
    t0 = ny(WED, 10, 0)
    bars = [
        bar(WED, 10, 0, 80_000, 80_001, 79_999, 80_000),
        bar(WED, 10, 1, 80_000, 80_002, 79_998, 80_001),  # fill
        bar(WED, 10, 2, 80_001, 80_010, 80_000, 80_009),  # tp1 at 80008
    ]
    writer = CrashAfterAccountWrite(tmp_path / "live-state" / "accounts" / "acct" / "account.json")
    clock = FakeClock(t0)
    plane = FakeDataPlane(
        clock,
        [
            (b.close_ns + 2 * NS_PER_SECOND, BarsEvent((b,), b.close_ns + 2 * NS_PER_SECOND))
            for b in bars
        ],
    )
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin},
        [host_entry("p1", account="acct")],
        clock,
        plane,
        accounts=[account()],
        writer=writer,
    )
    p1 = host.plugin("p1")
    assert isinstance(p1, RecordingPlugin)
    entries_on_bar(p1, {t0: lambda b: [enter("t1", b.close_ns, 1, target=8)]})
    with contextlib.suppress(Crash):
        asyncio.run(clock.run(host.run(until_ns=ny(WED, 10, 5))))
    assert writer.crashed
    booked = account_state(tmp_path)
    net = closed_net(p1.updates)
    assert net == Decimal("2.76")
    assert booked.balance == START + net

    second = run_host(tmp_path, ny(WED, 10, 10), ny(WED, 10, 11), recorded=plane.recorded)
    # the replay re-closed the same trade, and ``applied`` skipped it
    assert closed_net(second.plugins["p1"].updates) == net
    state = second.host.account("acct").state
    assert state.balance == START + net
    assert state.realized == net
    assert len(state.applied) == 1


def test_restore_failed_blocks_entries_and_does_not_flatten(tmp_path: Path) -> None:
    t0 = ny(WED, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {t0: lambda b: [enter("t1", b.close_ns, 1, stop=200, target=200)]}
        )

    run_host(tmp_path, t0, ny(WED, 10, 3), bars=flat_bars(WED, 10, 0, 2), setup=setup)
    path = tmp_path / "live-state" / "accounts" / "acct" / "account.json"
    path.write_text("not json", encoding="utf-8")

    def setup2(run: Run) -> None:
        on(run.plugins["p1"], "on_start", lambda ctx: [enter("t9", ctx.now)])

    second = run_host(tmp_path, ny(WED, 10, 10), ny(WED, 10, 11), setup=setup2)
    [failed] = host_log(tmp_path, "account_restore_failed")
    assert failed["level"] == "error"
    assert list(path.parent.glob("account.json.bad-*"))
    state = second.host.account("acct").state
    assert (state.status, state.breach_reason) == ("breached", "restore_failed")
    assert rejected(tmp_path) == {"t9": "account_breached"}
    assert host_log(tmp_path, "account_flatten") == []
    broker = second.host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    assert not any(w.placed.role == "exit" for w in broker.book.orders.values())


class FlakyAccountWriter(LogWriter):
    def __init__(self) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        self.fail = True

    def write_text(self, path: Any, text: str) -> Path:
        if Path(path).name == "account.json" and self.fail:
            raise OSError("disk full")
        return super().write_text(path, text)


def test_account_write_failures_block_entries_until_a_write_succeeds(tmp_path: Path) -> None:
    t0 = ny(WED, 10, 0)
    writer = FlakyAccountWriter()

    def recover(b: Bar) -> list[Any]:
        writer.fail = False  # the write at the end of this step succeeds
        return [enter("t2", b.close_ns)]

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                t0 + NS_PER_MINUTE: lambda b: [enter("t1", b.close_ns)],
                t0 + 2 * NS_PER_MINUTE: recover,
                t0 + 3 * NS_PER_MINUTE: lambda b: [enter("t3", b.close_ns)],
            },
        )

    run_host(
        tmp_path, t0, ny(WED, 10, 5), bars=flat_bars(WED, 10, 0, 4), setup=setup, writer=writer
    )
    failures = host_log(tmp_path, "account_state_write_failed")
    assert len(failures) >= 3
    assert failures[0]["level"] == "error"
    assert [f["failures"] for f in failures] == list(range(1, len(failures) + 1))
    assert rejected(tmp_path) == {
        "t1": "account_state_write_failed",
        "t2": "account_state_write_failed",
    }
    accepted = [ln["tag"] for ln in plugin_log(tmp_path) if ln["kind"] == "intent_accepted"]
    assert accepted == ["t3"]


def test_an_account_no_plugin_uses_is_logged(tmp_path: Path) -> None:
    run = run_host(
        tmp_path,
        ny(WED, 10, 0),
        ny(WED, 10, 1),
        accounts=[account(), account(acct_id="spare")],
    )
    assert run.host.account_ids == ("acct",)
    [unused] = host_log(tmp_path, "account_unused")
    assert unused["account"] == "spare"
    assert not (tmp_path / "live-state" / "accounts" / "spare").exists()


# ---------------------------------------------------------------- BC-3: a pre-feature plugin.json


def _install_fixture(tmp_path: Path) -> dict[str, Any]:
    target = tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME
    target.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE_PATH, target)
    doc: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return doc


def test_restore_pre_feature_plugin_json_without_an_account(tmp_path: Path) -> None:
    doc = _install_fixture(tmp_path)
    run = run_host(
        tmp_path, ny(WED, 10, 5), ny(WED, 10, 6), entries=[host_entry("p1")], accounts=()
    )
    assert host_log(tmp_path, "plugin_restored")
    broker = run.host.broker("p1")
    assert broker is not None
    assert broker.state == BROKER_CODEC.decode(doc["book"])
    assert not (tmp_path / "live-state" / "accounts").exists()


def test_restore_pre_feature_plugin_json_onto_a_fresh_account(tmp_path: Path) -> None:
    doc = _install_fixture(tmp_path)
    run = run_host(
        tmp_path,
        ny(WED, 10, 5),
        ny(WED, 10, 6),
        entries=[host_entry("p1", account="topstep_a")],
        accounts=[{"id": "topstep_a", "preset": "topstep_50k_combine"}],
    )
    assert host_log(tmp_path, "account_fresh_start")
    broker = run.host.broker("p1")
    assert broker is not None
    assert broker.state == BROKER_CODEC.decode(doc["book"])
    [trade] = broker.book.trades.values()
    mark = broker.state.ring["MNQ"][-1].c_t
    assert mark is not None
    state = run.host.account("topstep_a").state
    assert state.unrealized == trade.realized + trade.unrealized(mark)
    assert state.unrealized != ZERO
    assert account_state(tmp_path, "topstep_a").unrealized == state.unrealized


def test_fresh_account_restarted_mid_day_does_not_judge_trades_at_pre_entry_bars(
    tmp_path: Path,
) -> None:
    """A long opened 13:01 (no account yet), then ``account:`` added and a 14:00 restart.

    The 09:00 bar's low (200 ticks under the entry, $1000 on 10 lots) would lock
    the $50 DLL and its high would lift the intraday-trailing peak by $1000 if the
    startup replay valued today's open trade at it. The book already took every
    recorded bar, so the fresh account evaluates none of them.
    """
    t1 = ny(WED, 13, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {t1: lambda b: [enter("t1", b.close_ns, 10, stop=400, target=400)]}
        )

    bars = [bar(WED, 9, 0, 80_000, 80_200, 79_800, 80_000), *flat_bars(WED, 13, 0, 4)]
    first = run_host(
        tmp_path,
        ny(WED, 9, 0),
        ny(WED, 13, 5),
        bars=bars,
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
    )
    rules = {
        "daily_loss_limit": {"enabled": True, "value": "50.00"},
        "maximum_loss_limit": {"enabled": True, "value": "500.00", "mode": "intraday_trailing"},
    }
    second = run_host(
        tmp_path,
        ny(WED, 14, 0),
        ny(WED, 14, 1),
        accounts=[account(rules)],
        recorded=first.plane.recorded,
    )
    day = second.host.day
    assert day is not None
    assert len(list(second.plane.recorded_bars(day))) == len(bars)  # all replayed at startup
    assert host_log(tmp_path, "account_fresh_start")
    broker = second.host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    assert host_log(tmp_path, "account_status") == []
    assert host_log(tmp_path, "account_flatten") == []
    state = second.host.account("acct").state
    assert state.status == "active"
    assert state.day == WED
    # the day's worst open P&L is the startup valuation, not the 09:00 low
    assert state.day_open_pnl_worst == state.open_pnl_day_start == state.unrealized
    assert state.hwm == START
    assert state.last_bar_open == {}


def test_fresh_account_with_a_fresh_and_a_current_member_values_trades_at_their_book(
    tmp_path: Path,
) -> None:
    """Mixed lag: p1 restored with a long from 13:01, p2 new (no ``plugin.json``).

    p2 takes every recorded bar, so the fresh account evaluates them, but p1's
    book is already past them: its trade is valued at its own book's last close,
    never at the 09:00 low or at a mark the 09:00 bar left (close 200 ticks down).
    """
    t1 = ny(WED, 13, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {t1: lambda b: [enter("t1", b.close_ns, 10, stop=400, target=400)]}
        )

    bars = [bar(WED, 9, 0, 80_000, 80_200, 79_800, 79_800), *flat_bars(WED, 13, 0, 4)]
    first = run_host(
        tmp_path,
        ny(WED, 9, 0),
        ny(WED, 13, 5),
        bars=bars,
        entries=[host_entry("p1")],
        accounts=(),
        setup=setup,
    )
    rules = {
        "daily_loss_limit": {"enabled": True, "value": "50.00"},
        "maximum_loss_limit": {"enabled": True, "value": "500.00", "mode": "intraday_trailing"},
    }
    second = run_host(
        tmp_path,
        ny(WED, 14, 0),
        ny(WED, 14, 1),
        entries=[host_entry("p1", account="acct"), host_entry("p2", account="acct")],
        accounts=[account(rules)],
        recorded=first.plane.recorded,
    )
    assert host_log(tmp_path, "account_fresh_start")
    p1 = second.host.broker("p1")
    p2 = second.host.broker("p2")
    assert p1 is not None
    assert p2 is not None
    assert p1.open_tags() == ("t1",)
    assert p2.book.last_bar_open["MNQ"] == bars[-1].open_ns  # p2 took every replayed bar
    assert host_log(tmp_path, "account_status") == []
    assert host_log(tmp_path, "account_flatten") == []
    state = second.host.account("acct").state
    assert state.status == "active"
    assert state.day_open_pnl_worst == state.open_pnl_day_start
    assert state.hwm == START
    assert state.last_bar_open == {"MNQ": bars[-1].open_ns}  # the account did evaluate them


def test_mark_fallback_ring_close_then_entry_price(tmp_path: Path) -> None:
    _install_fixture(tmp_path)
    run = run_host(
        tmp_path, ny(WED, 10, 5), ny(WED, 10, 6), entries=[host_entry("p1")], accounts=()
    )
    broker = run.host.broker("p1")
    assert broker is not None
    [trade] = broker.book.trades.values()
    fresh = run_host(tmp_path / "other", ny(WED, 10, 5), ny(WED, 10, 6)).host.account("acct").state
    ring_close = broker.state.ring["MNQ"][-1].c_t
    assert PluginHost._mark_for(fresh, broker, trade) == ring_close
    newest = broker.state.ring["MNQ"][-1].open_ns
    current = replace(fresh, marks={"MNQ": 1}, last_bar_open={"MNQ": newest})
    assert PluginHost._mark_for(current, broker, trade) == 1
    # an account mark older than the book's newest bar loses to the ring close
    lagging = replace(fresh, marks={"MNQ": 1}, last_bar_open={"MNQ": newest - 1})
    assert PluginHost._mark_for(lagging, broker, trade) == ring_close
    broker.restore(replace(broker.state, ring={}))
    assert PluginHost._mark_for(fresh, broker, trade) == trade.entry_fill.price


# ---------------------------------------------------------------- day rollover


def test_backstop_closes_of_a_shared_account_book_to_the_closing_day(tmp_path: Path) -> None:
    t0 = ny(WED, 17, 50)
    rising = [
        bar(WED, 17, 50 + i, 80_000 + 2 * i, 80_001 + 2 * i, 79_999 + 2 * i, 80_002 + 2 * i)
        for i in range(9)
    ]

    def setup(run: Run) -> None:
        entries_on_bar(run.plugins["p1"], {t0: lambda b: [enter("t1", b.close_ns)]})
        entries_on_bar(run.plugins["p2"], {t0: lambda b: [enter("u1", b.close_ns)]})

    run = run_host(
        tmp_path,
        t0,
        ny(WED, 18, 1),
        bars=rising,
        entries=[host_entry("p1", account="acct"), host_entry("p2", account="acct")],
        setup=setup,
    )
    net = closed_net(run.plugins["p1"].updates) + closed_net(run.plugins["p2"].updates)
    assert net == 2 * Decimal("6.76")  # 16 ticks x 0.50 - 1.24, each
    state = run.host.account("acct").state
    assert state.day == THU
    assert state.day_net == ZERO
    assert state.trading_days == 1
    assert state.best_day == net
    assert state.balance == START + net
    # revalued after the backstop closes: the day-end equity is the balance alone
    assert state.unrealized == ZERO
    assert state.eod_high == state.balance
    assert len(host_log(tmp_path, "host_break_backstop")) == 2
    # journaled under the closing day: _rotate delivers the backstop before start_day(THU)
    journal = trade_lines(tmp_path, "p1") + trade_lines(tmp_path, "p2")
    assert [(ln["plugin"], ln["tag"]) for ln in journal] == [("p1", "t1"), ("p2", "u1")]
    for ln in journal:
        assert (ln["globex_day"], ln["day_source"]) == (WED.isoformat(), "open")
        assert ln["exit_reason"] == "host_break_backstop"
    # the open-day journal nets equal the closed day's net (the account's Wednesday day_net)
    assert sum(Decimal(ln["net"]) for ln in journal) == state.best_day


def test_dll_day_restarted_after_the_rotation_comes_up_active(tmp_path: Path) -> None:
    """DLL on Wed, stopped 17:30, restarted 18:30 (Globex day Thu)."""
    run_host(
        tmp_path,
        ny(WED, 16, 0),
        ny(WED, 17, 30),
        bars=dll_bars(WED, 16),
        accounts=[account(DLL_50)],
        setup=dll_setup(WED, 16),
    )
    second = run_host(tmp_path, ny(WED, 18, 30), ny(WED, 18, 31), accounts=[account(DLL_50)])
    state = second.host.account("acct").state
    assert (state.status, state.day, state.trading_days) == ("active", THU, 1)
    assert state.day_net == ZERO
    assert statuses(tmp_path, since=ny(WED, 18, 30)) == [
        ("flat_locked", "active", "account_day_end")
    ]


def test_dll_day_restarted_in_the_break_stays_locked_until_the_rotation(tmp_path: Path) -> None:
    """DLL on Tue, restarted Tue 17:30 (still Globex day Tue), then the 18:00 rotation."""
    run_host(
        tmp_path,
        ny(TUE, 10, 0),
        ny(TUE, 10, 10),
        bars=dll_bars(TUE, 10),
        accounts=[account(DLL_50)],
        setup=dll_setup(TUE, 10),
    )
    at_start: list[tuple[str, date | None]] = []

    def setup(run: Run) -> None:
        def start(ctx: Any) -> list[Any]:
            s = run.host.account("acct").state
            at_start.append((s.status, s.day))
            return [enter("t9", ctx.now)]

        on(run.plugins["p1"], "on_start", start)

    second = run_host(
        tmp_path, ny(TUE, 17, 30), ny(TUE, 18, 1), accounts=[account(DLL_50)], setup=setup
    )
    assert at_start == [("flat_locked", TUE)]
    assert rejected(tmp_path) == {"t9": "account_daily_loss"}
    state = second.host.account("acct").state
    assert (state.status, state.day, state.trading_days) == ("active", WED, 1)
    lines = host_log(tmp_path, "account_status", since=ny(TUE, 17, 30))
    assert [(ln["old"], ln["new"], ln["t_ns"]) for ln in lines] == [
        ("flat_locked", "active", ny(TUE, 18, 0))
    ]


PASS_AT_1 = {"profit_target": {"enabled": True, "value": "1.00"}}


def friday_win(run: Run) -> None:
    entries_on_bar(
        run.plugins["p1"], {ny(FRI, 10, 0): lambda b: [enter("t1", b.close_ns, 1, target=8)]}
    )


FRIDAY_WIN_BARS = [
    bar(FRI, 10, 0, 80_000, 80_001, 79_999, 80_000),
    bar(FRI, 10, 1, 80_000, 80_002, 79_998, 80_001),  # fill
    bar(FRI, 10, 2, 80_001, 80_010, 80_000, 80_009),  # tp1 at 80008: +2.76
]


def test_friday_killed_before_the_rotate_rolls_once_on_a_saturday_restart(tmp_path: Path) -> None:
    run_host(
        tmp_path,
        ny(FRI, 10, 0),
        ny(FRI, 16, 30),
        bars=FRIDAY_WIN_BARS,
        accounts=[account(PASS_AT_1)],
        setup=friday_win,
    )
    seen: list[PaperAccountState] = []

    def setup(run: Run) -> None:
        on(run.plugins["p1"], "on_start", lambda ctx: seen.append(run.host.account("acct").state))

    second = run_host(
        tmp_path, ny(SAT, 10, 0), ny(SUN, 18, 1), accounts=[account(PASS_AT_1)], setup=setup
    )
    net = Decimal("2.76")
    [sat] = seen
    assert sat.day is None
    assert (sat.trading_days, sat.best_day, sat.status) == (1, net, "passed")
    assert sat.eod_high == START + net == sat.balance
    state = second.host.account("acct").state
    assert (state.day, state.trading_days, state.best_day) == (NEXT_MON, 1, net)
    assert state.day_net == ZERO
    assert statuses(tmp_path) == [("active", "passed", "account_profit_target")]


def test_friday_rotate_then_sunday_open_rolls_once(tmp_path: Path) -> None:
    bars = [
        bar(FRI, 17, 50, 80_000, 80_001, 79_999, 80_000),
        bar(FRI, 17, 51, 80_000, 80_002, 79_998, 80_001),
        bar(FRI, 17, 52, 80_001, 80_010, 80_000, 80_009),
    ]

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {ny(FRI, 17, 50): lambda b: [enter("t1", b.close_ns, 1, target=8)]}
        )

    run = run_host(
        tmp_path,
        ny(FRI, 17, 50),
        ny(SUN, 18, 1),
        bars=bars,
        accounts=[account(PASS_AT_1)],
        setup=setup,
    )
    state = run.host.account("acct").state
    assert (state.day, state.trading_days, state.status) == (NEXT_MON, 1, "passed")
    assert statuses(tmp_path) == [("active", "passed", "account_profit_target")]


def test_weekend_restart_close_books_to_the_ledger_only(tmp_path: Path) -> None:
    """A held position open Friday, killed before the rotate, closed by restart_stale Saturday."""
    t0 = ny(FRI, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {t0: lambda b: [enter("t1", b.close_ns, 1, stop=200, target=200)]}
        )

    up = [bar(FRI, 10, i, 80_000 + i, 80_001 + i, 79_999 + i, 80_001 + i) for i in range(4)]
    run_host(tmp_path, t0, ny(FRI, 16, 30), bars=up, classes={"p1": HoldPlugin}, setup=setup)
    seen: list[PaperAccountState] = []

    def setup2(run: Run) -> None:
        def at_open(day: Any, ctx: Any) -> None:
            seen.append(run.host.account("acct").state)

        on(run.plugins["p1"], "on_day_open", at_open)

    second = run_host(
        tmp_path, ny(SAT, 10, 0), ny(SUN, 18, 1), classes={"p1": HoldPlugin}, setup=setup2
    )
    net = closed_net(second.plugins["p1"].updates)
    assert net == Decimal("0.26")  # 3 ticks x 0.50 - 1.24
    [outside] = host_log(tmp_path, "account_close_outside_day")
    assert outside["account"] == "acct"
    assert len(host_log(tmp_path, "restart_stale")) == 1
    [mon] = seen
    assert (mon.day, mon.day_net, mon.traded_today) == (NEXT_MON, ZERO, False)
    assert mon.balance == START + net
    assert mon.realized == net
    assert mon.trading_days == 0  # Friday had no close; the Saturday close counts for no day


def test_fresh_account_books_a_stale_monday_close_to_tuesday(tmp_path: Path) -> None:
    """NIT-1: ``open_day(today)`` runs at startup, before ``restart_stale``."""
    t0 = ny(MON, 10, 0)

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"], {t0: lambda b: [enter("t1", b.close_ns, 1, stop=200, target=200)]}
        )

    up = [bar(MON, 10, i, 80_000 + i, 80_001 + i, 79_999 + i, 80_001 + i) for i in range(4)]
    run_host(
        tmp_path, t0, ny(MON, 10, 10), bars=up, entries=[host_entry("p1")], accounts=(), setup=setup
    )
    second = run_host(tmp_path, ny(TUE, 10, 0), ny(TUE, 10, 1))
    net = closed_net(second.plugins["p1"].updates)
    assert net == Decimal("0.26")
    state = second.host.account("acct").state
    assert (state.day, state.day_net, state.traded_today) == (TUE, net, True)
    assert state.balance == START + net
    assert host_log(tmp_path, "account_close_outside_day") == []
