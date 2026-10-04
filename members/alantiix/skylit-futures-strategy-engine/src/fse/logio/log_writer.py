"""The Log_Writer: the one way the Project writes, prints or sends text (design §1, Req 1.9).

Every sink redacts through one shared :class:`~fse.logio.redact.Redactor`
before a byte leaves the process:

- console: :meth:`LogWriter.echo` (stdout), :meth:`LogWriter.error` and
  :meth:`LogWriter.exception` (stderr);
- files: :meth:`LogWriter.write_text` and :meth:`LogWriter.write_json` (reports
  and manifests; temp file, fsync, rename), :meth:`LogWriter.append_line` and
  :meth:`LogWriter.append_json` (``O_APPEND``, optional fsync), and
  :meth:`LogWriter.open_lines` (decision logs, Fetch_Logs, ``.jsonl.gz``
  recordings);
- payloads: :meth:`LogWriter.json_text` and :meth:`LogWriter.json_bytes`
  (Notifier webhook bodies, Finding_Cards), :meth:`LogWriter.redact_json`
  (Narrator input);
- ``logging`` and uncaught errors: :meth:`LogWriter.install` (see
  :class:`InstalledHooks`).

JSON sinks encode canonical JSON first and redact the encoded text; the
Redactor knows the JSON-escaped form of each value. Text is written as UTF-8
with ``\\n`` line ends on every platform. Created files are private (``0600``).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import io
import logging
import os
import sys
import tempfile
import threading
import traceback
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import partial
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final, Self, TextIO

from fse.logio import canonical_json
from fse.logio.redact import REDACTED, Redactor

if TYPE_CHECKING:
    from fse.logio.canonical_json import JsonValue
    from fse.secrets.env import EnvView

__all__ = [
    "HTTP_LOGGERS",
    "HTTP_LOG_CAP",
    "InstalledHooks",
    "LineSink",
    "LogWriter",
    "RedactingFilter",
]

# Loggers that can log request lines and headers. Records below the cap are
# never emitted, so a header value cannot reach a log (design §1).
HTTP_LOGGERS: Final = ("httpx", "httpcore")
HTTP_LOG_CAP: Final = logging.WARNING

type PathLike = str | os.PathLike[str]

_ENCODING: Final = "utf-8"
_ENCODING_ERRORS: Final = "backslashreplace"  # a sink never fails on a lone surrogate
_FILE_MODE: Final = 0o600
_O_BINARY: Final[int] = getattr(os, "O_BINARY", 0)  # Windows only; 0 elsewhere
_REDACTION_FAILED: Final = f"{REDACTED} (output withheld: redaction failed)\n"


class LogWriter:
    """Redacting writer for console output, files, payloads and error output."""

    __slots__ = ("_redactor", "_stderr", "_stdout")

    def __init__(
        self,
        redactor: Redactor,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        """``stdout`` and ``stderr`` default to ``sys.stdout`` and ``sys.stderr`` at write time."""
        self._redactor = redactor
        self._stdout = stdout
        self._stderr = stderr

    @classmethod
    def from_env(
        cls, env: EnvView, *, stdout: TextIO | None = None, stderr: TextIO | None = None
    ) -> LogWriter:
        """A writer whose Redactor holds every non-blank Secret_Variable value in ``env``."""
        return cls(Redactor.from_env(env), stdout=stdout, stderr=stderr)

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def add_secret(self, value: str) -> None:
        """Register a value learned at run time, such as a ProjectX session token."""
        self._redactor.add(value)

    # ------------------------------------------------------------ text and payloads

    def redact(self, text: str) -> str:
        return self._redactor.redact(text)

    def json_text(self, obj: object) -> str:
        """Canonical JSON of ``obj``, redacted, without a trailing newline."""
        return self._redactor.redact(canonical_json.dumps(obj))

    def json_bytes(self, obj: object) -> bytes:
        """:meth:`json_text` as bytes, for a webhook body."""
        return self.json_text(obj).encode(_ENCODING, _ENCODING_ERRORS)

    def redact_json(self, obj: object) -> JsonValue:
        """``obj`` as plain JSON values with every string redacted, keys included."""
        return self._redactor.redact_json(canonical_json.to_jsonable(obj))

    def format_exception(self, exc: BaseException) -> str:
        """The redacted traceback of ``exc``, chained exceptions and notes included."""
        return self._redactor.redact("".join(traceback.format_exception(exc)))

    # ------------------------------------------------------------ console

    def echo(self, text: str = "") -> None:
        """Print one redacted line to stdout."""
        _write_stream(self._out(), self.redact(text))

    def error(self, text: str) -> None:
        """Print one redacted line to stderr."""
        _write_stream(self._err(), self.redact(text))

    def exception(self, exc: BaseException, *, message: str | None = None) -> None:
        """Print ``message`` (if given) and the redacted traceback of ``exc`` to stderr."""
        text = "".join(traceback.format_exception(exc))
        if message is not None:
            text = f"{message}\n{text}"
        _write_stream(self._err(), self.redact(text))

    def _out(self) -> TextIO | None:
        return sys.stdout if self._stdout is None else self._stdout

    def _err(self) -> TextIO | None:
        return sys.stderr if self._stderr is None else self._stderr

    def _report_uncaught(self, build: Callable[[], str]) -> None:
        """Write hook output to stderr. Never raises, and never writes unredacted text."""
        try:
            text = self.redact(build())
        except Exception:
            text = _REDACTION_FAILED
        with contextlib.suppress(Exception):
            _write_stream(self._err(), text)

    # ------------------------------------------------------------ files

    def write_text(self, path: PathLike, text: str) -> Path:
        """Replace ``path`` with the redacted ``text``: temp file, fsync, rename."""
        target = Path(path)
        _atomic_write(target, self.redact(text).encode(_ENCODING, _ENCODING_ERRORS))
        return target

    def write_json(self, path: PathLike, obj: object) -> Path:
        """Replace ``path`` with one canonical JSON line (redacted) and a newline."""
        target = Path(path)
        _atomic_write(target, self.json_bytes(obj) + b"\n")
        return target

    def append_line(self, path: PathLike, text: str, *, fsync: bool = False) -> None:
        """Append the redacted ``text`` and a newline with ``O_APPEND``; fsync when asked."""
        _append(Path(path), _line_bytes(self.redact(text)), fsync=fsync)

    def append_json(self, path: PathLike, obj: object, *, fsync: bool = False) -> None:
        """Append one canonical JSON line (redacted) with ``O_APPEND``; fsync when asked."""
        _append(Path(path), self.json_bytes(obj) + b"\n", fsync=fsync)

    def open_lines(self, path: PathLike, *, compress: bool = False) -> LineSink:
        """Open ``path`` for appending redacted lines; ``compress`` writes gzip members."""
        return LineSink(self._redactor, Path(path), compress=compress)

    # ------------------------------------------------------------ hooks

    def logging_filter(self) -> RedactingFilter:
        """A filter for a ``logging`` handler that this writer's Redactor drives."""
        return RedactingFilter(self._redactor)

    def install(self, *, loop: asyncio.AbstractEventLoop | None = None) -> InstalledHooks:
        """Redact ``logging`` records and uncaught-error output process-wide.

        Returns the :class:`InstalledHooks`; its ``uninstall()`` (or leaving it
        as a context manager) restores every previous hook. With ``loop``, the
        loop's exception handler is replaced too.
        """
        hooks = InstalledHooks(self)
        hooks._install()
        if loop is not None:
            hooks.install_asyncio(loop)
        return hooks

    def __repr__(self) -> str:
        return f"LogWriter({self._redactor!r})"


class LineSink:
    """An open line file (plain or gzip); each line is redacted before it is written.

    Opened in append mode. A compressed sink appends a new gzip member with no
    file name and a zero timestamp, so equal lines give equal bytes.
    """

    __slots__ = ("_file", "_lock", "_path", "_raw", "_redactor")

    def __init__(self, redactor: Redactor, path: Path, *, compress: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _O_BINARY, _FILE_MODE)
        try:
            raw = os.fdopen(fd, "ab")
        except BaseException:
            os.close(fd)
            raise
        self._raw: io.BufferedWriter = raw
        self._file: io.BufferedWriter | gzip.GzipFile = (
            gzip.GzipFile(filename="", mode="ab", fileobj=raw, mtime=0) if compress else raw
        )
        self._redactor = redactor
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def closed(self) -> bool:
        return self._raw.closed

    def write_line(self, text: str) -> None:
        """Write the redacted ``text`` and a newline."""
        data = _line_bytes(self._redactor.redact(text))
        with self._lock:
            self._file.write(data)

    def write_json(self, obj: object) -> None:
        """Write one canonical JSON line, redacted."""
        self.write_line(canonical_json.dumps(obj))

    def flush(self, *, fsync: bool = False) -> None:
        with self._lock:
            self._file.flush()
            if self._file is not self._raw:
                self._raw.flush()
            if fsync:
                os.fsync(self._raw.fileno())

    def close(self) -> None:
        with self._lock:
            try:
                if self._file is not self._raw:
                    self._file.close()  # writes the gzip trailer; leaves the raw file open
            finally:
                self._raw.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"LineSink({str(self._path)!r})"


# ---------------------------------------------------------------- logging


def _standard_record_attrs() -> frozenset[str]:
    sample = logging.LogRecord("fse", logging.INFO, __file__, 0, "", None, None)
    return frozenset(vars(sample)) | {"message", "asctime"}


_STANDARD_RECORD_ATTRS: Final = _standard_record_attrs()
_EXCEPTION_FORMATTER: Final = logging.Formatter()


def _is_http_logger(name: str) -> bool:
    return any(name == top or name.startswith(top + ".") for top in HTTP_LOGGERS)


class RedactingFilter(logging.Filter):
    """Redacts a record in place: message and arguments, traceback, stack, and extras.

    The message is formatted once (``getMessage()``) and its arguments
    dropped. A traceback is formatted into ``exc_text`` and ``exc_info`` is
    cleared, so no formatter can print an unredacted copy. String extras (from
    ``extra=``) are redacted too. Records below WARNING from the ``httpx`` and
    ``httpcore`` loggers, children included, are dropped.
    """

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < HTTP_LOG_CAP and _is_http_logger(record.name):
            return False
        self.redact_record(record)
        return True

    def redact_record(self, record: logging.LogRecord) -> None:
        """Redact ``record`` in place; on any internal error, withhold its text."""
        try:
            self._redact_record(record)
        except Exception:
            record.msg = _REDACTION_FAILED.rstrip("\n")
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None

    def _redact_record(self, record: logging.LogRecord) -> None:
        redact = self._redactor.redact
        try:
            message = record.getMessage()
        except Exception:
            message = f"{record.msg!r} % {record.args!r} (log message formatting failed)"
        record.msg = redact(message)
        record.args = ()
        if record.exc_info:
            record.exc_text = redact(_EXCEPTION_FORMATTER.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_RECORD_ATTRS and isinstance(value, str):
                setattr(record, key, redact(value))

    def redact_extra(self, record: logging.LogRecord, keys: Iterable[str]) -> None:
        """Redact the string attributes named by ``keys`` (the ``extra=`` fields) in place.

        On any internal error each of them is withheld.
        """
        attrs = vars(record)
        names = [key for key in keys if isinstance(attrs.get(key), str)]
        try:
            redacted = {name: self._redactor.redact(attrs[name]) for name in names}
        except Exception:
            redacted = dict.fromkeys(names, _REDACTION_FAILED.rstrip("\n"))
        attrs.update(redacted)


# ---------------------------------------------------------------- process hooks


class InstalledHooks:
    """Process-wide redaction hooks. Created by :meth:`LogWriter.install`.

    Installed:

    - a log record factory that redacts each record as it is created, so
      every handler sees redacted text, including handlers added later and
      ``logging.lastResort``;
    - a wrapper around ``logging.Logger.makeRecord`` that redacts the
      ``extra=`` fields, which ``makeRecord`` adds after the factory returns,
      so handlers added later see them redacted too (a Logger subclass whose
      ``makeRecord`` does not call the base method relies on the root
      handlers' filter for its extras);
    - a :class:`RedactingFilter` on each root handler and on
      ``logging.lastResort``;
    - the ``httpx`` and ``httpcore`` loggers (and any existing child with a
      level of its own) capped at WARNING;
    - ``sys.excepthook``, ``sys.unraisablehook`` and ``threading.excepthook``,
      which write the redacted report to the writer's stderr;
    - with :meth:`install_asyncio`, an event loop's exception handler.

    :meth:`uninstall` restores every value seen at install time, newest first.
    It is idempotent. The Redactor is shared with the writer, so a value
    registered later is redacted by the hooks too.
    """

    __slots__ = ("_filter", "_undo", "_uninstalled", "_writer")

    def __init__(self, writer: LogWriter) -> None:
        self._writer = writer
        self._filter = writer.logging_filter()
        self._undo: list[Callable[[], None]] = []
        self._uninstalled = False

    @property
    def active(self) -> bool:
        return not self._uninstalled

    def _install(self) -> None:
        try:
            self._install_logging()
            self._install_exception_hooks()
        except BaseException:
            self.uninstall()
            raise

    def _install_logging(self) -> None:
        previous_factory = logging.getLogRecordFactory()
        redact_record = self._filter.redact_record

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous_factory(*args, **kwargs)
            redact_record(record)
            return record

        logging.setLogRecordFactory(factory)
        self._undo.append(partial(logging.setLogRecordFactory, previous_factory))

        previous_make_record = logging.Logger.makeRecord
        redact_extra = self._filter.redact_extra

        def make_record(
            logger: logging.Logger,
            name: str,
            level: int,
            fn: str,
            lno: int,
            msg: object,
            args: Any,
            exc_info: Any,
            func: str | None = None,
            extra: Mapping[str, object] | None = None,
            sinfo: str | None = None,
        ) -> logging.LogRecord:
            record = previous_make_record(
                logger, name, level, fn, lno, msg, args, exc_info, func, extra, sinfo
            )
            if extra:
                redact_extra(record, extra)
            return record

        logging.Logger.makeRecord = make_record  # type: ignore[method-assign, assignment]
        self._undo.append(partial(setattr, logging.Logger, "makeRecord", previous_make_record))

        handlers: list[logging.Handler] = list(logging.getLogger().handlers)
        if logging.lastResort is not None:
            handlers.append(logging.lastResort)
        for handler in handlers:
            if self._filter not in handler.filters:
                handler.addFilter(self._filter)
                self._undo.append(partial(handler.removeFilter, self._filter))

        for logger in _http_loggers():
            level = logger.level
            top = logger.name in HTTP_LOGGERS
            if (top and level < HTTP_LOG_CAP) or (logging.NOTSET < level < HTTP_LOG_CAP):
                logger.setLevel(HTTP_LOG_CAP)
                self._undo.append(partial(logger.setLevel, level))

    def _install_exception_hooks(self) -> None:
        self._undo.append(partial(setattr, sys, "excepthook", sys.excepthook))
        sys.excepthook = self._sys_excepthook
        self._undo.append(partial(setattr, sys, "unraisablehook", sys.unraisablehook))
        sys.unraisablehook = self._sys_unraisablehook
        self._undo.append(partial(setattr, threading, "excepthook", threading.excepthook))
        threading.excepthook = self._thread_excepthook

    def install_asyncio(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Replace the exception handler of ``loop`` (default: the running loop)."""
        if self._uninstalled:
            raise RuntimeError("these hooks were uninstalled; call LogWriter.install() again")
        target = asyncio.get_running_loop() if loop is None else loop
        self._undo.append(partial(target.set_exception_handler, target.get_exception_handler()))
        target.set_exception_handler(self._asyncio_handler)

    def uninstall(self) -> None:
        """Restore every hook, logger level, filter, factory and ``makeRecord`` replaced."""
        self._uninstalled = True
        while self._undo:
            self._undo.pop()()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.uninstall()

    # ------------------------------------------------------------ the hooks

    def _sys_excepthook(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        self._writer._report_uncaught(
            lambda: "".join(traceback.format_exception(exc_type, exc, tb))
        )

    def _sys_unraisablehook(self, unraisable: sys.UnraisableHookArgs) -> None:
        def build() -> str:
            head = unraisable.err_msg or "Exception ignored in"
            if unraisable.object is not None:
                head = f"{head}: {_safe_repr(unraisable.object)}"
            body = traceback.format_exception(
                unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback
            )
            return f"{head}\n{''.join(body)}"

        self._writer._report_uncaught(build)

    def _thread_excepthook(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return  # the default hook ignores SystemExit as well

        def build() -> str:
            name = args.thread.name if args.thread is not None else threading.get_ident()
            body = traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            return f"Exception in thread {name}:\n{''.join(body)}"

        self._writer._report_uncaught(build)

    def _asyncio_handler(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        def build() -> str:
            lines = [str(context.get("message") or "Unhandled exception in event loop")]
            for key in sorted(context):
                if key in {"message", "exception"}:
                    continue
                value = context[key]
                if key in {"source_traceback", "handle_traceback"}:
                    frames = "".join(traceback.format_list(value)).rstrip("\n")
                    lines.append(f"{key}:\n{frames}")
                else:
                    lines.append(f"{key}: {_safe_repr(value)}")
            exc = context.get("exception")
            if isinstance(exc, BaseException):
                lines.append("".join(traceback.format_exception(exc)).rstrip("\n"))
            return "\n".join(lines)

        self._writer._report_uncaught(build)


def _http_loggers() -> Iterator[logging.Logger]:
    """The ``httpx`` and ``httpcore`` loggers, then each existing child logger."""
    yield from (logging.getLogger(name) for name in HTTP_LOGGERS)
    for name, logger in list(logging.root.manager.loggerDict.items()):
        is_child = name not in HTTP_LOGGERS and _is_http_logger(name)
        if is_child and isinstance(logger, logging.Logger):
            yield logger


def _safe_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__qualname__} object; repr failed>"


# ---------------------------------------------------------------- file helpers


def _line_bytes(text: str) -> bytes:
    if not text.endswith("\n"):
        text += "\n"
    return text.encode(_ENCODING, _ENCODING_ERRORS)


def _write_stream(stream: TextIO | None, text: str) -> None:
    if stream is None:  # no console (pythonw, detached process)
        return
    stream.write(text if text.endswith("\n") else text + "\n")
    stream.flush()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_dir(path.parent)


def _append(path: Path, data: bytes, *, fsync: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _O_BINARY, _FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        if fsync:
            os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(directory: Path) -> None:
    """Make a rename durable where the platform allows it (not on Windows)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)
