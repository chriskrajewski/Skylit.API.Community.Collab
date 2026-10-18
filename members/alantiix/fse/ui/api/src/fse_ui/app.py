"""The FastAPI application: wiring, security headers, auth routes and the SPA."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fse.apilog import FILE_NAME as API_LOG_FILE
from fse.apilog import enable as enable_api_log
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fse_ui import audit
from fse_ui.api import router as api_router
from fse_ui.api import ws_router
from fse_ui.auth import DEV_USER, AuthError, AuthService, principal
from fse_ui.aws.alarms import AlarmsReader
from fse_ui.aws.clients import AwsClients
from fse_ui.aws.ecs import EcsExecutor
from fse_ui.aws.runner_ssm import SsmRunnerController
from fse_ui.aws.s3store import (
    HOLDOUT_LOCK_KEY,
    PULL_LOCK_KEY,
    RECORDINGS_LOCK_KEY,
    CompositeArtifactStore,
    S3ArtifactStore,
    S3ConfigMirror,
    S3Lock,
)
from fse_ui.catalog import Catalog
from fse_ui.configs import ConfigStore
from fse_ui.db import Db
from fse_ui.jobs import Executor, JobManager, LocalExecutor
from fse_ui.runner import ProcessRunnerController, RunnerController
from fse_ui.runs import ArtifactStore, LocalArtifactStore, RunIndex
from fse_ui.secrets_store import LocalSecretStore, Redactor, SecretStore, SsmSecretStore
from fse_ui.settings import Settings

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = (
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"cache-control", b"no-store"),
)


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_asset = scope["path"].startswith("/assets/")

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for k, v in SECURITY_HEADERS:
                    if k == b"cache-control" and is_asset:
                        v = b"public, max-age=31536000, immutable"
                    if k not in present:
                        headers.append((k, v))
                if self.hsts:
                    headers.append(
                        (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, wrapped)


def child_env_factory(settings: Settings, secrets: SecretStore) -> Callable[[], dict[str, str]]:
    def child_env() -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("FSE_UI_")}
        env["HOME"] = str(settings.home)
        env["PYTHONUNBUFFERED"] = "1"
        env["PATH"] = os.pathsep.join(
            [str(Path(sys.executable).parent), env.get("PATH", "/usr/bin:/bin")]
        )
        env.update(secrets.child_env())
        if settings.data_bucket and not env.get("FSE_RECORDINGS_S3"):
            # recordings offloaded from the runner host (fse backtest-plugin --recordings-s3)
            env["FSE_RECORDINGS_S3"] = f"s3://{settings.data_bucket}/"
        return env

    return child_env


def _resolve_bin(name: str) -> str:
    if os.sep in name:
        return name
    beside = Path(sys.executable).parent / name
    if beside.exists():
        return str(beside)
    return shutil.which(name) or name


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    ssm_client: Any = None,
    aws: AwsClients | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check()
    settings = settings.model_copy(
        update={
            "fse_bin": _resolve_bin(settings.fse_bin),
            "python_bin": sys.executable
            if settings.python_bin == "python3"
            else settings.python_bin,
        }
    )
    # The runner child inherits this path and writes the same file the page reads.
    enable_api_log(settings.live_state_dir / API_LOG_FILE, force=True)
    if str(settings.project_dir) not in sys.path:
        sys.path.insert(0, str(settings.project_dir))  # research.* packages and plugins

    settings.ui_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = Db(settings.db_path)
    client = http_client or httpx.AsyncClient()
    uses_aws = (
        settings.executor == "ecs"
        or settings.runner_controller == "ssm"
        or bool(settings.alarm_names)
    )
    if uses_aws and aws is None:
        aws = AwsClients.from_settings(settings)
    if uses_aws or settings.secret_store == "ssm":  # noqa: S105 (a store kind)
        # Debug logging must never print PutParameter values or request bodies.
        for name in ("botocore", "boto3", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)
    secrets: SecretStore
    if settings.secret_store == "ssm":  # noqa: S105 (a store kind)
        if ssm_client is None:
            if aws is not None:
                ssm_client = aws.ssm
            else:
                import boto3

                ssm_client = boto3.client("ssm", region_name=settings.aws_region)
        secrets = SsmSecretStore(ssm_client)
    else:
        secrets = LocalSecretStore(settings.ui_dir / "secrets.json")

    def redactor() -> Callable[[str], str]:
        return Redactor(secrets.known_values())

    child_env = child_env_factory(settings, secrets)
    executors: dict[str, Executor] = {"local": LocalExecutor()}
    artifacts: ArtifactStore = LocalArtifactStore(settings.runs_dir)
    locks: dict[str, S3Lock] = {}
    s3_runs: S3ArtifactStore | None = None
    mirror: S3ConfigMirror | None = None
    if settings.executor == "ecs":
        assert aws is not None and settings.data_bucket is not None
        assert settings.ecs_security_group is not None
        bucket = settings.data_bucket
        locks = {
            "pull": S3Lock(aws.s3, bucket, PULL_LOCK_KEY),
            "holdout": S3Lock(aws.s3, bucket, HOLDOUT_LOCK_KEY),
            "recordings": S3Lock(aws.s3, bucket, RECORDINGS_LOCK_KEY),
        }
        executors["ecs"] = EcsExecutor(
            ecs=aws.ecs,
            logs=aws.logs,
            s3=aws.s3,
            cluster=settings.ecs_cluster,
            family=settings.ecs_task_family,
            container=settings.ecs_container,
            subnets=settings.ecs_subnets,
            security_group=settings.ecs_security_group,
            log_group=settings.batch_log_group,
            bucket=bucket,
            data_dir=settings.data_dir,
            locks=locks,
        )
        s3_runs = S3ArtifactStore(aws.s3, bucket)
        artifacts = CompositeArtifactStore(artifacts, s3_runs)
        mirror = S3ConfigMirror(aws.s3, bucket, settings.data_dir)
    store = ConfigStore(settings.configs_dir, mirror=mirror)
    jobs = JobManager(
        db,
        log_dir=settings.ui_dir / "jobs",
        runs_dir=settings.runs_dir,
        executors=executors,
        env=child_env,
        redact=redactor,
        cwd=settings.project_dir,
        heavy_slots=settings.heavy_jobs,
        ecs_slots=settings.ecs_max_tasks,
    )
    if s3_runs is not None:
        remote_runs = s3_runs
        jobs.on_finish(lambda job: remote_runs.invalidate())
    runner: RunnerController
    if settings.runner_controller == "ssm":
        assert aws is not None and settings.runner_instance_id is not None
        runner = SsmRunnerController(
            db,
            store,
            aws.ssm,
            aws.logs,
            instance_id=settings.runner_instance_id,
            document=settings.runner_document,
            log_group=settings.runner_log_group,
            state_file=settings.runner_state_file,
            held_file=settings.runner_held_file,
            enable_live=settings.enable_live,
            redact=redactor,
        )
    else:
        runner = ProcessRunnerController(
            db,
            store,
            fse_bin=settings.fse_bin,
            enable_live=settings.enable_live,
            env=child_env,
            redact=redactor,
            cwd=settings.project_dir,
            log_path=settings.ui_dir / "runner.log",
        )
    alarms = (
        AlarmsReader(aws.cloudwatch, settings.alarm_names)
        if settings.alarm_names and aws is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(store.seed, settings.project_dir)  # may mirror to S3
        jobs.recover()
        jobs.resume_remote()  # schedules re-attaches; never waits on AWS
        yield
        await jobs.shutdown()
        await runner.close()
        if http_client is None:
            await client.aclose()
        db.close()

    app = FastAPI(
        title="fse console",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.db = db
    app.state.auth = AuthService(settings, db, client)
    app.state.catalog = Catalog(target="aws" if settings.env == "aws" else "local")
    app.state.configs = store
    app.state.jobs = jobs
    app.state.runner = runner
    app.state.secrets = secrets
    app.state.child_env = child_env
    app.state.redactor = redactor
    app.state.runs = RunIndex(db, artifacts)
    app.state.locks = locks
    app.state.alarms = alarms
    app.state.aws = aws
    app.add_middleware(audit.AuditMiddleware, db=db)
    app.add_middleware(SecurityHeaders, hsts=settings.secure_cookies)

    # ------------------------------------------------------------ public routes

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "env": settings.env}

    @app.get("/auth/login")
    async def login() -> Response:
        auth: AuthService = app.state.auth
        if settings.dev_auth:
            response = RedirectResponse("/", status_code=303)
            _set_cookie(response, settings, auth.create_session(*DEV_USER))
            return response
        return RedirectResponse(auth.start_login(), status_code=303)

    @app.get("/auth/callback")
    async def callback(code: str = "", state: str = "", error: str = "") -> Response:
        if error:
            return JSONResponse({"detail": f"login failed: {error[:100]}"}, status_code=400)
        auth: AuthService = app.state.auth
        try:
            token = await auth.complete_login(code, state)
        except AuthError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        response = RedirectResponse("/", status_code=303)
        _set_cookie(response, settings, token)
        return response

    @app.post("/auth/logout")
    async def logout(request: Request) -> Response:
        who = principal(request)
        auth: AuthService = app.state.auth
        auth.end_session(who)
        response = JSONResponse({"logout_url": None if settings.dev_auth else auth.logout_url()})
        response.delete_cookie(
            settings.cookie_name,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    app.include_router(api_router)
    app.include_router(ws_router)

    # ------------------------------------------------------------ the SPA

    static = settings.static_dir
    if static is not None and (static / "index.html").is_file():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")
        root = static.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            if path.startswith(("api/", "auth/")):
                raise HTTPException(status_code=404)
            candidate = (root / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
            return FileResponse(root / "index.html")

    _install_openapi(app)
    return app


def _install_openapi(app: FastAPI) -> None:
    """Publish the contract. Card routes accept a bearer token or a cookie session."""

    def custom() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version="0.1.0",
            description=(
                "Cookie sessions send X-FSE-CSRF on every mutation. "
                "A service token is Authorization: Bearer fse_…, skips CSRF, "
                "and may only draft cards, run card backtests and judges, and read cards "
                "and the jobs those backtests launched. "
                "Mint a token with POST /api/service-tokens from a logged-in session. "
                "The raw token is returned once."
            ),
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes["ServiceToken"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "fse_<token>",
            "description": "Scoped card token. Not a session, and not accepted on other routes.",
        }
        schemes["SessionCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "fse_session",
        }
        schemes["CsrfHeader"] = {"type": "apiKey", "in": "header", "name": "X-FSE-CSRF"}
        schema["security"] = [{"SessionCookie": [], "CsrfHeader": []}]
        public = {"/api/health", "/auth/login", "/auth/callback"}
        cardish = ("/api/cards", "/api/schemas/card")
        for path, item in schema.get("paths", {}).items():
            if not isinstance(item, dict):
                continue
            for method, operation in item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not isinstance(operation, dict):
                    continue
                if path in public:
                    operation["security"] = []
                elif path.startswith(cardish):
                    if method == "get":
                        operation["security"] = [{"ServiceToken": []}, {"SessionCookie": []}]
                    else:
                        operation["security"] = [
                            {"ServiceToken": []},
                            {"SessionCookie": [], "CsrfHeader": []},
                        ]
        app.openapi_schema = schema
        return schema

    app.openapi = custom  # type: ignore[method-assign]


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_ttl_s,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
