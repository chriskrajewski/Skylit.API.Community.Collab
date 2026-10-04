"""The ``exits`` section of the Strategy_Config (design §12, "Strategy_Config shape").

Exit_Modes, per-Regime overrides and the breakeven rule (Req 12.1-12.14),
read by ``fse.engine.targets`` and the Order_Planner:

- ``global``: the Exit_Mode and stop rule of every Candidate_Setup whose
  Regime has no per-Regime setting (default ``opposition_or_fixed_r`` and
  ``one_node_beyond``, Req 12.1-12.2).
- ``per_regime``: an optional setting per Regime. A configured setting names
  both keys, ``mode`` and ``stop_rule``; ``null`` means "use ``global``"
  (Req 12.2). The default sets Positive_Gamma to ``next_node`` with
  ``one_node_beyond``, as in the design sketch.
- ``modes``: an enabled flag and the parameters of each Exit_Mode:

  - ``fixed_r``: ``r_multiple`` (default 3.0, Req 12.4);
  - ``next_node``: no parameters (Req 12.5);
  - ``tp1_partial_be``: ``tp1`` and ``tp2`` rules, each ``r`` (with its own
    ``r_multiple``) or ``node``, defaults ``r`` at 1.5 and ``node``; and
    ``tp1_fraction``, the share of contracts exited at TP1 (default 0.5,
    range 0.1 to 0.9) (Req 12.6-12.7);
  - ``opposition_or_fixed_r``: ``r_multiple`` (default 3.0) and ``fraction``,
    the share of the source Node's absolute value an opposition Node needs
    (default 0.85, range 0.01 to 1) (Req 12.9);
  - ``trailing``: ``mode`` ``node_to_node`` or ``fixed_ticks`` (default
    ``node_to_node``) and ``ticks``, the fixed-ticks distance (default 40,
    range 1 to 400) (Req 12.10-12.11).

  Every ``r_multiple`` is in 0.5 to 10.0. Selecting a mode in ``global`` or
  ``per_regime`` does not depend on its enabled flag: the flag is the rule's
  on/off switch for contradiction warnings and the Revised_Drafts (Req 17.6,
  26.10).
- ``breakeven``: on/off; ``trigger_r``, the favorable move in R that moves
  the stop to the Breakeven_Price (default 1.0, range 0.25 to 10); and
  ``offset_ticks``, the Breakeven_Price offset from the entry fill (default 1,
  range 0 to 20) (Req 12.13, glossary "Breakeven_Price").

Every float rejects NaN and infinity.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import ConfigDict, Field

from fse.config.schema._base import SchemaModel
from fse.config.schema.nodes import FRACTION_MIN

__all__ = [
    "BREAKEVEN_OFFSET_TICKS_MAX",
    "BREAKEVEN_TRIGGER_R_MAX",
    "BREAKEVEN_TRIGGER_R_MIN",
    "EXIT_MODES",
    "EXIT_REGIMES",
    "EXIT_STOP_RULES",
    "R_MULTIPLE_MAX",
    "R_MULTIPLE_MIN",
    "TARGET_RULES",
    "TP1_FRACTION_MAX",
    "TP1_FRACTION_MIN",
    "TRAILING_MODES",
    "TRAILING_TICKS_MAX",
    "BreakevenConfig",
    "ExitModeName",
    "ExitModesConfig",
    "ExitSetting",
    "ExitStopRule",
    "ExitsConfig",
    "FixedRConfig",
    "GlobalExitSetting",
    "NextNodeConfig",
    "OppositionOrFixedRConfig",
    "PerRegimeExits",
    "RMultiple",
    "TargetRule",
    "Tp1PartialBeConfig",
    "Tp1RuleConfig",
    "Tp2RuleConfig",
    "TrailingConfig",
    "TrailingMode",
]

type ExitModeName = Literal[
    "fixed_r", "next_node", "tp1_partial_be", "opposition_or_fixed_r", "trailing"
]
type ExitStopRule = Literal["one_node_beyond", "fixed_ticks"]
"""The same values as ``fse.engine.types.StopRule``; a test keeps them equal."""
type TargetRule = Literal["r", "node"]
type TrailingMode = Literal["node_to_node", "fixed_ticks"]

EXIT_MODES: Final[tuple[str, ...]] = (
    "fixed_r",
    "next_node",
    "tp1_partial_be",
    "opposition_or_fixed_r",
    "trailing",
)
EXIT_STOP_RULES: Final[frozenset[str]] = frozenset({"one_node_beyond", "fixed_ticks"})
TARGET_RULES: Final[frozenset[str]] = frozenset({"r", "node"})
TRAILING_MODES: Final[frozenset[str]] = frozenset({"node_to_node", "fixed_ticks"})
EXIT_REGIMES: Final[tuple[str, ...]] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
"""The ``per_regime`` keys: the ``fse.engine.types.Regime`` values; a test keeps them equal."""

R_MULTIPLE_MIN: Final = 0.5
R_MULTIPLE_MAX: Final = 10.0
TP1_FRACTION_MIN: Final = 0.1
TP1_FRACTION_MAX: Final = 0.9
TRAILING_TICKS_MAX: Final = 400
BREAKEVEN_TRIGGER_R_MIN: Final = 0.25
BREAKEVEN_TRIGGER_R_MAX: Final = 10.0
BREAKEVEN_OFFSET_TICKS_MAX: Final = 20

type RMultiple = Annotated[float, Field(ge=R_MULTIPLE_MIN, le=R_MULTIPLE_MAX, allow_inf_nan=False)]
"""An R multiple of the entry-to-stop distance, 0.5 to 10.0 (Req 12.4, 12.9)."""


# ---------------------------------------------------------------- mode selection


class ExitSetting(SchemaModel):
    """An Exit_Mode and stop rule; a per-Regime setting names both (Req 12.2)."""

    mode: ExitModeName
    stop_rule: ExitStopRule


class GlobalExitSetting(ExitSetting):
    """The setting for any Regime without its own; both keys have defaults."""

    mode: ExitModeName = "opposition_or_fixed_r"
    stop_rule: ExitStopRule = "one_node_beyond"


class PerRegimeExits(SchemaModel):
    """One optional setting per Regime; ``None`` falls back to ``global``."""

    Positive_Gamma: ExitSetting | None = ExitSetting(mode="next_node", stop_rule="one_node_beyond")
    Negative_Gamma: ExitSetting | None = None
    Vanna_Dominant: ExitSetting | None = None
    Whipsaw: ExitSetting | None = None
    Structureless: ExitSetting | None = None

    def get(self, regime: str) -> ExitSetting | None:
        """The setting configured for ``regime``, or ``None``; ``ValueError`` for a non-Regime."""
        if regime not in EXIT_REGIMES:
            raise ValueError(f"{regime!r} is not one of the Regimes {EXIT_REGIMES}")
        setting: ExitSetting | None = getattr(self, regime)
        return setting


# ---------------------------------------------------------------- mode parameters


class FixedRConfig(SchemaModel):
    """Fixed_R: one target ``r_multiple`` times the entry-to-stop distance (Req 12.4)."""

    enabled: bool = True
    r_multiple: RMultiple = 3.0


class NextNodeConfig(SchemaModel):
    """Next_Node: the nearest other Node beyond entry (Req 12.5)."""

    enabled: bool = True


class Tp1RuleConfig(SchemaModel):
    """TP1 of TP1_Partial_BE: an R multiple or the Next_Node Node (Req 12.6)."""

    rule: TargetRule = "r"
    r_multiple: RMultiple = 1.5


class Tp2RuleConfig(SchemaModel):
    """TP2 of TP1_Partial_BE: an R multiple or the nearest Node beyond TP1 (Req 12.6)."""

    rule: TargetRule = "node"
    r_multiple: RMultiple = 3.0


class Tp1PartialBeConfig(SchemaModel):
    """TP1_Partial_BE: TP1 for ``tp1_fraction`` of the contracts, TP2 for the rest."""

    enabled: bool = False
    tp1: Tp1RuleConfig = Tp1RuleConfig()
    tp2: Tp2RuleConfig = Tp2RuleConfig()
    tp1_fraction: float = Field(0.5, ge=TP1_FRACTION_MIN, le=TP1_FRACTION_MAX, allow_inf_nan=False)


class OppositionOrFixedRConfig(SchemaModel):
    """Opposition_Or_Fixed_R: the nearer of the opposition Node and the R target (Req 12.9)."""

    enabled: bool = True
    r_multiple: RMultiple = 3.0
    fraction: float = Field(0.85, ge=FRACTION_MIN, le=1.0, allow_inf_nan=False)


class TrailingConfig(SchemaModel):
    """Trailing: no target; node-to-node or fixed-ticks stop updates (Req 12.10-12.11)."""

    enabled: bool = False
    mode: TrailingMode = "node_to_node"
    ticks: int = Field(40, ge=1, le=TRAILING_TICKS_MAX)


class ExitModesConfig(SchemaModel):
    """The enabled flag and parameters of each of the five Exit_Modes (Req 12.1)."""

    fixed_r: FixedRConfig = FixedRConfig()
    next_node: NextNodeConfig = NextNodeConfig()
    tp1_partial_be: Tp1PartialBeConfig = Tp1PartialBeConfig()
    opposition_or_fixed_r: OppositionOrFixedRConfig = OppositionOrFixedRConfig()
    trailing: TrailingConfig = TrailingConfig()

    def enabled(self, mode: str) -> bool:
        """The enabled flag of ``mode``; ``ValueError`` for a name that is not an Exit_Mode."""
        if mode not in EXIT_MODES:
            raise ValueError(f"{mode!r} is not one of the Exit_Modes {EXIT_MODES}")
        flag: bool = getattr(self, mode).enabled
        return flag


# ---------------------------------------------------------------- breakeven and section


class BreakevenConfig(SchemaModel):
    """Move the stop to the Breakeven_Price after a ``trigger_r`` favorable move (Req 12.13)."""

    enabled: bool = True
    trigger_r: float = Field(
        1.0, ge=BREAKEVEN_TRIGGER_R_MIN, le=BREAKEVEN_TRIGGER_R_MAX, allow_inf_nan=False
    )
    offset_ticks: int = Field(1, ge=0, le=BREAKEVEN_OFFSET_TICKS_MAX)


class ExitsConfig(SchemaModel):
    """Exit_Mode selection (global and per Regime), mode parameters and the breakeven rule.

    ``global`` is a Python keyword, so the field is ``global_``; the YAML key,
    the dump and :meth:`model_validate` input all use ``global``.
    """

    model_config = ConfigDict(serialize_by_alias=True)

    global_: GlobalExitSetting = Field(GlobalExitSetting(), alias="global")
    per_regime: PerRegimeExits = PerRegimeExits()
    modes: ExitModesConfig = ExitModesConfig()
    breakeven: BreakevenConfig = BreakevenConfig()

    def setting_for(self, regime: str | None) -> ExitSetting:
        """The per-Regime setting for ``regime`` when configured, else ``global`` (Req 12.2).

        ``None`` (no Regime attached) gives ``global``.
        """
        if regime is None:
            return self.global_
        configured = self.per_regime.get(regime)
        return self.global_ if configured is None else configured
