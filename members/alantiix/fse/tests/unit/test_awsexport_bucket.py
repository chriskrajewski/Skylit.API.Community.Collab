"""The export bucket: create, verify, warn, refuse (design §8.4, review NITs 8-10).

moto 5.2.3 handles ``ObjectOwnership`` on ``CreateBucket``, the
``x-amz-bucket-region`` header on ``HeadBucket``, the ``bucket_exists``
waiter and the ``NoSuch*`` errors of the five getters, so those cases run
under moto. The exact request parameters (``ExpectedBucketOwner`` on every
call but ``CreateBucket``, the waiter between create and the first put) and
the responses moto does not emulate (403 owner mismatch, 301 redirect,
``BucketAlreadyExists``, a ``BucketRegion`` mismatch) use ``botocore.stub.Stubber``.

**Validates: aws-export design §14 test 7**
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from fse.awsexport import bucket as b
from fse.awsexport.errors import ExportAccessError, ExportInputError
from fse.logio import LogWriter, Redactor
from tests.fakes.aws import Recorder, compliant_bucket

BUCKET = "skylit-fse-data-test-us-east-1"
ACCOUNT = "1" * 12  # Stubber cases only; moto cases read the account from STS
# The bucket_exists waiter matches on the HTTP status in ResponseMetadata.
WAITER_OK: dict[str, Any] = {"ResponseMetadata": {"HTTPStatusCode": 200}}


@pytest.fixture
def moto() -> Iterator[None]:
    with mock_aws():
        yield


def _writer() -> tuple[LogWriter, io.StringIO]:
    out = io.StringIO()
    return LogWriter(Redactor(()), stdout=out), out


def _s3(recorder: Recorder | None = None) -> Any:
    s3 = boto3.client("s3", region_name="us-east-1")
    if recorder is not None:
        s3.meta.events.register("before-call.*.*", recorder)
    return s3


def _account() -> str:
    return b.resolve_account(boto3.client("sts", region_name="us-east-1"), profile="myprofile")


def _ensure(s3: Any, account: str, *, dry_run: bool = False) -> tuple[str, str]:
    writer, out = _writer()
    state = b.ensure_bucket(
        s3,
        bucket=BUCKET,
        region="us-east-1",
        account=account,
        profile="myprofile",
        dry_run=dry_run,
        writer=writer,
    )
    return state, out.getvalue()


def _puts(rec: Recorder) -> list[str]:
    return [op for svc, op in rec.calls if op.startswith(("Put", "Create", "Delete"))]


# ---------------------------------------------------------------- moto


def test_resolve_account(moto: None) -> None:
    account = _account()
    assert account.isdigit()
    assert len(account) == 12


def test_absent_bucket_is_created_with_the_exact_settings(moto: None) -> None:
    account = _account()
    s3 = _s3()
    state, out = _ensure(s3, account)
    assert state == "created"
    assert f"created bucket {BUCKET}" in out
    assert s3.get_public_access_block(Bucket=BUCKET)["PublicAccessBlockConfiguration"] == (
        b.PUBLIC_ACCESS_BLOCK
    )
    rules = s3.get_bucket_encryption(Bucket=BUCKET)["ServerSideEncryptionConfiguration"]["Rules"]
    assert rules[0]["ApplyServerSideEncryptionByDefault"] == {"SSEAlgorithm": "AES256"}
    assert s3.get_bucket_ownership_controls(Bucket=BUCKET)["OwnershipControls"] == b.OWNERSHIP
    assert json.loads(s3.get_bucket_policy(Bucket=BUCKET)["Policy"]) == b.bucket_policy(BUCKET)
    lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]
    assert lifecycle[0]["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 7}
    assert lifecycle[0]["ID"] == "fse-abort-incomplete-mpu"
    assert lifecycle[0]["Status"] == "Enabled"


def _full_bucket(s3: Any) -> None:
    compliant_bucket(BUCKET)
    s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(b.bucket_policy(BUCKET)))
    s3.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration=b.LIFECYCLE)


def test_compliant_existing_bucket_is_untouched(moto: None) -> None:
    account = _account()
    _full_bucket(_s3())
    rec = Recorder()
    state, out = _ensure(_s3(rec), account)
    assert state == "verified"
    assert "warning" not in out
    assert _puts(rec) == []


def test_existing_bucket_without_policy_warns(moto: None) -> None:
    account = _account()
    s3 = _s3()
    compliant_bucket(BUCKET)
    s3.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration=b.LIFECYCLE)
    rec = Recorder()
    state, out = _ensure(_s3(rec), account)
    assert state == "verified"
    assert f"warning: bucket {BUCKET} has no DenyInsecureTransport policy" in out
    assert "AbortIncompleteMultipartUpload" not in out
    assert _puts(rec) == []


def test_existing_bucket_without_lifecycle_warns(moto: None) -> None:
    account = _account()
    s3 = _s3()
    compliant_bucket(BUCKET)
    s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(b.bucket_policy(BUCKET)))
    rec = Recorder()
    _, out = _ensure(_s3(rec), account)
    assert f"warning: bucket {BUCKET} has no AbortIncompleteMultipartUpload rule" in out
    assert "DenyInsecureTransport" not in out
    assert _puts(rec) == []


def test_existing_bucket_without_public_access_block_is_exit_2(moto: None) -> None:
    account = _account()
    _s3().create_bucket(Bucket=BUCKET)
    rec = Recorder()
    with pytest.raises(ExportInputError, match="public access block") as info:
        _ensure(_s3(rec), account)
    assert info.value.exit_code == 2
    assert "default encryption" in str(info.value)  # every failing setting is listed
    assert _puts(rec) == []


def test_dry_run_makes_only_head_bucket(moto: None) -> None:
    account = _account()
    rec = Recorder()
    state, out = _ensure(_s3(rec), account, dry_run=True)
    assert state == "would_create"
    assert out.startswith(f"would create bucket {BUCKET} with: public access block")
    assert rec.calls == [("s3", "HeadBucket")]
    _full_bucket(_s3())
    rec2 = Recorder()
    state, out = _ensure(_s3(rec2), account, dry_run=True)
    assert state == "would_verify"
    assert "would verify bucket settings" in out
    assert rec2.calls == [("s3", "HeadBucket")]


# ---------------------------------------------------------------- Stubber


def _stubbed() -> tuple[Any, Stubber]:
    s3 = boto3.client("s3", region_name="us-east-1")
    return s3, Stubber(s3)


def test_create_request_parameters_exactly() -> None:
    s3, stub = _stubbed()
    owner = {"Bucket": BUCKET, "ExpectedBucketOwner": ACCOUNT}
    stub.add_client_error(
        "head_bucket", service_error_code="404", http_status_code=404, expected_params=owner
    )
    # NIT 8: CreateBucket carries no ExpectedBucketOwner.
    stub.add_response(
        "create_bucket", {}, {"Bucket": BUCKET, "ObjectOwnership": "BucketOwnerEnforced"}
    )
    # NIT 10: the bucket_exists waiter (HeadBucket) runs before the first put.
    stub.add_response("head_bucket", WAITER_OK, owner)
    stub.add_response(
        "put_public_access_block",
        {},
        {**owner, "PublicAccessBlockConfiguration": b.PUBLIC_ACCESS_BLOCK},
    )
    stub.add_response(
        "put_bucket_encryption", {}, {**owner, "ServerSideEncryptionConfiguration": b.ENCRYPTION}
    )
    stub.add_response(
        "put_bucket_ownership_controls", {}, {**owner, "OwnershipControls": b.OWNERSHIP}
    )
    stub.add_response(
        "put_bucket_policy", {}, {**owner, "Policy": json.dumps(b.bucket_policy(BUCKET))}
    )
    stub.add_response(
        "put_bucket_lifecycle_configuration", {}, {**owner, "LifecycleConfiguration": b.LIFECYCLE}
    )
    with stub:
        state, _ = _ensure(s3, ACCOUNT)
    stub.assert_no_pending_responses()
    assert state == "created"
    policy = b.bucket_policy(BUCKET)["Statement"][0]
    assert policy["Resource"] == [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"]


def test_bucket_already_owned_by_you_counts_as_created() -> None:
    s3, stub = _stubbed()
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stub.add_client_error("create_bucket", service_error_code="BucketAlreadyOwnedByYou")
    stub.add_response("head_bucket", WAITER_OK)
    for op in (
        "put_public_access_block",
        "put_bucket_encryption",
        "put_bucket_ownership_controls",
        "put_bucket_policy",
        "put_bucket_lifecycle_configuration",
    ):
        stub.add_response(op, {})
    with stub:
        assert _ensure(s3, ACCOUNT)[0] == "created"


@pytest.mark.parametrize(
    ("code", "status", "error", "exit_code"),
    [
        ("403", 403, ExportAccessError, 3),
        ("301", 301, ExportInputError, 2),
    ],
)
def test_head_bucket_errors(code: str, status: int, error: type[Exception], exit_code: int) -> None:
    s3, stub = _stubbed()
    stub.add_client_error(
        "head_bucket",
        service_error_code=code,
        http_status_code=status,
        response_meta={"HTTPHeaders": {"x-amz-bucket-region": "eu-west-1"}},
    )
    with stub, pytest.raises(error) as info:
        _ensure(s3, ACCOUNT)
    assert info.value.exit_code == exit_code  # type: ignore[attr-defined]


def test_bucket_in_another_region_is_exit_2() -> None:
    s3, stub = _stubbed()
    stub.add_response("head_bucket", {"BucketRegion": "eu-west-1"})
    with stub, pytest.raises(ExportInputError, match="eu-west-1"):
        _ensure(s3, ACCOUNT)


def test_bucket_name_taken_elsewhere_is_exit_3() -> None:
    s3, stub = _stubbed()
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stub.add_client_error("create_bucket", service_error_code="BucketAlreadyExists")
    with stub, pytest.raises(ExportAccessError, match="taken by another AWS account") as info:
        _ensure(s3, ACCOUNT)
    assert info.value.exit_code == 3


def test_access_denied_on_a_getter_is_exit_3() -> None:
    s3, stub = _stubbed()
    stub.add_response("head_bucket", {})
    stub.add_client_error(
        "get_public_access_block", service_error_code="AccessDenied", http_status_code=403
    )
    with stub, pytest.raises(ExportAccessError):
        _ensure(s3, ACCOUNT)


# ---------------------------------------------------------------- policy normalization (NIT 9)


def _policy(value: Any, action: Any = "s3:*") -> dict[str, Any]:
    return {
        "Statement": {
            "Effect": "Deny",
            "Principal": "*",
            "Action": action,
            "Condition": {"Bool": {"aws:SecureTransport": value}},
        }
    }


@pytest.mark.parametrize(
    "policy",
    [
        _policy(False),
        _policy("false"),
        _policy(["false"]),
        _policy("FALSE"),
        _policy("false", ["s3:GetObject", "s3:*"]),
        b.bucket_policy(BUCKET),
    ],
)
def test_tls_deny_recognized(policy: dict[str, Any]) -> None:
    assert b.has_tls_deny(policy)


@pytest.mark.parametrize(
    "policy",
    [
        _policy("true"),
        _policy([]),
        _policy("false", "s3:GetObject"),
        {"Statement": [{"Effect": "Deny", "Action": "s3:*"}]},
        {"Statement": [{**_policy("false")["Statement"], "Effect": "Allow"}]},
        None,
        "not a policy",
    ],
)
def test_tls_deny_not_recognized(policy: Any) -> None:
    assert not b.has_tls_deny(policy)


def test_abort_multipart_rule() -> None:
    assert b.has_abort_multipart_rule(b.LIFECYCLE)
    disabled = {"Rules": [{**b.LIFECYCLE["Rules"][0], "Status": "Disabled"}]}
    assert not b.has_abort_multipart_rule(disabled)
    assert not b.has_abort_multipart_rule(None)
