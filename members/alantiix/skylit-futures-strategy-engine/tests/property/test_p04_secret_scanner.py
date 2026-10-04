"""Property 4: Secret_Scanner reports exactly the matching files.

*For any* repository whose index holds generated files, some containing one or
more configured secret values, the Secret_Scanner prints exactly the set of
paths whose working-tree content contains a value, exits 0 if and only if that
set is empty, and prints no line other than a matching path or a status
message, with no secret value and no file content.

Each example builds a fresh repository with ``git init`` inside its own
temporary directory, with git config isolated as in
``tests/unit/test_path_guard.py``, so the real repository is never scanned.
The repository holds:

- committed and newly staged files, each left as is, edited after ``git add``
  (index and working tree differ) or deleted from the working tree;
- untracked and git-ignored files, which hold values too and never match;
- symlinks in each of those states, whose content for git and for the scanner
  is the link text.

File contents mix random bytes, whole values, near misses (a value with bytes
cut off either end, a Latin-1 copy of a non-ASCII value) and decoys: values set
only on variables that are not Secret_Variables. A :class:`Fill` piece pads the
content so the next piece starts a generated number of bytes before a read
chunk edge. The chunk size is either the scanner's real 1 MiB or a small size
patched in, so values cross chunk edges either way.

Values come only from the explicit environment passed to the scanner; the
temporary Project folder has no ``.env`` file. Every value holds one character
from ``MARKERS``, which no generated path and no status message contains, so a
correct output line can never hold a value or be redacted.

The expected result is a reference model: ``value in content`` over the whole
working-tree bytes of each index entry. It is checked against
:func:`fse.secrets.scanner.scan` and against ``fse scan-secrets`` run through
:func:`fse.cli.run`. Exit codes: 0 no match, 1 matches, 5 when every
Secret_Variable is blank or git cannot list the index (a corrupt index file).

**Validates: Requirements 1.13, 1.14, 1.16**
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from fse import cli
from fse.secrets import scanner
from fse.secrets.env import EnvView
from fse.secrets.scanner import (
    EXIT_CANNOT_RUN,
    EXIT_CLEAN,
    EXIT_MATCHES,
    GitListingError,
    NoSecretValuesError,
    ScanError,
    ScanResult,
    find_repo_root,
    scan,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
    pytest.mark.skipif(os.name != "posix", reason="needs POSIX file names and symlinks"),
]

COMMAND = "scan-secrets"
REAL_CHUNK: int = scanner._CHUNK_SIZE  # read before any example patches it

# Secret_Variables per design §1; the listable names count only when listed in
# FSE_SECRET_VARS. NOT_SECRET is never one: its value is a decoy.
BUILT_IN = (
    "SKYLIT_API_KEY",
    "PROJECTX_USERNAME",
    "PROJECTX_API_KEY",
    "NOTIFIER_WEBHOOK_URL",
    "NARRATOR_API_KEY",
)
LISTABLE = ("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID", "FSE_EXTRA_TOKEN")
NOT_SECRET = "FSE_NOT_A_SECRET"
SECRET_LIST = "FSE_SECRET_VARS"
BLANKS = ("", " ", "\t", "\n ")

# Every value holds a marker; no path, filler byte or status message does.
MARKERS = "~=+&@|^"
VALUE_CHARS = "ab \u00e9"  # é: its UTF-8 and Latin-1 bytes differ
NOISE = "ab.\n~="
LINK_NOISE = "ab./~="
FILLER = b"."
LINK_PREFIX = b"L"  # keeps every link text non-empty; in no value

# Lower-case names only (macOS repositories default to core.ignorecase=true).
# "d\u00edr" is NFC, which git reports with core.precomposeunicode=true.
DIRS = ("src", "docs", "with space", "d\u00edr")
STEMS = ("f", "notes", "with space", "d\u00edr")
EXTS = (".txt", ".bin", "")
IGNORED_DIR = "ignored"
IGNORED_EXT = ".local"
GITIGNORE_PATH = ".gitignore"
GITIGNORE = b"*.local\n/ignored/\n"

_GIT_DISCOVERY_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
)
_COMMIT = (
    "-c",
    "user.name=p04",
    "-c",
    "user.email=p04@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "commit",
    "-q",
    "-m",
    "base",
)

type Kind = Literal["committed", "staged", "untracked", "ignored"]
type Change = Literal["none", "edited", "deleted"]
KINDS: tuple[Kind, ...] = ("committed", "staged", "untracked", "ignored")
CHANGES: tuple[Change, ...] = ("none", "edited", "deleted")


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Fill:
    """Filler bytes so the next piece starts ``delta`` bytes before a chunk edge."""

    delta: int


type Piece = bytes | Fill


@dataclass(frozen=True, slots=True)
class FileSpec:
    path: str  # relative to the working tree root, "/"-separated
    kind: Kind
    link: bool  # a symlink whose link text is the rendered content
    content: tuple[Piece, ...]  # committed or staged content; the only content otherwise
    change: Change = "none"  # applied after `git add`; tracked kinds only
    edit: tuple[Piece, ...] = ()  # working-tree content when change == "edited"

    @property
    def tracked(self) -> bool:
        return self.kind in ("committed", "staged")


@dataclass(frozen=True, slots=True)
class Scenario:
    chunk: int  # read chunk size; REAL_CHUNK means unpatched
    environ: Mapping[str, str]  # the whole shell environment the scanner sees
    files: tuple[FileSpec, ...]
    broken_index: bool  # .git/index overwritten so `git ls-files` fails


def _utf8(text: str) -> bytes:
    return text.encode("utf-8")


def _latin1(text: str) -> bytes:
    return text.encode("latin-1")


def _byte_near_misses(data: bytes) -> st.SearchStrategy[bytes]:
    """``data`` with one or more bytes cut off its start or its end."""
    return st.integers(1, len(data)).flatmap(lambda k: st.sampled_from((data[:-k], data[k:])))


def _char_near_misses(text: str) -> st.SearchStrategy[bytes]:
    """Like :func:`_byte_near_misses` but cut by characters, so the result is UTF-8."""
    cuts = st.integers(1, len(text)).flatmap(lambda k: st.sampled_from((text[:-k], text[k:])))
    return cuts.map(_utf8)


def _pieces(pool: Sequence[str], *, link: bool) -> st.SearchStrategy[bytes]:
    value = st.sampled_from(pool)
    whole = value.map(_utf8)
    if link:  # link text: UTF-8, no NUL
        return st.one_of(
            st.text(LINK_NOISE, max_size=6).map(_utf8),
            whole,
            value.flatmap(_char_near_misses),
        )
    return st.one_of(
        st.binary(max_size=6),
        st.text(NOISE, max_size=6).map(_utf8),
        whole,
        whole.flatmap(_byte_near_misses),
        value.map(_latin1),
    )


@st.composite
def _content(draw: st.DrawFn, pool: tuple[str, ...], link: bool) -> tuple[Piece, ...]:
    pieces = _pieces(pool, link=link)
    head: list[Piece] = list(draw(st.lists(pieces, max_size=4)))
    if link or not draw(st.booleans()):
        return tuple(head)
    # One value or near miss placed `delta` bytes before a chunk edge: across
    # it, ending on it (delta == len) or starting on it (delta == 0).
    whole = draw(st.sampled_from(pool).map(_utf8))
    straddle = whole if draw(st.integers(0, 3)) < 3 else draw(_byte_near_misses(whole))
    delta = draw(st.integers(0, len(straddle) + 1))
    tail: list[Piece] = list(draw(st.lists(pieces, max_size=2)))
    return (*head, Fill(delta), straddle, *tail)


@st.composite
def _file(draw: st.DrawFn, index: int, pool: tuple[str, ...]) -> FileSpec:
    kind = draw(st.sampled_from(KINDS))
    link = draw(st.integers(0, 4)) == 4
    dirs = list(draw(st.lists(st.sampled_from(DIRS), max_size=2)))
    ext = draw(st.sampled_from(EXTS))
    if kind == "ignored":
        if draw(st.booleans()):
            dirs.insert(0, IGNORED_DIR)
        else:
            ext = IGNORED_EXT
    # The index suffix keeps paths unique and distinct from every directory name.
    name = f"{draw(st.sampled_from(STEMS))}-{index}{ext}"
    content = draw(_content(pool, link))
    if kind not in ("committed", "staged"):
        return FileSpec("/".join((*dirs, name)), kind, link, content)
    change = draw(st.sampled_from(CHANGES))
    edit = draw(_content(pool, link)) if change == "edited" else ()
    return FileSpec("/".join((*dirs, name)), kind, link, content, change, edit)


_VALUES = st.tuples(
    st.text(VALUE_CHARS, max_size=4),
    st.sampled_from(MARKERS),
    st.text(VALUE_CHARS + MARKERS, max_size=4),
).map("".join)
_BLANK: st.SearchStrategy[str | None] = st.none() | st.sampled_from(BLANKS)
_RARELY = st.integers(0, 7).map(lambda n: n == 7)
# Mostly small patched chunks (values cross many edges); the real size a quarter of the time.
_CHUNKS = st.integers(0, 3).flatmap(lambda n: st.just(REAL_CHUNK) if n == 3 else st.integers(1, 24))


@st.composite
def _scenarios(draw: st.DrawFn) -> Scenario:
    pool = tuple(draw(st.lists(_VALUES, min_size=1, max_size=4, unique=True)))
    chunk = draw(_CHUNKS)
    all_blank = draw(_RARELY)
    listed = draw(st.lists(st.sampled_from(LISTABLE), max_size=3))
    secret_names = {*BUILT_IN, *listed}
    environ: dict[str, str] = {}
    for name in (*BUILT_IN, *LISTABLE, NOT_SECRET):
        # A quarter of the variables blank, or every Secret_Variable when all_blank.
        blank = (all_blank and name in secret_names) or draw(st.integers(0, 3)) == 3
        value = draw(_BLANK if blank else st.sampled_from(pool))
        if value is not None:
            environ[name] = value
    set_list: bool = draw(st.booleans())  # FSE_SECRET_VARS present even with no names
    if listed or set_list:
        separator = draw(st.sampled_from((",", ", ", " , ")))
        environ[SECRET_LIST] = separator.join(listed) + draw(st.sampled_from(("", ",", ", ,")))
    count = draw(st.integers(0, 6))
    files = tuple(draw(_file(index, pool)) for index in range(count))
    return Scenario(chunk, environ, files, broken_index=draw(_RARELY))


# A value across, ending on and starting on the real 1 MiB chunk edge.
_EDGE_VALUE = b"fake~edge-0004"
_AT_THE_REAL_CHUNK_EDGE = Scenario(
    chunk=REAL_CHUNK,
    environ={"SKYLIT_API_KEY": _EDGE_VALUE.decode()},
    files=(
        FileSpec("across.bin", "committed", False, (b"head", Fill(5), _EDGE_VALUE, b"tail")),
        FileSpec("near-miss.bin", "committed", False, (Fill(5), _EDGE_VALUE[:-1], FILLER)),
        FileSpec("ends-on-edge.bin", "staged", False, (Fill(len(_EDGE_VALUE)), _EDGE_VALUE)),
        FileSpec(
            "starts-on-edge.bin",
            "committed",
            False,
            (b"x", Fill(0), _EDGE_VALUE),
            "edited",
            (b"x", Fill(1), _EDGE_VALUE[1:]),
        ),
    ),
    broken_index=False,
)


# ---------------------------------------------------------------- reference model


def _render(pieces: Sequence[Piece], chunk: int) -> bytes:
    out = bytearray()
    for piece in pieces:
        if isinstance(piece, Fill):
            out += FILLER * ((-piece.delta - len(out)) % chunk)
        else:
            out += piece
    return bytes(out)


def _bytes_of(spec: FileSpec, pieces: Sequence[Piece], chunk: int) -> bytes:
    data = _render(pieces, chunk)
    return LINK_PREFIX + data if spec.link else data


def _working_tree_bytes(spec: FileSpec, chunk: int) -> bytes | None:
    """What git sees in the working tree for ``spec``; ``None`` when deleted."""
    if spec.change == "deleted":
        return None
    return _bytes_of(spec, spec.edit if spec.change == "edited" else spec.content, chunk)


def _secret_values(environ: Mapping[str, str]) -> list[str]:
    """Non-blank values of the built-in Secret_Variables and each listed name."""
    listed = [name.strip() for name in environ.get(SECRET_LIST, "").split(",") if name.strip()]
    values: list[str] = []
    for name in (*BUILT_IN, *listed):
        value = environ.get(name)
        if value is not None and value.strip() and value not in values:
            values.append(value)
    return values


def _index(scenario: Scenario) -> dict[str, bytes | None]:
    """Every index path with its working-tree bytes (``None``: no working-tree file)."""
    entries: dict[str, bytes | None] = {GITIGNORE_PATH: GITIGNORE}
    for spec in scenario.files:
        if spec.tracked:
            entries[spec.path] = _working_tree_bytes(spec, scenario.chunk)
    return entries


def _expected_matches(index: Mapping[str, bytes | None], values: Sequence[str]) -> set[str]:
    needles = [_utf8(value) for value in values]
    return {
        path
        for path, data in index.items()
        if data is not None and any(needle in data for needle in needles)
    }


def _files(count: int) -> str:
    return f"{count} tracked file{'' if count == 1 else 's'}"


def _status_lines(matched: int, searched: int, skipped: int) -> list[str]:
    """The stderr lines of a completed scan, per ``fse.commands.scan_secrets.report``."""
    lines: list[str] = []
    if skipped:
        lines.append(
            f"{COMMAND}: {_files(skipped)} in the index have no working-tree file "
            "(deleted, sparse checkout or submodule) and were not searched"
        )
    if matched:
        lines.append(f"{COMMAND}: {_files(matched)} of {searched} searched contain a secret value")
    else:
        lines.append(f"{COMMAND}: no secret value found in {_files(searched)}")
    return lines


# ---------------------------------------------------------------- sandbox


@contextmanager
def _sandbox(chunk: int) -> Iterator[Path]:
    """A resolved temporary root with git config isolated and the chunk size set."""
    with (
        tempfile.TemporaryDirectory(prefix="fse-p04-") as tmp,
        pytest.MonkeyPatch.context() as mp,
    ):
        root = Path(tmp).resolve()
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        mp.setenv("XDG_CONFIG_HOME", str(root / "xdg"))  # no global excludes file
        for name in _GIT_DISCOVERY_VARS:
            mp.delenv(name, raising=False)
        if chunk != REAL_CHUNK:
            mp.setattr(scanner, "_CHUNK_SIZE", chunk)
        yield root


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL
    ).stdout


def _place(repo: Path, rel: str, data: bytes, *, link: bool) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        path.unlink()
    if link:
        os.symlink(data, os.fsencode(path))
    else:
        path.write_bytes(data)


def _build(root: Path, scenario: Scenario) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / GITIGNORE_PATH).write_bytes(GITIGNORE)
    chunk = scenario.chunk
    for kind in ("committed", "staged"):
        for spec in scenario.files:
            if spec.kind == kind:
                _place(repo, spec.path, _bytes_of(spec, spec.content, chunk), link=spec.link)
        _git(repo, "add", "-A")
        if kind == "committed":
            _git(repo, *_COMMIT)
    for spec in scenario.files:
        if spec.change == "edited":
            _place(repo, spec.path, _bytes_of(spec, spec.edit, chunk), link=spec.link)
        elif spec.change == "deleted":
            (repo / spec.path).unlink()
        elif not spec.tracked:
            _place(repo, spec.path, _bytes_of(spec, spec.content, chunk), link=spec.link)
    if scenario.broken_index:
        (repo / ".git" / "index").write_bytes(b"not an index\n")
    return repo


def _git_index_paths(repo: Path) -> set[str]:
    return {os.fsdecode(p) for p in _git(repo, "ls-files", "-z", "--cached").split(b"\0") if p}


@dataclass(frozen=True, slots=True)
class _Cli:
    code: int
    out: str
    err: str


def _run_api(repo: Path, environ: Mapping[str, str]) -> ScanResult | ScanError:
    try:
        return scan(repo, EnvView(dict(environ), {}))
    except ScanError as exc:
        return exc


def _run_cli(repo: Path, project: Path, environ: Mapping[str, str]) -> _Cli:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        [COMMAND, "--repo", str(repo)],
        project_dir=project,
        environ=dict(environ),
        stdout=out,
        stderr=err,
    )
    return _Cli(code, out.getvalue(), err.getvalue())


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 4: Secret_Scanner reports exactly the matching files  # noqa: E501
@example(scenario=_AT_THE_REAL_CHUNK_EDGE)
@given(scenario=_scenarios())
def test_scanner_reports_exactly_the_matching_files(scenario: Scenario) -> None:
    values = _secret_values(scenario.environ)
    index = _index(scenario)
    expected = _expected_matches(index, values)
    deleted = {path for path, data in index.items() if data is None}

    with _sandbox(scenario.chunk) as root:
        repo = _build(root, scenario)
        # Scan the temporary working tree only, never an enclosing one.
        assert find_repo_root(repo) == repo
        if not scenario.broken_index:
            assert _git_index_paths(repo) == set(index), "generator: unexpected git index"
        project = root / "project"  # a Project folder with no .env file
        project.mkdir()
        api = _run_api(repo, scenario.environ)
        run = _run_cli(repo, project, scenario.environ)

    # Req 1.16: no secret value in any output line (and nothing needed redacting).
    for value in values:
        assert value not in run.out
        assert value not in run.err
    assert "[REDACTED]" not in run.out + run.err

    if not values:  # Req 1.15: the scan cannot run
        assert isinstance(api, NoSecretValuesError)
        assert str(api).startswith("no secret values configured")
    elif scenario.broken_index:
        assert isinstance(api, GitListingError)
        assert str(api).startswith("cannot list the repository's tracked files: ")
    if isinstance(api, ScanError):
        assert api.exit_code == EXIT_CANNOT_RUN
        assert run.code == EXIT_CANNOT_RUN
        assert run.out == ""
        assert run.err.splitlines() == [f"error: {line}" for line in str(api).splitlines()]
        return

    # Req 1.13: exactly the index entries whose working-tree bytes hold a value.
    assert isinstance(api, ScanResult)
    assert len(set(api.matches)) == len(api.matches)
    assert set(api.matches) == expected
    assert set(api.skipped) == deleted
    assert api.unreadable == ()
    searched = len(index) - len(deleted)
    assert api.searched == searched

    # Req 1.14: 0 if and only if nothing matched, else 1.
    status = EXIT_MATCHES if expected else EXIT_CLEAN
    assert api.exit_code == status
    assert run.code == status

    # Req 1.16: stdout holds the matching paths only, stderr fixed status lines only.
    assert run.out == "".join(f"{path}\n" for path in api.matches)
    assert run.err.splitlines() == _status_lines(len(expected), searched, len(deleted))
