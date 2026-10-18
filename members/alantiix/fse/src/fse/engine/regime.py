"""The Regime_Classifier: Regime and Map_Grade (design §7, Req 7).

Inputs are the Regime_Symbol's (``data.regime_symbol``) gamma and vanna
Snapshots in Map_State, their trailing medians (precomputed per session by
``fse.data.medians``), the VIX state at the Decision_Time and the ``regime``
config section. ``a = |value|`` and ``spot`` is the Snapshot's spot.

- **Raw magnitude** (Req 7.2): :func:`raw_magnitude`, the largest ``a`` among
  strikes with ``|strike - spot| <= regime_distance_pct / 100 * spot``, or 0
  when no strike is that close. **Net GEX** (Req 7.6): :func:`net_gex`, the
  signed sum of the gamma values of those strikes (``math.fsum``, so its sign
  is exact).
- **Normalized magnitude** (Req 7.2): raw magnitude / trailing median of the
  same metric.
- **Missing inputs** (Req 7.11-7.12): :func:`regime` returns ``MissingInput``
  naming every absent input, in this order: the gamma Snapshot, the vanna
  Snapshot, the gamma trailing median, the vanna trailing median. A trailing
  median is absent when it is ``Unavailable``, covers fewer than
  :data:`TRAILING_SESSIONS` sessions or equals 0. :func:`map_state_grade`
  returns ``MissingInput`` only for an absent gamma Snapshot.
- **Check order** (Req 7.1-7.7), first match wins:

  1. Vanna_Dominant: ``nVEX > 0 and nVEX >= vanna_multiple * nGEX``, unless
     the VIX condition is enabled and fails (Req 7.3): the VIX value or the
     prior session's VIX close is unavailable, or ``VIX < prior + pct / 100 *
     prior``. The VIX value is ``last_1m_close`` when
     ``vix_condition.intraday_source`` is set, else ``daily_open`` (which the
     MarketView makes available from 09:31).
  2. Structureless: ``max(a) < min_abs_value`` (no strike reaches it).
  3. Whipsaw: the Floor and Ceiling both exist and ``|a_F - a_C| <=
     whipsaw_pct / 100 * max(a_F, a_C)``; or, among the Trinity gamma
     Snapshots present in Map_State, one King lies above its spot and another
     below its spot (a King at spot, or no King, counts on neither side).
  4. Negative_Gamma: net GEX < 0.
  5. Positive_Gamma.

- **Map_Grade** (Req 7.8-7.10), with ``F``, ``C`` the Floor and Ceiling
  ``a`` (0 when missing), ``big = max(F, C)`` and ``small = min(F, C)``:
  F_Map if the Snapshot has 5 or more Nodes, ``big == 0`` or ``big <
  floor_ceiling_ratio * small``; else A_Plus_Map if 1 or 2 Nodes (the King
  included) have ``a >= major_fraction * a[king]``; else Neutral_Map.

Floor, Ceiling, King and Nodes are the Node_Classifier labels
(:func:`fse.engine.nodes.classify`, memoized). Every comparison is float64
arithmetic evaluated left to right as written above. Strikes are expected
distinct, as Skylit returns them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, cast

import numpy as np
import numpy.typing as npt

from fse.config.schema.data import DataConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.regime import GradeConfig, RegimeConfig
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.types import (
    REGIMES,
    MapGrade,
    Metric,
    MissingInput,
    Regime,
    Snapshot,
    Unavailable,
    VixState,
)
from fse.pit.protocols import MapState

__all__ = [
    "MAX_A_PLUS_NODES",
    "TRAILING_SESSIONS",
    "TRINITY",
    "RegimeParams",
    "RegimeResult",
    "TrailingMedian",
    "TrailingMedians",
    "king_abs_value",
    "map_grade",
    "map_state_grade",
    "median_input_name",
    "net_gex",
    "raw_magnitude",
    "regime",
    "snapshot_input_name",
    "vix_value",
]

TRINITY: Final[tuple[str, ...]] = ("SPX", "SPY", "QQQ")
"""The Trinity symbols read by the Whipsaw King check (Glossary, Req 7.5)."""

TRAILING_SESSIONS: Final = 20
"""A trailing median must cover this many earlier sessions (Req 7.2, 7.12)."""

MAX_A_PLUS_NODES: Final = 4
"""An A_Plus_Map or Neutral_Map has fewer than 5 Nodes; 5 or more is an F_Map (Req 7.8-7.9)."""

_MAJOR_COUNTS: Final = frozenset({1, 2})
"""An A_Plus_Map has 1 or 2 major Nodes, the King included (Req 7.8)."""


# ---------------------------------------------------------------- input names


def snapshot_input_name(symbol: str, metric: Metric) -> str:
    """The ``MissingInput`` name of an absent Snapshot: ``SPX gamma Snapshot``."""
    return f"{symbol} {metric} Snapshot"


def median_input_name(symbol: str, metric: Metric) -> str:
    """The ``MissingInput`` name of an absent trailing median: ``SPX vanna trailing median``."""
    return f"{symbol} {metric} trailing median"


# ---------------------------------------------------------------- inputs and parameters


@dataclass(frozen=True, slots=True)
class TrailingMedian:
    """The median raw magnitude of one metric over the earlier sessions' RTH Snapshots.

    ``sessions`` is how many of the :data:`TRAILING_SESSIONS` most recent
    earlier sessions with Regime_Symbol Snapshots of that metric it covers
    (1 to 20). A session with no earlier session gets ``Unavailable`` instead.
    """

    median: float
    sessions: int

    def __post_init__(self) -> None:
        if isinstance(self.median, bool) or not isinstance(self.median, int | float):
            raise ValueError(f"TrailingMedian.median must be a number, got {self.median!r}")
        if not math.isfinite(self.median) or self.median < 0:
            raise ValueError(f"TrailingMedian.median must be finite and >= 0, got {self.median!r}")
        if (
            isinstance(self.sessions, bool)
            or not isinstance(self.sessions, int)
            or not 1 <= self.sessions <= TRAILING_SESSIONS
        ):
            raise ValueError(
                f"TrailingMedian.sessions must be a whole number from 1 to {TRAILING_SESSIONS}, "
                f"got {self.sessions!r}"
            )

    @property
    def usable(self) -> bool:
        """Whether it covers all :data:`TRAILING_SESSIONS` sessions and is above 0 (Req 7.12)."""
        return self.sessions == TRAILING_SESSIONS and self.median > 0


@dataclass(frozen=True, slots=True)
class TrailingMedians:
    """The current session's trailing medians of the Regime_Symbol's GEX and VEX magnitudes."""

    gamma: TrailingMedian | Unavailable
    vanna: TrailingMedian | Unavailable


def _default_node_params() -> NodeParams:
    return NodeParams.from_config(NodesConfig())


@dataclass(frozen=True, slots=True)
class RegimeParams:
    """The ``regime`` section, the Regime_Symbol and the Node_Classifier parameters."""

    config: RegimeConfig
    symbol: str = "SPX"
    nodes: NodeParams = field(default_factory=_default_node_params)

    def __post_init__(self) -> None:
        if not isinstance(self.config, RegimeConfig):
            raise ValueError(f"RegimeParams.config must be a RegimeConfig, got {self.config!r}")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError(f"RegimeParams.symbol must be a symbol, got {self.symbol!r}")
        if not isinstance(self.nodes, NodeParams):
            raise ValueError(f"RegimeParams.nodes must be NodeParams, got {self.nodes!r}")

    @classmethod
    def from_config(
        cls, regime: RegimeConfig, data: DataConfig, nodes: NodesConfig
    ) -> RegimeParams:
        """Parameters from the ``regime``, ``data`` and ``nodes`` sections."""
        return cls(config=regime, symbol=data.regime_symbol, nodes=NodeParams.from_config(nodes))


@dataclass(frozen=True, slots=True)
class RegimeResult:
    """The Regime at a Decision_Time with the measurements behind it.

    ``vanna_withheld`` is true when the magnitudes met Vanna_Dominant but the
    VIX condition withheld it (Req 7.3).
    """

    regime: Regime
    raw_gex: float
    raw_vex: float
    normalized_gex: float
    normalized_vex: float
    net_gex: float
    vanna_withheld: bool

    def __post_init__(self) -> None:
        if self.regime not in REGIMES:
            raise ValueError(f"RegimeResult.regime must be one of {sorted(REGIMES)}")


# ---------------------------------------------------------------- measurements


def _within(snapshot: Snapshot, distance_pct: float) -> npt.NDArray[np.bool_]:
    """Strikes with ``|strike - spot| <= distance_pct / 100 * spot``."""
    strikes = np.asarray(snapshot.strikes, dtype=np.float64)
    limit = distance_pct / 100.0 * snapshot.spot
    return np.abs(strikes - snapshot.spot) <= limit


def raw_magnitude(snapshot: Snapshot, distance_pct: float) -> float:
    """The largest ``a`` within ``distance_pct`` percent of spot, or 0 (Req 7.2)."""
    if not snapshot.strikes:
        return 0.0
    a = np.abs(np.asarray(snapshot.values, dtype=np.float64))[_within(snapshot, distance_pct)]
    return float(a.max()) if a.size else 0.0


def net_gex(snapshot: Snapshot, distance_pct: float) -> float:
    """The signed sum of the values within ``distance_pct`` percent of spot (Req 7.6)."""
    if not snapshot.strikes:
        return 0.0
    values = np.asarray(snapshot.values, dtype=np.float64)[_within(snapshot, distance_pct)]
    return math.fsum(cast("list[float]", values.tolist()))


def king_abs_value(snapshot: Snapshot) -> float:
    """The King's absolute value, ``max(a)``, or 0 for a Snapshot with no strikes."""
    return max((abs(v) for v in snapshot.values), default=0.0)


def _abs_by_strike(snapshot: Snapshot) -> dict[float, float]:
    out: dict[float, float] = {}
    for strike, value in zip(snapshot.strikes, snapshot.values, strict=True):
        a = abs(value)
        if strike not in out or a > out[strike]:
            out[strike] = a
    return out


def _abs_at(a: dict[float, float], strike: float | None) -> float:
    if strike is None:
        return 0.0
    try:
        return a[strike]
    except KeyError:
        raise ValueError(f"the labels name strike {strike}, which the Snapshot lacks") from None


def vix_value(vix: VixState, *, intraday_source: bool) -> float | Unavailable:
    """The VIX value of the VIX condition (Req 7.3); a non-finite value is unavailable."""
    value = vix.last_1m_close if intraday_source else vix.daily_open
    if isinstance(value, Unavailable):
        return value
    if not math.isfinite(value):
        return Unavailable(f"the VIX value {value!r} is not a finite number")
    return value


def _vix_condition_holds(vix: VixState, cfg: RegimeConfig) -> bool:
    cond = cfg.vix_condition
    if not cond.enabled:
        return True
    value = vix_value(vix, intraday_source=cond.intraday_source)
    prior = vix.prior_close
    if isinstance(value, Unavailable) or isinstance(prior, Unavailable):
        return False
    if not math.isfinite(prior):
        return False
    return value >= prior + cond.pct / 100.0 * prior


def _usable_median(m: TrailingMedian | Unavailable) -> float | None:
    if isinstance(m, Unavailable) or not m.usable:
        return None
    return m.median


def _whipsaw(ms: MapState, gamma: Snapshot, labels: NodeLabels, p: RegimeParams) -> bool:
    if labels.floor is not None and labels.ceiling is not None:
        a = _abs_by_strike(gamma)
        a_f, a_c = a[labels.floor], a[labels.ceiling]
        if abs(a_f - a_c) <= p.config.whipsaw_pct / 100.0 * max(a_f, a_c):
            return True
    above = below = False
    for symbol in TRINITY:
        snapshot = ms.get(symbol, "gamma")
        if isinstance(snapshot, Unavailable):
            continue
        king = classify(snapshot, p.nodes).king
        if king is None:
            continue
        if king > snapshot.spot:
            above = True
        elif king < snapshot.spot:
            below = True
    return above and below


# ---------------------------------------------------------------- Regime and Map_Grade


def regime(
    ms: MapState, med: TrailingMedians, vix: VixState, p: RegimeParams
) -> RegimeResult | MissingInput:
    """The Regime of ``ms`` by design §7, or the absent inputs (Req 7.1-7.7, 7.11-7.12)."""
    symbol = p.symbol
    gamma = ms.get(symbol, "gamma")
    vanna = ms.get(symbol, "vanna")
    gex_median = _usable_median(med.gamma)
    vex_median = _usable_median(med.vanna)
    if (
        isinstance(gamma, Unavailable)
        or isinstance(vanna, Unavailable)
        or gex_median is None
        or vex_median is None
    ):
        names: list[str] = []
        if isinstance(gamma, Unavailable):
            names.append(snapshot_input_name(symbol, "gamma"))
        if isinstance(vanna, Unavailable):
            names.append(snapshot_input_name(symbol, "vanna"))
        if gex_median is None:
            names.append(median_input_name(symbol, "gamma"))
        if vex_median is None:
            names.append(median_input_name(symbol, "vanna"))
        return MissingInput(tuple(names))

    cfg = p.config
    distance = cfg.regime_distance_pct
    raw_gex = raw_magnitude(gamma, distance)
    raw_vex = raw_magnitude(vanna, distance)
    n_gex = raw_gex / gex_median
    n_vex = raw_vex / vex_median
    net = net_gex(gamma, distance)
    magnitudes_hold = n_vex > 0 and n_vex >= cfg.vanna_multiple * n_gex
    vix_holds = _vix_condition_holds(vix, cfg)

    label: Regime
    if magnitudes_hold and vix_holds:
        label = "Vanna_Dominant"
    elif king_abs_value(gamma) < cfg.min_abs_value:
        label = "Structureless"
    elif _whipsaw(ms, gamma, classify(gamma, p.nodes), p):
        label = "Whipsaw"
    elif net < 0:
        label = "Negative_Gamma"
    else:
        label = "Positive_Gamma"
    return RegimeResult(
        regime=label,
        raw_gex=raw_gex,
        raw_vex=raw_vex,
        normalized_gex=n_gex,
        normalized_vex=n_vex,
        net_gex=net,
        vanna_withheld=magnitudes_hold and not vix_holds,
    )


def map_grade(gamma: Snapshot, labels: NodeLabels, p: GradeConfig) -> MapGrade:
    """The Map_Grade of a gamma Snapshot from its Node_Classifier labels (Req 7.8-7.10).

    Raises ``ValueError`` for a vanna Snapshot or labels naming a strike the
    Snapshot lacks.
    """
    if gamma.metric != "gamma":
        raise ValueError(f"map_grade needs a gamma Snapshot, got {gamma.symbol} {gamma.metric}")
    a = _abs_by_strike(gamma)
    f = _abs_at(a, labels.floor)
    c = _abs_at(a, labels.ceiling)
    big, small = max(f, c), min(f, c)
    if len(labels.nodes) > MAX_A_PLUS_NODES or big == 0 or big < p.floor_ceiling_ratio * small:
        return "F_Map"
    major = p.major_fraction * _abs_at(a, labels.king)
    majors = sum(1 for strike in labels.nodes if _abs_at(a, strike) >= major)
    return "A_Plus_Map" if majors in _MAJOR_COUNTS else "Neutral_Map"


def map_state_grade(ms: MapState, p: RegimeParams) -> MapGrade | MissingInput:
    """The Map_Grade of the Regime_Symbol's gamma Snapshot, or ``MissingInput`` (Req 7.11)."""
    gamma = ms.get(p.symbol, "gamma")
    if isinstance(gamma, Unavailable):
        return MissingInput((snapshot_input_name(p.symbol, "gamma"),))
    return map_grade(gamma, classify(gamma, p.nodes), p.config.grade)
