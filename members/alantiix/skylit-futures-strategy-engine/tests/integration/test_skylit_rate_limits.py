"""Integration test: rate limits, retries and stops through the real ``SkylitClient``.

Every request is answered by respx and the key is a fake value; nothing
reaches Skylit. Time is virtual (``FakeClock``): it moves only when every task
is asleep, and a mocked request takes no virtual time, so each response is
received at the instant its request was sent. Each mock records the virtual
instant a request reaches it (the "wire"), which is checked against the
client's own Fetch_Log.

Sequences: 429 with ``Retry-After``, with ``X-RateLimit-Reset`` only and with
neither; the low-water pause; 5xx, network and timeout retries and the 5th
failure; the rolling 60 s window; and the 401/402/403 stop.

**Validates: Requirements 2.3, 2.5, 2.6, 2.7, 2.8, 2.10**
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit.client import (
    EXIT_CREDENTIALS,
    ClientConfig,
    Failed,
    Response,
    SkylitClient,
    SkylitStoppedError,
)
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

KEY = "fake-skylit-key-0000"
SEED = 7
T0_S = 1_772_721_000
T0 = T0_S * NS_PER_SECOND
S = NS_PER_SECOND
MINUTE = 60 * S

SYMBOLS = "/v1/symbols"
CONFIG = "/v1/config"  # on the Atlas host: a second host shares the one limiter

type Answer = httpx.Response | type[httpx.TransportError]


def account(rpm: int = 600, historical_in_flight: int = 1) -> httpx.Response:
    limits = {"requestsPerMinute": rpm, "historicalInFlight": historical_in_flight}
    body = {
        "data": {
            "customerId": "fake-customer-0000",
            "status": "active",
            "apiEligible": True,
            "unlimited": False,
            "creditsBalance": 4321,
            "limits": limits,
        }
    }
    return httpx.Response(200, json=body)


def ok(headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"data": {}}, headers=headers)


def error(
    status: int, headers: dict[str, str] | None = None, code: str = "fake_code"
) -> httpx.Response:
    body = {"error": {"code": code, "message": "fake message"}}
    return httpx.Response(status, json=body, headers=headers)


class Wire:
    """respx side effects that record when (virtual time) each request reaches the mock."""

    def __init__(self, router: respx.MockRouter, clock: FakeClock) -> None:
        self._router = router
        self._clock = clock
        self.sent: list[tuple[str, Instant]] = []

    def serve(self, path: str, *answers: Answer, host: Host = Host.API) -> respx.Route:
        """Answer ``GET path`` with ``answers`` in order; the last one repeats."""
        queue = list(answers)

        def respond(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == f"Bearer {KEY}"
            self.sent.append((request.url.path, self._clock.now()))
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(answer, type):
                raise answer("fake transport failure", request=request)
            # A fresh copy each time, so no response object is reused.
            return httpx.Response(
                answer.status_code, headers=answer.headers, content=answer.content
            )

        return self._router.route(method="GET", scheme="https", host=host.value, path=path).mock(
            side_effect=respond
        )

    def times(self, path: str) -> list[Instant]:
        return [t for p, t in self.sent if p == path]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def wire(respx_router: respx.MockRouter, clock: FakeClock) -> Wire:
    return Wire(respx_router, clock)


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / FETCH_LOG_FILE_NAME


@pytest.fixture
def fetch_log(log_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), log_path) as log:
        yield log


def make_client(
    fetch_log: FetchLog, clock: FakeClock, cfg: ClientConfig | None = None
) -> SkylitClient:
    env = EnvView({"SKYLIT_API_KEY": KEY}, {})
    return SkylitClient(env, cfg or ClientConfig(), fetch_log, clock, random.Random(SEED))


def read_log(path: Path) -> list[dict[str, Any]]:
    """The Fetch_Log lines; the fake key must not appear anywhere in the file."""
    text = path.read_text(encoding="utf-8")
    assert KEY not in text
    return [json.loads(line) for line in text.splitlines()]


async def until(predicate: Callable[[], bool]) -> None:
    """Yield to the loop until ``predicate()`` holds; no virtual time passes here."""
    for _ in range(1_000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------- 429 (Req 2.6)


@pytest.mark.parametrize(
    ("headers", "base_wait_ns", "jittered"),
    [
        pytest.param({"Retry-After": "7"}, 7 * S, False, id="retry-after"),
        pytest.param({"X-RateLimit-Reset": str(T0_S + 20)}, 20 * S, True, id="reset-only"),
        pytest.param({}, MINUTE, True, id="neither"),
    ],
)
async def test_429_holds_every_request_then_retries(
    wire: Wire,
    fetch_log: FetchLog,
    log_path: Path,
    clock: FakeClock,
    headers: dict[str, str],
    base_wait_ns: int,
    jittered: bool,
) -> None:
    wire.serve("/v1/account", account())
    wire.serve(SYMBOLS, error(429, headers, code="rate_limited"), ok())
    wire.serve(CONFIG, ok(), host=Host.ATLAS)

    async with make_client(fetch_log, clock) as client:

        async def scenario() -> tuple[Response | Failed, Response | Failed]:
            first = asyncio.create_task(client.get(Host.API, SYMBOLS))
            # Issue a second request, on another host, once the 429 hold is in force.
            await until(lambda: client.limiter.paused_until is not None)
            other = await client.get(Host.ATLAS, CONFIG)
            return await first, other

        retried, other = await clock.run(scenario())

    log = read_log(log_path)
    rate_limited = log[1]
    assert (rate_limited["path"], rate_limited["status"], rate_limited["outcome"]) == (
        SYMBOLS,
        429,
        "retry",
    )
    wait = rate_limited["retry_wait_ns"]
    if jittered:
        assert base_wait_ns <= wait <= base_wait_ns + S  # + U(0, 1 s)
    else:
        assert wait == base_wait_ns  # Retry-After is used as is
    resume = T0 + wait
    assert client.limiter.paused_until == resume
    # Nothing at all is sent during the hold; the retry and the other host go at its end.
    assert wire.sent == [
        ("/v1/account", T0),
        (SYMBOLS, T0),
        (SYMBOLS, resume),
        (CONFIG, resume),
    ]
    assert isinstance(retried, Response)
    assert (retried.status, retried.attempts) == (200, 2)
    assert isinstance(other, Response)
    assert other.attempts == 1
    assert [(e["path"], e["attempt"], e["sent_at"], e["outcome"]) for e in log[2:]] == [
        (SYMBOLS, 2, resume, "ok"),
        (CONFIG, 1, resume, "ok"),
    ]


# ---------------------------------------------------------------- low water (Req 2.5)


@pytest.mark.parametrize(
    ("low_water", "headers", "pause_ns"),
    [
        pytest.param(
            5,
            {"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": str(T0_S + 30)},
            30 * S,
            id="at-mark-until-reset",
        ),
        pytest.param(5, {"X-RateLimit-Remaining": "0"}, MINUTE, id="no-reset-60s"),
        pytest.param(
            5,
            {"X-RateLimit-Remaining": "6", "X-RateLimit-Reset": str(T0_S + 30)},
            0,
            id="above-mark",
        ),
        pytest.param(10, {"X-RateLimit-Remaining": "8"}, MINUTE, id="configured-mark"),
    ],
)
async def test_low_water_pauses_requests_on_every_host(
    wire: Wire,
    fetch_log: FetchLog,
    clock: FakeClock,
    low_water: int,
    headers: dict[str, str],
    pause_ns: int,
) -> None:
    wire.serve("/v1/account", account())
    wire.serve(SYMBOLS, ok(headers))
    wire.serve(CONFIG, ok(), host=Host.ATLAS)

    async with make_client(fetch_log, clock, ClientConfig(low_water=low_water)) as client:

        async def scenario() -> Response | Failed:
            await client.get(Host.API, SYMBOLS)
            return await client.get(Host.ATLAS, CONFIG)

        later = await clock.run(scenario())

    assert isinstance(later, Response)
    assert wire.sent == [("/v1/account", T0), (SYMBOLS, T0), (CONFIG, T0 + pause_ns)]
    assert client.limiter.paused_until == (T0 + pause_ns if pause_ns else None)


async def test_low_water_holds_a_retry_beyond_its_backoff(
    wire: Wire, fetch_log: FetchLog, log_path: Path, clock: FakeClock
) -> None:
    low = {"X-RateLimit-Remaining": "2", "X-RateLimit-Reset": str(T0_S + 30)}
    wire.serve("/v1/account", account())
    wire.serve(SYMBOLS, error(503, low), ok())

    async with make_client(fetch_log, clock) as client:
        result = await clock.run(client.get(Host.API, SYMBOLS))

    assert isinstance(result, Response)
    assert result.attempts == 2
    backoff = read_log(log_path)[1]["retry_wait_ns"]
    assert 0 <= backoff <= 2 * S < 30 * S
    assert wire.times(SYMBOLS) == [T0, T0 + 30 * S]  # the pause, not the backoff, decides


# ---------------------------------------------------------------- 5xx (Req 2.7, 2.8)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_retries_after_its_own_backoff_only(
    wire: Wire, fetch_log: FetchLog, log_path: Path, clock: FakeClock, status: int
) -> None:
    wire.serve("/v1/account", account())
    wire.serve(SYMBOLS, error(status), ok())
    wire.serve(CONFIG, ok(), host=Host.ATLAS)

    async with make_client(fetch_log, clock) as client:

        async def scenario() -> tuple[Response | Failed, Response | Failed]:
            first = asyncio.create_task(client.get(Host.API, SYMBOLS))
            await until(lambda: bool(clock.sleeps))  # the first request is backing off
            other = await client.get(Host.ATLAS, CONFIG)
            return await first, other

        retried, other = await clock.run(scenario())

    failed_attempt = read_log(log_path)[1]
    assert (failed_attempt["status"], failed_attempt["outcome"]) == (status, "retry")
    backoff = failed_attempt["retry_wait_ns"]
    assert 0 < backoff <= 2 * S  # U(0, min(30 s, 1 s x 2^1))
    assert clock.sleeps == [backoff]
    assert isinstance(retried, Response)
    assert retried.attempts == 2
    assert isinstance(other, Response)
    # The backoff delays only the failed request; the other host is sent at once.
    assert wire.sent == [
        ("/v1/account", T0),
        (SYMBOLS, T0),
        (CONFIG, T0),
        (SYMBOLS, T0 + backoff),
    ]
    assert client.limiter.paused_until is None


@pytest.mark.parametrize(
    ("answer", "cause", "status", "error_type"),
    [
        pytest.param(error(503), "HTTP 503 (fake_code)", 503, None, id="503"),
        pytest.param(error(429), "HTTP 429 (fake_code)", 429, None, id="429"),
        pytest.param(httpx.ConnectError, "ConnectError", None, "ConnectError", id="network"),
        pytest.param(httpx.ReadTimeout, "ReadTimeout", None, "ReadTimeout", id="timeout"),
    ],
)
async def test_fifth_failure_is_logged_and_the_next_request_runs(
    wire: Wire,
    fetch_log: FetchLog,
    log_path: Path,
    clock: FakeClock,
    answer: Answer,
    cause: str,
    status: int | None,
    error_type: str | None,
) -> None:
    wire.serve("/v1/account", account())
    wire.serve(SYMBOLS, answer)
    wire.serve(CONFIG, ok(), host=Host.ATLAS)

    async with make_client(fetch_log, clock) as client:

        async def scenario() -> tuple[Response | Failed, Response | Failed]:
            failed = await client.get(Host.API, SYMBOLS, {"symbols": "SPX"})
            return failed, await client.get(Host.ATLAS, CONFIG)

        failed, following = await clock.run(scenario())

    assert isinstance(failed, Failed)
    assert (failed.outcome, failed.attempts, failed.cause) == ("failed", 5, cause)
    attempts = [e for e in read_log(log_path) if e["path"] == SYMBOLS]
    assert [(e["attempt"], e["outcome"]) for e in attempts] == [
        (1, "retry"),
        (2, "retry"),
        (3, "retry"),
        (4, "retry"),
        (5, "failed"),
    ]
    assert all((e["status"], e["error_type"]) == (status, error_type) for e in attempts)
    assert all(e["params"] == {"symbols": "SPX"} for e in attempts)
    assert attempts[-1]["retry_wait_ns"] is None
    sent = wire.times(SYMBOLS)
    assert [e["sent_at"] for e in attempts] == sent
    for n, entry in enumerate(attempts[:-1], start=1):
        wait = entry["retry_wait_ns"]
        if status == 429:
            assert MINUTE <= wait <= MINUTE + S  # 60 s + U(0, 1 s)
        else:
            assert 0 <= wait <= min(30 * S, S * 2**n)  # U(0, min(30 s, 1 s x 2^n))
        assert sent[n] == sent[n - 1] + wait
    # The client moves on: the next request is sent once the last wait is over.
    assert isinstance(following, Response)
    assert wire.times(CONFIG) == [sent[-1]]


# ---------------------------------------------------------------- rolling window (Req 2.3)


async def test_rolling_window_counts_every_attempt_retries_included(
    wire: Wire, fetch_log: FetchLog, clock: FakeClock
) -> None:
    wire.serve("/v1/account", account(rpm=3))
    wire.serve(SYMBOLS, error(503), ok())  # the first symbols attempt is retried

    async with make_client(fetch_log, clock) as client:

        async def scenario() -> list[Response | Failed]:
            return list(await asyncio.gather(*(client.get(Host.API, SYMBOLS) for _ in range(5))))

        results = await clock.run(scenario())

    assert all(isinstance(r, Response) for r in results)
    assert sum(r.attempts for r in results) == 6
    times = sorted(t for _, t in wire.sent)  # the bootstrap counts too
    assert times == [T0] * 3 + [T0 + MINUTE] * 3 + [T0 + 2 * MINUTE]
    # At most 3 sends in any half-open 60 s window.
    assert all(times[i + 3] - times[i] >= MINUTE for i in range(len(times) - 3))


# ---------------------------------------------------------------- stop (Req 2.10)


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "unauthorized"), (402, "insufficient_credits"), (403, "forbidden")],
)
async def test_stop_also_fails_requests_waiting_for_a_send_slot(
    wire: Wire,
    fetch_log: FetchLog,
    log_path: Path,
    clock: FakeClock,
    status: int,
    code: str,
) -> None:
    # 2 requests per minute: after the bootstrap and the first request, the
    # second waits 60 s for a slot. The stop arrives while it waits.
    wire.serve("/v1/account", account(rpm=2))
    wire.serve(SYMBOLS, error(status, code=code))
    wire.serve(CONFIG, ok(), host=Host.ATLAS)

    async with make_client(fetch_log, clock) as client:

        async def scenario() -> list[Response | Failed | BaseException]:
            return list(
                await asyncio.gather(
                    client.get(Host.API, SYMBOLS),
                    client.get(Host.ATLAS, CONFIG),
                    return_exceptions=True,
                )
            )

        stopped, waiting = await clock.run(scenario())
        with pytest.raises(SkylitStoppedError) as later:
            await client.get(Host.ATLAS, CONFIG)

    assert isinstance(stopped, SkylitStoppedError)
    assert isinstance(waiting, SkylitStoppedError)
    message = str(stopped)
    assert stopped.exit_code == EXIT_CREDENTIALS
    assert f"HTTP {status}" in message
    assert code in message
    assert KEY not in message
    assert str(waiting) == str(later.value) == message
    assert wire.sent == [("/v1/account", T0), (SYMBOLS, T0)]  # nothing after the stop
    assert client.attempts_sent == 2
    log = read_log(log_path)
    assert [(e["path"], e["status"], e["outcome"]) for e in log] == [
        ("/v1/account", 200, "ok"),
        (SYMBOLS, status, "stopped"),
    ]
