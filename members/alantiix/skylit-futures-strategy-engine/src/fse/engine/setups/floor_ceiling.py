"""Floor_Ceiling_Bounce and its Empty_Basement variant (design §10 table, Req 10.1-10.2).

- ``floor_ceiling_bounce``: long at the Floor when the Snapshot is not
  Empty_Basement, then short at the Ceiling.
- ``floor_ceiling_bounce_empty_basement``: long at the Floor when the
  Snapshot is Empty_Basement. A Floor labeled Empty_Basement is a source only
  here (Req 10.2).

Both write the Pattern ``floor_ceiling_bounce`` to the Setup_Key; the
detector id tells them apart.
"""

from __future__ import annotations

from typing import Final

from fse.config.schema.patterns import (
    EmptyBasementConfig,
    FloorCeilingBounceConfig,
    PatternsConfig,
)
from fse.engine.setups.base import DetectContext, PatternDetector, Pick, Source

__all__ = [
    "FLOOR_CEILING_BOUNCE",
    "FLOOR_CEILING_BOUNCE_EMPTY_BASEMENT",
    "PATTERN",
    "select_empty_basement",
    "select_floor_ceiling",
]

PATTERN: Final = "floor_ceiling_bounce"


def select_floor_ceiling(
    ctx: DetectContext, source: Source, cfg: FloorCeilingBounceConfig
) -> tuple[Pick, ...]:
    labels = source.labels
    picks: list[Pick] = []
    if labels.floor is not None and not labels.empty_basement:
        picks.append((labels.floor, "long"))
    if labels.ceiling is not None:
        picks.append((labels.ceiling, "short"))
    return tuple(picks)


def select_empty_basement(
    ctx: DetectContext, source: Source, cfg: EmptyBasementConfig
) -> tuple[Pick, ...]:
    labels = source.labels
    if labels.floor is not None and labels.empty_basement:
        return ((labels.floor, "long"),)
    return ()


def _config(patterns: PatternsConfig) -> FloorCeilingBounceConfig:
    return patterns.floor_ceiling_bounce


def _config_eb(patterns: PatternsConfig) -> EmptyBasementConfig:
    return patterns.floor_ceiling_bounce_empty_basement


FLOOR_CEILING_BOUNCE: Final = PatternDetector(
    "floor_ceiling_bounce", PATTERN, _config, select_floor_ceiling
)
FLOOR_CEILING_BOUNCE_EMPTY_BASEMENT: Final = PatternDetector(
    "floor_ceiling_bounce_empty_basement", PATTERN, _config_eb, select_empty_basement
)
