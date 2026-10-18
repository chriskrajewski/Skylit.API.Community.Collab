"""Expected failures of ``fse export-aws`` (design §9).

Each class carries the design's exit code as ``exit_code``; :mod:`fse.cli`
prints the message as ``error:`` lines and returns the code. The codes are
declared here, not imported from :mod:`fse.cli`, which imports every command.
"""

from __future__ import annotations

from typing import ClassVar, Final

__all__ = [
    "EXIT_ACCESS",
    "EXIT_DATA_IO",
    "EXIT_INTERNAL",
    "EXIT_INVALID_INPUT",
    "DryRunViolation",
    "ExportAccessError",
    "ExportDataError",
    "ExportError",
    "ExportInputError",
]

# Design "Exit codes".
EXIT_INTERNAL: Final = 1
EXIT_INVALID_INPUT: Final = 2
EXIT_ACCESS: Final = 3
EXIT_DATA_IO: Final = 4


class ExportError(Exception):
    """An expected export failure."""

    exit_code: ClassVar[int] = EXIT_DATA_IO


class ExportInputError(ExportError):
    """Invalid input: an option, the staging path, a bucket or table that does not fit."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


class ExportAccessError(ExportError):
    """AWS credentials are missing or expired, or access was denied."""

    exit_code: ClassVar[int] = EXIT_ACCESS


class ExportDataError(ExportError):
    """A data or I/O failure: the cache, staging, or an AWS call that failed."""

    exit_code: ClassVar[int] = EXIT_DATA_IO


class DryRunViolation(ExportError):
    """A dry-run client tried a call outside the read-only allowlist (a bug)."""

    exit_code: ClassVar[int] = EXIT_INTERNAL
