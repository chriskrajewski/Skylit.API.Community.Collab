"""ProjectX bar history: ``POST /api/History/retrieveBars`` (design §4, Req 4.3, OQ9).

Sources (official ProjectX Gateway docs; content rephrased for compliance with
licensing restrictions):

- https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/
  (request and response fields, at most 20,000 bars per request);
- https://gateway.docs.projectx.com/docs/getting-started/rate-limits/
  (``retrieveBars``: 50 requests per 30 seconds; HTTP 429 when exceeded).

:class:`ProjectXBars` is the alternate bar source the Bar_Source uses when
Atlas does not serve an instrument (Req 4.3), and the bar feed of the
Live_Runner.

- **Windows.** ``[start_ns, end_ns]`` is split into contiguous request windows
  of at most ``bars_per_request`` bars (default 20,000), each a whole number of
  bars long. Both ends must lie on the bar grid, so window seams fall on bar
  boundaries whichever way ProjectX treats ``endTime``.
- **Bar size (OQ9).** Whole minutes use minute bars; any other interval uses
  second bars, so ``--bar-interval 5`` asks for ``unit=1, unitNumber=5``.
- **Pacing.** Every attempt, retries included, passes through one
  :class:`~fse.skylit.ratelimit.RateLimiter` with a rolling 30 s window,
  default 45 sends, which keeps the client under the documented 50 with room
  for network jitter. Share one limiter between every user of the same
  ProjectX login (:func:`bars_rate_limiter`).
- **Retries** follow the Skylit policy (:func:`fse.skylit.retry.decide`): 429
  holds every bar request (``Retry-After`` if sent, else 60 s plus jitter);
  500/502/503/504, network errors and timeouts back off with full jitter; 5
  attempts in all. A 401 renews the session token once
  (:meth:`ProjectXSession.refresh`) and resends without counting an attempt.
  A second 401, a 402 or a 403 raises
  :class:`~fse.projectx.session.ProjectXAuthError` (exit 3).
- **Results.** Bars are normalized to :class:`~fse.engine.types.Bar` (open and
  close Instants, prices as received and as ticks) and returned oldest first.
  A window that fails (after retries, another HTTP status, ``success: false``,
  or an unusable body) contributes no bars and one
  :class:`~fse.projectx.models.BarFetchFailure` naming the cause; the next
  window is still fetched. Nothing is synthesized (Req 4.8).
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from typing import Final

import httpx

from fse.clock import Clock
from fse.engine.types import Bar
from fse.projectx.models import (
    MAX_BARS_PER_REQUEST,
    BarFetchFailure,
    BarHistory,
    RetrieveBarsRequest,
    RetrieveBarsResponse,
    bar_unit_for,
    to_bar,
)
from fse.projectx.session import (
    API_KEY_VARIABLE,
    DEFAULT_TIMEOUT_S,
    USERNAME_VARIABLE,
    ProjectXAuthError,
    ProjectXSession,
)
from fse.skylit.ratelimit import RateHeaders, RateLimiter
from fse.skylit.retry import (
    Action,
    AttemptResult,
    ErrorKind,
    HttpResult,
    TransportFailure,
    decide,
)
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "BARS_RATE_LIMIT",
    "BARS_RATE_WINDOW_NS",
    "DEFAULT_BARS_PER_WINDOW",
    "DUPLICATE_BAR",
    "MALFORMED_RESPONSE",
    "MISALIGNED_BAR",
    "RETRIEVE_BARS_PATH",
    "ProjectXBars",
    "bars_rate_limiter",
    "request_windows",
]

RETRIEVE_BARS_PATH: Final = "/api/History/retrieveBars"

BARS_RATE_LIMIT: Final = 50
"""Documented ``retrieveBars`` limit: requests per :data:`BARS_RATE_WINDOW_NS`."""
BARS_RATE_WINDOW_NS: Final = 30 * NS_PER_SECOND

DEFAULT_BARS_PER_WINDOW: Final = 45
"""Sends allowed in any rolling 30 s window: under the documented 50."""

# BarFetchFailure.error_type values for an answer that cannot be used.
MALFORMED_RESPONSE: Final = "malformed_response"
DUPLICATE_BAR: Final = "duplicate_bar"
MISALIGNED_BAR: Final = "misaligned_bar"


def bars_rate_limiter(clock: Clock, sends_per_window: int = DEFAULT_BARS_PER_WINDOW) -> RateLimiter:
    """A limiter of ``sends_per_window`` (1 to 49) sends per rolling 30 s window."""
    if isinstance(sends_per_window, bool) or not isinstance(sends_per_window, int):
        raise TypeError(f"sends_per_window must be an int, got {type(sends_per_window).__name__}")
    if not 1 <= sends_per_window < BARS_RATE_LIMIT:
        raise ValueError(
            f"sends_per_window must be from 1 to {BARS_RATE_LIMIT - 1} to stay under the "
            f"ProjectX limit of {BARS_RATE_LIMIT} per 30 s, got {sends_per_window}"
        )
    return RateLimiter(clock, sends_per_window, low_water=0, window_ns=BARS_RATE_WINDOW_NS)


def request_windows(
    start_ns: Instant, end_ns: Instant, interval_ns: int, bars_per_request: int
) -> list[tuple[Instant, Instant]]:
    """Contiguous ``(start, end)`` windows of at most ``bars_per_request`` bars each."""
    if interval_ns <= 0 or bars_per_request <= 0:
        raise ValueError("interval_ns and bars_per_request must be positive")
    if end_ns <= start_ns:
        raise ValueError("end_ns must be after start_ns")
    span = interval_ns * bars_per_request
    windows: list[tuple[Instant, Instant]] = []
    lo = start_ns
    while lo < end_ns:
        hi = min(lo + span, end_ns)
        windows.append((lo, hi))
        lo = hi
    return windows


class ProjectXBars:
    """Fetches closed bars for one ProjectX contract, paced and retried."""

    __slots__ = (
        "_bars_per_request",
        "_clock",
        "_limiter",
        "_live",
        "_rng",
        "_session",
        "_timeout_s",
    )

    def __init__(
        self,
        session: ProjectXSession,
        clock: Clock,
        rng: random.Random,
        *,
        limiter: RateLimiter | None = None,
        live: bool = False,
        bars_per_request: int = MAX_BARS_PER_REQUEST,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """``limiter`` defaults to :func:`bars_rate_limiter`; ``live`` picks the live data feed."""
        if isinstance(bars_per_request, bool) or not 1 <= bars_per_request <= MAX_BARS_PER_REQUEST:
            raise ValueError(
                f"bars_per_request must be from 1 to {MAX_BARS_PER_REQUEST}, "
                f"got {bars_per_request!r}"
            )
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s}")
        self._session = session
        self._clock = clock
        self._rng = rng
        self._limiter = bars_rate_limiter(clock) if limiter is None else limiter
        self._live = live
        self._bars_per_request = bars_per_request
        self._timeout_s = timeout_s

    @property
    def limiter(self) -> RateLimiter:
        return self._limiter

    async def retrieve(
        self,
        *,
        instrument: str,
        contract: str,
        contract_id: str,
        start_ns: Instant,
        end_ns: Instant,
        interval_s: int,
    ) -> BarHistory:
        """Each bar of ``contract_id`` opening at or after ``start_ns`` and closing by ``end_ns``.

        ``instrument`` (``MES``) and ``contract`` (``MESH6``) label the bars;
        ``contract_id`` is the ProjectX id from the roll calendar. Both ends
        must be multiples of ``interval_s`` seconds since the Unix epoch.
        """
        unit, unit_number = bar_unit_for(interval_s)
        for name, value in (
            ("instrument", instrument),
            ("contract", contract),
            ("contract_id", contract_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        interval_ns = interval_s * NS_PER_SECOND
        if end_ns <= start_ns:
            raise ValueError("end_ns must be after start_ns")
        if start_ns % interval_ns or end_ns % interval_ns:
            raise ValueError(f"start_ns and end_ns must lie on the {interval_s} s bar grid")

        bars: list[Bar] = []
        failures: list[BarFetchFailure] = []
        sent = 0
        for lo, hi in request_windows(start_ns, end_ns, interval_ns, self._bars_per_request):
            request = RetrieveBarsRequest(
                contract_id=contract_id,
                live=self._live,
                start_ns=lo,
                end_ns=hi,
                unit=unit,
                unit_number=unit_number,
                limit=(hi - lo) // interval_ns,
            )
            response, attempts = await self._send(request)
            sent += attempts
            if isinstance(response, BarFetchFailure):
                failures.append(response)
                continue
            outcome = _window_bars(
                response, request, instrument=instrument, contract=contract, interval_s=interval_s
            )
            if isinstance(outcome, BarFetchFailure):
                failures.append(outcome)
            else:
                bars.extend(outcome)
        return BarHistory(
            instrument=instrument,
            contract=contract,
            contract_id=contract_id,
            interval_s=interval_s,
            start_ns=start_ns,
            end_ns=end_ns,
            bars=tuple(bars),
            failures=tuple(failures),
            requests=sent,
        )

    async def _send(
        self, request: RetrieveBarsRequest
    ) -> tuple[httpx.Response | BarFetchFailure, int]:
        """POST one window until it succeeds, fails for good, or auth stops the run."""
        payload = request.to_json()
        attempt = 1
        sent = 0
        renewed = False
        while True:
            token = await self._session.token()
            await self._limiter.acquire()  # immediately before the send
            sent += 1
            result, response = await self._post(payload, token)
            if isinstance(result, HttpResult) and result.status == 401 and not renewed:
                renewed = True
                await self._session.refresh(token)
                continue
            now = self._clock.now()
            decision = decide(result, attempt, now=now, rng=self._rng)
            match decision.action:
                case Action.OK:
                    assert response is not None  # an HttpResult always has its response
                    return response, sent
                case Action.RETRY:
                    if decision.hold_all:
                        self._limiter.hold_until(now + decision.wait_ns)
                    else:
                        await self._clock.sleep(decision.wait_ns)
                    attempt += 1
                case Action.STOPPED:
                    status = result.status if isinstance(result, HttpResult) else None
                    raise ProjectXAuthError(
                        f"ProjectX retrieveBars was refused with HTTP {status}; check "
                        f"{USERNAME_VARIABLE}, {API_KEY_VARIABLE} and the API subscription"
                    )
                case Action.FAILED | Action.REJECTED:
                    return _failure(request, result), sent

    async def _post(
        self, payload: dict[str, object], token: str
    ) -> tuple[AttemptResult, httpx.Response | None]:
        try:
            response = await self._session.http.post(
                self._session.url(RETRIEVE_BARS_PATH),
                json=payload,
                headers=ProjectXSession.auth_headers(token),
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException as exc:
            return TransportFailure(ErrorKind.TIMEOUT, type(exc).__name__), None
        except (httpx.HTTPError, httpx.StreamError) as exc:
            return TransportFailure(ErrorKind.NETWORK, type(exc).__name__), None
        headers = RateHeaders.parse(response.headers, self._clock.now())
        return HttpResult(response.status_code, headers), response

    def __repr__(self) -> str:
        return (
            f"ProjectXBars(live={self._live}, bars_per_request={self._bars_per_request}, "
            f"limiter={self._limiter!r})"
        )


def _failure(request: RetrieveBarsRequest, result: AttemptResult) -> BarFetchFailure:
    if isinstance(result, TransportFailure):
        return BarFetchFailure(request.start_ns, request.end_ns, error_type=result.error_type)
    return BarFetchFailure(request.start_ns, request.end_ns, status=result.status)


def _window_bars(
    response: httpx.Response,
    request: RetrieveBarsRequest,
    *,
    instrument: str,
    contract: str,
    interval_s: int,
) -> Sequence[Bar] | BarFetchFailure:
    """The bars of one successful answer that open inside the request window, oldest first."""
    lo, hi = request.start_ns, request.end_ns
    try:
        parsed = RetrieveBarsResponse.parse(json.loads(response.content))
    except ValueError:  # MalformedResponseError, JSONDecodeError or UnicodeDecodeError
        return BarFetchFailure(lo, hi, error_type=MALFORMED_RESPONSE)
    if not parsed.success or parsed.error_code != 0:
        return BarFetchFailure(lo, hi, error_code=parsed.error_code)
    interval_ns = interval_s * NS_PER_SECOND
    seen: set[Instant] = set()
    for wire in parsed.bars:
        if wire.t_ns % interval_ns:
            return BarFetchFailure(lo, hi, error_type=MISALIGNED_BAR)
        if wire.t_ns in seen:
            return BarFetchFailure(lo, hi, error_type=DUPLICATE_BAR)
        seen.add(wire.t_ns)
    inside = sorted((w for w in parsed.bars if lo <= w.t_ns < hi), key=lambda w: w.t_ns)
    return [
        to_bar(w, instrument=instrument, contract=contract, interval_s=interval_s) for w in inside
    ]
