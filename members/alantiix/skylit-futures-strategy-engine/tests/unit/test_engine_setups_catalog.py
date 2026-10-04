"""Smoke test for the Setup_Detector catalog (design §10, task 13.8).

The registry and the ``patterns`` Config_Schema section list the same eight
detectors, each detector reads its own ``patterns`` entry, and every entry has
an ``enabled`` bool and numeric parameters whose defaults validate.

**Validates: Requirements 10.1**
"""

from __future__ import annotations

import pytest

from fse.config.schema.patterns import DETECTOR_IDS, PatternsConfig
from fse.engine.setups.base import PatternDetector
from fse.engine.setups.registry import DETECTORS, REGISTRY, detector


def test_catalog_lists_eight_detectors_in_schema_order() -> None:
    assert len(REGISTRY) == len(DETECTOR_IDS) == 8
    assert tuple(d.id for d in REGISTRY) == DETECTOR_IDS == tuple(DETECTORS)
    assert tuple(PatternsConfig.model_fields) == DETECTOR_IDS


@pytest.mark.parametrize("detector_id", DETECTOR_IDS)
def test_each_detector_has_an_id_a_flag_and_numeric_parameters(detector_id: str) -> None:
    found = detector(detector_id)
    assert found is DETECTORS[detector_id]
    assert found.id == detector_id
    cfg = PatternsConfig()
    entry = cfg.get(detector_id)
    # The detector reads its own entry of the section.
    assert isinstance(found, PatternDetector)
    assert found.config_of(cfg) is entry
    dumped = entry.model_dump()
    assert isinstance(dumped["enabled"], bool)
    numeric = [k for k, v in dumped.items() if type(v) in (int, float)]
    assert numeric, f"{detector_id} has no numeric parameter"


def test_defaults_validate() -> None:
    defaults = PatternsConfig()
    assert PatternsConfig.model_validate({}) == defaults
    assert PatternsConfig.model_validate(defaults.model_dump(mode="json")) == defaults
