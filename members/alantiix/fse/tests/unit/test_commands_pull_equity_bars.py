"""Offline CLI tests for ``fse pull-equity-bars``."""

from __future__ import annotations

import io
import json
from datetime import date, time
from pathlib import Path

import httpx
import pytest
import respx

from fse import cli
from fse.commands import pull_equity_bars as equity_command
from fse.skylit.endpoints import Host
from fse.timekit import NS_PER_SECOND, ny_instant

PROJECT = Path(__file__).resolve().parents[2]
CALENDARS = PROJECT / "calendars"


def run_command(tmp_path: Path, *extra: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2026-03-05",
            "--end",
            "2026-03-05",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(tmp_path / "out"),
            *extra,
        ],
        project_dir=None,
        environ={},
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_help_mentions_rth_credits_and_dry_run() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.run(
        ["pull-equity-bars", "--help"],
        project_dir=None,
        environ={},
        stdout=stdout,
        stderr=stderr,
    )
    text = stdout.getvalue() + stderr.getvalue()
    assert code == 0
    assert "60-second" in text
    assert "RTH" in text
    assert "credit" in text
    assert "dry-run" in text
    assert "equity_bars" in text


def test_dry_run_validates_locally_and_writes_nothing(tmp_path: Path) -> None:
    code, stdout, stderr = run_command(tmp_path, "--symbols", "QQQ,SPY", "--dry-run")

    assert code == 0
    assert "history_requests=2" in stdout
    assert "estimated_credits=2" in stdout
    assert "no network request" in stdout
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "out").exists()
    assert stderr == ""


@pytest.mark.parametrize(
    "extra",
    [
        ("--symbols", "qqq"),
        ("--symbols", "QQQ, SPY"),
        ("--symbols", "QQQ,QQQ"),
        ("--symbols", "DIA"),
        ("--interval", "5"),
    ],
)
def test_symbol_and_interval_rejection_is_before_any_request(
    tmp_path: Path, extra: tuple[str, str]
) -> None:
    code, _stdout, stderr = run_command(tmp_path, *extra)
    assert code == 2
    assert "error" in stderr
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "out").exists()


def test_reversed_dates_and_non_session_range_are_rejected(tmp_path: Path) -> None:
    code, _stdout, stderr = run_command(
        tmp_path,
        "--start",
        "2026-03-06",
        "--end",
        "2026-03-05",
    )
    assert code == 2
    assert "after" in stderr

    code, _stdout, stderr = run_command(
        tmp_path,
        "--start",
        "2026-03-07",
        "--end",
        "2026-03-08",
    )
    assert code == 2
    assert "no exchange sessions" in stderr


def test_project_env_is_not_required_for_dry_run(tmp_path: Path) -> None:
    code, stdout, _stderr = run_command(tmp_path, "--dry-run")
    assert code == 0
    assert "symbols=QQQ,SPY" in stdout


def test_calendar_date_is_inclusive_new_york_session_date() -> None:
    assert date.fromisoformat("2026-03-05") < date.fromisoformat("2026-03-06")


def test_mocked_live_pull_writes_equity_coverage_and_fetch_log(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    router = respx_router
    account = router.route(
        method="GET", scheme="https", host=Host.API.value, path="/v1/account"
    ).mock(return_value=httpx.Response(200, json={"data": {"limits": {}}}))
    config = router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/config"
    ).mock(return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}}))

    def history(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        start, end = int(params["from"]), int(params["to"])
        timestamps = list(range(start, end, 60))
        prices = [100.0] * len(timestamps)
        return httpx.Response(
            200,
            json={
                "s": "ok",
                "t": timestamps,
                "o": prices,
                "h": [100.2] * len(timestamps),
                "l": [99.8] * len(timestamps),
                "c": [100.1] * len(timestamps),
                "v": [1.0] * len(timestamps),
            },
        )

    history_route = router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/history"
    ).mock(side_effect=history)
    out = tmp_path / "out"
    code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2026-03-05",
            "--end",
            "2026-03-05",
            "--symbols",
            "QQQ",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(out),
        ],
        project_dir=None,
        environ={"SKYLIT_API_KEY": "fake-skylit-key-0000"},
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    assert account.call_count == 1
    assert config.call_count == 1
    assert history_route.call_count == 1
    assert len(list(out.glob("equity_coverage_*.json"))) == 1
    assert len(list(out.glob("equity_coverage_*.md"))) == 1
    fetch_log = out / "fetch_log.jsonl"
    assert fetch_log.exists()
    assert "/v1/history" in fetch_log.read_text(encoding="utf-8")
    request = history_route.calls[0].request
    assert dict(request.url.params)["extended"] == "false"
    assert dict(request.url.params)["from"] == str(
        ny_instant(date(2026, 3, 5), time(9, 30)) // NS_PER_SECOND
    )

    second_code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2026-03-05",
            "--end",
            "2026-03-05",
            "--symbols",
            "QQQ",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(out),
        ],
        project_dir=None,
        environ={"SKYLIT_API_KEY": "fake-skylit-key-0000"},
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert second_code == 0
    assert history_route.call_count == 1
    assert len(list(out.glob("equity_coverage_*.json"))) == 1
    assert len(list(out.glob("equity_coverage_*.md"))) == 1


def test_failed_pull_manifest_preserves_completed_session(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    respx_router.route(
        method="GET", scheme="https", host=Host.API.value, path="/v1/account"
    ).mock(return_value=httpx.Response(200, json={"data": {"limits": {}}}))
    respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/config"
    ).mock(return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}}))
    regular_start = ny_instant(date(2025, 7, 2), time(9, 30)) // NS_PER_SECOND

    def history(request: httpx.Request) -> httpx.Response:
        start, end = int(request.url.params["from"]), int(request.url.params["to"])
        if start == regular_start:
            timestamps = list(range(start, end, 60))
            prices = [100.0] * len(timestamps)
            return httpx.Response(
                200,
                json={
                    "s": "ok",
                    "t": timestamps,
                    "o": prices,
                    "h": [100.2] * len(timestamps),
                    "l": [99.8] * len(timestamps),
                    "c": [100.1] * len(timestamps),
                    "v": [1.0] * len(timestamps),
                },
            )
        return httpx.Response(200, json={"s": "ok", "t": [start], "o": []})

    history_route = respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/history"
    ).mock(side_effect=history)
    out = tmp_path / "out"
    code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2025-07-02",
            "--end",
            "2025-07-03",
            "--symbols",
            "QQQ",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(out),
        ],
        project_dir=None,
        environ={"SKYLIT_API_KEY": "fake-skylit-key-0000"},
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 4
    assert history_route.call_count == 2
    manifest = json.loads(next(out.glob("equity_coverage_*.json")).read_text(encoding="utf-8"))
    sessions = manifest["by_symbol"]["QQQ"]["sessions"]
    assert [(item["session"], item["status"]) for item in sessions] == [
        ("2025-07-02", "complete"),
        ("2025-07-03", "incomplete"),
    ]
    assert sessions[0]["sha256"]
    assert sessions[1]["error"] == "malformed_response"



def test_failed_pull_writes_every_requested_session_to_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_pull(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(equity_command, "pull_equity_bars", fail_pull)
    out = tmp_path / "out"
    code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2026-03-05",
            "--end",
            "2026-03-06",
            "--symbols",
            "QQQ,SPY",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(out),
        ],
        project_dir=None,
        environ={"SKYLIT_API_KEY": "fake-skylit-key-0000"},
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code != 0
    manifests = list(out.glob("equity_coverage_*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["outcome"] == "failed"
    for symbol in ("QQQ", "SPY"):
        section = manifest["by_symbol"][symbol]
        assert section["sessions_requested"] == 2
        assert section["sessions_incomplete"] == 2
        assert [item["session"] for item in section["sessions"]] == [
            "2026-03-05",
            "2026-03-06",
        ]
        assert all(item["error"] == "bootstrap failed" for item in section["sessions"])
