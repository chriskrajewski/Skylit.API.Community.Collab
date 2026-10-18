"""Paper runner files built with the engine's own types, codecs and journal writer.

Books go through ``BROKER_CODEC``, accounts through ``ACCOUNT_CODEC`` and journal
lines through ``fse.live.host.journal.journal_lines``, so the API reads exactly
what the host writes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from fse.engine.types import Bar, Fill, Order, SetupKey, Trade
from fse.live.host.accounts import ACCOUNT_CODEC, PaperAccount, PaperAccountState
from fse.live.host.broker import BROKER_CODEC, PluginBrokerState
from fse.live.host.config import AccountEntry
from fse.live.host.journal import JournalDay, JournalSlot, journal_lines
from fse.live.plugins.api import OrderUpdate
from fse.logio import canonical_json
from fse.sim.fills import OpenTrade, SimBook, WorkingOrder
from fse.timekit import NS_PER_MINUTE, ny_instant

MON, TUE, WED = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)
TICK_USD = Decimal("0.50")  # MNQ
HALF_RT = Decimal("0.62")
RISK_TICKS = 40


def ny(d: date, hour: int, minute: int = 0) -> int:
    return ny_instant(d, time(hour, minute))


def key(plugin: str, tag: str, session: date, direction: str = "long") -> SetupKey:
    return SetupKey("MNQ", f"{plugin}:{tag}", 0.0, direction, session, 1)  # type: ignore[arg-type]


def mk_trade(
    plugin: str,
    tag: str,
    session: date,
    entry_ns: int,
    entry_t: int,
    legs: Sequence[tuple[str, int, int, int]],
    *,
    direction: str = "long",
) -> Trade:
    """A closed MNQ trade; ``legs`` = ``(leg, exit_ns, exit_t, qty)``, nets exact."""
    sign = 1 if direction == "long" else -1
    qty = sum(q for _, _, _, q in legs)
    entry = Fill(f"{plugin}:{tag}:entry", entry_ns, entry_t, qty, HALF_RT * qty)
    exits = tuple(Fill(f"{plugin}:{tag}:{leg}", t, px, q, HALF_RT * q) for leg, t, px, q in legs)
    gross = sum(((px - entry_t) * sign * q * TICK_USD for _, _, px, q in legs), Decimal(0))
    net = gross - entry.fees - sum((f.fees for f in exits), Decimal(0))
    r = RISK_TICKS * TICK_USD * qty
    return Trade(
        key(plugin, tag, session, direction),
        entry,
        entry_t - RISK_TICKS * sign,
        qty,
        exits,
        net,
        r,
        (net / r).quantize(Decimal("0.0001")),
        any(leg == "tp1" for leg, _, _, _ in legs),
        Decimal("0.25"),
        Decimal("1.10"),
        0,
        False,
    )


def simple(
    plugin: str, tag: str, day: date, ticks: int, *, hour: int = 10, minute: int = 0
) -> Trade:
    """One contract, one leg, ``ticks`` in the trade's favour (negative: a loss)."""
    t0 = ny(day, hour, minute)
    leg = "tp1" if ticks > 0 else "stop"
    return mk_trade(
        plugin, tag, day, t0, 80_000, [(leg, t0 + 5 * NS_PER_MINUTE, 80_000 + ticks, 1)]
    )


def lines_of(
    trade: Trade, *, account: str | None = None, day: date | None = None
) -> list[dict[str, Any]]:
    plugin, tag = trade.setup_key.pattern.split(":")
    update = OrderUpdate(
        tag, "closed", None, trade.exits[-1], "exit", trade, None, trade.exits[-1].bar_open_ns
    )
    found = journal_lines(
        JournalSlot(plugin, account, {}),
        update,
        JournalDay(day or trade.setup_key.session, "open"),
        None,
    )
    return [dict(x) for x in found]


def write_journal(
    runs: Path, run: str, plugin: str, lines: Iterable[Mapping[str, Any]], *, raw: str = ""
) -> Path:
    path = runs / run / "plugins" / plugin / "trades.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(canonical_json.dumps(line) + "\n")
        fh.write(raw)
    return path


def journal(
    runs: Path,
    run: str,
    trades: Iterable[Trade],
    *,
    account: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        plugin = t.setup_key.pattern.split(":")[0]
        lines = lines_of(t, account=account)
        write_journal(runs, run, plugin, lines)
        out += lines
    return out


def host_log(runs: Path, run: str, lines: Iterable[Mapping[str, Any]]) -> Path:
    path = runs / run / "host_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return path


def day_open(d: date) -> dict[str, Any]:
    return {"t_ns": ny(d, 0, 0), "kind": "day_open", "level": "info", "day": d.isoformat()}


def bar(open_ns: int, c_t: int, *, low: int | None = None, high: int | None = None) -> Bar:
    lo, hi = low if low is not None else c_t - 4, high if high is not None else c_t + 4
    return Bar(
        "MNQ",
        "MNQZ6",
        60,
        open_ns,
        open_ns + 60 * 1_000_000_000,
        c_t * 0.25,
        hi * 0.25,
        lo * 0.25,
        c_t * 0.25,
        0.0,
        c_t,
        hi,
        lo,
        c_t,
        "projectx",
    )


def open_trade(
    plugin: str, tag: str, day: date, entry_ns: int, entry_t: int, qty: int
) -> OpenTrade:
    k = key(plugin, tag, day)
    return OpenTrade(
        setup_key=k,
        instrument="MNQ",
        direction="long",
        entry_fill=Fill(f"{plugin}:{tag}:entry", entry_ns, entry_t, qty, HALF_RT * qty),
        initial_stop=entry_t - RISK_TICKS,
        qty_at_entry=qty,
        open_qty=qty,
        last_close_ns=entry_ns,
        fees=HALF_RT * qty,
    )


def stop_order(plugin: str, tag: str, day: date, price_t: int, qty: int, at: int) -> WorkingOrder:
    order = Order(
        f"{plugin}:{tag}:stop",
        key(plugin, tag, day),
        "MNQ",
        "sell",
        "stop",
        qty,
        price_t,
        at,
        "stop",
    )
    return WorkingOrder(((at, order),))


def write_book(
    live_state: Path,
    plugin: str,
    *,
    day: date | None = WED,
    day_net: str = "0.00",
    trades: Sequence[OpenTrade] = (),
    orders: Sequence[WorkingOrder] = (),
    ring: Sequence[Bar] = (),
    seq: int = 1,
) -> Path:
    state = PluginBrokerState(
        book=SimBook(
            orders={w.placed.client_id: w for w in orders},
            trades={t.setup_key: t for t in trades},
        ),
        ring={"MNQ": tuple(ring)} if ring else {},
        day=day,
        day_net=Decimal(day_net),
    )
    path = live_state / "plugins" / plugin / "plugin.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "seq": seq, "book": BROKER_CODEC.encode(state), "plugin": {}}
    path.write_text(canonical_json.dumps(doc) + "\n", encoding="utf-8")
    return path


def write_marks(
    live_state: Path,
    *,
    quote: tuple[int, int] | None = None,
    bar_close: tuple[int, int] | None = None,
    instrument: str = "MNQ",
) -> Path:
    """The host's ``marks.json`` (``fse.live.host.marks``): ``quote`` and ``bar_close`` are
    ``(price_t, at_ns)`` of the latest quote and 1-minute close."""
    entry: dict[str, Any] = {"quote": None, "bar": None}
    if quote is not None:
        entry["quote"] = {
            "price_t": quote[0],
            "price": quote[0] / 4,
            "basis": "last",
            "t_ns": quote[1],
            "received_ns": quote[1],
            "contract_id": "CON.F.US.MNQ.Z26",
        }
    if bar_close is not None:
        entry["bar"] = {
            "price_t": bar_close[0],
            "price": bar_close[0] / 4,
            "close_ns": bar_close[1],
            "contract": "MNQZ6",
        }
    path = live_state / "marks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "updated_ns": max(quote or (0, 0), bar_close or (0, 0))[1]}
    path.write_text(json.dumps({**doc, "marks": {instrument: entry}}), encoding="utf-8")
    return path


def write_status(live_state: Path, plugin: str, *, exit_fee: Decimal | None = HALF_RT) -> Path:
    """The host's ``status.json`` of the book on disk, built by the host's ``build_status``."""
    from fse.live.host.status import build_status

    d = live_state / "plugins" / plugin
    state = BROKER_CODEC.decode(json.loads((d / "plugin.json").read_text())["book"])
    doc = build_status(
        plugin_id=plugin,
        label=plugin,
        module=f"tests.{plugin}",
        brokerage="host",
        now=0,
        faulted=None,
        blocks=(),
        state=state,
        day=None if state.day is None else state.day.isoformat(),
        next_wake_ns=None,
        recent=[],
        last_log=None,
        plugin_status=None,
        exit_fee=lambda _instrument: exit_fee,
    )
    path = d / "status.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def account_state(
    acct_id: str, plugins: Sequence[str], preset: str = "topstep_50k_combine", **change: Any
) -> PaperAccountState:
    rules = AccountEntry.model_validate({"id": acct_id, "preset": preset}).rules
    return replace(PaperAccount.fresh(acct_id, rules, plugins).state, **change)


def write_account(
    live_state: Path, state: PaperAccountState, view: Mapping[str, Any] | None = None
) -> Path:
    path = live_state / "accounts" / state.id / "account.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "seq": 1, "account": ACCOUNT_CODEC.encode(state), "view": view or {}}
    path.write_text(canonical_json.dumps(doc) + "\n", encoding="utf-8")
    return path
