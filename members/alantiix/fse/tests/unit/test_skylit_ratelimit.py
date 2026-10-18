"""Unit tests for ``fse.skylit.ratelimit``: header parsing, the rolling window and holds.

Time is virtual (``tests.fakes.clock.FakeClock``); nothing waits in real time.

**Validates: Requirements 2.3, 2.5**
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from fse.skylit.ratelimit import LOW_WATER_PAUSE_NS, WINDOW_NS, RateHeaders, RateLimiter
from fse.timekit import NS_PER_SECOND
from tests.fakes.clock import FakeClock

S = NS_PER_SECOND
T0 = 1_760_000_000 * S  # a whole Unix second


# ---------------------------------------------------------------- RateHeaders


def test_parse_reads_every_header_without_regard_to_case() -> None:
    parsed = RateHeaders.parse(
        {
            "x-ratelimit-remaining": "4",
            "X-RATELIMIT-RESET": "1760000030",
            "Retry-After": "7",
            "X-Credits-Remaining": " 4999 ",
        },
        T0,
    )
    assert parsed == RateHeaders(
        remaining=4, reset_at=T0 + 30 * S, retry_after_ns=7 * S, credits_remaining="4999"
    )


def test_parse_accepts_httpx_headers() -> None:
    headers = httpx.Headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1760000001"})
    assert RateHeaders.parse(headers, T0) == RateHeaders(remaining=0, reset_at=T0 + S)


def test_parse_keeps_fractional_seconds_exactly() -> None:
    parsed = RateHeaders.parse({"X-RateLimit-Reset": "1760000000.25", "Retry-After": "1.5"}, T0)
    assert parsed.reset_at == T0 + S // 4
    assert parsed.retry_after_ns == 3 * S // 2


def test_retry_after_http_date_is_a_delay_from_receipt() -> None:
    # T0 is 2025-10-09T08:53:20Z.
    parsed = RateHeaders.parse({"Retry-After": "Thu, 09 Oct 2025 08:53:30 GMT"}, T0)
    assert parsed.retry_after_ns == 10 * S
    past = RateHeaders.parse({"Retry-After": "Thu, 09 Oct 2025 08:00:00 GMT"}, T0)
    assert past.retry_after_ns == 0


@pytest.mark.parametrize("value", ["", "-1", "+3", "five", "1_000", "٣", "3, 4"])
def test_unreadable_values_count_as_absent(value: str) -> None:
    parsed = RateHeaders.parse(
        {
            "X-RateLimit-Remaining": value,
            "X-RateLimit-Reset": value,
            "Retry-After": value,
        },
        T0,
    )
    assert parsed.remaining is None
    assert parsed.reset_at is None
    assert parsed.retry_after_ns is None


def test_missing_headers_parse_to_none() -> None:
    assert RateHeaders.parse({}, T0) == RateHeaders()
    assert RateHeaders.parse({"X-Credits-Remaining": "  "}, T0).credits_remaining is None


# ---------------------------------------------------------------- rolling window


async def _sends(clock: FakeClock, limiter: RateLimiter, count: int) -> list[int]:
    return [await limiter.acquire() - T0 for _ in range(count)]


@pytest.mark.asyncio
async def test_burst_up_to_the_limit_then_wait_for_the_oldest_send_to_leave() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 3)
    sent = await clock.run(_sends(clock, limiter, 7))
    assert sent == [0, 0, 0, 60 * S, 60 * S, 60 * S, 120 * S]


@pytest.mark.asyncio
async def test_spaced_sends_free_their_slots_one_at_a_time() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 3)

    async def scenario() -> list[int]:
        sent = []
        for gap in (0, 10 * S, 10 * S, 0, 0, 0):
            await clock.sleep(gap)
            sent.append(await limiter.acquire() - T0)
        return sent

    sent = await clock.run(scenario())
    # Sends at 0, 10 and 20 s; the 4th to 6th wait for each to leave the 60 s window.
    assert sent == [0, 10 * S, 20 * S, 60 * S, 70 * S, 80 * S]
    _assert_window(sent, 3)


@pytest.mark.asyncio
async def test_concurrent_callers_are_served_in_order_within_the_limit() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 2)

    async def scenario() -> list[tuple[int, int]]:
        results: list[tuple[int, int]] = []

        async def caller(i: int) -> None:
            results.append((i, await limiter.acquire() - T0))

        await asyncio.gather(*(caller(i) for i in range(5)))
        return results

    results = await clock.run(scenario())
    assert results == [(0, 0), (1, 0), (2, 60 * S), (3, 60 * S), (4, 120 * S)]


@pytest.mark.asyncio
async def test_lowering_the_limit_counts_sends_already_made() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 60)
    first = await clock.run(_sends(clock, limiter, 3))
    limiter.set_requests_per_minute(2)
    assert limiter.next_send_at() == T0 + WINDOW_NS
    later = await clock.run(_sends(clock, limiter, 2))
    assert first == [0, 0, 0]
    _assert_window([*first, *later], 3)
    assert later == [60 * S, 60 * S]


def test_limits_are_validated() -> None:
    clock = FakeClock(T0)
    with pytest.raises(ValueError, match="requests_per_minute"):
        RateLimiter(clock, 0)
    with pytest.raises(TypeError, match="requests_per_minute"):
        RateLimiter(clock, True)
    with pytest.raises(ValueError, match="low_water"):
        RateLimiter(clock, 10, low_water=-1)
    limiter = RateLimiter(clock, 10)
    with pytest.raises(ValueError, match="requests_per_minute"):
        limiter.set_requests_per_minute(0)
    assert limiter.requests_per_minute == 10


# ---------------------------------------------------------------- low-water pause and holds


@pytest.mark.asyncio
async def test_low_water_holds_every_send_until_the_reset_time() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 600)
    until = limiter.observe(RateHeaders(remaining=5, reset_at=T0 + 42 * S))
    assert until == T0 + 42 * S
    assert limiter.paused_until == T0 + 42 * S
    sent = await clock.run(_sends(clock, limiter, 2))
    assert sent == [42 * S, 42 * S]


@pytest.mark.asyncio
async def test_low_water_without_a_reset_header_holds_for_60_s() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 600)
    clock.advance_to(T0 + 5 * S)
    assert limiter.observe(RateHeaders(remaining=0)) == T0 + 5 * S + LOW_WATER_PAUSE_NS
    sent = await clock.run(_sends(clock, limiter, 1))
    assert sent == [65 * S]


def test_remaining_above_the_mark_or_absent_does_not_hold() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 600, low_water=5)
    assert limiter.observe(RateHeaders(remaining=6, reset_at=T0 + 30 * S)) is None
    assert limiter.observe(RateHeaders(reset_at=T0 + 30 * S)) is None
    assert limiter.paused_until is None
    assert limiter.next_send_at() == T0


def test_a_hold_is_never_shortened() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 600)
    limiter.hold_until(T0 + 50 * S)
    limiter.observe(RateHeaders(remaining=1, reset_at=T0 + 10 * S))
    assert limiter.paused_until == T0 + 50 * S
    limiter.observe(RateHeaders(remaining=1, reset_at=T0 + 70 * S))
    assert limiter.paused_until == T0 + 70 * S


@pytest.mark.asyncio
async def test_a_hold_set_while_a_caller_waits_is_honoured() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 1)

    async def scenario() -> list[int]:
        first = await limiter.acquire()
        waiter = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)  # the waiter now sleeps until the window frees at 60 s
        await clock.sleep(30 * S)
        limiter.observe(RateHeaders(remaining=0, reset_at=T0 + 90 * S))
        return [first - T0, await waiter - T0]

    assert await clock.run(scenario()) == [0, 90 * S]


@pytest.mark.asyncio
async def test_window_and_hold_combine_to_the_later_instant() -> None:
    clock = FakeClock(T0)
    limiter = RateLimiter(clock, 1)
    await clock.run(_sends(clock, limiter, 1))
    limiter.hold_until(T0 + 20 * S)
    assert limiter.next_send_at() == T0 + 60 * S
    limiter.hold_until(T0 + 75 * S)
    assert limiter.next_send_at() == T0 + 75 * S


def _assert_window(sent: list[int], rpm: int) -> None:
    """No half-open 60 s window holds more than ``rpm`` sends."""
    for start in sent:
        assert sum(start <= s < start + WINDOW_NS for s in sent) <= rpm
