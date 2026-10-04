"""Run metrics: counts, expectancy, drawdowns, win rates and excursions (design §20).

:func:`summarize` turns the closed trades of one Backtester run into a
:class:`Metrics` record (Req 20.1-20.6, 20.13, 20.16). It is pure: no I/O, no
clock and no randomness. Bootstrap intervals (Req 20.12) live in
``fse.analytics.bootstrap``.

Every value is exact:

- dollar and R sums, drawdowns and excursion quantiles are ``Decimal``,
  computed in a wide context that raises on any rounding;
- means, ratios and percentages are ``Fraction``. :func:`round_fraction` and
  :func:`metrics_to_jsonable` round them only for display.

An undefined value is ``NotApplicable`` (Req 20.16), never 0 or infinity:
averages with no trades to average, a profit factor with a gross loss of $0,
win rates of a run with zero trades, and win rate (c) when the Exit_Mode sets
no first target. A zero-trade run still reports a trade count of 0 and 0
trades per day.

Shared conventions:

- Only accepted trades count. Shadow_Trades in the input are dropped
  (Req 20.1, 19.9), and a trade with partial exits is one trade.
- Trades are ordered by exit time: the ``bar_open_ns`` of their last exit
  fill, then their entry fill's, then input order. The longest losing streak
  and both drawdowns use this order.
- A trade belongs to the session of its Setup_Key.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow
from fractions import Fraction
from typing import Final, Literal

from fse.engine.types import NotApplicable, Trade
from fse.logio.canonical_json import JsonValue, to_jsonable

__all__ = [
    "LOW_SAMPLE_FIELDS",
    "MIN_SAMPLE_TRADES_DEFAULT",
    "QUANTILE_LEVELS",
    "SCRATCH_TOLERANCE_R_DEFAULT",
    "Metrics",
    "MetricsCfg",
    "MetricsError",
    "PrimaryWinRate",
    "Quantiles",
    "metrics_to_jsonable",
    "quantiles",
    "round_fraction",
    "summarize",
]

type PrimaryWinRate = Literal["a", "b", "c"]
"""Which win rate of Req 20.4 is the Primary_Win_Rate (``reporting.primary_win_rate``)."""

SCRATCH_TOLERANCE_R_DEFAULT: Final = Decimal("0.1")
MIN_SAMPLE_TRADES_DEFAULT: Final = 30

QUANTILE_LEVELS: Final[tuple[Decimal, ...]] = (
    Decimal(0),
    Decimal("0.25"),
    Decimal("0.5"),
    Decimal("0.75"),
    Decimal("0.9"),
    Decimal(1),
)
"""The levels of :class:`Quantiles`: minimum, P25, median, P75, P90 and maximum."""

LOW_SAMPLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "win_rate_a_pct",
        "win_rate_b_pct",
        "win_rate_c_pct",
        "primary_win_rate_pct",
        "break_even_win_rate_pct",
        "expectancy_r",
        "expectancy_usd",
    }
)
"""The :class:`Metrics` fields that carry the low-sample label (Req 20.13)."""

_PRIMARY: Final = frozenset({"a", "b", "c"})
_NA: Final = NotApplicable()
_ZERO: Final = Decimal(0)
_HUNDRED: Final = 100

# Sums and products of Decimals are exact here, or raise: nothing rounds silently.
# This context never divides, so the wide precision costs nothing.
_EXACT: Final = Context(prec=10_000, traps=[Inexact, InvalidOperation, DivisionByZero, Overflow])


class MetricsError(ValueError):
    """The trades, sessions or settings given to :func:`summarize` are inconsistent."""


@dataclass(frozen=True, slots=True)
class MetricsCfg:
    """The settings :func:`summarize` reads.

    The caller builds one from ``reporting.*`` and the run's Exit_Mode:

    - ``scratch_tolerance_r``: win rate (b) counts a trade whose net P&L is at
      or above minus this multiple of the trade's R (Req 20.4).
    - ``min_sample_trades``: fewer trades than this make the run low-sample
      (Req 20.13).
    - ``primary_win_rate``: which of win rates (a), (b) and (c) is the
      Primary_Win_Rate.
    - ``has_first_target``: ``False`` when the run's Exit_Mode sets no first
      target (TP1), as with Trailing; win rate (c) is then not applicable
      (Req 20.16).
    """

    scratch_tolerance_r: Decimal = SCRATCH_TOLERANCE_R_DEFAULT
    min_sample_trades: int = MIN_SAMPLE_TRADES_DEFAULT
    primary_win_rate: PrimaryWinRate = "a"
    has_first_target: bool = True

    def __post_init__(self) -> None:
        tolerance = self.scratch_tolerance_r
        if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
            raise MetricsError(
                f"scratch_tolerance_r must be a finite Decimal of at least 0, got {tolerance!r}"
            )
        minimum = self.min_sample_trades
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise MetricsError(
                f"min_sample_trades must be an integer of at least 1, got {minimum!r}"
            )
        if self.primary_win_rate not in _PRIMARY:
            raise MetricsError(
                f"primary_win_rate must be one of a, b, c, got {self.primary_win_rate!r}"
            )
        if not isinstance(self.has_first_target, bool):
            raise MetricsError(f"has_first_target must be a bool, got {self.has_first_target!r}")


@dataclass(frozen=True, slots=True)
class Quantiles:
    """Minimum, 25th, 50th, 75th and 90th percentiles and maximum, in R (Req 20.6).

    Percentiles interpolate linearly between order statistics: level ``q`` of
    ``n`` sorted values sits at position ``q x (n - 1)``. Each value is exact,
    since every level has at most two decimal places.
    """

    minimum: Decimal
    p25: Decimal
    median: Decimal
    p75: Decimal
    p90: Decimal
    maximum: Decimal


@dataclass(frozen=True, slots=True)
class Metrics:
    """The metrics of one run's accepted trades (Req 20.1-20.6, 20.13, 20.16).

    Rates and shares are percentages (0 to 100). ``gross_loss_usd`` is the sum
    of the losing trades' net P&L, so it is 0 or below. Both drawdowns are 0 or
    above. ``NotApplicable`` marks every undefined value.
    """

    # Req 20.1
    trade_count: int
    session_count: int
    trades_per_day: Fraction
    sessions_with_trade_pct: Fraction | NotApplicable
    longest_losing_streak: int
    # Req 20.2
    win_count: int
    loss_count: int
    avg_win_r: Fraction | NotApplicable
    avg_loss_r: Fraction | NotApplicable
    expectancy_r: Fraction | NotApplicable
    expectancy_usd: Fraction | NotApplicable
    gross_profit_usd: Decimal
    gross_loss_usd: Decimal
    profit_factor: Fraction | NotApplicable
    # Req 20.3
    max_drawdown_usd: Decimal | NotApplicable
    max_drawdown_r: Decimal | NotApplicable
    # Req 20.4 and 20.5
    win_rate_a_pct: Fraction | NotApplicable
    win_rate_b_pct: Fraction | NotApplicable
    win_rate_c_pct: Fraction | NotApplicable
    primary_win_rate: PrimaryWinRate
    primary_win_rate_pct: Fraction | NotApplicable
    break_even_win_rate_pct: Fraction | NotApplicable
    # Req 20.6
    mae_r: Quantiles | NotApplicable
    mfe_r: Quantiles | NotApplicable
    # Req 20.13
    low_sample: bool

    def is_low_sample(self, field: str) -> bool:
        """Whether the report labels ``field`` low-sample (Req 20.13)."""
        if field not in _FIELD_NAMES:
            raise MetricsError(f"Metrics has no field {field!r}")
        return self.low_sample and field in LOW_SAMPLE_FIELDS


_FIELD_NAMES: Final = frozenset(f.name for f in dataclasses.fields(Metrics))


# ---------------------------------------------------------------- summarize


def summarize(trades: Iterable[Trade], sessions: Collection[date], cfg: MetricsCfg) -> Metrics:
    """The metrics of the accepted trades of a run over ``sessions``.

    ``sessions`` are the run's evaluated sessions, with or without trades, so
    trades per day and the share of sessions with a trade use the full
    session count (Req 20.1). Shadow_Trades in ``trades`` are ignored.

    Raises ``MetricsError`` when a session is not a date or is repeated, or
    an accepted trade's session is not in ``sessions``.
    """
    session_set = _session_set(sessions)
    accepted = [t for t in trades if not t.shadow]
    for t in accepted:
        if t.setup_key.session not in session_set:
            raise MetricsError(
                f"trade {t.entry_fill.client_id} is in session {t.setup_key.session}, "
                "which is not among the run's sessions"
            )
    ordered = sorted(accepted, key=_exit_order)
    n = len(ordered)
    n_sessions = len(session_set)

    wins = [t for t in ordered if t.net > 0]
    losses = [t for t in ordered if t.net < 0]
    avg_win_r = _mean([t.r_multiple for t in wins])
    avg_loss_r = _mean([t.r_multiple for t in losses])
    gross_profit = _sum(t.net for t in wins)
    gross_loss = _sum(t.net for t in losses)

    win_rate_a = _pct(len(wins), n)
    win_rate_b = _pct(sum(1 for t in ordered if _is_scratch_win(t, cfg.scratch_tolerance_r)), n)
    win_rate_c = _pct(sum(1 for t in ordered if t.reached_tp1), n) if cfg.has_first_target else _NA
    primary = {"a": win_rate_a, "b": win_rate_b, "c": win_rate_c}[cfg.primary_win_rate]

    return Metrics(
        trade_count=n,
        session_count=n_sessions,
        trades_per_day=Fraction(n, n_sessions) if n else Fraction(0),
        sessions_with_trade_pct=_pct(len({t.setup_key.session for t in ordered}), n_sessions),
        longest_losing_streak=_longest_losing_streak(ordered),
        win_count=len(wins),
        loss_count=len(losses),
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        expectancy_r=_mean([t.r_multiple for t in ordered]),
        expectancy_usd=_mean([t.net for t in ordered]),
        gross_profit_usd=gross_profit,
        gross_loss_usd=gross_loss,
        profit_factor=Fraction(gross_profit) / -Fraction(gross_loss) if gross_loss < 0 else _NA,
        max_drawdown_usd=_max_drawdown([t.net for t in ordered]),
        max_drawdown_r=_max_drawdown([t.r_multiple for t in ordered]),
        win_rate_a_pct=win_rate_a,
        win_rate_b_pct=win_rate_b,
        win_rate_c_pct=win_rate_c,
        primary_win_rate=cfg.primary_win_rate,
        primary_win_rate_pct=primary,
        break_even_win_rate_pct=_break_even_pct(avg_win_r, avg_loss_r),
        mae_r=quantiles([t.mae_r for t in ordered]),
        mfe_r=quantiles([t.mfe_r for t in ordered]),
        low_sample=n < cfg.min_sample_trades,
    )


def quantiles(values: Iterable[Decimal]) -> Quantiles | NotApplicable:
    """The :data:`QUANTILE_LEVELS` of ``values``, or ``NotApplicable`` when empty."""
    xs = sorted(values)
    if not xs:
        return _NA
    last = len(xs) - 1
    points: list[Decimal] = []
    for level in QUANTILE_LEVELS:
        position = _EXACT.multiply(Decimal(last), level)
        lo = int(position)
        weight = _EXACT.subtract(position, Decimal(lo))
        if lo == last or weight == 0:
            points.append(xs[lo])
        else:
            step = _EXACT.multiply(_EXACT.subtract(xs[lo + 1], xs[lo]), weight)
            points.append(_EXACT.add(xs[lo], step))
    return Quantiles(*points)


# ---------------------------------------------------------------- display


def round_fraction(value: Fraction, places: int) -> Decimal:
    """``value`` rounded half-even to ``places`` decimal places, for display only."""
    if isinstance(places, bool) or not isinstance(places, int) or places < 0:
        raise MetricsError(f"places must be an integer of at least 0, got {places!r}")
    scaled = round(value * 10**places)
    return Decimal(scaled).scaleb(-places, _EXACT)


def metrics_to_jsonable(metrics: Metrics, *, places: int = 6) -> JsonValue:
    """``metrics`` as canonical JSON values, with each ``Fraction`` rounded to ``places``.

    ``Decimal`` values keep their exact digits, and ``NotApplicable`` encodes
    as the empty object of every sentinel (see ``fse.engine.types``).
    """
    plain: dict[str, object] = {}
    for f in dataclasses.fields(metrics):
        value = getattr(metrics, f.name)
        plain[f.name] = round_fraction(value, places) if isinstance(value, Fraction) else value
    return to_jsonable(plain)


# ---------------------------------------------------------------- internals


def _session_set(sessions: Collection[date]) -> frozenset[date]:
    out: set[date] = set()
    for d in sessions:
        if isinstance(d, datetime) or not isinstance(d, date):
            raise MetricsError(f"sessions must be dates, got {d!r}")
        if d in out:
            raise MetricsError(f"session {d} is listed twice")
        out.add(d)
    return frozenset(out)


def _exit_order(t: Trade) -> tuple[int, int]:
    return max(f.bar_open_ns for f in t.exits), t.entry_fill.bar_open_ns


def _sum(values: Iterable[Decimal]) -> Decimal:
    total = _ZERO
    for v in values:
        total = _EXACT.add(total, v)
    return total


def _mean(values: Sequence[Decimal]) -> Fraction | NotApplicable:
    if not values:
        return _NA
    return sum((Fraction(v) for v in values), Fraction(0)) / len(values)


def _pct(count: int, total: int) -> Fraction | NotApplicable:
    return Fraction(_HUNDRED * count, total) if total else _NA


def _is_scratch_win(t: Trade, tolerance_r: Decimal) -> bool:
    """Win rate (b): net P&L at or above minus ``tolerance_r`` times the trade's R."""
    return Fraction(t.net) >= -Fraction(tolerance_r) * Fraction(t.r)


def _longest_losing_streak(ordered: Sequence[Trade]) -> int:
    longest = run = 0
    for t in ordered:
        run = run + 1 if t.net < 0 else 0
        longest = max(longest, run)
    return longest


def _max_drawdown(values: Sequence[Decimal]) -> Decimal | NotApplicable:
    """Largest peak-to-trough fall of the running sum, starting from a peak of 0."""
    if not values:
        return _NA
    cumulative = peak = drawdown = _ZERO
    for v in values:
        cumulative = _EXACT.add(cumulative, v)
        peak = max(peak, cumulative)
        drawdown = max(drawdown, _EXACT.subtract(peak, cumulative))
    return drawdown


def _break_even_pct(
    avg_win_r: Fraction | NotApplicable, avg_loss_r: Fraction | NotApplicable
) -> Fraction | NotApplicable:
    """``|avg loss| / (avg win + |avg loss|)`` as a percentage (Req 20.5)."""
    if isinstance(avg_win_r, NotApplicable) or isinstance(avg_loss_r, NotApplicable):
        return _NA
    denominator = avg_win_r + abs(avg_loss_r)
    if denominator == 0:
        return _NA
    return _HUNDRED * abs(avg_loss_r) / denominator
