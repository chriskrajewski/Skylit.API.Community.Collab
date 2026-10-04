"""Property 5: Pacing invariants.

*For any* schedule of requests and any sequence of simulated responses and
latencies, in any rolling 60-second window the client sends at most
``requestsPerMinute`` requests (retries included), never has more than
``historicalInFlight`` historical replay requests awaiting a response, and
sends nothing between a response at or below the low-water mark and that
response's reset time (or 60 s without a reset header).

Each example drives the real :class:`SkylitClient` in virtual time
(``FakeClock.run``) against one respx route with a fake key. A :class:`Case`
holds:

- the account limits (``requestsPerMinute`` 1 to 8, ``historicalInFlight``
  1 to 3), the fallback rate that paces the bootstrap, and the low-water mark
  (0 to 6, the default 5 often);
- the ``GET /v1/account`` script: up to 3 retryable failures, then a 200
  carrying the limits;
- 1 to 8 requests on both replay paths, ``/v1/symbols`` and Atlas
  ``/v1/history``, each with a start time and a script of up to 3 answers
  (200, 404, 429, 500/502/503/504, a network error, a 60 s timeout). An HTTP
  answer has a latency of up to 30 s and optional ``X-RateLimit-Remaining``,
  ``X-RateLimit-Reset`` (relative to receipt, past instants included) and,
  on a 429, ``Retry-After``. A request whose script runs out gets a plain 200.

The mock records what goes over the wire, independently of the client: each
attempt's send instant, the replay attempts awaiting a response, and every
response at or below the low-water mark with the pause end it asks for (the
reset instant the mock wrote, or receipt + 60 s). The checks:

1. Rolling window: for every send at ``t``, that send and the sends before it
   in ``(t - 60 s, t]`` are at most the limit in force for it. That is the fallback for the
   bootstrap's own attempts (design §2: until the bootstrap ends, the limiter
   runs at the fallback rate) and the account's ``requestsPerMinute`` for
   every later send; bootstrap sends still count in later windows.
2. Replays awaiting a response never exceed ``historicalInFlight``.
3. No send falls strictly between a low-water response's receipt and its
   pause end. A send at the receipt instant itself is concurrent, not after.

401, 402 and 403 are not generated: they stop the client (Req 2.10), and a
stopped client sends nothing, so they add no pacing case.

**Validates: Requirements 2.3, 2.4, 2.5**
"""

from __future__ import annotations

import asyncio
import random
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import httpx
import respx
from hypothesis import example, given
from hypothesis import strategies as st

from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit.client import (
    DEFAULT_FALLBACK_REQUESTS_PER_MINUTE,
    ClientConfig,
    Failed,
    Limits,
    Response,
    SkylitClient,
)
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.skylit.ratelimit import DEFAULT_LOW_WATER
from fse.timekit import NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock

KEY: Final = "fake-skylit-key-0000"
S: Final = NS_PER_SECOND
MS: Final = S // 1_000
T0: Final = 1_772_721_000 * S  # a whole Unix second
WINDOW_NS: Final = 60 * S
NO_RESET_PAUSE_NS: Final = 60 * S
TIMEOUT_S: Final = 60
ACCOUNT: Final = "account"
REPLAY_PATHS: Final = frozenset({"/v1/historical", "/v1/historical/range"})
TARGETS: Final[tuple[tuple[Host, str], ...]] = (
    (Host.API, "/v1/historical"),
    (Host.API, "/v1/historical/range"),
    (Host.API, "/v1/symbols"),
    (Host.ATLAS, "/v1/history"),
)

type Outcome = int | Literal["network", "timeout"]

RETRYABLE: Final[tuple[Outcome, ...]] = (429, 500, 502, 503, 504, "network", "timeout")
ANY_OUTCOME: Final[tuple[Outcome, ...]] = (200, 404, *RETRYABLE)


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Answer:
    """One simulated attempt outcome; header fields apply to HTTP answers only."""

    outcome: Outcome = 200
    latency_ns: int = 0
    remaining: int | None = None
    reset_in_ns: int | None = None
    """``X-RateLimit-Reset`` = receipt + this (negative = already past)."""
    retry_after_s: int | None = None


@dataclass(frozen=True, slots=True)
class Call:
    start_ns: int
    host: Host
    path: str
    answers: tuple[Answer, ...] = ()


@dataclass(frozen=True, slots=True)
class Case:
    rpm: int
    in_flight: int
    fallback_rpm: int
    low_water: int
    account: tuple[Answer, ...]
    """Retryable failures, then the 200 that carries the limits."""
    calls: tuple[Call, ...]
    seed: int = 0


@dataclass(frozen=True, slots=True)
class Send:
    at: Instant
    rid: str


@dataclass(frozen=True, slots=True)
class Pause:
    received_at: Instant
    until: Instant
    rid: str


@dataclass(slots=True)
class Observed:
    sends: list[Send] = field(default_factory=list)
    pauses: list[Pause] = field(default_factory=list)
    replays_waiting: int = 0
    peak_replays_waiting: int = 0
    limits: Limits | None = None
    attempts_sent: int = 0


# ---------------------------------------------------------------- generators

_latency = st.just(0) | st.integers(1, 30_000).map(lambda ms: ms * MS)


@st.composite
def _answers(draw: st.DrawFn, outcomes: tuple[Outcome, ...]) -> Answer:
    outcome = draw(st.sampled_from(outcomes))
    if outcome == "timeout":
        return Answer(outcome, TIMEOUT_S * S)
    if outcome == "network":
        return Answer(outcome, draw(_latency))
    return Answer(
        outcome,
        draw(_latency),
        remaining=draw(st.none() | st.integers(0, 10)),
        reset_in_ns=draw(st.none() | st.integers(-10_000, 120_000).map(lambda ms: ms * MS)),
        retry_after_s=draw(st.none() | st.integers(0, 90)) if outcome == 429 else None,
    )


@st.composite
def _calls(draw: st.DrawFn) -> Call:
    host, path = draw(st.sampled_from(TARGETS))
    start = draw(st.just(0) | st.integers(0, 120_000).map(lambda ms: ms * MS))
    answers = draw(st.lists(_answers(ANY_OUTCOME), max_size=3))
    return Call(start, host, path, tuple(answers))


@st.composite
def _cases(draw: st.DrawFn) -> Case:
    failures = draw(st.lists(_answers(RETRYABLE), max_size=3))
    return Case(
        rpm=draw(st.integers(1, 8)),
        in_flight=draw(st.integers(1, 3)),
        fallback_rpm=draw(st.just(DEFAULT_FALLBACK_REQUESTS_PER_MINUTE) | st.integers(1, 8)),
        low_water=draw(st.just(DEFAULT_LOW_WATER) | st.integers(0, 6)),
        account=(*failures, draw(_answers((200,)))),
        calls=tuple(draw(st.lists(_calls(), min_size=1, max_size=8))),
        seed=draw(st.integers(0, 2**32 - 1)),
    )


# ---------------------------------------------------------------- mock Skylit


def _account_body(case: Case) -> dict[str, Any]:
    return {
        "data": {
            "customerId": "fake-customer-0000",
            "status": "active",
            "apiEligible": True,
            "unlimited": False,
            "creditsBalance": 4321,
            "limits": {"requestsPerMinute": case.rpm, "historicalInFlight": case.in_flight},
        }
    }


class MockSkylit:
    """The respx side effect: plays each request's script and records the wire."""

    def __init__(self, case: Case, clock: FakeClock, seen: Observed) -> None:
        self._case = case
        self._clock = clock
        self._seen = seen
        self._attempts: Counter[str] = Counter()

    def _next_answer(self, rid: str) -> Answer:
        script = self._case.account if rid == ACCOUNT else self._case.calls[int(rid)].answers
        n = self._attempts[rid]
        self._attempts[rid] += 1
        return script[n] if n < len(script) else Answer()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        seen = self._seen
        path = request.url.path
        rid = ACCOUNT if path == "/v1/account" else request.url.params.get("rid", "")
        answer = self._next_answer(rid)
        seen.sends.append(Send(self._clock.now(), rid))

        replay = request.url.host == Host.API.value and path in REPLAY_PATHS
        if replay:
            seen.replays_waiting += 1
            seen.peak_replays_waiting = max(seen.peak_replays_waiting, seen.replays_waiting)
        try:
            await self._clock.sleep(answer.latency_ns)
        finally:
            if replay:
                seen.replays_waiting -= 1

        status = answer.outcome
        if status == "network":
            raise httpx.ConnectError("fake connect error", request=request)
        if status == "timeout":
            raise httpx.ReadTimeout("fake read timeout", request=request)
        assert isinstance(status, int)

        received = self._clock.now()
        headers: dict[str, str] = {}
        reset_at: Instant | None = None
        if answer.remaining is not None:
            headers["X-RateLimit-Remaining"] = str(answer.remaining)
        if answer.reset_in_ns is not None:
            reset_ms = (received + answer.reset_in_ns) // MS
            reset_at = reset_ms * MS
            headers["X-RateLimit-Reset"] = f"{reset_ms // 1_000}.{reset_ms % 1_000:03d}"
        if answer.retry_after_s is not None:
            headers["Retry-After"] = str(answer.retry_after_s)
        if answer.remaining is not None and answer.remaining <= self._case.low_water:
            until = reset_at if reset_at is not None else received + NO_RESET_PAUSE_NS
            seen.pauses.append(Pause(received, until, rid))

        if status == 200:
            body: object = _account_body(self._case) if rid == ACCOUNT else {"data": {}}
        else:
            body = {"error": {"code": "fake_error", "message": "fake message"}}
        return httpx.Response(status, json=body, headers=headers)


async def _scenario(case: Case) -> Observed:
    clock = FakeClock(T0)
    seen = Observed()
    cfg = ClientConfig(
        request_timeout_s=TIMEOUT_S,
        low_water=case.low_water,
        fallback_requests_per_minute=case.fallback_rpm,
    )
    env = EnvView({"SKYLIT_API_KEY": KEY}, {})

    async def issue(client: SkylitClient) -> None:
        async def one(i: int, call: Call) -> None:
            await clock.sleep(call.start_ns)
            result = await client.get(call.host, call.path, {"rid": str(i)})
            assert isinstance(result, Response | Failed)

        await asyncio.gather(*(one(i, call) for i, call in enumerate(case.calls)))

    with (
        tempfile.TemporaryDirectory(prefix="fse-p05-") as tmp,
        FetchLog(LogWriter(Redactor([KEY])), Path(tmp) / FETCH_LOG_FILE_NAME) as fetch_log,
        respx.mock(assert_all_mocked=True, assert_all_called=False) as router,
    ):
        router.route().mock(side_effect=MockSkylit(case, clock, seen))
        async with SkylitClient(env, cfg, fetch_log, clock, random.Random(case.seed)) as client:
            await clock.run(issue(client))
        seen.limits = client.limits
        seen.attempts_sent = client.attempts_sent
    return seen


# ---------------------------------------------------------------- checks


def _rel(t: Instant) -> str:
    return f"{(t - T0) / S:.3f}s"


def _check(case: Case, seen: Observed) -> None:
    sends = seen.sends
    # The harness saw every attempt and the bootstrap read the generated limits.
    assert seen.limits == Limits(case.rpm, case.in_flight, True, True)
    assert seen.attempts_sent == len(sends)
    assert sends[0].rid == ACCOUNT

    # 1. Rolling window, retries and the bootstrap included (Req 2.3). Sends are in wire
    # order, so the window ending at a send holds it and the sends before it in (t - 60 s, t].
    for i, send in enumerate(sends):
        limit = case.fallback_rpm if send.rid == ACCOUNT else case.rpm
        window = [s for s in sends[: i + 1] if s.at > send.at - WINDOW_NS]
        assert len(window) <= limit, (
            f"{len(window)} sends in the 60 s window ending {_rel(send.at)} (limit {limit}): "
            f"{[(_rel(s.at), s.rid) for s in window]}"
        )

    # 2. Replays awaiting a response (Req 2.4).
    assert seen.replays_waiting == 0
    assert seen.peak_replays_waiting <= case.in_flight, (
        f"{seen.peak_replays_waiting} replays awaiting a response, limit {case.in_flight}"
    )

    # 3. Low-water pauses (Req 2.5).
    for pause in seen.pauses:
        inside = [s for s in sends if pause.received_at < s.at < pause.until]
        assert not inside, (
            f"response to {pause.rid} at {_rel(pause.received_at)} paused sends until "
            f"{_rel(pause.until)}, but sent {[(_rel(s.at), s.rid) for s in inside]}"
        )


# ---------------------------------------------------------------- property

# Five replays at once: the window (2 per minute, the bootstrap counted) and one slot bind.
_BURST = Case(
    rpm=2,
    in_flight=1,
    fallback_rpm=DEFAULT_FALLBACK_REQUESTS_PER_MINUTE,
    low_water=DEFAULT_LOW_WATER,
    account=(Answer(),),
    calls=tuple(
        Call(0, Host.API, "/v1/historical/range", (Answer(200, 10 * S),)) for _ in range(5)
    ),
)
# A 60 s pause after the bootstrap, a reset-header pause, and a 429 inside the pause.
_LOW_WATER = Case(
    rpm=8,
    in_flight=2,
    fallback_rpm=3,
    low_water=DEFAULT_LOW_WATER,
    account=(Answer(503, S), Answer(200, remaining=DEFAULT_LOW_WATER)),
    calls=(
        Call(0, Host.API, "/v1/symbols", (Answer(200, S, remaining=0, reset_in_ns=45 * S),)),
        Call(0, Host.ATLAS, "/v1/history", (Answer(429, 2 * S, remaining=3, retry_after_s=1),)),
        Call(70 * S, Host.API, "/v1/historical", (Answer("timeout", TIMEOUT_S * S),)),
        Call(90 * S, Host.API, "/v1/historical", ()),
    ),
    seed=1,
)


# Feature: skylit-futures-strategy-engine, Property 5: Pacing invariants
@given(case=_cases())
@example(case=_BURST)
@example(case=_LOW_WATER)
def test_pacing_invariants_hold_for_any_schedule_and_responses(case: Case) -> None:
    _check(case, asyncio.run(_scenario(case)))
