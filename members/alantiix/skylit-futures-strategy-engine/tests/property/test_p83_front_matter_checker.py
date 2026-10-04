"""Property 83: Front-matter checker.

*For any* generated Markdown document (no front matter, one block, stacked
blocks, invalid YAML, missing or empty ``name`` or ``description``, U+FFFD
present), ``fse skilldocs check`` passes exactly the documents with one leading
block that parses to a mapping with non-empty ``name`` and ``description`` and
no U+FFFD.

Each document is built from parts whose verdict is known by construction, so
the reference model needs no YAML parser:

- layout: no front matter, a block on line 1, a block after blank lines,
  stacked blocks with only blank lines between, or a line-1 ``---`` that never
  closes;
- the YAML between the delimiters: a mapping, a list, a scalar, empty, or
  invalid (a last entry that fails to parse or to construct);
- ``name`` and ``description``: a non-empty string (plain, quoted, folded or
  literal), missing, empty or blank, or not a string;
- encoding: clean, U+FFFD in the body or in a YAML comment, or bytes that are
  not UTF-8.

Delimiters may carry trailing whitespace, lines end in LF or CRLF, and bodies
hold ``----``, ``---x`` and (after their first text line) ``---`` lines, none
of which is a front-matter delimiter.

The checker runs as for a skill file (``front_matter_required=True``) with one
fake secret value that no generated document holds. Besides the verdict, each
check must fail exactly when its part is broken (``front-matter`` 26.1,
``yaml-mapping`` 26.2, ``name-description`` 26.3, ``utf-8`` 26.7), and no
reason may quote document content.

**Validates: Requirements 26.1, 26.2, 26.3, 26.7**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from hypothesis import example, given
from hypothesis import strategies as st

from fse.skilldocs.check import Check, check_document

type Layout = Literal["one", "none", "late", "stacked", "unclosed"]
type Shape = Literal["mapping", "list", "scalar", "empty", "invalid"]
type FieldState = Literal["ok", "missing", "empty", "not-string"]
type Defect = Literal["none", "fffd-body", "fffd-yaml", "bad-bytes"]
type Entry = tuple[str, ...]  # one YAML entry: its first line and any continuation lines
type Line = str | bytes  # bytes only for a line that is not UTF-8

ONE_BLOCK: Layout = "one"
OTHER_LAYOUTS: tuple[Layout, ...] = ("none", "late", "stacked", "unclosed")
MAPPING: Shape = "mapping"
OTHER_SHAPES: tuple[Shape, ...] = ("list", "scalar", "empty", "invalid")
OK: FieldState = "ok"
BAD_FIELDS: tuple[FieldState, ...] = ("missing", "empty", "not-string")
CLEAN: Defect = "none"
DEFECTS: tuple[Defect, ...] = ("fffd-body", "fffd-yaml", "bad-bytes")

# In every generated value, comment, fragment and body text; in no reason.
MARK = "\u00a4"  # ¤
FFFD = "\ufffd"
# "|" appears in no generated document, so no-secret never fails.
FAKE_SECRET = "fake|p83|secret|0000"

DELIMITERS = ("---", "--- ", "---\t", "---   ")
BLANKS = ("", " ", "\t")
RULE = "---"  # a delimiter line: allowed in a body only after its first text line
WORDS = ("a", "Fresh", "\u2192", "tested", "\u22653:1", "question?", "\u00e9")  # →, ≥, é
BODY_TEXT = (
    f"# {MARK} Heading",
    f"{MARK} A real question? Yes.",
    f"Fresh \u2192 tested \u2192 delivered {MARK}",
    f"\u22653:1 {MARK}",
    f"- {MARK} item",
    f"{MARK} name: not front matter",
    "----",
    "--",
    f"---{MARK}",
)
FFFD_TEXT = f"{MARK} broken {FFFD} here"
FFFD_COMMENT = f"# {MARK} {FFFD}"
BAD_UTF8 = (b"\xff", b"\x80", b"\xc3(", b"\xed\xa0\x80", b"\xf0\x9f\x98 ")

EXTRAS: tuple[Entry, ...] = (
    ("version: 2",),
    (f"tags: [{MARK}, b]",),
    (f"license: '{MARK} fake'",),
    ("metadata:", f"  owner: {MARK}"),
)
NOT_MAPPINGS: Mapping[Shape, tuple[Entry, ...]] = {
    "list": ((f"- {MARK} a",), ("- b",)),
    "scalar": ((f"{MARK} just text",),),
    "empty": (),
}
# Each fails to parse or construct wherever it ends a block.
INVALID: tuple[str, ...] = (
    f"bad: [{MARK} unclosed",
    f'bad: "{MARK} unclosed',
    f"bad: {MARK} x: y",
    f"@{MARK} reserved",
    "bad: *undefined",
    "when: 2026-02-30",  # parses as a timestamp; no such date
)
FILLERS = ("", "  ", f"# {MARK} comment")  # between entries


def _ok_forms(key: str, text: str) -> tuple[Entry, ...]:
    return (
        (f"{key}: {text}",),
        (f"{key}: '{text}'",),
        (f'{key}: "{text}"',),
        (f"{key}: >-", f"  {text}", f"  {text}"),
        (f"{key}: |", f"  {text}"),
    )


def _empty_forms(key: str) -> tuple[Entry, ...]:
    return (
        (f"{key}:",),
        (f"{key}: ~",),
        (f"{key}: null",),
        (f"{key}: ''",),
        (f'{key}: ""',),
        (f"{key}: '   '",),
        (f'{key}: " \\t "',),
    )


def _non_string_forms(key: str) -> tuple[Entry, ...]:
    return (
        (f"{key}: 123",),
        (f"{key}: 1.5",),
        (f"{key}: true",),
        (f"{key}: 2026-01-01",),
        (f"{key}: [{MARK} a]",),
        (f"{key}: {{k: {MARK}}}",),
        (f"{key}:", f"  k: {MARK}"),
    )


# ---------------------------------------------------------------- documents


@dataclass(frozen=True, slots=True)
class Block:
    lines: tuple[str, ...]  # the YAML between the delimiters
    shape: Shape
    name_ok: bool
    description_ok: bool

    @property
    def passes(self) -> bool:
        return self.shape == "mapping" and self.name_ok and self.description_ok


@dataclass(frozen=True, slots=True)
class Doc:
    data: bytes
    layout: Layout
    block: Block | None  # the first block; None when there is no front matter
    defect: Defect


def _weighted[T](good: T, bad: Sequence[T]) -> st.SearchStrategy[T]:
    """``good`` three times in four, else one of ``bad``, so passing documents are common."""
    return st.integers(0, 3).flatmap(lambda n: st.sampled_from(bad) if n == 3 else st.just(good))


_TEXT = st.lists(st.sampled_from(WORDS), max_size=3).map(lambda words: " ".join((MARK, *words)))


@st.composite
def _fields(draw: st.DrawFn, key: str) -> tuple[bool, Entry | None]:
    """Whether ``key`` holds a non-empty string, and its entry (None when missing)."""
    state = draw(_weighted(OK, BAD_FIELDS))
    if state == "ok":
        return True, draw(st.sampled_from(_ok_forms(key, draw(_TEXT))))
    if state == "missing":
        return False, None
    forms = _empty_forms(key) if state == "empty" else _non_string_forms(key)
    return False, draw(st.sampled_from(forms))


@st.composite
def _blocks(draw: st.DrawFn, *, fffd: bool) -> Block:
    shape = draw(_weighted(MAPPING, OTHER_SHAPES))
    name_ok = description_ok = False
    entries: list[Entry]
    if shape in ("mapping", "invalid"):
        name_ok, name = draw(_fields("name"))
        description_ok, description = draw(_fields("description"))
        extras = draw(st.lists(st.sampled_from(EXTRAS), max_size=3, unique=True))
        present = [e for e in (name, description, *extras) if e is not None]
        entries = list(draw(st.permutations(present)))
        if shape == "mapping" and not entries:
            entries = [EXTRAS[0]]  # fillers alone are an empty document, not a mapping
        if shape == "invalid":
            entries.append((draw(st.sampled_from(INVALID)),))
    else:
        entries = list(NOT_MAPPINGS[shape])
    filler = st.lists(st.sampled_from(FILLERS), max_size=1)
    gaps = [list(draw(filler)) for _ in range(len(entries) + 1)]
    if fffd:
        gaps[draw(st.integers(0, len(entries)))].append(FFFD_COMMENT)
    lines: list[str] = []
    for gap, entry in zip(gaps, [*entries, ()], strict=True):
        lines.extend((*gap, *entry))
    return Block(tuple(lines), shape, name_ok, description_ok)


@st.composite
def _body(draw: st.DrawFn, *, rules: bool) -> list[Line]:
    """Blank lines, then maybe text; the first non-blank line is never a delimiter."""
    lines: list[Line] = list(draw(st.lists(st.sampled_from(BLANKS), max_size=2)))
    if draw(st.integers(0, 3)) > 0:
        later = (*BODY_TEXT, *BLANKS, *((RULE,) if rules else ()))
        lines.append(draw(st.sampled_from(BODY_TEXT)))
        lines.extend(draw(st.lists(st.sampled_from(later), max_size=4)))
    return lines


def _defect_lines(defect: Defect) -> st.SearchStrategy[Line]:
    if defect == "bad-bytes":
        return st.sampled_from(BAD_UTF8).map(lambda bad: MARK.encode() + b" bad " + bad + b" byte")
    return st.just(FFFD_TEXT)


@st.composite
def _documents(draw: st.DrawFn) -> Doc:
    layout = draw(_weighted(ONE_BLOCK, OTHER_LAYOUTS))
    defect = draw(_weighted(CLEAN, DEFECTS))
    in_block = defect == "fffd-yaml" and layout != "none"
    body = draw(_body(rules=layout != "unclosed"))
    if defect != "none" and not in_block:
        body.insert(draw(st.integers(0, len(body))), draw(_defect_lines(defect)))
    delimiter = st.sampled_from(DELIMITERS)
    block: Block | None = None
    head: list[Line] = []
    if layout != "none":
        block = draw(_blocks(fffd=in_block))
        head = [draw(delimiter), *block.lines]
    if layout in ("one", "late"):
        head.append(draw(delimiter))
    if layout == "late":
        head[:0] = draw(st.lists(st.sampled_from(BLANKS), min_size=1, max_size=2))
    if layout == "stacked":
        second = draw(_blocks(fffd=False))
        between = draw(st.lists(st.sampled_from(BLANKS), max_size=2))
        head.extend((draw(delimiter), *between, draw(delimiter), *second.lines))
        if draw(st.booleans()):
            head.append(draw(delimiter))
    lines = [*head, *body]
    eol = draw(st.sampled_from((b"\n", b"\r\n")))
    data = eol.join(line if isinstance(line, bytes) else line.encode() for line in lines)
    final_eol = draw(st.booleans())
    if lines and final_eol:
        data += eol
    return Doc(data, layout, block, defect)


# The stacked blocks of current_working_SKILL_100226.md before the fix.
_STACKED_AS_IN_THE_WORKING_SKILL = Doc(
    data=(
        f"---\nname: {MARK} a\ndescription: {MARK} b\n---\n\n"
        f"---\nname: {MARK} a\ndescription: {MARK} fuller b\n---\n# {MARK} Body\n"
    ).encode(),
    layout="stacked",
    block=Block((f"name: {MARK} a", f"description: {MARK} b"), "mapping", True, True),
    defect="none",
)
# One folded description, the fixed arrows and a real question mark.
_LIKE_THE_FIXED_SKILL = Doc(
    data=(
        f"---\nname: {MARK} skill\ndescription: >-\n  {MARK} Use this.\n---\n"
        f"# {MARK} Body\nFresh \u2192 tested\n\u22653:1 and a real question?\n"
    ).encode(),
    layout="one",
    block=Block(
        (f"name: {MARK} skill", "description: >-", f"  {MARK} Use this."), "mapping", True, True
    ),
    defect="none",
)


# ---------------------------------------------------------------- reference model


def _expected_failed_checks(doc: Doc) -> set[Check]:
    failed: set[Check] = set()
    if doc.layout != "one":
        failed.add(Check.FRONT_MATTER)
    if doc.layout in ("one", "stacked"):  # a closed block on line 1 is parsed
        assert doc.block is not None
        if doc.block.shape != "mapping":
            failed.add(Check.YAML_MAPPING)
        elif not doc.block.passes:
            failed.add(Check.NAME_DESCRIPTION)
    if doc.defect != "none":
        failed.add(Check.UTF8)
    return failed


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 83: Front-matter checker
@example(doc=_STACKED_AS_IN_THE_WORKING_SKILL)
@example(doc=_LIKE_THE_FIXED_SKILL)
@given(doc=_documents())
def test_checker_passes_exactly_the_well_formed_documents(doc: Doc) -> None:
    assert FAKE_SECRET.encode() not in doc.data, "generator: the fake secret leaked in"

    found = check_document(doc.data, front_matter_required=True, secret_values=(FAKE_SECRET,))

    passes = doc.layout == "one" and doc.block is not None and doc.block.passes
    passes = passes and doc.defect == "none"
    assert (found == ()) == passes
    assert {failure.check for failure in found} == _expected_failed_checks(doc)
    for failure in found:  # fixed text and line numbers only
        assert MARK not in failure.reason
        assert FFFD not in failure.reason
        assert FAKE_SECRET not in failure.reason
