"""Target planning: target prices per Exit_Mode (design §12 "Targets", Req 12).

:func:`plan_targets` is the one target rule. The detectors call it to fill
``CandidateSetup.targets``, the min_reward_risk Gate to measure reward, and
the Order_Planner to place TP orders, so the three always agree (Req 10.13,
11.5).

**Which Exit_Mode** (Req 12.1-12.2). :func:`exit_mode_for` picks the
per-Regime setting for the setup's Regime, else ``exits.global``; a setup with
no Regime (``MissingInput``) gets ``global``. The setting also names the stop
rule, which the detectors apply.

**Targets** come from the source Node's Snapshot at the Candidate_Setup's
Decision_Time (Req 12.3), held in a :class:`TargetContext`: its Nodes with
converted tick levels. "Beyond" a price means strictly above it for a long
and strictly below it for a short; Node searches skip the source Node.

- ``fixed_r`` (Req 12.4): ``entry ± r x |entry - stop|``.
- ``next_node`` (Req 12.5): the level of the nearest Node beyond entry.
- ``tp1_partial_be`` (Req 12.6-12.7): TP1 by its rule (``r`` as Fixed_R with
  TP1's multiple, ``node`` as Next_Node); TP2 by its rule (``r`` with TP2's
  multiple, ``node`` the nearest Node beyond TP1). TP1 takes
  ``max(1, floor(tp1_fraction x qty))`` contracts and TP2 the rest, so a
  1-contract position exits in full at TP1 (:meth:`Targets.quantities`).
- ``opposition_or_fixed_r`` (Req 12.9): the nearer to entry of the R target
  and the nearest opposition Node (a Node beyond entry with ``|value| >=
  fraction x |source value|``); the R target when there is no opposition Node.
- ``trailing``: :class:`~fse.engine.types.NoTarget`.

**Rounding** (Req 12.3). An R target is ``r x risk`` ticks from entry, rounded
toward entry to a whole tick and kept at least 1 tick beyond entry. ``r`` is
taken as the decimal written in the Strategy_Config (``Fraction(repr(r))``), so
2.3 x 10 ticks is exactly 23 ticks, not 22. Node levels are already ticks
(``fse.engine.levels``) and lie beyond entry, so they need no rounding. The
opposition threshold compares exact decimals the same way (``fraction`` and
both Node values by their ``repr``): a Node of 1.75e8 is exactly 7% of a 2.5e9
source and is opposition, though the float product is 175000000.00000003.

**Failures** (Req 12.12) are a :class:`TargetRejection`: ``no_target_node``
when a Node-based target (Next_Node, or a ``node`` TP1 or TP2) finds no Node,
and ``tp2_not_beyond_tp1`` when TP2 is not farther from entry than TP1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Final, Literal

from fse.config.schema.exits import (
    EXIT_MODES,
    ExitModeName,
    ExitModesConfig,
    ExitsConfig,
    ExitStopRule,
)
from fse.engine.levels import ConvertedMap
from fse.engine.nodes import NodeLabels
from fse.engine.types import (
    DIRECTIONS,
    STOP_RULES,
    CandidateSetup,
    Direction,
    MissingInput,
    NoTarget,
    Regime,
    Snapshot,
    Ticks,
    direction_sign,
)

__all__ = [
    "TARGET_REJECTION_REASONS",
    "ExitModeCfg",
    "NodeLevel",
    "TargetBasis",
    "TargetContext",
    "TargetRejection",
    "TargetRejectionReason",
    "Targets",
    "exit_mode_for",
    "plan_targets",
    "r_target",
    "tp_quantities",
]

type TargetRejectionReason = Literal["no_target_node", "tp2_not_beyond_tp1"]
TARGET_REJECTION_REASONS: Final[frozenset[str]] = frozenset(
    {"no_target_node", "tp2_not_beyond_tp1"}
)

_TARGET_MODES: Final[frozenset[str]] = frozenset(EXIT_MODES) - {"trailing"}
"""The Exit_Modes that plan target prices."""


def _require_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")


def _require_finite(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


def _exact(x: float) -> Fraction:
    """The decimal value ``x`` was written as: ``2.3`` is 23/10, not its binary float."""
    return Fraction(repr(x))


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class ExitModeCfg:
    """The Exit_Mode and stop rule that apply to one Candidate_Setup, with every mode's parameters.

    Build it with :func:`exit_mode_for`. ``mode`` is the value detectors write
    to ``CandidateSetup.exit_mode``.
    """

    mode: ExitModeName
    stop_rule: ExitStopRule
    modes: ExitModesConfig

    def __post_init__(self) -> None:
        if self.mode not in EXIT_MODES:
            raise ValueError(f"ExitModeCfg.mode must be one of {EXIT_MODES}, got {self.mode!r}")
        if self.stop_rule not in STOP_RULES:
            raise ValueError(
                f"ExitModeCfg.stop_rule must be one of {sorted(STOP_RULES)}, got {self.stop_rule!r}"
            )


def exit_mode_for(cfg: ExitsConfig, regime: Regime | MissingInput) -> ExitModeCfg:
    """The per-Regime setting for ``regime`` when configured, else ``global`` (Req 12.2).

    A missing Regime (``MissingInput``) gets ``global``.
    """
    setting = cfg.setting_for(None if isinstance(regime, MissingInput) else regime)
    return ExitModeCfg(setting.mode, setting.stop_rule, cfg.modes)


@dataclass(frozen=True, slots=True)
class TargetBasis:
    """The prices a target plan starts from: direction, entry, stop and the source Node.

    The stop is on the risk side of entry: below it for a long, above it for a
    short. :meth:`of` reads a priced Candidate_Setup; a detector builds one
    directly before the setup exists.
    """

    direction: Direction
    entry: Ticks
    stop: Ticks
    source_strike: float
    source_value: float

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ValueError(f"TargetBasis.direction must be long or short, got {self.direction!r}")
        _require_int("TargetBasis.entry", self.entry)
        _require_int("TargetBasis.stop", self.stop)
        _require_finite("TargetBasis.source_strike", self.source_strike)
        _require_finite("TargetBasis.source_value", self.source_value)
        sign = direction_sign(self.direction)
        if not sign * self.stop < sign * self.entry:
            side = "below" if self.direction == "long" else "above"
            raise ValueError(
                f"a {self.direction} TargetBasis needs its stop {side} entry: "
                f"stop {self.stop}, entry {self.entry}"
            )

    @classmethod
    def of(cls, c: CandidateSetup) -> TargetBasis:
        """The basis of a priced Candidate_Setup; ``ValueError`` for an unpriced one."""
        if c.entry is None or c.stop is None:
            raise ValueError("an unpriced CandidateSetup has no entry or stop to plan from")
        return cls(c.key.direction, c.entry, c.stop, c.source.strike, c.source.value)

    @property
    def sign(self) -> int:
        """``+1`` for a long, ``-1`` for a short."""
        return direction_sign(self.direction)

    @property
    def risk(self) -> int:
        """The entry-to-stop distance in ticks (1R), at least 1."""
        return abs(self.entry - self.stop)


@dataclass(frozen=True, slots=True)
class NodeLevel:
    """One Node of the source Node's Snapshot: strike, signed value and converted level."""

    strike: float
    value: float
    level: Ticks

    def __post_init__(self) -> None:
        _require_finite("NodeLevel.strike", self.strike)
        _require_finite("NodeLevel.value", self.value)
        _require_int("NodeLevel.level", self.level)


@dataclass(frozen=True, slots=True)
class TargetContext:
    """The Nodes of the source Node's Snapshot at the Decision_Time, with distinct strikes.

    :meth:`from_snapshot` builds it from the Node_Classifier labels and the
    Level_Converter's map. Any order is accepted; searches scan every Node.
    """

    nodes: tuple[NodeLevel, ...]

    def __post_init__(self) -> None:
        strikes = [n.strike for n in self.nodes]
        if len(set(strikes)) != len(strikes):
            raise ValueError("TargetContext.nodes must have distinct strikes")

    @classmethod
    def from_snapshot(
        cls, snapshot: Snapshot, labels: NodeLabels, converted: ConvertedMap
    ) -> TargetContext:
        """``labels.nodes`` with their values in ``snapshot`` and levels in ``converted``.

        ``labels`` must be ``snapshot``'s labels and ``converted`` its map;
        ``ValueError`` when the map is of another Snapshot or a Node strike is
        not in ``snapshot``.
        """
        if (converted.symbol, converted.metric, converted.as_of_ns) != (
            snapshot.symbol,
            snapshot.metric,
            snapshot.as_of_ns,
        ):
            raise ValueError(
                f"the converted map of {converted.symbol} {converted.metric} at "
                f"{converted.as_of_ns} is not of the {snapshot.symbol} {snapshot.metric} "
                f"Snapshot at {snapshot.as_of_ns}"
            )
        values: dict[float, float] = {}
        for strike, value in zip(snapshot.strikes, snapshot.values, strict=True):
            values.setdefault(strike, value)
        nodes: list[NodeLevel] = []
        for strike in labels.nodes:
            if strike not in values:
                raise ValueError(f"Node strike {strike} is not in the {snapshot.symbol} Snapshot")
            nodes.append(NodeLevel(strike, values[strike], converted.level_of(strike)))
        return cls(tuple(nodes))


# ---------------------------------------------------------------- results


def _require_qty(qty: object) -> None:
    _require_int("qty", qty)
    if isinstance(qty, int) and qty < 1:
        raise ValueError(f"qty must be at least 1, got {qty}")


def _require_tp1_fraction(name: str, value: object) -> None:
    _require_finite(name, value)
    if isinstance(value, int | float) and not 0 < value < 1:
        raise ValueError(f"{name} must be above 0 and below 1, got {value}")


def tp_quantities(qty: int, tp1_fraction: float) -> tuple[int, int]:
    """``(TP1, TP2)`` contracts: ``max(1, floor(tp1_fraction x qty))`` and the rest (Req 12.7).

    ``tp1_fraction`` is taken as the decimal written in the Strategy_Config. A
    1-contract position gives ``(1, 0)``: it exits in full at TP1.
    """
    _require_qty(qty)
    _require_tp1_fraction("tp1_fraction", tp1_fraction)
    tp1 = max(1, math.floor(_exact(tp1_fraction) * qty))
    return tp1, qty - tp1


@dataclass(frozen=True, slots=True)
class Targets:
    """The planned target prices, in plan order.

    ``tp2`` and ``tp1_fraction`` are set together, only by ``tp1_partial_be``;
    every other mode plans the single target ``tp1``. ``prices`` is the value
    for ``CandidateSetup.targets``.
    """

    mode: ExitModeName
    tp1: Ticks
    tp2: Ticks | None = None
    tp1_fraction: float | None = None

    def __post_init__(self) -> None:
        if self.mode not in _TARGET_MODES:
            raise ValueError(
                f"Targets.mode must be one of {sorted(_TARGET_MODES)}, got {self.mode!r}"
            )
        _require_int("Targets.tp1", self.tp1)
        two = self.mode == "tp1_partial_be"
        if two != (self.tp2 is not None) or two != (self.tp1_fraction is not None):
            raise ValueError("Targets.tp2 and tp1_fraction are set exactly for tp1_partial_be")
        if self.tp2 is not None:
            _require_int("Targets.tp2", self.tp2)
            if self.tp2 == self.tp1:
                raise ValueError("Targets.tp2 must differ from tp1")
        if self.tp1_fraction is not None:
            _require_tp1_fraction("Targets.tp1_fraction", self.tp1_fraction)

    @property
    def prices(self) -> tuple[Ticks, ...]:
        """``(tp1,)`` or ``(tp1, tp2)``."""
        return (self.tp1,) if self.tp2 is None else (self.tp1, self.tp2)

    def quantities(self, qty: int) -> tuple[int, int]:
        """``(TP1, TP2)`` contracts for a ``qty``-contract position; TP2 is 0 for one target."""
        if self.tp1_fraction is None:
            _require_qty(qty)
            return qty, 0
        return tp_quantities(qty, self.tp1_fraction)


@dataclass(frozen=True, slots=True)
class TargetRejection:
    """No order for the Candidate_Setup, with the Rejection_Reason (Req 12.12)."""

    reason: TargetRejectionReason

    def __post_init__(self) -> None:
        if self.reason not in TARGET_REJECTION_REASONS:
            raise ValueError(
                f"TargetRejection.reason must be one of {sorted(TARGET_REJECTION_REASONS)}, "
                f"got {self.reason!r}"
            )


_NO_TARGET_NODE: Final = TargetRejection("no_target_node")
_TP2_NOT_BEYOND_TP1: Final = TargetRejection("tp2_not_beyond_tp1")


# ---------------------------------------------------------------- planning


def r_target(basis: TargetBasis, r_multiple: float) -> Ticks:
    """``r_multiple`` R beyond entry, rounded toward entry, >= 1 tick beyond (Req 12.3-12.4)."""
    _require_finite("r_multiple", r_multiple)
    if r_multiple <= 0:
        raise ValueError(f"r_multiple must be above 0, got {r_multiple}")
    offset = max(1, math.floor(_exact(r_multiple) * basis.risk))
    return basis.entry + basis.sign * offset


def _nearest_beyond(
    ctx: TargetContext, basis: TargetBasis, origin: Ticks, min_abs: Fraction | None = None
) -> Ticks | None:
    """The nearest Node level beyond ``origin`` in the trade direction, skipping the source.

    With ``min_abs``, only Nodes with ``|value| >= min_abs`` count, ``|value|``
    taken as its exact decimal.
    """
    sign = basis.sign
    best: Ticks | None = None
    for node in ctx.nodes:
        if node.strike == basis.source_strike or sign * (node.level - origin) <= 0:
            continue
        if min_abs is not None and not _exact(abs(node.value)) >= min_abs:
            continue
        if best is None or sign * node.level < sign * best:
            best = node.level
    return best


def _tp1_partial_be(
    basis: TargetBasis, ctx: TargetContext, modes: ExitModesConfig
) -> Targets | TargetRejection:
    cfg = modes.tp1_partial_be
    if cfg.tp1.rule == "r":
        tp1: Ticks | None = r_target(basis, cfg.tp1.r_multiple)
    else:
        tp1 = _nearest_beyond(ctx, basis, basis.entry)
    if tp1 is None:
        return _NO_TARGET_NODE
    if cfg.tp2.rule == "r":
        tp2: Ticks | None = r_target(basis, cfg.tp2.r_multiple)
    else:
        tp2 = _nearest_beyond(ctx, basis, tp1)
    if tp2 is None:
        return _NO_TARGET_NODE
    if basis.sign * (tp2 - basis.entry) <= basis.sign * (tp1 - basis.entry):
        return _TP2_NOT_BEYOND_TP1
    return Targets("tp1_partial_be", tp1, tp2, cfg.tp1_fraction)


def plan_targets(
    c: CandidateSetup | TargetBasis, mode: ExitModeCfg, ctx: TargetContext
) -> Targets | NoTarget | TargetRejection:
    """The targets of ``c`` under ``mode`` from the source Snapshot's Nodes in ``ctx``.

    ``c`` is a priced Candidate_Setup or a :class:`TargetBasis` (a detector
    plans before the setup exists). Returns :class:`Targets`, ``NoTarget`` for
    Trailing, or a :class:`TargetRejection` (Req 12.12).
    """
    basis = c if isinstance(c, TargetBasis) else TargetBasis.of(c)
    modes = mode.modes
    match mode.mode:
        case "fixed_r":
            return Targets("fixed_r", r_target(basis, modes.fixed_r.r_multiple))
        case "next_node":
            level = _nearest_beyond(ctx, basis, basis.entry)
            return _NO_TARGET_NODE if level is None else Targets("next_node", level)
        case "tp1_partial_be":
            return _tp1_partial_be(basis, ctx, modes)
        case "opposition_or_fixed_r":
            cfg = modes.opposition_or_fixed_r
            fixed = r_target(basis, cfg.r_multiple)
            threshold = _exact(cfg.fraction) * _exact(abs(basis.source_value))
            opposition = _nearest_beyond(ctx, basis, basis.entry, min_abs=threshold)
            nearer = (
                fixed
                if opposition is None or basis.sign * opposition > basis.sign * fixed
                else opposition
            )
            return Targets("opposition_or_fixed_r", nearer)
        case "trailing":
            return NoTarget()
