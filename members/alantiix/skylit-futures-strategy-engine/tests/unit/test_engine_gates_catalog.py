"""Smoke test for the Gate catalog (design §11, task 15.4).

The registry, :data:`GATE_IDS` and the ``gates`` Config_Schema section list
the 27 Gate ids of Req 11.1 in its order, every id resolves to its Gate, and
every entry has an ``enabled`` bool and defaults that validate.

**Validates: Requirements 11.1**
"""

from __future__ import annotations

import pytest

from fse.config.schema.gates import GATE_IDS, GatesConfig
from fse.engine.gates.registry import GATES, REGISTRY, gate

# Copied from the Req 11.1 text, not from the code.
REQ_11_1_IDS = (
    "stale_map",
    "map_grade",
    "midpoint",
    "deflection_band",
    "chart_confluence",
    "stdev_fib_zone",
    "dark_pool_confluence",
    "trinity_agreement",
    "candle_color",
    "tap_count",
    "third_gatekeeper_test",
    "weekly_node_tests",
    "sloppy_seconds",
    "air_pocket_fade",
    "min_reward_risk",
    "opposition_inside_target",
    "fomo_travel",
    "open_shuffle",
    "entry_cutoff",
    "late_session_chase",
    "news_window",
    "vix_gap",
    "regime_match",
    "dormant_node",
    "gatekeepers_on_path",
    "node_growth_divergence",
    "kill_switch_lockout",
)


def test_catalog_lists_the_27_req_11_1_gates_in_order() -> None:
    assert len(REQ_11_1_IDS) == len(set(REQ_11_1_IDS)) == 27
    assert len(REGISTRY) == 27
    assert tuple(g.id for g in REGISTRY) == REQ_11_1_IDS
    assert GATE_IDS == REQ_11_1_IDS == tuple(GATES)
    assert GatesConfig().order == REQ_11_1_IDS


@pytest.mark.parametrize("gate_id", REQ_11_1_IDS)
def test_each_gate_resolves_and_has_an_enabled_flag(gate_id: str) -> None:
    found = gate(gate_id)
    assert found is GATES[gate_id]
    assert found.id == gate_id
    assert isinstance(GatesConfig().get(gate_id).model_dump()["enabled"], bool)


def test_defaults_validate() -> None:
    defaults = GatesConfig()
    assert GatesConfig.model_validate({}) == defaults
    assert GatesConfig.model_validate(defaults.model_dump(mode="json")) == defaults
