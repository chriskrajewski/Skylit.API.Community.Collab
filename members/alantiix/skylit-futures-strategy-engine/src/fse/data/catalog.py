"""The Data_Cache catalog: ``catalog.sqlite`` (design §3, "Storage schemas").

Three tables:

- ``windows``: one row per Cache_Window key (symbol, metric, view_id, session,
  start_ns) with its status (``incomplete``, ``complete`` or ``no_data``), the
  stored row count, the ``source_endpoint`` and the sha256 of the window file.
  No row means the window is ``absent``.
- ``bars_coverage``: the same for each stored bar file, keyed by dataset
  (``bars`` for futures, ``vix`` for VIX index bars), instrument, bar interval
  and session, plus the contract and source of that session's bars.
- ``pulls``: one row per pull with its start Instant and arguments as JSON.

Sessions are stored as ``YYYY-MM-DD`` text and Instants as integer ns UTC.
Each status change is one statement inside one ``BEGIN IMMEDIATE`` transaction,
so a window is ``complete`` with its row count, endpoint and sha256 together or
not at all (Req 3.9). The database is opened lazily. Reading a catalog that does
not exist yet creates nothing and reports every entry ``absent``; the first
write calls ``before_write`` (the path guard) before the file is created.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Literal, Protocol, Self, cast

from fse.data.cache_io import CacheError, check_component
from fse.timekit import Instant

__all__ = [
    "BARS_DATASETS",
    "CATALOG_FILE_NAME",
    "FINAL_STATUSES",
    "SCHEMA_VERSION",
    "STORED_STATUSES",
    "BarsCoverageRecord",
    "BarsDataset",
    "BarsKey",
    "Catalog",
    "CatalogError",
    "FinalStatus",
    "PullRecord",
    "StoredStatus",
    "WindowKeyLike",
    "WindowRecord",
    "WindowStatus",
]

CATALOG_FILE_NAME: Final = "catalog.sqlite"
SCHEMA_VERSION: Final = 1

type StoredStatus = Literal["incomplete", "complete", "no_data"]
type FinalStatus = Literal["complete", "no_data"]
type WindowStatus = Literal["absent", "incomplete", "complete", "no_data"]
type BarsDataset = Literal["bars", "vix"]

STORED_STATUSES: frozenset[str] = frozenset({"incomplete", "complete", "no_data"})
FINAL_STATUSES: frozenset[str] = frozenset({"complete", "no_data"})
BARS_DATASETS: frozenset[str] = frozenset({"bars", "vix"})

_SHA256_HEX_LEN: Final = 64
_BUSY_TIMEOUT_S: Final = 30.0

_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS windows (
        symbol TEXT NOT NULL,
        metric TEXT NOT NULL CHECK (metric IN ('gamma', 'vanna')),
        view_id TEXT NOT NULL,
        session TEXT NOT NULL,
        start_ns INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('incomplete', 'complete', 'no_data')),
        "rows" INTEGER,
        source_endpoint TEXT,
        sha256 TEXT,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (symbol, metric, view_id, session, start_ns)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS bars_coverage (
        dataset TEXT NOT NULL CHECK (dataset IN ('bars', 'vix')),
        instrument TEXT NOT NULL,
        interval_s INTEGER NOT NULL,
        session TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('incomplete', 'complete', 'no_data')),
        contract TEXT,
        source TEXT,
        "rows" INTEGER,
        sha256 TEXT,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (dataset, instrument, interval_s, session)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS pulls (
        pull_id TEXT NOT NULL PRIMARY KEY,
        started_at INTEGER NOT NULL,
        args_json TEXT NOT NULL
    )
    """,
)

_WINDOW_COLUMNS: Final = (
    'symbol, metric, view_id, session, start_ns, status, "rows", source_endpoint, sha256, '
    "updated_at"
)
_BARS_COLUMNS: Final = (
    'dataset, instrument, interval_s, session, status, contract, source, "rows", sha256, updated_at'
)


class CatalogError(CacheError):
    """The catalog file cannot be used (unknown schema version or not a catalog)."""


# ---------------------------------------------------------------- keys and records


class WindowKeyLike(Protocol):
    """The five fields that key a Cache_Window (Req 3.6); see ``fse.data.cache``."""

    @property
    def symbol(self) -> str: ...
    @property
    def metric(self) -> str: ...
    @property
    def view_id(self) -> str: ...
    @property
    def session(self) -> date: ...
    @property
    def start_ns(self) -> Instant: ...


def _check_session(label: str, value: object) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{label} must be a date, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class BarsKey:
    """One stored bar file: dataset, instrument, bar interval and session."""

    dataset: BarsDataset
    instrument: str
    interval_s: int
    session: date

    def __post_init__(self) -> None:
        if self.dataset not in BARS_DATASETS:
            raise ValueError(f"dataset must be one of {sorted(BARS_DATASETS)}: {self.dataset!r}")
        check_component("instrument", self.instrument)
        if (
            not isinstance(self.interval_s, int)
            or isinstance(self.interval_s, bool)
            or self.interval_s <= 0
        ):
            raise ValueError(f"interval_s must be a positive integer: {self.interval_s!r}")
        _check_session("session", self.session)

    def describe(self) -> str:
        return f"{self.dataset} {self.instrument} {self.interval_s}s bars, session {self.session}"


@dataclass(frozen=True, slots=True)
class WindowRecord:
    """One ``windows`` row."""

    symbol: str
    metric: str
    view_id: str
    session: date
    start_ns: Instant
    status: StoredStatus
    rows: int | None
    source_endpoint: str | None
    sha256: str | None
    updated_at: Instant


@dataclass(frozen=True, slots=True)
class BarsCoverageRecord:
    """One ``bars_coverage`` row."""

    dataset: BarsDataset
    instrument: str
    interval_s: int
    session: date
    status: StoredStatus
    contract: str | None
    source: str | None
    rows: int | None
    sha256: str | None
    updated_at: Instant


@dataclass(frozen=True, slots=True)
class PullRecord:
    """One ``pulls`` row."""

    pull_id: str
    started_at: Instant
    args_json: str


def _check_final(status: str, rows: int, sha256: str) -> None:
    if status not in FINAL_STATUSES:
        raise ValueError(f"final status must be one of {sorted(FINAL_STATUSES)}: {status!r}")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
        raise ValueError(f"rows must be a non-negative integer: {rows!r}")
    if status == "no_data" and rows != 0:
        raise ValueError(f"a no_data entry has 0 rows, got {rows}")
    if len(sha256) != _SHA256_HEX_LEN or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError(f"sha256 must be 64 lowercase hex characters: {sha256!r}")


def _window_record(row: tuple[object, ...]) -> WindowRecord:
    symbol, metric, view_id, session, start_ns, status, rows, endpoint, sha, updated = row
    return WindowRecord(
        symbol=cast(str, symbol),
        metric=cast(str, metric),
        view_id=cast(str, view_id),
        session=date.fromisoformat(cast(str, session)),
        start_ns=cast(int, start_ns),
        status=cast(StoredStatus, status),
        rows=cast(int | None, rows),
        source_endpoint=cast(str | None, endpoint),
        sha256=cast(str | None, sha),
        updated_at=cast(int, updated),
    )


def _bars_record(row: tuple[object, ...]) -> BarsCoverageRecord:
    dataset, instrument, interval_s, session, status, contract, source, rows, sha, updated = row
    return BarsCoverageRecord(
        dataset=cast(BarsDataset, dataset),
        instrument=cast(str, instrument),
        interval_s=cast(int, interval_s),
        session=date.fromisoformat(cast(str, session)),
        status=cast(StoredStatus, status),
        contract=cast(str | None, contract),
        source=cast(str | None, source),
        rows=cast(int | None, rows),
        sha256=cast(str | None, sha),
        updated_at=cast(int, updated),
    )


def _window_params(key: WindowKeyLike) -> tuple[str, str, str, str, int]:
    return (key.symbol, key.metric, key.view_id, key.session.isoformat(), key.start_ns)


def _bars_params(key: BarsKey) -> tuple[str, str, int, str]:
    return (key.dataset, key.instrument, key.interval_s, key.session.isoformat())


# ---------------------------------------------------------------- the catalog


class Catalog:
    """``catalog.sqlite``: completion state of every cached window and bar file."""

    __slots__ = ("_before_write", "_clock", "_conn", "_guarded", "_path", "_schema_ready")

    def __init__(
        self,
        path: Path,
        *,
        before_write: Callable[[], object] | None = None,
        clock: Callable[[], Instant] = time.time_ns,
    ) -> None:
        self._path = path.absolute()
        self._before_write = before_write
        self._clock = clock
        self._conn: sqlite3.Connection | None = None
        self._schema_ready = False
        self._guarded = False

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._schema_ready = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---------------------------------------------------------------- windows

    def window(self, key: WindowKeyLike) -> WindowRecord | None:
        conn = self._reader()
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_WINDOW_COLUMNS} FROM windows "
            "WHERE symbol = ? AND metric = ? AND view_id = ? AND session = ? AND start_ns = ?",
            _window_params(key),
        ).fetchone()
        return None if row is None else _window_record(row)

    def window_status(self, key: WindowKeyLike) -> WindowStatus:
        record = self.window(key)
        return "absent" if record is None else record.status

    def windows(
        self,
        *,
        symbol: str | None = None,
        metric: str | None = None,
        view_id: str | None = None,
        session: date | None = None,
    ) -> list[WindowRecord]:
        """Rows matching every given field, ordered by key."""
        conn = self._reader()
        if conn is None:
            return []
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (
            ("symbol", symbol),
            ("metric", metric),
            ("view_id", view_id),
            ("session", None if session is None else session.isoformat()),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT {_WINDOW_COLUMNS} FROM windows{where} "
            "ORDER BY symbol, metric, view_id, session, start_ns",
            params,
        ).fetchall()
        return [_window_record(row) for row in rows]

    def mark_window_incomplete(self, key: WindowKeyLike) -> None:
        """Step 1 of the write protocol: the window is ``incomplete`` until finished."""
        self._upsert_window(key, "incomplete", None, None, None)

    def finish_window(
        self,
        key: WindowKeyLike,
        *,
        status: FinalStatus,
        rows: int,
        source_endpoint: str,
        sha256: str,
    ) -> None:
        """Step 3: status, row count, endpoint and sha256 in one transaction."""
        _check_final(status, rows, sha256)
        if not source_endpoint:
            raise ValueError("source_endpoint must not be blank")
        self._upsert_window(key, status, rows, source_endpoint, sha256)

    def _upsert_window(
        self,
        key: WindowKeyLike,
        status: StoredStatus,
        rows: int | None,
        source_endpoint: str | None,
        sha256: str | None,
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                f"INSERT INTO windows ({_WINDOW_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (symbol, metric, view_id, session, start_ns) DO UPDATE SET "
                'status = excluded.status, "rows" = excluded."rows", '
                "source_endpoint = excluded.source_endpoint, sha256 = excluded.sha256, "
                "updated_at = excluded.updated_at",
                (*_window_params(key), status, rows, source_endpoint, sha256, self._clock()),
            )

    # ---------------------------------------------------------------- bars coverage

    def bars(self, key: BarsKey) -> BarsCoverageRecord | None:
        conn = self._reader()
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_BARS_COLUMNS} FROM bars_coverage "
            "WHERE dataset = ? AND instrument = ? AND interval_s = ? AND session = ?",
            _bars_params(key),
        ).fetchone()
        return None if row is None else _bars_record(row)

    def bars_status(self, key: BarsKey) -> WindowStatus:
        record = self.bars(key)
        return "absent" if record is None else record.status

    def bars_coverage(
        self,
        *,
        dataset: BarsDataset | None = None,
        instrument: str | None = None,
        interval_s: int | None = None,
    ) -> list[BarsCoverageRecord]:
        """Rows matching every given field, ordered by key."""
        conn = self._reader()
        if conn is None:
            return []
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (
            ("dataset", dataset),
            ("instrument", instrument),
            ("interval_s", interval_s),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT {_BARS_COLUMNS} FROM bars_coverage{where} "
            "ORDER BY dataset, instrument, interval_s, session",
            params,
        ).fetchall()
        return [_bars_record(row) for row in rows]

    def mark_bars_incomplete(
        self, key: BarsKey, *, contract: str | None = None, source: str | None = None
    ) -> None:
        self._upsert_bars(key, "incomplete", contract, source, None, None)

    def finish_bars(
        self,
        key: BarsKey,
        *,
        status: FinalStatus,
        rows: int,
        contract: str,
        source: str,
        sha256: str,
    ) -> None:
        _check_final(status, rows, sha256)
        self._upsert_bars(key, status, contract, source, rows, sha256)

    def _upsert_bars(
        self,
        key: BarsKey,
        status: StoredStatus,
        contract: str | None,
        source: str | None,
        rows: int | None,
        sha256: str | None,
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                f"INSERT INTO bars_coverage ({_BARS_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (dataset, instrument, interval_s, session) DO UPDATE SET "
                "status = excluded.status, contract = excluded.contract, "
                'source = excluded.source, "rows" = excluded."rows", '
                "sha256 = excluded.sha256, updated_at = excluded.updated_at",
                (*_bars_params(key), status, contract, source, rows, sha256, self._clock()),
            )

    # ---------------------------------------------------------------- pulls

    def record_pull(self, pull_id: str, started_at: Instant, args_json: str) -> None:
        """Add a pull. ``ValueError`` if the id exists or ``args_json`` is not JSON."""
        if not pull_id.strip():
            raise ValueError("pull_id must not be blank")
        if not isinstance(started_at, int) or isinstance(started_at, bool):
            raise ValueError(f"started_at must be an integer Instant: {started_at!r}")
        try:
            json.loads(args_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"args_json is not valid JSON: {exc}") from exc
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO pulls (pull_id, started_at, args_json) VALUES (?, ?, ?)",
                    (pull_id, started_at, args_json),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"pull {pull_id!r} is already recorded") from exc

    def pull(self, pull_id: str) -> PullRecord | None:
        conn = self._reader()
        if conn is None:
            return None
        row = conn.execute(
            "SELECT pull_id, started_at, args_json FROM pulls WHERE pull_id = ?", (pull_id,)
        ).fetchone()
        return None if row is None else PullRecord(*row)

    def pulls(self) -> list[PullRecord]:
        conn = self._reader()
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT pull_id, started_at, args_json FROM pulls ORDER BY started_at, pull_id"
        ).fetchall()
        return [PullRecord(*row) for row in rows]

    # ---------------------------------------------------------------- connections

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        mode = "rwc" if create else "rw"
        conn = sqlite3.connect(
            f"{self._path.as_uri()}?mode={mode}",
            uri=True,
            timeout=_BUSY_TIMEOUT_S,
            isolation_level=None,  # explicit BEGIN IMMEDIATE / COMMIT below
        )
        try:
            conn.execute("PRAGMA synchronous = FULL")
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise CatalogError(f"{self._path} is not a Data_Cache catalog: {exc}") from exc
        return conn

    @staticmethod
    def _version(conn: sqlite3.Connection) -> int:
        return cast(int, conn.execute("PRAGMA user_version").fetchone()[0])

    def _reader(self) -> sqlite3.Connection | None:
        """The connection, or ``None`` while the catalog has no schema yet."""
        if self._conn is not None and self._schema_ready:
            return self._conn
        if self._conn is None:
            if not self._path.exists():
                return None
            self._conn = self._connect(create=False)
        try:
            version = self._version(self._conn)
        except sqlite3.DatabaseError as exc:
            self.close()
            raise CatalogError(f"{self._path} is not a Data_Cache catalog: {exc}") from exc
        if version == 0:
            return None
        if version != SCHEMA_VERSION:
            self.close()
            raise CatalogError(
                f"{self._path} has catalog schema version {version}; "
                f"this version reads only {SCHEMA_VERSION}"
            )
        self._schema_ready = True
        return self._conn

    def _writer(self) -> sqlite3.Connection:
        """The connection after the path guard, with the schema in place."""
        if not self._guarded:
            # Also when a read already opened an existing catalog: the guard
            # runs before this object's first write, whatever came before.
            if self._before_write is not None:
                self._before_write()
            self._guarded = True
        if self._conn is None:
            self._conn = self._connect(create=True)
        if self._schema_ready:
            return self._conn
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                version = self._version(conn)
                if version == 0:
                    for statement in _SCHEMA:
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise CatalogError(
                        f"{self._path} has catalog schema version {version}; "
                        f"this version writes only {SCHEMA_VERSION}"
                    )
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            self.close()
            raise CatalogError(f"{self._path} is not a Data_Cache catalog: {exc}") from exc
        except CatalogError:
            self.close()
            raise
        self._schema_ready = True
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._writer()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
