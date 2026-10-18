"""Unit tests for the run metrics (``fse.analytics.metrics``).

**Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.13, 20.16**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from fse.analytics.metrics import (
    LOW_SAMPLE_FIELDS,
    Metrics,
    MetricsCfg,
    MetricsError,
    Quantiles,
    metrics_to_jsonable,
    quantiles,
    round_fraction,
    summarize,
)
from fse.engine.types import Fill, NotApplicable, SetupKey, Trade
from fse.logio.canonical_json import dumps
from fse.timekit import NS_PER_MINUTE

F = Fraction
NA = NotApplicable()
CFG = MetricsCfg()
D1, D2, D3, D4, D5 = (date(2026, 1, d) for d in (5, 6, 7, 8, 9))
SESSIONS = (D1, D2, D3, D4, D5)
T0 = 1_767_600_000 * 1_000_000_000  # any instant: only the order of exit times matters


def trade(
    net: str,
    *,
    exit_min: int | Sequence[int] = 1,
    session: date = D1,
    r: str = "100",
    tp1: bool = False,
    mae: str = "0",
    mfe: str = "0",
    shadow: bool = False,
) -> Trade:
    """A closed one-contract-per-exit trade with ``r_multiple = net / r``."""
    exit_mins = [exit_min] if isinstance(exit_min, int) else list(exit_min)
    key = SetupKey("MES", "gatekeeper_fade", 5800.0, "long", session, 1)
    entry = Fill("e", T0, 23120, len(exit_mins), Decimal("0.00"))
    exits = tuple(
        Fill(f"x{i}", T0 + m * NS_PER_MINUTE, 23120, 1, Decimal("0.00"))
        for i, m in enumerate(exit_mins)
    )
    net_d, r_d = Decimal(net), Decimal(r)
    return Trade(
        setup_key=key,
        entry_fill=entry,
        initial_stop=23100,
        qty_at_entry=len(exit_mins),
        exits=exits,
        net=net_d,
        r=r_d,
        r_multiple=net_d / r_d,
        reached_tp1=tp1,
        mae_r=Decimal(mae),
        mfe_r=Decimal(mfe),
        missing_bars=0,
        shadow=shadow,
    )


# ---------------------------------------------------------------- a worked run


def worked_run() -> list[Trade]:
    """Five trades over sessions D1, D2 and D4 of five, given out of exit order."""
    t1 = trade("200", exit_min=1, session=D1, tp1=True, mae="0.5", mfe="2.5")
    t2 = trade("-100", exit_min=2, session=D1, mae="1", mfe="0.25")
    t3 = trade("-5", exit_min=3, session=D2, mae="0.75", mfe="1")  # a scratch: -5 >= -0.1 x 100
    t4 = trade("0.00", exit_min=4, session=D4, mae="0.25", mfe="0.5")
    t5 = trade("300", exit_min=5, session=D4, tp1=True, mae="0", mfe="3")
    big_shadow = trade("-9000", exit_min=6, session=D5, shadow=True)
    return [t5, t3, big_shadow, t1, t4, t2]


def test_worked_run_counts() -> None:
    m = summarize(worked_run(), SESSIONS, CFG)
    assert (m.trade_count, m.session_count) == (5, 5)
    assert m.trades_per_day == 1
    assert m.sessions_with_trade_pct == 60  # D1, D2 and D4
    assert m.longest_losing_streak == 2  # t2, t3; the $0 trade t4 ends the streak
    assert (m.win_count, m.loss_count) == (2, 2)  # the $0 trade is neither


def test_worked_run_expectancy_and_profit_factor() -> None:
    m = summarize(worked_run(), SESSIONS, CFG)
    assert m.avg_win_r == Fraction(5, 2)
    assert m.avg_loss_r == Fraction(-21, 40)  # (-1 - 0.05) / 2
    assert m.expectancy_r == Fraction(79, 100)  # 3.95 / 5
    assert m.expectancy_usd == 79  # 395 / 5
    assert (m.gross_profit_usd, m.gross_loss_usd) == (Decimal(500), Decimal(-105))
    assert m.profit_factor == Fraction(100, 21)


def test_worked_run_drawdowns_and_win_rates() -> None:
    m = summarize(worked_run(), SESSIONS, CFG)
    assert m.max_drawdown_usd == Decimal(105)  # 200 -> 95
    assert m.max_drawdown_r == Decimal("1.05")
    assert (m.win_rate_a_pct, m.win_rate_b_pct, m.win_rate_c_pct) == (F(40), F(80), F(40))
    assert m.primary_win_rate == "a"
    assert m.primary_win_rate_pct == 40
    assert m.break_even_win_rate_pct == Fraction(2100, 121)  # 0.525 / 3.025


def test_worked_run_excursion_quantiles() -> None:
    m = summarize(worked_run(), SESSIONS, CFG)
    d = Decimal
    assert m.mae_r == Quantiles(d("0"), d("0.25"), d("0.5"), d("0.75"), d("0.900"), d("1"))
    assert m.mfe_r == Quantiles(d("0.25"), d("0.5"), d("1"), d("2.5"), d("2.80"), d("3"))
    assert m.low_sample


def test_input_order_does_not_change_the_metrics() -> None:
    trades = worked_run()
    assert summarize(reversed(trades), list(reversed(SESSIONS)), CFG) == summarize(
        trades, SESSIONS, CFG
    )


def test_order_is_by_last_exit_fill() -> None:
    # The partial-exit loser closes last, so the two losers are not consecutive.
    early_loser = trade("-10", exit_min=1)
    partial_loser = trade("-10", exit_min=(2, 9))
    winner = trade("10", exit_min=5)
    m = summarize([partial_loser, early_loser, winner], SESSIONS, CFG)
    assert m.trade_count == 3  # partial exits count once
    assert m.longest_losing_streak == 1
    assert m.max_drawdown_usd == Decimal(10)  # -10, 0, -10


# ---------------------------------------------------------------- not applicable (Req 20.16)

UNDEFINED_WITHOUT_TRADES = (
    "sessions_with_trade_pct",
    "avg_win_r",
    "avg_loss_r",
    "expectancy_r",
    "expectancy_usd",
    "profit_factor",
    "max_drawdown_usd",
    "max_drawdown_r",
    "win_rate_a_pct",
    "win_rate_b_pct",
    "win_rate_c_pct",
    "primary_win_rate_pct",
    "break_even_win_rate_pct",
    "mae_r",
    "mfe_r",
)


def test_zero_trade_run_reports_zero_counts_and_not_applicable_metrics() -> None:
    shadow_only = [trade("50", shadow=True)]
    m = summarize(shadow_only, SESSIONS, CFG)
    assert (m.trade_count, m.trades_per_day, m.longest_losing_streak) == (0, F(0), 0)
    assert m.sessions_with_trade_pct == 0
    assert (m.gross_profit_usd, m.gross_loss_usd) == (Decimal(0), Decimal(0))
    for name in UNDEFINED_WITHOUT_TRADES[1:]:
        assert getattr(m, name) == NA, name
    assert m.low_sample


def test_zero_sessions_leaves_the_session_share_not_applicable() -> None:
    m = summarize([], [], CFG)
    assert (m.trade_count, m.session_count, m.trades_per_day) == (0, 0, F(0))
    for name in UNDEFINED_WITHOUT_TRADES:
        assert getattr(m, name) == NA, name


def test_no_losing_trade_leaves_loss_metrics_not_applicable() -> None:
    m = summarize([trade("40", exit_min=1), trade("60", exit_min=2)], SESSIONS, CFG)
    assert (m.avg_loss_r, m.profit_factor, m.break_even_win_rate_pct) == (NA, NA, NA)
    assert m.avg_win_r == Fraction(1, 2)
    assert m.max_drawdown_usd == m.max_drawdown_r == 0
    assert m.win_rate_a_pct == 100


def test_no_winning_trade_gives_a_zero_profit_factor() -> None:
    m = summarize([trade("-40", exit_min=1), trade("-60", exit_min=2)], SESSIONS, CFG)
    assert (m.avg_win_r, m.break_even_win_rate_pct) == (NA, NA)
    assert m.profit_factor == 0
    assert m.win_rate_a_pct == 0
    assert m.max_drawdown_usd == Decimal(100)  # from the $0 start
    assert m.max_drawdown_r == Decimal(1)


def test_only_breakeven_trades() -> None:
    m = summarize([trade("0.00", exit_min=1), trade("0.00", exit_min=2)], SESSIONS, CFG)
    assert (m.avg_win_r, m.avg_loss_r, m.profit_factor) == (NA, NA, NA)
    assert (m.expectancy_r, m.expectancy_usd) == (F(0), F(0))
    assert (m.win_rate_a_pct, m.win_rate_b_pct) == (F(0), F(100))
    assert m.longest_losing_streak == 0


def test_exit_mode_without_first_target_leaves_win_rate_c_not_applicable() -> None:
    cfg = MetricsCfg(primary_win_rate="c", has_first_target=False)
    m = summarize([trade("10", tp1=True)], SESSIONS, cfg)
    assert m.win_rate_c_pct == m.primary_win_rate_pct == NA
    assert m.win_rate_a_pct == 100


# ---------------------------------------------------------------- win rates and labels


@pytest.mark.parametrize(
    ("tolerance", "net", "counts"),
    [
        ("0.1", "-10.00", True),
        ("0.1", "-10.01", False),
        ("0", "0.00", True),
        ("0", "-0.01", False),
        ("0.25", "-25", True),
    ],
)
def test_win_rate_b_scratch_tolerance_boundary(tolerance: str, net: str, counts: bool) -> None:
    cfg = MetricsCfg(scratch_tolerance_r=Decimal(tolerance), primary_win_rate="b")
    m = summarize([trade(net, r="100")], SESSIONS, cfg)
    assert m.win_rate_b_pct == (100 if counts else 0)
    assert m.primary_win_rate_pct == m.win_rate_b_pct


def test_win_rates_are_exact_percentages() -> None:
    trades = [trade("10", exit_min=1), trade("-10", exit_min=2), trade("-10", exit_min=3)]
    m = summarize(trades, SESSIONS, CFG)
    assert m.win_rate_a_pct == Fraction(100, 3)
    assert m.trades_per_day == Fraction(3, 5)


def test_low_sample_label_below_the_minimum() -> None:
    cfg = MetricsCfg(min_sample_trades=2)
    one = summarize([trade("10")], SESSIONS, cfg)
    two = summarize([trade("10", exit_min=1), trade("5", exit_min=2)], SESSIONS, cfg)
    assert one.low_sample
    assert not two.low_sample
    assert one.is_low_sample("expectancy_r")
    assert not one.is_low_sample("trade_count")
    assert not two.is_low_sample("win_rate_a_pct")
    assert {"win_rate_a_pct", "win_rate_b_pct", "win_rate_c_pct"} <= LOW_SAMPLE_FIELDS
    with pytest.raises(MetricsError, match="no field"):
        one.is_low_sample("win_rate")


# ---------------------------------------------------------------- quantiles


def test_quantiles_interpolate_linearly_and_exactly() -> None:
    d = Decimal
    assert quantiles([d(4), d(1), d(3), d(2)]) == Quantiles(
        d(1), d("1.75"), d("2.5"), d("3.25"), d("3.7"), d(4)
    )
    assert quantiles([d("0.1234567890123456789012345678901")]) == Quantiles(
        *[d("0.1234567890123456789012345678901")] * 6
    )
    assert quantiles([]) == NA


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("sessions", "match"),
    [
        ([D1, D1], "listed twice"),
        ([datetime(2026, 1, 5, 9, 30)], "must be dates"),
        ([D2], "not among the run's sessions"),
    ],
)
def test_inconsistent_sessions_are_rejected(sessions: list[Any], match: str) -> None:
    with pytest.raises(MetricsError, match=match):
        summarize([trade("10", session=D1)], sessions, CFG)


def test_shadow_trade_outside_the_sessions_is_ignored() -> None:
    m = summarize([trade("10", session=D2, shadow=True)], [D1], CFG)
    assert m.trade_count == 0


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"scratch_tolerance_r": Decimal("-0.1")}, "scratch_tolerance_r"),
        ({"scratch_tolerance_r": 0.1}, "scratch_tolerance_r"),
        ({"scratch_tolerance_r": Decimal("NaN")}, "scratch_tolerance_r"),
        ({"min_sample_trades": 0}, "min_sample_trades"),
        ({"min_sample_trades": True}, "min_sample_trades"),
        ({"primary_win_rate": "d"}, "primary_win_rate"),
        ({"has_first_target": 1}, "has_first_target"),
    ],
)
def test_invalid_settings_are_rejected(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(MetricsError, match=match):
        MetricsCfg(**overrides)


# ---------------------------------------------------------------- display


def test_round_fraction_rounds_half_even() -> None:
    assert round_fraction(Fraction(1, 3), 6) == Decimal("0.333333")
    assert round_fraction(Fraction(5, 2), 0) == Decimal(2)
    assert round_fraction(Fraction(-1, 8), 2) == Decimal("-0.12")
    assert str(round_fraction(Fraction(40), 2)) == "40.00"
    with pytest.raises(MetricsError, match="places"):
        round_fraction(Fraction(1), -1)


def test_metrics_encode_as_canonical_json() -> None:
    m = summarize(worked_run(), SESSIONS, CFG)
    encoded = metrics_to_jsonable(m, places=4)
    assert isinstance(encoded, dict)
    assert encoded["profit_factor"] == "4.7619"
    assert encoded["trades_per_day"] == "1.0000"
    assert encoded["max_drawdown_usd"] == "105"
    assert encoded["mae_r"] == {
        "minimum": "0",
        "p25": "0.25",
        "median": "0.5",
        "p75": "0.75",
        "p90": "0.900",
        "maximum": "1",
    }
    assert encoded["low_sample"] is True
    empty = metrics_to_jsonable(summarize([], SESSIONS, CFG))
    assert isinstance(empty, dict)
    assert empty["profit_factor"] == {}  # NotApplicable, like every sentinel
    assert dumps(empty) == dumps(metrics_to_jsonable(summarize([], SESSIONS, CFG)))


def test_metrics_is_immutable() -> None:
    m: Metrics = summarize([], SESSIONS, CFG)
    with pytest.raises(AttributeError):
        m.trade_count = 1  # type: ignore[misc]
