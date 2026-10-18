"""Helpers for the ``fse export-aws`` tests that run under ``moto.mock_aws()``.

Plain functions only, no fixtures: each AWS test module opens its own
``mock_aws()`` block. The suite-wide ``_aws_isolated`` fixture in
``tests/conftest.py`` already gives every test fake credentials and
nonexistent AWS config files.

- :func:`moto_session_factory` returns a ``(profile, region) -> Session``
  that ignores the profile, so no test resolves a real profile.
- :func:`recording_session_factory` also registers a recorder for every
  ``before-call`` event on the session. botocore copies session handlers into
  each client created afterwards, so the recorder runs before the dry-run
  guard that :func:`fse.awsexport.aws.client` adds (review NIT 4).
- :func:`compliant_bucket` creates a bucket that passes the required checks.
  It has no ``DenyInsecureTransport`` policy: moto evaluates
  ``aws:SecureTransport`` as false and would then refuse every upload.
- :func:`writer` builds a Log_Writer on two ``StringIO`` buffers.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any, Final

from fse.logio import LogWriter, Redactor

__all__ = [
    "MOTO_REGION",
    "Recorder",
    "compliant_bucket",
    "moto_session_factory",
    "recording_session_factory",
    "writer",
]

MOTO_REGION: Final = "us-east-1"

type SessionFactory = Callable[[str, str], Any]


def moto_session_factory(region: str = MOTO_REGION) -> SessionFactory:
    import boto3

    def factory(profile: str, requested_region: str) -> Any:
        return boto3.Session(region_name=region)

    return factory


class Recorder:
    """Records ``(service, Operation)`` of every API call made through a session."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, event_name: str = "", **_: Any) -> None:
        parts = event_name.split(".")
        if len(parts) >= 3:
            self.calls.append((parts[1], parts[2]))


def recording_session_factory(recorder: Recorder, region: str = MOTO_REGION) -> SessionFactory:
    import boto3

    def factory(profile: str, requested_region: str) -> Any:
        session = boto3.Session(region_name=region)
        session.events.register("before-call.*.*", recorder)
        return session

    return factory


def compliant_bucket(name: str, region: str = MOTO_REGION) -> None:
    """Create ``name`` with the required settings (public access block, SSE, ownership)."""
    import boto3

    s3 = boto3.client("s3", region_name=region)
    s3.create_bucket(Bucket=name, ObjectOwnership="BucketOwnerEnforced")
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )


def writer() -> tuple[LogWriter, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return LogWriter(Redactor(()), stdout=out, stderr=err), out, err
