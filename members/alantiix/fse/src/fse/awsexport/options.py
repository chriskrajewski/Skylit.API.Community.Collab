"""``fse export-aws`` options: defaults and syntax checks (design §7, review NIT 6).

:func:`build_options` turns the raw command-line values into a frozen
:class:`ExportOptions`. Every check here is syntax only and raises
:class:`~fse.awsexport.errors.ExportInputError` (exit 2) before anything is
written; the staging directory also goes through the path guard
(``PathGuardError``, exit 2). Checks that need the catalog (the
``--cache-dir`` schema version, the ``--symbols`` typo guard) run in
:func:`fse.awsexport.source.plan`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from fse.awsexport.errors import ExportInputError
from fse.awsexport.tables import MONTH_RE, TABLES
from fse.data.cache_io import check_component
from fse.data.path_guard import check_output_dir, resolve_output_dir
from fse.settings import default_paths

__all__ = [
    "DATASETS",
    "DEFAULT_GLUE_DATABASE",
    "DEFAULT_PREFIX",
    "DEFAULT_PROFILE",
    "DEFAULT_REGION",
    "STAGING_DIR_NAME",
    "ExportOptions",
    "Mode",
    "build_options",
    "check_bucket",
    "check_glue_database",
    "check_prefix",
    "check_profile",
    "check_region",
    "default_bucket",
    "default_cache_dir",
    "default_staging_dir",
    "parse_datasets",
    "parse_months",
    "parse_symbols",
]

type Mode = Literal["full", "dry_run", "stage_only"]

# --datasets names, in registry order. darkpool covers two tables.
DATASETS: Final = ("heatmaps", "bars", "vix_bars", "vix_daily", "darkpool", "regime_medians")

DEFAULT_PROFILE: Final = "default"
DEFAULT_REGION: Final = "us-east-1"
DEFAULT_PREFIX: Final = "fse"
DEFAULT_GLUE_DATABASE: Final = "skylit_fse"
STAGING_DIR_NAME: Final = "aws-export"

_REGION_RE: Final = re.compile(r"[a-z]{2}(-[a-z]+)+-\d{1,2}")
_BUCKET_RE: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
_BUCKET_BAD_PREFIXES: Final = ("xn--", "sthree-")
_BUCKET_BAD_SUFFIXES: Final = ("-s3alias", "--ol-s3", "--x-s3", "--table-s3")
_PREFIX_RE: Final = re.compile(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*")
_PREFIX_MAX: Final = 200
_GLUE_DB_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,254}")


def default_cache_dir() -> Path:
    return default_paths().cache


def default_staging_dir() -> Path:
    return default_paths().root / STAGING_DIR_NAME


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """Validated options of one export run."""

    mode: Mode
    profile: str
    region: str
    bucket: str | None  # None: derived from STS after the session exists
    prefix: str  # normalized, no leading or trailing '/'
    glue_database: str
    datasets: tuple[str, ...]  # registry order
    symbols: frozenset[str] | None
    months: frozenset[str] | None
    cache_dir: Path  # resolved
    staging_dir: Path  # resolved, path-guarded

    @property
    def dry_run(self) -> bool:
        return self.mode == "dry_run"

    @property
    def stage_only(self) -> bool:
        return self.mode == "stage_only"

    @property
    def tables(self) -> tuple[str, ...]:
        """The selected output tables, in registry order."""
        return tuple(name for name, spec in TABLES.items() if spec.dataset in self.datasets)


# ---------------------------------------------------------------- validators


def _bad(option: str, value: object, rule: str) -> ExportInputError:
    return ExportInputError(f"{option} {value!r}: {rule}")


def check_profile(value: str) -> str:
    if not value or re.search(r"\s", value):
        raise _bad("--profile", value, "must be non-blank and contain no whitespace")
    return value


def check_region(value: str) -> str:
    if _REGION_RE.fullmatch(value) is None:
        raise _bad("--region", value, "is not an AWS region name such as us-east-1")
    return value


def check_bucket(value: str) -> str:
    if _BUCKET_RE.fullmatch(value) is None:
        raise _bad(
            "--bucket",
            value,
            "must be 3-63 lowercase letters, digits and hyphens, starting and ending with a "
            "letter or digit (no dots)",
        )
    if value.startswith(_BUCKET_BAD_PREFIXES) or value.endswith(_BUCKET_BAD_SUFFIXES):
        raise _bad("--bucket", value, "uses a prefix or suffix that S3 reserves")
    return value


def check_prefix(value: str) -> str:
    """The prefix with leading and trailing ``/`` stripped, if valid."""
    stripped = value.strip("/")
    if (
        not stripped
        or len(stripped) > _PREFIX_MAX
        or _PREFIX_RE.fullmatch(stripped) is None
        or ".." in stripped.split("/")
    ):
        raise _bad(
            "--prefix",
            value,
            f"must be 1-{_PREFIX_MAX} characters of lowercase letters, digits, '.', '_', '-' "
            "in '/'-separated segments, each starting with a letter or digit",
        )
    return stripped


def check_glue_database(value: str) -> str:
    if _GLUE_DB_RE.fullmatch(value) is None:
        raise _bad(
            "--glue-database",
            value,
            "must start with a lowercase letter and hold only lowercase letters, digits and '_'",
        )
    return value


def _items(option: str, text: str) -> list[str]:
    items = [item.strip() for item in text.split(",")]
    if not items or any(not item for item in items):
        raise _bad(option, text, "is not a comma-separated list")
    if len(set(items)) != len(items):
        raise _bad(option, text, "lists a value more than once")
    return items


def parse_datasets(text: str | None) -> tuple[str, ...]:
    """The datasets in registry order; all six when ``text`` is ``None``."""
    if text is None:
        return DATASETS
    items = _items("--datasets", text)
    unknown = [item for item in items if item not in DATASETS]
    if unknown:
        raise _bad("--datasets", text, f"unknown {unknown}; valid: {', '.join(DATASETS)}")
    return tuple(name for name in DATASETS if name in items)


def parse_symbols(text: str | None) -> frozenset[str] | None:
    if text is None:
        return None
    items = _items("--symbols", text)
    for item in items:
        try:
            check_component("symbol", item)
        except ValueError as exc:
            raise _bad("--symbols", text, str(exc)) from None
    return frozenset(items)


def parse_months(text: str | None) -> frozenset[str] | None:
    if text is None:
        return None
    items = _items("--months", text)
    bad = [item for item in items if MONTH_RE.fullmatch(item) is None]
    if bad:
        raise _bad("--months", text, f"{bad} are not YYYY-MM months")
    return frozenset(items)


def default_bucket(account: str, region: str) -> str:
    """``skylit-fse-data-{account}-{region}``, the default bucket name."""
    return check_bucket(f"skylit-fse-data-{account}-{check_region(region)}")


def _check_staging(staging_dir: Path, cache_dir: Path) -> Path:
    staging = check_output_dir(staging_dir, label="export staging directory")
    if staging.is_relative_to(cache_dir) or cache_dir.is_relative_to(staging):
        raise ExportInputError(
            f"export staging directory {staging} overlaps the Data_Cache {cache_dir}; "
            "use a directory outside the cache. No file was written."
        )
    return staging


def build_options(
    *,
    mode: Mode,
    profile: str = DEFAULT_PROFILE,
    region: str = DEFAULT_REGION,
    bucket: str | None = None,
    prefix: str = DEFAULT_PREFIX,
    glue_database: str = DEFAULT_GLUE_DATABASE,
    datasets: str | Sequence[str] | None = None,
    symbols: str | None = None,
    months: str | None = None,
    cache_dir: Path | None = None,
    staging_dir: Path | None = None,
) -> ExportOptions:
    """Validated :class:`ExportOptions`; ``ExportInputError`` (exit 2) on a bad value."""
    if mode not in ("full", "dry_run", "stage_only"):
        raise ExportInputError(f"unknown export mode {mode!r}")
    if datasets is not None and not isinstance(datasets, str):
        datasets = ",".join(datasets)
    checked_profile = check_profile(profile)
    checked_region = check_region(region)
    checked_prefix = check_prefix(prefix)
    checked_database = check_glue_database(glue_database)
    checked_bucket = None if bucket is None else check_bucket(bucket)
    checked_datasets = parse_datasets(datasets)
    checked_symbols = parse_symbols(symbols)
    checked_months = parse_months(months)
    cache = resolve_output_dir(default_cache_dir() if cache_dir is None else cache_dir)
    # Last: the path guard runs git.
    staging = _check_staging(default_staging_dir() if staging_dir is None else staging_dir, cache)
    return ExportOptions(
        mode=mode,
        profile=checked_profile,
        region=checked_region,
        bucket=checked_bucket,
        prefix=checked_prefix,
        glue_database=checked_database,
        datasets=checked_datasets,
        symbols=checked_symbols,
        months=checked_months,
        cache_dir=cache,
        staging_dir=staging,
    )
