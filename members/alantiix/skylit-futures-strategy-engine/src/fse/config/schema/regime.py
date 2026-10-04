"""The ``regime`` section of the Strategy_Config (design §7, "Strategy_Config shape").

Regime_Classifier parameters (Req 7.1-7.10), read by ``fse.engine.regime``:

- ``regime_distance_pct``: the regime distance, in percent of the Snapshot's
  spot; raw GEX/VEX magnitudes and net GEX read strikes at most this far from
  spot (default 1.0, Req 7.2, 7.6).
- ``vanna_multiple``: Vanna_Dominant needs the normalized VEX magnitude to be
  at least this multiple of the normalized GEX magnitude (default 2.0, above
  0, Req 7.2).
- ``min_abs_value``: Structureless when no gamma strike's absolute value
  reaches it. Required, with no default, above 0 (Req 7.4); ``fse calibrate
  regime-min-abs`` prints a percentile of cached King values to choose it from.
- ``whipsaw_pct``: Whipsaw when the Floor and Ceiling absolute values differ by
  at most this percent of the larger (default 15.0, Req 7.5).
- ``vix_condition``: on/off; ``pct``, the rise over the prior session's VIX
  close that Vanna_Dominant needs (default 5.0, at least 0); and
  ``intraday_source``, whether an intraday 1-minute VIX source is configured
  (default false: the VIX value is the session's daily open) (Req 7.3).
- ``grade``: ``major_fraction``, the share of the King's absolute value a
  major Node needs (default 0.50, range 0.01 to 1), and
  ``floor_ceiling_ratio``, the larger-to-smaller Floor/Ceiling ratio an
  A_Plus_Map needs (default 1.5, at least 1) (Req 7.8-7.9).

Percents of spot or of a value must be above 0 and at most 100, except the
VIX ``pct``, which has no upper bound. Every float rejects NaN and infinity.
"""

from __future__ import annotations

from pydantic import Field

from fse.config.schema._base import SchemaModel
from fse.config.schema.nodes import FRACTION_MIN, PCT_MAX

__all__ = ["GradeConfig", "RegimeConfig", "VixConditionConfig"]


class VixConditionConfig(SchemaModel):
    """The VIX rise that Vanna_Dominant needs, and where the VIX value comes from."""

    enabled: bool = True
    pct: float = Field(5.0, ge=0, allow_inf_nan=False)
    intraday_source: bool = False


class GradeConfig(SchemaModel):
    """Map_Grade parameters (Req 7.8-7.10)."""

    major_fraction: float = Field(0.50, ge=FRACTION_MIN, le=1.0)
    floor_ceiling_ratio: float = Field(1.5, ge=1.0, allow_inf_nan=False)


class RegimeConfig(SchemaModel):
    """Regime and Map_Grade parameters; ``min_abs_value`` has no default."""

    regime_distance_pct: float = Field(1.0, gt=0, le=PCT_MAX)
    vanna_multiple: float = Field(2.0, gt=0, allow_inf_nan=False)
    min_abs_value: float = Field(gt=0, allow_inf_nan=False)
    whipsaw_pct: float = Field(15.0, gt=0, le=PCT_MAX)
    vix_condition: VixConditionConfig = VixConditionConfig()
    grade: GradeConfig = GradeConfig()
