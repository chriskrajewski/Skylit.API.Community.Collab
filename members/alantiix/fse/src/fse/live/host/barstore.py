"""``LiveBarStore``: the data plane's closed 1-minute bars as parquet (design 4.5).

Layout: ``<root>/<INSTR>/60s/<day>.parquet``, one file per instrument per
Globex day (``day`` = the Globex day holding the bar's open, so a Monday file
starts at Sunday 18:00 NY). Columns, all int64: ``open_ns, close_ns, o_t,
h_t, l_t, c_t, received_ns``; schema metadata ``fse.contract`` names the
day's contract (``MNQZ6``). This is a supplement layout a plugin's own
backtest tooling can read, so it can be pointed at ``<root>`` with an
``--extra-bars <root>`` option.

A day's contract is fixed (the roll calendar's contract for that Globex day):
a bar of another contract for the same file is dropped and reported through
``log``. Bars are kept once per ``open_ns`` (the first one wins), oldest
first. Every flushed change rewrites the whole file (at most 1,380 rows) through a
temp file in the same directory, fsync and ``os.replace``, so a reader never
sees a partial file. ``add(..., flush=False)`` keeps the new rows in memory
until :meth:`flush`, :meth:`forget`, or a later flushing add; :meth:`view`
reads that memory and returns the same bars a file read would. An existing
file is read back before the first new bar of its day is added (restart
continuity): a day's file is never truncated on a restart, every bar stored
that day stays in it, and its ``fse.contract`` metadata is kept.
:meth:`LiveBarStore.write_day` (replace a whole file) is backfill only: the
data plane calls it for past days that have no file yet.

:meth:`LiveBarStore.view` is the read-only :class:`BarStoreView` handed to
plugins. Bars read back from a file carry the prices of their ticks
(``ticks * 0.25``) and ``Bar.v = 0.0``: there is no volume column, so volume
is not stored.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from fse.engine.types import Bar
from fse.live.host.dayclock import globex_day_at
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant

__all__ = [
    "BAR_STORE_COLUMNS",
    "CONTRACT_METADATA_KEY",
    "INTERVAL_DIR",
    "LiveBarStore",
    "LiveBarStoreView",
    "StoredBar",
    "bar_store_path",
]

INTERVAL_S: Final = 60
INTERVAL_DIR: Final = f"{INTERVAL_S}s"
CONTRACT_METADATA_KEY: Final = "fse.contract"
BAR_STORE_COLUMNS: Final = ("open_ns", "close_ns", "o_t", "h_t", "l_t", "c_t", "received_ns")
TICK_SIZE: Final = 0.25
"""MNQ/MES/NQ/ES tick size, used only to give read-back bars their prices."""

_SCHEMA_FIELDS: Final = [pa.field(name, pa.int64(), nullable=False) for name in BAR_STORE_COLUMNS]

type StoreLog = Callable[[Mapping[str, JsonValue]], None]


def _no_log(_entry: Mapping[str, JsonValue]) -> None:
    pass


def bar_store_path(root: Path, instrument: str, day: date) -> Path:
    """``<root>/<instrument>/60s/<day>.parquet``."""
    return Path(root) / instrument / INTERVAL_DIR / f"{day.isoformat()}.parquet"


@dataclass(frozen=True, slots=True)
class StoredBar:
    """One stored row: the bar and when the data plane received it."""

    bar: Bar
    received_ns: Instant


def _bar_from_row(instrument: str, contract: str, row: Sequence[int]) -> Bar:
    open_ns, close_ns, o_t, h_t, l_t, c_t = row[:6]
    return Bar(
        instrument=instrument,
        contract=contract,
        interval_s=INTERVAL_S,
        open_ns=open_ns,
        close_ns=close_ns,
        o=o_t * TICK_SIZE,
        h=h_t * TICK_SIZE,
        l=l_t * TICK_SIZE,
        c=c_t * TICK_SIZE,
        v=0.0,
        o_t=o_t,
        h_t=h_t,
        l_t=l_t,
        c_t=c_t,
        source="projectx",
    )


def read_day(root: Path, instrument: str, day: date) -> tuple[str, tuple[StoredBar, ...]] | None:
    """The contract and rows of one stored day; ``None`` when there is no file.

    An unreadable file raises ``OSError`` (``ValueError`` from pyarrow is re-raised as one).
    """
    path = bar_store_path(root, instrument, day)
    if not path.is_file():
        return None
    try:
        table = pq.read_table(path)
        meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
        contract = meta[CONTRACT_METADATA_KEY]
        columns = [table.column(name).to_pylist() for name in BAR_STORE_COLUMNS]
    except (KeyError, ValueError, pa.ArrowException) as exc:
        raise OSError(f"bar store file {path} is unreadable: {type(exc).__name__}") from None
    rows: list[StoredBar] = []
    for values in zip(*columns, strict=True):
        ints = [int(v) for v in values]
        rows.append(StoredBar(_bar_from_row(instrument, contract, ints), ints[6]))
    return contract, tuple(rows)


class _Day:
    __slots__ = ("contract", "rows")

    def __init__(self, contract: str, rows: Iterable[StoredBar] = ()) -> None:
        self.contract = contract
        self.rows: dict[Instant, StoredBar] = {r.bar.open_ns: r for r in rows}


class LiveBarStore:
    """Writes the data plane's closed bars (see the module notes). Dispatcher thread only."""

    __slots__ = ("_days", "_dirty", "_log", "_root")

    def __init__(self, root: Path, *, log: StoreLog = _no_log) -> None:
        self._root = Path(root)
        self._log = log
        self._days: dict[tuple[str, date], _Day] = {}
        self._dirty: set[tuple[str, date]] = set()

    @property
    def root(self) -> Path:
        return self._root

    def view(self) -> LiveBarStoreView:
        return LiveBarStoreView(self._root, self)

    def has_day(self, instrument: str, day: date) -> bool:
        """Whether a file (or bars in memory) exists for ``instrument`` on ``day``."""
        return (instrument, day) in self._days or bar_store_path(
            self._root, instrument, day
        ).is_file()

    def add(self, bars: Iterable[Bar], received_ns: Instant, *, flush: bool = True) -> int:
        """Store new closed bars. The number of rows added.

        ``flush=True`` rewrites each changed day's file. ``flush=False`` keeps
        the rows in memory until :meth:`flush` (a replay writes once per day).
        """
        changed: set[tuple[str, date]] = set()
        added = 0
        for bar in bars:
            if bar.interval_s != INTERVAL_S or bar.o_t is None:
                continue
            gday = globex_day_at(bar.open_ns)
            if gday is None:
                self._log(
                    {
                        "kind": "bar_store_skipped",
                        "level": "warning",
                        "instrument": bar.instrument,
                        "open_ns": bar.open_ns,
                        "reason": "no_globex_day",
                    }
                )
                continue
            key = (bar.instrument, gday.day)
            day = self._load(key, bar.contract)
            if bar.contract != day.contract:
                self._log(
                    {
                        "kind": "bar_store_skipped",
                        "level": "warning",
                        "instrument": bar.instrument,
                        "open_ns": bar.open_ns,
                        "reason": "contract_mismatch",
                        "contract": bar.contract,
                        "day_contract": day.contract,
                    }
                )
                continue
            if bar.open_ns in day.rows:
                continue
            day.rows[bar.open_ns] = StoredBar(bar, received_ns)
            changed.add(key)
            added += 1
        if flush:
            for key in sorted(changed):
                self._write(key)
            self._dirty.difference_update(changed)
        else:
            self._dirty.update(changed)
        return added

    def flush(self) -> None:
        """Rewrite every day added with ``flush=False`` and not yet written."""
        for key in sorted(self._dirty):
            if key in self._days:
                self._write(key)
        self._dirty.clear()

    def write_day(
        self, instrument: str, day: date, contract: str, rows: Iterable[StoredBar]
    ) -> None:
        """Replace ``instrument``'s file for ``day`` with ``rows`` (backfill)."""
        entry = _Day(contract, (r for r in rows if r.bar.contract == contract))
        self._days[(instrument, day)] = entry
        self._write((instrument, day))

    def forget(self, before: date) -> None:
        """Drop in-memory days before ``before`` after their files are written."""
        for key in [k for k in self._days if k[1] < before]:
            if key in self._dirty:
                self._write(key)
                self._dirty.discard(key)
            del self._days[key]

    def _load(self, key: tuple[str, date], contract: str) -> _Day:
        day = self._days.get(key)
        if day is None:
            found = read_day(self._root, key[0], key[1])
            day = _Day(contract) if found is None else _Day(found[0], found[1])
            self._days[key] = day
        return day

    def _write(self, key: tuple[str, date]) -> None:
        instrument, d = key
        day = self._days[key]
        rows = [day.rows[k] for k in sorted(day.rows)]
        arrays = [
            pa.array([r.bar.open_ns for r in rows], pa.int64()),
            pa.array([r.bar.close_ns for r in rows], pa.int64()),
            pa.array([r.bar.o_t for r in rows], pa.int64()),
            pa.array([r.bar.h_t for r in rows], pa.int64()),
            pa.array([r.bar.l_t for r in rows], pa.int64()),
            pa.array([r.bar.c_t for r in rows], pa.int64()),
            pa.array([r.received_ns for r in rows], pa.int64()),
        ]
        schema = pa.schema(_SCHEMA_FIELDS, metadata={CONTRACT_METADATA_KEY: day.contract})
        table = pa.Table.from_arrays(arrays, schema=schema)
        path = bar_store_path(self._root, instrument, d)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                pq.write_table(table, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class LiveBarStoreView:
    """The read-only :class:`~fse.live.plugins.api.BarStoreView` over a bar store root."""

    __slots__ = ("_root", "_store")

    def __init__(self, root: Path, store: LiveBarStore | None = None) -> None:
        self._root = Path(root)
        self._store = store

    @property
    def root(self) -> Path:
        return self._root

    def _found(self, instrument: str, day: date) -> tuple[str, tuple[StoredBar, ...]] | None:
        store = self._store
        if store is not None:
            held = store._days.get((instrument, day))
            if held is not None:
                rows: list[StoredBar] = []
                for open_ns in sorted(held.rows):
                    row = held.rows[open_ns]
                    bar = row.bar
                    if bar.o_t is None or bar.h_t is None or bar.l_t is None or bar.c_t is None:
                        continue
                    rebuilt = _bar_from_row(
                        instrument,
                        held.contract,
                        (
                            bar.open_ns,
                            bar.close_ns,
                            bar.o_t,
                            bar.h_t,
                            bar.l_t,
                            bar.c_t,
                            row.received_ns,
                        ),
                    )
                    rows.append(StoredBar(rebuilt, row.received_ns))
                return held.contract, tuple(rows)
        return read_day(self._root, instrument, day)

    def bars(self, instrument: str, day: date) -> tuple[Bar, ...]:
        """The stored bars of ``instrument`` for Globex day ``day``, oldest first."""
        found = self._found(instrument, day)
        return () if found is None else tuple(r.bar for r in found[1])

    def rows(self, instrument: str, day: date) -> tuple[StoredBar, ...]:
        """The stored rows (bar and receipt time) of one day."""
        found = self._found(instrument, day)
        return () if found is None else found[1]
