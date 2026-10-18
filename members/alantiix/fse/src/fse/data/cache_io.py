"""Low-level Data_Cache file helpers shared by every store (design §3, D5).

- :class:`CacheRoot` is the resolved cache directory. It runs the path guard
  once, before the first write, and only then creates the directory, so a
  rejected path gets no file at all (Req 1.11). Reads never create anything.
- :func:`write_atomic` writes a temp file, fsyncs it, renames it over the
  target and fsyncs the directory. A crash leaves the old file or the new one,
  never a torn file.
- :func:`read_verified` reads a file and checks its sha256 against the catalog.
- Parquet and Arrow IPC helpers work on in-memory bytes, so the sha256 that
  goes into the catalog is the hash of exactly the bytes on disk.

Errors carry the design's exit codes: :class:`CacheError` and its subclasses
are data or I/O failures (exit 4); a rejected cache path raises
:class:`fse.data.path_guard.PathGuardError` (exit 2), which is never wrapped.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, Final

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pyarrow.parquet as pq

from fse.data.path_guard import check_output_dir, resolve_output_dir

__all__ = [
    "EXIT_DATA_IO",
    "PARQUET_COMPRESSION",
    "TMP_SUFFIX",
    "CacheError",
    "CacheIntegrityError",
    "CacheReadError",
    "CacheRoot",
    "CacheWriteError",
    "check_component",
    "expect_meta",
    "fsync_dir",
    "ipc_to_table",
    "make_dirs",
    "meta_bytes",
    "meta_text",
    "parquet_to_table",
    "read_verified",
    "require_columns",
    "sha256_hex",
    "table_to_ipc",
    "table_to_parquet",
    "write_atomic",
]

# Design "Exit codes": 4 = data or I/O failure, which includes a Data_Cache write.
EXIT_DATA_IO: Final = 4

PARQUET_COMPRESSION: Final = "zstd"
TMP_SUFFIX: Final = ".tmp"

type PathLike = str | os.PathLike[str]


# ---------------------------------------------------------------- errors


class CacheError(Exception):
    """A Data_Cache data or I/O failure."""

    exit_code: ClassVar[int] = EXIT_DATA_IO


class CacheWriteError(CacheError):
    """A write failed; ``target`` names what was being written (Req 3.15)."""

    def __init__(self, target: str, cause: BaseException) -> None:
        self.target = target
        self.cause = cause
        super().__init__(f"Data_Cache write failed for {target}: {type(cause).__name__}: {cause}")


class CacheReadError(CacheError):
    """The requested entry cannot be read: absent, incomplete or damaged."""


class CacheIntegrityError(CacheReadError):
    """A cached file is missing, does not match its sha256, or is malformed."""


# ---------------------------------------------------------------- the cache root


class CacheRoot:
    """The resolved Data_Cache directory, guarded before the first write."""

    __slots__ = ("_checked", "_configured", "_path")

    def __init__(self, root: PathLike) -> None:
        self._configured = os.fspath(root)
        self._path = resolve_output_dir(root)
        self._checked = False

    @property
    def path(self) -> Path:
        """The absolute, resolved directory. It may not exist yet."""
        return self._path

    def ensure_writable(self) -> Path:
        """Run the path guard (once), then create the directory if needed.

        Raises :class:`fse.data.path_guard.PathGuardError` (exit 2) without
        creating anything when the directory is inside a git working tree that
        does not ignore it.
        """
        if not self._checked:
            check_output_dir(self._configured, label="Data_Cache directory")
            self._checked = True
        make_dirs(self._path)
        return self._path


# ---------------------------------------------------------------- path components

_UNSAFE_COMPONENT = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|\s]")


def check_component(label: str, value: object) -> str:
    """``value`` if it is safe as one directory or file name, else ``ValueError``."""
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(".")
        or _UNSAFE_COMPONENT.search(value) is not None
    ):
        raise ValueError(
            f"{label} must be a non-empty name without path separators, whitespace, "
            f"control characters or a leading dot, got {value!r}"
        )
    return value


# ---------------------------------------------------------------- durable files


def fsync_dir(path: Path) -> None:
    """Flush a directory entry change (a rename or a new file) to disk."""
    if os.name == "nt":  # directories cannot be opened for fsync on Windows
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_dirs(path: Path) -> None:
    """``mkdir -p`` that fsyncs the parent of every directory it creates."""
    missing: list[Path] = []
    current = path
    while not current.is_dir():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        fsync_dir(directory.parent)


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` with temp file, fsync and rename.

    The temp file is ``<name>.tmp`` next to ``path`` and is removed if any step
    fails. Readers see the old content or the new content, never a mix.
    """
    make_dirs(path.parent)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_verified(path: Path, sha256: str | None) -> bytes:
    """The bytes of ``path``; ``CacheIntegrityError`` if missing or not matching ``sha256``."""
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise CacheIntegrityError(f"cached file {path} is missing") from exc
    except OSError as exc:
        raise CacheIntegrityError(f"cached file {path} cannot be read: {exc}") from exc
    if sha256 is not None:
        actual = sha256_hex(data)
        if actual != sha256:
            raise CacheIntegrityError(
                f"cached file {path} has sha256 {actual}, but the catalog records {sha256}"
            )
    return data


# ---------------------------------------------------------------- Parquet and IPC


def table_to_parquet(table: Any) -> bytes:
    """A zstd-compressed Parquet file of ``table``, as bytes."""
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression=PARQUET_COMPRESSION)
    return bytes(sink.getvalue().to_pybytes())


def parquet_to_table(data: bytes, *, source: Path) -> Any:
    """The table in Parquet ``data``; ``CacheIntegrityError`` naming ``source`` if unreadable."""
    try:
        return pq.read_table(pa.BufferReader(data))
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise CacheIntegrityError(f"cached file {source} is not a readable Parquet file") from exc


def table_to_ipc(table: Any) -> bytes:
    """An Arrow IPC stream of ``table``, as bytes (exact for every Arrow type)."""
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return bytes(sink.getvalue().to_pybytes())


def ipc_to_table(data: bytes, *, source: Path) -> Any:
    try:
        return pa_ipc.open_stream(pa.BufferReader(data)).read_all()
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise CacheIntegrityError(f"cached file {source} holds an unreadable Arrow table") from exc


def meta_bytes(metadata: Mapping[bytes, bytes] | None, key: str, *, source: Path) -> bytes:
    """The schema-metadata value ``key``; ``CacheIntegrityError`` if absent."""
    raw = None if metadata is None else metadata.get(key.encode())
    if raw is None:
        raise CacheIntegrityError(f"cached file {source} has no {key!r} metadata")
    return raw


def meta_text(metadata: Mapping[bytes, bytes] | None, key: str, *, source: Path) -> str:
    """The UTF-8 schema-metadata value ``key``; ``CacheIntegrityError`` if absent."""
    try:
        return meta_bytes(metadata, key, source=source).decode()
    except UnicodeDecodeError as exc:
        raise CacheIntegrityError(f"cached file {source} has non-UTF-8 {key!r} metadata") from exc


def expect_meta(
    metadata: Mapping[bytes, bytes] | None, expected: Mapping[str, str], *, source: Path
) -> None:
    """``CacheIntegrityError`` unless every ``expected`` metadata value matches."""
    for key, want in expected.items():
        got = meta_text(metadata, key, source=source)
        if got != want:
            raise CacheIntegrityError(f"cached file {source} has {key} {got!r}, expected {want!r}")


def require_columns(
    table: Any, names: Iterable[str], *, non_null: Iterable[str], source: Path
) -> None:
    """``CacheIntegrityError`` if a column is missing or a ``non_null`` column has nulls.

    pyarrow does not enforce field nullability when a table is built, so the
    readers check it.
    """
    present = set(table.column_names)
    missing = [name for name in names if name not in present]
    if missing:
        raise CacheIntegrityError(f"cached file {source} lacks columns {missing}")
    for name in non_null:
        if table.column(name).null_count:
            raise CacheIntegrityError(f"cached file {source} has nulls in column {name!r}")
