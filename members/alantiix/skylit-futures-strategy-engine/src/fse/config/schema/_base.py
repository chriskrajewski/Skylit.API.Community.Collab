"""Shared pieces of the Config_Schema section models (design §17, D6).

Every section model derives from :class:`SchemaModel`: strict types, unknown
keys rejected, immutable. YAML has no tuple, so list fields are declared as
``YamlList[...]``: a YAML list validates into a tuple (strictly, item by item)
and dumps back as a list, which keeps ``yaml.safe_dump`` round trips exact.

Custom errors use pydantic's own ``literal_error`` type for "not one of the
allowed values" (ctx ``expected``, like a ``Literal`` failure) and
``duplicate_id`` for a repeated list entry, so the Config_Loader (task 20.1)
maps every error to a Req 17.4 kind from its type.

``ClockTime`` is a quoted ``"HH:MM"`` time of day with no range of its own,
for keys outside RTH (the live run window, the premarket card); a section
adds its range with a validator.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import time
from typing import Annotated, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer, PlainValidator
from pydantic_core import PydanticCustomError

__all__ = [
    "DUPLICATE_ID",
    "ClockTime",
    "SchemaModel",
    "YamlList",
    "clock_text",
    "duplicate_error",
    "not_allowed_error",
    "require_unique",
]

DUPLICATE_ID = "duplicate_id"

_HHMM: Final = re.compile(r"([01]\d|2[0-3]):([0-5]\d)", re.ASCII)


class SchemaModel(BaseModel):
    """Base of every Config_Schema model: strict, closed and frozen (D6)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _list_to_tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _tuple_to_list(value: tuple[object, ...]) -> list[object]:
    return list(value)


type YamlList[T] = Annotated[
    tuple[T, ...], BeforeValidator(_list_to_tuple), PlainSerializer(_tuple_to_list)
]
"""A list field: YAML list in, tuple in the model, list out."""


def _quoted(values: Iterable[object]) -> str:
    return ", ".join(repr(v) for v in values)


def not_allowed_error(value: object, allowed: Iterable[object], where: str) -> PydanticCustomError:
    """A ``literal_error`` naming the bad value, the allowed values and where they come from."""
    return PydanticCustomError(
        "literal_error",
        "{value} should be one of {where}: {expected}",
        {"value": repr(value), "where": where, "expected": _quoted(allowed)},
    )


def duplicate_error(value: object) -> PydanticCustomError:
    return PydanticCustomError(DUPLICATE_ID, "{value} is listed more than once", {"value": value})


def require_unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    """Return ``values`` unchanged, or raise ``duplicate_id`` for the first repeat."""
    seen: set[T] = set()
    for value in values:
        if value in seen:
            raise duplicate_error(value)
        seen.add(value)
    return values


def clock_text(value: time) -> str:
    """``value`` as ``"HH:MM"``."""
    return f"{value.hour:02d}:{value.minute:02d}"


def _parse_clock(value: object) -> time:
    """A quoted ``"HH:MM"`` time of day (or a naive whole-minute ``time``)."""
    if isinstance(value, str):
        match = _HHMM.fullmatch(value)
        if match is None:
            raise PydanticCustomError(
                "time_parsing",
                "{value} should be a quoted 'HH:MM' New York time such as '09:00'",
                {"value": repr(value)},
            )
        return time(int(match[1]), int(match[2]))
    if isinstance(value, time) and value.tzinfo is None and not (value.second or value.microsecond):
        return value
    raise PydanticCustomError(
        "time_type",
        "{value} should be a quoted 'HH:MM' New York time such as '09:00' "
        "(an unquoted 09:00 reads as a number in YAML)",
        {"value": repr(value)},
    )


type ClockTime = Annotated[time, PlainValidator(_parse_clock), PlainSerializer(clock_text)]
"""A New York time of day: ``"HH:MM"`` in YAML, a naive ``datetime.time`` in the model."""
