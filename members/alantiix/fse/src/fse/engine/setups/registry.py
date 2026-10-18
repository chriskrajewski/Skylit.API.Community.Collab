"""The Setup_Detector registry (design §10, Req 10.1-10.4).

:data:`REGISTRY` holds the eight detectors in the design §10 table order,
which is also ``fse.config.schema.patterns.DETECTOR_IDS``. :func:`detect_setups`
runs each enabled detector over the same frozen :class:`DetectContext` and
concatenates their outputs in registry order. Detectors share no mutable
state and each reads only its own ``patterns`` entry, so a detector's output
never depends on another detector's flag (Req 10.3-10.4); with no detector
enabled the output is empty.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from fse.engine.setups.base import (
    DetectContext,
    Detection,
    Detector,
    DetectorParams,
    PatternDetector,
    Pick,
    Source,
    construct,
    sources,
)
from fse.engine.setups.beach_ball import BEACH_BALL
from fse.engine.setups.floor_ceiling import (
    FLOOR_CEILING_BOUNCE,
    FLOOR_CEILING_BOUNCE_EMPTY_BASEMENT,
)
from fse.engine.setups.gatekeeper_fade import GATEKEEPER_FADE
from fse.engine.setups.rug import REVERSE_RUG, RUG
from fse.engine.setups.trend import TREND_FOLLOW
from fse.engine.setups.whipsaw import WHIPSAW_FADE

__all__ = [
    "DETECTORS",
    "REGISTRY",
    "DetectContext",
    "Detection",
    "Detector",
    "DetectorParams",
    "PatternDetector",
    "Pick",
    "Source",
    "construct",
    "detect_setups",
    "detector",
    "sources",
]

REGISTRY: Final[tuple[Detector, ...]] = (
    GATEKEEPER_FADE,
    FLOOR_CEILING_BOUNCE,
    FLOOR_CEILING_BOUNCE_EMPTY_BASEMENT,
    BEACH_BALL,
    RUG,
    REVERSE_RUG,
    WHIPSAW_FADE,
    TREND_FOLLOW,
)
"""Every detector, in run order."""

DETECTORS: Final[Mapping[str, Detector]] = MappingProxyType({d.id: d for d in REGISTRY})
"""The detectors by id."""


def detector(detector_id: str) -> Detector:
    """The detector with ``detector_id``; ``ValueError`` for an unknown id."""
    found = DETECTORS.get(detector_id)
    if found is None:
        raise ValueError(f"{detector_id!r} is not one of the detector ids {tuple(DETECTORS)}")
    return found


def detect_setups(ctx: DetectContext, p: DetectorParams) -> tuple[Detection, ...]:
    """Candidate_Setups and Detection_Skips of the enabled detectors, in registry order."""
    out: list[Detection] = []
    for d in REGISTRY:
        if p.patterns.enabled(d.id):
            out.extend(d.detect(ctx, p))
    return tuple(out)
