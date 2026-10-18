"""Staging directory, manifest and commit protocol (design §8.3).

Layout::

    {staging}/manifest.json
    {staging}/data/{rel_path}part-NNNNN.parquet
    {staging}/.work/{rel_path}          an interrupted partition, cleared at start

Commit protocol per partition: write into ``.work/{rel_path}`` (each file
fsynced), hash each file, remove the old ``data/{rel_path}``, ``os.replace``
the work directory into place, then write the manifest atomically. An
interrupt before the manifest write leaves the manifest at the old state or
without the partition, so the next run restages it.

A partition is up to date when the manifest fingerprint equals the plan's and
every listed file exists with its recorded size. After each partition the
staged rows (summed from the part footers) must equal the plan's
``source_rows``; a mismatch discards the work directory and raises
``ExportDataError`` (exit 4) with the manifest untouched.

Deletes happen only under ``.work/`` and ``data/{rel_path}``, each re-checked
just before the ``rmtree``. The staging root itself is never removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from fse.awsexport import transform
from fse.awsexport.errors import ExportDataError
from fse.awsexport.source import BarInput, PartitionPlan, Plan, WindowInput
from fse.awsexport.tables import PART_RE, TABLES, partition_rel, staged_dir
from fse.data.cache_io import fsync_dir, make_dirs, write_atomic
from fse.logio.log_writer import LogWriter

__all__ = [
    "DISK_HEADROOM_BYTES",
    "MANIFEST_FILE_NAME",
    "MANIFEST_FORMAT",
    "STAGED_TO_SOURCE_BYTES",
    "STAGED_TO_SOURCE_DISK_FACTOR",
    "Manifest",
    "ManifestFile",
    "ManifestPartition",
    "StageReport",
    "file_sha256",
    "load_manifest",
    "manifest_tables",
    "stage",
    "write_manifest",
]

MANIFEST_FORMAT: Final = "fse.aws-export-manifest/1"
MANIFEST_FILE_NAME: Final = "manifest.json"
WORK_DIR_NAME: Final = ".work"
DATA_DIR_NAME: Final = "data"
DISK_HEADROOM_BYTES: Final = 2**30
# The disk check assumes staged bytes = source bytes x this factor. The
# measured ratio is lower (0.699 for SPX gamma 2025-10-06 and 0.859 for that
# session's MES bars, from tests/integration/test_aws_export_localcache.py;
# the design measured 0.57 on one SPY session), so 1.0 leaves margin.
STAGED_TO_SOURCE_DISK_FACTOR: Final = 1.0
# Staged/source byte ratio for the dry-run size estimate only (never the disk
# check): the localcache test's heatmap measurement, 0.699, rounded up. Heatmaps
# are over 99% of the cache bytes.
STAGED_TO_SOURCE_BYTES: Final = 0.70
_HASH_CHUNK: Final = 1 << 20

UNREADABLE_WARNING: Final = "staging manifest unreadable; restaging everything"


# ---------------------------------------------------------------- the manifest


@dataclass(frozen=True, slots=True)
class ManifestFile:
    name: str
    size: int
    sha256: str
    rows: int


@dataclass(frozen=True, slots=True)
class ManifestPartition:
    rel_path: str
    table: str
    values: tuple[tuple[str, str], ...]
    fingerprint: str
    rows: int
    files: tuple[ManifestFile, ...]
    staged_at_ns: int

    @property
    def value_map(self) -> dict[str, str]:
        return dict(self.values)


@dataclass(slots=True)
class Manifest:
    """``rel_path`` -> committed partition."""

    partitions: dict[str, ManifestPartition] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "format": MANIFEST_FORMAT,
            "partitions": {
                rel: {
                    "table": p.table,
                    "values": dict(p.values),
                    "fingerprint": p.fingerprint,
                    "rows": p.rows,
                    "files": [
                        {"name": f.name, "size": f.size, "sha256": f.sha256, "rows": f.rows}
                        for f in p.files
                    ],
                    "staged_at_ns": p.staged_at_ns,
                }
                for rel, p in sorted(self.partitions.items())
            },
        }


def _parse_manifest(raw: Any) -> Manifest:
    if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
        raise ValueError("unknown format")
    out = Manifest()
    for rel, entry in raw["partitions"].items():
        table = entry["table"]
        spec = TABLES[table]
        values = tuple((k, str(entry["values"][k])) for k in spec.partition_keys)
        if partition_rel(spec, dict(values)) != rel:
            raise ValueError(f"partition {rel!r} does not match its values")
        files = tuple(
            ManifestFile(str(f["name"]), int(f["size"]), str(f["sha256"]), int(f["rows"]))
            for f in entry["files"]
        )
        if any(PART_RE.fullmatch(f.name) is None for f in files):
            raise ValueError(f"partition {rel!r} lists a file that is not a part")
        out.partitions[rel] = ManifestPartition(
            rel_path=rel,
            table=table,
            values=values,
            fingerprint=str(entry["fingerprint"]),
            rows=int(entry["rows"]),
            files=files,
            staged_at_ns=int(entry["staged_at_ns"]),
        )
    return out


def load_manifest(staging_dir: Path) -> tuple[Manifest, str | None]:
    """The staged manifest, and a warning when it is unreadable (then it is empty)."""
    path = staging_dir / MANIFEST_FILE_NAME
    if not path.exists():
        return Manifest(), None
    try:
        return _parse_manifest(json.loads(path.read_bytes())), None
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return Manifest(), UNREADABLE_WARNING


def write_manifest(staging_dir: Path, manifest: Manifest) -> None:
    data = json.dumps(manifest.to_json(), indent=1, sort_keys=True).encode()
    write_atomic(staging_dir / MANIFEST_FILE_NAME, data)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- the report


@dataclass(frozen=True, slots=True)
class StageReport:
    restaged: tuple[str, ...]  # rel_paths (dry-run: the ones that would be)
    up_to_date: tuple[str, ...]
    pruned: tuple[str, ...]
    rows_written: int
    bytes_written: int
    windows_read: int
    bar_sessions_read: int
    pending_source_bytes: int  # source bytes of the partitions to restage
    manifest: Manifest


# ---------------------------------------------------------------- guarded deletes


def _rmtree_checked(path: Path, staging_dir: Path, *, rel_path: str | None) -> None:
    """``rmtree(path)`` after re-checking it is ``.work/...`` or ``data/{rel_path}``."""
    work = staging_dir / WORK_DIR_NAME
    allowed = path == work or path.is_relative_to(work)
    if rel_path is not None:
        allowed = allowed or path == staged_dir(staging_dir, rel_path)
    if not allowed or path == staging_dir:
        raise AssertionError(f"refusing to delete {path}: not under {work} or a staged partition")
    shutil.rmtree(path)


def _is_up_to_date(pp: PartitionPlan, entry: ManifestPartition | None, staging_dir: Path) -> bool:
    if entry is None or entry.fingerprint != pp.fingerprint:
        return False
    directory = staged_dir(staging_dir, pp.rel_path)
    for f in entry.files:
        try:
            if (directory / f.name).stat().st_size != f.size:
                return False
        except OSError:
            return False
    return True


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return Path(path.anchor)


def _check_disk(staging_dir: Path, pending: int) -> None:
    need = int(pending * STAGED_TO_SOURCE_DISK_FACTOR) + DISK_HEADROOM_BYTES
    free = shutil.disk_usage(_nearest_existing(staging_dir)).free
    if free < need:
        raise ExportDataError(
            f"not enough free disk space for the export staging directory {staging_dir}: "
            f"needs about {need} bytes, {free} free. Nothing was written."
        )


# ---------------------------------------------------------------- staging


def _commit(
    pp: PartitionPlan, staging_dir: Path, manifest: Manifest, now_ns: int
) -> ManifestPartition:
    work = staging_dir / WORK_DIR_NAME
    work_part = work.joinpath(*pp.rel_path.strip("/").split("/"))
    if work_part.exists():
        _rmtree_checked(work_part, staging_dir, rel_path=None)
    make_dirs(work_part)
    try:
        parts = transform.write_partition(pp, work_part)
        staged_rows = sum(p.rows for p in parts)
        if staged_rows != pp.source_rows:
            raise ExportDataError(
                f"partition {pp.rel_path}: staged {staged_rows} rows, but the source holds "
                f"{pp.source_rows}; nothing was committed for it"
            )
        files = tuple(
            ManifestFile(p.name, p.size, file_sha256(work_part / p.name), p.rows) for p in parts
        )
        fsync_dir(work_part)
        target = staged_dir(staging_dir, pp.rel_path)
        if target.exists():
            _rmtree_checked(target, staging_dir, rel_path=pp.rel_path)
        make_dirs(target.parent)
        os.replace(work_part, target)
        fsync_dir(target.parent)
    except BaseException:
        if work_part.exists():
            _rmtree_checked(work_part, staging_dir, rel_path=None)
        raise
    entry = ManifestPartition(
        rel_path=pp.rel_path,
        table=pp.table,
        values=pp.values,
        fingerprint=pp.fingerprint,
        rows=staged_rows,
        files=files,
        staged_at_ns=now_ns,
    )
    manifest.partitions[pp.rel_path] = entry
    write_manifest(staging_dir, manifest)
    return entry


def stage(
    plan: Plan, staging_dir: Path, *, writer: LogWriter, dry_run: bool = False
) -> StageReport:
    """Bring ``staging_dir`` up to date with ``plan``.

    With ``dry_run`` only the manifest and file sizes are read and the lines
    describe what would happen; nothing is written or deleted.
    """
    manifest, warning = load_manifest(staging_dir)
    if warning is not None:
        writer.echo(f"warning: {warning}")
    planned = {pp.rel_path for pp in plan.partitions}
    stale = sorted(
        rel
        for rel, entry in manifest.partitions.items()
        if rel not in planned and plan.in_scope(entry.table, entry.value_map)
    )
    todo = [
        pp
        for pp in plan.partitions
        if not _is_up_to_date(pp, manifest.partitions.get(pp.rel_path), staging_dir)
    ]
    todo_rels = {pp.rel_path for pp in todo}
    current = [pp.rel_path for pp in plan.partitions if pp.rel_path not in todo_rels]
    pending = sum(pp.source_bytes for pp in todo)
    windows = sum(1 for pp in todo for i in pp.inputs if isinstance(i, WindowInput))
    bar_sessions = sum(1 for pp in todo for i in pp.inputs if isinstance(i, BarInput))
    if dry_run:
        for rel in stale:
            writer.echo(f"would prune staged partition {rel} (its source no longer exists)")
        for pp in todo:
            writer.echo(
                f"would stage {pp.rel_path}: {pp.source_rows} rows from {len(pp.inputs)} "
                f"files, {pp.source_bytes} source bytes"
            )
        for rel in current:
            writer.echo(f"plan: {rel} up to date")
        return StageReport(
            restaged=tuple(pp.rel_path for pp in todo),
            up_to_date=tuple(current),
            pruned=tuple(stale),
            rows_written=0,
            bytes_written=0,
            windows_read=windows,
            bar_sessions_read=bar_sessions,
            pending_source_bytes=pending,
            manifest=manifest,
        )

    if todo:
        _check_disk(staging_dir, pending)
    try:
        work = staging_dir / WORK_DIR_NAME
        if work.exists():
            _rmtree_checked(work, staging_dir, rel_path=None)
        make_dirs(staging_dir)
        for rel in stale:
            directory = staged_dir(staging_dir, rel)
            if directory.exists():
                _rmtree_checked(directory, staging_dir, rel_path=rel)
            del manifest.partitions[rel]
            writer.echo(f"pruned staged partition {rel} (its source no longer exists)")
        if stale or warning is not None:
            write_manifest(staging_dir, manifest)
        rows = size = 0
        for pp in todo:
            entry = _commit(pp, staging_dir, manifest, time.time_ns())
            rows += entry.rows
            size += sum(f.size for f in entry.files)
            writer.echo(
                f"staged {pp.rel_path}: {entry.rows} rows in {len(entry.files)} files, "
                f"{sum(f.size for f in entry.files)} bytes"
            )
        if (staging_dir / WORK_DIR_NAME).exists():
            _rmtree_checked(staging_dir / WORK_DIR_NAME, staging_dir, rel_path=None)
    except OSError as exc:
        raise ExportDataError(f"staging write failed under {staging_dir}: {exc}") from exc
    return StageReport(
        restaged=tuple(pp.rel_path for pp in todo),
        up_to_date=tuple(current),
        pruned=tuple(stale),
        rows_written=rows,
        bytes_written=size,
        windows_read=windows,
        bar_sessions_read=bar_sessions,
        pending_source_bytes=pending,
        manifest=manifest,
    )


def manifest_tables(manifest: Manifest) -> Mapping[str, list[ManifestPartition]]:
    """Committed partitions per table, sorted by ``rel_path``."""
    out: dict[str, list[ManifestPartition]] = {}
    for rel in sorted(manifest.partitions):
        entry = manifest.partitions[rel]
        out.setdefault(entry.table, []).append(entry)
    return out
