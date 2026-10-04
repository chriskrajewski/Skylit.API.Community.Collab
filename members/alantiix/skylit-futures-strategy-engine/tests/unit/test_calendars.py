"""The shipped calendar files load and cover the stated range (task 1.6).

Error cases (missing file, parse error, invalid entries) are task 1.8.

**Validates: Requirements 4.5, 4.14, 4.15, 4.16**
"""

from __future__ import annotations

import io
from collections import Counter
from datetime import date, time
from pathlib import Path

import pytest

from fse.calendars import (
    ECONOMIC_EVENTS_FILE,
    EXCHANGE_CALENDAR_FILE,
    EXIT_INVALID_INPUT,
    ROLL_CALENDAR_FILE,
    Calendars,
    ContractPeriod,
    enforce_calendars,
    load_calendars,
    project_calendar_dir,
)
from fse.engine.types import EconomicEvent
from fse.timekit import ny_datetime, ny_instant

PROJECT_DIR = Path(__file__).resolve().parents[2]
CALENDAR_DIR = project_calendar_dir(PROJECT_DIR)
FIRST = date(2023, 3, 28)
LAST = date(2026, 12, 31)
INSTRUMENTS = ("ES", "NQ", "MES", "MNQ")


@pytest.fixture(scope="module")
def calendars() -> Calendars:
    return load_calendars(CALENDAR_DIR, FIRST, LAST)


def _contract(calendars: Calendars, instrument: str, session: date) -> ContractPeriod:
    period = calendars.roll.contract_for(instrument, session)
    assert period is not None, (instrument, session)
    return period


def test_shipped_files_cover_the_stated_range(calendars: Calendars) -> None:
    for cal in (calendars.exchange, calendars.events, calendars.roll):
        assert cal.covers.first == FIRST
        assert cal.covers.last >= LAST
    assert calendars.run_sessions[0] == FIRST
    assert calendars.run_sessions[-1] == LAST


@pytest.mark.parametrize("name", [EXCHANGE_CALENDAR_FILE, ECONOMIC_EVENTS_FILE, ROLL_CALENDAR_FILE])
def test_shipped_files_start_by_asking_the_operator_to_verify(name: str) -> None:
    first_line = (CALENDAR_DIR / name).read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("# OPERATOR:")
    assert "VERIFY" in first_line


def test_exchange_calendar_holidays_and_early_closes(calendars: Calendars) -> None:
    sessions = calendars.exchange.sessions
    assert not sessions.is_session(date(2025, 12, 25))  # Christmas
    assert not sessions.is_session(date(2025, 1, 9))  # National Day of Mourning
    assert not sessions.is_session(date(2026, 7, 3))  # Independence Day observed
    assert sessions.is_session(date(2026, 7, 2))
    assert not sessions.is_early_close(date(2026, 7, 2))
    friday = date(2025, 11, 28)
    assert sessions.is_early_close(friday)
    assert sessions.rth_close(friday) == ny_instant(friday, time(13, 15))


def test_economic_events_list_cpi_nfp_and_fomc(calendars: Calendars) -> None:
    events = calendars.events.events
    assert list(events) == sorted(events, key=lambda e: e.release_ns)
    counts = Counter((e.event_type, e.release_date.year) for e in events)
    for year in (2024, 2025, 2026):
        assert counts["FOMC", year] == 8
    for kind in ("CPI", "NFP"):
        assert counts[kind, 2024] == 12
        assert counts[kind, 2026] == 12
    expected_time = {"CPI": "08:30", "NFP": "08:30", "FOMC": "14:00"}
    for event in events:
        assert ny_datetime(event.release_ns).strftime("%H:%M") == expected_time[event.event_type]
        assert ny_datetime(event.release_ns).date() == event.release_date


def test_roll_calendar_assigns_a_contract_to_every_session(calendars: Calendars) -> None:
    assert set(INSTRUMENTS) <= set(calendars.roll.instruments)
    for instrument in INSTRUMENTS:
        for session in calendars.run_sessions:
            assert _contract(calendars, instrument, session).contract.startswith(instrument)
    assert _contract(calendars, "ES", date(2026, 6, 12)).contract == "ESM6"
    assert _contract(calendars, "ES", date(2026, 6, 15)).contract == "ESU6"
    assert _contract(calendars, "MNQ", LAST).contract == "MNQH7"


def test_run_past_the_covered_range_exits_with_status_2() -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as excinfo:
        enforce_calendars(CALENDAR_DIR, date(2026, 12, 28), date(2027, 1, 8), stream=out)
    assert excinfo.value.code == EXIT_INVALID_INPUT
    message = out.getvalue()
    assert message.startswith("error: ")
    assert EXCHANGE_CALENDAR_FILE in message
    assert "2027-01-01" in message


def test_economic_event_requires_a_type() -> None:
    with pytest.raises(ValueError, match="event_type"):
        EconomicEvent(" ", date(2026, 1, 9), ny_instant(date(2026, 1, 9), time(8, 30)))
