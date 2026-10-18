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
    # The roll follows the Atlas ES1 switch (2026-06-16 20:00 ET), not CME's 06-15.
    assert _contract(calendars, "ES", date(2026, 6, 15)).contract == "ESM6"
    assert _contract(calendars, "ES", date(2026, 6, 16)).contract == "ESM6"
    assert _contract(calendars, "ES", date(2026, 6, 17)).contract == "ESU6"
    assert _contract(calendars, "MNQ", LAST).contract == "MNQH7"


_ALL = ("ES", "MES", "NQ", "MNQ")
_EX = ("ES", "MES", "NQ")
_SHIPPED_MIXED: dict[date, tuple[tuple[str, ...], time]] = {
    date(2024, 3, 13): (_ALL, time(20)),
    date(2024, 9, 18): (_ALL, time(20)),
    date(2024, 12, 18): (("ES", "MES"), time(19)),
    date(2024, 12, 19): (("NQ", "MNQ"), time(19)),
    date(2025, 3, 19): (("ES", "MES"), time(20)),
    date(2025, 3, 20): (("NQ", "MNQ"), time(20)),
    date(2025, 6, 18): (_ALL, time(20)),
    date(2025, 9, 17): (("ES", "MES"), time(20)),
    date(2025, 9, 18): (("NQ", "MNQ"), time(20)),
    date(2025, 12, 17): (_EX, time(19)),
    date(2025, 12, 18): (("MNQ",), time(19)),
    date(2026, 3, 18): (_EX, time(20)),
    date(2026, 3, 19): (("MNQ",), time(20)),
    date(2026, 6, 17): (_ALL, time(20)),
    date(2026, 9, 16): (_EX, time(20)),
    date(2026, 9, 17): (("MNQ",), time(20)),
    date(2026, 12, 16): (_EX, time(19)),  # projected
    date(2026, 12, 17): (("MNQ",), time(19)),  # projected
}


def test_shipped_mixed_sessions_match_the_atlas_switches(calendars: Calendars) -> None:
    mixed = calendars.roll.mixed
    assert {d: (m.instruments, m.switch) for d, m in mixed.items()} == _SHIPPED_MIXED
    projected = {d for d, m in mixed.items() if m.note is not None}
    assert projected == {date(2026, 12, 16), date(2026, 12, 17)}
    assert calendars.roll.mixed_instruments(date(2026, 9, 17), ["MES", "MNQ"]) == ("MNQ",)


def test_each_mixed_session_starts_a_contract(calendars: Calendars) -> None:
    roll = calendars.roll
    for session, entry in roll.mixed.items():
        for instrument in entry.instruments:
            assert _contract(calendars, instrument, session).first == session
    # Conversely every Atlas-switch roll is listed, except June 2024: the
    # 2024-06-19 holiday leaves the 2024-06-20 session all new contract.
    for instrument in INSTRUMENTS:
        for period in roll.periods[instrument]:
            if period.first < date(2024, 3, 13) or period.first == date(2024, 6, 20):
                continue
            assert roll.mixed_instruments(period.first, [instrument]) == (instrument,), period


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
