"""``fse import-vix``: load an Operator CSV of VIX daily values into the Data_Cache (OQ2).

The fallback for sessions Atlas does not serve VIX bars for (design OQ2,
Req 4.9). The CSV has a header row with the columns ``session``, ``open`` and
``close`` in any order, then one row per session::

    session,open,close
    2026-03-02,19.85,20.10
    2026-03-03,,21.02

- ``session``: ``YYYY-MM-DD``, an exchange session of ``exchange_calendar.yaml``.
- ``open``: the VIX index open at 09:30, stored as known at 09:31.
- ``close``: the VIX index close at the RTH close (16:00, or the early close),
  stored as known at that instant. It is the next session's prior close.
- A blank cell stores that value as missing; each row needs at least one
  value. A value must be a finite number above 0. A session appears once.

The whole file is checked before anything is written: any bad row stops the
import with exit 2 and names the file, line and reason. Each imported row
replaces the stored record of its session (source ``import``); other
sessions are kept. The cache directory goes through the path guard first.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
from datetime import date
from pathlib import Path
from typing import ClassVar, Final

from fse.calendars import EXCHANGE_CALENDAR_FILE, load_exchange_calendar, project_calendar_dir
from fse.commands import CommandContext, SubParsers
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import DataCache
from fse.data.vix import open_observed_at
from fse.settings import default_paths
from fse.timekit import NotASessionError, OutsideCoverageError, SessionCalendar

__all__ = ["COLUMNS", "EXIT_INVALID_INPUT", "NAME", "VixImportError", "parse_vix_csv", "register"]

NAME: Final = "import-vix"
COLUMNS: Final = ("session", "open", "close")

# Design "Exit codes": 2 = invalid input.
EXIT_INVALID_INPUT: Final = 2

_SHOWN_CHARS: Final = 40

_DESCRIPTION = """\
Load VIX daily values from an Operator CSV into the Data_Cache, for sessions
Atlas does not serve VIX bars for.

The CSV starts with a header row naming the columns session, open and close
(any order), then one row per session:

  session,open,close
  2026-03-02,19.85,20.10
  2026-03-03,,21.02

session is YYYY-MM-DD and must be an exchange session. open is the VIX open at
09:30 (known at 09:31); close is the VIX close at the RTH close (16:00 or the
early close). A blank cell is a missing value; each row needs at least one.
Each imported row replaces the stored values of its session."""

_EPILOG = """\
exit status:
  0  every row was imported
  2  the CSV, a row, the calendar or the cache path is invalid; nothing is written
  4  the Data_Cache write failed"""


class VixImportError(Exception):
    """The CSV cannot be imported; the message names the file and, for a row, its line."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="load VIX daily values from an Operator CSV into the Data_Cache",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("csv", type=Path, metavar="CSV", help="the CSV file to import")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="the Data_Cache directory (default: ~/.skylit-fse/cache)",
    )
    parser.add_argument(
        "--calendar-dir",
        type=Path,
        metavar="DIR",
        help=f"the folder holding {EXCHANGE_CALENDAR_FILE} (default: the Project's calendars/)",
    )
    parser.set_defaults(handler=run_import)


def run_import(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Check every row, then write the records. Returns 0; failures raise with an exit code."""
    calendar_dir: Path | None = args.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise VixImportError("the Project folder was not found; pass --calendar-dir DIR")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    calendar = load_exchange_calendar(calendar_dir / EXCHANGE_CALENDAR_FILE).sessions
    path: Path = args.csv
    records = parse_vix_csv(_read_text(path), calendar, source=path)
    cache_dir: Path = default_paths().cache if args.cache_dir is None else args.cache_dir
    with DataCache(cache_dir) as cache:
        cache.vix.write_daily(records)
        target = cache.vix.daily_path
    first, last = records[0].session, records[-1].session
    sessions = "1 session" if len(records) == 1 else f"{len(records)} sessions"
    ctx.writer.echo(
        f"imported VIX daily values for {sessions} ({first} to {last}) from {path} into {target}"
    )
    no_open = sum(r.open is None for r in records)
    no_close = sum(r.close is None for r in records)
    if no_open or no_close:
        ctx.writer.echo(f"missing values: {no_open} without an open, {no_close} without a close")
    return 0


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise VixImportError(f"cannot read {path}: {exc.strerror or type(exc).__name__}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise VixImportError(f"{path} is not UTF-8 text") from None


def parse_vix_csv(
    text: str, calendar: SessionCalendar, *, source: Path | str = "CSV"
) -> list[VixDailyRecord]:
    """Every row of ``text`` as an ``import`` record, in session order.

    Raises :class:`VixImportError` naming ``source`` and the first bad line.
    """
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise VixImportError(f"{source} is empty; the first row must be the header")
        index = _header(header, source)
        records: dict[date, VixDailyRecord] = {}
        for row in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in row):
                continue  # a blank line
            record = _row(row, index, calendar, f"{source}, line {line}")
            if record.session in records:
                raise VixImportError(
                    f"{source}, line {line}: session {record.session} appears more than once"
                )
            records[record.session] = record
    except csv.Error as exc:
        raise VixImportError(f"{source}, line {reader.line_num}: not valid CSV: {exc}") from None
    if not records:
        raise VixImportError(f"{source} holds no data rows after the header")
    return [records[d] for d in sorted(records)]


def _header(header: list[str], source: Path | str) -> dict[str, int]:
    names = [cell.strip() for cell in header]
    if names and names[0].startswith("\ufeff"):
        names[0] = names[0][1:]
    if sorted(names) != sorted(COLUMNS):
        raise VixImportError(
            f"{source}, line 1: the header must name exactly the columns "
            f"{', '.join(COLUMNS)}, got {_shown(','.join(names))}"
        )
    return {name: i for i, name in enumerate(names)}


def _row(
    row: list[str], index: dict[str, int], calendar: SessionCalendar, where: str
) -> VixDailyRecord:
    if len(row) != len(COLUMNS):
        raise VixImportError(f"{where}: expected {len(COLUMNS)} cells, got {len(row)}")
    session = _session(row[index["session"]].strip(), calendar, where)
    open_value = _value(row[index["open"]].strip(), "open", where)
    close_value = _value(row[index["close"]].strip(), "close", where)
    if open_value is None and close_value is None:
        raise VixImportError(f"{where}: the row holds neither an open nor a close")
    return VixDailyRecord(
        session=session,
        open=open_value,
        open_at_ns=None if open_value is None else open_observed_at(calendar, session),
        close=close_value,
        close_at_ns=None if close_value is None else calendar.rth_close(session),
        source="import",
    )


def _session(text: str, calendar: SessionCalendar, where: str) -> date:
    try:
        if len(text) != 10:
            raise ValueError(text)
        session = date.fromisoformat(text)
    except ValueError:
        raise VixImportError(f"{where}: session {_shown(text)} is not a YYYY-MM-DD date") from None
    try:
        if not calendar.is_session(session):
            raise NotASessionError(f"{session} is not a session (weekend or exchange holiday)")
    except (OutsideCoverageError, NotASessionError) as exc:
        raise VixImportError(f"{where}: {exc}") from None
    return session


def _value(text: str, column: str, where: str) -> float | None:
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise VixImportError(f"{where}: {column} {_shown(text)} is not a number") from None
    if not math.isfinite(value) or value <= 0:
        raise VixImportError(f"{where}: {column} {_shown(text)} must be a finite number above 0")
    return value


def _shown(text: str) -> str:
    """``text`` quoted and cut short, for an error message."""
    return repr(text if len(text) <= _SHOWN_CHARS else text[:_SHOWN_CHARS] + "...")
