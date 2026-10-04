"""Property 52: Defaults and contradiction warnings.

*For any* valid config file with any subset of defaulted keys omitted, the
loaded config has a value for every Pattern, Gate, Exit_Mode, account rule and
Kill_Switch, with omitted keys equal to their Config_Schema defaults; and
exactly one contradiction warning is returned per enabled Fixed_R or
Opposition_Or_Fixed_R R multiple (global or per Regime) below an enabled
min_reward_risk threshold.

**Inputs.** A full config file: the plain mapping of the minimal config
(``tests.fakes.configs``) with every Config_Schema key written out. On top of
it the test draws the Order_Mode and config id; the ``min_reward_risk``
enabled flag and ``min``; the enabled flag and ``r_multiple`` of Fixed_R and
Opposition_Or_Fixed_R; the ``exits.global`` setting and each
``exits.per_regime`` setting (a mode and stop rule, or ``null``); and a flip
of any other ``enabled`` flag. R multiples and ``min`` come mostly from one
shared list, so equal values (no warning) are common. Then a random set of
defaulted keys is omitted at any depth; an omitted section drops its keys.
Keys whose omission makes the file invalid stay: ``fills.costs.MES`` and
``fills.costs.MNQ`` with their sections (the traded instruments need costs,
Req 13.8), and ``min_reward_risk.alert_min`` when ``min`` is drawn below the
2.0 ``alert_min`` default.

**Oracle.** Req 17.2: each omitted key equals the default the Config_Schema
declares for it, and an omitted Order_Mode is Paper. Req 17.1: every Pattern,
Gate, Exit_Mode, account rule and Kill_Switch id has an entry whose enabled
flag is the file's, or, when omitted, the design-sketch default (on, except
``stdev_fib_zone``, ``dark_pool_confluence``, ``tp1_partial_be``,
``trailing`` and ``internal_daily_loss_stop``). Req 17.6 is restated from the
file with the design-sketch defaults (``min`` 3.0, both R multiples 3.0, all
three rules on): while the Gate is on, each enabled mode whose ``r_multiple``
is strictly below ``min`` gives one warning naming
``exits.modes.<mode>.r_multiple`` and ``gates.min_reward_risk.min`` and both
values; nothing else gives one. The schema holds one R multiple per mode and a
per-Regime setting only selects a mode, so the drawn global and per-Regime
selections must not change the warnings.

**Validates: Requirements 17.1, 17.2, 17.6**
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from types import UnionType
from typing import Any, Final, get_args

from hypothesis import event, given, note
from hypothesis import strategies as st
from pydantic import BaseModel

from fse.config.loader import LoadOk, validate_data
from fse.config.schema import StrategyConfig
from fse.config.schema.account import ACCOUNT_RULE_IDS
from fse.config.schema.exits import EXIT_MODES
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.kill_switches import KILL_SWITCH_IDS
from fse.config.schema.patterns import DETECTOR_IDS
from tests.fakes.configs import minimal_config_data

type Tree = dict[str, Any]
type Case = tuple[Tree, tuple[str, ...]]

MIN_RR: Final = "gates.min_reward_risk.min"
ALERT_MIN: Final = "gates.min_reward_risk.alert_min"
GATE_ENABLED: Final = "gates.min_reward_risk.enabled"
R_MODES: Final = ("fixed_r", "opposition_or_fixed_r")

# Defaults from the design's "Strategy_Config shape" sketch, used by the oracle.
MIN_RR_DEFAULT: Final = 3.0
ALERT_MIN_DEFAULT: Final = 2.0
R_MULTIPLE_DEFAULT: Final = 3.0
ORDER_MODE_DEFAULT: Final = "paper"
OFF_BY_DEFAULT: Final = frozenset(
    {
        "gates.stdev_fib_zone",
        "gates.dark_pool_confluence",
        "exits.modes.tp1_partial_be",
        "exits.modes.trailing",
        "kill_switches.internal_daily_loss_stop",
    }
)

ORDER_MODES: Final = ("paper", "practice", "combine")
REGIMES: Final = ("Positive_Gamma", "Negative_Gamma", "Vanna_Dominant", "Whipsaw", "Structureless")
STOP_RULES: Final = ("one_node_beyond", "fixed_ticks")

RULE_PATHS: Final[tuple[str, ...]] = (
    *(f"patterns.{d}" for d in DETECTOR_IDS),
    *(f"gates.{g}" for g in GATE_IDS),
    *(f"exits.modes.{m}" for m in EXIT_MODES),
    *(f"account.{r}" for r in ACCOUNT_RULE_IDS),
    *(f"kill_switches.{k}" for k in KILL_SWITCH_IDS),
)
"""Every Pattern, Gate, Exit_Mode, account rule and Kill_Switch entry (Req 17.1)."""

# The traded instruments' costs have no default the loader accepts (Req 13.8).
PINNED: Final = frozenset({"fills", "fills.costs", "fills.costs.MES", "fills.costs.MNQ"})

_ABSENT: Final = object()


# ---------------------------------------------------------------- schema and tree walks


def _model_class(annotation: object) -> type[BaseModel] | None:
    """The model of a field annotated ``M`` or ``M | None``, else ``None``."""
    members = get_args(annotation) if isinstance(annotation, UnionType) else (annotation,)
    for member in members:
        if isinstance(member, type) and issubclass(member, BaseModel):
            return member
    return None


def _field(model: type[BaseModel], key: str) -> str:
    """The field name of the YAML key ``key`` (an alias or a name)."""
    for name, info in model.model_fields.items():
        if key in (info.alias, name):
            return name
    raise KeyError(f"{model.__name__} has no key {key!r}")


def _defaulted_paths(
    model: type[BaseModel], tree: Mapping[str, Any], prefix: str = ""
) -> list[str]:
    """Every key path of ``tree`` whose field has a Config_Schema default."""
    paths: list[str] = []
    for key, value in tree.items():
        info = model.model_fields[_field(model, key)]
        path = prefix + key
        if not info.is_required():
            paths.append(path)
        sub = _model_class(info.annotation)
        if sub is not None and isinstance(value, dict):
            paths.extend(_defaulted_paths(sub, value, f"{path}."))
    return paths


def _leaf_paths(tree: Mapping[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, value in tree.items():
        path = prefix + key
        if isinstance(value, dict):
            paths.extend(_leaf_paths(value, f"{path}."))
        else:
            paths.append(path)
    return paths


def _lookup(tree: Mapping[str, Any], path: str) -> object:
    """The file's value at ``path``, or ``_ABSENT`` when it or a section above it is omitted."""
    node: object = tree
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return _ABSENT
        node = node[key]
    return node


def _set(tree: Tree, path: str, value: object) -> None:
    *parents, last = path.split(".")
    node = tree
    for key in parents:
        node = node[key]
    node[last] = value


def _remove(tree: Tree, path: str) -> bool:
    """Delete ``path``; ``False`` when it or a section above it is already gone."""
    *parents, last = path.split(".")
    node: object = tree
    for key in parents:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    if not isinstance(node, dict) or last not in node:
        return False
    del node[last]
    return True


def _file_bool(tree: Tree, path: str, default: bool) -> bool:
    value = _lookup(tree, path)
    if value is _ABSENT:
        return default
    assert isinstance(value, bool), (path, value)
    return value


def _file_float(tree: Tree, path: str, default: float) -> float:
    value = _lookup(tree, path)
    if value is _ABSENT:
        return default
    assert isinstance(value, float), (path, value)
    return value


def _loaded(cfg: StrategyConfig, path: str) -> tuple[BaseModel, str]:
    """The loaded model holding ``path`` and the field name of its last key."""
    *parents, last = path.split(".")
    node: BaseModel = cfg
    for key in parents:
        child = getattr(node, _field(type(node), key))
        assert isinstance(child, BaseModel), path
        node = child
    return node, _field(type(node), last)


FULL_FILE: Final[Tree] = StrategyConfig.model_validate(minimal_config_data()).model_dump(
    mode="python"
)
"""The minimal config with every key written out, as a config file holds it."""

DEFAULTED: Final = tuple(p for p in _defaulted_paths(StrategyConfig, FULL_FILE) if p not in PINNED)
TOP_LEVEL: Final = tuple(p for p in DEFAULTED if "." not in p)
ENABLED_FLAGS: Final = tuple(p for p in _leaf_paths(FULL_FILE) if p.rsplit(".", 1)[-1] == "enabled")

_FOCUS_KEYS: Final = (
    "order_mode",
    GATE_ENABLED,
    MIN_RR,
    ALERT_MIN,
    "exits.global",
    "exits.per_regime",
    *(f"exits.modes.{m}.{k}" for m in R_MODES for k in ("enabled", "r_multiple")),
)
FOCUS: Final = tuple(
    p
    for p in DEFAULTED
    if any(p == k or k.startswith(f"{p}.") or p.startswith(f"{k}.") for k in _FOCUS_KEYS)
)
"""The defaulted keys the warnings and Order_Mode read, with their sections."""


# ---------------------------------------------------------------- oracle


def expected_warnings(tree: Tree) -> list[tuple[str, float, float]]:
    """Req 17.6 restated: ``(R multiple key path, R multiple, min)`` per contradiction."""
    if not _file_bool(tree, GATE_ENABLED, True):
        return []
    threshold = _file_float(tree, MIN_RR, MIN_RR_DEFAULT)
    found: list[tuple[str, float, float]] = []
    for mode in R_MODES:
        path = f"exits.modes.{mode}.r_multiple"
        r_multiple = _file_float(tree, path, R_MULTIPLE_DEFAULT)
        if _file_bool(tree, f"exits.modes.{mode}.enabled", True) and r_multiple < threshold:
            found.append((path, r_multiple, threshold))
    return found


# ---------------------------------------------------------------- strategies

R_POOL: Final = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0)
THRESHOLD_POOL: Final = (*R_POOL, 0.25, 12.0, 20.0)

r_multiples = st.one_of(
    st.sampled_from(R_POOL),
    st.floats(min_value=0.5, max_value=10.0, allow_nan=False, allow_infinity=False),
)
thresholds = st.one_of(
    st.sampled_from(THRESHOLD_POOL),
    st.floats(min_value=0.0, max_value=20.0, exclude_min=True, allow_nan=False),
)
exit_settings = st.fixed_dictionaries(
    {"mode": st.sampled_from(EXIT_MODES), "stop_rule": st.sampled_from(STOP_RULES)}
)
mostly_true = st.sampled_from((True, True, True, False))


@st.composite
def config_files(draw: st.DrawFn) -> Case:
    """A valid config file with some defaulted keys omitted, and the omitted key paths."""
    tree = copy.deepcopy(FULL_FILE)
    for path in draw(st.sets(st.sampled_from(ENABLED_FLAGS))):
        _set(tree, path, not _lookup(tree, path))
    for path in (GATE_ENABLED, *(f"exits.modes.{m}.enabled" for m in R_MODES)):
        _set(tree, path, draw(mostly_true))
    threshold = draw(thresholds) if draw(mostly_true) else None
    if threshold is not None:
        _set(tree, MIN_RR, threshold)
        if threshold < ALERT_MIN_DEFAULT:
            _set(tree, ALERT_MIN, threshold)  # alert_min may not exceed min
    for mode in R_MODES:
        if draw(mostly_true):
            _set(tree, f"exits.modes.{mode}.r_multiple", draw(r_multiples))
    global_setting = draw(st.none() | exit_settings)
    if global_setting is not None:
        _set(tree, "exits.global", global_setting)
    for regime in REGIMES:
        if draw(st.booleans()):
            _set(tree, f"exits.per_regime.{regime}", draw(st.none() | exit_settings))
    order_mode = draw(st.none() | st.sampled_from(ORDER_MODES))
    if order_mode is not None:
        _set(tree, "order_mode", order_mode)
    config_id = draw(st.none() | st.sampled_from(("p52", "baseline.v2", "A-1_b")))
    if config_id is not None:
        _set(tree, "config_id", config_id)

    omit = (
        draw(st.sets(st.sampled_from(TOP_LEVEL)))
        | draw(st.sets(st.sampled_from(FOCUS)))
        | draw(st.sets(st.sampled_from(DEFAULTED), max_size=40))
    )
    if threshold is not None and threshold < ALERT_MIN_DEFAULT:
        omit.discard(ALERT_MIN)  # its 2.0 default would exceed the drawn min
    shallow_first = sorted(omit, key=lambda p: (p.count("."), p))
    removed = tuple(p for p in shallow_first if _remove(tree, p))
    return tree, removed


# ---------------------------------------------------------------- property


@given(config_files())
def test_defaults_and_contradiction_warnings(case: Case) -> None:
    tree, removed = case
    note(f"omitted: {removed}")
    result = validate_data(tree)
    assert isinstance(result, LoadOk), result
    cfg = result.config

    # Req 17.2: an omitted key takes its Config_Schema default; Paper for the Order_Mode.
    for path in removed:
        parent, name = _loaded(cfg, path)
        default = type(parent).model_fields[name].get_default(call_default_factory=True)
        assert getattr(parent, name) == default, path
    order_mode = _lookup(tree, "order_mode")
    assert cfg.order_mode == (ORDER_MODE_DEFAULT if order_mode is _ABSENT else order_mode)

    # Req 17.1: every rule has an entry with an enabled flag, the file's or the default.
    for rule in RULE_PATHS:
        entry, name = _loaded(cfg, f"{rule}.enabled")
        expected = _file_bool(tree, f"{rule}.enabled", rule not in OFF_BY_DEFAULT)
        assert getattr(entry, name) is expected, rule

    # Req 17.6: one warning per enabled R multiple below an enabled min, naming both.
    expected_found = expected_warnings(tree)
    got = sorted((w.key_paths, w.values) for w in result.warnings)
    assert got == sorted(((path, MIN_RR), (r, m)) for path, r, m in expected_found)
    for warning in result.warnings:
        assert warning.kind == "contradiction"
        for text in (*warning.key_paths, *(repr(v) for v in warning.values)):
            assert text in warning.message, (text, warning.message)

    event(f"warnings: {len(expected_found)}")
    event(f"omitted keys: {min(len(removed), 10)}{'+' if len(removed) >= 10 else ''}")
