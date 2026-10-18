"""Property 9: Cache round-trip.

*For any* Snapshot (any finite float64 strike values and spot, with or without
``nodeType``, any ``asOf`` string, any extra fields), writing it to the
Data_Cache and reading it back yields a Snapshot equal in every field.

Each example writes one Cache_Window of generated Snapshots and reads it back
twice: from the same :class:`DataCache` and from a fresh one opened on the same
root, so the second read comes only from disk. Floats are compared by bit
pattern, so ``-0.0`` versus ``0.0`` or a flushed subnormal counts as a
difference. The design allows only finite floats here, so no NaN or infinity is
generated. Reads return Snapshots in ``asOf`` order, stable on ties, so the
expected list is the written list stably sorted by ``as_of_ns``.

The property covers Snapshots only; bars and dark-pool prints are not part of
it. Every cache root is a fresh ``tempfile`` directory, outside any git
repository, created inside the test body.

**Validates: Requirements 3.10**
"""

from __future__ import annotations

import dataclasses
import struct
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from hypothesis import example, given
from hypothesis import strategies as st

from fse.data.cache import CACHE_WINDOW_NS, CacheWindowKey, DataCache, HeatmapView
from fse.engine.types import METRICS, RESOLUTIONS, SOURCE_ENDPOINTS, Snapshot
from fse.logio import canonical_json
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

FIRST_SESSION = date(2023, 3, 28)
LAST_SESSION = date(2026, 12, 31)
# A New York day has at least 23 hours, so midnight + this many minutes stays
# on the same date, including spring-forward days.
SAFE_MINUTES_PER_DAY = 23 * 60

MIN_NORMAL = sys.float_info.min  # 2.2250738585072014e-308
MAX_SUBNORMAL = 2.225073858507201e-308
MIN_SUBNORMAL = 5e-324
MAX_FLOAT = sys.float_info.max

SPECIAL_FLOATS: tuple[float, ...] = (
    0.0,
    -0.0,
    MIN_SUBNORMAL,
    -MIN_SUBNORMAL,
    MAX_SUBNORMAL,
    -MAX_SUBNORMAL,
    MIN_NORMAL,
    -MIN_NORMAL,
    MAX_FLOAT,
    -MAX_FLOAT,
    1e308,
    -1e300,
    0.1,
    5800.25,
    float(2**53 + 2),  # integral, above the contiguous-integer range of float64
)

# ---------------------------------------------------------------- strategies

FINITE: st.SearchStrategy[float] = st.one_of(
    st.sampled_from(SPECIAL_FLOATS),
    st.floats(allow_nan=False, allow_infinity=False),
    st.floats(min_value=-MIN_NORMAL, max_value=MIN_NORMAL, allow_subnormal=True),
    st.floats(min_value=1e300, allow_infinity=False),
    st.floats(max_value=-1e300, allow_infinity=False),
)
TEXT = st.text(st.characters(codec="utf-8"), max_size=24)
ISO_DATE = st.dates(FIRST_SESSION, LAST_SESSION).map(date.isoformat)
SYMBOLS = st.one_of(
    st.sampled_from(("SPX", "SPY", "QQQ", "NDX", "NDXP")),
    st.from_regex(r"[A-Z][A-Z0-9_-]{0,9}", fullmatch=True),
)
VIEW_LIMIT = st.one_of(st.integers(1, 500), st.just("all"))
VIEWS = st.builds(
    HeatmapView,
    max_strikes=VIEW_LIMIT,
    max_expirations=VIEW_LIMIT,
    expirations=st.none() | st.lists(ISO_DATE, min_size=1, max_size=3).map(tuple),
    include_empty=st.booleans(),
)
NODE_TYPE = st.one_of(st.none(), st.sampled_from(("", "king", "gatekeeper")), TEXT)
JSON_SCALAR = st.one_of(st.none(), st.booleans(), st.integers(-(2**63), 2**64), FINITE, TEXT)
JSON_VALUE = st.recursive(
    JSON_SCALAR,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(TEXT, inner, max_size=3),
    max_leaves=8,
)
EXTRA_JSON = st.dictionaries(TEXT, JSON_VALUE, max_size=4).map(canonical_json.dumps)

type Axis = tuple[tuple[float, ...], tuple[str, ...]]

AXIS: st.SearchStrategy[Axis] = st.tuples(
    st.lists(FINITE, max_size=12).map(tuple),
    st.lists(st.one_of(ISO_DATE, TEXT), max_size=4).map(tuple),
)


def _flip_zero_signs(strikes: tuple[float, ...]) -> tuple[float, ...]:
    """``strikes`` with ``0.0`` and ``-0.0`` swapped: equal by ``==``, not by bits."""
    return tuple((-x if x == 0.0 else x) for x in strikes)


def _rfc3339(t: Instant, offset_min: int, sep: str, zulu: bool, strip: bool) -> str:
    """``t`` as an RFC 3339 string in one of several valid spellings."""
    seconds, ns = divmod(t, NS_PER_SECOND)
    zone = timezone(timedelta(minutes=offset_min))
    local = datetime.fromtimestamp(seconds, tz=UTC).astimezone(zone)
    frac = f"{ns:09d}"
    if strip:
        frac = frac.rstrip("0")
    if offset_min == 0 and zulu:
        tz = "Z"
    else:
        sign = "+" if offset_min >= 0 else "-"
        hours, minutes = divmod(abs(offset_min), 60)
        tz = f"{sign}{hours:02d}:{minutes:02d}"
    return local.strftime(f"%Y-%m-%d{sep}%H:%M:%S") + (f".{frac}" if frac else "") + tz


def _as_of_raw(t: Instant) -> st.SearchStrategy[str]:
    """A realistic RFC 3339 spelling of ``t``, or any text at all."""
    return st.one_of(
        st.builds(
            _rfc3339,
            st.just(t),
            st.sampled_from((0, 0, -240, -300, 330, -(23 * 60 + 59))),
            st.sampled_from(("T", "t", " ")),
            st.booleans(),
            st.booleans(),
        ),
        TEXT,
    )


@st.composite
def _snapshot(
    draw: st.DrawFn, key: CacheWindowKey, axes: Sequence[Axis], ties: Sequence[Instant]
) -> Snapshot:
    strikes, expirations = draw(st.sampled_from(axes))
    n = len(strikes)
    as_of_ns = draw(
        st.one_of(st.sampled_from(ties), st.integers(key.start_ns, key.start_ns + CACHE_WINDOW_NS))
    )
    node_types = draw(st.none() | st.lists(NODE_TYPE, min_size=n, max_size=n).map(tuple))
    return Snapshot(
        symbol=key.symbol,
        metric=key.metric,
        view_id=key.view_id,
        as_of_ns=as_of_ns,
        as_of_raw=draw(_as_of_raw(as_of_ns)),
        spot=draw(FINITE),
        previous_close=draw(st.none() | FINITE),
        strikes=strikes,
        values=tuple(draw(st.lists(FINITE, min_size=n, max_size=n))),
        node_types=node_types,
        expirations=expirations,
        resolution=draw(st.sampled_from(sorted(RESOLUTIONS))),
        source_endpoint=draw(st.sampled_from(sorted(SOURCE_ENDPOINTS))),
        extra_json=draw(EXTRA_JSON),
    )


@dataclass(frozen=True)
class WindowCase:
    key: CacheWindowKey
    view: HeatmapView | None  # passed to write_window, or None
    source_endpoint: str
    snapshots: tuple[Snapshot, ...]  # in write order


@st.composite
def _window_cases(draw: st.DrawFn) -> WindowCase:
    view = draw(VIEWS)
    session = draw(st.dates(FIRST_SESSION, LAST_SESSION))
    minute = draw(st.integers(0, SAFE_MINUTES_PER_DAY - 1))
    key = CacheWindowKey.for_view(
        draw(SYMBOLS),
        draw(st.sampled_from(sorted(METRICS))),
        view,
        session,
        ny_instant(session, time(0, 0)) + minute * NS_PER_MINUTE,
    )
    axes = draw(st.lists(AXIS, min_size=1, max_size=3))
    if draw(st.booleans()):  # a twin axis that differs only in the sign of zero
        axes.append((_flip_zero_signs(axes[0][0]), axes[0][1]))
    # A few shared asOf values, so ties are common and stability is exercised.
    ties = draw(
        st.lists(st.integers(key.start_ns, key.start_ns + CACHE_WINDOW_NS), min_size=1, max_size=3)
    )
    snapshots = draw(st.lists(_snapshot(key, axes, ties), max_size=10))
    return WindowCase(
        key=key,
        view=view if draw(st.booleans()) else None,
        source_endpoint=draw(st.sampled_from(sorted(SOURCE_ENDPOINTS))),
        snapshots=tuple(snapshots),
    )


# ---------------------------------------------------------------- explicit edge case


def _edge_case() -> WindowCase:
    """-0.0 versus 0.0 axes, subnormals, extreme magnitudes, null labels and ties."""
    view = HeatmapView()
    session = date(2026, 3, 9)  # first weekday after the 2026 spring-forward
    key = CacheWindowKey.for_view("SPX", "gamma", view, session, ny_instant(session, time(9, 15)))
    t = key.start_ns + NS_PER_SECOND
    axis_a = ((0.0, -0.0, MIN_SUBNORMAL, -MAX_FLOAT), ("2026-03-09", "2026-03-10"))
    axis_b = (_flip_zero_signs(axis_a[0]), axis_a[1])
    base: dict[str, Any] = {
        "symbol": key.symbol,
        "metric": key.metric,
        "view_id": key.view_id,
        "resolution": "1s",
        "source_endpoint": "range",
    }
    tie_a = Snapshot(
        **base,
        as_of_ns=t,
        as_of_raw="2026-03-09T13:15:01Z",
        spot=-0.0,
        previous_close=None,
        strikes=axis_a[0],
        values=(-0.0, MIN_SUBNORMAL, MAX_FLOAT, MAX_SUBNORMAL),
        node_types=(None, "", "king", "ünï\x00"),
        expirations=axis_a[1],
        extra_json=canonical_json.dumps({"k": -0.0, "n": 2**64, "s": "ü"}),
    )
    tie_b = Snapshot(
        **base,
        as_of_ns=t,
        as_of_raw="2026-03-09T09:15:01.000000000-04:00",
        spot=5800.25,
        previous_close=-0.0,
        strikes=axis_b[0],
        values=(0.0, -MIN_SUBNORMAL, 1e308, -MIN_NORMAL),
        node_types=None,
        expirations=axis_b[1],
        extra_json="{}",
    )
    earliest = Snapshot(
        **base,
        as_of_ns=key.start_ns,
        as_of_raw="not a timestamp \x00 ✓",
        spot=MIN_SUBNORMAL,
        previous_close=MAX_FLOAT,
        strikes=(),
        values=(),
        node_types=(),
        expirations=(),
        extra_json="{}",
    )
    return WindowCase(key, view, "range", (tie_a, tie_b, earliest))


def _empty_case() -> WindowCase:
    view = HeatmapView(max_strikes="all", include_empty=True)
    session = date(2025, 11, 2)  # the 2025 fall-back date
    key = CacheWindowKey.for_view("NDXP", "vanna", view, session, ny_instant(session, time(9, 0)))
    return WindowCase(key, None, "historical", ())


# ---------------------------------------------------------------- exact comparison


def _bits(x: float) -> bytes:
    return struct.pack("<d", x)


def _assert_exact(path: str, got: object, want: object) -> None:
    """``got`` equals ``want`` with the same types and float bit patterns."""
    assert type(got) is type(want), f"{path}: {type(got).__name__} is not {type(want).__name__}"
    if isinstance(want, float):
        assert isinstance(got, float)
        assert _bits(got) == _bits(want), f"{path}: read {got!r}, wrote {want!r}"
    elif isinstance(want, tuple):
        assert isinstance(got, tuple)
        assert len(got) == len(want), f"{path}: read {len(got)} items, wrote {len(want)}"
        for i, (g, w) in enumerate(zip(got, want, strict=True)):
            _assert_exact(f"{path}[{i}]", g, w)
    else:
        assert got == want, f"{path}: read {got!r}, wrote {want!r}"


def _assert_snapshots_exact(label: str, got: Sequence[Snapshot], want: Sequence[Snapshot]) -> None:
    assert len(got) == len(want), f"{label}: read {len(got)} Snapshots, wrote {len(want)}"
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert type(g) is Snapshot
        for field in dataclasses.fields(Snapshot):
            _assert_exact(
                f"{label}[{i}].{field.name}", getattr(g, field.name), getattr(w, field.name)
            )


# ---------------------------------------------------------------- the property


@example(case=_edge_case())
@example(case=_empty_case())
@given(case=_window_cases())
def test_cache_round_trip_is_exact_in_every_field(case: WindowCase) -> None:
    expected = sorted(case.snapshots, key=lambda s: s.as_of_ns)  # stable on ties
    with tempfile.TemporaryDirectory(prefix="fse-p09-") as tmp:
        root = Path(tmp) / "cache"
        with DataCache(root) as cache:
            cache.write_window(case.key, case.snapshots, case.source_endpoint, view=case.view)
            same_object = cache.read_window(case.key)
            status = cache.status(case.key)
        with DataCache(root) as reopened:
            from_disk = reopened.read_window(case.key)
    assert status == ("complete" if case.snapshots else "no_data")
    _assert_snapshots_exact("same cache", same_object, expected)
    _assert_snapshots_exact("reopened cache", from_disk, expected)
