"""Console settings, read once from the environment (``FSE_UI_*``).

Every path the API writes lives under ``data_dir`` (``<home>/.skylit-fse``, the
engine's default root), so the console and the commands it launches agree on
where runs, caches and live state are without passing a flag for each one.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from fse.settings import app_home
from fse.settings import project_dir as engine_project_dir
from pydantic import BaseModel, ConfigDict, Field

type Env = Literal["local", "aws"]

SESSION_TTL_S_DEFAULT = 8 * 3600
"""Absolute session lifetime: 8 hours, then the operator logs in again."""

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
_ECS_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_SUBNET_RE = re.compile(r"^subnet-[0-9a-f]{8,17}$")
_SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
_LOG_GROUP_RE = re.compile(r"^[\w/.#-]{1,512}$")
_INSTANCE_RE = re.compile(r"^i-[0-9a-f]{8,17}$")
_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9_.-]{3,128}$")
_ALARM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SettingsError(Exception):
    """A setting is invalid or two settings contradict each other; the API refuses to start."""


class OidcSettings(BaseModel):
    """The Cognito (or any OIDC) endpoints and the confidential client."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    client_id: str
    client_secret: str = Field(repr=False)
    authorize_url: str
    token_url: str
    jwks_url: str
    logout_url: str | None = None
    scopes: str = "openid email"


class Settings(BaseModel):
    """Everything the API reads from its environment."""

    model_config = ConfigDict(frozen=True)

    env: Env = "local"
    bind_host: str = "127.0.0.1"
    port: int = 8080
    home: Path
    """The home folder whose ``.skylit-fse`` is the data root (child ``HOME``)."""
    project_dir: Path
    """The engine Project folder (configs, calendars, research packages)."""
    public_url: str = "http://localhost:5173"
    """The origin the browser uses: WebSocket Origin check, OIDC callback."""
    dev_auth: bool = False
    enable_live: bool = False
    """``fse live`` (Practice / Combine). Off until the TopstepX terms are verified."""
    plugin_orders: bool = False
    """Terraform ``plugin_orders_enabled`` (``FSE_UI_PLUGIN_ORDERS=enabled``): Practice/Combine may
    be chosen on Live → Order mode. The runner itself reads the SSM switch."""
    plugin_orders_param: str = ""
    """The SSM switch parameter (``FSE_UI_PLUGIN_ORDERS_PARAM``), read live per request."""
    topstep_verified_on: str | None = None
    """``YYYY-MM-DD`` the owner verified the TopstepX terms (Terraform); ``None``: not."""
    session_ttl_s: int = SESSION_TTL_S_DEFAULT
    oidc: OidcSettings | None = None
    fse_bin: str = "fse"
    python_bin: str = "python3"
    static_dir: Path | None = None
    """The built SPA (``ui/web/dist``); served at ``/`` when set."""
    image_tag: str | None = None
    secret_store: Literal["local", "ssm"] = "local"  # noqa: S105 (a store kind)
    aws_region: str = "us-east-1"
    heavy_jobs: int = Field(1, ge=1, le=4)
    """Batch-type jobs that may run at once on the local executor."""
    # ---- AWS (Phase B): batch on Fargate Spot, runner through SSM, alarms
    executor: Literal["local", "ecs"] = "local"
    data_bucket: str | None = None
    ecs_cluster: str = "fse"
    ecs_task_family: str = "fse-batch"
    ecs_container: str = "fse"
    ecs_subnets: tuple[str, ...] = ()
    ecs_security_group: str | None = None
    batch_log_group: str = "/fse/batch"
    runner_controller: Literal["process", "ssm"] = "process"
    runner_instance_id: str | None = None
    runner_document: str = "fse-runner-control"
    runner_log_group: str = "/fse/runner"
    alarm_names: tuple[str, ...] = ()
    ecs_max_tasks: int = 4

    @property
    def runner_state_file(self) -> Path:
        """Written by host hooks; read-only in the UI container (``/data/host``)."""
        return self.data_dir / "host" / "runner-state.json"

    @property
    def runner_held_file(self) -> Path:
        """The hold marker the ``fse-runner-control`` stop action writes on the host."""
        return self.data_dir / "host" / "runner-held"

    @property
    def uploads_dir(self) -> Path:
        return self.ui_dir / "uploads"

    @property
    def data_dir(self) -> Path:
        return app_home(self.home)

    @property
    def ui_dir(self) -> Path:
        return self.data_dir / "ui"

    @property
    def db_path(self) -> Path:
        return self.ui_dir / "ui.sqlite"

    @property
    def configs_dir(self) -> Path:
        return self.data_dir / "configs"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def live_state_dir(self) -> Path:
        return self.data_dir / "live-state"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def cookie_name(self) -> str:
        # ``__Host-`` needs Secure; plain http is allowed only for local development.
        return "__Host-fse_session" if self.secure_cookies else "fse_session"

    def check(self) -> None:
        """Refuse unsafe combinations (raises :class:`SettingsError`)."""
        if self.dev_auth:
            if self.env != "local":
                raise SettingsError("FSE_UI_DEV_AUTH=1 is allowed only with FSE_UI_ENV=local")
            if not _is_loopback(self.bind_host):
                raise SettingsError(
                    f"FSE_UI_DEV_AUTH=1 needs a loopback bind address, not {self.bind_host!r}"
                )
        if not self.dev_auth and self.oidc is None:
            raise SettingsError(
                "no login is configured: set the FSE_UI_OIDC_* variables "
                "(or FSE_UI_DEV_AUTH=1 for local development)"
            )
        if self.env == "aws" and not self.secure_cookies:
            raise SettingsError("FSE_UI_PUBLIC_URL must be https:// when FSE_UI_ENV=aws")
        self._check_aws()

    def _check_aws(self) -> None:
        def match(name: str, value: str | None, pattern: re.Pattern[str]) -> None:
            if value is None or not pattern.fullmatch(value):
                raise SettingsError(f"FSE_UI_{name} is invalid: {value!r}")

        match("ECS_CLUSTER", self.ecs_cluster, _ECS_NAME_RE)
        match("ECS_TASK_FAMILY", self.ecs_task_family, _ECS_NAME_RE)
        match("ECS_CONTAINER", self.ecs_container, _ECS_NAME_RE)
        match("BATCH_LOG_GROUP", self.batch_log_group, _LOG_GROUP_RE)
        match("RUNNER_DOCUMENT", self.runner_document, _DOCUMENT_RE)
        match("RUNNER_LOG_GROUP", self.runner_log_group, _LOG_GROUP_RE)
        if len(self.alarm_names) > 20:
            raise SettingsError("FSE_UI_ALARM_NAMES lists more than 20 alarms")
        for alarm in self.alarm_names:
            match("ALARM_NAMES", alarm, _ALARM_RE)
        if not 1 <= self.ecs_max_tasks <= 16:
            raise SettingsError(f"FSE_UI_ECS_MAX_TASKS must be 1..16, not {self.ecs_max_tasks}")
        if self.data_bucket is not None:
            match("DATA_BUCKET", self.data_bucket, _BUCKET_RE)
        if self.executor == "ecs":
            if self.secret_store != "ssm":  # noqa: S105 (a store kind)
                raise SettingsError(
                    "FSE_UI_EXECUTOR=ecs needs FSE_UI_SECRET_STORE=ssm "
                    "(Fargate tasks read secrets from SSM)"
                )
            match("DATA_BUCKET", self.data_bucket, _BUCKET_RE)
            if not 1 <= len(self.ecs_subnets) <= 16:
                raise SettingsError("FSE_UI_ECS_SUBNETS needs 1 to 16 subnet ids")
            for subnet in self.ecs_subnets:
                match("ECS_SUBNETS", subnet, _SUBNET_RE)
            match("ECS_SECURITY_GROUP", self.ecs_security_group, _SG_RE)
        if self.runner_controller == "ssm":
            if self.enable_live:
                raise SettingsError(
                    "FSE_UI_RUNNER_CONTROLLER=ssm runs only fse run or fse paper: "
                    "FSE_UI_ENABLE_LIVE must stay false"
                )
            match("RUNNER_INSTANCE_ID", self.runner_instance_id, _INSTANCE_RE)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        e = dict(os.environ if environ is None else environ)

        def get(name: str, default: str | None = None) -> str | None:
            value = e.get(f"FSE_UI_{name}")
            return value if value is not None and value.strip() else default

        project = get("PROJECT_DIR")
        found = Path(project) if project else engine_project_dir()
        if found is None:
            raise SettingsError(
                "the engine Project folder is unknown: set FSE_UI_PROJECT_DIR "
                "(the folder with configs/, calendars/ and research/)"
            )
        oidc: OidcSettings | None = None
        if get("OIDC_ISSUER"):
            try:
                oidc = OidcSettings(
                    issuer=get("OIDC_ISSUER") or "",
                    client_id=get("OIDC_CLIENT_ID") or "",
                    client_secret=get("OIDC_CLIENT_SECRET") or "",
                    authorize_url=get("OIDC_AUTHORIZE_URL") or "",
                    token_url=get("OIDC_TOKEN_URL") or "",
                    jwks_url=get("OIDC_JWKS_URL") or "",
                    logout_url=get("OIDC_LOGOUT_URL"),
                )
            except ValueError as exc:
                raise SettingsError(f"invalid FSE_UI_OIDC_* settings: {exc}") from None
            missing = [k for k, v in oidc.model_dump().items() if v == ""]
            if missing:
                raise SettingsError(f"FSE_UI_OIDC_* settings missing: {', '.join(missing)}")
        static = get("STATIC_DIR")
        verified = get("TOPSTEP_VERIFIED_ON")
        if verified is not None:
            verified = verified.strip()
            if not _DATE_RE.fullmatch(verified):
                raise SettingsError(
                    f"FSE_UI_TOPSTEP_VERIFIED_ON must be YYYY-MM-DD or empty, not {verified!r}"
                )
        try:
            return cls(
                env=get("ENV", "local"),
                bind_host=get("BIND_HOST", "127.0.0.1") or "127.0.0.1",
                port=int(get("PORT", "8080") or "8080"),
                home=Path(get("HOME") or e.get("HOME") or str(Path.home())),
                project_dir=found,
                public_url=(get("PUBLIC_URL", "http://localhost:5173") or "").rstrip("/"),
                dev_auth=get("DEV_AUTH", "0") == "1",
                enable_live=get("ENABLE_LIVE", "false") in ("1", "true"),
                plugin_orders=get("PLUGIN_ORDERS", "disabled") == "enabled",
                plugin_orders_param=(get("PLUGIN_ORDERS_PARAM") or "").strip(),
                topstep_verified_on=verified,
                session_ttl_s=int(get("SESSION_TTL_S", str(SESSION_TTL_S_DEFAULT)) or 0),
                oidc=oidc,
                fse_bin=get("FSE_BIN", "fse") or "fse",
                python_bin=get("PYTHON_BIN", "python3") or "python3",
                static_dir=Path(static) if static else None,
                image_tag=get("IMAGE_TAG"),
                secret_store=get("SECRET_STORE", "local"),
                aws_region=get("AWS_REGION", "us-east-1") or "us-east-1",
                heavy_jobs=int(get("HEAVY_JOBS", "1") or "1"),
                executor=get("EXECUTOR", "local"),
                data_bucket=get("DATA_BUCKET"),
                ecs_cluster=get("ECS_CLUSTER", "fse"),
                ecs_task_family=get("ECS_TASK_FAMILY", "fse-batch"),
                ecs_container=get("ECS_CONTAINER", "fse"),
                ecs_subnets=_split_list(get("ECS_SUBNETS")),
                ecs_security_group=get("ECS_SECURITY_GROUP"),
                batch_log_group=get("BATCH_LOG_GROUP", "/fse/batch"),
                runner_controller=get("RUNNER_CONTROLLER", "process"),
                runner_instance_id=get("RUNNER_INSTANCE_ID"),
                runner_document=get("RUNNER_DOCUMENT", "fse-runner-control"),
                runner_log_group=get("RUNNER_LOG_GROUP", "/fse/runner"),
                alarm_names=_split_list(get("ALARM_NAMES")),
                ecs_max_tasks=int(get("ECS_MAX_TASKS", "4") or "4"),
            )
        except ValueError as exc:
            raise SettingsError(f"invalid FSE_UI_* settings: {exc}") from None


def _split_list(value: str | None) -> tuple[str, ...]:
    """``"a, b,,c"`` → ``("a", "b", "c")``."""
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
