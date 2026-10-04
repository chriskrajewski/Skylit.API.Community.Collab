"""Bar, VIX and dark-pool stores of the Data_Cache (design §3-4, "Storage schemas").

- :class:`BarStore`: one Parquet file of :class:`~fse.engine.types.Bar` per
  instrument, bar interval and session, with the heatmap window's write
  protocol (``incomplete``, temp file + fsync + rename, then ``complete`` or
  ``no_data`` with rows, contract, source and sha256 in one transaction).
  Futures bars live under ``bars/`` (dataset ``bars``); VIX index bars under
  ``vix/bars/`` (dataset ``vix``). Each session's bars come from one contract
  and one source (Req 4.5), recorded in ``bars_coverage``.
- :class:`VixStore`: the VIX bars plus ``vix/daily.parquet``, one
  :class:`VixDailyRecord` per session (daily open and close with the
  Observation_Time of each, from bars or an Operator CSV import).
- :class:`DarkPoolStore`: prints per ticker and month in
  ``darkpool/{ticker}/{YYYY-MM}.parquet``. Each file lists the trade dates it
  holds fetch results for, so a fetched date with no prints is told apart from
  a date that was never fetched (Req 4.13, 11.11). A print's trade date is the
  New York date of its timestamp.

All stores share the cache root, so the path guard runs before the first write
of any of them (Req 1.11). Values are stored as float64 and int64 and read back
equal in every field.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast

import pyarrow as pa
import pyarrow.parquet as pq

from fse.data.cache_io import (
    CacheIntegrityError,
    CacheReadError,
    CacheRoot,
    CacheWriteError,
    check_component,
    expect_meta,
    meta_text,
    parquet_to_table,
    read_verified,
    require_columns,
    sha256_hex,
    table_to_parquet,
    write_atomic,
)
from fse.data.catalog import BarsCoverageRecord, BarsDataset, BarsKey, Catalog, WindowStatus
from fse.data.path_guard import PathGuardError
from fse.engine.types import BAR_SOURCES, Bar, BarSourceName, DarkPoolPrint
from fse.timekit import Instant, ny_datetime

__all__ = [
    "BARS_FORMAT",
    "DARKPOOL_FORMAT",
    "VIX_DAILY_FORMAT",
    "VIX_DAILY_SOURCES",
    "BarStore",
    "DarkPoolStore",
    "VixDailyRecord",
    "VixDailySource",
    "VixStore",
    "trade_date_of",
]

BARS_FORMAT: Final = "fse.bars/1"
VIX_DAILY_FORMAT: Final = "fse.vix-daily/1"
DARKPOOL_FORMAT: Final = "fse.darkpool/1"

type VixDailySource = Literal["atlas", "projectx", "import"]
VIX_DAILY_SOURCES: frozenset[str] = frozenset({"atlas", "projectx", "import"})


def _check_date(label: str, value: object) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{label} must be a date, got {value!r}")
    return value


def _check_optional_instant(label: str, value: object) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{label} must be an integer Instant or None, got {value!r}")


# ---------------------------------------------------------------- bars

_BARS_SCHEMA: Final = pa.schema(
    [
        pa.field("open_ns", pa.int64(), nullable=False),
        pa.field("close_ns", pa.int64(), nullable=False),
        pa.field("o", pa.float64(), nullable=False),
        pa.field("h", pa.float64(), nullable=False),
        pa.field("l", pa.float64(), nullable=False),
        pa.field("c", pa.float64(), nullable=False),
        pa.field("v", pa.float64(), nullable=False),
        pa.field("o_t", pa.int64()),
        pa.field("h_t", pa.int64()),
        pa.field("l_t", pa.int64()),
        pa.field("c_t", pa.int64()),
    ]
)
_BARS_REQUIRED: Final = tuple(f.name for f in _BARS_SCHEMA if not f.nullable)


class BarStore:
    """Bars per instrument, interval and session, tracked in ``bars_coverage``."""

    __slots__ = ("_catalog", "_dataset", "_root", "_subdir")

    def __init__(
        self,
        root: CacheRoot,
        catalog: Catalog,
        *,
        dataset: BarsDataset,
        subdir: tuple[str, ...],
    ) -> None:
        self._root = root
        self._catalog = catalog
        self._dataset: BarsDataset = dataset
        self._subdir = subdir

    @property
    def dataset(self) -> BarsDataset:
        return self._dataset

    def key(self, instrument: str, interval_s: int, session: date) -> BarsKey:
        return BarsKey(self._dataset, instrument, interval_s, session)

    def path(self, instrument: str, interval_s: int, session: date) -> Path:
        key = self.key(instrument, interval_s, session)
        return self._path(key)

    def _path(self, key: BarsKey) -> Path:
        return (
            self._root.path.joinpath(*self._subdir)
            / key.instrument
            / f"{key.interval_s}s"
            / f"{key.session.isoformat()}.parquet"
        )

    def record(self, instrument: str, interval_s: int, session: date) -> BarsCoverageRecord | None:
        return self._catalog.bars(self.key(instrument, interval_s, session))

    def status(self, instrument: str, interval_s: int, session: date) -> WindowStatus:
        """``absent``, ``incomplete``, ``complete`` or ``no_data``. Creates nothing."""
        return self._catalog.bars_status(self.key(instrument, interval_s, session))

    def mark_incomplete(
        self,
        instrument: str,
        interval_s: int,
        session: date,
        *,
        contract: str | None = None,
        source: BarSourceName | None = None,
    ) -> None:
        key = self.key(instrument, interval_s, session)
        try:
            self._catalog.mark_bars_incomplete(key, contract=contract, source=source)
        except PathGuardError:
            raise
        except Exception as exc:
            raise CacheWriteError(key.describe(), exc) from exc

    def write_session(
        self,
        instrument: str,
        interval_s: int,
        session: date,
        bars: Iterable[Bar],
        *,
        contract: str,
        source: BarSourceName,
    ) -> None:
        """Store one session's bars; an empty ``bars`` stores ``no_data``.

        Every bar must have this instrument, interval, contract and source, and
        open times must strictly increase. Otherwise ``ValueError`` is raised
        before anything is written.
        """
        key = self.key(instrument, interval_s, session)
        rows = tuple(bars)
        if not isinstance(contract, str) or not contract.strip():
            raise ValueError(f"contract must be a non-blank string, got {contract!r}")
        if source not in BAR_SOURCES:
            raise ValueError(f"source must be one of {sorted(BAR_SOURCES)}, got {source!r}")
        previous: Instant | None = None
        for i, bar in enumerate(rows):
            if not isinstance(bar, Bar):
                raise ValueError(f"bars[{i}] is not a Bar: {type(bar).__name__}")
            if (bar.instrument, bar.interval_s, bar.contract, bar.source) != (
                instrument,
                interval_s,
                contract,
                source,
            ):
                raise ValueError(
                    f"bars[{i}] is {bar.instrument} {bar.interval_s}s {bar.contract} from "
                    f"{bar.source}, not {instrument} {interval_s}s {contract} from {source}"
                )
            if previous is not None and bar.open_ns <= previous:
                raise ValueError(f"bars[{i}] does not open after bars[{i - 1}]")
            previous = bar.open_ns
        try:
            self._catalog.mark_bars_incomplete(key, contract=contract, source=source)
            data = self._encode(key, rows, contract, source)
            write_atomic(self._path(key), data)
            self._catalog.finish_bars(
                key,
                status="complete" if rows else "no_data",
                rows=len(rows),
                contract=contract,
                source=source,
                sha256=sha256_hex(data),
            )
        except PathGuardError:
            raise
        except Exception as exc:
            raise CacheWriteError(key.describe(), exc) from exc

    def read_session(self, instrument: str, interval_s: int, session: date) -> list[Bar]:
        """The stored bars in open-time order; ``[]`` for a ``no_data`` session."""
        key = self.key(instrument, interval_s, session)
        record = self._catalog.bars(key)
        if record is None or record.status == "incomplete":
            status = "absent" if record is None else record.status
            raise CacheReadError(
                f"{key.describe()} is {status}; only complete and no_data bars can be read"
            )
        path = self._path(key)
        table = parquet_to_table(read_verified(path, record.sha256), source=path)
        metadata = table.schema.metadata
        expect_meta(
            metadata,
            {
                "fse.format": BARS_FORMAT,
                "fse.dataset": key.dataset,
                "fse.instrument": key.instrument,
                "fse.interval_s": str(key.interval_s),
                "fse.session": key.session.isoformat(),
            },
            source=path,
        )
        require_columns(table, _BARS_SCHEMA.names, non_null=_BARS_REQUIRED, source=path)
        contract = meta_text(metadata, "fse.contract", source=path)
        source_text = meta_text(metadata, "fse.source", source=path)
        if source_text not in BAR_SOURCES:
            raise CacheIntegrityError(f"cached file {path} has bar source {source_text!r}")
        source = cast(BarSourceName, source_text)
        cols: dict[str, list[Any]] = {n: table.column(n).to_pylist() for n in _BARS_SCHEMA.names}
        try:
            out = [
                Bar(
                    instrument=key.instrument,
                    contract=contract,
                    interval_s=key.interval_s,
                    open_ns=cols["open_ns"][i],
                    close_ns=cols["close_ns"][i],
                    o=cols["o"][i],
                    h=cols["h"][i],
                    l=cols["l"][i],
                    c=cols["c"][i],
                    v=cols["v"][i],
                    o_t=cols["o_t"][i],
                    h_t=cols["h_t"][i],
                    l_t=cols["l_t"][i],
                    c_t=cols["c_t"][i],
                    source=source,
                )
                for i in range(table.num_rows)
            ]
        except (ValueError, TypeError) as exc:
            raise CacheIntegrityError(f"cached file {path} holds an invalid Bar: {exc}") from exc
        if len(out) != record.rows:
            raise CacheIntegrityError(
                f"cached file {path} holds {len(out)} bars; the catalog records {record.rows}"
            )
        return out

    @staticmethod
    def _encode(key: BarsKey, bars: tuple[Bar, ...], contract: str, source: str) -> bytes:
        table = pa.Table.from_pydict(
            {name: [getattr(bar, name) for bar in bars] for name in _BARS_SCHEMA.names},
            schema=_BARS_SCHEMA,
        )
        metadata = {
            b"fse.format": BARS_FORMAT.encode(),
            b"fse.dataset": key.dataset.encode(),
            b"fse.instrument": key.instrument.encode(),
            b"fse.interval_s": str(key.interval_s).encode(),
            b"fse.session": key.session.isoformat().encode(),
            b"fse.contract": contract.encode(),
            b"fse.source": source.encode(),
        }
        return table_to_parquet(table.replace_schema_metadata(metadata))


# ---------------------------------------------------------------- VIX


@dataclass(frozen=True, slots=True)
class VixDailyRecord:
    """A session's VIX daily open and close, each with its Observation_Time.

    The open is known at 09:31 of the session and the close at the session's
    close (design "Time model"); the producer computes both instants once.
    A missing value has ``None`` for both the value and its instant.
    """

    session: date
    open: float | None
    open_at_ns: Instant | None
    close: float | None
    close_at_ns: Instant | None
    source: VixDailySource

    def __post_init__(self) -> None:
        _check_date("VixDailyRecord.session", self.session)
        _check_optional_instant("VixDailyRecord.open_at_ns", self.open_at_ns)
        _check_optional_instant("VixDailyRecord.close_at_ns", self.close_at_ns)
        if (self.open is None) != (self.open_at_ns is None):
            raise ValueError("VixDailyRecord.open and open_at_ns must both be set or both None")
        if (self.close is None) != (self.close_at_ns is None):
            raise ValueError("VixDailyRecord.close and close_at_ns must both be set or both None")
        if self.source not in VIX_DAILY_SOURCES:
            raise ValueError(
                f"VixDailyRecord.source must be one of {sorted(VIX_DAILY_SOURCES)}, "
                f"got {self.source!r}"
            )


_VIX_DAILY_SCHEMA: Final = pa.schema(
    [
        pa.field("session", pa.date32(), nullable=False),
        pa.field("open", pa.float64()),
        pa.field("open_at_ns", pa.int64()),
        pa.field("close", pa.float64()),
        pa.field("close_at_ns", pa.int64()),
        pa.field("source", pa.string(), nullable=False),
    ]
)


class VixStore:
    """VIX index bars (``self.bars``) and daily values (``vix/daily.parquet``)."""

    __slots__ = ("_root", "bars")

    def __init__(self, root: CacheRoot, catalog: Catalog) -> None:
        self._root = root
        self.bars = BarStore(root, catalog, dataset="vix", subdir=("vix", "bars"))

    @property
    def daily_path(self) -> Path:
        return self._root.path / "vix" / "daily.parquet"

    def write_daily(self, records: Iterable[VixDailyRecord]) -> None:
        """Insert or replace the given sessions' records; other sessions are kept."""
        new = tuple(records)
        seen: set[date] = set()
        for i, record in enumerate(new):
            if not isinstance(record, VixDailyRecord):
                raise ValueError(f"records[{i}] is not a VixDailyRecord")
            if record.session in seen:
                raise ValueError(f"records holds session {record.session} more than once")
            seen.add(record.session)
        target = f"VIX daily values ({len(new)} sessions)"
        try:
            self._root.ensure_writable()
            merged = self.read_daily() if self.daily_path.exists() else {}
            merged.update({record.session: record for record in new})
            rows = [merged[d] for d in sorted(merged)]
            table = pa.Table.from_pydict(
                {name: [getattr(r, name) for r in rows] for name in _VIX_DAILY_SCHEMA.names},
                schema=_VIX_DAILY_SCHEMA,
            ).replace_schema_metadata({b"fse.format": VIX_DAILY_FORMAT.encode()})
            write_atomic(self.daily_path, table_to_parquet(table))
        except PathGuardError:
            raise
        except Exception as exc:
            raise CacheWriteError(target, exc) from exc

    def read_daily(self) -> dict[date, VixDailyRecord]:
        """Every stored session's record, in session order; ``{}`` before any write."""
        path = self.daily_path
        if not path.exists():
            return {}
        table = parquet_to_table(read_verified(path, None), source=path)
        expect_meta(table.schema.metadata, {"fse.format": VIX_DAILY_FORMAT}, source=path)
        require_columns(table, _VIX_DAILY_SCHEMA.names, non_null=("session", "source"), source=path)
        cols: dict[str, list[Any]] = {
            n: table.column(n).to_pylist() for n in _VIX_DAILY_SCHEMA.names
        }
        try:
            records = [
                VixDailyRecord(**{name: cols[name][i] for name in _VIX_DAILY_SCHEMA.names})
                for i in range(table.num_rows)
            ]
        except (ValueError, TypeError) as exc:
            raise CacheIntegrityError(f"cached file {path} holds an invalid record: {exc}") from exc
        return {record.session: record for record in sorted(records, key=lambda r: r.session)}


# ---------------------------------------------------------------- dark pool

_DARKPOOL_SCHEMA: Final = pa.schema(
    [
        pa.field("ts_ns", pa.int64(), nullable=False),
        pa.field("price", pa.float64(), nullable=False),
        pa.field("size", pa.int64(), nullable=False),
        pa.field("notional", pa.float64(), nullable=False),
        pa.field("venue", pa.string(), nullable=False),
    ]
)


def trade_date_of(ts_ns: Instant) -> date:
    """The New York calendar date of a print's timestamp."""
    return ny_datetime(ts_ns).date()


@dataclass(frozen=True, slots=True)
class _Month:
    trade_dates: frozenset[date]
    prints: tuple[DarkPoolPrint, ...]


class DarkPoolStore:
    """Dark-pool prints per ticker and month, with the trade dates fetched."""

    __slots__ = ("_root",)

    def __init__(self, root: CacheRoot) -> None:
        self._root = root

    def path(self, ticker: str, year: int, month: int) -> Path:
        check_component("ticker", ticker)
        return self._root.path / "darkpool" / ticker / f"{year:04d}-{month:02d}.parquet"

    def write_trade_dates(
        self, ticker: str, trade_dates: Iterable[date], prints: Iterable[DarkPoolPrint]
    ) -> None:
        """Store the fetch result for ``trade_dates``: exactly ``prints``.

        Earlier prints on those dates are replaced; other dates are kept. Every
        print must be for ``ticker`` and fall on one of ``trade_dates`` (New
        York date of its timestamp). A date with no prints is stored as fetched
        with none. Each month file is replaced atomically.
        """
        check_component("ticker", ticker)
        dates = frozenset(_check_date("trade_dates entry", d) for d in trade_dates)
        if not dates:
            raise ValueError("trade_dates must name at least one date")
        new = tuple(prints)
        by_month: dict[tuple[int, int], list[DarkPoolPrint]] = defaultdict(list)
        for i, p in enumerate(new):
            if not isinstance(p, DarkPoolPrint):
                raise ValueError(f"prints[{i}] is not a DarkPoolPrint")
            if p.ticker != ticker:
                raise ValueError(f"prints[{i}] is for {p.ticker!r}, not {ticker!r}")
            day = trade_date_of(p.ts_ns)
            if day not in dates:
                raise ValueError(f"prints[{i}] is on {day}, which is not in trade_dates")
            by_month[(day.year, day.month)].append(p)
        dates_by_month: dict[tuple[int, int], set[date]] = defaultdict(set)
        for d in dates:
            dates_by_month[(d.year, d.month)].add(d)
        for year, month in sorted(dates_by_month):
            month_dates = dates_by_month[(year, month)]
            target = f"dark-pool prints {ticker} {year:04d}-{month:02d}"
            try:
                self._root.ensure_writable()
                path = self.path(ticker, year, month)
                old = self._load(path, ticker, year, month) if path.exists() else None
                kept = () if old is None else old.prints
                merged = [p for p in kept if trade_date_of(p.ts_ns) not in month_dates]
                merged.extend(by_month.get((year, month), ()))
                merged.sort(key=lambda p: p.ts_ns)  # stable: input order on ties
                fetched = month_dates | (frozenset() if old is None else old.trade_dates)
                write_atomic(path, self._encode(ticker, year, month, fetched, merged))
            except PathGuardError:
                raise
            except Exception as exc:
                raise CacheWriteError(target, exc) from exc

    def fetched_dates(self, ticker: str) -> frozenset[date]:
        """Every trade date with a stored fetch result for ``ticker``."""
        directory = self._root.path / "darkpool" / check_component("ticker", ticker)
        if not directory.is_dir():
            return frozenset()
        out: set[date] = set()
        for path in sorted(directory.glob("*.parquet")):
            try:
                metadata = pq.read_schema(path).metadata
            except (pa.ArrowException, OSError) as exc:
                raise CacheIntegrityError(
                    f"cached file {path} is not a readable Parquet file"
                ) from exc
            out.update(self._dates(metadata, path))
        return frozenset(out)

    def read(self, ticker: str, first: date, last: date) -> list[DarkPoolPrint]:
        """Stored prints with trade dates in ``[first, last]``, in timestamp order."""
        check_component("ticker", ticker)
        _check_date("first", first)
        _check_date("last", last)
        out: list[DarkPoolPrint] = []
        year, month = first.year, first.month
        while (year, month) <= (last.year, last.month):
            path = self.path(ticker, year, month)
            if path.exists():
                for p in self._load(path, ticker, year, month).prints:
                    if first <= trade_date_of(p.ts_ns) <= last:
                        out.append(p)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return out

    # ---------------------------------------------------------------- files

    @staticmethod
    def _encode(
        ticker: str, year: int, month: int, dates: Iterable[date], prints: list[DarkPoolPrint]
    ) -> bytes:
        table = pa.Table.from_pydict(
            {name: [getattr(p, name) for p in prints] for name in _DARKPOOL_SCHEMA.names},
            schema=_DARKPOOL_SCHEMA,
        )
        metadata = {
            b"fse.format": DARKPOOL_FORMAT.encode(),
            b"fse.ticker": ticker.encode(),
            b"fse.month": f"{year:04d}-{month:02d}".encode(),
            b"fse.trade_dates": json.dumps(sorted(d.isoformat() for d in dates)).encode(),
        }
        return table_to_parquet(table.replace_schema_metadata(metadata))

    @staticmethod
    def _dates(metadata: Any, path: Path) -> frozenset[date]:
        try:
            raw = json.loads(meta_text(metadata, "fse.trade_dates", source=path))
            return frozenset(date.fromisoformat(d) for d in raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CacheIntegrityError(f"cached file {path} has invalid trade dates") from exc

    def _load(self, path: Path, ticker: str, year: int, month: int) -> _Month:
        table = parquet_to_table(read_verified(path, None), source=path)
        metadata = table.schema.metadata
        expect_meta(
            metadata,
            {
                "fse.format": DARKPOOL_FORMAT,
                "fse.ticker": ticker,
                "fse.month": f"{year:04d}-{month:02d}",
            },
            source=path,
        )
        require_columns(table, _DARKPOOL_SCHEMA.names, non_null=_DARKPOOL_SCHEMA.names, source=path)
        cols: dict[str, list[Any]] = {
            n: table.column(n).to_pylist() for n in _DARKPOOL_SCHEMA.names
        }
        prints = tuple(
            DarkPoolPrint(ticker=ticker, **{name: cols[name][i] for name in _DARKPOOL_SCHEMA.names})
            for i in range(table.num_rows)
        )
        return _Month(trade_dates=self._dates(metadata, path), prints=prints)
