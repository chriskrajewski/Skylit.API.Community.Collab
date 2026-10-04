"""Unit tests for ``fse import-vix`` (``fse.commands.import_vix``).

Each run uses the Project's real ``calendars/exchange_calendar.yaml``, a CSV of
fake VIX values and a Data_Cache under ``tmp_path``. No network is used.

**Validates: Requirements 4.9, 4.11**
"""

from __future__ import annotations

import io
from datetime import date, time
from pathlib import Path

import pytest

from fse import cli
from fse.commands import import_vix
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import DataCache
from fse.timekit import ny_instant

PROJECT_DIR = Path(__file__).resolve().parents[2]
CALENDARS = PROJECT_DIR / "calendars"
EARLY_CLOSE = date(2025, 11, 28)  # 13:15 in exchange_calendar.yaml


def run(csv_text: str, tmp_path: Path) -> tuple[int, str, str, Path]:
    csv_path = tmp_path / "vix.csv"
    csv_path.write_text(csv_text, encoding="utf-8")
    cache = tmp_path / "cache"
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        ["import-vix", str(csv_path), "--cache-dir", str(cache), "--calendar-dir", str(CALENDARS)],
        project_dir=None,
        environ={},
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue(), cache


def test_command_is_discovered() -> None:
    assert import_vix.register in cli.discover_commands()


def test_import_stores_each_row_with_its_observation_times(tmp_path: Path) -> None:
    text = (
        "\ufeffclose, session ,open\n"
        "20.10,2026-03-02,19.85\n"
        "\n"
        "21.02,2026-03-03,\n"
        "17.0,2025-11-28,16.5\n"
    )
    code, out, err, cache_dir = run(text, tmp_path)

    assert (code, err) == (0, "")
    assert "imported VIX daily values for 3 sessions (2025-11-28 to 2026-03-03)" in out
    assert "missing values: 1 without an open, 0 without a close" in out
    with DataCache(cache_dir) as cache:
        daily = cache.vix.read_daily()
    mar2, mar3 = date(2026, 3, 2), date(2026, 3, 3)
    assert daily == {
        EARLY_CLOSE: VixDailyRecord(
            EARLY_CLOSE,
            16.5,
            ny_instant(EARLY_CLOSE, time(9, 31)),
            17.0,
            ny_instant(EARLY_CLOSE, time(13, 15)),
            "import",
        ),
        mar2: VixDailyRecord(
            mar2,
            19.85,
            ny_instant(mar2, time(9, 31)),
            20.10,
            ny_instant(mar2, time(16, 0)),
            "import",
        ),
        mar3: VixDailyRecord(mar3, None, None, 21.02, ny_instant(mar3, time(16, 0)), "import"),
    }


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "is empty"),
        ("session,open\n2026-03-02,19.8\n", "line 1: the header must name exactly"),
        ("session,open,close\n", "holds no data rows"),
        ("session,open,close\n2026-03-07,19.8,20\n", "line 2: 2026-03-07 is not a session"),
        ("session,open,close\n2026-04-03,19.8,20\n", "line 2: 2026-04-03 is not a session"),
        ("session,open,close\n2030-01-02,19.8,20\n", "line 2: date 2030-01-02 is outside"),
        ("session,open,close\n03/02/2026,19.8,20\n", "is not a YYYY-MM-DD date"),
        ("session,open,close\n2026-03-02,abc,20\n", "line 2: open 'abc' is not a number"),
        ("session,open,close\n2026-03-02,nan,20\n", "must be a finite number above 0"),
        ("session,open,close\n2026-03-02,19.8,-1\n", "close '-1' must be a finite number"),
        ("session,open,close\n2026-03-02,,\n", "neither an open nor a close"),
        ("session,open,close\n2026-03-02,1,2,3\n", "expected 3 cells, got 4"),
        (
            "session,open,close\n2026-03-02,1,2\n2026-03-02,1,2\n",
            "line 3: session 2026-03-02 appears more than once",
        ),
    ],
)
def test_a_bad_file_stops_the_import_with_exit_2_and_writes_nothing(
    tmp_path: Path, text: str, reason: str
) -> None:
    code, out, err, cache_dir = run(text, tmp_path)

    assert code == 2
    assert out == ""
    assert err.startswith("error: ")
    assert reason in err
    assert not cache_dir.exists()


def test_a_missing_file_or_calendar_dir_is_invalid_input(tmp_path: Path) -> None:
    err = io.StringIO()
    code = cli.run(
        ["import-vix", str(tmp_path / "absent.csv"), "--calendar-dir", str(CALENDARS)],
        project_dir=None,
        environ={},
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 2
    assert "cannot read" in err.getvalue()

    err = io.StringIO()
    code = cli.run(
        ["import-vix", str(tmp_path / "absent.csv")],
        project_dir=None,
        environ={},
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 2
    assert "pass --calendar-dir DIR" in err.getvalue()
