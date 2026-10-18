"""Which 1m bar sessions a card backtest would find missing, before a task starts.

The rules plugin reads ``cache/bars/<symbol>/60s/<session>.parquet`` plus
``catalog.sqlite``. On ECS that is the data bucket, not the UI host's disk.
Listing keys is enough: the parquet bodies stay in S3.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from fse.calendars import load_calendars, project_calendar_dir
from fse.live.host.replay import globex_days

from fse_ui.aws.errors import error_code

_MISSING = frozenset({"404", "NoSuchKey", "NotFound"})
_LOCK = threading.Lock()
BAR_INTERVAL_S = 60


def session_days(project_dir: Path, first: date, last: date) -> list[date]:
    """Exchange sessions in ``[first, last]``, oldest first."""
    if last < first:
        return []
    with _LOCK:
        calendars = load_calendars(
            project_calendar_dir(project_dir),
            first - timedelta(days=7),
            last + timedelta(days=7),
        )
    return [day.day for day in globex_days(first, last, calendars)]


def sessions_before(project_dir: Path, first: date, count: int) -> list[date]:
    """The ``count`` exchange sessions strictly before ``first``, oldest first.

    The lookback matches ``build_history`` (``max(14, 3 * count)`` calendar days).
    Fewer sessions in that window means the cache cannot fill the request.
    """
    if count <= 0:
        return []
    start = first - timedelta(days=max(14, 3 * count))
    end = first - timedelta(days=1)
    days = session_days(project_dir, start, end)
    return days[-count:]


def local_sessions(cache: Path, symbol: str, interval_s: int = BAR_INTERVAL_S) -> set[date]:
    root = cache / "bars" / symbol / f"{interval_s}s"
    found: set[date] = set()
    if not root.is_dir():
        return found
    for path in root.glob("*.parquet"):
        try:
            found.add(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return found


def catalog_local(cache: Path) -> bool:
    return (cache / "catalog.sqlite").is_file()


def s3_sessions(s3: Any, bucket: str, symbol: str, interval_s: int = BAR_INTERVAL_S) -> set[date]:
    prefix = f"cache/bars/{symbol}/{interval_s}s/"
    found: set[date] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            key = obj.get("Key")
            if not isinstance(key, str) or not key.startswith(prefix):
                continue
            name = key.removeprefix(prefix)
            if "/" in name or not name.endswith(".parquet"):
                continue
            try:
                found.add(date.fromisoformat(name.removesuffix(".parquet")))
            except ValueError:
                continue
    return found


def catalog_s3(s3: Any, bucket: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key="cache/catalog.sqlite")
    except ClientError as exc:
        if error_code(exc) in _MISSING:
            return False
        raise
    return True


def missing_ranges(sessions: list[date], present: set[date]) -> list[tuple[date, date, int]]:
    """Consecutive missing sessions as ``(first, last, count)``."""
    ranges: list[tuple[date, date, int]] = []
    start: date | None = None
    end: date | None = None
    count = 0
    for day in sessions:
        if day in present:
            if start is not None and end is not None:
                ranges.append((start, end, count))
            start, end, count = None, None, 0
            continue
        if start is None:
            start = day
            count = 1
        else:
            count += 1
        end = day
    if start is not None and end is not None:
        ranges.append((start, end, count))
    return ranges


def gap_message(
    symbol: str,
    ranges: list[tuple[date, date, int]],
    *,
    catalog_missing: bool,
    warmup_history: int = 0,
    warmup_ranges: list[tuple[date, date, int]] | None = None,
) -> str:
    """One sentence a caller can show without reading a per-day log."""
    parts: list[str] = []
    if ranges:
        total = sum(count for _, _, count in ranges)
        shown = ", ".join(_span(first, last, count) for first, last, count in ranges)
        parts.append(f"{symbol} 1m bars are missing for {total} sessions: {shown}.")
    if warmup_ranges:
        total = sum(count for _, _, count in warmup_ranges)
        shown = ", ".join(_span(first, last, count) for first, last, count in warmup_ranges)
        parts.append(
            f"Warmup history={warmup_history} before the design window is also missing "
            f"for {total} sessions: {shown}."
        )
    if catalog_missing:
        parts.append("cache/catalog.sqlite is missing.")
    parts.append(
        "Atlas MES1/MNQ1 futures bars stopped after 2026-10-06 and are not in this "
        "bucket. From the machine that holds ~/.skylit-fse/cache, run "
        "scripts/aws-seed-bars.sh --yes (bars and catalog only)."
    )
    return " ".join(parts)


def warmup_detail(history: int, before: list[date], present: set[date]) -> dict[str, object]:
    """How much of the warmup window is actually in the cache."""
    gaps = missing_ranges(before, present)
    return {
        "history": history,
        "available": sum(day in present for day in before),
        "missing": [
            {"first": first.isoformat(), "last": last.isoformat(), "sessions": count}
            for first, last, count in gaps
        ],
    }


def clamp_warning(symbol: str, first: date, requested: int, available: int) -> str:
    """Job-log line when warmup is shortened to the bars that exist before ``first``."""
    return (
        f"warning: warmup history clamped from {requested} to {available} "
        f"({symbol} 1m bars before {first.isoformat()} cover {available} of "
        f"{requested} sessions). Indicators may be cold at the start of the range."
    )


def gap_detail(
    symbol: str,
    ranges: list[tuple[date, date, int]],
    *,
    catalog_missing: bool,
    warmup_history: int = 0,
    warmup_ranges: list[tuple[date, date, int]] | None = None,
    warmup: dict[str, object] | None = None,
) -> dict[str, object]:
    detail: dict[str, object] = {
        "code": "bars_missing",
        "message": gap_message(
            symbol,
            ranges,
            catalog_missing=catalog_missing,
            warmup_history=warmup_history,
            warmup_ranges=warmup_ranges,
        ),
        "symbol": symbol,
        "interval_s": BAR_INTERVAL_S,
        "catalog_missing": catalog_missing,
        "missing": [
            {"first": first.isoformat(), "last": last.isoformat(), "sessions": count}
            for first, last, count in ranges
        ],
    }
    if warmup is not None:
        detail["warmup"] = warmup
    return detail


def _span(first: date, last: date, count: int) -> str:
    if first == last:
        return f"{first.isoformat()} (1 session)"
    return f"{first.isoformat()}..{last.isoformat()} ({count} sessions)"
