"""Unit tests for ``fse.logio.log_writer``: every sink redacts, and hooks are reversible.

Each hook test installs inside a ``with`` block, so ``sys.excepthook``,
``sys.unraisablehook``, ``threading.excepthook``, the log record factory,
``Logger.makeRecord`` and the asyncio handler are restored even when an
assertion fails. Fake values only.

**Validates: Requirements 1.9**
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from fse.logio import REDACTED, LogWriter, RedactingFilter, Redactor
from fse.secrets.env import load_env
from tests.conftest import FakeEnv

KEY = "fake-skylit-key-0000"
TOKEN = "fake.session.token-0000"


class Streams:
    def __init__(self) -> None:
        self.out = io.StringIO()
        self.err = io.StringIO()


@pytest.fixture
def streams() -> Streams:
    return Streams()


@pytest.fixture
def writer(streams: Streams) -> LogWriter:
    return LogWriter(Redactor([KEY]), stdout=streams.out, stderr=streams.err)


@pytest.fixture
def fresh_logger() -> Iterator[logging.Logger]:
    """A logger with its own string handler and no propagation; cleaned up after."""
    logger = logging.getLogger("fse.tests.log_writer")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


def _attach(logger: logging.Logger, fmt: str = "%(levelname)s %(message)s") -> io.StringIO:
    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    return sink


def _raise_with_secret() -> None:
    raise ValueError(f"bad key {KEY}")


# ---------------------------------------------------------------- console and payloads


def test_console_output_is_redacted(writer: LogWriter, streams: Streams) -> None:
    writer.echo(f"using {KEY}")
    writer.error(f"error: rejected {KEY}")
    assert streams.out.getvalue() == f"using {REDACTED}\n"
    assert streams.err.getvalue() == f"error: rejected {REDACTED}\n"


def test_exception_output_is_redacted(writer: LogWriter, streams: Streams) -> None:
    try:
        _raise_with_secret()
    except ValueError as exc:
        writer.exception(exc, message=f"failed with {KEY}")
        formatted = writer.format_exception(exc)
    err = streams.err.getvalue()
    assert KEY not in err
    assert err.startswith(f"failed with {REDACTED}\nTraceback (most recent call last):")
    assert f"ValueError: bad key {REDACTED}" in err
    assert KEY not in formatted
    assert formatted in err


def test_payloads_are_canonical_and_redacted(writer: LogWriter) -> None:
    card = {"z": 1, "url": f"https://x.invalid/?k={KEY}", "note": f'quoted "{KEY}"'}
    expected = f'{{"note":"quoted \\"{REDACTED}\\"","url":"https://x.invalid/?k={REDACTED}","z":1}}'
    assert writer.json_text(card) == expected
    assert writer.json_bytes(card) == expected.encode()
    assert writer.redact_json(card) == {
        "note": f'quoted "{REDACTED}"',
        "url": f"https://x.invalid/?k={REDACTED}",
        "z": 1,
    }


def test_add_secret_reaches_every_sink(writer: LogWriter, streams: Streams) -> None:
    writer.add_secret(TOKEN)
    writer.echo(f"Bearer {TOKEN}")
    assert streams.out.getvalue() == f"Bearer {REDACTED}\n"
    assert writer.json_text({"t": TOKEN}) == f'{{"t":"{REDACTED}"}}'


def test_from_env_redacts_every_secret_variable(fake_secrets: FakeEnv, tmp_path: Path) -> None:
    out = io.StringIO()
    writer = LogWriter.from_env(load_env(tmp_path), stdout=out)
    for value in fake_secrets.secret_values():
        writer.echo(value)
    assert out.getvalue() == f"{REDACTED}\n" * len(fake_secrets.secret_values())


# ---------------------------------------------------------------- files


def test_write_text_replaces_atomically(writer: LogWriter, tmp_path: Path) -> None:
    path = tmp_path / "runs" / "r1" / "report.md"
    writer.write_text(path, "old\n")
    writer.write_text(path, f"# Report\nkey {KEY}\n")
    assert path.read_text(encoding="utf-8") == f"# Report\nkey {REDACTED}\n"
    assert sorted(p.name for p in path.parent.iterdir()) == ["report.md"]  # no temp file left
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_write_json_writes_one_canonical_line(writer: LogWriter, tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    writer.write_json(path, {"status": "aborted", "error": f"401 for {KEY}"})
    assert path.read_bytes() == f'{{"error":"401 for {REDACTED}","status":"aborted"}}\n'.encode()


def test_append_lines_and_json(writer: LogWriter, tmp_path: Path) -> None:
    path = tmp_path / "logs" / "fetch_log.jsonl"
    writer.append_json(path, {"attempt": 1, "params": {"apiKey": KEY}})
    writer.append_json(path, {"attempt": 2}, fsync=True)
    writer.append_line(path, f"note {KEY}")
    assert path.read_text(encoding="utf-8").splitlines() == [
        f'{{"attempt":1,"params":{{"apiKey":"{REDACTED}"}}}}',
        '{"attempt":2}',
        f"note {REDACTED}",
    ]


@pytest.mark.parametrize("compress", [False, True], ids=["plain", "gzip"])
def test_line_sink_appends_redacted_lines(
    writer: LogWriter, tmp_path: Path, compress: bool
) -> None:
    path = tmp_path / ("session.jsonl.gz" if compress else "decisions.jsonl")
    with writer.open_lines(path, compress=compress) as sink:
        sink.write_json({"kind": "snapshot", "payload": {"token": KEY}})
        sink.flush(fsync=True)
    with writer.open_lines(path, compress=compress) as sink:  # reopen: append
        sink.write_line(f"plain {KEY}")
    assert sink.closed
    data = gzip.decompress(path.read_bytes()) if compress else path.read_bytes()
    assert data.decode().splitlines() == [
        f'{{"kind":"snapshot","payload":{{"token":"{REDACTED}"}}}}',
        f"plain {REDACTED}",
    ]


def test_gzip_sink_bytes_do_not_depend_on_time_or_name(writer: LogWriter, tmp_path: Path) -> None:
    paths = [tmp_path / "a.jsonl.gz", tmp_path / "b.jsonl.gz"]
    for path in paths:
        with writer.open_lines(path, compress=True) as sink:
            sink.write_json({"n": 1})
    assert paths[0].read_bytes() == paths[1].read_bytes()


# ---------------------------------------------------------------- logging


def test_logging_records_are_redacted(writer: LogWriter, fresh_logger: logging.Logger) -> None:
    with writer.install():
        sink = _attach(fresh_logger, "%(levelname)s %(message)s detail=%(detail)s")
        fresh_logger.warning("key=%s", KEY, extra={"detail": f"d-{KEY}"})
        try:
            _raise_with_secret()
        except ValueError:
            fresh_logger.exception("request failed for %s", KEY, extra={"detail": "-"})
        fresh_logger.info("stack %s", KEY, stack_info=True, extra={"detail": "-"})
    text = sink.getvalue()
    assert KEY not in text
    assert f"WARNING key={REDACTED} detail=d-{REDACTED}" in text
    assert f"ERROR request failed for {REDACTED}" in text
    assert f"ValueError: bad key {REDACTED}" in text
    assert "Stack (most recent call last)" in text


def test_last_resort_handler_gets_the_filter(
    writer: LogWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = io.StringIO()
    last_resort = logging.StreamHandler(sink)
    monkeypatch.setattr(logging, "lastResort", last_resort)
    with writer.install():
        # Built directly, so only the handler filter can redact it.
        raw = logging.LogRecord("fse.x", logging.WARNING, __file__, 1, "key %s", (KEY,), None)
        last_resort.handle(raw)
    assert sink.getvalue() == f"key {REDACTED}\n"
    assert last_resort.filters == []


def test_filter_on_a_handler_redacts_and_drops_http_debug(writer: LogWriter) -> None:
    filt = writer.logging_filter()
    assert isinstance(filt, RedactingFilter)

    def record(name: str, level: int) -> logging.LogRecord:
        return logging.LogRecord(name, level, __file__, 1, "auth %s", (KEY,), None)

    kept = record("fse.live", logging.INFO)
    assert filt.filter(kept) is True
    assert kept.getMessage() == f"auth {REDACTED}"
    assert filt.filter(record("httpx", logging.INFO)) is False
    assert filt.filter(record("httpcore.http11", logging.DEBUG)) is False
    assert filt.filter(record("httpcore.connection", logging.WARNING)) is True
    assert filt.filter(record("httpxtra", logging.DEBUG)) is True  # not an httpx child


def test_http_loggers_are_capped_and_restored(writer: LogWriter) -> None:
    child = logging.getLogger("httpcore.http11")
    httpx_logger = logging.getLogger("httpx")
    before = (httpx_logger.level, logging.getLogger("httpcore").level)
    child.setLevel(logging.DEBUG)
    try:
        with writer.install():
            assert httpx_logger.level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
            assert child.level == logging.WARNING
            assert not httpx_logger.isEnabledFor(logging.INFO)
        assert (httpx_logger.level, logging.getLogger("httpcore").level) == before
        assert child.level == logging.DEBUG
    finally:
        child.setLevel(logging.NOTSET)


# ---------------------------------------------------------------- uncaught errors


def test_sys_excepthook_is_redacted_and_restored(writer: LogWriter, streams: Streams) -> None:
    original = sys.excepthook
    with writer.install():
        assert sys.excepthook is not original
        try:
            _raise_with_secret()
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
    assert sys.excepthook is original
    err = streams.err.getvalue()
    assert err.startswith("Traceback (most recent call last):")
    assert err.endswith(f"ValueError: bad key {REDACTED}\n")
    assert KEY not in err


def test_unraisable_hook_is_redacted_and_restored(writer: LogWriter, streams: Streams) -> None:
    class Leaky:
        def __del__(self) -> None:
            raise RuntimeError(f"close failed for {KEY}")

    original = sys.unraisablehook
    with writer.install():
        leaky = Leaky()
        del leaky  # CPython runs __del__ here, and reports through sys.unraisablehook
    assert sys.unraisablehook is original
    err = streams.err.getvalue()
    assert f"RuntimeError: close failed for {REDACTED}" in err
    assert KEY not in err


def test_thread_excepthook_is_redacted_and_restored(writer: LogWriter, streams: Streams) -> None:
    original = threading.excepthook
    with writer.install():
        worker = threading.Thread(target=_raise_with_secret, name="fake-worker")
        worker.start()
        worker.join()
        quiet = threading.Thread(target=sys.exit, name="exits")  # SystemExit is not reported
        quiet.start()
        quiet.join()
    assert threading.excepthook is original
    err = streams.err.getvalue()
    assert err.startswith("Exception in thread fake-worker:\nTraceback")
    assert f"ValueError: bad key {REDACTED}" in err
    assert "exits" not in err
    assert KEY not in err


def test_asyncio_handler_is_redacted_and_restored(writer: LogWriter, streams: Streams) -> None:
    def callback() -> None:
        raise RuntimeError(f"stream closed for {KEY}")

    loop = asyncio.new_event_loop()
    try:
        with writer.install(loop=loop) as hooks:
            assert loop.get_exception_handler() is not None
            loop.call_soon(callback)
            loop.run_until_complete(asyncio.sleep(0))
            loop.call_exception_handler({"message": f"custom {KEY}", "detail": {"k": KEY}})
        assert not hooks.active
        assert loop.get_exception_handler() is None
    finally:
        loop.close()
    err = streams.err.getvalue()
    assert KEY not in err
    assert "Exception in callback" in err
    assert f"RuntimeError: stream closed for {REDACTED}" in err
    assert f"custom {REDACTED}\ndetail: {{'k': '{REDACTED}'}}" in err


def test_uninstall_restores_logging_and_is_idempotent(writer: LogWriter) -> None:
    factory = logging.getLogRecordFactory()
    make_record = logging.Logger.makeRecord
    last_resort_filters = list(logging.lastResort.filters) if logging.lastResort else []
    hooks = writer.install()
    assert logging.getLogRecordFactory() is not factory
    assert logging.Logger.makeRecord is not make_record
    hooks.uninstall()
    hooks.uninstall()
    assert logging.getLogRecordFactory() is factory
    assert logging.Logger.makeRecord is make_record
    assert (list(logging.lastResort.filters) if logging.lastResort else []) == last_resort_filters
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(RuntimeError, match="uninstalled"):
            hooks.install_asyncio(loop)
        assert loop.get_exception_handler() is None
    finally:
        loop.close()
