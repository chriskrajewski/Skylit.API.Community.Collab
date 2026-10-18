"""The ``levels`` section of the Strategy_Config (design §8 "Level_Converter").

How the Level_Converter maps strikes to ES and NQ prices:

- ``methods``: the conversion method of SPY, NDX and NDXP, ``offset`` or
  ``ratio`` (default ``ratio``, Req 8.3). SPX always uses the offset method and
  QQQ the ratio method (Req 8.1-8.2), so neither has a key here.
- ``es_half_width_pts``: the ES Deflection_Band half-width h in points
  (default 5, range 0.25 to 25, Req 8.6).
- ``qqq_half_width_usd``: the QQQ half-width in dollars (default 0.50, range
  0.01 to 2.50). An NQ band's half-width is this times NQ price ÷ QQQ spot
  (Req 8.7).
- ``max_price_gap_s``: the maximum price gap, in seconds, between a paired
  futures bar's close and the Snapshot's ``asOf`` (default 120, range 60 to
  600, Req 8.10).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from fse.config.schema._base import SchemaModel

__all__ = [
    "ES_HALF_WIDTH_MAX_PTS",
    "ES_HALF_WIDTH_MIN_PTS",
    "MAX_PRICE_GAP_MAX_S",
    "MAX_PRICE_GAP_MIN_S",
    "QQQ_HALF_WIDTH_MAX_USD",
    "QQQ_HALF_WIDTH_MIN_USD",
    "LevelMethod",
    "LevelMethodsConfig",
    "LevelsConfig",
]

type LevelMethod = Literal["offset", "ratio"]
"""The same values as ``fse.engine.types.ConversionMethod``; a test keeps them equal."""

ES_HALF_WIDTH_MIN_PTS: Final = 0.25
ES_HALF_WIDTH_MAX_PTS: Final = 25.0
QQQ_HALF_WIDTH_MIN_USD: Final = 0.01
QQQ_HALF_WIDTH_MAX_USD: Final = 2.50
MAX_PRICE_GAP_MIN_S: Final = 60
MAX_PRICE_GAP_MAX_S: Final = 600


class LevelMethodsConfig(SchemaModel):
    """The configurable conversion method per symbol; keys match ``data.symbols`` names."""

    SPY: LevelMethod = "ratio"
    NDX: LevelMethod = "ratio"
    NDXP: LevelMethod = "ratio"


class LevelsConfig(SchemaModel):
    """Conversion methods, Deflection_Band half-widths and the maximum price gap."""

    methods: LevelMethodsConfig = LevelMethodsConfig()
    es_half_width_pts: float = Field(5.0, ge=ES_HALF_WIDTH_MIN_PTS, le=ES_HALF_WIDTH_MAX_PTS)
    qqq_half_width_usd: float = Field(0.50, ge=QQQ_HALF_WIDTH_MIN_USD, le=QQQ_HALF_WIDTH_MAX_USD)
    max_price_gap_s: int = Field(120, ge=MAX_PRICE_GAP_MIN_S, le=MAX_PRICE_GAP_MAX_S)
