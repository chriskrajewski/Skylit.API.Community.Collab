"""Trend_Follow (design §10 table, Req 10.1).

Conditions: the King is at least ``trend_min_pct / 100 x spot`` from spot,
and at most ``max_intermediate`` Nodes lie strictly between spot and the
King. Source Node: the nearest Node on the side of spot opposite the King.
Direction: toward the King (long when the King is above spot).
"""

from __future__ import annotations

from typing import Final

from fse.config.schema.patterns import PatternsConfig, TrendFollowConfig
from fse.engine.setups.base import DetectContext, PatternDetector, Pick, Source

__all__ = ["TREND_FOLLOW", "select_trend_follow"]

_PCT: Final = 100.0


def select_trend_follow(
    ctx: DetectContext, source: Source, cfg: TrendFollowConfig
) -> tuple[Pick, ...]:
    labels = source.labels
    king = labels.king
    spot = source.spot
    if king is None or king == spot:
        return ()
    if not abs(king - spot) >= cfg.trend_min_pct / _PCT * spot:
        return ()
    lo, hi = min(king, spot), max(king, spot)
    if sum(1 for n in labels.nodes if lo < n < hi) > cfg.max_intermediate:
        return ()
    if king > spot:
        below = [n for n in labels.nodes if n < spot]
        return ((max(below), "long"),) if below else ()
    above = [n for n in labels.nodes if n > spot]
    return ((min(above), "short"),) if above else ()


def _config(patterns: PatternsConfig) -> TrendFollowConfig:
    return patterns.trend_follow


TREND_FOLLOW: Final = PatternDetector("trend_follow", "trend_follow", _config, select_trend_follow)
