"""Unit tests for the ``nodes`` Config_Schema section.

Design "Strategy_Config shape", D6 and §17 step 3: strict types, unknown keys
rejected, frozen, defaults and ranges from the design sketch and Req 6. Inputs
are plain dicts, as a YAML safe loader produces them.

**Validates: Requirements 6.1, 6.8, 6.9, 6.10, 6.11**
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.nodes import ClusteringConfig, NodesConfig, SloppySecondsConfig

DESIGN_NODES_YAML = """
{node_fraction: 0.20, gatekeeper_fraction: 0.30, air_pocket_min_width_pct: 0.5,
 lookout_pct: 1.0, decay_fraction: 0.20, dormant_distance_pct: 3.5,
 sloppy_seconds: {window_min: 15, fraction: 0.10},
 clustering: {enabled: false, es_width_pts: 5.0}}
"""


def errors(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        NodesConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    cfg = NodesConfig()
    assert cfg == NodesConfig.model_validate(yaml.safe_load(DESIGN_NODES_YAML))
    assert (cfg.node_fraction, cfg.gatekeeper_fraction) == (0.20, 0.30)
    assert (cfg.air_pocket_min_width_pct, cfg.lookout_pct) == (0.5, 1.0)
    assert (cfg.decay_fraction, cfg.dormant_distance_pct) == (0.20, 3.5)
    assert cfg.sloppy_seconds == SloppySecondsConfig(window_min=15, fraction=0.10)
    assert cfg.clustering == ClusteringConfig(enabled=False, es_width_pts=5.0)


def test_dump_is_plain_yaml_and_round_trips() -> None:
    cfg = NodesConfig.model_validate(
        {
            "node_fraction": 1,
            "gatekeeper_fraction": 0.99,
            "lookout_pct": 0.1,
            "sloppy_seconds": {"window_min": 390, "fraction": 0.01},
            "clustering": {"enabled": True, "es_width_pts": 2.5},
        }
    )
    text = yaml.safe_dump(cfg.model_dump(mode="python"), sort_keys=False)
    assert NodesConfig.model_validate(yaml.safe_load(text)) == cfg
    assert list(cfg.model_dump()) == [
        "node_fraction",
        "gatekeeper_fraction",
        "air_pocket_min_width_pct",
        "lookout_pct",
        "decay_fraction",
        "dormant_distance_pct",
        "sloppy_seconds",
        "clustering",
    ]


def test_model_is_frozen() -> None:
    cfg = NodesConfig()
    with pytest.raises(ValidationError):
        cfg.node_fraction = 0.5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"node_fraction": 0.009}, [(("node_fraction",), "greater_than_equal")]),
        ({"node_fraction": 1.01}, [(("node_fraction",), "less_than_equal")]),
        ({"node_fraction": "0.2"}, [(("node_fraction",), "float_type")]),
        ({"node_fraction": True}, [(("node_fraction",), "float_type")]),
        ({"gatekeeper_fraction": 1.0}, [(("gatekeeper_fraction",), "less_than_equal")]),
        ({"air_pocket_min_width_pct": 0}, [(("air_pocket_min_width_pct",), "greater_than")]),
        ({"lookout_pct": 100.5}, [(("lookout_pct",), "less_than_equal")]),
        ({"lookout_pct": float("nan")}, [(("lookout_pct",), "less_than_equal")]),
        ({"decay_fraction": 0.0}, [(("decay_fraction",), "greater_than_equal")]),
        ({"dormant_distance_pct": -1.0}, [(("dormant_distance_pct",), "greater_than")]),
        ({"dormant_pct": 3.5}, [(("dormant_pct",), "extra_forbidden")]),
        (
            {"sloppy_seconds": {"window_min": 391}},
            [(("sloppy_seconds", "window_min"), "less_than_equal")],
        ),
        (
            {"sloppy_seconds": {"window_min": 15.0}},
            [(("sloppy_seconds", "window_min"), "int_type")],
        ),
        (
            {"sloppy_seconds": {"fraction": 0.0}},
            [(("sloppy_seconds", "fraction"), "greater_than_equal")],
        ),
        (
            {"clustering": {"es_width_pts": 0.0}},
            [(("clustering", "es_width_pts"), "greater_than")],
        ),
        (
            {"clustering": {"es_width_pts": float("inf")}},
            [(("clustering", "es_width_pts"), "finite_number")],
        ),
        ({"clustering": {"enabled": "yes"}}, [(("clustering", "enabled"), "bool_type")]),
        (
            {"clustering": {"nq_width_pts": 5.0}},
            [(("clustering", "nq_width_pts"), "extra_forbidden")],
        ),
    ],
)
def test_invalid_values_name_the_key_path(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert errors(value) == expected


def test_every_error_is_reported_together() -> None:
    found = errors({"node_fraction": 0.0, "lookout_pct": 0.0, "typo": 1})
    assert found == [
        (("node_fraction",), "greater_than_equal"),
        (("lookout_pct",), "greater_than"),
        (("typo",), "extra_forbidden"),
    ]
