"""Unit tests for ``fse.logio.canonical_json``: one value, one byte sequence (design D7).

**Validates: Requirements 1.9**
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import PurePosixPath

import numpy as np
import pytest

from fse.logio.canonical_json import dumps, dumps_bytes, instant_fields, ny_iso, to_jsonable
from fse.timekit import NS_PER_SECOND, ny_instant


class Side(enum.StrEnum):
    LONG = "long"


class Grade(enum.IntEnum):
    A = 1


@dataclass(frozen=True)
class Point:
    y: Decimal
    x: int


def test_layout_is_sorted_and_compact() -> None:
    value = {"b": 1, "a": [1.0, 0.1, 1e-7, -0.0, 10**20], "c": None, "d": True}
    expected = '{"a":[1.0,0.1,1e-07,-0.0,100000000000000000000],"b":1,"c":null,"d":true}'
    assert dumps(value) == expected


def test_key_order_does_not_change_the_bytes() -> None:
    first = {"z": {"b": 2, "a": 1}, "a": 0}
    second = {"a": 0, "z": {"a": 1, "b": 2}}
    assert dumps_bytes(first) == dumps_bytes(second) == b'{"a":0,"z":{"a":1,"b":2}}'


def test_floats_round_trip_exactly() -> None:
    values = [0.1 + 0.2, 1 / 3, 5e-324, 1.7976931348623157e308]
    assert json.loads(dumps(values)) == values
    assert dumps(values) == "[" + ",".join(repr(v) for v in values) + "]"


def test_output_is_ascii() -> None:
    assert dumps({"name": "caf\u00e9 \u2603"}) == '{"name":"caf\\u00e9 \\u2603"}'


def test_python_types_are_normalized() -> None:
    value = {
        "decimal": Decimal("1.50"),
        "enum": [Side.LONG, Grade.A],
        "tuple": (1, 2),
        "set": frozenset({"b", "a"}),
        "point": Point(y=Decimal("-0.25"), x=3),
        "date": date(2026, 1, 14),
        "path": PurePosixPath("/tmp/run"),
        "numpy": [np.int64(5), np.float64(0.1)],
        Side.LONG: "enum key",
    }
    assert to_jsonable(value) == {
        "date": "2026-01-14",
        "decimal": "1.50",
        "enum": ["long", 1],
        "long": "enum key",
        "numpy": [5, 0.1],
        "path": "/tmp/run",
        "point": {"x": 3, "y": "-0.25"},
        "set": ["a", "b"],
        "tuple": [1, 2],
    }


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), -float("inf"), Decimal("NaN"), Decimal("Infinity")],
    ids=["nan", "inf", "-inf", "decimal-nan", "decimal-inf"],
)
def test_non_finite_numbers_are_refused(bad: object) -> None:
    with pytest.raises(ValueError, match=r"non-finite .* at \$\.m\[0\]"):
        dumps({"m": [bad]})


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"a": [object()]}, r"cannot encode object at \$\.a\[0\]"),
        ({1: "x"}, r"keys must be str, got int at \$"),
        ({"t": datetime(2026, 1, 14, tzinfo=UTC)}, r"datetime at \$\.t; use an Instant"),
        (b"bytes", r"cannot encode bytes at \$"),
    ],
    ids=["object", "int-key", "datetime", "bytes"],
)
def test_unsupported_values_are_refused_with_their_path(bad: object, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        dumps(bad)


class Kind(enum.Enum):
    X = "x"


def test_keys_that_collide_are_refused() -> None:
    with pytest.raises(ValueError, match="two keys encode as 'x'"):
        dumps({"x": 1, Kind.X: 2})


def test_ny_iso_uses_the_offset_in_effect() -> None:
    winter = ny_instant(date(2026, 1, 14), time(9, 30))
    summer = ny_instant(date(2026, 7, 14), time(9, 30))
    assert ny_iso(winter) == "2026-01-14T09:30:00-05:00"
    assert ny_iso(summer) == "2026-07-14T09:30:00-04:00"
    assert ny_iso(winter + 1) == "2026-01-14T09:30:00.000000001-05:00"
    assert ny_iso(winter + NS_PER_SECOND // 2) == "2026-01-14T09:30:00.500000000-05:00"


def test_instant_fields_pair_the_int_with_its_ny_string() -> None:
    t = ny_instant(date(2026, 1, 14), time(9, 31))
    assert instant_fields("decision_time", t) == {
        "decision_time": t,
        "decision_time_ny": "2026-01-14T09:31:00-05:00",
    }
    with pytest.raises(TypeError, match="Instant must be an int"):
        ny_iso(True)
