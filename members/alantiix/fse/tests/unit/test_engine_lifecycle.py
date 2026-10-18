"""Unit tests for Node lifecycle, Sloppy_Seconds and Dormant labels (design §6).

Snapshots are read through the real historical MarketView; bars are synthetic
1-minute MES bars fed with fixed Deflection_Bands to both the Tap tracker and
the lifecycle state, as the engine does.

**Validates: Requirements 6.15, 6.16, 6.17, 6.18**
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, time

import pytest

from fse.config.schema.nodes import NodesConfig
from fse.engine.levels import round_to_tick
from fse.engine.lifecycle import (
    Lifecycle,
    LifecycleParams,
    LifecycleState,
    NodeState,
    NodeStates,
    dormant_nodes,
    lifecycle_of,
)
from fse.engine.nodes import NodeParams
from fse.engine.regime import RegimeResult
from fse.engine.taps import NodeBand, NodeId, TapState
from fse.engine.types import Bar, MissingInput, Regime, Snapshot
from fse.pit.market_view import HistoricalInputs
from fse.pit.protocols import MapState
from fse.timekit import NS_PER_SECOND, ny_instant

S = NS_PER_SECOND
THU = date(2026, 3, 5)
FRI = date(2026, 3, 6)
MON = date(2026, 3, 9)
VIEW_ID = "view-test"
P = LifecycleParams.from_config(NodesConfig())

X: NodeId = ("SPX", "gamma", 5800.0)
Y: NodeId = ("SPX", "gamma", 5825.0)
Z: NodeId = ("SPX", "gamma", 5780.0)
BAND_X = NodeBand("SPX", "gamma", 5800.0, "MES", 5805.0, 5815.0)
BAND_Y = NodeBand("SPX", "gamma", 5825.0, "MES", 5830.0, 5840.0)
BAND_Z = NodeBand("SPX", "gamma", 5780.0, "MES", 5785.0, 5795.0)
BAND_V = NodeBand("SPX", "gamma", 5805.0, "MES", 5812.0, 5822.0)  # overlaps BAND_X
IN_X = (5810.0, 5811.0)
IN_Y = (5835.0, 5836.0)
IN_V = (5818.0, 5820.0)  # inside BAND_V only
GAP = (5822.25, 5823.0)  # between the bands, inside none


def at(d: date, h: int, m: int, s: int = 0) -> int:
    return ny_instant(d, time(h, m, s))


def mbar(open_ns: int, low: float, high: float) -> Bar:
    lo_t, hi_t = round_to_tick(low), round_to_tick(high)
    return Bar(
        "MES", "MESH6", 60, open_ns, open_ns + 60 * S,
        low, high, low, high, 1.0, lo_t, hi_t, lo_t, hi_t, "atlas",
    )  # fmt: skip


def snap(as_of_ns: int, x_value: float, spot: float = 5800.0) -> Snapshot:
    """An SPX gamma Snapshot; Nodes 5780, 5800 and 5825 while ``x_value >= 200``."""
    return Snapshot(
        symbol="SPX", metric="gamma", view_id=VIEW_ID, as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}", spot=spot, previous_close=None,
        strikes=(5780.0, 5800.0, 5825.0), values=(1000.0, x_value, -1000.0), node_types=None,
        expirations=("2026-03-05",), resolution="1s", source_endpoint="range", extra_json="{}",
    )  # fmt: skip


def bars_from(d: date, h: int, m: int, ranges: Sequence[tuple[float, float]]) -> list[Bar]:
    start = at(d, h, m)
    return [mbar(start + i * 60 * S, low, high) for i, (low, high) in enumerate(ranges)]


def run(
    snapshots: Sequence[Snapshot],
    bars: Sequence[Bar],
    times: Sequence[int],
    params: LifecycleParams = P,
    bands: Sequence[NodeBand] = (BAND_X, BAND_Y, BAND_Z),
    start: tuple[LifecycleState, TapState] | None = None,
) -> tuple[LifecycleState, TapState, list[NodeStates]]:
    """Feed bars closing by each Decision_Time, then evaluate there."""
    inputs = HistoricalInputs(
        symbols=("SPX",), metrics=("gamma",), view_id=VIEW_ID, snapshots=snapshots
    )
    life, taps = (LifecycleState.initial(), TapState.initial()) if start is None else start
    pending = sorted(bars, key=lambda b: b.open_ns)
    out: list[NodeStates] = []
    for t in times:
        while pending and pending[0].close_ns <= t:
            bar = pending.pop(0)
            taps = taps.on_bar(bar, bands)
            life = life.on_bar(bar, bands)
        life, states = life.evaluate(inputs.view(t), taps.view(t), params)
        out.append(states)
    return life, taps, out


# ---------------------------------------------------------------- lifecycle


@pytest.mark.parametrize(
    ("a_now", "peak", "delivered", "weekly", "expected"),
    [
        (75.0, 100.0, True, 3, "Decaying"),  # exactly 25% below the peak
        (75.25, 100.0, True, 3, "Delivered"),
        (75.25, 100.0, False, 1, "Tested"),
        (100.0, 100.0, False, 0, "Fresh"),
        (0.0, None, True, 1, "Delivered"),  # no peak since 09:30: never Decaying
    ],
)
def test_lifecycle_takes_the_first_rule_that_holds(
    a_now: float, peak: float | None, delivered: bool, weekly: int, expected: Lifecycle
) -> None:
    got = lifecycle_of(a_now, peak, delivered=delivered, weekly_taps=weekly, decay_fraction=0.25)
    assert got == expected


def test_decaying_compares_with_the_peak_since_09_30() -> None:
    snapshots = [
        snap(at(THU, 9, 29), 900.0),  # before 09:30: not part of the peak
        snap(at(THU, 9, 30), 400.0),
        snap(at(THU, 10, 0), 300.0),  # 25% below the 400 peak
        snap(at(THU, 10, 30), 301.0),
    ]
    params = replace(P, decay_fraction=0.25)
    times = [at(THU, 9, 45), at(THU, 10, 0), at(THU, 10, 30)]
    life, _, states = run(snapshots, [], times, params)
    assert [s[X].lifecycle for s in states] == ["Fresh", "Decaying", "Fresh"]
    assert [s[Z].lifecycle for s in states] == ["Fresh"] * 3
    (peaks,) = life.peaks
    assert (peaks.folded_to, peaks.peak(5800.0), peaks.peak(5790.0)) == (
        at(THU, 10, 30),
        400.0,
        None,
    )
    # One evaluation from a fresh state folds the same Snapshots.
    _, _, (batch,) = run(snapshots, [], [at(THU, 10, 0)], params)
    assert batch[X].lifecycle == "Decaying"


def test_delivered_after_the_tap_ends_and_price_reaches_another_node() -> None:
    bars = bars_from(THU, 9, 30, [IN_X, GAP, IN_Y])
    _, _, (before, after) = run(
        [snap(at(THU, 9, 30), 400.0)], bars, [at(THU, 9, 32), at(THU, 9, 33)]
    )
    assert before[X].lifecycle == "Tested"  # the Tap ended, nothing delivered yet
    assert {n: s.lifecycle for n, s in after.items()} == {
        Z: "Fresh",
        X: "Delivered",
        Y: "Tested",  # its Tap is still open
    }
    assert list(after) == [Z, X, Y]  # ascending strike


@pytest.mark.parametrize(
    ("ranges", "bands"),
    [
        ([IN_X, IN_V], (BAND_X, BAND_V)),  # the other band overlaps this Node's band
        ([IN_Y, IN_X, GAP], (BAND_X, BAND_Y)),  # delivered before the latest Tap
        ([IN_X, IN_X], (BAND_X, BAND_Y)),  # the Tap has not ended
    ],
)
def test_not_delivered(ranges: list[tuple[float, float]], bands: tuple[NodeBand, ...]) -> None:
    bars = bars_from(THU, 9, 30, ranges)
    _, _, (states,) = run([snap(at(THU, 9, 30), 400.0)], bars, [at(THU, 9, 40)], bands=bands)
    assert states[X].lifecycle == "Tested"


def test_delivered_lasts_for_the_week() -> None:
    thu = run(
        [snap(at(THU, 9, 30), 400.0)], bars_from(THU, 9, 30, [IN_X, GAP, IN_Y]), [at(THU, 9, 33)]
    )
    life, taps, _ = thu
    _, _, (fri,) = run([snap(at(FRI, 9, 30), 400.0)], [], [at(FRI, 9, 30)], start=(life, taps))
    assert fri[X].lifecycle == "Delivered"
    _, _, (mon,) = run([snap(at(MON, 9, 30), 400.0)], [], [at(MON, 9, 30)], start=(life, taps))
    assert mon[X].lifecycle == "Fresh"


# ---------------------------------------------------------------- Sloppy_Seconds

SLOPPY = replace(P, sloppy_fraction=0.25, decay_fraction=0.5)
TAP_BARS = bars_from(THU, 10, 0, [IN_X, GAP])  # first bar closes at 10:01


def test_sloppy_seconds_from_the_first_drop_until_the_session_ends() -> None:
    snapshots = [
        snap(at(THU, 9, 30), 400.0),
        snap(at(THU, 10, 0, 30), 400.0),
        snap(at(THU, 10, 1), 300.0),  # the reference: latest at or before the close
        snap(at(THU, 10, 5), 230.0),
        snap(at(THU, 10, 16), 225.0),  # 25% below 300, at the window's last instant
        snap(at(THU, 10, 20), 400.0),
    ]
    times = [at(THU, 10, 15), at(THU, 10, 16), at(THU, 10, 30)]
    life, taps, states = run(snapshots, TAP_BARS, times, SLOPPY, bands=(BAND_X,))
    assert [s[X].sloppy_since for s in states] == [None, at(THU, 10, 16), at(THU, 10, 16)]
    assert [s[X].lifecycle for s in states] == ["Tested"] * 3  # labeled in addition
    assert states[1][Z] == NodeState("Fresh")
    assert life.watches == ()
    _, _, (batch,) = run(snapshots, TAP_BARS, [at(THU, 10, 30)], SLOPPY, bands=(BAND_X,))
    assert batch[X].sloppy_since == at(THU, 10, 16)
    # The next session starts unlabeled.
    _, _, (fri,) = run(
        [snap(at(FRI, 9, 30), 225.0)], [], [at(FRI, 9, 31)], SLOPPY, start=(life, taps)
    )
    assert (fri[X].sloppy, fri[X].lifecycle) == (False, "Tested")


def test_sloppy_seconds_window_is_open_at_the_close_and_closed_at_its_end() -> None:
    snapshots = [
        snap(at(THU, 10, 0, 30), 400.0),
        snap(at(THU, 10, 1), 300.0),  # at the close: the reference
        snap(at(THU, 10, 10), 240.0),  # a drop from 400, but not 25% below 300
        snap(at(THU, 10, 16, 1), 225.0),  # one second after the window
    ]
    times = [at(THU, 10, 10), at(THU, 10, 30)]
    life, _, states = run(snapshots, TAP_BARS, times, SLOPPY, bands=(BAND_X,))
    assert [s[X].sloppy for s in states] == [False, False]
    assert life.watches == ()
    assert life.sloppy == ()


# ---------------------------------------------------------------- Dormant

FAR = Snapshot(
    symbol="SPX", metric="gamma", view_id=VIEW_ID, as_of_ns=at(THU, 10, 0), as_of_raw="raw",
    spot=4000.0, previous_close=None, strikes=(2999.0, 3000.0, 4000.0, 5000.0, 5001.0),
    values=(1.0,) * 5, node_types=None, expirations=(), resolution="1s",
    source_endpoint="range", extra_json="{}",
)  # fmt: skip
FAR_MS = MapState(at(THU, 10, 0), {("SPX", "gamma"): FAR})


def _result(regime: Regime) -> RegimeResult:
    return RegimeResult(regime, 1.0, 1.0, 1.0, 1.0, 1.0, vanna_withheld=False)


@pytest.mark.parametrize(
    ("regime", "dormant"),
    [
        ("Positive_Gamma", True),
        (_result("Whipsaw"), True),
        (MissingInput(("SPX vanna Snapshot",)), True),
        ("Vanna_Dominant", False),
        (_result("Vanna_Dominant"), False),
    ],
)
def test_dormant_nodes_are_beyond_the_distance_unless_vanna_dominant(
    regime: Regime | RegimeResult | MissingInput, dormant: bool
) -> None:
    params = replace(P, dormant_distance_pct=25.0)  # 1000 points at spot 4000
    expected = (("SPX", "gamma", 2999.0), ("SPX", "gamma", 5001.0)) if dormant else ()
    assert dormant_nodes(FAR_MS, regime, params) == expected


# ---------------------------------------------------------------- parameters and errors


def test_params_from_config_and_validation() -> None:
    cfg = NodesConfig()
    params = LifecycleParams.from_config(cfg)
    assert params == LifecycleParams(0.20, 15, 0.10, 3.5, NodeParams.from_config(cfg))
    for bad in (
        {"decay_fraction": 0.0},
        {"sloppy_fraction": 1.5},
        {"sloppy_window_min": 0},
        {"sloppy_window_min": 391},
        {"sloppy_window_min": True},
        {"dormant_distance_pct": 0.0},
        {"dormant_distance_pct": float("nan")},
    ):
        with pytest.raises(ValueError, match="LifecycleParams"):
            replace(P, **bad)
    with pytest.raises(ValueError, match="must be NodeParams"):
        replace(P, nodes=cfg)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lifecycle must be one of"):
        NodeState("Stale")  # type: ignore[arg-type]


def test_evaluate_is_point_in_time() -> None:
    inputs = HistoricalInputs(
        symbols=("SPX",), metrics=("gamma",), view_id=VIEW_ID, snapshots=[snap(at(THU, 9, 30), 1.0)]
    )
    t = at(THU, 9, 40)
    taps = TapState.initial()
    life = LifecycleState.initial().on_bar(mbar(at(THU, 9, 39), *IN_X), (BAND_X,))
    with pytest.raises(ValueError, match="Tap counts at"):
        life.evaluate(inputs.view(t), taps.view(t - 1), P)
    with pytest.raises(ValueError, match="after consuming MES bars"):
        life.evaluate(inputs.view(t - 1), taps.view(t - 1), P)
    later, _ = life.evaluate(inputs.view(t), taps.view(t), P)
    later.evaluate(inputs.view(t), taps.view(t), P)  # the same instant again is allowed
    rewound = replace(later, horizons=())
    with pytest.raises(ValueError, match="after evaluating"):
        rewound.evaluate(inputs.view(t - 1), taps.view(t - 1), P)


def test_bad_bars_are_rejected() -> None:
    life = LifecycleState.initial().on_bar(mbar(at(THU, 9, 30), *IN_X), (BAND_X,))
    with pytest.raises(ValueError, match="takes 60 s bars"):
        life.on_bar(
            Bar("MES", "MESH6", 300, at(THU, 9, 35), at(THU, 9, 40),
                1.0, 1.0, 1.0, 1.0, 1.0, 4, 4, 4, 4, "atlas"),
            (),
        )  # fmt: skip
    with pytest.raises(ValueError, match="no tick prices"):
        life.on_bar(
            Bar("VIX", "VIX", 60, at(THU, 9, 31), at(THU, 9, 32),
                1.0, 1.0, 1.0, 1.0, 0.0, None, None, None, None, "atlas"),
            (),
        )  # fmt: skip
    with pytest.raises(ValueError, match="out of order"):
        life.on_bar(mbar(at(THU, 9, 29), *IN_X), (BAND_X,))


def test_states_are_values_and_record_the_latest_delivery() -> None:
    bands = (BAND_X, BAND_Y)
    bars = [mbar(at(THU, 9, 30), *IN_X), mbar(at(THU, 9, 31), *IN_Y)]
    first = LifecycleState.initial().on_bar(bars[0], bands)
    second = first.on_bar(bars[1], bands)
    assert first.deliveries == ((Y, at(THU, 9, 30)),)  # on_bar leaves ``first`` unchanged
    assert second.deliveries == ((X, at(THU, 9, 31)), (Y, at(THU, 9, 30)))
    assert LifecycleState.initial().on_bars((b, bands) for b in bars) == second
    next_week = second.on_bar(mbar(at(MON, 9, 30), *GAP), bands)
    assert next_week.deliveries == ()
