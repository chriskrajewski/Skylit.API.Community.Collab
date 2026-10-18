"""Rule presets of the runner YAML's paper accounts (``accounts[].preset``).

A preset is a partial ``PaperAccountRules`` mapping. The account's own
``rules:`` is deep-merged over it at leaf level before validation, so
``rules: {maximum_loss_limit: {value: "2500.00"}}`` keeps the preset's
``enabled`` and ``mode``.

- ``topstep_50k_combine``: the empty override. ``PaperAccountRules()``
  defaults are ``AccountConfig()`` defaults, i.e. the Topstep 50K Combine.
- ``none``: every rule off, $50,000.00 starting balance (overridable). The
  inherited "MLL below the starting balance" check runs even when the MLL is
  disabled, so the disabled MLL's value is $0.01; a user who re-enables the
  MLL on a ``none`` account must set its value too.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

__all__ = ["PRESETS", "PresetName"]

type PresetName = Literal["topstep_50k_combine", "none"]

PRESETS: Final[Mapping[str, Mapping[str, object]]] = MappingProxyType(
    {
        "topstep_50k_combine": MappingProxyType({}),
        "none": MappingProxyType(
            {
                "profit_target": {"enabled": False},
                "maximum_loss_limit": {"enabled": False, "value": "0.01"},
                "daily_loss_limit": {"enabled": False},
                "consistency_target": {"enabled": False},
                "position_cap": {"enabled": False},
                "flat_deadline_enabled": False,
            }
        ),
    }
)
