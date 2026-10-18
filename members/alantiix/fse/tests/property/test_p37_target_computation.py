"""Property 37: Target computation.

*For any* accepted Candidate_Setup, Map_State and Exit_Mode configuration
(including per-Regime overrides), the targets equal the Requirement 12 rules
for the active mode (Fixed_R, Next_Node, TP1_Partial_BE,
Opposition_Or_Fixed_R), are rounded to a tick toward entry and at least 1
tick beyond it, TP1 and TP2 quantities sum to the position with
TP1 = max(1, floor(f x qty)), and a missing Node target or a TP2 not beyond
TP1 yields no order with the matching Rejection_Reason.

The reference model reads Requirement 12 criteria 2-7, 9 and 12 directly, on
whole ticks and exact rationals built from the decimal text a Strategy_Config
or a Snapshot carries ("0.07" is 7/100, not its binary float):

- setting (12.2): the per-Regime Exit_Mode and stop rule of the setup's Regime
  when one is configured, else the global ones; a setup with no Regime gets
  the global ones;
- "beyond" a price is strictly above it for a long and strictly below it for
  a short; a Node target is never the source Node (12.5, 12.9);
- an R target is floor(r x |entry - stop|) ticks beyond entry, at least 1:
  rounded toward entry, never onto it (12.3-12.4);
- Next_Node: the nearest Node beyond entry, else ``no_target_node`` (12.5, 12.12);
- TP1_Partial_BE: TP1 by its rule (R, or the Next_Node Node), TP2 by its rule
  (R, or the nearest Node beyond TP1); a missing Node gives ``no_target_node``
  and a TP2 not farther from entry than TP1 gives ``tp2_not_beyond_tp1``; TP1
  takes max(1, floor(f x qty)) contracts and TP2 the rest (12.6-12.7, 12.12);
- Opposition_Or_Fixed_R: the nearer to entry of the R target and the nearest
  Node beyond entry with |value| >= fraction x |source value|, or the R target
  when no such Node exists (12.9);
- Fixed_R and Opposition_Or_Fixed_R plan one target for every contract;
  Trailing plans none.

Generators: a long or short setup with 1 to 400 ticks of risk, a source Node
within 20 ticks of entry on either side (so it can lie beyond entry), and up
to 8 other Nodes in any order. A Node level lands anywhere, or on (or a tick
either side of) entry and each R target the configuration makes; a Node
value is a short decimal, or exactly (or just below) the opposition
threshold. R multiples, fractions and TP1 shares are short decimals anywhere
in their valid ranges; each Regime has its own setting or none, and the setup
has a Regime or none. Two explicit examples put an opposition Node exactly on
the threshold, long under the global setting and short under a per-Regime one.

**Validates: Requirements 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.9, 12.12**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Final

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.exits import ExitModeName, ExitsConfig
from fse.engine.targets import (
    NodeLevel,
    TargetContext,
    TargetRejection,
    Targets,
    exit_mode_for,
    plan_targets,
)
from fse.engine.types import (
    CandidateSetup,
    Direction,
    MissingInput,
    NoTarget,
    Regime,
    SetupInputs,
    SetupKey,
    SourceNodeRef,
    StopRule,
)

DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
REGIMES: Final[tuple[Regime, ...]] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
MODES: Final[tuple[ExitModeName, ...]] = (
    "fixed_r",
    "next_node",
    "tp1_partial_be",
    "opposition_or_fixed_r",
    "trailing",
)
STOP_RULES: Final[tuple[StopRule, ...]] = ("one_node_beyond", "fixed_ticks")
TARGET_RULES: Final = ("r", "node")

TICKS_PER_POINT: Final = 4
BAND_HALF_WIDTH_PTS: Final = 5.0
SOURCE_REACH: Final = 20  # ticks: 5 points, the band half-width
SESSION: Final = date(2026, 3, 5)
T_1000: Final = 1_772_722_800 * 10**9  # 2026-03-05 10:00 America/New_York
NO_REGIME: Final = MissingInput(("regime",))

type Setting = tuple[ExitModeName, StopRule]
type RawNode = tuple[float, Decimal, int]  # strike, signed value as written, level in ticks


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Raw:
    """One scenario as written: prices in whole ticks and every other number as a decimal.

    The mode parameters default to the Config_Schema defaults; ``per_regime``
    lists the configured Regimes only.
    """

    direction: Direction
    entry: int
    risk: int  # |entry - stop| ticks
    source_strike: float
    nodes: tuple[RawNode, ...]  # the source Node's Snapshot, source included, in context order
    global_setting: Setting
    regime: Regime | None  # None: no Regime attached
    per_regime: Mapping[Regime, Setting] = field(default_factory=dict)
    fixed_r: Decimal = Decimal("3.0")
    tp1_rule: str = "r"
    tp1_r: Decimal = Decimal("1.5")
    tp2_rule: str = "node"
    tp2_r: Decimal = Decimal("3.0")
    tp1_fraction: Decimal = Decimal("0.5")
    opp_r: Decimal = Decimal("3.0")
    opp_fraction: Decimal = Decimal("0.85")
    qty: int = 1

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1

    @property
    def source(self) -> RawNode:
        return next(n for n in self.nodes if n[0] == self.source_strike)


@dataclass(frozen=True, slots=True)
class Scenario:
    setup: CandidateSetup
    cfg: ExitsConfig
    ctx: TargetContext
    raw: Raw


def build(raw: Raw) -> Scenario:
    """The accepted Candidate_Setup, exits config and Map_State Nodes ``raw`` describes."""
    sign = raw.sign
    cfg = ExitsConfig.model_validate(
        {
            "global": {"mode": raw.global_setting[0], "stop_rule": raw.global_setting[1]},
            "per_regime": {  # every Regime written out; null falls back to global
                name: (
                    {"mode": raw.per_regime[name][0], "stop_rule": raw.per_regime[name][1]}
                    if name in raw.per_regime
                    else None
                )
                for name in REGIMES
            },
            "modes": {
                "fixed_r": {"r_multiple": float(raw.fixed_r)},
                "tp1_partial_be": {
                    "tp1": {"rule": raw.tp1_rule, "r_multiple": float(raw.tp1_r)},
                    "tp2": {"rule": raw.tp2_rule, "r_multiple": float(raw.tp2_r)},
                    "tp1_fraction": float(raw.tp1_fraction),
                },
                "opposition_or_fixed_r": {
                    "r_multiple": float(raw.opp_r),
                    "fraction": float(raw.opp_fraction),
                },
            },
        }
    )
    ctx = TargetContext(tuple(NodeLevel(k, float(v), level) for k, v, level in raw.nodes))

    # A priced Candidate_Setup; plan_targets does not read its targets.
    _, source_value, source_level = raw.source
    pts = source_level / TICKS_PER_POINT
    source = SourceNodeRef(
        "SPX",
        "gamma",
        raw.source_strike,
        float(source_value),
        source_level,
        (pts - BAND_HALF_WIDTH_PTS, pts + BAND_HALF_WIDTH_PTS),
    )
    mode, stop_rule = setting(raw)
    inputs = SetupInputs(
        map_as_of=(),
        source_spot=raw.source_strike,
        futures_price=raw.entry,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=BAND_HALF_WIDTH_PTS,
        regime=NO_REGIME if raw.regime is None else raw.regime,
        map_grade="Neutral_Map",
        stop_rule=stop_rule,
    )
    setup = CandidateSetup(
        key=SetupKey("MES", "floor_ceiling_bounce", raw.source_strike, raw.direction, SESSION, 1),
        detector_id="floor_ceiling_bounce",
        t=T_1000,
        entry=raw.entry,
        stop=raw.entry - sign * raw.risk,
        targets=(raw.entry + sign,),
        exit_mode=mode,
        source=source,
        inputs=inputs,
    )
    return Scenario(setup, cfg, ctx, raw)


def decimals(lo: int, hi: int, places: int) -> st.SearchStrategy[Decimal]:
    """Decimals from ``lo`` to ``hi`` units of ``10 ** -places``, written with ``places`` digits."""
    return st.integers(lo, hi).map(lambda n: Decimal(n).scaleb(-places))


R_MULTIPLES: Final = st.one_of(decimals(5, 100, 1), decimals(50, 1000, 2))  # 0.5 to 10.0
TP1_SHARES: Final = decimals(10, 90, 2)  # 0.1 to 0.9
OPP_FRACTIONS: Final = st.one_of(st.just(Decimal("0.85")), decimals(1, 100, 2))  # 0.01 to 1
MAGNITUDES: Final = st.builds(
    lambda n, e: Decimal(n).scaleb(e), st.integers(1, 9999), st.integers(-3, 6)
)
MODE_NAMES: Final = st.integers(0, 12).map(lambda i: MODES[min(i // 3, 4)])  # Trailing 1 in 13
SETTINGS: Final = st.tuples(MODE_NAMES, st.sampled_from(STOP_RULES))
SIGNS: Final = st.sampled_from((1, -1))


def r_offset(r: Fraction, risk: int) -> int:
    """Ticks from entry to an ``r`` R target: rounded toward entry, at least 1 (Req 12.3-12.4)."""
    return max(1, math.floor(r * risk))


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    # The setup's prices.
    direction = draw(st.sampled_from(DIRECTIONS))
    sign = 1 if direction == "long" else -1
    entry = draw(st.integers(16_000, 100_000))
    risk = draw(st.one_of(st.integers(1, 8), st.integers(9, 400)))

    # The exits section: mode parameters, global and per-Regime settings.
    fixed_r, tp1_r, tp2_r, opp_r = (draw(R_MULTIPLES) for _ in range(4))
    opp_fraction = draw(OPP_FRACTIONS)
    per_regime = {name: s for name in REGIMES if (s := draw(st.none() | SETTINGS)) is not None}

    # The source Node, within its band of entry, and the other Nodes of its Snapshot.
    strikes = draw(st.lists(st.integers(1000, 1400), min_size=1, max_size=9, unique=True))
    source_strike = 5.0 * strikes[0]
    source_abs = draw(MAGNITUDES)
    source_level = entry + draw(st.integers(-SOURCE_REACH, SOURCE_REACH))

    marks = sorted({0, *(r_offset(Fraction(r), risk) for r in (fixed_r, tp1_r, tp2_r, opp_r))})
    near = st.sampled_from(marks).flatmap(lambda m: st.integers(m - 1, m + 1))
    threshold = opp_fraction * source_abs
    just_below = threshold - Decimal(1).scaleb(threshold.adjusted() - 6)
    magnitudes = st.one_of(MAGNITUDES, st.just(threshold), st.just(just_below))
    nodes: list[RawNode] = [(source_strike, draw(SIGNS) * source_abs, source_level)]
    for k in strikes[1:]:
        offset = draw(st.one_of(near, st.integers(-600, 600)))
        nodes.append((5.0 * k, draw(SIGNS) * draw(magnitudes), entry + sign * offset))

    return build(
        Raw(
            direction=direction,
            entry=entry,
            risk=risk,
            source_strike=source_strike,
            nodes=tuple(draw(st.permutations(nodes))),
            global_setting=draw(SETTINGS),
            regime=draw(st.none() | st.sampled_from(REGIMES)),
            per_regime=per_regime,
            fixed_r=fixed_r,
            tp1_rule=draw(st.sampled_from(TARGET_RULES)),
            tp1_r=tp1_r,
            tp2_rule=draw(st.sampled_from(TARGET_RULES)),
            tp2_r=tp2_r,
            tp1_fraction=draw(TP1_SHARES),
            opp_r=opp_r,
            opp_fraction=opp_fraction,
            qty=draw(st.one_of(st.integers(1, 3), st.integers(4, 60))),
        )
    )


# ---------------------------------------------------------------- reference model


@dataclass(frozen=True, slots=True)
class Expected:
    """The active mode and its plan: prices and (TP1, TP2) contracts, or a rejection reason."""

    mode: str
    prices: tuple[int, ...] | None  # None: Trailing or a rejection
    quantities: tuple[int, int] | None
    reason: str | None  # None: no rejection


def setting(raw: Raw) -> Setting:
    """The Exit_Mode and stop rule for the setup's Regime (Req 12.2)."""
    configured = None if raw.regime is None else raw.per_regime.get(raw.regime)
    return raw.global_setting if configured is None else configured


def reference(raw: Raw) -> Expected:
    """Req 12.3-12.7, 12.9 and 12.12 for the active mode, on whole ticks and exact decimals."""
    mode, _ = setting(raw)
    sign, entry, qty = raw.sign, raw.entry, raw.qty
    source_abs = abs(Fraction(raw.source[1]))

    def r_target(r: Decimal) -> int:
        return entry + sign * r_offset(Fraction(r), raw.risk)

    def nearest_beyond(origin: int, min_abs: Fraction | None = None) -> int | None:
        levels = [
            level
            for strike, value, level in raw.nodes
            if strike != raw.source_strike
            and sign * (level - origin) > 0
            and (min_abs is None or abs(Fraction(value)) >= min_abs)
        ]
        return min(levels, key=lambda level: sign * level, default=None)

    def one(price: int) -> Expected:
        return Expected(mode, (price,), (qty, 0), None)

    def rejected(reason: str) -> Expected:
        return Expected(mode, None, None, reason)

    match mode:
        case "fixed_r":  # 12.4
            return one(r_target(raw.fixed_r))
        case "next_node":  # 12.5, 12.12
            level = nearest_beyond(entry)
            return rejected("no_target_node") if level is None else one(level)
        case "tp1_partial_be":  # 12.6, 12.7, 12.12
            tp1 = r_target(raw.tp1_r) if raw.tp1_rule == "r" else nearest_beyond(entry)
            if tp1 is None:
                return rejected("no_target_node")
            tp2 = r_target(raw.tp2_r) if raw.tp2_rule == "r" else nearest_beyond(tp1)
            if tp2 is None:
                return rejected("no_target_node")
            if sign * (tp2 - entry) <= sign * (tp1 - entry):
                return rejected("tp2_not_beyond_tp1")
            at_tp1 = max(1, math.floor(Fraction(raw.tp1_fraction) * qty))
            return Expected(mode, (tp1, tp2), (at_tp1, qty - at_tp1), None)
        case "opposition_or_fixed_r":  # 12.9
            fixed = r_target(raw.opp_r)
            opposition = nearest_beyond(entry, Fraction(raw.opp_fraction) * source_abs)
            if opposition is None:
                return one(fixed)
            return one(min(fixed, opposition, key=lambda level: sign * level))
        case _:  # trailing
            return Expected(mode, None, None, None)


def observed(mode: str, result: Targets | NoTarget | TargetRejection, qty: int) -> Expected:
    if isinstance(result, Targets):
        return Expected(result.mode, result.prices, result.quantities(qty), None)
    if isinstance(result, TargetRejection):
        return Expected(mode, None, None, result.reason)
    return Expected(mode, None, None, None)


# ---------------------------------------------------------------- explicit examples

# An opposition Node at exactly 7% of a 2.5e9 source: 40 ticks out, nearer than 3R (60).
LONG_AT_THRESHOLD: Final = build(
    Raw(
        direction="long",
        entry=23120,
        risk=20,
        source_strike=5780.0,
        nodes=((5780.0, Decimal("2.5e9"), 23120), (5790.0, Decimal("-1.75e8"), 23160)),
        global_setting=("opposition_or_fixed_r", "one_node_beyond"),
        regime="Negative_Gamma",
        opp_fraction=Decimal("0.07"),
    )
)
# The short mirror at exactly 7% of a 1.7e9 source, under a per-Regime override.
SHORT_AT_THRESHOLD_PER_REGIME: Final = build(
    Raw(
        direction="short",
        entry=23120,
        risk=20,
        source_strike=5780.0,
        nodes=((5770.0, Decimal("1.19e8"), 23080), (5780.0, Decimal("-1.7e9"), 23120)),
        global_setting=("fixed_r", "fixed_ticks"),
        regime="Whipsaw",
        per_regime={"Whipsaw": ("opposition_or_fixed_r", "one_node_beyond")},
        opp_fraction=Decimal("0.07"),
    )
)


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 37: Target computation
@example(s=LONG_AT_THRESHOLD)
@example(s=SHORT_AT_THRESHOLD_PER_REGIME)
@given(s=scenarios())
def test_targets_follow_the_requirement_12_rules(s: Scenario) -> None:
    raw = s.raw
    mode_cfg = exit_mode_for(s.cfg, s.setup.inputs.regime)
    assert (mode_cfg.mode, mode_cfg.stop_rule) == setting(raw)  # 12.2

    got = observed(mode_cfg.mode, plan_targets(s.setup, mode_cfg, s.ctx), raw.qty)
    expected = reference(raw)
    event(f"{expected.mode}: {expected.reason or ('planned' if expected.prices else 'none')}")
    assert got == expected

    if got.prices is not None and got.quantities is not None:
        assert all(raw.sign * (p - raw.entry) >= 1 for p in got.prices)  # 12.3
        at_tp1, at_tp2 = got.quantities
        assert at_tp1 >= 1  # 12.7
        assert at_tp1 + at_tp2 == raw.qty
