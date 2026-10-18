"""Property 18: Node labels match the reference model.

*For any* Snapshot (empty, all-zero, ties forced, any spot position) and any
node parameters, the Node_Classifier's Nodes, King, Floor, Ceiling,
Gatekeepers, Air_Pockets, Clear_Skies and Empty_Basement equal those of a
straightforward reference implementation of Requirement 6 criteria 1-11,
including the tie rule (nearest spot, then lower strike) and empty labels for
empty or all-zero Snapshots.

The reference model is pure Python and brute force. It reads each criterion
literally: King, Floor and Ceiling are the minimum of one sort key
``(-a, |strike - spot|, strike)``, and a Gatekeeper is checked against every
Node pair. It writes each threshold in the arithmetic form the
``fse.engine.nodes`` docstring states (``lookout_pct / 100 * spot`` and so on),
so float rounding can never separate it from the code under test.

"Within the lookout distance" (Req 6.10-6.11) is inclusive here: a Node
exactly ``lookout_pct / 100 * spot`` away blocks Clear_Skies or Empty_Basement.
Design section 6 rule 8 agrees (``[floor - lookout * spot, floor)``), but rule 7
writes the open interval ``(spot, spot * (1 + lookout))``, which would not
count a Node at exactly ``spot + lookout``. This test follows the requirement;
``test_lookout_distance_is_inclusive`` pins the boundary.

Generators:

- grid layouts put strikes on a tick (0.25, 1, 5, the lookout distance or the
  Air_Pocket width) anchored at spot, half a tick off, or 13 ticks to either
  side, so Nodes land on spot, at exactly the lookout or width boundary,
  equidistant from spot, or all on one side; "edges" grids use a round spot
  and a lookout or width tick, so those boundaries are exact in float64;
- free layouts draw arbitrary strikes, with spot sometimes on one of them;
- values come from a small pool two times in three (forcing King, Floor and
  Ceiling ties), and up to three are rewritten to exactly
  ``gatekeeper_fraction * a[n]`` or ``node_fraction * a[king]``; one Snapshot
  in eight has only zero values, and strike order is shuffled;
- the Snapshot with no strikes is an explicit example, run every time.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11**
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from itertools import pairwise

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from fse.engine.nodes import EMPTY_LABELS, NodeLabels, NodeParams, classify
from fse.engine.types import Snapshot

DEFAULT_PARAMS = NodeParams(
    node_fraction=0.20, gatekeeper_fraction=0.30, air_pocket_min_width_pct=0.5, lookout_pct=1.0
)


@dataclass(frozen=True, slots=True)
class Case:
    """One Snapshot's spot, strikes (Snapshot order) and values, with the parameters."""

    spot: float
    strikes: tuple[float, ...]
    values: tuple[float, ...]
    params: NodeParams = DEFAULT_PARAMS

    def snapshot(self) -> Snapshot:
        return Snapshot(
            symbol="SPX",
            metric="gamma",
            view_id="test-view",
            as_of_ns=0,
            as_of_raw="raw",
            spot=self.spot,
            previous_close=None,
            strikes=self.strikes,
            values=self.values,
            node_types=None,
            expirations=("2026-03-05",),
            resolution="1s",
            source_endpoint="range",
            extra_json="{}",
        )


# ---------------------------------------------------------------- reference model

type Row = tuple[float, float]  # (strike, absolute value)


def reference(case: Case) -> NodeLabels:
    """Requirement 6 criteria 1-11, read literally."""
    spot, p = case.spot, case.params
    a = [abs(v) for v in case.values]
    if not case.strikes or max(a) == 0.0:  # 6.4
        return EMPTY_LABELS
    rows: list[Row] = list(zip(case.strikes, a, strict=True))

    def top(candidates: Sequence[Row]) -> float:
        """Largest a; ties to the strike nearest spot, then the lower strike (6.3)."""
        return min(candidates, key=lambda r: (-r[1], abs(r[0] - spot), r[0]))[0]

    king = top(rows)  # 6.2
    a_king = max(a)
    nodes = sorted(r for r in rows if r[1] >= p.node_fraction * a_king)  # 6.1
    below = [r for r in nodes if r[0] < spot]
    above = [r for r in nodes if r[0] > spot]
    floor = top(below) if below else None  # 6.5, 6.7
    ceiling = top(above) if above else None  # 6.6, 6.7

    def guards(g: Row, n: Row) -> bool:
        """``g`` lies strictly between spot and the larger same-side Node ``n`` (6.8)."""
        (gk, ga), (nk, na) = g, n
        between = spot < gk < nk or nk < gk < spot
        return between and na > ga and ga >= p.gatekeeper_fraction * na

    gatekeepers = tuple(g[0] for g in nodes if any(guards(g, n) for n in nodes))

    min_width = p.air_pocket_min_width_pct / 100 * spot  # 6.9
    air_pockets = tuple((lo, hi) for (lo, _), (hi, _) in pairwise(nodes) if hi - lo >= min_width)

    lookout = p.lookout_pct / 100 * spot
    # 6.10: no Node above spot and within the lookout distance of it.
    clear_skies = bool(nodes) and not any(k > spot and k - spot <= lookout for k, _ in nodes)
    # 6.11: no other Node below the Floor and within the lookout distance of it.
    empty_basement = floor is not None and not any(
        k < floor and floor - k <= lookout for k, _ in nodes
    )

    return NodeLabels(
        king=king,
        nodes=tuple(k for k, _ in nodes),
        floor=floor,
        ceiling=ceiling,
        gatekeepers=gatekeepers,
        air_pockets=air_pockets,
        clear_skies=clear_skies,
        empty_basement=empty_basement,
    )


# ---------------------------------------------------------------- generators

PCT = st.floats(min_value=0.0, max_value=100.0, exclude_min=True)
SPOTS = st.one_of(
    st.sampled_from((100.0, 450.25, 5800.0, 5812.5, 21000.75)), st.floats(1.0, 50_000.0)
)
NICE_SPOTS = st.sampled_from((100.0, 400.0, 5800.0, 20_000.0))  # exact lookout and width
POOL = st.sampled_from((1.0, 2.0, 3.0, 10.0, 1.5e9, 3.0e9, 0.0))
MAGNITUDES = st.one_of(POOL, POOL, st.floats(1e-3, 1e12))


def _signed(pair: tuple[float, bool]) -> float:
    magnitude, negative = pair
    return -magnitude if negative else magnitude


VALUES = st.tuples(MAGNITUDES, st.booleans()).map(_signed)


@st.composite
def node_params(draw: st.DrawFn) -> NodeParams:
    return NodeParams(
        node_fraction=draw(st.one_of(st.sampled_from((0.01, 0.2, 0.5, 1.0)), st.floats(0.01, 1.0))),
        gatekeeper_fraction=draw(
            st.one_of(st.sampled_from((0.01, 0.3, 0.5, 0.99)), st.floats(0.01, 0.99))
        ),
        air_pocket_min_width_pct=draw(st.one_of(st.sampled_from((0.1, 0.5, 1.0, 2.0)), PCT)),
        lookout_pct=draw(st.one_of(st.sampled_from((0.5, 1.0, 2.0, 5.0)), PCT)),
    )


@st.composite
def cases(draw: st.DrawFn) -> Case:
    params = draw(node_params())
    layout = draw(st.sampled_from(("grid", "edges", "free")))
    if layout != "free":
        # "edges": round spot, ticks of exactly the lookout distance or Air_Pocket width.
        edges = layout == "edges"
        spot = draw(NICE_SPOTS if edges else st.one_of(NICE_SPOTS, SPOTS))
        lookout = params.lookout_pct / 100 * spot
        min_width = params.air_pocket_min_width_pct / 100 * spot
        ticks = (lookout, lookout, min_width) if edges else (0.25, 1.0, 5.0, lookout, min_width)
        tick = draw(st.sampled_from(ticks))
        # spot on the grid, between two strikes, or past every strike
        shift = draw(st.sampled_from((0.0, -13.0) if edges else (0.0, 0.5, 13.0, -13.0)))
        steps = draw(st.lists(st.integers(-12, 12), min_size=1, max_size=14, unique=True))
        if edges:  # always one tick above and two below the anchor
            steps = [*steps, 1, -1, -2]
        raw = [spot + (shift + i) * tick for i in steps]
    else:
        raw = draw(st.lists(st.floats(1.0, 50_000.0), min_size=1, max_size=12))
        spot = draw(st.one_of(SPOTS, st.sampled_from(raw)))
    strikes = draw(st.permutations(list(dict.fromkeys(raw))))  # distinct, shuffled
    n = len(strikes)
    values = draw(st.lists(VALUES, min_size=n, max_size=n))
    if draw(st.sampled_from((False,) * 7 + (True,))):
        values = draw(st.lists(st.sampled_from((0.0, -0.0)), min_size=n, max_size=n))
    else:
        index = st.integers(0, n - 1)
        rewrites = draw(
            st.lists(st.tuples(index, index, st.sampled_from(("gk", "node"))), max_size=3)
        )
        for target, source, kind in rewrites:
            if kind == "gk":
                exact = params.gatekeeper_fraction * abs(values[source])
            else:
                exact = params.node_fraction * max(abs(v) for v in values)
            values[target] = -exact if values[target] < 0 else exact
    return Case(spot, tuple(strikes), tuple(values), params)


# ---------------------------------------------------------------- explicit examples

_EXAMPLES = (
    Case(5800.0, (), ()),  # no strikes (6.4)
    Case(5800.0, (5790.0, 5800.0, 5810.0), (0.0, -0.0, 0.0)),  # all zero (6.4)
    Case(100.0, (105.0, 95.0, 100.5), (10.0, -10.0, 3.0)),  # King tie, equidistant: lower
    Case(100.0, (90.0, 95.0, 105.0, 110.0), (8.0, -8.0, 8.0, -8.0)),  # Floor/Ceiling ties
    Case(  # Gatekeeper at exactly the fraction; an equal Node never guards
        100.0,
        (105.0, 110.0, 94.0, 90.0),
        (5.0, 10.0, 10.0, 10.0),
        NodeParams(0.2, 0.5, 0.5, 1.0),
    ),
    Case(  # Nodes exactly one lookout (1.0) above spot and below the Floor; width exactly 1.0
        100.0,
        (97.0, 98.0, 101.0, 102.0),
        (5.0, 10.0, 6.0, 9.0),
        NodeParams(0.2, 0.3, 1.0, 1.0),
    ),
    Case(100.0, (100.0, 120.0, 80.0), (4.0, -0.8, 0.5)),  # Node on spot; -0.8 exactly 0.2 * 4
)


def _with_examples[F: Callable[..., None]](test: F) -> F:
    for case in _EXAMPLES:
        test = example(case=case)(test)
    return test


# ---------------------------------------------------------------- tests


# Feature: skylit-futures-strategy-engine, Property 18: Node labels match the reference model
@_with_examples
@given(case=cases())
def test_node_labels_match_reference(case: Case) -> None:
    got = classify(case.snapshot(), case.params)
    want = reference(case)
    for field in fields(NodeLabels):
        name = field.name
        assert getattr(got, name) == getattr(want, name), (
            f"{name}: got {getattr(got, name)!r}, want {getattr(want, name)!r} for {case!r}"
        )
    assert got == want


@pytest.mark.parametrize(
    ("strikes", "values", "clear_skies", "empty_basement"),
    [
        ((101.0,), (5.0,), False, False),  # a Node at exactly spot + lookout is within it
        ((101.25,), (5.0,), True, False),
        ((97.0, 98.0), (5.0, 10.0), True, False),  # a Node exactly lookout below the Floor
        ((96.75, 98.0), (5.0, 10.0), True, True),
    ],
)
def test_lookout_distance_is_inclusive(
    strikes: tuple[float, ...], values: tuple[float, ...], clear_skies: bool, empty_basement: bool
) -> None:
    """Spot 100 and lookout 1% put the lookout boundary at exactly 1.0 (Req 6.10-6.11)."""
    labels = classify(Case(100.0, strikes, values).snapshot(), DEFAULT_PARAMS)
    assert (labels.clear_skies, labels.empty_basement) == (clear_skies, empty_basement)
