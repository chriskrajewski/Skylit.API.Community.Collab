"""``plugins[].execution`` (design D3, T1-T2): shape and the cross-field rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fse.live.host.config import (
    RunnerConfig,
    RunnerConfigError,
    execution_brokerage_problem,
    parse_runner_config,
)

type Doc = dict[str, Any]

RISK: Doc = {"max_contracts": 4, "max_daily_loss": "500.00", "max_trades_per_day": 6}


def plugin(pid: str = "alpha", mode: str | None = "practice", **extra: Any) -> Doc:
    out: Doc = {
        "id": pid,
        "module": "x.y:Plugin",
        "risk": dict(RISK),
        "hold_through_break": False,
        **extra,
    }
    if mode is not None:
        out["execution"] = {"order_mode": mode}
    return out


def parse(plugins: list[Doc], accounts: list[Doc] | None = None) -> RunnerConfig:
    doc: Doc = {"version": 1, "plugins": plugins}
    if accounts is not None:
        doc["accounts"] = accounts
    return parse_runner_config(doc, Path("runner.yaml"))


def problems(plugins: list[Doc], accounts: list[Doc] | None = None) -> list[str]:
    with pytest.raises(RunnerConfigError) as info:
        parse(plugins, accounts)
    assert info.value.exit_code == 2
    return info.value.problems


# ---------------------------------------------------------------- T1 shape


@pytest.mark.parametrize("execution", [None, {}, {"order_mode": "paper"}])
def test_absent_empty_and_paper_all_parse_as_paper(execution: Doc | None) -> None:
    entry: Doc = {"id": "alpha", "module": "x.y:Plugin"}
    if execution is not None:
        entry["execution"] = execution
    cfg = parse([entry])
    assert cfg.plugins[0].execution.order_mode == "paper"


@pytest.mark.parametrize(
    ("execution", "part"),
    [
        ({"order_mode": "paper", "surprise": 1}, "plugins[0].execution.surprise"),
        ({"order_mode": "live"}, "plugins[0].execution.order_mode"),
        (None, "plugins[0].execution"),
    ],
)
def test_bad_shapes_give_path_errors(execution: Doc | None, part: str) -> None:
    entry: Doc = {"id": "alpha", "module": "x.y:Plugin", "execution": execution}
    assert any(p.startswith(part) for p in problems([entry]))


@pytest.mark.parametrize("mode", ["practice", "combine"])
def test_a_complete_broker_entry_parses(mode: str) -> None:
    accounts = [{"id": "tsx", "preset": "topstep_50k_combine"}]
    cfg = parse([plugin(mode=mode, account="tsx")], accounts)
    assert cfg.plugins[0].execution.order_mode == mode


# ---------------------------------------------------------------- T2 rules


def test_no_risk_block_reports_only_rule_1() -> None:
    entry = plugin()
    del entry["risk"]
    found = problems([entry])
    assert found == [
        "plugins[0].execution.order_mode: order_mode practice needs a host-brokered plugin "
        "(a risk block)"
    ]


@pytest.mark.parametrize("cap", ["max_daily_loss", "max_trades_per_day"])
def test_each_cap_is_required(cap: str) -> None:
    entry = plugin()
    del entry["risk"][cap]
    assert problems([entry]) == [
        f"plugins[0].risk.{cap}: required when execution.order_mode is practice"
    ]


@pytest.mark.parametrize("explicit", [True, False])
def test_hold_through_break_true_or_defaulted_is_refused(explicit: bool) -> None:
    entry = plugin()
    if explicit:
        entry["hold_through_break"] = True
    else:
        del entry["hold_through_break"]
    assert problems([entry]) == [
        "plugins[0].hold_through_break: must be false when execution.order_mode is practice "
        "(Topstep accounts are flat by the close)"
    ]


def test_combine_needs_an_account() -> None:
    assert problems([plugin(mode="combine")]) == [
        "plugins[0].account: combine needs a paper account with the flat deadline on"
    ]


def test_combine_needs_the_flat_deadline_on() -> None:
    accounts = [
        {
            "id": "tsx",
            "preset": "topstep_50k_combine",
            "rules": {"flat_deadline_enabled": False},
        }
    ]
    assert problems([plugin(mode="combine", account="tsx")], accounts) == [
        "plugins[0].account: combine needs a paper account with the flat deadline on"
    ]


def test_practice_is_not_held_to_the_flat_deadline() -> None:
    accounts = [{"id": "free", "preset": "none"}]
    parse([plugin(account="free")], accounts)


def test_two_non_paper_entries_are_refused_even_when_one_is_disabled() -> None:
    found = problems([plugin("alpha"), plugin("other", enabled=False)])
    assert found == [
        "plugins: at most one plugin may have a non-paper execution.order_mode; "
        "found alpha, other"
    ]


def test_a_practice_plugin_may_be_on_a_nexus_mirrored_account() -> None:
    nexus = {"enabled": True, "account": "practice"}
    accounts = [{"id": "nx", "preset": "none", "nexus": nexus}]
    assert parse([plugin(account="nx")], accounts).plugins[0].account == "nx"
    combine = [
        {"id": "nx", "preset": "none", "rules": {"flat_deadline_enabled": True}, "nexus": nexus}
    ]
    parse([plugin(mode="combine", account="nx")], combine)


def test_all_problems_are_reported_together() -> None:
    bad = plugin(hold_through_break=True)
    del bad["risk"]["max_daily_loss"]
    found = problems([bad, plugin("other", mode="combine")])
    assert len(found) == 4
    assert found[-1].startswith("plugins: at most one plugin")


def test_paper_entries_are_never_checked() -> None:
    parse([{"id": "a", "module": "x.y:Plugin"}, {"id": "b", "module": "x.y:Plugin"}])


# ---------------------------------------------------------------- execution_brokerage_problem


def _brokerage(**kw: Any) -> str | None:
    cfg = parse([plugin()])
    args: dict[str, Any] = {
        "brokerage": "host",
        "broker_orders": True,
        "instruments": frozenset({"MNQ"}),
    } | kw
    return execution_brokerage_problem(cfg, cfg.plugins[0], **args)


def test_a_broker_capable_host_plugin_has_no_brokerage_problem() -> None:
    assert _brokerage() is None


def test_brokerage_problems_name_the_rule() -> None:
    assert _brokerage(brokerage="self") == (
        "plugins[0].execution.order_mode: plugin 'alpha' is self-brokered; "
        'order_mode practice needs brokerage "host"'
    )
    assert "needs().broker_orders is false" in str(_brokerage(broker_orders=False))
    found = str(_brokerage(instruments=frozenset({"SIL", "MNQ", "MGC"})))
    assert "declares MGC, SIL" in found
    assert "live.ignored_instruments" in found


def test_a_paper_entry_has_no_brokerage_problem() -> None:
    cfg = parse([{"id": "a", "module": "x.y:Plugin"}])
    assert (
        execution_brokerage_problem(
            cfg, cfg.plugins[0], brokerage="self", broker_orders=False, instruments=frozenset()
        )
        is None
    )
