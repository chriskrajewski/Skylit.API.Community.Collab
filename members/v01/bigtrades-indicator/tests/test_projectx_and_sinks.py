# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
from __future__ import annotations

import asyncio
import json

import httpx

from bigtrades.config import Settings
from bigtrades.format import fmt_price
from bigtrades.models import BigTrade, Side
from bigtrades.projectx import ProjectXClient, contract_label, display_root, hub_ws_url, pick_front_month
from bigtrades.projectx.signalr import HANDSHAKE, RS, decode, encode, invocation
from bigtrades.sinks import DiscordWebhookSink


def test_front_month_prefers_active_nearest_and_exact_root(contract_rows):
    row = pick_front_month(contract_rows, "ES")
    assert row["id"] == "CON.F.US.EP.Z26"  # U26 is inactive, H27 is later, MES is another root
    assert pick_front_month(contract_rows, "MES")["id"] == "CON.F.US.MES.Z26"
    assert pick_front_month(contract_rows, "NQ") is None


def test_contract_labels_and_roots():
    assert contract_label("CON.F.US.MNQ.Z26") == "MNQ DEC26"
    assert contract_label("CON.F.US.ENQ.H27") == "NQ MAR27"
    assert display_root("F.US.EP") == "ES"


def test_signalr_framing():
    assert HANDSHAKE.endswith(RS)
    frame = encode(invocation("SubscribeContractTrades", "CON.F.US.EP.Z26")) + encode({"type": 6})
    messages = decode(frame)
    assert messages[0] == {"type": 1, "target": "SubscribeContractTrades", "arguments": ["CON.F.US.EP.Z26"]}
    assert messages[1] == {"type": 6}
    assert decode("{}" + RS + "not json" + RS) == [{}]
    url = hub_ws_url("https://rtc.example.test/hubs/market", "abc.def")
    assert url == "wss://rtc.example.test/hubs/market?access_token=abc.def"


def test_login_then_search_uses_bearer_token():
    seen: list[tuple[str, dict, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        seen.append((request.url.path, body, request.headers.get("Authorization")))
        if request.url.path == "/api/Auth/loginKey":
            return httpx.Response(200, json={"token": "jwt-1", "success": True, "errorCode": 0})
        return httpx.Response(200, json={"contracts": [], "success": True, "errorCode": 0})

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with ProjectXClient("user", "key", base_url="https://api.example.test", http=http) as px:
                assert await px.search_contracts("ES") == []

    asyncio.run(go())
    assert seen[0][0] == "/api/Auth/loginKey" and seen[0][1] == {"userName": "user", "apiKey": "key"}
    assert seen[1] == ("/api/Contract/search", {"searchText": "ES", "live": False}, "Bearer jwt-1")


def test_token_refresh_uses_validate_after_20h():
    now = [0.0]
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/Auth/loginKey":
            return httpx.Response(200, json={"token": "jwt-1", "success": True, "errorCode": 0})
        if request.url.path == "/api/Auth/validate":
            return httpx.Response(200, json={"newToken": "jwt-2", "success": True, "errorCode": 0})
        return httpx.Response(200, json={"accounts": [], "success": True, "errorCode": 0})

    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            px = ProjectXClient("u", "k", base_url="https://api.example.test", http=http, clock=lambda: now[0])
            await px.search_accounts()
            now[0] = 21 * 3600
            return await px.token()

    assert asyncio.run(go()) == "jwt-2"
    assert paths == ["/api/Auth/loginKey", "/api/Account/search", "/api/Auth/validate"]


def test_gateway_error_body_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "errorCode": 3, "errorMessage": None})

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with ProjectXClient("u", "k", base_url="https://api.example.test", http=http) as px:
                await px.login()

    try:
        asyncio.run(go())
    except Exception as exc:
        assert "errorCode 3" in str(exc)
    else:
        raise AssertionError("expected ProjectXError")


def test_discord_sink_posts_content_only():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(204)

    trade = BigTrade("ES", "CON.F.US.EP.Z26", Side.SELL, 501, 7830, 7829.5, 7829.5, 7830, 7829.8, 6, 0, 0, "final")

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await DiscordWebhookSink("https://discord.example.test/hook", http).publish(trade, "hello")

    asyncio.run(go())
    assert captured == [{"content": "hello", "allowed_mentions": {"parse": []}}]
    assert "username" not in captured[0] and "avatar_url" not in captured[0]


def test_settings_defaults_are_examples():
    s = Settings.from_env({})
    assert s.thresholds.for_root("ES") == 200 and s.thresholds.for_root("NQ") == 100
    assert (s.join_ms, s.quiet_ms, s.publish_mode) == (0, 300, "final")
    assert fmt_price(0.005) == "0.005" and fmt_price(7830) == "7830.00"
