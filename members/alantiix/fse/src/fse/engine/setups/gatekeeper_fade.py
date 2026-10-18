"""Gatekeeper_Fade (design §10 table, Req 10.1).

Source Nodes: every Gatekeeper with a value above 0 (a Pika), in strike
order. Direction: short when the Gatekeeper is above spot, long when below
(a Gatekeeper is never at spot). No extra condition.
"""

from __future__ import annotations

from typing import Final

from fse.config.schema.patterns import GatekeeperFadeConfig, PatternsConfig
from fse.engine.setups.base import DetectContext, PatternDetector, Pick, Source

__all__ = ["GATEKEEPER_FADE", "select_gatekeeper_fade"]


def select_gatekeeper_fade(
    ctx: DetectContext, source: Source, cfg: GatekeeperFadeConfig
) -> tuple[Pick, ...]:
    spot = source.spot
    return tuple(
        (g, "short" if g > spot else "long")
        for g in source.labels.gatekeepers
        if source.value(g) > 0
    )


def _config(patterns: PatternsConfig) -> GatekeeperFadeConfig:
    return patterns.gatekeeper_fade


GATEKEEPER_FADE: Final = PatternDetector(
    "gatekeeper_fade", "gatekeeper_fade", _config, select_gatekeeper_fade
)
