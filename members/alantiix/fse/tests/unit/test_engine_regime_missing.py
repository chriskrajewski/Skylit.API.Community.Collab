"""Unit tests for the Regime_Classifier's missing-input results (``fse.engine.regime``).

Design §7: a missing gamma Snapshot replaces both the Regime and the
Map_Grade (Req 7.11); a missing vanna Snapshot, or a trailing median covering
fewer than 20 sessions or equal to 0, replaces the Regime only (Req 7.12).
The end-to-end cases build the medians with ``fse.data.medians.trailing_medians``
from synthetic daily magnitudes (no disk I/O) and feed
``RegimeMedians.for_session`` into ``regime``. Snapshots have spot 1000, so
the default 1% regime distance keeps strikes 990 to 1010.

**Validates: Requirements 7.11, 7.12**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pytest

from fse.config.schema.regime import RegimeConfig
from fse.data.medians import MedianParams, RegimeMedians, no_history, trailing_medians
from fse.engine.regime import (
    TRAILING_SESSIONS,
    RegimeParams,
    RegimeResult,
    TrailingMedian,
    TrailingMedians,
    map_state_grade,
    regime,
)
from fse.engine.types import Metric, MissingInput, Snapshot, Unavailable, VixState
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_SECOND, SessionCalendar

T0 = 1_772_721_000 * NS_PER_SECOND
NO_VIX = Unavailable("no VIX")
VIX = VixState(daily_open=NO_VIX, prior_close=20.0, last_1m_close=NO_VIX)
P = RegimeParams(
    RegimeConfig.model_validate({"min_abs_value": 50.0, "vix_condition": {"enabled": False}})
)

CALENDAR = SessionCalendar(date(2026, 1, 5), date(2026, 3, 27))  # 60 weekday sessions
SESSIONS = CALENDAR.sessions()
N = len(SESSIONS)


def snap(
    strikes: tuple[float, ...],
    values: tuple[float, ...],
    *,
    symbol: str = "SPX",
    metric: Metric = "gamma",
    spot: float = 1000.0,
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id="view",
        as_of_ns=T0,
        as_of_raw="2026-03-05T14:30:00Z",
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


# Raw GEX 100, net GEX +160, King and Floor 990: Positive_Gamma and A_Plus_Map.
GAMMA = snap((990.0, 1000.0, 1010.0), (100.0, 50.0, 10.0))
# Raw VEX 400.
VANNA = snap((995.0, 1005.0), (300.0, -400.0), metric="vanna")

FULL_GAMMA = TrailingMedian(100.0, TRAILING_SESSIONS)
FULL_VANNA = TrailingMedian(400.0, TRAILING_SESSIONS)
FULL = TrailingMedians(gamma=FULL_GAMMA, vanna=FULL_VANNA)


def state(*snapshots: Snapshot, missing: tuple[SymMetric, ...] = ()) -> MapState:
    entries: dict[SymMetric, Snapshot | Unavailable] = {(s.symbol, s.metric): s for s in snapshots}
    for key in missing:
        entries[key] = Unavailable("no Snapshot")
    return MapState(t=T0, entries=entries)


def with_median(metric: Metric, m: TrailingMedian | Unavailable) -> TrailingMedians:
    """Full medians with the ``metric`` one replaced by ``m``."""
    if metric == "gamma":
        return TrailingMedians(gamma=m, vanna=FULL_VANNA)
    return TrailingMedians(gamma=FULL_GAMMA, vanna=m)


def build(gamma: Sequence[list[float]], vanna: Sequence[list[float]]) -> RegimeMedians:
    """The trailing medians of every session from each session's raw magnitudes."""
    g = dict(trailing_medians(zip(SESSIONS, gamma, strict=True), symbol="SPX", metric="gamma"))
    v = dict(trailing_medians(zip(SESSIONS, vanna, strict=True), symbol="SPX", metric="vanna"))
    return RegimeMedians(
        view_id="view",
        params=MedianParams.from_regime(P),
        inputs_sha256="0" * 64,
        by_session={d: TrailingMedians(gamma=g[d], vanna=v[d]) for d in SESSIONS},
    )


# ---------------------------------------------------------------- missing Snapshots


def test_full_inputs_give_a_regime_and_a_map_grade() -> None:
    ms = state(GAMMA, VANNA)
    result = regime(ms, FULL, VIX, P)
    assert isinstance(result, RegimeResult)
    assert result.regime == "Positive_Gamma"
    assert map_state_grade(ms, P) == "A_Plus_Map"


def test_missing_gamma_replaces_both_the_regime_and_the_map_grade() -> None:
    expected = MissingInput(("SPX gamma Snapshot",))
    unavailable = state(VANNA, missing=(("SPX", "gamma"),))
    not_configured = state(VANNA)
    for ms in (unavailable, not_configured):
        assert regime(ms, FULL, VIX, P) == expected
        assert map_state_grade(ms, P) == expected


def test_missing_gamma_names_the_configured_regime_symbol() -> None:
    ndx_vanna = snap((21_000.0,), (5.0,), symbol="NDX", metric="vanna", spot=21_000.0)
    p = RegimeParams(P.config, symbol="NDX")
    ms = state(GAMMA, VANNA, ndx_vanna)  # SPX gamma is present but SPX is not the Regime_Symbol
    expected = MissingInput(("NDX gamma Snapshot",))
    assert regime(ms, FULL, VIX, p) == expected
    assert map_state_grade(ms, p) == expected


def test_missing_vanna_replaces_the_regime_only() -> None:
    ms = state(GAMMA, missing=(("SPX", "vanna"),))
    assert regime(ms, FULL, VIX, P) == MissingInput(("SPX vanna Snapshot",))
    assert map_state_grade(ms, P) == "A_Plus_Map"


# ---------------------------------------------------------------- unusable trailing medians


@pytest.mark.parametrize("sessions", [0, 1, TRAILING_SESSIONS - 1])
@pytest.mark.parametrize("metric", ["gamma", "vanna"])
def test_a_trailing_median_covering_fewer_than_20_sessions_replaces_the_regime_only(
    metric: Metric, sessions: int
) -> None:
    # 0 sessions: no earlier session qualifies, so the median is Unavailable.
    short = (
        no_history("SPX", metric, SESSIONS[0]) if sessions == 0 else TrailingMedian(100.0, sessions)
    )
    ms = state(GAMMA, VANNA)
    assert regime(ms, with_median(metric, short), VIX, P) == MissingInput(
        (f"SPX {metric} trailing median",)
    )
    assert map_state_grade(ms, P) == "A_Plus_Map"


@pytest.mark.parametrize("metric", ["gamma", "vanna"])
def test_a_trailing_median_of_zero_replaces_the_regime_only(metric: Metric) -> None:
    ms = state(GAMMA, VANNA)
    zero = with_median(metric, TrailingMedian(0.0, TRAILING_SESSIONS))
    assert regime(ms, zero, VIX, P) == MissingInput((f"SPX {metric} trailing median",))
    assert map_state_grade(ms, P) == "A_Plus_Map"


# ---------------------------------------------------------------- end to end through for_session


def test_built_medians_make_the_regime_missing_until_20_sessions_are_covered() -> None:
    built = build(gamma=[[100.0, 100.0]] * N, vanna=[[400.0]] * N)
    ms = state(GAMMA, VANNA)
    both = MissingInput(("SPX gamma trailing median", "SPX vanna trailing median"))
    assert regime(ms, built.for_session(SESSIONS[0]), VIX, P) == both  # no earlier session
    short = built.for_session(SESSIONS[TRAILING_SESSIONS - 1])
    assert short == TrailingMedians(
        gamma=TrailingMedian(100.0, TRAILING_SESSIONS - 1),
        vanna=TrailingMedian(400.0, TRAILING_SESSIONS - 1),
    )
    assert regime(ms, short, VIX, P) == both
    ready = built.for_session(SESSIONS[TRAILING_SESSIONS])
    assert ready == FULL
    result = regime(ms, ready, VIX, P)
    assert isinstance(result, RegimeResult)
    assert (result.regime, result.normalized_gex, result.normalized_vex) == (
        "Positive_Gamma",
        1.0,
        1.0,
    )


def test_sessions_without_vanna_snapshots_do_not_count_toward_the_20() -> None:
    # Vanna magnitudes on even-indexed sessions only: sessions 0, 2, ..., 38 are the first 20.
    vanna = [[400.0] if i % 2 == 0 else [] for i in range(N)]
    built = build(gamma=[[100.0]] * N, vanna=vanna)
    ms = state(GAMMA, VANNA)
    at_20 = built.for_session(SESSIONS[TRAILING_SESSIONS])
    assert at_20 == TrailingMedians(gamma=FULL_GAMMA, vanna=TrailingMedian(400.0, 10))
    assert regime(ms, at_20, VIX, P) == MissingInput(("SPX vanna trailing median",))
    # Session 38 follows 19 vanna sessions (0, 2, ..., 36); session 39 follows 20.
    assert regime(ms, built.for_session(SESSIONS[38]), VIX, P) == MissingInput(
        ("SPX vanna trailing median",)
    )
    assert isinstance(regime(ms, built.for_session(SESSIONS[39]), VIX, P), RegimeResult)
    assert map_state_grade(ms, P) == "A_Plus_Map"


def test_a_zero_median_built_from_mostly_zero_magnitudes_makes_the_regime_missing() -> None:
    # Two of every three pooled gamma magnitudes are 0, so the median is 0.
    built = build(gamma=[[0.0, 0.0, 100.0]] * N, vanna=[[400.0]] * N)
    at_20 = built.for_session(SESSIONS[TRAILING_SESSIONS])
    assert at_20 == TrailingMedians(gamma=TrailingMedian(0.0, TRAILING_SESSIONS), vanna=FULL_VANNA)
    ms = state(GAMMA, VANNA)
    assert regime(ms, at_20, VIX, P) == MissingInput(("SPX gamma trailing median",))
    assert map_state_grade(ms, P) == "A_Plus_Map"
