"""``fse skilldocs check``: check the Skill_Documents and Revised_Drafts (design §26).

Output. stdout holds one line per failed check: ``<path>: <check>: <reason>``,
the path relative to the skill root. stderr holds fixed status lines (counts,
documents that could not be read). No line holds file content or a secret
value; every line also passes through the Log_Writer's Redactor.

Secret values come from the shell environment and the Project ``.env``, like
``fse scan-secrets``.
"""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath

from fse.commands import CommandContext, SubParsers
from fse.logio.log_writer import LogWriter
from fse.skilldocs import SKILL_ROOT_NAME, default_skill_root
from fse.skilldocs.check import (
    EXIT_FAILED,
    CheckResult,
    SkillDocsError,
    check_skill_docs,
)

__all__ = ["NAME", "register", "report", "run_check"]

NAME = "skilldocs"
_LABEL = f"{NAME} check"

_CHECK_DESCRIPTION = """\
Check each Skill_Document (SKILL.md, current_working_SKILL_100226.md, TASK.md)
and each Revised_Draft (revised_SKILL_*.md, revised_TASK_*.md):

  front-matter      one --- block opens on line 1 and closes, and no second
                    --- block follows it (skill files must have the block;
                    task files may omit it)
  yaml-mapping      the block parses as a YAML mapping
  name-description  name and description are non-empty strings
  utf-8             the file is valid UTF-8 with no U+FFFD
  no-secret         the file holds no Secret_Variable value

Each Revised_Draft also gets the draft lint:

  narrator-rules        it holds the Narrator restatement rule and the
                        engine-decides rule
  no-forecast           no line with a performance figure holds "target",
                        "expect", "will" or another forecast word
  no-trade-instruction  no sentence tells the Narrator to take, skip, size
                        or exit a trade, to place, modify or cancel an
                        order, or to change the Order_Mode
  figure-provenance     every performance figure line holds its trade
                        count, session count, labeled dates and config hash
                        (and the path count for a Combine_Pass probability)

Each failed check is printed as "<path>: <check>: <reason>". No file content
and no secret value is ever printed. Secret values are read from the shell
environment and from the .env file in the Project folder."""

_CHECK_EPILOG = """\
exit status:
  0  every document passes every check
  2  one or more documents fail a check (lines on stdout)
  4  a document or the skill folder could not be read"""


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="check the Skill_Documents and Revised_Drafts",
        description="Skill_Document and Revised_Draft tools.",
    )
    actions = parser.add_subparsers(
        dest="skilldocs_action", metavar="<action>", title="actions", required=True
    )
    check = actions.add_parser(
        "check",
        help="check front matter, encoding and secret values",
        description=_CHECK_DESCRIPTION,
        epilog=_CHECK_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check.add_argument(
        "--skill-root",
        type=Path,
        metavar="DIR",
        help=f"the folder that holds SKILL.md (default: ../{SKILL_ROOT_NAME} next to the Project)",
    )
    check.set_defaults(handler=run_check)


def run_check(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Check, print the result, and return 0, 2 or 4.

    A :class:`~fse.skilldocs.check.SkillDocsError` propagates to the CLI,
    which prints its cause and exits with its ``exit_code``.
    """
    given: Path | None = args.skill_root
    if given is not None:
        root = given
    elif ctx.project_dir is not None:
        root = default_skill_root(ctx.project_dir)
    else:
        raise SkillDocsError(
            "the Project folder was not found; pass --skill-root DIR", exit_code=EXIT_FAILED
        )
    return report(check_skill_docs(root, ctx.env), ctx.writer)


def report(result: CheckResult, writer: LogWriter) -> int:
    """Print ``result``: failed checks on stdout, status lines on stderr. Returns the status."""
    for document in result.documents:
        for failure in document.failures:
            writer.echo(f"{_shown(document.path)}: {failure.check}: {failure.reason}")
    for document in result.unreadable:
        writer.error(f"error: cannot read {_shown(document.path)}: {document.unreadable}")
    if not result.secret_values_configured:
        writer.error(
            f"{_LABEL}: no Secret_Variable value is configured, "
            "so no-secret had no value to search for"
        )
    total = len(result.documents)
    if result.failed:
        writer.error(
            f"{_LABEL}: {len(result.failed)} of {_documents(total)} in {result.root} fail a check"
        )
    elif result.unreadable:
        writer.error(
            f"error: {_LABEL} incomplete: {len(result.unreadable)} of "
            f"{_documents(total)} in {result.root} could not be read"
        )
    else:
        verb = "passes" if total == 1 else "pass"
        writer.error(f"{_LABEL}: {_documents(total)} in {result.root} {verb} every check")
    return result.exit_code


def _shown(path: PurePosixPath) -> str:
    """``path`` on one line: a path with a control character is printed escaped."""
    text = str(path)
    return text if text.isprintable() else repr(text)


def _documents(count: int) -> str:
    return f"{count} document{'' if count == 1 else 's'}"
