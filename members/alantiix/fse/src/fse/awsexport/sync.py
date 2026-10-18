"""Sync staged parts to S3 (design §8.5, review finding 2).

Per selected table with staged data, in registry order:

1. list the remote keys under the table prefix (one paginated listing);
2. upload every staged part whose remote copy is absent or differs (size,
   then the ``sha256`` object metadata); each staged file is re-hashed first;
3. delete superseded parts: remote part keys of a staged partition that the
   partition no longer has (a partition that shrank from two parts to one);
4. warn about foreign keys (not a well-formed part key) and orphaned
   partitions (no staged source); both are kept.

``DeleteObjects`` reports per-key failures in ``Errors`` with HTTP 200, so
every response is checked: any error stops the run (exit 3 for access denied,
else 4), because a superseded part left in place doubles rows in Athena.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from fse.awsexport import aws
from fse.awsexport.errors import ExportAccessError, ExportDataError
from fse.awsexport.options import ExportOptions
from fse.awsexport.staging import Manifest, manifest_tables, write_manifest
from fse.awsexport.tables import (
    EXPORT_FORMAT,
    PART_RE,
    TABLES,
    parse_partition_key,
    partition_prefix,
    partition_rel,
    staged_dir,
    table_prefix,
)
from fse.logio.log_writer import LogWriter

__all__ = [
    "CONTENT_TYPE",
    "DELETE_BATCH",
    "RemotePartitions",
    "SyncReport",
    "delete_superseded",
    "sync_tables",
]

type S3Client = Any
type PartitionValues = tuple[tuple[str, str], ...]
# Table name -> parsed partition values of the remote part keys kept after sync.
type RemotePartitions = Mapping[str, frozenset[PartitionValues]]

CONTENT_TYPE: Final = "application/vnd.apache.parquet"
DELETE_BATCH: Final = 1000
_MIB: Final = 2**20
_ACCESS_DENIED: Final = frozenset({"AccessDenied", "AccessDeniedException"})
_HASH_CHUNK: Final = _MIB


@dataclass(frozen=True, slots=True)
class SyncReport:
    uploaded: tuple[str, ...]
    skipped: tuple[str, ...]
    deleted: tuple[str, ...]
    bytes_uploaded: int
    remote: RemotePartitions


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _list(s3: S3Client, *, bucket: str, account: str, prefix: str, profile: str) -> dict[str, int]:
    paginator = s3.get_paginator("list_objects_v2")
    out: dict[str, int] = {}

    def run() -> None:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, ExpectedBucketOwner=account):
            for obj in page.get("Contents", []):
                out[obj["Key"]] = int(obj["Size"])

    aws.call(profile, "ListObjectsV2", f"s3://{bucket}/{prefix}", run)
    return out


def delete_superseded(
    s3: S3Client, *, bucket: str, account: str, keys: Sequence[str], profile: str
) -> list[str]:
    """Delete ``keys`` (superseded part keys) in batches of up to 1,000; the keys deleted.

    Every key is re-checked to end in a ``part-NNNNN.parquet`` segment just
    before its batch is sent (``AssertionError`` otherwise, a bug). A batch
    whose response lists ``Errors`` raises at once, so the run stops there:
    ``ExportAccessError`` (exit 3) when any code is access denied, else
    ``ExportDataError`` (exit 4).
    """
    deleted: list[str] = []
    for start in range(0, len(keys), DELETE_BATCH):
        batch = list(keys[start : start + DELETE_BATCH])
        for key in batch:
            if PART_RE.fullmatch(key.rsplit("/", 1)[-1]) is None:
                raise AssertionError(f"refusing to delete {key}: not a part key")
        resp = aws.call(
            profile,
            "DeleteObjects",
            f"s3://{bucket}",
            s3.delete_objects,
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            ExpectedBucketOwner=account,
        )
        errors = resp.get("Errors", []) or []
        if errors:
            codes = {str(e.get("Code", "")) for e in errors}
            shown = ", ".join(str(e.get("Key", "?")) for e in errors[:10])
            if codes & _ACCESS_DENIED:
                raise ExportAccessError(
                    f"DeleteObjects denied for {len(errors)} superseded keys in s3://{bucket} "
                    f"(profile '{profile}'): {shown}; Athena may see duplicate rows until a "
                    "rerun deletes them"
                )
            raise ExportDataError(
                f"DeleteObjects failed for {len(errors)} superseded keys in s3://{bucket} "
                f"({sorted(codes)}): {shown}; Athena may see duplicate rows until a rerun "
                "deletes them"
            )
        deleted.extend(batch)
    return deleted


def sync_tables(
    s3: S3Client,
    *,
    options: ExportOptions,
    account: str,
    bucket: str,
    manifest: Manifest,
    staging_dir: Path,
    writer: LogWriter,
) -> SyncReport:
    """Upload, delete superseded parts and report remote partitions, table by table."""
    assert not options.dry_run
    from boto3.exceptions import S3UploadFailedError
    from boto3.s3.transfer import TransferConfig
    from botocore.exceptions import BotoCoreError, ClientError

    profile = options.profile
    transfer = TransferConfig(
        multipart_threshold=64 * _MIB, multipart_chunksize=64 * _MIB, max_concurrency=8
    )
    by_table = manifest_tables(manifest)
    uploaded: list[str] = []
    skipped: list[str] = []
    deleted: list[str] = []
    sent = 0
    remote_values: dict[str, frozenset[PartitionValues]] = {}
    for name in options.tables:
        partitions = by_table.get(name, [])
        if not partitions:
            continue
        spec = TABLES[name]
        root = table_prefix(options.prefix, spec)
        remote = _list(s3, bucket=bucket, account=account, prefix=root, profile=profile)
        staged_names: dict[PartitionValues, set[str]] = {}
        for p in partitions:
            staged_names[p.values] = {f.name for f in p.files}
            directory = staged_dir(staging_dir, p.rel_path)
            for f in p.files:
                path = directory / f.name
                key = partition_prefix(options.prefix, spec, p.value_map) + f.name
                try:
                    digest = _sha256(path)
                except OSError:
                    digest = ""
                if digest != f.sha256:
                    del manifest.partitions[p.rel_path]
                    write_manifest(staging_dir, manifest)
                    raise ExportDataError(f"staged file changed; rerun to restage: {path}")
                if remote.get(key) == f.size:
                    head = aws.call(
                        profile,
                        "HeadObject",
                        f"s3://{bucket}/{key}",
                        s3.head_object,
                        Bucket=bucket,
                        Key=key,
                        ExpectedBucketOwner=account,
                    )
                    if head.get("Metadata", {}).get("sha256") == f.sha256:
                        skipped.append(key)
                        writer.echo(f"unchanged s3://{bucket}/{key}")
                        continue
                extra = {
                    "Metadata": {"sha256": f.sha256, "fse-export-format": EXPORT_FORMAT},
                    "ContentType": CONTENT_TYPE,
                    "ExpectedBucketOwner": account,
                }
                try:
                    s3.upload_file(str(path), bucket, key, ExtraArgs=extra, Config=transfer)
                except (ClientError, BotoCoreError, S3UploadFailedError) as exc:
                    raise aws.translate_aws_error(
                        exc, "PutObject", f"s3://{bucket}/{key}", profile=profile
                    ) from exc
                uploaded.append(key)
                sent += f.size
                writer.echo(f"uploaded s3://{bucket}/{key} ({f.size} bytes)")
        superseded: list[str] = []
        kept: set[PartitionValues] = set()
        for key in sorted(remote):
            values = parse_partition_key(options.prefix, spec, key)
            if values is None:
                writer.echo(f"warning: foreign object s3://{bucket}/{key} ignored")
                continue
            vals = tuple((k, values[k]) for k in spec.partition_keys)
            names = staged_names.get(vals)
            if names is None:
                rel = partition_rel(spec, values)
                writer.echo(f"warning: remote partition {rel} has no staged source; kept")
                kept.add(vals)
            elif key.rsplit("/", 1)[-1] not in names:
                superseded.append(key)
            else:
                kept.add(vals)
        for key in delete_superseded(
            s3, bucket=bucket, account=account, keys=superseded, profile=profile
        ):
            deleted.append(key)
            writer.echo(f"deleted superseded s3://{bucket}/{key}")
        kept.update(staged_names)
        remote_values[name] = frozenset(kept)
    return SyncReport(
        uploaded=tuple(uploaded),
        skipped=tuple(skipped),
        deleted=tuple(deleted),
        bytes_uploaded=sent,
        remote=remote_values,
    )
