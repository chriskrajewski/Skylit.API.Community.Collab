"""The export bucket: account lookup, creation and verification (design §8.4, NITs 8-10).

An absent bucket is created private: public access block (all four flags),
SSE-S3 (AES256) default encryption, ``BucketOwnerEnforced`` object ownership,
a deny-only ``DenyInsecureTransport`` policy and a 7-day abort-incomplete-
multipart lifecycle rule. An existing bucket is never modified: its required
settings are verified (exit 2 if any fails, before any upload) and the two
extras are only warned about.

Every S3 call passes ``ExpectedBucketOwner`` except ``CreateBucket``, which
does not accept it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from fse.awsexport import aws
from fse.awsexport.errors import ExportAccessError, ExportDataError, ExportInputError
from fse.logio.log_writer import LogWriter

__all__ = [
    "ENCRYPTION",
    "LIFECYCLE",
    "OWNERSHIP",
    "PUBLIC_ACCESS_BLOCK",
    "bucket_policy",
    "ensure_bucket",
    "has_abort_multipart_rule",
    "has_tls_deny",
    "resolve_account",
]

type S3Client = Any
type StsClient = Any

_ACCOUNT_RE: Final = re.compile(r"\d{12}")
_WAITER: Final = {"Delay": 2, "MaxAttempts": 15}

LIFECYCLE: Final = {
    "Rules": [
        {
            "ID": "fse-abort-incomplete-mpu",
            "Filter": {},
            "Status": "Enabled",
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
        }
    ]
}
PUBLIC_ACCESS_BLOCK: Final = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}
ENCRYPTION: Final = {
    "Rules": [
        {
            "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
            "BucketKeyEnabled": False,
        }
    ]
}
OWNERSHIP: Final = {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}

_CREATE_SUMMARY: Final = (
    "public access block (all four), SSE-S3 AES256, BucketOwnerEnforced, "
    "DenyInsecureTransport policy, abort-incomplete-multipart lifecycle (7 days)"
)


def bucket_policy(bucket: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }
        ],
    }


# ---------------------------------------------------------------- account


def resolve_account(sts: StsClient, *, profile: str) -> str:
    """The 12-digit account id of the session's principal, from ``sts:GetCallerIdentity``."""
    identity = aws.call(profile, "GetCallerIdentity", "sts", sts.get_caller_identity)
    account = str(identity.get("Account", ""))
    if _ACCOUNT_RE.fullmatch(account) is None:
        raise ExportDataError("sts:GetCallerIdentity returned no valid account id")
    return account


# ---------------------------------------------------------------- policy checks


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def has_tls_deny(policy: Any) -> bool:
    """Whether a policy holds a ``Deny`` on ``s3:*`` when ``aws:SecureTransport`` is false.

    Hand-written policies spell the condition ``false``, ``"false"`` or
    ``["false"]`` (any case); ``Action`` may be ``"s3:*"`` or a list holding it.
    """
    if not isinstance(policy, dict):
        return False
    for statement in _as_list(policy.get("Statement")):
        if not isinstance(statement, dict) or statement.get("Effect") != "Deny":
            continue
        if "s3:*" not in _as_list(statement.get("Action")):
            continue
        condition = statement.get("Condition")
        bool_cond = condition.get("Bool", {}) if isinstance(condition, dict) else {}
        v = bool_cond.get("aws:SecureTransport") if isinstance(bool_cond, dict) else None
        if isinstance(v, list):
            v = v[0] if v else None
        if v is not None and str(v).lower() == "false":
            return True
    return False


def has_abort_multipart_rule(lifecycle: Any) -> bool:
    rules = lifecycle.get("Rules", []) if isinstance(lifecycle, dict) else []
    return any(
        isinstance(r, dict)
        and r.get("Status") == "Enabled"
        and "AbortIncompleteMultipartUpload" in r
        for r in rules
    )


# ---------------------------------------------------------------- the bucket


def _head(s3: S3Client, *, bucket: str, region: str, account: str, profile: str) -> bool:
    """Whether the bucket exists (in ``region``, owned by ``account``)."""
    from botocore.exceptions import BotoCoreError, ClientError

    resource = f"s3://{bucket}"
    try:
        resp = s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=account)
    except ClientError as exc:
        code = aws.error_code(exc)
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchBucket", "NotFound") or status == 404:
            return False
        if code == "403" or status == 403:
            raise ExportAccessError(
                f"bucket {bucket} exists in another account or access is denied "
                f"(HeadBucket 403 for profile '{profile}')"
            ) from exc
        if code in ("301", "PermanentRedirect") or status == 301:
            headers = exc.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            other = headers.get("x-amz-bucket-region", "another region")
            raise ExportInputError(f"bucket {bucket} is in {other}, not {region}") from exc
        raise aws.translate_aws_error(exc, "HeadBucket", resource, profile=profile) from exc
    except BotoCoreError as exc:
        raise aws.translate_aws_error(exc, "HeadBucket", resource, profile=profile) from exc
    headers = resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    actual = resp.get("BucketRegion") or headers.get("x-amz-bucket-region")
    if actual and actual != region:
        raise ExportInputError(f"bucket {bucket} is in {actual}, not {region}")
    return True


def _create(
    s3: S3Client, *, bucket: str, region: str, account: str, profile: str, writer: LogWriter
) -> None:
    from botocore.exceptions import BotoCoreError, ClientError

    resource = f"s3://{bucket}"
    kwargs: dict[str, Any] = {"Bucket": bucket, "ObjectOwnership": "BucketOwnerEnforced"}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)  # CreateBucket takes no ExpectedBucketOwner
    except ClientError as exc:
        code = aws.error_code(exc)
        if code == "BucketAlreadyExists":
            raise ExportAccessError(
                f"bucket name {bucket} is taken by another AWS account; pass --bucket NAME"
            ) from exc
        if code != "BucketAlreadyOwnedByYou":
            raise aws.translate_aws_error(exc, "CreateBucket", resource, profile=profile) from exc
    except BotoCoreError as exc:
        raise aws.translate_aws_error(exc, "CreateBucket", resource, profile=profile) from exc
    owner = {"Bucket": bucket, "ExpectedBucketOwner": account}
    waiter = s3.get_waiter("bucket_exists")
    aws.call(profile, "HeadBucket (wait)", resource, waiter.wait, WaiterConfig=_WAITER, **owner)
    puts: tuple[tuple[str, Any, dict[str, Any]], ...] = (
        (
            "PutPublicAccessBlock",
            s3.put_public_access_block,
            {"PublicAccessBlockConfiguration": PUBLIC_ACCESS_BLOCK},
        ),
        (
            "PutBucketEncryption",
            s3.put_bucket_encryption,
            {"ServerSideEncryptionConfiguration": ENCRYPTION},
        ),
        (
            "PutBucketOwnershipControls",
            s3.put_bucket_ownership_controls,
            {"OwnershipControls": OWNERSHIP},
        ),
        ("PutBucketPolicy", s3.put_bucket_policy, {"Policy": json.dumps(bucket_policy(bucket))}),
        (
            "PutBucketLifecycleConfiguration",
            s3.put_bucket_lifecycle_configuration,
            {"LifecycleConfiguration": LIFECYCLE},
        ),
    )
    for operation, fn, args in puts:
        aws.call(profile, operation, resource, fn, **args, **owner)
    writer.echo(f"created bucket {bucket} with: {_CREATE_SUMMARY}")


def _get(
    s3: S3Client, operation: str, fn: Any, *, bucket: str, account: str, profile: str, missing: str
) -> Any:
    """``fn(...)``, or ``None`` when the error code is ``missing`` (the setting is absent)."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return fn(Bucket=bucket, ExpectedBucketOwner=account)
    except ClientError as exc:
        if aws.error_code(exc) == missing:
            return None
        raise aws.translate_aws_error(exc, operation, f"s3://{bucket}", profile=profile) from exc
    except BotoCoreError as exc:
        raise aws.translate_aws_error(exc, operation, f"s3://{bucket}", profile=profile) from exc


def _verify(s3: S3Client, *, bucket: str, account: str, profile: str, writer: LogWriter) -> None:
    """Check an existing bucket's settings; modify nothing."""
    where = {"bucket": bucket, "account": account, "profile": profile}
    failing: list[str] = []
    pab = _get(
        s3,
        "GetPublicAccessBlock",
        s3.get_public_access_block,
        missing="NoSuchPublicAccessBlockConfiguration",
        **where,
    )
    flags = {} if pab is None else pab.get("PublicAccessBlockConfiguration", {})
    if not all(flags.get(k) is True for k in PUBLIC_ACCESS_BLOCK):
        failing.append("public access block is not fully on (all four flags)")
    enc = _get(
        s3,
        "GetBucketEncryption",
        s3.get_bucket_encryption,
        missing="ServerSideEncryptionConfigurationNotFoundError",
        **where,
    )
    rules = [] if enc is None else enc.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    algorithms = {
        r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm") for r in rules
    }
    if not algorithms & {"AES256", "aws:kms"}:
        failing.append("default encryption is not SSE-S3 (AES256) or SSE-KMS")
    own = _get(
        s3,
        "GetBucketOwnershipControls",
        s3.get_bucket_ownership_controls,
        missing="OwnershipControlsNotFoundError",
        **where,
    )
    own_rules = [] if own is None else own.get("OwnershipControls", {}).get("Rules", [])
    if not any(r.get("ObjectOwnership") == "BucketOwnerEnforced" for r in own_rules):
        failing.append("object ownership is not BucketOwnerEnforced")
    if failing:
        raise ExportInputError(
            f"bucket {bucket} exists but does not meet the required settings: "
            + "; ".join(failing)
            + ". Fix them in the S3 console or pass another --bucket; nothing was uploaded."
        )
    policy = _get(
        s3, "GetBucketPolicy", s3.get_bucket_policy, missing="NoSuchBucketPolicy", **where
    )
    try:
        parsed = None if policy is None else json.loads(policy.get("Policy", ""))
    except ValueError:
        parsed = None
    if not has_tls_deny(parsed):
        writer.echo(f"warning: bucket {bucket} has no DenyInsecureTransport policy")
    lifecycle = _get(
        s3,
        "GetBucketLifecycleConfiguration",
        s3.get_bucket_lifecycle_configuration,
        missing="NoSuchLifecycleConfiguration",
        **where,
    )
    if not has_abort_multipart_rule(lifecycle):
        writer.echo(f"warning: bucket {bucket} has no AbortIncompleteMultipartUpload rule")
    writer.echo(f"verified bucket {bucket} settings")


def ensure_bucket(
    s3: S3Client,
    *,
    bucket: str,
    region: str,
    account: str,
    profile: str,
    dry_run: bool,
    writer: LogWriter,
) -> str:
    """Create or verify the bucket: ``created``, ``verified``, ``would_create`` or ``would_verify``.

    Dry-run makes only ``HeadBucket``.
    """
    exists = _head(s3, bucket=bucket, region=region, account=account, profile=profile)
    if dry_run:
        if exists:
            writer.echo(f"would verify bucket settings of {bucket} (not checked in dry-run)")
            return "would_verify"
        writer.echo(f"would create bucket {bucket} with: {_CREATE_SUMMARY}")
        return "would_create"
    assert not dry_run
    if exists:
        _verify(s3, bucket=bucket, account=account, profile=profile, writer=writer)
        return "verified"
    _create(s3, bucket=bucket, region=region, account=account, profile=profile, writer=writer)
    return "created"
