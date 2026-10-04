"""Percentile bootstrap confidence intervals (design §20, Req 20.12).

:func:`bootstrap_intervals` resamples one run's accepted trades with
replacement and reports 95% percentile intervals for the Primary_Win_Rate and
for expectancy in R.

- Definitions come from :func:`fse.analytics.metrics.summarize`. Each trade's
  Primary_Win_Rate (100 or 0) and expectancy in R (its R_Multiple) are what
  ``summarize`` reports for that trade alone, so a resample's statistic is
  what ``summarize`` reports for the resampled trades. Shadow_Trades are
  dropped, as in ``summarize``.
- Trades are put in ``summarize``'s exit-time order first, so the draws do not
  depend on the order the trades are given in.
- Draws come from ``numpy.random.Generator(numpy.random.PCG64(seed))``. The
  indices equal one ``rng.integers(0, n, size=(resamples, n))`` call, where
  row ``i`` is resample ``i``; they are drawn in blocks of rows only to bound
  memory. The same trades, seed and resample count give identical bounds
  (Property 64). The result carries the seed and the resample count, which
  the Run_Manifest records.
- The bounds are the 2.5th and 97.5th percentiles of the resample
  statistics, interpolated linearly between order statistics: level ``q`` of
  ``B`` sorted values sits at position ``q x (B - 1)``, as in
  :func:`fse.analytics.metrics.quantiles`. Statistics and bounds are float64.
- Undefined intervals are ``NotApplicable`` (Req 20.16): both, for a run
  with no accepted trades; the Primary_Win_Rate interval, when it is win
  rate (c) and the Exit_Mode sets no first target. One trade gives a
  zero-width interval.

The function is pure apart from its seeded generator: no I/O and no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import numpy as np
import numpy.typing as npt

from fse.analytics.metrics import MetricsCfg, PrimaryWinRate, summarize
from fse.config.schema.bootstrap import (
    BOOTSTRAP_RESAMPLES_DEFAULT,
    BOOTSTRAP_RESAMPLES_MAX,
    BOOTSTRAP_RESAMPLES_MIN,
)
from fse.engine.types import NotApplicable, Trade

__all__ = [
    "CONFIDENCE_PCT",
    "PERCENTILE_LEVELS",
    "BootstrapError",
    "BootstrapIntervals",
    "Interval",
    "bootstrap_intervals",
]

CONFIDENCE_PCT: Final = 95
"""The confidence level of every interval, in percent (Req 20.12)."""

PERCENTILE_LEVELS: Final[tuple[float, float]] = (0.025, 0.975)
"""The percentiles of the resample statistics that bound a 95% interval."""

_NA: Final = NotApplicable()
_HUNDRED: Final = Fraction(100)
# Index cells drawn per block: 8 MiB of int64 indices, whatever the trade count.
_BLOCK_CELLS: Final = 1 << 20


class BootstrapError(ValueError):
    """The resample count or seed given to :func:`bootstrap_intervals` is invalid."""


@dataclass(frozen=True, slots=True)
class Interval:
    """A closed interval from ``lower`` to ``upper``, with ``lower <= upper``."""

    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class BootstrapIntervals:
    """The 95% percentile bootstrap intervals of one run (Req 20.12, 20.13).

    ``seed`` and ``resamples`` are the values the intervals were drawn with,
    for the Run_Manifest. ``primary_win_rate_pct`` is in percent (0 to 100)
    and ``expectancy_r`` in R. ``low_sample`` is true when the run has fewer
    accepted trades than ``MetricsCfg.min_sample_trades``; reports then label
    both intervals low-sample.
    """

    seed: int
    resamples: int
    trade_count: int
    primary_win_rate: PrimaryWinRate
    primary_win_rate_pct: Interval | NotApplicable
    expectancy_r: Interval | NotApplicable
    low_sample: bool
    confidence_pct: int = CONFIDENCE_PCT


def bootstrap_intervals(
    trades: Iterable[Trade],
    cfg: MetricsCfg,
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES_DEFAULT,
) -> BootstrapIntervals:
    """95% percentile bootstrap intervals for the Primary_Win_Rate and expectancy in R.

    ``cfg`` is the run's :class:`MetricsCfg`, the one given to ``summarize``.
    ``seed`` is the run's seed, a whole number of at least 0. ``resamples``
    is ``reporting.bootstrap_resamples``, 1,000 to 100,000.

    Raises ``BootstrapError`` for a resample count or seed out of range.
    """
    _check_resamples(resamples)
    _check_seed(seed)
    accepted = sorted((t for t in trades if not t.shadow), key=_exit_order)
    n = len(accepted)
    low_sample = n < cfg.min_sample_trades
    if n == 0:
        return BootstrapIntervals(
            seed, resamples, 0, cfg.primary_win_rate, _NA, _NA, low_sample=low_sample
        )

    wins, r_values = _per_trade(accepted, cfg)
    rng = np.random.Generator(np.random.PCG64(seed))
    win_stats = np.empty(resamples, dtype=np.float64)
    r_stats = np.empty(resamples, dtype=np.float64)
    rows = max(1, _BLOCK_CELLS // n)
    for start in range(0, resamples, rows):
        stop = min(start + rows, resamples)
        idx = rng.integers(0, n, size=(stop - start, n))
        r_stats[start:stop] = r_values[idx].mean(axis=1)
        if wins is not None:
            win_stats[start:stop] = 100.0 * np.count_nonzero(wins[idx], axis=1) / n

    return BootstrapIntervals(
        seed=seed,
        resamples=resamples,
        trade_count=n,
        primary_win_rate=cfg.primary_win_rate,
        primary_win_rate_pct=_NA if wins is None else _interval(win_stats),
        expectancy_r=_interval(r_stats),
        low_sample=low_sample,
    )


# ---------------------------------------------------------------- internals


def _check_resamples(resamples: int) -> None:
    if (
        isinstance(resamples, bool)
        or not isinstance(resamples, int)
        or not BOOTSTRAP_RESAMPLES_MIN <= resamples <= BOOTSTRAP_RESAMPLES_MAX
    ):
        raise BootstrapError(
            f"resamples must be a whole number from {BOOTSTRAP_RESAMPLES_MIN:,} to "
            f"{BOOTSTRAP_RESAMPLES_MAX:,}, got {resamples!r}"
        )


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise BootstrapError(f"seed must be a whole number of at least 0, got {seed!r}")


def _exit_order(t: Trade) -> tuple[int, int]:
    """``summarize``'s trade order: the last exit fill's bar, then the entry fill's."""
    return max(f.bar_open_ns for f in t.exits), t.entry_fill.bar_open_ns


def _per_trade(
    accepted: list[Trade], cfg: MetricsCfg
) -> tuple[npt.NDArray[np.bool_] | None, npt.NDArray[np.float64]]:
    """Each trade's Primary_Win_Rate win flag and R, from ``summarize`` on that trade alone.

    The flags are ``None`` when the Primary_Win_Rate is not applicable, which
    depends only on ``cfg`` (win rate (c) with no first target).
    """
    flags: list[bool] = []
    applicable = True
    r_values: list[float] = []
    for t in accepted:
        m = summarize((t,), (t.setup_key.session,), cfg)
        win, r = m.primary_win_rate_pct, m.expectancy_r
        if isinstance(r, NotApplicable):  # pragma: no cover - one accepted trade has an R
            raise AssertionError(f"summarize gave no expectancy for trade {t}")
        r_values.append(float(r))
        if isinstance(win, NotApplicable):
            applicable = False
        else:
            flags.append(win == _HUNDRED)
    wins = np.array(flags, dtype=np.bool_) if applicable else None
    return wins, np.array(r_values, dtype=np.float64)


def _interval(stats: npt.NDArray[np.float64]) -> Interval:
    lower, upper = np.quantile(stats, PERCENTILE_LEVELS, method="linear")
    return Interval(float(lower), float(upper))
