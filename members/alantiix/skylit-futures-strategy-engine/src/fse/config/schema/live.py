"""The ``live`` section of the Strategy_Config (design §23-24, "Strategy_Config shape").

Settings of the Live_Runner and the Broker_Adapter (Req 23-24):

- ``refresh_interval_s``: the Map_State refresh interval, whole seconds, 5 to
  3600 (default 5; Req 23.2, 23.13: below 5 s is refused).
- ``mode``: ``polling`` (default) or ``stream`` (``GET /v1/stream``, Req 23.3).
- ``live_max_snapshot_age_s``: the live Snapshot_Age maximum; above it resting
  entries are cancelled and new entries blocked (default 30, 1 to 3600,
  Req 23.7).
- ``levels_compare_interval_s``: how often ``GET /v1/gex/levels`` is fetched
  for the label comparison log (default 60, 5 to 3600, Req 23.10).
- ``run_window``: ``start`` (inclusive) and ``end`` (exclusive) New York times,
  quoted ``"HH:MM"``, ``start < end`` (default ``"09:00"`` to ``"16:00"``,
  Req 23.2).
- ``stop_confirm_s``: how long a fill may go without a working stop-loss for
  its full quantity (default 5, 1 to 30, Req 24.9).
- ``lookup_timeout_s``: the client-id lookup timeout before a resubmission
  (default 5, 1 to 30, Req 24.15).
- ``outage_s``: no successful ProjectX response for this long is an outage
  (default 30, 5 to 300, Req 24.21).
- ``ignored_instruments``: instrument symbols the Live_Runner lists but never
  compares or trades (default ``[MGC, SIL]``, each once, Req 24.19-24.20).
- ``expected_contracts``: the expected front-month contract id per
  instrument; ``null`` means none is configured. A resolved id that differs
  blocks orders for the instrument (Req 24.11-24.12). The defaults are the
  design sketch's; the Operator updates them at each roll.

Contract ids are public exchange symbols, not account data (Req 17.5).
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from fse.config.schema._base import ClockTime, SchemaModel, YamlList, clock_text, require_unique

__all__ = [
    "INTERVAL_MAX_S",
    "LIVE_MODES",
    "OUTAGE_MAX_S",
    "OUTAGE_MIN_S",
    "REFRESH_INTERVAL_MIN_S",
    "TIMEOUT_MAX_S",
    "ContractId",
    "ExpectedContractsConfig",
    "InstrumentSymbol",
    "LiveConfig",
    "LiveMode",
    "RunWindowConfig",
]

type LiveMode = Literal["polling", "stream"]

LIVE_MODES: Final[tuple[str, ...]] = ("polling", "stream")

REFRESH_INTERVAL_MIN_S: Final = 5
"""The shortest Map_State refresh interval (Req 23.2, 23.13)."""
INTERVAL_MAX_S: Final = 3600
TIMEOUT_MAX_S: Final = 30
OUTAGE_MIN_S: Final = 5
OUTAGE_MAX_S: Final = 300

type InstrumentSymbol = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]{0,9}$")]
"""A futures root symbol such as ``MES`` or ``MGC``."""

type ContractId = Annotated[
    str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(\.[A-Z0-9]+)+$", max_length=64)
]
"""A ProjectX contract id such as ``CON.F.US.MES.Z26``."""


class RunWindowConfig(SchemaModel):
    """The daily window in which the Live_Runner refreshes and trades: ``[start, end)``."""

    start: ClockTime = time(9, 0)
    end: ClockTime = Field(time(16, 0), validate_default=True)

    @field_validator("end")
    @classmethod
    def _after_start(cls, value: time, info: ValidationInfo) -> time:
        start: time | None = info.data.get("start")
        if start is not None and not value > start:
            raise PydanticCustomError(
                "greater_than",
                "end {value} should be after start {gt}",
                {"value": clock_text(value), "gt": clock_text(start)},
            )
        return value


class ExpectedContractsConfig(SchemaModel):
    """The expected front-month contract id per instrument; ``None`` = not configured."""

    MES: ContractId | None = "CON.F.US.MES.Z26"
    MNQ: ContractId | None = "CON.F.US.MNQ.Z26"
    ES: ContractId | None = None
    NQ: ContractId | None = None

    def for_instrument(self, instrument: str) -> str | None:
        """The expected id of ``instrument`` (MES, MNQ, ES or NQ); ``ValueError`` otherwise."""
        if instrument not in type(self).model_fields:
            raise ValueError(f"{instrument!r} is not one of the traded instruments")
        found: str | None = getattr(self, instrument)
        return found


class LiveConfig(SchemaModel):
    """Refresh, staleness, run window, broker timeouts, ignored instruments, contracts."""

    refresh_interval_s: int = Field(5, ge=REFRESH_INTERVAL_MIN_S, le=INTERVAL_MAX_S)
    mode: LiveMode = "polling"
    live_max_snapshot_age_s: int = Field(30, ge=1, le=INTERVAL_MAX_S)
    levels_compare_interval_s: int = Field(60, ge=REFRESH_INTERVAL_MIN_S, le=INTERVAL_MAX_S)
    run_window: RunWindowConfig = RunWindowConfig()
    stop_confirm_s: int = Field(5, ge=1, le=TIMEOUT_MAX_S)
    lookup_timeout_s: int = Field(5, ge=1, le=TIMEOUT_MAX_S)
    outage_s: int = Field(30, ge=OUTAGE_MIN_S, le=OUTAGE_MAX_S)
    ignored_instruments: YamlList[InstrumentSymbol] = ("MGC", "SIL")
    expected_contracts: ExpectedContractsConfig = ExpectedContractsConfig()

    @field_validator("ignored_instruments")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique(value)
