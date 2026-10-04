"""The one-time fixes to the Skill_Documents (design §26, Req 26.4-26.6, 26.8).

======================================  ============================================  ====
document and line                       fix                                           Req
======================================  ============================================  ====
``current_working_SKILL_100226.md``     the stacked front-matter blocks become one    26.4
                                        block holding the union of their keys, a
                                        later block's value winning
``SKILL.md`` line 107                   each ``?`` between steps becomes ``→``        26.5
``SKILL.md`` line 144                   ``?3:1`` becomes ``≥3:1``                     26.6
``SKILL.md`` line 154                   each ``?`` between chain steps becomes ``→``  26.5
======================================  ============================================  ====

Only those spans change (Req 26.8). :func:`fix_text` checks its own output:
every line outside the front matter is unchanged except the listed lines,
and on a listed line only the ``?`` characters of the listed spans differ.
Other question marks, such as ``SKILL.md`` lines 59 and 61, stay as they are.

Merge. Each top-level key of the merged block keeps its source lines
verbatim from the block whose value wins, so for the two stacked blocks of
``current_working_SKILL_100226.md`` (both hold ``name`` and ``description``)
the merged block is the second block, byte for byte. The merged block must
parse to the union of the stacked mappings, or the fixer refuses.

Idempotent. A listed line that already shows the replacement is left alone,
and a document with one front-matter block has nothing to merge, so a second
run changes nothing. A listed line that matches neither its before nor its
after form stops the run before any file is written.

:func:`apply_fixes` runs :func:`fse.skilldocs.check.check_document` on each
fixed document before writing. Run once with::

    python -m fse.skilldocs.fixes [--skill-root DIR] [--dry-run]

No message holds document content: only paths, line numbers, keys and the
fix spans defined here.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TextIO

import yaml

from fse.logio.log_writer import LogWriter
from fse.secrets.env import EnvView
from fse.settings import project_dir as find_project_dir
from fse.skilldocs import FINE_TUNE_DIR, SKILL_FILE, WORKING_SKILL_FILE, default_skill_root
from fse.skilldocs.check import (
    EXIT_FAILED,
    EXIT_PASSED,
    EXIT_UNREADABLE,
    SkillDocsError,
    check_document,
)

__all__ = [
    "PLAN",
    "SKILL_MD_FIXES",
    "CharFix",
    "FileFix",
    "FilePlan",
    "FixError",
    "Merge",
    "TextFix",
    "apply_fixes",
    "describe",
    "fix_text",
    "main",
]

_DELIMITER: Final = "---"  # the same delimiter rule as fse.skilldocs.check
_QUESTION: Final = "?"


class FixError(SkillDocsError):
    """A document is not in the before or after form the fixes expect. Nothing is written."""

    def __init__(self, message: str, *, exit_code: int = EXIT_FAILED) -> None:
        super().__init__(message, exit_code=exit_code)


@dataclass(frozen=True, slots=True)
class CharFix:
    """Replace the ``?`` in every ``old`` span on one line with one character.

    ``old`` holds exactly one ``?`` and ``new`` is ``old`` with that ``?``
    replaced, so every other character of the line stays where it is. Before
    the fix the line holds ``count`` ``old`` spans; after it, no ``old`` span
    and ``count`` ``new`` spans.
    """

    line: int  # 1-based, in the document as read
    old: str
    new: str
    count: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.count < 1:
            raise ValueError("a CharFix needs line >= 1 and count >= 1")
        if self.old.count(_QUESTION) != 1:
            raise ValueError("a CharFix `old` span holds exactly one `?`")
        at = self.old.index(_QUESTION)
        if (
            len(self.new) != len(self.old)
            or self.new[at] == _QUESTION
            or self.new != self.old.replace(_QUESTION, self.new[at])
        ):
            raise ValueError("a CharFix `new` span is `old` with only its `?` replaced")

    @property
    def replacement(self) -> str:
        """The character that takes the place of each ``?``."""
        return self.new[self.old.index(_QUESTION)]


# The SKILL.md fixes (Req 26.5, 26.6). Each `?` between steps has a space on
# both sides; a real question mark ends a word and is not matched.
SKILL_MD_FIXES: Final = (
    CharFix(107, " ? ", " \u2192 ", 3),  # Fresh → tested → delivered → decaying.
    CharFix(144, "?3:1", "\u22653:1", 1),  # ≥3:1
    CharFix(154, " ? ", " \u2192 ", 6),  # the "How to speak a read" chain
)


@dataclass(frozen=True, slots=True)
class FilePlan:
    """The fixes for one document, by path relative to the skill root."""

    path: PurePosixPath
    merge_front_matter: bool = False
    char_fixes: tuple[CharFix, ...] = ()


PLAN: Final = (
    FilePlan(PurePosixPath(FINE_TUNE_DIR) / WORKING_SKILL_FILE, merge_front_matter=True),
    FilePlan(PurePosixPath(SKILL_FILE), char_fixes=SKILL_MD_FIXES),
)


@dataclass(frozen=True, slots=True)
class Merge:
    """Stacked front-matter blocks replaced by one block."""

    first_line: int  # 1-based lines of the stacked blocks, as read
    last_line: int
    blocks: int  # how many blocks were merged
    merged_lines: int  # lines in the merged block, both delimiters included
    keys: tuple[str, ...]  # the merged keys, in order


@dataclass(frozen=True, slots=True)
class TextFix:
    """The result of :func:`fix_text`."""

    text: str
    merge: Merge | None
    applied: tuple[CharFix, ...]
    already_applied: tuple[CharFix, ...]

    @property
    def changed(self) -> bool:
        return self.merge is not None or bool(self.applied)


@dataclass(frozen=True, slots=True)
class FileFix:
    """What :func:`apply_fixes` changed, or found already fixed, in one document."""

    path: PurePosixPath
    merge: Merge | None
    applied: tuple[CharFix, ...]
    already_applied: tuple[CharFix, ...]

    @property
    def changed(self) -> bool:
        return self.merge is not None or bool(self.applied)


# ---------------------------------------------------------------- text


def fix_text(
    text: str, char_fixes: Sequence[CharFix] = (), *, merge_front_matter: bool = True
) -> TextFix:
    """Apply ``char_fixes`` and, if asked, merge stacked front-matter blocks.

    Line numbers in ``char_fixes`` refer to ``text`` and must lie after its
    front matter. Raises :class:`FixError` when a listed line holds neither
    its before nor its after form, when the blocks cannot be merged
    faithfully, or when the output differs from ``text`` anywhere else.
    """
    lines = text.split("\n")
    blocks = _front_matter_blocks(lines)
    front_end = blocks[-1][1] if blocks else -1
    fixes = _fixes_by_index(char_fixes, len(lines), front_end)

    new_lines = list(lines)
    applied: list[CharFix] = []
    already: list[CharFix] = []
    for index, fix in sorted(fixes.items()):
        line = lines[index]
        found = line.count(fix.old)
        if found == fix.count:
            new_lines[index] = line.replace(fix.old, fix.new)
            applied.append(fix)
        elif found == 0 and line.count(fix.new) == fix.count:
            already.append(fix)
        else:
            raise FixError(
                f"line {fix.line}: expected {fix.count} {fix.old!r} spans to fix "
                f"or {fix.count} {fix.new!r} spans already fixed, found {found} {fix.old!r}"
            )

    merge: Merge | None = None
    head_before = head_after = 0
    if merge_front_matter and len(blocks) > 1:
        merged, keys = _merge_blocks(lines, blocks)
        head_before, head_after = front_end + 1, len(merged)
        new_lines[:head_before] = merged
        merge = Merge(1, head_before, len(blocks), head_after, keys)

    _assert_only_targets_changed(
        lines, new_lines, head_before, head_after, {fix.line - 1: fix for fix in applied}
    )
    return TextFix("\n".join(new_lines), merge, tuple(applied), tuple(already))


def _front_matter_blocks(lines: Sequence[str]) -> list[tuple[int, int]]:
    """``(open, close)`` line indexes of the block on line 1 and each block stacked on it.

    A block is stacked when only blank lines separate it from the one before,
    the same rule as ``fse skilldocs check``. An unclosed block on line 1
    gives no blocks (the checker reports it); an unclosed stacked block raises.
    """
    if not lines or not _is_delimiter(lines[0]):
        return []
    blocks: list[tuple[int, int]] = []
    start = 0
    while True:
        close = next((i for i in range(start + 1, len(lines)) if _is_delimiter(lines[i])), None)
        if close is None:
            if blocks:
                raise FixError(f"the stacked `---` block on line {start + 1} never closes")
            return []
        blocks.append((start, close))
        after = next((i for i in range(close + 1, len(lines)) if lines[i].strip()), None)
        if after is None or not _is_delimiter(lines[after]):
            return blocks
        start = after


def _fixes_by_index(
    char_fixes: Sequence[CharFix], line_count: int, front_end: int
) -> dict[int, CharFix]:
    fixes: dict[int, CharFix] = {}
    for fix in char_fixes:
        index = fix.line - 1
        if index in fixes:
            raise ValueError(f"two CharFix entries for line {fix.line}")
        if index >= line_count:
            raise FixError(f"line {fix.line}: the document has only {line_count} lines")
        if index <= front_end:
            raise FixError(f"line {fix.line}: inside the front matter (lines 1-{front_end + 1})")
        fixes[index] = fix
    return fixes


def _merge_blocks(
    lines: Sequence[str], blocks: Sequence[tuple[int, int]]
) -> tuple[list[str], tuple[str, ...]]:
    """One block for ``blocks``: the union of keys, a later block's lines winning."""
    expected: dict[object, object] = {}
    chosen: dict[object, list[str]] = {}
    for open_, close in blocks:
        where = f"the front-matter block on lines {open_ + 1}-{close + 1}"
        body = list(lines[open_ + 1 : close])
        mapping = _parse_mapping(body, where)
        for key, segment in _key_segments(body, open_ + 2, mapping, where):
            chosen[key] = segment  # a repeated key keeps its first position
        expected.update(mapping)
    merged = [lines[blocks[0][0]], *(line for seg in chosen.values() for line in seg)]
    merged.append(lines[blocks[-1][1]])
    if _parse_mapping(merged[1:-1], "the merged front-matter block") != expected:
        raise FixError("the merged front-matter block does not parse to the union of the blocks")
    return merged, tuple(str(key) for key in chosen)


def _key_segments(
    body: Sequence[str], first_line: int, mapping: dict[object, object], where: str
) -> list[tuple[object, list[str]]]:
    """Each top-level key of a block with its source lines, checked against ``mapping``.

    A segment is a line that starts a key (no indent, not a comment) plus
    every line up to the next one. ``first_line`` is the 1-based number of
    ``body[0]``.
    """
    groups: list[list[str]] = []
    for offset, line in enumerate(body):
        if line[:1].strip() and not line.startswith("#"):
            groups.append([line])
        elif groups:
            groups[-1].append(line)
        elif line.strip():
            raise FixError(f"line {first_line + offset}: text before the first key of {where}")
    segments: list[tuple[object, list[str]]] = []
    values: dict[object, object] = {}
    for group in groups:
        parsed = _parse_mapping(group, where)
        if len(parsed) != 1:
            raise FixError(f"{where}: a top-level line does not start exactly one key")
        ((key, value),) = parsed.items()
        if key in values:
            raise FixError(f"{where}: the key {key!s} appears twice")
        values[key] = value
        segments.append((key, group))
    if values != mapping:
        raise FixError(f"{where}: its keys could not be split into separate lines")
    return segments


def _parse_mapping(lines: Sequence[str], where: str) -> dict[object, object]:
    try:
        data = yaml.safe_load("\n".join(lines))
    except Exception:
        # The error text quotes the YAML, so it is dropped.
        raise FixError(f"{where} is not valid YAML") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FixError(f"{where} is not a YAML mapping")
    return data


def _assert_only_targets_changed(
    before: Sequence[str],
    after: Sequence[str],
    head_before: int,
    head_after: int,
    applied: dict[int, CharFix],
) -> None:
    """Every line after the front-matter head is unchanged but the ``?`` of applied fixes."""
    body_before, body_after = before[head_before:], after[head_after:]
    if len(body_before) != len(body_after):
        raise FixError("the fixes changed the number of lines after the front matter")
    for offset, (old, new) in enumerate(zip(body_before, body_after, strict=True)):
        index = head_before + offset
        fix = applied.get(index)
        if fix is None:
            if old != new:
                raise FixError(f"line {index + 1} changed but no fix lists it")
            continue
        changed = (
            [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
            if len(old) == len(new)
            else None
        )
        if (
            changed is None
            or len(changed) != fix.count
            or any(old[i] != _QUESTION or new[i] != fix.replacement for i in changed)
        ):
            raise FixError(f"line {fix.line}: the fix changed more than its `?` characters")


def _is_delimiter(line: str) -> bool:
    return line.rstrip() == _DELIMITER


# ---------------------------------------------------------------- files


def apply_fixes(
    root: Path, *, write: bool = True, plan: Sequence[FilePlan] = PLAN
) -> tuple[FileFix, ...]:
    """Fix each document in ``plan`` under the skill ``root``.

    Every document is read, fixed and checked with
    :func:`~fse.skilldocs.check.check_document` (front matter required, as
    for every skill file) before any file is written, so a :class:`FixError`
    leaves every file as it was. With ``write=False`` nothing is written.
    """
    if not root.is_dir():
        raise FixError(
            f"the Skill_Document folder {root} does not exist", exit_code=EXIT_UNREADABLE
        )
    pending: list[tuple[Path, bytes, FileFix]] = []
    for item in plan:
        path = root / item.path
        try:
            data = path.read_bytes()
        except OSError as exc:
            cause = exc.strerror or type(exc).__name__
            raise FixError(f"cannot read {item.path}: {cause}", exit_code=EXIT_UNREADABLE) from None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise FixError(f"{item.path}: not valid UTF-8") from None
        try:
            result = fix_text(text, item.char_fixes, merge_front_matter=item.merge_front_matter)
        except FixError as exc:
            raise FixError(f"{item.path}: {exc}") from None
        fixed = result.text.encode("utf-8")
        failures = check_document(fixed, front_matter_required=True)
        if failures:
            first = failures[0]
            raise FixError(f"{item.path}: after the fixes, {first.check} fails: {first.reason}")
        fix = FileFix(item.path, result.merge, result.applied, result.already_applied)
        pending.append((path, fixed, fix))
    if write:
        for path, fixed, fix in pending:
            if fix.changed:
                path.write_bytes(fixed)
    return tuple(fix for _, _, fix in pending)


def describe(fix: FileFix) -> list[str]:
    """One report line per change in ``fix``: ``<path>: <what>``."""
    lines: list[str] = []
    if fix.merge is not None:
        m = fix.merge
        lines.append(
            f"{fix.path}: lines {m.first_line}-{m.last_line}: merged {m.blocks} stacked "
            f"front-matter blocks into one block of {m.merged_lines} lines "
            f"(keys: {', '.join(m.keys)})"
        )
    for char_fix in fix.applied:
        lines.append(
            f"{fix.path}: line {char_fix.line}: replaced {char_fix.count} "
            f"{char_fix.old!r} with {char_fix.new!r}"
        )
    lines.extend(f"{fix.path}: line {f.line}: already fixed" for f in fix.already_applied)
    if not lines:
        lines.append(f"{fix.path}: nothing to fix")
    return lines


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """``python -m fse.skilldocs.fixes``: apply the fixes once. Returns 0, 2 or 4.

    ``environ`` (default ``os.environ``) only feeds the Redactor; no ``.env``
    file is read, because no output line holds a value.
    """
    parser = argparse.ArgumentParser(
        prog="python -m fse.skilldocs.fixes",
        description="Apply the one-time Skill_Document fixes (Req 26.4-26.6).",
    )
    parser.add_argument(
        "--skill-root", type=Path, metavar="DIR", help="the folder that holds SKILL.md"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report the fixes without writing any file"
    )
    args = parser.parse_args(argv)
    shell = dict(os.environ if environ is None else environ)
    writer = LogWriter.from_env(EnvView(shell, {}), stdout=stdout, stderr=stderr)
    root: Path | None = args.skill_root
    if root is None:
        project = find_project_dir()
        if project is None:
            writer.error("error: the Project folder was not found; pass --skill-root DIR")
            return EXIT_FAILED
        root = default_skill_root(project)
    try:
        fixes = apply_fixes(root, write=not args.dry_run)
    except FixError as exc:
        writer.error(f"error: {exc}")
        return exc.exit_code
    for fix in fixes:
        for line in describe(fix):
            writer.echo(line)
    changed = sum(fix.changed for fix in fixes)
    verb = "would change" if args.dry_run else "changed"
    writer.error(f"skilldocs fixes: {verb} {changed} of {len(fixes)} documents in {root}")
    return EXIT_PASSED


if __name__ == "__main__":
    raise SystemExit(main())
