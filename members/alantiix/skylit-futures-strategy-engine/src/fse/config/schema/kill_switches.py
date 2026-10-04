"""The ``kill_switches`` section of the Strategy_Config (design §16, Req 16).

Settings of the Risk_Manager (``fse.engine.risk``). Each Kill_Switch has an
``enabled`` flag (all on by default) and one value:

- ``max_trades.max``: filled entry orders per session (default 3; at least 1,
  Req 16.1).
- ``max_losers.limit``: Losing_Trades per session (default 2; at least 1,
  Req 16.2).
- ``consecutive_losers.limit``: the Loss_Streak, counted across sessions
  (default 3; at least 1, Req 16.3).
- ``red_day.threshold_usd``: the session's net realized P&L below which a
  closed trade starts a Lockout (default ``"0"``, Req 16.4).
- ``daily_profit_cap.cap_usd``: the session's net realized P&L at or above
  which an exit fill starts a Lockout (default ``"1200"``; above 0, Req 16.5).

The internal daily loss stop (Req 16.6) is off by default; ``multiple`` (default
2.0; above 0) times the Position_Sizer's ``sizing.risk_usd`` is the loss limit.
``losing_trade_tolerance_usd`` (default ``"0"``; at least 0) sets the
Losing_Trade line: a closed trade is a Losing_Trade when its net P&L is below
minus the tolerance (Glossary).

Money is a quoted decimal string in YAML, read into a ``Decimal``
(``fse.config.schema.sizing.YamlMoney``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from pydantic import Field

from fse.config.schema._base import SchemaModel
from fse.config.schema.sizing import YamlMoney

__all__ = [
    "KILL_SWITCH_IDS",
    "ConsecutiveLosersConfig",
    "DailyProfitCapConfig",
    "InternalDailyLossStopConfig",
    "KillSwitchesConfig",
    "MaxLosersConfig",
    "MaxTradesConfig",
    "RedDayConfig",
]

KILL_SWITCH_IDS: Final[tuple[str, ...]] = (
    "max_trades",
    "max_losers",
    "consecutive_losers",
    "red_day",
    "daily_profit_cap",
    "internal_daily_loss_stop",
)
"""Every rule that can start a Lockout, in schema order; also the Lockout rule ids."""


class MaxTradesConfig(SchemaModel):
    """Lock out once the session's filled entry orders reach ``max`` (Req 16.1)."""

    enabled: bool = True
    max: int = Field(3, ge=1)


class MaxLosersConfig(SchemaModel):
    """Lock out once the session's Losing_Trades reach ``limit`` (Req 16.2)."""

    enabled: bool = True
    limit: int = Field(2, ge=1)


class ConsecutiveLosersConfig(SchemaModel):
    """Lock out this session and the next one once the Loss_Streak reaches ``limit`` (Req 16.3)."""

    enabled: bool = True
    limit: int = Field(3, ge=1)


class RedDayConfig(SchemaModel):
    """Lock out when a closed trade leaves the session's net below ``threshold_usd`` (Req 16.4)."""

    enabled: bool = True
    threshold_usd: YamlMoney = Decimal("0")


class DailyProfitCapConfig(SchemaModel):
    """Lock out when an exit fill brings the session's net to ``cap_usd`` or more (Req 16.5)."""

    enabled: bool = True
    cap_usd: YamlMoney = Field(Decimal("1200"), gt=0)


class InternalDailyLossStopConfig(SchemaModel):
    """Close out and lock out at ``-multiple x sizing.risk_usd`` (Req 16.6); off by default."""

    enabled: bool = False
    multiple: float = Field(2.0, gt=0, allow_inf_nan=False)


class KillSwitchesConfig(SchemaModel):
    """The five playbook Kill_Switches, the internal daily loss stop and the Losing_Trade line."""

    max_trades: MaxTradesConfig = MaxTradesConfig()
    max_losers: MaxLosersConfig = MaxLosersConfig()
    consecutive_losers: ConsecutiveLosersConfig = ConsecutiveLosersConfig()
    red_day: RedDayConfig = RedDayConfig()
    daily_profit_cap: DailyProfitCapConfig = DailyProfitCapConfig()
    internal_daily_loss_stop: InternalDailyLossStopConfig = InternalDailyLossStopConfig()
    losing_trade_tolerance_usd: YamlMoney = Field(Decimal("0"), ge=0)
