"""Unit tests for ``fse.skylit.client``: key check, limits bootstrap, replay slots, stop.

Every request is mocked with respx and the key is a fake value; nothing reaches
Skylit. Retry timing is virtual (``FakeClock``). The pacing and retry
properties (tasks 3.4-3.6) are tested elsewhere.

**Validates: Requirements 1.7, 2.1, 2.2, 2.4, 2.10**
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fse.data.cache import HeatmapView
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit.client import (
    EXIT_CREDENTIALS,
    MALFORMED_RESPONSE,
    ClientConfig,
    Failed,
    Limits,
    Response,
    SkylitClient,
    SkylitKeyMissingError,
    SkylitStoppedError,
)
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.skylit.models import AccountInfo, RangeResponse, SymbolCatalog
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, parse_rfc3339
from tests.fakes.clock import FakeClock

KEY = "fake-skylit-key-0000"
T0 = 1_772_721_000 * NS_PER_SECOND
VIEW = HeatmapView()
WINDOW_START = parse_rfc3339("2026-03-05T14:30:00Z")

SYMBOLS_BODY: dict[str, Any] = {
    "data": {
        "symbols": [
            {
                "symbol": "SPX",
                "isIndex": True,
                "metrics": ["gamma", "vanna"],
                "history": {"from": "2023-03-28", "to": "2026-03-05"},
            }
        ]
    }
}
RANGE_BODY: dict[str, Any] = {
    "data": {
        "from": "2026-03-05T14:30:00Z",
        "to": "2026-03-05T14:45:00Z",
        "symbols": [
            {
                "symbol": "SPX",
                "axes": [{"id": 0, "strikes": [5800, 5805], "expirations": ["2026-03-05"]}],
                "frames": [
                    {
                        "asOf": "2026-03-05T14:30:00.000Z",
                        "axis": 0,
                        "spot": 5801.5,
                        "previousClose": 5790.0,
                        "values": [1.5, -2.25],
                    }
                ],
            }
        ],
    },
    "meta": {"metric": "gamma", "resolution": "1s", "mode": "historical", "cached": False},
}


def account_body(**limits: object) -> dict[str, Any]:
    return {
        "data": {
            "customerId": "fake-customer-0000",
            "status": "active",
            "apiEligible": True,
            "unlimited": False,
            "creditsBalance": 4321,
            "limits": limits,
        }
    }


def ok(body: object, **headers: str) -> httpx.Response:
    return httpx.Response(200, json=body, headers=headers)


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": "fake message"}})


def route(router: respx.MockRouter, path: str, host: Host = Host.API) -> respx.Route:
    return router.route(method="GET", scheme="https", host=host.value, path=path)


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / FETCH_LOG_FILE_NAME


@pytest.fixture
def fetch_log(log_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), log_path) as log:
        yield log


def make_client(
    fetch_log: FetchLog,
    *,
    key: str | None = KEY,
    clock: FakeClock | None = None,
    cfg: ClientConfig | None = None,
) -> SkylitClient:
    env = EnvView({} if key is None else {"SKYLIT_API_KEY": key}, {})
    return SkylitClient(
        env, cfg or ClientConfig(), fetch_log, clock or FakeClock(T0), random.Random(7)
    )


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- key check (Req 1.7)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   "])
async def test_blank_key_sends_nothing_and_exits_3(
    respx_router: respx.MockRouter, fetch_log: FetchLog, log_path: Path, key: str | None
) -> None:
    every = respx_router.route(host__regex=r".*").mock(return_value=ok({}))
    env = EnvView({}, {} if key is None else {"SKYLIT_API_KEY": key})
    client = SkylitClient(env, ClientConfig(), fetch_log, FakeClock(T0), random.Random(7))
    async with client:
        with pytest.raises(SkylitKeyMissingError) as exc:
            await client.symbols()
        with pytest.raises(SkylitKeyMissingError):
            await client.get(Host.ATLAS, "/v1/config")
    assert exc.value.exit_code == EXIT_CREDENTIALS
    assert "SKYLIT_API_KEY" in str(exc.value)
    assert every.call_count == 0
    assert client.attempts_sent == 0
    assert not log_path.exists()


# ---------------------------------------------------------------- bootstrap (Req 2.1, 2.2)


@pytest.mark.asyncio
async def test_bootstrap_reads_limits_before_the_first_request(
    respx_router: respx.MockRouter, fetch_log: FetchLog, log_path: Path
) -> None:
    account = route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=120, historicalInFlight=2))
    )
    symbols = route(respx_router, "/v1/symbols").mock(return_value=ok(SYMBOLS_BODY))
    async with make_client(fetch_log) as client:
        catalog = await client.symbols()
    assert isinstance(catalog, SymbolCatalog)
    assert client.limits == Limits(120, 2, True, True)
    assert client.limiter.requests_per_minute == 120
    assert [c.request.url.path for c in respx_router.calls] == ["/v1/account", "/v1/symbols"]
    for call in (account.calls.last, symbols.calls.last):
        assert call.request.headers["authorization"] == f"Bearer {KEY}"
        assert KEY not in str(call.request.url)
    text = log_path.read_text(encoding="utf-8")
    assert KEY not in text
    assert [(e["path"], e["status"], e["outcome"]) for e in lines(log_path)] == [
        ("/v1/account", 200, "ok"),
        ("/v1/symbols", 200, "ok"),
    ]
    assert KEY not in repr(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"requestsPerMinute": 120}, Limits(120, 1, True, False)),
        ({"historicalInFlight": 3}, Limits(60, 3, False, True)),
        ({"requestsPerMinute": 0, "historicalInFlight": -1}, Limits(60, 1, False, False)),
        ({"requestsPerMinute": True, "historicalInFlight": "2"}, Limits(60, 1, False, False)),
        ({"requestsPerMinute": 120.5, "historicalInFlight": None}, Limits(60, 1, False, False)),
    ],
)
async def test_unusable_limits_get_their_fallback(
    respx_router: respx.MockRouter,
    fetch_log: FetchLog,
    limits: dict[str, object],
    expected: Limits,
) -> None:
    route(respx_router, "/v1/account").mock(return_value=ok(account_body(**limits)))
    route(respx_router, "/v1/symbols").mock(return_value=ok(SYMBOLS_BODY))
    async with make_client(fetch_log) as client:
        await client.symbols()
    assert client.limits == expected
    assert client.limiter.requests_per_minute == expected.requests_per_minute


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        error(404, "not_found"),
        ok({"error": "no data object"}),
        httpx.Response(200, content=b"<html>not json</html>"),
    ],
)
async def test_unreadable_account_answer_uses_the_configured_fallbacks(
    respx_router: respx.MockRouter, fetch_log: FetchLog, answer: httpx.Response
) -> None:
    account = route(respx_router, "/v1/account").mock(return_value=answer)
    route(respx_router, "/v1/symbols").mock(return_value=ok(SYMBOLS_BODY))
    cfg = ClientConfig(fallback_requests_per_minute=30, fallback_historical_in_flight=2)
    async with make_client(fetch_log, cfg=cfg) as client:
        assert isinstance(await client.symbols(), SymbolCatalog)
    assert client.limits == Limits(30, 2, False, False)
    assert account.call_count == 1  # not retried: a 404 is refused, a 200 is an answer


@pytest.mark.asyncio
async def test_account_failing_on_its_last_attempt_uses_fallbacks(
    respx_router: respx.MockRouter, fetch_log: FetchLog, log_path: Path
) -> None:
    account = route(respx_router, "/v1/account").mock(return_value=error(503, "unavailable"))
    route(respx_router, "/v1/symbols").mock(return_value=ok(SYMBOLS_BODY))
    clock = FakeClock(T0)
    async with make_client(fetch_log, clock=clock) as client:
        catalog = await clock.run(client.symbols())
    assert isinstance(catalog, SymbolCatalog)
    assert account.call_count == 5
    assert client.limits == Limits(60, 1, False, False)
    outcomes = [e["outcome"] for e in lines(log_path)]
    assert outcomes == ["retry", "retry", "retry", "retry", "failed", "ok"]


@pytest.mark.asyncio
async def test_bootstrap_runs_once_for_concurrent_first_requests(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    account = route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=2))
    )
    symbols = route(respx_router, "/v1/symbols").mock(return_value=ok(SYMBOLS_BODY))
    async with make_client(fetch_log) as client:
        await asyncio.gather(*(client.symbols() for _ in range(4)))
    assert account.call_count == 1
    assert symbols.call_count == 4


@pytest.mark.asyncio
async def test_the_first_account_call_reuses_the_bootstrap_answer(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    account = route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=2))
    )
    async with make_client(fetch_log) as client:
        first = await client.account()
        second = await client.account()
    assert isinstance(first, AccountInfo)
    assert isinstance(second, AccountInfo)
    assert first.limits.requests_per_minute == 600
    assert account.call_count == 2  # the bootstrap's answer, then one fresh call


# ---------------------------------------------------------------- replay slots (Req 2.4)


def _concurrency_probe(body: object) -> tuple[Any, list[int]]:
    """An async side effect that records how many requests are awaiting a response."""
    state = [0, 0]  # in flight, peak

    async def respond(request: httpx.Request) -> httpx.Response:
        state[0] += 1
        state[1] = max(state[1], state[0])
        for _ in range(25):  # stay "in flight" while the other tasks run
            await asyncio.sleep(0)
        state[0] -= 1
        return ok(body)

    return respond, state


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_in_flight", [1, 2])
async def test_replays_in_flight_never_exceed_the_account_limit(
    respx_router: respx.MockRouter, fetch_log: FetchLog, historical_in_flight: int
) -> None:
    route(respx_router, "/v1/account").mock(
        return_value=ok(
            account_body(requestsPerMinute=600, historicalInFlight=historical_in_flight)
        )
    )
    respond, state = _concurrency_probe(RANGE_BODY)
    replay = route(respx_router, "/v1/historical/range").mock(side_effect=respond)
    params = {"symbols": "SPX", "from": "2026-03-05T14:30:00Z", "to": "2026-03-05T14:45:00Z"}
    async with make_client(fetch_log) as client:
        typed = [
            client.historical_range(
                ["SPX"],
                from_ns=WINDOW_START,
                to_ns=WINDOW_START + 15 * NS_PER_MINUTE,
                metric="gamma",
                view=VIEW,
            )
            for _ in range(4)
        ]
        # A raw get on a replay path counts against the cap even without historical=True.
        raw = [client.get(Host.API, "/v1/historical/range", params) for _ in range(2)]
        results = await asyncio.gather(*typed, *raw)
    assert replay.call_count == 6
    assert state[1] == historical_in_flight
    assert all(isinstance(r, RangeResponse) for r in results[:4])
    assert all(isinstance(r, Response) for r in results[4:])


@pytest.mark.asyncio
async def test_other_requests_do_not_take_replay_slots(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=1))
    )
    respond, state = _concurrency_probe(SYMBOLS_BODY)
    route(respx_router, "/v1/symbols").mock(side_effect=respond)
    async with make_client(fetch_log) as client:
        await asyncio.gather(*(client.symbols() for _ in range(3)))
    assert state[1] == 3


# ---------------------------------------------------------------- stop (Req 2.10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "unauthorized"), (402, "insufficient_credits"), (403, "forbidden")],
)
async def test_401_402_403_stop_every_later_request(
    respx_router: respx.MockRouter,
    fetch_log: FetchLog,
    log_path: Path,
    status: int,
    code: str,
) -> None:
    route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=1))
    )
    symbols = route(respx_router, "/v1/symbols").mock(return_value=error(status, code))
    config = route(respx_router, "/v1/config", Host.ATLAS).mock(return_value=ok({}))
    async with make_client(fetch_log) as client:
        with pytest.raises(SkylitStoppedError) as exc:
            await client.symbols()
        with pytest.raises(SkylitStoppedError) as again:
            await client.atlas_config()
        with pytest.raises(SkylitStoppedError):
            await client.account()
    message = str(exc.value)
    assert exc.value.exit_code == EXIT_CREDENTIALS
    assert f"HTTP {status}" in message
    assert code in message
    assert KEY not in message
    assert str(again.value) == message
    assert symbols.call_count == 1
    assert config.call_count == 0
    assert client.stopped is not None
    assert lines(log_path)[-1]["outcome"] == "stopped"


@pytest.mark.asyncio
async def test_a_stop_during_the_bootstrap_sends_nothing_else(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    account = route(respx_router, "/v1/account").mock(return_value=error(403, "forbidden"))
    symbols = route(respx_router, "/v1/symbols").mock(return_value=ok(SYMBOLS_BODY))
    async with make_client(fetch_log) as client:
        with pytest.raises(SkylitStoppedError, match="HTTP 403"):
            await client.symbols()
        with pytest.raises(SkylitStoppedError):
            await client.symbols()
    assert account.call_count == 1
    assert symbols.call_count == 0
    assert client.limits is None


@pytest.mark.asyncio
async def test_a_server_code_that_is_not_code_shaped_is_dropped(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    route(respx_router, "/v1/account").mock(return_value=error(403, "Bearer " + KEY))
    async with make_client(fetch_log) as client:
        with pytest.raises(SkylitStoppedError) as exc:
            await client.symbols()
    assert exc.value.error_code is None
    assert KEY not in str(exc.value)


# ---------------------------------------------------------------- results


@pytest.mark.asyncio
async def test_no_data_is_a_rejected_failure_the_caller_can_read(
    respx_router: respx.MockRouter, fetch_log: FetchLog, log_path: Path
) -> None:
    route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=1))
    )
    historical = route(respx_router, "/v1/historical").mock(return_value=error(404, "no_data"))
    async with make_client(fetch_log) as client:
        result = await client.historical(["SPX"], at_ns=WINDOW_START, metric="gamma", view=VIEW)
    assert isinstance(result, Failed)
    assert (result.outcome, result.attempts, result.no_data) == ("rejected", 1, True)
    assert result.cause == "HTTP 404 (no_data)"
    assert historical.call_count == 1
    last = lines(log_path)[-1]
    assert (last["status"], last["error_code"], last["outcome"]) == (404, "no_data", "rejected")
    assert last["params"]["at"] == "2026-03-05T14:30:00Z"


@pytest.mark.asyncio
async def test_a_body_without_the_documented_shape_is_malformed(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=1))
    )
    route(respx_router, "/v1/symbols").mock(return_value=ok({"data": {}}))
    async with make_client(fetch_log) as client:
        result = await client.symbols()
    assert isinstance(result, Failed)
    assert (result.outcome, result.status, result.error_type) == (
        "malformed",
        200,
        MALFORMED_RESPONSE,
    )
    assert result.detail is not None
    assert "data.symbols is missing" in result.detail


@pytest.mark.asyncio
async def test_range_request_sends_the_documented_query(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> None:
    route(respx_router, "/v1/account").mock(
        return_value=ok(account_body(requestsPerMinute=600, historicalInFlight=1))
    )
    replay = route(respx_router, "/v1/historical/range").mock(return_value=ok(RANGE_BODY))
    async with make_client(fetch_log) as client:
        result = await client.historical_range(
            ["SPX"],
            from_ns=WINDOW_START,
            to_ns=WINDOW_START + 15 * NS_PER_MINUTE,
            metric="gamma",
            view=VIEW,
        )
    assert isinstance(result, RangeResponse)
    assert result.snapshots(view_id="0123456789abcdef")["SPX"][0].values == (1.5, -2.25)
    assert dict(replay.calls.last.request.url.params) == {
        "symbols": "SPX",
        "from": "2026-03-05T14:30:00Z",
        "to": "2026-03-05T14:45:00Z",
        "metric": "gamma",
        "maxStrikes": "92",
        "maxExpirations": "5",
    }
