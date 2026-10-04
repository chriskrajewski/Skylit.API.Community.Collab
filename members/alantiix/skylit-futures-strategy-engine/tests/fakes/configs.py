"""Minimal valid Strategy_Config inputs for tests (task 20.1).

Only the keys with no schema default are set: ``regime.min_abs_value`` and
the commission and exchange fee of the two default traded instruments. The
fee values are test numbers, not Topstep's.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MINIMAL_CONFIG_YAML", "minimal_config_data"]

MINIMAL_CONFIG_YAML = """\
regime:
  min_abs_value: 1500.0
fills:
  costs:
    MES: {commission: "0.37", exchange_fee: "0.35"}
    MNQ: {commission: "0.37", exchange_fee: "0.35"}
"""


def minimal_config_data() -> dict[str, Any]:
    """:data:`MINIMAL_CONFIG_YAML` as the plain mapping a YAML safe loader returns."""
    return {
        "regime": {"min_abs_value": 1500.0},
        "fills": {
            "costs": {
                "MES": {"commission": "0.37", "exchange_fee": "0.35"},
                "MNQ": {"commission": "0.37", "exchange_fee": "0.35"},
            }
        },
    }
