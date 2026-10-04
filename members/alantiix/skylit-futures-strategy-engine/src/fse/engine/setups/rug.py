"""Rug and Reverse_Rug (design §10 table, Req 10.1).

With ``stack = stack_pct / 100 x spot``, a Pika a Node with value above 0
and a Barney one with value below 0:

- ``rug``: the nearest Pika Node above spot, when a Barney Node lies below it
  by at most ``stack`` and the Floor is not a Pika (no Floor, or a Barney
  Floor). Direction: short.
- ``reverse_rug``: the nearest Pika Node below spot, when a Barney Node lies
  above it by at most ``stack``. Direction: long.
"""

from __future__ import annotations

from typing import Final

from fse.config.schema.patterns import PatternsConfig, ReverseRugConfig, RugConfig
from fse.engine.setups.base import DetectContext, PatternDetector, Pick, Source

__all__ = ["REVERSE_RUG", "RUG", "select_reverse_rug", "select_rug"]

_PCT: Final = 100.0


def _barney_within(source: Source, pika: float, stack: float, *, above: bool) -> bool:
    """Whether a Barney Node lies above (or below) ``pika`` by at most ``stack``."""
    sign = 1.0 if above else -1.0
    return any(source.value(n) < 0 and 0 < sign * (n - pika) <= stack for n in source.labels.nodes)


def select_rug(ctx: DetectContext, source: Source, cfg: RugConfig) -> tuple[Pick, ...]:
    labels = source.labels
    spot = source.spot
    if labels.floor is not None and source.value(labels.floor) > 0:
        return ()
    pikas = [n for n in labels.nodes if n > spot and source.value(n) > 0]
    if not pikas:
        return ()
    pika = min(pikas)
    stack = cfg.stack_pct / _PCT * spot
    return ((pika, "short"),) if _barney_within(source, pika, stack, above=False) else ()


def select_reverse_rug(
    ctx: DetectContext, source: Source, cfg: ReverseRugConfig
) -> tuple[Pick, ...]:
    spot = source.spot
    pikas = [n for n in source.labels.nodes if n < spot and source.value(n) > 0]
    if not pikas:
        return ()
    pika = max(pikas)
    stack = cfg.stack_pct / _PCT * spot
    return ((pika, "long"),) if _barney_within(source, pika, stack, above=True) else ()


def _config(patterns: PatternsConfig) -> RugConfig:
    return patterns.rug


def _config_reverse(patterns: PatternsConfig) -> ReverseRugConfig:
    return patterns.reverse_rug


RUG: Final = PatternDetector("rug", "rug", _config, select_rug)
REVERSE_RUG: Final = PatternDetector(
    "reverse_rug", "reverse_rug", _config_reverse, select_reverse_rug
)
