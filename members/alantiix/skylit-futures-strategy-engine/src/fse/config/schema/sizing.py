"""The ``sizing`` section of the Strategy_Config (design §14 "Position_Sizer", Req 14).

Settings of the Position_Sizer (``fse.engine.sizing``):

- ``mode``: ``fixed_contracts`` (the default, Req 14.1) or ``fixed_dollar_risk``
  (Req 14.2).
- ``fixed``: base contracts per instrument for ``fixed_contracts`` (default 5
  MES, 3 MNQ; a whole number of at least 1).
- ``risk_usd``: risk dollars per trade for ``fixed_dollar_risk`` (default
  ``"375"``; above 0).
- ``big_win``: the previous session's net P&L threshold ``usd`` (default
  ``"1200"``; above 0), its summed R_Multiple threshold ``r`` (default 3; above
  0) and the ``reduced`` contracts per instrument (default 3 MES, 2 MNQ; a
  whole number of at least 1) (Req 14.5).
- ``trinity_size_down``: on/off and the ``fraction`` applied at exactly 2 of 3
  agreeing symbols (default 0.5; above 0, at most 1) (Req 14.6).
- ``vix_gap``: on/off and the VIX gap ``pct`` at or above which contracts are
  halved (default 15.0; above 0) (Req 14.7).
- ``micro_equivalent_limit``: the Micro_Equivalent cap (default 50; a whole
  number of at least 1) (Req 14.4).

The per-instrument counts also have ES and NQ keys, default 1, because
``data.instruments`` may select ES or NQ. Money is a quoted decimal string in
YAML (``"375"``), validated into a ``Decimal`` and dumped back as a string, so
no dollar amount ever passes through a float (design D4).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Final, Literal

from pydantic import BeforeValidator, Field, PlainSerializer
from pydantic_core import PydanticCustomError

from fse.config.schema._base import SchemaModel

__all__ = [
    "SIZING_INSTRUMENTS",
    "SIZING_MODES",
    "BigWinConfig",
    "FixedContracts",
    "ReducedContracts",
    "SizingConfig",
    "SizingInstrument",
    "SizingMode",
    "TrinitySizeDownConfig",
    "VixGapSizingConfig",
    "YamlMoney",
]

type SizingMode = Literal["fixed_contracts", "fixed_dollar_risk"]
type SizingInstrument = Literal["MES", "MNQ", "ES", "NQ"]

SIZING_MODES: Final[frozenset[str]] = frozenset({"fixed_contracts", "fixed_dollar_risk"})
SIZING_INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ", "ES", "NQ")


def _to_money(value: object) -> object:
    """A quoted decimal string becomes a ``Decimal``; a ``Decimal`` passes; nothing else does."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            raise PydanticCustomError(
                "decimal_parsing",
                "{value} is not a decimal dollar amount",
                {"value": repr(value)},
            ) from None
    raise PydanticCustomError(
        "decimal_type",
        'money must be a quoted decimal string such as "375", got {value}',
        {"value": repr(value)},
    )


type YamlMoney = Annotated[
    Decimal, BeforeValidator(_to_money), PlainSerializer(str, return_type=str)
]
"""Exact dollars: a quoted decimal string in YAML, a ``Decimal`` in the model."""


class _PerInstrument(SchemaModel):
    """Contracts per instrument; subclasses set the defaults."""

    MES: int
    MNQ: int
    ES: int
    NQ: int

    def for_instrument(self, instrument: str) -> int:
        """The count for ``instrument`` (MES, MNQ, ES or NQ)."""
        if instrument not in SIZING_INSTRUMENTS:
            raise ValueError(
                f"instrument {instrument!r} is not one of the sized instruments "
                f"{SIZING_INSTRUMENTS}"
            )
        count: int = getattr(self, instrument)
        return count


class FixedContracts(_PerInstrument):
    """Base contracts for ``fixed_contracts`` sizing (Req 14.1)."""

    MES: int = Field(5, ge=1)
    MNQ: int = Field(3, ge=1)
    ES: int = Field(1, ge=1)
    NQ: int = Field(1, ge=1)


class ReducedContracts(_PerInstrument):
    """The big-win reduced size (Req 14.5)."""

    MES: int = Field(3, ge=1)
    MNQ: int = Field(2, ge=1)
    ES: int = Field(1, ge=1)
    NQ: int = Field(1, ge=1)


class BigWinConfig(SchemaModel):
    """Size down after a previous session that netted ``usd`` or more, or ``r`` R or more."""

    usd: YamlMoney = Field(Decimal("1200"), gt=0)
    r: float = Field(3.0, gt=0, allow_inf_nan=False)
    reduced: ReducedContracts = ReducedContracts()


class TrinitySizeDownConfig(SchemaModel):
    """Multiply contracts by ``fraction`` when exactly 2 of 3 Trinity symbols agree (Req 14.6)."""

    enabled: bool = True
    fraction: float = Field(0.5, gt=0, le=1)


class VixGapSizingConfig(SchemaModel):
    """Halve contracts when the session's VIX gap is at least ``pct`` percent (Req 14.7)."""

    enabled: bool = True
    pct: float = Field(15.0, gt=0, allow_inf_nan=False)


class SizingConfig(SchemaModel):
    """Sizing mode, base and reduced counts, risk dollars, size-down rules and the cap."""

    mode: SizingMode = "fixed_contracts"
    fixed: FixedContracts = FixedContracts()
    risk_usd: YamlMoney = Field(Decimal("375"), gt=0)
    big_win: BigWinConfig = BigWinConfig()
    trinity_size_down: TrinitySizeDownConfig = TrinitySizeDownConfig()
    vix_gap: VixGapSizingConfig = VixGapSizingConfig()
    micro_equivalent_limit: int = Field(50, ge=1)
