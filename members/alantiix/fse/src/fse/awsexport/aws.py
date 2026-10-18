"""AWS session, the single client factory, the dry-run guard and error mapping (design §8.7, §9).

boto3 and botocore are imported inside functions only, so ``fse`` starts and
``fse export-aws --stage-only`` runs without them.

:func:`client` is the only place in the export that creates a boto3 client.
Every client gets :data:`DEFAULT_RETRIES` (standard mode, 10 attempts). A
dry-run client also gets :func:`_dry_run_guard` on its own event system: it
rejects any operation outside :data:`ALLOWED_DRY_RUN` before the request is
signed or sent. The guard is per client, not per session, so the internal
clients botocore's credential providers create (``sts:AssumeRole``, SSO) are
never blocked.

:func:`translate_aws_error` maps every botocore and boto3 failure to an
:class:`~fse.awsexport.errors.ExportError` with the design's exit code.
Messages hold the profile name, operation, resource and error code only,
never a credential value.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from fse.awsexport.errors import (
    DryRunViolation,
    ExportAccessError,
    ExportDataError,
    ExportError,
    ExportInputError,
)

__all__ = [
    "ACCESS_CODES",
    "ALLOWED_DRY_RUN",
    "DEFAULT_RETRIES",
    "TOKEN_CODES",
    "call",
    "client",
    "credential_hint",
    "default_config",
    "error_code",
    "make_session",
    "translate_aws_error",
]

type Session = Any  # boto3.Session
type Client = Any  # a boto3 client

ALLOWED_DRY_RUN: Final = frozenset(
    {("sts", "GetCallerIdentity"), ("s3", "HeadBucket"), ("glue", "GetDatabase")}
)
DEFAULT_RETRIES: Final[Mapping[str, object]] = MappingProxyType(
    {"mode": "standard", "max_attempts": 10}
)

# ClientError codes that mean credentials or access (exit 3).
TOKEN_CODES: Final = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "SignatureDoesNotMatch",
        "UnrecognizedClientException",
    }
)
ACCESS_CODES: Final = frozenset({"AccessDenied", "AccessDeniedException", "403"}) | TOKEN_CODES

_INSTALL_HINT: Final = (
    "boto3 is not installed; install it with: python -m pip install --require-hashes "
    "-r requirements.lock"
)


def credential_hint(profile: str) -> str:
    return (
        f"AWS credentials for profile '{profile}' are missing, invalid or expired; check with: "
        f"aws sts get-caller-identity --profile {profile} (for an SSO profile, run aws sso "
        f"login --profile {profile} first)"
    )


def _import_boto3() -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise ExportInputError(_INSTALL_HINT) from exc
    return boto3


def default_config() -> Any:
    """The botocore ``Config`` every export client starts from."""
    _import_boto3()
    from botocore.config import Config

    return Config(retries=dict(DEFAULT_RETRIES))


def make_session(profile: str, region: str) -> Session:
    """``boto3.Session(profile_name=profile, region_name=region)``; exit 3 if unknown."""
    boto3 = _import_boto3()
    from botocore.exceptions import ProfileNotFound

    try:
        return boto3.Session(profile_name=profile, region_name=region)
    except ProfileNotFound as exc:
        raise ExportAccessError(
            f"profile '{profile}' not found in ~/.aws/config or ~/.aws/credentials"
        ) from exc


def _dry_run_guard(event_name: str = "", **_: Any) -> None:
    """``before-call.{service}.{Operation}`` handler: only the allowlisted calls pass."""
    parts = event_name.split(".")
    service, operation = (parts[1], parts[2]) if len(parts) >= 3 else ("?", event_name)
    if (service, operation) not in ALLOWED_DRY_RUN:
        raise DryRunViolation(
            f"dry-run blocked AWS call {service}:{operation}; only "
            f"{', '.join(f'{s}:{o}' for s, o in sorted(ALLOWED_DRY_RUN))} are allowed (a bug)"
        )


def client(session: Session, service: str, *, dry_run: bool, config: Any = None) -> Client:
    """A client of ``service`` with the default retries, guarded when ``dry_run``."""
    base = default_config()
    c = session.client(service, config=base if config is None else base.merge(config))
    if dry_run:
        c.meta.events.register("before-call.*.*", _dry_run_guard)
    return c


def error_code(exc: BaseException) -> str:
    """The ``Error.Code`` of a ``ClientError``, else ``''``."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


def _status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status if isinstance(status, int) else None
    return None


def translate_aws_error(
    exc: BaseException, operation: str, resource: str, *, profile: str
) -> ExportError:
    """The :class:`ExportError` for a botocore or boto3 failure of ``operation`` on ``resource``."""
    from boto3.exceptions import S3UploadFailedError
    from botocore.exceptions import (
        BotoCoreError,
        ClientError,
        NoCredentialsError,
        PartialCredentialsError,
        ProfileNotFound,
        SSOTokenLoadError,
        TokenRetrievalError,
        UnauthorizedSSOTokenError,
    )

    if isinstance(exc, ExportError):
        return exc
    if isinstance(exc, S3UploadFailedError):
        inner = exc.__cause__ or exc.__context__
        if isinstance(inner, (ClientError, NoCredentialsError, PartialCredentialsError)):
            return translate_aws_error(inner, operation, resource, profile=profile)
        return ExportDataError(f"{operation} {resource} failed: {exc}")
    if isinstance(exc, ProfileNotFound):
        return ExportAccessError(
            f"profile '{profile}' not found in ~/.aws/config or ~/.aws/credentials"
        )
    if isinstance(
        exc,
        (
            NoCredentialsError,
            PartialCredentialsError,
            TokenRetrievalError,
            SSOTokenLoadError,
            UnauthorizedSSOTokenError,
        ),
    ):
        return ExportAccessError(f"{operation} {resource}: {credential_hint(profile)}")
    if isinstance(exc, ClientError):
        code = error_code(exc) or str(_status(exc) or "")
        if code in TOKEN_CODES:
            return ExportAccessError(
                f"{operation} {resource} failed ({code}): {credential_hint(profile)}"
            )
        if code in ACCESS_CODES or _status(exc) == 403:
            return ExportAccessError(
                f"{operation} {resource} was denied ({code}) for profile '{profile}'"
            )
        message = exc.response.get("Error", {}).get("Message", "")
        return ExportDataError(f"{operation} {resource} failed ({code}): {message}")
    if isinstance(exc, BotoCoreError):
        return ExportDataError(f"{operation} {resource} failed ({type(exc).__name__}): {exc}")
    return ExportDataError(f"{operation} {resource} failed ({type(exc).__name__}): {exc}")


def call(profile: str, operation: str, resource: str, fn: Any, /, **kwargs: Any) -> Any:
    """``fn(**kwargs)`` with every botocore failure translated."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return fn(**kwargs)
    except (ClientError, BotoCoreError) as exc:
        raise translate_aws_error(exc, operation, resource, profile=profile) from exc
