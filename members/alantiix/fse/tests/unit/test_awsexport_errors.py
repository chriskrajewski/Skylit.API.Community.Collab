"""Exit codes of the ``fse export-aws`` errors and their mapping through ``fse.cli``.

**Validates: aws-export design §9**
"""

from __future__ import annotations

import argparse
import io

import pytest

from fse import cli
from fse.awsexport.errors import (
    DryRunViolation,
    ExportAccessError,
    ExportDataError,
    ExportError,
    ExportInputError,
)
from fse.commands import CommandContext, SubParsers


@pytest.mark.parametrize(
    ("cls", "code"),
    [
        (ExportError, 4),
        (ExportInputError, 2),
        (ExportAccessError, 3),
        (ExportDataError, 4),
        (DryRunViolation, 1),
    ],
)
def test_exit_codes(cls: type[ExportError], code: int) -> None:
    assert cls.exit_code == code
    assert issubclass(cls, ExportError)


def test_cli_maps_export_access_error_to_3(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(args: argparse.Namespace, ctx: CommandContext) -> int:
        raise ExportAccessError("denied for profile 'x'")

    def register(subparsers: SubParsers) -> None:
        subparsers.add_parser("boom").set_defaults(handler=handler)

    monkeypatch.setattr(cli, "discover_commands", lambda package=None: [register])
    err = io.StringIO()
    code = cli.run(["boom"], project_dir=None, environ={}, stdout=io.StringIO(), stderr=err)
    assert code == 3
    assert "error: denied for profile 'x'" in err.getvalue()
