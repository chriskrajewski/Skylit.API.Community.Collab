"""Node_Classifier labels and Node_Velocity (design §5 and §6, Req 5.9-5.10, 6.1-6.11).

:func:`classify` labels one Snapshot by design §6 rules 1 to 8, with
``a = |values|`` and ``spot`` the Snapshot's spot:

1. No strikes, or ``max(a) == 0``: :data:`EMPTY_LABELS` (Req 6.4).
2. King: the strike with the largest ``a`` (Req 6.2).
3. Nodes: every strike with ``a >= node_fraction * a[king]`` (Req 6.1).
4. Floor / Ceiling: the Node with the largest ``a`` among Nodes with
   ``strike < spot`` / ``strike > spot`` (Req 6.5-6.7).
5. Gatekeeper: a Node ``g`` with some Node ``n`` farther out on the same side
   (``spot < g < n`` or ``n < g < spot``) where ``a[n] > a[g]`` and
   ``a[g] >= gatekeeper_fraction * a[n]`` (Req 6.8).
6. Air_Pocket: the open interval ``(lo, hi)`` between strike-adjacent Nodes
   with ``hi - lo >= air_pocket_min_width_pct / 100 * spot`` (Req 6.9).
7. Clear_Skies: at least one Node, and no Node with
   ``0 < strike - spot <= lookout_pct / 100 * spot`` (Req 6.10).
8. Empty_Basement: a Floor exists, and no Node with
   ``0 < floor - strike <= lookout_pct / 100 * spot`` (Req 6.11).

Tie rule (Req 6.3): when strikes tie on ``a`` for King, Floor or Ceiling, the
label goes to the tied strike nearest spot, then to the lower strike. Every
comparison is exact float64 arithmetic in the order written above. Strikes are
expected distinct, as Skylit returns them; strike, value and spot are finite
(the adapters reject anything else).

Labels are memoized per (Snapshot, :class:`NodeParams`): both are frozen and
hashable, so an identical or equal Snapshot under the same parameters returns
the same :class:`NodeLabels` object, and ablations that change only Gates
reuse them. Clusters need converted levels and live in ``fse.engine.clusters``.

:func:`velocity` computes Node_Velocity from Snapshots only; it never reads the
live ``velocityPct`` field, which Snapshots keep in ``extra_json`` (Req 5.9).
"""

from __future__ import annotations

from bisect import bisect_right, insort
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from types import MappingProxyType
from typing import Final, cast

import numpy as np

from fse.config.schema.nodes import FRACTION_MIN, GATEKEEPER_FRACTION_MAX, PCT_MAX, NodesConfig
from fse.engine.types import Snapshot, Unavailable
from fse.pit.protocols import MarketView
from fse.timekit import NS_PER_SECOND

__all__ = [
    "EMPTY_LABELS",
    "LABEL_CACHE_SIZE",
    "NO_PRIOR_SNAPSHOT",
    "PRIOR_VALUE_ZERO",
    "STRIKE_ABSENT",
    "NodeLabels",
    "NodeParams",
    "Velocities",
    "classify",
    "velocity",
    "velocity_between",
]

LABEL_CACHE_SIZE: Final = 4096
"""Memoized (Snapshot, params) pairs; about one session of 1 s Map_States."""


def _require_range(name: str, value: object, lo: float, hi: float, *, lo_open: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"NodeParams.{name} must be a number, got {value!r}")
    above_lo = value > lo if lo_open else value >= lo
    if not (above_lo and value <= hi):
        bound = f"above {lo}" if lo_open else f"at least {lo}"
        raise ValueError(f"NodeParams.{name} must be {bound} and at most {hi}, got {value!r}")


@dataclass(frozen=True, slots=True)
class NodeParams:
    """The ``nodes`` config keys that change labels; their hash keys the memo.

    Ranges match :class:`~fse.config.schema.nodes.NodesConfig`.
    """

    node_fraction: float
    gatekeeper_fraction: float
    air_pocket_min_width_pct: float
    lookout_pct: float

    def __post_init__(self) -> None:
        _require_range("node_fraction", self.node_fraction, FRACTION_MIN, 1.0, lo_open=False)
        _require_range(
            "gatekeeper_fraction",
            self.gatekeeper_fraction,
            FRACTION_MIN,
            GATEKEEPER_FRACTION_MAX,
            lo_open=False,
        )
        _require_range(
            "air_pocket_min_width_pct", self.air_pocket_min_width_pct, 0.0, PCT_MAX, lo_open=True
        )
        _require_range("lookout_pct", self.lookout_pct, 0.0, PCT_MAX, lo_open=True)

    @classmethod
    def from_config(cls, cfg: NodesConfig) -> NodeParams:
        return cls(
            node_fraction=cfg.node_fraction,
            gatekeeper_fraction=cfg.gatekeeper_fraction,
            air_pocket_min_width_pct=cfg.air_pocket_min_width_pct,
            lookout_pct=cfg.lookout_pct,
        )


@dataclass(frozen=True, slots=True)
class NodeLabels:
    """The Node_Classifier labels of one Snapshot; strikes in Snapshot units.

    ``nodes`` and ``gatekeepers`` are in ascending strike order. Each
    ``air_pockets`` entry is the open interval ``(lo, hi)`` between two
    strike-adjacent Nodes, in ascending order. ``king`` is ``None`` exactly
    when the Snapshot has no strikes or every value is 0; then every label is
    empty and both flags are false.
    """

    king: float | None
    nodes: tuple[float, ...]
    floor: float | None
    ceiling: float | None
    gatekeepers: tuple[float, ...]
    air_pockets: tuple[tuple[float, float], ...]
    clear_skies: bool
    empty_basement: bool


EMPTY_LABELS: Final = NodeLabels(
    king=None,
    nodes=(),
    floor=None,
    ceiling=None,
    gatekeepers=(),
    air_pockets=(),
    clear_skies=False,
    empty_basement=False,
)
"""Labels of a Snapshot with no strikes or only zero values (Req 6.4)."""


def _nearest(tied: Sequence[float], spot: float) -> float:
    """The tie rule: nearest spot, then the lower strike (Req 6.3)."""
    return min(tied, key=lambda k: (abs(k - spot), k))


def _largest(side: Sequence[tuple[float, float]], spot: float) -> float | None:
    """The strike with the largest ``a`` among ``(strike, a)`` pairs, by the tie rule."""
    if not side:
        return None
    top = max(a for _, a in side)
    return _nearest([k for k, a in side if a == top], spot)


def _gatekeepers(outward: Sequence[tuple[float, float]], fraction: float) -> list[float]:
    """Gatekeepers among one side's ``(strike, a)`` Nodes, ordered from spot outward.

    Walks from the outermost Node inward, keeping the sorted ``a`` of every
    Node strictly farther out. ``fraction * a[n]`` grows with ``a[n]``, so the
    smallest farther ``a[n] > a[g]`` decides whether any farther Node qualifies.
    """
    farther: list[float] = []
    same_strike: list[float] = []
    current: float | None = None
    found: list[float] = []
    for strike, a in reversed(outward):
        if strike != current:
            for value in same_strike:
                insort(farther, value)
            same_strike = []
            current = strike
        i = bisect_right(farther, a)
        if i < len(farther) and a >= fraction * farther[i]:
            found.append(strike)
        same_strike.append(a)
    return found


def _classify(snapshot: Snapshot, params: NodeParams) -> NodeLabels:
    if not snapshot.strikes:
        return EMPTY_LABELS
    strikes = np.asarray(snapshot.strikes, dtype=np.float64)
    a = np.abs(np.asarray(snapshot.values, dtype=np.float64))
    a_king = float(a.max())
    if a_king == 0.0:
        return EMPTY_LABELS
    spot = snapshot.spot

    king = _nearest(cast("list[float]", strikes[a == a_king].tolist()), spot)

    is_node = a >= params.node_fraction * a_king
    order = np.argsort(strikes[is_node], kind="stable")
    node_k = cast("list[float]", strikes[is_node][order].tolist())
    node_a = cast("list[float]", a[is_node][order].tolist())
    pairs = list(zip(node_k, node_a, strict=True))
    below = [(k, v) for k, v in pairs if k < spot]  # ascending: outermost first
    above = [(k, v) for k, v in pairs if k > spot]  # ascending: nearest first

    floor = _largest(below, spot)
    ceiling = _largest(above, spot)

    gk = params.gatekeeper_fraction
    gatekeepers = sorted(_gatekeepers(below[::-1], gk) + _gatekeepers(above, gk))

    min_width = params.air_pocket_min_width_pct / 100.0 * spot
    air_pockets = tuple((lo, hi) for lo, hi in pairwise(node_k) if hi - lo >= min_width)

    lookout = params.lookout_pct / 100.0 * spot
    clear_skies = not any(0.0 < k - spot <= lookout for k in node_k)
    empty_basement = floor is not None and not any(0.0 < floor - k <= lookout for k in node_k)

    return NodeLabels(
        king=king,
        nodes=tuple(node_k),
        floor=floor,
        ceiling=ceiling,
        gatekeepers=tuple(gatekeepers),
        air_pockets=air_pockets,
        clear_skies=clear_skies,
        empty_basement=empty_basement,
    )


_classify_memo = lru_cache(maxsize=LABEL_CACHE_SIZE)(_classify)


def classify(snapshot: Snapshot, params: NodeParams) -> NodeLabels:
    """The Node_Classifier labels of ``snapshot`` (design §6 rules 1-8), memoized."""
    return _classify_memo(snapshot, params)


# ---------------------------------------------------------------- Node_Velocity

type Velocities = Mapping[float, float | Unavailable]
"""Node_Velocity in percent per strike of the Map_State Snapshot, in strike order."""

NO_PRIOR_SNAPSHOT: Final = Unavailable(
    "no Snapshot with asOf at or before the Decision_Time minus the velocity window"
)
STRIKE_ABSENT: Final = Unavailable("the strike is absent from the velocity window's start Snapshot")
PRIOR_VALUE_ZERO: Final = Unavailable("the strike's value is 0 in the velocity window's start")


def velocity_between(current: Snapshot, prior: Snapshot | None) -> Velocities:
    """Node_Velocity of each strike of ``current`` against the window-start ``prior``.

    ``100 * (|v1| - |v0|) / |v0|``, evaluated left to right, with ``v1`` from
    ``current`` and ``v0`` from ``prior``. A strike is :data:`NO_PRIOR_SNAPSHOT`
    when ``prior`` is ``None``, :data:`STRIKE_ABSENT` when ``prior`` lacks it and
    :data:`PRIOR_VALUE_ZERO` when ``v0 == 0`` (Req 5.10). A strike listed twice
    keeps its first value. Raises ``ValueError`` if ``prior`` is of another
    symbol, metric or Heatmap_View, or has a later ``asOf`` than ``current``.
    """
    if prior is None:
        return MappingProxyType(dict.fromkeys(current.strikes, NO_PRIOR_SNAPSHOT))
    if (prior.symbol, prior.metric, prior.view_id) != (
        current.symbol,
        current.metric,
        current.view_id,
    ):
        raise ValueError(
            f"velocity needs Snapshots of one symbol, metric and view: {current.symbol} "
            f"{current.metric} {current.view_id} against {prior.symbol} {prior.metric} "
            f"{prior.view_id}"
        )
    if prior.as_of_ns > current.as_of_ns:
        raise ValueError(
            f"the velocity window's start Snapshot (asOf {prior.as_of_ns}) is after "
            f"the Map_State Snapshot (asOf {current.as_of_ns})"
        )
    v0_by_strike: dict[float, float] = {}
    for strike, value in zip(prior.strikes, prior.values, strict=True):
        v0_by_strike.setdefault(strike, value)
    out: dict[float, float | Unavailable] = {}
    for strike, v1 in zip(current.strikes, current.values, strict=True):
        if strike in out:
            continue
        v0 = v0_by_strike.get(strike)
        if v0 is None:
            out[strike] = STRIKE_ABSENT
        elif v0 == 0:
            out[strike] = PRIOR_VALUE_ZERO
        else:
            base = abs(v0)
            out[strike] = 100.0 * (abs(v1) - base) / base
    return MappingProxyType(out)


def velocity(view: MarketView, current: Snapshot, window_s: int) -> Velocities:
    """Node_Velocity at ``view.t`` for each strike of the Map_State Snapshot ``current``.

    ``v0`` comes from the latest Snapshot of the same symbol, metric and
    Heatmap_View with ``asOf <= view.t - window_s`` (Req 5.9). The view holds
    one session (``fse.pit.market_view``), so ``v0`` is from the same session.
    ``window_s`` is ``data.velocity_window_s``.
    """
    if isinstance(window_s, bool) or not isinstance(window_s, int) or window_s < 1:
        raise ValueError(f"window_s must be a whole number of seconds >= 1, got {window_s!r}")
    if current.as_of_ns > view.t:
        raise ValueError(f"Snapshot asOf {current.as_of_ns} is after the Decision_Time {view.t}")
    start = view.t - window_s * NS_PER_SECOND
    prior = view.snapshot_at_or_before(current.symbol, current.metric, start)
    return velocity_between(current, prior)
