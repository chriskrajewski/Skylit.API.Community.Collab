"""Property 51: Validation reports every violation.

*For any* valid config with k injected violations (unknown keys, duplicate
ids, removed required keys, wrong types, out-of-range values including
account-rule and fee ranges), the Config_Loader rejects the file before any
Decision_Time and reports exactly the k failing key paths, each with its
failure kind and, for type and range failures, the expected type, allowed
values or range.

**Inputs.** A valid config file: the minimal config (``tests.fakes.configs``)
or the same config with every key written out by the Config_Printer. On top of
it the test injects 1 to 8 violations, each claiming the key paths it touches;
a drawn violation whose claims overlap an earlier one (same path, or one path
inside the other) is dropped, so the kept violations are independent:

- unknown keys at the root, in a section or in a rule entry, named like a
  credential or ``x_...``, holding a fake secret;
- duplicate keys in the YAML text: a Pattern, Gate, Exit_Mode, Kill_Switch or
  account rule id, or ``order_mode``, repeated after its first occurrence with
  a fake secret as the repeat; and a symbol listed twice in ``data.symbols``;
- missing required keys: ``regime.min_abs_value`` (or the whole section), a
  per-Regime exit setting without ``mode`` or ``stop_rule``, a commission or
  exchange fee, the costs of a traded instrument (removed, ``null``, or the
  traded instrument switched to ES or NQ, Req 13.8);
- wrong types: whole numbers, numbers, flags, text, dollar strings, times and
  mappings given a value of another type;
- out-of-range values: tick, count and minute ranges, literals (Order_Mode,
  schema version, traded instruments), fees outside $0.00-$25.00 (Req 13.8),
  account dollar amounts outside $0.01-$10,000,000.00 or not in whole cents,
  the consistency target, position cap, early-close offset and Flat_Deadline,
  a Maximum Loss Limit at or above the starting balance (Req 15.4), and a
  ``config_id`` holding a fake secret that fails its pattern.

The tree is emitted as YAML with a dumper that writes a mapping as key/value
pairs, so a key can repeat, and loaded with ``load_text``. A counterexample
the property found (``data.instruments.es_levels: NQ``) is pinned with
``@example`` so it runs every time.

**Oracle.** Req 17.4: the result is ``LoadErr`` (no config, so no Decision_Time
can run) and the multiset of (key path, kind) of its errors equals the one of
the injected violations, so the count is k. Each wrong-type and out-of-range
error has the expected type, allowed values or range the schema declares (the
fee and account ranges as Req 13.8 and 15.4 state them) and shows the
configured value, except a text that fails its pattern; no other error shows a
value. A missing-costs error names the commission, the exchange fee and their
range (Req 13.8). No error text contains any fake secret.

**Validates: Requirements 13.8, 15.4, 17.4**
"""

from __future__ import annotations

import copy
import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import yaml
from hypothesis import event, example, given, note
from hypothesis import strategies as st

from fse.config.loader import (
    KIND_DUPLICATE_ID,
    KIND_MISSING_KEY,
    KIND_OUT_OF_RANGE,
    KIND_UNKNOWN_KEY,
    KIND_WRONG_TYPE,
    LoadErr,
    LoadOk,
    load_text,
)
from fse.config.printer import dump
from fse.config.schema import ORDER_MODES, SECTION_KEYS, StrategyConfig
from fse.config.schema.account import ACCOUNT_RULE_IDS
from fse.config.schema.data import VELOCITY_WINDOW_MAX_S
from fse.config.schema.exits import EXIT_MODES
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.kill_switches import KILL_SWITCH_IDS
from fse.config.schema.orders import MAX_OPEN_MAX
from fse.config.schema.patterns import DETECTOR_IDS, FIXED_STOP_TICKS_MAX
from tests.fakes.configs import minimal_config_data

type Tree = dict[str, Any]

SOURCE: Final = "p51.yaml"
MAX_VIOLATIONS: Final = 8

# Expected types, as the Config_Loader names them (Req 17.4).
WHOLE: Final = "a whole number"
NUMBER: Final = "a number"
FLAG: Final = "true or false"
TEXT: Final = "a string"
MAPPING: Final = "a mapping"
DOLLARS: Final = "a quoted decimal string such as '0.37'"
HHMM: Final = "a quoted 'HH:MM' time"

# Ranges stated by the requirements.
FEE_MAX: Final = "at most 25.00"  # Req 13.8: $0.00 to $25.00 per contract per side
FEE_MIN: Final = "at least 0.00"
FEE_RANGE_TEXT: Final = "0.00 to 25.00"
USD_RANGE: Final = "from 0.01 to 10000000.00 dollars"  # Req 15.4: $0.01 to $10,000,000.00
WHOLE_CENTS: Final = "at most 2 decimal places"
CONFIG_ID_RULE: Final = "text matching ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

SYMBOLS: Final = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
REGIMES: Final = ("Positive_Gamma", "Negative_Gamma", "Vanna_Dominant", "Whipsaw", "Structureless")
STOP_RULES: Final = ("one_node_beyond", "fixed_ticks")

# Unknown-key names never share a 12-letter run with a secret token: tokens use
# letters that are neither hex digits nor in ``NAME_ALPHABET``.
SECRET_ALPHABET: Final = "ghjkmnpqrstvwxyz"
NAME_ALPHABET: Final = "abcdef0123456789_"
CREDENTIAL_NAMES: Final = (
    "api_key",
    "skylit_api_key",
    "webhook_url",
    "account_id",
    "account_name",
    "password",
    "token",
)
NON_DECIMAL_TEXTS: Final = ("abc", "$0.37", "0,37", "free", "", "1.2.3")


class _Delete:
    """The ``sets`` value that removes a key."""


DELETE: Final = _Delete()


@dataclass(frozen=True, slots=True)
class Want:
    """One error the Config_Loader must report."""

    path: str
    kind: str
    expected: str | None = None  # checked for wrong-type and out-of-range errors
    shows_value: bool = False
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Duplicate:
    """A key written twice: ``first`` when the base has no value there, then ``repeat``."""

    path: str
    first: object
    repeat: object


@dataclass(frozen=True, slots=True)
class Violation:
    label: str
    claims: tuple[str, ...]
    want: Want
    sets: tuple[tuple[str, object], ...] = ()
    duplicate: Duplicate | None = None
    secrets: tuple[str, ...] = ()


# ---------------------------------------------------------------- bases and YAML


MINIMAL: Final[Tree] = minimal_config_data()
FULL: Final[Tree] = yaml.safe_load(dump(StrategyConfig.model_validate(minimal_config_data())))
BASES: Final = (MINIMAL, FULL)


class _Pairs(list[tuple[object, object]]):
    """A YAML mapping as (key, value) pairs, so a key can repeat."""


class _PairsDumper(yaml.SafeDumper):  # type: ignore[misc]  # PyYAML ships no types
    def ignore_aliases(self, data: object) -> bool:
        return True


def _represent_pairs(dumper: Any, data: _Pairs) -> Any:
    return dumper.represent_mapping("tag:yaml.org,2002:map", list(data))


_PairsDumper.add_representer(_Pairs, _represent_pairs)


def _pairs(value: object) -> object:
    if isinstance(value, dict):
        return _Pairs((k, _pairs(v)) for k, v in value.items())
    if isinstance(value, list):
        return [_pairs(v) for v in value]
    return value


def _get(tree: Tree, path: str) -> object:
    node: object = tree
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return DELETE
        node = node[key]
    return node


def _set(tree: Tree, path: str, value: object) -> None:
    *parents, last = path.split(".")
    node = tree
    for key in parents:
        child = node.setdefault(key, {})
        assert isinstance(child, dict), path
        node = child
    if isinstance(value, _Delete):
        node.pop(last, None)
    else:
        node[last] = copy.deepcopy(value)


def _member(pairs: object, key: str) -> _Pairs:
    assert isinstance(pairs, _Pairs), key
    for name, value in pairs:
        if name == key:
            assert isinstance(value, _Pairs), key
            return value
    raise AssertionError(f"no mapping {key!r}")


def emit(base: Tree, violations: Sequence[Violation]) -> str:
    """``base`` with every violation applied, as YAML text."""
    tree = copy.deepcopy(base)
    for v in violations:
        for path, value in v.sets:
            _set(tree, path, value)
        if v.duplicate is not None and _get(tree, v.duplicate.path) is DELETE:
            _set(tree, v.duplicate.path, v.duplicate.first)
    doc = _pairs(tree)
    for v in violations:
        if v.duplicate is not None:
            *parents, key = v.duplicate.path.split(".")
            node = doc
            for name in parents:
                node = _member(node, name)
            assert isinstance(node, _Pairs)
            node.append((key, _pairs(v.duplicate.repeat)))
    text: str = yaml.dump(doc, Dumper=_PairsDumper, sort_keys=False, allow_unicode=True)
    return text


def _overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(f"{b}.") or b.startswith(f"{a}.")


# ---------------------------------------------------------------- value strategies


def _cents(n: int) -> str:
    sign = "-" if n < 0 else ""
    whole, cents = divmod(abs(n), 100)
    return f"{sign}{whole}.{cents:02d}"


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@st.composite
def _sub_cent(draw: st.DrawFn) -> str:
    """A dollar amount in range with a non-zero part below one cent."""
    cents = draw(st.integers(1, 10**9 - 1))
    extra = draw(st.integers(1, 99))
    return f"{cents // 100}.{cents % 100:02d}{extra:02d}"


tokens = st.text(SECRET_ALPHABET, min_size=12, max_size=12)
finite_floats = st.floats(allow_nan=False, allow_infinity=False)
digit_texts = st.from_regex(r"[0-9]{1,6}(\.[0-9]{1,2})?", fullmatch=True)
letters = st.text(string.ascii_letters, max_size=8)

not_whole = finite_floats | st.booleans() | digit_texts | st.none()
not_number = st.booleans() | digit_texts
not_flag = st.integers(-2, 2) | finite_floats | st.sampled_from(("yes", "no", "on", "true", "1"))
not_text = st.integers() | st.booleans() | finite_floats
not_mapping = (
    st.integers()
    | st.booleans()
    | st.text(string.ascii_letters + string.digits, max_size=8)
    | st.lists(st.integers(-9, 9), max_size=3)
)
not_dollars = (
    finite_floats | st.integers() | st.booleans() | st.none() | st.sampled_from(NON_DECIMAL_TEXTS)
)
not_hhmm = st.integers(0, 10**5) | st.sampled_from(("25:00", "4pm", "16:10:00", "1610", "9:30"))


def _secret_value(token: str, shape: int) -> object:
    secret = f"fake-secret-{token}"
    shapes: tuple[object, ...] = (secret, {"url": f"https://u:{secret}@hook.invalid"}, [secret])
    return shapes[shape]


# ---------------------------------------------------------------- violation strategies


def _put(
    label: str, path: str, values: st.SearchStrategy[object], want: Want
) -> st.SearchStrategy[Violation]:
    """Set ``path`` to a drawn value; the loader must report ``want``."""

    def build(value: object) -> Violation:
        return Violation(label, (path,), want, sets=((path, value),))

    return values.map(build)


def _bad(path: str, kind: str, expected: str, values: st.SearchStrategy[object]) -> Any:
    label = "wrong type" if kind == KIND_WRONG_TYPE else "out of range"
    return _put(label, path, values, Want(path, kind, expected, shows_value=True))


UNKNOWN_PARENTS: Final = (
    "",
    *SECTION_KEYS,
    "data.instruments",
    "exits.modes",
    "exits.per_regime",
    "fills.costs",
    "fills.costs.MES",
    "fills.costs.MNQ",
    "account.starting_balance",
    *(f"patterns.{d}" for d in DETECTOR_IDS),
    *(f"gates.{g}" for g in GATE_IDS),
    *(f"exits.modes.{m}" for m in EXIT_MODES),
    *(f"kill_switches.{k}" for k in KILL_SWITCH_IDS),
    *(f"account.{r}" for r in ACCOUNT_RULE_IDS),
)


@st.composite
def unknown_keys(draw: st.DrawFn) -> Violation:
    parent = draw(st.sampled_from(UNKNOWN_PARENTS))
    name = draw(
        st.sampled_from(CREDENTIAL_NAMES)
        | st.text(NAME_ALPHABET, min_size=1, max_size=10).map(lambda s: f"x_{s}")
    )
    token = draw(tokens)
    path = f"{parent}.{name}" if parent else name
    value = _secret_value(token, draw(st.integers(0, 2)))
    want = Want(path, KIND_UNKNOWN_KEY)
    return Violation("unknown key", (path,), want, sets=((path, value),), secrets=(token,))


DUPLICATE_TARGETS: Final[tuple[tuple[str, object], ...]] = (
    *((f"patterns.{d}", {}) for d in DETECTOR_IDS),
    *((f"gates.{g}", {}) for g in GATE_IDS),
    *((f"exits.modes.{m}", {}) for m in EXIT_MODES),
    *((f"kill_switches.{k}", {}) for k in KILL_SWITCH_IDS),
    *((f"account.{r}", {}) for r in ACCOUNT_RULE_IDS),
    ("order_mode", "paper"),
)


@st.composite
def duplicate_keys(draw: st.DrawFn) -> Violation:
    path, first = draw(st.sampled_from(DUPLICATE_TARGETS))
    token = draw(tokens)
    repeat = _secret_value(token, draw(st.integers(0, 2)))
    want = Want(path, KIND_DUPLICATE_ID)
    return Violation(
        "duplicate key", (path,), want, duplicate=Duplicate(path, first, repeat), secrets=(token,)
    )


@st.composite
def duplicate_symbols(draw: st.DrawFn) -> Violation:
    symbols = list(draw(st.permutations(SYMBOLS)))
    symbols.insert(draw(st.integers(0, len(symbols))), draw(st.sampled_from(SYMBOLS)))
    path = "data.symbols"
    want = Want(path, KIND_DUPLICATE_ID)
    return Violation("duplicate id in a list", (path,), want, sets=((path, symbols),))


def _missing_costs(instrument: str) -> Want:
    return Want(
        f"fills.costs.{instrument}",
        KIND_MISSING_KEY,
        mentions=(instrument, "commission", "exchange_fee", FEE_RANGE_TEXT),
    )


MISSING_REQUIRED: Final = (
    Violation(
        "missing key",
        ("regime.min_abs_value",),
        Want("regime.min_abs_value", KIND_MISSING_KEY),
        sets=(("regime.min_abs_value", DELETE),),
    ),
    Violation(
        "missing key",
        ("regime",),
        Want("regime.min_abs_value", KIND_MISSING_KEY),
        sets=(("regime", DELETE),),
    ),
)
MISSING_COSTS: Final = (
    *(
        Violation(
            "missing costs (Req 13.8)",
            (f"fills.costs.{inst}", f"data.instruments.{key}"),
            _missing_costs(inst),
            sets=((f"fills.costs.{inst}", gone),),
        )
        for inst, key in (("MES", "es_levels"), ("MNQ", "nq_levels"))
        for gone in (DELETE, None)
    ),
    *(
        Violation(
            "missing costs (Req 13.8)",
            (f"data.instruments.{key}", f"fills.costs.{inst}"),
            _missing_costs(inst),
            sets=((f"data.instruments.{key}", inst),),
        )
        for inst, key in (("ES", "es_levels"), ("NQ", "nq_levels"))
    ),
)


@st.composite
def per_regime_missing(draw: st.DrawFn) -> Violation:
    regime = draw(st.sampled_from(REGIMES))
    path = f"exits.per_regime.{regime}"
    setting: dict[str, str]
    if draw(st.booleans()):
        setting, missing = {"mode": draw(st.sampled_from(EXIT_MODES))}, "stop_rule"
    else:
        setting, missing = {"stop_rule": draw(st.sampled_from(STOP_RULES))}, "mode"
    want = Want(f"{path}.{missing}", KIND_MISSING_KEY)
    return Violation("missing key", (path,), want, sets=((path, setting),))


def fee_violations() -> list[Any]:
    """Req 13.8: each fee is required and from $0.00 to $25.00."""
    found: list[Any] = []
    for inst in ("MES", "MNQ"):
        for name in ("commission", "exchange_fee"):
            path = f"fills.costs.{inst}.{name}"
            found += [
                _bad(path, KIND_OUT_OF_RANGE, FEE_MAX, st.integers(2501, 10**8).map(_cents)),
                _bad(path, KIND_OUT_OF_RANGE, FEE_MIN, st.integers(-(10**8), -1).map(_cents)),
                _bad(path, KIND_WRONG_TYPE, DOLLARS, not_dollars),
                st.just(
                    Violation(
                        "missing key",
                        (path,),
                        Want(path, KIND_MISSING_KEY),
                        sets=((path, DELETE),),
                    )
                ),
            ]
    return found


def account_dollar_violations() -> list[Any]:
    """Req 15.4: each account dollar amount is from $0.01 to $10,000,000.00, in cents."""
    found: list[Any] = []
    for rule in ("starting_balance", "profit_target", "maximum_loss_limit", "daily_loss_limit"):
        path = f"account.{rule}.value"
        found += [
            _bad(path, KIND_OUT_OF_RANGE, USD_RANGE, st.integers(-(10**9), 0).map(_cents)),
            _bad(path, KIND_OUT_OF_RANGE, USD_RANGE, st.integers(10**9 + 1, 10**14).map(_cents)),
            _bad(path, KIND_OUT_OF_RANGE, WHOLE_CENTS, _sub_cent()),
            _bad(path, KIND_WRONG_TYPE, DOLLARS, not_dollars),
        ]
    return found


@st.composite
def mll_not_below_start(draw: st.DrawFn) -> Violation:
    """Req 15.4: the Maximum Loss Limit must be less than the starting balance."""
    start = draw(st.integers(1, 10**9))
    mll = start if draw(st.booleans()) else draw(st.integers(start, 10**9))
    want = Want(
        "account.maximum_loss_limit", KIND_OUT_OF_RANGE, f"below {_cents(start)}", shows_value=True
    )
    return Violation(
        "MLL not below starting balance (Req 15.4)",
        ("account.starting_balance", "account.maximum_loss_limit"),
        want,
        sets=(
            ("account.starting_balance.value", _cents(start)),
            ("account.maximum_loss_limit.value", _cents(mll)),
        ),
    )


INT_RANGES: Final[tuple[tuple[str, int, int | None], ...]] = (
    ("fills.trade_through_ticks", 0, 20),  # Req 13.1
    ("fills.slippage_ticks", 0, 20),  # Req 13.3
    ("account.position_cap.micro_equivalents", 1, 1000),  # Req 15.4
    ("account.early_close_offset_min", 0, 60),  # Req 15.4
    ("orders.max_open", 1, MAX_OPEN_MAX),
    ("data.velocity_window_s", 1, VELOCITY_WINDOW_MAX_S),
    ("kill_switches.max_trades.max", 1, None),
    ("kill_switches.max_losers.limit", 1, None),
    ("kill_switches.consecutive_losers.limit", 1, None),
    *((f"patterns.{d}.fixed_stop_ticks", 1, FIXED_STOP_TICKS_MAX) for d in DETECTOR_IDS),
)


def int_violations() -> list[Any]:
    found: list[Any] = []
    for path, lo, hi in INT_RANGES:
        found += [
            _bad(path, KIND_OUT_OF_RANGE, f"at least {lo}", st.integers(lo - 10**6, lo - 1)),
            _bad(path, KIND_WRONG_TYPE, WHOLE, not_whole),
        ]
        if hi is not None:
            found.append(
                _bad(path, KIND_OUT_OF_RANGE, f"at most {hi}", st.integers(hi + 1, hi + 10**9))
            )
    return found


non_positive = st.floats(max_value=0.0, allow_nan=False, allow_infinity=False) | st.integers(
    -(10**9), 0
)
above_100 = st.floats(
    min_value=100.0, exclude_min=True, allow_nan=False, allow_infinity=False
) | st.integers(101, 10**9)

FLAG_PATHS: Final = (
    *(f"account.{r}.enabled" for r in ACCOUNT_RULE_IDS),
    *(f"patterns.{d}.enabled" for d in DETECTOR_IDS),
    *(f"kill_switches.{k}.enabled" for k in KILL_SWITCH_IDS),
)
MAPPING_PATHS: Final = (
    *SECTION_KEYS,
    "data.instruments",
    "fills.costs.MES",
    "fills.costs.MNQ",
    "account.starting_balance",
    *(f"patterns.{d}" for d in DETECTOR_IDS),
    *(f"gates.{g}" for g in GATE_IDS),
    *(f"exits.modes.{m}" for m in EXIT_MODES),
    *(f"kill_switches.{k}" for k in KILL_SWITCH_IDS),
    *(f"account.{r}" for r in ACCOUNT_RULE_IDS),
)


def number_and_time_violations() -> list[Any]:
    return [
        _bad("regime.min_abs_value", KIND_OUT_OF_RANGE, "above 0.0", non_positive),
        _bad("regime.min_abs_value", KIND_WRONG_TYPE, NUMBER, not_number),
        _bad("account.consistency_target.pct", KIND_OUT_OF_RANGE, "above 0.0", non_positive),
        _bad("account.consistency_target.pct", KIND_OUT_OF_RANGE, "at most 100.0", above_100),
        _bad("account.consistency_target.pct", KIND_WRONG_TYPE, NUMBER, not_number),
        _bad(
            "account.flat_deadline",
            KIND_OUT_OF_RANGE,
            "above 09:30",
            st.integers(0, 570).map(_hhmm),
        ),
        _bad(
            "account.flat_deadline",
            KIND_OUT_OF_RANGE,
            "below 18:00",
            st.integers(1080, 1439).map(_hhmm),
        ),
        _bad("account.flat_deadline", KIND_WRONG_TYPE, HHMM, not_hhmm),
    ]


def allowed_value_violations() -> list[Any]:
    """Literals: the Order_Mode, the schema version and the traded instruments."""
    order_modes = st.sampled_from(("live", "Paper", "sim")) | letters.filter(
        lambda s: s not in ORDER_MODES
    )
    return [
        _bad(
            "order_mode", KIND_OUT_OF_RANGE, "one of 'paper', 'practice' or 'combine'", order_modes
        ),
        _bad(
            "schema_version", KIND_OUT_OF_RANGE, "one of 1", st.integers().filter(lambda n: n != 1)
        ),
        _bad(
            "data.instruments.es_levels",
            KIND_OUT_OF_RANGE,
            "one of 'ES' or 'MES'",
            st.sampled_from(("NQ", "MNQ", "SPX", "es", "")),
        ),
        _bad(
            "data.instruments.nq_levels",
            KIND_OUT_OF_RANGE,
            "one of 'NQ' or 'MNQ'",
            st.sampled_from(("ES", "MES", "QQQ", "nq", "")),
        ),
    ]


@st.composite
def hidden_config_ids(draw: st.DrawFn) -> Violation:
    """A text that fails its pattern may be a misplaced credential: its value is not shown."""
    token = draw(tokens)
    want = Want("config_id", KIND_OUT_OF_RANGE, CONFIG_ID_RULE)
    return Violation(
        "hidden value",
        ("config_id",),
        want,
        sets=(("config_id", f"fake secret {token}"),),
        secrets=(token,),
    )


# ``st.one_of`` flattens nested ``one_of``s, which would let the groups with many
# key paths crowd out the rest; drawing the group first weighs them equally.
VIOLATION_GROUPS: Final[tuple[tuple[str, st.SearchStrategy[Violation]], ...]] = (
    ("unknown key", unknown_keys()),
    ("duplicate key", duplicate_keys()),
    ("duplicate id in a list", duplicate_symbols()),
    ("missing key", st.sampled_from(MISSING_REQUIRED) | per_regime_missing()),
    ("missing costs", st.sampled_from(MISSING_COSTS)),
    ("fee", st.one_of(*fee_violations())),
    ("MLL vs starting balance", mll_not_below_start()),
    ("account dollars", st.one_of(*account_dollar_violations())),
    ("whole numbers", st.one_of(*int_violations())),
    ("numbers and times", st.one_of(*number_and_time_violations())),
    ("allowed values", st.one_of(*allowed_value_violations())),
    ("flags", st.one_of(*(_bad(p, KIND_WRONG_TYPE, FLAG, not_flag) for p in FLAG_PATHS))),
    (
        "mappings",
        st.one_of(*(_bad(p, KIND_WRONG_TYPE, MAPPING, not_mapping) for p in MAPPING_PATHS)),
    ),
    ("config_id", _bad("config_id", KIND_WRONG_TYPE, TEXT, not_text) | hidden_config_ids()),
)


@st.composite
def a_violation(draw: st.DrawFn) -> Violation:
    _, group = draw(st.sampled_from(VIOLATION_GROUPS))
    return draw(group)


@st.composite
def violation_sets(draw: st.DrawFn) -> tuple[Violation, ...]:
    """1 to 8 independent violations: a drawn one that overlaps a kept one is dropped."""
    kept: list[Violation] = []
    for v in draw(st.lists(a_violation(), min_size=1, max_size=MAX_VIOLATIONS)):
        if not any(_overlaps(a, b) for k in kept for a in k.claims for b in v.claims):
            kept.append(v)
    return tuple(kept)


# ---------------------------------------------------------------- tests


def test_base_configs_load() -> None:
    for base in BASES:
        assert isinstance(load_text(emit(base, ()), source=SOURCE), LoadOk)


# A counterexample this property found, pinned so it runs every time: an invalid
# ``data.instruments.es_levels`` that names another fill instrument (NQ) also
# gets a "missing required key" error for ``fills.costs.NQ``, though no valid
# setting trades NQ: 2 errors for 1 violation.
ES_LEVELS_NQ: Final = Violation(
    "out of range",
    ("data.instruments.es_levels",),
    Want("data.instruments.es_levels", KIND_OUT_OF_RANGE, "one of 'ES' or 'MES'", shows_value=True),
    sets=(("data.instruments.es_levels", "NQ"),),
)


@example(MINIMAL, (ES_LEVELS_NQ,))
@given(st.sampled_from(BASES), violation_sets())
def test_validation_reports_every_violation(base: Tree, injected: tuple[Violation, ...]) -> None:
    text = emit(base, injected)
    note(text)
    result = load_text(text, source=SOURCE)

    # Rejected with no config, so no Decision_Time runs (Req 17.4).
    assert isinstance(result, LoadErr), [v.label for v in injected]
    wants = [v.want for v in injected]
    assert len({w.path for w in wants}) == len(wants), "the generator claims overlap"

    # Exactly the injected key paths, each with its kind: the count is k.
    got = sorted((e.key_path, e.kind) for e in result.errors)
    assert got == sorted((w.path, w.kind) for w in wants)

    by_path = {e.key_path: e for e in result.errors}
    for want in wants:
        error = by_path[want.path]
        assert error.file == SOURCE
        if want.kind in (KIND_WRONG_TYPE, KIND_OUT_OF_RANGE):
            assert error.expected == want.expected, want.path
        assert (error.value is not None) is want.shows_value, (want.path, error.value)
        for part in want.mentions:
            assert part in str(error), (want.path, part)

    # A value that may be a credential is never shown.
    shown = "\n".join(str(e) for e in result.errors)
    for v in injected:
        for token in v.secrets:
            assert token not in shown, v.want.path

    event(f"violations: {len(injected)}")
    for v in injected:
        event(v.label)
