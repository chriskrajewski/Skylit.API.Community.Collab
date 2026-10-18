"""``--dry-run``: only three read-only AWS calls, and nothing written (design §8.7).

Calls are recorded by a handler registered on the session before any client
exists (review NIT 4): botocore copies it into every client, ahead of the
per-client guard, so a blocked call is recorded and then rejected.

**Validates: aws-export design §14 test 10**
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from fse.awsexport import aws, runner
from fse.awsexport.errors import DryRunViolation
from fse.clock import SystemClock
from fse.logio import LogWriter, Redactor
from tests.fakes.aws import Recorder, recording_session_factory
from tests.fakes.export_cache import build_cache, opts, tree_snapshot

ALLOWED = [("sts", "GetCallerIdentity"), ("s3", "HeadBucket"), ("glue", "GetDatabase")]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Path]:
    build_cache(tmp_path / "cache")
    with mock_aws():
        yield tmp_path


def _dry_run(tmp_path: Path, factory: runner.SessionFactory) -> str:
    out = io.StringIO()
    code = runner.run_export(
        opts(tmp_path / "cache", tmp_path / "stage", mode="dry_run"),
        writer=LogWriter(Redactor(()), stdout=out),
        session_factory=factory,
        clock=SystemClock(),
    )
    assert code == 0
    return out.getvalue()


def test_dry_run_makes_only_the_three_allowed_calls(env: Path) -> None:
    rec = Recorder()
    out = _dry_run(env, recording_session_factory(rec))
    assert rec.calls == ALLOWED
    assert boto3.client("s3", region_name="us-east-1").list_buckets()["Buckets"] == []
    assert boto3.client("glue", region_name="us-east-1").get_databases()["DatabaseList"] == []
    assert not (env / "stage").exists()
    lines = out.splitlines()
    assert lines
    for line in lines:
        assert line.startswith(("would ", "plan: ", "warning: ")), line
    assert (
        sum(line.startswith(("would create bucket", "would verify bucket")) for line in lines) == 1
    )
    glue_lines = [line for line in lines if "table skylit_fse." in line]
    assert glue_lines
    assert all("marker not checked in dry-run" in line for line in glue_lines)
    assert any("local plan only" in line for line in lines)
    assert "would create database skylit_fse" in lines
    assert any(line.startswith("plan: staged size estimate") for line in lines)


def test_dry_run_on_a_staged_directory_lists_uploads_and_writes_nothing(env: Path) -> None:
    out = io.StringIO()
    runner.run_export(
        opts(env / "cache", env / "stage", mode="stage_only"),
        writer=LogWriter(Redactor(()), stdout=out),
        session_factory=lambda p, r: pytest.fail("stage-only must not create a session"),
        clock=SystemClock(),
    )
    before = tree_snapshot(env / "stage")
    rec = Recorder()
    text = _dry_run(env, recording_session_factory(rec))
    assert tree_snapshot(env / "stage") == before
    assert rec.calls == ALLOWED
    uploads = [line for line in text.splitlines() if line.startswith("would upload ")]
    assert len(uploads) == 14
    assert "would stage 0 partitions first" in text


def test_disallowed_call_on_a_dry_run_client_is_recorded_then_rejected(env: Path) -> None:
    rec = Recorder()
    session = recording_session_factory(rec)("myprofile", "us-east-1")
    s3 = aws.client(session, "s3", dry_run=True)
    with pytest.raises(DryRunViolation, match="s3:CreateBucket"):
        s3.create_bucket(Bucket="never-created-bucket")
    assert rec.calls == [("s3", "CreateBucket")]
    assert boto3.client("s3", region_name="us-east-1").list_buckets()["Buckets"] == []


def test_guard_is_per_client_not_per_session(env: Path) -> None:
    session = boto3.Session(region_name="us-east-1")
    aws.client(session, "s3", dry_run=True)
    other = session.client("s3")  # same session, created after a guarded client
    assert other.list_buckets()["Buckets"] == []


def test_role_arn_profile_works_in_dry_run(env: Path, isolated_home: Path) -> None:
    import os

    account = "1" * 12  # moto accepts any account in the role ARN
    Path(os.environ["AWS_CONFIG_FILE"]).write_text(
        "[profile base]\nregion = us-east-1\n"
        "[profile role]\n"
        f"role_arn = arn:aws:iam::{account}:role/fse-export\n"
        "source_profile = base\nregion = us-east-1\n"
    )
    Path(os.environ["AWS_SHARED_CREDENTIALS_FILE"]).write_text(
        "[base]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    rec = Recorder()

    def factory(profile: str, region: str) -> object:
        session = boto3.Session(profile_name="role", region_name=region)
        session.events.register("before-call.*.*", rec)
        return session

    _dry_run(env, factory)
    # The credential provider's own sts client ran AssumeRole (authentication, not
    # a resource operation) and was not blocked; the export's clients made only
    # the three allowed calls.
    assert ("sts", "AssumeRole") in rec.calls
    assert [c for c in rec.calls if c != ("sts", "AssumeRole")] == ALLOWED
