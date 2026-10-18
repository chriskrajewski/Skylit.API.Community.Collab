"""``iter_recording``: data-plane recordings streamed with bounded memory.

A day's recording reaches gigabytes (full matrix boards every 15 s); the host
restart (``DataPlane.recorded_bars``) and the replay must not hold it whole.
``read_recording`` is built on the stream and keeps its earlier behavior, which
:func:`_reference_read` (the previous whole-file reader) pins.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import tracemalloc
import zlib
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from fse.engine.types import Snapshot
from fse.live.host.dataplane import dataplane_recording_path
from fse.live.host.dayclock import globex_day
from fse.live.recorder import (
    RECORDING_KINDS,
    RecordedInput,
    Recorder,
    Recording,
    RecordingError,
    iter_recording,
    read_recording,
)
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import JsonValue
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.dataplane import make_bar

DAY = date(2026, 10, 14)
MB = 1 << 20


def writer() -> LogWriter:
    return LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())


def big_snapshot(t: int, payload: str) -> Snapshot:
    return Snapshot(
        symbol="AAA",
        metric="gamma",
        view_id="v1",
        as_of_ns=t,
        as_of_raw=str(t),
        spot=100.0,
        previous_close=None,
        strikes=(99.0, 100.0, 101.0),
        values=(1.0, 2.0, 3.0),
        node_types=None,
        expirations=("2026-10-14",),
        resolution="1s",
        source_endpoint="stream",
        extra_json=json.dumps({"grid": payload}),
    )


def _reference_read(path: Path) -> Recording:
    """The whole-file reader ``read_recording`` replaced (behavior reference)."""
    data = path.read_bytes()
    out = bytearray()
    truncated = False
    while data:
        member = zlib.decompressobj(wbits=31)
        try:
            out += member.decompress(data)
        except zlib.error as exc:
            raise RecordingError(f"{path}: not a gzip recording ({exc})") from None
        if not member.eof:
            truncated = True
            break
        data = member.unused_data
    lines = bytes(out).split(b"\n")
    if lines.pop():
        truncated = True
    entries: list[RecordedInput] = []
    for number, line in enumerate(lines, start=1):
        try:
            obj = json.loads(line)
        except ValueError:
            raise RecordingError(f"{path} line {number}: not a JSON object") from None
        if not isinstance(obj, dict) or set(obj) != {"kind", "received_ns", "payload"}:
            raise RecordingError(f"{path} line {number}: not a recording entry")
        kind, received = obj["kind"], obj["received_ns"]
        if kind not in RECORDING_KINDS:
            raise RecordingError(f"{path} line {number}: unknown kind {kind!r}")
        if isinstance(received, bool) or not isinstance(received, int):
            raise RecordingError(f"{path} line {number}: received_ns is not an integer")
        entries.append(RecordedInput(kind, received, cast("JsonValue", obj["payload"])))
    return Recording(path, tuple(entries), truncated)


LINES = 9  # the lines every_kind writes (two vix lines)


def every_kind(path: Path, start: int = 0) -> None:
    """One session's recording with a line of every kind."""
    t = 1_000_000 + start
    with Recorder(writer(), path) as r:
        r.snapshot(big_snapshot(t, "g"), t + 1)
        r.bar(make_bar("MNQ", t, 80_000, 80_004, 79_996, 80_001), t + 2)
        r.dark_pool("AAA", [], t + 3)
        r.vix_daily(None, None, t + 4)
        r.vix_bar(make_bar("VIX", t, 1_500), t + 5)
        r.broker_event({"event": "fill", "qty": 1}, t + 6)
        r.operator_event({"event": "feed_status", "feed": "bars", "up": True}, t + 7)
        r.guard_event(t, [], t + 8)
        r.decision_time(t + 9)


def outcome(fn: Any, path: Path) -> Any:
    try:
        rec = fn(path)
    except RecordingError as exc:
        return ("error", str(exc))
    return (rec.entries, rec.truncated)


# ---------------------------------------------------------------- memory


@pytest.mark.parametrize("compressible", [False, True], ids=["random", "compressible"])
def test_bars_stream_out_of_a_large_recording_with_bounded_memory(
    tmp_path: Path, compressible: bool
) -> None:
    path = tmp_path / "big.jsonl.gz"
    t0 = globex_day(DAY).open_ns
    raw = 0
    with Recorder(writer(), path) as r:
        for i in range(300):
            # ~200 KB per board line; the compressible one shrinks ~1000x in gzip
            payload = "x" * 200_000 if compressible else os.urandom(100_000).hex()
            raw += len(payload)
            r.snapshot(big_snapshot(t0 + i, payload), t0 + i * NS_PER_SECOND)
            if i % 3 == 0:
                bar = make_bar("MNQ", t0 + (i // 3) * NS_PER_MINUTE, 80_000 + i)
                r.bar(bar, t0 + i * NS_PER_SECOND + 1)
    assert raw > 55 * MB
    tracemalloc.start()
    try:
        stream = iter_recording(path, kinds=("bar",))
        bars = [e for e in stream]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(bars) == 100
    assert {e.kind for e in bars} == {"bar"}
    assert not stream.truncated
    print(f"peak {peak / MB:.1f} MB for {raw / MB:.0f} MB of lines")
    assert peak < 8 * MB, f"peak {peak / MB:.1f} MB for {raw / MB:.0f} MB of lines"


def test_recorded_bars_stay_bounded_on_a_large_recording(tmp_path: Path) -> None:
    from tests.fakes.clock import FakeClock
    from tests.fakes.dataplane import ny
    from tests.fakes.skylit_plane import make_dataplane

    clock = FakeClock(ny(DAY, 9, 0))
    rig = make_dataplane(tmp_path, clock, lambda live: cast("Any", None))
    path = dataplane_recording_path(tmp_path / "rec", DAY)
    path.parent.mkdir(parents=True)
    day = globex_day(DAY)
    with Recorder(rig.writer, path) as r:
        for i in range(120):
            t = day.open_ns + i * NS_PER_MINUTE
            r.snapshot(big_snapshot(t, os.urandom(100_000).hex()), t)
            r.bar(make_bar("MNQ", t, 80_000 + i), t + NS_PER_MINUTE + NS_PER_SECOND)
    tracemalloc.start()
    try:
        got = rig.plane.recorded_bars(day)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [b.open_ns for b, _ in got] == [day.open_ns + i * NS_PER_MINUTE for i in range(120)]
    print(f"recorded_bars peak {peak / MB:.1f} MB")
    assert peak < 8 * MB, f"peak {peak / MB:.1f} MB"


# ---------------------------------------------------------------- same behavior


def test_read_recording_equals_the_stream_and_the_old_reader(tmp_path: Path) -> None:
    path = tmp_path / "all.jsonl.gz"
    every_kind(path)
    rec = read_recording(path)
    stream = iter_recording(path)
    assert rec.entries == tuple(stream)
    assert rec.truncated is stream.truncated is False
    assert outcome(read_recording, path) == outcome(_reference_read, path)
    assert {e.kind for e in rec.entries} == set(RECORDING_KINDS)
    assert len(rec.entries) == LINES


def test_kinds_filter_keeps_only_the_wanted_kinds_in_file_order(tmp_path: Path) -> None:
    path = tmp_path / "all.jsonl.gz"
    every_kind(path)
    got = list(iter_recording(path, kinds=("bar", "operator_event")))
    want = [e for e in read_recording(path).entries if e.kind in ("bar", "operator_event")]
    assert got == want


def test_a_truncated_tail_keeps_the_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "all.jsonl.gz"
    every_kind(path)
    data = path.read_bytes()
    for cut in (len(data) - 4, len(data) // 2, 15):  # trailer, mid-member, mid-header
        p = tmp_path / f"cut{cut}.jsonl.gz"
        p.write_bytes(data[:cut])
        stream = iter_recording(p)
        entries = tuple(stream)
        assert stream.truncated is True
        assert outcome(read_recording, p) == (entries, True) == outcome(_reference_read, p)
    # a whole member whose last line has no newline: truncated, that line dropped
    p = tmp_path / "partial.jsonl.gz"
    lines = gzip.decompress(data).split(b"\n")
    p.write_bytes(gzip.compress(b"\n".join(lines[:3]) + b"\n" + lines[3][:10]))
    rec = read_recording(p)
    assert rec.truncated
    assert len(rec.entries) == 3
    assert outcome(read_recording, p) == outcome(_reference_read, p)


def test_a_bad_line_names_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl.gz"
    good = gzip.decompress(_every_kind_bytes(tmp_path)).split(b"\n")
    bad = [*good[:4], b'{"kind":"nope","payload":{},"received_ns":1}', *good[4:]]
    path.write_bytes(gzip.compress(b"\n".join(bad)))
    with pytest.raises(RecordingError, match=r"line 5: unknown kind 'nope'"):
        read_recording(path)
    # a filtered stream skips the unknown kind's line unparsed, but counts it
    assert [e.kind for e in iter_recording(path, kinds=("bar",))] == ["bar"]
    path.write_bytes(gzip.compress(b"\n".join([*good[:6], b"not json", *good[6:]])))
    with pytest.raises(RecordingError, match=r"line 7: not a JSON object"):
        list(iter_recording(path, kinds=("bar",)))
    assert outcome(read_recording, path) == outcome(_reference_read, path)
    path.write_bytes(b"not gzip at all")
    with pytest.raises(RecordingError, match="not a gzip recording"):
        read_recording(path)
    assert outcome(read_recording, path) == outcome(_reference_read, path)
    with pytest.raises(RecordingError, match="cannot read the recording"):
        read_recording(tmp_path / "absent.jsonl.gz")


def _every_kind_bytes(tmp_path: Path) -> bytes:
    p = tmp_path / "src.jsonl.gz"
    every_kind(p)
    return p.read_bytes()


def test_two_sessions_appended_are_read_as_one_recording(tmp_path: Path) -> None:
    path = tmp_path / "two.jsonl.gz"
    every_kind(path)
    every_kind(path, start=100)  # a restart appends a second gzip member
    assert path.read_bytes().count(b"\x1f\x8b\x08") >= 2
    rec = read_recording(path)
    assert len(rec.entries) == 2 * LINES
    assert not rec.truncated
    assert rec.entries == tuple(iter_recording(path))
    assert outcome(read_recording, path) == outcome(_reference_read, path)
    assert [e.received_ns for e in iter_recording(path, kinds=("bar",))] == [
        1_000_002,
        1_000_102,
    ]
