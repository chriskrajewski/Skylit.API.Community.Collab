"""GET /api/api-logs: the shared ring, filters, and redacted errors."""

from __future__ import annotations

import json
import time

from fse.apilog import FILE_NAME, WINDOW_NS, record

from fse_ui.settings import Settings
from tests.conftest import Console

SECRET = "fake-logged-secret-0000"


def _log_dir(console: Console):
    settings: Settings = console.app.state.settings
    return settings.live_state_dir


def test_a_missing_log_is_empty_and_needs_a_session(console: Console) -> None:
    fresh = console.client.__class__(console.app, base_url="http://localhost:5173")
    assert fresh.get("/api/api-logs").status_code == 401
    body = console.get("/api/api-logs").json()
    assert body == {"calls": [], "total": 0, "endpoints": []}


def test_filters_errors_api_and_endpoint(console: Console) -> None:
    now = time.time_ns()
    record(
        api="skylit",
        method="GET",
        path="/v1/stream",
        status=400,
        latency_ms=12.5,
        error="bad_request — nope",
        caller="map_feed",
        ok=False,
        t_ns=now,
    )
    record(
        api="skylit",
        method="GET",
        path="/v1/account",
        status=200,
        latency_ms=3,
        error=None,
        caller="skylit",
        ok=True,
        t_ns=now + 1,
    )
    record(
        api="projectx",
        method="POST",
        path="/api/Order/place",
        status=200,
        latency_ms=40,
        error="errorCode 2 — bracket rejected",
        caller="alpha",
        ok=False,
        t_ns=now + 2,
    )
    errors = console.get("/api/api-logs?status=errors").json()
    assert errors["total"] == 3
    assert [row["path"] for row in errors["calls"]] == ["/api/Order/place", "/v1/stream"]
    assert errors["calls"][0]["api"] == "projectx"
    assert errors["calls"][0]["caller"] == "alpha"
    assert errors["calls"][0]["ok"] is False
    projectx = console.get("/api/api-logs?api=projectx&status=all").json()
    assert projectx["total"] == 1
    assert projectx["endpoints"] == ["/api/Order/place"]
    assert projectx["calls"][0]["method"] == "POST"
    one = console.get("/api/api-logs?status=all&endpoint=/v1/stream").json()
    assert [row["path"] for row in one["calls"]] == ["/v1/stream"]
    assert one["calls"][0]["status"] == 400
    assert one["total"] == 3


def test_secrets_are_not_returned_and_old_rows_are_dropped(console: Console) -> None:
    record(
        api="skylit",
        method="GET",
        path="/v1/account",
        status=401,
        latency_ms=1,
        error=f"Bearer {SECRET} api_key={SECRET}",
        caller="skylit",
        ok=False,
        redact=(SECRET,),
    )
    record(
        api="projectx",
        method="POST",
        path="/api/Auth/loginKey",
        status=200,
        latency_ms=1,
        error="stale-row",
        caller="projectx.login",
        ok=False,
        t_ns=time.time_ns() - WINDOW_NS - 1_000_000_000,
    )
    body = console.get("/api/api-logs?status=all").json()
    text = json.dumps(body)
    assert SECRET not in text
    assert "Bearer [REDACTED]" in text
    assert "stale-row" not in text
    assert "authorization" not in {key.lower() for row in body["calls"] for key in row}


def test_exchange_detail_is_returned_without_authorization(console: Console) -> None:
    log = _log_dir(console) / FILE_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    secret = "planted-secret-value"
    line = {
        "id": "planted",
        "t_ns": time.time_ns(),
        "api": "skylit",
        "method": "GET",
        "path": "/v1/stream",
        "status": 400,
        "latency_ms": 8,
        "error": "bad_request",
        "caller": "map_feed",
        "ok": False,
        "url": "https://api.skylit.ai/v1/stream?symbols=SPX",
        "req_headers": {"Authorization": f"Bearer {secret}", "Accept": "text/event-stream"},
        "req_body": None,
        "req_truncated": False,
        "res_body": f'{{"token":"{secret}","error":{{"message":"rejected"}}}}',
        "res_truncated": True,
        "res_note": None,
    }
    log.write_text(json.dumps(line) + "\n", encoding="utf-8")
    body = console.get("/api/api-logs?status=errors").json()
    call = body["calls"][0]
    dumped = json.dumps(body)
    assert secret not in dumped
    assert "Authorization" not in dumped
    assert call["url"] == "https://api.skylit.ai/v1/stream?symbols=SPX"
    assert call["req_headers"] == {"Accept": "text/event-stream"}
    assert call["req_body"] is None
    assert "[REDACTED]" in call["res_body"]
    assert call["res_truncated"] is True


def test_a_bad_filter_is_422_and_an_unreadable_log_is_500(console: Console) -> None:
    assert console.get("/api/api-logs?api=other").status_code == 422
    assert console.get("/api/api-logs?status=nope").status_code == 422
    log = _log_dir(console) / FILE_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    log.mkdir()
    failed = console.get("/api/api-logs")
    assert failed.status_code == 500
    assert failed.json()["detail"] == "the API log could not be read"
