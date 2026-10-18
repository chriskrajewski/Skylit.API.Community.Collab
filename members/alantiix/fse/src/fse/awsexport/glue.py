"""Glue Data Catalog: one database, one table per output table (design §8.6).

Partitioned tables use partition projection (no crawler, no ``MSCK REPAIR``,
no registered partitions). Enum values are the union of the staged and the
remote partitions; ``month`` is a date projection from the earliest month to
``NOW``. Every call passes ``CatalogId`` (the account).

A table is created when absent, updated only when its columns, location,
partition keys or managed parameters differ (so a rerun adds no table
version), and refused when it exists without the ``fse.export.format``
marker (exit 2). An existing database is used as is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from fse.awsexport import aws
from fse.awsexport.errors import ExportInputError
from fse.awsexport.options import ExportOptions
from fse.awsexport.staging import Manifest, manifest_tables
from fse.awsexport.tables import (
    EXPORT_FORMAT,
    MONTH_RE,
    PARTITION_VALUE_RE,
    TABLES,
    TableSpec,
    glue_type,
    location_template,
    table_uri,
)
from fse.logio.log_writer import LogWriter

__all__ = [
    "DATABASE_DESCRIPTION",
    "MARKER",
    "apply_glue",
    "managed_parameters",
    "plan_glue",
    "projection_values",
    "table_input",
]

type GlueClient = Any
type PartitionValues = tuple[tuple[str, str], ...]

MARKER: Final = "fse.export.format"
DATABASE_DESCRIPTION: Final = "Skylit FSE Data_Cache export (licensed Skylit API data)"
_INPUT_FORMAT: Final = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
_OUTPUT_FORMAT: Final = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
_SERDE: Final = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
_MANAGED_KEYS: Final = ("EXTERNAL", "classification", "parquet.compression")
_MANAGED_PREFIXES: Final = ("fse.", "projection.", "storage.location.template")
_COMMENT_MAX: Final = 255


def managed_parameters(parameters: Mapping[str, str]) -> dict[str, str]:
    """The ``Parameters`` this tool owns; Glue adds others (``transient_lastDdlTime``)."""
    return {
        k: v for k, v in parameters.items() if k in _MANAGED_KEYS or k.startswith(_MANAGED_PREFIXES)
    }


def projection_values(
    spec: TableSpec, partitions: Iterable[PartitionValues]
) -> dict[str, list[str]]:
    """Sorted values per partition key, each re-checked (``AssertionError`` on a bug)."""
    out: dict[str, set[str]] = {k: set() for k in spec.partition_keys}
    for values in partitions:
        for k, v in values:
            assert PARTITION_VALUE_RE.fullmatch(v) is not None, (spec.name, k, v)
            assert k != "month" or MONTH_RE.fullmatch(v) is not None, (spec.name, k, v)
            out[k].add(v)
    return {k: sorted(v) for k, v in out.items()}


def table_input(
    spec: TableSpec, *, bucket: str, prefix: str, partitions: Iterable[PartitionValues]
) -> dict[str, Any]:
    """The full Glue ``TableInput`` of ``spec``."""
    parameters = {
        "EXTERNAL": "TRUE",
        "classification": "parquet",
        "parquet.compression": "ZSTD",
        MARKER: EXPORT_FORMAT,
    }
    if spec.partition_keys:
        values = projection_values(spec, partitions)
        parameters["projection.enabled"] = "true"
        for k in spec.partition_keys:
            if spec.projection_type(k) == "date":
                parameters.update(
                    {
                        f"projection.{k}.type": "date",
                        f"projection.{k}.format": "yyyy-MM",
                        f"projection.{k}.range": f"{values[k][0]},NOW",
                        f"projection.{k}.interval": "1",
                        f"projection.{k}.interval.unit": "MONTHS",
                    }
                )
            else:
                parameters[f"projection.{k}.type"] = "enum"
                parameters[f"projection.{k}.values"] = ",".join(values[k])
        parameters["storage.location.template"] = location_template(bucket, prefix, spec)
    columns = []
    for f in spec.schema:
        column = {"Name": f.name, "Type": glue_type(f.type)}
        comment = spec.comments.get(f.name)
        if comment:
            column["Comment"] = comment[:_COMMENT_MAX]
        columns.append(column)
    return {
        "Name": spec.name,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": parameters,
        "StorageDescriptor": {
            "Columns": columns,
            "Location": table_uri(bucket, prefix, spec),
            "InputFormat": _INPUT_FORMAT,
            "OutputFormat": _OUTPUT_FORMAT,
            "SerdeInfo": {
                "SerializationLibrary": _SERDE,
                "Parameters": {"serialization.format": "1"},
            },
        },
        "PartitionKeys": [{"Name": k, "Type": "string"} for k in spec.partition_keys],
    }


def _shape(table: Mapping[str, Any]) -> tuple[object, ...]:
    sd = table.get("StorageDescriptor", {})
    return (
        [(c.get("Name"), c.get("Type"), c.get("Comment")) for c in sd.get("Columns", [])],
        sd.get("Location"),
        [(k.get("Name"), k.get("Type")) for k in table.get("PartitionKeys", [])],
        managed_parameters(table.get("Parameters", {})),
    )


def _union(
    spec: TableSpec, manifest: Manifest, remote: Mapping[str, frozenset[PartitionValues]]
) -> set[PartitionValues]:
    values = {p.values for p in manifest_tables(manifest).get(spec.name, [])}
    values.update(remote.get(spec.name, frozenset()))
    return values


def _ensure_database(glue: GlueClient, *, options: ExportOptions, account: str) -> None:
    from botocore.exceptions import ClientError

    db = options.glue_database
    try:
        glue.get_database(Name=db, CatalogId=account)
        return
    except ClientError as exc:
        if aws.error_code(exc) != "EntityNotFoundException":
            raise aws.translate_aws_error(
                exc, "GetDatabase", f"glue:{db}", profile=options.profile
            ) from exc
    aws.call(
        options.profile,
        "CreateDatabase",
        f"glue:{db}",
        glue.create_database,
        CatalogId=account,
        DatabaseInput={
            "Name": db,
            "Description": DATABASE_DESCRIPTION,
            "Parameters": {MARKER: EXPORT_FORMAT},
        },
    )


def apply_glue(
    glue: GlueClient,
    *,
    options: ExportOptions,
    account: str,
    bucket: str,
    manifest: Manifest,
    remote: Mapping[str, frozenset[PartitionValues]],
    writer: LogWriter,
) -> None:
    """Create the database if needed, then create, update or keep each table with data."""
    assert not options.dry_run
    from botocore.exceptions import ClientError

    db = options.glue_database
    profile = options.profile
    _ensure_database(glue, options=options, account=account)
    staged = manifest_tables(manifest)
    for name in options.tables:
        if not staged.get(name):
            continue
        spec = TABLES[name]
        resource = f"glue:{db}.{name}"
        new = table_input(
            spec, bucket=bucket, prefix=options.prefix, partitions=_union(spec, manifest, remote)
        )
        try:
            existing = glue.get_table(CatalogId=account, DatabaseName=db, Name=name)["Table"]
        except ClientError as exc:
            if aws.error_code(exc) != "EntityNotFoundException":
                raise aws.translate_aws_error(exc, "GetTable", resource, profile=profile) from exc
            existing = None
        if existing is None:
            aws.call(
                profile,
                "CreateTable",
                resource,
                glue.create_table,
                CatalogId=account,
                DatabaseName=db,
                TableInput=new,
            )
            writer.echo(f"created table {db}.{name}")
            continue
        if MARKER not in existing.get("Parameters", {}):
            raise ExportInputError(
                f"table {db}.{name} exists and was not created by fse export-aws; "
                "drop it or pass another --glue-database"
            )
        if _shape(existing) == _shape(new):
            writer.echo(f"plan: table {db}.{name} unchanged")
            continue
        aws.call(
            profile,
            "UpdateTable",
            resource,
            glue.update_table,
            CatalogId=account,
            DatabaseName=db,
            TableInput=new,
        )
        writer.echo(f"updated table {db}.{name}")


def plan_glue(
    glue: GlueClient,
    *,
    options: ExportOptions,
    account: str,
    partitions: Mapping[str, Iterable[PartitionValues]],
    writer: LogWriter,
) -> None:
    """Dry-run: ``GetDatabase`` only, then the tables that would be created or updated.

    ``partitions`` holds the local plan's partition values per table (staged
    and planned); remote values are not listed in dry-run.
    """
    from botocore.exceptions import ClientError

    db = options.glue_database
    try:
        glue.get_database(Name=db, CatalogId=account)
        writer.echo(f"plan: database {db} exists")
    except ClientError as exc:
        if aws.error_code(exc) != "EntityNotFoundException":
            raise aws.translate_aws_error(
                exc, "GetDatabase", f"glue:{db}", profile=options.profile
            ) from exc
        writer.echo(f"would create database {db}")
    for name in options.tables:
        values = set(partitions.get(name, ()))
        if not values:
            continue
        spec = TABLES[name]
        writer.echo(f"would create or update table {db}.{name} (marker not checked in dry-run)")
        if spec.partition_keys:
            shown = projection_values(spec, values)
            listed = " ".join(f"{k}={','.join(v)}" for k, v in shown.items())
            writer.echo(
                f"would set projection values (local plan only; remote values not listed): {listed}"
            )
