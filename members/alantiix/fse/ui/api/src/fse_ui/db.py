"""The console's SQLite store: sessions, audit, jobs, settings and the run index.

One file at ``<data>/ui/ui.sqlite``. Every call takes the process lock and a
short transaction; the API is single-process, so this is enough.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: tuple[str, ...] = (
    # 1: sessions, pending logins, audit
    """
    CREATE TABLE sessions (
        id_hash TEXT PRIMARY KEY,
        sub TEXT NOT NULL,
        email TEXT NOT NULL,
        csrf TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE TABLE auth_pending (
        state TEXT PRIMARY KEY,
        nonce TEXT NOT NULL,
        verifier TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        user TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT NOT NULL,
        result TEXT NOT NULL
    );
    """,
    # 2: jobs and settings
    """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        program TEXT NOT NULL,
        command TEXT NOT NULL,
        argv TEXT NOT NULL,
        placement TEXT NOT NULL,
        executor TEXT NOT NULL,
        status TEXT NOT NULL,
        exit_code INTEGER,
        title TEXT NOT NULL,
        user TEXT NOT NULL,
        created_at REAL NOT NULL,
        started_at REAL,
        stopped_at REAL,
        run_dir TEXT,
        external_id TEXT
    );
    CREATE INDEX jobs_created ON jobs (created_at DESC);
    CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 3: run index cache
    """
    CREATE TABLE runs_index (
        name TEXT PRIMARY KEY,
        mtime REAL NOT NULL,
        summary TEXT NOT NULL
    );
    """,
    # 4: the CloudWatch Logs cursor of a remote (ECS) job, for re-attach after a restart
    """
    ALTER TABLE jobs ADD COLUMN external_cursor TEXT;
    """,
    # 5: summaries cached before has_report looked under report/ (G-F1) are recomputed
    """
    DELETE FROM runs_index;
    """,
    # 6: service tokens (bearer, hashed) and strategy cards
    """
    CREATE TABLE service_tokens (
        id_hash TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        sub TEXT NOT NULL,
        email TEXT NOT NULL,
        scopes TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL,
        revoked_at REAL
    );
    CREATE TABLE strategy_cards (
        id TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        author TEXT NOT NULL,
        post_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        source_text TEXT NOT NULL,
        hypothesis TEXT NOT NULL,
        spec_json TEXT NOT NULL,
        spec_hash TEXT NOT NULL,
        design_json TEXT NOT NULL,
        design_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        job_id TEXT,
        run_dir TEXT,
        judge_json TEXT,
        grade TEXT NOT NULL,
        judged_at REAL
    );
    CREATE INDEX strategy_cards_hash ON strategy_cards (spec_hash);
    CREATE INDEX strategy_cards_created ON strategy_cards (created_at DESC);
    """,
    # 7: each backtest phase keeps its own job, so a holdout does not hide the design run
    """
    ALTER TABLE strategy_cards ADD COLUMN design_job_id TEXT;
    ALTER TABLE strategy_cards ADD COLUMN design_run_dir TEXT;
    ALTER TABLE strategy_cards ADD COLUMN holdout_job_id TEXT;
    ALTER TABLE strategy_cards ADD COLUMN holdout_run_dir TEXT;
    """,
)


class Db:
    """A thread-safe wrapper over one SQLite connection."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if new:
            os.chmod(path, 0o600)
        self.migrate()

    def migrate(self) -> int:
        """Apply pending migrations; the schema version after."""
        with self.tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = c.execute("SELECT version FROM schema_version").fetchone()
            version = 0 if row is None else int(row[0])
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                for statement in (s.strip() for s in script.split(";")):
                    if statement:
                        c.execute(statement)
                if version == 0 and i == 1:
                    c.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
                else:
                    c.execute("UPDATE schema_version SET version = ?", (i,))
                version = i
            return version

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, params).rowcount

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
            return row

    def all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    # ------------------------------------------------------------ key/value

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return default if row is None else json.loads(row["value"])

    def set_kv(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
