"""Unit tests for ``fse.projectx.bars``: windows, pacing, retries, token renewal and OQ9.

A respx side effect plays a small fake ProjectX: it answers each
``retrieveBars`` request with one bar per interval of the requested window
(newest first, as in the docs example). Credentials are fake; time is virtual,
so pacing waits cost nothing.

**Validates: Requirements 1.9, 4.3, 24.7**
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio
import respx

from fse.logio import REDACTED, Redactor
from fse.projectx.bars import (
    BARS_RATE_LIMIT,
    BARS_RATE_WINDOW_NS,
    DEFAULT_BARS_PER_WINDOW,
    DUPLICATE_BAR,
    MALFORMED_RESPONSE,
    MISALIGNED_BAR,
    RETRIEVE_BARS_PATH,
    ProjectXBars,
    bars_rate_limiter,
    request_windows,
)
from fse.projectx.models import BarHistory, format_instant
from fse.projectx.session import LOGIN_PATH, ProjectXAuthError, ProjectXSession
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, parse_rfc3339
from tests.conftest import FakeEnv
from tests.fakes.clock import FakeClock

BASE = "https://api.projectx.invalid"
LOGIN_URL = BASE + LOGIN_PATH
BARS_URL = BASE + RETRIEVE_BARS_PATH
S = NS_PER_SECOND
M = NS_PER_MINUTE
T0 = parse_rfc3339("2026-03-02T23:00:00Z")  # 18:00 New York, a trading-day start
TOKEN = "fake-session-token-0000"
TOKEN_2 = "fake-session-token-0001"
CONTRACT_ID = "CON.F.US.MES.H26"


def login_ok(token: str) -> httpx.Response:
    return httpx.Response(200, json={"token": token, "success": True, "errorCode": 0})


def bars_body(times: list[Instant], *, price: float = 6065.25) -> dict[str, object]:
    bars = [
        {
            "t": format_instant(t).replace("Z", "+00:00"),
            "o": price,
            "h": price + 1,
            "l": price - 1,
            "c": price + 0.5,
            "v": 10,
        }
        for t in sorted(times, reverse=True)
    ]
    return {"bars": bars, "success": True, "errorCode": 0, "errorMessage": None}


@dataclass
class FakeGateway:
    """Answers retrieveBars from the request body; records each send instant."""

    clock: FakeClock
    sent_at: list[Instant] = field(default_factory=list)
    bodies: list[dict[str, object]] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    inclusive_end: bool = True  # also return the bar that opens at endTime

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent_at.append(self.clock.now())
        body = json.loads(request.content)
        self.bodies.append(body)
        self.tokens.append(request.headers["authorization"])
        lo = parse_rfc3339(str(body["startTime"]))
        hi = parse_rfc3339(str(body["endTime"]))
        unit_s = {1: 1, 2: 60}[int(body["unit"])] * int(body["unitNumber"])
        step = unit_s * S
        times = list(range(lo, hi + (step if self.inclusive_end else 0), step))
        return httpx.Response(200, json=bars_body(times))


@pytest.fixture
def env(fake_secrets: FakeEnv) -> EnvView:
    return EnvView(dict(fake_secrets.values), {})


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0 + 7 * 24 * 3600 * S)  # a week later: history, not live


@pytest_asyncio.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def make_bars(
    env: EnvView,
    http: httpx.AsyncClient,
    clock: FakeClock,
    redactor: Redactor | None = None,
    *,
    live: bool = False,
    bars_per_request: int = 20_000,
) -> ProjectXBars:
    session = ProjectXSession(env, redactor or Redactor(), http, clock, base_url=BASE)
    return ProjectXBars(
        session, clock, random.Random(7), live=live, bars_per_request=bars_per_request
    )


async def fetch(
    client: ProjectXBars, clock: FakeClock, *, minutes: int = 5, interval_s: int = 60
) -> BarHistory:
    return await clock.run(
        client.retrieve(
            instrument="MES",
            contract="MESH6",
            contract_id=CONTRACT_ID,
            start_ns=T0,
            end_ns=T0 + minutes * M,
            interval_s=interval_s,
        )
    )


def opens(history: BarHistory) -> list[Instant]:
    return [b.open_ns for b in history.bars]


# ---------------------------------------------------------------- request shape


@pytest.mark.asyncio
async def test_one_minute_bars_request_and_normalization(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock)
    respx_router.post(BARS_URL).mock(side_effect=gateway)
    history = await fetch(make_bars(env, http, clock), clock)

    assert gateway.bodies == [
        {
            "contractId": CONTRACT_ID,
            "live": False,
            "startTime": "2026-03-02T23:00:00Z",
            "endTime": "2026-03-02T23:05:00Z",
            "unit": 2,
            "unitNumber": 1,
            "limit": 5,
            "includePartialBar": False,
        }
    ]
    assert gateway.tokens == [f"Bearer {TOKEN}"]
    # Oldest first; the bar opening at endTime would close after it and is dropped.
    assert opens(history) == [T0 + i * M for i in range(5)]
    first = history.bars[0]
    assert (first.close_ns, first.source, first.contract, first.instrument) == (
        T0 + M,
        "projectx",
        "MESH6",
        "MES",
    )
    assert (first.o, first.o_t, first.h_t, first.l_t, first.c_t) == (
        6065.25,
        24261,
        24265,
        24257,
        24263,
    )
    assert history.complete
    assert history.requests == 1


@pytest.mark.asyncio
async def test_bar_interval_5_asks_for_second_bars(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock)
    respx_router.post(BARS_URL).mock(side_effect=gateway)
    history = await fetch(make_bars(env, http, clock), clock, minutes=2, interval_s=5)

    assert (gateway.bodies[0]["unit"], gateway.bodies[0]["unitNumber"]) == (1, 5)
    assert gateway.bodies[0]["limit"] == 24
    assert opens(history) == [T0 + i * 5 * S for i in range(24)]
    assert all(b.interval_s == 5 and b.close_ns - b.open_ns == 5 * S for b in history.bars)


@pytest.mark.asyncio
async def test_live_flag_is_sent(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock)
    respx_router.post(BARS_URL).mock(side_effect=gateway)
    await fetch(make_bars(env, http, clock, live=True), clock, minutes=1)
    assert gateway.bodies[0]["live"] is True


# ---------------------------------------------------------------- windows


def test_request_windows_are_contiguous_and_bounded() -> None:
    assert request_windows(T0, T0 + 5 * M, M, 2) == [
        (T0, T0 + 2 * M),
        (T0 + 2 * M, T0 + 4 * M),
        (T0 + 4 * M, T0 + 5 * M),
    ]
    assert request_windows(T0, T0 + M, M, 20_000) == [(T0, T0 + M)]


@pytest.mark.asyncio
@pytest.mark.parametrize("inclusive_end", [True, False])
async def test_windows_join_without_gaps_or_duplicates(
    respx_router: respx.MockRouter,
    env: EnvView,
    http: httpx.AsyncClient,
    clock: FakeClock,
    inclusive_end: bool,
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock, inclusive_end=inclusive_end)
    respx_router.post(BARS_URL).mock(side_effect=gateway)
    history = await fetch(make_bars(env, http, clock, bars_per_request=2), clock, minutes=5)

    assert [b["limit"] for b in gateway.bodies] == [2, 2, 1]
    assert [b["startTime"] for b in gateway.bodies] == [
        format_instant(T0 + i * M) for i in (0, 2, 4)
    ]
    assert opens(history) == [T0 + i * M for i in range(5)]
    assert history.requests == 3


@pytest.mark.asyncio
async def test_off_grid_range_is_rejected_before_any_request(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    route = respx_router.post(BARS_URL)
    client = make_bars(env, http, clock)
    with pytest.raises(ValueError, match="bar grid"):
        await client.retrieve(
            instrument="MES",
            contract="MESH6",
            contract_id=CONTRACT_ID,
            start_ns=T0 + S,
            end_ns=T0 + M,
            interval_s=60,
        )
    with pytest.raises(ValueError, match="after"):
        await client.retrieve(
            instrument="MES",
            contract="MESH6",
            contract_id=CONTRACT_ID,
            start_ns=T0,
            end_ns=T0,
            interval_s=60,
        )
    assert route.call_count == 0


# ---------------------------------------------------------------- pacing


@pytest.mark.asyncio
async def test_requests_stay_under_50_per_30_seconds(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock, inclusive_end=False)
    respx_router.post(BARS_URL).mock(side_effect=gateway)
    history = await fetch(make_bars(env, http, clock, bars_per_request=1), clock, minutes=120)

    sends = gateway.sent_at
    assert len(sends) == 120
    assert history.complete
    # No rolling 30 s window holds more than 45 sends, so never 50.
    n = DEFAULT_BARS_PER_WINDOW
    assert all(sends[i + n] - sends[i] >= BARS_RATE_WINDOW_NS for i in range(len(sends) - n))
    assert sends[n - 1] == sends[0]  # the first 45 go out at once
    assert sends[-1] - sends[0] == 2 * BARS_RATE_WINDOW_NS  # 120 sends need two waits


@pytest.mark.parametrize("sends", [0, BARS_RATE_LIMIT, BARS_RATE_LIMIT + 1])
def test_limiter_must_stay_under_the_documented_limit(sends: int) -> None:
    with pytest.raises(ValueError, match="under"):
        bars_rate_limiter(FakeClock(T0), sends)


# ---------------------------------------------------------------- token renewal and auth


@pytest.mark.asyncio
async def test_401_renews_the_token_once_and_resends(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    login = respx_router.post(LOGIN_URL).mock(side_effect=[login_ok(TOKEN), login_ok(TOKEN_2)])
    gateway = FakeGateway(clock)
    replies: Iterator[Callable[[httpx.Request], httpx.Response]] = iter(
        [lambda _req: httpx.Response(401), gateway]
    )
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return next(replies)(request)

    respx_router.post(BARS_URL).mock(side_effect=answer)
    redactor = Redactor()
    history = await fetch(make_bars(env, http, clock, redactor), clock, minutes=1)

    assert seen == [f"Bearer {TOKEN}", f"Bearer {TOKEN_2}"]
    assert login.call_count == 2
    assert history.complete
    assert len(history.bars) == 1
    assert history.requests == 2
    assert redactor.redact(f"{TOKEN} {TOKEN_2}") == f"{REDACTED} {REDACTED}"


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses", [(401, 401), (403,)])
async def test_refused_access_raises_exit_3_without_secrets(
    respx_router: respx.MockRouter,
    env: EnvView,
    http: httpx.AsyncClient,
    clock: FakeClock,
    fake_secrets: FakeEnv,
    statuses: tuple[int, ...],
) -> None:
    respx_router.post(LOGIN_URL).mock(side_effect=[login_ok(TOKEN), login_ok(TOKEN_2)])
    respx_router.post(BARS_URL).mock(side_effect=[httpx.Response(s) for s in statuses])
    with pytest.raises(ProjectXAuthError) as info:
        await fetch(make_bars(env, http, clock), clock, minutes=1)
    assert info.value.exit_code == 3
    assert f"HTTP {statuses[-1]}" in str(info.value)
    text = f"{info.value} {info.value!r}"
    for value in (*fake_secrets.secret_values(), TOKEN, TOKEN_2):
        assert value not in text


# ---------------------------------------------------------------- retries and failures


@pytest.mark.asyncio
async def test_5xx_is_retried_with_backoff(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock)
    replies: Iterator[Callable[[httpx.Request], httpx.Response]] = iter(
        [lambda _req: httpx.Response(503), gateway]
    )
    respx_router.post(BARS_URL).mock(side_effect=lambda req: next(replies)(req))
    history = await fetch(make_bars(env, http, clock), clock, minutes=1)
    assert history.complete
    assert history.requests == 2
    assert any(0 <= wait <= 2 * S for wait in clock.sleeps)  # U(0, min(30 s, 2^1 s))


@pytest.mark.asyncio
async def test_429_holds_every_bar_request_for_retry_after(
    respx_router: respx.MockRouter, env: EnvView, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock)
    times: list[Instant] = []

    def answer(request: httpx.Request) -> httpx.Response:
        times.append(clock.now())
        if len(times) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return gateway(request)

    respx_router.post(BARS_URL).mock(side_effect=answer)
    client = make_bars(env, http, clock)
    history = await fetch(client, clock, minutes=1)
    assert history.complete
    assert times[1] - times[0] == 7 * S
    assert client.limiter.paused_until == times[0] + 7 * S


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "cause", "attempts"),
    [
        (httpx.Response(500), "HTTP 500", 5),
        (httpx.ConnectError("fake"), "ConnectError", 5),
        (httpx.ReadTimeout("fake"), "ReadTimeout", 5),
        (httpx.Response(404), "HTTP 404", 1),
        (
            httpx.Response(200, json={"bars": None, "success": False, "errorCode": 1}),
            "errorCode 1",
            1,
        ),
        (httpx.Response(200, text="not json"), MALFORMED_RESPONSE, 1),
        (
            httpx.Response(200, json={"bars": "x", "success": True, "errorCode": 0}),
            MALFORMED_RESPONSE,
            1,
        ),
        (httpx.Response(200, json=bars_body([T0 + S])), MISALIGNED_BAR, 1),
        (httpx.Response(200, json=bars_body([T0, T0])), DUPLICATE_BAR, 1),
    ],
)
async def test_failed_window_is_recorded_and_the_next_window_is_fetched(
    respx_router: respx.MockRouter,
    env: EnvView,
    http: httpx.AsyncClient,
    clock: FakeClock,
    reply: httpx.Response | Exception,
    cause: str,
    attempts: int,
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=login_ok(TOKEN))
    gateway = FakeGateway(clock)

    def answer(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["startTime"] == format_instant(T0):
            if isinstance(reply, Exception):
                raise reply
            return reply
        return gateway(request)

    respx_router.post(BARS_URL).mock(side_effect=answer)
    history = await fetch(make_bars(env, http, clock, bars_per_request=2), clock, minutes=4)

    assert opens(history) == [T0 + 2 * M, T0 + 3 * M]  # nothing synthesized for the failure
    assert not history.complete
    failure = history.last_failure
    assert failure is not None
    assert (failure.window_start_ns, failure.window_end_ns, failure.cause) == (
        T0,
        T0 + 2 * M,
        cause,
    )
    assert history.requests == attempts + 1
