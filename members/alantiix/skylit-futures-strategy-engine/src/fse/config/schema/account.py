"""The ``account`` section of the Strategy_Config (design §15, "Strategy_Config shape").

The Topstep 50K Trading Combine rules the Account_Simulator applies (Req 15).
Defaults (Req 15.1) come from a third-party summary dated July 2026; the
Operator checks each one against the Topstep help center before enabling
Combine Order_Mode (``docs/account-rules.md``, Req 15.19-15.20).

- ``starting_balance.value``: $50,000.00. Always applies.
- ``profit_target``: enabled, $3,000.00.
- ``maximum_loss_limit``: enabled, $2,000.00, and less than the starting
  balance, so the first MLL_Floor is $48,000.00 (Req 15.6).
- ``daily_loss_limit``: enabled, $1,000.00.
- ``consistency_target``: enabled, 55 percent (above 0, at most 100).
- ``position_cap``: enabled, 50 Micro_Equivalents (integer 1 to 1,000).
- ``flat_deadline``: ``"16:10"`` New York time (3:10 PM CT). Quoted in YAML:
  an unquoted ``16:10`` is a base-60 integer to a YAML 1.1 loader.
- ``early_close_offset_min``: 15 minutes before an early close (0 to 60).

Money is exact: each dollar value is a quoted decimal string in YAML, held as
a ``Decimal`` quantized to cents, from $0.01 to $10,000,000.00 (Req 15.4), and
dumped back as a string. The five toggleable rules (Req 15.2) have an
``enabled`` flag; their values are validated whether or not they are enabled.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import time
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import Field, PlainSerializer, PlainValidator, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from fse.config.schema._base import SchemaModel
from fse.timekit import RTH_OPEN, TRADING_DAY_START, SessionTimes

__all__ = [
    "ACCOUNT_RULE_IDS",
    "CONSISTENCY_PCT_MAX",
    "EARLY_CLOSE_OFFSET_MAX_MIN",
    "POSITION_CAP_MAX",
    "USD_MAX",
    "USD_MIN",
    "AccountConfig",
    "AccountRuleId",
    "ConsistencyTargetConfig",
    "DailyLossLimitConfig",
    "MaximumLossLimitConfig",
    "PositionCapConfig",
    "ProfitTargetConfig",
    "StartingBalanceConfig",
    "Usd",
    "WallTime",
]

type AccountRuleId = Literal[
    "profit_target",
    "maximum_loss_limit",
    "daily_loss_limit",
    "consistency_target",
    "position_cap",
]
"""The ids of the account rules the Strategy_Config can disable (Req 15.2)."""

ACCOUNT_RULE_IDS: Final[tuple[AccountRuleId, ...]] = (
    "profit_target",
    "maximum_loss_limit",
    "daily_loss_limit",
    "consistency_target",
    "position_cap",
)

USD_MIN: Final = Decimal("0.01")
USD_MAX: Final = Decimal("10000000.00")
CONSISTENCY_PCT_MAX: Final = 100.0
POSITION_CAP_MAX: Final = 1000
EARLY_CLOSE_OFFSET_MAX_MIN: Final = 60

_CENT: Final = Decimal("0.01")
_USD_RANGE: Final = "from 0.01 to 10000000.00 dollars"
_DECIMAL_TEXT: Final = re.compile(r"[+-]?\d+(?:\.\d+)?", re.ASCII)
_HHMM: Final = re.compile(r"([01]\d|2[0-3]):([0-5]\d)", re.ASCII)


# ---------------------------------------------------------------- dollars


def _parse_usd(value: object) -> Decimal:
    """A quoted dollar string (or a ``Decimal``) with at most cents, in range."""
    if isinstance(value, str):
        if _DECIMAL_TEXT.fullmatch(value) is None:
            raise PydanticCustomError(
                "decimal_parsing",
                "{value} should be a dollar amount such as '3000.00', {range}",
                {"value": repr(value), "range": _USD_RANGE},
            )
        amount = Decimal(value)
    elif isinstance(value, Decimal) and value.is_finite():
        amount = value
    else:
        raise PydanticCustomError(
            "decimal_type",
            "{value} should be a quoted dollar string such as '3000.00', {range}",
            {"value": repr(value), "range": _USD_RANGE},
        )
    if amount < USD_MIN:
        raise PydanticCustomError(
            "greater_than_equal",
            "{value} should be {range}",
            {"value": str(amount), "range": _USD_RANGE, "ge": str(USD_MIN)},
        )
    if amount > USD_MAX:
        raise PydanticCustomError(
            "less_than_equal",
            "{value} should be {range}",
            {"value": str(amount), "range": _USD_RANGE, "le": str(USD_MAX)},
        )
    cents = amount.quantize(_CENT)
    if cents != amount:
        raise PydanticCustomError(
            "decimal_max_places",
            "{value} should have at most 2 decimal places (whole cents)",
            {"value": str(amount), "decimal_places": 2},
        )
    return cents


def _usd_text(value: Decimal) -> str:
    return str(value)


type Usd = Annotated[Decimal, PlainValidator(_parse_usd), PlainSerializer(_usd_text)]
"""Exact dollars: a quoted string in YAML, a cent-quantized ``Decimal`` in the model."""


# ---------------------------------------------------------------- wall time


def _parse_wall(value: object) -> time:
    """A quoted ``"HH:MM"`` New York time after 09:30 and before 18:00."""
    if isinstance(value, str):
        match = _HHMM.fullmatch(value)
        if match is None:
            raise PydanticCustomError(
                "time_parsing",
                "{value} should be a quoted 'HH:MM' New York time such as '16:10'",
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
            "{value} should be a quoted 'HH:MM' New York time such as '16:10' "
            "(an unquoted 16:10 reads as a number in YAML)",
            {"value": repr(value)},
        )
    if not wall > RTH_OPEN:
        raise PydanticCustomError(
            "greater_than",
            "{value} should be after 09:30 and before 18:00",
            {"value": _wall_text(wall), "gt": "09:30"},
        )
    if not wall < TRADING_DAY_START:
        raise PydanticCustomError(
            "less_than",
            "{value} should be after 09:30 and before 18:00",
            {"value": _wall_text(wall), "lt": "18:00"},
        )
    return wall


def _wall_text(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


type WallTime = Annotated[time, PlainValidator(_parse_wall), PlainSerializer(_wall_text)]
"""A New York wall time: ``"HH:MM"`` in YAML, a naive ``datetime.time`` in the model."""


# ---------------------------------------------------------------- rules


class StartingBalanceConfig(SchemaModel):
    """The balance each Combine_Attempt starts at (Req 15.6)."""

    value: Usd = Decimal("50000.00")


class ProfitTargetConfig(SchemaModel):
    """Profit target: a Combine_Pass needs start + current target at a day end (Req 15.17)."""

    enabled: bool = True
    value: Usd = Decimal("3000.00")


class MaximumLossLimitConfig(SchemaModel):
    """Maximum Loss Limit: the MLL_Floor trails at this distance below the day-end balance."""

    enabled: bool = True
    value: Usd = Decimal("2000.00")


class DailyLossLimitConfig(SchemaModel):
    """Daily Loss Limit: liquidate and block for the day at minus this value (Req 15.10)."""

    enabled: bool = True
    value: Usd = Decimal("1000.00")


class ConsistencyTargetConfig(SchemaModel):
    """Consistency target: the best day's share of the profit target, in percent (Req 15.13)."""

    enabled: bool = True
    pct: float = Field(55.0, gt=0, le=CONSISTENCY_PCT_MAX)


class PositionCapConfig(SchemaModel):
    """Position cap: open plus working-entry Micro_Equivalents, any direction (Req 15.5)."""

    enabled: bool = True
    micro_equivalents: int = Field(50, ge=1, le=POSITION_CAP_MAX)


class AccountConfig(SchemaModel):
    """The Topstep 50K Trading Combine rules (Req 15.1-15.4)."""

    starting_balance: StartingBalanceConfig = StartingBalanceConfig()
    profit_target: ProfitTargetConfig = ProfitTargetConfig()
    maximum_loss_limit: MaximumLossLimitConfig = MaximumLossLimitConfig()
    daily_loss_limit: DailyLossLimitConfig = DailyLossLimitConfig()
    consistency_target: ConsistencyTargetConfig = ConsistencyTargetConfig()
    position_cap: PositionCapConfig = PositionCapConfig()
    flat_deadline: WallTime = time(16, 10)
    early_close_offset_min: int = Field(15, ge=0, le=EARLY_CLOSE_OFFSET_MAX_MIN)

    @field_validator("maximum_loss_limit")
    @classmethod
    def _below_starting_balance(
        cls, value: MaximumLossLimitConfig, info: ValidationInfo
    ) -> MaximumLossLimitConfig:
        start: StartingBalanceConfig | None = info.data.get("starting_balance")
        if start is not None and not value.value < start.value:
            raise PydanticCustomError(
                "less_than",
                "maximum_loss_limit.value {value} should be less than starting_balance.value {lt}",
                {"value": str(value.value), "lt": str(start.value)},
            )
        return value

    def disabled_rules(self) -> tuple[AccountRuleId, ...]:
        """The ids of the disabled rules, in ``ACCOUNT_RULE_IDS`` order (Req 15.3)."""
        rules = {
            "profit_target": self.profit_target.enabled,
            "maximum_loss_limit": self.maximum_loss_limit.enabled,
            "daily_loss_limit": self.daily_loss_limit.enabled,
            "consistency_target": self.consistency_target.enabled,
            "position_cap": self.position_cap.enabled,
        }
        return tuple(rule for rule in ACCOUNT_RULE_IDS if not rules[rule])

    def session_times(self, base: SessionTimes) -> SessionTimes:
        """``base`` with this section's Flat_Deadline and early-close offset (Req 15.15)."""
        return dataclasses.replace(
            base,
            flat_deadline=self.flat_deadline,
            flat_deadline_early_close_offset_min=self.early_close_offset_min,
        )
