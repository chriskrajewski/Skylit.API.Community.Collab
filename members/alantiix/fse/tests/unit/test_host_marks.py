"""``marks.json``: the host's live marks (fse.live.host.marks).

At most one write per second, only when a mark changed; the quote is its last
price, else the mid, else the one side it has; the 1-minute close is kept
next to it. Every plugin gets marks, with no plugin code.
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

from fse.live.host.host import HOST_LOG_FILE_NAME, BarsEvent, QuoteEvent
from fse.live.host.marks import MARKS_FILE_NAME, MarksWriter
from fse.live.plugins import Quote
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import MinimalPlugin

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)


class CountingWriter(LogWriter):
    __slots__ = ("fail", "writes")

    def __init__(self) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        self.writes = 0
        self.fail = False

    def write_text(self, path: Any, text: str) -> Path:
        if Path(path).name == MARKS_FILE_NAME:
            if self.fail:
                raise OSError("disk full")
            self.writes += 1
        return super().write_text(path, text)


def quote(
    t: int, bid: int | None = 80_000, ask: int | None = 80_002, last: int | None = None
) -> Quote:
    return Quote("MNQ", "CON.F.US.MNQ.Z26", t, t + 5_000_000, bid, ask, last)


def quote_mark(marks: MarksWriter) -> dict[str, Any]:
    doc: Any = marks.document(T0)
    found: dict[str, Any] = doc["marks"]["MNQ"]["quote"]
    return found


def writer_at(tmp_path: Path) -> tuple[MarksWriter, CountingWriter, Path]:
    w = CountingWriter()
    path = tmp_path / MARKS_FILE_NAME
    return MarksWriter(w, path), w, path


def test_two_quotes_in_one_second_write_once_then_the_next_second_writes_again(
    tmp_path: Path,
) -> None:
    marks, w, path = writer_at(tmp_path)
    marks.quote(quote(T0))
    assert marks.maybe_write(T0)
    marks.quote(quote(T0 + 300_000_000, bid=80_004, ask=80_006))
    assert not marks.maybe_write(T0 + 300_000_000)
    assert w.writes == 1
    marks.quote(quote(T0 + NS_PER_SECOND, bid=80_004, ask=80_006))
    assert marks.maybe_write(T0 + NS_PER_SECOND)
    assert w.writes == 2
    doc = json.loads(path.read_text())
    assert doc["updated_ns"] == T0 + NS_PER_SECOND
    assert doc["marks"]["MNQ"]["quote"]["price_t"] == 80_005


def test_unchanged_marks_are_not_rewritten(tmp_path: Path) -> None:
    marks, w, _ = writer_at(tmp_path)
    q = quote(T0)
    marks.quote(q)
    assert marks.maybe_write(T0)
    marks.quote(q)
    assert not marks.maybe_write(T0 + 5 * NS_PER_SECOND)
    assert w.writes == 1


def test_nothing_to_write_before_the_first_mark(tmp_path: Path) -> None:
    marks, w, path = writer_at(tmp_path)
    assert not marks.maybe_write(T0)
    assert w.writes == 0
    assert not path.exists()


def test_the_quote_basis_is_last_then_mid_then_one_side(tmp_path: Path) -> None:
    marks, _, _ = writer_at(tmp_path)
    marks.quote(quote(T0, bid=80_000, ask=80_002, last=80_001))
    q = quote_mark(marks)
    assert (q["price_t"], q["basis"], q["price"]) == (80_001, "last", 20_000.25)
    assert q["received_ns"] == T0 + 5_000_000
    assert q["contract_id"] == "CON.F.US.MNQ.Z26"
    marks.quote(quote(T0, bid=80_000, ask=80_004))
    q = quote_mark(marks)
    assert (q["price_t"], q["basis"], q["price"]) == (80_002, "mid", 20_000.5)
    marks.quote(quote(T0, bid=80_000, ask=None))
    q = quote_mark(marks)
    assert (q["price_t"], q["basis"]) == (80_000, "bid")
    # a quote with no price keeps the previous mark
    marks.quote(quote(T0, bid=None, ask=None))
    q = quote_mark(marks)
    assert q["price_t"] == 80_000


def test_the_bar_mark_is_the_newest_close(tmp_path: Path) -> None:
    marks, _, _ = writer_at(tmp_path)
    marks.bar(make_bar("MES", T0 + NS_PER_MINUTE, 24_000, c_t=24_010))
    marks.bar(make_bar("MES", T0, 23_000))  # older: ignored
    doc = marks.document(T0)
    assert doc["marks"] == {
        "MES": {
            "quote": None,
            "bar": {
                "price_t": 24_010,
                "price": 6_002.5,
                "close_ns": T0 + 2 * NS_PER_MINUTE,
                "contract": "MESZ6",
            },
        }
    }


def test_a_write_error_is_logged_once_and_never_raises(tmp_path: Path) -> None:
    w = CountingWriter()
    w.fail = True
    logged: list[Any] = []
    marks = MarksWriter(w, tmp_path / MARKS_FILE_NAME, log=logged.append)
    marks.quote(quote(T0))
    assert not marks.maybe_write(T0)
    marks.quote(quote(T0, bid=79_000))
    assert not marks.maybe_write(T0 + 2 * NS_PER_SECOND)
    assert [e["kind"] for e in logged] == ["marks_write_failed"]


def test_the_host_writes_marks_for_quotes_and_bars(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    bar = make_bar("MNQ", T0, 80_000, 80_008, 79_996, 80_004)
    t1 = T0 + NS_PER_MINUTE + 2 * NS_PER_SECOND
    plane = FakeDataPlane(
        clock,
        [
            (t1, BarsEvent((bar,), t1)),
            (t1 + NS_PER_SECOND, QuoteEvent(quote(t1 + NS_PER_SECOND, last=80_012))),
        ],
    )
    # MinimalPlugin does not ask for quotes: it gets marks all the same
    host = make_host(tmp_path, {"p1": MinimalPlugin}, [host_entry("p1")], clock, plane)
    asyncio.run(clock.run(host.run(until_ns=t1 + 5 * NS_PER_SECOND)))
    doc = json.loads((tmp_path / "live-state" / MARKS_FILE_NAME).read_text())
    assert doc["version"] == 1
    mnq = doc["marks"]["MNQ"]
    assert mnq["bar"]["price_t"] == 80_004
    assert mnq["quote"]["price_t"] == 80_012
    assert mnq["quote"]["basis"] == "last"
    host_log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    assert not [e for e in host_log if e["kind"] == "marks_write_failed"]
