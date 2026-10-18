"""Paper account rules, presets and the ``accounts:`` schema (design 2.1-2.3, A-2, A-3, BC-7)."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse.config.schema.account import (
    PAPER_RULE_IDS,
    AccountConfig,
    NoTradeWindowConfig,
    PaperAccountRules,
    in_no_trade_window,
)
from fse.live.host.accounts_config import PRESETS
from fse.live.host.config import (
    AccountEntry,
    RunnerConfig,
    RunnerConfigError,
    parse_runner_config,
)
from fse.sim.account import AccountSim
from fse.timekit import SessionCalendar, SessionTimes

PLUGIN: dict[str, Any] = {
    "id": "alpha",
    "module": "research.example.plugin:ExamplePlugin",
    "risk": {"max_contracts": 4},
    "account": "topstep_a",
}


def doc(account: dict[str, Any], plugin: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"version": 1, "plugins": [plugin or PLUGIN], "accounts": [account]}


def parse(d: dict[str, Any]) -> RunnerConfig:
    return parse_runner_config(d, Path("runner.yaml"))


def problems(d: dict[str, Any]) -> list[str]:
    with pytest.raises(RunnerConfigError) as info:
        parse(d)
    return info.value.problems


def account(
    rules: object = None, preset: str = "topstep_50k_combine", **extra: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "topstep_a", "preset": preset, **extra}
    if rules is not None:
        out["rules"] = rules
    return out


def rules_of(rules: object = None, preset: str = "topstep_50k_combine") -> PaperAccountRules:
    return parse(doc(account(rules, preset))).accounts[0].rules


# ---------------------------------------------------------------- presets (A-2, A-3)


def test_topstep_preset_equals_account_config_defaults_field_by_field() -> None:
    rules = rules_of()
    base = AccountConfig()
    for name in AccountConfig.model_fields:
        got = getattr(rules, name)
        want = getattr(base, name)
        if name == "maximum_loss_limit":
            assert (got.enabled, got.value) == (want.enabled, want.value)
            assert (got.mode, got.trail_lock) == ("eod_trailing", True)
        else:
            assert got == want, name
    assert rules == PaperAccountRules()
    assert (rules.flat_deadline_enabled, rules.no_trade_windows, rules.min_trading_days) == (
        True,
        (),
        None,
    )


def test_a_single_override_changes_only_that_field() -> None:
    rules = rules_of({"starting_balance": {"value": "100000.00"}})
    assert rules.starting_balance.value == Decimal("100000.00")
    default = PaperAccountRules().model_dump()
    dumped = rules.model_dump()
    changed = {k for k in dumped if dumped[k] != default[k]}
    assert changed == {"starting_balance"}


def test_preset_merge_keeps_siblings_at_leaf_level() -> None:
    rules = rules_of({"maximum_loss_limit": {"value": "2500.00"}}, preset="none")
    mll = rules.maximum_loss_limit
    assert (mll.enabled, mll.value, mll.mode) == (False, Decimal("2500.00"), "eod_trailing")
    assert not rules.profit_target.enabled
    assert not rules.daily_loss_limit.enabled
    on = rules_of({"maximum_loss_limit": {"enabled": True, "value": "2500.00"}}, preset="none")
    assert on.maximum_loss_limit.enabled
    assert not on.position_cap.enabled  # the other preset keys stay


def test_preset_merge_does_not_mutate_the_presets_or_the_yaml() -> None:
    before = {k: dict(v) if isinstance(v, dict) else v for k, v in PRESETS["none"].items()}
    raw = {"maximum_loss_limit": {"value": "2500.00"}}
    rules_of(raw, preset="none")
    assert raw == {"maximum_loss_limit": {"value": "2500.00"}}
    assert {k: dict(v) if isinstance(v, dict) else v for k, v in PRESETS["none"].items()} == (
        before
    )


def test_none_preset_turns_every_rule_off_and_takes_a_small_balance() -> None:
    rules = rules_of({"starting_balance": {"value": "1000.00"}}, preset="none")
    assert rules.starting_balance.value == Decimal("1000.00")
    assert rules.maximum_loss_limit.value == Decimal("0.01")
    assert rules.disabled_rules() == PAPER_RULE_IDS
    assert rules_of(preset="none").starting_balance.value == Decimal("50000.00")


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        (
            {"starting_balance": {"value": "0.00"}},
            "accounts[0].rules.starting_balance.value: 0.00 should be from 0.01 to "
            "10000000.00 dollars",
        ),
        (
            {"profit_target": {"value": "10000000.01"}},
            "accounts[0].rules.profit_target.value: 10000000.01 should be from 0.01 to "
            "10000000.00 dollars",
        ),
        (
            {"daily_loss_limit": {"value": "1000.001"}},
            "accounts[0].rules.daily_loss_limit.value: 1000.001 should have at most 2 decimal "
            "places (whole cents)",
        ),
        (
            {"consistency_target": {"pct": 0}},
            "accounts[0].rules.consistency_target.pct: Input should be greater than 0",
        ),
        (
            {"consistency_target": {"pct": 100.5}},
            "accounts[0].rules.consistency_target.pct: Input should be less than or equal to 100",
        ),
        (
            {"flat_deadline": "4:10 PM"},
            "accounts[0].rules.flat_deadline: '4:10 PM' should be a quoted 'HH:MM' New York "
            "time such as '16:10'",
        ),
        (
            {"maximum_loss_limit": {"value": "50000.00"}},
            "accounts[0].rules.maximum_loss_limit: maximum_loss_limit.value 50000.00 should be "
            "less than starting_balance.value 50000.00",
        ),
        (
            {"no_trade_windows": [{"start": "9:25", "end": "09:35"}]},
            "accounts[0].rules.no_trade_windows[0].start: '9:25' should be a quoted 'HH:MM' New "
            "York time such as '09:00'",
        ),
        (
            {"min_trading_days": 0},
            "accounts[0].rules.min_trading_days: Input should be greater than or equal to 1",
        ),
        (
            {"maximum_loss_limit": {"mode": "weekly"}},
            "accounts[0].rules.maximum_loss_limit.mode: Input should be 'eod_trailing', "
            "'intraday_trailing' or 'static'",
        ),
    ],
)
def test_invalid_values_report_the_existing_messages_at_their_yaml_path(
    rules: dict[str, Any], expected: str
) -> None:
    assert problems(doc(account(rules))) == [expected]


@pytest.mark.parametrize("rules", [5, ["a"]])
def test_non_mapping_rules_are_reported_as_not_a_dictionary(rules: object) -> None:
    [problem] = problems(doc(account(rules)))
    assert problem.startswith("accounts[0].rules: Input should be a valid dictionary")


@pytest.mark.parametrize("preset", ["topstep_50k_combine", "none"])
def test_an_empty_rules_key_means_no_overrides(preset: str) -> None:
    # YAML ``rules:`` with nothing under it parses to None.
    entry = {"id": "topstep_a", "preset": preset, "rules": None}
    got = parse(doc(entry)).accounts[0].rules
    assert got == rules_of(preset=preset)


def test_unknown_preset_is_reported_by_the_literal() -> None:
    found = problems(doc(account({}, preset="topstep_150k")))
    assert found[0] == "accounts[0].preset: Input should be 'topstep_50k_combine' or 'none'"


# ---------------------------------------------------------------- no-trade windows


def window(start: str, end: str) -> NoTradeWindowConfig:
    return NoTradeWindowConfig.model_validate({"start": start, "end": end})


@pytest.mark.parametrize(
    ("at", "inside"),
    [
        (time(23, 0), True),
        (time(0, 0), True),
        (time(0, 59), True),
        (time(0, 59, 59), True),
        (time(1, 0), False),
        (time(22, 59), False),
        (time(12, 0), False),
    ],
)
def test_a_window_past_midnight_wraps_half_open(at: time, inside: bool) -> None:
    assert in_no_trade_window([window("23:00", "01:00")], at) is inside


def test_a_window_across_the_globex_open_needs_no_wrap() -> None:
    w = [window("17:55", "18:10")]
    assert in_no_trade_window(w, time(18, 0))
    assert in_no_trade_window(w, time(17, 55))
    assert not in_no_trade_window(w, time(18, 10))
    assert not in_no_trade_window([], time(18, 0))


def test_window_around_the_open_is_expressible() -> None:
    rules = rules_of({"no_trade_windows": [{"start": "09:25", "end": "09:35"}]})
    assert in_no_trade_window(rules.no_trade_windows, time(9, 30))


def test_equal_start_and_end_is_refused() -> None:
    assert problems(doc(account({"no_trade_windows": [{"start": "10:00", "end": "10:00"}]}))) == [
        "accounts[0].rules.no_trade_windows[0]: start and end are equal (10:00)"
    ]


def test_overlapping_windows_are_refused_and_touching_ones_accepted() -> None:
    overlap = [{"start": "23:00", "end": "01:00"}, {"start": "00:30", "end": "02:00"}]
    assert problems(doc(account({"no_trade_windows": overlap}))) == [
        "accounts[0].rules.no_trade_windows: window [1] overlaps window [0]"
    ]
    touching = [{"start": "10:00", "end": "11:00"}, {"start": "11:00", "end": "12:00"}]
    assert len(rules_of({"no_trade_windows": touching}).no_trade_windows) == 2
    wrap_touch = [{"start": "23:00", "end": "01:00"}, {"start": "01:00", "end": "02:00"}]
    assert len(rules_of({"no_trade_windows": wrap_touch}).no_trade_windows) == 2


def test_at_most_eight_windows() -> None:
    nine = [{"start": f"{h:02d}:00", "end": f"{h:02d}:30"} for h in range(9)]
    [problem] = problems(doc(account({"no_trade_windows": nine})))
    assert problem.startswith("accounts[0].rules.no_trade_windows:")


# ---------------------------------------------------------------- AccountSim guard (BC-7)


def _calendar(cfg: AccountConfig) -> SessionCalendar:
    return SessionCalendar(
        date(2026, 3, 1), date(2026, 3, 31), times=cfg.session_times(SessionTimes())
    )


FEES = {"MES": Decimal("0"), "MNQ": Decimal("0"), "ES": Decimal("0"), "NQ": Decimal("0")}


@pytest.mark.parametrize(
    "override",
    [
        {"maximum_loss_limit": {"mode": "intraday_trailing"}},
        {"maximum_loss_limit": {"mode": "static"}},
        {"maximum_loss_limit": {"trail_lock": False}},
        {"flat_deadline_enabled": False},
        {"no_trade_windows": [{"start": "09:25", "end": "09:35"}]},
        {"min_trading_days": 5},
    ],
)
def test_account_sim_refuses_non_default_paper_settings(override: dict[str, Any]) -> None:
    cfg = PaperAccountRules.model_validate(override)
    with pytest.raises(ValueError, match="not supported by the backtest Account_Simulator"):
        AccountSim(cfg, _calendar(cfg), FEES)


def test_account_sim_accepts_plain_and_default_paper_configs() -> None:
    for cfg in (AccountConfig(), PaperAccountRules()):
        assert AccountSim(cfg, _calendar(cfg), FEES).balance == Decimal("50000.00")


def test_account_sim_reports_only_backtest_rule_ids_for_a_paper_config() -> None:
    # A default PaperAccountRules lists its opt-in paper rules as disabled; the
    # backtest's AttemptResult.disabled_rules holds AccountRuleId values only.
    paper = PaperAccountRules()
    assert "no_trade_windows" in paper.disabled_rules()
    sim = AccountSim(paper, _calendar(paper), FEES)
    assert sim.disabled_rules == AccountConfig().disabled_rules() == ()


# ---------------------------------------------------------------- nexus block


@pytest.mark.parametrize(
    "target", ["practice", "evaluation", "funded", "agent", "env:SKYLIT_NEXUS_ACCT"]
)
def test_nexus_account_takes_a_type_or_an_env_reference(target: str) -> None:
    cfg = parse(doc(account(nexus={"enabled": True, "account": target})))
    assert cfg.accounts[0].nexus.account == target
    assert cfg.accounts[0].nexus.enabled


@pytest.mark.parametrize("target", ["acct_12345", "12345", "env:lower", "env:"])
def test_nexus_account_refuses_a_literal_id(target: str) -> None:
    [problem] = problems(doc(account(nexus={"account": target})))
    assert problem.startswith("accounts[0].nexus.account: String should match pattern")


def test_nexus_defaults_off_and_has_no_instrument_map() -> None:
    entry = AccountEntry.model_validate({"id": "a", "preset": "none"})
    assert (entry.nexus.enabled, entry.nexus.account, entry.nexus.test_only) == (
        False,
        "practice",
        False,
    )
    [problem] = problems(doc(account(nexus={"instrument_map": {"MNQ": "NQ"}})))
    assert problem.startswith("accounts[0].nexus.instrument_map: Extra inputs are not permitted")
