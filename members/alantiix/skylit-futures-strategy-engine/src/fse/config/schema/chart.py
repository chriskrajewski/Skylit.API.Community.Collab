"""The ``chart`` section of the Strategy_Config (design §9, "Strategy_Config shape").

Parameters of the Chart_Feature_Builder (``fse.engine.chart``, Req 9):

- ``pivot_len``: the swing pivot length N, in bars on each side, for 1-hour
  (key ``"60"``) and 4-hour (key ``"240"``) bars. Integer 1 to 20, default 3
  (Req 9.7). Keys are minutes, as in the design sketch.
- ``swings_kept``: how many of the most recent confirmed swing highs, and of
  the swing lows, are kept per instrument and timeframe. Integer 1 to 50,
  default 5 (Req 9.8).
- ``bos_pivot_len``: the break-of-structure pivot length for 1-minute (key
  ``"1"``) and 5-minute (key ``"5"``) bars. Integer 1 to 20, default 3
  (Req 9.9-9.10).
- ``sweep_ticks``: how far a wick must cross a level, in ticks, for a BOS_Leg
  sweep (Req 9.14) or a liquidity sweep (Req 9.16-9.17). Integer 1 to 20,
  default 2.
- ``candle_timeframe_s`` / ``sweep_timeframe_s``: the bar timeframe whose bars
  get candle labels (Req 9.15) and are checked for liquidity sweeps
  (Req 9.16-9.17). One of 60, 300, 3600 or 14400 seconds; default 60.
"""

from __future__ import annotations

from typing import Final

from pydantic import ConfigDict, Field, field_validator

from fse.config.schema._base import SchemaModel, not_allowed_error

__all__ = [
    "BOS_TIMEFRAMES_S",
    "CHART_TIMEFRAMES_S",
    "PIVOT_LEN_MAX",
    "SWEEP_TICKS_MAX",
    "SWINGS_KEPT_MAX",
    "SWING_TIMEFRAMES_S",
    "BosPivotLenConfig",
    "ChartConfig",
    "SwingPivotLenConfig",
]

CHART_TIMEFRAMES_S: Final[tuple[int, ...]] = (60, 300, 3600, 14400)
"""The bar timeframes the Chart_Feature_Builder builds: 1 minute, 5 minutes, 1 and 4 hours."""

BOS_TIMEFRAMES_S: Final[tuple[int, ...]] = (60, 300)
"""Timeframes with breaks of structure and BOS_Legs (Req 9.9-9.10)."""

SWING_TIMEFRAMES_S: Final[tuple[int, ...]] = (3600, 14400)
"""Timeframes whose confirmed swings are exposed as Chart_Levels (Req 9.8)."""

PIVOT_LEN_MAX: Final = 20
SWINGS_KEPT_MAX: Final = 50
SWEEP_TICKS_MAX: Final = 20


class SwingPivotLenConfig(SchemaModel):
    """Swing pivot length per swing timeframe; keys are minutes (``"60"``, ``"240"``)."""

    model_config = ConfigDict(serialize_by_alias=True)

    one_hour: int = Field(3, ge=1, le=PIVOT_LEN_MAX, alias="60")
    four_hour: int = Field(3, ge=1, le=PIVOT_LEN_MAX, alias="240")


class BosPivotLenConfig(SchemaModel):
    """Break-of-structure pivot length per BOS timeframe; keys are minutes (``"1"``, ``"5"``)."""

    model_config = ConfigDict(serialize_by_alias=True)

    one_minute: int = Field(3, ge=1, le=PIVOT_LEN_MAX, alias="1")
    five_minute: int = Field(3, ge=1, le=PIVOT_LEN_MAX, alias="5")


class ChartConfig(SchemaModel):
    """Pivot lengths, swing retention, sweep ticks, and the candle and sweep timeframes."""

    pivot_len: SwingPivotLenConfig = SwingPivotLenConfig()
    swings_kept: int = Field(5, ge=1, le=SWINGS_KEPT_MAX)
    bos_pivot_len: BosPivotLenConfig = BosPivotLenConfig()
    sweep_ticks: int = Field(2, ge=1, le=SWEEP_TICKS_MAX)
    candle_timeframe_s: int = 60
    sweep_timeframe_s: int = 60

    @field_validator("candle_timeframe_s", "sweep_timeframe_s")
    @classmethod
    def _known_timeframe(cls, value: int) -> int:
        if value not in CHART_TIMEFRAMES_S:
            raise not_allowed_error(value, CHART_TIMEFRAMES_S, "the chart timeframes")
        return value

    def pivot_len_for(self, timeframe_s: int) -> int:
        """N for ``timeframe_s``: the BOS pivot on 1m and 5m, the swing pivot on 1h and 4h."""
        match timeframe_s:
            case 60:
                return self.bos_pivot_len.one_minute
            case 300:
                return self.bos_pivot_len.five_minute
            case 3600:
                return self.pivot_len.one_hour
            case 14400:
                return self.pivot_len.four_hour
        raise ValueError(
            f"timeframe {timeframe_s!r} s is not one of the chart timeframes {CHART_TIMEFRAMES_S}"
        )
