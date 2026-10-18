"""Reading botocore errors without depending on their classes at every call site."""

from __future__ import annotations

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

ACCESS_DENIED = frozenset(
    {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "UnauthorizedException"}
)
TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    EndpointConnectionError,
    ReadTimeoutError,
    ConnectTimeoutError,
    NoCredentialsError,  # IMDS credentials not available yet at boot
)


def error_code(exc: BaseException) -> str:
    """The AWS error code (``NoSuchKey``, ``ThrottlingException``) or the exception type."""
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = str(err.get("Code") or "")
        if code:
            return code
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return str(status) if status else "ClientError"
    return type(exc).__name__


def http_status(exc: ClientError) -> int | None:
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return int(status) if status is not None else None


def is_access_denied(exc: BaseException) -> bool:
    return isinstance(exc, ClientError) and error_code(exc) in ACCESS_DENIED


def is_transient(exc: BaseException) -> bool:
    """Throttling, timeouts, 5xx, IMDS at boot: any ClientError but access denied, plus
    transport failures."""
    if isinstance(exc, ClientError):
        return not is_access_denied(exc)
    return isinstance(exc, TRANSPORT_ERRORS)


AWS_ERRORS: tuple[type[Exception], ...] = (ClientError, BotoCoreError)
