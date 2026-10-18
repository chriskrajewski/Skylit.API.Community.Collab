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

Draft lint. A Revised_Draft (``revised_SKILL_*.md``, ``revised_TASK_*.md``)
also gets four checks (design §26, "Template lint"):

========================  ====================================================  ===========
check                     passes when                                           Req
========================  ====================================================  ===========
``narrator-rules``        the draft holds :data:`RESTATEMENT_RULE` and          26.13-26.14
                          :data:`ENGINE_DECIDES_RULE` (whitespace-normalized)
``no-forecast``           no line with a performance figure holds a target or   26.16
                          forecast word (:data:`FORECAST_WORDS`)
``no-trade-instruction``  no sentence starts with a trade verb, and no          26.14
                          sentence about the Narrator (or "you") holds a trade
                          verb without a negation
``figure-provenance``     every line with a performance figure holds a trade    26.15
                          count, a session count, a ``pre-holdout`` or
                          ``Holdout_Period`` label and a config hash, plus a
                          path count for a Combine_Pass probability
========================  ====================================================  ===========

A performance figure line is a line holding a performance term
(:func:`is_figure_line`) and a number that is not part of a date or a hex
hash. Sentences are split per line, so a sentence wrapped over two lines is
checked as two.

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
import re
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
    "DRAFT_CHECKS",
    "ENGINE_DECIDES_RULE",
    "EXIT_FAILED",
    "EXIT_PASSED",
    "EXIT_UNREADABLE",
    "FORECAST_WORDS",
    "REQUIRED_KEYS",
    "RESTATEMENT_RULE",
    "Check",
    "CheckResult",
    "Document",
    "DocumentResult",
    "Failure",
    "SkillDocsError",
    "check_document",
    "check_skill_docs",
    "draft_failures",
    "find_documents",
    "is_figure_line",
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

RESTATEMENT_RULE: Final = (
    "The Narrator restates Finding_Card data as prose and adds no level, price, Grade or "
    "performance figure that the Finding_Card does not contain."
)
"""The Narrator restatement rule every Revised_Draft holds (Req 26.13)."""

ENGINE_DECIDES_RULE: Final = (
    "The Strategy_Engine makes every entry and exit decision. The Narrator does not take, "
    "skip, size or exit a trade, does not place, modify or cancel an order, and does not "
    "change the Order_Mode."
)
"""The engine-decides rule every Revised_Draft holds (Req 26.14)."""

FORECAST_WORDS: Final[tuple[str, ...]] = (
    "target", "targets", "targeted", "targeting",
    "expect", "expects", "expected", "expecting", "expectation", "expectations",
    "will", "forecast", "forecasts", "forecasted", "predict", "predicts", "predicted",
)  # fmt: skip
"""Words a performance figure line must not hold (design §26 lint, Req 26.16)."""


class Check(enum.StrEnum):
    """The check names printed by ``fse skilldocs check``, in report order."""

    FRONT_MATTER = "front-matter"
    YAML_MAPPING = "yaml-mapping"
    NAME_DESCRIPTION = "name-description"
    UTF8 = "utf-8"
    NO_SECRET = "no-secret"
    NARRATOR_RULES = "narrator-rules"
    NO_FORECAST = "no-forecast"
    NO_TRADE_INSTRUCTION = "no-trade-instruction"
    FIGURE_PROVENANCE = "figure-provenance"


DRAFT_CHECKS: Final[tuple[Check, ...]] = (
    Check.NARRATOR_RULES,
    Check.NO_FORECAST,
    Check.NO_TRADE_INSTRUCTION,
    Check.FIGURE_PROVENANCE,
)
"""The checks only a Revised_Draft gets."""


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed check. ``reason`` is fixed text plus line numbers; never file content."""

    check: Check
    reason: str


@dataclass(frozen=True, slots=True)
class Document:
    """A Skill_Document or Revised_Draft (``draft``), by path relative to the skill root."""

    path: PurePosixPath
    front_matter_required: bool
    draft: bool = False


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
            Document(fine_tune / name, front_matter_required=required, draft=True) for name in names
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
    data: bytes,
    *,
    front_matter_required: bool,
    secret_values: Sequence[str] = (),
    draft: bool = False,
) -> tuple[Failure, ...]:
    """Every failed check for one document's bytes, in :class:`Check` order.

    ``front_matter_required``: a document without a block on line 1 fails
    ``front-matter`` (skill files) instead of skipping the front-matter checks
    (task files). An empty ``secret_values`` passes ``no-secret``. ``draft``
    adds the draft lint (:func:`draft_failures`).
    """
    text, encoding_failure = _decode(data)
    failures = list(_front_matter_failures(text, required=front_matter_required))
    if encoding_failure is not None:
        failures.append(encoding_failure)
    if contains_secret_value(data, secret_values):
        failures.append(Failure(Check.NO_SECRET, "holds a Secret_Scanner value"))
    if draft:
        failures.extend(draft_failures(text))
    return tuple(failures)


# ---------------------------------------------------------------- draft lint

# Performance terms (Req 26.15 list). A term must not touch a letter, digit or
# underscore on either side, so Strategy_Config key paths such as
# `kill_switches.daily_profit_cap` or `reporting.primary_win_rate` are not terms.
_TERM_EDGE: Final = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"
_TERMS_ANY_CASE: Final = (
    r"win[ -]rates?", r"expectancy", r"trades per (?:session|day)", r"p&l", r"pnl",
    r"drawdowns?", r"pass probabilit(?:y|ies)", r"profit factor",
)  # fmt: skip
_TERMS_EXACT_CASE: Final = (r"R_Multiples?", r"Primary_Win_Rate", r"Combine_Pass")
_FIGURE_TERM: Final = re.compile(
    "|".join(
        [f"(?i:{_TERM_EDGE.format(t)})" for t in _TERMS_ANY_CASE]
        + [_TERM_EDGE.format(t) for t in _TERMS_EXACT_CASE]
    )
)
_PASS_TERM: Final = re.compile(r"(?i:pass probabilit)|Combine_Pass")
_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_HEX_HASH: Final = re.compile(r"(?<![A-Za-z0-9_])[0-9a-f]{12,}(?![A-Za-z0-9_])")
_NUMBER: Final = re.compile(r"(?<![A-Za-z0-9_.])[+\-\u2212]?\$?\d")
_FORECAST: Final = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:" + "|".join(FORECAST_WORDS) + r")(?![A-Za-z0-9_])"
)
_PROVENANCE: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("trade count", re.compile(r"(?<![\w.])\d[\d,]* trades?\b")),
    ("session count", re.compile(r"(?<![\w.])\d[\d,]* sessions?\b")),
    ("pre-holdout or Holdout_Period label", re.compile(r"\bpre-holdout\b|\bHoldout_Period\b")),
    ("config hash", re.compile(r"\bconfig hash [0-9a-f]{8,}")),
)
_PATHS: Final = re.compile(r"(?<![\w.])\d[\d,]* (?:Monte_Carlo_Simulator )?paths\b")

# Trade instructions (Req 26.14). A sentence starting with one of these verbs
# (after list markers and the adverbs in _LEAD_WORDS) is an imperative trade
# instruction; so is a sentence about the Narrator or "you" that holds one of
# _NARRATOR_VERBS (any form) and no word of _NEGATIONS.
_LEADING_VERBS: Final = frozenset(
    {
        "take", "skip", "size", "exit", "enter", "place", "modify", "cancel", "buy", "sell",
        "short", "long", "flatten", "close", "scale", "add", "hold", "fade", "cover",
        "trail", "reverse", "arm", "chase",
    }
)  # fmt: skip
_LEAD_WORDS: Final = frozenset(
    {"always", "never", "now", "then", "just", "please", "do", "don't", "dont", "not",
     "immediately", "simply", "also", "and", "or", "so", "only"}
)  # fmt: skip
_NARRATOR_VERBS: Final = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:take|takes|taking|took|skip|skips|skipping|skipped|size|sizes|"
    r"sizing|sized|exit|exits|exiting|exited|enter|enters|entering|entered|place|places|"
    r"placing|placed|modify|modifies|modifying|modified|cancel|cancels|cancelling|canceling|"
    r"cancelled|canceled|buy|buys|buying|bought|sell|sells|selling|sold|flatten|flattens|"
    r"flattening|flattened|scale|scales|scaling|scaled|fade|fades|fading|faded|cover|covers|"
    r"covering|covered|trail|trails|trailing|trailed|arm|arms|arming|armed|chase|chases|"
    r"chasing|chased)(?![A-Za-z0-9_])"
)
_CHANGE_MODE: Final = re.compile(r"(?i)\b(?:change|changes|changing|set|sets|switch)\b.*Order_Mode")
_ADDRESSEE: Final = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:narrator|you|your)(?![A-Za-z0-9_])")
_NEGATIONS: Final = frozenset(
    {"not", "never", "no", "nothing", "neither", "nor", "without", "cannot"}
)
_LINE_MARKERS: Final = re.compile(r"^\s*(?:(?:[-*+>|#]+|\d+[.)])\s*)*")
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[.!?:;])\s+|\s*\|\s*")
_SENTENCE_LEAD: Final = re.compile(r"^[\s*_`\"'\u201c\u201d\u2018\u2019(\[]+")
_WORD: Final = re.compile(r"[A-Za-z][A-Za-z_']*")


def is_figure_line(line: str) -> bool:
    """Whether ``line`` holds a performance figure: a term and a number (module notes)."""
    if not _FIGURE_TERM.search(line):
        return False
    stripped = _HEX_HASH.sub(" ", _DATE.sub(" ", line))
    return _NUMBER.search(stripped) is not None


def draft_failures(text: str) -> tuple[Failure, ...]:
    """The draft lint failures of a Revised_Draft's ``text`` (module notes)."""
    failures: list[Failure] = []
    flat = " ".join(text.split())
    missing = [
        label
        for label, rule in (
            ("the Narrator restatement rule", RESTATEMENT_RULE),
            ("the engine-decides rule", ENGINE_DECIDES_RULE),
        )
        if rule not in flat
    ]
    if missing:
        failures.append(Failure(Check.NARRATOR_RULES, f"lacks {' and '.join(missing)}"))
    lines = text.split("\n")
    figures = [(n, line) for n, line in enumerate(lines, 1) if is_figure_line(line)]
    forecast = [n for n, line in figures if _FORECAST.search(line)]
    if forecast:
        reason = f"a target or forecast word next to a performance figure on {_line_list(forecast)}"
        failures.append(Failure(Check.NO_FORECAST, reason))
    instructions = [n for n, line in enumerate(lines, 1) if _has_trade_instruction(line)]
    if instructions:
        reason = f"a trade instruction on {_line_list(instructions)}"
        failures.append(Failure(Check.NO_TRADE_INSTRUCTION, reason))
    bare = [n for n, line in figures if _lacks_provenance(line)]
    if bare:
        reason = (
            "a performance figure without its trade count, session count, labeled dates, "
            f"config hash or path count on {_line_list(bare)}"
        )
        failures.append(Failure(Check.FIGURE_PROVENANCE, reason))
    return tuple(failures)


def _lacks_provenance(line: str) -> bool:
    if any(pattern.search(line) is None for _, pattern in _PROVENANCE):
        return True
    return _PASS_TERM.search(line) is not None and _PATHS.search(line) is None


def _has_trade_instruction(line: str) -> bool:
    body = _LINE_MARKERS.sub("", line)
    for sentence in _SENTENCE_SPLIT.split(body):
        sentence = _SENTENCE_LEAD.sub("", sentence)
        if not sentence:
            continue
        words = [w.lower() for w in _WORD.findall(sentence)]
        lead = next((w for w in words if w not in _LEAD_WORDS), None)
        if lead in _LEADING_VERBS:
            return True
        if lead in {"go", "going"} and {"long", "short"} & set(words[:4]):
            return True
        if lead == "change" and "order_mode" in words[:6]:
            return True
        addressed = _ADDRESSEE.search(sentence) and not _NEGATIONS & set(words)
        if addressed and (_NARRATOR_VERBS.search(sentence) or _CHANGE_MODE.search(sentence)):
            return True
    return False


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
        draft=document.draft,
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
