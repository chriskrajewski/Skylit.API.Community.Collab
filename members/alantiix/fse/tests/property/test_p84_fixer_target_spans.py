"""Property 84: Fixer changes only target spans.

*For any* document containing stacked front-matter blocks and ``?``
characters in the target chains, the fixer output differs from the input only
in the front-matter block and the replaced characters, and the merged block
contains every distinct key from the stacked blocks.

Documents are built from parts whose YAML value is known by construction:

- front matter: blocks stacked on line 1 with blank lines between, each a
  mapping of distinct keys (plain, quoted, folded, literal, number, list or
  nested values), so one key can appear in several blocks;
- body: text with real question marks, target forms on unlisted lines,
  ``----`` and (after the first text line) ``---`` lines, and chain lines
  listed in a :class:`CharFix`, each in its before form or already fixed;
- LF or CRLF line ends and delimiters with trailing whitespace.

Req 26.8: every line after the front matter is unchanged except listed lines
in their before form, where only the ``?`` of each span becomes the
replacement. Req 26.4: the front matter becomes one block that keeps the
first opening and the last closing delimiter, holds only lines of the stacked
blocks and parses to the union of their mappings, a later block's value
winning. A second run, with line numbers shifted by the merge, changes
nothing.

On temporary copies laid out like the skill root, :func:`apply_fixes` with the
real plan writes exactly that and leaves ``TASK.md`` alone. A listed
``SKILL.md`` line in neither form raises :class:`FixError` naming the line,
quoting no document content, and leaves every file as it was.

A ``|`` value is always followed by a blank or comment line in its block. The
fixer reads a block without its last line break, so a ``|`` value that ends
a block would gain a line break when another key follows it; the fixer
refuses that merge by design and the generator does not build it.

**Validates: Requirements 26.4, 26.8**
"""

from __future__ import annotations

import dataclasses
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import pytest
import yaml
from hypothesis import example, given
from hypothesis import strategies as st

from fse.skilldocs import (
    FINE_TUNE_DIR,
    SKILL_FILE,
    SKILL_ROOT_NAME,
    TASK_FILE,
    WORKING_SKILL_FILE,
)
from fse.skilldocs.check import EXIT_FAILED, check_document
from fse.skilldocs.fixes import SKILL_MD_FIXES, CharFix, FixError, Merge, apply_fixes, fix_text

type Form = Literal["before", "after", "broken"]
type Entry = tuple[str, ...]  # one YAML entry: its key line and any continuation lines

FIXABLE: tuple[Form, ...] = ("before", "after")

MARK = "\u00a4"  # ¤: in every generated value and chain line; in no FixError message
ARROW = "\u2192"  # →
GE = "\u2265"  # ≥

DELIMITERS = ("---", "--- ", "---\t")
BLANKS = ("", " ", "\t")
RULE = "---"  # a delimiter line: allowed in a body only after its first text line
KEYS = ("name", "description", "version", "tags", "license", "metadata", "allowed-tools")
STRING_KEYS = ("name", "description")
VALUE_WORDS = ("Fresh", "tested", "air?", "a?b", ARROW, GE, "\u00e9", "GEX/VEX")
FILLERS = ("", f"# {MARK} note")  # after an entry, inside a block

# CharFix spans: the two SKILL.md shapes and one more.
SPANS = ((" ? ", f" {ARROW} "), ("?3:1", f"{GE}3:1"), ("(?)", f"({ARROW})"))
# No `?`, `→` or `≥`, so a chain line holds exactly the spans placed in it.
CHAIN_WORDS = ("Fresh", "tested", "King / floor", "GEX vs VEX", "Chart thesis (ES/NQ)", "3:1")
PREFIXES = (f"{MARK} ", f"Why? {MARK} ", f"3. {MARK} ")
SUFFIXES = ("", ".", " Who?")
BODY_TEXT = (
    f"# {MARK} Heading",
    f"5. {MARK} Where is spot: floor, ceiling, Gatekeeper, or air?",
    f"7. {MARK} VEX + VIX: who is in control?",
    f"{MARK} Fresh ? tested ? delivered ? decaying.",  # a target form on an unlisted line
    f"{MARK} correct stop, ?3:1, regime-matched",
    f"{MARK} (?) and a?b",
    f"{MARK} Fresh {ARROW} tested, {GE}3:1",
    "----",
    f"---{MARK}",
)
_FILLER = st.sampled_from((*BODY_TEXT, *BLANKS, RULE))

WORKING = PurePosixPath(FINE_TUNE_DIR) / WORKING_SKILL_FILE
SKILL = PurePosixPath(SKILL_FILE)
TASK = PurePosixPath(FINE_TUNE_DIR) / TASK_FILE
TASK_TEXT = f"{MARK} You are a test copilot. Is this a question? Fresh ? tested.\n"


# ---------------------------------------------------------------- documents


@dataclass(frozen=True, slots=True)
class Head:
    lines: tuple[str, ...]  # the stacked blocks, delimiters and blank lines between included
    mappings: tuple[Mapping[str, object], ...]  # each block's mapping, by construction


@dataclass(frozen=True, slots=True)
class Chain:
    old: str
    new: str
    count: int
    form: Form
    text: str  # the line as generated
    fixed: str  # the line after the fix (``text`` unless in the before form)


@dataclass(frozen=True, slots=True)
class Doc:
    text: str
    head: Head
    fixes: tuple[CharFix, ...]  # in the order given to the fixer
    chains: Mapping[int, Chain]  # by the 1-based line of each fix

    @property
    def head_lines(self) -> int:
        return len(self.head.lines)


@dataclass(frozen=True, slots=True)
class SkillRoot:
    working: Doc  # current_working_SKILL_100226.md: merge only
    skill: Doc  # SKILL.md: one block, SKILL_MD_FIXES


_TEXT = st.lists(st.sampled_from(VALUE_WORDS), max_size=3).map(lambda w: " ".join((MARK, *w)))


@st.composite
def _entries(draw: st.DrawFn, key: str) -> tuple[Entry, object]:
    text, more = draw(_TEXT), draw(_TEXT)
    forms: list[tuple[Entry, object]] = [
        ((f"{key}: {text}",), text),
        ((f"{key}: '{text}'",), text),
        ((f'{key}: "{text}"',), text),
        ((f"{key}: >-", f"  {text}", f"  {more}"), f"{text} {more}"),
        # A `|` value is position-independent only when a line follows it.
        (
            (f"{key}: |", f"  {text}", f"  {more}", draw(st.sampled_from(FILLERS))),
            f"{text}\n{more}\n",
        ),
    ]
    if key not in STRING_KEYS:
        number = draw(st.integers(0, 999))
        forms += [
            ((f"{key}: {number}",), number),
            ((f"{key}: ['{text}', '{more}']",), [text, more]),
            ((f"{key}:", f"  - {text}", f"  - {more}"), [text, more]),
            ((f"{key}:", f"  owner: {text}"), {"owner": text}),
        ]
    return draw(st.sampled_from(forms))


@st.composite
def _heads(draw: st.DrawFn, *, min_blocks: int, max_blocks: int) -> Head:
    """Blocks stacked on line 1; ``name`` and ``description`` hold strings in at least one."""
    count = draw(st.integers(min_blocks, max_blocks))
    key_lists = [
        draw(st.lists(st.sampled_from(KEYS), max_size=4, unique=True)) for _ in range(count)
    ]
    seen = {key for keys in key_lists for key in keys}
    key_lists[-1].extend(key for key in STRING_KEYS if key not in seen)
    lines: list[str] = []
    mappings: list[Mapping[str, object]] = []
    for index, keys in enumerate(key_lists):
        if index:
            lines.extend(draw(st.lists(st.sampled_from(BLANKS), max_size=2)))
        lines.append(draw(st.sampled_from(DELIMITERS)))
        lines.extend(draw(st.lists(st.just(""), max_size=1)))  # blank lines before the first key
        mapping: dict[str, object] = {}
        for key in keys:
            entry, value = draw(_entries(key))
            lines.extend(entry)
            lines.extend(draw(st.lists(st.sampled_from(FILLERS), max_size=1)))
            mapping[key] = value
        lines.append(draw(st.sampled_from(DELIMITERS)))
        mappings.append(mapping)
    return Head(tuple(lines), tuple(mappings))


def _broken_shapes(count: int) -> list[tuple[int, int]]:
    """``(spans, old spans)`` for a line in neither form: not ``count`` old, not ``count`` new."""
    return [
        (total, olds)
        for total in range(count + 3)
        for olds in range(total + 1)
        if olds != count and (olds, total) != (0, count)
    ]


@st.composite
def _chains(draw: st.DrawFn, old: str, new: str, count: int, form: Form) -> Chain:
    if form == "broken":
        total, olds = draw(st.sampled_from(_broken_shapes(count)))
        seps = draw(st.permutations([old] * olds + [new] * (total - olds)))
    else:
        seps = [old if form == "before" else new] * count
    size = len(seps) + 1
    words = draw(st.lists(st.sampled_from(CHAIN_WORDS), min_size=size, max_size=size))
    prefix, suffix = draw(st.sampled_from(PREFIXES)), draw(st.sampled_from(SUFFIXES))

    def line(spans: Sequence[str]) -> str:
        return (
            prefix
            + words[0]
            + "".join(s + w for s, w in zip(spans, words[1:], strict=True))
            + suffix
        )

    fixed = line([new] * count) if form != "broken" else line(seps)
    return Chain(old, new, count, form, line(seps), fixed)


@st.composite
def _random_chains(draw: st.DrawFn) -> Chain:
    old, new = draw(st.sampled_from(SPANS))
    return draw(_chains(old, new, draw(st.integers(1, 6)), draw(st.sampled_from(FIXABLE))))


@st.composite
def _render(draw: st.DrawFn, head: Head, body: Sequence[str | Chain]) -> Doc:
    items = list(body)
    first = next(
        (i for i, item in enumerate(items) if isinstance(item, Chain) or item.strip()), None
    )
    if first is not None and items[first] == RULE:
        items[first] = BODY_TEXT[0]  # a `---` right after the blocks would stack
    chains: dict[int, Chain] = {}
    for index, item in enumerate(items):
        if isinstance(item, Chain):
            chains[len(head.lines) + index + 1] = item
    fixes = [CharFix(n, c.old, c.new, c.count) for n, c in chains.items()]
    lines = [*head.lines, *(item.text if isinstance(item, Chain) else item for item in items)]
    eol = draw(st.sampled_from(("\n", "\r\n")))
    text = eol.join(lines) + (eol if draw(st.booleans()) else "")
    return Doc(text, head, tuple(draw(st.permutations(fixes))), chains)


@st.composite
def _stacked_documents(draw: st.DrawFn) -> Doc:
    head = draw(_heads(min_blocks=2, max_blocks=3))
    body = draw(st.lists(st.one_of(_FILLER, _random_chains()), min_size=1, max_size=12))
    return draw(_render(head, body))


@st.composite
def _skill_roots(draw: st.DrawFn, *, broken: bool) -> SkillRoot:
    """A working skill with 1-3 blocks and a SKILL.md whose lines 107, 144, 154 are chains."""
    working_head = draw(_heads(min_blocks=1, max_blocks=3))
    working = draw(_render(working_head, draw(st.lists(_FILLER, min_size=1, max_size=8))))
    head = draw(_heads(min_blocks=1, max_blocks=1))
    assert len(head.lines) < SKILL_MD_FIXES[0].line, "generator: SKILL.md head too long"
    lines = {fix.line for fix in SKILL_MD_FIXES}
    broken_lines = draw(st.sets(st.sampled_from(sorted(lines)), min_size=1)) if broken else set()
    size = SKILL_MD_FIXES[-1].line + draw(st.integers(0, 3)) - len(head.lines)
    body: list[str | Chain] = list(draw(st.lists(_FILLER, min_size=size, max_size=size)))
    for fix in SKILL_MD_FIXES:
        form: Form = "broken" if fix.line in broken_lines else draw(st.sampled_from(FIXABLE))
        body[fix.line - 1 - len(head.lines)] = draw(_chains(fix.old, fix.new, fix.count, form))
    return SkillRoot(working, draw(_render(head, body)))


def _real_shape() -> Doc:
    """The two stacked blocks of the working skill and SKILL.md's line 107 and 144 chains."""
    head = Head(
        (
            "---",
            "name: Skylit Academy playbook",
            "description: >-",
            "  use this when reading Skylit Heatseeker",
            "---",
            "",
            "---",
            "name: Skylit Academy playbook",
            "description: >-",
            "  Use this when reading Skylit Heatseeker. Bootcamp notes overlay kill switches.",
            "---",
        ),
        (
            {
                "name": "Skylit Academy playbook",
                "description": "use this when reading Skylit Heatseeker",
            },
            {
                "name": "Skylit Academy playbook",
                "description": "Use this when reading Skylit Heatseeker. "
                "Bootcamp notes overlay kill switches.",
            },
        ),
    )
    chains = {
        12: Chain(
            " ? ",
            f" {ARROW} ",
            3,
            "before",
            "Fresh ? tested ? delivered ? decaying.",
            f"Fresh {ARROW} tested {ARROW} delivered {ARROW} decaying.",
        ),
        14: Chain(
            "?3:1",
            f"{GE}3:1",
            1,
            "before",
            "3. Tap entry, correct stop, ?3:1, regime-matched.",
            f"3. Tap entry, correct stop, {GE}3:1, regime-matched.",
        ),
    }
    body = [chains[12].text, "7. VEX + VIX: who is in control?", chains[14].text, ""]
    fixes = tuple(CharFix(n, c.old, c.new, c.count) for n, c in chains.items())
    return Doc("\n".join((*head.lines, *body)), head, fixes, chains)


# ---------------------------------------------------------------- reference model


def _expected_lines(doc: Doc) -> list[str]:
    """``doc`` after the char fixes alone: each chain line shows its fixed form."""
    lines = doc.text.split("\n")
    for number, chain in doc.chains.items():
        line = lines[number - 1]
        assert line.startswith(chain.text)
        lines[number - 1] = chain.fixed + line[len(chain.text) :]  # keeps a CR
    return lines


def _assert_only_listed_question_marks_changed(
    before: Sequence[str], after: Sequence[str], fixes: Sequence[CharFix], first_line: int
) -> None:
    """Req 26.8 on the lines after the front matter; ``first_line`` numbers ``before[0]``."""
    by_line = {fix.line: fix for fix in fixes}
    assert len(after) == len(before)
    for offset, (old, new) in enumerate(zip(before, after, strict=True)):
        if old == new:
            continue
        number = first_line + offset
        assert number in by_line, f"unlisted line {number} changed"
        fix = by_line[number]
        assert len(new) == len(old)
        changed = [(a, b) for a, b in zip(old, new, strict=True) if a != b]
        assert changed == [("?", fix.replacement)] * fix.count


def _assert_one_block(
    before: Sequence[str], after: Sequence[str], mappings: Sequence[Mapping[str, object]]
) -> None:
    """Req 26.4: ``after`` is one block made of ``before``'s lines, holding the union."""
    union: dict[str, object] = {}
    for mapping in mappings:
        union.update(mapping)  # a later block's value wins
    assert len(after) >= 2
    assert (after[0], after[-1]) == (before[0], before[-1])
    inner = after[1:-1]
    assert not any(line.rstrip() == RULE for line in inner)
    assert set(inner) <= set(before)
    assert (yaml.safe_load("\n".join(inner) + "\n") or {}) == union


def _keys(mappings: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(key for mapping in mappings for key in mapping))


def _write(tmp: Path, root: SkillRoot) -> Path:
    skill_root = tmp / SKILL_ROOT_NAME
    (skill_root / FINE_TUNE_DIR).mkdir(parents=True)
    (skill_root / WORKING).write_bytes(root.working.text.encode())
    (skill_root / SKILL).write_bytes(root.skill.text.encode())
    (skill_root / TASK).write_bytes(TASK_TEXT.encode())
    return skill_root


def _read_all(skill_root: Path) -> dict[PurePosixPath, bytes]:
    return {rel: (skill_root / rel).read_bytes() for rel in (WORKING, SKILL, TASK)}


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 84: Fixer changes only target spans
@example(doc=_real_shape())
@given(doc=_stacked_documents())
def test_fixer_changes_only_the_front_matter_and_listed_question_marks(doc: Doc) -> None:
    result = fix_text(doc.text, doc.fixes, merge_front_matter=True)

    before, after = doc.text.split("\n"), result.text.split("\n")
    head = len(after) - (len(before) - doc.head_lines)  # lines in the merged block
    _assert_one_block(before[: doc.head_lines], after[:head], doc.head.mappings)
    keys = _keys(doc.head.mappings)
    assert result.merge == Merge(1, doc.head_lines, len(doc.head.mappings), head, keys)
    _assert_only_listed_question_marks_changed(
        before[doc.head_lines :], after[head:], doc.fixes, doc.head_lines + 1
    )
    assert after[head:] == _expected_lines(doc)[doc.head_lines :]
    pending = {fix for fix in doc.fixes if doc.chains[fix.line].form == "before"}
    assert set(result.applied) == pending
    assert set(result.already_applied) == set(doc.fixes) - pending

    # Idempotent: the merge moved every later line by the same amount.
    shift = head - doc.head_lines
    shifted = tuple(dataclasses.replace(fix, line=fix.line + shift) for fix in doc.fixes)
    again = fix_text(result.text, shifted, merge_front_matter=True)
    assert again.text == result.text
    assert not again.changed
    assert set(again.already_applied) == set(shifted)


# Feature: skylit-futures-strategy-engine, Property 84: Fixer changes only target spans
@given(root=_skill_roots(broken=False))
def test_planned_fixes_on_temporary_copies_change_only_target_spans(root: SkillRoot) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-p84-") as tmp:
        skill_root = _write(Path(tmp), root)
        working_fix, skill_fix = apply_fixes(skill_root)
        written = _read_all(skill_root)
        second = apply_fixes(skill_root)
        rewritten = _read_all(skill_root)

    # SKILL.md: one block, so only the listed `?` change.
    skill = root.skill
    skill_after = written[SKILL].decode().split("\n")
    _assert_only_listed_question_marks_changed(skill.text.split("\n"), skill_after, skill.fixes, 1)
    assert skill_after == _expected_lines(skill)
    assert skill_fix.merge is None
    pending = {fix for fix in skill.fixes if skill.chains[fix.line].form == "before"}
    assert set(skill_fix.applied) == pending

    # The working skill: merge only; every line after the front matter stays.
    working = root.working
    before, after = working.text.split("\n"), written[WORKING].decode().split("\n")
    head = len(after) - (len(before) - working.head_lines)
    assert after[head:] == before[working.head_lines :]
    if len(working.head.mappings) > 1:
        _assert_one_block(before[: working.head_lines], after[:head], working.head.mappings)
        assert working_fix.merge is not None
        assert working_fix.merge.keys == _keys(working.head.mappings)
    else:
        assert after == before
        assert not working_fix.changed

    assert written[TASK] == TASK_TEXT.encode()
    for rel in (WORKING, SKILL):
        assert check_document(written[rel], front_matter_required=True) == ()
    # Idempotent: the second run finds every fix applied and writes nothing new.
    assert not any(fix.changed for fix in second)
    assert second[1].already_applied == SKILL_MD_FIXES
    assert rewritten == written


# Feature: skylit-futures-strategy-engine, Property 84: Fixer changes only target spans
@given(root=_skill_roots(broken=True))
def test_listed_line_in_neither_form_stops_the_run_before_any_write(root: SkillRoot) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-p84-") as tmp:
        skill_root = _write(Path(tmp), root)
        original = _read_all(skill_root)
        with pytest.raises(FixError) as info:
            apply_fixes(skill_root)
        after = _read_all(skill_root)

    assert after == original
    first_broken = min(n for n, chain in root.skill.chains.items() if chain.form == "broken")
    message = str(info.value)
    assert message.startswith(f"{SKILL_FILE}: line {first_broken}: ")
    assert info.value.exit_code == EXIT_FAILED
    assert MARK not in message
