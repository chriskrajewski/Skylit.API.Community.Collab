"""Unit tests for the Node_Classifier labels and Node_Velocity (``fse.engine.nodes``).

Design §5 (Node_Velocity) and §6 rules 1 to 8. Snapshots are synthetic; the
velocity tests read them through a historical MarketView.

**Validates: Requirements 5.9, 5.10, 6.1-6.11, 6.19-6.21**
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from fse.config.schema.nodes import NodesConfig
from fse.data.cache import HeatmapView
from fse.engine.nodes import (
    EMPTY_LABELS,
    NO_PRIOR_SNAPSHOT,
    PRIOR_VALUE_ZERO,
    STRIKE_ABSENT,
    NodeLabels,
    NodeParams,
    classify,
    velocity,
    velocity_between,
)
from fse.engine.types import Snapshot
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND

S = NS_PER_SECOND
T0 = 1_772_721_000 * S  # any instant; velocity only uses differences
VIEW_ID = HeatmapView().view_id()
DEFAULTS = NodeParams.from_config(NodesConfig())


def snap(
    strikes: tuple[float, ...],
    values: tuple[float, ...],
    spot: float = 1000.0,
    as_of_ns: int = T0,
    **overrides: Any,
) -> Snapshot:
    fields: dict[str, Any] = {
        "symbol": "SPX",
        "metric": "gamma",
        "view_id": VIEW_ID,
        "as_of_ns": as_of_ns,
        "as_of_raw": f"raw-{as_of_ns}",
        "spot": spot,
        "previous_close": None,
        "strikes": strikes,
        "values": values,
        "node_types": None,
        "expirations": ("2026-03-05",),
        "resolution": "1s",
        "source_endpoint": "range",
        "extra_json": "{}",
    }
    fields.update(overrides)
    return Snapshot(**fields)


def params(**overrides: float) -> NodeParams:
    fields = {
        "node_fraction": 0.20,
        "gatekeeper_fraction": 0.30,
        "air_pocket_min_width_pct": 0.5,
        "lookout_pct": 1.0,
    }
    fields.update(overrides)
    return NodeParams(**fields)


# ---------------------------------------------------------------- NodeParams


def test_params_come_from_the_nodes_config() -> None:
    assert NodeParams.from_config(NodesConfig()) == NodeParams(0.20, 0.30, 0.5, 1.0)
    cfg = NodesConfig.model_validate({"node_fraction": 0.5, "lookout_pct": 2})
    assert NodeParams.from_config(cfg) == NodeParams(0.5, 0.30, 0.5, 2)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"node_fraction": 0.0}, "node_fraction must be at least 0.01"),
        ({"node_fraction": 1.5}, "node_fraction"),
        ({"gatekeeper_fraction": 1.0}, "gatekeeper_fraction"),
        ({"air_pocket_min_width_pct": 0.0}, "air_pocket_min_width_pct must be above 0"),
        ({"lookout_pct": float("nan")}, "lookout_pct"),
        ({"lookout_pct": True}, "lookout_pct must be a number"),
    ],
)
def test_params_reject_out_of_range_values(overrides: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        params(**overrides)


# ---------------------------------------------------------------- classify


@pytest.mark.parametrize(
    ("strikes", "values"),
    [((), ()), ((990.0, 1000.0, 1010.0), (0.0, -0.0, 0.0))],
    ids=["no strikes", "all zero"],
)
def test_empty_or_all_zero_snapshot_has_empty_labels(
    strikes: tuple[float, ...], values: tuple[float, ...]
) -> None:
    labels = classify(snap(strikes, values), DEFAULTS)
    assert labels == EMPTY_LABELS
    assert labels.king is None
    assert not labels.clear_skies
    assert not labels.empty_basement


def test_worked_example_labels_every_rule() -> None:
    # spot 1000: Air_Pocket width 5 points, lookout 10 points; Node threshold 20.
    strikes = (980.0, 990.0, 995.0, 1000.0, 1005.0, 1010.0, 1030.0, 1032.0)
    values = (-50.0, 30.0, 10.0, 5.0, 100.0, -40.0, 60.0, 25.0)
    labels = classify(snap(strikes, values), DEFAULTS)
    assert labels == NodeLabels(
        king=1005.0,
        nodes=(980.0, 990.0, 1005.0, 1010.0, 1030.0, 1032.0),
        floor=980.0,
        ceiling=1005.0,
        gatekeepers=(990.0, 1010.0),
        # (1005, 1010) is exactly the minimum width; (1030, 1032) is narrower.
        air_pockets=((980.0, 990.0), (990.0, 1005.0), (1005.0, 1010.0), (1010.0, 1030.0)),
        clear_skies=False,
        empty_basement=True,
    )


def test_strike_order_of_the_snapshot_does_not_matter() -> None:
    strikes = (1030.0, 980.0, 1005.0, 990.0)
    values = (60.0, -50.0, 100.0, 30.0)
    labels = classify(snap(strikes, values), DEFAULTS)
    assert labels.nodes == (980.0, 990.0, 1005.0, 1030.0)
    assert (labels.king, labels.floor, labels.ceiling) == (1005.0, 980.0, 1005.0)


def test_node_fraction_is_inclusive_and_uses_absolute_values() -> None:
    labels = classify(snap((990.0, 1010.0, 1020.0), (-20.0, 100.0, 19.99)), DEFAULTS)
    assert labels.nodes == (990.0, 1010.0)
    assert labels.king == 1010.0


@pytest.mark.parametrize(
    ("strikes", "values", "king"),
    [
        ((990.0, 1012.0), (-100.0, 100.0), 990.0),  # nearer spot wins
        ((990.0, 1010.0), (100.0, -100.0), 990.0),  # equally near: lower strike
        ((970.0, 1010.0, 1004.0), (100.0, 100.0, 100.0), 1004.0),
    ],
)
def test_king_ties_go_to_the_strike_nearest_spot_then_the_lower(
    strikes: tuple[float, ...], values: tuple[float, ...], king: float
) -> None:
    assert classify(snap(strikes, values), DEFAULTS).king == king


def test_floor_and_ceiling_ties_go_to_the_strike_nearest_spot() -> None:
    strikes = (980.0, 990.0, 1010.0, 1020.0, 1050.0)
    values = (50.0, 50.0, -50.0, 50.0, 100.0)
    labels = classify(snap(strikes, values), DEFAULTS)
    assert (labels.floor, labels.ceiling) == (990.0, 1050.0)
    labels = classify(snap(strikes, (50.0, 50.0, -50.0, 50.0, 10.0)), DEFAULTS)
    assert (labels.floor, labels.ceiling) == (990.0, 1010.0)


def test_a_node_at_spot_is_neither_floor_ceiling_nor_gatekeeper() -> None:
    labels = classify(snap((1000.0, 1010.0), (100.0, 5.0)), DEFAULTS)
    assert labels.nodes == (1000.0,)
    assert (labels.king, labels.floor, labels.ceiling) == (1000.0, None, None)
    assert labels.gatekeepers == ()
    assert labels.clear_skies
    assert not labels.empty_basement


def test_no_node_below_or_above_means_no_floor_or_ceiling() -> None:
    above_only = classify(snap((1010.0, 1020.0), (50.0, 100.0)), DEFAULTS)
    assert (above_only.floor, above_only.ceiling) == (None, 1020.0)
    below_only = classify(snap((980.0, 990.0), (100.0, 50.0)), DEFAULTS)
    assert (below_only.floor, below_only.ceiling) == (980.0, None)


def test_gatekeeper_is_checked_against_every_larger_node_farther_out() -> None:
    # 1010 (25) fails against 1020 (100: needs 30) but passes against 1030 (50: needs 15).
    strikes = (1010.0, 1020.0, 1030.0)
    labels = classify(snap(strikes, (25.0, 100.0, 50.0)), DEFAULTS)
    assert labels.gatekeepers == (1010.0,)
    # A larger Node nearer spot, or on the other side, does not make a Gatekeeper.
    labels = classify(snap((990.0, 1005.0, 1010.0), (100.0, 100.0, 60.0)), DEFAULTS)
    assert labels.gatekeepers == ()


def test_gatekeeper_fraction_is_inclusive_and_needs_a_strictly_larger_node() -> None:
    p = params(gatekeeper_fraction=0.25)
    assert classify(snap((1010.0, 1020.0), (25.0, 100.0)), p).gatekeepers == (1010.0,)
    assert classify(snap((1010.0, 1020.0), (24.99, 100.0)), p).gatekeepers == ()
    assert classify(snap((980.0, 990.0), (100.0, -25.0)), p).gatekeepers == (990.0,)
    assert classify(snap((1010.0, 1020.0), (100.0, 100.0)), p).gatekeepers == ()


def test_air_pocket_width_scales_with_spot() -> None:
    strikes, values = (990.0, 1000.0), (100.0, 100.0)
    assert classify(snap(strikes, values, spot=995.0), DEFAULTS).air_pockets == ((990.0, 1000.0),)
    # 10 points is under 0.5% of 2500 (12.5).
    assert classify(snap(strikes, values, spot=2500.0), DEFAULTS).air_pockets == ()


@pytest.mark.parametrize(
    ("ceiling_strike", "clear"),
    [(1010.0, False), (1010.25, True), (1000.25, False)],
)
def test_clear_skies_lookout_is_inclusive(ceiling_strike: float, clear: bool) -> None:
    labels = classify(snap((990.0, ceiling_strike), (100.0, 50.0)), DEFAULTS)
    assert labels.clear_skies is clear


def test_clear_skies_with_no_node_above() -> None:
    assert classify(snap((980.0, 1020.0), (100.0, 1.0)), DEFAULTS).clear_skies


@pytest.mark.parametrize(
    ("other_strike", "empty"),
    [(980.0, False), (979.75, True), (989.75, False)],
)
def test_empty_basement_lookout_is_inclusive(other_strike: float, empty: bool) -> None:
    labels = classify(snap((other_strike, 990.0), (50.0, 100.0)), DEFAULTS)
    assert labels.floor == 990.0
    assert labels.empty_basement is empty


def test_empty_basement_needs_a_floor() -> None:
    assert not classify(snap((1010.0,), (100.0,)), DEFAULTS).empty_basement


def test_labels_are_memoized_per_snapshot_and_params() -> None:
    s = snap((990.0, 1010.0, 1020.0), (50.0, 100.0, 30.0))
    first = classify(s, DEFAULTS)
    assert classify(s, DEFAULTS) is first
    assert classify(replace(s), params()) is first  # equal Snapshot, equal params
    other = classify(s, params(node_fraction=0.6))
    assert other is not first
    assert other.nodes == (1010.0,)
    assert first.nodes == (990.0, 1010.0, 1020.0)


# ---------------------------------------------------------------- Node_Velocity


def inputs(*snapshots: Snapshot) -> HistoricalInputs:
    return HistoricalInputs(symbols=("SPX",), view_id=VIEW_ID, snapshots=snapshots)


def test_velocity_uses_the_latest_snapshot_at_or_before_t_minus_window() -> None:
    strikes = (990.0, 1000.0, 1010.0)
    early = snap(strikes, (10.0, 50.0, -20.0), as_of_ns=T0)
    start = snap(strikes, (-20.0, 40.0, 25.0), as_of_ns=T0 + 30 * S)
    later = snap(strikes, (99.0, 99.0, 99.0), as_of_ns=T0 + 60 * S)
    current = snap(strikes, (30.0, -10.0, 25.0), as_of_ns=T0 + 90 * S)
    view = inputs(early, start, later, current).view(T0 + 90 * S)
    assert view.map_state().get("SPX", "gamma") is current
    assert velocity(view, current, 60) == {990.0: 50.0, 1000.0: -75.0, 1010.0: 0.0}


def test_velocity_window_start_is_inclusive() -> None:
    start = snap((1000.0,), (40.0,), as_of_ns=T0)
    current = snap((1000.0,), (50.0,), as_of_ns=T0 + 60 * S)
    view = inputs(start, current).view(T0 + 60 * S)
    assert velocity(view, current, 60) == {1000.0: 25.0}
    assert velocity(view, current, 61) == {1000.0: NO_PRIOR_SNAPSHOT}


def test_a_stale_map_snapshot_is_its_own_window_start() -> None:
    current = snap((1000.0,), (-40.0,), as_of_ns=T0)
    view = inputs(current).view(T0 + 300 * S)
    assert velocity(view, current, 60) == {1000.0: 0.0}


def test_velocity_is_unavailable_without_a_usable_v0() -> None:
    start = snap((990.0, 1000.0), (0.0, 20.0), as_of_ns=T0)
    current = snap((990.0, 1000.0, 1010.0), (5.0, 30.0, 7.0), as_of_ns=T0 + 60 * S)
    view = inputs(start, current).view(T0 + 60 * S)
    assert velocity(view, current, 60) == {
        990.0: PRIOR_VALUE_ZERO,
        1000.0: 50.0,
        1010.0: STRIKE_ABSENT,
    }
    assert velocity(view, current, 120) == dict.fromkeys(current.strikes, NO_PRIOR_SNAPSHOT)


def test_velocity_never_reads_the_live_velocity_pct() -> None:
    start = snap((1000.0,), (40.0,), as_of_ns=T0)
    plain = snap((1000.0,), (50.0,), as_of_ns=T0 + 60 * S)
    live = replace(plain, extra_json='{"strikes":[{"velocityPct":-99.0}]}')
    assert velocity_between(live, start) == velocity_between(plain, start) == {1000.0: 25.0}


def test_velocity_keeps_the_first_value_of_a_repeated_strike() -> None:
    start = snap((1000.0, 1000.0), (40.0, 80.0), as_of_ns=T0)
    current = snap((1000.0, 1000.0), (50.0, 0.0), as_of_ns=T0 + 60 * S)
    assert velocity_between(current, start) == {1000.0: 25.0}


@pytest.mark.parametrize(
    ("prior_overrides", "match"),
    [
        ({"symbol": "SPY"}, "one symbol, metric and view"),
        ({"metric": "vanna"}, "one symbol, metric and view"),
        ({"view_id": "other"}, "one symbol, metric and view"),
        ({"as_of_ns": T0 + 61 * S}, "after the Map_State Snapshot"),
    ],
)
def test_velocity_between_rejects_a_mismatched_prior(
    prior_overrides: dict[str, Any], match: str
) -> None:
    current = snap((1000.0,), (50.0,), as_of_ns=T0 + 60 * S)
    prior = snap((1000.0,), (40.0,), **{"as_of_ns": T0, **prior_overrides})
    with pytest.raises(ValueError, match=match):
        velocity_between(current, prior)


@pytest.mark.parametrize("window_s", [0, -60, True, 60.0])
def test_velocity_rejects_a_bad_window(window_s: Any) -> None:
    current = snap((1000.0,), (50.0,))
    with pytest.raises(ValueError, match="window_s"):
        velocity(inputs(current).view(T0), current, window_s)


def test_velocity_rejects_a_snapshot_after_t() -> None:
    current = snap((1000.0,), (50.0,), as_of_ns=T0 + S)
    with pytest.raises(ValueError, match="after the Decision_Time"):
        velocity(inputs(current).view(T0), current, 60)
