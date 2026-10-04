"""Calendar validation errors stop the run with exit 2 (task 1.8).

Each case writes three small calendar files under ``tmp_path``, breaks one of
them and checks that the run stops before the first Decision_Time: one
``error:`` line per failing file, naming the file and its first invalid entry
(lowest line number) or the first run session it does not cover, and exit
status 2.

**Validates: Requirements 4.16**
"""

from __future__ import annotations

import io
import textwrap
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

import pytest

from fse.calendars import (
    ECONOMIC_EVENTS_FILE,
    EXCHANGE_CALENDAR_FILE,
    EXIT_INVALID_INPUT,
    ROLL_CALENDAR_FILE,
    CalendarError,
    CalendarProblem,
    enforce_calendars,
    load_calendars,
    load_economic_events,
    load_exchange_calendar,
    load_roll_calendar,
)

FIRST = date(2024, 7, 1)  # Monday
LAST = date(2024, 8, 2)  # Friday
COVERS = "2024-07-01 to 2024-08-02"
FILES = (EXCHANGE_CALENDAR_FILE, ECONOMIC_EVENTS_FILE, ROLL_CALENDAR_FILE)

EXCHANGE = """\
covers: {first: 2024-07-01, last: 2024-08-02}
holidays:
  - date: 2024-07-04
    name: Independence Day
early_closes:
  - date: 2024-07-03
    time: 13:00
    name: Independence Day eve
"""

EVENTS = """\
covers: {first: 2024-07-01, last: 2024-08-02}
events:
  - type: NFP
    date: 2024-07-05
    time: 08:30
  - type: CPI
    date: 2024-07-11
    time: 08:30
  - type: FOMC
    date: 2024-07-31
    time: 14:00
"""

ROLL = """\
covers: {first: 2024-07-01, last: 2024-08-02}
instruments:
  ES:
    - first: 2024-07-01
      last: 2024-08-02
      contract: ESU4
  NQ:
    - first: 2024-07-01
      last: 2024-08-02
      contract: NQU4
"""

VALID: Mapping[str, str] = {
    EXCHANGE_CALENDAR_FILE: EXCHANGE,
    ECONOMIC_EVENTS_FILE: EVENTS,
    ROLL_CALENDAR_FILE: ROLL,
}

LOADERS: Mapping[str, Callable[[Path], object]] = {
    EXCHANGE_CALENDAR_FILE: load_exchange_calendar,
    ECONOMIC_EVENTS_FILE: load_economic_events,
    ROLL_CALENDAR_FILE: load_roll_calendar,
}


# ---------------------------------------------------------------- helpers


@pytest.fixture
def calendar_dir(tmp_path: Path) -> Path:
    """A folder with three valid calendar files covering FIRST to LAST."""
    directory = tmp_path / "calendars"
    directory.mkdir()
    for name, text in VALID.items():
        _put(directory, name, text)
    return directory


def _put(directory: Path, name: str, text: str) -> str:
    """Write ``text``, dedented, as ``directory/name``; return what was written."""
    body = textwrap.dedent(text)
    (directory / name).write_text(body, encoding="utf-8")
    return body


def _line(text: str, needle: str) -> int:
    """The 1-based number of the one line of ``text`` that contains ``needle``."""
    hits = [n for n, line in enumerate(text.splitlines(), start=1) if needle in line]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


def _stop(directory: Path, first: date = FIRST, last: date = LAST) -> tuple[CalendarProblem, ...]:
    """The problems that stop a run, checked through both entry points.

    ``load_calendars`` raises CalendarError with exit code 2, and
    ``enforce_calendars`` prints one ``error:`` line per problem, then exits 2.
    """
    with pytest.raises(CalendarError) as caught:
        load_calendars(directory, first, last)
    assert caught.value.exit_code == EXIT_INVALID_INPUT == 2
    problems = caught.value.problems
    out = io.StringIO()
    with pytest.raises(SystemExit) as exited:
        enforce_calendars(directory, first, last, stream=out)
    assert exited.value.code == 2
    assert out.getvalue().splitlines() == [f"error: {p}" for p in problems]
    return problems


def _only_problem(
    directory: Path, name: str, first: date = FIRST, last: date = LAST
) -> CalendarProblem:
    """The run's single problem, which must name ``directory/name``."""
    problems = _stop(directory, first, last)
    path = directory / name
    assert [p.path for p in problems] == [path]
    problem = problems[0]
    message = str(problem)
    assert message.startswith(str(path))
    if problem.line is not None:
        assert message.startswith(f"{path}, line {problem.line}: ")
    if problem.entry is not None:
        assert f": {problem.entry}: " in message
    return problem


def _first_bad(directory: Path, name: str) -> CalendarProblem:
    """The run's problem for ``name``; the file's own loader reports the same one."""
    problem = _only_problem(directory, name)
    with pytest.raises(CalendarError) as caught:
        LOADERS[name](directory / name)
    assert caught.value.exit_code == 2
    assert caught.value.problems == (problem,)
    return problem


def test_valid_files_load(calendar_dir: Path) -> None:
    calendars = load_calendars(calendar_dir, FIRST, LAST)
    assert calendars.run_sessions[0] == FIRST
    assert calendars.run_sessions[-1] == LAST
    assert date(2024, 7, 4) not in calendars.run_sessions


# ---------------------------------------------------------------- missing file


@pytest.mark.parametrize("name", FILES)
def test_missing_file_is_named(calendar_dir: Path, name: str) -> None:
    (calendar_dir / name).unlink()
    problem = _first_bad(calendar_dir, name)
    assert (problem.line, problem.entry, problem.reason) == (None, None, "the file is missing")
    assert str(problem) == f"{calendar_dir / name}: the file is missing"


def test_each_failing_file_gets_its_own_error_line(calendar_dir: Path) -> None:
    (calendar_dir / EXCHANGE_CALENDAR_FILE).unlink()
    _put(calendar_dir, ECONOMIC_EVENTS_FILE, "covers: {first: 2024-07-01\nevents: []\n")
    bad_roll = ROLL.replace("ES:\n    - first: 2024-07-01", "ES:\n    - first: 2024-7-01")
    _put(calendar_dir, ROLL_CALENDAR_FILE, bad_roll)
    problems = _stop(calendar_dir)
    assert [p.path for p in problems] == [calendar_dir / name for name in FILES]
    assert problems[0].reason == "the file is missing"
    assert problems[1].reason.startswith("the file does not parse as YAML: ")
    assert problems[2].entry == "ES entry 1 (first: 2024-7-01, last: 2024-08-02, contract: ESU4)"


def test_errors_go_to_stderr_by_default(
    calendar_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (calendar_dir / ROLL_CALENDAR_FILE).unlink()
    with pytest.raises(SystemExit) as exited:
        enforce_calendars(calendar_dir, FIRST, LAST)
    assert exited.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {calendar_dir / ROLL_CALENDAR_FILE}: the file is missing\n"


# ---------------------------------------------------------------- parse errors


@pytest.mark.parametrize(
    ("name", "text", "needle", "fragment"),
    [
        pytest.param(
            EXCHANGE_CALENDAR_FILE,
            EXCHANGE.replace("name: Independence Day\n", "name: Independence: Day\n"),
            "Independence: Day",
            "mapping values are not allowed here",
            id="exchange-colon-in-value",
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE,
            EVENTS.replace("time: 14:00", "time: `14:00`"),
            "`14:00`",
            "found character '`' that cannot start any token",
            id="events-backtick",
        ),
        pytest.param(
            ROLL_CALENDAR_FILE,
            ROLL.replace("contract: NQU4", "contract: @NQU4"),
            "@NQU4",
            "found character '@' that cannot start any token",
            id="roll-reserved-indicator",
        ),
    ],
)
def test_parse_error_names_the_file_and_line(
    calendar_dir: Path, name: str, text: str, needle: str, fragment: str
) -> None:
    written = _put(calendar_dir, name, text)
    problem = _first_bad(calendar_dir, name)
    assert problem.line == _line(written, needle)
    assert problem.entry is None
    assert problem.reason.startswith("the file does not parse as YAML: ")
    assert fragment in problem.reason


@pytest.mark.parametrize(
    ("name", "text", "needle", "entry", "fragment"),
    [
        pytest.param(
            EXCHANGE_CALENDAR_FILE,
            EXCHANGE.replace("    time: 13:00\n", "    time: 13:00\n    date: 2024-07-05\n"),
            "date: 2024-07-05",
            None,
            "repeats the key 'date'",
            id="exchange-repeated-key",
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE,
            EVENTS.replace("  - type: NFP\n", "  - &nfp\n    type: NFP\n") + "  - *nfp\n",
            "&nfp",
            None,
            "reused through a YAML alias",
            id="events-alias",
        ),
        pytest.param(
            ROLL_CALENDAR_FILE,
            ROLL.replace("contract: ESU4", "contrat: ESU4"),
            "contrat: ESU4",
            "ES entry 1 (first: 2024-07-01, last: 2024-08-02, contrat: ESU4)",
            "unknown key 'contrat'",
            id="roll-unknown-key",
        ),
    ],
)
def test_strict_parse_rejection_names_the_file_and_line(
    calendar_dir: Path, name: str, text: str, needle: str, entry: str | None, fragment: str
) -> None:
    written = _put(calendar_dir, name, text)
    problem = _first_bad(calendar_dir, name)
    assert problem.line == _line(written, needle)
    assert problem.entry == entry
    assert fragment in problem.reason


# ---------------------------------------------------------------- invalid dates

# Each file holds two invalid dates; the one on the lower line is reported.
EXCHANGE_BAD_DATES = """\
covers: {first: 2024-07-01, last: 2024-08-02}
early_closes:
  - date: 2024-07-3
    time: 13:00
holidays:
  - date: 2024-02-30
"""

EVENTS_BAD_DATES = """\
covers: {first: 2024-07-01, last: 2024-08-02}
events:
  - type: NFP
    date: 2024-07-05
    time: 08:30
  - type: CPI
    date: 07/11/2024
    time: 08:30
  - type: FOMC
    date: 2024-13-31
    time: 14:00
"""

ROLL_BAD_DATES = """\
covers: {first: 2024-07-01, last: 2024-08-02}
instruments:
  NQ:
    - first: 2024-07-01
      last: 2024-08-32
      contract: NQU4
  ES:
    - first: 2024-02-30
      last: 2024-08-02
      contract: ESU4
"""


@pytest.mark.parametrize(
    ("name", "text", "bad", "entry"),
    [
        # Holidays are checked before early closes, but the early close comes
        # first in this file, so it is the one reported.
        pytest.param(
            EXCHANGE_CALENDAR_FILE,
            EXCHANGE_BAD_DATES,
            "2024-07-3",
            "early_closes entry 1 (date: 2024-07-3, time: 13:00)",
            id="exchange-early-close-above-holiday",
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE,
            EVENTS_BAD_DATES,
            "07/11/2024",
            "events entry 2 (type: CPI, date: 07/11/2024, time: 08:30)",
            id="events-second-entry",
        ),
        pytest.param(
            ROLL_CALENDAR_FILE,
            ROLL_BAD_DATES,
            "2024-08-32",
            "NQ entry 1 (first: 2024-07-01, last: 2024-08-32, contract: NQU4)",
            id="roll-first-instrument",
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE,
            EVENTS.replace("last: 2024-08-02}", "last: 2024-8-02}"),
            "2024-8-02",
            None,
            id="events-covers",
        ),
    ],
)
def test_invalid_date_names_the_first_bad_entry(
    calendar_dir: Path, name: str, text: str, bad: str, entry: str | None
) -> None:
    written = _put(calendar_dir, name, text)
    problem = _first_bad(calendar_dir, name)
    assert problem.line == _line(written, bad)
    assert problem.entry == entry
    assert f"{bad!r} is not a valid date (YYYY-MM-DD)" in problem.reason


# ---------------------------------------------------------------- invalid times

# Entry 2 holds the time under test; entry 3 is also invalid but comes later.
EXCHANGE_BAD_TIMES = """\
covers: {first: 2024-07-01, last: 2024-08-02}
holidays:
  - date: 2024-07-04
early_closes:
  - date: 2024-07-03
    time: 13:00
  - date: 2024-07-05
    time: BAD
  - date: 2024-07-08
    time: 25:00
"""

EVENTS_BAD_TIMES = """\
covers: {first: 2024-07-01, last: 2024-08-02}
events:
  - type: NFP
    date: 2024-07-05
    time: 08:30
  - type: CPI
    date: 2024-07-11
    time: BAD
  - type: FOMC
    date: 2024-07-31
    time: 24:00
"""

NOT_HH_MM = "is not a valid time (HH:MM, 24-hour)"


@pytest.mark.parametrize(
    ("name", "template", "written", "text", "fragment"),
    [
        pytest.param(
            EXCHANGE_CALENDAR_FILE, EXCHANGE_BAD_TIMES, "1:00", "1:00", NOT_HH_MM, id="early-h:mm"
        ),
        pytest.param(
            EXCHANGE_CALENDAR_FILE, EXCHANGE_BAD_TIMES, "13:60", "13:60", NOT_HH_MM, id="early-60m"
        ),
        pytest.param(
            EXCHANGE_CALENDAR_FILE,
            EXCHANGE_BAD_TIMES,
            "09:15",
            "09:15",
            "time 09:15 must be after 09:30, before 16:00",
            id="early-before-open",
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE, EVENTS_BAD_TIMES, "8:30", "8:30", NOT_HH_MM, id="event-h:mm"
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE,
            EVENTS_BAD_TIMES,
            "'08:30:00'",
            "08:30:00",
            NOT_HH_MM,
            id="event-seconds",
        ),
        pytest.param(
            ECONOMIC_EVENTS_FILE, EVENTS_BAD_TIMES, "08.30", "08.30", NOT_HH_MM, id="event-dot"
        ),
    ],
)
def test_invalid_time_names_the_first_bad_entry(
    calendar_dir: Path, name: str, template: str, written: str, text: str, fragment: str
) -> None:
    body = _put(calendar_dir, name, template.replace("BAD", written))
    problem = _first_bad(calendar_dir, name)
    # A malformed time is reported at its own line; a well-formed time that
    # breaks a range rule is reported at the line where its entry starts.
    needle = f"time: {written}" if fragment == NOT_HH_MM else "date: 2024-07-05"
    assert problem.line == _line(body, needle)
    if name == EXCHANGE_CALENDAR_FILE:
        assert problem.entry == f"early_closes entry 2 (date: 2024-07-05, time: {text})"
    else:
        assert problem.entry == f"events entry 2 (type: CPI, date: 2024-07-11, time: {text})"
    assert fragment in problem.reason
    assert text in problem.reason


# ---------------------------------------------------------------- uncovered sessions


@pytest.mark.parametrize(
    ("first", "last", "gap"),
    [
        pytest.param(date(2024, 6, 28), date(2024, 7, 5), date(2024, 6, 28), id="before"),
        # 2024-08-03 and 08-04 are a weekend, so the first gap is Monday 08-05.
        pytest.param(date(2024, 7, 29), date(2024, 8, 9), date(2024, 8, 5), id="after"),
    ],
)
def test_exchange_calendar_names_the_first_uncovered_weekday(
    calendar_dir: Path, first: date, last: date, gap: date
) -> None:
    # The events and roll files are not checked against a run the exchange
    # calendar itself does not cover, so only the exchange file is named.
    problem = _only_problem(calendar_dir, EXCHANGE_CALENDAR_FILE, first, last)
    assert (problem.line, problem.entry) == (None, None)
    assert problem.reason == (
        f"does not cover {gap}, a weekday of the run {first} to {last}; the file covers {COVERS}"
    )


EVENTS_TO_JULY_3 = """\
covers: {first: 2024-07-01, last: 2024-07-03}
events:
  - type: CPI
    date: 2024-07-02
    time: 08:30
"""

ROLL_FROM_JULY_8 = """\
covers: {first: 2024-07-08, last: 2024-08-02}
instruments:
  ES:
    - first: 2024-07-08
      last: 2024-08-02
      contract: ESU4
"""


@pytest.mark.parametrize(
    ("name", "text", "missing", "covers"),
    [
        # 2024-07-04 is a holiday, so the first uncovered session is 07-05.
        pytest.param(
            ECONOMIC_EVENTS_FILE,
            EVENTS_TO_JULY_3,
            date(2024, 7, 5),
            "2024-07-01 to 2024-07-03",
            id="events-end-early",
        ),
        pytest.param(
            ROLL_CALENDAR_FILE,
            ROLL_FROM_JULY_8,
            date(2024, 7, 1),
            "2024-07-08 to 2024-08-02",
            id="roll-starts-late",
        ),
    ],
)
def test_uncovered_session_is_named(
    calendar_dir: Path, name: str, text: str, missing: date, covers: str
) -> None:
    _put(calendar_dir, name, text)
    LOADERS[name](calendar_dir / name)  # the file itself is valid
    problem = _only_problem(calendar_dir, name)
    assert (problem.line, problem.entry) == (None, None)
    assert problem.reason == (
        f"does not cover session {missing} of the run {FIRST} to {LAST}; the file covers {covers}"
    )


def test_events_and_roll_both_uncovered_give_two_error_lines(calendar_dir: Path) -> None:
    _put(calendar_dir, ECONOMIC_EVENTS_FILE, EVENTS_TO_JULY_3)
    _put(calendar_dir, ROLL_CALENDAR_FILE, ROLL_FROM_JULY_8)
    problems = _stop(calendar_dir)
    assert [p.path for p in problems] == [
        calendar_dir / ECONOMIC_EVENTS_FILE,
        calendar_dir / ROLL_CALENDAR_FILE,
    ]
    assert "does not cover session 2024-07-05 " in problems[0].reason
    assert "does not cover session 2024-07-01 " in problems[1].reason


@pytest.mark.parametrize(
    ("first", "last", "sessions"),
    [
        # Saturday 06-29 and Sunday 06-30 lie before covers but are never sessions.
        pytest.param(
            date(2024, 6, 29),
            date(2024, 7, 5),
            (date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3), date(2024, 7, 5)),
            id="starts-on-saturday-before-covers",
        ),
        pytest.param(
            date(2024, 7, 31),
            date(2024, 8, 4),
            (date(2024, 7, 31), date(2024, 8, 1), date(2024, 8, 2)),
            id="ends-on-sunday-after-covers",
        ),
    ],
)
def test_weekend_days_outside_covers_need_no_coverage(
    calendar_dir: Path, first: date, last: date, sessions: tuple[date, ...]
) -> None:
    assert load_calendars(calendar_dir, first, last).run_sessions == sessions
