"""The plugin status document (``<live-state>/plugins/<id>/status.json``).

Written by the host for **every** plugin, with no plugin code: what the
console's Live page shows at a glance. It is separate from ``plugin.json``
(the restart state) so restore never reads it and a changing field here never
forces a state write. Layout (``STATUS_VERSION`` 1)::

    {"version": 1, "plugin": id, "label": str, "module": str, "brokerage": "host"|"self",
     "updated_ns": int, "day": "YYYY-MM-DD" | null,
     "phase": "faulted"|"blocked"|"in_trade"|"working"|"waiting"|"closed",
     "phase_text": str, "blocks": [str], "next_wake_ns": int | null,
     "positions": [{"tag", "instrument", "side", "qty", "entry", "stop", "targets",
                    "opened_ns", "entry_t", "open_qty", "realized", "fees",
                    "tick_value", "exit_fee"}],
     "working": [{"tag", "instrument", "side", "kind", "role", "qty", "price", "placed_ns"}],
     "today": {"net": str, "entries": int, "closed": int,
               "trades": int} | null,               # trades: deprecated alias of entries
     "recent": [{"at_ns", "kind", "tag", "text"}],      # newest last, at most 25
     "last_log": {"at_ns", "kind"} | null,
     "plugin_status": {...} | null}                       # StrategyPlugin.status()

Prices are futures points (ticks / 4); money is a decimal string. A position
carries what the console needs to value it at a mark (live P&L): ``entry_t``
(the entry fill in ticks), ``open_qty``, ``realized`` (the exits' price P&L so
far minus every fee so far, the entry fee included), ``fees``, ``tick_value``
(dollars per tick per contract) and ``exit_fee`` (commission plus exchange fee
per contract per side, ``null`` when the broker has no costs for it). The
host does not rewrite this file as prices move: marks are in ``marks.json``
(:mod:`fse.live.host.marks`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from fse.engine.types import Money, Order, SetupKey, Trade
from fse.live.host.broker import PluginBrokerState, tag_of
from fse.live.plugins.api import OrderUpdate
from fse.logio.canonical_json import JsonValue
from fse.sim.fills import TICK_VALUE_USD
from fse.timekit import Instant

__all__ = [
    "MAX_RECENT",
    "STATUS_FILE_NAME",
    "STATUS_VERSION",
    "Activity",
    "book_view",
    "build_status",
    "clean_plugin_status",
    "describe_update",
]

STATUS_FILE_NAME: Final = "status.json"
STATUS_VERSION: Final = 1
MAX_RECENT: Final = 25
TICKS_PER_POINT: Final = 4
MAX_SCHEDULE: Final = 64
MAX_FACTS: Final = 16
MAX_TEXT: Final = 300

PHASE_TEXT: Final[Mapping[str, str]] = {
    "faulted": "Stopped: the plugin raised an error and gets no more calls until a restart",
    "blocked": "New entries are blocked",
    "in_trade": "In a trade",
    "working": "Order working, waiting for a fill",
    "waiting": "Waiting for its next setup",
    "closed": "Market closed: waiting for the next Globex day",
}


def _price(ticks: int | None) -> float | None:
    return None if ticks is None else ticks / TICKS_PER_POINT


def _fmt_price(ticks: int | None) -> str:
    p = _price(ticks)
    return "market" if p is None else f"{p:,.2f}"


@dataclass(slots=True)
class Activity:
    """The recent order events of one plugin (kept in memory, saved in status.json)."""

    items: list[dict[str, JsonValue]] = field(default_factory=list)

    def add(self, item: dict[str, JsonValue]) -> None:
        self.items.append(item)
        if len(self.items) > MAX_RECENT:
            del self.items[: len(self.items) - MAX_RECENT]


_EXIT_WORDS: Final[Mapping[str, str]] = {
    "stop": "Stop hit",
    "tp1": "Target 1 hit",
    "tp2": "Target 2 hit",
}


def _side_word(direction: str) -> str:
    return "long" if direction == "long" else "short"


def describe_update(update: OrderUpdate, key: SetupKey | None) -> dict[str, JsonValue] | None:
    """One ``recent`` item for an order update; ``None`` for an update not worth showing."""
    tag = update.tag
    inst = key.instrument if key is not None else None
    side = _side_word(key.direction) if key is not None else None
    what = f"{side} {inst}" if side and inst else tag
    text: str
    if update.status == "accepted":
        text = f"Order accepted: {what} ({tag})"
    elif update.status == "rejected":
        text = f"Order refused ({update.reason or 'no reason given'}): {tag}"
    elif update.status == "filled_entry" and update.fill is not None:
        text = f"Entered {what}: {update.fill.qty} @ {_fmt_price(update.fill.price)} ({tag})"
    elif update.status == "filled_exit" and update.fill is not None:
        how = _EXIT_WORDS.get(update.role or "", "Closed at market")
        text = f"{how}: {update.fill.qty} @ {_fmt_price(update.fill.price)} ({tag})"
    elif update.status == "closed" and update.trade is not None:
        t: Trade = update.trade
        text = f"Trade closed {what}: net ${t.net:,.2f}, {t.r_multiple:+.2f} R ({tag})"
    elif update.status == "expired":
        text = f"Entry expired unfilled ({update.reason or 'expired'}): {tag}"
    elif update.status == "cancelled":
        text = f"Entry cancelled ({update.reason or 'by the plugin'}): {tag}"
    else:
        return None
    item: dict[str, JsonValue] = {
        "at_ns": update.at,
        "kind": update.status,
        "tag": tag,
        "text": text,
    }
    if update.status == "closed" and update.trade is not None:
        item["net"] = str(update.trade.net)
    return item


def _order_view(order: Order) -> dict[str, JsonValue]:
    return {
        "tag": tag_of(order.setup_key) if order.setup_key is not None else order.client_id,
        "instrument": order.instrument,
        "side": order.side,
        "kind": order.kind,
        "role": order.role,
        "qty": order.qty,
        "price": _price(order.price),
        "placed_ns": order.placed_at,
    }


def book_view(
    state: PluginBrokerState, exit_fee: Callable[[str], Money | None] | None = None
) -> dict[str, JsonValue]:
    """Open positions (with their working stop and targets) and the other working orders.

    ``exit_fee`` gives the fee per contract per side of an instrument.
    """
    book = state.book
    legs: dict[SetupKey, list[Order]] = {}
    for order in book.working_orders():
        if order.setup_key is not None:
            legs.setdefault(order.setup_key, []).append(order)
    positions: list[JsonValue] = []
    for key, trade in book.trades.items():
        own = legs.get(key, [])
        stops = [o.price for o in own if o.role == "stop" and o.price is not None]
        targets = [_price(o.price) for o in own if o.role in ("tp1", "tp2") and o.price is not None]
        tick_value = TICK_VALUE_USD.get(trade.instrument)
        fee = exit_fee(trade.instrument) if exit_fee is not None else None
        positions.append(
            {
                "tag": tag_of(key),
                "instrument": trade.instrument,
                "side": _side_word(trade.direction),
                "qty": trade.open_qty,
                "entry": _price(trade.entry_fill.price),
                "stop": _price(stops[0]) if stops else _price(trade.initial_stop),
                "targets": list(targets),
                "opened_ns": trade.entry_fill.bar_open_ns,
                "entry_t": trade.entry_fill.price,
                "open_qty": trade.open_qty,
                "realized": str(trade.realized),
                "fees": str(trade.fees),
                "tick_value": None if tick_value is None else str(tick_value),
                "exit_fee": None if fee is None else str(fee),
            }
        )
    working: list[JsonValue] = [
        _order_view(o)
        for o in book.working_orders()
        if o.setup_key is None or o.setup_key not in book.trades
    ]
    return {"positions": positions, "working": working}


def _text(value: JsonValue) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:MAX_TEXT]
    return None


def _when(value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    text = _text(value.get("text"))
    if text is None:
        return None
    at = value.get("at_ns")
    return {"at_ns": at if isinstance(at, int) and not isinstance(at, bool) else None, "text": text}


def clean_plugin_status(raw: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The known keys of a ``StrategyPlugin.status()`` value, size-capped."""
    out: dict[str, JsonValue] = {}
    for key in ("summary", "now"):
        text = _text(raw.get(key))
        if text is not None:
            out[key] = text
    for key in ("next", "last"):
        when = _when(raw.get(key))
        if when is not None:
            out[key] = when
    schedule = raw.get("schedule")
    if isinstance(schedule, Sequence) and not isinstance(schedule, str):
        rows: list[JsonValue] = []
        for row in list(schedule)[:MAX_SCHEDULE]:
            if not isinstance(row, Mapping):
                continue
            label = _text(row.get("label"))
            if label is None:
                continue
            at = row.get("at_ns")
            rows.append(
                {
                    "at_ns": at if isinstance(at, int) and not isinstance(at, bool) else None,
                    "label": label,
                    "state": _text(row.get("state")) or "planned",
                }
            )
        out["schedule"] = rows
    facts = raw.get("facts")
    if isinstance(facts, Sequence) and not isinstance(facts, str):
        pairs: list[JsonValue] = []
        for pair in list(facts)[:MAX_FACTS]:
            if (
                isinstance(pair, Sequence)
                and not isinstance(pair, str)
                and len(pair) == 2
                and _text(pair[0]) is not None
            ):
                pairs.append([_text(pair[0]), str(pair[1])[:MAX_TEXT]])
        out["facts"] = pairs
    return out


def _phase(
    *,
    faulted: bool,
    blocks: Sequence[str],
    view: Mapping[str, JsonValue] | None,
    day: str | None,
) -> str:
    if faulted:
        return "faulted"
    positions = view.get("positions") if view else None
    working = view.get("working") if view else None
    if positions:
        return "in_trade"
    if blocks:
        return "blocked"
    if working:
        return "working"
    if day is None:
        return "closed"
    return "waiting"


def build_status(
    *,
    plugin_id: str,
    label: str,
    module: str,
    brokerage: str,
    now: Instant,
    faulted: str | None,
    blocks: Sequence[str],
    state: PluginBrokerState | None,
    day: str | None,
    next_wake_ns: Instant | None,
    recent: Sequence[dict[str, JsonValue]],
    last_log: dict[str, JsonValue] | None,
    plugin_status: dict[str, JsonValue] | None,
    exit_fee: Callable[[str], Money | None] | None = None,
) -> dict[str, JsonValue]:
    """The status document (module notes); ``updated_ns`` is ``now``."""
    view = book_view(state, exit_fee) if state is not None else None
    phase = _phase(faulted=faulted is not None, blocks=blocks, view=view, day=day)
    text = PHASE_TEXT[phase]
    if phase == "blocked":
        text = f"{text}: {', '.join(blocks)}"
    elif phase == "faulted" and faulted:
        text = f"{text} ({faulted})"
    return {
        "version": STATUS_VERSION,
        "plugin": plugin_id,
        "label": label,
        "module": module,
        "brokerage": brokerage,
        "updated_ns": now,
        "day": day,
        "phase": phase,
        "phase_text": text,
        "blocks": list(blocks),
        "next_wake_ns": next_wake_ns,
        "positions": [] if view is None else view["positions"],
        "working": [] if view is None else view["working"],
        "today": None
        if state is None
        else {
            "net": str(state.day_net),
            "entries": state.day_trades,
            "closed": state.day_closed,
            "trades": state.day_trades,
        },
        "recent": list(recent),
        "last_log": last_log,
        "plugin_status": plugin_status,
    }
