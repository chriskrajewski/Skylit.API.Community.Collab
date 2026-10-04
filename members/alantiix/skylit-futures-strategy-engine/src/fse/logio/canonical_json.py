"""Canonical JSON for logs, manifests, recordings and hashes (design D7 and "Decision log").

The same value always encodes to the same bytes, so reruns produce
byte-identical decision logs (Req 18.5):

- object keys sorted by code point, ``separators=(",", ":")``, no whitespace;
- pure ASCII: every other character is a ``\\uXXXX`` escape;
- floats by ``repr`` (shortest round-trip form); NaN and infinities are refused;
- ``Decimal`` as its ``str`` (``"1.50"`` stays ``"1.50"``); non-finite refused;
- Instants are plain ints. :func:`instant_fields` adds the ISO New York string
  next to one.

:func:`to_jsonable` turns supported Python values into plain JSON values:
``None``, ``bool``, ``int`` (and other integral numbers such as numpy ints),
``float`` (and subclasses such as ``numpy.float64``), ``str``, ``Decimal``,
``Enum`` (its value), mappings with ``str`` keys, ``list`` and ``tuple``,
``set`` and ``frozenset`` (sorted by their canonical encoding), dataclass
instances (their fields), ``date`` (ISO date) and paths (``str``). Anything else,
including ``datetime`` (use an Instant), raises ``TypeError`` naming the type
and where it sits, as a ``$.key[0]`` path.

Nothing here does I/O or reads the clock.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import math
import numbers
import operator
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Final, cast

from fse.timekit import NS_PER_SECOND, Instant, ny_datetime

__all__ = [
    "JsonValue",
    "dumps",
    "dumps_bytes",
    "instant_fields",
    "ny_iso",
    "to_jsonable",
]

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

_ISO_SECONDS_LEN: Final = len("2026-01-14T09:30:00")


def dumps(obj: object) -> str:
    """The canonical JSON text of ``obj``, without a trailing newline."""
    return _encode(to_jsonable(obj))


def dumps_bytes(obj: object) -> bytes:
    """:func:`dumps` as ASCII bytes, for hashing and binary sinks."""
    return dumps(obj).encode("ascii")


def to_jsonable(obj: object) -> JsonValue:
    """``obj`` as plain JSON values, with every mapping's keys in sorted order."""
    return _convert(obj, "$")


def ny_iso(t: Instant) -> str:
    """``t`` as an ISO 8601 America/New_York time with its UTC offset.

    Whole seconds print as ``2026-01-14T09:30:00-05:00``; otherwise all nine
    fractional digits are kept: ``2026-01-14T09:30:00.000000001-05:00``.
    """
    _require_instant(t)
    seconds, frac_ns = divmod(t, NS_PER_SECOND)
    text = ny_datetime(seconds * NS_PER_SECOND).isoformat(timespec="seconds")
    if frac_ns == 0:
        return text
    return f"{text[:_ISO_SECONDS_LEN]}.{frac_ns:09d}{text[_ISO_SECONDS_LEN:]}"


def instant_fields(name: str, t: Instant) -> dict[str, JsonValue]:
    """``{name: t, name + "_ny": ny_iso(t)}``: an Instant as an int plus its NY string."""
    return {name: t, f"{name}_ny": ny_iso(t)}


# ---------------------------------------------------------------- internals


def _encode(value: JsonValue) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _require_instant(t: object) -> None:
    if isinstance(t, bool) or not isinstance(t, int):
        raise TypeError(f"an Instant must be an int of ns UTC, not {type(t).__name__}")


def _convert(obj: object, path: str) -> JsonValue:
    handler = _EXACT.get(type(obj))
    if handler is not None:
        return handler(obj, path)
    return _convert_other(obj, path)


def _same(obj: object, path: str) -> JsonValue:
    return cast("bool | int | str | None", obj)  # exact None, bool, int or str only


def _from_float(obj: object, path: str) -> JsonValue:
    value = float(cast("float", obj))
    if not math.isfinite(value):
        raise ValueError(f"cannot encode the non-finite float {value!r} at {path}")
    return value


def _from_sequence(obj: object, path: str) -> JsonValue:
    items = cast("list[object] | tuple[object, ...]", obj)
    return [_convert(item, f"{path}[{i}]") for i, item in enumerate(items)]


def _from_mapping(obj: object, path: str) -> JsonValue:
    mapping = cast("Mapping[object, object]", obj)
    out: dict[str, JsonValue] = {}
    for raw_key, item in mapping.items():
        key = raw_key.value if isinstance(raw_key, enum.Enum) else raw_key
        if not isinstance(key, str):
            raise TypeError(f"object keys must be str, got {type(raw_key).__name__} at {path}")
        key = str.__str__(key)
        if key in out:
            raise ValueError(f"two keys encode as {key!r} at {path}")
        out[key] = _convert(item, f"{path}.{key}")
    return dict(sorted(out.items()))


_EXACT: Final[dict[type, Callable[[object, str], JsonValue]]] = {
    type(None): _same,
    bool: _same,
    int: _same,
    str: _same,
    float: _from_float,
    list: _from_sequence,
    tuple: _from_sequence,
    dict: _from_mapping,
}


def _convert_other(obj: object, path: str) -> JsonValue:
    if isinstance(obj, enum.Enum):
        return _convert(obj.value, path)
    if isinstance(obj, str):
        return str.__str__(obj)
    if isinstance(obj, float):
        return _from_float(obj, path)
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise ValueError(f"cannot encode the non-finite Decimal {obj} at {path}")
        return str(obj)
    if isinstance(obj, numbers.Integral):
        return operator.index(obj)
    if isinstance(obj, Mapping):
        return _from_mapping(obj, path)
    if isinstance(obj, list | tuple):
        return _from_sequence(obj, path)
    if isinstance(obj, set | frozenset):
        items = [_convert(item, f"{path}[]") for item in obj]
        return sorted(items, key=_encode)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _from_mapping({f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}, path)
    if isinstance(obj, datetime):
        raise TypeError(f"cannot encode a datetime at {path}; use an Instant (int ns UTC)")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, PurePath):
        return str(obj)
    raise TypeError(f"cannot encode {type(obj).__qualname__} at {path} as canonical JSON")
