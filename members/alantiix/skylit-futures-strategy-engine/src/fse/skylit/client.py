"""The Skylit_Client: every Skylit REST request goes through here (design §2, Req 1.7, 2.1-2.10).

Sources (official Skylit docs; content rephrased for compliance with licensing
restrictions):

- Bearer authentication, hosts and the error envelope:
  https://www.skylit.ai/docs/api-reference/introduction and
  https://www.skylit.ai/docs/api-reference/errors
- Limits from ``GET /v1/account``, retryable statuses, the gateway's 429
  without ``Retry-After`` and the historical-replay concurrency cap:
  https://www.skylit.ai/docs/api-reference/rate-limits-and-retries
- ``GET /v1/account`` fields (``limits.requestsPerMinute``,
  ``limits.historicalInFlight``):
  https://www.skylit.ai/docs/api-reference/account/your-balance-and-limits

What one :class:`SkylitClient` does for every request:

1. **Key check (Req 1.7).** ``SKYLIT_API_KEY`` comes only from the
   :class:`~fse.secrets.env.EnvView` (shell, then the Project ``.env``). When
   it is blank, :class:`SkylitKeyMissingError` (exit 3) names the variable
   and nothing is sent.
2. **Limits bootstrap (Req 2.1-2.2).** The first request due triggers one
   ``GET /v1/account``, under the same pacing and retry policy, before that
   request is sent. ``limits.requestsPerMinute`` sets the
   :class:`~fse.skylit.ratelimit.RateLimiter`; ``limits.historicalInFlight``
   sizes the replay semaphore. A failed call, or a limit that is not a
   positive integer, gets the configured fallback (default 60 and 1) for that
   value. Until the bootstrap ends, the limiter runs at the fallback rate.
3. **Pacing (Req 2.3, 2.5).** ``RateLimiter.acquire()`` immediately before
   every attempt, retries included; ``observe(RateHeaders.parse(...))`` on
   every response, whatever its status.
4. **Replays in flight (Req 2.4).** ``GET /v1/historical`` and
   ``GET /v1/historical/range`` hold one slot of an ``asyncio.Semaphore``
   from before their first attempt until their last attempt ends, so at most
   ``historicalInFlight`` of them await a response at any instant.
5. **Retries (Req 2.6-2.9).** :func:`fse.skylit.retry.decide` after every
   attempt. A 429 retry holds every request (``RateLimiter.hold_until``);
   any other retry sleeps on its own request only. Each attempt writes one
   Fetch_Log line (Req 2.8, 2.9, 2.11).
6. **Stop (Req 2.10).** A 401, 402 or 403 raises :class:`SkylitStoppedError`
   (exit 3) naming the status and any error code. From then on the client
   sends nothing: every later call, and every call already waiting for a send
   slot, raises the same error.

**Stream** (Req 23.3-23.4). :meth:`SkylitClient.stream` opens one
``GET /v1/stream`` connection under the same key check, limits bootstrap,
pacing, Fetch_Log line and 401/402/403 stop, and yields its server-sent
events (:mod:`fse.skylit.sse`). It is never retried here: any other failure to
open raises :class:`SkylitStreamError`, and the Live_Runner's map feed decides
when to reconnect. ``last_event_id`` goes in the ``Last-Event-ID`` header.

Results: a 2xx is a :class:`Response` (decoded JSON body); a request that
failed for good (Req 2.8) or was refused without a retry (Req 2.9) is a
:class:`Failed`, and the caller continues with its next request. The typed
helpers (:meth:`SkylitClient.symbols`, :meth:`SkylitClient.historical_range`,
...) parse the body with :mod:`fse.skylit.models` and turn a body without the
documented shape into a ``Failed`` with outcome ``malformed``.

Secrets: the key is only ever written into the ``Authorization`` header. No
message, repr or Fetch_Log line holds a header, an exception from httpx is
reduced to its class name, and a server error code is kept only when it looks
like a code (letters, digits, ``_.:-``, at most 64 characters).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType, TracebackType
from typing import ClassVar, Final, Literal, Self

import httpx

from fse.clock import Clock
from fse.secrets.env import EnvView
from fse.skylit import endpoints as ep
from fse.skylit.endpoints import Endpoint, Host, ViewParams
from fse.skylit.fetch_log import FetchLog, FetchLogEntry
from fse.skylit.models import (
    AccountInfo,
    AtlasConfig,
    AtlasHistory,
    DarkPoolTradesResponse,
    HeatmapResponse,
    LevelsResponse,
    MalformedResponseError,
    RangeResponse,
    SearchResults,
    SymbolCatalog,
    parse_atlas_history,
)
from fse.skylit.ratelimit import DEFAULT_LOW_WATER, RateHeaders, RateLimiter
from fse.skylit.retry import (
    Action,
    AttemptResult,
    Decision,
    ErrorKind,
    HttpResult,
    TransportFailure,
    decide,
    error_code_of,
)
from fse.skylit.sse import SseDecoder, SseMessage
from fse.timekit import Instant

__all__ = [
    "DEFAULT_FALLBACK_HISTORICAL_IN_FLIGHT",
    "DEFAULT_FALLBACK_REQUESTS_PER_MINUTE",
    "DEFAULT_TIMEOUT_S",
    "EXIT_CREDENTIALS",
    "KEY_VARIABLE",
    "MALFORMED_RESPONSE",
    "ClientConfig",
    "Failed",
    "FailureOutcome",
    "Limits",
    "Response",
    "SkylitAccessError",
    "SkylitClient",
    "SkylitKeyMissingError",
    "SkylitStoppedError",
    "SkylitStreamError",
]

# Design "Exit codes": 3 = credentials or access (blank SKYLIT_API_KEY, Skylit 401/402/403).
EXIT_CREDENTIALS: Final = 3
KEY_VARIABLE: Final = "SKYLIT_API_KEY"

DEFAULT_TIMEOUT_S: Final = 60.0
DEFAULT_FALLBACK_REQUESTS_PER_MINUTE: Final = 60
DEFAULT_FALLBACK_HISTORICAL_IN_FLIGHT: Final = 1

MALFORMED_RESPONSE: Final = "malformed_response"
"""``Failed.error_type`` of a 2xx whose body does not have the documented shape."""

_SAFE_CODE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,64}", re.ASCII)


class SkylitAccessError(Exception):
    """Skylit cannot be used with this key. The message holds no secret value."""

    exit_code: ClassVar[int] = EXIT_CREDENTIALS


class SkylitKeyMissingError(SkylitAccessError):
    """``SKYLIT_API_KEY`` is blank in the shell and in the Project ``.env`` (Req 1.7)."""

    def __init__(self) -> None:
        super().__init__(
            f"{KEY_VARIABLE} is blank; set it in the shell environment or the Project .env "
            "file. No Skylit request was sent."
        )


class SkylitStoppedError(SkylitAccessError):
    """Skylit answered 401, 402 or 403; the client sends no further request (Req 2.10)."""

    def __init__(self, status: int, error_code: str | None, host: Host, path: str) -> None:
        self.status = status
        self.error_code = error_code
        self.host = host
        self.path = path
        code = f" (error code {error_code})" if error_code else ""
        super().__init__(
            f"Skylit refused GET {path} on {host.value} with HTTP {status}{code}; "
            f"no further Skylit request will be sent. Check {KEY_VARIABLE}, the account's "
            "credits and its API access."
        )


class SkylitStreamError(Exception):
    """``GET /v1/stream`` did not open: another HTTP status, a timeout or a network error."""

    def __init__(self, cause: str) -> None:
        self.cause = cause
        super().__init__(f"GET /v1/stream did not open: {cause}")


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Client settings with the Requirement 2 defaults."""

    request_timeout_s: float = DEFAULT_TIMEOUT_S
    """No complete response within this many seconds counts as a timeout (Req 2.7)."""
    low_water: int = DEFAULT_LOW_WATER
    """``X-RateLimit-Remaining`` at or below this pauses every request (Req 2.5)."""
    fallback_requests_per_minute: int = DEFAULT_FALLBACK_REQUESTS_PER_MINUTE
    fallback_historical_in_flight: int = DEFAULT_FALLBACK_HISTORICAL_IN_FLIGHT

    def __post_init__(self) -> None:
        timeout = self.request_timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not timeout > 0:
            raise ValueError("request_timeout_s must be a positive number of seconds")
        for name, value, minimum in (
            ("low_water", self.low_water, 0),
            ("fallback_requests_per_minute", self.fallback_requests_per_minute, 1),
            ("fallback_historical_in_flight", self.fallback_historical_in_flight, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an int of at least {minimum}")


@dataclass(frozen=True, slots=True)
class Limits:
    """The limits in force, and whether each came from ``GET /v1/account`` or the fallback."""

    requests_per_minute: int
    historical_in_flight: int
    requests_per_minute_from_account: bool
    historical_in_flight_from_account: bool


@dataclass(frozen=True, slots=True)
class Response:
    """A 2xx answer. ``body`` is the decoded JSON, or ``None`` when it is not JSON."""

    host: Host
    path: str
    status: int
    headers: RateHeaders
    attempts: int
    body: object = field(repr=False)


type FailureOutcome = Literal["failed", "rejected", "malformed"]


@dataclass(frozen=True, slots=True)
class Failed:
    """A request that produced no usable answer; the caller moves on (Req 2.8, 2.9).

    ``outcome`` is ``failed`` (retryable failures on all 5 attempts),
    ``rejected`` (a status that is not retried, such as 400, 404 or 422) or
    ``malformed`` (a 2xx whose body does not have the documented shape;
    ``detail`` names the field). Exactly one of ``status`` and ``error_type``
    describes the last attempt, except ``malformed``, which has both.
    """

    host: Host
    path: str
    params: Mapping[str, str]
    outcome: FailureOutcome
    attempts: int
    status: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    detail: str | None = None

    @property
    def no_data(self) -> bool:
        """``404 no_data``: Skylit holds no Snapshot there (Req 3.8)."""
        return self.status == 404 and self.error_code == "no_data"

    @property
    def cause(self) -> str:
        """A short cause for logs and coverage: ``HTTP 404 (no_data)``, ``ReadTimeout``."""
        if self.outcome == "malformed":
            return MALFORMED_RESPONSE
        if self.status is not None:
            return f"HTTP {self.status}" + (f" ({self.error_code})" if self.error_code else "")
        return self.error_type or "unknown"


class SkylitClient:
    """Paced, retried and logged ``GET`` requests to every Skylit host, with one key."""

    __slots__ = (
        "_account",
        "_bootstrap_lock",
        "_cfg",
        "_clock",
        "_env",
        "_fetch_log",
        "_http",
        "_limiter",
        "_limits",
        "_owns_http",
        "_replay_slots",
        "_rng",
        "_sent",
        "_stopped",
    )

    def __init__(
        self,
        env: EnvView,
        cfg: ClientConfig,
        fetch_log: FetchLog,
        clock: Clock,
        rng: random.Random,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """``http`` is closed by :meth:`aclose` only when the client created it."""
        self._env = env
        self._cfg = cfg
        self._fetch_log = fetch_log
        self._clock = clock
        self._rng = rng
        self._limiter = RateLimiter(
            clock, cfg.fallback_requests_per_minute, low_water=cfg.low_water
        )
        self._limits: Limits | None = None
        self._account: AccountInfo | Failed | None = None
        self._bootstrap_lock = asyncio.Lock()
        self._replay_slots: asyncio.Semaphore | None = None
        self._stopped: SkylitStoppedError | None = None
        self._sent = 0
        self._owns_http = http is None
        self._http = http if http is not None else httpx.AsyncClient(follow_redirects=False)

    # ------------------------------------------------------------ state

    @property
    def limits(self) -> Limits | None:
        """The limits in force, or ``None`` before the bootstrap has run."""
        return self._limits

    @property
    def limiter(self) -> RateLimiter:
        return self._limiter

    @property
    def stopped(self) -> SkylitStoppedError | None:
        """The 401/402/403 that stopped the client, if any."""
        return self._stopped

    @property
    def attempts_sent(self) -> int:
        """Attempts sent so far, the bootstrap and retries included."""
        return self._sent

    # ------------------------------------------------------------ requests

    async def get(
        self,
        host: Host,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        historical: bool = False,
    ) -> Response | Failed:
        """Send ``GET path`` on ``host`` under the full policy and return the outcome.

        ``historical`` marks a replay for the in-flight cap; the two replay
        paths count as replays either way. Raises :class:`SkylitKeyMissingError`
        before any request when the key is blank, and
        :class:`SkylitStoppedError` on (or after) a 401, 402 or 403.
        """
        if not path.startswith("/v1/"):
            raise ValueError("a Skylit path starts with /v1/")
        query = dict(params or {})
        key = self._require_key()
        await self._ensure_limits(key)
        if historical or ep.is_replay(host, path):
            slots = self._replay_slots
            assert slots is not None  # set by the bootstrap
            async with slots:
                return await self._request(host, path, query, key)
        return await self._request(host, path, query, key)

    async def call(self, endpoint: Endpoint, params: Mapping[str, str]) -> Response | Failed:
        """:meth:`get` for a documented endpoint."""
        return await self.get(endpoint.host, endpoint.path, params, historical=endpoint.historical)

    async def account(self) -> AccountInfo | Failed:
        """``GET /v1/account`` (free).

        The bootstrap's own answer is returned to the first caller, so a client
        that starts with ``account()`` sends one account request, not two.
        """
        key = self._require_key()
        await self._ensure_limits(key)
        pending, self._account = self._account, None
        if pending is not None:
            return pending
        return await self._typed(ep.ACCOUNT, {}, AccountInfo.parse)

    async def symbols(self) -> SymbolCatalog | Failed:
        """``GET /v1/symbols`` (free): every symbol with its history dates."""
        return await self._typed(ep.SYMBOLS, {}, SymbolCatalog.parse)

    async def heatmap(
        self, symbols: Iterable[str], *, metric: str, view: ViewParams
    ) -> HeatmapResponse | Failed:
        """``GET /v1/heatmap`` (1 credit): the live boards."""
        params = ep.heatmap_params(symbols, metric=metric, view=view)
        return await self._typed(ep.HEATMAP, params, HeatmapResponse.parse)

    async def historical(
        self, symbols: Iterable[str], *, at_ns: Instant, metric: str, view: ViewParams
    ) -> HeatmapResponse | Failed:
        """``GET /v1/historical`` (5 credits): the latest Snapshot at or before ``at_ns``."""
        params = ep.historical_params(symbols, at_ns=at_ns, metric=metric, view=view)
        return await self._typed(
            ep.HISTORICAL, params, lambda b: HeatmapResponse.parse(b, where="GET /v1/historical")
        )

    async def historical_range(
        self,
        symbols: Iterable[str],
        *,
        from_ns: Instant,
        to_ns: Instant,
        metric: str,
        view: ViewParams,
    ) -> RangeResponse | Failed:
        """``GET /v1/historical/range`` (25 credits): every Snapshot in ``[from_ns, to_ns]``."""
        params = ep.range_params(symbols, from_ns=from_ns, to_ns=to_ns, metric=metric, view=view)
        return await self._typed(ep.HISTORICAL_RANGE, params, RangeResponse.parse)

    async def gex_levels(
        self, symbols: Iterable[str], *, metric: str, view: ViewParams
    ) -> LevelsResponse | Failed:
        """``GET /v1/gex/levels`` (1 credit, live only)."""
        params = ep.gex_levels_params(symbols, metric=metric, view=view)
        return await self._typed(ep.GEX_LEVELS, params, LevelsResponse.parse)

    async def dark_pool_trades(
        self,
        tickers: Iterable[str],
        *,
        date_start: date,
        date_end: date,
        limit: int = ep.DARK_POOL_MAX_LIMIT,
        offset: int = 0,
        min_notional: float | None = None,
    ) -> DarkPoolTradesResponse | Failed:
        """``GET /v1/dark-pool/trades`` (5 credits): one page of prints."""
        params = ep.dark_pool_trades_params(
            tickers,
            date_start=date_start,
            date_end=date_end,
            limit=limit,
            offset=offset,
            min_notional=min_notional,
        )
        return await self._typed(ep.DARK_POOL_TRADES, params, DarkPoolTradesResponse.parse)

    async def atlas_config(self) -> AtlasConfig | Failed:
        """Atlas ``GET /v1/config`` (free): resolutions and ``max_fetch_trading_days``."""
        return await self._typed(ep.ATLAS_CONFIG, {}, AtlasConfig.parse)

    async def atlas_search(self, query: str, *, limit: int = 25) -> SearchResults | Failed:
        """Atlas ``GET /v1/search`` (1 credit)."""
        params = ep.atlas_search_params(query, limit=limit)
        return await self._typed(ep.ATLAS_SEARCH, params, SearchResults.parse)

    async def atlas_history(
        self, symbol: str, *, resolution: str, from_s: int, to_s: int, extended: bool = False
    ) -> AtlasHistory | Failed:
        """Atlas ``GET /v1/history`` (1 credit): bars or ``no_data`` over ``[from_s, to_s)``."""
        params = ep.atlas_history_params(
            symbol, resolution=resolution, from_s=from_s, to_s=to_s, extended=extended
        )
        return await self._typed(ep.ATLAS_HISTORY, params, parse_atlas_history)

    @contextlib.asynccontextmanager
    async def stream(
        self, params: Mapping[str, str], *, last_event_id: str | None = None
    ) -> AsyncIterator[AsyncIterator[SseMessage]]:
        """Open ``GET /v1/stream`` with ``params`` and yield its events (see the module notes).

        ``params`` come from :func:`fse.skylit.endpoints.stream_params`. Raises
        :class:`SkylitKeyMissingError` or :class:`SkylitStoppedError` as
        :meth:`get` does, and :class:`SkylitStreamError` when the connection
        does not open with a 2xx. The connection closes when the block exits.
        """
        endpoint = ep.STREAM
        key = self._require_key()
        await self._ensure_limits(key)
        sent_at = await self._limiter.acquire()
        self._raise_if_stopped()
        self._sent += 1
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        query = dict(params)
        opened = self._cfg.request_timeout_s
        request = self._http.build_request(
            "GET",
            endpoint.url,
            params=query,
            headers=headers,
            timeout=httpx.Timeout(opened, read=None),
        )
        result: AttemptResult
        try:
            async with asyncio.timeout(opened):
                response = await self._http.send(request, stream=True)
        except (httpx.TimeoutException, TimeoutError) as exc:
            result = TransportFailure(ErrorKind.TIMEOUT, type(exc).__name__)
            self._log_stream_open(sent_at, query, result)
            raise SkylitStreamError(type(exc).__name__) from None
        except (httpx.HTTPError, httpx.StreamError) as exc:
            result = TransportFailure(ErrorKind.NETWORK, type(exc).__name__)
            self._log_stream_open(sent_at, query, result)
            raise SkylitStreamError(type(exc).__name__) from None
        try:
            received_at = self._clock.now()
            status = response.status_code
            code = None
            if not 200 <= status <= 299:
                code = _safe_code(error_code_of(_json_body(await response.aread())))
            result = HttpResult(status, RateHeaders.parse(response.headers, received_at), code)
            self._limiter.observe(result.headers)
            decision = self._log_stream_open(sent_at, query, result)
            if decision.action is Action.STOPPED:
                self._stopped = SkylitStoppedError(status, code, endpoint.host, endpoint.path)
                self._raise_if_stopped()
            if not 200 <= status <= 299:
                if decision.action is Action.RETRY and decision.hold_all:
                    self._limiter.hold_until(received_at + decision.wait_ns)
                raise SkylitStreamError(f"HTTP {status}" + (f" ({code})" if code else ""))
            yield _sse_messages(response, last_event_id)
        finally:
            await response.aclose()

    def _log_stream_open(
        self, sent_at: Instant, params: Mapping[str, str], result: AttemptResult
    ) -> Decision:
        received_at = self._clock.now()
        decision = decide(result, 1, now=received_at, rng=self._rng)
        self._fetch_log.record(
            FetchLogEntry.from_attempt(
                sent_at=sent_at,
                host=ep.STREAM.host.value,
                path=ep.STREAM.path,
                params=params,
                attempt=1,
                result=result,
                decision=decision,
                duration_ns=max(0, received_at - sent_at),
            )
        )
        return decision

    # ------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        # Never show the key or the environment.
        return (
            f"SkylitClient(limits={self._limits!r}, stopped={self._stopped is not None}, "
            f"attempts_sent={self._sent})"
        )

    # ------------------------------------------------------------ internals

    def _require_key(self) -> str:
        self._raise_if_stopped()
        key = self._env.get(KEY_VARIABLE)
        if key is None:
            raise SkylitKeyMissingError
        return key

    def _raise_if_stopped(self) -> None:
        """Raise a fresh copy of the stop error, so tracebacks do not pile up on one instance."""
        stop = self._stopped
        if stop is not None:
            raise SkylitStoppedError(stop.status, stop.error_code, stop.host, stop.path)

    async def _ensure_limits(self, key: str) -> None:
        """Run the ``GET /v1/account`` bootstrap once, before the first other request."""
        if self._limits is not None:
            return
        async with self._bootstrap_lock:
            if self.limits is not None:  # another caller ran the bootstrap meanwhile
                return
            result = await self._request(ep.ACCOUNT.host, ep.ACCOUNT.path, {}, key)
            info: AccountInfo | Failed
            if isinstance(result, Failed):
                info = result
            else:
                info = _parse_or_fail(result, {}, AccountInfo.parse)
            rpm: int | None = None
            hif: int | None = None
            if isinstance(info, AccountInfo):
                rpm = info.limits.requests_per_minute
                hif = info.limits.historical_in_flight
            limits = Limits(
                requests_per_minute=rpm or self._cfg.fallback_requests_per_minute,
                historical_in_flight=hif or self._cfg.fallback_historical_in_flight,
                requests_per_minute_from_account=rpm is not None,
                historical_in_flight_from_account=hif is not None,
            )
            self._limiter.set_requests_per_minute(limits.requests_per_minute)
            self._replay_slots = asyncio.Semaphore(limits.historical_in_flight)
            self._account = info
            self._limits = limits

    async def _typed[T](
        self, endpoint: Endpoint, params: Mapping[str, str], parse: Callable[[object], T]
    ) -> T | Failed:
        result = await self.call(endpoint, params)
        if isinstance(result, Failed):
            return result
        return _parse_or_fail(result, params, parse)

    async def _request(
        self, host: Host, path: str, params: dict[str, str], key: str
    ) -> Response | Failed:
        """Attempts 1 to 5 of one request under pacing, retries and the Fetch_Log."""
        url = host.base_url + path
        attempt = 1
        while True:
            sent_at = await self._limiter.acquire()
            self._raise_if_stopped()  # another request may have been stopped meanwhile
            self._sent += 1
            result, body = await self._send(url, params, key)
            received_at = self._clock.now()
            if isinstance(result, HttpResult):
                self._limiter.observe(result.headers)
            decision = decide(result, attempt, now=received_at, rng=self._rng)
            self._fetch_log.record(
                FetchLogEntry.from_attempt(
                    sent_at=sent_at,
                    host=host.value,
                    path=path,
                    params=params,
                    attempt=attempt,
                    result=result,
                    decision=decision,
                    duration_ns=max(0, received_at - sent_at),
                )
            )
            match decision.action:
                case Action.OK:
                    assert isinstance(result, HttpResult)
                    return Response(host, path, result.status, result.headers, attempt, body)
                case Action.RETRY:
                    if decision.hold_all:
                        self._limiter.hold_until(received_at + decision.wait_ns)
                    else:
                        await self._clock.sleep(decision.wait_ns)
                    attempt += 1
                case Action.FAILED | Action.REJECTED:
                    return _failed(host, path, params, decision.action, attempt, result)
                case Action.STOPPED:
                    assert isinstance(result, HttpResult)
                    self._stopped = SkylitStoppedError(result.status, result.error_code, host, path)
                    self._raise_if_stopped()

    async def _send(
        self, url: str, params: Mapping[str, str], key: str
    ) -> tuple[AttemptResult, object]:
        """One attempt: the result for the retry policy and the decoded body (or ``None``)."""
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        timeout = self._cfg.request_timeout_s
        try:
            # httpx times each phase; the outer timeout bounds the whole response.
            async with asyncio.timeout(timeout):
                response = await self._http.get(
                    url, params=params, headers=headers, timeout=timeout
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            return TransportFailure(ErrorKind.TIMEOUT, type(exc).__name__), None
        except (httpx.HTTPError, httpx.StreamError) as exc:
            return TransportFailure(ErrorKind.NETWORK, type(exc).__name__), None
        received_at = self._clock.now()
        body = _json_body(response.content)
        status = response.status_code
        code = None if 200 <= status <= 299 else _safe_code(error_code_of(body))
        return HttpResult(status, RateHeaders.parse(response.headers, received_at), code), body


async def _sse_messages(
    response: httpx.Response, last_event_id: str | None
) -> AsyncIterator[SseMessage]:
    decoder = SseDecoder(last_event_id)
    async for line in response.aiter_lines():
        message = decoder.feed(line.rstrip("\r\n"))
        if message is not None:
            yield message


def _parse_or_fail[T](
    response: Response, params: Mapping[str, str], parse: Callable[[object], T]
) -> T | Failed:
    try:
        return parse(response.body)
    except MalformedResponseError as exc:
        return Failed(
            host=response.host,
            path=response.path,
            params=MappingProxyType(dict(params)),
            outcome="malformed",
            attempts=response.attempts,
            status=response.status,
            error_type=MALFORMED_RESPONSE,
            detail=str(exc),
        )


def _failed(
    host: Host,
    path: str,
    params: Mapping[str, str],
    action: Action,
    attempts: int,
    result: AttemptResult,
) -> Failed:
    outcome: FailureOutcome = "failed" if action is Action.FAILED else "rejected"
    frozen = MappingProxyType(dict(params))
    if isinstance(result, TransportFailure):
        return Failed(host, path, frozen, outcome, attempts, error_type=result.error_type)
    return Failed(
        host, path, frozen, outcome, attempts, status=result.status, error_code=result.error_code
    )


def _json_body(content: bytes) -> object:
    """The decoded body, or ``None`` when it is empty or not JSON (the error is dropped)."""
    if not content:
        return None
    try:
        return json.loads(content)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError keep the body text
        return None


def _safe_code(code: str | None) -> str | None:
    """Keep a server error code only when it has the shape of a code."""
    return code if code is not None and _SAFE_CODE.fullmatch(code) else None
