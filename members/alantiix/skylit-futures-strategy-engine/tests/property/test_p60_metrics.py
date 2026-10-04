"""Property 60: Metrics match the reference model.

*For any* list of trades and session count, trade counts, trades per day,
longest losing streak, average win and loss in R, expectancy in R and
dollars, profit factor, maximum drawdown (dollars and R, non-negative), the
three win rates (with rate (a) <= rate (b)), break-even win rate and MAE and
MFE quantiles (non-negative and non-decreasing across quantiles) equal a
reference computation, and every undefined value is not applicable rather
than 0 or infinity.

The reference model below is deliberately plain: exact ``Fraction``
arithmetic over every input, brute-force drawdowns (every peak/trough pair)
and streaks (every start), and percentiles by linear interpolation between
order statistics at ``q x (n - 1)``. It follows the conventions of
``fse.analytics.metrics``: Shadow_Trades are dropped, trades are ordered by
the time of their last exit fill (ties by entry fill, then input order), a
trade belongs to the session of its Setup_Key, and a zero-trade run reports
counts of 0, sums of $0 and 0 trades per day with every mean, rate, ratio,
drawdown and quantile not applicable.

Trades are built like the Fill_Simulator builds them: R from the risk in
ticks, the tick value and the entry contracts, and ``r_multiple``,
``mae_r`` and ``mfe_r`` as 34-digit quotients, so the R figures are often
non-terminating decimals.

**Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.16**
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from fractions import Fraction
from itertools import pairwise
from types import MappingProxyType
from typing import Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.analytics.metrics import (
    Metrics,
    MetricsCfg,
    PrimaryWinRate,
    Quantiles,
    summarize,
)
from fse.engine.types import Direction, Fill, NotApplicable, SetupKey, Trade
from fse.timekit import NS_PER_MINUTE, Instant

NA: Final = NotApplicable()
T0: Final[Instant] = 1_767_600_000 * 1_000_000_000
"""Any instant: only the order of fill times matters."""
FIRST_SESSION: Final = date(2026, 1, 5)
TICK_USD: Final[Mapping[str, Decimal]] = MappingProxyType(
    {
        "MES": Decimal("1.25"),
        "MNQ": Decimal("0.50"),
        "ES": Decimal("12.50"),
        "NQ": Decimal("5.00"),
    }
)
"""Dollars per tick per contract (Req 13.9)."""
INSTRUMENTS: Final[tuple[str, ...]] = tuple(TICK_USD)
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
PRIMARY: Final[tuple[PrimaryWinRate, ...]] = ("a", "b", "c")
ENTRY_PRICE: Final = 23_000
QUOTIENT: Final = Context(
    prec=34, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero, Overflow]
)
"""The Fill_Simulator's context for R_Multiple and excursions in R."""

LEVELS: Final[tuple[Fraction, ...]] = (
    Fraction(0),
    Fraction(1, 4),
    Fraction(1, 2),
    Fraction(3, 4),
    Fraction(9, 10),
    Fraction(1),
)
"""Minimum, P25, median, P75, P90 and maximum (Req 20.6)."""

type Exact = int | str | Fraction | NotApplicable | tuple[Fraction, ...]
"""A metric in exact form: counts, the primary letter, a number, n/a or quantiles."""


@dataclass(frozen=True, slots=True)
class Case:
    """The input to ``summarize``: trades (shadows included), sessions and settings."""

    trades: tuple[Trade, ...]
    sessions: tuple[date, ...]
    cfg: MetricsCfg


# ---------------------------------------------------------------- generators


def _cents(n: int) -> Decimal:
    return Decimal(n).scaleb(-2)


def minute_ns(minute: int) -> Instant:
    return T0 + minute * NS_PER_MINUTE


TOLERANCES: Final = st.one_of(st.just(Decimal("0.1")), st.integers(0, 100).map(_cents))
CFGS: Final = st.builds(
    MetricsCfg,
    scratch_tolerance_r=TOLERANCES,
    min_sample_trades=st.integers(1, 50),
    primary_win_rate=st.sampled_from(PRIMARY),
    has_first_target=st.booleans(),
)


def nets(r: Decimal, tolerance: Decimal) -> st.SearchStrategy[Decimal]:
    """Net P&L: any amount, breakeven, a full stop-out, or at the scratch-win line +- 1 cent."""
    return st.one_of(
        st.integers(-300_000, 300_000).map(_cents),
        st.sampled_from((Decimal("0.00"), Decimal("-0.00"))),
        st.just(-r),
        st.integers(-1, 1).map(lambda c: -tolerance * r + _cents(c)),
    )


@st.composite
def trades(draw: st.DrawFn, sessions: Sequence[date], tolerance: Decimal, seq: int) -> Trade:
    instrument = draw(st.sampled_from(INSTRUMENTS))
    direction = draw(st.sampled_from(DIRECTIONS))
    qty = draw(st.integers(1, 5))
    risk = draw(st.integers(1, 40))  # ticks from the entry fill to the initial stop
    r = Decimal(risk * qty) * TICK_USD[instrument]
    net = draw(nets(r, tolerance))

    # Exits split the entry contracts; small time ranges give many exit-time ties.
    n_exits = draw(st.integers(1, min(qty, 3)))
    left, parts = qty, []
    for i in range(n_exits - 1):
        part = draw(st.integers(1, left - (n_exits - 1 - i)))
        parts.append(part)
        left -= part
    parts.append(left)
    entry_minute = draw(st.integers(0, 6))
    exit_minutes = sorted(entry_minute + draw(st.integers(0, 6)) for _ in parts)

    sign = 1 if direction == "long" else -1
    key = SetupKey(
        instrument, "gatekeeper_fade", 5800.0, direction, draw(st.sampled_from(sessions)), seq + 1
    )
    zero = Decimal("0.00")
    entry = Fill(f"t{seq}:entry", minute_ns(entry_minute), ENTRY_PRICE, qty, zero)
    exits = tuple(
        Fill(f"t{seq}:x{i}", minute_ns(m), ENTRY_PRICE, part, zero)
        for i, (m, part) in enumerate(zip(exit_minutes, parts, strict=True))
    )
    adverse = draw(st.integers(0, 3 * risk))
    favorable = draw(st.integers(0, 3 * risk))
    return Trade(
        setup_key=key,
        entry_fill=entry,
        initial_stop=ENTRY_PRICE - sign * risk,
        qty_at_entry=qty,
        exits=exits,
        net=net,
        r=r,
        r_multiple=QUOTIENT.divide(net, r),
        reached_tp1=draw(st.booleans()),
        mae_r=QUOTIENT.divide(Decimal(adverse), Decimal(risk)),
        mfe_r=QUOTIENT.divide(Decimal(favorable), Decimal(risk)),
        missing_bars=0,
        shadow=draw(st.integers(0, 4)) == 0,
    )


@st.composite
def cases(draw: st.DrawFn) -> Case:
    cfg = draw(CFGS)
    offsets = draw(st.lists(st.integers(0, 90), unique=True, max_size=8))
    sessions = tuple(FIRST_SESSION + timedelta(days=k) for k in offsets)
    drawn: list[Trade] = []
    if sessions:
        for seq in range(draw(st.integers(0, 25))):
            drawn.append(draw(trades(sessions, cfg.scratch_tolerance_r, seq)))
    return Case(tuple(drawn), sessions, cfg)


# ---------------------------------------------------------------- reference model


def ref_mean(xs: Sequence[Fraction]) -> Fraction | NotApplicable:
    return sum(xs, Fraction(0)) / len(xs) if xs else NA


def ref_pct(count: int, total: int) -> Fraction | NotApplicable:
    return Fraction(100 * count, total) if total else NA


def ref_longest_run(flags: Sequence[bool]) -> int:
    """The longest run of consecutive ``True`` values, trying every start."""
    best = 0
    for start in range(len(flags)):
        end = start
        while end < len(flags) and flags[end]:
            end += 1
        best = max(best, end - start)
    return best


def ref_max_drawdown(values: Sequence[Fraction]) -> Fraction | NotApplicable:
    """The largest ``cum[i] - cum[j]`` for ``i <= j``; ``cum`` starts at 0 before any trade."""
    if not values:
        return NA
    cum = [Fraction(0)]
    for v in values:
        cum.append(cum[-1] + v)
    return max(cum[i] - cum[j] for j in range(len(cum)) for i in range(j + 1))


def ref_quantiles(values: Sequence[Fraction]) -> tuple[Fraction, ...] | NotApplicable:
    """Each level ``q`` interpolated linearly at position ``q x (n - 1)`` of the sorted values."""
    xs = sorted(values)
    if not xs:
        return NA
    out: list[Fraction] = []
    for q in LEVELS:
        position = q * (len(xs) - 1)
        lo, hi = math.floor(position), math.ceil(position)
        out.append(xs[lo] + (xs[hi] - xs[lo]) * (position - lo))
    return tuple(out)


def reference(case: Case) -> dict[str, Exact]:
    """Every field of ``Metrics`` but ``low_sample``, recomputed exactly."""
    cfg = case.cfg
    accepted = [t for t in case.trades if not t.shadow]
    order = sorted(
        range(len(accepted)),
        key=lambda i: (accepted[i].exits[-1].bar_open_ns, accepted[i].entry_fill.bar_open_ns, i),
    )
    ts = [accepted[i] for i in order]
    n, n_sessions = len(ts), len(case.sessions)

    net = [Fraction(t.net) for t in ts]
    rm = [Fraction(t.r_multiple) for t in ts]
    win_r = [x for x, p in zip(rm, net, strict=True) if p > 0]
    loss_r = [x for x, p in zip(rm, net, strict=True) if p < 0]
    gross_profit = sum((p for p in net if p > 0), Fraction(0))
    gross_loss = sum((p for p in net if p < 0), Fraction(0))
    avg_win, avg_loss = ref_mean(win_r), ref_mean(loss_r)

    tolerance = Fraction(cfg.scratch_tolerance_r)
    rate_a = ref_pct(len(win_r), n)
    rate_b = ref_pct(sum(1 for t in ts if Fraction(t.net) >= -tolerance * Fraction(t.r)), n)
    rate_c = ref_pct(sum(1 for t in ts if t.reached_tp1), n) if cfg.has_first_target else NA
    rates: dict[PrimaryWinRate, Fraction | NotApplicable] = {"a": rate_a, "b": rate_b, "c": rate_c}

    break_even: Fraction | NotApplicable = NA
    if isinstance(avg_win, Fraction) and isinstance(avg_loss, Fraction):
        denominator = avg_win + abs(avg_loss)
        if denominator != 0:
            break_even = 100 * abs(avg_loss) / denominator

    return {
        # Req 20.1
        "trade_count": n,
        "session_count": n_sessions,
        "trades_per_day": Fraction(n, n_sessions) if n else Fraction(0),
        "sessions_with_trade_pct": ref_pct(len({t.setup_key.session for t in ts}), n_sessions),
        "longest_losing_streak": ref_longest_run([p < 0 for p in net]),
        # Req 20.2
        "win_count": len(win_r),
        "loss_count": len(loss_r),
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "expectancy_r": ref_mean(rm),
        "expectancy_usd": ref_mean(net),
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
        "profit_factor": gross_profit / -gross_loss if gross_loss else NA,
        # Req 20.3
        "max_drawdown_usd": ref_max_drawdown(net),
        "max_drawdown_r": ref_max_drawdown(rm),
        # Req 20.4 and 20.5
        "win_rate_a_pct": rate_a,
        "win_rate_b_pct": rate_b,
        "win_rate_c_pct": rate_c,
        "primary_win_rate": cfg.primary_win_rate,
        "primary_win_rate_pct": rates[cfg.primary_win_rate],
        "break_even_win_rate_pct": break_even,
        # Req 20.6
        "mae_r": ref_quantiles([Fraction(t.mae_r) for t in ts]),
        "mfe_r": ref_quantiles([Fraction(t.mfe_r) for t in ts]),
    }


# ---------------------------------------------------------------- exact form


def exact_number(name: str, value: object) -> Fraction:
    """A finite ``Decimal`` or a ``Fraction`` as a ``Fraction``; floats and infinities fail."""
    if isinstance(value, Fraction):
        return value
    assert isinstance(value, Decimal), f"{name} is a {type(value).__name__}, not exact"
    assert value.is_finite(), f"{name} is {value}, not a finite value"
    return Fraction(value)


def exact(name: str, value: object) -> Exact:
    if isinstance(value, (NotApplicable, str)):
        return value
    if isinstance(value, Quantiles):
        return tuple(
            exact_number(f"{name}.{f.name}", getattr(value, f.name))
            for f in dataclasses.fields(value)
        )
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return exact_number(name, value)


def exact_metrics(m: Metrics) -> dict[str, Exact]:
    return {
        f.name: exact(f.name, getattr(m, f.name))
        for f in dataclasses.fields(m)
        if f.name != "low_sample"
    }


# ---------------------------------------------------------------- property


def record_events(case: Case, m: Metrics) -> None:
    accepted = [t for t in case.trades if not t.shadow]
    event(f"accepted trades: {'0' if not accepted else '1-9' if len(accepted) < 10 else '10+'}")
    exit_times = [t.exits[-1].bar_open_ns for t in accepted]
    if len(set(exit_times)) < len(exit_times):
        event("exit-time tie")
    if accepted and isinstance(m.profit_factor, NotApplicable):
        event("no losing trade: profit factor not applicable")
    if accepted and isinstance(m.avg_win_r, NotApplicable):
        event("no winning trade")
    if isinstance(m.win_rate_c_pct, NotApplicable) and accepted:
        event("no first target: win rate (c) not applicable")
    if m.win_rate_a_pct != m.win_rate_b_pct:
        event("scratch wins: rate (b) above rate (a)")


# Feature: skylit-futures-strategy-engine, Property 60: Metrics match the reference model
@given(case=cases())
def test_metrics_match_reference(case: Case) -> None:
    m = summarize(case.trades, case.sessions, case.cfg)
    actual, expected = exact_metrics(m), reference(case)
    assert actual.keys() == expected.keys()
    for name, value in expected.items():
        assert actual[name] == value, f"{name}: summarize gives {actual[name]!r}, not {value!r}"

    # Both drawdowns are 0 or above (Req 20.3).
    for drawdown in (m.max_drawdown_usd, m.max_drawdown_r):
        assert isinstance(drawdown, NotApplicable) or drawdown >= 0
    # Rate (b) counts every rate (a) win, so (a) <= (b) (Req 20.4).
    a, b = m.win_rate_a_pct, m.win_rate_b_pct
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        assert a <= b
    # Excursions are non-negative and the quantiles non-decreasing (Req 20.6).
    for q in (m.mae_r, m.mfe_r):
        if isinstance(q, Quantiles):
            points = (q.minimum, q.p25, q.median, q.p75, q.p90, q.maximum)
            assert points[0] >= 0
            assert all(x <= y for x, y in pairwise(points))
    # A zero-trade run reports 0 trades and 0 trades per day (Req 20.16).
    if m.trade_count == 0:
        assert m.trades_per_day == 0
        assert isinstance(m.expectancy_r, NotApplicable)
        assert isinstance(m.win_rate_a_pct, NotApplicable)

    record_events(case, m)
