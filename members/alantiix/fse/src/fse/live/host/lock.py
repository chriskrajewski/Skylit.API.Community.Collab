"""One runner per live-state directory (design 10.8).

:class:`RunnerLock` takes ``fcntl.flock(LOCK_EX | LOCK_NB)`` on
``<live-state>/runner.lock``. ``fse run``, ``fse paper`` and ``fse live``
(which shares ``fse paper``'s run path) take it; while another process (or
another open of the file) holds it, :meth:`RunnerLock.acquire` raises
:class:`RunnerLockHeld` (exit 2, "another runner holds <path>"). The file
holds the holder's pid, for the message only. The lock ends with the process,
so a crash never leaves it held.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Final, Self

__all__ = ["RUNNER_LOCK_FILE_NAME", "RunnerLock", "RunnerLockHeld"]

RUNNER_LOCK_FILE_NAME: Final = "runner.lock"
EXIT_INVALID_INPUT: Final = 2


class RunnerLockHeld(Exception):
    """Another runner holds the live-state directory's lock (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, path: Path, pid: str | None) -> None:
        self.path = path
        holder = f" (pid {pid})" if pid else ""
        super().__init__(f"another runner holds {path}{holder}")


class RunnerLock:
    """An exclusive, non-blocking ``flock`` on ``<state_dir>/runner.lock``."""

    __slots__ = ("_fd", "_path")

    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / RUNNER_LOCK_FILE_NAME
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Take the lock; :class:`RunnerLockHeld` when another holder has it."""
        if self._fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = _read_pid(fd)
            os.close(fd)
            raise RunnerLockHeld(self._path, pid) from None
        except BaseException:
            os.close(fd)
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._fd = fd

    def release(self) -> None:
        """Release the lock (the file stays)."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _read_pid(fd: int) -> str | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        text = os.read(fd, 32).decode("ascii", "replace").strip()
    except OSError:
        return None
    return text if text.isdigit() else None
