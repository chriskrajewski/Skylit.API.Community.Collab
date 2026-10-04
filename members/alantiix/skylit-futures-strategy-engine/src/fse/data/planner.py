"""The pull planner (design §3 "Planning", D11 and OQ3; Req 3.3-3.5, 3.8, 3.14).

:func:`plan_pull` turns a :class:`PullSpec` into the ordered Replay_Requests
the puller sends. It makes no network request and reads no clock: the caller
passes the ``GET /v1/symbols`` result (each listed symbol with its first
history date), the instant the pull started, the exchange session calendar and
the Data_Cache.

1. Validate before any Replay_Request (Req 3.14): the start date is not after
   the end date, the end date is not after the pull date (the New York date of
   the pull start), every configured symbol is listed, and the exchange
   calendar covers the range. Every problem found is raised together as one
   :class:`PullPlanError` (exit 2).
2. Sessions come from the exchange calendar. Each session's Pull_Window is
   tiled into 15-minute Cache_Windows from its start; the last window may be
   shorter (Glossary).
3. A symbol's sessions dated before its first history date get no request and
   are recorded as skipped (Req 3.5).
4. A ``complete`` or ``no_data`` window of a completed session (one whose
   Pull_Window ended before the pull started) is served from the Data_Cache
   (Req 3.8).
5. Every other window is fetched, for each metric:

   - a session less than 365 days before the pull date: one
     ``GET /v1/historical/range`` request spanning the window per group of up
     to 5 symbols (Req 3.3);
   - an older session: ``GET /v1/historical`` at each instant of the
     sample-interval grid (the Pull_Window start and every multiple of the
     interval after it, before the Pull_Window end) that falls in the window,
     per group of up to 10 symbols (Req 3.4).

   With ``label_sample_minutes`` N (``--label-sample-minutes``, OQ3), each
   fetched window of a range session also gets ``GET /v1/historical`` at the
   instants of the N-minute grid inside it, because range frames carry no
   ``nodeType`` labels.
6. Sessions are ordered newest first (D11). Inside a session, windows run in
   time order, then metrics in configured order.

Each request carries one metric and covers exactly one Cache_Window of every
symbol it names. Symbol groups keep the configured symbol order.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import ClassVar, Final, Literal, Protocol

from fse.data.cache import (
    CacheWindowKey,
    HeatmapView,
    cache_window_end,
    cache_window_starts,
)
from fse.data.cache_io import check_component
from fse.data.catalog import FINAL_STATUSES, WindowStatus
from fse.engine.types import METRICS, Metric
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, ny_datetime

__all__ = [
    "DEFAULT_METRICS",
    "DEFAULT_SAMPLE_INTERVAL_MINUTES",
    "DEFAULT_SYMBOLS",
    "DEFAULT_VIEW",
    "EXIT_INVALID_INPUT",
    "HISTORICAL_MAX_SYMBOLS",
    "LABEL_SAMPLE_MAX_MINUTES",
    "LABEL_SAMPLE_MIN_MINUTES",
    "RANGE_MAX_AGE_DAYS",
    "RANGE_MAX_SYMBOLS",
    "REPLAY_PATHS",
    "SAMPLE_INTERVAL_MAX_MINUTES",
    "SAMPLE_INTERVAL_MIN_MINUTES",
    "PullPlan",
    "PullPlanError",
    "PullSpec",
    "ReplayEndpoint",
    "ReplayRequest",
    "SessionPlan",
    "WindowPlan",
    "WindowStatusLookup",
    "grid_instants",
    "plan_pull",
    "pull_date",
    "validate_pull",
]

# Design "Exit codes": 2 = invalid input, which includes the pull date range.
EXIT_INVALID_INPUT: Final = 2

DEFAULT_SYMBOLS: Final[tuple[str, ...]] = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
DEFAULT_METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")

RANGE_MAX_AGE_DAYS: Final = 365
"""Sessions this many days or more before the pull date use ``/v1/historical``."""
RANGE_MAX_SYMBOLS: Final = 5
HISTORICAL_MAX_SYMBOLS: Final = 10

SAMPLE_INTERVAL_MIN_MINUTES: Final = 1
SAMPLE_INTERVAL_MAX_MINUTES: Final = 15
DEFAULT_SAMPLE_INTERVAL_MINUTES: Final = 1
LABEL_SAMPLE_MIN_MINUTES: Final = 1
LABEL_SAMPLE_MAX_MINUTES: Final = 60

DEFAULT_VIEW: Final = HeatmapView()
"""The Skylit defaults: 92 strikes, 5 expirations, ``includeEmpty=false``."""

type ReplayEndpoint = Literal["range", "historical"]

REPLAY_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {"range": "/v1/historical/range", "historical": "/v1/historical"}
)
_MAX_SYMBOLS: Final[Mapping[str, int]] = MappingProxyType(
    {"range": RANGE_MAX_SYMBOLS, "historical": HISTORICAL_MAX_SYMBOLS}
)
_ONE_DAY: Final = timedelta(days=1)


class PullPlanError(Exception):
    """The pull cannot start; one line per problem (Req 3.14, exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__("\n".join(self.problems))


class WindowStatusLookup(Protocol):
    """The Data_Cache status of a Cache_Window; :class:`~fse.data.cache.DataCache` is one."""

    def status(self, key: CacheWindowKey) -> WindowStatus: ...


# ---------------------------------------------------------------- inputs


def _is_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _int_problem(name: str, value: object, lo: int, hi: int, unit: str) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi:
        return None
    return f"{name} must be a whole number of {unit} from {lo} to {hi}, got {value!r}"


def _name_problems(what: str, values: object, allowed: frozenset[str] | None) -> list[str]:
    if not isinstance(values, tuple) or not values:
        return [f"at least one {what} must be configured, as a tuple, got {values!r}"]
    out: list[str] = []
    seen: set[object] = set()
    for value in values:
        if allowed is None:
            try:
                check_component(what, value)
            except ValueError as exc:
                out.append(str(exc))
                continue
        elif not (isinstance(value, str) and value in allowed):
            out.append(f"{what} must be one of {sorted(allowed)}, got {value!r}")
            continue
        if value in seen:
            out.append(f"{what} {value} is configured more than once")
        seen.add(value)
    return out


@dataclass(frozen=True, slots=True)
class PullSpec:
    """What the Operator asked to pull. Defaults are the requirement defaults.

    ``start`` and ``end`` are inclusive session dates. ``sample_interval_minutes``
    sets the ``/v1/historical`` grid of sessions 365 or more days old (Req 3.4).
    ``label_sample_minutes`` (off by default) adds ``/v1/historical`` label
    samples to range sessions (OQ3). :func:`validate_pull` checks every field.
    """

    start: date
    end: date
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    metrics: tuple[Metric, ...] = DEFAULT_METRICS
    view: HeatmapView = DEFAULT_VIEW
    sample_interval_minutes: int = DEFAULT_SAMPLE_INTERVAL_MINUTES
    label_sample_minutes: int | None = None

    def problems(self) -> list[str]:
        """Every problem with the fields themselves; ``[]`` when they are valid."""
        out: list[str] = []
        for name in ("start", "end"):
            value: object = getattr(self, name)
            if not _is_date(value):
                out.append(f"pull {name} date must be a date, got {value!r}")
        out += _name_problems("symbol", self.symbols, None)
        out += _name_problems("metric", self.metrics, METRICS)
        sample = _int_problem(
            "sample interval",
            self.sample_interval_minutes,
            SAMPLE_INTERVAL_MIN_MINUTES,
            SAMPLE_INTERVAL_MAX_MINUTES,
            "minutes",
        )
        if sample is not None:
            out.append(sample)
        if self.label_sample_minutes is not None:
            label = _int_problem(
                "label sample interval",
                self.label_sample_minutes,
                LABEL_SAMPLE_MIN_MINUTES,
                LABEL_SAMPLE_MAX_MINUTES,
                "minutes",
            )
            if label is not None:
                out.append(label)
        return out


def pull_date(started_ns: Instant) -> date:
    """The pull date: the America/New_York date of the instant the pull started."""
    return ny_datetime(started_ns).date()


def _first_uncovered_weekday(calendar: SessionCalendar, start: date, end: date) -> date | None:
    """The first weekday of ``[start, end]`` outside the calendar, else ``None``.

    Outside its covered range the calendar knows no holidays, so any weekday
    there may be a session. Each scan meets a weekday within three days.
    """
    d = start
    while d <= end and d < calendar.first:
        if d.weekday() < 5:
            return d
        d += _ONE_DAY  # d < calendar.first, so no overflow
    if end > calendar.last:
        d = max(start, calendar.last + _ONE_DAY)  # calendar.last < end, so no overflow
        while d.weekday() >= 5:
            if d == end:
                return None
            d += _ONE_DAY
        return d
    return None


def validate_pull(
    spec: PullSpec,
    *,
    listed: Mapping[str, date],
    today: date,
    calendar: SessionCalendar,
) -> tuple[date, ...]:
    """Check the pull before any Replay_Request; return its sessions, oldest first.

    ``listed`` is the ``GET /v1/symbols`` result: each listed symbol with its
    first history date (``history.from``). ``today`` is the pull date. Raises
    :class:`PullPlanError` naming every invalid date, invalid field, unlisted
    symbol and uncovered weekday it finds (Req 3.14).
    """
    problems = spec.problems()
    dates_ok = _is_date(spec.start) and _is_date(spec.end)
    if dates_ok:
        if spec.start > spec.end:
            problems.append(f"pull start date {spec.start} is after the end date {spec.end}")
        if spec.end > today:
            problems.append(f"pull end date {spec.end} is after the current date {today}")
    if isinstance(spec.symbols, tuple):
        for symbol in dict.fromkeys(spec.symbols):
            if symbol not in listed:
                problems.append(f"symbol {symbol} is not listed by GET /v1/symbols")
            elif not _is_date(listed[symbol]):
                problems.append(
                    f"GET /v1/symbols gives symbol {symbol} no first history date: "
                    f"{listed[symbol]!r}"
                )
    sessions: tuple[date, ...] = ()
    if dates_ok and spec.start <= spec.end:
        gap = _first_uncovered_weekday(calendar, spec.start, spec.end)
        if gap is not None:
            problems.append(
                f"the exchange calendar covers {calendar.first} to {calendar.last}, not {gap}, "
                f"a weekday of the pull range {spec.start} to {spec.end}"
            )
        else:
            lo, hi = max(spec.start, calendar.first), min(spec.end, calendar.last)
            sessions = calendar.sessions(lo, hi) if lo <= hi else ()
    if problems:
        raise PullPlanError(problems)
    return sessions


# ---------------------------------------------------------------- the plan


def grid_instants(origin_ns: Instant, step_ns: int, lo_ns: Instant, hi_ns: Instant) -> range:
    """Instants ``origin + k * step`` (k >= 0) with ``lo <= t < hi``."""
    if step_ns <= 0:
        raise ValueError(f"grid step must be positive, got {step_ns}")
    k = max(0, -((origin_ns - lo_ns) // step_ns))  # ceil((lo - origin) / step), at least 0
    return range(origin_ns + k * step_ns, hi_ns, step_ns)


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """One planned Replay_Request: one metric, one Cache_Window, up to 5 or 10 symbols.

    A ``range`` request spans the window from ``window_start_ns`` to
    ``window_end_ns``; a ``historical`` request asks for the Snapshot at
    ``at_ns``, which lies in ``[window_start_ns, window_end_ns)``.
    ``label_sample`` marks a ``--label-sample-minutes`` request on a range
    session.
    """

    endpoint: ReplayEndpoint
    metric: Metric
    symbols: tuple[str, ...]
    view_id: str
    session: date
    window_start_ns: Instant
    window_end_ns: Instant
    at_ns: Instant | None = None
    label_sample: bool = False

    def __post_init__(self) -> None:
        cap = _MAX_SYMBOLS.get(self.endpoint)
        if cap is None:
            raise ValueError(f"endpoint must be range or historical, got {self.endpoint!r}")
        if not 1 <= len(self.symbols) <= cap:
            raise ValueError(f"a {self.endpoint} request carries 1 to {cap} symbols")
        if not self.window_start_ns < self.window_end_ns:
            raise ValueError("the window must end after it starts")
        if self.endpoint == "range":
            if self.at_ns is not None or self.label_sample:
                raise ValueError("a range request has no at_ns and is never a label sample")
        elif self.at_ns is None or not self.window_start_ns <= self.at_ns < self.window_end_ns:
            raise ValueError("a historical request needs at_ns inside its window")

    @property
    def path(self) -> str:
        return REPLAY_PATHS[self.endpoint]

    def keys(self) -> tuple[CacheWindowKey, ...]:
        """The Cache_Window of each symbol this request fills."""
        return tuple(
            CacheWindowKey(s, self.metric, self.view_id, self.session, self.window_start_ns)
            for s in self.symbols
        )


@dataclass(frozen=True, slots=True)
class WindowPlan:
    """One Cache_Window slot of a session, across symbols and metrics.

    ``served`` keys are read from the Data_Cache (Req 3.8); ``fetch`` keys are
    filled by ``requests``. A symbol skipped for the session is in neither.
    """

    session: date
    start_ns: Instant
    end_ns: Instant
    served: tuple[CacheWindowKey, ...]
    fetch: tuple[CacheWindowKey, ...]
    requests: tuple[ReplayRequest, ...]


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """One session: its endpoint, cache use, skipped symbols and windows."""

    session: date
    age_days: int  # calendar days before the pull date
    endpoint: ReplayEndpoint
    completed: bool  # the Pull_Window ended before the pull started
    pull_start_ns: Instant
    pull_end_ns: Instant
    symbols: tuple[str, ...]  # on or after their first history date
    skipped_symbols: tuple[str, ...]  # before their first history date (Req 3.5)
    windows: tuple[WindowPlan, ...]

    def requests(self) -> Iterator[ReplayRequest]:
        for window in self.windows:
            yield from window.requests


@dataclass(frozen=True, slots=True)
class PullPlan:
    """Every session of the pull, newest first, with its Replay_Requests."""

    spec: PullSpec
    started_ns: Instant
    today: date
    view_id: str
    sessions: tuple[SessionPlan, ...]

    def requests(self) -> Iterator[ReplayRequest]:
        """Every Replay_Request in send order."""
        for session in self.sessions:
            yield from session.requests()

    @property
    def request_count(self) -> int:
        return sum(len(w.requests) for s in self.sessions for w in s.windows)

    def served_keys(self) -> Iterator[CacheWindowKey]:
        for session in self.sessions:
            for window in session.windows:
                yield from window.served

    def fetch_keys(self) -> Iterator[CacheWindowKey]:
        for session in self.sessions:
            for window in session.windows:
                yield from window.fetch

    def skipped(self) -> Iterator[tuple[str, date]]:
        """``(symbol, session)`` for each session dated before the symbol's first history."""
        for session in self.sessions:
            for symbol in session.skipped_symbols:
                yield symbol, session.session


def _groups(symbols: Sequence[str], size: int) -> Iterator[tuple[str, ...]]:
    """Consecutive groups of at most ``size`` symbols, in the given order."""
    for i in range(0, len(symbols), size):
        yield tuple(symbols[i : i + size])


@dataclass(frozen=True, slots=True)
class _Window:
    """One Cache_Window of one metric, while its requests are planned."""

    metric: Metric
    view_id: str
    session: date
    start_ns: Instant
    end_ns: Instant

    def range_requests(self, symbols: Sequence[str]) -> list[ReplayRequest]:
        return [
            ReplayRequest(
                "range", self.metric, group, self.view_id, self.session, self.start_ns, self.end_ns
            )
            for group in _groups(symbols, RANGE_MAX_SYMBOLS)
        ]

    def historical_requests(
        self, symbols: Sequence[str], at_ns: Instant, *, label_sample: bool
    ) -> list[ReplayRequest]:
        return [
            ReplayRequest(
                "historical",
                self.metric,
                group,
                self.view_id,
                self.session,
                self.start_ns,
                self.end_ns,
                at_ns=at_ns,
                label_sample=label_sample,
            )
            for group in _groups(symbols, HISTORICAL_MAX_SYMBOLS)
        ]


def plan_pull(
    spec: PullSpec,
    *,
    listed: Mapping[str, date],
    started_ns: Instant,
    calendar: SessionCalendar,
    cache: WindowStatusLookup,
) -> PullPlan:
    """Validate the pull, then plan every session newest first.

    ``listed`` is the ``GET /v1/symbols`` result (symbol to first history
    date), ``started_ns`` the instant the pull started, ``calendar`` the
    exchange calendar's sessions and ``cache`` the Data_Cache. Raises
    :class:`PullPlanError` before planning anything when the pull is invalid.
    """
    today = pull_date(started_ns)
    sessions = validate_pull(spec, listed=listed, today=today, calendar=calendar)
    view_id = spec.view.view_id()
    plans = tuple(
        _plan_session(spec, session, listed, today, started_ns, calendar, cache, view_id)
        for session in reversed(sessions)
    )
    return PullPlan(spec=spec, started_ns=started_ns, today=today, view_id=view_id, sessions=plans)


def _plan_session(
    spec: PullSpec,
    session: date,
    listed: Mapping[str, date],
    today: date,
    started_ns: Instant,
    calendar: SessionCalendar,
    cache: WindowStatusLookup,
    view_id: str,
) -> SessionPlan:
    pull_start, pull_end = calendar.pull_window(session)
    age_days = (today - session).days
    endpoint: ReplayEndpoint = "range" if age_days < RANGE_MAX_AGE_DAYS else "historical"
    completed = pull_end < started_ns
    active = tuple(s for s in spec.symbols if session >= listed[s])
    skipped = tuple(s for s in spec.symbols if session < listed[s])
    sample_step = spec.sample_interval_minutes * NS_PER_MINUTE
    label_step = (
        None if spec.label_sample_minutes is None else spec.label_sample_minutes * NS_PER_MINUTE
    )

    windows: list[WindowPlan] = []
    for start_ns in cache_window_starts(pull_start, pull_end):
        end_ns = cache_window_end(start_ns, pull_end)
        served: list[CacheWindowKey] = []
        fetch: list[CacheWindowKey] = []
        requests: list[ReplayRequest] = []
        for metric in spec.metrics:
            missing: list[str] = []
            for symbol in active:
                key = CacheWindowKey(symbol, metric, view_id, session, start_ns)
                if completed and cache.status(key) in FINAL_STATUSES:
                    served.append(key)
                else:
                    fetch.append(key)
                    missing.append(symbol)
            if not missing:
                continue
            window = _Window(metric, view_id, session, start_ns, end_ns)
            if endpoint == "range":
                requests += window.range_requests(missing)
                if label_step is not None:
                    for at_ns in grid_instants(pull_start, label_step, start_ns, end_ns):
                        requests += window.historical_requests(missing, at_ns, label_sample=True)
            else:
                for at_ns in grid_instants(pull_start, sample_step, start_ns, end_ns):
                    requests += window.historical_requests(missing, at_ns, label_sample=False)
        windows.append(
            WindowPlan(session, start_ns, end_ns, tuple(served), tuple(fetch), tuple(requests))
        )
    return SessionPlan(
        session=session,
        age_days=age_days,
        endpoint=endpoint,
        completed=completed,
        pull_start_ns=pull_start,
        pull_end_ns=pull_end,
        symbols=active,
        skipped_symbols=skipped,
        windows=tuple(windows),
    )
