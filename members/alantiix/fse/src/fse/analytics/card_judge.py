"""Card-protocol Rule 9.1, reconstructed from ``agents/dashingbixby/*-card-v1``.

The protocol file and ``tools/cards.py`` are not in this repo. The measured
shape in those results is: a setup hits its judged R before the stop, or it
does not; the clock baseline is the same symbol, minute, and side on other
sessions; probabilities are averaged inside a day first. FXR's brief judged
at 1R and 2R with ``p <= 0.05 / variants``. This pipeline's Rule 9.1, as
specified for the strategy-card service, judges one cell:

- judged at 2R (``k`` is the Bonferroni divisor, default 2, so ``p <= 0.025``)
- lift versus the clock baseline is at least 0.10
- at least 15 distinct days
- positive lift on at least 60% of the symbols that actually traded
- permutation p at or below ``0.05 / k``
- positive lift on the outside symbol group

Thresholds are fields on :class:`JudgeThresholds`. Hits are supplied by the
caller (or derived from ``mfe_r``). This module does not read the market.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BaselineObservation",
    "CheckResult",
    "JudgeReport",
    "JudgeThresholds",
    "Observation",
    "judge",
]

_DEFAULT_P = Decimal("0.05")


class JudgeThresholds(BaseModel):
    """The pass bar. ``max_permutation_p`` defaults to ``0.05 / k``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    judged_r: Decimal = Field(default=Decimal("2"), gt=0)
    k: int = Field(default=2, ge=1, le=100)
    min_lift: Decimal = Field(default=Decimal("0.10"), ge=0)
    min_days: int = Field(default=15, ge=1)
    min_symbol_positive_frac: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    max_permutation_p: Decimal | None = Field(default=None, ge=0, le=1)
    permutations: int = Field(default=10_000, ge=1, le=100_000)
    baseline_draws: int = Field(default=150, ge=1, le=10_000)
    seed: int = 20261011

    @property
    def p_bar(self) -> Decimal:
        if self.max_permutation_p is not None:
            return self.max_permutation_p
        return _DEFAULT_P / Decimal(self.k)


class Observation(BaseModel):
    """One setup entry. ``hit`` wins over ``mfe_r`` when both are set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    day: date
    clock: str = Field(pattern=r"^\d{2}:\d{2}$")
    side: Literal["long", "short"]
    hit: bool | None = None
    mfe_r: Decimal | None = None
    group: Literal["design", "outside"] | None = None


class BaselineObservation(BaseModel):
    """One clock-baseline entry on some other session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    day: date
    clock: str = Field(pattern=r"^\d{2}:\d{2}$")
    side: Literal["long", "short"]
    hit: bool


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    detail: str


class JudgeReport(BaseModel):
    """PASS only when every check passed. Nothing here is a profit claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grade: Literal["PASS", "FAIL"]
    judged_at: str
    k: int
    lift: str | None
    permutation_p: str | None
    days: int
    checks: list[CheckResult]
    symbol_lifts: dict[str, str]
    outside_lift: str | None
    baseline_draws: int
    permutations: int


def judge(
    trades: list[Observation],
    baseline: list[BaselineObservation],
    *,
    design_symbols: list[str],
    outside_symbols: list[str] | None = None,
    thresholds: JudgeThresholds | None = None,
) -> JudgeReport:
    """Rule 9.1 on ``trades`` against ``baseline``. Day is the sample unit."""
    cfg = thresholds or JudgeThresholds()
    design = set(design_symbols)
    outside_named = set(outside_symbols or [])
    setup = [_hit(item, cfg) for item in trades if not _is_outside(item, design, outside_named)]
    outside = [_hit(item, cfg) for item in trades if _is_outside(item, design, outside_named)]
    pools = _pools(baseline)
    rng = random.Random(cfg.seed)
    setup_sorted = sorted(setup, key=lambda item: (item.day, item.clock, item.symbol, item.side))
    paired, missing = _pair(setup_sorted, pools, rng, cfg.baseline_draws)
    lift, setup_rate, base_rate = _lift(paired)
    symbol_lifts = _symbol_lifts(paired)
    outside_paired, outside_missing = _pair(
        sorted(outside, key=lambda item: (item.day, item.clock, item.symbol, item.side)),
        pools,
        rng,
        cfg.baseline_draws,
    )
    outside_lift, _, _ = _lift(outside_paired) if outside_paired else (None, None, None)
    p_value = _permutation(paired, rng, cfg.permutations) if paired and missing == 0 else None
    checks = _checks(
        cfg,
        lift=lift,
        days=len({row.trade.day for row in paired}),
        symbol_lifts=symbol_lifts,
        p_value=p_value,
        outside_lift=outside_lift,
        outside_n=len(outside),
        missing=missing,
        outside_missing=outside_missing,
        setup_rate=setup_rate,
        base_rate=base_rate,
    )
    return JudgeReport(
        grade="PASS" if checks and all(item.passed for item in checks) else "FAIL",
        judged_at=f"{cfg.judged_r}R",
        k=cfg.k,
        lift=None if lift is None else str(lift),
        permutation_p=None if p_value is None else str(p_value),
        days=len({row.trade.day for row in paired}),
        checks=checks,
        symbol_lifts={key: str(value) for key, value in sorted(symbol_lifts.items())},
        outside_lift=None if outside_lift is None else str(outside_lift),
        baseline_draws=cfg.baseline_draws,
        permutations=cfg.permutations,
    )


class _Hit:
    __slots__ = ("clock", "day", "hit", "side", "symbol")

    def __init__(self, symbol: str, day: date, clock: str, side: str, hit: bool) -> None:
        self.symbol = symbol
        self.day = day
        self.clock = clock
        self.side = side
        self.hit = hit


def _hit(item: Observation, cfg: JudgeThresholds) -> _Hit:
    if item.hit is not None:
        flag = item.hit
    elif item.mfe_r is not None:
        flag = item.mfe_r >= cfg.judged_r
    else:
        flag = False
    return _Hit(item.symbol, item.day, item.clock, item.side, flag)


def _is_outside(item: Observation, design: set[str], outside: set[str]) -> bool:
    if item.group == "outside":
        return True
    if item.group == "design":
        return False
    if item.symbol in outside:
        return True
    return item.symbol not in design


def _pools(
    baseline: list[BaselineObservation],
) -> dict[tuple[str, str, str], list[BaselineObservation]]:
    pools: dict[tuple[str, str, str], list[BaselineObservation]] = defaultdict(list)
    for item in baseline:
        pools[(item.symbol, item.clock, item.side)].append(item)
    return pools


class _Paired:
    __slots__ = ("hits", "rate", "trade")

    def __init__(self, trade: _Hit, rate: Fraction, hits: list[bool]) -> None:
        self.trade = trade
        self.rate = rate
        self.hits = hits


def _pair(
    trades: list[_Hit],
    pools: dict[tuple[str, str, str], list[BaselineObservation]],
    rng: random.Random,
    draws: int,
) -> tuple[list[_Paired], int]:
    paired: list[_Paired] = []
    missing = 0
    for trade in trades:
        pool = [
            item
            for item in pools.get((trade.symbol, trade.clock, trade.side), [])
            if item.day != trade.day
        ]
        if not pool:
            missing += 1
            continue
        chosen = pool if len(pool) <= draws else rng.sample(pool, draws)
        hits = [item.hit for item in chosen]
        rate = Fraction(sum(1 for hit in hits if hit), len(hits))
        paired.append(_Paired(trade, rate, hits))
    return paired, missing


def _day_rate(flags: list[bool]) -> Fraction:
    return Fraction(sum(1 for flag in flags if flag), len(flags))


def _mean_days(values: dict[date, list[Fraction]]) -> Fraction | None:
    if not values:
        return None
    total = sum((sum(day, Fraction(0)) / len(day) for day in values.values()), Fraction(0))
    return total / len(values)


def _lift(
    paired: list[_Paired],
) -> tuple[Fraction | None, Fraction | None, Fraction | None]:
    if not paired:
        return None, None, None
    setup: dict[date, list[bool]] = defaultdict(list)
    base: dict[date, list[Fraction]] = defaultdict(list)
    for row in paired:
        setup[row.trade.day].append(row.trade.hit)
        base[row.trade.day].append(row.rate)
    setup_rate = sum((_day_rate(flags) for flags in setup.values()), Fraction(0)) / len(setup)
    base_rate = _mean_days(base)
    if base_rate is None:
        return None, setup_rate, None
    return setup_rate - base_rate, setup_rate, base_rate


def _symbol_lifts(paired: list[_Paired]) -> dict[str, Fraction]:
    by_symbol: dict[str, list[_Paired]] = defaultdict(list)
    for item in paired:
        by_symbol[item.trade.symbol].append(item)
    lifts: dict[str, Fraction] = {}
    for symbol, rows in by_symbol.items():
        lift, _, _ = _lift(rows)
        if lift is not None:
            lifts[symbol] = lift
    return lifts


def _permutation(paired: list[_Paired], rng: random.Random, draws: int) -> Fraction:
    """Share of baseline redraws whose day-averaged hit rate matches or beats the setup.

    Each draw picks one hit, with replacement, from that trade's clock-baseline pool.
    """
    setup_days: dict[date, list[bool]] = defaultdict(list)
    for row in paired:
        setup_days[row.trade.day].append(row.trade.hit)
    setup_rate = sum((_day_rate(flags) for flags in setup_days.values()), Fraction(0)) / len(
        setup_days
    )
    wins = 0
    for _ in range(draws):
        by_day: dict[date, list[bool]] = defaultdict(list)
        for row in paired:
            by_day[row.trade.day].append(row.hits[rng.randrange(len(row.hits))])
        sampled = sum((_day_rate(flags) for flags in by_day.values()), Fraction(0)) / len(by_day)
        if sampled >= setup_rate:
            wins += 1
    return Fraction(wins, draws)


def _checks(
    cfg: JudgeThresholds,
    *,
    lift: Fraction | None,
    days: int,
    symbol_lifts: dict[str, Fraction],
    p_value: Fraction | None,
    outside_lift: Fraction | None,
    outside_n: int,
    missing: int,
    outside_missing: int,
    setup_rate: Fraction | None,
    base_rate: Fraction | None,
) -> list[CheckResult]:
    min_lift = Fraction(cfg.min_lift)
    lift_ok = lift is not None and missing == 0 and lift >= min_lift
    lift_detail = (
        "no setup trades with a clock baseline"
        if lift is None
        else f"lift {lift} (setup {setup_rate} vs baseline {base_rate})"
    )
    if missing:
        lift_detail += f"; {missing} trades had no baseline pool"
    symbols = list(symbol_lifts)
    positive = sum(1 for value in symbol_lifts.values() if value > 0)
    breadth = Fraction(positive, len(symbols)) if symbols else Fraction(0)
    breadth_ok = bool(symbols) and breadth >= Fraction(cfg.min_symbol_positive_frac)
    p_decimal = (
        None
        if p_value is None
        else Decimal(p_value.numerator) / Decimal(p_value.denominator)
    )
    p_ok = p_decimal is not None and p_decimal <= cfg.p_bar
    outside_ok = (
        outside_n > 0 and outside_missing == 0 and outside_lift is not None and outside_lift > 0
    )
    outside_detail = (
        "no outside symbol group"
        if outside_n == 0
        else f"outside lift {outside_lift} on {outside_n} trades"
    )
    if outside_missing:
        outside_detail += f"; {outside_missing} had no baseline pool"
    return [
        CheckResult(name="lift_vs_baseline", passed=lift_ok, detail=lift_detail),
        CheckResult(
            name="min_days",
            passed=days >= cfg.min_days,
            detail=f"{days} distinct days (bar {cfg.min_days})",
        ),
        CheckResult(
            name="symbol_breadth",
            passed=breadth_ok,
            detail=(
                f"{positive} of {len(symbols)} symbols have positive lift "
                f"(need {cfg.min_symbol_positive_frac})"
            ),
        ),
        CheckResult(
            name="permutation_p",
            passed=p_ok,
            detail="no permutation (baseline missing)"
            if p_value is None
            else f"p {p_value} (bar {cfg.p_bar}, k={cfg.k})",
        ),
        CheckResult(name="outside_group", passed=outside_ok, detail=outside_detail),
    ]
