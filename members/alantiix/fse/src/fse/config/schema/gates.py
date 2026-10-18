"""The ``gates`` section of the Strategy_Config (design §11, Req 11.1-11.12).

One entry per Gate, keyed by Gate id (:data:`GATE_IDS`, the Req 11.1 order),
each with an ``enabled`` flag and the parameters below. Read by
``fse.engine.gates``. Two keys apply to the whole section:

- ``order``: the Gate evaluation order, a list of all 27 Gate ids, each once
  (default :data:`GATE_IDS`, Req 11.2).
- ``fade_detectors``: the detector ids that count as fade Patterns for
  ``air_pocket_fade`` and ``node_growth_divergence`` (default
  ``gatekeeper_fade``, both ``floor_ceiling_bounce`` detectors, ``beach_ball``
  and ``whipsaw_fade``).

Per-Gate parameters (defaults from the design §11 table). A Gate "fails at"
a value when the value itself fails, and has a "max" when only values above
it fail:

- ``stale_map.max_snapshot_age_s``: 90 (1 to 3600, Req 5.7).
- ``map_grade.fail_grades``: ``[F_Map]``; a missing Map_Grade also fails.
- ``midpoint.lo`` / ``hi``: 0.33 / 0.67, each 0 to 1 with ``lo < hi``.
- ``deflection_band``, ``chart_confluence``, ``candle_color``,
  ``sloppy_seconds``, ``dormant_node``, ``kill_switch_lockout``: no
  parameters; the band Gates compare against the Deflection_Band half-width.
- ``stdev_fib_zone.zones``: the Fibonacci ratio ranges ``{near, far}``,
  default ``[{-2.0, -2.5}, {-3.5, -4.5}]`` (each ratio -10 to 1).
- ``dark_pool_confluence``: ``min_notional_usd`` 1000000 (above 0),
  ``lookback_sessions`` 5 (1 to 60), and the dark-pool tickers scaled to ES
  (``es_ticker``, SPX or SPY, default SPY) and to NQ (``nq_ticker``, QQQ, NDX
  or NDXP, default QQQ).
- ``trinity_agreement``: ``min_agree`` 2 (1 to 3) and
  ``empty_basement_exception`` on (Req 11.9-11.10).
- ``tap_count.max_tap_seq``: 2. ``third_gatekeeper_test.fail_at_test``: 3.
  ``weekly_node_tests.max_tests``: 2. Each 1 to 100.
- ``air_pocket_fade.max_depth_pts``: 0.0 (0 to 100).
- ``min_reward_risk``: ``min`` 3.0 and ``alert_min`` 2.0, each above 0 and at
  most 20, with ``alert_min <= min`` (Req 11.5-11.7, 11.12).
- ``opposition_inside_target``: ``fraction`` 0.85 (0.01 to 1) and
  ``window_r`` 3.0 (above 0, at most 20) (Req 11.8).
- ``fomo_travel.max_fraction``: 0.60 (above 0, at most 10).
- ``open_shuffle.min_minutes``: 15 (0 to 390).
- ``entry_cutoff.cutoff``: ``"15:25"``.
- ``late_session_chase.start`` / ``end``: ``"15:30"`` / ``"16:00"``, ``start < end``.
- ``news_window``: ``minutes`` 5 (0 to 240) and ``event_types``
  ``[CPI, NFP, FOMC]`` (calendar event-type names, each once).
- ``vix_gap``: ``pct`` 15.0 (above 0) and ``until`` ``"12:00"``.
- ``regime_match.allowed``: per detector id, its allowed Regimes, each once.
  The default is the Playbook_Baseline set (design §11): fades
  (``gatekeeper_fade``, both ``floor_ceiling_bounce`` detectors and
  ``beach_ball``) allow Positive_Gamma and Whipsaw; ``whipsaw_fade`` allows
  Whipsaw; ``rug`` and ``reverse_rug`` allow Positive_Gamma and
  Negative_Gamma; ``trend_follow`` allows Negative_Gamma and Vanna_Dominant.
  No detector allows Structureless.
- ``gatekeepers_on_path.fail_at``: 2 (1 to 50).
- ``node_growth_divergence.fail_at_pct``: 20.0 (0 to 1000).

Times are quoted ``"HH:MM"`` New York times after 09:30 and before 18:00
(``fse.config.schema.account.WallTime``). Every Gate is enabled by default
except ``dark_pool_confluence`` and ``stdev_fib_zone``, which the Skill_Documents
list as confluence layers, not hard passes (design "Playbook_Baseline
choices"); both are still measured and recorded ``disabled``. Every float
rejects NaN and infinity.
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from fse.config.schema._base import SchemaModel, YamlList, require_unique
from fse.config.schema.account import WallTime
from fse.config.schema.nodes import FRACTION_MIN

__all__ = [
    "DEFAULT_FADE_DETECTORS",
    "DEFAULT_NEWS_EVENT_TYPES",
    "FIB_RATIO_MAX",
    "FIB_RATIO_MIN",
    "GATE_IDS",
    "RR_MAX",
    "AirPocketFadeConfig",
    "AllowedRegimesConfig",
    "CandleColorConfig",
    "ChartConfluenceConfig",
    "DarkPoolConfluenceConfig",
    "DeflectionBandConfig",
    "DormantNodeConfig",
    "EntryCutoffConfig",
    "FibZoneConfig",
    "FomoTravelConfig",
    "GateConfig",
    "GateDetectorId",
    "GateId",
    "GatekeepersOnPathConfig",
    "GatesConfig",
    "KillSwitchLockoutConfig",
    "LateSessionChaseConfig",
    "MapGradeGateConfig",
    "MapGradeName",
    "MidpointConfig",
    "MinRewardRiskConfig",
    "NewsWindowConfig",
    "NodeGrowthDivergenceConfig",
    "OpenShuffleConfig",
    "OppositionInsideTargetConfig",
    "RegimeMatchConfig",
    "RegimeName",
    "SloppySecondsGateConfig",
    "StaleMapConfig",
    "StdevFibZoneConfig",
    "TapCountConfig",
    "ThirdGatekeeperTestConfig",
    "TrinityAgreementConfig",
    "VixGapGateConfig",
    "WeeklyNodeTestsConfig",
]

type GateId = Literal[
    "stale_map",
    "map_grade",
    "midpoint",
    "deflection_band",
    "chart_confluence",
    "stdev_fib_zone",
    "dark_pool_confluence",
    "trinity_agreement",
    "candle_color",
    "tap_count",
    "third_gatekeeper_test",
    "weekly_node_tests",
    "sloppy_seconds",
    "air_pocket_fade",
    "min_reward_risk",
    "opposition_inside_target",
    "fomo_travel",
    "open_shuffle",
    "entry_cutoff",
    "late_session_chase",
    "news_window",
    "vix_gap",
    "regime_match",
    "dormant_node",
    "gatekeepers_on_path",
    "node_growth_divergence",
    "kill_switch_lockout",
]

GATE_IDS: Final[tuple[GateId, ...]] = (
    "stale_map",
    "map_grade",
    "midpoint",
    "deflection_band",
    "chart_confluence",
    "stdev_fib_zone",
    "dark_pool_confluence",
    "trinity_agreement",
    "candle_color",
    "tap_count",
    "third_gatekeeper_test",
    "weekly_node_tests",
    "sloppy_seconds",
    "air_pocket_fade",
    "min_reward_risk",
    "opposition_inside_target",
    "fomo_travel",
    "open_shuffle",
    "entry_cutoff",
    "late_session_chase",
    "news_window",
    "vix_gap",
    "regime_match",
    "dormant_node",
    "gatekeepers_on_path",
    "node_growth_divergence",
    "kill_switch_lockout",
)
"""The 27 Gate ids in the Req 11.1 order: the default ``order`` and the section keys."""

type RegimeName = Literal[
    "Positive_Gamma", "Negative_Gamma", "Vanna_Dominant", "Whipsaw", "Structureless"
]
"""The same values as ``fse.engine.types.Regime``; a test keeps them equal."""

type MapGradeName = Literal["A_Plus_Map", "Neutral_Map", "F_Map"]
"""The same values as ``fse.engine.types.MapGrade``; a test keeps them equal."""

type GateDetectorId = Literal[
    "gatekeeper_fade",
    "floor_ceiling_bounce",
    "floor_ceiling_bounce_empty_basement",
    "beach_ball",
    "rug",
    "reverse_rug",
    "whipsaw_fade",
    "trend_follow",
]
"""The ``fse.config.schema.patterns.DETECTOR_IDS`` values; a test keeps them equal."""

type EsTicker = Literal["SPX", "SPY"]
type NqTicker = Literal["QQQ", "NDX", "NDXP"]
type EventType = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")]
"""An economic-calendar event type, as ``fse.calendars`` accepts it."""

DEFAULT_FADE_DETECTORS: Final[tuple[GateDetectorId, ...]] = (
    "gatekeeper_fade",
    "floor_ceiling_bounce",
    "floor_ceiling_bounce_empty_basement",
    "beach_ball",
    "whipsaw_fade",
)
DEFAULT_NEWS_EVENT_TYPES: Final[tuple[str, ...]] = ("CPI", "NFP", "FOMC")

MAX_SNAPSHOT_AGE_MAX_S: Final = 3600
COUNT_MAX: Final = 100
RR_MAX: Final = 20.0
"""The largest reward-to-risk threshold or R window a Gate takes."""
FIB_RATIO_MIN: Final = -10.0
FIB_RATIO_MAX: Final = 1.0
LOOKBACK_SESSIONS_MAX: Final = 60
NEWS_MINUTES_MAX: Final = 240
RTH_MINUTES: Final = 390

type RegimeList = YamlList[RegimeName]


def _require_after(name: str, value: float | time, info: ValidationInfo, lower: str) -> None:
    """Raise ``greater_than`` unless ``value`` is above the already-validated ``lower`` key."""
    bound = info.data.get(lower)
    if bound is not None and not value > bound:
        raise PydanticCustomError(
            "greater_than",
            "{name} {value} should be greater than {lower} {gt}",
            {"name": name, "value": str(value), "lower": lower, "gt": str(bound)},
        )


# ---------------------------------------------------------------- per-Gate settings


class GateConfig(SchemaModel):
    """The key every Gate has: its on/off switch (Req 11.1, 11.4)."""

    enabled: bool = True


class StaleMapConfig(GateConfig):
    """Fail when Snapshot_Age exceeds ``max_snapshot_age_s`` (Req 5.7)."""

    max_snapshot_age_s: int = Field(90, ge=1, le=MAX_SNAPSHOT_AGE_MAX_S)


class MapGradeGateConfig(GateConfig):
    """Fail on a Map_Grade in ``fail_grades`` or a missing Map_Grade."""

    fail_grades: YamlList[MapGradeName] = ("F_Map",)

    @field_validator("fail_grades")
    @classmethod
    def _unique(cls, value: tuple[MapGradeName, ...]) -> tuple[MapGradeName, ...]:
        return require_unique(value)


class MidpointConfig(GateConfig):
    """Fail an entry strictly between ``lo`` and ``hi`` of the Floor-to-Ceiling range."""

    lo: float = Field(0.33, ge=0, le=1, allow_inf_nan=False)
    hi: float = Field(0.67, ge=0, le=1, allow_inf_nan=False, validate_default=True)

    @field_validator("hi")
    @classmethod
    def _above_lo(cls, value: float, info: ValidationInfo) -> float:
        _require_after("hi", value, info, "lo")
        return value


class DeflectionBandConfig(GateConfig):
    """Fail when Futures_Price is past the level by more than the band half-width."""


class ChartConfluenceConfig(GateConfig):
    """Fail when no Chart_Level is within the band half-width of the level."""


class FibZoneConfig(SchemaModel):
    """One Fibonacci zone of a BOS_Leg: the ratios at its two ends."""

    near: float = Field(ge=FIB_RATIO_MIN, le=FIB_RATIO_MAX, allow_inf_nan=False)
    far: float = Field(ge=FIB_RATIO_MIN, le=FIB_RATIO_MAX, allow_inf_nan=False)


class StdevFibZoneConfig(GateConfig):
    """Fail when no active BOS_Leg zone is within the band half-width of entry; off by default."""

    enabled: bool = False
    zones: Annotated[YamlList[FibZoneConfig], Field(min_length=1)] = (
        FibZoneConfig(near=-2.0, far=-2.5),
        FibZoneConfig(near=-3.5, far=-4.5),
    )


class DarkPoolConfluenceConfig(GateConfig):
    """Fail when no large dark-pool print is within the band half-width; off by default."""

    enabled: bool = False
    min_notional_usd: float = Field(1_000_000.0, gt=0, allow_inf_nan=False)
    lookback_sessions: int = Field(5, ge=1, le=LOOKBACK_SESSIONS_MAX)
    es_ticker: EsTicker = "SPY"
    nq_ticker: NqTicker = "QQQ"


class TrinityAgreementConfig(GateConfig):
    """Fail when fewer than ``min_agree`` Trinity or NQ_Sources symbols agree (Req 11.9-11.10)."""

    min_agree: int = Field(2, ge=1, le=3)
    empty_basement_exception: bool = True


class CandleColorConfig(GateConfig):
    """A long needs a red latest candle and a short a green one; a doji fails."""


class TapCountConfig(GateConfig):
    """Fail a Setup_Key ``tap_seq`` above ``max_tap_seq``."""

    max_tap_seq: int = Field(2, ge=1, le=COUNT_MAX)


class ThirdGatekeeperTestConfig(GateConfig):
    """Fail a Gatekeeper_Fade at weekly test ``fail_at_test`` or later."""

    fail_at_test: int = Field(3, ge=1, le=COUNT_MAX)


class WeeklyNodeTestsConfig(GateConfig):
    """Fail a weekly test number above ``max_tests``."""

    max_tests: int = Field(2, ge=1, le=COUNT_MAX)


class SloppySecondsGateConfig(GateConfig):
    """Fail when the source Node is labeled Sloppy_Seconds."""


class AirPocketFadeConfig(GateConfig):
    """Fail a fade whose entry sits more than ``max_depth_pts`` inside an Air_Pocket."""

    max_depth_pts: float = Field(0.0, ge=0, le=100, allow_inf_nan=False)


class MinRewardRiskConfig(GateConfig):
    """Fail reward:risk below ``min``; ``alert_min`` is the Alert_2R floor (Req 11.5-11.7)."""

    min: float = Field(3.0, gt=0, le=RR_MAX, allow_inf_nan=False)
    alert_min: float = Field(2.0, gt=0, le=RR_MAX, allow_inf_nan=False, validate_default=True)

    @field_validator("alert_min")
    @classmethod
    def _not_above_min(cls, value: float, info: ValidationInfo) -> float:
        bound = info.data.get("min")
        if bound is not None and value > bound:
            raise PydanticCustomError(
                "less_than_equal",
                "alert_min {value} should be at most min {le}",
                {"value": str(value), "le": str(bound)},
            )
        return value


class OppositionInsideTargetConfig(GateConfig):
    """Fail on a large other Node beyond entry within ``window_r`` R (Req 11.8)."""

    fraction: float = Field(0.85, ge=FRACTION_MIN, le=1.0, allow_inf_nan=False)
    window_r: float = Field(3.0, gt=0, le=RR_MAX, allow_inf_nan=False)


class FomoTravelConfig(GateConfig):
    """Fail when the move since the Tap began exceeds ``max_fraction`` of the way to TP1."""

    max_fraction: float = Field(0.60, gt=0, le=10.0, allow_inf_nan=False)


class OpenShuffleConfig(GateConfig):
    """Fail fewer than ``min_minutes`` after 09:30."""

    min_minutes: int = Field(15, ge=0, le=RTH_MINUTES)


class EntryCutoffConfig(GateConfig):
    """Fail at or after ``cutoff``."""

    cutoff: WallTime = time(15, 25)


class LateSessionChaseConfig(GateConfig):
    """Fail a Trend_Follow or a setup toward the King in ``[start, end)``."""

    start: WallTime = time(15, 30)
    end: WallTime = Field(time(16, 0), validate_default=True)

    @field_validator("end")
    @classmethod
    def _after_start(cls, value: time, info: ValidationInfo) -> time:
        _require_after("end", value, info, "start")
        return value


class NewsWindowConfig(GateConfig):
    """Fail within ``minutes`` of a release of one of ``event_types``."""

    minutes: int = Field(5, ge=0, le=NEWS_MINUTES_MAX)
    event_types: Annotated[YamlList[EventType], Field(min_length=1)] = DEFAULT_NEWS_EVENT_TYPES

    @field_validator("event_types")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique(value)


class VixGapGateConfig(GateConfig):
    """Fail a session VIX gap of ``pct`` percent or more before ``until``."""

    pct: float = Field(15.0, gt=0, allow_inf_nan=False)
    until: WallTime = time(12, 0)


class AllowedRegimesConfig(SchemaModel):
    """The Regimes each detector's Candidate_Setups may trade in; the Playbook_Baseline sets."""

    gatekeeper_fade: RegimeList = ("Positive_Gamma", "Whipsaw")
    floor_ceiling_bounce: RegimeList = ("Positive_Gamma", "Whipsaw")
    floor_ceiling_bounce_empty_basement: RegimeList = ("Positive_Gamma", "Whipsaw")
    beach_ball: RegimeList = ("Positive_Gamma", "Whipsaw")
    rug: RegimeList = ("Positive_Gamma", "Negative_Gamma")
    reverse_rug: RegimeList = ("Positive_Gamma", "Negative_Gamma")
    whipsaw_fade: RegimeList = ("Whipsaw",)
    trend_follow: RegimeList = ("Negative_Gamma", "Vanna_Dominant")

    @field_validator("*")
    @classmethod
    def _unique(cls, value: tuple[RegimeName, ...]) -> tuple[RegimeName, ...]:
        return require_unique(value)

    def for_detector(self, detector_id: str) -> tuple[RegimeName, ...]:
        """The allowed Regimes of ``detector_id``; ``ValueError`` for an unknown id."""
        if detector_id not in type(self).model_fields:
            raise ValueError(f"{detector_id!r} is not a detector id")
        allowed: tuple[RegimeName, ...] = getattr(self, detector_id)
        return allowed


class RegimeMatchConfig(GateConfig):
    """Fail a Regime outside the detector's allowed set, or a missing Regime."""

    allowed: AllowedRegimesConfig = AllowedRegimesConfig()


class DormantNodeConfig(GateConfig):
    """Fail when the source Node is Dormant."""


class GatekeepersOnPathConfig(GateConfig):
    """Fail at ``fail_at`` or more Gatekeepers strictly between entry and TP1."""

    fail_at: int = Field(2, ge=1, le=50)


class NodeGrowthDivergenceConfig(GateConfig):
    """Fail a fade whose source Node_Velocity is ``fail_at_pct`` or more, or unavailable."""

    fail_at_pct: float = Field(20.0, ge=0, le=1000, allow_inf_nan=False)


class KillSwitchLockoutConfig(GateConfig):
    """Fail while any Lockout is active (Req 16.9)."""


# ---------------------------------------------------------------- section


class GatesConfig(SchemaModel):
    """The Gate order, the fade detectors and one entry per Gate, keyed by Gate id."""

    order: YamlList[GateId] = GATE_IDS
    fade_detectors: YamlList[GateDetectorId] = DEFAULT_FADE_DETECTORS
    stale_map: StaleMapConfig = StaleMapConfig()
    map_grade: MapGradeGateConfig = MapGradeGateConfig()
    midpoint: MidpointConfig = MidpointConfig()
    deflection_band: DeflectionBandConfig = DeflectionBandConfig()
    chart_confluence: ChartConfluenceConfig = ChartConfluenceConfig()
    stdev_fib_zone: StdevFibZoneConfig = StdevFibZoneConfig()
    dark_pool_confluence: DarkPoolConfluenceConfig = DarkPoolConfluenceConfig()
    trinity_agreement: TrinityAgreementConfig = TrinityAgreementConfig()
    candle_color: CandleColorConfig = CandleColorConfig()
    tap_count: TapCountConfig = TapCountConfig()
    third_gatekeeper_test: ThirdGatekeeperTestConfig = ThirdGatekeeperTestConfig()
    weekly_node_tests: WeeklyNodeTestsConfig = WeeklyNodeTestsConfig()
    sloppy_seconds: SloppySecondsGateConfig = SloppySecondsGateConfig()
    air_pocket_fade: AirPocketFadeConfig = AirPocketFadeConfig()
    min_reward_risk: MinRewardRiskConfig = MinRewardRiskConfig()
    opposition_inside_target: OppositionInsideTargetConfig = OppositionInsideTargetConfig()
    fomo_travel: FomoTravelConfig = FomoTravelConfig()
    open_shuffle: OpenShuffleConfig = OpenShuffleConfig()
    entry_cutoff: EntryCutoffConfig = EntryCutoffConfig()
    late_session_chase: LateSessionChaseConfig = LateSessionChaseConfig()
    news_window: NewsWindowConfig = NewsWindowConfig()
    vix_gap: VixGapGateConfig = VixGapGateConfig()
    regime_match: RegimeMatchConfig = RegimeMatchConfig()
    dormant_node: DormantNodeConfig = DormantNodeConfig()
    gatekeepers_on_path: GatekeepersOnPathConfig = GatekeepersOnPathConfig()
    node_growth_divergence: NodeGrowthDivergenceConfig = NodeGrowthDivergenceConfig()
    kill_switch_lockout: KillSwitchLockoutConfig = KillSwitchLockoutConfig()

    @field_validator("order")
    @classmethod
    def _every_gate_once(cls, value: tuple[GateId, ...]) -> tuple[GateId, ...]:
        require_unique(value)
        missing = [g for g in GATE_IDS if g not in value]
        if missing:
            raise PydanticCustomError(
                "missing",
                "order should list every Gate id once; it lacks {missing}",
                {"missing": ", ".join(missing)},
            )
        return value

    @field_validator("fade_detectors")
    @classmethod
    def _unique_detectors(cls, value: tuple[GateDetectorId, ...]) -> tuple[GateDetectorId, ...]:
        return require_unique(value)

    def get(self, gate_id: str) -> GateConfig:
        """The entry of ``gate_id``; ``ValueError`` for a name that is not a Gate id."""
        if gate_id not in GATE_IDS:
            raise ValueError(f"{gate_id!r} is not one of the Gate ids {GATE_IDS}")
        cfg: GateConfig = getattr(self, gate_id)
        return cfg

    def enabled(self, gate_id: str) -> bool:
        """The enabled flag of ``gate_id``."""
        return self.get(gate_id).enabled

    def enabled_ids(self) -> tuple[GateId, ...]:
        """The enabled Gate ids, in ``order``."""
        return tuple(g for g in self.order if self.enabled(g))
