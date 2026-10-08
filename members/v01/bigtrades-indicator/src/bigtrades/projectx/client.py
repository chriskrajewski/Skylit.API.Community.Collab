# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Minimal async REST client for the ProjectX Gateway API.

Docs: https://gateway.docs.projectx.com/docs/intro

Auth flow:
    1. POST /api/Auth/loginKey {"userName", "apiKey"} -> {"token": <JWT>}
    2. Send "Authorization: Bearer <JWT>" on every request and as
       ``?access_token=<JWT>`` on the realtime hubs.
    3. Tokens live 24 hours. POST /api/Auth/validate returns ``newToken``;
       we refresh well before expiry and fall back to a fresh login.

Every Gateway response carries ``success`` / ``errorCode`` / ``errorMessage``.
A failed call can still be HTTP 200, so the body is always checked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.topstepx.com"
TOKEN_REFRESH_AFTER_SEC = 20 * 3600  # tokens are valid for 24h; refresh early
MAX_429_RETRIES = 3


class ProjectXError(RuntimeError):
    """The Gateway refused a request (``success`` false or a non-2xx status)."""

    def __init__(self, path: str, message: str, code: int | None = None) -> None:
        super().__init__(f"{path}: {message}" + (f" (errorCode {code})" if code is not None else ""))
        self.path = path
        self.code = code


def iso_utc(moment: datetime) -> str:
    """ISO-8601 with a Z suffix, the format the Gateway examples use."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ProjectXClient:
    """Async client. Use as ``async with ProjectXClient(...) as px:``.

    Never logs the API key or the token.
    """

    def __init__(
        self,
        username: str,
        api_key: str,
        *,
        base_url: str = DEFAULT_API_URL,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._username = username
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._http = http
        self._owns_http = http is None
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._issued_at = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> ProjectXClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("ProjectXClient used outside 'async with'")
        return self._http

    # -- auth -----------------------------------------------------------------

    async def login(self) -> str:
        body = await self._raw_post(
            "/api/Auth/loginKey",
            {"userName": self._username, "apiKey": self._api_key},
            token=None,
        )
        token = body.get("token")
        if not token:
            raise ProjectXError("/api/Auth/loginKey", "no token in response")
        self._token = str(token)
        self._issued_at = self._clock()
        log.info("ProjectX login ok")
        return self._token

    async def validate(self) -> str:
        """Exchange the current token for a fresh one (``newToken``)."""
        if not self._token:
            return await self.login()
        body = await self._raw_post("/api/Auth/validate", {}, token=self._token)
        new = body.get("newToken") or self._token
        self._token = str(new)
        self._issued_at = self._clock()
        return self._token

    async def token(self) -> str:
        """A token that is safe to use now. Logs in or refreshes as needed."""
        async with self._lock:
            if not self._token:
                return await self.login()
            if self._clock() - self._issued_at >= TOKEN_REFRESH_AFTER_SEC:
                try:
                    return await self.validate()
                except (ProjectXError, httpx.HTTPError):
                    return await self.login()
            return self._token

    # -- transport ------------------------------------------------------------

    async def _raw_post(self, path: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
        headers = {"accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = self.base_url + path
        for attempt in range(MAX_429_RETRIES + 1):
            response = await self.http.post(url, json=payload, headers=headers)
            if response.status_code == 429 and attempt < MAX_429_RETRIES:
                # Courtesy backoff. Limits: 200 req / 60 s (50 / 30 s for bars).
                wait = _retry_after(response) or 2.0 * (attempt + 1)
                log.warning("ProjectX rate limited on %s, waiting %.1fs", path, wait)
                await self._sleep(wait)
                continue
            if response.status_code == 401:
                raise ProjectXError(path, "unauthorized", 401)
            if response.status_code >= 400:
                raise ProjectXError(path, f"HTTP {response.status_code}", response.status_code)
            body = response.json()
            if not isinstance(body, dict):
                raise ProjectXError(path, "unexpected response shape")
            if body.get("success") is False or body.get("errorCode") not in (None, 0):
                raise ProjectXError(path, str(body.get("errorMessage") or "request failed"), body.get("errorCode"))
            return body
        raise ProjectXError(path, "rate limited", 429)

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Authenticated POST. Re-logs in once on HTTP 401."""
        token = await self.token()
        try:
            return await self._raw_post(path, payload, token)
        except ProjectXError as exc:
            if exc.code != 401:
                raise
            async with self._lock:
                self._token = None
            return await self._raw_post(path, payload, await self.token())

    # -- endpoints --------------------------------------------------------------

    async def search_accounts(self, only_active: bool = True) -> list[dict[str, Any]]:
        body = await self.post("/api/Account/search", {"onlyActiveAccounts": only_active})
        return list(body.get("accounts") or [])

    async def search_contracts(self, text: str, live: bool = False) -> list[dict[str, Any]]:
        body = await self.post("/api/Contract/search", {"searchText": text, "live": live})
        return list(body.get("contracts") or [])

    async def contract_by_id(self, contract_id: str) -> dict[str, Any] | None:
        body = await self.post("/api/Contract/searchById", {"contractId": contract_id})
        contract = body.get("contract")
        return contract if isinstance(contract, dict) else None

    async def search_trades(
        self, account_id: int, start: datetime, end: datetime | None = None
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"accountId": account_id, "startTimestamp": iso_utc(start)}
        if end is not None:
            payload["endTimestamp"] = iso_utc(end)
        body = await self.post("/api/Trade/search", payload)
        return list(body.get("trades") or [])

    async def search_orders(
        self, account_id: int, start: datetime, end: datetime | None = None
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"accountId": account_id, "startTimestamp": iso_utc(start)}
        if end is not None:
            payload["endTimestamp"] = iso_utc(end)
        body = await self.post("/api/Order/search", payload)
        return list(body.get("orders") or [])

    async def search_open_positions(self, account_id: int) -> list[dict[str, Any]]:
        body = await self.post("/api/Position/searchOpen", {"accountId": account_id})
        return list(body.get("positions") or [])


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
