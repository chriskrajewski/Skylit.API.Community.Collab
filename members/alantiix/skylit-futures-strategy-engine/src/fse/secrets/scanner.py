"""The Secret_Scanner: find tracked files that contain a secret value (design §1, Req 1.13-1.16).

:func:`scan` searches the working-tree bytes of every path in the git index
(``git ls-files -z --cached``, so newly staged files count) for the UTF-8 bytes
of each non-blank Secret_Variable value. It returns the matching paths and
never a value or any file content; ``fse scan-secrets`` prints only those paths
and fixed status lines.

Per index entry:

- a regular file is read in chunks, with an overlap so a value that crosses a
  chunk edge is still found;
- a symlink is searched by its link text, which is what git stores, so a link
  to a file outside the repository is never followed;
- a path with no working-tree file (deleted, outside a sparse checkout) or a
  directory (a submodule) has no content here and is counted as skipped;
- a file that cannot be read makes the scan incomplete (exit 5 unless a match
  was found), so an unreadable file never passes as clean.

Exit statuses (design "Exit codes"): 0 no file matched, 1 one or more files
matched, 5 the scan could not run (no secret value configured, the tracked
files could not be listed, or a tracked file could not be read).
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final

from fse.secrets.env import EnvView

__all__ = [
    "EXIT_CANNOT_RUN",
    "EXIT_CLEAN",
    "EXIT_MATCHES",
    "GitListingError",
    "NoSecretValuesError",
    "ScanError",
    "ScanResult",
    "contains_secret_value",
    "find_repo_root",
    "list_index_paths",
    "scan",
]

# Design "Exit codes".
EXIT_CLEAN: Final = 0
EXIT_MATCHES: Final = 1
EXIT_CANNOT_RUN: Final = 5

_CHUNK_SIZE: Final = 1 << 20
_GIT_TIMEOUT_S: Final = 60.0

# Variables that override git's repository discovery. They are dropped, so the
# repository is found from the given path alone, even inside a git hook.
_GIT_DISCOVERY_VARS: Final = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_CEILING_DIRECTORIES",
    }
)


class ScanError(Exception):
    """The scan could not run. The message states the cause and holds no secret value."""

    exit_code: ClassVar[int] = EXIT_CANNOT_RUN


class NoSecretValuesError(ScanError):
    """Every Secret_Variable is blank in the shell environment and the Project ``.env``."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names: tuple[str, ...] = tuple(names)
        super().__init__(
            "no secret values configured: every Secret_Variable "
            f"({', '.join(self.names)}) is blank in the shell environment "
            "and in the Project .env file"
        )


class GitListingError(ScanError):
    """git could not find the repository or list its tracked files."""

    def __init__(self, cause: str) -> None:
        self.cause = cause
        super().__init__(f"cannot list the repository's tracked files: {cause}")


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The outcome of one scan. Paths are relative to ``repo_root``, in git index order."""

    repo_root: Path
    searched: int  # index entries whose working-tree content was searched
    matches: tuple[str, ...]  # paths whose content holds at least one value
    unreadable: tuple[str, ...]  # paths that exist but could not be read
    skipped: tuple[str, ...]  # paths with no working-tree file, or a submodule

    @property
    def complete(self) -> bool:
        """True when every index entry with working-tree content was searched."""
        return not self.unreadable

    @property
    def exit_code(self) -> int:
        """1 when a file matched, else 5 when a file could not be read, else 0."""
        if self.matches:
            return EXIT_MATCHES
        if self.unreadable:
            return EXIT_CANNOT_RUN
        return EXIT_CLEAN


def scan(repo_root: Path, env: EnvView) -> ScanResult:
    """Search every index path of ``repo_root`` for each value in ``env.secret_values()``.

    ``repo_root`` is the working tree root (see :func:`find_repo_root`).
    Raises :class:`NoSecretValuesError` when no Secret_Variable has a value
    and :class:`GitListingError` when git cannot list the index.
    """
    needles = _needles(env.secret_values())
    if not needles:
        raise NoSecretValuesError(env.secret_names())
    raw_paths = list_index_paths(repo_root)
    root = os.fsencode(repo_root)
    overlap = max(len(n) for n in needles) - 1
    matches: list[str] = []
    unreadable: list[str] = []
    skipped: list[str] = []
    searched = 0
    for raw in raw_paths:
        shown = os.fsdecode(raw)
        try:
            found = _search(os.path.join(root, raw), needles, overlap)
        except OSError:
            unreadable.append(shown)
            continue
        if found is None:
            skipped.append(shown)
            continue
        searched += 1
        if found:
            matches.append(shown)
    return ScanResult(
        repo_root=repo_root,
        searched=searched,
        matches=tuple(matches),
        unreadable=tuple(unreadable),
        skipped=tuple(skipped),
    )


def find_repo_root(start: Path) -> Path:
    """The root of the git working tree that holds ``start`` (a file or directory)."""
    if not start.exists():
        raise GitListingError(f"{start} does not exist")
    anchor = start if start.is_dir() else start.parent
    out = _git(["rev-parse", "--show-toplevel"], cwd=anchor)
    top = os.fsdecode(out).rstrip("\n")
    if not top:
        raise GitListingError(f"{start} is not inside a git working tree")
    return Path(top).resolve()


def list_index_paths(repo_root: Path) -> list[bytes]:
    """Every path in the index of ``repo_root``, once each, as raw bytes in index order.

    Uses ``git ls-files -z --cached`` from the working tree root, so paths are
    root-relative, unquoted, and include newly staged files. An unmerged path
    appears once per stage in git's output and once here.
    """
    out = _git(["ls-files", "-z", "--cached"], cwd=repo_root)
    entries = (entry for entry in out.split(b"\0") if entry)
    return list(dict.fromkeys(entries))


def contains_secret_value(data: bytes, values: Sequence[str]) -> bool:
    """True when ``data`` holds the UTF-8 bytes of any non-empty value in ``values``.

    The matching rule :func:`scan` applies to each tracked file, for callers
    that already hold a file's bytes (``fse skilldocs check``, Req 26.17).
    """
    return _contains(data, _needles(values))


# ---------------------------------------------------------------- internals


def _needles(values: Sequence[str]) -> tuple[bytes, ...]:
    """The UTF-8 bytes of each non-empty value, without repeats."""
    encoded = (_utf8(value) for value in values)
    return tuple(dict.fromkeys(b for b in encoded if b))


def _utf8(value: str) -> bytes:
    # A shell value that was not valid UTF-8 arrives with surrogate escapes;
    # "surrogateescape" gives back the original bytes.
    try:
        return value.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass")


def _search(path: bytes, needles: tuple[bytes, ...], overlap: int) -> bool | None:
    """Whether ``path`` holds a needle; ``None`` when it has no content to search.

    Raises ``OSError`` when the path exists but cannot be read.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError, NotADirectoryError:
        return None  # in the index, not in the working tree
    if stat.S_ISLNK(info.st_mode):
        return _contains(os.readlink(path), needles)
    if not stat.S_ISREG(info.st_mode):
        return None  # a submodule directory, or a special file git cannot track
    tail = b""
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            window = tail + chunk
            if _contains(window, needles):
                return True
            tail = window[-overlap:] if overlap else b""
    return False


def _contains(data: bytes, needles: tuple[bytes, ...]) -> bool:
    return any(needle in data for needle in needles)


def _git(args: list[str], *, cwd: Path) -> bytes:
    """stdout of ``git <args>`` run in ``cwd``; :class:`GitListingError` on any failure."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_DISCOVERY_VARS}
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
        )
    except FileNotFoundError:
        raise GitListingError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitListingError(f"git {args[0]} timed out after {_GIT_TIMEOUT_S:g} s") from None
    except OSError as exc:
        raise GitListingError(f"git could not be run ({exc.strerror or exc})") from None
    if result.returncode != 0:
        lines = os.fsdecode(result.stderr).strip().splitlines()
        cause = lines[0] if lines else f"git {args[0]} exited with status {result.returncode}"
        raise GitListingError(cause)
    return result.stdout
