"""The pull estimate and size confirmation (design §3 "Estimate"; Req 3.1-3.2).

Before the first Replay_Request a pull prints, in total and per symbol and
metric, what the Cache_Windows it will request from Skylit cost
(:func:`estimate_pull`, :func:`print_estimate`):

- **Requests**: exact counts from the :class:`~fse.data.planner.PullPlan`, split
  into ``/v1/historical/range`` and ``/v1/historical`` requests.
- **Credits**: each request at the configured credits of its endpoint
  (defaults: range 25, historical 5).
- **Disk**: frames x strikes x 8 bytes x the compression ratio, in GB
  (10^9 bytes). Served windows (Req 3.8) and skipped sessions (Req 3.5) add
  nothing.

Frames per fetched Cache_Window of one symbol and metric:

- a range window holds one frame per second of the window (range resolution
  is 1 s where Skylit stores it, else 1 min, so this is the upper bound), plus
  one frame per ``--label-sample-minutes`` sample;
- a ``/v1/historical`` window holds one frame per sample instant;
- with a storage interval (Req 3.7), at most one frame per interval boundary:
  ``window seconds // interval + 1``.

Strikes per frame are the Heatmap_View's ``max_strikes`` (default 92). For a
view with ``max_strikes="all"``, :data:`ALL_STRIKES_ASSUMED` stands in unless a
count is configured.

The compression ratio is the configured value (default 0.35) until earlier
pulls have stored windows of the same Heatmap_View. Then
:func:`calibrate_compression` replaces it with the bytes on disk divided by
the raw bytes the same formula gives for the stored rows. Because the ratio is
measured against the formula itself, it also absorbs any difference between
the assumed and the stored strike counts.

A request carries up to 5 (range) or 10 (historical) symbols. It counts once
in the row of every symbol it carries, so the per-symbol rows add up to more
than the total. Disk and windows are per symbol and add up to the total.

When the estimated disk size exceeds the configured limit (default 20 GB),
:func:`confirm_size` asks the Operator at a prompt in an interactive run. A
non-interactive run, a declined prompt or a closed input raises
:class:`PullSizeLimitError` (exit 2) before any Replay_Request (Req 3.2).
Every console line goes through the :class:`~fse.logio.LogWriter`; the input
function reads a line and prints nothing.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Final, Literal, TextIO, cast

from fse.data.cache import CacheWindowKey, DataCache, HeatmapView, check_storage_interval
from fse.data.planner import (
    EXIT_INVALID_INPUT,
    HISTORICAL_MAX_SYMBOLS,
    RANGE_MAX_SYMBOLS,
    PullPlan,
    ReplayEndpoint,
    WindowPlan,
)
from fse.engine.types import Metric
from fse.logio import LogWriter
from fse.timekit import NS_PER_SECOND

__all__ = [
    "ALL_STRIKES_ASSUMED",
    "BYTES_PER_GB",
    "BYTES_PER_VALUE",
    "DEFAULT_COMPRESSION_RATIO",
    "DEFAULT_ESTIMATE_CONFIG",
    "DEFAULT_HISTORICAL_CREDITS",
    "DEFAULT_RANGE_CREDITS",
    "DEFAULT_SIZE_LIMIT_GB",
    "RANGE_FRAME_SECONDS",
    "CompressionCalibration",
    "EstimateConfig",
    "EstimateLine",
    "PullEstimate",
    "PullSizeLimitError",
    "SizeDecision",
    "announce_pull",
    "calibrate_compression",
    "check_size_limit",
    "confirm_size",
    "estimate_pull",
    "format_gb",
    "is_interactive",
    "print_estimate",
    "render_estimate",
]

DEFAULT_RANGE_CREDITS: Final = 25
DEFAULT_HISTORICAL_CREDITS: Final = 5
DEFAULT_COMPRESSION_RATIO: Final = 0.35
DEFAULT_SIZE_LIMIT_GB: Final = 20.0

BYTES_PER_VALUE: Final = 8  # one float64 strike value
BYTES_PER_GB: Final = 1_000_000_000
RANGE_FRAME_SECONDS: Final = 1  # the finest range resolution Skylit stores
ALL_STRIKES_ASSUMED: Final = 1000
"""Strikes per frame assumed for ``max_strikes="all"`` until calibration corrects it."""

_YES: Final = frozenset({"y", "yes"})

type SizeDecision = Literal["within_limit", "confirmed"]
type CompressionSource = Literal["configured", "calibrated"]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_finite(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True, slots=True)
class EstimateConfig:
    """The configured inputs of the estimate. Defaults are the design defaults.

    ``storage_interval_s`` is the Data_Cache storage interval (Req 3.7), or
    ``None`` to store every Snapshot. ``strikes_per_frame`` overrides the
    Heatmap_View's ``max_strikes``.
    """

    range_credits: int = DEFAULT_RANGE_CREDITS
    historical_credits: int = DEFAULT_HISTORICAL_CREDITS
    compression_ratio: float = DEFAULT_COMPRESSION_RATIO
    storage_interval_s: int | None = None
    strikes_per_frame: int | None = None

    def __post_init__(self) -> None:
        for name in ("range_credits", "historical_credits"):
            value: object = getattr(self, name)
            if not _is_int(value) or cast(int, value) < 0:
                raise ValueError(f"{name} must be a whole number of credits >= 0, got {value!r}")
        if not _is_positive_finite(self.compression_ratio):
            raise ValueError(
                f"compression_ratio must be a finite number above 0, got {self.compression_ratio!r}"
            )
        if self.storage_interval_s is not None:
            check_storage_interval(self.storage_interval_s)
        if self.strikes_per_frame is not None and (
            not _is_int(self.strikes_per_frame) or self.strikes_per_frame < 1
        ):
            raise ValueError(
                f"strikes_per_frame must be None or a whole number >= 1, "
                f"got {self.strikes_per_frame!r}"
            )

    def credits(self, endpoint: ReplayEndpoint) -> int:
        """The configured credits of one request to ``endpoint``."""
        return self.range_credits if endpoint == "range" else self.historical_credits

    def strikes_for(self, view: HeatmapView) -> int:
        """Strikes per frame: the override, else the view's ``max_strikes``."""
        if self.strikes_per_frame is not None:
            return self.strikes_per_frame
        return view.max_strikes if isinstance(view.max_strikes, int) else ALL_STRIKES_ASSUMED


DEFAULT_ESTIMATE_CONFIG: Final = EstimateConfig()


# ---------------------------------------------------------------- calibration


@dataclass(frozen=True, slots=True)
class CompressionCalibration:
    """Stored bytes against the raw bytes of the stored rows, for one Heatmap_View."""

    view_id: str
    windows: int
    rows: int
    stored_bytes: int
    strikes_per_frame: int

    def __post_init__(self) -> None:
        for name in ("windows", "rows", "stored_bytes", "strikes_per_frame"):
            value: object = getattr(self, name)
            if not _is_int(value) or cast(int, value) < 1:
                raise ValueError(f"{name} must be a whole number >= 1, got {value!r}")

    @property
    def raw_bytes(self) -> int:
        return self.rows * self.strikes_per_frame * BYTES_PER_VALUE

    @property
    def ratio(self) -> float:
        return self.stored_bytes / self.raw_bytes


def calibrate_compression(
    cache: DataCache, view_id: str, *, strikes_per_frame: int
) -> CompressionCalibration | None:
    """The compression ratio of the ``complete`` windows already stored for ``view_id``.

    Counts each window with at least one row whose file is on disk; the size
    is the file's size in bytes. Returns ``None`` when there is no such window,
    so the configured ratio stays in use. Reads only the catalog and file
    sizes; writes nothing.
    """
    windows = rows = stored = 0
    for record in cache.catalog.windows(view_id=view_id):
        if record.status != "complete" or not record.rows:
            continue
        try:
            key = CacheWindowKey(
                record.symbol,
                cast(Metric, record.metric),
                record.view_id,
                record.session,
                record.start_ns,
            )
            size = cache.window_path(key).stat().st_size
        except ValueError, OSError:
            continue  # not a window this version can locate, or the file is gone
        windows += 1
        rows += record.rows
        stored += size
    if not windows or not stored:
        return None
    return CompressionCalibration(view_id, windows, rows, stored, strikes_per_frame)


# ---------------------------------------------------------------- the estimate


@dataclass(frozen=True, slots=True)
class EstimateLine:
    """Requests, credits and disk for one symbol and metric, or for the whole pull.

    ``symbol`` and ``metric`` are ``None`` on the total line. ``windows`` counts
    the Cache_Windows the pull requests; ``frames`` the frames they will hold.
    """

    symbol: str | None
    metric: Metric | None
    range_requests: int
    historical_requests: int
    credits: int
    windows: int
    frames: int
    disk_bytes: float

    @property
    def requests(self) -> int:
        return self.range_requests + self.historical_requests

    @property
    def disk_gb(self) -> float:
        return self.disk_bytes / BYTES_PER_GB

    @property
    def label(self) -> str:
        return "total" if self.symbol is None else f"{self.symbol} {self.metric}"


@dataclass(frozen=True, slots=True)
class PullEstimate:
    """The estimate of one pull plan (Req 3.1).

    ``lines`` holds one line per configured symbol and metric, symbols in
    configured order, then metrics. ``served_windows`` are Cache_Windows read
    from the Data_Cache with no request; ``skipped_sessions`` counts the
    (symbol, session) pairs dated before the symbol's first history.
    """

    total: EstimateLine
    lines: tuple[EstimateLine, ...]
    sessions: int
    served_windows: int
    skipped_sessions: int
    strikes_per_frame: int
    compression_ratio: float
    calibration: CompressionCalibration | None
    config: EstimateConfig

    @property
    def request_count(self) -> int:
        return self.total.requests

    @property
    def credits(self) -> int:
        return self.total.credits

    @property
    def disk_gb(self) -> float:
        return self.total.disk_gb

    @property
    def compression_source(self) -> CompressionSource:
        return "configured" if self.calibration is None else "calibrated"

    def line(self, symbol: str, metric: Metric) -> EstimateLine:
        for line in self.lines:
            if (line.symbol, line.metric) == (symbol, metric):
                return line
        raise KeyError(f"{symbol} {metric} is not a configured symbol and metric of the pull")

    def exceeds(self, limit_gb: float) -> bool:
        """True when the estimated disk size is above ``limit_gb`` (Req 3.2)."""
        return self.total.disk_bytes > check_size_limit(limit_gb) * BYTES_PER_GB


class _Tally:
    __slots__ = ("credits", "frames", "historical", "range", "windows")

    def __init__(self) -> None:
        self.range = self.historical = self.credits = self.windows = self.frames = 0

    def add_request(self, endpoint: ReplayEndpoint, credits: int) -> None:
        if endpoint == "range":
            self.range += 1
        else:
            self.historical += 1
        self.credits += credits

    def add_window(self, frames: int) -> None:
        self.windows += 1
        self.frames += frames

    def line(
        self, symbol: str | None, metric: Metric | None, bytes_per_frame: float
    ) -> EstimateLine:
        return EstimateLine(
            symbol=symbol,
            metric=metric,
            range_requests=self.range,
            historical_requests=self.historical,
            credits=self.credits,
            windows=self.windows,
            frames=self.frames,
            disk_bytes=self.frames * bytes_per_frame,
        )


def _window_frames(
    window: WindowPlan, storage_interval_s: int | None
) -> dict[tuple[str, str], int]:
    """Frames each fetched (symbol, metric) of one Cache_Window will hold."""
    duration_ns = window.end_ns - window.start_ns
    ranged: set[tuple[str, str]] = set()
    samples: dict[tuple[str, str], set[int]] = {}
    for request in window.requests:
        for symbol in request.symbols:
            pair: tuple[str, str] = (symbol, request.metric)
            if request.endpoint == "range":
                ranged.add(pair)
            elif request.at_ns is not None:
                samples.setdefault(pair, set()).add(request.at_ns)
    per_range = -(-duration_ns // (RANGE_FRAME_SECONDS * NS_PER_SECOND))  # ceil
    cap = (
        None
        if storage_interval_s is None
        else duration_ns // (storage_interval_s * NS_PER_SECOND) + 1
    )
    out: dict[tuple[str, str], int] = {}
    for key in ranged | samples.keys():
        frames = (per_range if key in ranged else 0) + len(samples.get(key, ()))
        out[key] = frames if cap is None else min(frames, cap)
    return out


def estimate_pull(
    plan: PullPlan,
    config: EstimateConfig = DEFAULT_ESTIMATE_CONFIG,
    *,
    cache: DataCache | None = None,
) -> PullEstimate:
    """Requests, credits and disk of ``plan``, in total and per symbol and metric.

    With ``cache``, the compression ratio is recalibrated from the windows
    already stored for the plan's Heatmap_View (:func:`calibrate_compression`).
    """
    strikes = config.strikes_for(plan.spec.view)
    calibration = (
        None
        if cache is None
        else calibrate_compression(cache, plan.view_id, strikes_per_frame=strikes)
    )
    ratio = config.compression_ratio if calibration is None else calibration.ratio
    bytes_per_frame = strikes * BYTES_PER_VALUE * ratio

    tallies: dict[tuple[str, str], _Tally] = {
        (symbol, metric): _Tally() for symbol in plan.spec.symbols for metric in plan.spec.metrics
    }
    total = _Tally()
    served = 0
    for session in plan.sessions:
        for window in session.windows:
            served += len(window.served)
            for request in window.requests:
                cost = config.credits(request.endpoint)
                total.add_request(request.endpoint, cost)
                for symbol in request.symbols:
                    tallies[(symbol, request.metric)].add_request(request.endpoint, cost)
            frames = _window_frames(window, config.storage_interval_s)
            for key in window.fetch:
                n = frames.get((key.symbol, key.metric), 0)
                tallies[(key.symbol, key.metric)].add_window(n)
                total.add_window(n)

    lines = tuple(
        tallies[(symbol, metric)].line(symbol, metric, bytes_per_frame)
        for symbol in plan.spec.symbols
        for metric in plan.spec.metrics
    )
    return PullEstimate(
        total=total.line(None, None, bytes_per_frame),
        lines=lines,
        sessions=len(plan.sessions),
        served_windows=served,
        skipped_sessions=sum(1 for _ in plan.skipped()),
        strikes_per_frame=strikes,
        compression_ratio=ratio,
        calibration=calibration,
        config=config,
    )


# ---------------------------------------------------------------- printing


def format_gb(gb: float) -> str:
    """GB with 3 decimals, or 6 below 0.001 GB so a small pull does not print 0."""
    return f"{gb:,.6f}" if 0 < gb < 0.001 else f"{gb:,.3f}"


_HEADERS: Final = ("scope", "requests", "range", "historical", "credits", "windows", "disk GB")


def _cells(line: EstimateLine) -> tuple[str, ...]:
    return (
        line.label,
        f"{line.requests:,}",
        f"{line.range_requests:,}",
        f"{line.historical_requests:,}",
        f"{line.credits:,}",
        f"{line.windows:,}",
        format_gb(line.disk_gb),
    )


def render_estimate(estimate: PullEstimate) -> list[str]:
    """The estimate as console lines: a summary, a table and how it was computed."""
    rows = [_HEADERS, *(_cells(line) for line in (estimate.total, *estimate.lines))]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_HEADERS))]

    def fmt(row: tuple[str, ...]) -> str:
        first = row[0].ljust(widths[0])
        rest = (cell.rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True))
        return "  " + "  ".join((first, *rest))

    config = estimate.config
    if estimate.calibration is None:
        source = "configured"
    else:
        cal = estimate.calibration
        source = (
            f"calibrated from {cal.windows:,} stored windows: "
            f"{cal.stored_bytes:,} bytes for {cal.rows:,} rows"
        )
    storage = (
        "every Snapshot stored"
        if config.storage_interval_s is None
        else f"storage interval {config.storage_interval_s} s"
    )
    return [
        "Pull estimate for the Cache_Windows this pull requests from Skylit: "
        f"{estimate.sessions:,} sessions, {estimate.total.windows:,} windows to request, "
        f"{estimate.served_windows:,} served from the Data_Cache, "
        f"{estimate.skipped_sessions:,} symbol sessions skipped before first history.",
        *(fmt(row) for row in rows),
        f"Credits per request: range {config.range_credits}, "
        f"historical {config.historical_credits}.",
        f"Disk: frames x {estimate.strikes_per_frame} strikes x {BYTES_PER_VALUE} bytes x "
        f"compression ratio {estimate.compression_ratio:.3f} ({source}); {storage}.",
        f"A request carries up to {RANGE_MAX_SYMBOLS} (range) or {HISTORICAL_MAX_SYMBOLS} "
        "(historical) symbols and counts in the row of each symbol it carries.",
    ]


def print_estimate(estimate: PullEstimate, writer: LogWriter) -> None:
    """Print :func:`render_estimate` through the Log_Writer."""
    for line in render_estimate(estimate):
        writer.echo(line)


# ---------------------------------------------------------------- size confirmation


class PullSizeLimitError(Exception):
    """The pull is over the size limit and was not confirmed; nothing was sent (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, message: str, *, estimated_gb: float, limit_gb: float) -> None:
        super().__init__(message)
        self.estimated_gb = estimated_gb
        self.limit_gb = limit_gb


def check_size_limit(limit_gb: object) -> float:
    """``limit_gb`` as a float if it is a finite number of GB above 0."""
    if not _is_positive_finite(limit_gb):
        raise ValueError(f"the size limit must be a finite number of GB above 0, got {limit_gb!r}")
    return float(cast(float, limit_gb))


def _isatty(stream: TextIO | None) -> bool:
    if stream is None:
        return False
    try:
        return stream.isatty()
    except ValueError, OSError:  # closed or detached
        return False


def is_interactive(stdin: TextIO | None = None, stdout: TextIO | None = None) -> bool:
    """True when both stdin and stdout are terminals (defaults: ``sys.stdin``, ``sys.stdout``)."""
    return _isatty(sys.stdin if stdin is None else stdin) and _isatty(
        sys.stdout if stdout is None else stdout
    )


def confirm_size(
    estimate: PullEstimate,
    *,
    writer: LogWriter,
    limit_gb: float = DEFAULT_SIZE_LIMIT_GB,
    interactive: bool | None = None,
    read_line: Callable[[], str] = input,
) -> SizeDecision:
    """Let the pull proceed only within the size limit or after the Operator confirms (Req 3.2).

    Returns ``"within_limit"`` without a prompt when the estimate is at or
    under ``limit_gb``. Over the limit, an interactive run (``interactive``,
    default :func:`is_interactive`) prints the question through ``writer`` and
    reads one line with ``read_line``; ``y`` or ``yes`` (any case) returns
    ``"confirmed"``. A non-interactive run, any other answer or end of input
    raises :class:`PullSizeLimitError` (exit 2), so the caller sends no
    Replay_Request.
    """
    limit = check_size_limit(limit_gb)
    if not estimate.exceeds(limit):
        return "within_limit"
    over = (
        f"the estimated disk size {format_gb(estimate.disk_gb)} GB exceeds "
        f"the size limit {limit:g} GB"
    )
    if interactive is None:
        interactive = is_interactive()
    if not interactive:
        raise PullSizeLimitError(
            f"Pull stopped: {over}, and a non-interactive run cannot confirm it. "
            "No Replay_Request was sent. Raise the size limit or run the pull from a terminal.",
            estimated_gb=estimate.disk_gb,
            limit_gb=limit,
        )
    writer.echo(f"The {over}.")
    writer.echo("Type yes to start the pull, or press Enter to stop it:")
    try:
        answer = read_line()
    except EOFError:
        answer = ""
    if answer.strip().lower() in _YES:
        writer.echo("Confirmed: the pull continues.")
        return "confirmed"
    raise PullSizeLimitError(
        f"Pull stopped: {over}, and the Operator did not confirm it. No Replay_Request was sent.",
        estimated_gb=estimate.disk_gb,
        limit_gb=limit,
    )


def announce_pull(
    plan: PullPlan,
    *,
    writer: LogWriter,
    config: EstimateConfig = DEFAULT_ESTIMATE_CONFIG,
    cache: DataCache | None = None,
    limit_gb: float = DEFAULT_SIZE_LIMIT_GB,
    interactive: bool | None = None,
    read_line: Callable[[], str] = input,
) -> PullEstimate:
    """Estimate, print and confirm the pull; call before the first Replay_Request.

    Raises :class:`PullSizeLimitError` (exit 2) when the size check stops the
    pull, after the estimate is printed so the Operator sees why.
    """
    check_size_limit(limit_gb)
    estimate = estimate_pull(plan, config, cache=cache)
    print_estimate(estimate, writer)
    confirm_size(
        estimate,
        writer=writer,
        limit_gb=limit_gb,
        interactive=interactive,
        read_line=read_line,
    )
    return estimate
