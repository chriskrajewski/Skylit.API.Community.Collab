"""AWS session, client factory, dry-run guard and error mapping (design §8.7, §9, NIT 3).

Every request runs under ``moto.mock_aws()``; the suite-wide ``_aws_isolated``
fixture gives fake credentials and no AWS config files.

**Validates: aws-export design §9 and §14 test 10 (guard and client-site scan)**
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
    ReadTimeoutError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)
from moto import mock_aws

from fse.awsexport import aws
from fse.awsexport.errors import (
    DryRunViolation,
    ExportAccessError,
    ExportDataError,
    ExportError,
    ExportInputError,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "fse"
HINT = "aws sts get-caller-identity --profile myprofile"


@pytest.fixture
def moto() -> Iterator[None]:
    with mock_aws():
        yield


def _client_error(code: str, status: int = 400, op: str = "HeadObject") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"msg {code}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        op,
    )


def _translate(exc: BaseException) -> ExportError:
    return aws.translate_aws_error(exc, "Op", "s3://b/k", profile="myprofile")


@pytest.mark.parametrize(
    "exc",
    [
        NoCredentialsError(),
        PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY"),
        TokenRetrievalError(provider="sso", error_msg="expired"),
        SSOTokenLoadError(error_msg="no token"),
        UnauthorizedSSOTokenError(),
    ],
)
def test_credential_errors_are_exit_3_with_the_hint(exc: Exception) -> None:
    err = _translate(exc)
    assert isinstance(err, ExportAccessError)
    assert err.exit_code == 3
    assert HINT in str(err)
    assert "aws sso login --profile myprofile" in str(err)


def test_profile_not_found_is_exit_3() -> None:
    err = _translate(ProfileNotFound(profile="myprofile"))
    assert err.exit_code == 3
    assert "profile 'myprofile' not found" in str(err)


@pytest.mark.parametrize(
    "code",
    [
        "AccessDenied",
        "AccessDeniedException",
        "403",
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "SignatureDoesNotMatch",
        "UnrecognizedClientException",
    ],
)
def test_access_codes_are_exit_3(code: str) -> None:
    err = _translate(_client_error(code, 403 if code == "403" else 400))
    assert isinstance(err, ExportAccessError)
    assert code in str(err)
    if code in aws.TOKEN_CODES:
        assert HINT in str(err)


@pytest.mark.parametrize(
    "exc",
    [
        EndpointConnectionError(endpoint_url="https://s3.invalid"),
        ConnectTimeoutError(endpoint_url="https://s3.invalid"),
        ReadTimeoutError(endpoint_url="https://s3.invalid"),
        _client_error("InternalError", 500),
        _client_error("SlowDown", 503),
    ],
)
def test_other_failures_are_exit_4(exc: Exception) -> None:
    err = _translate(exc)
    assert isinstance(err, ExportDataError)
    assert "Op s3://b/k" in str(err)


def _upload_failed(inner: Exception) -> S3UploadFailedError:
    """``S3UploadFailedError`` raised while handling ``inner``, as boto3 does (``__context__``)."""
    try:
        try:
            raise inner
        except Exception:
            raise S3UploadFailedError("Failed to upload x to b/k") from None
    except S3UploadFailedError as exc:
        exc.__suppress_context__ = False  # `from None` hid it; boto3 leaves it visible
        return exc


def test_upload_failure_unwraps_the_credential_error() -> None:
    exc = _upload_failed(_client_error("ExpiredToken", 400, "PutObject"))
    assert exc.__cause__ is None
    err = _translate(exc)
    assert err.exit_code == 3
    assert HINT in str(err)


def test_upload_failure_with_other_inner_error_is_exit_4() -> None:
    err = _translate(_upload_failed(_client_error("InternalError", 500, "PutObject")))
    assert err.exit_code == 4
    assert "InternalError" in str(err)


def test_bare_upload_failure_is_exit_4() -> None:
    err = _translate(S3UploadFailedError("Failed to upload x"))
    assert isinstance(err, ExportDataError)


def test_messages_never_hold_the_key_values() -> None:
    err = _translate(_client_error("SignatureDoesNotMatch"))
    assert "testing" not in str(err)


def test_default_retries_on_every_client(moto: None) -> None:
    session = boto3.Session(region_name="us-east-1")
    plain = aws.client(session, "s3", dry_run=False)
    merged = aws.client(session, "glue", dry_run=True, config=Config(connect_timeout=7))
    for c in (plain, merged):
        assert c.meta.config.retries["mode"] == "standard"
        assert c.meta.config.retries["total_max_attempts"] == 11  # 10 retries + 1
    assert merged.meta.config.connect_timeout == 7


def test_guard_blocks_before_any_request_and_is_per_client(moto: None) -> None:
    session = boto3.Session(region_name="us-east-1")
    sent: list[str] = []
    guarded = aws.client(session, "s3", dry_run=True)
    guarded.meta.events.register("before-send.*", lambda **kw: sent.append("sent"))
    with pytest.raises(DryRunViolation, match="s3:ListBuckets") as info:
        guarded.list_buckets()
    assert info.value.exit_code == 1
    assert sent == []
    free = aws.client(session, "s3", dry_run=False)
    assert free.list_buckets()["Buckets"] == []


def test_allowed_calls_pass_the_guard(moto: None) -> None:
    session = boto3.Session(region_name="us-east-1")
    assert aws.client(session, "sts", dry_run=True).get_caller_identity()["Account"]
    with pytest.raises(ClientError):
        aws.client(session, "glue", dry_run=True).get_database(Name="nope")
    with pytest.raises(ClientError):
        aws.client(session, "s3", dry_run=True).head_bucket(Bucket="nope-nope")


def test_make_session_profile_not_found_is_exit_3() -> None:
    with pytest.raises(ExportAccessError, match="profile 'myprofile' not found") as info:
        aws.make_session("myprofile", "us-east-1")
    assert info.value.exit_code == 3


def test_missing_boto3_is_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(ExportInputError, match="pip install --require-hashes") as info:
        aws.make_session("myprofile", "us-east-1")
    assert info.value.exit_code == 2


def test_the_only_client_factory_is_aws_client() -> None:
    files = [*sorted((SRC / "awsexport").rglob("*.py")), SRC / "commands" / "export_aws.py"]
    pattern = re.compile(r"\b(\w+)\.(client|resource)\(")
    sites = [
        (p.name, m.group(0))
        for p in files
        for m in pattern.finditer(p.read_text(encoding="utf-8"))
        if m.group(1) != "aws"  # calls of fse.awsexport.aws.client itself
    ]
    assert sites == [("aws.py", "session.client(")]
