"""The ``time`` section of the Strategy_Config (design "Strategy_Config shape", D2).

- ``decision_cadence_s``: the Decision_Cadence in whole seconds (default 60, so
  390 Decision_Times per session, Req 18.1). Range 1 to 3600.
- ``timezone``: the session-time zone. Only America/New_York is allowed, because
  every session rule is New York time (Req 5.8).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from fse.config.schema._base import SchemaModel

__all__ = ["DECISION_CADENCE_MAX_S", "SESSION_TIMEZONE", "TimeConfig"]

SESSION_TIMEZONE: Final = "America/New_York"
DECISION_CADENCE_MAX_S: Final = 3600


class TimeConfig(SchemaModel):
    """Decision_Cadence and the session-time zone."""

    decision_cadence_s: int = Field(60, ge=1, le=DECISION_CADENCE_MAX_S)
    timezone: Literal["America/New_York"] = SESSION_TIMEZONE
