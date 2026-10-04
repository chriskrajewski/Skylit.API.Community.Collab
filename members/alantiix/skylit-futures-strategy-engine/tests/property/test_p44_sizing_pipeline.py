"""Property 44: Sizing pipeline.

*For any* Candidate_Setup, prior-session results, trinity count, VIX gap, open
exposure and sizing configuration, the contracts equal a reference application
of base size (fixed or floor(risk / (stop x point value))), big-win reduction,
Trinity size-down, VIX-gap halving and the Micro_Equivalent cap in that order,
stopping with ``size_zero`` naming the step at the first zero, and
``data_unavailable`` naming the input when an enabled rule's input is missing.

The reference model reads Requirement 14 criteria 2-9 directly, on exact
rationals built from the decimal text a Strategy_Config or a bar carries
("0.29" is 29/100, not its binary float):

- base: the configured count (``fixed_contracts``), or
  floor(risk / (|entry - stop| / 4 points x point value)) at $5 MES, $2 MNQ,
  $50 ES and $20 NQ per point (``fixed_dollar_risk``); an unpriced setup has
  no stop distance, so ``fixed_dollar_risk`` names ``entry`` and ``stop``;
- big win: prior net >= the dollar threshold or summed R >= the R threshold
  gives min(q, reduced);
- Trinity: enabled with exactly 2 agreeing gives max(1, floor(q x fraction));
  enabled with no measured value names ``trinity_agreement``;
- VIX gap: enabled with (open - prior close) / prior close x 100 >= pct gives
  max(1, floor(q / 2)); enabled with an unavailable (or NaN) open names
  ``vix_daily_open``, an unavailable prior close (or one of 0, where the gap
  is undefined) names ``vix_prior_close``, open first;
- cap: min(q, floor(max(0, limit - used) / Micro_Equivalents per contract)),
  1 per MES or MNQ contract and 10 per ES or NQ contract.

Every step that runs records its contracts and whether its rule fired (always
for the base; for the cap, when it binds). The run stops at the first step
that yields 0 (``size_zero`` at that step, which is the last one recorded) or
at the first enabled step with a missing input (``data_unavailable`` at that
step, holding only the steps before it).

Generators: any of MES, MNQ, ES and NQ, long or short, with a stop 1 to 800
ticks from entry, or (1 in 10) an unpriced setup. Counts, thresholds,
fractions and percents are drawn as short decimals anywhere in their valid
range and, half the time or so, on or one unit below the boundary the setup
makes: a risk that is an exact multiple of one contract's risk, a prior
session exactly at either big-win threshold, a VIX open whose gap is exactly
the configured percent, and an exposure near the Micro_Equivalent limit.

**Validates: Requirements 14.2, 14.3, 14.5, 14.6, 14.7, 14.8, 14.9**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.sizing import SizingConfig
from fse.engine.sizing import PriorSession, SizedSetup, SizingContext, SizingRejection, size
from fse.engine.types import (
    CandidateSetup,
    Direction,
    SetupInputs,
    SetupKey,
    SourceNodeRef,
    Unavailable,
    VixState,
)

INSTRUMENTS: Final = ("MES", "MNQ", "ES", "NQ")
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
TICKS_PER_POINT: Final = 4
POINT_VALUE_USD: Final[Mapping[str, int]] = {"MES": 5, "MNQ": 2, "ES": 50, "NQ": 20}
MICRO_EQUIVALENTS: Final[Mapping[str, int]] = {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}
STEPS: Final = (  # Req 14.8
    "base_contracts",
    "big_win_reduce",
    "trinity_size_down",
    "vix_gap_halve",
    "micro_cap",
)
TRINITY_SIZE_DOWN_AGREEING: Final = 2  # exactly 2 of 3 (Req 14.6)

SESSION: Final = date(2026, 3, 5)
T_1000: Final = 1_772_722_800 * 10**9  # 2026-03-05 10:00 America/New_York
STRIKE: Final = 5800.0
CENT: Final = Decimal("0.01")
VIX_STEP: Final = Decimal("0.000001")


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Written:
    """The scenario as the reference model reads it: whole counts and exact decimals."""

    instrument: str
    stop_ticks: int | None  # |entry - stop|; None for an unpriced setup
    dollar_risk: bool
    fixed: int
    risk: Fraction
    big_win_usd: Fraction
    big_win_r: Fraction
    reduced: int
    trinity_on: bool
    trinity_fraction: Fraction
    vix_on: bool
    vix_pct: Fraction
    limit: int
    prior_net: Fraction
    prior_r: Fraction
    agreeing: int | None  # None: no measured value
    vix_open: Fraction | None  # None: unavailable or NaN
    vix_prior_close: Fraction | None  # None: unavailable
    used: int


@dataclass(frozen=True, slots=True)
class Scenario:
    setup: CandidateSetup
    ctx: SizingContext
    cfg: SizingConfig
    written: Written


def make_setup(
    instrument: str, direction: Direction, entry: int, stop_ticks: int | None
) -> CandidateSetup:
    """A long or short setup ``stop_ticks`` from its stop, or an unpriced one (Req 8.11)."""
    key = SetupKey(instrument, "floor_ceiling_bounce", STRIKE, direction, SESSION, 1)
    priced = stop_ticks is not None
    inputs = SetupInputs(
        map_as_of=(),
        source_spot=STRIKE,
        futures_price=entry,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=5.0,
        regime="Positive_Gamma",
        map_grade="Neutral_Map",
        stop_rule="fixed_ticks" if priced else None,
    )
    if stop_ticks is None:
        return CandidateSetup(
            key=key,
            detector_id="floor_ceiling_bounce",
            t=T_1000,
            entry=None,
            stop=None,
            targets=(),
            exit_mode="fixed_r",
            source=SourceNodeRef("SPX", "gamma", STRIKE, 2.5e9, None, None),
            inputs=inputs,
        )
    sign = 1 if direction == "long" else -1
    level_pts = entry / TICKS_PER_POINT
    return CandidateSetup(
        key=key,
        detector_id="floor_ceiling_bounce",
        t=T_1000,
        entry=entry,
        stop=entry - sign * stop_ticks,
        targets=(entry + sign * 3 * stop_ticks,),
        exit_mode="fixed_r",
        source=SourceNodeRef(
            "SPX", "gamma", STRIKE, 2.5e9, entry, (level_pts - 5.0, level_pts + 5.0)
        ),
        inputs=inputs,
    )


def decimals(lo: int, hi: int, places: int) -> st.SearchStrategy[Decimal]:
    """Decimals from ``lo`` to ``hi`` units of ``10 ** -places``, written with ``places`` digits."""
    return st.integers(lo, hi).map(lambda n: Decimal(n).scaleb(-places))


COUNTS: Final = st.one_of(st.integers(1, 10), st.integers(11, 120))


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    # The setup.
    instrument = draw(st.sampled_from(INSTRUMENTS))
    direction = draw(st.sampled_from(DIRECTIONS))
    priced = draw(st.integers(0, 9)) > 0
    stop_ticks = draw(st.one_of(st.integers(1, 40), st.integers(41, 800))) if priced else None
    entry = draw(st.integers(16_000, 100_000))
    setup = make_setup(instrument, direction, entry, stop_ticks)

    # Base size: a risk anywhere, or on (or a cent below) a whole number of contracts.
    dollar_risk = draw(st.booleans())
    fixed = {name: draw(COUNTS) for name in INSTRUMENTS}
    risk = draw(decimals(1, 1_000_000, 2))
    if stop_ticks is not None and draw(st.booleans()):
        one_contract = Decimal(stop_ticks * POINT_VALUE_USD[instrument]) / TICKS_PER_POINT
        risk = one_contract * draw(st.integers(1, 30)) - draw(st.sampled_from((0, 1))) * CENT

    # Big win: thresholds anywhere; the prior session on, below or away from them.
    usd = draw(decimals(1, 300_000, 2))
    r = draw(decimals(1, 1_000, 2))
    reduced = {name: draw(COUNTS) for name in INSTRUMENTS}
    net = draw(st.one_of(st.just(usd), st.just(usd - CENT), decimals(-500_000, 500_000, 2)))
    r_sum = draw(st.one_of(st.just(r), st.just(r - CENT), decimals(-1_000, 1_000, 2)))

    # Trinity size-down.
    trinity_on = draw(st.booleans())
    fraction = draw(decimals(1, 1_000, 3))
    agreeing = draw(st.one_of(st.integers(0, 3), st.none()))

    # VIX gap: a prior close (unavailable, 0 or a level) and an open on the
    # threshold, a millionth below it, anywhere, unavailable or NaN.
    vix_on = draw(st.booleans())
    pct = draw(decimals(1, 10_000, 2))
    prior_close = draw(st.one_of(decimals(500, 8_000, 2), st.just(Decimal(0)), st.none()))
    open_kind = draw(st.integers(0, 9))
    daily_open: Decimal | None = draw(decimals(500, 16_000, 2))
    if open_kind == 0:
        daily_open = None
    elif open_kind <= 4 and prior_close:
        offset = draw(st.sampled_from((0, 1))) * VIX_STEP
        daily_open = prior_close + prior_close * pct / 100 - offset
    open_value: float | Unavailable = (
        Unavailable("no 09:30 VIX bar") if daily_open is None else float(daily_open)
    )
    if open_kind == 1:
        open_value = float("nan")
        daily_open = None

    # Micro_Equivalent exposure: anywhere, or near the limit.
    limit = draw(st.one_of(st.just(50), st.integers(1, 400)))
    used = draw(st.one_of(st.integers(0, limit + 30), st.integers(max(0, limit - 40), limit)))

    cfg = SizingConfig.model_validate(
        {
            "mode": "fixed_dollar_risk" if dollar_risk else "fixed_contracts",
            "fixed": fixed,
            "risk_usd": str(risk),
            "big_win": {"usd": str(usd), "r": float(r), "reduced": reduced},
            "trinity_size_down": {"enabled": trinity_on, "fraction": float(fraction)},
            "vix_gap": {"enabled": vix_on, "pct": float(pct)},
            "micro_equivalent_limit": limit,
        }
    )
    ctx = SizingContext(
        prior_session=PriorSession(net, r_sum),
        trinity_agreement=Unavailable("no SPY Snapshot") if agreeing is None else agreeing,
        vix=VixState(
            daily_open=open_value,
            prior_close=(
                Unavailable("no prior VIX close") if prior_close is None else float(prior_close)
            ),
            last_1m_close=16.0,
        ),
        used_micro_equivalents=used,
    )
    written = Written(
        instrument=instrument,
        stop_ticks=stop_ticks,
        dollar_risk=dollar_risk,
        fixed=fixed[instrument],
        risk=Fraction(risk),
        big_win_usd=Fraction(usd),
        big_win_r=Fraction(r),
        reduced=reduced[instrument],
        trinity_on=trinity_on,
        trinity_fraction=Fraction(fraction),
        vix_on=vix_on,
        vix_pct=Fraction(pct),
        limit=limit,
        prior_net=Fraction(net),
        prior_r=Fraction(r_sum),
        agreeing=agreeing,
        vix_open=None if daily_open is None else Fraction(daily_open),
        vix_prior_close=None if prior_close is None else Fraction(prior_close),
        used=used,
    )
    return Scenario(setup, ctx, cfg, written)


# ---------------------------------------------------------------- reference model


@dataclass(frozen=True, slots=True)
class Expected:
    """The steps that ran as ``(step, contracts, fired)``, and how the run ended."""

    steps: tuple[tuple[str, int, bool], ...]
    reason: str | None  # None: sized
    step: str | None
    missing: tuple[str, ...] | None


def reference(w: Written) -> Expected:
    """Req 14.2-14.9, step by step, on exact rationals."""
    ran: list[tuple[str, int, bool]] = []

    def unavailable(step: str, *names: str) -> Expected:
        return Expected(tuple(ran), "data_unavailable", step, names)

    def zero(step: str) -> Expected:
        return Expected(tuple(ran), "size_zero", step, None)

    # 1. Base contracts (Req 14.1-14.2).
    if not w.dollar_risk:
        q = w.fixed
    elif w.stop_ticks is None:
        return unavailable("base_contracts", "entry", "stop")
    else:
        stop_points = Fraction(w.stop_ticks, TICKS_PER_POINT)
        q = math.floor(w.risk / (stop_points * POINT_VALUE_USD[w.instrument]))
    ran.append(("base_contracts", q, True))
    if q == 0:
        return zero("base_contracts")

    # 2. Big-win reduced size (Req 14.5).
    fired = w.prior_net >= w.big_win_usd or w.prior_r >= w.big_win_r
    if fired:
        q = min(q, w.reduced)
    ran.append(("big_win_reduce", q, fired))
    if q == 0:
        return zero("big_win_reduce")

    # 3. Trinity size-down (Req 14.6, 14.9).
    fired = False
    if w.trinity_on:
        if w.agreeing is None:
            return unavailable("trinity_size_down", "trinity_agreement")
        fired = w.agreeing == TRINITY_SIZE_DOWN_AGREEING
        if fired:
            q = max(1, math.floor(q * w.trinity_fraction))
    ran.append(("trinity_size_down", q, fired))
    if q == 0:
        return zero("trinity_size_down")

    # 4. VIX-gap halving (Req 14.7, 14.9).
    fired = False
    if w.vix_on:
        daily_open, prior_close = w.vix_open, w.vix_prior_close
        if prior_close is not None and prior_close <= 0:
            prior_close = None  # the gap is undefined
        if daily_open is None or prior_close is None:
            names: tuple[str, ...] = ("vix_daily_open",) if daily_open is None else ()
            names += ("vix_prior_close",) if prior_close is None else ()
            return unavailable("vix_gap_halve", *names)
        gap_pct = (daily_open - prior_close) / prior_close * 100
        fired = gap_pct >= w.vix_pct
        if fired:
            q = max(1, q // 2)
    ran.append(("vix_gap_halve", q, fired))
    if q == 0:
        return zero("vix_gap_halve")

    # 5. Micro_Equivalent cap (Req 14.3-14.4).
    cap = max(0, w.limit - w.used) // MICRO_EQUIVALENTS[w.instrument]
    fired = cap < q
    q = min(q, cap)
    ran.append(("micro_cap", q, fired))
    if q == 0:
        return zero("micro_cap")
    return Expected(tuple(ran), None, None, None)


def observed(result: SizedSetup | SizingRejection) -> Expected:
    steps = tuple((s.step, s.contracts, s.triggered) for s in result.steps)
    if isinstance(result, SizedSetup):
        return Expected(steps, None, None, None)
    missing = None if result.missing is None else result.missing.names
    return Expected(steps, result.reason, result.step, missing)


# ---------------------------------------------------------------- property


@given(scenarios())
def test_sizing_matches_the_reference_pipeline(s: Scenario) -> None:
    expected = reference(s.written)
    event(f"{expected.reason or 'sized'} at {expected.step or 'end'}")
    result = size(s.setup, s.ctx, s.cfg)

    assert observed(result) == expected
    if isinstance(result, SizedSetup):
        assert result.setup is s.setup
        assert result.contracts == expected.steps[-1][1] >= 1
        assert result.base_contracts == expected.steps[0][1]
    else:
        assert result.key == s.setup.key
