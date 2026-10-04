"""Unit tests for the Config_Loader (design §17, D6).

File errors with the path and syntax-error line, duplicate keys, one error per
failing key path with its kind and expected value, the traded-instrument cost
check (Req 13.8), account-rule ranges (Req 15.4) and warnings on success.
Files are written under ``tmp_path``.

**Validates: Requirements 13.8, 15.4, 17.1, 17.2, 17.3, 17.4, 17.6**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fse.config.loader import (
    EXIT_INVALID_INPUT,
    ConfigError,
    ConfigLoadError,
    LoadErr,
    LoadOk,
    LoadResult,
    UniqueKeySafeLoader,
    key_path,
    load,
    load_or_raise,
    load_text,
)
from fse.config.schema import StrategyConfig
from tests.fakes.configs import MINIMAL_CONFIG_YAML, minimal_config_data

FAKE_SECRET = "fake-secret-value-0000"


def errors_of(result: LoadResult) -> tuple[ConfigError, ...]:
    assert isinstance(result, LoadErr), result
    return result.errors


def summary(result: LoadResult) -> list[tuple[str, str]]:
    return [(e.key_path, e.kind) for e in errors_of(result)]


def write(tmp_path: Path, text: str, name: str = "strategy.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- success


def test_minimal_file_loads_with_defaults_and_no_warnings(tmp_path: Path) -> None:
    path = write(tmp_path, MINIMAL_CONFIG_YAML)
    result = load(path)
    assert isinstance(result, LoadOk)
    assert result.file == str(path)
    assert result.config == StrategyConfig.model_validate(minimal_config_data())
    assert result.config.order_mode == "paper"
    assert result.warnings == ()
    assert load_or_raise(path).config == result.config


def test_contradiction_warnings_come_with_a_valid_config() -> None:
    text = MINIMAL_CONFIG_YAML + "exits:\n  modes:\n    fixed_r: {r_multiple: 2.0}\n"
    result = load_text(text)
    assert isinstance(result, LoadOk)
    assert [w.key_paths for w in result.warnings] == [
        ("exits.modes.fixed_r.r_multiple", "gates.min_reward_risk.min")
    ]


# ---------------------------------------------------------------- file errors


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    path = tmp_path / "nope.yaml"
    (error,) = errors_of(load(path))
    assert (error.file, error.kind, error.line) == (str(path), "missing file", None)
    assert str(path) in str(error)


def test_directory_and_non_utf8_files_are_unreadable(tmp_path: Path) -> None:
    (error,) = errors_of(load(tmp_path))
    assert error.kind == "unreadable file"
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"regime: {min_abs_value: 1}\n\xff\xfe")
    (error,) = errors_of(load(binary))
    assert (error.file, error.kind) == (str(binary), "unreadable file")
    assert "UTF-8" in error.message


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("regime:\n  min_abs_value: [1, 2\nfills: {}\n", 3),
        ("regime: {min_abs_value: 1}\n  bad: indent\n", 2),
        ("a: 1\n---\nb: 2\n", 2),
        ("a: !!python/object:os.system {}\n", 1),
    ],
)
def test_syntax_errors_name_the_path_and_line(tmp_path: Path, text: str, line: int) -> None:
    path = write(tmp_path, text)
    (error,) = errors_of(load(path))
    assert (error.file, error.kind, error.line) == (str(path), "syntax error", line)
    assert str(error).startswith(f"{path}:{line}: syntax error: ")


def test_load_or_raise_lists_every_error_and_exits_2(tmp_path: Path) -> None:
    path = write(tmp_path, "order_mode: live\n")
    with pytest.raises(ConfigLoadError) as info:
        load_or_raise(path)
    assert info.value.exit_code == EXIT_INVALID_INPUT == 2
    assert [e.key_path for e in info.value.errors] == [
        "order_mode",
        "regime.min_abs_value",
        "fills.costs.MES",
        "fills.costs.MNQ",
    ]
    assert len(str(info.value).splitlines()) == 4


# ---------------------------------------------------------------- duplicate keys


def test_duplicate_ids_are_reported_with_path_and_line() -> None:
    text = (
        MINIMAL_CONFIG_YAML
        + "patterns:\n"
        + "  rug: {enabled: false}\n"
        + "  rug: {enabled: true}\n"
        + "kill_switches:\n"
        + "  max_trades: {max: 2}\n"
        + "  max_trades: {max: 4}\n"
    )
    errors = errors_of(load_text(text, source="dup.yaml"))
    assert [(e.key_path, e.kind, e.line) for e in errors] == [
        ("patterns.rug", "duplicate id", 9),
        ("kill_switches.max_trades", "duplicate id", 12),
    ]


def test_duplicates_are_reported_with_every_other_error() -> None:
    text = "order_mode: paper\norder_mode: combine\nfills: {slippage_ticks: -1}\n"
    assert summary(load_text(text)) == [
        ("order_mode", "duplicate id"),
        ("regime.min_abs_value", "missing required key"),
        ("fills.slippage_ticks", "out of range"),
        ("fills.costs.MES", "missing required key"),
        ("fills.costs.MNQ", "missing required key"),
    ]


def test_unique_key_loader_keeps_the_first_value_and_honours_merges() -> None:
    loader = UniqueKeySafeLoader(
        "base: &b {x: 1, y: 2}\nm:\n  <<: *b\n  y: 3\n  y: 4\nl: [{a: 1}]\n"
    )
    try:
        data = loader.get_single_data()
    finally:
        loader.dispose()
    assert data == {"base": {"x": 1, "y": 2}, "m": {"x": 1, "y": 3}, "l": [{"a": 1}]}
    assert [(d.key_path, d.line) for d in loader.duplicates] == [("m.y", 5)]
    assert loader.key_lines["l[0].a"] == 6


# ---------------------------------------------------------------- validation errors


def test_each_failure_kind_has_path_and_expected_value() -> None:
    text = (
        MINIMAL_CONFIG_YAML
        + f"notifier_api_key: {FAKE_SECRET}\n"
        + "orders: {max_open: 1.5, flatten_time: '17:00'}\n"
        + "exits:\n  per_regime:\n    Whipsaw: {mode: next_node}\n"
        + "gates:\n  stdev_fib_zone:\n    zones: [{near: -2.0, far: -20.0}]\n"
    )
    errors = {e.key_path: e for e in errors_of(load_text(text, source="k.yaml"))}
    assert {path: e.kind for path, e in errors.items()} == {
        "notifier_api_key": "unknown key",
        "orders.max_open": "wrong type",
        "orders.flatten_time": "out of range",
        "exits.per_regime.Whipsaw.stop_rule": "missing required key",
        "gates.stdev_fib_zone.zones[0].far": "out of range",
    }
    assert errors["orders.max_open"].expected == "a whole number"
    assert errors["orders.max_open"].value == "1.5"
    assert errors["orders.flatten_time"].expected == "from 09:30 to 16:00"
    assert errors["gates.stdev_fib_zone.zones[0].far"].expected == "at least -10.0"
    assert errors["notifier_api_key"].expected is None
    assert errors["notifier_api_key"].line == 7
    assert errors["gates.stdev_fib_zone.zones[0].far"].line == 14
    # A missing key gets its parent's line.
    assert errors["exits.per_regime.Whipsaw.stop_rule"].line == 11


@pytest.mark.parametrize(
    ("extra", "path", "kind"),
    [
        (f"live:\n  webhook: {FAKE_SECRET}\n", "live.webhook", "unknown key"),
        (
            f"notify:\n  narrator: {{base_url: 'https://u:{FAKE_SECRET}@llm.invalid'}}\n",
            "notify.narrator.base_url",
            "out of range",
        ),
    ],
)
def test_possible_credentials_never_appear_in_errors(extra: str, path: str, kind: str) -> None:
    (error,) = errors_of(load_text(MINIMAL_CONFIG_YAML + extra))
    assert (error.key_path, error.kind, error.value) == (path, kind, None)
    assert FAKE_SECRET not in str(error)


def test_allowed_values_are_named() -> None:
    text = MINIMAL_CONFIG_YAML + "data: {symbols: [SPX, QQQ, NDX, NDXP], regime_symbol: SPY}\n"
    (error,) = errors_of(load_text(text))
    assert (error.key_path, error.kind) == ("data.regime_symbol", "out of range")
    assert error.expected == "one of data.symbols: 'SPX', 'QQQ', 'NDX', 'NDXP'"


def test_non_string_keys_are_unknown_keys() -> None:
    text = MINIMAL_CONFIG_YAML + "chart:\n  pivot_len: {60: 3}\n"
    (error,) = errors_of(load_text(text))
    assert (error.key_path, error.kind) == ("chart.pivot_len.60", "unknown key")


@pytest.mark.parametrize("text", ["", "# only a comment\n", "[1, 2]\n", "just text\n"])
def test_empty_or_non_mapping_documents_are_rejected(text: str) -> None:
    errors = errors_of(load_text(text))
    if text.startswith(("[", "just")):
        assert [(e.key_path, e.kind) for e in errors] == [("", "wrong type")]
        assert errors[0].expected == "a mapping"
    else:
        assert [(e.key_path, e.kind) for e in errors] == [
            ("regime.min_abs_value", "missing required key"),
            ("fills.costs.MES", "missing required key"),
            ("fills.costs.MNQ", "missing required key"),
        ]


# ---------------------------------------------------------------- Req 13.8 costs


def test_traded_instruments_need_costs() -> None:
    text = (
        "regime: {min_abs_value: 1.0}\n"
        "data:\n  instruments: {es_levels: ES}\n"
        "fills:\n  costs:\n    MNQ: {commission: '0.37', exchange_fee: '0.35'}\n    MES: null\n"
    )
    (error,) = errors_of(load_text(text))
    assert (error.key_path, error.kind) == ("fills.costs.ES", "missing required key")
    assert "ES" in error.message
    assert "commission" in error.message
    assert error.expected is not None
    assert "0.00 to 25.00" in error.expected


def test_untraded_instruments_need_no_costs() -> None:
    text = (
        "regime: {min_abs_value: 1.0}\n"
        "data:\n  instruments: {es_levels: ES, nq_levels: NQ}\n"
        "fills:\n  costs:\n"
        "    ES: {commission: '1.00', exchange_fee: '1.50'}\n"
        "    NQ: {commission: '1.00', exchange_fee: '1.50'}\n"
    )
    assert isinstance(load_text(text), LoadOk)


@pytest.mark.parametrize(
    ("costs", "path", "kind", "expected"),
    [
        ("{commission: '25.01', exchange_fee: '0'}", "commission", "out of range", "at most 25.00"),
        (
            "{commission: '0', exchange_fee: '-0.01'}",
            "exchange_fee",
            "out of range",
            "at least 0.00",
        ),
        ("{commission: 0.5, exchange_fee: '0'}", "commission", "wrong type", None),
        ("{exchange_fee: '0'}", "commission", "missing required key", None),
    ],
)
def test_fee_errors_name_the_setting_and_instrument(
    costs: str, path: str, kind: str, expected: str | None
) -> None:
    text = (
        "regime: {min_abs_value: 1.0}\n"
        f"fills:\n  costs:\n    MES: {costs}\n    MNQ: {{commission: '0', exchange_fee: '0'}}\n"
    )
    (error,) = errors_of(load_text(text))
    assert (error.key_path, error.kind) == (f"fills.costs.MES.{path}", kind)
    if expected is not None:
        assert error.expected == expected


# ---------------------------------------------------------------- Req 15.4 account ranges


@pytest.mark.parametrize(
    ("account", "path", "expected"),
    [
        (
            "{profit_target: {value: '0.00'}}",
            "account.profit_target.value",
            "from 0.01 to 10000000.00 dollars",
        ),
        (
            "{daily_loss_limit: {value: '10000000.01'}}",
            "account.daily_loss_limit.value",
            "from 0.01 to 10000000.00 dollars",
        ),
        (
            "{maximum_loss_limit: {value: '50000.00'}}",
            "account.maximum_loss_limit",
            "below 50000.00",
        ),
        ("{consistency_target: {pct: 0}}", "account.consistency_target.pct", "above 0.0"),
        (
            "{position_cap: {micro_equivalents: 1001}}",
            "account.position_cap.micro_equivalents",
            "at most 1000",
        ),
        ("{early_close_offset_min: 61}", "account.early_close_offset_min", "at most 60"),
    ],
)
def test_account_rule_ranges_report_rule_value_and_range(
    account: str, path: str, expected: str
) -> None:
    (error,) = errors_of(load_text(MINIMAL_CONFIG_YAML + f"account: {account}\n"))
    assert (error.key_path, error.kind, error.expected) == (path, "out of range", expected)
    assert error.value is not None


def test_key_path_formats_lists_and_keys() -> None:
    assert key_path(("gates", "stdev_fib_zone", "zones", 0, "near")) == (
        "gates.stdev_fib_zone.zones[0].near"
    )
    assert key_path(()) == ""
