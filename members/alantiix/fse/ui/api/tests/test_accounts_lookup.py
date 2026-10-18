"""The ProjectX account lookup job and setting an account id from it."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from fse_ui.accounts_lookup import (
    CONFIRM_NOT_PRACTICE,
    SAME_ACCOUNT,
    UseError,
    annotate,
    check_use,
    diagnostics,
    parse_accounts_lookup,
)
from fse_ui.catalog import ArgvError, Catalog, build_args, refuse_order_flags
from fse_ui.secrets_store import LocalSecretStore, SsmSecretStore
from tests.conftest import Console, wait_job

USERNAME, API_KEY = "px-user-canary-7", "px-key-canary-7"
NOT_NUMERIC = "50KTC-SKU-V2-DLL-000000-00000000"


def _line(accounts: list[dict[str, Any]]) -> str:
    return json.dumps({"kind": "fse_accounts", "accounts": accounts, "diagnostics": []})


def _secrets(console: Console) -> dict[str, str]:
    return {s["name"]: s["status"] for s in console.get("/api/secrets").json()["secrets"]}


def _lookup(console: Console) -> dict[str, Any]:
    r = console.post("/api/workflows/accounts-lookup", json={})
    assert r.status_code == 201, r.text
    job = wait_job(console, r.json()["id"])
    assert job["status"] == "succeeded", job
    return job


def _result(console: Console, job_id: str) -> dict[str, Any]:
    r = console.get(f"/api/jobs/{job_id}/accounts")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _roles(result: dict[str, Any]) -> dict[str, str]:
    return {a["id"]: a["role"] for a in result["accounts"]}


def _codes(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(d["code"], d["variable"]) for d in result["diagnostics"]]


def _use(console: Console, job_id: str, account_id: str, role: str, **kw: Any) -> Any:
    return console.post(
        "/api/accounts/use",
        json={"job_id": job_id, "account_id": account_id, "role": role, **kw},
    )


# ---------------------------------------------------------------- catalog and argv


def test_accounts_job_argv_and_unknown_flags(console: Console) -> None:
    r = console.post("/api/jobs", json={"command": "fse accounts", "values": {"json": True}})
    assert r.status_code == 201, r.text
    assert r.json()["argv"][-2:] == ["accounts", "--json"]
    r = console.post(
        "/api/jobs",
        json={"command": "fse accounts", "values": {"json": True, "plugin_orders": True}},
    )
    assert r.status_code == 422 and "unknown argument" in r.json()["detail"]


def test_accounts_is_a_light_batch_command() -> None:
    cmd = Catalog().get("fse accounts")
    assert (cmd.placement, cmd.heavy, cmd.disabled_reason) == ("batch", False, None)
    assert build_args(cmd, {"json": True}) == ["--json"]


def test_runner_jobs_and_order_flags_are_still_refused(console: Console) -> None:
    for command in ("fse run", "fse paper", "fse live"):
        r = console.post("/api/jobs", json={"command": command, "values": {"config": "x"}})
        assert r.status_code == 409, command
    for tokens in (["--plugin-orders"], ["--live-orders=1"]):
        with pytest.raises(ArgvError, match="is not available in the console"):
            refuse_order_flags(tokens)


def test_lookup_workflow_argv(console: Console) -> None:
    job = _lookup(console)
    assert job["argv"][-2:] == ["accounts", "--json"]
    assert job["title"] == "ProjectX accounts"


# ---------------------------------------------------------------- parsing


def test_parse_skips_status_lines_and_reads_the_newest_line() -> None:
    old = _line([{"id": "1", "name": "OLD", "can_trade": True, "role": ""}])
    new = _line([{"id": "101", "name": "PRAC-A", "can_trade": False, "role": "practice"}])
    parsed = parse_accounts_lookup(
        ["stub argv: ['accounts', '--json']", old, new, 'task abc: {"kind": "x"}']
    )
    assert parsed["error"] is None
    assert parsed["accounts"] == [
        {"id": "101", "name": "PRAC-A", "can_trade": False, "redacted": False, "engine_role": ""}
    ]
    status_only = parse_accounts_lookup(["fse-entrypoint: " + new])
    assert status_only == {"accounts": None, "error": "the job printed no accounts line"}


def test_parse_reports_malformed_json_and_rows() -> None:
    bad = parse_accounts_lookup(['{"kind":"fse_accounts","accounts":[{"id":'])
    assert bad["accounts"] is None and "not valid JSON" in bad["error"]
    row = parse_accounts_lookup([_line([{"id": 101, "name": "X", "can_trade": True}])])
    assert row["accounts"] is None and "malformed account row" in row["error"]


def test_parse_keeps_redacted_ids_with_the_engine_role(tmp_path: Path) -> None:
    parsed = parse_accounts_lookup(
        [
            _line(
                [
                    {"id": "[redacted]", "name": "PRAC-A", "can_trade": True, "role": "practice"},
                    {"id": "[REDACTED]", "name": "B", "can_trade": True, "role": "bogus"},
                ]
            )
        ]
    )
    rows = parsed["accounts"]
    assert [r["redacted"] for r in rows] == [True, True]
    store = LocalSecretStore(tmp_path / "s.json")
    store.put("PRACTICE_ACCOUNT_ID", "88267128")
    annotated = annotate(rows, store)
    assert [a["role"] for a in annotated] == ["practice", ""]
    assert "engine_role" not in annotated[0]
    # The redacted row is the stored practice id: no false no_match for it.
    codes = [(d["code"], d["variable"]) for d in diagnostics(annotated, store)]
    assert codes == [("unset", "COMBINE_ACCOUNT_ID")]
    with pytest.raises(UseError, match="pick an account"):
        check_use(rows, "[redacted]", "combine", False, store)


def test_redacted_row_without_an_engine_role_on_aws(tmp_path: Path) -> None:
    # An AWS batch task has no account ids in its env: the engine's role is '' and
    # it reports the ids unset, but the console stored them.
    parsed = parse_accounts_lookup(
        [
            _line(
                [
                    {"id": "[redacted]", "name": "PRAC-A", "can_trade": True, "role": ""},
                    {"id": "202", "name": "B", "can_trade": True, "role": ""},
                ]
            )
        ]
    )
    rows = parsed["accounts"]
    store = LocalSecretStore(tmp_path / "s.json")
    store.put("PRACTICE_ACCOUNT_ID", "88267128")
    annotated = annotate(rows, store)
    assert [a["role"] for a in annotated] == ["practice", ""]
    codes = [(d["code"], d["variable"]) for d in diagnostics(annotated, store)]
    assert codes == [("unset", "COMBINE_ACCOUNT_ID")]
    # Two stored ids and one redacted row: the role is undecided, never a false no_match.
    store.put("COMBINE_ACCOUNT_ID", "99999999")
    annotated = annotate(rows, store)
    assert [a["role"] for a in annotated] == ["", ""]
    assert diagnostics(annotated, store) == []


# ---------------------------------------------------------------- the lookup endpoint


def test_lookup_rows_roles_and_diagnostics(console: Console) -> None:
    job = _lookup(console)
    result = _result(console, job["id"])
    assert result["job"]["id"] == job["id"] and result["error"] is None
    assert result["accounts"] == [
        {"id": "101", "name": "PRAC-FAKE-1", "can_trade": True, "role": "", "redacted": False},
        {"id": "202", "name": "50KTC-FAKE-2", "can_trade": True, "role": "", "redacted": False},
    ]
    assert _codes(result) == [("unset", "PRACTICE_ACCOUNT_ID"), ("unset", "COMBINE_ACCOUNT_ID")]

    console.put("/api/secrets/PRACTICE_ACCOUNT_ID", json={"value": NOT_NUMERIC})
    console.put("/api/secrets/COMBINE_ACCOUNT_ID", json={"value": "999"})
    result = _result(console, job["id"])
    assert _codes(result) == [
        ("not_numeric", "PRACTICE_ACCOUNT_ID"),
        ("no_match", "COMBINE_ACCOUNT_ID"),
    ]
    assert "use the numeric id, not the account name" in result["diagnostics"][0]["message"]
    assert NOT_NUMERIC not in json.dumps(result)

    console.put("/api/secrets/PRACTICE_ACCOUNT_ID", json={"value": "101"})
    console.put("/api/secrets/COMBINE_ACCOUNT_ID", json={"value": "101"})
    result = _result(console, job["id"])
    assert _roles(result) == {"101": "practice,combine", "202": ""}
    assert _codes(result) == [("same", "PRACTICE_ACCOUNT_ID")]
    assert result["diagnostics"][0]["message"] == "PRACTICE_ACCOUNT_ID equals COMBINE_ACCOUNT_ID"


def test_lookup_of_another_command_is_404(console: Console) -> None:
    r = console.post("/api/jobs", json={"command": "fse scan-secrets", "values": {}})
    job = wait_job(console, r.json()["id"])
    assert console.get(f"/api/jobs/{job['id']}/accounts").status_code == 404
    assert _use(console, job["id"], "101", "practice").status_code == 409
    assert console.get("/api/jobs/nope/accounts").status_code == 404
    assert _use(console, "nope", "101", "practice").status_code == 404


def test_failed_lookup_cannot_set_an_account(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "4")
    r = console.post("/api/workflows/accounts-lookup", json={})
    job = wait_job(console, r.json()["id"])
    assert job["status"] == "failed"
    assert _use(console, job["id"], "101", "practice").status_code == 409
    assert _secrets(console)["PRACTICE_ACCOUNT_ID"] == "missing"


# ---------------------------------------------------------------- setting an account id


def test_use_sets_the_secret_with_the_prac_confirm_and_same_refusal(console: Console) -> None:
    console.put("/api/secrets/PROJECTX_USERNAME", json={"value": USERNAME})
    console.put("/api/secrets/PROJECTX_API_KEY", json={"value": API_KEY})
    job = _lookup(console)
    bodies: list[str] = []

    r = _use(console, job["id"], "101", "practice")
    bodies.append(r.text)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "name": "PRACTICE_ACCOUNT_ID",
        "status": "set",
        "restart": "Restart the runner for it to read the new value.",
    }
    assert _secrets(console)["PRACTICE_ACCOUNT_ID"] == "set"
    assert _roles(_result(console, job["id"])) == {"101": "practice", "202": ""}

    # A non-PRAC account as Practice needs the confirmation; the secret is unchanged.
    r = _use(console, job["id"], "202", "practice")
    bodies.append(r.text)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "confirm_required", "message": CONFIRM_NOT_PRACTICE}
    assert _roles(_result(console, job["id"])) == {"101": "practice", "202": ""}
    r = _use(console, job["id"], "202", "practice", confirm_not_practice=True)
    bodies.append(r.text)
    assert r.status_code == 200
    assert _roles(_result(console, job["id"])) == {"101": "", "202": "practice"}

    # Combine on the Practice id is refused; on the other account it is set.
    r = _use(console, job["id"], "202", "combine")
    bodies.append(r.text)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "same_account", "message": SAME_ACCOUNT}
    r = _use(console, job["id"], "101", "combine")
    bodies.append(r.text)
    assert r.status_code == 200 and r.json()["name"] == "COMBINE_ACCOUNT_ID"
    result = _result(console, job["id"])
    assert _roles(result) == {"101": "combine", "202": "practice"}
    assert result["diagnostics"] == []

    r = _use(console, job["id"], "303", "combine")
    bodies.append(r.text)
    assert r.status_code == 422 and r.json()["detail"] == "pick an account from the lookup"
    r = _use(console, job["id"], "1" * 33, "combine")
    assert r.status_code == 422

    audit = console.get("/api/audit").text
    texts = [*bodies, json.dumps(result), audit]
    for text in texts:
        assert USERNAME not in text and API_KEY not in text
    use_rows = [row for row in console.get("/api/audit").json() if "/accounts/use" in row["action"]]
    assert len(use_rows) == 7
    assert all(set(row["detail"]) <= {"job", "role", "name"} for row in use_rows)


# ---------------------------------------------------------------- store helpers


def test_local_equals_and_id_shape(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    store = LocalSecretStore(tmp_path / "s.json")
    assert store.equals("PRACTICE_ACCOUNT_ID", "101") is None
    assert store.id_shape("PRACTICE_ACCOUNT_ID") == "unset"
    store.put("PRACTICE_ACCOUNT_ID", "101")
    assert store.equals("PRACTICE_ACCOUNT_ID", "101") is True
    assert store.equals("PRACTICE_ACCOUNT_ID", "202") is False
    assert store.id_shape("PRACTICE_ACCOUNT_ID") == "numeric"
    store.put("PRACTICE_ACCOUNT_ID", NOT_NUMERIC)
    assert store.id_shape("PRACTICE_ACCOUNT_ID") == "not_numeric"
    store.put("PRACTICE_ACCOUNT_ID", "0101")
    assert store.id_shape("PRACTICE_ACCOUNT_ID") == "not_numeric"
    for rec in caplog.records:
        assert NOT_NUMERIC not in rec.getMessage()


@mock_aws
def test_ssm_equals_and_id_shape() -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    store = SsmSecretStore(ssm)
    assert store.equals("COMBINE_ACCOUNT_ID", "202") is None
    assert store.id_shape("COMBINE_ACCOUNT_ID") == "unset"
    ssm.put_parameter(
        Name="/fse/secrets/COMBINE_ACCOUNT_ID", Value="PLACEHOLDER-x", Type="SecureString"
    )
    assert store.equals("COMBINE_ACCOUNT_ID", "PLACEHOLDER-x") is None
    assert store.id_shape("COMBINE_ACCOUNT_ID") == "unset"
    store.put("COMBINE_ACCOUNT_ID", "202")
    assert store.equals("COMBINE_ACCOUNT_ID", "202") is True
    assert store.equals("COMBINE_ACCOUNT_ID", "101") is False
    assert store.id_shape("COMBINE_ACCOUNT_ID") == "numeric"
    store.put("COMBINE_ACCOUNT_ID", NOT_NUMERIC)
    assert store.id_shape("COMBINE_ACCOUNT_ID") == "not_numeric"
