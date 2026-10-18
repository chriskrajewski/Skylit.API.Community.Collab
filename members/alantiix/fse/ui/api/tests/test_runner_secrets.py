from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from fse_ui.runner import live_snapshot, plugin_raw
from fse_ui.secrets_store import SECRET_NAMES, LocalSecretStore, SsmSecretStore
from tests.conftest import PROJECT, Console

REAL_FSE = str(Path(sys.executable).parent / "fse")


@pytest.fixture
def real(console_factory: Callable[..., Console]) -> Console:
    return console_factory(fse_bin=REAL_FSE)


# ---------------------------------------------------------------- halt and clear


def test_halt_and_clear_one_plugin_with_the_real_cli(real: Console) -> None:
    live_state = real.app.state.settings.live_state_dir
    r = real.post("/api/halt", json={"plugin_id": "alpha", "reason": "pause before CPI"})
    assert r.status_code == 200, r.text
    blocks = json.loads((live_state / "plugins" / "alpha" / "blocks.json").read_text())
    assert blocks["blocks"][0]["kind"] == "halt_command"
    assert blocks["blocks"][0]["detail"] == "pause before CPI"
    snap = real.get("/api/live").json()
    assert snap["blocks"]["plugins"]["alpha"]["blocks"][0]["detail"] == "pause before CPI"
    assert not snap["blocks"]["halted"]  # only the plugin is blocked
    assert real.post("/api/clear", json={"plugin_id": "alpha"}).status_code == 200
    assert real.get("/api/live").json()["blocks"]["plugins"]["alpha"]["blocks"] == []
    actions = [a["action"] for a in real.get("/api/audit").json()]
    assert actions[:2] == ["POST /api/clear", "POST /api/halt"]


def test_halt_all(real: Console) -> None:
    assert real.post("/api/halt", json={"reason": "stop everything"}).status_code == 200
    assert real.get("/api/live").json()["blocks"]["halted"]


def test_bad_plugin_id_and_reason(real: Console) -> None:
    assert real.post("/api/halt", json={"plugin_id": "../x"}).status_code == 422
    assert real.post("/api/halt", json={"reason": "a\nb"}).status_code == 422


def test_reset_day_loss_writes_request_with_the_real_cli(real: Console) -> None:
    live_state = real.app.state.settings.live_state_dir
    r = real.post(
        "/api/plugins/alpha/reset-day-loss",
        json={"reason": "clear today budget"},
    )
    assert r.status_code == 200, r.text
    req = live_state / "plugins" / "alpha" / "day_net_reset.json"
    assert req.is_file()
    assert json.loads(req.read_text())["reason"] == "clear today budget"
    assert real.post("/api/plugins/Bad-Id/reset-day-loss", json={}).status_code == 422
    assert (
        real.post("/api/plugins/alpha/reset-day-loss", json={"reason": "a\nb"}).status_code
        == 422
    )


# ---------------------------------------------------------------- runner control


def test_live_mode_is_refused_server_side(console: Console) -> None:
    r = console.put(
        "/api/runner/settings",
        json={"mode": "live", "runner_config": "runner", "strategy_config": "playbook_baseline"},
    )
    assert r.status_code == 422 and "fse live is disabled" in r.json()["detail"]


def test_paper_with_a_practice_config_is_refused(console: Console) -> None:
    cfg = console.get("/api/configs/strategy/playbook_baseline").json()
    text = cfg["text"].replace("order_mode: paper", "order_mode: practice")
    assert "order_mode: practice" in text
    assert (
        console.post(
            "/api/configs/strategy/practice_cfg", json={"text": text, "create": True}
        ).status_code
        == 200
    )
    assert (
        console.put(
            "/api/runner/settings",
            json={
                "mode": "paper",
                "runner_config": "runner",
                "strategy_config": "practice_cfg",
            },
        ).status_code
        == 200
    )
    r = console.post("/api/runner/start")
    assert r.status_code == 409 and "only paper runs are allowed" in r.json()["detail"]


def test_start_stop_restart_are_audited(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_SLEEP", "30")
    r = console.post("/api/runner/start")
    assert r.status_code == 200 and r.json()["state"] == "running"
    time.sleep(0.3)
    assert console.post("/api/runner/start").status_code == 409  # one runner only
    r = console.post("/api/runner/restart")
    assert r.status_code == 200 and r.json()["state"] == "running"
    time.sleep(0.3)
    r = console.post("/api/runner/stop")
    assert r.json()["state"] == "stopped" and r.json()["last_exit_code"] == 130
    actions = [
        a for a in console.get("/api/audit").json() if a["action"].startswith("POST /api/runner/")
    ]
    assert [a["detail"]["action"] for a in actions][:3] == ["stop", "restart", "start"]
    assert actions[0]["detail"]["settings"]["mode"] == "run"


# ---------------------------------------------------------------- monitoring


def test_live_snapshot_from_a_replayed_host_log(tmp_path: Path) -> None:
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    run = runs / "run-20261009T220000Z-0a1b2c3d"
    (run / "plugins" / "alpha").mkdir(parents=True)
    (state / "plugins" / "alpha").mkdir(parents=True)
    lines = [
        {"t_ns": 1, "kind": "plugin_loaded", "level": "info", "plugin": "alpha"},
        {
            "t_ns": 2,
            "kind": "heartbeat",
            "level": "info",
            "transport": "stream",
            "connected": True,
            "lag": {"MNQ": {"p50": 1.2}},
            "rt_dropped": 0,
        },
        {"t_ns": 3, "kind": "plugin_faulted", "level": "error", "plugin": "bravo"},
        {
            "t_ns": 4,
            "kind": "heartbeat",
            "level": "info",
            "transport": "polling",
            "connected": False,
            "lag": {},
            "rt_dropped": 2,
        },
    ]
    host = run / "host_log.jsonl"
    host.write_text("".join(json.dumps(x) + "\n" for x in lines[:2]))
    (state / "plugins" / "alpha" / "plugin.json").write_text(
        json.dumps({"version": 1, "seq": 9, "book": {"positions": {}}, "plugin": {}})
    )
    status = {"version": 1, "plugin": "alpha", "phase": "waiting", "positions": []}
    (state / "plugins" / "alpha" / "status.json").write_text(json.dumps(status))
    first = live_snapshot(runs, state)
    assert first["heartbeat"]["transport"] == "stream"
    p0 = first["plugins"][0]
    assert p0["status"] == status and p0["has_raw"] and not p0["faulted"]
    assert "book" not in p0  # the raw book is only behind plugin_raw (Details)
    said = [e["say"]["text"] for e in first["host_log"]]
    assert said == [
        "Plugin alpha loaded",
        "Heartbeat: ProjectX connected (stream)",
    ]
    raw = plugin_raw(state, "alpha")
    assert raw is not None and raw["state"]["seq"] == 9 and raw["status"] == status
    assert plugin_raw(state, "../etc") is None
    assert plugin_raw(state, "nope") is None
    with host.open("a") as fh:
        fh.write("".join(json.dumps(x) + "\n" for x in lines[2:]))
    second = live_snapshot(runs, state)
    assert second["heartbeat"]["transport"] == "polling" and not second["heartbeat"]["connected"]
    faulted = {p["id"]: p["faulted"] for p in second["plugins"]}
    assert faulted == {"alpha": False, "bravo": True}


def test_live_snapshot_uses_market_hub_until_heartbeat(tmp_path: Path) -> None:
    """Hub/feed status must light the ProjectX tile before the 5-minute heartbeat."""
    runs, state = tmp_path / "runs", tmp_path / "live-state"
    run = runs / "run-20261015T011318Z-733c9ac6"
    run.mkdir(parents=True)
    state.mkdir(parents=True)
    lines = [
        {"t_ns": 1, "kind": "day_open", "level": "info", "day": "2026-10-15"},
        {"t_ns": 2, "kind": "market_hub_connected", "level": "info"},
        {
            "t_ns": 3,
            "kind": "feed_status",
            "level": "info",
            "feed": "quotes",
            "up": True,
            "detail": "transport stream: connected",
        },
        {
            "t_ns": 4,
            "kind": "broker",
            "level": "info",
            "plugin": "alpha",
            "event": "reconciliation",
            "differences": [],
        },
    ]
    (run / "host_log.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    snap = live_snapshot(runs, state)
    hb = snap["heartbeat"]
    assert hb is not None
    assert hb["connected"] is True
    assert hb["provisional"] is True
    assert hb["transport"] == "stream"


# ---------------------------------------------------------------- secrets


CANARY = "canary-SECRET-value-7f3a"


def test_secret_is_write_only_everywhere(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    r = console.put("/api/secrets/SKYLIT_API_KEY", json={"value": CANARY})
    assert r.status_code == 200 and CANARY not in r.text
    listed = console.get("/api/secrets")
    assert CANARY not in listed.text
    status = {s["name"]: s["status"] for s in listed.json()["secrets"]}
    assert status["SKYLIT_API_KEY"] == "set" and status["PROJECTX_API_KEY"] == "missing"
    assert CANARY not in console.get("/api/audit").text
    # A command that prints the value gets it redacted in its log.
    monkeypatch.setenv("STUB_PRINT_ENV", "SKYLIT_API_KEY")
    job = console.post("/api/jobs", json={"command": "fse scan-secrets", "values": {}}).json()
    from tests.conftest import wait_job

    wait_job(console, job["id"])
    log = console.get(f"/api/jobs/{job['id']}/log").text
    assert CANARY not in log and "[redacted]" in log
    log_file = console.app.state.settings.ui_dir / "jobs" / f"{job['id']}.log"
    assert CANARY not in log_file.read_text()
    secrets_file = console.app.state.settings.ui_dir / "secrets.json"
    assert secrets_file.stat().st_mode & 0o777 == 0o600


def test_secret_validation(console: Console) -> None:
    assert console.put("/api/secrets/NOPE", json={"value": "x"}).status_code == 422
    assert console.put("/api/secrets/SKYLIT_API_KEY", json={"value": " "}).status_code == 422
    assert (
        console.put("/api/secrets/SKYLIT_API_KEY", json={"value": "PLACEHOLDER-x"}).status_code
        == 422
    )


def test_local_store_status(tmp_path: Path) -> None:
    store = LocalSecretStore(tmp_path / "s.json")
    assert set(store.statuses().values()) == {"missing"}
    store.put("PROJECTX_USERNAME", "op")
    assert store.statuses()["PROJECTX_USERNAME"] == "set"
    assert store.child_env() == {"PROJECTX_USERNAME": "op"}


@mock_aws
def test_ssm_store_against_moto() -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    ssm.put_parameter(
        Name="/fse/secrets/SKYLIT_API_KEY", Value="PLACEHOLDER-set-by-hand", Type="SecureString"
    )
    store = SsmSecretStore(ssm)
    assert store.statuses()["SKYLIT_API_KEY"] == "placeholder"
    store.put("SKYLIT_API_KEY", CANARY)
    assert store.statuses()["SKYLIT_API_KEY"] == "set"
    assert store.statuses()["NARRATOR_API_KEY"] == "missing"
    assert set(store.statuses()) == set(SECRET_NAMES)
    stored = ssm.get_parameter(Name="/fse/secrets/SKYLIT_API_KEY", WithDecryption=True)
    assert stored["Parameter"]["Type"] == "SecureString"
    assert store.child_env() == {}


# ---------------------------------------------------------------- plugin orders (FEAT-002)

VARIABLES_TF = PROJECT / "infra" / "modules" / "params" / "variables.tf"


def test_secret_names_equal_the_params_module_default() -> None:
    text = VARIABLES_TF.read_text(encoding="utf-8")
    block = re.search(r'variable "secret_names" \{.*?default\s*=\s*\[(.*?)\]', text, re.S)
    assert block is not None
    assert tuple(re.findall(r'"([A-Z0-9_]+)"', block.group(1))) == SECRET_NAMES


def test_practice_account_id_is_writable(console: Console) -> None:
    r = console.put("/api/secrets/PRACTICE_ACCOUNT_ID", json={"value": "practice-canary-1"})
    assert r.status_code == 200 and r.json() == {"name": "PRACTICE_ACCOUNT_ID", "status": "set"}
    listed = console.get("/api/secrets")
    status = {s["name"]: s["status"] for s in listed.json()["secrets"]}
    assert status["PRACTICE_ACCOUNT_ID"] == "set" and "practice-canary-1" not in listed.text


def test_same_returns_a_bool_or_none_and_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    local = LocalSecretStore(tmp_path / "s.json")
    assert local.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is None
    local.put("PRACTICE_ACCOUNT_ID", "acct-canary-a")
    assert local.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is None
    local.put("COMBINE_ACCOUNT_ID", "acct-canary-b")
    assert local.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is False
    local.put("COMBINE_ACCOUNT_ID", "acct-canary-a")
    assert local.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is True
    with mock_aws():
        ssm = boto3.client("ssm", region_name="us-east-1")
        store = SsmSecretStore(ssm)
        assert store.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is None
        store.put("PRACTICE_ACCOUNT_ID", "acct-canary-a")
        ssm.put_parameter(
            Name="/fse/secrets/COMBINE_ACCOUNT_ID", Value="PLACEHOLDER-x", Type="SecureString"
        )
        assert store.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is None
        store.put("COMBINE_ACCOUNT_ID", "acct-canary-b")
        assert store.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is False
        store.put("COMBINE_ACCOUNT_ID", "acct-canary-a")
        assert store.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is True
    for rec in caplog.records:
        assert "acct-canary" not in rec.getMessage()
