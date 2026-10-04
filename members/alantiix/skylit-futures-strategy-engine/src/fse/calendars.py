"""Calendar files: exchange sessions, economic events and futures rolls.

Design "Calendar files", Req 4.5 and 4.14-4.16. Three YAML files live in the
Project's ``calendars/`` folder:

- ``exchange_calendar.yaml``: exchange holidays (weekdays with no RTH session)
  and early closes with their America/New_York close time (Req 4.15).
- ``economic_events.yaml``: scheduled CPI, NFP, FOMC and Operator-added
  releases, each with a type, a date and a New York release time (Req 4.14).
- ``roll_calendar.yaml``: per futures instrument, session ranges that each
  name the one contract whose bars serve those sessions, with an optional
  Atlas symbol and ProjectX contract id (Req 4.5).

Each file states ``covers: {first: YYYY-MM-DD, last: YYYY-MM-DD}``.
:func:`load_calendars` parses and validates all three files and checks that
they cover every session of the run. Runners call :func:`enforce_calendars`
before the first Decision_Time: it prints one ``error:`` line per failing file
and exits with status 2 (Req 4.16, design "Exit codes"). Each message names the
file and either its first invalid entry (lowest line number) or the first
session of the run it does not cover.

Parsing is strict. Every scalar is read as text (PyYAML ``BaseLoader``), so
``2024-07-04`` and ``14:00`` are never turned into dates or base-60 integers
behind the parser's back. Dates must be ``YYYY-MM-DD`` and times ``HH:MM``
(24-hour). Unknown keys, repeated keys and YAML aliases are rejected, so a
typo cannot silently drop or duplicate an entry.

Nothing here writes a file or reads the clock.
"""

from __future__ import annotations

import re
import sys
from bisect import bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final, TextIO

import yaml

from fse.engine.types import EconomicEvent
from fse.timekit import RTH_CLOSE, RTH_OPEN, SessionCalendar, SessionTimes, ny_instant

__all__ = [
    "CALENDAR_DIR_NAME",
    "ECONOMIC_EVENTS_FILE",
    "EXCHANGE_CALENDAR_FILE",
    "EXIT_INVALID_INPUT",
    "ROLL_CALENDAR_FILE",
    "CalendarError",
    "CalendarProblem",
    "Calendars",
    "ContractPeriod",
    "Covers",
    "EconomicCalendar",
    "ExchangeCalendar",
    "RollCalendar",
    "enforce_calendars",
    "load_calendars",
    "load_economic_events",
    "load_exchange_calendar",
    "load_roll_calendar",
    "project_calendar_dir",
]

# Design "Exit codes": 2 = invalid input, which includes a calendar file.
EXIT_INVALID_INPUT: Final = 2

CALENDAR_DIR_NAME: Final = "calendars"
EXCHANGE_CALENDAR_FILE: Final = "exchange_calendar.yaml"
ECONOMIC_EVENTS_FILE: Final = "economic_events.yaml"
ROLL_CALENDAR_FILE: Final = "roll_calendar.yaml"

_ONE_DAY: Final = timedelta(days=1)
_MAX_DEPTH: Final = 8  # the deepest valid file nests 4 levels
_SHOWN_CHARS: Final = 40

_DATE_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_TIME_RE: Final = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2})", re.ASCII)
_EVENT_TYPE_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,31}", re.ASCII)
_INSTRUMENT_RE: Final = re.compile(r"[A-Z][A-Z0-9]{0,15}", re.ASCII)
_IDENTIFIER_RE: Final = re.compile(r"[\x21-\x7e]{1,64}", re.ASCII)  # printable, no spaces
_MONTH_CODES: Final = "FGHJKMNQUVXZ"
# Plain (unquoted) YAML scalars that mean "no value" for an optional field.
_NULL_WORDS: Final = frozenset({"", "~", "null", "Null", "NULL"})


def project_calendar_dir(project_dir: Path) -> Path:
    """``<project_dir>/calendars``, where the three calendar files live."""
    return project_dir / CALENDAR_DIR_NAME


# ---------------------------------------------------------------- errors


@dataclass(frozen=True, slots=True)
class CalendarProblem:
    """The first problem found in one calendar file."""

    path: Path
    line: int | None  # 1-based; None for file-level problems
    entry: str | None  # e.g. "holidays entry 3 (date: 2024-02-30)"
    reason: str

    def __str__(self) -> str:
        where = str(self.path) if self.line is None else f"{self.path}, line {self.line}"
        if self.entry is None:
            return f"{where}: {self.reason}"
        return f"{where}: {self.entry}: {self.reason}"


class CalendarError(Exception):
    """One or more calendar files are missing, invalid or do not cover the run."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, problems: Sequence[CalendarProblem]) -> None:
        self.problems: tuple[CalendarProblem, ...] = tuple(problems)
        super().__init__("\n".join(str(p) for p in self.problems))


class _Invalid(Exception):
    """Internal: one problem, before the file path is attached."""

    def __init__(self, line: int | None, reason: str, entry: str | None = None) -> None:
        super().__init__(reason)
        self.line = line
        self.reason = reason
        self.entry = entry

    def for_entry(self, entry: str) -> _Invalid:
        return _Invalid(self.line, self.reason, entry)

    def problem(self, path: Path) -> CalendarProblem:
        return CalendarProblem(path, self.line, self.entry, self.reason)


def _first(problems: Sequence[_Invalid]) -> _Invalid | None:
    """The problem that comes first in the file (file-level problems first)."""
    if not problems:
        return None
    return min(problems, key=lambda p: -1 if p.line is None else p.line)


# ---------------------------------------------------------------- data


@dataclass(frozen=True, slots=True)
class Covers:
    """The first and last dates a calendar file covers, both inclusive."""

    first: date
    last: date

    def contains(self, d: date) -> bool:
        return self.first <= d <= self.last

    def __str__(self) -> str:
        return f"{self.first} to {self.last}"


@dataclass(frozen=True, slots=True)
class ExchangeCalendar:
    """``exchange_calendar.yaml``: holidays, early closes and the session rules."""

    path: Path
    covers: Covers
    holidays: Mapping[date, str]  # date -> name ("" when none is given)
    early_closes: Mapping[date, time]  # date -> naive New York close time
    sessions: SessionCalendar


@dataclass(frozen=True, slots=True)
class EconomicCalendar:
    """``economic_events.yaml``: scheduled releases in release order."""

    path: Path
    covers: Covers
    events: tuple[EconomicEvent, ...]  # sorted by (release_ns, event_type)


@dataclass(frozen=True, slots=True)
class ContractPeriod:
    """One contract assigned to every session from ``first`` to ``last``."""

    instrument: str
    first: date
    last: date
    contract: str
    atlas_symbol: str | None = None
    projectx_contract_id: str | None = None


@dataclass(frozen=True, slots=True)
class RollCalendar:
    """``roll_calendar.yaml``: the contract for each instrument and session."""

    path: Path
    covers: Covers
    periods: Mapping[str, tuple[ContractPeriod, ...]]  # each sorted by first

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(self.periods)

    def contract_for(self, instrument: str, session: date) -> ContractPeriod | None:
        """The period whose range holds ``session``; ``None`` when there is none.

        ``None`` is the "no contract in the roll calendar" cause of Req 4.8.
        """
        periods = self.periods.get(instrument, ())
        i = bisect_right(periods, session, key=lambda p: p.first)
        if i == 0:
            return None
        period = periods[i - 1]
        return period if session <= period.last else None


@dataclass(frozen=True, slots=True)
class Calendars:
    """All three calendars, validated for one run."""

    exchange: ExchangeCalendar
    events: EconomicCalendar
    roll: RollCalendar
    run_sessions: tuple[date, ...]  # the exchange sessions from first to last


# ---------------------------------------------------------------- YAML tree


@dataclass(frozen=True, slots=True)
class _Scalar:
    line: int
    text: str
    plain: bool  # written without quotes


@dataclass(frozen=True, slots=True)
class _Seq:
    line: int
    items: tuple[_Node, ...]


@dataclass(frozen=True, slots=True)
class _Map:
    line: int
    items: Mapping[str, _Node]  # in file order


type _Node = _Scalar | _Seq | _Map


def _read_root(path: Path, keys: tuple[str, ...]) -> _Map:
    """Read, parse and convert ``path``; the top level must hold exactly ``keys``."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise _Invalid(None, "the file is missing") from None
    except IsADirectoryError:
        raise _Invalid(None, "is a directory, not a calendar file") from None
    except OSError as exc:
        cause = exc.strerror or type(exc).__name__
        raise _Invalid(None, f"the file cannot be read ({cause})") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _Invalid(None, f"the file is not UTF-8 text (byte {exc.start})") from None
    try:
        node = yaml.compose(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise _yaml_problem(exc) from None
    if node is None:
        raise _Invalid(None, "the file is empty")
    root = _convert(node, 0, set())
    if not isinstance(root, _Map):
        raise _Invalid(root.line, f"the file must be a mapping with the keys {', '.join(keys)}")
    _check_keys(root, keys, (), "the file")
    return root


def _yaml_problem(exc: Exception) -> _Invalid:
    mark: Any = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    problem: Any = getattr(exc, "problem", None) or getattr(exc, "context", None)
    reason = str(problem) if problem else " ".join(str(exc).split())
    if mark is None:
        return _Invalid(None, f"the file does not parse as YAML: {reason}")
    line, column = int(mark.line) + 1, int(mark.column) + 1
    return _Invalid(line, f"the file does not parse as YAML: {reason} (column {column})")


def _convert(node: Any, depth: int, seen: set[int]) -> _Node:
    """A PyYAML node graph as plain text scalars, lists and mappings."""
    line = int(node.start_mark.line) + 1
    if id(node) in seen:
        raise _Invalid(
            line,
            "the value that starts here is reused through a YAML alias (*name); "
            "write each value out in full",
        )
    seen.add(id(node))
    if depth > _MAX_DEPTH:
        raise _Invalid(line, "nests more deeply than any calendar entry needs")
    if isinstance(node, yaml.ScalarNode):
        return _Scalar(line, str(node.value), node.style is None)
    if isinstance(node, yaml.SequenceNode):
        return _Seq(line, tuple(_convert(child, depth + 1, seen) for child in node.value))
    if isinstance(node, yaml.MappingNode):
        items: dict[str, _Node] = {}
        for key_node, value_node in node.value:
            key = _convert(key_node, depth + 1, seen)
            if not isinstance(key, _Scalar):
                raise _Invalid(key.line, "a mapping key must be plain text")
            if key.text in items:
                raise _Invalid(key.line, f"repeats the key {key.text!r}")
            items[key.text] = _convert(value_node, depth + 1, seen)
        return _Map(line, MappingProxyType(items))
    raise _Invalid(line, "holds an unsupported YAML node")


# ---------------------------------------------------------------- field readers


def _check_keys(
    node: _Map, required: tuple[str, ...], optional: tuple[str, ...], what: str
) -> None:
    for key, value in node.items.items():
        if key not in required and key not in optional:
            allowed = ", ".join((*required, *optional))
            raise _Invalid(value.line, f"{what} has an unknown key {key!r} (allowed: {allowed})")
    for key in required:
        if key not in node.items:
            raise _Invalid(node.line, f"{what} is missing the key {key!r}")


def _mapping(node: _Node, what: str) -> _Map:
    if not isinstance(node, _Map):
        raise _Invalid(node.line, f"{what} must be a mapping")
    return node


def _sequence(node: _Node, what: str) -> _Seq:
    if isinstance(node, _Scalar) and node.plain and node.text == "":
        return _Seq(node.line, ())  # `holidays:` with nothing after it
    if not isinstance(node, _Seq):
        raise _Invalid(node.line, f"{what} must be a list")
    return node


def _scalar(node: _Node, what: str) -> _Scalar:
    if not isinstance(node, _Scalar):
        raise _Invalid(node.line, f"{what} must be a single value, not a list or mapping")
    return node


def _date(node: _Node, what: str) -> date:
    s = _scalar(node, what)
    if _DATE_RE.fullmatch(s.text):
        try:
            return date.fromisoformat(s.text)
        except ValueError:
            pass
    raise _Invalid(s.line, f"{what} {s.text!r} is not a valid date (YYYY-MM-DD)")


def _time(node: _Node, what: str) -> time:
    s = _scalar(node, what)
    m = _TIME_RE.fullmatch(s.text)
    if m is not None and int(m["h"]) <= 23 and int(m["m"]) <= 59:
        return time(int(m["h"]), int(m["m"]))
    raise _Invalid(s.line, f"{what} {s.text!r} is not a valid time (HH:MM, 24-hour)")


def _optional_text(node: _Node | None, what: str) -> str | None:
    """Free text; absent or a plain null word means no value."""
    if node is None:
        return None
    s = _scalar(node, what)
    if s.plain and s.text.strip() in _NULL_WORDS:
        return None
    text = s.text.strip()
    if not text:
        raise _Invalid(s.line, f"{what} must not be blank")
    return text


def _optional_identifier(node: _Node | None, what: str) -> str | None:
    text = _optional_text(node, what)
    if text is not None and not _IDENTIFIER_RE.fullmatch(text):
        raise _Invalid(
            node.line if node is not None else None,
            f"{what} {text!r} must be 1 to 64 printable ASCII characters without spaces",
        )
    return text


def _require_covered(d: date, covers: Covers, what: str, line: int) -> None:
    if not covers.contains(d):
        raise _Invalid(line, f"{what} {d} is outside covers ({covers})")


def _describe(section: str, index: int, node: _Node) -> str:
    """``section entry N (key: value, ...)`` for error messages."""
    label = f"{section} entry {index}"
    if not isinstance(node, _Map):
        return label
    shown = [
        f"{key}: {value.text[:_SHOWN_CHARS]}"
        for key, value in node.items.items()
        if isinstance(value, _Scalar)
    ]
    return f"{label} ({', '.join(shown)})" if shown else label


def _entries(root: _Map, section: str) -> Iterator[tuple[str, _Node]]:
    for i, item in enumerate(_sequence(root.items[section], section).items, start=1):
        yield _describe(section, i, item), item


def _covers(root: _Map) -> Covers:
    node = _mapping(root.items["covers"], "covers")
    _check_keys(node, ("first", "last"), (), "covers")
    first = _date(node.items["first"], "covers.first")
    last = _date(node.items["last"], "covers.last")
    if first > last:
        raise _Invalid(node.line, f"covers.first {first} is after covers.last {last}")
    return Covers(first, last)


# ---------------------------------------------------------------- exchange calendar


def load_exchange_calendar(path: Path, times: SessionTimes | None = None) -> ExchangeCalendar:
    """Parse and validate ``exchange_calendar.yaml``.

    ``times`` are the configured session times; an early close must fall
    after their Pull_Window start. Raises :class:`CalendarError` naming the
    file and its first invalid entry.
    """
    try:
        return _load_exchange(path, SessionTimes() if times is None else times)
    except _Invalid as exc:
        raise CalendarError([exc.problem(path)]) from None


def _weekday(d: date, line: int) -> None:
    if d.weekday() >= 5:
        raise _Invalid(line, f"date {d} is a {d:%A}; list the weekday the exchange observes")


def _load_exchange(path: Path, times: SessionTimes) -> ExchangeCalendar:
    root = _read_root(path, ("covers", "holidays", "early_closes"))
    covers = _covers(root)
    problems: list[_Invalid] = []

    holidays: dict[date, str] = {}
    for entry, item in _entries(root, "holidays"):
        try:
            node = _mapping(item, "a holiday")
            _check_keys(node, ("date",), ("name",), "a holiday")
            d = _date(node.items["date"], "date")
            name = _optional_text(node.items.get("name"), "name")
            _require_covered(d, covers, "date", node.line)
            _weekday(d, node.line)
            if d in holidays:
                raise _Invalid(node.line, f"date {d} is already listed as a holiday")
            holidays[d] = name or ""
        except _Invalid as exc:
            problems.append(exc.for_entry(entry))

    early_closes: dict[date, time] = {}
    for entry, item in _entries(root, "early_closes"):
        try:
            node = _mapping(item, "an early close")
            _check_keys(node, ("date", "time"), ("name",), "an early close")
            d = _date(node.items["date"], "date")
            close = _time(node.items["time"], "time")
            _optional_text(node.items.get("name"), "name")
            _require_covered(d, covers, "date", node.line)
            _weekday(d, node.line)
            if d in holidays:
                raise _Invalid(node.line, f"date {d} is also listed as a holiday")
            if d in early_closes:
                raise _Invalid(node.line, f"date {d} is already listed as an early close")
            if not RTH_OPEN < close < RTH_CLOSE:
                raise _Invalid(node.line, f"time {close:%H:%M} must be after 09:30, before 16:00")
            if not times.pull_start < close:
                raise _Invalid(
                    node.line,
                    f"time {close:%H:%M} is not after the configured Pull_Window start "
                    f"{times.pull_start:%H:%M}",
                )
            early_closes[d] = close
        except _Invalid as exc:
            problems.append(exc.for_entry(entry))

    first = _first(problems)
    if first is not None:
        raise first
    try:
        sessions = SessionCalendar(covers.first, covers.last, holidays, early_closes, times)
    except ValueError as exc:  # every rule above mirrors SessionCalendar's checks
        raise _Invalid(None, str(exc)) from None
    return ExchangeCalendar(
        path=path,
        covers=covers,
        holidays=MappingProxyType(holidays),
        early_closes=MappingProxyType(early_closes),
        sessions=sessions,
    )


# ---------------------------------------------------------------- economic events


def load_economic_events(path: Path) -> EconomicCalendar:
    """Parse and validate ``economic_events.yaml``.

    Raises :class:`CalendarError` naming the file and its first invalid entry.
    """
    try:
        return _load_events(path)
    except _Invalid as exc:
        raise CalendarError([exc.problem(path)]) from None


def _load_events(path: Path) -> EconomicCalendar:
    root = _read_root(path, ("covers", "events"))
    covers = _covers(root)
    problems: list[_Invalid] = []
    events: list[EconomicEvent] = []
    seen: set[tuple[str, date, time]] = set()
    for entry, item in _entries(root, "events"):
        try:
            node = _mapping(item, "an event")
            _check_keys(node, ("type", "date", "time"), ("note",), "an event")
            kind = _scalar(node.items["type"], "type")
            if not _EVENT_TYPE_RE.fullmatch(kind.text):
                raise _Invalid(
                    kind.line,
                    f"type {kind.text!r} must be capital letters, digits or underscores "
                    "(for example CPI, NFP, FOMC), starting with a letter",
                )
            d = _date(node.items["date"], "date")
            at = _time(node.items["time"], "time")
            note = _optional_text(node.items.get("note"), "note")
            _require_covered(d, covers, "date", node.line)
            try:
                release_ns = ny_instant(d, at)
            except ValueError:
                raise _Invalid(
                    node.line,
                    f"time {at:%H:%M} does not occur exactly once on {d} in America/New_York "
                    "(daylight-saving change)",
                ) from None
            key = (kind.text, d, at)
            if key in seen:
                raise _Invalid(node.line, f"{kind.text} at {d} {at:%H:%M} is already listed")
            seen.add(key)
            events.append(EconomicEvent(kind.text, d, release_ns, note))
        except _Invalid as exc:
            problems.append(exc.for_entry(entry))
    first = _first(problems)
    if first is not None:
        raise first
    events.sort(key=lambda e: (e.release_ns, e.event_type, e.note or ""))
    return EconomicCalendar(path=path, covers=covers, events=tuple(events))


# ---------------------------------------------------------------- roll calendar


def load_roll_calendar(path: Path) -> RollCalendar:
    """Parse and validate ``roll_calendar.yaml``.

    Ranges of one instrument must not overlap. A gap is allowed: sessions in
    it have no contract, which the Bar_Source records as a coverage cause
    (Req 4.8). Raises :class:`CalendarError` naming the file and its first
    invalid entry.
    """
    try:
        return _load_roll(path)
    except _Invalid as exc:
        raise CalendarError([exc.problem(path)]) from None


def _load_roll(path: Path) -> RollCalendar:
    root = _read_root(path, ("covers", "instruments"))
    covers = _covers(root)
    instruments = _mapping(root.items["instruments"], "instruments")
    problems: list[_Invalid] = []
    periods: dict[str, tuple[ContractPeriod, ...]] = {}
    for instrument, node in instruments.items.items():
        if not _INSTRUMENT_RE.fullmatch(instrument):
            problems.append(
                _Invalid(
                    node.line,
                    "must be capital letters and digits, starting with a letter (for example ES)",
                    f"instrument {instrument[:_SHOWN_CHARS]!r}",
                )
            )
            continue
        contract_re = re.compile(re.escape(instrument) + f"[{_MONTH_CODES}]\\d{{1,2}}", re.ASCII)
        accepted: list[tuple[str, ContractPeriod]] = []
        try:
            items = _sequence(node, "the value")
        except _Invalid as exc:
            problems.append(exc.for_entry(f"instrument {instrument}"))
            continue
        for i, item in enumerate(items.items, start=1):
            entry = _describe(instrument, i, item)
            try:
                period = _period(instrument, item, covers, contract_re)
                for other_entry, other in accepted:
                    if period.first <= other.last and other.first <= period.last:
                        raise _Invalid(
                            item.line,
                            f"sessions {period.first} to {period.last} overlap {other.contract} "
                            f"({other.first} to {other.last}, {other_entry.partition(' (')[0]})",
                        )
                accepted.append((entry, period))
            except _Invalid as exc:
                problems.append(exc.for_entry(entry))
        periods[instrument] = tuple(sorted((p for _, p in accepted), key=lambda p: p.first))
    first = _first(problems)
    if first is not None:
        raise first
    return RollCalendar(path=path, covers=covers, periods=MappingProxyType(periods))


def _period(
    instrument: str, item: _Node, covers: Covers, contract_re: re.Pattern[str]
) -> ContractPeriod:
    node = _mapping(item, "a roll entry")
    _check_keys(
        node,
        ("first", "last", "contract"),
        ("atlas_symbol", "projectx_contract_id"),
        "a roll entry",
    )
    first = _date(node.items["first"], "first")
    last = _date(node.items["last"], "last")
    contract = _scalar(node.items["contract"], "contract")
    atlas_symbol = _optional_identifier(node.items.get("atlas_symbol"), "atlas_symbol")
    projectx_id = _optional_identifier(
        node.items.get("projectx_contract_id"), "projectx_contract_id"
    )
    if first > last:
        raise _Invalid(node.line, f"first {first} is after last {last}")
    _require_covered(first, covers, "first", node.line)
    _require_covered(last, covers, "last", node.line)
    if not contract_re.fullmatch(contract.text):
        raise _Invalid(
            contract.line,
            f"contract {contract.text[:_SHOWN_CHARS]!r} must be {instrument} plus a month code "
            f"({_MONTH_CODES}) and a 1- or 2-digit year, for example {instrument}Z6",
        )
    return ContractPeriod(instrument, first, last, contract.text, atlas_symbol, projectx_id)


# ---------------------------------------------------------------- run validation


def _first_weekday_outside(covers: Covers, first: date, last: date) -> date | None:
    """The first weekday in ``[first, last]`` outside ``covers``, else ``None``.

    Outside the covered range holidays are unknown, so every weekday there
    may be a session.
    """
    d = first
    while d <= last:
        if covers.contains(d):
            if covers.last >= last:
                return None
            d = covers.last  # skip the covered stretch
        elif d.weekday() < 5:
            return d
        d += _ONE_DAY
    return None


def load_calendars(
    calendar_dir: Path, first: date, last: date, *, times: SessionTimes | None = None
) -> Calendars:
    """Load all three calendar files and check they cover the run ``[first, last]``.

    The run's sessions come from the exchange calendar. Each file that fails
    contributes one :class:`CalendarProblem` (its first invalid entry, or the
    first run session it does not cover), and all of them are raised together
    as one :class:`CalendarError`, in the order exchange, events, roll.
    Coverage of the events and roll files is checked only once the exchange
    calendar loads and covers the run, since the sessions come from it.
    """
    if first > last:
        raise ValueError(f"run first date {first} is after last date {last}")
    found: dict[str, CalendarProblem] = {}

    exchange: ExchangeCalendar | None = None
    events: EconomicCalendar | None = None
    roll: RollCalendar | None = None
    try:
        exchange = load_exchange_calendar(calendar_dir / EXCHANGE_CALENDAR_FILE, times)
    except CalendarError as exc:
        found[EXCHANGE_CALENDAR_FILE] = exc.problems[0]
    try:
        events = load_economic_events(calendar_dir / ECONOMIC_EVENTS_FILE)
    except CalendarError as exc:
        found[ECONOMIC_EVENTS_FILE] = exc.problems[0]
    try:
        roll = load_roll_calendar(calendar_dir / ROLL_CALENDAR_FILE)
    except CalendarError as exc:
        found[ROLL_CALENDAR_FILE] = exc.problems[0]

    run = f"{first} to {last}"
    sessions: tuple[date, ...] = ()
    if exchange is not None:
        gap = _first_weekday_outside(exchange.covers, first, last)
        if gap is not None:
            found[EXCHANGE_CALENDAR_FILE] = CalendarProblem(
                exchange.path,
                None,
                None,
                f"does not cover {gap}, a weekday of the run {run}; "
                f"the file covers {exchange.covers}",
            )
        else:
            # Any run day outside covers is a weekend day, never a session, so
            # list sessions over the run clipped to covers.
            lo = max(first, exchange.covers.first)
            hi = min(last, exchange.covers.last)
            sessions = exchange.sessions.sessions(lo, hi)
            for name, cal in ((ECONOMIC_EVENTS_FILE, events), (ROLL_CALENDAR_FILE, roll)):
                if cal is None:
                    continue
                missing = next((d for d in sessions if not cal.covers.contains(d)), None)
                if missing is not None:
                    found[name] = CalendarProblem(
                        cal.path,
                        None,
                        None,
                        f"does not cover session {missing} of the run {run}; "
                        f"the file covers {cal.covers}",
                    )

    if found or exchange is None or events is None or roll is None:
        order = (EXCHANGE_CALENDAR_FILE, ECONOMIC_EVENTS_FILE, ROLL_CALENDAR_FILE)
        raise CalendarError([found[name] for name in order if name in found])
    return Calendars(exchange=exchange, events=events, roll=roll, run_sessions=sessions)


def enforce_calendars(
    calendar_dir: Path,
    first: date,
    last: date,
    *,
    times: SessionTimes | None = None,
    stream: TextIO | None = None,
) -> Calendars:
    """:func:`load_calendars`, printing each problem and exiting with status 2.

    Call before the first Decision_Time (Req 4.16). Each failing file gets
    one ``error:`` line on ``stream`` (default stderr), then
    :class:`SystemExit` is raised with :data:`EXIT_INVALID_INPUT`.
    """
    try:
        return load_calendars(calendar_dir, first, last, times=times)
    except CalendarError as exc:
        out = sys.stderr if stream is None else stream
        for problem in exc.problems:
            print(f"error: {problem}", file=out)
        out.flush()
        raise SystemExit(exc.exit_code) from exc
