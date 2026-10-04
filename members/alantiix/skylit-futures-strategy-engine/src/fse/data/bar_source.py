"""Bar_Source: futures bars per session from Atlas or ProjectX (design §4, Req 4.1-4.8, OQ10).

Sources (Skylit docs; content rephrased for compliance with licensing restrictions):

- Atlas ``GET /v1/history``:
  https://www.skylit.ai/docs/api-reference/history/ohlcv-price-bars-for-a-symbol-and-resolution
  UDF bars over ``[from, to)`` in Unix seconds, oldest first. ``extended=true``
  adds the bars outside 09:30-16:00 ET. One call may span at most the bar
  tier's trading-day cap (90 for the 1-minute tier). A wider window is a
  refunded ``400`` and is never shortened, so a short ``ok`` answer means the
  data ends there. An unknown symbol is ``404 symbol_not_found``.
- Atlas ``GET /v1/search``: https://www.skylit.ai/docs/api-reference/symbols/search-symbols
  ranked matches, exact ticker first (1 credit).
- Atlas ``GET /v1/config`` (free): ``max_fetch_trading_days`` per resolution.

Rules:

- **Session range** (Req 4.1). Session ``d``'s bars open at or after 18:00 on
  the calendar day before ``d`` (Sunday 18:00 for a Monday) and close by the
  later of the Flat_Deadline and the RTH close, plus one bar: the bar that
  opens at that instant, which the Account_Simulator's Flat_Deadline exit
  fills on (Req 15.16). With the defaults that is 18:00 to 16:11.
- **One contract per session** (Req 4.5). Every bar of ``d`` comes from the
  roll calendar's contract for ``d``. A session without one gets no request
  and is reported with ``contract=None`` (cause ``no_contract``, Req 4.8).
- **Atlas symbol.** The roll entry's ``atlas_symbol``, else ``GET /v1/search``
  for the contract code, once per contract. Only a match whose ticker or
  symbol equals the contract code (an ``EXCHANGE:`` prefix is ignored) is
  used; otherwise the error code is :data:`ATLAS_SYMBOL_NOT_FOUND`.
- **Probe** (Req 4.2-4.3). Per instrument, one 1-minute ``extended=true``
  request for the first and for the last session of the pull. Served when
  both return at least one bar; otherwise every session of the pull comes
  from ProjectX. The result and the last error code go to coverage.
- **Windows** (Req 4.4). Atlas requests cover contiguous spans of at most
  ``max_days`` weekdays (``GET /v1/config``, default 90) per contract, via
  :func:`fse.data.spans.split_date_range`. A ``400`` on a multi-session span
  (Atlas counted more trading days than we did) splits it in half and
  retries, since a rejected call is free. ProjectX requests cover runs of
  consecutive sessions and are paced by :class:`ProjectXBars`. Second bars
  (``--bar-interval 5``) always come from ProjectX (OQ9).
- **Normalization** (Req 4.7). Atlas ``t`` (Unix seconds) and ProjectX ``t``
  (ISO 8601) are both taken as the bar open (OQ10); ``close = open +
  interval``. Prices are kept as received and as ticks
  (``round_half_up(price x 4)``); a price off the tick grid is listed in
  :attr:`SessionBars.off_grid` for coverage. An answer with a bar off the
  interval grid or a repeated bar is unusable. Nothing is synthesized:
  missing minutes stay missing (Req 4.8).
- **OQ10 check.** When Atlas serves an instrument and ProjectX is
  configured, the probe compares the first RTH hour of the last session from
  both sources. If their tick prices line up better one bar apart than at the
  same timestamps, :class:`BarTimestampError` stops the pull (exit 4).

A session whose request failed keeps its ``error_code`` and is never stored
as ``complete``: :func:`pull_bars` leaves it ``incomplete`` so a rerun fetches
it again.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from types import MappingProxyType
from typing import ClassVar, Final, Literal

from fse.calendars import ContractPeriod, RollCalendar
from fse.data.aux_stores import BarStore
from fse.data.coverage import CoverageCollector, OffGridPrice
from fse.data.spans import ATLAS_DEFAULT_MAX_DAYS, is_weekday, split_date_range
from fse.engine.types import Bar, BarSourceName, Ticks
from fse.projectx.bars import DUPLICATE_BAR, MISALIGNED_BAR, ProjectXBars
from fse.projectx.models import TICKS_PER_POINT, price_to_ticks
from fse.skylit.client import Failed, SkylitClient
from fse.skylit.endpoints import ATLAS_RESOLUTIONS
from fse.skylit.models import AtlasConfig, HistoryBars, HistoryNoData, SearchResults
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, SessionCalendar

__all__ = [
    "ATLAS_SYMBOL_NOT_FOUND",
    "CHECK_MINUTES",
    "CHECK_MIN_BARS",
    "EXIT_DATA_IO",
    "NO_ALTERNATE_SOURCE",
    "NO_CONTRACT",
    "NO_PROJECTX_CONTRACT_ID",
    "PROBE_INTERVAL_S",
    "AtlasAnswer",
    "AtlasBars",
    "BarSource",
    "BarTimestampError",
    "FetchedBars",
    "ProbeResult",
    "SessionBars",
    "SessionRange",
    "TimestampCheck",
    "TimestampStatus",
    "Unresolved",
    "assign_to_sessions",
    "atlas_resolution",
    "compare_bar_timestamps",
    "failure_code",
    "match_contract",
    "normalize_atlas_bars",
    "off_grid_prices",
    "pull_bars",
    "session_bar_range",
]

# Design "Exit codes": 4 = data or I/O failure.
EXIT_DATA_IO: Final = 4

PROBE_INTERVAL_S: Final = 60
"""The probe asks for 1-minute bars (Req 4.2)."""

CHECK_MINUTES: Final = 60
"""The OQ10 check compares the first RTH hour of the probe's last session."""
CHECK_MIN_BARS: Final = 10
"""Fewer common timestamps than this make the OQ10 check inconclusive."""

# Error codes for causes that are not an HTTP answer. Coverage shows them as
# the last error code of the session (Req 4.2, 4.8).
NO_CONTRACT: Final = "no_contract"
ATLAS_SYMBOL_NOT_FOUND: Final = "atlas_symbol_not_found"
NO_PROJECTX_CONTRACT_ID: Final = "no_projectx_contract_id"
NO_ALTERNATE_SOURCE: Final = "no_alternate_source"

type SessionRange = Callable[[date], tuple[Instant, Instant]]
"""``[start, end)`` of one session's bars."""

type TimestampStatus = Literal["aligned", "shifted", "inconclusive", "skipped"]

type _PriceColumn = Literal["o", "h", "l", "c"]
_PRICE_COLUMNS: Final[tuple[_PriceColumn, ...]] = ("o", "h", "l", "c")
_SHIFTS: Final = (-1, 0, 1)


class BarTimestampError(Exception):
    """Atlas and ProjectX bars disagree by a whole bar (OQ10); the pull stops."""

    exit_code: ClassVar[int] = EXIT_DATA_IO

    def __init__(self, check: TimestampCheck) -> None:
        self.check = check
        super().__init__(
            f"Atlas and ProjectX {check.instrument} {check.contract} bars on {check.session} "
            f"match best {check.offset_bars:+d} bar apart ({check.describe()}); Atlas t may "
            "not be the bar open time (OQ10). Fix the bar convention before pulling bars"
        )


# ---------------------------------------------------------------- pure helpers


def atlas_resolution(interval_s: int) -> str | None:
    """The Atlas ``resolution`` for bars of ``interval_s`` seconds, or ``None`` (OQ9)."""
    if isinstance(interval_s, bool) or interval_s <= 0 or interval_s % 60:
        return None
    resolution = str(interval_s // 60)
    return resolution if resolution in ATLAS_RESOLUTIONS else None


def session_bar_range(
    calendar: SessionCalendar, session: date, interval_s: int
) -> tuple[Instant, Instant]:
    """``[start, end)`` of session ``session``'s bars (Req 4.1, 15.16).

    ``start`` is 18:00 on the calendar day before; ``end`` is one bar after
    the later of the Flat_Deadline and the RTH close.
    """
    if isinstance(interval_s, bool) or interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number of seconds, got {interval_s!r}")
    last = max(calendar.flat_deadline(session), calendar.rth_close(session))
    return calendar.trading_day_start(session), last + interval_s * NS_PER_SECOND


def failure_code(failed: Failed) -> str:
    """The server's error code, else a short cause (``HTTP 503``, ``ReadTimeout``)."""
    return failed.error_code or failed.cause


def match_contract(results: SearchResults, contract: str) -> str | None:
    """The Atlas ``symbol`` of the match whose ticker or symbol is ``contract``, else ``None``."""
    want = contract.strip().upper()
    for result in results.results:
        names = (result.ticker, result.symbol, result.symbol.rpartition(":")[2])
        if any(name.strip().upper() == want for name in names):
            return result.symbol
    return None


@dataclass(frozen=True, slots=True)
class Unresolved:
    """A value that could not be obtained; ``error_code`` says why."""

    error_code: str


def normalize_atlas_bars(
    history: HistoryBars,
    *,
    instrument: str,
    contract: str,
    interval_s: int,
    start_ns: Instant,
    end_ns: Instant,
    futures: bool = True,
) -> tuple[Bar, ...] | Unresolved:
    """Atlas UDF columns as :class:`Bar` values that open and close in ``[start_ns, end_ns)``.

    ``t`` is the open (OQ10) and ``close = open + interval``. Futures bars
    get tick prices; index bars (``futures=False``) get ``None``. A bar off
    the interval grid or a repeated ``t`` makes the whole answer unusable.
    """
    interval_ns = interval_s * NS_PER_SECOND
    opens = history.open_ns()
    if len(set(opens)) != len(opens):
        return Unresolved(DUPLICATE_BAR)
    if any(t % interval_ns for t in opens):
        return Unresolved(MISALIGNED_BAR)
    bars: list[Bar] = []
    for i in sorted(range(len(opens)), key=opens.__getitem__):
        open_ns = opens[i]
        close_ns = open_ns + interval_ns
        if open_ns < start_ns or close_ns > end_ns:
            continue
        o, h, low, c = history.o[i], history.h[i], history.l[i], history.c[i]
        ticks: tuple[Ticks | None, ...] = (
            tuple(price_to_ticks(p) for p in (o, h, low, c)) if futures else (None,) * 4
        )
        bars.append(
            Bar(
                instrument=instrument,
                contract=contract,
                interval_s=interval_s,
                open_ns=open_ns,
                close_ns=close_ns,
                o=o,
                h=h,
                l=low,
                c=c,
                v=history.v[i],
                o_t=ticks[0],
                h_t=ticks[1],
                l_t=ticks[2],
                c_t=ticks[3],
                source="atlas",
            )
        )
    return tuple(bars)


def off_grid_prices(bars: Iterable[Bar]) -> tuple[OffGridPrice, ...]:
    """Every received futures price that is not a whole 0.25-point tick."""
    out: list[OffGridPrice] = []
    for bar in bars:
        if bar.o_t is None:
            continue  # an index bar has no tick grid
        prices = (bar.o, bar.h, bar.l, bar.c)
        ticks = (bar.o_t, bar.h_t, bar.l_t, bar.c_t)
        for column, price, tick in zip(_PRICE_COLUMNS, prices, ticks, strict=True):
            if price * TICKS_PER_POINT != tick:
                out.append(OffGridPrice(bar.open_ns, column, price))
    return tuple(out)


def assign_to_sessions(
    bars: Sequence[Bar], days: Iterable[date], session_range: SessionRange
) -> dict[date, tuple[Bar, ...]]:
    """The bars of each day that open and close inside its session range.

    ``bars`` must be sorted by open time; bars outside every range are dropped.
    """
    opens = [b.open_ns for b in bars]
    out: dict[date, tuple[Bar, ...]] = {}
    for d in days:
        start, end = session_range(d)
        lo, hi = bisect_left(opens, start), bisect_left(opens, end)
        out[d] = tuple(b for b in bars[lo:hi] if b.close_ns <= end)
    return out


# ---------------------------------------------------------------- OQ10 check


@dataclass(frozen=True, slots=True)
class TimestampCheck:
    """The OQ10 comparison of overlapping Atlas and ProjectX bars.

    ``matches[k]`` counts Atlas bars whose four tick prices equal the
    ProjectX bar opening ``k`` bars later. ``compared`` counts timestamps
    present in both sources.
    """

    instrument: str
    session: date
    contract: str
    status: TimestampStatus
    compared: int = 0
    matches: Mapping[int, int] = field(default_factory=lambda: MappingProxyType({}))
    detail: str | None = None

    @property
    def offset_bars(self) -> int:
        """The offset with the most matches (0 on a tie with 0)."""
        if not self.matches:
            return 0
        best = max(self.matches.values())
        return 0 if self.matches.get(0) == best else max(self.matches, key=self.matches.__getitem__)

    def describe(self) -> str:
        if self.status == "skipped":
            return f"skipped: {self.detail}"
        counts = ", ".join(f"{k:+d}: {self.matches.get(k, 0)}" for k in _SHIFTS)
        text = f"{self.status}, {self.compared} common bars, matches by offset {counts}"
        return text if self.detail is None else f"{text}; {self.detail}"


def compare_bar_timestamps(
    atlas: Sequence[Bar], projectx: Sequence[Bar], interval_s: int
) -> tuple[TimestampStatus, int, Mapping[int, int]]:
    """``(status, compared, matches)`` for two bar series of one contract.

    ``shifted`` when a one-bar offset matches more bars than offset 0 and at
    least half of the common timestamps; ``aligned`` when offset 0 matches at
    least half; else ``inconclusive`` (too few common bars, or prices that
    differ at every offset).
    """
    step = interval_s * NS_PER_SECOND
    a = {b.open_ns: (b.o_t, b.h_t, b.l_t, b.c_t) for b in atlas}
    p = {b.open_ns: (b.o_t, b.h_t, b.l_t, b.c_t) for b in projectx}
    matches = {k: sum(1 for t, v in a.items() if p.get(t + k * step) == v) for k in _SHIFTS}
    compared = len(a.keys() & p.keys())
    frozen = MappingProxyType(matches)
    if compared < CHECK_MIN_BARS:
        return "inconclusive", compared, frozen
    shifted = max(matches[-1], matches[1])
    if shifted > matches[0] and 2 * shifted >= compared:
        return "shifted", compared, frozen
    if 2 * matches[0] >= compared:
        return "aligned", compared, frozen
    return "inconclusive", compared, frozen


# ---------------------------------------------------------------- Atlas


@dataclass(frozen=True, slots=True)
class AtlasAnswer:
    """One ``/v1/history`` request: bars, or the error code and HTTP status of the failure."""

    bars: tuple[Bar, ...]
    error_code: str | None = None
    status: int | None = None


@dataclass(frozen=True, slots=True)
class FetchedBars:
    """One session's bars from one source; ``error_code`` is set when its request failed."""

    bars: tuple[Bar, ...]
    error_code: str | None = None


class AtlasBars:
    """Atlas ``/v1/history`` bars through the :class:`SkylitClient` (paced, retried, logged)."""

    __slots__ = ("_client", "_config", "_requests", "_symbols")

    def __init__(self, client: SkylitClient) -> None:
        self._client = client
        self._config: AtlasConfig | Failed | None = None
        self._symbols: dict[str, str | Unresolved] = {}
        self._requests = 0

    @property
    def requests(self) -> int:
        """``/v1/history`` requests sent so far (each costs 1 credit unless refunded)."""
        return self._requests

    @property
    def config(self) -> AtlasConfig | Failed | None:
        """The ``GET /v1/config`` answer, once :meth:`max_days` has read it."""
        return self._config

    async def max_days(self, resolution: str) -> int:
        """The trading-day cap of one request at ``resolution`` (read once; default 90)."""
        if self._config is None:
            self._config = await self._client.atlas_config()
        if isinstance(self._config, Failed):
            return ATLAS_DEFAULT_MAX_DAYS
        return self._config.max_days(resolution, ATLAS_DEFAULT_MAX_DAYS)

    async def symbol_for(self, period: ContractPeriod) -> str | Unresolved:
        """The Atlas symbol of ``period``'s contract, resolved through search at most once."""
        if period.atlas_symbol:
            return period.atlas_symbol
        known = self._symbols.get(period.contract)
        if known is not None:
            return known
        result = await self._client.atlas_search(period.contract)
        found: str | Unresolved
        if isinstance(result, Failed):
            found = Unresolved(failure_code(result))
        else:
            match = match_contract(result, period.contract)
            found = Unresolved(ATLAS_SYMBOL_NOT_FOUND) if match is None else match
        self._symbols[period.contract] = found
        return found

    async def history(
        self,
        symbol: str,
        *,
        instrument: str,
        contract: str,
        start_ns: Instant,
        end_ns: Instant,
        interval_s: int,
        extended: bool,
        futures: bool = True,
    ) -> AtlasAnswer:
        """One request for ``[start_ns, end_ns)``; both ends must be whole seconds."""
        resolution = atlas_resolution(interval_s)
        if resolution is None:
            raise ValueError(f"Atlas serves no {interval_s} s bars")
        if start_ns % NS_PER_SECOND or end_ns % NS_PER_SECOND:
            raise ValueError("start_ns and end_ns must be whole seconds")
        self._requests += 1
        result = await self._client.atlas_history(
            symbol,
            resolution=resolution,
            from_s=start_ns // NS_PER_SECOND,
            to_s=end_ns // NS_PER_SECOND,
            extended=extended,
        )
        if isinstance(result, Failed):
            return AtlasAnswer((), failure_code(result), result.status)
        if isinstance(result, HistoryNoData):
            return AtlasAnswer(())
        bars = normalize_atlas_bars(
            result,
            instrument=instrument,
            contract=contract,
            interval_s=interval_s,
            start_ns=start_ns,
            end_ns=end_ns,
            futures=futures,
        )
        if isinstance(bars, Unresolved):
            return AtlasAnswer((), bars.error_code)
        return AtlasAnswer(bars)

    async def fetch_sessions(
        self,
        symbol: str,
        days: Iterable[date],
        *,
        instrument: str,
        contract: str,
        session_range: SessionRange,
        interval_s: int,
        extended: bool,
        futures: bool = True,
    ) -> dict[date, FetchedBars]:
        """Each day's bars, in spans of at most ``max_days`` weekdays (Req 4.4)."""
        resolution = atlas_resolution(interval_s)
        if resolution is None:
            raise ValueError(f"Atlas serves no {interval_s} s bars")
        ordered = sorted(set(days))
        if not ordered:
            return {}
        cap = await self.max_days(resolution)
        out: dict[date, FetchedBars] = {}
        for span in split_date_range(ordered[0], ordered[-1], cap, counts=is_weekday):
            part = [d for d in ordered if span.contains(d)]
            if part:
                out.update(
                    await self._span(
                        symbol,
                        part,
                        instrument,
                        contract,
                        session_range,
                        interval_s,
                        extended,
                        futures,
                    )
                )
        return out

    async def _span(
        self,
        symbol: str,
        days: list[date],
        instrument: str,
        contract: str,
        session_range: SessionRange,
        interval_s: int,
        extended: bool,
        futures: bool,
    ) -> dict[date, FetchedBars]:
        answer = await self.history(
            symbol,
            instrument=instrument,
            contract=contract,
            start_ns=session_range(days[0])[0],
            end_ns=session_range(days[-1])[1],
            interval_s=interval_s,
            extended=extended,
            futures=futures,
        )
        if answer.status == 400 and len(days) > 1:
            # Atlas counted more trading days than the weekday count: halve and retry.
            mid = len(days) // 2
            args = (instrument, contract, session_range, interval_s, extended, futures)
            return {
                **await self._span(symbol, days[:mid], *args),
                **await self._span(symbol, days[mid:], *args),
            }
        if answer.error_code is not None:
            return {d: FetchedBars((), answer.error_code) for d in days}
        return {
            d: FetchedBars(bars)
            for d, bars in assign_to_sessions(answer.bars, days, session_range).items()
        }

    def __repr__(self) -> str:
        return f"AtlasBars(requests={self._requests})"


# ---------------------------------------------------------------- the Bar_Source


@dataclass(frozen=True, slots=True)
class SessionBars:
    """One session's futures bars and where they came from (Req 4.5, 4.6, 4.8).

    ``contract`` and ``source`` are ``None`` when the roll calendar assigns no
    contract. ``error_code`` is the last error code of a failed request; such
    a session must not be stored as complete.
    """

    instrument: str
    session: date
    interval_s: int
    contract: str | None
    source: BarSourceName | None
    bars: tuple[Bar, ...]
    error_code: str | None = None
    off_grid: tuple[OffGridPrice, ...] = ()

    @property
    def usable(self) -> bool:
        """True when the bars can be stored: a contract, a source and no failed request."""
        return self.contract is not None and self.source is not None and self.error_code is None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Whether Atlas serves an instrument (Req 4.2), with the OQ10 check when it ran."""

    instrument: str
    served: bool
    error_code: str | None
    bars_first: int
    bars_last: int
    timestamp_check: TimestampCheck | None = None


class BarSource:
    """Futures bars per session: Atlas when the probe says it serves them, else ProjectX."""

    __slots__ = ("_atlas", "_calendar", "_projectx", "_roll", "_served")

    def __init__(
        self,
        *,
        calendar: SessionCalendar,
        roll: RollCalendar,
        atlas: AtlasBars,
        projectx: ProjectXBars | None,
        served: Mapping[str, bool] | None = None,
    ) -> None:
        """``projectx`` is the alternate source (Req 4.3); ``None`` when not configured.

        ``served`` presets probe results; an instrument absent from it is not served.
        """
        self._calendar = calendar
        self._roll = roll
        self._atlas = atlas
        self._projectx = projectx
        self._served: dict[str, bool] = dict(served or {})

    @property
    def calendar(self) -> SessionCalendar:
        return self._calendar

    @property
    def roll(self) -> RollCalendar:
        return self._roll

    @property
    def atlas(self) -> AtlasBars:
        return self._atlas

    @property
    def served(self) -> Mapping[str, bool]:
        return MappingProxyType(self._served)

    def source_for(self, instrument: str, interval_s: int) -> BarSourceName:
        """``atlas`` when the probe found it serves ``instrument`` at this bar size."""
        if self._served.get(instrument, False) and atlas_resolution(interval_s) is not None:
            return "atlas"
        return "projectx"

    def session_range(self, interval_s: int) -> SessionRange:
        return lambda d: session_bar_range(self._calendar, d, interval_s)

    # ---------------------------------------------------------------- probe

    async def probe(
        self,
        instruments: Iterable[str],
        first: date,
        last: date,
        *,
        coverage: CoverageCollector | None = None,
        check_timestamps: bool = True,
    ) -> dict[str, ProbeResult]:
        """Probe each instrument on the first and last session (Req 4.2-4.3) and run OQ10.

        Records each result in ``coverage``. Raises :class:`BarTimestampError`
        (after recording) when the OQ10 check finds a whole-bar shift.
        """
        out: dict[str, ProbeResult] = {}
        for instrument in instruments:
            result, last_bars = await self._probe_one(instrument, first, last)
            check: TimestampCheck | None = None
            if result.served and check_timestamps:
                check = await self._timestamp_check(instrument, last, last_bars)
                result = replace(result, timestamp_check=check)
            self._served[instrument] = result.served
            out[instrument] = result
            if coverage is not None:
                coverage.record_atlas_probe(
                    instrument, served=result.served, error_code=result.error_code
                )
            if check is not None and check.status == "shifted":
                raise BarTimestampError(check)
        return out

    async def _probe_one(
        self, instrument: str, first: date, last: date
    ) -> tuple[ProbeResult, tuple[Bar, ...]]:
        counts: dict[date, int] = {}
        codes: list[str] = []
        last_bars: tuple[Bar, ...] = ()
        span = self.session_range(PROBE_INTERVAL_S)
        for d in dict.fromkeys((first, last)):
            period = self._roll.contract_for(instrument, d)
            counts[d] = 0
            if period is None:
                codes.append(NO_CONTRACT)
                continue
            symbol = await self._atlas.symbol_for(period)
            if isinstance(symbol, Unresolved):
                codes.append(symbol.error_code)
                continue
            start, end = span(d)
            answer = await self._atlas.history(
                symbol,
                instrument=instrument,
                contract=period.contract,
                start_ns=start,
                end_ns=end,
                interval_s=PROBE_INTERVAL_S,
                extended=True,
            )
            if answer.error_code is not None:
                codes.append(answer.error_code)
            counts[d] = len(answer.bars)
            if d == last:
                last_bars = answer.bars
        served = all(n > 0 for n in counts.values())
        result = ProbeResult(
            instrument,
            served,
            None if served or not codes else codes[-1],
            counts[first],
            counts[last],
        )
        return result, last_bars

    async def _timestamp_check(
        self, instrument: str, session: date, atlas_bars: Sequence[Bar]
    ) -> TimestampCheck:
        period = self._roll.contract_for(instrument, session)
        assert period is not None  # the probe served this session, so it has a contract
        contract = period.contract
        if self._projectx is None:
            return TimestampCheck(instrument, session, contract, "skipped", detail="no ProjectX")
        if period.projectx_contract_id is None:
            return TimestampCheck(
                instrument,
                session,
                contract,
                "skipped",
                detail="no projectx_contract_id in the roll calendar",
            )
        start = self._calendar.rth_open(session)
        end = min(start + CHECK_MINUTES * NS_PER_MINUTE, self._calendar.rth_close(session))
        history = await self._projectx.retrieve(
            instrument=instrument,
            contract=contract,
            contract_id=period.projectx_contract_id,
            start_ns=start,
            end_ns=end,
            interval_s=PROBE_INTERVAL_S,
        )
        if history.failures:
            cause = history.failures[-1].cause
            return TimestampCheck(
                instrument, session, contract, "skipped", detail=f"ProjectX bars failed: {cause}"
            )
        sample = [b for b in atlas_bars if start <= b.open_ns < end]
        status, compared, matches = compare_bar_timestamps(sample, history.bars, PROBE_INTERVAL_S)
        return TimestampCheck(instrument, session, contract, status, compared, matches)

    # ---------------------------------------------------------------- sessions

    async def fetch_sessions(
        self, instrument: str, sessions: Iterable[date], *, interval_s: int = 60
    ) -> tuple[SessionBars, ...]:
        """The bars of each session, oldest first, from :meth:`source_for`'s source."""
        days = sorted(set(sessions))
        source = self.source_for(instrument, interval_s)
        out: dict[date, SessionBars] = {}
        groups: list[tuple[ContractPeriod, list[date]]] = []
        for d in days:
            period = self._roll.contract_for(instrument, d)
            if period is None:
                out[d] = SessionBars(instrument, d, interval_s, None, None, ())
            elif groups and groups[-1][0] == period:
                groups[-1][1].append(d)
            else:
                groups.append((period, [d]))
        for period, group in groups:
            if source == "atlas":
                fetched = await self._from_atlas(instrument, period, group, interval_s)
            else:
                fetched = await self._from_projectx(instrument, period, group, interval_s)
            for d, got in fetched.items():
                out[d] = SessionBars(
                    instrument,
                    d,
                    interval_s,
                    period.contract,
                    source,
                    got.bars,
                    got.error_code,
                    off_grid_prices(got.bars),
                )
        return tuple(out[d] for d in days)

    async def _from_atlas(
        self, instrument: str, period: ContractPeriod, days: list[date], interval_s: int
    ) -> dict[date, FetchedBars]:
        symbol = await self._atlas.symbol_for(period)
        if isinstance(symbol, Unresolved):
            return {d: FetchedBars((), symbol.error_code) for d in days}
        return await self._atlas.fetch_sessions(
            symbol,
            days,
            instrument=instrument,
            contract=period.contract,
            session_range=self.session_range(interval_s),
            interval_s=interval_s,
            extended=True,
        )

    async def _from_projectx(
        self, instrument: str, period: ContractPeriod, days: list[date], interval_s: int
    ) -> dict[date, FetchedBars]:
        if self._projectx is None:
            return {d: FetchedBars((), NO_ALTERNATE_SOURCE) for d in days}
        contract_id = period.projectx_contract_id
        if contract_id is None:
            return {d: FetchedBars((), NO_PROJECTX_CONTRACT_ID) for d in days}
        span = self.session_range(interval_s)
        out: dict[date, FetchedBars] = {}
        for run in self._runs(days):
            history = await self._projectx.retrieve(
                instrument=instrument,
                contract=period.contract,
                contract_id=contract_id,
                start_ns=span(run[0])[0],
                end_ns=span(run[-1])[1],
                interval_s=interval_s,
            )
            per_day = assign_to_sessions(history.bars, run, span)
            for d in run:
                start, end = span(d)
                failed = [
                    f
                    for f in history.failures
                    if f.window_start_ns < end and start < f.window_end_ns
                ]
                out[d] = FetchedBars(per_day[d], failed[-1].cause if failed else None)
        return out

    def _runs(self, days: list[date]) -> list[list[date]]:
        """``days`` split into runs of consecutive exchange sessions."""
        index = {d: i for i, d in enumerate(self._calendar.sessions(days[0], days[-1]))}
        runs: list[list[date]] = []
        for d in days:
            if runs and index[d] == index[runs[-1][-1]] + 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        return runs

    def __repr__(self) -> str:
        return f"BarSource(served={self._served!r}, projectx={self._projectx is not None})"


# ---------------------------------------------------------------- storing


async def pull_bars(
    source: BarSource,
    store: BarStore,
    instrument: str,
    sessions: Iterable[date],
    *,
    interval_s: int = 60,
    coverage: CoverageCollector | None = None,
) -> tuple[SessionBars, ...]:
    """Fetch and store each session's bars with the window write protocol.

    Before the first request each session with a contract is marked
    ``incomplete``. A usable session is then written ``complete`` (or
    ``no_data`` when it has no bars); a session whose request failed stays
    ``incomplete`` so a rerun fetches it again. Each session's contract,
    source, error code and off-grid prices go to ``coverage``. Pass only the
    sessions to fetch: a stored session passed here is fetched again. A
    :class:`~fse.data.cache_io.CacheWriteError` propagates (exit 4).
    """
    days = sorted(set(sessions))
    planned = source.source_for(instrument, interval_s)
    for d in days:
        period = source.roll.contract_for(instrument, d)
        if period is not None:
            store.mark_incomplete(
                instrument, interval_s, d, contract=period.contract, source=planned
            )
    results = await source.fetch_sessions(instrument, days, interval_s=interval_s)
    for r in results:
        if r.contract is not None and r.source is not None and r.error_code is None:
            store.write_session(
                instrument, interval_s, r.session, r.bars, contract=r.contract, source=r.source
            )
        if coverage is not None:
            coverage.record_bar_fetch(
                instrument, r.session, contract=r.contract, source=r.source, error_code=r.error_code
            )
            for p in r.off_grid:
                coverage.record_off_grid_price(
                    instrument, r.session, open_ns=p.open_ns, column=p.column, price=p.price
                )
    return results
