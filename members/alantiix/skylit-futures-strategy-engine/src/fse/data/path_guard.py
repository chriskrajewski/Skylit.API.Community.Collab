"""Cache and output path guard (design §1 "Path guard", Req 1.10-1.11).

Skylit Snapshot values, bars and dark-pool prints must never land where git
could commit them to this public repository. Every configured Data_Cache or
output directory goes through :func:`check_output_dirs` before the first write:

1. The path is expanded (``~``) and resolved to an absolute path with symlinks
   followed, so ``..`` segments and links cannot hide where files would go.
2. ``git rev-parse --show-toplevel``, run from the nearest existing ancestor,
   finds the working tree that holds the path, if any. Any working tree counts,
   not only the one this package lives in.
3. The working tree root itself is always rejected: git never ignores it,
   whatever ``.gitignore`` says, so files written there can be committed.
4. Anywhere else inside a working tree, ``git check-ignore -q`` must report the
   directory itself, or one of its ancestors, as an ignored directory. Then git
   never looks inside it and no negation can re-include a file written there.
   See :func:`_check_ignore_as_dir` for how a missing directory is checked.

A path outside every working tree, or ignored by git, passes. Any other path is
rejected with a message that names it. :func:`enforce_output_dirs` prints one
error line per rejected path and exits with status 2 (invalid input).

The guard fails closed. When git cannot answer (not installed, timed out, or the
repository is unreadable) and an ancestor directory holds a ``.git`` entry, the
path is rejected. The guard never creates a file or directory in a working tree.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, TextIO

__all__ = [
    "EXIT_INVALID_INPUT",
    "PathGuardError",
    "PathRejection",
    "check_output_dir",
    "check_output_dirs",
    "enforce_output_dirs",
    "resolve_output_dir",
]

# Design "Exit codes": 2 = invalid input, which includes a cache or output path.
EXIT_INVALID_INPUT: Final = 2

_GIT_TIMEOUT_S: Final = 30.0

# Variables that override git's repository discovery. The guard's git calls run
# without them, so the working tree is found from the path alone. A git hook that
# runs the test suite sets GIT_DIR and GIT_INDEX_FILE, for example.
_GIT_DISCOVERY_VARS: Final = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_CEILING_DIRECTORIES",
    }
)

type PathLike = str | os.PathLike[str]


@dataclass(frozen=True, slots=True)
class PathRejection:
    """One configured directory the guard refused."""

    label: str
    configured: str
    resolved: Path
    message: str


class PathGuardError(Exception):
    """One or more configured directories could put data under git control."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, rejections: Sequence[PathRejection]) -> None:
        self.rejections: tuple[PathRejection, ...] = tuple(rejections)
        super().__init__("\n".join(r.message for r in self.rejections))


def resolve_output_dir(path: PathLike) -> Path:
    """Absolute path with ``~`` expanded and symlinks followed.

    Relative paths resolve against the current directory. The path need not exist.
    """
    return Path(path).expanduser().resolve()


def check_output_dirs(paths: Mapping[str, PathLike]) -> dict[str, Path]:
    """Resolve and check each ``label -> path``; return the resolved paths.

    Every path is checked before anything is raised, so one
    :class:`PathGuardError` lists every rejected directory. Writes nothing.
    """
    resolved: dict[str, Path] = {}
    rejections: list[PathRejection] = []
    for label, configured in paths.items():
        target = resolve_output_dir(configured)
        resolved[label] = target
        rejection = _inspect(label, os.fspath(configured), target)
        if rejection is not None:
            rejections.append(rejection)
    if rejections:
        raise PathGuardError(rejections)
    return resolved


def check_output_dir(path: PathLike, *, label: str = "output directory") -> Path:
    """Single-path form of :func:`check_output_dirs`."""
    return check_output_dirs({label: path})[label]


def enforce_output_dirs(
    paths: Mapping[str, PathLike], *, stream: TextIO | None = None
) -> dict[str, Path]:
    """:func:`check_output_dirs`, printing each rejection and exiting with status 2.

    Each rejected path gets one ``error:`` line on ``stream`` (default stderr),
    then :class:`SystemExit` is raised with :data:`EXIT_INVALID_INPUT`.
    """
    try:
        return check_output_dirs(paths)
    except PathGuardError as exc:
        out = sys.stderr if stream is None else stream
        for rejection in exc.rejections:
            print(f"error: {rejection.message}", file=out)
        out.flush()
        raise SystemExit(exc.exit_code) from exc


# ---------------------------------------------------------------- internals


def _inspect(label: str, configured: str, target: Path) -> PathRejection | None:
    """None when files may be written under ``target``, else the rejection."""
    shown = str(target) if configured == str(target) else f"{target} (configured as {configured})"

    def reject(reason: str) -> PathRejection:
        return PathRejection(
            label=label,
            configured=configured,
            resolved=target,
            message=f"{label} {shown} {reason} No file was written.",
        )

    def cannot_check(marker: Path, cause: str) -> PathRejection:
        return reject(
            f"is under {marker}, which holds a .git entry, and git could not check "
            f"whether git ignores it ({cause})."
        )

    anchor = _nearest_existing_dir(target)
    top = _git(["rev-parse", "--show-toplevel", "--absolute-git-dir"], cwd=anchor)
    lines = os.fsdecode(top.stdout).splitlines() if top is not None else []
    if top is None or top.returncode != 0 or len(lines) != 2:
        marker = _git_marker(anchor)
        if marker is None:
            return None  # outside every working tree
        return cannot_check(marker, _cause(top))

    toplevel = Path(lines[0]).resolve()
    if not target.is_relative_to(toplevel):
        return None  # a work tree set elsewhere (core.worktree) cannot hold the path

    not_ignored = reject(
        f"is inside the git working tree {toplevel} and git does not ignore it. "
        "Use a directory outside the repository, or add this directory to .gitignore."
    )
    rel = target.relative_to(toplevel)
    if not rel.parts:
        # git never ignores the working tree root, even when check-ignore says it
        # matches a pattern such as "*", so files written there can be committed.
        return not_ignored
    ignored = _check_ignore_as_dir(toplevel, Path(lines[1]), rel)
    if isinstance(ignored, str):
        return cannot_check(toplevel, ignored)
    if ignored.returncode == 0:
        return None
    if ignored.returncode == 1:
        return not_ignored
    return cannot_check(toplevel, _cause(ignored))


def _check_ignore_as_dir(
    toplevel: Path, git_dir: Path, rel: Path
) -> subprocess.CompletedProcess[bytes] | str:
    """``git check-ignore -q`` on ``toplevel / rel`` as a directory, or why it could not run.

    Exit status 0 means ``rel`` or one of its ancestors is an ignored directory.
    Git needs the directory to exist for that answer: without a trailing slash a
    directory-only pattern such as ``cache/`` cannot match a missing directory,
    and with one git also matches the empty name after the slash, so patterns such
    as ``*`` or ``cache/*`` (or ``*/`` in the directory's own ``.gitignore``)
    report a directory as ignored while ``!*.bin`` re-includes files written in it.

    So git runs on a scratch work tree in the system temporary directory that holds
    ``rel`` as an empty directory and a copy of each ``.gitignore`` above it, with
    the repository's git dir for ``info/exclude``, config and index. A
    ``.gitignore`` above ``rel`` that is not a regular file fails closed. Nothing
    is written to the working tree.
    """
    temp_root = Path(tempfile.gettempdir()).resolve()
    if temp_root.is_relative_to(toplevel):
        return f"the temporary directory {temp_root} is inside the working tree"
    try:
        with tempfile.TemporaryDirectory(prefix="fse-path-guard-", dir=temp_root) as scratch:
            mirror = Path(scratch)
            mirror.joinpath(rel).mkdir(parents=True)
            for depth in range(len(rel.parts)):
                parts = rel.parts[:depth]
                source = toplevel.joinpath(*parts, ".gitignore")
                try:
                    mode = source.lstat().st_mode
                except FileNotFoundError, NotADirectoryError:
                    continue  # the directory does not exist yet, or holds no .gitignore
                if not stat.S_ISREG(mode):
                    return f"{source} is not a regular file"
                mirror.joinpath(*parts, ".gitignore").write_bytes(source.read_bytes())
            result = _git(
                [
                    f"--git-dir={git_dir}",
                    f"--work-tree={mirror}",
                    "check-ignore",
                    "-q",
                    "--",
                    rel.as_posix(),
                ],
                cwd=mirror,
            )
    except OSError as exc:
        return f"the scratch work tree could not be built: {exc}"
    return _cause(None) if result is None else result


def _nearest_existing_dir(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return Path(path.anchor)


def _git_marker(start: Path) -> Path | None:
    """Nearest directory at or above ``start`` that holds a ``.git`` entry."""
    for candidate in (start, *start.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes] | None:
    """Run git in ``cwd``; None when git could not be run or timed out."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_DISCOVERY_VARS}
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
        )
    except OSError, subprocess.TimeoutExpired:
        return None


def _cause(result: subprocess.CompletedProcess[bytes] | None) -> str:
    if result is None:
        return "git could not be run"
    lines = os.fsdecode(result.stderr).strip().splitlines()
    return lines[0] if lines else f"git exit status {result.returncode}"
