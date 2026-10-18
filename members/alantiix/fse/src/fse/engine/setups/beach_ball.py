"""Beach_Ball (design §10 table, Req 10.1).

Source Nodes: every Barney Node (value below 0) below spot whose absolute
value is at least ``major_fraction`` x the King's, in strike order. Direction:
long. No extra condition.
"""

from __future__ import annotations

from typing import Final

from fse.config.schema.patterns import BeachBallConfig, PatternsConfig
from fse.engine.setups.base import DetectContext, PatternDetector, Pick, Source

__all__ = ["BEACH_BALL", "select_beach_ball"]


def select_beach_ball(ctx: DetectContext, source: Source, cfg: BeachBallConfig) -> tuple[Pick, ...]:
    labels = source.labels
    if labels.king is None:
        return ()
    major = cfg.major_fraction * abs(source.value(labels.king))
    spot = source.spot
    return tuple(
        (n, "long")
        for n in labels.nodes
        if n < spot and source.value(n) < 0 and abs(source.value(n)) >= major
    )


def _config(patterns: PatternsConfig) -> BeachBallConfig:
    return patterns.beach_ball


BEACH_BALL: Final = PatternDetector("beach_ball", "beach_ball", _config, select_beach_ball)
