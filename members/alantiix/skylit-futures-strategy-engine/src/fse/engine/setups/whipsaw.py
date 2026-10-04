"""Whipsaw_Fade (design §10 table, Req 10.1).

Source Nodes: the Floor (long), then the Ceiling (short), any Floor included.
Condition: the Regime at the Decision_Time is Whipsaw; a missing Regime
selects nothing.
"""

from __future__ import annotations

from typing import Final

from fse.config.schema.patterns import PatternsConfig, WhipsawFadeConfig
from fse.engine.setups.base import DetectContext, PatternDetector, Pick, Source

__all__ = ["WHIPSAW_FADE", "select_whipsaw_fade"]


def select_whipsaw_fade(
    ctx: DetectContext, source: Source, cfg: WhipsawFadeConfig
) -> tuple[Pick, ...]:
    if ctx.regime_value != "Whipsaw":
        return ()
    labels = source.labels
    picks: list[Pick] = []
    if labels.floor is not None:
        picks.append((labels.floor, "long"))
    if labels.ceiling is not None:
        picks.append((labels.ceiling, "short"))
    return tuple(picks)


def _config(patterns: PatternsConfig) -> WhipsawFadeConfig:
    return patterns.whipsaw_fade


WHIPSAW_FADE: Final = PatternDetector("whipsaw_fade", "whipsaw_fade", _config, select_whipsaw_fade)
