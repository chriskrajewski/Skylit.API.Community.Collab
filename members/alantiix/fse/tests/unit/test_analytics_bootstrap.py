"""Unit tests for the bootstrap confidence intervals (``fse.analytics.bootstrap``).

Determinism, the draw contract, bounds, the point estimates inside the
intervals on typical data, resample-count and seed validation, empty and
single-trade runs, and the ``reporting.bootstrap_resamples`` range.

**Validates: Requirements 20.12, 20.13, 20.16**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from fse.analytics.bootstrap import (
    CONFIDENCE_PCT,
    PERCENTILE_LEVELS,
    BootstrapError,
    BootstrapIntervals,
    Interval,
    bootstrap_intervals,
)
from fse.analytics.metrics import MetricsCfg, summarize
from fse.config.schema._base import SchemaModel
from fse.config.schema.bootstrap import (
    BOOTSTRAP_RESAMPLES_DEFAULT,
    BOOTSTRAP_RESAMPLES_MAX,
    BOOTSTRAP_RESAMPLES_MIN,
    BootstrapResamples,
)
from fse.engine.types import Fill, NotApplicable, SetupKey, Trade
from fse.timekit import NS_PER_MINUTE

NA = NotApplicable()
CFG = MetricsCfg()
SEED = 20_260_105
SESSIONS = tuple(date(2026, 1, d) for d in (5, 6, 7, 8, 9))
T0 = 1_767_600_000 * 1_000_000_000  # any instant: only the order of exit times matters


def trade(
    net: str,
    *,
    exit_min: int = 1,
    session: date = SESSIONS[0],
    r: str = "100",
    tp1: bool = False,
    shadow: bool = False,
) -> Trade:
    """A closed one-contract trade with ``r_multiple = net / r``."""
    key = SetupKey("MES", "gatekeeper_fade", 5800.0, "long", session, 1)
    entry = Fill("e", T0, 23120, 1, Decimal("0.00"))
    exit_fill = Fill("x", T0 + exit_min * NS_PER_MINUTE, 23120, 1, Decimal("0.00"))
    net_d, r_d = Decimal(net), Decimal(r)
    return Trade(
        setup_key=key,
        entry_fill=entry,
        initial_stop=23100,
        qty_at_entry=1,
        exits=(exit_fill,),
        net=net_d,
        r=r_d,
        r_multiple=net_d / r_d,
        reached_tp1=tp1,
        mae_r=Decimal(0),
        mfe_r=Decimal(0),
        missing_bars=0,
        shadow=shadow,
    )


NETS = ("150", "-100", "200", "-5", "80", "-50", "120", "-100", "0.00", "60")


def typical_run(n: int = 60) -> list[Trade]:
    """``n`` trades over the five sessions, cycling through :data:`NETS`."""
    return [
        trade(
            NETS[i % len(NETS)],
            exit_min=i + 1,
            session=SESSIONS[i % len(SESSIONS)],
            tp1=NETS[i % len(NETS)] in {"150", "200"},
        )
        for i in range(n)
    ]


def contains(interval: Interval | NotApplicable, value: Fraction | NotApplicable) -> bool:
    assert isinstance(interval, Interval)
    assert isinstance(value, Fraction)
    return interval.lower <= value <= interval.upper


# ---------------------------------------------------------------- determinism


def test_same_seed_gives_identical_intervals_in_any_input_order() -> None:
    trades = typical_run()
    first = bootstrap_intervals(trades, CFG, seed=SEED)
    assert bootstrap_intervals(trades, CFG, seed=SEED) == first
    assert bootstrap_intervals(reversed(trades), CFG, seed=SEED) == first


def test_another_seed_draws_other_resamples() -> None:
    trades = typical_run()
    a = bootstrap_intervals(trades, CFG, seed=1)
    b = bootstrap_intervals(trades, CFG, seed=2)
    assert (a.primary_win_rate_pct, a.expectancy_r) != (b.primary_win_rate_pct, b.expectancy_r)


def test_seed_and_resample_count_are_recorded() -> None:
    result = bootstrap_intervals(typical_run(), CFG, seed=SEED, resamples=1_234)
    assert (result.seed, result.resamples) == (SEED, 1_234)
    assert (result.confidence_pct, result.trade_count) == (CONFIDENCE_PCT, 60)
    assert result.primary_win_rate == "a"
    assert bootstrap_intervals(typical_run(), CFG, seed=0).resamples == 5_000


def test_draws_match_one_full_draw_across_blocks() -> None:
    # 500 trades x 5,000 resamples spans several blocks of drawn indices.
    trades = typical_run(500)
    result = bootstrap_intervals(trades, CFG, seed=SEED)
    ordered = sorted(trades, key=lambda t: t.exits[-1].bar_open_ns)
    r_values = np.array([float(t.r_multiple) for t in ordered])
    wins = np.array([t.net > 0 for t in ordered])
    idx = np.random.Generator(np.random.PCG64(SEED)).integers(0, 500, size=(5_000, 500))
    win_q = np.quantile(100.0 * np.count_nonzero(wins[idx], axis=1) / 500, PERCENTILE_LEVELS)
    r_q = np.quantile(r_values[idx].mean(axis=1), PERCENTILE_LEVELS)
    assert result.primary_win_rate_pct == Interval(float(win_q[0]), float(win_q[1]))
    assert result.expectancy_r == Interval(float(r_q[0]), float(r_q[1]))


# ---------------------------------------------------------------- bounds


def test_bounds_are_ordered_and_within_the_trade_range() -> None:
    trades = typical_run()
    result = bootstrap_intervals(trades, CFG, seed=SEED)
    win, exp = result.primary_win_rate_pct, result.expectancy_r
    assert isinstance(win, Interval)
    assert isinstance(exp, Interval)
    assert 0 <= win.lower <= win.upper <= 100
    r_values = [float(t.r_multiple) for t in trades]
    assert min(r_values) <= exp.lower <= exp.upper <= max(r_values)
    assert win.lower < win.upper  # mixed outcomes: the interval has width


def test_intervals_contain_the_point_estimates() -> None:
    trades = typical_run()
    point = summarize(trades, SESSIONS, CFG)
    for seed in (0, 1, SEED):
        result = bootstrap_intervals(trades, CFG, seed=seed)
        assert contains(result.primary_win_rate_pct, point.primary_win_rate_pct)
        assert contains(result.expectancy_r, point.expectancy_r)


# ---------------------------------------------------------------- definitions


def test_primary_win_rate_follows_the_configured_definition() -> None:
    scratches = [trade("-5", exit_min=i) for i in range(1, 6)]  # -5 >= -0.1 x 100
    a = bootstrap_intervals(scratches, MetricsCfg(primary_win_rate="a"), seed=SEED)
    b = bootstrap_intervals(scratches, MetricsCfg(primary_win_rate="b"), seed=SEED)
    assert a.primary_win_rate_pct == Interval(0.0, 0.0)
    assert b.primary_win_rate_pct == Interval(100.0, 100.0)
    assert b.primary_win_rate == "b"


def test_win_rate_c_without_a_first_target_is_not_applicable() -> None:
    trades = typical_run()
    cfg = MetricsCfg(primary_win_rate="c", has_first_target=False)
    result = bootstrap_intervals(trades, cfg, seed=SEED)
    assert result.primary_win_rate_pct == NA
    assert result.expectancy_r == bootstrap_intervals(trades, CFG, seed=SEED).expectancy_r
    with_tp1 = bootstrap_intervals(trades, MetricsCfg(primary_win_rate="c"), seed=SEED)
    assert contains(with_tp1.primary_win_rate_pct, summarize(trades, SESSIONS, CFG).win_rate_c_pct)


def test_shadow_trades_are_ignored() -> None:
    trades = typical_run()
    with_shadow = [*trades, trade("-9000", exit_min=999, shadow=True)]
    assert bootstrap_intervals(with_shadow, CFG, seed=SEED) == bootstrap_intervals(
        trades, CFG, seed=SEED
    )


# ---------------------------------------------------------------- edge cases


@pytest.mark.parametrize("trades", [[], [trade("-9000", shadow=True)]], ids=["empty", "shadow"])
def test_no_accepted_trades_gives_not_applicable_intervals(trades: list[Trade]) -> None:
    result = bootstrap_intervals(trades, CFG, seed=SEED)
    assert result == BootstrapIntervals(
        seed=SEED,
        resamples=BOOTSTRAP_RESAMPLES_DEFAULT,
        trade_count=0,
        primary_win_rate="a",
        primary_win_rate_pct=NA,
        expectancy_r=NA,
        low_sample=True,
    )


@pytest.mark.parametrize(("net", "win_pct"), [("150", 100.0), ("-100", 0.0)])
def test_one_trade_gives_a_zero_width_interval(net: str, win_pct: float) -> None:
    result = bootstrap_intervals([trade(net)], CFG, seed=SEED)
    r = float(Decimal(net) / 100)
    assert result.primary_win_rate_pct == Interval(win_pct, win_pct)
    assert result.expectancy_r == Interval(r, r)
    assert (result.trade_count, result.low_sample) == (1, True)


@pytest.mark.parametrize(("n", "low"), [(29, True), (30, False)])
def test_low_sample_below_the_minimum(n: int, low: bool) -> None:
    assert bootstrap_intervals(typical_run(n), CFG, seed=SEED).low_sample is low


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("resamples", [BOOTSTRAP_RESAMPLES_MIN, BOOTSTRAP_RESAMPLES_MAX])
def test_resample_count_bounds_are_accepted(resamples: int) -> None:
    result = bootstrap_intervals(
        [trade("150"), trade("-100", exit_min=2)], CFG, seed=SEED, resamples=resamples
    )
    assert result.resamples == resamples


@pytest.mark.parametrize("resamples", [0, 999, 100_001, True, 5_000.0, "5000"])
def test_resample_count_outside_the_range_is_rejected(resamples: Any) -> None:
    with pytest.raises(BootstrapError, match=r"resamples must be .* 1,000 to 100,000"):
        bootstrap_intervals(typical_run(), CFG, seed=SEED, resamples=resamples)


@pytest.mark.parametrize("seed", [-1, True, 1.5, None])
def test_invalid_seed_is_rejected(seed: Any) -> None:
    with pytest.raises(BootstrapError, match="seed must be"):
        bootstrap_intervals(typical_run(), CFG, seed=seed)


# ---------------------------------------------------------------- config key


class ReportingSketch(SchemaModel):
    """How the ``reporting`` section (task 20.1) declares the key."""

    bootstrap_resamples: BootstrapResamples = BOOTSTRAP_RESAMPLES_DEFAULT


def test_config_default_and_range() -> None:
    assert ReportingSketch().bootstrap_resamples == 5_000
    for value in (1_000, 100_000):
        assert ReportingSketch(bootstrap_resamples=value).bootstrap_resamples == value


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (999, "greater_than_equal"),
        (100_001, "less_than_equal"),
        (True, "int_type"),
        ("5000", "int_type"),
        (5_000.0, "int_type"),
    ],
)
def test_config_rejects_out_of_range_and_non_integers(value: object, error: str) -> None:
    with pytest.raises(ValidationError) as info:
        ReportingSketch.model_validate({"bootstrap_resamples": value})
    assert [e["type"] for e in info.value.errors()] == [error]
