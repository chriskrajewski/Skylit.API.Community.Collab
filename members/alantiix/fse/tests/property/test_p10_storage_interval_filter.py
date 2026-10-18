"""Property 10: Storage-interval filter.

*For any* sequence of Snapshot ``asOf`` values in a Cache_Window and any storage
interval from 1 to 300 s that divides 900 s, the stored set for that
Cache_Window equals the set of its Snapshots that are the latest at or before
at least one boundary of that Cache_Window (Cache_Window start + k x interval,
from the Cache_Window start to the Cache_Window end, both inclusive).

The reference model :func:`_expected` applies that definition literally. Among
Snapshots with the same ``asOf``, the last one given is "the latest" (task
4.1). Every Snapshot carries a unique ``as_of_raw`` label, so the checks
compare identities, not just ``asOf`` values.

Two checks:

1. :func:`filter_storage_interval` over one Cache_Window (1 to 15 minutes, so
   a shorter last window is covered) keeps exactly the reference set, in
   ``asOf`` order.
2. The stored set: every Cache_Window of a short Pull_Window is written through
   :meth:`DataCache.write_window` with a storage interval, as a pull would write
   it (each window gets the Snapshots whose ``asOf`` falls inside it), then read
   back. Each window's stored set equals the reference set for that window.

Intervals are drawn from the divisors of 900 from 1 to 300, computed here
independently of the cache. The ``asOf`` generator concentrates on the places
a filter gets wrong: storage boundaries and Cache_Window seams (exactly on, one
nanosecond either side, or within one interval), runs of 1 s range frames, and
repeated ``asOf`` values.

**Validates: Requirements 3.7**
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from fse.data.cache import (
    CACHE_WINDOW_MINUTES,
    CACHE_WINDOW_NS,
    STORAGE_INTERVAL_MAX_S,
    STORAGE_INTERVAL_MIN_S,
    CacheWindowKey,
    DataCache,
    HeatmapView,
    cache_window_end,
    cache_window_starts,
    filter_storage_interval,
)
from fse.engine.types import Snapshot
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, SessionCalendar, SessionTimes

SESSION = date(2026, 3, 5)  # a Thursday, Eastern Standard Time
EARLIEST_START = time(9, 0)
LATEST_END_MINUTES = 7 * 60  # 16:00, the default Pull_Window end
MAX_START_OFFSET_MINUTES = 60
VIEW = HeatmapView()
SYMBOL = "SPX"
S = NS_PER_SECOND
INTERVALS_S = tuple(
    d for d in range(STORAGE_INTERVAL_MIN_S, STORAGE_INTERVAL_MAX_S + 1) if 900 % d == 0
)


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Case:
    """A Pull_Window, a storage interval and ``asOf`` offsets in the given order."""

    start_offset_min: int  # Pull_Window start, minutes after 09:00
    minutes: int  # Pull_Window length
    interval_s: int
    offsets_ns: tuple[int, ...]  # asOf - Pull_Window start, each in [0, length)

    def calendar(self) -> SessionCalendar:
        start = datetime.combine(SESSION, EARLIEST_START) + timedelta(minutes=self.start_offset_min)
        end = start + timedelta(minutes=self.minutes)
        times = SessionTimes(pull_start=start.time(), pull_end=end.time())
        return SessionCalendar(SESSION, SESSION, times=times)

    def describe(self) -> str:
        offsets = ", ".join(f"{o // S}.{o % S:09d}" for o in self.offsets_ns)
        return (
            f"Pull_Window {self.minutes} min from 09:00 + {self.start_offset_min} min, "
            f"interval {self.interval_s} s, asOf offsets (s) [{offsets}]"
        )


def _jitter(step_ns: int) -> st.SearchStrategy[int]:
    return st.sampled_from((0, -1, 1)) | st.integers(-step_ns, step_ns)


@st.composite
def _offsets(draw: st.DrawFn, span_ns: int, step_ns: int) -> list[int]:
    """``asOf`` offsets in ``[0, span)``, clustered where a filter can go wrong."""
    values: list[int] = []
    for _ in range(draw(st.integers(0, 10))):
        kind = draw(st.integers(0, 4))
        if kind == 0:  # anywhere in the Pull_Window
            values.append(draw(st.integers(0, span_ns - 1)))
        elif kind == 1:  # at or near a storage boundary
            k = draw(st.integers(0, span_ns // step_ns))
            values.append(k * step_ns + draw(_jitter(step_ns)))
        elif kind == 2:  # at or near a Cache_Window seam or the Pull_Window end
            j = draw(st.integers(1, -(-span_ns // CACHE_WINDOW_NS)))
            values.append(min(j * CACHE_WINDOW_NS, span_ns) + draw(_jitter(step_ns)))
        elif kind == 3:  # a run of 1 s range frames
            first = draw(st.integers(0, span_ns - 1))
            values.extend(first + i * S for i in range(draw(st.integers(1, 20))))
        elif values:  # the same asOf as an earlier Snapshot
            values.append(draw(st.sampled_from(values)))
    return draw(st.permutations([v for v in values if 0 <= v < span_ns]))


@st.composite
def _cases(draw: st.DrawFn, *, max_minutes: int) -> Case:
    minutes = draw(st.integers(1, max_minutes))
    start_offset = draw(st.integers(0, min(MAX_START_OFFSET_MINUTES, LATEST_END_MINUTES - minutes)))
    interval_s = draw(st.sampled_from(INTERVALS_S))
    offsets = draw(_offsets(minutes * NS_PER_MINUTE, interval_s * S))
    return Case(start_offset, minutes, interval_s, tuple(offsets))


def _snapshots(start_ns: int, case: Case) -> list[Snapshot]:
    return [
        Snapshot(
            symbol=SYMBOL,
            metric="gamma",
            view_id=VIEW.view_id(),
            as_of_ns=start_ns + offset,
            as_of_raw=f"#{i}",
            spot=5800.25,
            previous_close=5790.5,
            strikes=(5800.0,),
            values=(1.0,),
            node_types=None,
            expirations=("2026-03-05",),
            resolution="1s",
            source_endpoint="range",
            extra_json="{}",
        )
        for i, offset in enumerate(case.offsets_ns)
    ]


# ---------------------------------------------------------------- reference model


def _first_boundary_at_or_after(t: int, origin: int, step: int) -> int:
    return origin + (t - origin + step - 1) // step * step  # t >= origin


def _expected(snaps: Sequence[Snapshot], window_start: int, interval_s: int, end: int) -> list[str]:
    """Labels of a Cache_Window's Snapshots that are the latest at or before some boundary.

    ``snaps`` are the window's Snapshots, each with ``window_start <= asOf <
    end``. Boundaries are ``window_start + k * interval`` from ``window_start``
    to ``end`` inclusive. Only the first boundary at or after each ``asOf``
    needs checking: if a boundary ``b`` selects Snapshot ``s``, the first
    boundary at or after ``s.asOf`` is no later than ``b`` and has no Snapshot
    after ``s`` before it, so it selects ``s`` too.
    """
    step = interval_s * S
    candidates = {_first_boundary_at_or_after(s.as_of_ns, window_start, step) for s in snaps}
    kept: set[int] = set()
    for boundary in candidates:
        if boundary > end:
            continue
        at_or_before = [i for i, s in enumerate(snaps) if s.as_of_ns <= boundary]
        latest = max(snaps[i].as_of_ns for i in at_or_before)
        kept.add(max(i for i in at_or_before if snaps[i].as_of_ns == latest))  # last given
    return [snaps[i].as_of_raw for i in sorted(kept, key=lambda i: snaps[i].as_of_ns)]


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 10: Storage-interval filter
@given(case=_cases(max_minutes=CACHE_WINDOW_MINUTES))
def test_filter_keeps_the_latest_snapshot_at_or_before_each_boundary(case: Case) -> None:
    # A Pull_Window of at most 15 minutes is a single Cache_Window.
    start, end = case.calendar().pull_window(SESSION)
    snaps = _snapshots(start, case)

    kept = filter_storage_interval(snaps, origin_ns=start, interval_s=case.interval_s, last_ns=end)

    assert [s.as_of_raw for s in kept] == _expected(snaps, start, case.interval_s, end), (
        case.describe()
    )
    assert all(any(k is s for s in snaps) for k in kept)  # the given objects, unchanged


# Feature: skylit-futures-strategy-engine, Property 10: Storage-interval filter
@given(case=_cases(max_minutes=4 * CACHE_WINDOW_MINUTES))
def test_stored_set_of_each_cache_window_is_the_reference_set(case: Case) -> None:
    calendar = case.calendar()
    start, end = calendar.pull_window(SESSION)
    snaps = _snapshots(start, case)

    with (
        tempfile.TemporaryDirectory(prefix="fse-p10-") as tmp,
        DataCache(
            Path(tmp) / "cache", calendar=calendar, storage_interval_s=case.interval_s
        ) as cache,
    ):
        for window_start in cache_window_starts(start, end):
            window_end = cache_window_end(window_start, end)
            key = CacheWindowKey.for_view(SYMBOL, "gamma", VIEW, SESSION, window_start)
            in_window = [s for s in snaps if window_start <= s.as_of_ns < window_end]
            cache.write_window(key, in_window, "range", view=VIEW)
            stored = [s.as_of_raw for s in cache.read_window(key)]

            expected = _expected(in_window, window_start, case.interval_s, window_end)
            assert stored == expected, (
                f"{case.describe()}; Cache_Window from +{(window_start - start) // S} s"
            )
