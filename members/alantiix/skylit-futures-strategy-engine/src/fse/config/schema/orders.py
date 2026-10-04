"""The ``orders`` section of the Strategy_Config (design §12, "Strategy_Config shape").

Resting-entry handling, Flatten_Time and the Cancel_Triggers that the
Order_Planner (``fse.engine.planner``) reads (Req 12.15-12.23):

- ``flatten_time``: the strategy's Flatten_Time on a full session, a quoted
  ``"HH:MM"`` New York time from 09:30 to 16:00 (default ``"15:55"``). It is
  distinct from the account's Flat_Deadline (``account.flat_deadline``).
- ``early_close_flatten_lead_min``: on an early-close session, Flatten_Time is
  this many minutes before the close (default 30, range 15 to 120).
- ``max_open``: resting entries plus open positions across the configured
  instruments (default 1, range 1 to 10, Req 12.18).
- ``max_age_min``: cancel a resting entry this many minutes after placement
  (range 1 to 390); ``null`` (the default) turns the rule off (Req 12.23).
- ``cancel_triggers``: one on/off flag per Cancel_Trigger for resting entries
  (all on by default, Req 12.19).
- ``invalidation``: per Cancel_Trigger, the action on an open position:
  ``exit_market``, ``breakeven`` (move the stop to the Breakeven_Price) or
  ``hold`` (record only); ``null`` means the trigger is not enabled for open
  positions (Req 12.21). Defaults from the design sketch: King flip, source
  Node gone and sign flip exit at market ("treat it as scratched"); stdev leg
  dropped and opposition in target hold.

The Cancel_Trigger ids, in check and record order, are :data:`CANCEL_TRIGGERS`.
"""

from __future__ import annotations

import re
from datetime import time
from typing import Annotated, Final, Literal

from pydantic import Field, PlainSerializer, PlainValidator
from pydantic_core import PydanticCustomError

from fse.config.schema._base import SchemaModel
from fse.timekit import RTH_CLOSE, RTH_OPEN

__all__ = [
    "CANCEL_TRIGGERS",
    "EARLY_CLOSE_FLATTEN_LEAD_MAX_MIN",
    "EARLY_CLOSE_FLATTEN_LEAD_MIN_MIN",
    "INVALIDATION_ACTIONS",
    "MAX_AGE_MAX_MIN",
    "MAX_OPEN_MAX",
    "CancelTrigger",
    "CancelTriggersConfig",
    "FlattenTime",
    "InvalidationAction",
    "InvalidationConfig",
    "OrdersConfig",
]

type CancelTrigger = Literal[
    "king_flip", "source_node_gone", "sign_flip", "stdev_leg_dropped", "opposition_in_target"
]
type InvalidationAction = Literal["exit_market", "breakeven", "hold"]

CANCEL_TRIGGERS: Final[tuple[CancelTrigger, ...]] = (
    "king_flip",
    "source_node_gone",
    "sign_flip",
    "stdev_leg_dropped",
    "opposition_in_target",
)
"""The Cancel_Trigger ids (Glossary), in the order they are checked and recorded."""

INVALIDATION_ACTIONS: Final[tuple[InvalidationAction, ...]] = ("exit_market", "breakeven", "hold")
"""The open-position actions, strongest first: the precedence of Req 12.21."""

EARLY_CLOSE_FLATTEN_LEAD_MIN_MIN: Final = 15
EARLY_CLOSE_FLATTEN_LEAD_MAX_MIN: Final = 120
MAX_OPEN_MAX: Final = 10
MAX_AGE_MAX_MIN: Final = 390

_HHMM: Final = re.compile(r"([01]\d|2[0-3]):([0-5]\d)", re.ASCII)
_FLATTEN_RANGE: Final = "from 09:30 to 16:00"


def _wall_text(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _parse_flatten(value: object) -> time:
    """A quoted ``"HH:MM"`` New York time from 09:30 to 16:00, both included."""
    if isinstance(value, str):
        match = _HHMM.fullmatch(value)
        if match is None:
            raise PydanticCustomError(
                "time_parsing",
                "{value} should be a quoted 'HH:MM' New York time such as '15:55'",
                {"value": repr(value)},
            )
        wall = time(int(match[1]), int(match[2]))
    elif (
        isinstance(value, time) and value.tzinfo is None and not (value.second or value.microsecond)
    ):
        wall = value
    else:
        raise PydanticCustomError(
            "time_type",
            "{value} should be a quoted 'HH:MM' New York time such as '15:55' "
            "(an unquoted 15:55 reads as a number in YAML)",
            {"value": repr(value)},
        )
    if wall < RTH_OPEN:
        raise PydanticCustomError(
            "greater_than_equal",
            "{value} should be {range}",
            {"value": _wall_text(wall), "range": _FLATTEN_RANGE, "ge": "09:30"},
        )
    if wall > RTH_CLOSE:
        raise PydanticCustomError(
            "less_than_equal",
            "{value} should be {range}",
            {"value": _wall_text(wall), "range": _FLATTEN_RANGE, "le": "16:00"},
        )
    return wall


type FlattenTime = Annotated[time, PlainValidator(_parse_flatten), PlainSerializer(_wall_text)]
"""Flatten_Time: ``"HH:MM"`` in YAML from 09:30 to 16:00, a naive ``datetime.time`` in the model."""


class CancelTriggersConfig(SchemaModel):
    """Which Cancel_Triggers cancel a resting entry (Req 12.19)."""

    king_flip: bool = True
    source_node_gone: bool = True
    sign_flip: bool = True
    stdev_leg_dropped: bool = True
    opposition_in_target: bool = True

    def enabled(self, trigger: str) -> bool:
        """The flag of ``trigger``; ``ValueError`` for a name that is not a Cancel_Trigger."""
        if trigger not in CANCEL_TRIGGERS:
            raise ValueError(f"{trigger!r} is not one of the Cancel_Triggers {CANCEL_TRIGGERS}")
        flag: bool = getattr(self, trigger)
        return flag


class InvalidationConfig(SchemaModel):
    """The action per Cancel_Trigger on an open position; ``None`` = not enabled (Req 12.21)."""

    king_flip: InvalidationAction | None = "exit_market"
    source_node_gone: InvalidationAction | None = "exit_market"
    sign_flip: InvalidationAction | None = "exit_market"
    stdev_leg_dropped: InvalidationAction | None = "hold"
    opposition_in_target: InvalidationAction | None = "hold"

    def action(self, trigger: str) -> InvalidationAction | None:
        """The action of ``trigger``; ``ValueError`` for a name that is not a Cancel_Trigger."""
        if trigger not in CANCEL_TRIGGERS:
            raise ValueError(f"{trigger!r} is not one of the Cancel_Triggers {CANCEL_TRIGGERS}")
        action: InvalidationAction | None = getattr(self, trigger)
        return action


class OrdersConfig(SchemaModel):
    """Flatten_Time, the exposure cap, resting-entry max age and the Cancel_Triggers."""

    flatten_time: FlattenTime = time(15, 55)
    early_close_flatten_lead_min: int = Field(
        30, ge=EARLY_CLOSE_FLATTEN_LEAD_MIN_MIN, le=EARLY_CLOSE_FLATTEN_LEAD_MAX_MIN
    )
    max_open: int = Field(1, ge=1, le=MAX_OPEN_MAX)
    max_age_min: int | None = Field(None, ge=1, le=MAX_AGE_MAX_MIN)
    cancel_triggers: CancelTriggersConfig = CancelTriggersConfig()
    invalidation: InvalidationConfig = InvalidationConfig()
