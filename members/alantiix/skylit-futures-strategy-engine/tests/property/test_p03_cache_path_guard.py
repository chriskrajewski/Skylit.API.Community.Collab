"""Property 3: Cache path guard.

*For any* configured Data_Cache or output path relative to a temporary git
repository, the command writes files only if the resolved path is outside the
working tree or ignored by git; otherwise it writes nothing, names the path and
exits non-zero.

Each example builds a fresh repository with ``git init`` inside its own
temporary directory, so the real repository is never written. The generated
``.gitignore`` lines come from a small grammar that :func:`_expected_allowed`
models exactly: a directory is ignored when it or an ancestor is excluded,
because git never looks inside an excluded directory, and the working tree root
is never excluded. A second, model-free check writes a probe file into every
directory the guard passes and asks ``git ls-files --others --exclude-standard``
whether git could commit it.

**Validates: Requirements 1.10, 1.11**
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
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from fse.data.path_guard import EXIT_INVALID_INPUT, PathGuardError, enforce_output_dirs

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

# Lower-case names only: macOS repositories default to core.ignorecase=true.
NAMES = ("cache", "runs", "data", "logs", "a", "b")
REPO = "repo"
OUTSIDE = ".skylit-fse"  # sibling of the repository, like the ~/.skylit-fse defaults (Req 1.10)
LINK = "lnk"  # repo/lnk -> .skylit-fse/linked and .skylit-fse/lnk -> a repo directory
LINKED = "linked"
SEGMENTS = (*NAMES, REPO, OUTSIDE, LINK, ".", "..")
LABELS = ("cache directory", "runs directory", "recordings directory")
# A digit first and a .bin suffix: no generated directory pattern matches a probe
# name, and only the "!*.bin" line refers to one.
PROBE = "0-probe-{}.bin"
REINCLUDE_PROBES = "!*.bin"

_GIT_DISCOVERY_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
)

type Form = Literal["absolute", "relative", "home"]
type Anchor = Literal["repo", ".skylit-fse"]
FORMS: tuple[Form, ...] = ("absolute", "relative", "home")
ANCHORS: tuple[Anchor, ...] = ("repo", ".skylit-fse")


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Configured:
    """One configured directory: an anchor under the temporary root plus segments."""

    form: Form  # absolute; relative to the cwd (the temporary root); or ~-prefixed (HOME = root)
    anchor: Anchor
    segments: tuple[str, ...]

    def text(self, root: Path) -> str:
        rel = "/".join((self.anchor, *self.segments))
        if self.form == "absolute":
            return f"{root}/{rel}"
        if self.form == "home":
            return f"~/{rel}"
        return rel


@dataclass(frozen=True, slots=True)
class Scenario:
    root_ignore: tuple[str, ...]  # repo/.gitignore
    project: tuple[str, ...]  # a nested directory holding its own .gitignore; () for none
    project_ignore: tuple[str, ...]
    existing: tuple[tuple[str, ...], ...]  # repo directories created up front
    link_in: bool  # repo/lnk -> .skylit-fse/linked
    link_out: tuple[str, ...] | None  # .skylit-fse/lnk -> repo/<parts>
    paths: tuple[Configured, ...]


_NAME = st.sampled_from(NAMES)
_PARTS = st.lists(_NAME, min_size=1, max_size=3).map(tuple)
_BODY = st.one_of(
    st.just("*"),
    _NAME,  # cache: any depth below the .gitignore
    _NAME.flatmap(lambda n: st.integers(0, len(n) - 1).map(lambda k: f"{n[:k]}*")),  # ca*
    _NAME.map(lambda n: f"**/{n}"),  # **/cache: any depth
    _NAME.map(lambda n: f"/{n}"),  # /cache: this directory only
    st.tuples(st.sampled_from(("", "/")), _NAME, _NAME).map(
        lambda t: f"{t[0]}{t[1]}/{t[2]}"  # a/b and /a/b: anchored by the slash
    ),
)


@st.composite
def _pattern(draw: st.DrawFn) -> str:
    """One .gitignore line from the grammar :func:`_parse` covers."""
    if draw(st.integers(0, 4)) == 0:
        return REINCLUDE_PROBES  # matches probe files only, never a directory
    negated = "!" if draw(st.booleans()) else ""
    trailing = "/" if draw(st.booleans()) else ""
    return f"{negated}{draw(_BODY)}{trailing}"


@st.composite
def _configured(draw: st.DrawFn) -> Configured:
    segments: list[str] = []
    depth = 0  # names minus ".." after the anchor; -1 is the temporary root itself
    for _ in range(draw(st.integers(0, 4))):
        segment = draw(st.sampled_from(SEGMENTS if depth >= 0 else SEGMENTS[:-1]))
        if segment == "..":
            depth -= 1
        elif segment != ".":
            depth += 1
        segments.append(segment)
    return Configured(draw(st.sampled_from(FORMS)), draw(st.sampled_from(ANCHORS)), tuple(segments))


@st.composite
def _scenarios(draw: st.DrawFn) -> Scenario:
    project = draw(st.lists(_NAME, max_size=2).map(tuple))
    return Scenario(
        root_ignore=tuple(draw(st.lists(_pattern(), max_size=4))),
        project=project,
        project_ignore=tuple(draw(st.lists(_pattern(), max_size=3))) if project else (),
        existing=tuple(draw(st.lists(_PARTS, max_size=3))),
        link_in=draw(st.booleans()),
        link_out=draw(st.none() | _PARTS),
        paths=tuple(draw(st.lists(_configured(), min_size=1, max_size=len(LABELS)))),
    )


# The working tree root under an "ignore everything but *.bin" file: git never
# ignores the root itself, and a .bin file written there is committable.
_ROOT_UNDER_STAR = Scenario(
    root_ignore=("*", REINCLUDE_PROBES),
    project=(),
    project_ignore=(),
    existing=(),
    link_in=False,
    link_out=None,
    paths=(Configured("absolute", "repo", ()),),
)


def _cache_holding(project_ignore: tuple[str, ...]) -> Scenario:
    """repo/cache, configured by absolute path, with its own .gitignore."""
    return Scenario(
        root_ignore=(),
        project=("cache",),
        project_ignore=project_ignore,
        existing=(),
        link_in=False,
        link_out=None,
        paths=(Configured("absolute", "repo", ("cache",)),),
    )


# A directory's own .gitignore never applies to the directory: `git check-ignore
# cache` reports no match, so git does not ignore cache/ and the guard rejects it.
# `git check-ignore cache/` matched "*" or "*/" in cache/.gitignore against the
# empty name after the slash; with "*/" the probe file in cache/ is committable.
_OWN_GITIGNORE_STAR = _cache_holding(("*",))
_OWN_GITIGNORE_DIRS_ONLY = _cache_holding(("*/",))
# `git check-ignore cache/` matched "*" against the empty name although "!cache/"
# keeps cache/ itself, and "!*.bin" re-includes the probe written there.
_NEGATED_UNDER_STAR = Scenario(
    root_ignore=("*", "!cache/", REINCLUDE_PROBES),
    project=(),
    project_ignore=(),
    existing=(),
    link_in=False,
    link_out=None,
    paths=(Configured("absolute", "repo", ("cache",)),),
)
# "cache/" in the parent's .gitignore ignores cache/ before it exists.
_MISSING_DIR_PATTERN = Scenario(
    root_ignore=("cache/",),
    project=(),
    project_ignore=(),
    existing=(),
    link_in=False,
    link_out=None,
    paths=(Configured("absolute", "repo", ("cache", "a")),),
)


# ---------------------------------------------------------------- reference model


@dataclass(frozen=True, slots=True)
class _Rule:
    negated: bool
    anchored: bool  # relative to the .gitignore's directory, one glob per component
    globs: tuple[str, ...]


def _parse(line: str) -> _Rule:
    negated = line.startswith("!")
    # Every candidate below is a directory, so a trailing slash changes nothing.
    body = line.removeprefix("!").removesuffix("/").removeprefix("**/")
    anchored = "/" in body
    return _Rule(negated, anchored, tuple(body.removeprefix("/").split("/")))


def _matches(rule: _Rule, rel: tuple[str, ...]) -> bool:
    if rule.anchored:
        return len(rel) == len(rule.globs) and all(map(fnmatchcase, rel, rule.globs, strict=True))
    return fnmatchcase(rel[-1], rule.globs[0])


type _IgnoreFile = tuple[tuple[str, ...], list[_Rule]]  # (directory parts, lines)


def _excluded(path: tuple[str, ...], ignore_files: Sequence[_IgnoreFile]) -> bool:
    """Git's verdict for one directory: the last matching line of the deepest file wins."""
    for base, rules in sorted(ignore_files, key=lambda f: len(f[0]), reverse=True):
        if len(path) <= len(base) or path[: len(base)] != base:
            continue  # a .gitignore applies to paths below its own directory
        for rule in reversed(rules):
            if _matches(rule, path[len(base) :]):
                return not rule.negated
    return False


def _expected_allowed(target: Path, repo: Path, scenario: Scenario) -> bool:
    if not target.is_relative_to(repo):
        return True
    ignore_files: list[_IgnoreFile] = [((), [_parse(line) for line in scenario.root_ignore])]
    if scenario.project:
        ignore_files.append((scenario.project, [_parse(line) for line in scenario.project_ignore]))
    parts = target.relative_to(repo).parts
    return any(_excluded(parts[:k], ignore_files) for k in range(1, len(parts) + 1))


# ---------------------------------------------------------------- sandbox


@contextmanager
def _sandbox() -> Iterator[Path]:
    """A resolved temporary root, used as HOME and cwd, with git config isolated."""
    with (
        tempfile.TemporaryDirectory(prefix="fse-p03-") as tmp,
        pytest.MonkeyPatch.context() as mp,
    ):
        root = Path(tmp).resolve()
        mp.setenv("HOME", str(root))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        mp.setenv("XDG_CONFIG_HOME", str(root / "xdg"))  # no global excludes file
        for name in _GIT_DISCOVERY_VARS:
            mp.delenv(name, raising=False)
        mp.chdir(root)
        yield root


def _build(root: Path, scenario: Scenario) -> Path:
    repo = root / REPO
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / ".gitignore").write_text("".join(f"{x}\n" for x in scenario.root_ignore), "utf-8")
    for parts in scenario.existing:
        repo.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    if scenario.project:
        project = repo.joinpath(*scenario.project)
        project.mkdir(parents=True, exist_ok=True)
        (project / ".gitignore").write_text(
            "".join(f"{x}\n" for x in scenario.project_ignore), "utf-8"
        )
    outside = root / OUTSIDE
    outside.mkdir()
    if scenario.link_in:
        (outside / LINKED).mkdir()
        (repo / LINK).symlink_to(outside / LINKED, target_is_directory=True)
    if scenario.link_out is not None:
        target = repo.joinpath(*scenario.link_out)
        target.mkdir(parents=True, exist_ok=True)
        (outside / LINK).symlink_to(target, target_is_directory=True)
    return repo


def _snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    """Mode, size and mtime of every entry under ``root``, ``.git`` included."""
    entries: dict[str, tuple[int, int, int]] = {}
    for path in root.rglob("*"):
        info = path.lstat()
        entries[path.relative_to(root).as_posix()] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return entries


def _run_guard(paths: Mapping[str, str]) -> tuple[dict[str, Path] | None, SystemExit | None, str]:
    stream = io.StringIO()
    try:
        return enforce_output_dirs(paths, stream=stream), None, stream.getvalue()
    except SystemExit as exc:
        return None, exc, stream.getvalue()


def _write_probe(configured: str, name: str) -> None:
    """Write a file the way a command would: through the configured path."""
    directory = os.path.expanduser(configured)
    os.makedirs(directory, exist_ok=True)
    Path(directory, name).write_bytes(b"probe\n")


def _committable(repo: Path) -> set[str]:
    """Untracked files git does not ignore, relative to ``repo``."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    return {os.fsdecode(p) for p in out.split(b"\0") if p}


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 3: Cache path guard
@example(scenario=_ROOT_UNDER_STAR)
@example(scenario=_OWN_GITIGNORE_STAR)
@example(scenario=_OWN_GITIGNORE_DIRS_ONLY)
@example(scenario=_NEGATED_UNDER_STAR)
@example(scenario=_MISSING_DIR_PATTERN)
@given(scenario=_scenarios())
def test_guard_passes_only_paths_outside_the_tree_or_ignored(scenario: Scenario) -> None:
    with _sandbox() as root:
        repo = _build(root, scenario)
        paths = {label: c.text(root) for label, c in zip(LABELS, scenario.paths, strict=False)}
        resolved = {
            label: Path(os.path.realpath(os.path.expanduser(text))) for label, text in paths.items()
        }
        # Generator invariant: every path, and so every probe below, stays in the sandbox.
        assert all(target.is_relative_to(root) for target in resolved.values())

        before = _snapshot(root)
        returned, exited, stderr = _run_guard(paths)
        assert _snapshot(root) == before, "the guard wrote to the file system"

        if exited is None:
            assert returned == resolved
            assert stderr == ""
            rejected: list[str] = []
        else:
            assert exited.code == EXIT_INVALID_INPUT  # 2: invalid input, non-zero
            cause = exited.__cause__
            assert isinstance(cause, PathGuardError)
            assert cause.exit_code == EXIT_INVALID_INPUT
            rejected = [r.label for r in cause.rejections]
            assert [r.resolved for r in cause.rejections] == [resolved[x] for x in rejected]
            lines = stderr.splitlines()
            assert len(lines) == len(rejected), stderr
            for line, label in zip(lines, rejected, strict=True):
                assert line.startswith(f"error: {label} {resolved[label]} "), line

        # Model-free safety check: a file written into a passed directory lands at
        # the resolved path, and inside the working tree git cannot commit it.
        passed_inside: dict[str, str] = {}
        for index, label in enumerate(paths):
            if label in rejected:
                continue
            probe = PROBE.format(index)
            _write_probe(paths[label], probe)
            assert (resolved[label] / probe).is_file()
            if resolved[label].is_relative_to(repo):
                passed_inside[label] = (resolved[label] / probe).relative_to(repo).as_posix()
        if passed_inside:
            committable = _committable(repo)
            leaked = {label: rel for label, rel in passed_inside.items() if rel in committable}
            assert not leaked, f"guard passed directories whose files git can commit: {leaked}"

        # Git never ignores the working tree root, whatever .gitignore says.
        root_passed = [x for x in paths if resolved[x] == repo and x not in rejected]
        assert not root_passed, f"guard passed the working tree root {repo}: {root_passed}"

        # Exact verdict against the reference model.
        expected = [x for x in paths if not _expected_allowed(resolved[x], repo, scenario)]
        assert rejected == expected
