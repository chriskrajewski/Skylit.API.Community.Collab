"""Unit tests for the ``gates`` Config_Schema section.

Design §11 and "Strategy_Config shape": the Gate order, one entry per Gate id
with its defaults, the Playbook_Baseline allowed-Regime sets, ranges,
cross-key checks and unknown keys rejected. Inputs are plain dicts, as a YAML
safe loader produces. Task 15.4 adds the Gate catalog smoke test.

**Validates: Requirements 11.1, 11.2, 11.4**
"""

from __future__ import annotations

from datetime import time
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.gates import (
    GATE_IDS,
    GateConfig,
    GateDetectorId,
    GateId,
    GatesConfig,
    MapGradeName,
    RegimeName,
)
from fse.config.schema.patterns import DETECTOR_IDS
from fse.engine.types import MAP_GRADES, REGIMES

DESIGN_YAML = """
order: [stale_map, map_grade, midpoint, deflection_band, chart_confluence, stdev_fib_zone,
        dark_pool_confluence, trinity_agreement, candle_color, tap_count, third_gatekeeper_test,
        weekly_node_tests, sloppy_seconds, air_pocket_fade, min_reward_risk,
        opposition_inside_target, fomo_travel, open_shuffle, entry_cutoff, late_session_chase,
        news_window, vix_gap, regime_match, dormant_node, gatekeepers_on_path,
        node_growth_divergence, kill_switch_lockout]
stale_map: {enabled: true, max_snapshot_age_s: 90}
min_reward_risk: {enabled: true, min: 3.0, alert_min: 2.0}
opposition_inside_target: {enabled: true, fraction: 0.85, window_r: 3.0}
trinity_agreement: {enabled: true, min_agree: 2, empty_basement_exception: true}
dark_pool_confluence: {enabled: false}
stdev_fib_zone: {enabled: false}
"""


def error_types(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        GatesConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_one_entry_per_gate_in_req_11_1_order() -> None:
    assert len(GATE_IDS) == 27
    assert tuple(GatesConfig.model_fields) == ("order", "fade_detectors", *GATE_IDS)
    cfg = GatesConfig()
    assert cfg.order == GATE_IDS
    for gate_id in GATE_IDS:
        assert isinstance(cfg.get(gate_id), GateConfig)
    disabled = {"stdev_fib_zone", "dark_pool_confluence"}
    assert cfg.enabled_ids() == tuple(g for g in GATE_IDS if g not in disabled)


def test_defaults_match_the_design_sketch_and_baseline_sets() -> None:
    cfg = GatesConfig()
    assert cfg == GatesConfig.model_validate(yaml.safe_load(DESIGN_YAML))
    allowed = cfg.regime_match.allowed
    fades = ("gatekeeper_fade", "floor_ceiling_bounce", "floor_ceiling_bounce_empty_basement")
    for detector_id in (*fades, "beach_ball"):
        assert allowed.for_detector(detector_id) == ("Positive_Gamma", "Whipsaw")
    assert allowed.whipsaw_fade == ("Whipsaw",)
    assert allowed.rug == allowed.reverse_rug == ("Positive_Gamma", "Negative_Gamma")
    assert allowed.trend_follow == ("Negative_Gamma", "Vanna_Dominant")
    assert not any("Structureless" in allowed.for_detector(d) for d in DETECTOR_IDS)
    assert (cfg.entry_cutoff.cutoff, cfg.vix_gap.until) == (time(15, 25), time(12, 0))
    assert (cfg.late_session_chase.start, cfg.late_session_chase.end) == (time(15, 30), time(16))


def test_literals_match_the_engine_and_detector_ids() -> None:
    assert tuple(get_args(GateId.__value__)) == GATE_IDS
    assert set(get_args(RegimeName.__value__)) == REGIMES
    assert set(get_args(MapGradeName.__value__)) == MAP_GRADES
    assert tuple(get_args(GateDetectorId.__value__)) == DETECTOR_IDS


def test_custom_order_and_yaml_round_trip() -> None:
    order = list(reversed(GATE_IDS))
    cfg = GatesConfig.model_validate(
        {"order": order, "news_window": {"event_types": ["CPI", "PPI"]}, "midpoint": {"lo": 0.4}}
    )
    assert list(cfg.order) == order
    assert cfg.news_window.event_types == ("CPI", "PPI")
    dumped = yaml.safe_load(yaml.safe_dump(cfg.model_dump(mode="json")))
    assert GatesConfig.model_validate(dumped) == cfg


@pytest.mark.parametrize(
    ("value", "loc", "kind"),
    [
        ({"order": ["stale_map"]}, ("order",), "missing"),
        ({"order": [*GATE_IDS, "stale_map"]}, ("order",), "duplicate_id"),
        ({"order": [*GATE_IDS[:-1], "vwap"]}, ("order", 26), "literal_error"),
        ({"midpoint": {"lo": 0.7}}, ("midpoint", "hi"), "greater_than"),
        ({"min_reward_risk": {"min": 1.5}}, ("min_reward_risk", "alert_min"), "less_than_equal"),
        ({"late_session_chase": {"end": "15:00"}}, ("late_session_chase", "end"), "greater_than"),
        ({"entry_cutoff": {"cutoff": 1525}}, ("entry_cutoff", "cutoff"), "time_type"),
        (
            {"regime_match": {"allowed": {"rug": ["Whipsaw", "Whipsaw"]}}},
            ("regime_match", "allowed", "rug"),
            "duplicate_id",
        ),
        (
            {"regime_match": {"allowed": {"vwap_fade": []}}},
            ("regime_match", "allowed", "vwap_fade"),
            "extra_forbidden",
        ),
        ({"fade_detectors": ["rug", "rug"]}, ("fade_detectors",), "duplicate_id"),
        (
            {"stale_map": {"max_snapshot_age_s": 0}},
            ("stale_map", "max_snapshot_age_s"),
            "greater_than_equal",
        ),
        (
            {"trinity_agreement": {"min_agree": 4}},
            ("trinity_agreement", "min_agree"),
            "less_than_equal",
        ),
        (
            {"news_window": {"event_types": ["cpi"]}},
            ("news_window", "event_types", 0),
            "string_pattern_mismatch",
        ),
        ({"tap_count": {"enabled": "yes"}}, ("tap_count", "enabled"), "bool_type"),
        ({"vwap_gate": {}}, ("vwap_gate",), "extra_forbidden"),
    ],
)
def test_bad_values_are_rejected(value: dict[str, Any], loc: tuple[Any, ...], kind: str) -> None:
    assert (loc, kind) in error_types(value)


def test_get_rejects_an_unknown_gate_id() -> None:
    with pytest.raises(ValueError, match="vwap"):
        GatesConfig().get("vwap")
    with pytest.raises(ValueError, match="vwap"):
        GatesConfig().regime_match.allowed.for_detector("vwap")
