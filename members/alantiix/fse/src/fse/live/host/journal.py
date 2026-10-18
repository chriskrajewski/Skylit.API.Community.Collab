"""The host-written trade journal, ``<run>/plugins/<id>/trades.jsonl`` (paper-analytics §4).

:meth:`PluginHost._record` turns every :class:`OrderUpdate` of a host-brokered
plugin into journal lines (``v: 1``), one ``O_APPEND`` write each, before the
update reaches the plugin:

- a ``fill`` line per entry or exit fill (``filled_entry`` / ``filled_exit``);
- a ``trade`` line per exit fill of a closed trade (``closed``), with the
  trade-level fields repeated on every leg so :func:`journal_trades` can
  rebuild the engine :class:`~fse.engine.types.Trade` from the group.

Money is a plain decimal string with two places (``format(m, ".2f")``);
other decimals (``r_multiple``, ``mae_r``, ``mfe_r``) are written exactly
(``format(d, "f")``). Prices are integer ticks plus a decimal ``*_price``
(ticks x 0.25). Every line carries ``globex_day`` (never ``null``) and
``day_source``: ``"open"`` when the slot's broker had a Globex day, else
``"after_close"`` (the weekend gap; the line is dated to the last closed day).

This module is pure: it builds lines and rebuilds trades, it does no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final, Literal

from fse.engine.types import Direction, Fill, Money, SetupKey, Ticks, Trade
from fse.live.host.dayclock import next_open, previous_globex_day
from fse.live.plugins.api import OrderUpdate
from fse.logio.canonical_json import JsonValue
from fse.sim.fills import MONEY_CONTEXT, tick_value
from fse.timekit import Instant, SessionCalendar

__all__ = [
    "JOURNAL_FILE_NAME",
    "JOURNAL_VERSION",
    "PENDING_EXIT_RESTART",
    "DaySource",
    "JournalDay",
    "JournalSlot",
    "journal_day",
    "journal_lines",
    "journal_trades",
]

JOURNAL_FILE_NAME: Final = "trades.jsonl"
JOURNAL_VERSION: Final = 1
PENDING_EXIT_RESTART: Final = "pending_exit_restart"
"""The exit reason of an exit working at a restart whose real reason was not restored."""

_CENT: Final = Decimal("0.01")
_R_PLACES: Final = Decimal("0.0001")
_TICK_POINTS: Final = Decimal("0.25")
_ZERO: Final = Decimal(0)
_EXIT_LEGS: Final = frozenset({"stop", "tp1", "tp2", "exit"})

type DaySource = Literal["open", "after_close"]


@dataclass(frozen=True, slots=True)
class JournalDay:
    """The Globex day a line is dated to and where that date came from."""

    day: date
    source: DaySource


@dataclass(frozen=True, slots=True)
class JournalSlot:
    """What the journal needs to know about the plugin that produced an update."""

    plugin: str
    account: str | None
    exit_reasons: Mapping[str, str]
    """Tag -> reason of the flatten routed for it (``slot.exit_reasons``)."""


def journal_day(
    broker_day: date | None, at: Instant, sessions: SessionCalendar | None
) -> JournalDay:
    """The broker's Globex day; in the weekend gap the last closed Globex day (never ``None``)."""
    if broker_day is not None:
        return JournalDay(broker_day, "open")
    last = previous_globex_day(next_open(at, sessions), sessions)
    return JournalDay(last.day, "after_close")


# ---------------------------------------------------------------- writing


def _money(m: Money) -> str:
    return format(m, ".2f")


def _exact(d: Decimal) -> str:
    return format(d, "f")


def _price(t: Ticks) -> str:
    return _money(MONEY_CONTEXT.multiply(Decimal(t), _TICK_POINTS))


def _fill_doc(f: Fill) -> dict[str, JsonValue]:
    return {
        "client_id": f.client_id,
        "bar_open_ns": f.bar_open_ns,
        "price_t": f.price,
        "qty": f.qty,
        "fees": _money(f.fees),
    }


def _setup_key_doc(k: SetupKey) -> dict[str, JsonValue]:
    return {
        "instrument": k.instrument,
        "pattern": k.pattern,
        "source_strike": k.source_strike,
        "direction": k.direction,
        "session": k.session.isoformat(),
        "tap_seq": k.tap_seq,
    }


def _side(direction: Direction, leg: str) -> str:
    entry = "buy" if direction == "long" else "sell"
    if leg == "entry":
        return entry
    return "sell" if entry == "buy" else "buy"


def _leg_of(plugin: str, tag: str, client_id: str) -> str:
    """The leg of an exit fill id ``<plugin>:<tag>:<leg>``; a close_setup id is ``exit``."""
    prefix = f"{plugin}:{tag}:"
    if client_id.startswith(prefix):
        leg = client_id[len(prefix) :]
        if leg in _EXIT_LEGS:
            return leg
    return "exit"


def _exit_reason(slot: JournalSlot, tag: str, leg: str, client_id: str) -> str:
    if leg == "stop":
        return "stop"
    if leg in ("tp1", "tp2"):
        return "target"
    reason = slot.exit_reasons.get(tag)
    if reason is not None:
        return reason
    head, sep, rest = client_id.partition(":")
    if sep and rest.startswith(f"{slot.plugin}:{tag}:"):
        return head  # close_setup: "<reason>:<entry client id>"
    return "market"


def _allocate(total: Money, qtys: list[int], whole: int) -> list[Money]:
    """``total`` split pro rata by qty in cents; the rounding remainder on the last part."""
    out: list[Money] = []
    left = total
    for q in qtys[:-1]:
        part = MONEY_CONTEXT.divide(MONEY_CONTEXT.multiply(total, Decimal(q)), Decimal(whole))
        part = part.quantize(_CENT, rounding=ROUND_HALF_EVEN, context=MONEY_CONTEXT)
        out.append(part)
        left = MONEY_CONTEXT.subtract(left, part)
    out.append(left)
    return out


def _fill_line(
    slot: JournalSlot, u: OrderUpdate, fill: Fill, key: SetupKey, day: JournalDay
) -> dict[str, JsonValue]:
    leg = u.role if u.role is not None else "exit"
    detail = u.detail or {}
    return {
        "v": JOURNAL_VERSION,
        "kind": "fill",
        "plugin": slot.plugin,
        "account": slot.account,
        "tag": u.tag,
        "leg": leg,
        "client_id": fill.client_id,
        "instrument": key.instrument,
        "side": _side(key.direction, leg),
        "qty": fill.qty,
        "bar_open_ns": fill.bar_open_ns,
        "price_t": fill.price,
        "price": _price(fill.price),
        "fees": _money(fill.fees),
        "globex_day": day.day.isoformat(),
        "day_source": day.source,
        "submit_quote_t": detail.get("submit_quote_t"),
        "fill_minus_quote_t": detail.get("fill_minus_quote_t"),
    }


def _trade_lines(
    slot: JournalSlot, tag: str, t: Trade, day: JournalDay
) -> list[dict[str, JsonValue]]:
    key = t.setup_key
    entry = t.entry_fill
    sign = 1 if key.direction == "long" else -1
    tv = tick_value(key.instrument)
    entry_fees = _allocate(entry.fees, [x.qty for x in t.exits], t.qty_at_entry)
    trade_id = f"{entry.client_id}@{entry.bar_open_ns}"
    common: dict[str, JsonValue] = {
        "trade_id": trade_id,
        "plugin": slot.plugin,
        "account": slot.account,
        "tag": tag,
        "instrument": key.instrument,
        "side": key.direction,
        "qty_at_entry": t.qty_at_entry,
        "entry_ns": entry.bar_open_ns,
        "entry_t": entry.price,
        "entry_price": _price(entry.price),
        "initial_stop_t": t.initial_stop,
        "trade_r": _money(t.r),
        "trade_net": _money(t.net),
        "r_multiple": _exact(t.r_multiple),
        "mae_r": _exact(t.mae_r),
        "mfe_r": _exact(t.mfe_r),
        "reached_tp1": t.reached_tp1,
        "missing_bars": t.missing_bars,
        "entry_day": key.session.isoformat(),
        "globex_day": day.day.isoformat(),
        "day_source": day.source,
        "entry_fill": _fill_doc(entry),
        "setup_key": _setup_key_doc(key),
    }
    lines: list[dict[str, JsonValue]] = []
    for x, alloc in zip(t.exits, entry_fees, strict=True):
        leg = _leg_of(slot.plugin, tag, x.client_id)
        ticks = (x.price - entry.price) * sign * x.qty
        gross = MONEY_CONTEXT.multiply(Decimal(ticks), tv)
        fees = MONEY_CONTEXT.add(x.fees, alloc)
        net = MONEY_CONTEXT.subtract(gross, fees)
        leg_r = MONEY_CONTEXT.divide(
            MONEY_CONTEXT.multiply(t.r, Decimal(x.qty)), Decimal(t.qty_at_entry)
        )
        r: JsonValue = (
            None
            if leg_r == _ZERO
            else _exact(
                MONEY_CONTEXT.divide(net, leg_r).quantize(
                    _R_PLACES, rounding=ROUND_HALF_EVEN, context=MONEY_CONTEXT
                )
            )
        )
        lines.append(
            {
                "v": JOURNAL_VERSION,
                "kind": "trade",
                "line_id": f"{x.client_id}@{x.bar_open_ns}",
                **common,
                "qty": x.qty,
                "exit_ns": x.bar_open_ns,
                "exit_t": x.price,
                "exit_price": _price(x.price),
                "gross": _money(gross),
                "fees": _money(fees),
                "net": _money(net),
                "exit_reason": _exit_reason(slot, tag, leg, x.client_id),
                "leg": leg,
                "r": r,
                "client_id": x.client_id,
                "exit_fill": _fill_doc(x),
            }
        )
    return lines


def journal_lines(
    slot: JournalSlot, update: OrderUpdate, day: JournalDay, key: SetupKey | None
) -> list[dict[str, JsonValue]]:
    """The journal lines of one update (``[]`` for one without a fill or a close).

    ``key`` is the Setup_Key of the update's tag (instrument and direction of a
    fill line); a ``closed`` update uses its own trade's key. ``ValueError``
    when a fill line has no key.
    """
    if update.status == "closed":
        if update.trade is None:
            raise ValueError(f"closed update of {update.tag!r} has no trade")
        return _trade_lines(slot, update.tag, update.trade, day)
    if update.status in ("filled_entry", "filled_exit") and update.fill is not None:
        if key is None:
            raise ValueError(f"no Setup_Key for the fill of {update.tag!r}")
        return [_fill_line(slot, update, update.fill, key, day)]
    return []


# ---------------------------------------------------------------- reading


def _str(line: Mapping[str, object], name: str) -> str:
    v = line[name]
    if not isinstance(v, str):
        raise ValueError(f"{name} is not a string")
    return v


def _int(line: Mapping[str, object], name: str) -> int:
    v = line[name]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{name} is not an integer")
    return v


def _fill_from(doc: object) -> Fill:
    if not isinstance(doc, Mapping):
        raise ValueError("a fill is not an object")
    return Fill(
        client_id=_str(doc, "client_id"),
        bar_open_ns=_int(doc, "bar_open_ns"),
        price=_int(doc, "price_t"),
        qty=_int(doc, "qty"),
        fees=Decimal(_str(doc, "fees")),
    )


def _key_from(doc: object) -> SetupKey:
    if not isinstance(doc, Mapping):
        raise ValueError("setup_key is not an object")
    strike = doc["source_strike"]
    if isinstance(strike, bool) or not isinstance(strike, int | float):
        raise ValueError("source_strike is not a number")
    direction = _str(doc, "direction")
    if direction not in ("long", "short"):
        raise ValueError(f"direction {direction!r}")
    return SetupKey(
        instrument=_str(doc, "instrument"),
        pattern=_str(doc, "pattern"),
        source_strike=float(strike),
        direction="long" if direction == "long" else "short",
        session=date.fromisoformat(_str(doc, "session")),
        tap_seq=_int(doc, "tap_seq"),
    )


def journal_trades(lines: Iterable[Mapping[str, object]]) -> list[Trade]:
    """Rebuild the engine trades from ``trade`` lines (grouped by ``trade_id``, deduped by
    ``line_id``), in the order each trade's first leg appears. Other kinds are skipped;
    a malformed line raises ``ValueError``/``KeyError``."""
    groups: dict[str, list[Mapping[str, object]]] = {}
    seen: set[str] = set()
    for line in lines:
        if line.get("kind") != "trade":
            continue
        line_id = _str(line, "line_id")
        if line_id in seen:
            continue
        seen.add(line_id)
        groups.setdefault(_str(line, "trade_id"), []).append(line)
    out: list[Trade] = []
    for legs in groups.values():
        first = legs[0]
        reached = first["reached_tp1"]
        if not isinstance(reached, bool):
            raise ValueError("reached_tp1 is not a boolean")
        out.append(
            Trade(
                setup_key=_key_from(first["setup_key"]),
                entry_fill=_fill_from(first["entry_fill"]),
                initial_stop=_int(first, "initial_stop_t"),
                qty_at_entry=_int(first, "qty_at_entry"),
                exits=tuple(_fill_from(leg["exit_fill"]) for leg in legs),
                net=Decimal(_str(first, "trade_net")),
                r=Decimal(_str(first, "trade_r")),
                r_multiple=Decimal(_str(first, "r_multiple")),
                reached_tp1=reached,
                mae_r=Decimal(_str(first, "mae_r")),
                mfe_r=Decimal(_str(first, "mfe_r")),
                missing_bars=_int(first, "missing_bars"),
                shadow=False,
            )
        )
    return out
