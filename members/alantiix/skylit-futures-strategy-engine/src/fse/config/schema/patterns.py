"""The ``patterns`` section of the Strategy_Config (design §10, Req 10.1).

One entry per Setup_Detector detector, in registry order (:data:`DETECTOR_IDS`):
``gatekeeper_fade``, ``floor_ceiling_bounce``,
``floor_ceiling_bounce_empty_basement``, ``beach_ball``, ``rug``,
``reverse_rug``, ``whipsaw_fade`` and ``trend_follow``. Read by
``fse.engine.setups``. Every detector has these keys:

- ``enabled``: the on/off switch (default on, Req 10.3).
- ``metric``: the source Snapshot metric, ``gamma`` or ``vanna`` (default
  ``gamma``, Req 10.5).
- ``entry_offset_ticks``: the limit entry's offset from the converted level;
  a positive offset raises a long entry and lowers a short entry (default 0,
  range -80 to 80, Req 10.7).
- ``stop_rule``: ``one_node_beyond``, ``fixed_ticks`` or ``null`` (default).
  ``null`` applies the stop rule of the ``exits`` setting for the setup's
  Regime (``exits.per_regime``, else ``exits.global``, Req 12.2); a set value
  overrides it for this detector (Req 10.8-10.9).
- ``fixed_stop_ticks``: the fixed-ticks distance beyond the Deflection_Band
  edge (default 8, range 1 to 200, Req 10.9).
- ``invalidation``: the one-Node-beyond invalidation level, ``level`` (the
  source Node's converted level, default) or ``band_edge`` (the band edge on
  the stop side) (Req 10.8).
- ``stop_lookout_pct``: the one-Node-beyond lookout distance from the
  invalidation level, in percent of the source Snapshot's paired futures price
  (default 1.0, above 0 and at most 100, Req 10.10).
- ``arming``: ``es_pts``, the ES arming distance in points (default 10.0,
  above 0 and at most 100), and ``nq_qqq_usd``, the NQ arming distance in QQQ
  dollars, scaled by NQ ÷ QQQ (default 1.00, above 0 and at most 10)
  (Req 10.6).

Pattern-specific keys (design §10 table):

- ``beach_ball.major_fraction``: the share of the King's absolute value a
  Barney Node needs (default 0.50, range 0.01 to 1).
- ``rug.stack_pct`` / ``reverse_rug.stack_pct``: the largest distance from the
  Pika Node to the Barney Node stacked beside it, in percent of the Snapshot's
  spot (default 0.5, above 0 and at most 100).
- ``trend_follow.trend_min_pct``: the smallest King-to-spot distance, in
  percent of spot (default 1.0, above 0 and at most 100), and
  ``max_intermediate``, the most Nodes strictly between spot and the King
  (default 1, range 0 to 20).

Every float rejects NaN and infinity.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from fse.config.schema._base import SchemaModel
from fse.config.schema.nodes import FRACTION_MIN, PCT_MAX

__all__ = [
    "ARMING_ES_MAX_PTS",
    "ARMING_NQ_MAX_USD",
    "DETECTOR_IDS",
    "ENTRY_OFFSET_TICKS_MAX",
    "FIXED_STOP_TICKS_MAX",
    "INVALIDATION_LEVELS",
    "MAX_INTERMEDIATE_MAX",
    "PATTERN_STOP_RULES",
    "ArmingConfig",
    "BeachBallConfig",
    "DetectorConfig",
    "EmptyBasementConfig",
    "FloorCeilingBounceConfig",
    "GatekeeperFadeConfig",
    "InvalidationLevel",
    "PatternMetric",
    "PatternStopRule",
    "PatternsConfig",
    "ReverseRugConfig",
    "RugConfig",
    "TrendFollowConfig",
    "WhipsawFadeConfig",
]

type PatternMetric = Literal["gamma", "vanna"]
"""The same values as ``fse.engine.types.Metric``."""
type PatternStopRule = Literal["one_node_beyond", "fixed_ticks"]
"""The same values as ``fse.engine.types.StopRule``."""
type InvalidationLevel = Literal["level", "band_edge"]

DETECTOR_IDS: Final[tuple[str, ...]] = (
    "gatekeeper_fade",
    "floor_ceiling_bounce",
    "floor_ceiling_bounce_empty_basement",
    "beach_ball",
    "rug",
    "reverse_rug",
    "whipsaw_fade",
    "trend_follow",
)
"""The detector ids in registry order (design §10 table); also the section keys."""

PATTERN_STOP_RULES: Final[frozenset[str]] = frozenset({"one_node_beyond", "fixed_ticks"})
INVALIDATION_LEVELS: Final[frozenset[str]] = frozenset({"level", "band_edge"})

ENTRY_OFFSET_TICKS_MAX: Final = 80
FIXED_STOP_TICKS_MAX: Final = 200
ARMING_ES_MAX_PTS: Final = 100.0
ARMING_NQ_MAX_USD: Final = 10.0
MAX_INTERMEDIATE_MAX: Final = 20


class ArmingConfig(SchemaModel):
    """How close Futures_Price must be to the converted level to arm (Req 10.6)."""

    es_pts: float = Field(10.0, gt=0, le=ARMING_ES_MAX_PTS, allow_inf_nan=False)
    nq_qqq_usd: float = Field(1.00, gt=0, le=ARMING_NQ_MAX_USD, allow_inf_nan=False)


class DetectorConfig(SchemaModel):
    """The keys every detector has: switch, metric, entry, stop and arming."""

    enabled: bool = True
    metric: PatternMetric = "gamma"
    entry_offset_ticks: int = Field(0, ge=-ENTRY_OFFSET_TICKS_MAX, le=ENTRY_OFFSET_TICKS_MAX)
    stop_rule: PatternStopRule | None = None
    fixed_stop_ticks: int = Field(8, ge=1, le=FIXED_STOP_TICKS_MAX)
    invalidation: InvalidationLevel = "level"
    stop_lookout_pct: float = Field(1.0, gt=0, le=PCT_MAX, allow_inf_nan=False)
    arming: ArmingConfig = ArmingConfig()


class GatekeeperFadeConfig(DetectorConfig):
    """Gatekeeper_Fade: fade a Pika Gatekeeper, short above spot and long below."""


class FloorCeilingBounceConfig(DetectorConfig):
    """Floor_Ceiling_Bounce: long a Floor that is not Empty_Basement, short a Ceiling."""


class EmptyBasementConfig(DetectorConfig):
    """The Empty_Basement variant of Floor_Ceiling_Bounce: long an Empty_Basement Floor."""


class BeachBallConfig(DetectorConfig):
    """Beach_Ball: long a major Barney Node below spot."""

    major_fraction: float = Field(0.50, ge=FRACTION_MIN, le=1.0, allow_inf_nan=False)


class RugConfig(DetectorConfig):
    """Rug: short the nearest Pika above spot stacked over a Barney, with no Pika Floor."""

    stack_pct: float = Field(0.5, gt=0, le=PCT_MAX, allow_inf_nan=False)


class ReverseRugConfig(DetectorConfig):
    """Reverse_Rug: long the nearest Pika below spot with a Barney stacked above it."""

    stack_pct: float = Field(0.5, gt=0, le=PCT_MAX, allow_inf_nan=False)


class WhipsawFadeConfig(DetectorConfig):
    """Whipsaw_Fade: long the Floor and short the Ceiling while the Regime is Whipsaw."""


class TrendFollowConfig(DetectorConfig):
    """Trend_Follow: from the nearest Node opposite the King, toward a distant King."""

    trend_min_pct: float = Field(1.0, gt=0, le=PCT_MAX, allow_inf_nan=False)
    max_intermediate: int = Field(1, ge=0, le=MAX_INTERMEDIATE_MAX)


class PatternsConfig(SchemaModel):
    """One entry per detector, keyed by detector id (Req 10.1)."""

    gatekeeper_fade: GatekeeperFadeConfig = GatekeeperFadeConfig()
    floor_ceiling_bounce: FloorCeilingBounceConfig = FloorCeilingBounceConfig()
    floor_ceiling_bounce_empty_basement: EmptyBasementConfig = EmptyBasementConfig()
    beach_ball: BeachBallConfig = BeachBallConfig()
    rug: RugConfig = RugConfig()
    reverse_rug: ReverseRugConfig = ReverseRugConfig()
    whipsaw_fade: WhipsawFadeConfig = WhipsawFadeConfig()
    trend_follow: TrendFollowConfig = TrendFollowConfig()

    def get(self, detector_id: str) -> DetectorConfig:
        """The entry of ``detector_id``; ``ValueError`` for a name that is not a detector id."""
        if detector_id not in DETECTOR_IDS:
            raise ValueError(f"{detector_id!r} is not one of the detector ids {DETECTOR_IDS}")
        cfg: DetectorConfig = getattr(self, detector_id)
        return cfg

    def enabled(self, detector_id: str) -> bool:
        """The enabled flag of ``detector_id``."""
        return self.get(detector_id).enabled
