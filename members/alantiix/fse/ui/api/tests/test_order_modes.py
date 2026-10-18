"""Live → Order mode (design.md 2-4, plan.md Feature 2): reasons, the YAML edit, the
POST rules and restart safety. moto SSM only; nothing reaches AWS or a broker."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from fse.live.host.config import parse_runner_config

import fse_ui.api as api_module
from fse_ui import order_modes as om
from fse_ui.configs import ORDER_MODE_ELSEWHERE, ConfigConflict, ConfigStore
from fse_ui.runner import RunnerError, runner_settings
from fse_ui.secrets_store import LocalSecretStore, SsmSecretStore
from tests.aws_support import INSTANCE, Aws
from tests.conftest import PROJECT, Console, make_settings
from tests.runner_fixture import runner_text

RUNNER = runner_text(PROJECT)
CONTROL = "/fse/control/plugin_orders"
PRACTICE_ID = "PRX-canary-7731"
COMBINE_ID = "CMB-canary-5512"
API_KEY = "key-canary-0042"
CANARIES = (PRACTICE_ID, COMBINE_ID, API_KEY)
TEMPLATE_CONFIG = {
    "instrument": "MNQ",
    "entry_time": "10:00",
    "direction": "long",
    "qty": 1,
    "stop_ticks": 40,
    "target_ticks": 40,
    "flatten_time": "15:55",
    "warmup_bars": 5,
}
FILLS = {
    "trade_through_ticks": 1,
    "slippage_ticks": 0,
    "stop_fill": "at_stop",
    "commission_rt": {"MNQ": "1.24"},
}
PRACTICE = {"order_mode": "practice", "max_daily_loss": "375.50", "max_trades_per_day": 6}


def plugin(pid: str, cls: str = "BrokerTemplate", **over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": pid,
        "module": f"tests.order_plugins:{cls}",
        "enabled": True,
        "config": dict(TEMPLATE_CONFIG),
        "fills": dict(FILLS),
        "risk": {"max_contracts": 1, "max_open_trades": 1},
        "hold_through_break": False,
    }
    entry.update(over)
    return entry


def runner_text(*extra: dict[str, Any], **top: Any) -> str:
    data = yaml.safe_load(RUNNER)
    data["plugins"].extend(extra)
    data.update(top)
    return yaml.safe_dump(data, sort_keys=False)


def practice_text(text: str, pid: str = "alpha") -> str:
    return om.apply_order_mode(text, pid, "practice", ("375", 5))


def with_combine_account(text: str, pid: str = "alpha") -> str:
    """``text`` with a flat-deadline paper account on ``pid`` (Combine needs it)."""
    data = yaml.safe_load(text)
    data["accounts"] = [{"id": "topstep_a", "preset": "topstep_50k_combine"}]
    for entry in data["plugins"]:
        if entry.get("id") == pid:
            entry["account"] = "topstep_a"
            break
    return yaml.safe_dump(data, sort_keys=False)


def combine_text(text: str, pid: str = "alpha") -> str:
    return om.apply_order_mode(with_combine_account(text), pid, "combine", ("375", 5))


# ---------------------------------------------------------------- consoles


class FakeRunner:
    """A runner controller whose state the test sets; counts restarts."""

    def __init__(self, state: str = "stopped", *, held: bool = False) -> None:
        self.state, self.held = state, held
        self.error: str | None = None
        self.fail: str | None = None
        self.gate: threading.Event | None = None
        self.calls: list[str] = []

    async def status(self) -> dict[str, Any]:
        return {"state": self.state, "held": self.held, "error": self.error, "settings": {}}

    async def start(self) -> dict[str, Any]:
        self.calls.append("start")
        return await self.status()

    async def stop(self) -> dict[str, Any]:
        self.calls.append("stop")
        return await self.status()

    async def restart(self) -> dict[str, Any]:
        self.calls.append("restart")
        if self.gate is not None:
            await asyncio.to_thread(self.gate.wait, 10)
        if self.fail is not None:
            raise RunnerError(self.fail)
        return await self.status()

    async def close(self) -> None:
        return None


@pytest.fixture
def orders(aws_console: Callable[..., Console], aws: Aws) -> Callable[..., Console]:
    """``make(switch=..., secrets=..., **settings)``: an AWS console (SSM runner, moto)."""

    def make(*, switch: str | None = "enabled", secrets: bool = True, **over: Any) -> Console:
        if switch is not None:
            aws.ssm.put_parameter(Name=CONTROL, Value=switch, Type="String", Overwrite=True)
        if secrets:
            for name, value in (
                ("PRACTICE_ACCOUNT_ID", PRACTICE_ID),
                ("COMBINE_ACCOUNT_ID", COMBINE_ID),
                ("PROJECTX_USERNAME", "op-user"),
                ("PROJECTX_API_KEY", API_KEY),
            ):
                aws.ssm.put_parameter(
                    Name=f"/fse/secrets/{name}", Value=value, Type="SecureString", Overwrite=True
                )
        base: dict[str, Any] = {
            "executor": "local",
            "runner_controller": "ssm",
            "runner_instance_id": INSTANCE,
            "plugin_orders": True,
            "plugin_orders_param": CONTROL,
            "topstep_verified_on": "2026-10-13",
        }
        base.update(over)
        return aws_console(**base)

    return make


def report(console: Console, state: str, *, result: str = "success") -> None:
    path: Path = console.app.state.settings.runner_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": state, "at": time.time(), "result": result}))


def fake(console: Console, state: str = "stopped", **kw: Any) -> FakeRunner:
    runner = FakeRunner(state, **kw)
    console.app.state.runner = runner
    return runner


def store(console: Console) -> ConfigStore:
    s: ConfigStore = console.app.state.configs
    return s


def use(console: Console, name: str, text: str, *, practice: bool = False) -> None:
    """Create runner config ``name`` and select it. ``practice``: create it paper, select
    it, then save ``text`` as the Order mode endpoint does (``allow_order_mode``)."""
    st = store(console)
    result, saved = st.save("runner", name, RUNNER if practice else text, author="t", create=True)
    assert saved is not None, result.public()
    r = console.put(
        "/api/runner/settings",
        json={"mode": "run", "runner_config": name, "strategy_config": "playbook_baseline"},
    )
    assert r.status_code == 200, r.text
    if practice:
        result, saved = st.save("runner", name, text, author="t", allow_order_mode=True)
        assert saved is not None, result.public()


def sha(console: Console, name: str = "runner") -> str:
    return str(store(console).get("runner", name)["sha"])


def post(console: Console, plugin_id: str = "alpha", **body: Any) -> Any:
    body.setdefault("order_mode", "practice")
    if body["order_mode"] in ("practice", "combine"):
        body.setdefault("max_daily_loss", PRACTICE["max_daily_loss"])
        body.setdefault("max_trades_per_day", PRACTICE["max_trades_per_day"])
    name = runner_settings(console.app.state.db)["runner_config"]
    body.setdefault("expected_sha", sha(console, name))
    return console.post("/api/order-mode", json={"plugin_id": plugin_id, **body})


def busy(console: Console, pid: str, *, positions: int = 1, working: int = 0) -> None:
    d: Path = console.app.state.settings.live_state_dir / "plugins" / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(
        json.dumps(
            {
                "positions": [{"tag": f"s{i}"} for i in range(positions)],
                "working": [{"tag": f"w{i}"} for i in range(working)],
                "updated_ns": 1_791_900_000_000_000_000,
            }
        )
    )


def order_line(
    console: Console, pid: str, mode: str, *, failures: tuple[str, ...] = (), filler: int = 0
) -> None:
    runs: Path = console.app.state.settings.runs_dir
    run = runs / "run-20261013T130000Z-abcd1234"
    (run / "plugins" / pid).mkdir(parents=True, exist_ok=True)
    started = {"t_ns": 2, "kind": "plugin_started", "plugin": pid}
    (run / "host_log.jsonl").write_text(
        json.dumps({"t_ns": 1, "kind": "heartbeat"}) + "\n" + json.dumps(started) + "\n"
    )
    line = {
        "t_ns": 5,
        "kind": "order_mode",
        "level": "warning" if failures else "info",
        "plugin": pid,
        "requested": "practice",
        "mode": mode,
        "failures": list(failures),
    }
    rows = [json.dumps(line)] + [json.dumps({"t_ns": 6 + i, "kind": "bar"}) for i in range(filler)]
    (run / "plugins" / pid / "plugin_log.jsonl").write_text("\n".join(rows) + "\n")


def current_text(console: Console, name: str = "runner") -> str:
    return str(store(console).get("runner", name)["text"])


# ---------------------------------------------------------------- disabled reasons


def _settings(home: Path, **over: Any) -> Any:
    base: dict[str, Any] = {
        "runner_controller": "ssm",
        "runner_instance_id": INSTANCE,
        "plugin_orders": True,
        "plugin_orders_param": CONTROL,
        "topstep_verified_on": "2026-10-13",
    }
    base.update(over)
    return make_settings(home, **base)


def _ready(aws: Aws) -> SsmSecretStore:
    aws.ssm.put_parameter(Name=CONTROL, Value="enabled", Type="String", Overwrite=True)
    for name, value in (
        ("PRACTICE_ACCOUNT_ID", PRACTICE_ID),
        ("COMBINE_ACCOUNT_ID", COMBINE_ID),
        ("PROJECTX_USERNAME", "op-user"),
        ("PROJECTX_API_KEY", API_KEY),
    ):
        aws.ssm.put_parameter(
            Name=f"/fse/secrets/{name}", Value=value, Type="SecureString", Overwrite=True
        )
    return SsmSecretStore(aws.ssm)


def test_no_reason_when_everything_is_in_place(home: Path, aws: Aws) -> None:
    secrets = _ready(aws)
    assert om.disabled_reasons(_settings(home), secrets, aws.ssm, runner_mode="run") == []


def test_each_global_reason_in_order(home: Path, aws: Aws) -> None:
    secrets = _ready(aws)

    def reasons(**over: Any) -> list[str]:
        mode = over.pop("runner_mode", "run")
        kw = {k: over.pop(k) for k in ("non_paper", "plugin_id") if k in over}
        return om.disabled_reasons(
            _settings(home, **over), secrets, aws.ssm, runner_mode=mode, **kw
        )

    assert reasons(plugin_orders=False) == [om.REASON_SWITCH_OFF]
    assert reasons(topstep_verified_on=None) == [om.REASON_TERMS]
    assert reasons(runner_mode="paper") == [om.REASON_PAPER_MODE]
    assert reasons(non_paper=["beta"], plugin_id="alpha") == [
        "another plugin (beta) already uses a non-paper order mode"
    ]
    assert reasons(non_paper=["alpha"], plugin_id="alpha") == []  # itself is not another
    aws.ssm.put_parameter(Name=CONTROL, Value="disabled", Type="String", Overwrite=True)
    assert reasons() == [om.REASON_RUNNER_SWITCH]
    aws.ssm.delete_parameter(Name=CONTROL)
    assert reasons() == ["cannot read control/plugin_orders: ParameterNotFound"]
    aws.ssm.put_parameter(Name=CONTROL, Value="enabled", Type="String", Overwrite=True)
    aws.ssm.put_parameter(
        Name="/fse/secrets/COMBINE_ACCOUNT_ID",
        Value=PRACTICE_ID,
        Type="SecureString",
        Overwrite=True,
    )
    assert reasons() == [om.REASON_SAME_ACCOUNT]
    aws.ssm.delete_parameter(Name="/fse/secrets/COMBINE_ACCOUNT_ID")
    assert reasons() == []  # same() is None: reason 5 is skipped
    aws.ssm.delete_parameter(Name="/fse/secrets/PROJECTX_API_KEY")
    assert reasons() == [om.REASON_PROJECTX]
    aws.ssm.put_parameter(
        Name="/fse/secrets/PRACTICE_ACCOUNT_ID",
        Value="PLACEHOLDER-x",
        Type="SecureString",
        Overwrite=True,
    )
    assert reasons(plugin_orders=False, topstep_verified_on=None, runner_mode="paper") == [
        om.REASON_SWITCH_OFF,
        om.REASON_TERMS,
        om.REASON_PRACTICE_ID,
        om.REASON_PROJECTX,
        om.REASON_PAPER_MODE,
    ]


def test_a_local_or_process_controller_has_one_reason(home: Path, tmp_path: Path) -> None:
    secrets = LocalSecretStore(tmp_path / "s.json")
    s = make_settings(home, plugin_orders=True, topstep_verified_on="2026-10-13")
    assert om.disabled_reasons(s, secrets, None, runner_mode="run") == [om.REASON_LOCAL]


# ---------------------------------------------------------------- practice_blocked


def _broker(text: str, pid: str, tmp_path: Path) -> om.BrokerPlugin:
    found = {p.id: p for p in om.broker_plugins(text, PROJECT, tmp_path)}
    return found[pid]


def test_broker_plugins_key_on_needs_broker_orders(tmp_path: Path) -> None:
    text = runner_text(plugin("beta"), plugin("plain", "BrokerTemplate", enabled=False))
    data = yaml.safe_load(text)
    data["plugins"].append({**plugin("tpl"), "module": "fse.live.plugins.template:TemplatePlugin"})
    found = om.broker_plugins(yaml.safe_dump(data), PROJECT, tmp_path)
    assert [p.id for p in found] == ["alpha", "beta"]  # not disabled, not without broker_orders
    alpha = found[0]
    assert (alpha.order_mode, alpha.has_risk, alpha.brokerage) == ("paper", True, "host")
    assert alpha.instruments == frozenset({"MNQ"}) and alpha.hold_through_break is True


def test_practice_blocked_cases(tmp_path: Path) -> None:
    ok = runner_text(plugin("beta"))
    assert om.practice_blocked(ok, _broker(ok, "alpha", tmp_path)) == []
    no_risk = plugin("beta")
    del no_risk["risk"]
    text = runner_text(no_risk)
    assert om.practice_blocked(text, _broker(text, "beta", tmp_path)) == [
        "beta has no risk block (max_contracts); add one on the Configs page"
    ]
    text = runner_text(plugin("selfb", "SelfBrokerTemplate"))
    [problem] = om.practice_blocked(text, _broker(text, "selfb", tmp_path))
    assert "is self-brokered" in problem and problem.startswith("plugins[5].execution")
    text = runner_text(plugin("gold", "GoldBrokerTemplate"))
    [problem] = om.practice_blocked(text, _broker(text, "gold", tmp_path))
    assert "MGC" in problem and "live.ignored_instruments" in problem
    data = yaml.safe_load(RUNNER)
    data["accounts"] = [{"id": "a", "preset": "none", "nexus": {"enabled": True}}]
    data["plugins"][0]["account"] = "a"
    text = yaml.safe_dump(data, sort_keys=False)
    assert om.practice_blocked(text, _broker(text, "alpha", tmp_path)) == []
    text = runner_text(plugin("beta"))
    assert om.practice_blocked(text, _broker(text, "beta", tmp_path), non_paper=["alpha"]) == [
        "another plugin (alpha) already uses a non-paper order mode"
    ]


def test_combine_blocked_needs_flat_deadline_account(tmp_path: Path) -> None:
    assert om.combine_blocked(RUNNER, _broker(RUNNER, "alpha", tmp_path)) == [
        "plugins[0].account: combine needs a paper account with the flat deadline on"
    ]
    ready = with_combine_account(RUNNER)
    assert om.combine_blocked(ready, _broker(ready, "alpha", tmp_path)) == []
    # preset none turns the flat deadline off
    data = yaml.safe_load(RUNNER)
    data["accounts"] = [{"id": "open", "preset": "none"}]
    data["plugins"][0]["account"] = "open"
    text = yaml.safe_dump(data, sort_keys=False)
    assert om.combine_blocked(text, _broker(text, "alpha", tmp_path)) == [
        "plugins[0].account: combine needs a paper account with the flat deadline on"
    ]


# ---------------------------------------------------------------- the YAML edit


def test_block_edit_keeps_comments_and_quotes_money() -> None:
    new = om.apply_order_mode(RUNNER, "alpha", "practice", ("375.50", 6))
    assert new.count("\n") == RUNNER.count("\n") + 4  # 2 risk keys + execution block
    for line in RUNNER.splitlines():
        if line.lstrip().startswith("#"):
            assert line in new  # every comment kept
    assert '      max_daily_loss: "375.50"\n      max_trades_per_day: 6\n' in new
    assert "    hold_through_break: false\n    execution:\n      order_mode: practice\n" in new
    cfg = parse_runner_config(yaml.safe_load(new), Path("runner.yaml"))
    alpha = cfg.plugins[0]
    assert alpha.execution.order_mode == "practice" and alpha.hold_through_break is False
    assert alpha.risk is not None and alpha.risk.max_daily_loss == Decimal("375.50")
    assert alpha.risk.max_trades_per_day == 6
    # every other plugin untouched
    assert yaml.safe_load(new)["plugins"][1:] == yaml.safe_load(RUNNER)["plugins"][1:]
    back = om.apply_order_mode(new, "alpha", "paper")
    assert back == new.replace("\n      order_mode: practice\n", "\n      order_mode: paper\n")
    again = om.apply_order_mode(new, "alpha", "practice", ("200", 3))
    assert '"200"' in again and "max_trades_per_day: 3" in again
    assert again.count("\n") == new.count("\n")  # replaced in place


def test_flow_risk_is_regenerated_as_flow() -> None:
    new = om.apply_order_mode(RUNNER, "bravo", "practice", ("375", 5))
    assert (
        '    risk: {max_contracts: 3, max_open_trades: 1, max_daily_loss: "375", '
        "max_trades_per_day: 5}\n" in new
    )


def test_existing_execution_and_flow_execution_are_edited() -> None:
    text = runner_text(plugin("beta", execution={"order_mode": "paper"}))
    new = om.apply_order_mode(text, "beta", "practice", ("5", 1))
    assert yaml.safe_load(new)["plugins"][-1]["execution"] == {"order_mode": "practice"}
    flow = RUNNER.replace(
        "    hold_through_break: true\n", "    hold_through_break: true\n    execution: {}\n", 1
    )
    new = om.apply_order_mode(flow, "alpha", "practice", ("5", 1))
    assert "    execution: {order_mode: practice}\n" in new


def test_no_risk_is_refused_with_no_edit() -> None:
    entry = plugin("beta")
    del entry["risk"]
    text = runner_text(entry)
    with pytest.raises(om.OrderModeError, match="beta has no risk block"):
        om.apply_order_mode(text, "beta", "practice", ("5", 1))
    assert om.apply_order_mode(text, "beta", "paper") == text  # missing execution is paper


def test_an_edit_that_does_not_round_trip_is_refused() -> None:
    anchored = RUNNER.replace(
        "    risk:\n      max_contracts: 16", "    risk: &caps\n      max_contracts: 16", 1
    )
    anchored = anchored.replace(
        "    risk: {max_contracts: 3, max_open_trades: 1}", "    risk: *caps", 1
    )
    assert yaml.safe_load(anchored)["plugins"][3]["risk"]["max_contracts"] == 16
    with pytest.raises(om.OrderModeError, match=re.escape(om.CANNOT_EDIT)):
        om.apply_order_mode(anchored, "alpha", "practice", ("5", 1))
    with pytest.raises(om.OrderModeError, match=re.escape(om.CANNOT_EDIT)):
        om.apply_order_mode("plugins: [{id: alpha, execution: practice}]\n", "alpha", "paper")


# ---------------------------------------------------------------- the routes


def test_post_order_mode_is_not_shadowed_by_runner_action(console: Console) -> None:
    routes = {
        (r.path, tuple(sorted(r.methods))): r.endpoint
        for r in api_module.router.routes
        if hasattr(r, "methods")
    }
    assert routes[("/api/order-mode", ("POST",))] is api_module.post_order_mode
    assert routes[("/api/order-modes", ("GET",))] is api_module.get_order_modes
    r = console.post(
        "/api/order-mode",
        json={
            "plugin_id": "alpha",
            "order_mode": "combine",
            "expected_sha": sha(console),
            "max_daily_loss": "375",
            "max_trades_per_day": 5,
        },
    )
    assert r.status_code == 422 and om.REASON_LOCAL in r.json()["detail"]
    # what the /runner/ prefix would have hit
    assert console.post("/api/runner/order-mode").json()["detail"] == "unknown runner action"


def test_get_on_a_local_console(console: Console) -> None:
    body = console.get("/api/order-modes").json()
    assert body["disabled_reasons"] == [om.REASON_LOCAL]
    assert body["combine_disabled_reasons"] == [om.REASON_LOCAL]
    assert body["runner_config"] == "runner" and re.fullmatch(r"[0-9a-f]{12}", body["sha"])
    assert body["runner_state"] == "stopped" and body["switch_pending"] is False
    [alpha] = body["plugins"]
    assert alpha == {
        "id": "alpha",
        "label": "Alpha",
        "order_mode": "paper",
        "max_daily_loss": None,
        "max_trades_per_day": None,
        "hold_through_break": True,
        "practice_blocked": [],
        "combine_blocked": [
            "plugins[0].account: combine needs a paper account with the flat deadline on"
        ],
        "effective": None,
        "busy": False,
    }
    r = post(console)
    assert r.status_code == 422 and r.json()["detail"] == om.REASON_LOCAL


def test_request_rules(orders: Callable[..., Console]) -> None:
    c = orders()
    fake(c)
    before = sha(c)
    # Default runner has no Combine account: dry-run names the flat-deadline need.
    detail = post(c, order_mode="combine").json()["detail"]
    assert "combine needs a paper account with the flat deadline on" in detail
    assert post(c, order_mode="live").status_code == 422
    assert post(c, plugin_id="Bad-Id").status_code == 422
    for loss in ("0", "-1", "1.234", "abc", "1e3"):
        r = post(c, max_daily_loss=loss)
        assert r.status_code == 422 and "max_daily_loss" in r.json()["detail"], loss
    for trades in (0, 501):
        r = post(c, max_trades_per_day=trades)
        assert r.status_code == 422 and "max_trades_per_day" in r.json()["detail"]
    r = c.post(
        "/api/order-mode",
        json={"plugin_id": "alpha", "order_mode": "practice", "expected_sha": sha(c)},
    )
    assert r.status_code == 422 and "max_daily_loss" in r.json()["detail"]
    r = post(c, plugin_id="template")  # disabled, and no broker_orders
    assert r.status_code == 422 and "not an enabled broker plugin" in r.json()["detail"]
    assert post(c, expected_sha="ABC").status_code == 422
    r = post(c, expected_sha="0" * 12)
    assert r.status_code == 409 and r.json()["detail"] == "the runner config changed; reload"
    assert sha(c) == before  # nothing saved by any of these


def test_practice_while_stopped_saves_only(orders: Callable[..., Console]) -> None:
    c = orders()
    report(c, "stopped")
    before = sha(c)
    r = post(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] != before and body["saved"] == sha(c)
    assert body["message"] == f"saved revision {body['saved']}; applies on the next start"
    assert body["restarted"] is False and body["note"] is None
    assert body["runner_state"] == "stopped"
    [alpha] = body["plugins"]
    assert alpha["order_mode"] == "practice" and alpha["max_daily_loss"] == "375.50"
    assert alpha["max_trades_per_day"] == 6 and alpha["hold_through_break"] is False
    text = current_text(c)
    assert "      order_mode: practice" in text and '"375.50"' in text
    assert "# the largest Alpha qty" in text  # comments kept
    meta = store(c).get("runner", "runner")["revisions"][0]
    assert meta["message"] == "order_mode practice for alpha (console)"
    assert meta["author"] == "operator@localhost"
    assert c.app.state.aws.ssm.list_commands()["Commands"] == []  # no restart
    audit = next(a for a in c.get("/api/audit").json() if a["action"] == "POST /api/order-mode")
    assert audit["detail"]["sha"] == body["saved"] and audit["detail"]["plugin"] == "alpha"
    # back to paper: allowed whatever the reasons, keeps hold_through_break false
    r = post(c, order_mode="paper")
    assert r.status_code == 200 and r.json()["note"] == om.PAPER_NOTE
    assert "      order_mode: paper" in current_text(c)
    assert "    hold_through_break: false" in current_text(c)


def test_paper_is_allowed_with_reasons(orders: Callable[..., Console]) -> None:
    c = orders(switch="disabled", plugin_orders=False, secrets=False)
    use(c, "pr", practice_text(RUNNER), practice=True)
    fake(c)
    body = c.get("/api/order-modes").json()
    assert om.REASON_SWITCH_OFF in body["disabled_reasons"]
    r = post(c, order_mode="paper")
    assert r.status_code == 200, r.text
    assert "order_mode: paper" in current_text(c, "pr")
    r = post(c)
    assert r.status_code == 422
    assert r.json()["detail"].startswith(om.REASON_SWITCH_OFF + "; ")


def test_running_needs_confirm_then_saves_and_restarts(orders: Callable[..., Console]) -> None:
    c = orders()
    report(c, "running")
    before = sha(c)
    r = post(c)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "confirm_required", "message": api_module.CONFIRM_RESTART}
    assert sha(c) == before
    r = post(c, confirm_restart=True)
    assert r.status_code == 200, r.text
    assert r.json()["restarted"] is True and r.json()["runner_state"] == "starting"
    [cmd] = c.app.state.aws.ssm.list_commands()["Commands"]
    assert cmd["Parameters"] == {"action": ["restart"]}


def test_save_then_restart_called_once(orders: Callable[..., Console]) -> None:
    c = orders()
    runner = fake(c, "running")
    r = post(c, confirm_restart=True)
    assert r.status_code == 200 and runner.calls == ["restart"]


def test_restart_failure_is_502_with_the_saved_sha(
    orders: Callable[..., Console], caplog: pytest.LogCaptureFixture
) -> None:
    c = orders()
    runner = fake(c, "running")
    runner.fail = "cannot send restart to the runner host: ThrottlingException"
    with caplog.at_level(logging.WARNING, logger="fse_ui.api"):
        r = post(c, confirm_restart=True)
    assert r.status_code == 502
    m = re.fullmatch(r"saved revision ([0-9a-f]{12}); restart failed: (.*)", r.json()["detail"])
    assert m is not None and m.group(1) == sha(c) and m.group(2) == runner.fail
    assert any("restart failed" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize(
    ("state", "error", "detail"),
    [
        (
            "unknown",
            "the host has not reported the runner state yet",
            "the runner state is unknown",
        ),
        ("starting", None, "the runner is changing state; retry"),
        ("stopping", None, "the runner is changing state; retry"),
    ],
)
def test_unsettled_runner_states_are_409(
    orders: Callable[..., Console], state: str, error: str | None, detail: str
) -> None:
    c = orders()
    runner = fake(c, state)
    runner.error = error
    before = sha(c)
    r = post(c, confirm_restart=True)
    assert r.status_code == 409 and r.json()["detail"].startswith(detail)
    assert sha(c) == before and runner.calls == []


def test_unknown_from_the_real_controller(orders: Callable[..., Console]) -> None:
    c = orders()  # no runner-state.json written
    r = post(c, confirm_restart=True)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "the runner state is unknown (the host has not reported the runner state yet); "
        "retry when the Live page shows it"
    )


@pytest.mark.parametrize(
    ("state", "held"), [("stopped", True), ("failed", False), ("failed", True)]
)
def test_stopped_or_failed_saves_only(
    orders: Callable[..., Console], state: str, held: bool
) -> None:
    c = orders()
    runner = fake(c, state, held=held)
    r = post(c)
    assert r.status_code == 200 and r.json()["message"].endswith("applies on the next start")
    assert runner.calls == [] and r.json()["held"] is held


def test_busy_rules(orders: Callable[..., Console]) -> None:
    c = orders()
    use(c, "duo", runner_text(plugin("beta")))
    runner = fake(c, "running")
    # (a) the target plugin holds a position
    busy(c, "alpha")
    r = post(c, confirm_restart=True)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("alpha have an open position or working order (as of ")
    assert r.json()["detail"].endswith("); flatten or wait, then retry")
    # a paper-only plugin restores from state: it does not block
    r = post(c, plugin_id="beta", confirm_restart=True)
    assert r.status_code == 200, r.text
    # (b) beta is practice in the config and holds a working order: alpha -> paper blocked
    (c.app.state.settings.live_state_dir / "plugins" / "alpha" / "status.json").unlink()
    busy(c, "beta", positions=0, working=1)
    r = post(c, order_mode="paper", confirm_restart=True)
    assert r.status_code == 409 and "beta have an open position" in r.json()["detail"]
    # B -> practice while A is in practice: the one-plugin rule answers first
    r = post(c, confirm_restart=True)
    assert r.status_code == 422
    assert "another plugin (beta) already uses a non-paper order mode" in r.json()["detail"]
    assert runner.calls == ["restart"]


def test_busy_scope_includes_the_running_order_mode(orders: Callable[..., Console]) -> None:
    c = orders()
    use(c, "duo", runner_text(plugin("beta")))
    fake(c, "stopped")
    order_line(c, "alpha", "practice")  # the runner still trades alpha in practice
    busy(c, "alpha")
    r = post(c, plugin_id="beta", order_mode="paper")
    assert r.status_code == 409 and r.json()["detail"].startswith("alpha have an open position")


@pytest.mark.parametrize("action", ["start", "restart"])
def test_runner_start_and_restart_refuse_a_busy_practice_plugin(
    orders: Callable[..., Console], action: str
) -> None:
    c = orders()
    runner = fake(c, "running")
    # paper-only and busy: restores from state, does not block
    busy(c, "alpha")
    r = c.post(f"/api/runner/{action}")
    assert r.status_code == 200, r.text
    assert runner.calls == [action]
    # (c) the runner still trades alpha in practice (switch went off, config is paper)
    order_line(c, "alpha", "practice")
    r = c.post(f"/api/runner/{action}")
    assert r.status_code == 409
    assert r.json()["detail"].startswith("alpha have an open position or working order (as of ")
    assert r.json()["detail"].endswith("); flatten or wait, then retry")
    # (b) the selected config has alpha in practice, the last run was paper
    use(c, "mine", practice_text(RUNNER), practice=True)
    order_line(c, "alpha", "paper")
    r = c.post(f"/api/runner/{action}")
    assert r.status_code == 409 and "alpha have an open position" in r.json()["detail"]
    # flattened: no position or working order visible
    (c.app.state.settings.live_state_dir / "plugins" / "alpha" / "status.json").unlink()
    r = c.post(f"/api/runner/{action}")
    assert r.status_code == 200, r.text
    assert runner.calls == [action, action]


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("state", ["stopped", "failed"])
def test_same_mode_practice_start_passes_a_stale_busy_status(
    orders: Callable[..., Console], action: str, state: str
) -> None:
    """A stopped or failed runner left status.json busy; Alpha restores from state."""
    c = orders()
    runner = fake(c, state)
    use(c, "mine", practice_text(RUNNER), practice=True)
    order_line(c, "alpha", "practice")
    busy(c, "alpha")
    r = c.post(f"/api/runner/{action}")
    assert r.status_code == 200, r.text
    assert runner.calls == [action]


@pytest.mark.parametrize(
    ("switch", "effective"),
    [
        ("disabled", "practice"),  # the switch went off: practice -> paper
        ("enabled", "paper"),  # the config went practice while paper held a position
        (None, "practice"),  # the switch cannot be read: it may go either way
    ],
)
@pytest.mark.parametrize("action", ["start", "restart"])
def test_a_mode_change_on_a_busy_plugin_is_refused(
    orders: Callable[..., Console], aws: Aws, switch: str | None, effective: str, action: str
) -> None:
    c = orders()
    runner = fake(c, "stopped")
    use(c, "mine", practice_text(RUNNER), practice=True)
    if switch is None:
        aws.ssm.delete_parameter(Name=CONTROL)
    else:
        aws.ssm.put_parameter(Name=CONTROL, Value=switch, Type="String", Overwrite=True)
    order_line(c, "alpha", effective)
    busy(c, "alpha", positions=0, working=1)
    r = c.post(f"/api/runner/{action}")
    assert r.status_code == 409
    assert r.json()["detail"].startswith("alpha have an open position or working order")
    assert runner.calls == []


def test_switch_pending(orders: Callable[..., Console], aws: Aws) -> None:
    c = orders(switch="disabled")
    fake(c)
    assert c.get("/api/order-modes").json()["switch_pending"] is False
    order_line(c, "alpha", "paper")
    assert c.get("/api/order-modes").json()["switch_pending"] is False
    order_line(c, "alpha", "practice")
    body = c.get("/api/order-modes").json()
    assert body["switch_pending"] is True
    assert body["plugins"][0]["effective"] == {
        "requested": "practice",
        "mode": "practice",
        "failures": [],
        "t_ns": 5,
    }
    assert om.REASON_RUNNER_SWITCH in body["disabled_reasons"]
    aws.ssm.put_parameter(Name=CONTROL, Value="enabled", Type="String", Overwrite=True)
    assert c.get("/api/order-modes").json()["switch_pending"] is False


def test_create_and_select_cannot_bring_in_a_non_paper_mode(orders: Callable[..., Console]) -> None:
    c = orders()
    practice = practice_text(RUNNER)
    combine = combine_text(RUNNER)
    for text in (practice, combine):
        r = c.post("/api/configs/runner/new_one", json={"text": text, "create": True})
        assert r.status_code == 422
        assert [e["message"] for e in r.json()["validation"]["errors"]] == [ORDER_MODE_ELSEWHERE]
    st = store(c)
    st.save("runner", "other", RUNNER, author="t", create=True)
    st.save("runner", "other", practice, author="t", allow_order_mode=True)
    r = c.put(
        "/api/runner/settings",
        json={"mode": "run", "runner_config": "other", "strategy_config": "playbook_baseline"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "other has a non-paper order_mode; select it while it is paper, then use Live → Order mode"
    )
    # the selected config may be re-saved with its settings (mode change) as is
    use(c, "mine", practice, practice=True)
    r = c.put(
        "/api/runner/settings",
        json={"mode": "run", "runner_config": "mine", "strategy_config": "playbook_baseline"},
    )
    assert r.status_code == 200


def test_combine_saves_from_order_mode_when_account_and_caps_are_set(
    orders: Callable[..., Console],
) -> None:
    c = orders()
    fake(c)
    report(c, "stopped")
    use(c, "with_acct", with_combine_account(RUNNER))
    r = post(c, order_mode="combine")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restarted"] is False
    [alpha] = body["plugins"]
    assert alpha["order_mode"] == "combine" and alpha["max_daily_loss"] == "375.50"
    assert alpha["max_trades_per_day"] == 6 and alpha["hold_through_break"] is False
    saved = yaml.safe_load(current_text(c, "with_acct"))
    assert saved["plugins"][0]["execution"]["order_mode"] == "combine"
    assert saved["plugins"][0]["hold_through_break"] is False


def test_combine_is_allowed_by_the_store_from_order_mode(tmp_path: Path) -> None:
    st = ConfigStore(tmp_path)
    st.seed(PROJECT)
    combine = combine_text(RUNNER)
    result, saved = st.save("runner", "runner", combine, author="t", allow_order_mode=True)
    assert saved is not None and result.ok
    current = st.get("runner", "runner")["sha"]
    with pytest.raises(ConfigConflict, match="the runner config changed; reload"):
        st.save("runner", "runner", RUNNER + "# x\n", author="t", expected_sha="0" * 12)
    result, saved = st.save("runner", "runner", RUNNER + "# x\n", author="t", expected_sha=current)
    assert saved is None  # combine → paper without allow_order_mode
    assert result.errors[0].message == ORDER_MODE_ELSEWHERE


def test_a_concurrent_configs_save_makes_the_post_409(orders: Callable[..., Console]) -> None:
    c = orders()
    fake(c)
    stale = sha(c)
    r = c.post("/api/configs/runner/runner", json={"text": RUNNER + "# edited\n"})
    assert r.status_code == 200
    r = post(c, expected_sha=stale)
    assert r.status_code == 409 and r.json()["detail"] == "the runner config changed; reload"


def test_strategy_combine_cannot_start(console: Console) -> None:
    text = console.get("/api/configs/strategy/playbook_baseline").json()["text"]
    text = text.replace("order_mode: paper", "order_mode: combine")
    assert "order_mode: combine" in text
    r = console.post("/api/configs/strategy/combine_cfg", json={"text": text, "create": True})
    assert r.status_code == 200, r.text
    r = console.put(
        "/api/runner/settings",
        json={"mode": "paper", "runner_config": "runner", "strategy_config": "combine_cfg"},
    )
    assert r.status_code == 200
    r = console.post("/api/runner/start")
    assert r.status_code == 409 and "only paper runs are allowed" in r.json()["detail"]


def test_the_lock_serializes_a_concurrent_restart(
    orders: Callable[..., Console], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_module, "order_mode_lock", asyncio.Lock())
    c = orders()
    runner = fake(c, "running")
    runner.gate = threading.Event()
    results: dict[str, int] = {}
    body = {
        "plugin_id": "alpha",
        **PRACTICE,
        "expected_sha": sha(c),
        "confirm_restart": True,
    }

    def order_mode() -> None:
        results["order"] = c.post("/api/order-mode", json=body).status_code

    def restart() -> None:
        results["restart"] = c.post("/api/runner/restart").status_code

    first = threading.Thread(target=order_mode)
    first.start()
    deadline = time.monotonic() + 10
    while runner.calls != ["restart"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    second = threading.Thread(target=restart)
    second.start()
    time.sleep(0.3)
    assert runner.calls == ["restart"]  # the runner restart waits for the order-mode lock
    runner.gate.set()
    first.join(10)
    second.join(10)
    assert results == {"order": 200, "restart": 200}
    assert runner.calls == ["restart", "restart"]


def test_no_account_value_in_any_body_or_log(
    orders: Callable[..., Console], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    c = orders()
    report(c, "running")
    order_line(
        c,
        "alpha",
        "paper",
        failures=("PRACTICE_ACCOUNT_ID matches no account the Broker_Adapter resolved",),
        filler=80,
    )
    bodies = [c.get("/api/order-modes").text, c.get("/api/session").text]
    bodies.append(post(c).text)
    bodies.append(post(c, confirm_restart=True).text)
    bodies.append(c.get("/api/order-modes").text)
    bodies.append(c.get("/api/live").text)
    bodies.append(c.get("/api/secrets").text)
    bodies.append(c.get("/api/audit").text)
    live = json.loads(bodies[-3])
    alpha = next(p for p in live["plugins"] if p["id"] == "alpha")
    assert alpha["order_mode"]["failures"] == [
        "PRACTICE_ACCOUNT_ID matches no account the Broker_Adapter resolved"
    ]
    # the GET redacts a known value even if a failure ever carried one
    order_line(c, "alpha", "paper", failures=(f"account {PRACTICE_ID} refused",))
    got = c.get("/api/order-modes").json()["plugins"][0]["effective"]["failures"]
    assert got == ["account [redacted] refused"]
    # and so does /api/live (the WebSocket sends the same snapshot)
    bodies.append(c.get("/api/live").text)
    live = json.loads(bodies[-1])
    alpha = next(p for p in live["plugins"] if p["id"] == "alpha")
    assert alpha["order_mode"]["failures"] == ["account [redacted] refused"]
    for text in bodies:
        for canary in CANARIES:
            assert canary not in text
    for rec in caplog.records:
        for canary in CANARIES:
            assert canary not in rec.getMessage()
