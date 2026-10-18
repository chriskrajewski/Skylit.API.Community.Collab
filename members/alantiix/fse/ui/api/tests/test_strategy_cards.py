"""Strategy cards and scoped service tokens. The stub fse stands in for the backtest."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fse.strategy.spec import EXAMPLE_SPEC

from fse_ui.bar_gaps import session_days, sessions_before

from .conftest import Console, wait_job


def _card() -> dict[str, Any]:
    return {
        "source": {
            "url": "https://x.com/trader/status/1",
            "author": "trader",
            "post_id": "1",
            "text": "10/21 EMA cross, Supertrend up, buy the pullback.",
        },
        "spec": EXAMPLE_SPEC,
        "hypothesis": "The cross with Supertrend context lifts the 2R hit rate.",
        "design": {
            "symbols": ["MNQ", "MES"],
            "timeframe": "1m",
            "design_range": ["2026-01-05", "2026-06-17"],
            "holdout_range": ["2026-06-18", "2026-10-01"],
            "judged_at": "2R",
            "k": 2,
        },
    }


def _pass_body() -> dict[str, Any]:
    trades = []
    baseline = []
    for i in range(16):
        symbol = "MNQ" if i % 2 == 0 else "MES"
        day = f"2026-02-{i + 1:02d}"
        trades.append(
            {
                "symbol": symbol,
                "day": day,
                "clock": "10:15",
                "side": "long",
                "hit": True,
                "group": "design",
            }
        )
        baseline.append(
            {
                "symbol": symbol,
                "day": "2025-03-03",
                "clock": "10:15",
                "side": "long",
                "hit": False,
            }
        )
    trades.append(
        {
            "symbol": "ES",
            "day": "2026-04-02",
            "clock": "10:15",
            "side": "long",
            "hit": True,
            "group": "outside",
        }
    )
    baseline.append(
        {"symbol": "ES", "day": "2025-04-01", "clock": "10:15", "side": "long", "hit": False}
    )
    return {
        "trades": trades,
        "baseline": baseline,
        "thresholds": {"permutations": 40, "min_days": 15},
    }


def test_openapi_names_the_card_contract_and_bearer_scheme(console: Console) -> None:
    schema = console.get("/api/openapi.json").json()
    paths = schema["paths"]
    assert "/api/cards" in paths
    assert "/api/cards/validate" in paths
    assert "/api/schemas/card" in paths
    assert "/api/service-tokens" in paths
    assert "ServiceToken" in schema["components"]["securitySchemes"]
    security = paths["/api/cards"]["post"]["security"]
    assert {"ServiceToken": []} in security


def test_service_token_is_scoped_and_skips_csrf(console: Console) -> None:
    minted = console.post(
        "/api/service-tokens",
        json={"name": "agent", "scopes": ["cards:read", "cards:draft", "cards:backtest"]},
    )
    assert minted.status_code == 200, minted.text
    token = minted.json()["token"]
    assert token.startswith("fse_")
    listed = console.get("/api/service-tokens").json()
    assert listed[0]["id_hash"] == minted.json()["id_hash"]
    assert "token" not in listed[0]
    bearer = {"Authorization": f"Bearer {token}"}
    bad = console.client.get("/api/cards", headers={"Authorization": "Bearer fse_nope"})
    assert bad.status_code == 401
    denied = console.client.get("/api/session", headers=bearer)
    assert denied.status_code == 403
    checked = console.client.post(
        "/api/cards/validate", headers=bearer, json={"spec": {"options": {"premium": 1}}}
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["ok"] is False
    named = " ".join(f"{item['path']} {item['rule']}" for item in body["unsupported"])
    assert "options" in named
    created = console.client.post("/api/cards", headers=bearer, json=_card())
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]
    assert created.json()["spec_hash"]
    assert created.json()["design_hash"]
    again = console.client.post("/api/cards", headers=bearer, json=_card())
    assert card_id in again.json()["duplicates"]
    found = console.client.get(
        "/api/cards",
        headers=bearer,
        params={"hash": created.json()["spec_hash"], "q": "Supertrend"},
    )
    assert found.status_code == 200
    assert {item["id"] for item in found.json()["cards"]} >= {card_id, again.json()["id"]}
    backtest = console.client.post(
        f"/api/cards/{card_id}/backtest", headers=bearer, json={"allow_gaps": True}
    )
    assert backtest.status_code == 200, backtest.text
    argv = " ".join(backtest.json()["job"]["argv"])
    data_dir = str(console.client.app.state.settings.data_dir)
    assert "backtest-plugin" in argv
    assert "--history=1" in argv
    assert "configs/cards/" in argv
    assert data_dir in argv
    assert backtest.json()["job"]["executor"] == "local"
    job_id = backtest.json()["job"]["id"]
    assert console.client.get(f"/api/jobs/{job_id}", headers=bearer).status_code == 200
    proxied = console.client.get(f"/api/cards/{card_id}/job", headers=bearer)
    assert proxied.status_code == 200
    assert proxied.json()["id"] == job_id
    assert console.client.get(f"/api/cards/{card_id}/job/log", headers=bearer).status_code == 200
    other = console.post("/api/jobs", json={"command": "fse scan-secrets", "values": {}})
    assert other.status_code == 201, other.text
    denied_job = console.client.get(f"/api/jobs/{other.json()['id']}", headers=bearer)
    assert denied_job.status_code == 403
    cancelled = console.client.post(f"/api/jobs/{job_id}/cancel", headers=bearer)
    assert cancelled.status_code == 403
    holdout = console.client.post(
        f"/api/cards/{card_id}/backtest",
        headers=bearer,
        json={"which": "holdout", "allow_gaps": True},
    )
    assert holdout.status_code == 200, holdout.text
    judged = console.client.post(f"/api/cards/{card_id}/judge", headers=bearer, json=_pass_body())
    assert judged.status_code == 200, judged.text
    assert judged.json()["report"]["grade"] == "PASS"
    view = console.client.get(f"/api/cards/{card_id}/render", headers=bearer)
    assert view.json()["grade"] == "PASS"
    assert view.json()["source"]["post_id"] == "1"
    html = console.client.get(
        f"/api/cards/{card_id}/render", headers=bearer, params={"format": "html"}
    )
    assert html.status_code == 200
    assert "PASS" in html.text
    report = console.client.get(f"/api/cards/{card_id}/report", headers=bearer)
    assert report.headers["content-type"].startswith("text/markdown")
    assert "PASS" in report.text
    assert "trader" in report.text
    revoked = console.client.delete(
        f"/api/service-tokens/{minted.json()['id_hash']}",
        headers={"X-FSE-CSRF": console.csrf},
    )
    assert revoked.status_code == 200, revoked.text
    gone = console.client.get("/api/cards", headers=bearer)
    assert gone.status_code == 401


def test_schema_includes_the_example(console: Console) -> None:
    schema = console.get("/api/schemas/card").json()
    assert schema["example"]["name"] == "ema-cross-supertrend-pullback"
    assert schema["limitations"]
    assert "spec" in schema and "judge" in schema


def _phases(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["phase"]: item for item in body["backtests"]}


def _write_outcomes(out_dir: str, rows: list[dict[str, Any]]) -> None:
    folder = Path(out_dir) / "run" / "plugins" / "rules"
    folder.mkdir(parents=True)
    text = "".join(json.dumps(row) + "\n" for row in rows)
    (folder / "outcomes.jsonl").write_text(text, encoding="utf-8")


def test_card_reports_each_phase_job(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    created = console.post("/api/cards", json=_card())
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]
    fresh = _phases(console.get(f"/api/cards/{card_id}").json())
    assert fresh["design"]["status"] == "none"
    assert fresh["holdout"]["status"] == "none"
    assert fresh["design"]["error"] is None
    started = console.post(f"/api/cards/{card_id}/backtest", json={"allow_gaps": True})
    assert started.status_code == 200, started.text
    assert "--history=1" in " ".join(started.json()["job"]["argv"])
    job_id = started.json()["job"]["id"]
    running = _phases(console.get(f"/api/cards/{card_id}").json())
    assert running["design"]["job_id"] == job_id
    assert running["design"]["status"] in {"queued", "running", "done"}
    assert running["holdout"]["status"] == "none"
    done = wait_job(console, job_id)
    assert done["status"] == "succeeded"
    finished = _phases(console.get(f"/api/cards/{card_id}").json())
    assert finished["design"]["status"] == "done"
    assert finished["design"]["error"] is None
    holdout = console.post(
        f"/api/cards/{card_id}/backtest",
        json={"which": "holdout", "history": 9, "allow_gaps": True},
    )
    assert holdout.status_code == 200, holdout.text
    assert "--history=9" in " ".join(holdout.json()["job"]["argv"])
    wait_job(console, holdout.json()["job"]["id"])
    both = _phases(console.get(f"/api/cards/{card_id}").json())
    assert both["design"]["status"] == "done"
    assert both["holdout"]["status"] == "done"
    assert both["design"]["job_id"] == job_id
    monkeypatch.setenv("STUB_EXIT", "1")
    failed = console.post(f"/api/cards/{card_id}/backtest", json={"allow_gaps": True})
    assert failed.status_code == 200, failed.text
    failed_job = wait_job(console, failed.json()["job"]["id"])
    assert failed_job["status"] == "failed"
    assert failed_job["exit_meaning"] == "failure"
    assert "secrets" not in (failed_job["exit_meaning"] or "")
    again = _phases(console.get(f"/api/cards/{card_id}").json())
    assert again["design"]["status"] == "failed"
    assert again["design"]["error"] is not None
    assert again["design"]["error"].startswith("exit 1: failure")
    assert "secrets found" not in again["design"]["error"]
    assert "stub argv:" in again["design"]["error"]
    assert again["holdout"]["status"] == "done"


def test_judge_reads_outside_cards(console: Console) -> None:
    first = console.post("/api/cards", json=_card())
    second = console.post("/api/cards", json=_card())
    assert first.status_code == 200 and second.status_code == 200
    design_id = first.json()["id"]
    twin_id = second.json()["id"]
    design_run = console.post(f"/api/cards/{design_id}/backtest", json={"allow_gaps": True}).json()
    twin_run = console.post(f"/api/cards/{twin_id}/backtest", json={"allow_gaps": True}).json()
    design_rows = []
    for i in range(16):
        design_rows.append(
            {
                "symbol": "MNQ" if i % 2 == 0 else "MES",
                "day": f"2026-02-{i + 1:02d}",
                "clock": "10:15",
                "side": "long",
                "mfe_r": 3,
            }
        )
    _write_outcomes(design_run["out_dir"], design_rows)
    _write_outcomes(
        twin_run["out_dir"],
        [{"symbol": "MNQ", "day": "2026-03-02", "clock": "10:15", "side": "long", "mfe_r": 3}],
    )
    baseline = [
        {"symbol": "MNQ", "day": "2025-03-03", "clock": "10:15", "side": "long", "hit": False},
        {"symbol": "MES", "day": "2025-03-03", "clock": "10:15", "side": "long", "hit": False},
    ]
    own = console.post(
        f"/api/cards/{design_id}/judge",
        json={"outside_card_ids": [design_id], "baseline": baseline},
    )
    assert own.status_code == 422
    missing = console.post(
        f"/api/cards/{design_id}/judge",
        json={"outside_card_ids": ["cdoesnotexist"], "baseline": baseline},
    )
    assert missing.status_code == 404
    judged = console.post(
        f"/api/cards/{design_id}/judge",
        json={
            "outside_card_ids": [twin_id],
            "baseline": baseline,
            "thresholds": {"permutations": 40, "min_days": 15},
        },
    )
    assert judged.status_code == 200, judged.text
    report = judged.json()["report"]
    assert report["grade"] == "PASS"
    outside = next(item for item in report["checks"] if item["name"] == "outside_group")
    assert outside["passed"] is True
    assert "1 trades" in outside["detail"]


def test_backtest_422_lists_missing_bar_ranges(console: Console) -> None:
    created = console.post("/api/cards", json=_card())
    assert created.status_code == 200, created.text
    refused = console.post(f"/api/cards/{created.json()['id']}/backtest", json={})
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "bars_missing"
    assert detail["symbol"] == "MNQ"
    assert detail["catalog_missing"] is True
    assert detail["missing"]
    assert "2026-01-05" in detail["message"]
    assert "aws-seed-bars.sh" in detail["message"]
    assert "sessions" in detail["message"]
    assert "Warmup" not in detail["message"]
    assert detail["warmup"]["history"] == 1
    assert detail["warmup"]["available"] == 0
    assert detail["warmup"]["missing"]
    phase = _phases(console.get(f"/api/cards/{created.json()['id']}").json())["design"]
    assert phase["status"] == "failed"
    assert phase["error"] is not None
    assert phase["error"].startswith("error:")
    assert "1m bars are missing" in phase["error"] or "catalog.sqlite is missing" in phase["error"]


def test_backtest_clamps_warmup_when_only_the_history_window_is_missing(console: Console) -> None:
    created = console.post("/api/cards", json=_card())
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]
    settings = console.app.state.settings
    cache = settings.cache_dir
    first, last = date(2026, 1, 5), date(2026, 6, 17)
    for day in session_days(settings.project_dir, first, last):
        path = cache / "bars" / "MNQ" / "60s" / f"{day.isoformat()}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (cache / "catalog.sqlite").write_bytes(b"catalog")
    assert sessions_before(settings.project_dir, first, 1)
    posted = console.post(f"/api/cards/{card_id}/backtest", json={"history": 1})
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert "--history=0" in " ".join(body["job"]["argv"])
    assert "clamped from 1 to 0" in body["warmup_warning"]
    job = wait_job(console, body["job"]["id"])
    lines = console.get(f"/api/jobs/{job['id']}/log").json()["lines"]
    assert any("clamped from 1 to 0" in line for line in lines)


def _mes_card() -> dict[str, Any]:
    spec = json.loads(json.dumps(EXAMPLE_SPEC))
    spec["symbols"] = ["MES"]
    body = _card()
    body["spec"] = spec
    body["design"] = {**body["design"], "symbols": ["MES", "MNQ"]}
    return body


def test_backtest_clamps_warmup_for_mes_the_same_way(console: Console) -> None:
    created = console.post("/api/cards", json=_mes_card())
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]
    settings = console.app.state.settings
    cache = settings.cache_dir
    first, last = date(2026, 1, 5), date(2026, 6, 17)
    for day in session_days(settings.project_dir, first, last):
        path = cache / "bars" / "MES" / "60s" / f"{day.isoformat()}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (cache / "catalog.sqlite").write_bytes(b"catalog")
    posted = console.post(f"/api/cards/{card_id}/backtest", json={"history": 1})
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert "--history=0" in " ".join(body["job"]["argv"])
    assert "MES" in body["warmup_warning"]
    assert "clamped from 1 to 0" in body["warmup_warning"]


def test_backtest_422_replaces_a_prior_phase_failure(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "1")
    created = console.post("/api/cards", json=_card())
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]
    first = console.post(f"/api/cards/{card_id}/backtest", json={"allow_gaps": True})
    assert first.status_code == 200, first.text
    old_id = first.json()["job"]["id"]
    old_out = first.json()["out_dir"]
    failed = wait_job(console, old_id)
    assert failed["status"] == "failed"
    refused = console.post(f"/api/cards/{card_id}/backtest", json={})
    assert refused.status_code == 422, refused.text
    phase = _phases(console.get(f"/api/cards/{card_id}").json())["design"]
    assert phase["status"] == "failed"
    assert phase["job_id"] != old_id
    assert phase["run_dir"] == old_out
    assert phase["error"] is not None
    assert phase["error"].startswith("error:")
    assert "secrets found" not in phase["error"]
    assert "exit 1:" not in phase["error"]
