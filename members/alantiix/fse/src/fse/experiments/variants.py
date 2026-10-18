"""Strategy_Config variants for experiments (design §19).

:func:`disable` returns the base config with one Gate's ``enabled`` flag set
to false and every other key, default or not, left as it was. The copy goes
through pydantic's ``model_copy`` on the Gate's entry and the ``gates``
section, so nothing else is re-parsed or re-defaulted.
"""

from __future__ import annotations

from fse.config.schema import StrategyConfig

__all__ = ["VariantError", "disable"]


class VariantError(ValueError):
    """A variant that cannot be built from its base."""


def disable(base: StrategyConfig, gate_id: str) -> StrategyConfig:
    """``base`` with Gate ``gate_id`` disabled; ``VariantError`` if it is not enabled."""
    try:
        entry = base.gates.get(gate_id)
    except ValueError as exc:
        raise VariantError(str(exc)) from None
    if not entry.enabled:
        raise VariantError(f"Gate {gate_id} is not enabled in the base Strategy_Config")
    gates = base.gates.model_copy(update={gate_id: entry.model_copy(update={"enabled": False})})
    return base.model_copy(update={"gates": gates})
