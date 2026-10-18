"""The ``fills`` section of the Strategy_Config (design §13 "Fill_Simulator", Req 13).

Settings of the Fill_Simulator (``fse.sim.fills``):

- ``trade_through_ticks``: how far a bar must trade through a resting limit
  (entry or target) for it to fill. Whole ticks 0 to 20, default 1 (Req 13.1).
  0 means touch fills, which reports label "touch fills (optimistic)" (Req 13.2).
- ``slippage_ticks``: how far stop and market fills move against the order.
  Whole ticks 0 to 20, default 1 (Req 13.3-13.4).
- ``costs``: per instrument (MES, MNQ, ES, NQ), the ``commission`` and the
  ``exchange_fee`` in dollars per contract per side. Each is required with no
  default and ranges from $0.00 to $25.00 (Req 13.7-13.8). An instrument with
  no entry is ``None``; the Config_Loader (task 20.1) rejects a config whose
  traded instruments (``data.instruments``) have no costs.

Money is a quoted decimal string in YAML (``"0.37"``), read into a ``Decimal``
(``fse.config.schema.sizing.YamlMoney``), so fees never pass through a float.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from pydantic import Field

from fse.config.schema._base import SchemaModel
from fse.config.schema.sizing import YamlMoney

__all__ = [
    "FEE_MAX_USD",
    "FEE_MIN_USD",
    "FILL_INSTRUMENTS",
    "FILL_TICKS_MAX",
    "CostsConfig",
    "FillInstrument",
    "FillsConfig",
    "InstrumentCosts",
]

type FillInstrument = Literal["MES", "MNQ", "ES", "NQ"]
"""The instruments with a contract value in the Fill_Simulator (Req 13.9)."""

FILL_INSTRUMENTS: Final[tuple[FillInstrument, ...]] = ("MES", "MNQ", "ES", "NQ")

FILL_TICKS_MAX: Final = 20
"""The largest trade-through distance and slippage, in ticks."""
FEE_MIN_USD: Final = Decimal("0.00")
FEE_MAX_USD: Final = Decimal("25.00")


class InstrumentCosts(SchemaModel):
    """Commission and exchange fee per contract per side; both required (Req 13.8)."""

    commission: YamlMoney = Field(ge=FEE_MIN_USD, le=FEE_MAX_USD)
    exchange_fee: YamlMoney = Field(ge=FEE_MIN_USD, le=FEE_MAX_USD)

    @property
    def per_contract(self) -> Decimal:
        """Commission plus exchange fee: what one contract costs per fill (Req 13.7)."""
        return self.commission + self.exchange_fee


class CostsConfig(SchemaModel):
    """Costs per instrument; ``None`` for an instrument the config gives no costs."""

    MES: InstrumentCosts | None = None
    MNQ: InstrumentCosts | None = None
    ES: InstrumentCosts | None = None
    NQ: InstrumentCosts | None = None


class FillsConfig(SchemaModel):
    """Trade-through distance, slippage, and per-instrument commission and exchange fee."""

    trade_through_ticks: int = Field(1, ge=0, le=FILL_TICKS_MAX)
    slippage_ticks: int = Field(1, ge=0, le=FILL_TICKS_MAX)
    costs: CostsConfig = CostsConfig()

    def costs_for(self, instrument: str) -> InstrumentCosts | None:
        """The costs of ``instrument``, or ``None`` when it has none or is not an instrument."""
        if instrument not in FILL_INSTRUMENTS:
            return None
        found: InstrumentCosts | None = getattr(self.costs, instrument)
        return found
