"""``fse export-aws`` through ``fse.cli.run``: exit statuses, help, lazy boto3, finding 2.

AWS runs happen under ``moto.mock_aws()`` with ``fse.awsexport.aws.make_session``
patched to a session that ignores the profile. Two fresh-interpreter
subprocesses prove that starting ``fse`` and running ``--stage-only`` never
import boto3 or botocore.

The finding-2 cases stage a partition as two parts, sync it, then restage it
as one part; the next sync's ``DeleteObjects`` gets a canned response with
``Errors`` (moto never returns per-key errors). The run must stop with exit 3
(``AccessDenied``) or 4 (other codes), keep the superseded part, and never
reach the Glue stage.

**Validates: aws-export design §14 test 11; design review round 3 finding 2**
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from moto import mock_aws

from fse import cli
from fse.awsexport import transform
from fse.commands import export_aws
from tests.fakes.aws import Recorder, compliant_bucket
from tests.fakes.export_cache import build_cache

PROJECT_DIR = Path(__file__).resolve().parents[2]
TWELVE_DIGITS = re.compile(r"\b\d{12}\b")
HEAT_OCT = "fse/heatmaps/symbol=SPX/metric=gamma/month=2025-10/"


def run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(["export-aws", *args], project_dir=None, environ={}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def dirs(tmp_path: Path) -> list[str]:
    return ["--cache-dir", str(tmp_path / "cache"), "--staging-dir", str(tmp_path / "stage")]


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    build_cache(tmp_path / "cache")
    return tmp_path


@pytest.fixture
def moto() -> Iterator[None]:
    with mock_aws():
        yield


def _account() -> str:
    return str(boto3.client("sts", region_name="us-east-1").get_caller_identity()["Account"])


def _patch_session(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder | None = None, **hooks: Any
) -> None:
    def make_session(profile: str, region: str) -> Any:
        session = boto3.Session(region_name=region)
        if rec is not None:
            session.events.register("before-call.*.*", rec)
        for event, handler in hooks.items():
            session.events.register(event, handler)
        return session

    monkeypatch.setattr("fse.awsexport.aws.make_session", make_session)


# ---------------------------------------------------------------- discovery and help


def test_command_is_discovered_and_help_has_no_account_id() -> None:
    assert export_aws.register in cli.discover_commands()
    code, out, _ = run(["--help"])
    assert code == 0
    for flag in ("--dry-run", "--stage-only", "--profile", "--bucket", "--prefix", "--datasets"):
        assert flag in out
    assert "exit status:" in out
    assert "view_id" in out
    assert "awswrangler" in out
    assert TWELVE_DIGITS.search(out) is None


def test_readme_has_no_account_id() -> None:
    text = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")
    assert TWELVE_DIGITS.search(text) is None
    assert "## Export to AWS (Athena and SageMaker)" in text


# ---------------------------------------------------------------- exit statuses


def test_full_run_exit_0(cache: Path, moto: None, monkeypatch: pytest.MonkeyPatch) -> None:
    compliant_bucket(f"skylit-fse-data-{_account()}-us-east-1")
    _patch_session(monkeypatch)
    code, out, err = run(dirs(cache))
    assert (code, err) == (0, "")
    assert "export done" in out
    glue = boto3.client("glue", region_name="us-east-1")
    assert len(glue.get_tables(DatabaseName="skylit_fse")["TableList"]) == 6


def test_dry_run_exit_0(cache: Path, moto: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch)
    code, out, _ = run(["--dry-run", *dirs(cache)])
    assert code == 0
    assert all(line.startswith(("would ", "plan: ", "warning: ")) for line in out.splitlines())


def test_both_modes_is_a_usage_error(cache: Path) -> None:
    code, _, err = run(["--dry-run", "--stage-only", *dirs(cache)])
    assert code == 2
    assert "not allowed with argument" in err


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["--region", "nowhere"], "--region"),
        (["--bucket", "Bad_Bucket"], "--bucket"),
        (["--datasets", "nope"], "--datasets"),
        (["--prefix", "../x"], "--prefix"),
        (["--symbols", "SPXX"], "SPXX"),
    ],
)
def test_invalid_input_exit_2(cache: Path, args: list[str], needle: str) -> None:
    code, _, err = run(["--stage-only", *args, *dirs(cache)])
    assert code == 2
    assert needle in err
    assert not (cache / "stage").exists()


def test_profile_not_found_exit_3(cache: Path) -> None:
    # the real factory, isolated config
    code, _, err = run(["--profile", "myprofile", *dirs(cache)])
    assert code == 3
    assert "profile 'myprofile' not found" in err


def test_no_credentials_exit_3_with_hint(
    cache: Path, moto: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    _patch_session(monkeypatch)
    code, _, err = run(["--dry-run", *dirs(cache)])
    assert code == 3
    assert "aws sts get-caller-identity --profile default" in err


def test_missing_cache_exit_4(tmp_path: Path) -> None:
    code, _, err = run(["--stage-only", *dirs(tmp_path)])
    assert code == 4
    assert "does not exist" in err


def test_stage_only_never_calls_the_session_factory(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(profile: str, region: str) -> None:
        raise AssertionError("stage-only created a session")

    monkeypatch.setattr("fse.awsexport.aws.make_session", boom)
    code, out, err = run(["--stage-only", *dirs(cache)])
    assert (code, err) == (0, "")
    assert "staged 14 partitions" in out


# ---------------------------------------------------------------- lazy boto3


def _fresh_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_starting_fse_imports_no_boto3() -> None:
    result = _fresh_python(
        "import sys; from fse import cli; cli.build_parser(); "
        "assert 'boto3' not in sys.modules and 'botocore' not in sys.modules, 'imported'"
    )
    assert result.returncode == 0, result.stderr


def test_stage_only_imports_no_boto3(cache: Path) -> None:
    c, s = str(cache / "cache"), str(cache / "stage")
    result = _fresh_python(
        "import sys; from fse import cli; "
        f"rc = cli.run(['export-aws', '--stage-only', '--cache-dir', {c!r}, '--staging-dir', "
        f"{s!r}, '--datasets', 'vix_daily'], project_dir=None); "
        "assert rc == 0, rc; "
        "assert 'boto3' not in sys.modules and 'botocore' not in sys.modules, 'imported'"
    )
    assert result.returncode == 0, result.stderr + result.stdout


# ---------------------------------------------------------------- finding 2, end to end


def _canned_delete(code: str) -> Any:
    def handler(params: dict[str, Any], **_: Any) -> tuple[Any, dict[str, Any]]:
        body = params.get("body", b"")
        text = body.decode() if isinstance(body, bytes) else str(body)
        keys = re.findall(r"<Key>([^<]+)</Key>", text)
        errors = [{"Key": k, "Code": code, "Message": "denied by policy"} for k in keys]
        return SimpleNamespace(status_code=200, headers={}), {"Errors": errors}

    return handler


@pytest.mark.parametrize(("code", "status"), [("AccessDenied", 3), ("InternalError", 4)])
def test_failed_superseded_delete_stops_the_run(
    cache: Path, moto: None, monkeypatch: pytest.MonkeyPatch, code: str, status: int
) -> None:
    bucket = f"skylit-fse-data-{_account()}-us-east-1"
    compliant_bucket(bucket)
    _patch_session(monkeypatch)
    monkeypatch.setattr(transform, "MAX_FILE_BYTES", 1)  # one part per row group
    first, _, err = run(["--datasets", "heatmaps", "--symbols", "SPX", *dirs(cache)])
    assert (first, err) == (0, "")
    s3 = boto3.client("s3", region_name="us-east-1")
    stale = HEAT_OCT + "part-00001.parquet"
    s3.head_object(Bucket=bucket, Key=stale)  # the partition has two parts remotely
    monkeypatch.setattr(transform, "MAX_FILE_BYTES", 256 * 2**20)
    staged = cache / "stage/data/heatmaps/symbol=SPX/metric=gamma/month=2025-10"
    for part in staged.glob("part-*.parquet"):
        part.unlink()  # forces a restage, now as one part
    rec = Recorder()
    _patch_session(monkeypatch, rec, **{"before-call.s3.DeleteObjects": _canned_delete(code)})
    second, out, err = run(["--datasets", "heatmaps", "--symbols", "SPX", *dirs(cache)])
    assert second == status
    assert stale in err
    assert "duplicate rows" in err
    s3.head_object(Bucket=bucket, Key=stale)  # still there
    assert ("s3", "DeleteObjects") in rec.calls
    assert [c for c in rec.calls if c[0] == "glue"] == []  # Glue never ran
    assert "export done" not in out
