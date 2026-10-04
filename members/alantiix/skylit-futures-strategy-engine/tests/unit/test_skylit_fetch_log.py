"""Unit tests for ``fse.skylit.fetch_log``: one redacted line per attempt, never the key.

Fake values only.

**Validates: Requirements 2.8, 2.9, 2.11**
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from fse.logio import REDACTED, LogWriter, Redactor
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog, FetchLogEntry
from fse.skylit.ratelimit import RateHeaders
from fse.skylit.retry import (
    MAX_ATTEMPTS,
    Action,
    Decision,
    ErrorKind,
    HttpResult,
    TransportFailure,
    decide,
)
from fse.timekit import NS_PER_SECOND

KEY = "fake-skylit-key-0000"
S = NS_PER_SECOND
T0 = 1_760_000_000 * S
PARAMS = {"symbol": "SPX", "metric": "gamma", "at": "2026-01-14T14:30:00Z"}


@pytest.fixture
def writer() -> LogWriter:
    return LogWriter(Redactor([KEY]))


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _entry(
    result: HttpResult | TransportFailure, attempt: int, decision: Decision
) -> FetchLogEntry:
    return FetchLogEntry.from_attempt(
        sent_at=T0 + attempt * S,
        host="api.skylit.ai",
        path="/v1/historical",
        params=PARAMS,
        attempt=attempt,
        result=result,
        decision=decision,
        duration_ns=1_234_567,
    )


def test_one_line_per_attempt_with_every_field(writer: LogWriter, tmp_path: Path) -> None:
    path = tmp_path / FETCH_LOG_FILE_NAME
    rng = random.Random(7)
    attempts: list[HttpResult | TransportFailure] = [
        HttpResult(503, RateHeaders(credits_remaining="4999")),
        TransportFailure(ErrorKind.TIMEOUT, "ReadTimeout"),
        HttpResult(200, RateHeaders(remaining=590, credits_remaining="4998")),
    ]
    with FetchLog(writer, path) as log:
        for n, result in enumerate(attempts, start=1):
            log.record(_entry(result, n, decide(result, n, now=T0, rng=rng)))

    lines = _lines(path)
    assert [line["attempt"] for line in lines] == [1, 2, 3]
    assert [line["outcome"] for line in lines] == ["retry", "retry", "ok"]
    first, second, third = lines
    assert set(first) == {
        "sent_at",
        "sent_at_ny",
        "host",
        "path",
        "params",
        "attempt",
        "status",
        "error_type",
        "error_code",
        "x_credits_remaining",
        "duration_ms",
        "outcome",
        "retry_wait_ns",
    }
    assert first["sent_at"] == T0 + S
    assert first["host"] == "api.skylit.ai"
    assert first["path"] == "/v1/historical"
    assert first["params"] == PARAMS
    assert first["status"] == 503
    assert first["error_type"] is None
    assert first["x_credits_remaining"] == "4999"
    assert first["duration_ms"] == 1.234
    assert 0 <= first["retry_wait_ns"] <= 2 * S
    assert second["status"] is None
    assert second["error_type"] == "ReadTimeout"
    assert second["x_credits_remaining"] is None
    assert third["x_credits_remaining"] == "4998"
    assert third["retry_wait_ns"] is None


def test_lines_are_canonical_json(writer: LogWriter, tmp_path: Path) -> None:
    path = tmp_path / FETCH_LOG_FILE_NAME
    with FetchLog(writer, path) as log:
        log.record(_entry(HttpResult(200), 1, Decision(Action.OK)))
    (text,) = path.read_text(encoding="utf-8").splitlines()
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))


def test_terminal_failure_records_the_last_status_or_error_type(
    writer: LogWriter, tmp_path: Path
) -> None:
    path = tmp_path / FETCH_LOG_FILE_NAME
    rng = random.Random(1)
    with FetchLog(writer, path) as log:
        for result in (HttpResult(504), TransportFailure(ErrorKind.NETWORK, "ConnectError")):
            log.record(_entry(result, MAX_ATTEMPTS, decide(result, MAX_ATTEMPTS, now=T0, rng=rng)))
    by_status, by_error = _lines(path)
    assert (by_status["outcome"], by_status["status"]) == ("failed", 504)
    assert (by_error["outcome"], by_error["error_type"]) == ("failed", "ConnectError")
    assert by_status["retry_wait_ns"] is None


def test_rejected_attempt_records_status_error_code_path_and_params(
    writer: LogWriter, tmp_path: Path
) -> None:
    path = tmp_path / FETCH_LOG_FILE_NAME
    result = HttpResult(404, error_code="no_data")
    with FetchLog(writer, path) as log:
        log.record(_entry(result, 1, decide(result, 1, now=T0, rng=random.Random(0))))
    (line,) = _lines(path)
    assert line["outcome"] == "rejected"
    assert (line["status"], line["error_code"]) == (404, "no_data")
    assert (line["path"], line["params"]) == ("/v1/historical", PARAMS)


def test_the_key_and_authorization_never_reach_the_file(writer: LogWriter, tmp_path: Path) -> None:
    path = tmp_path / FETCH_LOG_FILE_NAME
    leaky = {"symbol": "SPX", "api_key": "fake-other-0000", "Token": "fake-tok-0000", "q": KEY}
    entry = FetchLogEntry(
        sent_at=T0,
        host="api.skylit.ai",
        path=f"/v1/heatmap/{KEY}",
        params=leaky,
        attempt=1,
        outcome=Action.OK,
        duration_ns=0,
        status=200,
    )
    assert entry.params == {"symbol": "SPX", "api_key": REDACTED, "Token": REDACTED, "q": KEY}
    with FetchLog(writer, path) as log:
        log.record(entry)
    text = path.read_text(encoding="utf-8")
    for secret in (KEY, "fake-other-0000", "fake-tok-0000"):
        assert secret not in text
    assert "authorization" not in text.lower()
    assert "bearer" not in text.lower()


def test_the_file_is_created_on_the_first_record_and_close_is_idempotent(
    writer: LogWriter, tmp_path: Path
) -> None:
    path = tmp_path / "logs" / FETCH_LOG_FILE_NAME
    log = FetchLog(writer, path)
    assert not path.exists()
    log.record(_entry(HttpResult(200), 1, Decision(Action.OK)))
    assert len(_lines(path)) == 1  # flushed before close
    log.close()
    log.close()
    with pytest.raises(ValueError, match="closed"):
        log.record(_entry(HttpResult(200), 1, Decision(Action.OK)))
    FetchLog(writer, tmp_path / "unused.jsonl").close()
    assert not (tmp_path / "unused.jsonl").exists()


def test_an_entry_has_exactly_one_of_status_and_error_type() -> None:
    common: dict[str, Any] = {
        "sent_at": T0,
        "host": "api.skylit.ai",
        "path": "/v1/account",
        "params": {},
        "attempt": 1,
        "outcome": Action.OK,
        "duration_ns": 0,
    }
    with pytest.raises(ValueError, match="exactly one"):
        FetchLogEntry(**common)
    with pytest.raises(ValueError, match="exactly one"):
        FetchLogEntry(**common, status=200, error_type="ReadTimeout")
