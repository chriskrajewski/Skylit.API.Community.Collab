from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from fse_ui.catalog import (
    LIVE_DISABLED_REASON,
    RUNNER_REASON,
    ArgvError,
    Catalog,
    build_args,
    refuse_order_flags,
)
from fse_ui.jobs import exit_meaning
from tests.conftest import Console, wait_job

SNAPSHOT = Path(__file__).parent / "snapshots" / "catalog.json"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog()


def _shape(catalog: Catalog) -> dict[str, list[str]]:
    return {
        c.id: sorted(f"{a['dest']}:{a['kind']}:{a['flag']}" for a in c.args)
        for c in catalog.commands()
        if c.program == "fse"  # the shipped catalog; the test-only research CLIs are not in it
    }


def test_catalog_snapshot(catalog: Catalog) -> None:
    """Fails when a CLI flag changes: review the console form, then refresh the snapshot
    with ``FSE_UPDATE_SNAPSHOTS=1`` (not FSE_UI_*: conftest clears those)."""
    assert catalog.errors() == {}
    shape = _shape(catalog)
    if os.environ.get("FSE_UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(shape, indent=1, sort_keys=True) + "\n")
    assert shape == json.loads(SNAPSHOT.read_text())


def test_every_fse_command_is_present(catalog: Catalog) -> None:
    ids = {c.id for c in catalog.commands()}
    for name in (
        "backtest",
        "calibrate regime-min-abs",
        "drafts build",
        "drafts promote",
        "experiment ablation",
        "experiment sweep",
        "experiment montecarlo",
        "experiment holdout",
        "experiment walkforward",
        "experiment cadence",
        "export-aws",
        "halt",
        "clear",
        "day-loss-reset",
        "import-vix",
        "live",
        "paper",
        "pull",
        "report",
        "run",
        "scan-secrets",
        "skilldocs check",
    ):
        assert f"fse {name}" in ids
    assert "research.example.check" in ids
    assert any(i.startswith("research.example.run ") for i in ids)


def test_placement_and_live_disabled(catalog: Catalog) -> None:
    assert catalog.get("fse live").disabled_reason == LIVE_DISABLED_REASON
    assert catalog.get("fse halt").placement == "local"
    assert catalog.get("fse pull").placement == "batch"
    assert catalog.get("fse run").placement == "runner"


def test_run_order_flags_are_read_only(catalog: Catalog) -> None:
    """``fse run`` lists the order flags, but the command is never submitted as a job."""
    run = catalog.get("fse run")
    assert run.disabled_reason == RUNNER_REASON
    dests = {a["dest"] for a in run.args}
    assert {"plugin_orders", "live_orders"} <= dests


def test_build_args_quotes_spaces_and_checks_with_the_parser(catalog: Catalog) -> None:
    cmd = catalog.get("fse backtest")
    args = build_args(
        cmd,
        {
            "config": "/tmp/my config.yaml",
            "start": "2026-01-02",
            "end": "2026-02-27",
            "offline": True,
            "seed": 7,
        },
    )
    assert args == [
        "--config=/tmp/my config.yaml",
        "--start=2026-01-02",
        "--end=2026-02-27",
        "--offline",
        "--seed=7",
    ]
    with pytest.raises(ArgvError, match="required"):
        build_args(cmd, {"config": "x.yaml"})
    with pytest.raises(ArgvError, match="unknown argument"):
        build_args(cmd, {"config": "x", "start": "a", "end": "b", "nope": 1})
    with pytest.raises(ArgvError, match="one line"):
        build_args(cmd, {"config": "x\ny", "start": "2026-01-02", "end": "2026-01-03"})


def test_mutually_exclusive_groups_are_enforced(catalog: Catalog) -> None:
    cmd = catalog.get("fse export-aws")
    with pytest.raises(ArgvError, match="not allowed with"):
        build_args(cmd, {"dry_run": True, "stage_only": True})


def test_exit_meanings() -> None:
    assert exit_meaning(1) == "failure"
    assert exit_meaning(1, ["backtest-plugin"]) == "failure"
    assert exit_meaning(1, ["scan-secrets"]) == "secrets found"
    assert exit_meaning(1, ["paper"]) == "the feed stopped"
    assert exit_meaning(3) == "failure"
    assert exit_meaning(3, ["scan-secrets"]) == "failure"
    assert exit_meaning(3, ["pull"]) == "credentials missing or blank"
    assert exit_meaning(3, ["run"]) == "credentials missing or blank"
    assert exit_meaning(3, ["paper"]) == "credentials missing or blank"
    assert exit_meaning(3, ["account"]) == "the runner is already running"
    assert exit_meaning(3, ["backtest-plugin"]) == "needed bars or other inputs are missing"
    assert exit_meaning(5) == "failure"
    assert exit_meaning(5, ["scan-secrets"]) == "the scan could not run"
    assert exit_meaning(2) == "invalid input"
    assert exit_meaning(76) == "the EFS cache is not mounted"
    assert exit_meaning(130) == "interrupted"
    assert exit_meaning(-9) == "killed by signal 9"
    assert exit_meaning(None) is None


def test_job_runs_and_streams(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_LINES", "3")
    r = console.post(
        "/api/jobs", json={"command": "fse report", "values": {"run": "run with space"}}
    )
    assert r.status_code == 201, r.text
    job = wait_job(console, r.json()["id"])
    assert job["status"] == "succeeded" and job["exit_code"] == 0
    lines = console.get(f"/api/jobs/{job['id']}/log").json()["lines"]
    assert "stub argv: ['report', '--run=run with space']" in lines
    assert lines[-1] == "exit 0 (ok)"


def test_job_log_honors_limit_and_tail(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_LINES", "3")
    r = console.post("/api/jobs", json={"command": "fse report", "values": {"run": "r"}})
    job = wait_job(console, r.json()["id"])
    full = console.get(f"/api/jobs/{job['id']}/log").json()["lines"]
    assert len(full) > 2
    assert len(console.get(f"/api/jobs/{job['id']}/log", params={"limit": 1}).json()["lines"]) == 1
    tailed = console.get(f"/api/jobs/{job['id']}/log", params={"tail": 2}).json()["lines"]
    assert tailed == full[-2:]


def test_exit_3_is_a_failed_job_with_its_meaning(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "3")
    r = console.post("/api/jobs", json={"command": "fse scan-secrets", "values": {}})
    job = wait_job(console, r.json()["id"])
    assert job["status"] == "failed"
    assert job["exit_meaning"] == "failure"
    assert "secrets" not in (job["exit_meaning"] or "")


def test_cancel_sends_sigint(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_SLEEP", "30")
    r = console.post("/api/jobs", json={"command": "fse scan-secrets", "values": {}})
    job_id = r.json()["id"]
    # The stub prints its argv only after it installed the SIGINT handler: wait for
    # that line, not a fixed sleep (too short on a loaded CI runner).
    deadline = time.monotonic() + 20
    while not any(
        line.startswith("stub argv:")
        for line in console.get(f"/api/jobs/{job_id}/log").json()["lines"]
    ):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert console.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    job = wait_job(console, job_id)
    assert job["status"] == "cancelled" and job["exit_code"] == 130
    assert "stub: interrupted" in console.get(f"/api/jobs/{job_id}/log").json()["lines"]


def test_runner_commands_and_live_are_refused_as_jobs(console: Console) -> None:
    r = console.post("/api/jobs", json={"command": "fse live", "values": {"config": "x"}})
    assert r.status_code == 409 and "TopstepX" in r.json()["detail"]
    r = console.post("/api/jobs", json={"command": "fse run", "values": {"config": "x"}})
    assert r.status_code == 409


def test_invalid_values_are_422(console: Console) -> None:
    r = console.post("/api/jobs", json={"command": "fse backtest", "values": {"config": "x"}})
    assert r.status_code == 422
    assert console.post("/api/jobs", json={"command": "fse nope"}).status_code == 404


def test_job_websocket_replays_and_ends(console: Console) -> None:
    r = console.post("/api/jobs", json={"command": "fse scan-secrets", "values": {}})
    job = wait_job(console, r.json()["id"])
    cookie = f"fse_session={console.client.cookies.get('fse_session')}"
    with console.client.websocket_connect(
        f"/api/ws/jobs/{job['id']}", headers={"origin": "http://localhost:5173", "cookie": cookie}
    ) as ws:
        first = ws.receive_json()
        assert first["type"] == "status"
        events = [ws.receive_json() for _ in range(3)]
    assert any(e.get("line", "").startswith("exit 0") for e in events)


def test_research_command_runs_with_python_m(console: Console) -> None:
    cmd = console.app.state.catalog.get("research.example.check")
    assert cmd.placement == "batch"
    r = console.post(
        "/api/jobs",
        json={"command": "research.example.check", "values": {"date": "2026-10-09"}},
    )
    assert r.status_code == 201
    assert r.json()["argv"][1:4] == ["-m", "research.example.check", "--date=2026-10-09"]


def test_run_paper_and_live_are_refused_as_jobs(console: Console) -> None:
    for command in ("fse run", "fse paper", "fse live"):
        r = console.post("/api/jobs", json={"command": command, "values": {"config": "x"}})
        assert r.status_code == 409, command


@pytest.mark.parametrize(
    "tokens",
    [
        ["--plugin-orders"],
        ["--config=x.yaml", "--plugin-orders=enabled"],
        ["--live-orders"],
        ["--live-orders=x"],
        ["-m", "research.example.run", "--plugin-orders"],
    ],
)
def test_order_flags_are_refused_in_any_argv(tokens: list[str]) -> None:
    with pytest.raises(ArgvError, match="is not available in the console"):
        refuse_order_flags(tokens)


def test_order_flags_are_refused_for_a_command_that_has_them(catalog: Catalog) -> None:
    refuse_order_flags(["--config=x.yaml", "--start=2026-01-02"])  # other flags pass
    run = catalog.get("fse run")
    for dest, flag in (("plugin_orders", "--plugin-orders"), ("live_orders", "--live-orders")):
        with pytest.raises(ArgvError, match=f"{flag} is not available in the console"):
            build_args(run, {"config": "x.yaml", dest: True})
