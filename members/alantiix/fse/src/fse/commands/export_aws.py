"""``fse export-aws``: export the Data_Cache to S3 and Glue for Athena and SageMaker.

``fse export-aws [--dry-run | --stage-only] [options]`` runs
:func:`fse.awsexport.runner.run_export`. The cache is read only. Options are
checked before anything is written (exit 2); the account comes from
``sts:GetCallerIdentity`` and is never an option.

This module imports nothing from boto3 or botocore, nor
:mod:`fse.awsexport.aws`, at import time: ``fse`` discovers every command at
start-up, and ``--stage-only`` runs without the ``aws`` extra.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Final

from fse.awsexport.errors import EXIT_DATA_IO
from fse.awsexport.options import (
    DATASETS,
    DEFAULT_GLUE_DATABASE,
    DEFAULT_PREFIX,
    DEFAULT_PROFILE,
    DEFAULT_REGION,
    Mode,
    build_options,
)
from fse.awsexport.runner import run_export
from fse.clock import SystemClock
from fse.commands import CommandContext, SubParsers

__all__ = ["NAME", "register", "run_export_command"]

NAME: Final = "export-aws"

_DESCRIPTION = """\
Export the Data_Cache to a private S3 bucket as compacted, Hive-partitioned
Parquet, with one Glue table per output table (partition projection), so Athena
and SageMaker can query it. The cache is read only.

Stages: plan, stage (into --staging-dir), bucket (create or verify), sync
(upload changed files, delete superseded parts), glue. Each stage is
idempotent: a rerun only redoes work whose inputs changed.

--stage-only makes no AWS call at all. --dry-run writes nothing anywhere and
makes only sts:GetCallerIdentity, s3:HeadBucket and glue:GetDatabase. The bucket
defaults to skylit-fse-data-<account-id>-<region>, with the account from STS.
The bucket holds licensed Skylit API data: keep it private."""

_EPILOG = """\
exit status:
  0    success
  1    internal error (for example a non-read-only call in --dry-run)
  2    invalid input: an option, the staging directory, a bucket in another
       region or without the required settings, a Glue table not created by
       this tool
  3    AWS credentials missing, invalid or expired, profile not found, access
       denied, or the bucket name is owned by another account
  4    data or I/O failure: the cache catalog or a cache file, disk space,
       staging, or an AWS call that failed after retries
  130  interrupted; rerun to resume

Rows of different views are separate strike grids: always filter or group by
view_id (query 0 lists them).

Athena examples:
  -- 0. the views in a month
  SELECT DISTINCT symbol, view_id, view_json
  FROM skylit_fse.heatmaps WHERE month = '2026-10';

  -- 1. GEX per strike on one SPX snapshot minute
  SELECT from_unixtime_nanos(as_of_ns) AT TIME ZONE 'America/New_York' AS t_ny,
         spot, strike, value
  FROM skylit_fse.heatmaps
  CROSS JOIN UNNEST(strikes, strike_values) AS u(strike, value)
  WHERE symbol = 'SPX' AND metric = 'gamma' AND month = '2025-10'
    AND view_id = '29edd099d4c38876' AND session = DATE '2025-10-06'
    AND source_endpoint = 'range'
    AND as_of_ns BETWEEN 1759757400000000000 AND 1759757459999999999;

  -- 2. snapshot coverage per view and session
  SELECT symbol, metric, view_id, session, count(*) AS snapshots
  FROM skylit_fse.heatmaps WHERE month BETWEEN '2025-10' AND '2025-12'
  GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

SageMaker (awswrangler):
  import awswrangler as wr
  df = wr.athena.read_sql_query(
      "SELECT * FROM heatmaps WHERE symbol='SPX' AND metric='gamma' "
      "AND month='2025-10' AND view_id='29edd099d4c38876' "
      "AND session = DATE '2025-10-06'",
      database="skylit_fse", s3_output="s3://<bucket>/athena-results/",
      ctas_approach=False)"""


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="export the Data_Cache to S3 and Glue for Athena and SageMaker",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_argument_group("mode").add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="write nothing; print the planned actions (three read-only AWS calls only)",
    )
    mode.add_argument(
        "--stage-only",
        action="store_true",
        help="plan and stage locally; make no AWS call",
    )

    target = parser.add_argument_group("AWS")
    target.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        metavar="NAME",
        help="AWS CLI profile (default: %(default)s)",
    )
    target.add_argument(
        "--region",
        default=DEFAULT_REGION,
        metavar="REGION",
        help="AWS region (default: %(default)s)",
    )
    target.add_argument(
        "--bucket",
        metavar="NAME",
        help="S3 bucket (default: skylit-fse-data-<account-id>-<region>)",
    )
    target.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        metavar="PREFIX",
        help="key prefix inside the bucket (default: %(default)s)",
    )
    target.add_argument(
        "--glue-database",
        default=DEFAULT_GLUE_DATABASE,
        metavar="NAME",
        help="Glue database (default: %(default)s)",
    )

    select = parser.add_argument_group("selection")
    select.add_argument(
        "--datasets",
        metavar="LIST",
        help=f"comma list of datasets (default: all of {','.join(DATASETS)})",
    )
    select.add_argument(
        "--symbols",
        metavar="LIST",
        help="heatmaps only: comma list of symbols (default: every symbol in the catalog)",
    )
    select.add_argument(
        "--months",
        metavar="LIST",
        help="heatmaps only: comma list of YYYY-MM months (default: all)",
    )

    paths = parser.add_argument_group("paths")
    paths.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="the Data_Cache (default: ~/.skylit-fse/cache)",
    )
    paths.add_argument(
        "--staging-dir",
        type=Path,
        metavar="DIR",
        help="local staging directory, outside the cache (default: ~/.skylit-fse/aws-export)",
    )
    parser.set_defaults(handler=run_export_command, failure_exit_code=EXIT_DATA_IO)


def _session_factory(profile: str, region: str) -> Any:
    """The real session, imported only when an AWS stage runs."""
    from fse.awsexport import aws

    return aws.make_session(profile, region)


def run_export_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    mode: Mode = "dry_run" if args.dry_run else "stage_only" if args.stage_only else "full"
    options = build_options(
        mode=mode,
        profile=args.profile,
        region=args.region,
        bucket=args.bucket,
        prefix=args.prefix,
        glue_database=args.glue_database,
        datasets=args.datasets,
        symbols=args.symbols,
        months=args.months,
        cache_dir=args.cache_dir,
        staging_dir=args.staging_dir,
    )
    return run_export(
        options, writer=ctx.writer, session_factory=_session_factory, clock=SystemClock()
    )
