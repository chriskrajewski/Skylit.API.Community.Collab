"""The live marks document (``<live-state>/marks.json``).

Written by the host for every instrument it gets data for, with no plugin
code, so the console can value open positions between bars (live P&L). It is
rewritten at most once per ``min_interval_ns`` (1 s) and only when a mark
changed. Layout (``MARKS_VERSION`` 1)::

    {"version": 1, "updated_ns": int,
     "marks": {instrument: {
        "quote": {"price_t", "price", "basis": "last"|"mid"|"bid"|"ask",
                  "t_ns", "received_ns", "contract_id"} | null,
        "bar": {"price_t", "price", "close_ns", "contract"} | null}}}

``quote`` is the latest realtime quote: its last trade price, else the mid
of bid and ask (rounded to a tick), else the one side it has. ``bar`` is the
close of the latest 1-minute bar. Prices are futures points (ticks / 4).
A write error is logged once to the host log (``marks_write_failed``) and
never stops anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from fse.engine.types import Bar
from fse.live.plugins.api import Quote
from fse.logio import LogWriter, canonical_json
from fse.logio.canonical_json import JsonValue
from fse.timekit import NS_PER_SECOND, Instant

__all__ = ["MARKS_FILE_NAME", "MARKS_VERSION", "MarksWriter"]

MARKS_FILE_NAME: Final = "marks.json"
MARKS_VERSION: Final = 1
TICKS_PER_POINT: Final = 4


def _quote_mark(q: Quote) -> dict[str, JsonValue] | None:
    price: int | None
    if q.last_t is not None:
        price, basis = q.last_t, "last"
    elif q.bid_t is not None and q.ask_t is not None:
        price, basis = round((q.bid_t + q.ask_t) / 2), "mid"
    elif q.bid_t is not None:
        price, basis = q.bid_t, "bid"
    elif q.ask_t is not None:
        price, basis = q.ask_t, "ask"
    else:
        return None
    return {
        "price_t": price,
        "price": price / TICKS_PER_POINT,
        "basis": basis,
        "t_ns": q.t_ns,
        "received_ns": q.received_ns,
        "contract_id": q.contract_id,
    }


class MarksWriter:
    """The latest quote and 1-minute close per instrument, saved to ``marks.json``."""

    __slots__ = (
        "_bars",
        "_body",
        "_failed",
        "_last_write",
        "_log",
        "_min_interval_ns",
        "_path",
        "_quotes",
        "_writer",
    )

    def __init__(
        self,
        writer: LogWriter,
        path: Path,
        *,
        min_interval_ns: int = NS_PER_SECOND,
        log: Callable[[Mapping[str, JsonValue]], None] | None = None,
    ) -> None:
        self._writer = writer
        self._path = path
        self._min_interval_ns = min_interval_ns
        self._log = log
        self._quotes: dict[str, dict[str, JsonValue]] = {}
        self._bars: dict[str, dict[str, JsonValue]] = {}
        self._last_write: Instant | None = None
        self._body: str | None = None
        self._failed = False

    @property
    def path(self) -> Path:
        return self._path

    def quote(self, q: Quote) -> None:
        """Keep ``q`` as the instrument's quote mark (a quote with no price is ignored)."""
        mark = _quote_mark(q)
        if mark is not None:
            self._quotes[q.instrument] = mark

    def bar(self, bar: Bar) -> None:
        """Keep the close of ``bar`` when it is the instrument's newest 1-minute bar."""
        if bar.c_t is None:
            return
        old = self._bars.get(bar.instrument)
        old_close = old.get("close_ns") if old is not None else None
        if isinstance(old_close, int) and old_close > bar.close_ns:
            return
        self._bars[bar.instrument] = {
            "price_t": bar.c_t,
            "price": bar.c_t / TICKS_PER_POINT,
            "close_ns": bar.close_ns,
            "contract": bar.contract,
        }

    def document(self, now: Instant) -> dict[str, JsonValue]:
        marks: dict[str, JsonValue] = {
            inst: {"quote": self._quotes.get(inst), "bar": self._bars.get(inst)}
            for inst in sorted({*self._quotes, *self._bars})
        }
        return {"version": MARKS_VERSION, "updated_ns": now, "marks": marks}

    def maybe_write(self, now: Instant) -> bool:
        """Write ``marks.json`` when ``min_interval_ns`` has passed and a mark changed."""
        if self._last_write is not None and now - self._last_write < self._min_interval_ns:
            return False
        if not self._quotes and not self._bars:
            return False
        doc = self.document(now)
        body = canonical_json.dumps(doc["marks"])
        if body == self._body:
            return False
        try:
            self._writer.write_text(self._path, canonical_json.dumps(doc) + "\n")
        except OSError as exc:
            if not self._failed and self._log is not None:
                self._log(
                    {
                        "kind": "marks_write_failed",
                        "level": "warning",
                        "detail": self._writer.redact(f"{type(exc).__name__}: {exc}"),
                    }
                )
            self._failed = True
            return False
        self._last_write = now
        self._body = body
        return True
