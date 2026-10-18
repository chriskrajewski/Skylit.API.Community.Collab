"""The console API log: the ring, redaction, and Skylit / ProjectX capture.

Nothing here reaches a real host. Skylit uses respx; ProjectX uses the in-memory
gateway or a respx route. The log path is set only for the test and cleared
after, so a later test does not append to it.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fse.apilog import (
    BODY_LIMIT,
    FILE_NAME,
    MAX_CALLS,
    PATH_ENV,
    STREAM_BODY_NOTE,
    WINDOW_NS,
    bind_caller,
    body_text,
    enable,
    projectx_outcome,
    query,
    record,
    reset,
    safe_headers,
    sanitize,
    skylit_error_text,
    unbind_caller,
)
from fse.data.cache import HeatmapView
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import ORDER_PLACE_PATH, ORDER_SEARCH_PATH, BrokerAdapter, EntryIntent
from fse.projectx.session import (
    API_KEY_VARIABLE,
    LOGIN_PATH,
    USERNAME_VARIABLE,
    ProjectXLoginError,
    ProjectXSession,
)
from fse.secrets.env import EnvView
from fse.skylit.client import ClientConfig, SkylitClient, SkylitStreamError
from fse.skylit.endpoints import Host, stream_params
from fse.skylit.fetch_log import FetchLog
from fse.timekit import NS_PER_SECOND, parse_rfc3339
from tests.fakes.clock import FakeClock
from tests.fakes.projectx_gateway import BASE, TOKEN, FakeGateway

KEY = "fake-skylit-key-0000"
USER = "fake-projectx-user-0000"
PX_KEY = "fake-projectx-key-0000"
T0 = parse_rfc3339("2026-03-02T14:35:00Z")
VIEW = HeatmapView()
MNQ = "CON.F.US.MNQ.Z26"


class _NoJournal:
    def order_id_for(self, client_id: str) -> int | None:
        return None


@pytest.fixture
def api_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / FILE_NAME
    reset()
    monkeypatch.delenv(PATH_ENV, raising=False)
    enable(path)
    yield path
    reset()
    monkeypatch.delenv(PATH_ENV, raising=False)


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _account() -> dict[str, Any]:
    return {
        "data": {
            "customerId": "fake-customer-0000",
            "status": "active",
            "apiEligible": True,
            "unlimited": False,
            "creditsBalance": 10,
            "limits": {"requestsPerMinute": 60},
        }
    }


# ---------------------------------------------------------------- ring and redaction


def test_record_is_a_noop_until_a_path_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset()
    monkeypatch.delenv(PATH_ENV, raising=False)
    record(
        api="skylit",
        method="GET",
        path="/v1/account",
        status=200,
        latency_ms=1,
        error=None,
        caller=None,
        ok=True,
    )
    assert list(tmp_path.iterdir()) == []


def test_sanitize_strips_credentials_and_truncates() -> None:
    secret = "super-secret-value"
    text = f'api_key={secret} "token":"{secret}" sk_live_abcdefghijklmnop'
    cleaned = sanitize(text, (secret,))
    assert cleaned is not None
    assert secret not in cleaned
    assert "sk_live_" not in cleaned
    assert "[REDACTED]" in cleaned
    bearer = sanitize("saw Bearer abcdefghijklmnop in the body")
    assert bearer == "saw Bearer [REDACTED] in the body"
    long = sanitize("x" * 500)
    assert long is not None
    assert len(long) == 240
    assert long.endswith("…")
    assert sanitize("   ") is None
    assert sanitize(None) is None


def test_projectx_http_200_with_error_code_is_a_failure() -> None:
    ok, text = projectx_outcome(
        200,
        {"success": False, "errorCode": 2, "errorMessage": "bracket rejected"},
        include_message=True,
    )
    assert ok is False
    assert text is not None
    assert "errorCode 2" in text
    assert "bracket rejected" in text
    hidden, code_only = projectx_outcome(
        200,
        {"success": False, "errorCode": 2, "errorMessage": "echoed-username"},
        include_message=False,
    )
    assert hidden is False
    assert code_only == "errorCode 2"
    fine, none = projectx_outcome(200, {"success": True, "errorCode": 0}, include_message=True)
    assert fine is True
    assert none is None
    # A JSON boolean is not an error code.
    flagged, _ = projectx_outcome(200, {"success": True, "errorCode": True}, include_message=True)
    assert flagged is True


def test_skylit_error_text_joins_code_and_message() -> None:
    assert skylit_error_text({"error": {"code": "bad_request", "message": "nope"}}) == (
        "bad_request — nope"
    )
    assert skylit_error_text({"error": "plain"}) == "plain"
    assert skylit_error_text([]) is None


def test_the_ring_keeps_the_newest_500(api_log: Path) -> None:
    now = time.time_ns()
    for i in range(MAX_CALLS + 1):
        record(
            api="skylit",
            method="GET",
            path="/v1/account",
            status=200,
            latency_ms=1,
            error=f"n-{i}",
            caller="skylit",
            ok=True,
            t_ns=now - (MAX_CALLS - i) * NS_PER_SECOND,
        )
    body = query(api_log, now_ns=now + NS_PER_SECOND)
    assert body["total"] == MAX_CALLS
    errors = [row["error"] for row in body["calls"]]  # type: ignore[index]
    assert "n-0" not in errors
    assert errors[0] == f"n-{MAX_CALLS}"
    assert len(_rows(api_log)) == MAX_CALLS


def test_calls_older_than_24_hours_are_dropped(api_log: Path) -> None:
    now = time.time_ns()
    record(
        api="projectx",
        method="POST",
        path=ORDER_PLACE_PATH,
        status=200,
        latency_ms=1,
        error="stale-row",
        caller="alpha",
        ok=False,
        t_ns=now - WINDOW_NS - NS_PER_SECOND,
    )
    record(
        api="skylit",
        method="GET",
        path="/v1/account",
        status=200,
        latency_ms=1,
        error=None,
        caller="skylit",
        ok=True,
        t_ns=now,
    )
    body = query(api_log, now_ns=now)
    calls = body["calls"]
    assert isinstance(calls, list)
    assert len(calls) == 1
    assert calls[0]["path"] == "/v1/account"
    assert "stale-row" not in api_log.read_text(encoding="utf-8")


def test_query_filters_api_status_and_endpoint(api_log: Path) -> None:
    now = time.time_ns()
    record(
        api="skylit",
        method="GET",
        path="/v1/stream",
        status=400,
        latency_ms=12,
        error="bad_request",
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
        path=ORDER_PLACE_PATH,
        status=200,
        latency_ms=40,
        error="errorCode 2",
        caller="alpha",
        ok=False,
        t_ns=now + 2,
    )
    errors = query(api_log, now_ns=now + 10, status="errors")
    assert errors["total"] == 3
    assert [row["path"] for row in errors["calls"]] == [ORDER_PLACE_PATH, "/v1/stream"]  # type: ignore[index]
    projectx = query(api_log, now_ns=now + 10, api="projectx")
    assert projectx["total"] == 1
    assert projectx["endpoints"] == [ORDER_PLACE_PATH]
    one = query(api_log, now_ns=now + 10, endpoint="/v1/stream")
    assert [row["path"] for row in one["calls"]] == ["/v1/stream"]  # type: ignore[index]
    assert one["calls"][0]["t"] == pytest.approx((now) / 1_000_000_000)  # type: ignore[index]
    with pytest.raises(ValueError, match="api must be"):
        query(api_log, now_ns=now, api="nope")
    with pytest.raises(ValueError, match="endpoint must be"):
        query(api_log, now_ns=now, endpoint="not a path")


def test_bodies_are_truncated_and_secret_headers_are_dropped(api_log: Path) -> None:
    huge = "y" * (BODY_LIMIT + 25)
    text, cut = body_text(huge)
    assert cut is True
    assert text is not None
    assert len(text) == BODY_LIMIT
    assert text.endswith("…")
    headers = safe_headers(
        {
            "Authorization": "Bearer abcdefghijklmnop",
            "Accept": "application/json",
            "X-Api-Key": "secret-value-here",
            "Last-Event-ID": "evt-1",
        }
    )
    assert "Authorization" not in headers
    assert "X-Api-Key" not in headers
    assert headers["Accept"] == "application/json"
    assert headers["Last-Event-ID"] == "evt-1"
    record(
        api="skylit",
        method="GET",
        path="/v1/account",
        status=200,
        latency_ms=1,
        error=None,
        caller="skylit",
        ok=True,
        req_headers={"Authorization": "Bearer abcdefghijklmnop", "Accept": "application/json"},
        res_body=huge,
    )
    stored = _rows(api_log)[0]
    assert stored["req_truncated"] is False
    assert stored["res_truncated"] is True
    assert stored["res_body"].endswith("…")
    assert "Authorization" not in stored["req_headers"]
    shown = query(api_log, now_ns=time.time_ns(), status="all")
    call = shown["calls"][0]
    assert call["res_truncated"] is True
    assert call["req_body"] is None
    assert call["req_headers"] == {"Accept": "application/json"}


def test_query_strips_a_planted_authorization_header(api_log: Path) -> None:
    secret = "planted-secret-value"
    line = {
        "id": "planted",
        "t_ns": time.time_ns(),
        "api": "projectx",
        "method": "POST",
        "path": "/api/Order/place",
        "status": 200,
        "latency_ms": 1,
        "error": None,
        "caller": "alpha",
        "ok": False,
        "url": "https://api.projectx.invalid/api/Order/place",
        "req_headers": {"Authorization": f"Bearer {secret}", "Accept": "application/json"},
        "req_body": f'{{"token":"{secret}"}}',
        "res_body": None,
    }
    api_log.write_text(json.dumps(line) + "\n", encoding="utf-8")
    shown = query(api_log, now_ns=time.time_ns(), status="all")
    call = shown["calls"][0]
    dumped = json.dumps(shown)
    assert secret not in dumped
    assert "Authorization" not in dumped
    assert call["req_headers"] == {"Accept": "application/json"}
    assert call["req_body"] == '{"token":"[REDACTED]"}'
    assert call["res_body"] is None
    assert call["res_truncated"] is False


# ---------------------------------------------------------------- capture


@pytest.fixture
def fetch_log(tmp_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / "fetch_log.jsonl") as log:
        yield log


def _route(router: respx.MockRouter, path: str) -> respx.Route:
    return router.route(method="GET", scheme="https", host=Host.API.value, path=path)


@pytest.mark.asyncio
async def test_a_stream_400_logs_the_error_body_and_not_the_key(
    respx_router: respx.MockRouter, fetch_log: FetchLog, api_log: Path
) -> None:
    _route(respx_router, "/v1/account").mock(return_value=httpx.Response(200, json=_account()))
    _route(respx_router, "/v1/stream").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "bad_request", "message": f"rejected {KEY}"}},
        )
    )
    env = EnvView({"SKYLIT_API_KEY": KEY}, {})
    client = SkylitClient(env, ClientConfig(), fetch_log, FakeClock(T0), random.Random(1))
    params = stream_params(["SPX"], metric="gamma", view=VIEW)
    token = bind_caller("map_feed")
    try:
        async with client:
            with pytest.raises(SkylitStreamError, match="400"):
                async with client.stream(params):
                    pass
    finally:
        unbind_caller(token)
    rows = [row for row in _rows(api_log) if row["path"] == "/v1/stream"]
    assert len(rows) == 1
    assert rows[0]["api"] == "skylit"
    assert rows[0]["method"] == "GET"
    assert rows[0]["status"] == 400
    assert rows[0]["ok"] is False
    assert rows[0]["caller"] == "map_feed"
    assert rows[0]["error"] == "bad_request — rejected [REDACTED]"
    assert rows[0]["res_body"] is not None
    assert "bad_request" in rows[0]["res_body"]
    assert "[REDACTED]" in rows[0]["res_body"]
    assert rows[0]["url"] is not None
    assert "/v1/stream" in rows[0]["url"]
    assert "Authorization" not in rows[0]["req_headers"]
    assert rows[0]["req_headers"]["Accept"] == "text/event-stream"
    assert rows[0]["req_body"] is None
    stored = api_log.read_text(encoding="utf-8")
    assert KEY not in stored
    assert "Authorization" not in stored


@pytest.mark.asyncio
async def test_a_bracket_reject_is_logged_without_the_key(
    respx_router: respx.MockRouter, api_log: Path
) -> None:
    respx_router.post(BASE + LOGIN_PATH).mock(
        return_value=httpx.Response(200, json={"success": True, "errorCode": 0, "token": TOKEN})
    )
    respx_router.post(BASE + ORDER_PLACE_PATH).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "errorCode": 2,
                "errorMessage": f"bracket rejected {PX_KEY}",
                "orderId": 9001,
            },
        )
    )
    clock = FakeClock(T0)
    env = EnvView(
        {USERNAME_VARIABLE: USER, API_KEY_VARIABLE: PX_KEY, "PRACTICE_ACCOUNT_ID": "101"}, {}
    )
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
        adapter = BrokerAdapter(session, env, clock).for_mode("practice")
        adapter.set_caller("alpha")
        intent = EntryIntent.market(
            "fse-0a1b2c3d-000001", MNQ, "buy", 1, stop_ticks=4, target_ticks=8
        )
        result = await clock.run(adapter.place_bracketed(intent))
    assert result.outcome == "rejected"
    assert result.error_code == 2
    placed = [row for row in _rows(api_log) if row["path"] == ORDER_PLACE_PATH]
    assert len(placed) == 1
    assert placed[0]["api"] == "projectx"
    assert placed[0]["status"] == 200
    assert placed[0]["ok"] is False
    assert placed[0]["caller"] == "alpha"
    assert placed[0]["error"] is not None
    assert "errorCode 2" in placed[0]["error"]
    assert "bracket rejected" in placed[0]["error"]
    assert placed[0]["req_body"] is not None
    assert "customTag" in placed[0]["req_body"]
    assert "fse-0a1b2c3d-000001" in placed[0]["req_body"]
    assert placed[0]["res_body"] is not None
    assert "errorCode" in placed[0]["res_body"]
    assert "bracket rejected" in placed[0]["res_body"]
    assert "[REDACTED]" in placed[0]["res_body"]
    assert "Authorization" not in json.dumps(placed[0]["req_headers"])
    stored = api_log.read_text(encoding="utf-8")
    assert PX_KEY not in stored
    assert TOKEN not in stored
    assert USER not in stored


@pytest.mark.asyncio
async def test_a_client_order_id_mismatch_is_logged(
    respx_router: respx.MockRouter, api_log: Path
) -> None:
    gateway = FakeGateway()
    gateway.tag_field = False
    gateway.install(respx_router)
    gateway.add_foreign(MNQ)
    clock = FakeClock(T0)
    env = EnvView(
        {USERNAME_VARIABLE: USER, API_KEY_VARIABLE: PX_KEY, "PRACTICE_ACCOUNT_ID": "101"}, {}
    )
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
        adapter = BrokerAdapter(session, env, clock).for_mode("practice")
        adapter.set_caller("alpha")
        lookup = await clock.run(
            adapter.find_by_client_id("fse-0a1b2c3d-000099", _NoJournal(), start_ns=T0, timeout_s=5)
        )
    assert lookup.outcome == "failed"
    assert "neither customTag" in lookup.detail
    failed = [row for row in _rows(api_log) if row["ok"] is False]
    assert len(failed) == 1
    assert failed[0]["path"] == ORDER_SEARCH_PATH
    assert failed[0]["caller"] == "alpha"
    assert failed[0]["error"] is not None
    assert "client-order-id mismatch" in failed[0]["error"]
    stored = api_log.read_text(encoding="utf-8")
    assert TOKEN not in stored
    assert PX_KEY not in stored


@pytest.mark.asyncio
async def test_login_does_not_store_the_error_message_or_token(
    respx_router: respx.MockRouter, api_log: Path
) -> None:
    respx_router.post(BASE + LOGIN_PATH).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "errorCode": 3,
                "errorMessage": f"unknown user {USER}",
                "token": TOKEN,
            },
        )
    )
    clock = FakeClock(T0)
    env = EnvView({USERNAME_VARIABLE: USER, API_KEY_VARIABLE: PX_KEY}, {})
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
        with pytest.raises(ProjectXLoginError):
            await clock.run(session.token())
    rows = _rows(api_log)
    assert len(rows) == 1
    assert rows[0]["path"] == LOGIN_PATH
    assert rows[0]["caller"] == "projectx.login"
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "errorCode 3"
    assert rows[0]["req_body"] == '{"userName":"[REDACTED]","apiKey":"[REDACTED]"}'
    assert rows[0]["res_body"] is not None
    assert "errorCode" in rows[0]["res_body"]
    assert "[REDACTED]" in rows[0]["res_body"]
    stored = api_log.read_text(encoding="utf-8")
    assert USER not in stored
    assert TOKEN not in stored
    assert PX_KEY not in stored
    assert "unknown user" not in stored


@pytest.mark.asyncio
async def test_a_stream_200_does_not_store_frames(
    respx_router: respx.MockRouter, fetch_log: FetchLog, api_log: Path
) -> None:
    frame = "event: heatmap\ndata: " + ("x" * 200)
    _route(respx_router, "/v1/account").mock(return_value=httpx.Response(200, json=_account()))
    _route(respx_router, "/v1/stream").mock(return_value=httpx.Response(200, text=frame))
    env = EnvView({"SKYLIT_API_KEY": KEY}, {})
    client = SkylitClient(env, ClientConfig(), fetch_log, FakeClock(T0), random.Random(1))
    params = stream_params(["SPX"], metric="gamma", view=VIEW)
    async with client, client.stream(params):
        pass
    rows = [row for row in _rows(api_log) if row["path"] == "/v1/stream"]
    assert len(rows) == 1
    assert rows[0]["ok"] is True
    assert rows[0]["res_note"] == STREAM_BODY_NOTE
    assert rows[0]["res_body"] is None
    assert rows[0]["res_truncated"] is False
    stored = api_log.read_text(encoding="utf-8")
    assert "event: heatmap" not in stored
    assert KEY not in stored
    assert "Authorization" not in stored
