"""Property 8: Interrupted windows stay incomplete.

*For any* pull interrupted at any point (Operator interrupt, crash, failed
request, write error), every Cache_Window without all its Snapshots durably
written is marked incomplete, and the next pull requests exactly the
incomplete and absent windows again.

Each example runs a chain of 1 to 3 faulted pulls, then one clean pull, over
one Data_Cache root. :func:`run_pull` drives the real :class:`SkylitClient`
against respx mocks in virtual time (:class:`FakeClock`) with a fake key.
Every pull opens a fresh client and :class:`DataCache`, as a new process
would. A fault fires at a generated heatmap HTTP attempt (0-based, counted per
pull):

- ``interrupt``: that attempt raises ``asyncio.CancelledError`` (Ctrl-C);
- ``crash``: the cache root is copied as it is on disk at that attempt and the
  run is abandoned. Later pulls run on the copy, so nothing done after the
  crash point (``finally`` blocks included) counts;
- ``stop``: the attempt answers 401, 402 or 403 (a Requirement 2 stop);
- ``failed``: that request and up to two later new ones fail on every attempt
  (400, 404 without ``no_data``, 503, a connect error or a malformed body),
  and the pull carries on;
- ``write_error``: a directory sits at the window file path of a generated
  pending key, so that key's write fails. It is removed after the pull, as
  the Operator would fix the disk.

Skylit's answers are a pure function of the request: range frames at
``from``, ``from + 1 s`` and ``to``; ``/v1/historical`` boards 1 s before
``at``. A generated set of (metric, instant, symbol) cells has no data: such a
symbol is left out of the answer, and a request whose symbols all lack data
gets ``404 no_data``. So the oracle knows each key's full content up front:
``[start, start + 1 s]`` for a range window, ``at - 1 s`` for each sample of a
historical window, minus the no-data cells.

After every faulted pull, on the state the next pull sees:

1. a ``complete`` or ``no_data`` key holds exactly its oracle Snapshots
   (``no_data`` exactly when the oracle is empty), read back through the
   catalog's sha256 and row count, and no request for it went unanswered;
2. every other key is ``incomplete`` or ``absent``, and ``incomplete`` when any
   request for it was sent (it is marked before its window's first request);
3. the next pull plans exactly the ``incomplete`` and ``absent`` keys (and
   serves the rest), requests no other key, and requests every one of them
   when it runs to the end.

The clean pull ends normally and leaves every key final with its oracle
content. A window whose sample grid has no instant in it (a 7-minute grid
and the 5-minute 10:00 window) gets no Replay_Request by Req 3.4, so it is
planned but not requested and stays absent.

**Validates: Requirements 3.9**
"""

from __future__ import annotations

import asyncio
import io
import random
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any, Final, Literal, cast

import httpx
import respx
from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.calendars import ContractPeriod, Covers, RollCalendar
from fse.data.cache import (
    CACHE_WINDOW_NS,
    CacheWindowKey,
    DataCache,
    HeatmapView,
    cache_window_end,
    cache_window_starts,
)
from fse.data.catalog import FINAL_STATUSES, WindowStatus
from fse.data.planner import PullSpec, plan_pull
from fse.data.puller import PullContext, PullOptions, run_pull
from fse.engine.types import Metric
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit import endpoints as ep
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Endpoint, format_rfc3339
from fse.skylit.fetch_log import FetchLog
from fse.timekit import (
    NS_PER_MINUTE,
    NS_PER_SECOND,
    Instant,
    SessionCalendar,
    SessionTimes,
    ny_datetime,
    ny_instant,
    parse_rfc3339,
)
from tests.fakes.clock import FakeClock

KEY: Final = "fake-skylit-key-0000"
S: Final = NS_PER_SECOND
NS_PER_HOUR: Final = 60 * NS_PER_MINUTE
VIEW_ID: Final = HeatmapView().view_id()

# A short Pull_Window: three Cache_Windows, the last one 5 minutes long.
TIMES: Final = SessionTimes(pull_start=time(9, 30), pull_end=time(10, 5))
CALENDAR: Final = SessionCalendar(date(2025, 1, 2), date(2026, 3, 31), times=TIMES)
ROLL: Final = RollCalendar(
    path=Path("roll_calendar.yaml"),
    covers=Covers(date(2025, 1, 2), date(2026, 3, 31)),
    periods={
        "ES": (ContractPeriod("ES", date(2026, 2, 2), date(2026, 3, 13), "ESH6", "CME:ESH6"),)
    },
)
PULL_DATE: Final = date(2026, 3, 9)  # a Monday: every session below has ended
T0: Final = ny_instant(PULL_DATE, time(8, 0))
RANGE_MAX_AGE_DAYS: Final = 365  # Req 3.3-3.4, written here rather than imported

THU, FRI = date(2026, 3, 5), date(2026, 3, 6)  # /v1/historical/range
OLD_MON, OLD_TUE = date(2025, 2, 3), date(2025, 2, 4)  # /v1/historical
EDGE_FRI, EDGE_MON = date(2025, 3, 7), date(2025, 3, 10)  # 367 and 364 days old: both endpoints
RANGES: Final[tuple[tuple[date, date], ...]] = (
    (THU, FRI),
    (FRI, FRI),
    (OLD_MON, OLD_TUE),
    (OLD_TUE, OLD_TUE),
    (EDGE_FRI, EDGE_MON),
)
SYMBOL_POOL: Final[tuple[str, ...]] = ("SPX", "SPY", "QQQ")
LISTED: Final[Mapping[str, date]] = {s: date(2023, 3, 28) for s in SYMBOL_POOL}
METRIC_ORDERS: Final[tuple[tuple[Metric, ...], ...]] = (("gamma",), ("vanna",), ("gamma", "vanna"))

type FaultKind = Literal["interrupt", "crash", "stop", "failed", "write_error"]
type Failure = Literal["400", "404", "503", "connect", "malformed"]
type Cell = tuple[str, Instant, str]  # (metric, request instant, symbol) without data
type Ident = tuple[str, tuple[tuple[str, str], ...]]  # one logical request: path and query

FAULT_KINDS: Final[tuple[FaultKind, ...]] = ("interrupt", "crash", "stop", "failed", "write_error")
FAILURES: Final[tuple[Failure, ...]] = ("400", "404", "503", "connect", "malformed")


class SimulatedCrash(Exception):
    """Raised from the transport after the crash image is taken."""


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Fault:
    """One pull's fault. ``at`` is the heatmap HTTP attempt it fires on, or for
    ``write_error`` an index into the pending keys."""

    kind: FaultKind
    at: int
    stop_status: int = 401
    failure: Failure = "503"
    count: int = 1  # failed: new requests that fail


@dataclass(frozen=True, slots=True)
class Case:
    start: date
    end: date
    symbols: tuple[str, ...]
    metrics: tuple[Metric, ...]
    sample_minutes: int
    no_data: frozenset[Cell]
    faults: tuple[Fault, ...]

    def spec(self) -> PullSpec:
        return PullSpec(
            start=self.start,
            end=self.end,
            symbols=self.symbols,
            metrics=self.metrics,
            sample_interval_minutes=self.sample_minutes,
        )

    def options(self) -> PullOptions:
        return PullOptions(spec=self.spec(), instruments=("ES",))

    def windows(self) -> list[tuple[date, Instant, Instant, tuple[Instant, ...]]]:
        """Each Cache_Window as (session, start, end, request instants)."""
        out: list[tuple[date, Instant, Instant, tuple[Instant, ...]]] = []
        step = self.sample_minutes * NS_PER_MINUTE
        for d in CALENDAR.sessions(self.start, self.end):
            pull_start, pull_end = CALENDAR.pull_window(d)
            recent = (PULL_DATE - d).days < RANGE_MAX_AGE_DAYS
            for ws in cache_window_starts(pull_start, pull_end):
                we = cache_window_end(ws, pull_end)
                if recent:
                    instants: tuple[Instant, ...] = (ws,)
                else:  # the sample grid from the Pull_Window start, inside this window
                    instants = tuple(t for t in range(pull_start, pull_end, step) if ws <= t < we)
                out.append((d, ws, we, instants))
        return out

    def requestable(self) -> frozenset[CacheWindowKey]:
        """Keys whose window holds at least one request instant.

        A 7-minute grid puts no sample in the 5-minute 10:00 window, so that
        window gets no Replay_Request (Req 3.4) and stays absent.
        """
        return frozenset(
            CacheWindowKey(symbol, metric, VIEW_ID, d, ws)
            for d, ws, _, instants in self.windows()
            if instants
            for metric in self.metrics
            for symbol in self.symbols
        )

    def cells(self) -> list[Cell]:
        return [
            (metric, t, symbol)
            for _, _, _, instants in self.windows()
            for t in instants
            for metric in self.metrics
            for symbol in self.symbols
        ]

    def oracle(self) -> dict[CacheWindowKey, tuple[Instant, ...]]:
        """Every key the pull covers, with the ``asOf`` of each Snapshot it must hold."""
        out: dict[CacheWindowKey, tuple[Instant, ...]] = {}
        for d, ws, _, instants in self.windows():
            recent = (PULL_DATE - d).days < RANGE_MAX_AGE_DAYS
            for metric in self.metrics:
                for symbol in self.symbols:
                    key = CacheWindowKey(symbol, metric, VIEW_ID, d, ws)
                    if recent:
                        has = (metric, ws, symbol) not in self.no_data
                        out[key] = (ws, ws + S) if has else ()
                    else:
                        out[key] = tuple(
                            t - S for t in instants if (metric, t, symbol) not in self.no_data
                        )
        return out


def _call_keys(path: str, params: Mapping[str, str]) -> tuple[CacheWindowKey, ...]:
    """The Cache_Windows one heatmap request fills, read from the wire."""
    at = parse_rfc3339(params["from"] if path == ep.HISTORICAL_RANGE.path else params["at"])
    d = ny_datetime(at).date()
    pull_start, _ = CALENDAR.pull_window(d)
    ws = pull_start + (at - pull_start) // CACHE_WINDOW_NS * CACHE_WINDOW_NS
    metric = cast(Metric, params["metric"])
    return tuple(CacheWindowKey(s, metric, VIEW_ID, d, ws) for s in params["symbols"].split(","))


# ---------------------------------------------------------------- the fake Skylit


def _meta(metric: str) -> dict[str, Any]:
    return {"metric": metric, "resolution": "1s", "mode": "historical", "cached": False}


def _board(symbol: str, as_of_ns: Instant) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "asOf": format_rfc3339(as_of_ns),
        "spot": 101.5,
        "previousClose": 100.0,
        "expirations": ["2026-03-20"],
        "strikes": [
            {"strike": 100.0, "value": 1.5, "nodeType": "king"},
            {"strike": 105.0, "value": -2.25, "nodeType": "normal"},
        ],
    }


def _frames(symbol: str, lo: Instant, hi: Instant) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "axes": [{"id": 0, "strikes": [100.0, 105.0], "expirations": ["2026-03-20"]}],
        "frames": [
            {
                "asOf": format_rfc3339(t),
                "axis": 0,
                "spot": 101.5,
                "previousClose": 100.0,
                "values": [1.5, -2.25],
            }
            for t in (lo, lo + S, hi)
        ],
    }


def _failure(failure: Failure, request: httpx.Request, metric: str) -> httpx.Response:
    match failure:
        case "400":
            return httpx.Response(400, json={"error": {"code": "bad_request", "message": "x"}})
        case "404":
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})
        case "503":
            return httpx.Response(503)
        case "connect":
            raise httpx.ConnectError("fake connect error", request=request)
        case "malformed":
            return httpx.Response(200, json={"data": {"unexpected": 1}, "meta": _meta(metric)})


@dataclass
class FakeSkylit:
    """Heatmap replays from the request, with one pull's fault applied."""

    no_data: frozenset[Cell]
    fault: Fault | None = None
    root: Path = Path()
    crash_to: Path = Path()
    attempts: int = 0
    crashed: bool = False
    sent: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    seen: set[Ident] = field(default_factory=set)
    failing: set[Ident] = field(default_factory=set)
    unanswered: set[CacheWindowKey] = field(default_factory=set)

    def begin(self, fault: Fault | None, root: Path, crash_to: Path) -> None:
        self.fault, self.root, self.crash_to = fault, root, crash_to
        self.attempts, self.crashed = 0, False
        self.sent, self.seen, self.failing, self.unanswered = [], set(), set(), set()

    def requested(self) -> set[CacheWindowKey]:
        return {k for path, params in self.sent for k in _call_keys(path, params)}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.crashed:
            raise SimulatedCrash
        params = dict(request.url.params)
        path = request.url.path
        ident: Ident = (path, tuple(sorted(params.items())))
        n, self.attempts = self.attempts, self.attempts + 1
        self.sent.append((path, params))
        keys = _call_keys(path, params)
        fault = self.fault
        if fault is not None and n == fault.at and fault.kind in ("interrupt", "crash", "stop"):
            self.unanswered.update(keys)
            if fault.kind == "interrupt":
                raise asyncio.CancelledError  # what Ctrl-C does to the pull task
            if fault.kind == "stop":
                return httpx.Response(fault.stop_status)
            # A crash: what is on disk now is all the next process sees.
            if self.root.exists():
                shutil.copytree(self.root, self.crash_to)
            else:
                self.crash_to.mkdir(parents=True)
            self.crashed = True
            raise SimulatedCrash
        if (
            fault is not None
            and fault.kind == "failed"
            and n >= fault.at
            and ident not in self.seen
            and len(self.failing) < fault.count
        ):
            self.failing.add(ident)
        self.seen.add(ident)
        if fault is not None and ident in self.failing:
            self.unanswered.update(keys)
            return _failure(fault.failure, request, params["metric"])
        return self._answer(path, params)

    def _answer(self, path: str, params: Mapping[str, str]) -> httpx.Response:
        metric = params["metric"]
        if path == ep.HISTORICAL_RANGE.path:
            lo, hi = parse_rfc3339(params["from"]), parse_rfc3339(params["to"])
            instant = lo
        else:
            instant = parse_rfc3339(params["at"])
        symbols = [
            s for s in params["symbols"].split(",") if (metric, instant, s) not in self.no_data
        ]
        if not symbols:
            body = {"error": {"code": "no_data", "message": "no snapshot"}}
            return httpx.Response(404, json=body)
        if path == ep.HISTORICAL_RANGE.path:
            data: dict[str, Any] = {
                "from": params["from"],
                "to": params["to"],
                "symbols": [_frames(s, lo, hi) for s in symbols],
            }
        else:
            data = {"symbols": [_board(s, instant - S) for s in symbols]}
        return httpx.Response(200, json={"data": data, "meta": _meta(metric)})


@dataclass
class FakeAtlas:
    """Atlas ``/v1/history``: one bar per minute of ``[from, to)``."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        t = list(range(int(params["from"]), int(params["to"]), 60))
        p = [6000.0 + (s // 60 % 97) * 0.25 for s in t]
        body = {"s": "ok", "t": t, "o": p, "h": p, "l": p, "c": p, "v": [1] * len(t)}
        return httpx.Response(200, json=body)


def _route(router: respx.MockRouter, endpoint: Endpoint) -> respx.Route:
    return router.route(method="GET", scheme="https", host=endpoint.host.value, path=endpoint.path)


def _mock_endpoints(router: respx.MockRouter, skylit: FakeSkylit) -> None:
    account = {"data": {"limits": {"requestsPerMinute": 600, "historicalInFlight": 2}}}
    _route(router, ep.ACCOUNT).mock(return_value=httpx.Response(200, json=account))
    listed = [
        {
            "symbol": s,
            "isIndex": s == "SPX",
            "metrics": ["gamma", "vanna"],
            "history": {"from": "2023-03-28", "to": "2026-03-06"},
        }
        for s in SYMBOL_POOL
    ]
    _route(router, ep.SYMBOLS).mock(
        return_value=httpx.Response(200, json={"data": {"symbols": listed}})
    )
    _route(router, ep.HISTORICAL_RANGE).mock(side_effect=skylit)
    _route(router, ep.HISTORICAL).mock(side_effect=skylit)
    _route(router, ep.ATLAS_CONFIG).mock(
        return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}})
    )
    _route(router, ep.ATLAS_HISTORY).mock(side_effect=FakeAtlas())


# ---------------------------------------------------------------- one pull


@dataclass(frozen=True, slots=True)
class PullResult:
    root: Path  # the cache root the next pull runs on
    finished: bool  # run_pull returned normally
    requested: frozenset[CacheWindowKey]
    unanswered: frozenset[CacheWindowKey]


def _key_order(key: CacheWindowKey) -> tuple[date, Instant, str, str]:
    return (key.session, key.start_ns, key.symbol, key.metric)


async def _pull(
    case: Case,
    skylit: FakeSkylit,
    fault: Fault | None,
    *,
    root: Path,
    tmp: Path,
    index: int,
    pending: frozenset[CacheWindowKey],
    final: frozenset[CacheWindowKey],
) -> PullResult:
    crash_to = tmp / f"cache-{index + 1}"
    skylit.begin(fault, root, crash_to)
    clock = FakeClock(T0 + index * NS_PER_HOUR)
    blocked: Path | None = None
    writer = LogWriter(Redactor([KEY]), stdout=io.StringIO(), stderr=io.StringIO())
    with DataCache(root, calendar=CALENDAR) as cache:
        # Property 8, second half: the next pull plans exactly the incomplete and absent keys.
        plan = plan_pull(
            case.spec(), listed=LISTED, started_ns=clock.now(), calendar=CALENDAR, cache=cache
        )
        assert set(plan.fetch_keys()) == pending, f"pull {index} plan: {case}"
        assert set(plan.served_keys()) == final, f"pull {index} served: {case}"
        writable = sorted(pending & case.requestable(), key=_key_order)
        if fault is not None and fault.kind == "write_error" and writable:
            target = writable[fault.at % len(writable)]
            blocked = cache.window_path(target)
            blocked.mkdir(parents=True)
        with FetchLog(writer, tmp / f"fetch-{index}.jsonl") as log:
            async with httpx.AsyncClient() as http:
                client = SkylitClient(
                    EnvView({"SKYLIT_API_KEY": KEY}, {}),
                    ClientConfig(),
                    log,
                    clock,
                    random.Random(index),
                    http=http,
                )
                ctx = PullContext(
                    client=client,
                    cache=cache,
                    calendar=CALENDAR,
                    roll=ROLL,
                    writer=writer,
                    out_dir=tmp / f"out-{index}",
                    pull_id=f"pull-{index}",
                    started_ns=clock.now(),
                    interactive=False,
                    clock=clock.now,
                )
                finished = False
                try:
                    await clock.run(run_pull(case.options(), ctx))
                    finished = True
                except asyncio.CancelledError, Exception:
                    pass  # interrupt, crash, stop or write error: the state is what counts
    if blocked is not None:
        blocked.rmdir()
    return PullResult(
        root=crash_to if skylit.crashed else root,
        finished=finished and not skylit.crashed,
        requested=frozenset(skylit.requested()),
        unanswered=frozenset(skylit.unanswered),
    )


def _check_state(
    root: Path,
    oracle: Mapping[CacheWindowKey, tuple[Instant, ...]],
    result: PullResult | None,
    where: str,
) -> dict[CacheWindowKey, WindowStatus]:
    """Each key's status after a pull, checked against the oracle (fresh process)."""
    statuses: dict[CacheWindowKey, WindowStatus] = {}
    with DataCache(root, calendar=CALENDAR) as cache:
        for key, as_of in oracle.items():
            status = cache.status(key)
            statuses[key] = status
            label = f"{key.describe()} is {status}: {where}"
            if status in FINAL_STATUSES:
                assert status == ("complete" if as_of else "no_data"), label
                stored = tuple(s.as_of_ns for s in cache.read_window(key))
                assert stored == as_of, f"{label}; stored {stored}, want {as_of}"
                assert result is None or key not in result.unanswered, label
            else:
                assert status in ("incomplete", "absent"), label
                sent = result is not None and key in result.requested
                assert not sent or status == "incomplete", f"{label}; it was requested"
    return statuses


# ---------------------------------------------------------------- strategies


_faults = st.builds(
    Fault,
    kind=st.sampled_from(FAULT_KINDS),
    at=st.integers(0, 6) | st.integers(0, 40),
    stop_status=st.sampled_from((401, 402, 403)),
    failure=st.sampled_from(FAILURES),
    count=st.integers(1, 3),
)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    start, end = draw(st.sampled_from(RANGES))
    symbols = tuple(
        draw(st.lists(st.sampled_from(SYMBOL_POOL), min_size=1, max_size=3, unique=True))
    )
    shell = Case(
        start,
        end,
        symbols,
        draw(st.sampled_from(METRIC_ORDERS)),
        draw(st.sampled_from((5, 7, 15))),
        frozenset(),
        (),
    )
    cells = shell.cells()
    no_data = draw(st.just(frozenset[Cell]()) | st.frozensets(st.sampled_from(cells)))
    faults = tuple(draw(st.lists(_faults, min_size=1, max_size=3)))
    return Case(
        shell.start,
        shell.end,
        shell.symbols,
        shell.metrics,
        shell.sample_minutes,
        no_data,
        faults,
    )


def _example(start: date, end: date, *faults: Fault, sample: int = 15) -> Case:
    return Case(start, end, ("SPX", "SPY"), ("gamma", "vanna"), sample, frozenset(), faults)


# ---------------------------------------------------------------- property


async def _scenario(case: Case, tmp: Path, skylit: FakeSkylit) -> None:
    oracle = case.oracle()
    requestable = case.requestable()
    root = tmp / "cache-0"
    statuses = _check_state(root, oracle, None, f"before any pull: {case}")
    for index, fault in enumerate((*case.faults, None)):
        where = f"pull {index} with {fault}: {case}"
        pending = frozenset(k for k, s in statuses.items() if s not in FINAL_STATUSES)
        final = frozenset(oracle) - pending
        result = await _pull(
            case, skylit, fault, root=root, tmp=tmp, index=index, pending=pending, final=final
        )
        # The pull asks only for incomplete and absent keys, and for all of them
        # when it runs to the end.
        stray = sorted(result.requested - pending, key=_key_order)
        assert not stray, f"requested windows that were final: {stray[:3]}: {where}"
        if result.finished:
            missing = sorted((pending & requestable) - result.requested, key=_key_order)
            assert not missing, f"never requested: {missing[:3]}: {where}"
        root = result.root
        statuses = _check_state(root, oracle, result, where)
        if fault is not None:  # --hypothesis-show-statistics: how often each fault bites
            ended = "ran to the end" if result.finished else "stopped"
            some = "some incomplete" if "incomplete" in statuses.values() else "none incomplete"
            event(f"{fault.kind}: {ended}, {some}")
        else:
            assert result.finished, f"the clean pull did not end normally: {where}"
            left = sorted(
                (k for k in requestable if statuses[k] not in FINAL_STATUSES), key=_key_order
            )
            assert not left, f"left after the clean pull: {left[:3]}: {where}"


# Feature: skylit-futures-strategy-engine, Property 8: Interrupted windows stay incomplete
@given(case=cases())
@example(case=_example(THU, FRI, Fault("crash", 0)))  # crash at the first Replay_Request
@example(case=_example(THU, FRI, Fault("interrupt", 3), Fault("write_error", 2)))
@example(  # both endpoints; a failed range request, then a stop on the historical session
    case=_example(EDGE_FRI, EDGE_MON, Fault("failed", 0, count=2), Fault("stop", 1), sample=5)
)
def test_interrupted_windows_stay_incomplete_and_the_next_pull_requests_them(
    case: Case,
) -> None:
    skylit = FakeSkylit(case.no_data)
    with (
        tempfile.TemporaryDirectory(prefix="fse-p08-") as tmp,
        respx.mock(assert_all_mocked=True, assert_all_called=False) as router,
    ):
        _mock_endpoints(router, skylit)
        asyncio.run(_scenario(case, Path(tmp), skylit))
