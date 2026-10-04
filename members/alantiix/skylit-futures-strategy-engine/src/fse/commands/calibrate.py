"""``fse calibrate``: measure the local Data_Cache to help choose a config value (Req 7.4).

``fse calibrate regime-min-abs --start YYYY-MM-DD --end YYYY-MM-DD --percentile P``
prints the P-th percentile of the Regime_Symbol's gamma King absolute values
and the number of sessions they come from. The Operator picks
``regime.min_abs_value`` from it (the Structureless threshold, which has no
default) and records the derivation in ``docs/traceability.md``.

- **Value**: :func:`fse.engine.regime.king_abs_value` of each Snapshot,
  ``max(|value|)`` over its strikes. The Structureless check compares this
  with ``min_abs_value``.
- **Sample**: one value per cached RTH Snapshot (``asOf`` from the RTH open,
  09:30, up to but not including the RTH close, 16:00 or the early close) of
  the symbol's gamma Snapshots in the given Heatmap_View, for every exchange
  session from ``--start`` to ``--end`` (both inclusive).
- **Percentile**: linear interpolation between the closest ranks (NumPy's
  ``"linear"`` method, the same as a spreadsheet ``PERCENTILE.INC``). P = 0 is
  the smallest value and P = 100 the largest.
- **Windows**: ``complete`` windows are read and verified against the catalog;
  ``no_data`` windows add nothing; ``incomplete`` windows are skipped and
  counted, so the output says when a rerun of ``fse pull`` would add data.

The command reads only the local Data_Cache and the exchange calendar. It makes
no network request and writes no file.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Final

import numpy as np

from fse.calendars import EXCHANGE_CALENDAR_FILE, load_exchange_calendar, project_calendar_dir
from fse.commands import CommandContext, SubParsers
from fse.config.schema.data import DEFAULT_SYMBOLS, DataConfig
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView, ViewLimit
from fse.engine.regime import king_abs_value
from fse.engine.types import Metric
from fse.settings import default_paths
from fse.timekit import OutsideCoverageError, SessionCalendar

__all__ = [
    "EXIT_DATA_IO",
    "EXIT_INVALID_INPUT",
    "NAME",
    "REGIME_MIN_ABS",
    "CalibrateDataError",
    "CalibrateUsageError",
    "KingSample",
    "collect_king_values",
    "percentile",
    "register",
    "run_regime_min_abs",
]

NAME: Final = "calibrate"
REGIME_MIN_ABS: Final = "regime-min-abs"

# Design "Exit codes".
EXIT_INVALID_INPUT: Final = 2
EXIT_DATA_IO: Final = 4

METRIC: Final[Metric] = "gamma"
DEFAULT_SYMBOL: Final = DataConfig().regime_symbol  # the data.regime_symbol default

_DEFAULT_VIEW: Final = HeatmapView()

_DESCRIPTION = """\
Measure the local Data_Cache to help choose a Strategy_Config value. Each
calibration prints numbers only: it makes no network request and writes no
file."""

_MIN_ABS_DESCRIPTION = """\
Print the P-th percentile of the Regime_Symbol's gamma King absolute values,
max(|value|) over the strikes of each cached RTH Snapshot (09:30 to the RTH
close), for the exchange sessions from --start to --end, and the number of
sessions with such Snapshots. The Structureless Regime applies when the King
is below regime.min_abs_value, which has no default.

The percentile interpolates linearly between the closest ranks (0 is the
smallest value, 100 the largest). Pass the Heatmap_View flags of the pull that
filled the Data_Cache. Incomplete windows are skipped and counted.

Nothing is written: choose the value, set regime.min_abs_value in the
Strategy_Config and record its derivation in docs/traceability.md."""

_MIN_ABS_EPILOG = """\
exit status:
  0  the percentile was printed
  2  invalid input: a flag, the date range or the calendar file
  4  the Data_Cache holds no RTH Snapshot for the request, or a cached file
     failed its check"""


class CalibrateUsageError(Exception):
    """Flags, dates or a calendar that cannot be used (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


class CalibrateDataError(Exception):
    """The Data_Cache holds no Snapshot to measure for the request (exit 4)."""

    exit_code: ClassVar[int] = EXIT_DATA_IO


# ---------------------------------------------------------------- measurement


@dataclass(frozen=True, slots=True)
class KingSample:
    """The King absolute values of the cached RTH Snapshots of a session range.

    ``values`` holds one value per Snapshot, in session and window order.
    ``sessions`` counts the sessions with at least one RTH Snapshot, out of
    ``sessions_in_range`` exchange sessions. ``incomplete_windows`` counts the
    windows skipped because their write never finished.
    """

    values: tuple[float, ...]
    sessions: int
    sessions_in_range: int
    incomplete_windows: int


def collect_king_values(
    cache: DataCache,
    calendar: SessionCalendar,
    *,
    symbol: str,
    view_id: str,
    start: date,
    end: date,
) -> KingSample:
    """Read the ``symbol`` gamma windows of ``view_id`` for the sessions from ``start`` to ``end``.

    Raises :class:`CalibrateUsageError` when ``start`` is after ``end`` or the
    calendar does not cover the range, and ``CacheReadError`` (exit 4) when a
    complete window fails its check.
    """
    if start > end:
        raise CalibrateUsageError(f"--start {start} is after --end {end}")
    try:
        sessions = calendar.sessions(start, end)
    except OutsideCoverageError as exc:
        raise CalibrateUsageError(str(exc)) from None
    values: list[float] = []
    with_snapshots = 0
    incomplete = 0
    for session in sessions:
        rth_open, rth_close = calendar.rth_open(session), calendar.rth_close(session)
        before = len(values)
        records = cache.catalog.windows(
            symbol=symbol, metric=METRIC, view_id=view_id, session=session
        )
        for record in records:
            if record.status == "incomplete":
                incomplete += 1
                continue
            if record.status == "no_data":
                continue
            key = CacheWindowKey(symbol, METRIC, view_id, session, record.start_ns)
            values.extend(
                king_abs_value(snap)
                for snap in cache.read_window(key)
                if rth_open <= snap.as_of_ns < rth_close
            )
        with_snapshots += len(values) > before
    return KingSample(
        values=tuple(values),
        sessions=with_snapshots,
        sessions_in_range=len(sessions),
        incomplete_windows=incomplete,
    )


def percentile(values: Sequence[float], p: float) -> float:
    """The ``p``-th percentile (0 to 100) of ``values``, interpolated linearly between ranks."""
    if not values:
        raise ValueError("percentile of no values")
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentile must be from 0 to 100, got {p!r}")
    return float(np.percentile(np.asarray(values, dtype=np.float64), p, method="linear"))


# ---------------------------------------------------------------- argument types


def _date(text: str) -> date:
    try:
        if len(text) != 10:
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date") from None


def _percentile(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not 0.0 <= value <= 100.0:  # also rejects NaN
        raise argparse.ArgumentTypeError(f"{text!r} is not a percentile from 0 to 100")
    return value


def _view_limit(text: str) -> ViewLimit:
    if text == "all":
        return "all"
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is neither a whole number above 0 nor 'all'")
    return value


def _name_list(text: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in text.split(","))
    if any(not item for item in items):
        raise argparse.ArgumentTypeError(f"{text!r} is not a comma-separated list of names")
    return items


# ---------------------------------------------------------------- the parser


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="measure the local Data_Cache to help choose a Strategy_Config value",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    calibrations = parser.add_subparsers(
        dest="calibration", metavar="<calibration>", title="calibrations", required=True
    )
    _register_regime_min_abs(calibrations)


def _register_regime_min_abs(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        REGIME_MIN_ABS,
        help="percentile of the Regime_Symbol's gamma King absolute values, "
        "for regime.min_abs_value",
        description=_MIN_ABS_DESCRIPTION,
        epilog=_MIN_ABS_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add = parser.add_argument
    add("--start", type=_date, required=True, metavar="YYYY-MM-DD", help="first session")
    add("--end", type=_date, required=True, metavar="YYYY-MM-DD", help="last session")
    add(
        "--percentile",
        type=_percentile,
        required=True,
        metavar="P",
        help="the percentile to print, from 0 to 100",
    )
    add(
        "--symbol",
        choices=DEFAULT_SYMBOLS,
        default=DEFAULT_SYMBOL,
        help="the Regime_Symbol, data.regime_symbol (default: %(default)s)",
    )

    view = parser.add_argument_group("Heatmap_View (as passed to fse pull)")
    view.add_argument(
        "--max-strikes",
        type=_view_limit,
        default=_DEFAULT_VIEW.max_strikes,
        metavar="N|all",
        help="strikes per Snapshot (default: %(default)s)",
    )
    view.add_argument(
        "--max-expirations",
        type=_view_limit,
        default=_DEFAULT_VIEW.max_expirations,
        metavar="N|all",
        help="expirations per Snapshot (default: %(default)s)",
    )
    view.add_argument(
        "--expirations",
        type=_name_list,
        metavar="LIST",
        help="only these expirations, YYYY-MM-DD,... (default: the nearest ones)",
    )
    view.add_argument("--include-empty", action="store_true", help="keep strikes with no exposure")

    paths = parser.add_argument_group("paths")
    paths.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="the Data_Cache (default: ~/.skylit-fse/cache)",
    )
    paths.add_argument(
        "--calendar-dir",
        type=Path,
        metavar="DIR",
        help=f"the folder holding {EXCHANGE_CALENDAR_FILE} (default: the Project's calendars/)",
    )
    parser.set_defaults(handler=run_regime_min_abs)


# ---------------------------------------------------------------- the handler


def _view_from_args(args: argparse.Namespace) -> HeatmapView:
    try:
        return HeatmapView(
            max_strikes=args.max_strikes,
            max_expirations=args.max_expirations,
            expirations=args.expirations,
            include_empty=args.include_empty,
        )
    except ValueError as exc:
        raise CalibrateUsageError(str(exc)) from None


def run_regime_min_abs(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Print the percentile and the session count. Failures raise with an exit code."""
    start: date = args.start
    end: date = args.end
    p: float = args.percentile
    symbol: str = args.symbol
    view_id = _view_from_args(args).view_id()
    calendar_dir: Path | None = args.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise CalibrateUsageError("the Project folder was not found; pass --calendar-dir DIR")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    calendar = load_exchange_calendar(calendar_dir / EXCHANGE_CALENDAR_FILE).sessions
    cache_dir: Path = default_paths().cache if args.cache_dir is None else args.cache_dir
    with DataCache(cache_dir) as cache:
        sample = collect_king_values(
            cache, calendar, symbol=symbol, view_id=view_id, start=start, end=end
        )
        if not sample.values:
            raise CalibrateDataError(_no_data_message(cache, sample, symbol, view_id, start, end))
    value = percentile(sample.values, p)
    write = ctx.writer.echo
    write(
        f"{symbol} gamma King absolute values of RTH Snapshots, sessions {start} to {end}, "
        f"Heatmap_View {view_id}"
    )
    write(
        f"sessions: {sample.sessions} of the {sample.sessions_in_range} exchange sessions "
        "in the range"
    )
    write(f"snapshots: {len(sample.values)}")
    if sample.incomplete_windows:
        write(
            f"incomplete windows skipped: {sample.incomplete_windows}; rerun fse pull for the "
            "range to fill them"
        )
    write(f"percentile {p:g}: {value!r}")
    write(
        "No file was written. Choose regime.min_abs_value, set it in the Strategy_Config and "
        "record its derivation in docs/traceability.md."
    )
    return 0


def _no_data_message(
    cache: DataCache, sample: KingSample, symbol: str, view_id: str, start: date, end: date
) -> str:
    lines = [
        f"the Data_Cache {cache.root} holds no RTH {symbol} gamma Snapshot of Heatmap_View "
        f"{view_id} for the {sample.sessions_in_range} exchange sessions from {start} to {end}"
    ]
    if sample.incomplete_windows:
        lines.append(
            f"{sample.incomplete_windows} windows are incomplete; rerun fse pull for the range"
        )
    other_views = sorted(
        {
            record.view_id
            for record in cache.catalog.windows(symbol=symbol, metric=METRIC)
            if start <= record.session <= end and record.view_id != view_id
        }
    )
    if other_views:
        lines.append(
            f"it holds {symbol} gamma windows of other Heatmap_Views in the range: "
            f"{', '.join(other_views)}; pass the Heatmap_View flags of that pull"
        )
    else:
        lines.append("run fse pull for the range first")
    return "\n".join(lines)
