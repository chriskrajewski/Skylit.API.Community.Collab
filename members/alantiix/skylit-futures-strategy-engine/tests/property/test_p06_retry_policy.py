"""Property 6: Retry policy.

*For any* sequence of attempt outcomes (statuses 200-599, network errors,
timeouts, with or without ``Retry-After`` and ``X-RateLimit-Reset``) and any
``/v1/account`` payload, the client makes at most 5 attempts per request,
retries only 429, 500, 502, 503, 504, network errors and timeouts, waits
exactly as Requirement 2 specifies for each case (header-driven waits for 429;
a uniform wait in [0, min(30, 2^n)] s otherwise), records a terminal failure
in the Fetch_Log with the last status or error type, and uses the configured
fallback for each limit it could not read as a positive integer.

Both tests drive the real :class:`SkylitClient` against respx mocks in virtual
time (:class:`FakeClock`) with a fake key. A mocked route answers attempt n
with the n-th of 5 generated outcomes, and has a 200 ready for a 6th attempt
that must never be asked for.

1. ``test_retry_policy_per_request``: the bootstrap answers at once, then one
   ``GET`` on a sampled endpoint and host gets the generated outcomes.
2. ``test_account_bootstrap_uses_fallbacks``: ``GET /v1/account`` gets the
   generated outcomes (a 2xx carries any account payload) under generated
   fallbacks, then one ``GET /v1/symbols`` follows.

:func:`_check_request` compares each request with a reference written from
Requirement 2 alone (no constant is imported from ``fse.skylit.retry``):

- attempts stop at the first outcome that is not 429, 500, 502, 503, 504, a
  network error or a timeout, and at 5 in any case;
- every Fetch_Log line before the last is a ``retry`` of a retryable outcome.
  Its ``retry_wait_ns`` lies within the Requirement 2 bounds and equals the
  gap between the two sends seen on the wire. Bounds: a 429 with
  ``Retry-After`` (delta-seconds or HTTP-date) waits exactly that delay; with
  ``X-RateLimit-Reset`` only, from reset - now to reset - now + 1 s (never
  below 0); with neither, 60 to 61 s. Anything else waits 0 to
  min(30, 2^n) s, a 503 with ``Retry-After`` included (Req 2.7, by design);
- the last line is ``ok``, ``stopped``, ``rejected`` or ``failed`` as the
  reference says, with the last status and error code (or error type), the
  host, the path and the query parameters, and the returned value agrees.

Limits (Req 2.2): each of ``requestsPerMinute`` and ``historicalInFlight`` is
the account value when a 2xx body holds it as a positive integer, else the
configured fallback. Integral floats such as ``60.0`` are not generated:
JSON does not say whether they are integers.

Jitter uniformity is not a per-example property, so only its range is
checked. Responses carry no ``X-RateLimit-Remaining`` (the low-water pause is
Property 5), and the limits in force while retries run are at least 5 per
minute, so neither the pause nor the rolling window can delay a retry.

**Validates: Requirements 2.2, 2.6, 2.7, 2.8, 2.9**
"""

from __future__ import annotations

import asyncio
import json
import random
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.utils import formatdate
from pathlib import Path
from typing import Any, Final, Literal

import httpx
import respx
from hypothesis import example, given
from hypothesis import strategies as st

from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit.client import (
    ClientConfig,
    Failed,
    Limits,
    Response,
    SkylitClient,
    SkylitStoppedError,
)
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock

KEY: Final = "fake-skylit-key-0000"
S: Final = NS_PER_SECOND
T0: Final = 1_772_721_000 * S

# Requirement 2, restated.
MAX_ATTEMPTS: Final = 5
RATE_LIMITED: Final = 429
SERVER_ERRORS: Final = (500, 502, 503, 504)
RETRYABLE: Final = frozenset({RATE_LIMITED, *SERVER_ERRORS})
STOP: Final = frozenset({401, 402, 403})
BACKOFF_CAP_S: Final = 30
NO_HEADER_WAIT_NS: Final = 60 * S
JITTER_NS: Final = S

ACCOUNT_PATH: Final = "/v1/account"
FOLLOW_UP_PATH: Final = "/v1/symbols"
LIMIT_NAMES: Final = ("requestsPerMinute", "historicalInFlight")

TRANSPORT_ERRORS: Final[tuple[type[httpx.TransportError], ...]] = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)

type Action = Literal["ok", "stopped", "rejected", "failed"]
type Bounds = tuple[int, int]
type Line = dict[str, Any]


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Target:
    """One request: host, path and query parameters."""

    host: Host
    path: str
    params: tuple[tuple[str, str], ...] = ()


TARGETS: Final = (
    Target(Host.API, "/v1/symbols"),
    Target(
        Host.API,
        "/v1/historical/range",
        (
            ("symbols", "SPX"),
            ("from", "2026-03-05T14:30:00Z"),
            ("to", "2026-03-05T14:45:00Z"),
            ("metric", "gamma"),
        ),
    ),
    Target(
        Host.API,
        "/v1/historical",
        (("symbols", "SPX,SPY"), ("at", "2026-03-05T14:30:00Z"), ("metric", "vanna")),
    ),
    Target(
        Host.API,
        "/v1/dark-pool/trades",
        (("tickers", "SPY"), ("dateStart", "2026-03-02"), ("dateEnd", "2026-03-05")),
    ),
    Target(
        Host.ATLAS,
        "/v1/history",
        (("symbol", "ES"), ("resolution", "1"), ("from", "1772721000"), ("to", "1772724600")),
    ),
)


@dataclass(frozen=True, slots=True)
class RetryAfter:
    """``delta``: that many seconds. ``date``: an HTTP-date that many seconds past now's second."""

    form: Literal["delta", "date"]
    seconds: int


@dataclass(frozen=True, slots=True)
class Answer:
    """A complete response.

    ``error_code`` is the code in the error envelope (what the Fetch_Log must
    show). ``rpm`` and ``hif`` are the usable limits in a 2xx account body.
    """

    status: int
    content: bytes = b""
    error_code: str | None = None
    retry_after: RetryAfter | None = None
    reset_in_s: int | None = None
    rpm: int | None = None
    hif: int | None = None


@dataclass(frozen=True, slots=True)
class Broken:
    """No complete response: a network error or a timeout."""

    error: type[httpx.TransportError]


type Outcome = Answer | Broken


@dataclass(frozen=True, slots=True)
class AccountBody:
    content: bytes
    rpm: int | None
    hif: int | None


def _retryable(outcome: Outcome) -> bool:
    return isinstance(outcome, Broken) or outcome.status in RETRYABLE


def _expected(outcomes: Sequence[Outcome]) -> tuple[int, Action]:
    """Attempts the request takes and how its last attempt ends (Req 2.6-2.10)."""
    for n, outcome in enumerate(outcomes[:MAX_ATTEMPTS], start=1):
        if isinstance(outcome, Broken) or outcome.status in RETRYABLE:
            continue
        if 200 <= outcome.status <= 299:
            return n, "ok"
        return n, "stopped" if outcome.status in STOP else "rejected"
    return MAX_ATTEMPTS, "failed"


def _retry_after_ns(retry_after: RetryAfter, now: Instant) -> int:
    if retry_after.form == "delta":
        return retry_after.seconds * S
    return max(0, (now // S + retry_after.seconds) * S - now)


def _allowed_wait(outcome: Outcome, n: int, now: Instant) -> Bounds | None:
    """The wait Req 2.6-2.7 allow after attempt ``n`` ended at ``now``; ``None``: no retry."""
    if n >= MAX_ATTEMPTS or not _retryable(outcome):
        return None
    if isinstance(outcome, Answer) and outcome.status == RATE_LIMITED:
        if outcome.retry_after is not None:
            exact = _retry_after_ns(outcome.retry_after, now)
            return exact, exact
        if outcome.reset_in_s is not None:
            until = (now // S + outcome.reset_in_s) * S - now
            return max(0, until), max(0, until + JITTER_NS)
        return NO_HEADER_WAIT_NS, NO_HEADER_WAIT_NS + JITTER_NS
    return 0, min(BACKOFF_CAP_S, 2**n) * S


def _headers(answer: Answer, now: Instant) -> dict[str, str]:
    headers: dict[str, str] = {}
    retry_after = answer.retry_after
    if retry_after is not None:
        if retry_after.form == "delta":
            headers["Retry-After"] = str(retry_after.seconds)
        else:
            headers["Retry-After"] = formatdate(now // S + retry_after.seconds, usegmt=True)
    if answer.reset_in_s is not None:
        headers["X-RateLimit-Reset"] = str(now // S + answer.reset_in_s)
    return headers


@dataclass
class Wire:
    """One mocked route: answers attempt n with ``outcomes[n - 1]`` and records each send."""

    clock: FakeClock
    outcomes: Sequence[Outcome]
    sends: list[Instant] = field(default_factory=list)
    allowed: list[Bounds | None] = field(default_factory=list)

    def respond(self, request: httpx.Request) -> httpx.Response:
        now = self.clock.now()
        n = len(self.sends) + 1
        self.sends.append(now)
        if n > len(self.outcomes):  # a 6th attempt; the checks reject it
            self.allowed.append(None)
            return httpx.Response(200, json={"data": {}})
        outcome = self.outcomes[n - 1]
        self.allowed.append(_allowed_wait(outcome, n, now))
        if isinstance(outcome, Broken):
            raise outcome.error("fake transport failure", request=request)
        return httpx.Response(
            outcome.status, content=outcome.content, headers=_headers(outcome, now)
        )


# ---------------------------------------------------------------- strategies

_ERROR_CODES = st.sampled_from(
    ["no_data", "bad_request", "invalid_params", "rate_limited", "internal_error", "unavailable"]
)
_CODELESS_BODIES = st.sampled_from(
    [b"", b'{"error": "fake text"}', b'{"error": {"message": "fake"}}', b"<html>fake</html>"]
)
_RETRY_AFTERS = st.one_of(
    st.builds(RetryAfter, st.just("delta"), st.integers(0, 120)),
    st.builds(RetryAfter, st.just("date"), st.integers(-10, 120)),
)
_LIMIT_VALUES: st.SearchStrategy[object] = st.one_of(
    st.integers(1, 100_000),
    st.integers(-1_000, 0),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False).filter(lambda x: not x.is_integer()),
    st.sampled_from(["60", "1", ""]),
    st.text(max_size=4),
    st.none(),
    st.lists(st.integers(1, 9), max_size=2),
    st.just({"value": 60}),
)
_JUNK: st.SearchStrategy[object] = st.one_of(
    st.none(), st.integers(), st.text(max_size=4), st.lists(st.integers(), max_size=2)
)


def _usable(value: object) -> int | None:
    """Req 2.2: a positive integer (``bool`` is not one here)."""
    return value if type(value) is int and value >= 1 else None


def _json(value: object) -> bytes:
    return json.dumps(value).encode()


@st.composite
def account_bodies(draw: st.DrawFn) -> AccountBody:
    """Any ``GET /v1/account`` body, with the limits a correct reader finds in it."""
    shape = draw(st.sampled_from(["limits", "limits", "limits", "bad_limits", "bad_data", "raw"]))
    if shape == "limits":
        limits: dict[str, object] = {}
        found: dict[str, int | None] = {}
        for name in LIMIT_NAMES:
            if draw(st.integers(0, 7)) == 0:  # left out
                found[name] = None
                continue
            value = draw(_LIMIT_VALUES)
            limits[name] = value
            found[name] = _usable(value)
        others = st.sampled_from(["symbolsPerHeatmapCall", "activeKeys", "symbolsPerStream"])
        limits.update(draw(st.dictionaries(others, st.integers(-5, 50), max_size=2)))
        data = {
            "customerId": "fake-customer-0000",
            "status": "active",
            "apiEligible": True,
            "creditsBalance": 1234,
            "limits": limits,
        }
        return AccountBody(_json({"data": data}), found[LIMIT_NAMES[0]], found[LIMIT_NAMES[1]])
    if shape == "bad_limits":
        bad: dict[str, object] = {"status": "active"}
        if draw(st.booleans()):
            bad["limits"] = draw(_JUNK)
        return AccountBody(_json({"data": bad}), None, None)
    if shape == "bad_data":
        body: dict[str, object] = {"error": "fake"} if draw(st.booleans()) else {}
        if draw(st.booleans()):
            body = {"data": draw(_JUNK)}
        return AccountBody(_json(body), None, None)
    content = draw(st.one_of(_JUNK.map(_json), st.sampled_from([b"", b"{", b"<html>fake</html>"])))
    return AccountBody(content, None, None)


def answers(
    statuses: st.SearchStrategy[int], payloads: st.SearchStrategy[AccountBody]
) -> st.SearchStrategy[Answer]:
    @st.composite
    def build(draw: st.DrawFn) -> Answer:
        status = draw(statuses)
        retry_after = draw(st.none() | _RETRY_AFTERS)
        reset_in_s = draw(st.none() | st.integers(-30, 120))
        if 200 <= status <= 299:
            body = draw(payloads)
            return Answer(status, body.content, None, retry_after, reset_in_s, body.rpm, body.hif)
        code = draw(st.none() | _ERROR_CODES)
        if code is None:
            content = draw(_CODELESS_BODIES)
        else:
            content = _json({"error": {"code": code, "message": "fake message"}})
        return Answer(status, content, code, retry_after, reset_in_s)

    return build()


def outcome_lists(payloads: st.SearchStrategy[AccountBody]) -> st.SearchStrategy[list[Outcome]]:
    """Five outcomes, about 3 in 4 of them retryable, so many requests reach attempt 5."""
    rate_limited = answers(st.just(RATE_LIMITED), payloads)
    server_error = answers(st.sampled_from(SERVER_ERRORS), payloads)
    broken = st.builds(Broken, st.sampled_from(TRANSPORT_ERRORS))
    named = answers(st.sampled_from((200, 204, 400, 401, 402, 403, 404, 422, 501, 505)), payloads)
    anything = answers(st.integers(200, 599), payloads)
    outcome: st.SearchStrategy[Outcome] = st.one_of(
        rate_limited, rate_limited, server_error, server_error, broken, broken, named, anything
    )
    return st.lists(outcome, min_size=MAX_ATTEMPTS, max_size=MAX_ATTEMPTS)


_PLAIN_OK = st.just(AccountBody(b'{"data": {}}', None, None))


# ---------------------------------------------------------------- driving the client


@dataclass(frozen=True, slots=True)
class Run:
    result: Response | Failed | SkylitStoppedError
    limits: Limits | None
    rpm_in_force: int
    lines: list[Line]


def _route(router: respx.MockRouter, host: Host, path: str) -> respx.Route:
    return router.route(method="GET", scheme="https", host=host.value, path=path)


def _account_ok(rpm: int, hif: int) -> httpx.Response:
    limits = {"requestsPerMinute": rpm, "historicalInFlight": hif}
    return httpx.Response(200, json={"data": {"status": "active", "limits": limits}})


async def _get(
    clock: FakeClock, log_path: Path, cfg: ClientConfig, seed: int, target: Target
) -> tuple[Response | Failed | SkylitStoppedError, Limits | None, int]:
    env = EnvView({"SKYLIT_API_KEY": KEY}, {})
    with FetchLog(LogWriter(Redactor([KEY])), log_path) as log:
        async with SkylitClient(env, cfg, log, clock, random.Random(seed)) as client:
            result: Response | Failed | SkylitStoppedError
            try:
                result = await clock.run(client.get(target.host, target.path, dict(target.params)))
            except SkylitStoppedError as exc:
                result = exc
            return result, client.limits, client.limiter.requests_per_minute


def _run(
    target: Target,
    cfg: ClientConfig,
    seed: int,
    clock: FakeClock,
    routes: Mapping[tuple[Host, str], Wire | httpx.Response],
) -> Run:
    with (
        tempfile.TemporaryDirectory() as tmp,
        respx.mock(assert_all_mocked=True, assert_all_called=False) as router,
    ):
        for (host, path), answer in routes.items():
            if isinstance(answer, Wire):
                _route(router, host, path).mock(side_effect=answer.respond)
            else:
                _route(router, host, path).mock(return_value=answer)
        log_path = Path(tmp) / FETCH_LOG_FILE_NAME
        result, limits, rpm = asyncio.run(_get(clock, log_path, cfg, seed, target))
        text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert KEY not in text
    return Run(result, limits, rpm, [json.loads(line) for line in text.splitlines()])


# ---------------------------------------------------------------- checks


def _lines_for(run: Run, host: Host, path: str) -> list[Line]:
    return [line for line in run.lines if (line["host"], line["path"]) == (host.value, path)]


def _check_request(wire: Wire, lines: Sequence[Line], target: Target) -> tuple[int, Action]:
    """Attempts, retries, waits and Fetch_Log lines of one request against the reference."""
    attempts, action = _expected(wire.outcomes)
    assert len(wire.sends) == attempts <= MAX_ATTEMPTS
    assert [line["attempt"] for line in lines] == list(range(1, attempts + 1))
    for n, (line, outcome, sent) in enumerate(
        zip(lines, wire.outcomes, wire.sends, strict=False), start=1
    ):
        assert (line["host"], line["path"], line["params"]) == (
            target.host.value,
            target.path,
            dict(target.params),
        )
        assert line["sent_at"] == sent
        if isinstance(outcome, Broken):
            assert (line["status"], line["error_type"]) == (None, outcome.error.__name__)
        else:
            assert (line["status"], line["error_type"], line["error_code"]) == (
                outcome.status,
                None,
                outcome.error_code,
            )
        if n < attempts:
            bounds = wire.allowed[n - 1]
            assert bounds is not None, "only a retryable outcome before attempt 5 is retried"
            assert line["outcome"] == "retry"
            wait = line["retry_wait_ns"]
            assert bounds[0] <= wait <= bounds[1], (outcome, n, wait, bounds)
            assert wire.sends[n] - sent == wait, "the client waited other than it logged"
        else:
            assert line["outcome"] == action
            assert line["retry_wait_ns"] is None
    return attempts, action


def _check_result(
    result: Response | Failed | SkylitStoppedError,
    last: Outcome,
    attempts: int,
    action: Action,
    target: Target,
) -> None:
    if action == "ok":
        assert isinstance(result, Response)
        assert isinstance(last, Answer)
        assert (result.status, result.attempts) == (last.status, attempts)
        return
    if action == "stopped":
        assert isinstance(result, SkylitStoppedError)
        assert isinstance(last, Answer)
        assert (result.status, result.error_code) == (last.status, last.error_code)
        return
    assert isinstance(result, Failed)
    assert (result.host, result.path, dict(result.params)) == (
        target.host,
        target.path,
        dict(target.params),
    )
    assert (result.outcome, result.attempts) == (action, attempts)
    if isinstance(last, Broken):
        assert (result.status, result.error_type) == (None, last.error.__name__)
    else:
        assert (result.status, result.error_type, result.error_code) == (
            last.status,
            None,
            last.error_code,
        )


# ---------------------------------------------------------------- properties

_NO_DATA = _json({"error": {"code": "no_data", "message": "fake message"}})


@example(
    target=TARGETS[0],
    outcomes=[Answer(503, retry_after=RetryAfter("delta", 120))] * MAX_ATTEMPTS,
    seed=0,
)
@example(
    target=TARGETS[1],
    outcomes=[
        Answer(429, retry_after=RetryAfter("date", 30)),
        Answer(429, reset_in_s=-5),
        Answer(429),
        Broken(httpx.ReadTimeout),
        Answer(404, _NO_DATA, "no_data"),
    ],
    seed=1,
)
@given(
    target=st.sampled_from(TARGETS),
    outcomes=outcome_lists(_PLAIN_OK),
    seed=st.integers(0, 2**32 - 1),
)
def test_retry_policy_per_request(target: Target, outcomes: list[Outcome], seed: int) -> None:
    clock = FakeClock(T0)
    wire = Wire(clock, outcomes)
    routes: dict[tuple[Host, str], Wire | httpx.Response] = {
        (Host.API, ACCOUNT_PATH): _account_ok(600, 4),
        (target.host, target.path): wire,
    }
    run = _run(target, ClientConfig(), seed, clock, routes)

    attempts, action = _check_request(wire, _lines_for(run, target.host, target.path), target)
    _check_result(run.result, outcomes[attempts - 1], attempts, action, target)


@example(
    outcomes=[Answer(503)] * MAX_ATTEMPTS,
    fallback_rpm=60,
    fallback_hif=1,
    seed=0,
)
@example(
    outcomes=[
        Broken(httpx.ConnectError),
        Answer(
            200,
            _json({"data": {"limits": {"requestsPerMinute": True, "historicalInFlight": 3}}}),
            hif=3,
        ),
    ],
    fallback_rpm=60,
    fallback_hif=1,
    seed=0,
)
@given(
    outcomes=outcome_lists(account_bodies()),
    fallback_rpm=st.one_of(st.just(60), st.integers(MAX_ATTEMPTS, 1_000)),
    fallback_hif=st.one_of(st.just(1), st.integers(1, 16)),
    seed=st.integers(0, 2**32 - 1),
)
def test_account_bootstrap_uses_fallbacks(
    outcomes: list[Outcome], fallback_rpm: int, fallback_hif: int, seed: int
) -> None:
    clock = FakeClock(T0)
    wire = Wire(clock, outcomes)
    account = Target(Host.API, ACCOUNT_PATH)
    follow_up = Target(Host.API, FOLLOW_UP_PATH)
    routes: dict[tuple[Host, str], Wire | httpx.Response] = {
        (account.host, account.path): wire,
        (follow_up.host, follow_up.path): httpx.Response(200, json={"data": {"symbols": []}}),
    }
    cfg = ClientConfig(
        fallback_requests_per_minute=fallback_rpm, fallback_historical_in_flight=fallback_hif
    )
    run = _run(follow_up, cfg, seed, clock, routes)

    attempts, action = _check_request(wire, _lines_for(run, account.host, account.path), account)
    follow_ups = _lines_for(run, follow_up.host, follow_up.path)
    if action == "stopped":
        assert isinstance(run.result, SkylitStoppedError)
        assert run.limits is None
        assert follow_ups == []
        return
    last = outcomes[attempts - 1]
    rpm = last.rpm if action == "ok" and isinstance(last, Answer) else None
    hif = last.hif if action == "ok" and isinstance(last, Answer) else None
    assert run.limits == Limits(
        requests_per_minute=fallback_rpm if rpm is None else rpm,
        historical_in_flight=fallback_hif if hif is None else hif,
        requests_per_minute_from_account=rpm is not None,
        historical_in_flight_from_account=hif is not None,
    )
    assert run.rpm_in_force == run.limits.requests_per_minute
    assert isinstance(run.result, Response)
    assert [(line["attempt"], line["outcome"]) for line in follow_ups] == [(1, "ok")]
