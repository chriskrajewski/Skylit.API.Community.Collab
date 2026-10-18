"""Decision cards (``<run>/plugins/<id>/decision_cards.jsonl``).

A plugin log line carrying a ``"card"`` object is a decision card (built with
:func:`fse.live.plugins.api.decision_card`). When the plugin's runner entry has
``cards: true`` (the default) the host cleans the card with :func:`clean_card`
and appends ``{"t_ns", "plugin", "kind", "card"}`` to :data:`CARDS_FILE_NAME`
in the plugin's out dir; the console's Cards page reads that file. The plugin
log line itself is written unchanged either way, and cards never affect
trading.

Card (``CARD_VERSION`` 1; prices in futures points)::

    {"id": str, "title": str, "as_of_ns": int,
     "action": "enter"|"skip"|"hold"|"exit"|"result"|"info",
     "instrument": str|null, "symbol": str|null, "side": "long"|"short"|null,
     "qty": int|null, "entry": float|null, "stop": float|null, "targets": [float],
     "reason": str|null, "ref": str|null, "tag": str|null,
     "result": {"net", "exit_reason", "r", "ticks"}|null,
     "fields": [[label, value]],
     "spot": float|null, "dayPct": float|null, "rangePct": float|null,
     "gates": str|null, "bars": [{"o","h","l","c"}],
     "odds": {"label", "items"}|null,
     "ladder": [{"kind","level","strike?","value?","label?"}],
     "narration": str|null}

Like :func:`fse.live.host.status.clean_plugin_status`: known keys only,
strings stripped and capped, numbers finite, lists capped.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Final

from fse.logio.canonical_json import JsonValue

__all__ = [
    "CARDS_FILE_NAME",
    "CARD_ACTIONS",
    "CARD_VERSION",
    "MAX_BARS",
    "MAX_FIELDS",
    "MAX_LADDER",
    "MAX_TARGETS",
    "clean_card",
]

CARDS_FILE_NAME: Final = "decision_cards.jsonl"
CARD_VERSION: Final = 1
CARD_ACTIONS: Final = frozenset({"enter", "skip", "hold", "exit", "result", "info"})
MAX_TARGETS: Final = 4
MAX_FIELDS: Final = 24
MAX_BARS: Final = 40
MAX_LADDER: Final = 32
MAX_TITLE: Final = 120
MAX_REASON: Final = 300
MAX_NARRATION: Final = 800
MAX_KEY: Final = 80
MAX_LABEL: Final = 60
MAX_VALUE: Final = 300
_RESULT_KEYS: Final = ("net", "exit_reason", "r", "ticks")
_LADDER_KINDS: Final = frozenset({"king", "ceiling", "floor", "gatekeeper", "air", "wall"})


def _text(value: object, cap: int) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:cap]
    return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _seq(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def _result(value: object) -> dict[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    out: dict[str, JsonValue] = {}
    for key in _RESULT_KEYS:
        v = value.get(key)
        if key == "ticks":
            n = _int(v)
            if n is not None:
                out[key] = n
        elif isinstance(v, str):
            t = _text(v, MAX_KEY)
            if t is not None:
                out[key] = t
        elif key != "exit_reason":
            n2 = _num(v)
            if n2 is not None:
                out[key] = str(v)
    return out or None


def _bars(value: object) -> list[JsonValue]:
    out: list[JsonValue] = []
    for row in _seq(value)[:MAX_BARS]:
        if not isinstance(row, Mapping):
            continue
        o = _num(row.get("o"))
        h = _num(row.get("h"))
        low = _num(row.get("l"))
        c = _num(row.get("c"))
        if o is None or h is None or low is None or c is None:
            continue
        out.append({"o": o, "h": h, "l": low, "c": c})
    return out


def _odds(value: object) -> dict[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    label = _text(value.get("label"), MAX_TITLE) or ""
    items: list[JsonValue] = []
    for item in _seq(value.get("items"))[:8]:
        if not isinstance(item, Mapping):
            continue
        pct = _int(item.get("pct"))
        if pct is None:
            n = _num(item.get("pct"))
            pct = int(n) if n is not None else None
        lab = _text(item.get("label"), MAX_LABEL)
        if pct is None or lab is None:
            continue
        items.append({"pct": pct, "label": lab})
    if not items:
        return None
    return {"label": label, "items": items}


def _ladder(value: object) -> list[JsonValue]:
    out: list[JsonValue] = []
    for row in _seq(value)[:MAX_LADDER]:
        if not isinstance(row, Mapping):
            continue
        kind = row.get("kind")
        level = _num(row.get("level"))
        if kind not in _LADDER_KINDS or level is None:
            continue
        item: dict[str, JsonValue] = {"kind": kind, "level": level}
        strike = row.get("strike")
        if (
            isinstance(strike, (int, float))
            and not isinstance(strike, bool)
            and math.isfinite(strike)
        ):
            item["strike"] = float(strike)
        elif isinstance(strike, str) and strike.strip():
            item["strike"] = strike.strip()[:MAX_KEY]
        mag = _num(row.get("value"))
        if mag is not None:
            item["value"] = mag
        label = _text(row.get("label"), MAX_LABEL)
        if label is not None:
            item["label"] = label
        out.append(item)
    return out


def clean_card(raw: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    """The known keys of a card, size-capped; ``None`` when it is not a valid card.

    Invalid: ``id``/``title`` missing or blank, ``as_of_ns`` not an ``int``, or
    ``action`` not one of :data:`CARD_ACTIONS`.
    """
    card_id = _text(raw.get("id"), MAX_KEY)
    title = _text(raw.get("title"), MAX_TITLE)
    as_of = _int(raw.get("as_of_ns"))
    action = raw.get("action")
    if card_id is None or title is None or as_of is None or action not in CARD_ACTIONS:
        return None
    side = raw.get("side")
    qty = _int(raw.get("qty"))
    targets: list[JsonValue] = []
    for t in _seq(raw.get("targets"))[:MAX_TARGETS]:
        n = _num(t)
        if n is not None:
            targets.append(n)
    fields: list[JsonValue] = []
    for pair in _seq(raw.get("fields"))[:MAX_FIELDS]:
        items = _seq(pair)
        if len(items) != 2:
            continue
        label = _text(items[0], MAX_LABEL)
        if label is None:
            continue
        fields.append([label, str(items[1])[:MAX_VALUE]])
    return {
        "id": card_id,
        "title": title,
        "as_of_ns": as_of,
        "action": action,
        "instrument": _text(raw.get("instrument"), MAX_KEY),
        "symbol": _text(raw.get("symbol"), MAX_KEY),
        "side": side if side in ("long", "short") else None,
        "qty": qty,
        "entry": _num(raw.get("entry")),
        "stop": _num(raw.get("stop")),
        "targets": targets,
        "reason": _text(raw.get("reason"), MAX_REASON),
        "ref": _text(raw.get("ref"), MAX_KEY),
        "tag": _text(raw.get("tag"), MAX_KEY),
        "result": _result(raw.get("result")),
        "fields": fields,
        "spot": _num(raw.get("spot")),
        "dayPct": _num(raw.get("dayPct")),
        "rangePct": _num(raw.get("rangePct")),
        "gates": _text(raw.get("gates"), MAX_KEY),
        "bars": _bars(raw.get("bars")),
        "odds": _odds(raw.get("odds")),
        "ladder": _ladder(raw.get("ladder")),
        "narration": _text(raw.get("narration"), MAX_NARRATION),
    }
