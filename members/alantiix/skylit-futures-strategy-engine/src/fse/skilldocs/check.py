"""The Skill_Document checker behind ``fse skilldocs check`` (design §26).

Each Skill_Document and Revised_Draft is checked for:

=====================  =======================================================  ========
check                  passes when                                              Req
=====================  =======================================================  ========
``front-matter``       one ``---`` block opens on line 1 and closes, and no     26.1
                       second ``---`` block follows it after only blank lines
``yaml-mapping``       the block parses as a YAML mapping                       26.2
``name-description``   ``name`` and ``description`` are strings with at least   26.3
                       one non-whitespace character
``utf-8``              the file is valid UTF-8 and holds no U+FFFD              26.7
``no-secret``          the file holds no Secret_Scanner value                   26.17
=====================  =======================================================  ========

Front matter. The front-matter checks apply where a document carries front
matter (Req 26.1-26.3). The skill files (``SKILL.md``,
``current_working_SKILL_100226.md``, ``revised_SKILL_*.md``) must carry it,
because a skill loader reads ``name`` and ``description`` from it, so a skill
file without a block on line 1 fails ``front-matter``. The task files
(``TASK.md``, ``revised_TASK_*.md``) may omit it. A document whose first
non-blank line is ``---`` but not on line 1 carries front matter in the wrong
place and fails ``front-matter`` either way. When there is no block on line 1
(or it never closes), ``yaml-mapping`` and ``name-description`` are not run.

Lines are the ``\\n``-separated lines of the file, numbered from 1; a
delimiter line is ``---`` with optional trailing whitespace.

Output. A :class:`Failure` holds a check name and a fixed reason with line
numbers at most: never file content, a YAML error text, or a secret value.
Secret matching is :func:`fse.secrets.scanner.contains_secret_value`, the
Secret_Scanner's own rule (UTF-8 bytes of each non-blank Secret_Variable).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import yaml

from fse.secrets.env import EnvView
from fse.secrets.scanner import contains_secret_value
from fse.skilldocs import (
    FINE_TUNE_DIR,
    REVISED_SKILL_GLOB,
    REVISED_TASK_GLOB,
    SKILL_FILE,
    TASK_FILE,
    WORKING_SKILL_FILE,
)

__all__ = [
    "EXIT_FAILED",
    "EXIT_PASSED",
    "EXIT_UNREADABLE",
    "REQUIRED_KEYS",
    "Check",
    "CheckResult",
    "Document",
    "DocumentResult",
    "Failure",
    "SkillDocsError",
    "check_document",
    "check_skill_docs",
    "find_documents",
]

# Design "Exit codes": 2 invalid input (a document fails a check), 4 data or
# I/O failure (a document could not be read).
EXIT_PASSED: Final = 0
EXIT_FAILED: Final = 2
EXIT_UNREADABLE: Final = 4

REQUIRED_KEYS: Final = ("name", "description")

_DELIMITER: Final = "---"
_REPLACEMENT_CHAR: Final = "\ufffd"
_MAX_LINES_SHOWN: Final = 10


class Check(enum.StrEnum):
    """The check names printed by ``fse skilldocs check``, in report order."""

    FRONT_MATTER = "front-matter"
    YAML_MAPPING = "yaml-mapping"
    NAME_DESCRIPTION = "name-description"
    UTF8 = "utf-8"
    NO_SECRET = "no-secret"


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed check. ``reason`` is fixed text plus line numbers; never file content."""

    check: Check
    reason: str


@dataclass(frozen=True, slots=True)
class Document:
    """A Skill_Document or Revised_Draft to check, by path relative to the skill root."""

    path: PurePosixPath
    front_matter_required: bool


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """The outcome for one document: its failures, or why it could not be read."""

    path: PurePosixPath
    failures: tuple[Failure, ...] = ()
    unreadable: str | None = None  # the cause, when the file could not be read

    @property
    def passed(self) -> bool:
        return not self.failures and self.unreadable is None


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one ``fse skilldocs check`` run, documents in check order."""

    root: Path
    documents: tuple[DocumentResult, ...]
    secret_values_configured: bool  # False: the no-secret check had nothing to search for

    @property
    def failed(self) -> tuple[DocumentResult, ...]:
        return tuple(d for d in self.documents if d.failures)

    @property
    def unreadable(self) -> tuple[DocumentResult, ...]:
        return tuple(d for d in self.documents if d.unreadable is not None)

    @property
    def exit_code(self) -> int:
        """2 when a document fails a check, else 4 when one could not be read, else 0."""
        if self.failed:
            return EXIT_FAILED
        if self.unreadable:
            return EXIT_UNREADABLE
        return EXIT_PASSED


class SkillDocsError(Exception):
    """The check could not run. The message names a path or a cause, never content."""

    def __init__(self, message: str, *, exit_code: int = EXIT_UNREADABLE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------- documents


def find_documents(root: Path) -> tuple[Document, ...]:
    """The three Skill_Documents, then each Revised_Draft found, under the skill ``root``.

    Revised_Drafts are the ``revised_SKILL_*.md`` and ``revised_TASK_*.md``
    files in :data:`~fse.skilldocs.FINE_TUNE_DIR`, each set in name order.
    The Skill_Documents are listed whether or not they exist, so a missing
    one is reported.
    """
    fine_tune = PurePosixPath(FINE_TUNE_DIR)
    documents = [
        Document(PurePosixPath(SKILL_FILE), front_matter_required=True),
        Document(fine_tune / WORKING_SKILL_FILE, front_matter_required=True),
        Document(fine_tune / TASK_FILE, front_matter_required=False),
    ]
    for pattern, required in ((REVISED_SKILL_GLOB, True), (REVISED_TASK_GLOB, False)):
        names = sorted(path.name for path in (root / FINE_TUNE_DIR).glob(pattern))
        documents.extend(
            Document(fine_tune / name, front_matter_required=required) for name in names
        )
    return tuple(documents)


def check_skill_docs(root: Path, env: EnvView) -> CheckResult:
    """Check every document :func:`find_documents` lists under ``root``.

    Secret values are ``env.secret_values()``. Raises :class:`SkillDocsError`
    (exit 4) when ``root`` is not a directory.
    """
    if not root.is_dir():
        raise SkillDocsError(f"the Skill_Document folder {root} does not exist")
    values = env.secret_values()
    results = tuple(_check_file(root, document, values) for document in find_documents(root))
    return CheckResult(root=root, documents=results, secret_values_configured=bool(values))


def check_document(
    data: bytes, *, front_matter_required: bool, secret_values: Sequence[str] = ()
) -> tuple[Failure, ...]:
    """Every failed check for one document's bytes, in :class:`Check` order.

    ``front_matter_required``: a document without a block on line 1 fails
    ``front-matter`` (skill files) instead of skipping the front-matter checks
    (task files). An empty ``secret_values`` passes ``no-secret``.
    """
    text, encoding_failure = _decode(data)
    failures = list(_front_matter_failures(text, required=front_matter_required))
    if encoding_failure is not None:
        failures.append(encoding_failure)
    if contains_secret_value(data, secret_values):
        failures.append(Failure(Check.NO_SECRET, "holds a Secret_Scanner value"))
    return tuple(failures)


# ---------------------------------------------------------------- internals


def _check_file(root: Path, document: Document, secret_values: Sequence[str]) -> DocumentResult:
    try:
        data = (root / document.path).read_bytes()
    except OSError as exc:
        return DocumentResult(document.path, unreadable=_read_cause(exc))
    failures = check_document(
        data,
        front_matter_required=document.front_matter_required,
        secret_values=secret_values,
    )
    return DocumentResult(document.path, failures=failures)


def _read_cause(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "file not found"
    if isinstance(exc, IsADirectoryError):
        return "is a directory"
    if isinstance(exc, PermissionError):
        return "permission denied"
    return exc.strerror or type(exc).__name__


def _decode(data: bytes) -> tuple[str, Failure | None]:
    """The text of ``data`` and the ``utf-8`` failure, if any.

    Bytes that are not UTF-8 are decoded with replacement, so the
    front-matter checks still run on the rest of the file.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Report the line only: the exception text holds the offending bytes.
        line = data.count(b"\n", 0, exc.start) + 1
        failure = Failure(Check.UTF8, f"not valid UTF-8 (first invalid byte on line {line})")
        return data.decode("utf-8", "replace"), failure
    lines = [n for n, line in enumerate(text.split("\n"), 1) if _REPLACEMENT_CHAR in line]
    if lines:
        return text, Failure(Check.UTF8, f"holds U+FFFD on {_line_list(lines)}")
    return text, None


def _front_matter_failures(text: str, *, required: bool) -> list[Failure]:
    lines = text.split("\n")
    if not _is_delimiter(lines[0]):
        first = _first_non_blank(lines, 0)
        if first is not None and _is_delimiter(lines[first]):
            reason = f"the front-matter block starts on line {first + 1}, not line 1"
            return [Failure(Check.FRONT_MATTER, reason)]
        if required:
            return [Failure(Check.FRONT_MATTER, "no front-matter block on line 1")]
        return []
    close = next((i for i in range(1, len(lines)) if _is_delimiter(lines[i])), None)
    if close is None:
        reason = "the front-matter block on line 1 has no closing `---` line"
        return [Failure(Check.FRONT_MATTER, reason)]
    failures: list[Failure] = []
    after = _first_non_blank(lines, close + 1)
    if after is not None and _is_delimiter(lines[after]):
        reason = (
            f"a second `---` block starts on line {after + 1}, "
            f"after the block on lines 1-{close + 1}"
        )
        failures.append(Failure(Check.FRONT_MATTER, reason))
    failures.extend(_mapping_failures("\n".join(lines[1:close])))
    return failures


def _mapping_failures(block: str) -> list[Failure]:
    """``yaml-mapping`` and ``name-description`` failures for the YAML between the delimiters."""
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        # The error text quotes the YAML; report only where it is.
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark is not None else ""  # the block starts on line 2
        return [Failure(Check.YAML_MAPPING, f"the front matter is not valid YAML{where}")]
    except Exception:
        # PyYAML raises other errors for some scalars, such as an impossible
        # date (ValueError). Any of them means the block does not parse.
        return [Failure(Check.YAML_MAPPING, "the front matter is not valid YAML")]
    if not isinstance(data, dict):
        kind = "empty" if data is None else ("a list" if isinstance(data, list) else "a scalar")
        return [Failure(Check.YAML_MAPPING, f"the front matter is {kind}, not a YAML mapping")]
    failures: list[Failure] = []
    for key in REQUIRED_KEYS:
        value = data.get(key)
        if key not in data:
            reason = f"the front matter has no `{key}` key"
        elif value is None or (isinstance(value, str) and not value.strip()):
            reason = f"`{key}` is empty"
        elif not isinstance(value, str):
            reason = f"`{key}` is not a string"
        else:
            continue
        failures.append(Failure(Check.NAME_DESCRIPTION, reason))
    return failures


def _is_delimiter(line: str) -> bool:
    return line.rstrip() == _DELIMITER


def _first_non_blank(lines: Sequence[str], start: int) -> int | None:
    return next((i for i in range(start, len(lines)) if lines[i].strip()), None)


def _line_list(lines: Sequence[int]) -> str:
    """``line 7``, ``lines 1, 2, 3``, or the first ten and ``and N more``."""
    if len(lines) == 1:
        return f"line {lines[0]}"
    shown = ", ".join(str(n) for n in lines[:_MAX_LINES_SHOWN])
    rest = len(lines) - _MAX_LINES_SHOWN
    return f"lines {shown}" + (f" and {rest} more" if rest > 0 else "")
