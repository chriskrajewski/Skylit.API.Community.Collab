"""Property 19: Node label invariants.

*For any* Snapshot with at least one non-zero strike, exactly one King exists
and its absolute value is at least every strike's absolute value; any Floor is
strictly below spot and any Ceiling strictly above; every Gatekeeper lies
strictly between spot and a same-side Node with a larger absolute value.

Snapshots carry distinct strikes (as Skylit returns them) in random order,
either on a strike grid or anywhere in a wide range. Each Snapshot draws its
values in one style: a small pool of signed magnitudes (which forces ties,
zeros and ``-0.0``), comparable magnitudes (many Nodes, so many Gatekeepers),
or a mix that adds any finite float, subnormals included. Spot sits on a strike, on a midpoint
between adjacent strikes, outside every strike or anywhere near them. Empty and
all-zero Snapshots are generated too; for them only the Floor, Ceiling and
Gatekeeper invariants apply.

The checks never read the classifier's own Node list: a Node is recomputed from
Req 6.1 as a strike with ``|value| >= node_fraction * max|value|``.

**Validates: Requirements 6.19, 6.20, 6.21**
"""

from __future__ import annotations

from itertools import pairwise

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.nodes import FRACTION_MIN, GATEKEEPER_FRACTION_MAX, PCT_MAX
from fse.data.cache import HeatmapView
from fse.engine.nodes import NodeParams, classify
from fse.engine.types import Snapshot

VIEW_ID = HeatmapView().view_id()
DEFAULTS = NodeParams(
    node_fraction=0.20, gatekeeper_fraction=0.30, air_pocket_min_width_pct=0.5, lookout_pct=1.0
)

# ---------------------------------------------------------------- inputs


def snap(strikes: tuple[float, ...], values: tuple[float, ...], spot: float) -> Snapshot:
    return Snapshot(
        symbol="SPX",
        metric="gamma",
        view_id=VIEW_ID,
        as_of_ns=0,
        as_of_raw="raw-0",
        spot=spot,
        previous_close=None,
        strikes=strikes,
        values=values,
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


# ---------------------------------------------------------------- generators

GRID_BASES = (1.0, 450.0, 5800.0, 20_000.0)
GRID_STEPS = (0.5, 1.0, 5.0, 25.0)
TIE_MAGNITUDES = (0.0, 0.5e9, 1.0e9, 2.0e9, 3.0e9)
TIED_VALUES = tuple(sign * m for m in TIE_MAGNITUDES for sign in (1.0, -1.0))  # has -0.0

# One value style per Snapshot. Bounded st.floats never yield NaN or infinity;
# the unbounded one opts out explicitly.
SIMILAR = st.tuples(st.floats(1e8, 1e10), st.sampled_from((1.0, -1.0))).map(lambda t: t[0] * t[1])
VALUE_STYLES = (
    st.sampled_from(TIED_VALUES),  # ties, zeros and -0.0
    SIMILAR,  # comparable magnitudes: many Nodes, so many Gatekeepers
    st.one_of(
        st.sampled_from(TIED_VALUES),
        st.floats(-1e12, 1e12),
        st.floats(allow_nan=False, allow_infinity=False),
    ),
)


@st.composite
def strike_sets(draw: st.DrawFn) -> tuple[float, ...]:
    """Distinct strikes in random order: a grid or arbitrary floats."""
    if draw(st.booleans()):
        base = draw(st.sampled_from(GRID_BASES))
        step = draw(st.sampled_from(GRID_STEPS))
        steps = draw(st.lists(st.integers(0, 40), unique=True, max_size=25))
        strikes = [base + i * step for i in steps]
    else:
        strikes = draw(st.lists(st.floats(0.01, 1e5), unique=True, max_size=25))
    return tuple(draw(st.permutations(strikes)))


@st.composite
def snapshots(draw: st.DrawFn) -> Snapshot:
    strikes = draw(strike_sets())
    style = draw(st.sampled_from(VALUE_STYLES))
    values = tuple(draw(st.lists(style, min_size=len(strikes), max_size=len(strikes))))
    lo, hi = (min(strikes), max(strikes)) if strikes else (1000.0, 1000.0)
    spot_choices = [
        st.floats(lo - 50.0, hi + 50.0),
        st.sampled_from((lo - 1.0, hi + 1.0)),
    ]
    if strikes:
        spot_choices.append(st.sampled_from(strikes))  # spot exactly on a strike
    ordered = sorted(strikes)
    if len(ordered) >= 2:
        midpoints = [(x + y) / 2.0 for x, y in pairwise(ordered)]
        spot_choices.append(st.sampled_from(midpoints))
    spot = draw(st.one_of(*spot_choices))
    return snap(strikes, values, spot)


NODE_FRACTIONS = st.one_of(
    st.sampled_from((FRACTION_MIN, 0.20, 0.5, 1.0)), st.floats(FRACTION_MIN, 1.0)
)
GATEKEEPER_FRACTIONS = st.one_of(
    st.sampled_from((FRACTION_MIN, 0.30, GATEKEEPER_FRACTION_MAX)),
    st.floats(FRACTION_MIN, GATEKEEPER_FRACTION_MAX),
)
PCTS = st.one_of(st.sampled_from((0.5, 1.0, PCT_MAX)), st.floats(0.0, PCT_MAX, exclude_min=True))
NODE_PARAMS = st.builds(
    NodeParams,
    node_fraction=NODE_FRACTIONS,
    gatekeeper_fraction=GATEKEEPER_FRACTIONS,
    air_pocket_min_width_pct=PCTS,
    lookout_pct=PCTS,
)

# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 19: Node label invariants
# King tie at equal distance from spot: the lower strike wins.
@example(snapshot=snap((1010.0, 990.0), (-5e9, 5e9), 1000.0), params=DEFAULTS)
# Spot exactly on the King's strike: that strike is neither Floor nor Ceiling.
@example(snapshot=snap((995.0, 1000.0, 1005.0), (1e9, 3e9, 2e9), 1000.0), params=DEFAULTS)
# One Gatekeeper on each side of spot.
@example(
    snapshot=snap((970.0, 985.0, 1015.0, 1030.0), (4e9, 2e9, -2e9, -4e9), 1000.0),
    params=DEFAULTS,
)
# Subnormal King: node_fraction * a_king rounds to 0, so zero-valued strikes are Nodes.
@example(
    snapshot=snap((1020.0, 1010.0, 990.0, 980.0), (5e-324, 0.0, 0.0, -5e-324), 1000.0),
    params=DEFAULTS,
)
# A single strike on spot; all-zero values; no strikes.
@example(snapshot=snap((1000.0,), (1e9,), 1000.0), params=DEFAULTS)
@example(snapshot=snap((990.0, 1010.0), (0.0, -0.0), 1000.0), params=DEFAULTS)
@example(snapshot=snap((), (), 1000.0), params=DEFAULTS)
@given(snapshot=snapshots(), params=NODE_PARAMS)
def test_node_label_invariants(snapshot: Snapshot, params: NodeParams) -> None:
    labels = classify(snapshot, params)
    spot = snapshot.spot
    a_of = {k: abs(v) for k, v in zip(snapshot.strikes, snapshot.values, strict=True)}
    a_max = max(a_of.values(), default=0.0)

    # Req 6.19: exactly one King, with the largest absolute value.
    if a_max > 0.0:
        king = labels.king
        assert king is not None, f"no King although max |value| = {a_max!r}"
        assert snapshot.strikes.count(king) == 1, f"King {king!r} is not one input strike"
        assert all(a_of[king] >= a for a in a_of.values()), (
            f"King {king!r} has |value| {a_of[king]!r} below max {a_max!r}"
        )

    # Req 6.20: Floor strictly below spot, Ceiling strictly above.
    if labels.floor is not None:
        assert labels.floor < spot, f"Floor {labels.floor!r} not below spot {spot!r}"
    if labels.ceiling is not None:
        assert labels.ceiling > spot, f"Ceiling {labels.ceiling!r} not above spot {spot!r}"

    # Req 6.21: each Gatekeeper sits strictly between spot and a same-side Node
    # with a larger absolute value. Nodes recomputed from Req 6.1.
    nodes = [k for k, a in a_of.items() if a >= params.node_fraction * a_max] if a_max else []
    for g in labels.gatekeepers:
        assert g in a_of, f"Gatekeeper {g!r} is not an input strike"
        a_g = a_of[g]
        assert any((spot < g < n or n < g < spot) and a_of[n] > a_g for n in nodes), (
            f"Gatekeeper {g!r} (|value| {a_g!r}, spot {spot!r}) has no larger Node farther out"
        )
