"""Unit tests for the ``patterns`` Config_Schema section.

Design §10 and "Strategy_Config shape": one entry per detector id in registry
order, the shared keys with their defaults, the pattern-specific keys, ranges,
strict types and unknown keys rejected. Inputs are plain dicts, as a YAML safe
loader produces. Task 13.8 adds the detector catalog smoke test.

**Validates: Requirements 10.1**
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.patterns import (
    DETECTOR_IDS,
    INVALIDATION_LEVELS,
    PATTERN_STOP_RULES,
    ArmingConfig,
    DetectorConfig,
    InvalidationLevel,
    PatternMetric,
    PatternsConfig,
    PatternStopRule,
)
from fse.engine.types import METRICS, STOP_RULES, Metric, StopRule

DESIGN_GATEKEEPER_YAML = """
gatekeeper_fade: {enabled: true, metric: gamma, entry_offset_ticks: 0,
                  fixed_stop_ticks: 8, arming: {es_pts: 10.0, nq_qqq_usd: 1.00}}
"""


def error_types(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        PatternsConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_one_entry_per_detector_in_registry_order() -> None:
    assert tuple(PatternsConfig.model_fields) == DETECTOR_IDS
    assert len(DETECTOR_IDS) == 8
    cfg = PatternsConfig()
    for detector_id in DETECTOR_IDS:
        entry = cfg.get(detector_id)
        assert isinstance(entry, DetectorConfig)
        assert cfg.enabled(detector_id) is True


def test_defaults_match_the_design_sketch() -> None:
    cfg = PatternsConfig()
    assert cfg == PatternsConfig.model_validate(yaml.safe_load(DESIGN_GATEKEEPER_YAML))
    gk = cfg.gatekeeper_fade
    assert (gk.metric, gk.entry_offset_ticks, gk.stop_rule, gk.fixed_stop_ticks) == (
        "gamma",
        0,
        None,
        8,
    )
    assert (gk.invalidation, gk.stop_lookout_pct) == ("level", 1.0)
    assert gk.arming == ArmingConfig(es_pts=10.0, nq_qqq_usd=1.00)
    assert cfg.beach_ball.major_fraction == 0.50
    assert cfg.rug.stack_pct == cfg.reverse_rug.stack_pct == 0.5
    assert (cfg.trend_follow.trend_min_pct, cfg.trend_follow.max_intermediate) == (1.0, 1)


def test_literals_match_the_engine_types() -> None:
    assert set(get_args(PatternStopRule.__value__)) == PATTERN_STOP_RULES == STOP_RULES
    assert set(get_args(StopRule.__value__)) == STOP_RULES
    assert set(get_args(PatternMetric.__value__)) == METRICS == set(get_args(Metric.__value__))
    assert set(get_args(InvalidationLevel.__value__)) == INVALIDATION_LEVELS


def test_keys_parse_from_yaml() -> None:
    cfg = PatternsConfig.model_validate(
        {
            "rug": {"enabled": False, "metric": "vanna", "stack_pct": 1.25},
            "trend_follow": {"stop_rule": "fixed_ticks", "max_intermediate": 0},
            "beach_ball": {"entry_offset_ticks": -4, "invalidation": "band_edge"},
        }
    )
    assert (cfg.rug.enabled, cfg.rug.metric, cfg.rug.stack_pct) == (False, "vanna", 1.25)
    assert cfg.enabled("rug") is False
    assert (cfg.trend_follow.stop_rule, cfg.trend_follow.max_intermediate) == ("fixed_ticks", 0)
    assert (cfg.beach_ball.entry_offset_ticks, cfg.beach_ball.invalidation) == (-4, "band_edge")
    assert PatternsConfig.model_validate(cfg.model_dump()) == cfg


@pytest.mark.parametrize(
    ("value", "loc", "kind"),
    [
        ({"rug": {"stack_pct": 0}}, ("rug", "stack_pct"), "greater_than"),
        (
            {"beach_ball": {"major_fraction": 1.5}},
            ("beach_ball", "major_fraction"),
            "less_than_equal",
        ),
        (
            {"gatekeeper_fade": {"entry_offset_ticks": 81}},
            ("gatekeeper_fade", "entry_offset_ticks"),
            "less_than_equal",
        ),
        (
            {"whipsaw_fade": {"fixed_stop_ticks": 0}},
            ("whipsaw_fade", "fixed_stop_ticks"),
            "greater_than_equal",
        ),
        (
            {"trend_follow": {"arming": {"es_pts": float("nan")}}},
            ("trend_follow", "arming", "es_pts"),
            "finite_number",
        ),
        (
            {"floor_ceiling_bounce": {"stop_rule": "trailing"}},
            ("floor_ceiling_bounce", "stop_rule"),
            "literal_error",
        ),
        ({"gatekeeper_fade": {"metric": "delta"}}, ("gatekeeper_fade", "metric"), "literal_error"),
        ({"rug": {"enabled": "yes"}}, ("rug", "enabled"), "bool_type"),
        (
            {"gatekeeper_fade": {"stack_pct": 1.0}},
            ("gatekeeper_fade", "stack_pct"),
            "extra_forbidden",
        ),
        ({"vwap_fade": {}}, ("vwap_fade",), "extra_forbidden"),
    ],
)
def test_bad_values_are_rejected(value: dict[str, Any], loc: tuple[str, ...], kind: str) -> None:
    assert (loc, kind) in error_types(value)


def test_get_rejects_an_unknown_detector_id() -> None:
    with pytest.raises(ValueError, match="vwap_fade"):
        PatternsConfig().get("vwap_fade")
