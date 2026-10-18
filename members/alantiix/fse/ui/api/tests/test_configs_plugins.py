from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from fse_ui.configs import ORDER_MODE_ELSEWHERE, ConfigStore, validate
from fse_ui.order_modes import apply_order_mode
from fse_ui.plugins import check_plugins
from tests.conftest import PROJECT, Console
from tests.runner_fixture import baseline_text, runner_text

BASELINE = baseline_text(PROJECT)
RUNNER = runner_text(PROJECT)


# The shipped baseline needs Operator values; the playbook entries read this loadable copy.
BASELINE_FILE = Path(tempfile.mkdtemp(prefix="fse-baseline-")) / "playbook_baseline.yaml"
BASELINE_FILE.write_text(BASELINE, encoding="utf-8")


def _runner(**changes: object) -> dict:
    data = yaml.safe_load(RUNNER)
    for plugin in data["plugins"]:
        if plugin["id"] == "playbook":
            plugin["config"] = {"strategy_config": str(BASELINE_FILE)}
    return {**data, **changes}


def test_seed_and_round_trip(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    assert store.seed(PROJECT) >= 3
    assert store.seed(PROJECT) == 0  # only once
    first = store.get("strategy", "playbook_baseline")
    edited = first["text"].replace("config_id:", "config_id:", 1) + "# note\n"
    result, saved = store.save("strategy", "playbook_baseline", edited, author="t")
    assert result.ok and saved is not None and saved["sha"] != first["sha"]
    assert len(saved["revisions"]) == 2
    assert store.current_path("strategy", "playbook_baseline").read_text() == edited
    restored = store.restore("strategy", "playbook_baseline", first["sha"], author="t")
    assert restored["current"] == first["sha"]
    assert "+# note" in store.diff("strategy", "playbook_baseline", first["sha"], saved["sha"])


def test_invalid_strategy_is_not_stored(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.seed(PROJECT)
    before = store.get("strategy", "playbook_baseline")
    bad = before["text"].replace("kill_switches:", "kill_switches:\n  bogus_key: 1", 1)
    result, saved = store.save("strategy", "playbook_baseline", bad, author="t")
    assert saved is None and not result.ok
    err = next(e for e in result.errors if e.key_path == "kill_switches.bogus_key")
    assert err.line is not None and err.kind == "unknown key"
    assert store.get("strategy", "playbook_baseline")["sha"] == before["sha"]


def test_duplicate_keys_are_caught() -> None:
    result = validate("research", "a: 1\na: 2\n")
    assert not result.ok and result.errors[0].kind == "duplicate_key"
    assert result.errors[0].line == 2


def test_runner_problems_carry_key_paths_and_lines() -> None:
    data = _runner()
    data["data_plane"]["projectx"]["poll_interval_s"] = 1
    result = validate("runner", yaml.safe_dump(data, sort_keys=False))
    assert not result.ok
    assert result.errors[0].key_path == "data_plane.projectx.poll_interval_s"
    assert result.errors[0].line is not None


def test_sweep_is_checked_against_the_baseline() -> None:
    bad = validate("sweep", "exit_modes: [nope]\nunknown: 1\n", baseline_text=BASELINE)
    assert not bad.ok and any("unknown key" in e.message for e in bad.errors)


def test_config_api_blocks_an_out_of_range_save(console: Console) -> None:
    cfg = console.get("/api/configs/strategy/playbook_baseline").json()
    data = yaml.safe_load(cfg["text"])
    data["kill_switches"]["max_trades"][
        "limit"
        if "limit" in data["kill_switches"]["max_trades"]
        else next(iter(data["kill_switches"]["max_trades"]))
    ] = -5
    r = console.post(
        "/api/configs/strategy/playbook_baseline",
        json={"text": yaml.safe_dump(data, sort_keys=False)},
    )
    assert r.status_code == 422
    errors = r.json()["validation"]["errors"]
    assert errors and errors[0]["key_path"].startswith("kill_switches.max_trades")
    ok = console.post(
        "/api/configs/strategy/playbook_baseline",
        json={"text": cfg["text"] + "# fixed\n", "message": "fix"},
    )
    assert ok.status_code == 200
    assert len(ok.json()["config"]["revisions"]) == 2
    audit = console.get("/api/audit").json()
    assert audit[0]["detail"]["name"] == "playbook_baseline"


def test_schema_endpoints(console: Console) -> None:
    assert "properties" in console.get("/api/schemas/runner").json()
    assert "properties" in console.get("/api/schemas/strategy").json()
    s = console.get(
        "/api/schemas/plugin", params={"module": "fse.live.plugins.template:TemplatePlugin"}
    ).json()
    assert "entry_time" in s["properties"]


# ---------------------------------------------------------------- plugins


def _check(data: dict, tmp_path: Path) -> dict:
    return check_plugins(
        yaml.safe_dump(data, sort_keys=False), project_dir=PROJECT, scratch=tmp_path
    )


def test_shipped_runner_passes(tmp_path: Path) -> None:
    assert check_plugins(RUNNER, project_dir=PROJECT, scratch=tmp_path)["ok"]


def test_template_without_risk_needs_a_risk_block(tmp_path: Path) -> None:
    data = _runner()
    tpl = next(p for p in data["plugins"] if p["id"] == "template")
    tpl["enabled"] = True
    del tpl["risk"]
    result = _check(data, tmp_path)
    assert not result["ok"]
    assert any("needs a risk block" in p for p in result["problems"]), result
    tpl["risk"] = {"max_contracts": 1}
    assert _check(data, tmp_path)["ok"]


def test_self_brokered_plugin_with_fills_is_rejected(tmp_path: Path) -> None:
    data = _runner()
    pb = next(p for p in data["plugins"] if p["id"] == "playbook")
    pb["enabled"] = True
    pb["fills"] = {"commission_rt": {"MNQ": "1.24"}}
    pb["risk"] = {"max_contracts": 1}
    result = _check(data, tmp_path)
    assert any("must not set fills or risk" in p for p in result["problems"]), result


def test_module_outside_the_project_is_rejected(tmp_path: Path) -> None:
    data = _runner()
    data["plugins"].append(
        {
            "id": "evil",
            "module": "json.decoder:JSONDecoder",
            "enabled": True,
            "risk": {"max_contracts": 1},
        }
    )
    result = _check(data, tmp_path)
    assert any("outside the Project folder" in p for p in result["problems"]), result


def test_bad_plugin_config_is_reported(tmp_path: Path) -> None:
    data = _runner()
    tpl = next(p for p in data["plugins"] if p["id"] == "template")
    tpl["enabled"] = True
    tpl["config"]["qty"] = 0
    result = _check(data, tmp_path)
    assert any("bad config" in p for p in result["problems"]), result


def _post_check(console: Console, data: dict) -> dict:
    r = console.post("/api/plugins/check", json={"text": yaml.safe_dump(data, sort_keys=False)})
    assert r.status_code == 200
    return r.json()


def test_plugin_check_api_reports_account_cross_references(console: Console) -> None:
    def runner(accounts: list[dict], **accounts_of: str) -> dict:
        data = _runner(accounts=accounts)
        for plugin in data["plugins"]:
            if plugin["id"] in accounts_of:
                plugin["account"] = accounts_of[plugin["id"]]
        return data

    cases = [
        (
            runner([{"id": "topstep_a", "preset": "topstep_50k_combine"}], alpha="nope"),
            "plugins[0].account: unknown account 'nope'; accounts are ['topstep_a']",
        ),
        (
            runner([{"id": "a", "preset": "none"}], alpha="a", playbook="a"),
            "plugins[1].account: an account needs a host-brokered plugin (a risk block)",
        ),
        (
            runner([{"id": "a", "preset": "none"}, {"id": "a", "preset": "none"}], alpha="a"),
            "accounts[1].id: duplicate account id 'a'",
        ),
    ]
    netting = runner([{"id": "a", "preset": "none", "nexus": {"enabled": True}}], alpha="a")
    for plugin in netting["plugins"]:
        if plugin["id"] == "bravo":
            plugin.update(enabled=True, account="a")
    cases.append(
        (
            netting,
            "accounts[0].nexus: mirrored account 'a' has 2 enabled plugins (alpha, bravo); "
            "a mirrored account takes one",
        )
    )
    for data, expected in cases:
        body = _post_check(console, data)
        assert body["ok"] is False
        assert body["problems"] == [expected]
        assert body["plugins"] == []


def test_check_plugins_reports_a_self_brokered_plugin_on_an_account(tmp_path: Path) -> None:
    data = _runner(accounts=[{"id": "a", "preset": "none"}])
    pb = next(p for p in data["plugins"] if p["id"] == "playbook")
    pb.update(enabled=True, account="a", risk={"max_contracts": 1})
    result = _check(data, tmp_path)
    expected = (
        "plugins[1].account: plugin 'playbook' is self-brokered; an account needs brokerage "
        '"host"'
    )
    item = next(p for p in result["plugins"] if p["id"] == "playbook")
    assert item["brokerage"] == "self"
    assert expected in item["problems"]
    assert not result["ok"] and expected in result["problems"]
    alpha = next(p for p in result["plugins"] if p["id"] == "alpha")
    assert alpha["problems"] == []


def test_plugin_check_api(console: Console) -> None:
    r = console.post("/api/plugins/check", json={"text": RUNNER})
    assert r.status_code == 200 and r.json()["ok"]
    known = console.get("/api/plugins/known").json()
    assert {k["module"] for k in known} >= {"fse.live.plugins.template:TemplatePlugin"}


def test_no_backtested_values_are_served_without_the_file(console: Console) -> None:
    r = console.get("/api/plugins/backtested")
    assert r.status_code == 200
    assert r.json() == {"runners": {}}  # no research/paper_ops/backtested.yaml ships


# ---------------------------------------------------------------- order modes (Live only)


def _practice() -> str:
    return apply_order_mode(RUNNER, "alpha", "practice", ("375", 5))


def _combine() -> str:
    return _practice().replace("\n      order_mode: practice\n", "\n      order_mode: combine\n")


def test_a_configs_page_save_with_practice_is_422(console: Console) -> None:
    r = console.post("/api/configs/runner/runner", json={"text": _practice()})
    assert r.status_code == 422
    assert r.json()["validation"]["errors"] == [
        {
            "key_path": "plugins[0].execution.order_mode",
            "message": ORDER_MODE_ELSEWHERE,
            "line": None,
            "kind": "invalid",
        }
    ]
    # Once Live → Order mode set it, other edits that keep it are fine.
    store: ConfigStore = console.app.state.configs
    store.save("runner", "runner", _practice(), author="t", allow_order_mode=True)
    r = console.post("/api/configs/runner/runner", json={"text": _practice() + "# note\n"})
    assert r.status_code == 200, r.text
    # ... but taking it out (practice → paper) is an order-mode change too
    r = console.post("/api/configs/runner/runner", json={"text": RUNNER})
    assert r.status_code == 422


def test_a_restore_of_a_practice_revision_is_422(console: Console) -> None:
    store: ConfigStore = console.app.state.configs
    _, saved = store.save("runner", "runner", _practice(), author="t", allow_order_mode=True)
    assert saved is not None
    practice_sha = saved["sha"]
    back = apply_order_mode(_practice(), "alpha", "paper")
    _, saved = store.save("runner", "runner", back, author="t", allow_order_mode=True)
    assert saved is not None
    r = console.post("/api/configs/runner/runner/restore", json={"sha": practice_sha})
    assert r.status_code == 422 and r.json()["detail"] == ORDER_MODE_ELSEWHERE
    assert store.get("runner", "runner")["sha"] == saved["sha"]


def test_combine_is_order_mode_elsewhere_like_practice(console: Console) -> None:
    """Configs page / restore / select refuse combine the same way as practice; only
    Live → Order mode may set it (and even then needs an account + secrets)."""
    store: ConfigStore = console.app.state.configs
    r = console.post("/api/configs/runner/runner", json={"text": _combine()})
    assert r.status_code == 422
    assert [e["message"] for e in r.json()["validation"]["errors"]] == [ORDER_MODE_ELSEWHERE]
    r = console.post("/api/configs/runner/fresh", json={"text": _combine(), "create": True})
    assert r.status_code == 422
    assert [e["message"] for e in r.json()["validation"]["errors"]] == [ORDER_MODE_ELSEWHERE]
    planted = store._write_revision("runner", "runner", _combine(), author="t", message="x")
    store._write_revision("runner", "runner", RUNNER, author="t", message="y")
    r = console.post("/api/configs/runner/runner/restore", json={"sha": planted})
    assert r.status_code == 422 and r.json()["detail"] == ORDER_MODE_ELSEWHERE
    store._write_revision("runner", "planted", _combine(), author="t", message="x")
    r = console.put(
        "/api/runner/settings",
        json={"mode": "run", "runner_config": "planted", "strategy_config": "playbook_baseline"},
    )
    assert r.status_code == 422 and "has a non-paper order_mode" in r.json()["detail"]
    # Local console: Order mode refuses Combine for the same reason as Practice.
    sha = store.get("runner", "runner")["sha"]
    r = console.post(
        "/api/order-mode",
        json={
            "plugin_id": "alpha",
            "order_mode": "combine",
            "expected_sha": sha,
            "max_daily_loss": "375",
            "max_trades_per_day": 5,
        },
    )
    assert r.status_code == 422
    assert "console plugin orders run only on the AWS runner" in r.json()["detail"]


def test_check_plugins_reports_the_execution_brokerage_problem(tmp_path: Path) -> None:
    data = _runner()
    tpl = next(p for p in data["plugins"] if p["id"] == "template")
    tpl.update(enabled=True, hold_through_break=False, execution={"order_mode": "practice"})
    tpl["risk"].update(max_daily_loss="100", max_trades_per_day=2)
    result = _check(data, tmp_path)
    expected = (
        "plugins[2].execution.order_mode: plugin 'template' does not support broker orders "
        "(needs().broker_orders is false)"
    )
    assert not result["ok"] and expected in result["problems"], result
    item = next(p for p in result["plugins"] if p["id"] == "template")
    assert expected in item["problems"]
    # Alpha declares broker_orders: the same edit passes
    assert check_plugins(_practice(), project_dir=PROJECT, scratch=tmp_path)["ok"]
