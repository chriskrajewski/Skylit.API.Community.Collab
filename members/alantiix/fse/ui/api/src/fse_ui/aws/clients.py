"""The boto3 clients the console uses on AWS, built once with bounded retries and timeouts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fse_ui.settings import Settings

RETRIES = {"mode": "standard", "max_attempts": 5}
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 20


@dataclass(frozen=True, slots=True)
class AwsClients:
    """No Cognito client: the UI never calls Cognito (the host script reads the client secret)."""

    ecs: Any
    s3: Any
    logs: Any
    ssm: Any
    cloudwatch: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> AwsClients:
        import boto3
        from botocore.config import Config

        config = Config(
            retries=RETRIES,
            connect_timeout=CONNECT_TIMEOUT_S,
            read_timeout=READ_TIMEOUT_S,
        )
        # Default CRC32 checksums make PutObject If-None-Match:* return 409
        # even when the key is absent, so a new lock looks held forever.
        s3_config = Config(
            retries=RETRIES,
            connect_timeout=CONNECT_TIMEOUT_S,
            read_timeout=READ_TIMEOUT_S,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        session = boto3.session.Session(region_name=settings.aws_region)

        def client(name: str, *, conditional_writes: bool = False) -> Any:
            return session.client(name, config=s3_config if conditional_writes else config)

        return cls(
            ecs=client("ecs"),
            s3=client("s3", conditional_writes=True),
            logs=client("logs"),
            ssm=client("ssm"),
            cloudwatch=client("cloudwatch"),
        )
