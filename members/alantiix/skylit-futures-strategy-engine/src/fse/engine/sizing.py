"""The Position_Sizer: contracts per Candidate_Setup (design §14, Req 14).

:func:`size` runs five pure steps in order, each taking the previous step's
contracts (Req 14.8). A disabled or untriggered step leaves contracts
unchanged; the run stops at the first step that yields 0 contracts.

1. ``base_contracts``: the configured count for the instrument
   (``fixed_contracts``, Req 14.1), or ``floor(risk_usd / (stop distance in
   points x point value))`` (``fixed_dollar_risk``, Req 14.2). Commissions,
   fees and slippage are excluded.
2. ``big_win_reduce``: if the previous session's filled trades (Shadow_Trades
   excluded) netted at least ``big_win.usd`` or summed to at least
   ``big_win.r`` R, contracts become ``min(q, reduced)`` (Req 14.5).
3. ``trinity_size_down``: if exactly 2 of the 3 Trinity symbols agree (the
   trinity_agreement measured value; 2 of the 3 NQ_Sources for an NQ-family
   setup), contracts become ``max(1, floor(q x fraction))`` (Req 14.6).
4. ``vix_gap_halve``: if the session's VIX gap ``(daily open - prior close) /
   prior close`` is at least ``vix_gap.pct`` percent, contracts become
   ``max(1, floor(q / 2))`` (Req 14.7).
5. ``micro_cap``: contracts are capped at ``floor((limit - used) /
   Micro_Equivalents per contract)``, where ``used`` counts the open positions
   and resting entry orders (Req 14.4).

A step that yields 0 gives ``size_zero`` naming the step (Req 14.3); only steps
1 and 5 can. An enabled step 3 or 4 whose input is unavailable gives
``data_unavailable`` with a :class:`~fse.engine.types.MissingInput` naming it
(Req 14.9). So does ``fixed_dollar_risk`` for an unpriced setup, which has no
entry or stop (Req 8.11).

Thresholds and fractions are compared as the decimal values written in the
Strategy_Config (``Fraction(repr(x))``), and money is ``Decimal``, so every
boundary is exact: a VIX gap from 16.0 to 18.4 is exactly 15% and triggers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.sizing import SizingConfig
from fse.engine.levels import TICKS_PER_POINT
from fse.engine.types import (
    CandidateSetup,
    MissingInput,
    Money,
    SetupKey,
    Trade,
    Unavailable,
    VixState,
)

__all__ = [
    "INPUT_ENTRY",
    "INPUT_STOP",
    "INPUT_TRINITY_AGREEMENT",
    "INPUT_VIX_DAILY_OPEN",
    "INPUT_VIX_PRIOR_CLOSE",
    "MICRO_EQUIVALENTS_PER_CONTRACT",
    "POINT_VALUE_USD",
    "SIZING_STEPS",
    "TICK_VALUE_USD",
    "TRINITY_SIZE_DOWN_AGREEING",
    "TRINITY_SYMBOL_COUNT",
    "PriorSession",
    "SizedSetup",
    "SizingContext",
    "SizingRejection",
    "SizingRejectionReason",
    "SizingStep",
    "StepOutcome",
    "base_contracts",
    "big_win_triggered",
    "micro_equivalents",
    "size",
    "vix_gap_pct",
]

TICK_VALUE_USD: Final[Mapping[str, Money]] = MappingProxyType(
    {"MES": Decimal("1.25"), "MNQ": Decimal("0.50"), "ES": Decimal("12.50"), "NQ": Decimal("5.00")}
)
"""Dollars per 0.25-point tick per contract: the Fill_Simulator contract table (Req 13.9)."""

POINT_VALUE_USD: Final[Mapping[str, Money]] = MappingProxyType(
    {name: value * TICKS_PER_POINT for name, value in TICK_VALUE_USD.items()}
)
"""Dollars per point per contract: MES $5, MNQ $2, ES $50, NQ $20 (Req 14.2)."""

MICRO_EQUIVALENTS_PER_CONTRACT: Final[Mapping[str, int]] = MappingProxyType(
    {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}
)
"""1 MES or MNQ contract is 1 Micro_Equivalent; 1 ES or NQ contract is 10."""

TRINITY_SYMBOL_COUNT: Final = 3
TRINITY_SIZE_DOWN_AGREEING: Final = 2
"""Trinity size-down applies when exactly this many of the 3 symbols agree (Req 14.6)."""

_VIX_GAP_DIVISOR: Final = 2

INPUT_TRINITY_AGREEMENT: Final = "trinity_agreement"
INPUT_VIX_DAILY_OPEN: Final = "vix_daily_open"
INPUT_VIX_PRIOR_CLOSE: Final = "vix_prior_close"
INPUT_ENTRY: Final = "entry"
INPUT_STOP: Final = "stop"

type SizingStep = Literal[
    "base_contracts", "big_win_reduce", "trinity_size_down", "vix_gap_halve", "micro_cap"
]
SIZING_STEPS: Final[tuple[SizingStep, ...]] = (
    "base_contracts",
    "big_win_reduce",
    "trinity_size_down",
    "vix_gap_halve",
    "micro_cap",
)
"""The steps in the order :func:`size` runs them (Req 14.8)."""

type SizingRejectionReason = Literal["size_zero", "data_unavailable"]


def _lookup[V](table: Mapping[str, V], instrument: str) -> V:
    try:
        return table[instrument]
    except KeyError:
        raise ValueError(
            f"instrument {instrument!r} is not one of the sized instruments {sorted(table)}"
        ) from None


def _exact(x: float) -> Fraction:
    """The decimal value ``x`` was written as: ``0.29`` is 29/100, not its binary float."""
    return Fraction(repr(x))


def _require_count(name: str, value: object, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}, got {value!r}")


def _require_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal, got {value!r}")


def micro_equivalents(instrument: str, contracts: int) -> int:
    """The Micro_Equivalents of ``contracts`` contracts of ``instrument``."""
    _require_count("contracts", contracts, 0)
    return contracts * _lookup(MICRO_EQUIVALENTS_PER_CONTRACT, instrument)


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class PriorSession:
    """The filled trades of the immediately preceding session, summed (Req 14.5).

    ``net`` is their net P&L in dollars and ``r_sum`` the sum of their
    R_Multiples. A session without filled trades is ``PriorSession()``:
    $0 and 0 R.
    """

    net: Money = Decimal(0)
    r_sum: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _require_decimal("PriorSession.net", self.net)
        _require_decimal("PriorSession.r_sum", self.r_sum)

    @classmethod
    def from_trades(cls, trades: Iterable[Trade]) -> PriorSession:
        """Sum ``trades``, skipping every Shadow_Trade."""
        filled = [t for t in trades if not t.shadow]
        return cls(
            net=sum((t.net for t in filled), Decimal(0)),
            r_sum=sum((t.r_multiple for t in filled), Decimal(0)),
        )


@dataclass(frozen=True, slots=True)
class SizingContext:
    """What the Position_Sizer reads at the Decision_Time, besides the setup.

    - ``prior_session``: the previous session's filled trades (Req 14.5).
    - ``trinity_agreement``: the trinity_agreement measured value, the number
      of agreeing symbols out of 3 (Req 11.9), or ``Unavailable``.
    - ``vix``: the session's VIX daily open and prior close (Req 4.9-4.11).
    - ``used_micro_equivalents``: the Micro_Equivalents of the Strategy_Engine's
      open positions and resting entry orders (Req 14.4); see
      :func:`micro_equivalents`.
    """

    prior_session: PriorSession
    trinity_agreement: int | Unavailable
    vix: VixState
    used_micro_equivalents: int

    def __post_init__(self) -> None:
        if not isinstance(self.trinity_agreement, Unavailable):
            _require_count("SizingContext.trinity_agreement", self.trinity_agreement, 0)
            if self.trinity_agreement > TRINITY_SYMBOL_COUNT:
                raise ValueError(
                    f"SizingContext.trinity_agreement counts at most {TRINITY_SYMBOL_COUNT} "
                    f"symbols, got {self.trinity_agreement}"
                )
        _require_count("SizingContext.used_micro_equivalents", self.used_micro_equivalents, 0)


# ---------------------------------------------------------------- results


def _require_step(name: str, value: str) -> None:
    if value not in SIZING_STEPS:
        raise ValueError(f"{name} must be one of {SIZING_STEPS}, got {value!r}")


def _require_prefix(name: str, steps: tuple[StepOutcome, ...]) -> None:
    if tuple(s.step for s in steps) != SIZING_STEPS[: len(steps)]:
        raise ValueError(f"{name} must follow the sizing order {SIZING_STEPS}")


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """One sizing step that ran: the contracts after it, and whether its rule fired.

    ``triggered`` is always true for ``base_contracts``. For ``micro_cap`` it
    means the cap was below the contracts it received.
    """

    step: SizingStep
    contracts: int
    triggered: bool

    def __post_init__(self) -> None:
        _require_step("StepOutcome.step", self.step)
        _require_count("StepOutcome.contracts", self.contracts, 0)


@dataclass(frozen=True, slots=True)
class SizedSetup:
    """A Candidate_Setup with its contracts; ``steps`` holds all five steps in order."""

    setup: CandidateSetup
    contracts: int
    steps: tuple[StepOutcome, ...]

    def __post_init__(self) -> None:
        _require_count("SizedSetup.contracts", self.contracts, 1)
        if len(self.steps) != len(SIZING_STEPS):
            raise ValueError(f"SizedSetup.steps must hold all {len(SIZING_STEPS)} steps")
        _require_prefix("SizedSetup.steps", self.steps)
        if any(s.contracts == 0 for s in self.steps):
            raise ValueError("a SizedSetup has no step that yielded 0 contracts")
        if self.steps[-1].contracts != self.contracts:
            raise ValueError("SizedSetup.contracts must equal the last step's contracts")

    @property
    def base_contracts(self) -> int:
        """The ``base_contracts`` step's result: the contracts of a Shadow_Trade (Req 19.8)."""
        return self.steps[0].contracts


@dataclass(frozen=True, slots=True)
class SizingRejection:
    """A Candidate_Setup the Position_Sizer refused, with the step that refused it.

    - ``size_zero``: ``step`` yielded 0 contracts; it is the last of ``steps``
      and ``missing`` is ``None`` (Req 14.3).
    - ``data_unavailable``: ``step`` could not run; ``missing`` names its
      absent inputs and ``steps`` holds the steps before it (Req 14.9).
    """

    key: SetupKey
    reason: SizingRejectionReason
    step: SizingStep
    missing: MissingInput | None
    steps: tuple[StepOutcome, ...]

    def __post_init__(self) -> None:
        _require_step("SizingRejection.step", self.step)
        _require_prefix("SizingRejection.steps", self.steps)
        if self.reason == "size_zero":
            if self.missing is not None:
                raise ValueError("a size_zero SizingRejection names no missing input")
            if not self.steps or self.steps[-1].step != self.step or self.steps[-1].contracts:
                raise ValueError("a size_zero SizingRejection ends with its step at 0 contracts")
        elif self.reason == "data_unavailable":
            if self.missing is None:
                raise ValueError("a data_unavailable SizingRejection names the missing input")
            ran = len(self.steps)
            if ran >= len(SIZING_STEPS) or SIZING_STEPS[ran] != self.step:
                raise ValueError(
                    "a data_unavailable SizingRejection holds the steps before its step"
                )
        else:
            raise ValueError(
                f"SizingRejection.reason must be size_zero or data_unavailable, got {self.reason!r}"
            )


# ---------------------------------------------------------------- rules


def base_contracts(c: CandidateSetup, cfg: SizingConfig) -> int | MissingInput:
    """Step 1 on its own (Req 14.1-14.2); may be 0 under ``fixed_dollar_risk``."""
    instrument = c.key.instrument
    if cfg.mode == "fixed_contracts":
        return cfg.fixed.for_instrument(instrument)
    point_value = _lookup(POINT_VALUE_USD, instrument)
    if c.entry is None or c.stop is None:
        return MissingInput((INPUT_ENTRY, INPUT_STOP))
    stop_points = Fraction(abs(c.entry - c.stop), TICKS_PER_POINT)
    return math.floor(Fraction(cfg.risk_usd) / (stop_points * Fraction(point_value)))


def big_win_triggered(prior: PriorSession, cfg: SizingConfig) -> bool:
    """Whether the previous session reached the big-win dollar or R threshold (Req 14.5)."""
    rule = cfg.big_win
    return prior.net >= rule.usd or Fraction(prior.r_sum) >= _exact(rule.r)


def _usable(value: float | Unavailable, *, positive: bool) -> float | None:
    if isinstance(value, Unavailable) or not math.isfinite(value):
        return None
    if positive and value <= 0:
        return None
    return value


def vix_gap_pct(vix: VixState) -> Fraction | MissingInput:
    """The session's VIX gap in percent, exactly, or the inputs that are missing (Req 14.7).

    A prior close at or below 0 counts as missing: the gap is undefined.
    """
    daily_open = _usable(vix.daily_open, positive=False)
    prior_close = _usable(vix.prior_close, positive=True)
    if daily_open is None or prior_close is None:
        missing: tuple[str, ...] = (INPUT_VIX_DAILY_OPEN,) if daily_open is None else ()
        if prior_close is None:
            missing += (INPUT_VIX_PRIOR_CLOSE,)
        return MissingInput(missing)
    prior = _exact(prior_close)
    return (_exact(daily_open) - prior) / prior * 100


# ---------------------------------------------------------------- the pipeline

type _StepResult = tuple[int, bool] | MissingInput
type _Step = Callable[[int, CandidateSetup, SizingContext, SizingConfig], _StepResult]


def _base_step(q: int, c: CandidateSetup, ctx: SizingContext, cfg: SizingConfig) -> _StepResult:
    base = base_contracts(c, cfg)
    return base if isinstance(base, MissingInput) else (base, True)


def _big_win_step(q: int, c: CandidateSetup, ctx: SizingContext, cfg: SizingConfig) -> _StepResult:
    if not big_win_triggered(ctx.prior_session, cfg):
        return q, False
    return min(q, cfg.big_win.reduced.for_instrument(c.key.instrument)), True


def _trinity_step(q: int, c: CandidateSetup, ctx: SizingContext, cfg: SizingConfig) -> _StepResult:
    rule = cfg.trinity_size_down
    if not rule.enabled:
        return q, False
    agreeing = ctx.trinity_agreement
    if isinstance(agreeing, Unavailable):
        return MissingInput((INPUT_TRINITY_AGREEMENT,))
    if agreeing != TRINITY_SIZE_DOWN_AGREEING:
        return q, False
    return max(1, math.floor(q * _exact(rule.fraction))), True


def _vix_gap_step(q: int, c: CandidateSetup, ctx: SizingContext, cfg: SizingConfig) -> _StepResult:
    rule = cfg.vix_gap
    if not rule.enabled:
        return q, False
    gap = vix_gap_pct(ctx.vix)
    if isinstance(gap, MissingInput):
        return gap
    if gap < _exact(rule.pct):
        return q, False
    return max(1, q // _VIX_GAP_DIVISOR), True


def _micro_cap_step(
    q: int, c: CandidateSetup, ctx: SizingContext, cfg: SizingConfig
) -> _StepResult:
    remaining = max(0, cfg.micro_equivalent_limit - ctx.used_micro_equivalents)
    cap = remaining // micro_equivalents(c.key.instrument, 1)
    return min(q, cap), cap < q


_STEPS: Final[tuple[tuple[SizingStep, _Step], ...]] = (
    ("base_contracts", _base_step),
    ("big_win_reduce", _big_win_step),
    ("trinity_size_down", _trinity_step),
    ("vix_gap_halve", _vix_gap_step),
    ("micro_cap", _micro_cap_step),
)


def size(c: CandidateSetup, ctx: SizingContext, cfg: SizingConfig) -> SizedSetup | SizingRejection:
    """Contracts for ``c``, or the step that refused it (Req 14.1-14.9).

    Raises ``ValueError`` for an instrument outside MES, MNQ, ES and NQ.
    """
    q = 0
    done: list[StepOutcome] = []
    for name, step in _STEPS:
        result = step(q, c, ctx, cfg)
        if isinstance(result, MissingInput):
            return SizingRejection(c.key, "data_unavailable", name, result, tuple(done))
        q, triggered = result
        done.append(StepOutcome(name, q, triggered))
        if q == 0:
            return SizingRejection(c.key, "size_zero", name, None, tuple(done))
    return SizedSetup(c, q, tuple(done))
