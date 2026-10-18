"""Cross-reference rules of ``accounts:`` and ``plugins[].account`` (design 2.3, A-9, H-7, BC-1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from fse.live.host.config import (
    RunnerConfig,
    RunnerConfigError,
    account_brokerage_problem,
    account_problems,
    load_runner_config,
    parse_runner_config,
)
from fse.live.plugins import BarNeed, DataNeeds
from fse.live.plugins.loader import PluginLoadError
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import PROJECT_DIR, FakeDataPlane, host_entry, make_host, ny
from tests.fakes.plugins import RecordingPlugin

T0 = ny(date(2026, 10, 14), 10, 0)


type Doc = dict[str, Any]


def plugin(pid: str, account: str | None = None, *, risk: bool = True, **extra: Any) -> Doc:
    out: dict[str, Any] = {"id": pid, "module": "x.y:Plugin", **extra}
    if risk:
        out["risk"] = {"max_contracts": 4}
    if account is not None:
        out["account"] = account
    return out


def acct(aid: str, preset: str = "none", **extra: Any) -> dict[str, Any]:
    return {"id": aid, "preset": preset, **extra}


def parse(plugins: list[Doc], accounts: list[Doc]) -> RunnerConfig:
    return parse_runner_config(
        {"version": 1, "plugins": plugins, "accounts": accounts}, Path("runner.yaml")
    )


def problems(plugins: list[Doc], accounts: list[Doc]) -> list[str]:
    with pytest.raises(RunnerConfigError) as info:
        parse(plugins, accounts)
    assert info.value.exit_code == 2
    return info.value.problems


MIRROR: dict[str, Any] = {"enabled": True, "account": "practice"}


# ---------------------------------------------------------------- A-9


def test_unknown_account_id() -> None:
    assert problems([plugin("alpha", "nope")], [acct("topstep_a")]) == [
        "plugins[0].account: unknown account 'nope'; accounts are ['topstep_a']"
    ]


def test_account_on_a_self_brokered_entry_without_risk() -> None:
    playbook = plugin("playbook", "topstep_a", risk=False)
    assert problems([plugin("alpha"), playbook], [acct("topstep_a")]) == [
        "plugins[1].account: an account needs a host-brokered plugin (a risk block)"
    ]


def test_duplicate_account_ids_report_every_repeat() -> None:
    accounts = [acct("a"), acct("b"), acct("a"), acct("a")]
    assert problems([plugin("alpha", "a")], accounts) == [
        "accounts[2].id: duplicate account id 'a'",
        "accounts[3].id: duplicate account id 'a'",
    ]


def test_mirrored_account_takes_one_enabled_plugin() -> None:
    plugins = [
        plugin("alpha", "a"),
        plugin("off", "a", enabled=False),  # a disabled member does not count
        plugin("bravo", "a"),
    ]
    assert problems(plugins, [acct("a", nexus=MIRROR)]) == [
        "accounts[0].nexus: mirrored account 'a' has 2 enabled plugins (alpha, bravo); "
        "a mirrored account takes one"
    ]
    # Unmirrored, sharing is allowed (DP-2).
    assert parse(plugins, [acct("a")]).accounts[0].id == "a"


def test_two_mirrored_accounts_cannot_target_one_nexus_account() -> None:
    plugins = [plugin("alpha", "a"), plugin("bravo", "b")]
    accounts = [acct("a", nexus=MIRROR), acct("b", nexus=MIRROR)]
    assert problems(plugins, accounts) == [
        "accounts[1].nexus.account: accounts 'a' and 'b' both mirror to 'practice'"
    ]
    other = [acct("a", nexus=MIRROR), acct("b", nexus={"enabled": True, "account": "funded"})]
    assert len(parse(plugins, other).accounts) == 2
    disabled = [acct("a", nexus=MIRROR), acct("b", nexus={"account": "practice"})]
    assert len(parse(plugins, disabled).accounts) == 2


def test_cross_reference_problems_are_returned_together_with_their_paths() -> None:
    plugins = [
        plugin("alpha", "a"),
        plugin("bravo", "a"),
        plugin("tpl", "ghost"),
        plugin("pb", "b", risk=False),
    ]
    accounts = [acct("a", nexus=MIRROR), acct("b"), acct("b")]
    found = problems(plugins, accounts)
    assert found == [
        "accounts[2].id: duplicate account id 'b'",
        "plugins[2].account: unknown account 'ghost'; accounts are ['a', 'b']",
        "plugins[3].account: an account needs a host-brokered plugin (a risk block)",
        "accounts[0].nexus: mirrored account 'a' has 2 enabled plugins (alpha, bravo); "
        "a mirrored account takes one",
    ]


def test_a_field_error_reports_only_field_errors() -> None:
    plugins = [plugin("alpha", "ghost")]
    accounts = [acct("a", rules={"daily_loss_limit": {"value": "0"}})]
    assert problems(plugins, accounts) == [
        "accounts[0].rules.daily_loss_limit.value: 0 should be from 0.01 to 10000000.00 dollars"
    ]


def test_bad_ids_are_field_errors() -> None:
    found = problems([plugin("alpha", "implicit:alpha")], [acct("Bad")])
    assert found[0].startswith("plugins[0].account: String should match pattern")
    assert found[1].startswith("accounts[0].id: String should match pattern")


def test_at_most_eight_accounts() -> None:
    accounts = [acct(f"a{i}") for i in range(9)]
    [problem] = problems([plugin("alpha")], accounts)
    assert problem == "accounts: Value should have at most 8 items after validation, not 9"


def test_an_unused_account_is_valid() -> None:
    cfg = parse([plugin("alpha")], [acct("spare")])
    assert account_problems(cfg) == []
    assert cfg.plugins[0].account is None


# ---------------------------------------------------------------- brokerage helper


def test_account_brokerage_problem_uses_the_yaml_index() -> None:
    plugins = [plugin("alpha"), plugin("off", enabled=False), plugin("x", "a")]
    cfg = parse(plugins, [acct("a")])
    x = cfg.plugins[2]
    assert account_brokerage_problem(cfg, x, "self") == (
        "plugins[2].account: plugin 'x' is self-brokered; an account needs brokerage \"host\""
    )
    assert account_brokerage_problem(cfg, x, "host") is None
    assert account_brokerage_problem(cfg, cfg.plugins[0], "self") is None


class SelfBrokered(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth", brokerage="self")


@pytest.mark.asyncio
async def test_plugin_host_refuses_a_self_brokered_plugin_on_an_account(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    entries = [
        host_entry("p1"),
        host_entry("off", enabled=False),
        host_entry("x", account="a"),
    ]
    with pytest.raises(PluginLoadError) as info:
        make_host(
            tmp_path,
            {"p1": RecordingPlugin, "x": SelfBrokered},
            entries,
            clock,
            FakeDataPlane(clock),
            accounts=[acct("a")],
        )
    assert (
        "plugins[2].account: plugin 'x' is self-brokered; an account needs brokerage \"host\""
        in info.value.problems
    )


@pytest.mark.asyncio
async def test_plugin_host_accepts_a_host_brokered_plugin_on_an_account(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin},
        [host_entry("p1", account="a")],
        clock,
        FakeDataPlane(clock),
        accounts=[acct("a")],
    )
    assert host.plugin_ids == ("p1",)


# ---------------------------------------------------------------- BC-1


def _runner_documents() -> list[Path]:
    paths = sorted((PROJECT_DIR / "configs").glob("*.yaml"))
    paths += sorted(p for p in (PROJECT_DIR / "tests").rglob("*.yaml") if "fixtures" in p.parts)
    out: list[Path] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 1 and "plugins" in data:
            out.append(path)
    return out


def test_every_committed_runner_yaml_validates_unchanged() -> None:
    docs = _runner_documents()
    names = {p.name for p in docs}
    assert names >= {
        "runner.yaml",
        "runner.paper.yaml",
    }
    for path in docs:
        cfg = load_runner_config(path)
        assert account_problems(cfg) == [], path
        assert cfg.accounts == (), path
        assert all(p.account is None for p in cfg.plugins), path
        assert cfg.version == 1
