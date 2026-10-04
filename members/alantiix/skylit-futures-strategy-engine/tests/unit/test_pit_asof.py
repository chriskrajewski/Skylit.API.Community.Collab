"""Unit tests for the as-of indexes and the D8 availability rule (design §5, D8).

Records here are plain ``(label, observed_at)`` tuples, so the index logic is
tested on its own. A brute-force reference checks the late-receipt path.

**Validates: Requirements 5.1, 5.2, 5.3**
"""

from __future__ import annotations

from typing import Any

import pytest

from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.pit.asof import AsOfIndex, Received, available_at, observation_time
from fse.timekit import NS_PER_SECOND

type Rec = tuple[str, int]


def obs(record: Rec) -> int:
    return record[1]


# ---------------------------------------------------------------- availability rule


def test_available_at_is_observation_time_or_later_receipt() -> None:
    assert available_at(100) == 100
    assert available_at(100, 90) == 100  # received early (clock skew): still the asOf
    assert available_at(100, 130) == 130


@pytest.mark.parametrize(("observed", "received"), [(1.0, None), (True, None), (1, 2.5)])
def test_available_at_rejects_non_integer_instants(observed: Any, received: Any) -> None:
    with pytest.raises(ValueError, match="integer Instant"):
        available_at(observed, received)


def test_observation_time_of_each_record_kind() -> None:
    snap = Snapshot(
        symbol="SPX",
        metric="gamma",
        view_id="0123456789abcdef",
        as_of_ns=111,
        as_of_raw="raw",
        spot=5800.0,
        previous_close=None,
        strikes=(),
        values=(),
        node_types=None,
        expirations=(),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )
    bar = Bar(
        "MES", "MESH6", 60, 0, 60 * NS_PER_SECOND, 1.0, 1.0, 1.0, 1.0, 0.0, 4, 4, 4, 4, "atlas"
    )
    dp = DarkPoolPrint("SPY", 333, 580.0, 10, 5800.0, "TRF")
    assert observation_time(snap) == 111
    assert observation_time(bar) == 60 * NS_PER_SECOND  # a bar is usable at its close
    assert observation_time(dp) == 333


# ---------------------------------------------------------------- historical index


def test_historical_index_is_truncated_at_t() -> None:
    idx = AsOfIndex.of([("c", 30), ("a", 10), ("b", 20)], obs)
    assert len(idx) == 3
    assert idx.count_upto(9) == 0
    assert idx.upto(20) == (("a", 10), ("b", 20))
    assert idx.latest(5) is None
    assert idx.latest(20) == ("b", 20)  # inclusive at t
    assert idx.latest(29) == ("b", 20)
    assert idx.latest_at_or_before(30, 15) == ("a", 10)
    assert idx.latest_at_or_before(15, 25) == ("a", 10)  # never past t, even if at > t
    assert idx.latest_at_or_before(30, 9) is None
    assert idx.between(30, 10, 20) == [("a", 10), ("b", 20)]  # both bounds inclusive
    assert idx.between(15, 10, 30) == [("a", 10)]
    assert idx.between(30, 21, 20) == []


def test_equal_observation_times_go_to_the_later_input() -> None:
    idx = AsOfIndex.of([("first", 10), ("second", 10)], obs)
    assert idx.latest(10) == ("second", 10)
    assert idx.latest_at_or_before(10, 10) == ("second", 10)
    assert idx.between(10, 10, 10) == [("first", 10), ("second", 10)]


def test_empty_index() -> None:
    idx: AsOfIndex[Rec] = AsOfIndex()
    assert len(idx) == 0
    assert idx.latest(10) is None
    assert idx.latest_at_or_before(10, 5) is None
    assert idx.between(10, 0, 10) == []


# ---------------------------------------------------------------- received records


def test_received_record_waits_for_receipt_and_latest_is_by_observation() -> None:
    records: list[Rec | Received[Rec]] = [
        ("on_time", 15),
        Received(("late", 18), received_ns=25),
        ("newest", 20),
    ]
    idx = AsOfIndex.of(records, obs)
    assert idx.latest(17) == ("on_time", 15)
    assert idx.latest(20) == ("newest", 20)
    # The late record arrives last, but "latest" is the largest Observation_Time.
    assert idx.latest(30) == ("newest", 20)
    assert idx.latest_at_or_before(30, 19) == ("late", 18)
    assert idx.latest_at_or_before(24, 19) == ("on_time", 15)
    assert idx.between(30, 16, 20) == [("late", 18), ("newest", 20)]
    assert idx.between(24, 16, 20) == [("newest", 20)]


ENTRIES: list[tuple[Rec, int, int]] = [
    (("a", 10), 10, 10),
    (("b", 14), 14, 31),  # received late
    (("c", 12), 12, 12),
    (("d", 20), 20, 22),
    (("e", 14), 14, 14),  # same observation time as "b", available earlier
    (("f", 25), 25, 25),
    (("g", 5), 5, 40),  # very late
    (("h", 30), 30, 33),
]


def _ref_latest(t: int, at: int) -> Rec | None:
    cands = [(o, a, i, r) for i, (r, o, a) in enumerate(ENTRIES) if a <= t and o <= at]
    return max(cands, key=lambda c: (c[0], c[1], c[2]))[3] if cands else None


def _ref_between(t: int, lo: int, hi: int) -> list[Rec]:
    cands = [(o, a, i, r) for i, (r, o, a) in enumerate(ENTRIES) if a <= t and lo <= o <= hi]
    return [c[3] for c in sorted(cands, key=lambda c: (c[0], c[1], c[2]))]


def test_late_receipts_match_brute_force_reference() -> None:
    idx = AsOfIndex(ENTRIES)
    for t in range(0, 45):
        assert idx.latest(t) == _ref_latest(t, t), t
        for at in range(0, 45):
            assert idx.latest_at_or_before(t, at) == _ref_latest(t, at), (t, at)
        for lo in range(0, 45, 3):
            for hi in range(lo, 45, 4):
                assert idx.between(t, lo, hi) == _ref_between(t, lo, hi), (t, lo, hi)


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ((("x", 10), 10, 9), "before it is observed"),
        ((("x", 10), 10.0, 10), "integer Instant"),
        ((("x", 10), 10, True), "integer Instant"),
    ],
)
def test_invalid_entries_raise(entry: tuple[Rec, Any, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AsOfIndex([entry])


def test_received_requires_an_integer_receipt() -> None:
    with pytest.raises(ValueError, match="received_ns"):
        Received(("x", 1), received_ns=1.5)  # type: ignore[arg-type]
