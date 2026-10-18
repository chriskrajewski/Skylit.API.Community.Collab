"""Contradiction warnings of a valid Strategy_Config (design §17 step 4, Req 17.6).

When the min_reward_risk Gate is enabled, an enabled Fixed_R or
Opposition_Or_Fixed_R Exit_Mode whose ``r_multiple`` is below the Gate's
``min`` sets targets that the Gate then fails. The config still loads; the
Config_Loader returns one :class:`ConfigWarning` per such R multiple, naming
both key paths and both values.

The schema has one R multiple per Exit_Mode (``exits.modes.<mode>.r_multiple``);
a per-Regime exit setting selects a mode and a stop rule, not its own R
multiple. So there is at most one warning per mode, and its message lists
where the mode is selected (``exits.global`` or ``exits.per_regime.<Regime>``).
As ``fse.config.schema.exits`` documents, the enabled flag, not the selection,
decides whether a mode is checked.

Nothing here does I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from fse.config.schema import StrategyConfig
from fse.config.schema.exits import (
    EXIT_REGIMES,
    ExitsConfig,
    FixedRConfig,
    OppositionOrFixedRConfig,
)

__all__ = [
    "MIN_RR_PATH",
    "R_MULTIPLE_MODES",
    "ConfigWarning",
    "contradiction_warnings",
]

MIN_RR_PATH: Final = "gates.min_reward_risk.min"

R_MULTIPLE_MODES: Final[tuple[str, ...]] = ("fixed_r", "opposition_or_fixed_r")
"""The Exit_Modes whose R multiple Req 17.6 checks, in schema order."""

_LABELS: Final = {"fixed_r": "Fixed_R", "opposition_or_fixed_r": "Opposition_Or_Fixed_R"}


@dataclass(frozen=True, slots=True)
class ConfigWarning:
    """One contradiction: two key paths whose values cannot both hold for a setup."""

    kind: Literal["contradiction"]
    key_paths: tuple[str, str]
    values: tuple[float, float]
    message: str

    def __str__(self) -> str:
        return f"warning: {self.kind}: {self.message}"


def contradiction_warnings(cfg: StrategyConfig) -> tuple[ConfigWarning, ...]:
    """One warning per enabled R-multiple Exit_Mode below an enabled min_reward_risk ``min``."""
    gate = cfg.gates.min_reward_risk
    if not gate.enabled:
        return ()
    modes: tuple[tuple[str, FixedRConfig | OppositionOrFixedRConfig], ...] = (
        ("fixed_r", cfg.exits.modes.fixed_r),
        ("opposition_or_fixed_r", cfg.exits.modes.opposition_or_fixed_r),
    )
    found: list[ConfigWarning] = []
    for mode, settings in modes:
        if not settings.enabled or not settings.r_multiple < gate.min:
            continue
        path = f"exits.modes.{mode}.r_multiple"
        found.append(
            ConfigWarning(
                kind="contradiction",
                key_paths=(path, MIN_RR_PATH),
                values=(settings.r_multiple, gate.min),
                message=(
                    f"{path} = {settings.r_multiple!r} is below {MIN_RR_PATH} = {gate.min!r}: "
                    f"a {_LABELS[mode]} target at {settings.r_multiple!r}R fails the "
                    f"min_reward_risk Gate; {_selection(cfg.exits, mode)}"
                ),
            )
        )
    return tuple(found)


def _selection(exits: ExitsConfig, mode: str) -> str:
    """Where ``mode`` is selected, as key paths, for the warning text."""
    paths = ["exits.global.mode"] if exits.global_.mode == mode else []
    for regime in EXIT_REGIMES:
        setting = exits.per_regime.get(regime)
        if setting is not None and setting.mode == mode:
            paths.append(f"exits.per_regime.{regime}.mode")
    if not paths:
        return "the mode is enabled but no exits.global or exits.per_regime setting selects it"
    return "selected by " + ", ".join(paths)
