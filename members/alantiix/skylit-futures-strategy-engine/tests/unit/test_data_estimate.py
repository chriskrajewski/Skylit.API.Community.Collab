"""Unit tests for the pull estimate and size confirmation (design §3 "Estimate").

Plans come from the real planner over a real :class:`DataCache` under
``tmp_path``. The prompt is driven by an injected ``read_line`` and an explicit
``interactive`` flag (or a non-TTY stdin), so no test waits for input. No test
makes a network request.

**Validates: Requirements 3.1, 3.2**
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator, Mapping
from datetime import date, time
from pathlib import Path

import pytest

from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.data.estimate import (
    ALL_STRIKES_ASSUMED,
    BYTES_PER_GB,
    DEFAULT_COMPRESSION_RATIO,
    EstimateConfig,
    PullEstimate,
    PullSizeLimitError,
    announce_pull,
    calibrate_compression,
    check_size_limit,
    confirm_size,
    estimate_pull,
    format_gb,
    is_interactive,
    print_estimate,
)
from fse.data.planner import DEFAULT_SYMBOLS, PullPlan, PullSpec, plan_pull
from fse.engine.types import Metric, Snapshot
from fse.logio import LogWriter, Redactor
from fse.timekit import SessionCalendar, ny_instant

TODAY = date(2026, 3, 6)
STARTED = ny_instant(TODAY, time(17, 0))  # after the day's Pull_Window
CALENDAR = SessionCalendar(date(2024, 1, 1), date(2026, 12, 31))
FIRST_HISTORY = date(2023, 3, 28)
LISTED: Mapping[str, date] = {s: FIRST_HISTORY for s in (*DEFAULT_SYMBOLS, "AAA", "BBB")}
VIEW = HeatmapView()
RECENT = date(2026, 3, 5)  # range path
OLD = date(2025, 3, 6)  # 365 days old: historical path
WINDOWS = 28  # 09:00-16:00 in 15-minute Cache_Windows
RAW_FRAME = 92 * 8  # default strikes x float64


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as c:
        yield c


@pytest.fixture
def out() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def writer(out: io.StringIO) -> LogWriter:
    return LogWriter(Redactor(), stdout=out, stderr=io.StringIO())


def plan(spec: PullSpec, cache: DataCache, listed: Mapping[str, date] = LISTED) -> PullPlan:
    return plan_pull(spec, listed=listed, started_ns=STARTED, calendar=CALENDAR, cache=cache)


def key(symbol: str, metric: Metric = "gamma", *, view: HeatmapView = VIEW) -> CacheWindowKey:
    return CacheWindowKey.for_view(symbol, metric, view, RECENT, ny_instant(RECENT, time(9, 0)))


def snaps(k: CacheWindowKey, n: int) -> list[Snapshot]:
    return [
        Snapshot(
            symbol=k.symbol,
            metric=k.metric,
            view_id=k.view_id,
            as_of_ns=k.start_ns + i * 1_000_000_000,
            as_of_raw=f"raw-{i}",
            spot=5800.25 + i,
            previous_close=None,
            strikes=tuple(5700.0 + 5 * j for j in range(40)),
            values=tuple(1.0e9 + i * 7.5 + j for j in range(40)),
            node_types=None,
            expirations=("2026-03-05",),
            resolution="1s",
            source_endpoint="range",
            extra_json="{}",
        )
        for i in range(n)
    ]


def never_read() -> str:
    raise AssertionError("the prompt must not read input")


def answers(*lines: str) -> Callable[[], str]:
    it = iter(lines)
    return lambda: next(it)


# ---------------------------------------------------------------- counts and credits (Req 3.1)


def test_range_session_matches_the_design_budget(cache: DataCache) -> None:
    p = plan(PullSpec(RECENT, RECENT), cache)
    est = estimate_pull(p)
    assert est.request_count == p.request_count == WINDOWS * 2
    assert (est.total.range_requests, est.total.historical_requests) == (56, 0)
    assert est.credits == 56 * 25 == 1400
    assert est.total.windows == WINDOWS * 2 * len(DEFAULT_SYMBOLS)
    # 1 s frames over 7 hours: about 185 MB per session before compression.
    assert est.total.frames * RAW_FRAME == 185_472_000
    assert est.total.disk_bytes == pytest.approx(185_472_000 * DEFAULT_COMPRESSION_RATIO)
    assert est.compression_source == "configured"

    spx = est.line("SPX", "gamma")
    assert (spx.requests, spx.credits, spx.windows, spx.frames) == (28, 700, 28, 28 * 900)
    assert spx.disk_bytes == pytest.approx(28 * 900 * RAW_FRAME * 0.35)
    assert [(line.symbol, line.metric) for line in est.lines] == [
        (s, m) for s in DEFAULT_SYMBOLS for m in ("gamma", "vanna")
    ]
    assert sum(line.disk_bytes for line in est.lines) == pytest.approx(est.total.disk_bytes)


def test_historical_session_and_configured_credits(cache: DataCache) -> None:
    p = plan(PullSpec(OLD, OLD), cache)
    est = estimate_pull(p)
    assert (est.request_count, est.credits) == (840, 840 * 5)
    spx = est.line("SPX", "vanna")
    assert (spx.range_requests, spx.historical_requests, spx.frames) == (0, 420, 420)

    cheap = EstimateConfig(range_credits=10, historical_credits=2)
    assert estimate_pull(p, cheap).credits == 840 * 2
    both = plan(PullSpec(OLD, date(2025, 3, 7)), cache)
    assert estimate_pull(both, cheap).credits == 56 * 10 + 840 * 2


def test_shared_requests_count_in_each_symbol_row(cache: DataCache) -> None:
    seven = (*DEFAULT_SYMBOLS, "AAA", "BBB")
    p = plan(PullSpec(RECENT, RECENT, symbols=seven), cache)
    est = estimate_pull(p)
    assert est.request_count == p.request_count == WINDOWS * 2 * 2  # groups of 5 and 2
    assert est.line("SPX", "gamma").requests == est.line("BBB", "gamma").requests == 28
    assert sum(line.requests for line in est.lines) == 7 * 2 * 28
    assert sum(line.windows for line in est.lines) == est.total.windows


def test_served_and_skipped_windows_add_nothing(cache: DataCache) -> None:
    cache.write_window(key("SPX"), snaps(key("SPX"), 2), "range")  # complete
    cache.write_window(key("SPY"), [], "range")  # no_data
    listed = {**LISTED, "NDXP": TODAY}  # NDXP history starts after the session
    est = estimate_pull(plan(PullSpec(RECENT, RECENT), cache, listed))
    assert (est.served_windows, est.skipped_sessions) == (2, 1)
    spx = est.line("SPX", "gamma")
    assert (spx.requests, spx.windows, spx.frames) == (27, 27, 27 * 900)
    ndxp = est.line("NDXP", "vanna")
    assert (ndxp.requests, ndxp.credits, ndxp.windows, ndxp.disk_bytes) == (0, 0, 0, 0.0)
    assert est.total.windows == WINDOWS * 2 * 4 - 2
    assert est.request_count == 56  # the 09:00 gamma request still carries QQQ and NDX


def test_storage_interval_caps_frames_per_window(cache: DataCache) -> None:
    recent = estimate_pull(
        plan(PullSpec(RECENT, RECENT), cache), EstimateConfig(storage_interval_s=5)
    )
    assert recent.line("SPX", "gamma").frames == 28 * (900 // 5 + 1)
    old = estimate_pull(plan(PullSpec(OLD, OLD), cache), EstimateConfig(storage_interval_s=300))
    assert old.line("SPX", "gamma").frames == 28 * 4  # 15 samples, 4 boundaries per window
    with pytest.raises(ValueError, match="divide"):
        EstimateConfig(storage_interval_s=7)


def test_label_samples_add_historical_requests_and_frames(cache: DataCache) -> None:
    est = estimate_pull(plan(PullSpec(RECENT, RECENT, label_sample_minutes=30), cache))
    spx = est.line("SPX", "gamma")
    assert (spx.range_requests, spx.historical_requests) == (28, 14)
    assert spx.credits == 28 * 25 + 14 * 5
    assert spx.frames == 28 * 900 + 14


def test_strikes_per_frame_follow_the_view(cache: DataCache) -> None:
    wide = PullSpec(RECENT, RECENT, view=HeatmapView(max_strikes="all"))
    assert estimate_pull(plan(wide, cache)).strikes_per_frame == ALL_STRIKES_ASSUMED
    narrow = PullSpec(RECENT, RECENT, view=HeatmapView(max_strikes=50))
    assert estimate_pull(plan(narrow, cache)).strikes_per_frame == 50
    assert (
        estimate_pull(plan(narrow, cache), EstimateConfig(strikes_per_frame=7)).strikes_per_frame
        == 7
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"range_credits": -1},
        {"historical_credits": 1.5},
        {"range_credits": True},
        {"compression_ratio": 0},
        {"compression_ratio": float("nan")},
        {"strikes_per_frame": 0},
    ],
)
def test_invalid_config_is_rejected(fields: dict[str, object]) -> None:
    (name,) = fields
    with pytest.raises(ValueError, match=name):
        EstimateConfig(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------- recalibration


def test_compression_is_recalibrated_from_stored_bytes(cache: DataCache) -> None:
    assert calibrate_compression(cache, VIEW.view_id(), strikes_per_frame=92) is None

    cache.write_window(key("SPX"), snaps(key("SPX"), 3), "range")
    cache.write_window(key("SPY", "vanna"), snaps(key("SPY", "vanna"), 2), "range")
    cache.write_window(key("QQQ"), [], "range")  # no_data: no rows
    cache.mark_incomplete(key("NDX"))
    other = HeatmapView(max_strikes=50)
    cache.write_window(key("SPX", view=other), snaps(key("SPX", view=other), 4), "range")

    written: list[tuple[str, Metric]] = [("SPX", "gamma"), ("SPY", "vanna")]
    stored = sum(cache.window_path(key(s, m)).stat().st_size for s, m in written)
    cal = calibrate_compression(cache, VIEW.view_id(), strikes_per_frame=92)
    assert cal is not None
    assert (cal.windows, cal.rows, cal.stored_bytes) == (2, 5, stored)
    assert cal.ratio == stored / (5 * RAW_FRAME)

    p = plan(PullSpec(RECENT, RECENT), cache)
    est = estimate_pull(p, cache=cache)
    assert est.compression_source == "calibrated"
    assert est.compression_ratio == cal.ratio
    assert est.total.disk_bytes == pytest.approx(est.total.frames * RAW_FRAME * cal.ratio)
    assert estimate_pull(p).compression_ratio == DEFAULT_COMPRESSION_RATIO


# ---------------------------------------------------------------- printing


def test_estimate_prints_total_and_each_symbol_and_metric(
    cache: DataCache, writer: LogWriter, out: io.StringIO
) -> None:
    print_estimate(estimate_pull(plan(PullSpec(RECENT, RECENT), cache)), writer)
    lines = out.getvalue().splitlines()
    rows: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("  "):  # table rows are indented
            cells = line.split()
            n = 1 if cells[0] in {"scope", "total"} else 2
            rows[" ".join(cells[:n])] = cells[n:]
    assert rows["total"] == ["56", "56", "0", "1,400", "280", "0.065"]
    assert rows["SPX gamma"] == ["28", "28", "0", "700", "28", "0.006"]
    assert rows.keys() == {"scope", "total"} | {
        f"{s} {m}" for s in DEFAULT_SYMBOLS for m in ("gamma", "vanna")
    }
    assert any("range 25, historical 5" in line for line in lines)
    assert any("92 strikes" in line and "0.350 (configured)" in line for line in lines)


def test_format_gb_keeps_small_sizes_visible() -> None:
    assert (format_gb(0.0), format_gb(0.0000039), format_gb(1234.5678)) == (
        "0.000",
        "0.000004",
        "1,234.568",
    )


# ---------------------------------------------------------------- size confirmation (Req 3.2)


@pytest.fixture
def estimate(cache: DataCache) -> PullEstimate:
    return estimate_pull(plan(PullSpec(RECENT, RECENT), cache))  # about 0.065 GB


def test_within_the_limit_needs_no_prompt(
    estimate: PullEstimate, writer: LogWriter, out: io.StringIO
) -> None:
    decision = confirm_size(estimate, writer=writer, interactive=True, read_line=never_read)
    assert decision == "within_limit"
    assert out.getvalue() == ""
    assert not estimate.exceeds(estimate.disk_gb * 1.01)


def test_non_interactive_run_over_the_limit_aborts_with_exit_2(
    estimate: PullEstimate, writer: LogWriter
) -> None:
    with pytest.raises(PullSizeLimitError, match="No Replay_Request was sent") as info:
        confirm_size(
            estimate, writer=writer, limit_gb=0.01, interactive=False, read_line=never_read
        )
    assert info.value.exit_code == 2
    assert (info.value.limit_gb, info.value.estimated_gb) == (0.01, estimate.disk_gb)


def test_default_detection_treats_a_piped_stdin_as_non_interactive(
    estimate: PullEstimate, writer: LogWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    assert not is_interactive()
    with pytest.raises(PullSizeLimitError):
        confirm_size(estimate, writer=writer, limit_gb=0.01, read_line=never_read)


@pytest.mark.parametrize("answer", ["yes", " Y ", "YES"])
def test_interactive_confirmation_continues(
    estimate: PullEstimate, writer: LogWriter, out: io.StringIO, answer: str
) -> None:
    decision = confirm_size(
        estimate, writer=writer, limit_gb=0.01, interactive=True, read_line=answers(answer)
    )
    assert decision == "confirmed"
    printed = out.getvalue()
    assert "exceeds the size limit 0.01 GB" in printed
    assert "Type yes" in printed


@pytest.mark.parametrize("answer", ["", "no", "yess"])
def test_interactive_refusal_aborts(estimate: PullEstimate, writer: LogWriter, answer: str) -> None:
    with pytest.raises(PullSizeLimitError, match="did not confirm") as info:
        confirm_size(
            estimate, writer=writer, limit_gb=0.01, interactive=True, read_line=answers(answer)
        )
    assert info.value.exit_code == 2


def test_end_of_input_aborts(estimate: PullEstimate, writer: LogWriter) -> None:
    def eof() -> str:
        raise EOFError

    with pytest.raises(PullSizeLimitError, match="did not confirm"):
        confirm_size(estimate, writer=writer, limit_gb=0.01, interactive=True, read_line=eof)


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), True, "20"])
def test_invalid_size_limits_are_rejected(limit: object) -> None:
    with pytest.raises(ValueError, match="size limit"):
        check_size_limit(limit)


def test_announce_prints_the_estimate_before_stopping(
    cache: DataCache, writer: LogWriter, out: io.StringIO
) -> None:
    p = plan(PullSpec(RECENT, RECENT), cache)
    est = announce_pull(p, writer=writer, cache=cache, interactive=False, read_line=never_read)
    assert est.request_count == 56
    assert "Pull estimate" in out.getvalue()

    out.seek(0)
    out.truncate()
    with pytest.raises(PullSizeLimitError):
        announce_pull(p, writer=writer, limit_gb=0.01, interactive=False, read_line=never_read)
    assert "Pull estimate" in out.getvalue()
    assert est.total.disk_bytes > 0.01 * BYTES_PER_GB
