"""Property 50: Config round-trip.

*For any* Strategy_Config that matches the Config_Schema, printing it writes
every key, and loading the printed file returns a config with the same ids,
enabled flags and parameter values, each number exactly equal.

The generator writes the plain mapping a YAML safe loader returns for a
Strategy_Config file and validates it with the Config_Loader
(``validate_data``), which must accept it. Every section is generated. About
half the examples set every key at every level; the others leave any key out,
so its schema default gets printed. Values stay inside their schema ranges:
floats anywhere in range (bounds, subnormals, huge values), money as decimal
strings with any number of digits, account dollars in cents, ``"HH:MM"``
times, per-Regime exit settings set or ``null``, Gate orders as any
permutation of the 27 Gate ids, and the cross-key rules (``data`` symbols and
sources, ``midpoint.lo < hi``, ``alert_min <= min``, ``start < end`` windows,
the Maximum Loss Limit below the starting balance, costs for each traded
instrument).

For each generated config ``cfg``:

1. ``load_text(dump(cfg))`` is ``LoadOk`` with a config equal to ``cfg``,
   field by field with the same type and ``repr``. So ``-0.0`` versus ``0.0``,
   floats that only ``repr`` tells apart, and ``Decimal("0.370")`` versus
   ``Decimal("0.37")`` all count as differences.
2. The printed mapping has every schema key, in declaration order at every
   level. The root reads ``schema_version``, ``config_id``, ``order_mode``,
   then :data:`SECTION_KEYS`.
3. ``dump`` is idempotent: printing the loaded config gives the same text.
4. ``config_hash`` is the same for ``cfg``, the loaded config and a second
   config validated from the same mapping.
5. Changing one leaf of the printed mapping to another valid value changes the
   config and its ``config_hash``. A change is a flipped flag, the next float
   up or down, a cent, a minute, a reordered, shortened or longer list, another
   allowed value, or ``null``. Each example tries up to 8 leaves.

**Validates: Requirements 17.7, 17.8**
"""

from __future__ import annotations

import copy
import functools
import math
import operator
import re
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, time
from decimal import Decimal
from typing import Annotated, Any, Final, Literal, TypeAliasType, get_args, get_origin

import pytest
import yaml
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from fse.config.hashing import config_hash
from fse.config.loader import LoadErr, LoadResult, key_path, load_text, validate_data
from fse.config.printer import dump
from fse.config.schema import ORDER_MODES, SCHEMA_VERSION, SECTION_KEYS, StrategyConfig
from fse.config.schema.account import (
    CONSISTENCY_PCT_MAX,
    EARLY_CLOSE_OFFSET_MAX_MIN,
    POSITION_CAP_MAX,
    USD_MAX,
    USD_MIN,
)
from fse.config.schema.bootstrap import BOOTSTRAP_RESAMPLES_MAX, BOOTSTRAP_RESAMPLES_MIN
from fse.config.schema.chart import (
    CHART_TIMEFRAMES_S,
    PIVOT_LEN_MAX,
    SWEEP_TICKS_MAX,
    SWINGS_KEPT_MAX,
)
from fse.config.schema.data import (
    DEFAULT_NQ_SOURCES,
    DEFAULT_SYMBOLS,
    MAX_EXPIRATIONS_CAP,
    MAX_STRIKES_CAP,
    VELOCITY_WINDOW_MAX_S,
)
from fse.config.schema.exits import (
    BREAKEVEN_OFFSET_TICKS_MAX,
    BREAKEVEN_TRIGGER_R_MAX,
    BREAKEVEN_TRIGGER_R_MIN,
    EXIT_MODES,
    EXIT_REGIMES,
    EXIT_STOP_RULES,
    R_MULTIPLE_MAX,
    R_MULTIPLE_MIN,
    TARGET_RULES,
    TP1_FRACTION_MAX,
    TP1_FRACTION_MIN,
    TRAILING_MODES,
    TRAILING_TICKS_MAX,
)
from fse.config.schema.experiments import (
    HOLDOUT_FRACTION_MAX,
    HOLDOUT_FRACTION_MIN,
    RANKING_OBJECTIVES,
)
from fse.config.schema.fills import FEE_MAX_USD, FEE_MIN_USD, FILL_INSTRUMENTS, FILL_TICKS_MAX
from fse.config.schema.gates import FIB_RATIO_MAX, FIB_RATIO_MIN, GATE_IDS, RR_MAX
from fse.config.schema.levels import (
    ES_HALF_WIDTH_MAX_PTS,
    ES_HALF_WIDTH_MIN_PTS,
    MAX_PRICE_GAP_MAX_S,
    MAX_PRICE_GAP_MIN_S,
    QQQ_HALF_WIDTH_MAX_USD,
    QQQ_HALF_WIDTH_MIN_USD,
)
from fse.config.schema.live import (
    INTERVAL_MAX_S,
    LIVE_MODES,
    OUTAGE_MAX_S,
    OUTAGE_MIN_S,
    REFRESH_INTERVAL_MIN_S,
    TIMEOUT_MAX_S,
)
from fse.config.schema.nodes import (
    FRACTION_MIN,
    GATEKEEPER_FRACTION_MAX,
    PCT_MAX,
    SLOPPY_WINDOW_MAX_MIN,
)
from fse.config.schema.notify import (
    INTERVAL_MAX_MIN,
    NARRATOR_MAX_CHARS_MAX,
    NARRATOR_TIMEOUT_MAX_S,
    SINK_NAMES,
)
from fse.config.schema.orders import (
    CANCEL_TRIGGERS,
    EARLY_CLOSE_FLATTEN_LEAD_MAX_MIN,
    EARLY_CLOSE_FLATTEN_LEAD_MIN_MIN,
    INVALIDATION_ACTIONS,
    MAX_AGE_MAX_MIN,
    MAX_OPEN_MAX,
)
from fse.config.schema.patterns import (
    ARMING_ES_MAX_PTS,
    ARMING_NQ_MAX_USD,
    DETECTOR_IDS,
    ENTRY_OFFSET_TICKS_MAX,
    FIXED_STOP_TICKS_MAX,
    INVALIDATION_LEVELS,
    MAX_INTERMEDIATE_MAX,
    PATTERN_STOP_RULES,
)
from fse.config.schema.reporting import MIN_SAMPLE_MAX, WIN_RATE_DEFINITIONS
from fse.config.schema.sizing import SIZING_INSTRUMENTS, SIZING_MODES
from fse.config.schema.time import DECISION_CADENCE_MAX_S, SESSION_TIMEZONE
from fse.timekit import RTH_CLOSE, RTH_OPEN, TRADING_DAY_START

type KeyPath = tuple[str | int, ...]
type Section = st.SearchStrategy[dict[str, Any]]

MINUTES_PER_DAY: Final = 24 * 60
CENT: Final = Decimal("0.01")
CHANGED_LEAVES_PER_EXAMPLE: Final = 8
ROOT_KEYS: Final = ("schema_version", "config_id", "order_mode", *SECTION_KEYS)


def minute_of(t: time) -> int:
    return t.hour * 60 + t.minute


def hhmm(minute: int) -> str:
    """A minute of the day as the quoted ``"HH:MM"`` text the schema reads."""
    return f"{minute // 60:02d}:{minute % 60:02d}"


# ---------------------------------------------------------------- value strategies

BOOL: Final = st.booleans()


def floats(lo: float, hi: float | None, *, gt: bool = False) -> st.SearchStrategy[float]:
    """Finite floats from ``lo`` (excluded with ``gt``) to ``hi`` (``None``: no upper bound)."""
    return st.floats(
        min_value=lo, max_value=hi, exclude_min=gt, allow_nan=False, allow_infinity=False
    )


def unique_lists(values: Sequence[object], min_size: int = 0) -> st.SearchStrategy[list[Any]]:
    """Lists of distinct ``values`` in any order."""
    return st.lists(st.sampled_from(values), min_size=min_size, max_size=len(values), unique=True)


def money(
    lo: Decimal | None = None, hi: Decimal | None = None, *, above: Decimal | None = None
) -> st.SearchStrategy[str]:
    """Quoted decimal text for a ``YamlMoney`` key: from ``lo`` (or above ``above``) to ``hi``."""
    values = st.decimals(
        min_value=above if above is not None else lo,
        max_value=hi,
        allow_nan=False,
        allow_infinity=False,
    )
    if above is not None:
        values = values.filter(functools.partial(operator.lt, above))
    return values.map(str)


def usd_text(cents: int, short: bool) -> str:
    """Account dollars: ``"123.45"``, or ``"123"`` for whole dollars when ``short``."""
    if short and cents % 100 == 0:
        return str(cents // 100)
    return f"{cents // 100}.{cents % 100:02d}"


USD_CENTS_MIN: Final = int(USD_MIN * 100)
USD_CENTS_MAX: Final = int(USD_MAX * 100)


def usd(lo_cents: int = USD_CENTS_MIN, hi_cents: int = USD_CENTS_MAX) -> st.SearchStrategy[str]:
    return st.builds(usd_text, st.integers(lo_cents, hi_cents), BOOL)


def times(lo: int, hi: int) -> st.SearchStrategy[str]:
    """``"HH:MM"`` times from minute ``lo`` to minute ``hi`` of the day, both included."""
    return st.integers(lo, hi).map(hhmm)


# account.WallTime and the Gate times: after 09:30 and before 18:00.
WALL_MINUTES: Final = st.integers(minute_of(RTH_OPEN) + 1, minute_of(TRADING_DAY_START) - 1)
WALL: Final = WALL_MINUTES.map(hhmm)
FLATTEN: Final = times(minute_of(RTH_OPEN), minute_of(RTH_CLOSE))  # 09:30 to 16:00
PREMARKET: Final = times(0, minute_of(RTH_OPEN) - 1)  # before 09:30

CONFIG_IDS: Final = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", fullmatch=True)
EVENT_TYPES: Final = st.from_regex(r"[A-Z][A-Z0-9_]{0,31}", fullmatch=True)
INSTRUMENT_SYMBOLS: Final = st.from_regex(r"[A-Z][A-Z0-9]{0,9}", fullmatch=True)
CONTRACT_IDS: Final = st.from_regex(r"[A-Z][A-Z0-9]{0,8}(\.[A-Z0-9]{1,8}){1,4}", fullmatch=True)
NARRATOR_URLS: Final = st.from_regex(
    r"https?://[A-Za-z0-9.-]{1,40}(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]{0,40})?", fullmatch=True
)
MODEL_NAMES: Final = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", fullmatch=True)
ISO_DATES: Final = st.dates().map(date.isoformat)
R_MULTIPLES: Final = floats(R_MULTIPLE_MIN, R_MULTIPLE_MAX)
FEES: Final = money(FEE_MIN_USD, FEE_MAX_USD)
PCT: Final = floats(0.0, PCT_MAX, gt=True)  # a percent above 0 and at most 100
FRACTION: Final = floats(FRACTION_MIN, 1.0)
POSITIVE: Final = floats(0.0, None, gt=True)


# ---------------------------------------------------------------- mapping strategies

# One draw per example: a full config sets every key at every level, so deep
# keys get generated values; a sparse one leaves any key to its default.
FULL: Final = st.shared(BOOL, key="p50-full-config")


def omitted(draw: st.DrawFn) -> bool:
    """Whether to leave an omissible key out: never in a full config, else at random."""
    return not draw(FULL) and draw(BOOL)


def section(
    optional: Mapping[str, st.SearchStrategy[Any]],
    required: Mapping[str, st.SearchStrategy[Any]] | None = None,
) -> Section:
    """A mapping with every ``required`` key and every (full) or any subset of ``optional`` key."""
    required = dict(required or {})
    full = st.fixed_dictionaries({**required, **optional})
    sparse = st.fixed_dictionaries(required, optional=dict(optional))
    return FULL.flatmap(lambda is_full: full if is_full else sparse)


def merged(*parts: Section) -> Section:
    return st.tuples(*parts).map(lambda ds: {k: v for d in ds for k, v in d.items()})


@st.composite
def ordered(
    draw: st.DrawFn,
    keys: tuple[str, str],
    values: st.SearchStrategy[float],
    defaults: tuple[float, float],
    fmt: Callable[[Any], object] | None = None,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Two keys whose values are in order (``<``, or ``<=`` unless ``strict``).

    A key is left out only when its schema default keeps the order with the
    other key's value.
    """

    def in_order(lo: float, hi: float) -> bool:
        return lo < hi if strict else lo <= hi

    lo, hi = sorted((draw(values), draw(values)))
    assume(in_order(lo, hi))
    out: dict[str, Any] = {keys[0]: lo, keys[1]: hi}
    if in_order(defaults[0], hi) and omitted(draw):
        del out[keys[0]]
        lo = defaults[0]
    if in_order(lo, defaults[1]) and omitted(draw):
        del out[keys[1]]
    return {k: v if fmt is None else fmt(v) for k, v in out.items()}


def gate(params: Mapping[str, st.SearchStrategy[Any]] | None = None) -> Section:
    return section({"enabled": BOOL, **(params or {})})


# ---------------------------------------------------------------- sections

TIME: Final = section(
    {
        "decision_cadence_s": st.integers(1, DECISION_CADENCE_MAX_S),
        "timezone": st.just(SESSION_TIMEZONE),
    }
)


@st.composite
def data_sections(draw: st.DrawFn) -> dict[str, Any]:
    """``data``: each named symbol is in ``symbols``; the NQ source symbol is in ``nq_sources``."""
    out: dict[str, Any] = draw(
        section(
            {
                "heatmap_view": section(
                    {
                        "max_strikes": st.integers(1, MAX_STRIKES_CAP) | st.just("all"),
                        "max_expirations": st.integers(1, MAX_EXPIRATIONS_CAP) | st.just("all"),
                        "expirations": st.none()
                        | st.lists(ISO_DATES, min_size=1, max_size=5, unique=True),
                        "include_empty": BOOL,
                    }
                ),
                "instruments": section(
                    {
                        "es_levels": st.sampled_from(("ES", "MES")),
                        "nq_levels": st.sampled_from(("NQ", "MNQ")),
                    }
                ),
                "velocity_window_s": st.integers(1, VELOCITY_WINDOW_MAX_S),
            }
        )
    )
    symbols: list[str] = list(DEFAULT_SYMBOLS)
    if not omitted(draw):
        symbols = draw(unique_lists(DEFAULT_SYMBOLS, min_size=1))
        out["symbols"] = symbols
    for key in ("regime_symbol", "es_source_symbol"):  # both default to SPX
        if "SPX" not in symbols or not omitted(draw):
            out[key] = draw(st.sampled_from(symbols))
    if set(DEFAULT_NQ_SOURCES) <= set(symbols) and omitted(draw):
        nq_sources = list(DEFAULT_NQ_SOURCES)
    else:
        nq_sources = draw(unique_lists(symbols, min_size=1))
        out["nq_sources"] = nq_sources
    if "QQQ" not in nq_sources or not omitted(draw):  # nq_source_symbol defaults to QQQ
        out["nq_source_symbol"] = draw(st.sampled_from(nq_sources))
    return out


NODES: Final = section(
    {
        "node_fraction": FRACTION,
        "gatekeeper_fraction": floats(FRACTION_MIN, GATEKEEPER_FRACTION_MAX),
        "air_pocket_min_width_pct": PCT,
        "lookout_pct": PCT,
        "decay_fraction": FRACTION,
        "dormant_distance_pct": PCT,
        "sloppy_seconds": section(
            {"window_min": st.integers(1, SLOPPY_WINDOW_MAX_MIN), "fraction": FRACTION}
        ),
        "clustering": section({"enabled": BOOL, "es_width_pts": POSITIVE}),
    }
)

REGIME: Final = section(
    {
        "regime_distance_pct": PCT,
        "vanna_multiple": POSITIVE,
        "whipsaw_pct": PCT,
        "vix_condition": section(
            {"enabled": BOOL, "pct": floats(0.0, None), "intraday_source": BOOL}
        ),
        "grade": section({"major_fraction": FRACTION, "floor_ceiling_ratio": floats(1.0, None)}),
    },
    required={"min_abs_value": POSITIVE},
)

LEVELS: Final = section(
    {
        "methods": section(
            {s: st.sampled_from(("offset", "ratio")) for s in ("SPY", "NDX", "NDXP")}
        ),
        "es_half_width_pts": floats(ES_HALF_WIDTH_MIN_PTS, ES_HALF_WIDTH_MAX_PTS),
        "qqq_half_width_usd": floats(QQQ_HALF_WIDTH_MIN_USD, QQQ_HALF_WIDTH_MAX_USD),
        "max_price_gap_s": st.integers(MAX_PRICE_GAP_MIN_S, MAX_PRICE_GAP_MAX_S),
    }
)

PIVOT_LEN: Final = st.integers(1, PIVOT_LEN_MAX)
CHART: Final = section(
    {
        "pivot_len": section({"60": PIVOT_LEN, "240": PIVOT_LEN}),
        "swings_kept": st.integers(1, SWINGS_KEPT_MAX),
        "bos_pivot_len": section({"1": PIVOT_LEN, "5": PIVOT_LEN}),
        "sweep_ticks": st.integers(1, SWEEP_TICKS_MAX),
        "candle_timeframe_s": st.sampled_from(CHART_TIMEFRAMES_S),
        "sweep_timeframe_s": st.sampled_from(CHART_TIMEFRAMES_S),
    }
)

DETECTOR_KEYS: Final[Mapping[str, st.SearchStrategy[Any]]] = {
    "enabled": BOOL,
    "metric": st.sampled_from(("gamma", "vanna")),
    "entry_offset_ticks": st.integers(-ENTRY_OFFSET_TICKS_MAX, ENTRY_OFFSET_TICKS_MAX),
    "stop_rule": st.none() | st.sampled_from(sorted(PATTERN_STOP_RULES)),
    "fixed_stop_ticks": st.integers(1, FIXED_STOP_TICKS_MAX),
    "invalidation": st.sampled_from(sorted(INVALIDATION_LEVELS)),
    "stop_lookout_pct": PCT,
    "arming": section(
        {
            "es_pts": floats(0.0, ARMING_ES_MAX_PTS, gt=True),
            "nq_qqq_usd": floats(0.0, ARMING_NQ_MAX_USD, gt=True),
        }
    ),
}
DETECTOR_EXTRA_KEYS: Final[Mapping[str, Mapping[str, st.SearchStrategy[Any]]]] = {
    "beach_ball": {"major_fraction": FRACTION},
    "rug": {"stack_pct": PCT},
    "reverse_rug": {"stack_pct": PCT},
    "trend_follow": {
        "trend_min_pct": PCT,
        "max_intermediate": st.integers(0, MAX_INTERMEDIATE_MAX),
    },
}
PATTERNS: Final = section(
    {d: section({**DETECTOR_KEYS, **DETECTOR_EXTRA_KEYS.get(d, {})}) for d in DETECTOR_IDS}
)

GATE_SECTIONS: Final[Mapping[str, Section]] = {
    "stale_map": gate({"max_snapshot_age_s": st.integers(1, 3600)}),
    "map_grade": gate({"fail_grades": unique_lists(("A_Plus_Map", "Neutral_Map", "F_Map"))}),
    "midpoint": merged(gate(), ordered(("lo", "hi"), floats(0.0, 1.0), (0.33, 0.67))),
    "deflection_band": gate(),
    "chart_confluence": gate(),
    "stdev_fib_zone": gate(
        {
            "zones": st.lists(
                st.fixed_dictionaries(
                    {
                        "near": floats(FIB_RATIO_MIN, FIB_RATIO_MAX),
                        "far": floats(FIB_RATIO_MIN, FIB_RATIO_MAX),
                    }
                ),
                min_size=1,
                max_size=4,
            )
        }
    ),
    "dark_pool_confluence": gate(
        {
            "min_notional_usd": POSITIVE,
            "lookback_sessions": st.integers(1, 60),
            "es_ticker": st.sampled_from(("SPX", "SPY")),
            "nq_ticker": st.sampled_from(("QQQ", "NDX", "NDXP")),
        }
    ),
    "trinity_agreement": gate({"min_agree": st.integers(1, 3), "empty_basement_exception": BOOL}),
    "candle_color": gate(),
    "tap_count": gate({"max_tap_seq": st.integers(1, 100)}),
    "third_gatekeeper_test": gate({"fail_at_test": st.integers(1, 100)}),
    "weekly_node_tests": gate({"max_tests": st.integers(1, 100)}),
    "sloppy_seconds": gate(),
    "air_pocket_fade": gate({"max_depth_pts": floats(0.0, 100.0)}),
    "min_reward_risk": merged(
        gate(),
        ordered(("alert_min", "min"), floats(0.0, RR_MAX, gt=True), (2.0, 3.0), strict=False),
    ),
    "opposition_inside_target": gate(
        {"fraction": FRACTION, "window_r": floats(0.0, RR_MAX, gt=True)}
    ),
    "fomo_travel": gate({"max_fraction": floats(0.0, 10.0, gt=True)}),
    "open_shuffle": gate({"min_minutes": st.integers(0, 390)}),
    "entry_cutoff": gate({"cutoff": WALL}),
    "late_session_chase": merged(
        gate(), ordered(("start", "end"), WALL_MINUTES, (15 * 60 + 30, 16 * 60), hhmm)
    ),
    "news_window": gate(
        {
            "minutes": st.integers(0, 240),
            "event_types": st.lists(EVENT_TYPES, min_size=1, max_size=4, unique=True),
        }
    ),
    "vix_gap": gate({"pct": POSITIVE, "until": WALL}),
    "regime_match": gate(
        {"allowed": section({d: unique_lists(EXIT_REGIMES) for d in DETECTOR_IDS})}
    ),
    "dormant_node": gate(),
    "gatekeepers_on_path": gate({"fail_at": st.integers(1, 50)}),
    "node_growth_divergence": gate({"fail_at_pct": floats(0.0, 1000.0)}),
    "kill_switch_lockout": gate(),
}
assert tuple(GATE_SECTIONS) == GATE_IDS, "a Gate has no generator"

GATES: Final = section(
    {
        "order": st.permutations(GATE_IDS),
        "fade_detectors": unique_lists(DETECTOR_IDS),
        **GATE_SECTIONS,
    }
)

EXIT_STOP_RULE: Final = st.sampled_from(sorted(EXIT_STOP_RULES))
EXIT_SETTING: Final = st.fixed_dictionaries(
    {"mode": st.sampled_from(EXIT_MODES), "stop_rule": EXIT_STOP_RULE}
)
TARGET_RULE: Final = st.sampled_from(sorted(TARGET_RULES))
EXITS: Final = section(
    {
        "global": section({"mode": st.sampled_from(EXIT_MODES), "stop_rule": EXIT_STOP_RULE}),
        "per_regime": section({r: st.none() | EXIT_SETTING for r in EXIT_REGIMES}),
        "modes": section(
            {
                "fixed_r": section({"enabled": BOOL, "r_multiple": R_MULTIPLES}),
                "next_node": section({"enabled": BOOL}),
                "tp1_partial_be": section(
                    {
                        "enabled": BOOL,
                        "tp1": section({"rule": TARGET_RULE, "r_multiple": R_MULTIPLES}),
                        "tp2": section({"rule": TARGET_RULE, "r_multiple": R_MULTIPLES}),
                        "tp1_fraction": floats(TP1_FRACTION_MIN, TP1_FRACTION_MAX),
                    }
                ),
                "opposition_or_fixed_r": section(
                    {"enabled": BOOL, "r_multiple": R_MULTIPLES, "fraction": FRACTION}
                ),
                "trailing": section(
                    {
                        "enabled": BOOL,
                        "mode": st.sampled_from(sorted(TRAILING_MODES)),
                        "ticks": st.integers(1, TRAILING_TICKS_MAX),
                    }
                ),
            }
        ),
        "breakeven": section(
            {
                "enabled": BOOL,
                "trigger_r": floats(BREAKEVEN_TRIGGER_R_MIN, BREAKEVEN_TRIGGER_R_MAX),
                "offset_ticks": st.integers(0, BREAKEVEN_OFFSET_TICKS_MAX),
            }
        ),
    }
)

ORDERS: Final = section(
    {
        "flatten_time": FLATTEN,
        "early_close_flatten_lead_min": st.integers(
            EARLY_CLOSE_FLATTEN_LEAD_MIN_MIN, EARLY_CLOSE_FLATTEN_LEAD_MAX_MIN
        ),
        "max_open": st.integers(1, MAX_OPEN_MAX),
        "max_age_min": st.none() | st.integers(1, MAX_AGE_MAX_MIN),
        "cancel_triggers": section({t: BOOL for t in CANCEL_TRIGGERS}),
        "invalidation": section(
            {t: st.none() | st.sampled_from(INVALIDATION_ACTIONS) for t in CANCEL_TRIGGERS}
        ),
    }
)

INSTRUMENT_COSTS: Final = st.fixed_dictionaries({"commission": FEES, "exchange_fee": FEES})


def fills_sections(instruments: Mapping[str, Any]) -> Section:
    """``fills``: each instrument ``data.instruments`` trades has costs; the others may."""
    traded = {instruments.get("es_levels", "MES"), instruments.get("nq_levels", "MNQ")}
    costs = st.fixed_dictionaries(
        {i: INSTRUMENT_COSTS for i in FILL_INSTRUMENTS if i in traded},
        optional={i: st.none() | INSTRUMENT_COSTS for i in FILL_INSTRUMENTS if i not in traded},
    )
    return section(
        {
            "trade_through_ticks": st.integers(0, FILL_TICKS_MAX),
            "slippage_ticks": st.integers(0, FILL_TICKS_MAX),
        },
        required={"costs": costs},
    )


CONTRACT_COUNTS: Final = section({i: st.integers(min_value=1) for i in SIZING_INSTRUMENTS})
SIZING: Final = section(
    {
        "mode": st.sampled_from(sorted(SIZING_MODES)),
        "fixed": CONTRACT_COUNTS,
        "risk_usd": money(above=Decimal(0)),
        "big_win": section(
            {"usd": money(above=Decimal(0)), "r": POSITIVE, "reduced": CONTRACT_COUNTS}
        ),
        "trinity_size_down": section({"enabled": BOOL, "fraction": floats(0.0, 1.0, gt=True)}),
        "vix_gap": section({"enabled": BOOL, "pct": POSITIVE}),
        "micro_equivalent_limit": st.integers(min_value=1),
    }
)

KILL_SWITCHES: Final = section(
    {
        "max_trades": section({"enabled": BOOL, "max": st.integers(min_value=1)}),
        "max_losers": section({"enabled": BOOL, "limit": st.integers(min_value=1)}),
        "consecutive_losers": section({"enabled": BOOL, "limit": st.integers(min_value=1)}),
        "red_day": section({"enabled": BOOL, "threshold_usd": money()}),
        "daily_profit_cap": section({"enabled": BOOL, "cap_usd": money(above=Decimal(0))}),
        "internal_daily_loss_stop": section({"enabled": BOOL, "multiple": POSITIVE}),
        "losing_trade_tolerance_usd": money(Decimal(0)),
    }
)

DEFAULT_START_CENTS: Final = 5_000_000  # $50,000.00
DEFAULT_MLL_CENTS: Final = 200_000  # $2,000.00


@st.composite
def account_sections(draw: st.DrawFn) -> dict[str, Any]:
    """``account``: the Maximum Loss Limit stays below the starting balance."""
    out: dict[str, Any] = draw(
        section(
            {
                "profit_target": section({"enabled": BOOL, "value": usd()}),
                "daily_loss_limit": section({"enabled": BOOL, "value": usd()}),
                "consistency_target": section(
                    {"enabled": BOOL, "pct": floats(0.0, CONSISTENCY_PCT_MAX, gt=True)}
                ),
                "position_cap": section(
                    {"enabled": BOOL, "micro_equivalents": st.integers(1, POSITION_CAP_MAX)}
                ),
                "flat_deadline": WALL,
                "early_close_offset_min": st.integers(0, EARLY_CLOSE_OFFSET_MAX_MIN),
            }
        )
    )
    start = DEFAULT_START_CENTS
    if not omitted(draw):
        start = draw(st.integers(USD_CENTS_MIN + 1, USD_CENTS_MAX))
        out["starting_balance"] = {"value": usd_text(start, draw(BOOL))}
    elif draw(BOOL):
        out["starting_balance"] = {}
    mll: dict[str, Any] = draw(section({"enabled": BOOL}))
    if start <= DEFAULT_MLL_CENTS or not omitted(draw):
        mll["value"] = draw(usd(USD_CENTS_MIN, start - 1))
    if bool(mll) or draw(BOOL):
        out["maximum_loss_limit"] = mll
    return out


LIVE: Final = section(
    {
        "refresh_interval_s": st.integers(REFRESH_INTERVAL_MIN_S, INTERVAL_MAX_S),
        "mode": st.sampled_from(LIVE_MODES),
        "live_max_snapshot_age_s": st.integers(1, INTERVAL_MAX_S),
        "levels_compare_interval_s": st.integers(REFRESH_INTERVAL_MIN_S, INTERVAL_MAX_S),
        "run_window": ordered(
            ("start", "end"), st.integers(0, MINUTES_PER_DAY - 1), (9 * 60, 16 * 60), hhmm
        ),
        "stop_confirm_s": st.integers(1, TIMEOUT_MAX_S),
        "lookup_timeout_s": st.integers(1, TIMEOUT_MAX_S),
        "outage_s": st.integers(OUTAGE_MIN_S, OUTAGE_MAX_S),
        "ignored_instruments": st.lists(INSTRUMENT_SYMBOLS, max_size=4, unique=True),
        "expected_contracts": section(
            {i: st.none() | CONTRACT_IDS for i in ("MES", "MNQ", "ES", "NQ")}
        ),
    }
)

NOTIFY: Final = section(
    {
        "sinks": unique_lists(SINK_NAMES, min_size=1),
        "interval_min": st.integers(1, INTERVAL_MAX_MIN),
        "premarket": PREMARKET,
        "alerts_2r": BOOL,
        "narrator": section(
            {
                "enabled": BOOL,
                "base_url": st.none() | NARRATOR_URLS,
                "model": st.none() | MODEL_NAMES,
                "timeout_s": st.integers(1, NARRATOR_TIMEOUT_MAX_S),
                "max_chars": st.integers(1, NARRATOR_MAX_CHARS_MAX),
            }
        ),
    }
)

REPORTING: Final = section(
    {
        "primary_win_rate": st.sampled_from(WIN_RATE_DEFINITIONS),
        "scratch_tolerance_r": floats(0.0, 1.0),
        "min_sample_trades": st.integers(1, MIN_SAMPLE_MAX),
        "bootstrap_resamples": st.integers(BOOTSTRAP_RESAMPLES_MIN, BOOTSTRAP_RESAMPLES_MAX),
        "reference_win_rate": floats(0.0, 100.0, gt=True),
        "shadow_min_sample": st.integers(1, MIN_SAMPLE_MAX),
    }
)

OBJECTIVES: Final = st.sampled_from(RANKING_OBJECTIVES)
EXPERIMENTS: Final = section(
    {
        "holdout_fraction": floats(HOLDOUT_FRACTION_MIN, HOLDOUT_FRACTION_MAX),
        "ranking_objective": OBJECTIVES,
        "walkforward": section(
            {
                "train": st.integers(10, 500),
                "test": st.integers(5, 250),
                "objective": OBJECTIVES,
                "min_trades": st.integers(1, 1000),
            }
        ),
        "montecarlo": section(
            {
                "paths": st.integers(1_000, 1_000_000),
                "max_days": st.integers(1, 250),
                "min_sessions": st.integers(1, 1000),
            }
        ),
    }
)

# ``data`` and ``fills`` are drawn by ``config_data``: fills.costs depends on data.instruments.
SECTIONS: Final[Mapping[str, Section]] = {
    "time": TIME,
    "nodes": NODES,
    "regime": REGIME,
    "levels": LEVELS,
    "chart": CHART,
    "patterns": PATTERNS,
    "gates": GATES,
    "exits": EXITS,
    "orders": ORDERS,
    "sizing": SIZING,
    "kill_switches": KILL_SWITCHES,
    "account": account_sections(),
    "live": LIVE,
    "notify": NOTIFY,
    "reporting": REPORTING,
    "experiments": EXPERIMENTS,
}
assert {*SECTIONS, "data", "fills"} == set(SECTION_KEYS), "a section has no generator"


@st.composite
def config_data(draw: st.DrawFn) -> dict[str, Any]:
    """A Strategy_Config file's mapping: every section, any key left to its default."""
    out: dict[str, Any] = draw(
        section(
            {
                "schema_version": st.just(SCHEMA_VERSION),
                "config_id": CONFIG_IDS,
                "order_mode": st.sampled_from(ORDER_MODES),
            }
        )
    )
    for key in SECTION_KEYS:
        if key == "data":
            value = draw(data_sections())
        elif key == "fills":
            value = draw(fills_sections(out.get("data", {}).get("instruments", {})))
        else:
            value = draw(SECTIONS[key])
        if bool(value) or draw(BOOL):  # an empty section is written as {} or left out
            out[key] = value
    return out


# ---------------------------------------------------------------- helpers


def loaded(result: LoadResult) -> StrategyConfig:
    if isinstance(result, LoadErr):
        pytest.fail("\n".join(str(e) for e in result.errors))
    return result.config


def validated(raw: Mapping[str, Any]) -> StrategyConfig:
    """The config the Config_Loader builds from a generated mapping; it must accept it."""
    return loaded(validate_data(copy.deepcopy(dict(raw)), source="generated"))


def exact(value: object) -> object:
    """``value`` with each leaf as its type name and ``repr``: equal only when exactly equal."""
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        return (type(value).__name__, {name: exact(getattr(value, name)) for name in fields})
    if isinstance(value, tuple):
        return [exact(item) for item in value]
    return (type(value).__name__, repr(value))


def assert_schema_keys(model: BaseModel, printed: object, where: str = "") -> None:
    """``printed`` holds every key of ``model``'s schema, in declaration order, at every level."""
    assert isinstance(printed, dict), where
    fields = type(model).model_fields
    expected = [info.alias or name for name, info in fields.items()]
    assert list(printed) == expected, f"key order at {where or '<root>'}"
    for name, info in fields.items():
        key = info.alias or name
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            assert_schema_keys(value, printed[key], f"{where}{key}.")
        elif isinstance(value, tuple):
            for i, item in enumerate(value):
                if isinstance(item, BaseModel):
                    assert_schema_keys(item, printed[key][i], f"{where}{key}[{i}].")


# ---- one-leaf changes


@functools.cache
def schema_field(model: type[BaseModel], key: str) -> tuple[str, FieldInfo]:
    """The field name and info of the printed ``key`` (an alias or a name) of ``model``."""
    for name, info in model.model_fields.items():
        if (info.alias or name) == key:
            return name, info
    raise KeyError(f"{model.__name__} has no key {key!r}")


def resolve(cfg: StrategyConfig, path: KeyPath) -> tuple[object, object]:
    """The model value at a printed key path and its field annotation (``None`` for list items)."""
    value: object = cfg
    annotation: object = None
    for part in path:
        if isinstance(part, int):
            assert isinstance(value, tuple)
            value, annotation = value[part], None
        else:
            assert isinstance(value, BaseModel)
            name, info = schema_field(type(value), part)
            value, annotation = getattr(value, name), info.annotation
    return value, annotation


def members(tp: object) -> tuple[object, ...]:
    """The types a field annotation allows, with ``type`` aliases, ``Annotated`` and unions opened.

    A generic alias such as ``YamlList[Symbol]`` is kept whole.
    """
    if isinstance(tp, TypeAliasType):
        return members(tp.__value__)
    origin = get_origin(tp)
    if origin is Annotated:
        return members(get_args(tp)[0])
    if origin is types.UnionType:
        return tuple(m for arg in get_args(tp) for m in members(arg))
    return (tp,)


def literal_values(tp: object) -> list[object]:
    return [v for m in members(tp) if get_origin(m) is Literal for v in get_args(m)]


def allows_none(tp: object) -> bool:
    return any(m is type(None) for m in members(tp))


def item_type(tp: object) -> object:
    """``T`` of a ``YamlList[T]`` annotation, else ``None``."""
    for m in members(tp):
        if isinstance(get_origin(m), TypeAliasType):
            return get_args(m)[0]
    return None


def at(printed: Mapping[str, Any], path: KeyPath) -> Any:
    node: Any = printed
    for part in path:
        node = node[part]
    return node


def replaced(printed: Mapping[str, Any], path: KeyPath, value: object) -> dict[str, Any]:
    out = copy.deepcopy(dict(printed))
    at(out, path[:-1])[path[-1]] = value
    return out


def node_paths(value: object, path: KeyPath = ()) -> Iterator[KeyPath]:
    """The path of every value below the root of a printed mapping, mapping list items included."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield (*path, key)
            yield from node_paths(item, (*path, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, dict):
                yield (*path, i)
                yield from node_paths(item, (*path, i))


def list_changes(items: list[Any], item_tp: object) -> list[object]:
    out: list[object] = [items[::-1], items[1:], items[:-1]]
    out += [[*items, extra] for extra in literal_values(item_tp) if extra not in items][:1]
    if items:
        out.append([*items, items[0]])
    return out


def changes(cfg: StrategyConfig, printed: Mapping[str, Any], path: KeyPath) -> list[object]:
    """Other printed values for the node at ``path``; the schema may reject some of them."""
    value, annotation = resolve(cfg, path)
    current = at(printed, path)
    options: list[object] = []
    match value:
        case bool():
            options.append(not value)
        case int():
            options += [value + 1, value - 1]
        case float():
            options += [math.nextafter(value, math.inf), math.nextafter(value, -math.inf)]
        case Decimal():
            options += [str(value + CENT), str(value - CENT)]
        case time():
            minute = minute_of(value)
            options += [hhmm(m) for m in (minute + 1, minute - 1) if 0 <= m < MINUTES_PER_DAY]
        case str() if not literal_values(annotation):
            options += [value + "0", value[:-1]]
        case tuple():
            options += list_changes(current, item_type(annotation))
        case _:
            pass
    options += literal_values(annotation)
    if value is not None and allows_none(annotation):
        options.append(None)
    return [o for o in options if o != current]


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 50: Config round-trip
@given(raw=config_data())
def test_printing_then_loading_gives_back_the_same_config(raw: dict[str, Any]) -> None:
    cfg = validated(raw)
    text = dump(cfg)

    back = loaded(load_text(text, source="printed.yaml"))
    assert back == cfg
    assert exact(back) == exact(cfg), "a value changed type, sign or digits"

    printed = yaml.safe_load(text)
    assert printed == cfg.model_dump(mode="python")
    assert tuple(printed) == ROOT_KEYS
    assert_schema_keys(cfg, printed)

    assert dump(back) == text, "dump is not idempotent"

    digest = config_hash(cfg)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert config_hash(back) == digest
    assert config_hash(validated(raw)) == digest


# Feature: skylit-futures-strategy-engine, Property 50: Config round-trip
@given(raw=config_data(), data=st.data())
def test_changing_any_leaf_changes_the_config_hash(
    raw: dict[str, Any], data: st.DataObject
) -> None:
    cfg = validated(raw)
    printed = cfg.model_dump(mode="python")  # what dump writes (checked by the test above)
    options = {p: o for p in node_paths(printed) if (o := changes(cfg, printed, p))}
    paths = data.draw(
        st.lists(
            st.sampled_from(list(options)),
            min_size=1,
            max_size=CHANGED_LEAVES_PER_EXAMPLE,
            unique=True,
        ),
        label="leaves",
    )
    digest = config_hash(cfg)
    for path in paths:
        for option in options[path]:
            result = validate_data(replaced(printed, path, option), source="changed")
            if isinstance(result, LoadErr):
                continue  # not a valid value for this key
            where = key_path(path)
            assert exact(result.config) != exact(cfg), f"{where} = {option!r} changed nothing"
            assert config_hash(result.config) != digest, f"{where} = {option!r} kept the hash"
