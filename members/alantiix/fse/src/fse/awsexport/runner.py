"""Run the export stages in order and render the plan (design §8, §8.7).

plan -> stage, then for a full run: session -> STS account -> bucket ->
sync -> Glue. ``--stage-only`` stops after staging and never calls the
session factory or imports :mod:`fse.awsexport.aws`. ``--dry-run`` writes
nothing, makes only the three allowlisted AWS calls, and prints only lines
that start with ``would ``, ``plan: `` or ``warning: ``.

Any :class:`~fse.awsexport.errors.ExportError` propagates to :mod:`fse.cli`,
which prints it and returns its exit code; a failed superseded delete
therefore stops the run before the Glue stage.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Final

from fse.awsexport import source, staging
from fse.awsexport.options import ExportOptions, default_bucket
from fse.awsexport.tables import TABLES, partition_prefix, staged_dir
from fse.awsexport.transform import MAX_FILE_BYTES
from fse.clock import Clock
from fse.logio.log_writer import LogWriter
from fse.timekit import NS_PER_SECOND

__all__ = ["SessionFactory", "estimate_files", "render_plan", "run_export"]

type SessionFactory = Callable[[str, str], Any]

_GIB: Final = 2**30


def _gib(n: float) -> str:
    return f"{n / _GIB:.2f} GiB"


def estimate_files(plan: source.Plan, rels: set[str] | None = None) -> tuple[int, int]:
    """``(estimated staged bytes, estimated part files)`` for the plan's partitions.

    Uses the measured staged/source byte ratio; a partition needs one part per
    started :data:`~fse.awsexport.transform.MAX_FILE_BYTES`.
    """
    total = files = 0
    for pp in plan.partitions:
        if rels is not None and pp.rel_path not in rels:
            continue
        est = int(pp.source_bytes * staging.STAGED_TO_SOURCE_BYTES)
        total += est
        files += max(1, math.ceil(est / MAX_FILE_BYTES))
    return total, files


def render_plan(plan: source.Plan, writer: LogWriter) -> None:
    """The local plan as ``plan: `` and ``warning: `` lines."""
    writer.echo(
        f"plan: Data_Cache {plan.cache_dir}: {len(plan.partitions)} partitions, "
        f"{plan.window_count} heatmap windows, {plan.bar_session_count} bar sessions, "
        f"{plan.source_bytes} source bytes ({_gib(plan.source_bytes)})"
    )
    for table, counts in plan.skipped.items():
        if any(counts.values()):
            writer.echo(
                f"plan: {table}: skipped {counts.get('incomplete', 0)} incomplete and "
                f"{counts.get('no_data', 0)} no_data entries"
            )
    for table in plan.tables_without_data:
        writer.echo(f"plan: {table}: no data, skipped")
    for warning in plan.warnings:
        writer.echo(f"warning: {warning}")


def run_export(
    options: ExportOptions,
    *,
    writer: LogWriter,
    session_factory: SessionFactory,
    clock: Clock,
) -> int:
    """Run the export for ``options``; 0 on success. Failures raise with an exit code."""
    started = clock.now()
    plan = source.plan(options)
    render_plan(plan, writer)
    report = staging.stage(plan, options.staging_dir, writer=writer, dry_run=options.dry_run)
    if options.dry_run:
        est_bytes, est_files = estimate_files(plan)
        pending_bytes, pending_files = estimate_files(plan, set(report.restaged))
        writer.echo(
            f"plan: staged size estimate for all {len(plan.partitions)} partitions: "
            f"~{est_bytes} bytes ({_gib(est_bytes)}) in ~{est_files} files (staged/source "
            f"ratio {staging.STAGED_TO_SOURCE_BYTES})"
        )
        writer.echo(
            f"would stage {len(report.restaged)} partitions first: {report.pending_source_bytes} "
            f"source bytes, ~{pending_bytes} staged bytes in ~{pending_files} files; "
            f"{len(report.up_to_date)} partitions up to date"
        )
    else:
        writer.echo(
            f"staged {len(report.restaged)} partitions ({report.rows_written} rows, "
            f"{report.bytes_written} bytes); {len(report.up_to_date)} up to date; "
            f"staging directory {options.staging_dir}"
        )
    if options.stage_only:
        return 0
    return _aws_stages(
        options,
        plan,
        report,
        writer=writer,
        session_factory=session_factory,
        started=started,
        clock=clock,
    )


def _aws_stages(
    options: ExportOptions,
    plan: source.Plan,
    report: staging.StageReport,
    *,
    writer: LogWriter,
    session_factory: SessionFactory,
    started: int,
    clock: Clock,
) -> int:
    from fse.awsexport import aws, bucket, glue, sync

    session = session_factory(options.profile, options.region)
    sts = aws.client(session, "sts", dry_run=options.dry_run)
    account = bucket.resolve_account(sts, profile=options.profile)
    name = options.bucket or default_bucket(account, options.region)
    prefix = f"plan: account {account}, region {options.region}, bucket {name}"
    writer.echo(f"{prefix}, key prefix {options.prefix}, Glue database {options.glue_database}")
    s3 = aws.client(session, "s3", dry_run=options.dry_run)
    bucket.ensure_bucket(
        s3,
        bucket=name,
        region=options.region,
        account=account,
        profile=options.profile,
        dry_run=options.dry_run,
        writer=writer,
    )
    glue_client = aws.client(session, "glue", dry_run=options.dry_run)
    if options.dry_run:
        restaged = set(report.restaged)
        for entry in staging.manifest_tables(report.manifest).values():
            for p in entry:
                if p.rel_path in restaged or p.table not in options.tables:
                    continue
                directory = staged_dir(options.staging_dir, p.rel_path)
                key_prefix = partition_prefix(options.prefix, TABLES[p.table], p.value_map)
                for f in p.files:
                    writer.echo(
                        f"would upload {directory / f.name} -> s3://{name}/{key_prefix}{f.name} "
                        f"if changed ({f.size} bytes)"
                    )
        if report.restaged:
            writer.echo(
                f"would stage {len(report.restaged)} partitions first, then upload their parts"
            )
        local: dict[str, set[tuple[tuple[str, str], ...]]] = {}
        for pp in plan.partitions:
            local.setdefault(pp.table, set()).add(pp.values)
        for p in report.manifest.partitions.values():
            if p.table in options.tables:
                local.setdefault(p.table, set()).add(p.values)
        glue.plan_glue(
            glue_client, options=options, account=account, partitions=local, writer=writer
        )
        return 0
    synced = sync.sync_tables(
        s3,
        options=options,
        account=account,
        bucket=name,
        manifest=report.manifest,
        staging_dir=options.staging_dir,
        writer=writer,
    )
    glue.apply_glue(
        glue_client,
        options=options,
        account=account,
        bucket=name,
        manifest=report.manifest,
        remote=synced.remote,
        writer=writer,
    )
    elapsed = (clock.now() - started) / NS_PER_SECOND
    writer.echo(
        f"export done in {elapsed:.0f} s: uploaded {len(synced.uploaded)} files "
        f"({synced.bytes_uploaded} bytes), {len(synced.skipped)} unchanged, "
        f"{len(synced.deleted)} superseded deleted; bucket {name}"
    )
    return 0
