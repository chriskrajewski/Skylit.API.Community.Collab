"""Unit tests for ``fse.skylit.retry``: each row of the design §2 outcome table.

Jitter comes from a seeded ``random.Random``; each expected wait is the same
draw from a second generator with the same seed.

**Validates: Requirements 2.6, 2.7, 2.8, 2.9, 2.10**
"""

from __future__ import annotations

import random

import pytest

from fse.skylit.ratelimit import RateHeaders
from fse.skylit.retry import (
    MAX_ATTEMPTS,
    Action,
    Decision,
    ErrorKind,
    HttpResult,
    TransportFailure,
    backoff_ns,
    decide,
    error_code_of,
)
from fse.timekit import NS_PER_SECOND

S = NS_PER_SECOND
NOW = 1_760_000_000 * S
SEED = 20260214

NETWORK = TransportFailure(ErrorKind.NETWORK, "ConnectError")
TIMEOUT = TransportFailure(ErrorKind.TIMEOUT, "ReadTimeout")


def _rng() -> random.Random:
    return random.Random(SEED)


@pytest.mark.parametrize("status", [200, 204, 299])
@pytest.mark.parametrize("attempt", range(1, MAX_ATTEMPTS + 1))
def test_2xx_returns(status: int, attempt: int) -> None:
    assert decide(HttpResult(status), attempt, now=NOW, rng=_rng()) == Decision(Action.OK)


@pytest.mark.parametrize("status", [401, 402, 403])
@pytest.mark.parametrize("attempt", [1, MAX_ATTEMPTS])
def test_401_402_403_stop(status: int, attempt: int) -> None:
    result = HttpResult(status, error_code="account_suspended")
    assert decide(result, attempt, now=NOW, rng=_rng()) == Decision(Action.STOPPED)


@pytest.mark.parametrize("status", [400, 404, 405, 409, 422, 451, 501, 505, 599, 101, 302])
@pytest.mark.parametrize("attempt", [1, MAX_ATTEMPTS])
def test_other_statuses_are_rejected_without_retry(status: int, attempt: int) -> None:
    rng = _rng()
    state = rng.getstate()
    assert decide(HttpResult(status), attempt, now=NOW, rng=rng) == Decision(Action.REJECTED)
    assert rng.getstate() == state


def test_429_waits_exactly_retry_after_and_holds_all_requests() -> None:
    rng = _rng()
    state = rng.getstate()
    headers = RateHeaders(retry_after_ns=7 * S, reset_at=NOW + 30 * S)
    decision = decide(HttpResult(429, headers), 1, now=NOW, rng=rng)
    assert decision == Decision(Action.RETRY, wait_ns=7 * S, hold_all=True)
    assert rng.getstate() == state  # no jitter is drawn


def test_429_without_retry_after_waits_until_reset_plus_jitter() -> None:
    headers = RateHeaders(reset_at=NOW + 30 * S)
    decision = decide(HttpResult(429, headers), 2, now=NOW, rng=_rng())
    jitter = _rng().randint(0, S)
    assert decision == Decision(Action.RETRY, wait_ns=30 * S + jitter, hold_all=True)
    assert 30 * S <= decision.wait_ns <= 31 * S


def test_429_with_a_past_reset_waits_no_less_than_zero() -> None:
    headers = RateHeaders(reset_at=NOW - 10 * S)
    decision = decide(HttpResult(429, headers), 1, now=NOW, rng=_rng())
    assert decision == Decision(Action.RETRY, wait_ns=0, hold_all=True)


def test_429_with_neither_header_waits_60_s_plus_jitter() -> None:
    decision = decide(HttpResult(429), 4, now=NOW, rng=_rng())
    jitter = _rng().randint(0, S)
    assert decision == Decision(Action.RETRY, wait_ns=60 * S + jitter, hold_all=True)
    assert 60 * S <= decision.wait_ns <= 61 * S


@pytest.mark.parametrize(
    "result", [HttpResult(500), HttpResult(502), HttpResult(503), HttpResult(504), NETWORK, TIMEOUT]
)
@pytest.mark.parametrize("attempt", range(1, MAX_ATTEMPTS))
def test_5xx_network_and_timeout_back_off_with_full_jitter(
    result: HttpResult | TransportFailure, attempt: int
) -> None:
    decision = decide(result, attempt, now=NOW, rng=_rng())
    cap = min(30 * S, S * 2**attempt)
    assert decision == Decision(Action.RETRY, wait_ns=_rng().randint(0, cap))
    assert 0 <= decision.wait_ns <= cap


def test_503_ignores_retry_after() -> None:
    headers = RateHeaders(retry_after_ns=120 * S)
    decision = decide(HttpResult(503, headers), 1, now=NOW, rng=_rng())
    assert decision == Decision(Action.RETRY, wait_ns=_rng().randint(0, 2 * S))


@pytest.mark.parametrize(
    "result",
    [*(HttpResult(status) for status in (429, 500, 502, 503, 504)), NETWORK, TIMEOUT],
)
def test_the_fifth_retryable_attempt_fails(result: HttpResult | TransportFailure) -> None:
    rng = _rng()
    state = rng.getstate()
    assert decide(result, MAX_ATTEMPTS, now=NOW, rng=rng) == Decision(Action.FAILED)
    assert rng.getstate() == state


def test_backoff_cap_grows_then_stops_at_30_s() -> None:
    caps = []
    for attempts in range(1, 8):
        rng = random.Random(0)
        draws = [backoff_ns(attempts, rng) for _ in range(200)]
        caps.append(max(draws))
        assert min(draws) >= 0
    limits = [2 * S, 4 * S, 8 * S, 16 * S, 30 * S, 30 * S, 30 * S]
    assert all(cap <= limit for cap, limit in zip(caps, limits, strict=True))
    assert caps[4] > 16 * S  # at n = 5 the 30 s cap, not 32 s, is the bound


@pytest.mark.parametrize("attempt", [0, 6, -1])
def test_attempt_numbers_outside_1_to_5_are_refused(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt"):
        decide(HttpResult(200), attempt, now=NOW, rng=_rng())


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"error": {"code": "no_data", "message": "No snapshot"}}, "no_data"),
        ({"error": {"code": "", "message": "x"}}, None),
        ({"error": {"message": "x"}}, None),
        ({"error": "ip_rate_limited"}, None),
        ({"data": {}}, None),
        ([], None),
        (None, None),
    ],
)
def test_error_code_of(body: object, code: str | None) -> None:
    assert error_code_of(body) == code
