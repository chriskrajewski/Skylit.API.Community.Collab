"""The ``/api`` routes. Every route here needs a session (and CSRF on mutations)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from fse_ui import accounts_lookup as accounts
from fse_ui import audit, order_modes, paper, plugins, system
from fse_ui.api_logs import list_calls
from fse_ui.auth import Principal, principal, websocket_principal
from fse_ui.aws.ecs import lock_key
from fse_ui.aws.s3store import LockError, S3Lock, job_of
from fse_ui.backtest_overrides import OverrideError, materialize_override
from fse_ui.catalog import ArgvError, Catalog, build_args, full_argv
from fse_ui.configs import (
    SHA_RE,
    ConfigConflict,
    ConfigStore,
    ConfigStoreError,
    Kind,
    OrderModeRefused,
    runner_order_modes,
    validate,
)
from fse_ui.jobs import TERMINAL, JobManager, JobRequest, log_line_limit
from fse_ui.runner import (
    PLUGIN_ID_RE,
    RunnerController,
    RunnerError,
    account_reset_argv,
    card_publish,
    clear_argv,
    day_loss_reset_argv,
    halt_argv,
    live_cards,
    live_digest,
    live_snapshot,
    plugin_raw,
    runner_settings,
    set_runner_settings,
)
from fse_ui.runs import (
    DISCLAIMER,
    RunError,
    RunIndex,
    compare,
    equity_curve,
    r_histogram,
    read_csv,
    run_metrics,
)
from fse_ui.secrets_store import SECRET_NAMES, SecretError, SecretStore
from fse_ui.service_tokens import router as service_token_router
from fse_ui.settings import Settings
from fse_ui.strategy_cards import router as cards_router
from fse_ui.workflows import (
    PROBE_LIMIT_GB,
    UploadError,
    parse_pull_estimate,
    save_upload,
    upload_path,
)

if TYPE_CHECKING:
    from fse.live.host.config import RunnerConfig

log = logging.getLogger(__name__)
LIVE_INTERVAL_S = 1.0
"""The live WebSocket reads the live state this often (live P&L)."""
RUNNER_REFRESH_S = 5.0
"""How often the live WebSocket refreshes the runner status (CloudWatch reads on AWS)."""

router = APIRouter(prefix="/api", dependencies=[Depends(principal)])
router.include_router(service_token_router)
router.include_router(cards_router)
ws_router = APIRouter()


def _s(request: Request | WebSocket) -> Settings:
    s: Settings = request.app.state.settings
    return s


def _jobs(request: Request | WebSocket) -> JobManager:
    j: JobManager = request.app.state.jobs
    return j


def _store(request: Request) -> ConfigStore:
    c: ConfigStore = request.app.state.configs
    return c


def _runs(request: Request) -> RunIndex:
    r: RunIndex = request.app.state.runs
    return r


def _runner(request: Request) -> RunnerController:
    r: RunnerController = request.app.state.runner
    return r


def _who(request: Request) -> Principal:
    p: Principal = request.state.principal
    return p


# ---------------------------------------------------------------- session and audit


@router.get("/session")
async def session(request: Request) -> dict[str, Any]:
    who, s = _who(request), _s(request)
    return {
        "user": {"sub": who.sub, "email": who.email},
        "csrf": who.csrf,
        "env": s.env,
        "enable_live": s.enable_live,
        "plugin_orders": s.plugin_orders,
        "topstep_verified_on": s.topstep_verified_on,
        "dev_auth": s.dev_auth,
        "secret_store": s.secret_store,
        "executor": s.executor,
        "runner_controller": s.runner_controller,
    }


@router.get("/audit")
async def audit_log(
    request: Request, before: int | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    return audit.recent(request.app.state.db, limit=min(max(limit, 1), 500), before=before)


# ---------------------------------------------------------------- catalog and jobs


@router.get("/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    cat: Catalog = request.app.state.catalog
    return {"commands": [c.public() for c in cat.commands()], "errors": cat.errors()}


class JobIn(BaseModel):
    command: str = Field(max_length=200)
    values: dict[str, Any] = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=200)


def _resolve_uploads(request: Request, values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in values.items():
        if isinstance(v, dict) and set(v) == {"upload"}:
            try:
                v = str(upload_path(_s(request).ui_dir / "uploads", str(v["upload"])))
            except UploadError as exc:
                raise HTTPException(status_code=422, detail=f"{k}: {exc}") from None
        out[k] = v
    return out


def submit_command(
    request: Request, command_id: str, values: dict[str, Any], title: str | None = None
) -> dict[str, Any]:
    cat: Catalog = request.app.state.catalog
    try:
        cmd = cat.get(command_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if cmd.disabled_reason:
        raise HTTPException(status_code=409, detail=cmd.disabled_reason)
    try:
        args = build_args(cmd, _resolve_uploads(request, values))
    except ArgvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    s = _s(request)
    argv = full_argv(
        cmd,
        args,
        fse_bin=s.fse_bin,
        python_bin=s.python_bin,
        module=cat.program(cmd.program).module,
    )
    audit.note(request, command=command_id, args=args)
    executor = "ecs" if s.executor == "ecs" and cmd.placement == "batch" else "local"
    job = _jobs(request).submit(
        JobRequest(
            program=cmd.program,
            command=cmd.path,
            argv=tuple(argv),
            placement=cmd.placement,
            title=title or command_id,
            heavy=cmd.heavy,
            executor=executor,
            args=tuple(args),
        ),
        user=_who(request).email,
    )
    audit.note(request, job=job["id"])
    return job


@router.post("/jobs", status_code=201)
async def create_job(request: Request, body: JobIn) -> dict[str, Any]:
    return submit_command(request, body.command, body.values, body.title)


@router.get("/jobs")
async def list_jobs(
    request: Request, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    return _jobs(request).list_jobs(limit=min(max(limit, 1), 500), status=status)


def _job(request: Request, job_id: str) -> dict[str, Any]:
    job = _jobs(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


def _card_owns_job(request: Request, job_id: str) -> bool:
    row = request.app.state.db.one(
        "SELECT 1 AS ok FROM strategy_cards "
        "WHERE job_id = ? OR design_job_id = ? OR holdout_job_id = ?",
        (job_id, job_id, job_id),
    )
    return row is not None


def _job_for_caller(request: Request, job_id: str) -> dict[str, Any]:
    """A service token may read only a job a strategy card launched."""
    job = _job(request, job_id)
    if _who(request).kind == "service" and not _card_owns_job(request, job_id):
        raise HTTPException(
            status_code=403, detail="service token may only read a card backtest job"
        )
    return job


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict[str, Any]:
    return _job_for_caller(request, job_id)


@router.get("/jobs/{job_id}/log")
async def job_log(
    request: Request,
    job_id: str,
    lines: int | None = None,
    limit: int | None = None,
    tail: int | None = None,
) -> dict[str, Any]:
    """The last lines of a job log. ``lines``, ``limit`` and ``tail`` set the same cap."""
    _job_for_caller(request, job_id)
    return {"lines": _jobs(request).log_tail(job_id, lines=log_line_limit(lines, limit, tail))}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
    _job(request, job_id)
    audit.note(request, job=job_id)
    asyncio.get_running_loop().create_task(_jobs(request).cancel(job_id))
    return {"cancelling": True}


class PullEstimateIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.post("/workflows/pull-estimate", status_code=201)
async def pull_estimate(request: Request, body: PullEstimateIn) -> dict[str, Any]:
    values = {**body.values, "size_limit_gb": PROBE_LIMIT_GB}
    return submit_command(request, "fse pull", values, "pull estimate")


@router.get("/jobs/{job_id}/pull-estimate")
async def pull_estimate_result(request: Request, job_id: str) -> dict[str, Any]:
    job = _job(request, job_id)
    return {"job": job, **parse_pull_estimate(_jobs(request).log_tail(job_id, lines=20000))}


@router.post("/workflows/accounts-lookup", status_code=201)
async def accounts_lookup(request: Request) -> dict[str, Any]:
    return submit_command(request, accounts.ACCOUNTS_COMMAND, {"json": True}, "ProjectX accounts")


def _accounts_job(request: Request, job_id: str) -> dict[str, Any]:
    job = _job(request, job_id)
    if not accounts.is_accounts_job(job):
        raise HTTPException(status_code=404, detail="not an fse accounts job")
    return job


@router.get("/jobs/{job_id}/accounts")
async def accounts_lookup_result(request: Request, job_id: str) -> dict[str, Any]:
    job = _accounts_job(request, job_id)
    parsed = accounts.parse_accounts_lookup(_jobs(request).log_tail(job_id, lines=20000))
    rows = parsed["accounts"]
    if rows is None:
        # A queued or running lookup has not printed its line yet: no error so far.
        error = parsed["error"] if job["status"] in TERMINAL else None
        return {"job": job, "accounts": None, "diagnostics": [], "error": error}
    secrets: SecretStore = request.app.state.secrets

    def read() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        annotated = accounts.annotate(rows, secrets)
        return annotated, accounts.diagnostics(annotated, secrets)

    annotated, diags = await asyncio.to_thread(read)
    return {"job": job, "accounts": annotated, "diagnostics": diags, "error": None}


class AccountUseIn(BaseModel):
    job_id: str = Field(max_length=64)
    account_id: str = Field(max_length=32)
    role: Literal["practice", "combine"]
    confirm_not_practice: bool = False


@router.post("/accounts/use")
async def use_account(request: Request, body: AccountUseIn) -> dict[str, Any]:
    """Store a looked-up account id as PRACTICE_ACCOUNT_ID or COMBINE_ACCOUNT_ID."""
    audit.note(request, job=body.job_id, role=body.role)  # never the value
    job = _job(request, body.job_id)
    if not accounts.is_accounts_job(job) or job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="pick an account from a finished lookup")
    parsed = accounts.parse_accounts_lookup(_jobs(request).log_tail(body.job_id, lines=20000))
    rows = parsed["accounts"]
    if rows is None:
        raise HTTPException(status_code=409, detail=parsed["error"])
    secrets: SecretStore = request.app.state.secrets

    def write() -> str:
        name = accounts.check_use(
            rows, body.account_id, body.role, body.confirm_not_practice, secrets
        )
        secrets.put(name, body.account_id)
        return name

    try:
        name = await asyncio.to_thread(write)
    except accounts.UseError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    except SecretError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.note(request, name=name)
    return {"name": name, "status": "set", "restart": accounts.RESTART_NOTE}


@router.post("/uploads", status_code=201)
async def upload(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    data = await file.read(50 * 1024 * 1024 + 1)
    try:
        saved = save_upload(_s(request).ui_dir / "uploads", file.filename or "upload.csv", data)
    except UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.note(request, upload=saved["id"], name=saved["name"], size=saved["size"])
    return saved


# ---------------------------------------------------------------- batch locks (AWS)


def _locks(request: Request) -> dict[str, S3Lock]:
    locks: dict[str, S3Lock] = request.app.state.locks
    return locks


@router.get("/batch/locks")
async def batch_locks(request: Request) -> dict[str, Any]:
    locks = _locks(request)
    out: list[dict[str, Any]] = []
    for name, lock in locks.items():
        try:
            holder = await lock.holder()
        except LockError as exc:
            log.warning("cannot read %s: %s", lock.url, exc.code)
            out.append(
                {
                    "name": name,
                    "held": False,
                    "holder": None,
                    "job": None,
                    "error": f"cannot read the lock: {exc.code}",
                }
            )
            continue
        out.append(
            {"name": name, "held": holder is not None, "holder": holder, "job": job_of(holder)}
        )
    return {"enabled": bool(locks) and _s(request).executor == "ecs", "locks": out}


class UnlockIn(BaseModel):
    lock: Literal["pull", "holdout", "recordings"]
    confirm: Literal["unlock"]


@router.post("/batch/unlock")
async def batch_unlock(request: Request, body: UnlockIn) -> dict[str, Any]:
    locks = _locks(request)
    lock = locks.get(body.lock)
    if lock is None or _s(request).executor != "ecs":
        raise HTTPException(status_code=404, detail="batch locks exist only on AWS")
    running = _jobs(request).active_with(
        lambda j: j["executor"] == "ecs" and lock_key(j["program"], j["command"]) == lock.key
    )
    if running is not None:
        audit.note(request, lock=body.lock, refused=running["id"])
        raise HTTPException(
            status_code=409,
            detail=f"a {body.lock} job is running ({running['id']}): cancel it first",
        )
    try:
        holder = await lock.holder()
        deleted = await lock.force_release()
    except LockError as exc:
        audit.note(request, lock=body.lock, error=exc.code)
        raise HTTPException(status_code=502, detail=f"cannot delete the lock: {exc.code}") from None
    audit.note(request, lock=body.lock, holder=holder)
    return {"deleted": deleted}


# ---------------------------------------------------------------- configs and plugins


@router.get("/configs")
async def list_configs(request: Request, kind: str | None = None) -> list[dict[str, Any]]:
    try:
        return _store(request).list_configs(kind)
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/configs/{kind}/{name}")
async def get_config(
    request: Request, kind: str, name: str, sha: str | None = None
) -> dict[str, Any]:
    try:
        return _store(request).get(kind, name, sha)
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


class ConfigIn(BaseModel):
    text: str = Field(max_length=512 * 1024)
    message: str = Field(default="", max_length=200)
    create: bool = False


@router.post("/configs/{kind}/{name}")
async def save_config(request: Request, kind: str, name: str, body: ConfigIn) -> JSONResponse:
    audit.note(request, kind=kind, name=name, message=body.message, create=body.create)
    try:
        result, saved = await asyncio.to_thread(
            _store(request).save,
            kind,
            name,
            body.text,
            author=_who(request).email,
            message=body.message,
            create=body.create,
        )
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404 if not body.create else 409, detail=str(exc)) from None
    if saved is None:
        return JSONResponse({"validation": result.public(), "config": None}, status_code=422)
    audit.note(request, sha=saved["sha"])
    return JSONResponse({"validation": result.public(), "config": saved})


class ValidateIn(BaseModel):
    kind: str
    text: str = Field(max_length=512 * 1024)


@router.post("/configs/validate")
async def validate_config(request: Request, body: ValidateIn) -> dict[str, Any]:
    if body.kind not in ("runner", "strategy", "sweep", "research"):
        raise HTTPException(status_code=404, detail="unknown kind")
    kind = cast(Kind, body.kind)
    return validate(kind, body.text, baseline_text=_store(request).baseline_text()).public()


class RestoreIn(BaseModel):
    sha: str


@router.post("/configs/{kind}/{name}/restore")
async def restore_config(request: Request, kind: str, name: str, body: RestoreIn) -> dict[str, Any]:
    audit.note(request, kind=kind, name=name, sha=body.sha)
    try:
        return await asyncio.to_thread(
            _store(request).restore, kind, name, body.sha, author=_who(request).email
        )
    except OrderModeRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/configs/{kind}/{name}/diff")
async def diff_config(request: Request, kind: str, name: str, a: str, b: str) -> dict[str, str]:
    try:
        return {"diff": _store(request).diff(kind, name, a, b)}
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/schemas/{kind}")
async def schema(request: Request, kind: str, module: str | None = None) -> dict[str, Any]:
    if kind == "runner":
        from fse.live.host.config import RunnerConfig

        return RunnerConfig.model_json_schema()
    if kind == "strategy":
        from fse.config.schema import StrategyConfig

        return StrategyConfig.model_json_schema()
    if kind == "plugin" and module:
        found = plugins.config_schema(module, _s(request).project_dir)
        if found is not None:
            return found
    raise HTTPException(status_code=404, detail="no schema")


@router.get("/plugins/known")
async def known_plugins(request: Request) -> list[dict[str, Any]]:
    return await asyncio.to_thread(plugins.known_plugins, _s(request).project_dir)


@router.get("/plugins/backtested")
async def backtested(request: Request) -> dict[str, Any]:
    """The values each paper plugin was backtested with.

    Read from an optional ``research/paper_ops/backtested.yaml`` that you add.

    ``{"runners": {config name: {plugin id: {"source", "values": {dotted path: value}}}}}``;
    the Plugins page marks each field as matching or differing. Empty when there is none.
    """
    path = _s(request).project_dir / "research" / "paper_ops" / "backtested.yaml"

    def read() -> dict[str, Any]:
        import yaml

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return {"runners": {}}
        runners = data.get("runners") if isinstance(data, dict) else None
        return {"runners": runners if isinstance(runners, dict) else {}}

    return await asyncio.to_thread(read)


class PluginCheckIn(BaseModel):
    text: str = Field(max_length=512 * 1024)


@router.post("/plugins/check")
async def check(request: Request, body: PluginCheckIn) -> dict[str, Any]:
    s = _s(request)
    scratch = s.ui_dir / "plugin-check"
    return await asyncio.to_thread(
        plugins.check_plugins, body.text, project_dir=s.project_dir, scratch=scratch
    )


class CoverageIn(BaseModel):
    config: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    """A runner config name in the config store."""
    plugins: list[str] | None = Field(None, max_length=8)
    first: date
    last: date


@router.post("/backtest-plugin/coverage")
async def backtest_coverage(request: Request, body: CoverageIn) -> dict[str, Any]:
    """Which inputs ``fse backtest-plugin`` would find for each day (nothing runs)."""
    s = _s(request)
    if body.first > body.last or (body.last - body.first).days > 400:
        raise HTTPException(
            status_code=422, detail="first must be on or before last (at most 400 days)"
        )
    try:
        text = _store(request).current_path("runner", body.config).read_text(encoding="utf-8")
    except (ConfigStoreError, KeyError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"no runner config {body.config!r}") from exc
    return await asyncio.to_thread(
        plugins.plugin_coverage,
        text,
        body.plugins,
        body.first,
        body.last,
        project_dir=s.project_dir,
        cache_dir=s.cache_dir,
        recordings_root=s.data_dir,
        scratch=s.ui_dir / "plugin-check",
        recordings_s3=f"s3://{s.data_bucket}/" if s.data_bucket else None,
    )


class PluginOverrideIn(BaseModel):
    """Risk/fills/hold_through_break/config patches for one plugin (no secrets)."""

    risk: dict[str, Any] | None = None
    fills: dict[str, Any] | None = None
    hold_through_break: bool | None = None
    config: dict[str, Any] | None = None


class OverrideConfigIn(BaseModel):
    """Build a backtest-only runner copy. Never writes the live/prod source YAML."""

    source: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    keep: bool = False
    """True: save as a new ConfigStore runner name. False: one-shot under uploads/."""
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    text: str | None = Field(default=None, max_length=512 * 1024)
    """Full override YAML (paperized). Mutually exclusive with ``plugins``."""
    plugins: dict[str, PluginOverrideIn] | None = None
    """Per-plugin patches applied on top of ``source``."""


@router.post("/backtest-plugin/override-config")
async def backtest_override_config(request: Request, body: OverrideConfigIn) -> JSONResponse:
    """Materialize a runner YAML for ``fse backtest-plugin`` without touching live config.

    The live runner (SSM / ``configs/runner/<source>/current.yaml``) is unchanged. The
    job should pass the returned ``path`` as ``--config``. Order modes are forced to
    paper so a Practice/Combine source can still be forked for A/B backtests.
    """
    audit.note(
        request,
        source=body.source,
        keep=body.keep,
        name=body.name,
        has_text=body.text is not None,
        plugins=list((body.plugins or {}).keys()),
    )
    patches = None
    if body.plugins is not None:
        patches = {pid: p.model_dump(exclude_none=True) for pid, p in body.plugins.items()}
    try:
        result, payload = await asyncio.to_thread(
            materialize_override,
            _store(request),
            source=body.source,
            uploads_root=_s(request).ui_dir / "uploads",
            author=_who(request).email,
            keep=body.keep,
            name=body.name,
            text=body.text,
            plugins=patches,
        )
    except OverrideError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ConfigStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if payload is None:
        return JSONResponse({"validation": result.public(), "override": None}, status_code=422)
    audit.note(
        request,
        storage=payload["storage"],
        path=payload["path"],
        source_sha=payload["source_sha"],
    )
    return JSONResponse({"validation": result.public(), "override": payload})


# ---------------------------------------------------------------- runs and analytics


def _run_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except RunError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from None


@router.get("/runs")
async def list_runs(
    request: Request, kind: str | None = None, config: str | None = None, q: str | None = None
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_runs(request).list_runs, kind=kind, config=config, text=q)


def _run_detail(store: Any, run: str) -> dict[str, Any]:
    from fse_ui.runs import summarize_run

    summary = _run_call(summarize_run, store, run)
    manifest = None
    if store.exists(run, "run_manifest.json"):
        with contextlib.suppress(ValueError):
            manifest = json.loads(_run_call(store.read_bytes, run, "run_manifest.json"))
    return {
        **summary,
        "files": _run_call(store.files, run),
        "manifest": manifest,
        "disclaimer": DISCLAIMER,
    }


@router.get("/runs/{run}")
async def get_run(request: Request, run: str) -> dict[str, Any]:
    return await asyncio.to_thread(_run_detail, _runs(request).store, run)


@router.get("/runs/{run}/metrics")
async def get_metrics(request: Request, run: str) -> dict[str, Any]:
    return await asyncio.to_thread(_run_call, run_metrics, _runs(request).store, run)


async def _trades(request: Request, run: str) -> list[dict[str, Any]]:
    store = _runs(request).store
    raw = await asyncio.to_thread(_run_call, store.read_bytes, run, "trades.json")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="trades.json is not valid JSON") from None
    return data if isinstance(data, list) else []


@router.get("/runs/{run}/trades")
async def get_trades(
    request: Request, run: str, offset: int = 0, limit: int = 200, shadow: bool = False
) -> dict[str, Any]:
    trades = [t for t in await _trades(request, run) if bool(t.get("shadow")) == shadow]
    limit = min(max(limit, 1), 500)
    return {"total": len(trades), "rows": trades[max(offset, 0) : max(offset, 0) + limit]}


@router.get("/runs/{run}/equity")
async def get_equity(request: Request, run: str) -> dict[str, Any]:
    trades = await _trades(request, run)
    return {**equity_curve(trades), "histogram": r_histogram(trades)}


@router.get("/runs/{run}/file")
async def get_file(
    request: Request, run: str, name: str, offset: int = 0, limit: int = 200
) -> dict[str, Any]:
    store = _runs(request).store
    if name.endswith(".csv"):
        return {
            "type": "csv",
            **await asyncio.to_thread(
                _run_call,
                read_csv,
                store,
                run,
                name,
                offset=max(offset, 0),
                limit=min(max(limit, 1), 1000),
            ),
        }
    raw = await asyncio.to_thread(_run_call, store.read_bytes, run, name)
    if name.endswith(".json"):
        try:
            return {"type": "json", "data": json.loads(raw)}
        except ValueError:
            return {"type": "text", "text": raw.decode("utf-8", "replace")}
    if name.endswith((".jsonl", ".log")):
        lines = raw.decode("utf-8", "replace").splitlines()
        return {"type": "lines", "total": len(lines), "lines": lines[-min(max(limit, 1), 2000) :]}
    if name.endswith((".md", ".txt", ".yaml", ".yml")):
        return {"type": "text", "text": raw.decode("utf-8", "replace")}
    raise HTTPException(status_code=415, detail="this file type is not shown in the console")


@router.get("/compare")
async def compare_runs(request: Request, runs: str) -> dict[str, Any]:
    names = [r for r in runs.split(",") if r]
    return await asyncio.to_thread(_run_call, compare, _runs(request).store, names)


# ---------------------------------------------------------------- runner and live


@router.get("/runner")
async def runner_status(request: Request) -> dict[str, Any]:
    status = await _runner(request).status()
    return {**status, "enable_live": _s(request).enable_live}


class RunnerSettingsIn(BaseModel):
    mode: str
    runner_config: str
    strategy_config: str


@router.put("/runner/settings")
async def put_runner_settings(request: Request, body: RunnerSettingsIn) -> dict[str, Any]:
    audit.note(request, **body.model_dump())
    try:
        return set_runner_settings(
            request.app.state.db,
            mode=body.mode,
            runner_config=body.runner_config,
            strategy_config=body.strategy_config,
            store=_store(request),
        )
    except (RunnerError, ConfigStoreError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


order_mode_lock = asyncio.Lock()
"""Live → Order mode (checks, save, restart) and runner start/restart, one at a time."""


def _busy_order_problem(request: Request, current: dict[str, Any] | None = None) -> str | None:
    """Section 3 rule 1 for anything that (re)starts the runner: the busy message when a
    plugin that shows an open position or a working order would start in another mode
    than it last ran in. Next mode: its config ``order_mode`` (``current``: the selected
    runner config's, read here if ``None``) when the runner switch is enabled, else
    paper; an unreadable switch counts as either. Last mode: its newest ``order_mode``
    line, paper without one. A same-mode start restores from state, so a ``status.json``
    left by a stopped or failed runner (only the running one rewrites it) never blocks."""
    s = _s(request)
    busy = order_modes.busy_plugins(s.live_state_dir)
    if not busy:
        return None
    if current is None:
        name = runner_settings(request.app.state.db)["runner_config"]
        try:
            current = runner_order_modes(str(_store(request).get("runner", name)["text"]))
        except ConfigStoreError:
            current = {}  # the start itself reports the missing config
    switch = order_modes.read_switch(s, _order_ssm(request))
    effective = order_modes.effective_modes(s.runs_dir)
    blocking: list[str] = []
    for pid in sorted(busy):
        line = effective.get(pid)
        last = "paper" if line is None else str(line.get("mode"))
        configured = str(current[pid][1]) if pid in current else "paper"
        if switch.error is not None:
            nexts = {configured, "paper"}
        else:
            nexts = {configured if switch.enabled else "paper"}
        if nexts != {last}:
            blocking.append(pid)
    return order_modes.busy_message(blocking, busy) if blocking else None


@router.post("/runner/{action}")
async def runner_action(request: Request, action: str) -> dict[str, Any]:
    runner = _runner(request)
    audit.note(request, action=action, settings=runner_settings(request.app.state.db))
    try:
        if action == "start":
            async with order_mode_lock:
                if problem := await asyncio.to_thread(_busy_order_problem, request):
                    raise HTTPException(status_code=409, detail=problem)
                return await runner.start()
        if action == "stop":
            return await runner.stop()
        if action == "restart":
            async with order_mode_lock:
                if problem := await asyncio.to_thread(_busy_order_problem, request):
                    raise HTTPException(status_code=409, detail=problem)
                return await runner.restart()  # SSM: one SendCommand action=restart
    except (RunnerError, ConfigStoreError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    raise HTTPException(status_code=404, detail="unknown runner action")


# ---------------------------------------------------------------- order mode (Live)

CONFIRM_RESTART = (
    "Saving restarts the runner. Every plugin stops and restores from its state; a restart "
    "mid-session can miss bars. Practice and Combine send real orders to ProjectX."
)


def _order_ssm(request: Request) -> Any:
    """The SSM client for the live switch read (AWS runner only)."""
    aws = request.app.state.aws
    return aws.ssm if aws is not None and _s(request).runner_controller == "ssm" else None


def _order_modes_view(request: Request, status: dict[str, Any]) -> dict[str, Any]:
    """``GET /api/order-modes``: the selected runner config's broker plugins and why
    Practice or Combine is or is not available (blocking: run it in a thread)."""
    s, store, db = _s(request), _store(request), request.app.state.db
    secrets: SecretStore = request.app.state.secrets
    chosen = runner_settings(db)
    try:
        cfg = store.get("runner", chosen["runner_config"])
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    text = str(cfg["text"])
    ssm = _order_ssm(request)
    switch = order_modes.read_switch(s, ssm)
    brokers = order_modes.broker_plugins(text, s.project_dir, s.ui_dir / "plugin-check")
    non_paper = [pid for pid, (_, m) in runner_order_modes(text).items() if m != "paper"]
    effective = order_modes.effective_modes(s.runs_dir)
    busy = order_modes.busy_plugins(s.live_state_dir)
    redact = request.app.state.redactor()
    runner_mode = str(chosen["mode"])
    plugin_rows = []
    for p in brokers:
        line = effective.get(p.id)
        if line is not None:
            line = {**line, "failures": [redact(f) for f in line["failures"]]}
        plugin_rows.append(
            {
                "id": p.id,
                "label": p.label,
                "order_mode": p.order_mode,
                "max_daily_loss": p.max_daily_loss,
                "max_trades_per_day": p.max_trades_per_day,
                "hold_through_break": p.hold_through_break,
                "practice_blocked": order_modes.practice_blocked(text, p, non_paper=non_paper),
                "combine_blocked": order_modes.combine_blocked(text, p, non_paper=non_paper),
                "effective": line,
                "busy": p.id in busy,
            }
        )
    return {
        "runner_config": chosen["runner_config"],
        "sha": cfg["sha"],
        "runner_state": status.get("state"),
        "held": bool(status.get("held")),
        "plugin_orders": s.plugin_orders,
        "disabled_reasons": order_modes.disabled_reasons(
            s, secrets, ssm, runner_mode=runner_mode, switch=switch, for_mode="practice"
        ),
        "combine_disabled_reasons": order_modes.disabled_reasons(
            s, secrets, ssm, runner_mode=runner_mode, switch=switch, for_mode="combine"
        ),
        "switch_pending": order_modes.switch_pending(switch, effective),
        "plugins": plugin_rows,
    }


@router.get("/order-modes")
async def get_order_modes(request: Request) -> dict[str, Any]:
    status = await _runner(request).status()
    return await asyncio.to_thread(_order_modes_view, request, status)


class OrderModeIn(BaseModel):
    plugin_id: str = Field(max_length=64)
    order_mode: str = Field(max_length=32)
    max_daily_loss: str | None = Field(default=None, max_length=32)
    max_trades_per_day: int | None = None
    expected_sha: str = Field(max_length=64)
    confirm_restart: bool = False


def _order_mode_prepare(request: Request, body: OrderModeIn, caps: Any) -> tuple[str, str]:
    """Checks 422 then busy 409 (section 3 rule 1); the runner config name and edited text."""
    s, store, db = _s(request), _store(request), request.app.state.db
    chosen = runner_settings(db)
    name = str(chosen["runner_config"])
    try:
        cfg = store.get("runner", name)
    except ConfigStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if cfg["sha"] != body.expected_sha:
        raise HTTPException(status_code=409, detail="the runner config changed; reload")
    text = str(cfg["text"])
    scratch = s.ui_dir / "plugin-check"
    brokers = order_modes.broker_plugins(text, s.project_dir, scratch)
    target = next((p for p in brokers if p.id == body.plugin_id), None)
    if target is None:
        raise HTTPException(
            status_code=422, detail=f"{body.plugin_id} is not an enabled broker plugin in {name}"
        )
    current = runner_order_modes(text)
    brokered = body.order_mode in order_modes.BROKER_ORDER_MODES
    if brokered:
        ssm = _order_ssm(request)
        non_paper = [pid for pid, (_, m) in current.items() if m != "paper"]
        mode = cast(Literal["practice", "combine"], body.order_mode)
        reasons = order_modes.disabled_reasons(
            s,
            request.app.state.secrets,
            ssm,
            runner_mode=str(chosen["mode"]),
            non_paper=non_paper,
            plugin_id=target.id,
            for_mode=mode,
        )
        for reason in order_modes.mode_blocked(text, target, mode, non_paper=non_paper):
            if reason not in reasons:
                reasons.append(reason)
        if reasons:
            raise HTTPException(status_code=422, detail="; ".join(reasons))
    try:
        new = order_modes.apply_order_mode(text, target.id, body.order_mode, caps)
    except order_modes.OrderModeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if brokered:  # what fse run checks at start; paper is never blocked by a plugin problem
        checked = plugins.check_plugins(new, project_dir=s.project_dir, scratch=scratch)
        if not checked["ok"]:
            raise HTTPException(status_code=422, detail="; ".join(checked["problems"]))
    busy = order_modes.busy_plugins(s.live_state_dir)
    scope = {target.id} | {pid for pid, (_, m) in current.items() if m != "paper"}
    scope |= {
        pid
        for pid, line in order_modes.effective_modes(s.runs_dir).items()
        if line is not None and line.get("mode") != "paper"
    }
    blocking = sorted(scope & busy.keys())
    if blocking:
        raise HTTPException(status_code=409, detail=order_modes.busy_message(blocking, busy))
    return name, new


@router.post("/order-mode")
async def post_order_mode(request: Request, body: OrderModeIn) -> dict[str, Any]:
    """Set a broker plugin's order mode in the selected runner config (design 2.6, 3)."""
    audit.note(
        request,
        plugin=body.plugin_id,
        order_mode=body.order_mode,
        max_daily_loss=body.max_daily_loss,
        max_trades_per_day=body.max_trades_per_day,
        confirm_restart=body.confirm_restart,
    )
    if not PLUGIN_ID_RE.fullmatch(body.plugin_id):
        raise HTTPException(status_code=422, detail=f"invalid plugin id {body.plugin_id!r}")
    if body.order_mode not in order_modes.ORDER_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"order_mode must be paper, practice or combine, not {body.order_mode!r}",
        )
    if not SHA_RE.fullmatch(body.expected_sha):
        raise HTTPException(
            status_code=422, detail="expected_sha must be 12 lowercase hex characters"
        )
    caps = None
    if body.order_mode in order_modes.BROKER_ORDER_MODES:
        try:
            caps = order_modes.check_caps(body.max_daily_loss, body.max_trades_per_day)
        except order_modes.OrderModeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    async with order_mode_lock:
        name, new = await asyncio.to_thread(_order_mode_prepare, request, body, caps)
        runner = _runner(request)
        status = await runner.status()
        state = status.get("state")
        if state == "unknown":
            raise HTTPException(
                status_code=409,
                detail=f"the runner state is unknown ({status.get('error') or 'no report'}); "
                "retry when the Live page shows it",
            )
        if state in ("starting", "stopping"):
            raise HTTPException(status_code=409, detail="the runner is changing state; retry")
        if state == "running" and not body.confirm_restart:
            raise HTTPException(
                status_code=409, detail={"code": "confirm_required", "message": CONFIRM_RESTART}
            )
        try:
            result, saved = await asyncio.to_thread(
                _store(request).save,
                "runner",
                name,
                new,
                author=_who(request).email,
                message=f"order_mode {body.order_mode} for {body.plugin_id} (console)",
                expected_sha=body.expected_sha,
                allow_order_mode=True,
            )
        except ConfigConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ConfigStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if saved is None:
            raise HTTPException(
                status_code=422, detail="; ".join(e.message for e in result.errors)
            )
        sha = str(saved["sha"])
        audit.note(request, runner_config=name, sha=sha)
        restarted = False
        if state == "running":
            try:
                await runner.restart()
            except Exception as exc:  # the save stands; the runner keeps its old mode
                reason = str(exc) or type(exc).__name__
                log.warning("order mode saved as revision %s; restart failed: %s", sha, reason)
                audit.note(request, restart_failed=reason)
                raise HTTPException(
                    status_code=502, detail=f"saved revision {sha}; restart failed: {reason}"
                ) from None
            restarted = True
            message = f"saved revision {sha}; the runner is restarting with it"
        else:
            message = f"saved revision {sha}; applies on the next start"
        status = await runner.status()
    view = await asyncio.to_thread(_order_modes_view, request, status)
    return {
        **view,
        "saved": sha,
        "restarted": restarted,
        "message": message,
        "note": order_modes.PAPER_NOTE if body.order_mode == "paper" else None,
    }


class HaltIn(BaseModel):
    plugin_id: str | None = None
    reason: str = Field(default="", max_length=200)


async def _quick(
    request: Request, argv: list[str], ok_codes: tuple[int, ...] = (0,)
) -> dict[str, Any]:
    """Run a short CLI; 500 with its output unless the exit code is in ``ok_codes``."""
    s = _s(request)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=request.app.state.child_env(),
        cwd=s.project_dir,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
    except TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail=f"{argv[1]} did not finish in 60 s") from None
    redact = request.app.state.redactor()
    lines = [redact(ln) for ln in out.decode("utf-8", "replace").splitlines()]
    result = {"exit_code": proc.returncode, "output": lines}
    if proc.returncode not in ok_codes:
        raise HTTPException(status_code=500, detail=result)
    return result


@router.post("/halt")
async def halt(request: Request, body: HaltIn) -> dict[str, Any]:
    s = _s(request)
    try:
        argv = halt_argv(s.fse_bin, s.live_state_dir, body.plugin_id, body.reason)
    except RunnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.note(request, plugin=body.plugin_id or "all", reason=body.reason)
    return await _quick(request, argv)


class ClearIn(BaseModel):
    plugin_id: str | None = None


@router.post("/clear")
async def clear(request: Request, body: ClearIn) -> dict[str, Any]:
    s = _s(request)
    try:
        argv = clear_argv(s.fse_bin, s.live_state_dir, body.plugin_id)
    except RunnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.note(request, plugin=body.plugin_id or "all")
    return await _quick(request, argv)


class DayLossResetIn(BaseModel):
    reason: str = Field(default="", max_length=200)


@router.post("/plugins/{plugin_id}/reset-day-loss")
async def reset_day_loss(request: Request, plugin_id: str, body: DayLossResetIn) -> dict[str, Any]:
    """Zero one plugin's paper day_net (today's max-loss budget). Does not edit YAML."""
    s = _s(request)
    try:
        argv = day_loss_reset_argv(s.fse_bin, s.live_state_dir, plugin_id, body.reason)
    except RunnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.note(request, plugin=plugin_id, reason=body.reason)
    return await _quick(request, argv)


def _snapshot(app: Any) -> dict[str, Any]:
    s: Settings = app.state.settings
    snap = live_snapshot(s.runs_dir, s.live_state_dir, runner_cfg=_runner_cfg(app))
    redact = app.state.redactor()
    for item in snap["plugins"]:
        line = item.get("order_mode")
        if line is not None:  # order_router interpolates broker error text into failures
            item["order_mode"] = {**line, "failures": [redact(f) for f in line["failures"]]}
    return snap


@router.get("/live")
async def live(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_snapshot, request.app)


@router.get("/api-logs")
async def api_logs(
    request: Request,
    api: str = "all",
    status: str = "all",
    endpoint: str = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> dict[str, object]:
    """Recent Skylit and ProjectX calls (live-state ring, polled by the API logs page)."""
    try:
        return await asyncio.to_thread(
            list_calls,
            _s(request).live_state_dir,
            api=api,
            status=status,
            endpoint=endpoint,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except OSError:
        raise HTTPException(status_code=500, detail="the API log could not be read") from None


# ---------------------------------------------------------------- paper analytics

# One parsed runner YAML per console home, keyed by the sha256 of its text, so the
# 1 s live poll re-parses only when the config changes. ``None``: it does not parse.
_RUNNER_CFG: dict[str, tuple[str, RunnerConfig | None]] = {}


def _home_paths(obj: object, home: Path) -> object:
    """The YAML document with ``~`` in ``paths`` meaning the console's home, the
    ``HOME`` the runner child gets (``child_env``), not this process's."""
    if not isinstance(obj, dict) or not isinstance(obj.get("paths"), dict):
        return obj
    paths = {}
    for key, value in obj["paths"].items():
        if isinstance(value, str) and (value == "~" or value.startswith("~/")):
            value = str(home) + value[1:]
        paths[key] = value
    return {**obj, "paths": paths}


def _runner_cfg(app: Any) -> RunnerConfig | None:
    """The runner YAML the console runner settings point at, parsed (cached)."""
    import yaml
    from fse.live.host.config import RunnerConfigError, parse_runner_config

    s: Settings = app.state.settings
    store: ConfigStore = app.state.configs
    try:
        path = store.current_path("runner", runner_settings(app.state.db)["runner_config"])
        text = path.read_text(encoding="utf-8")
    except ConfigStoreError, OSError, KeyError:
        return None
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = str(s.home)
    hit = _RUNNER_CFG.get(key)
    if hit is not None and hit[0] == sha:
        return hit[1]
    cfg: RunnerConfig | None
    try:
        cfg = parse_runner_config(_home_paths(yaml.safe_load(text), s.home), path)
    except RunnerConfigError, yaml.YAMLError, ValueError:
        cfg = None
    _RUNNER_CFG[key] = (sha, cfg)
    return cfg


def _plugin_id(plugin_id: str) -> str:
    if not paper.PLUGIN_ID_RE.fullmatch(plugin_id):
        raise HTTPException(status_code=422, detail=f"invalid plugin id {plugin_id!r}")
    return plugin_id


def _account_id(account_id: str) -> str:
    if not paper.ACCOUNT_ID_RE.fullmatch(account_id):
        raise HTTPException(status_code=422, detail=f"invalid account id {account_id!r}")
    return account_id


def _range(start: str | None, end: str | None) -> tuple[date, date]:
    try:
        return paper.parse_range(start, end, paper.globex_today(time.time_ns()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


_From = Annotated[str | None, Query(alias="from")]
_To = Annotated[str | None, Query()]
_Limit = Annotated[int, Query(ge=1, le=5000)]


def _paper_today(app: Any) -> dict[str, Any]:
    s: Settings = app.state.settings
    return paper.today(
        s.runs_dir,
        s.live_state_dir,
        run_dir=paper.newest_run(s.runs_dir),
        runner_cfg=_runner_cfg(app),
    )


@router.get("/paper/plugins")
async def paper_plugins(request: Request) -> dict[str, Any]:
    t = await asyncio.to_thread(_paper_today, request.app)
    return {k: t[k] for k in ("as_of_ns", "day", "plugins", "total", "skipped_lines")}


@router.get("/paper/accounts")
async def paper_accounts(request: Request) -> dict[str, Any]:
    t = await asyncio.to_thread(_paper_today, request.app)
    return {"accounts": t["accounts"]}


async def _history(
    request: Request, scope: paper.Scope, what: str, start: str | None, end: str | None, limit: int
) -> dict[str, Any]:
    frm, to = _range(start, end)
    s = _s(request)

    def run() -> dict[str, Any]:
        if scope.kind == "plugin" and scope.id is not None:
            known = paper.plugin_known(s.runs_dir, s.live_state_dir, scope.id)
        elif scope.kind != "total":
            account = f"{paper.IMPLICIT_PREFIX}{scope.id}" if scope.kind == "implicit" else scope.id
            known = account is not None and paper.account_known(
                s.runs_dir, s.live_state_dir, account, _runner_cfg(request.app)
            )
        else:
            known = True
        if not known:
            raise HTTPException(status_code=404, detail=f"no {scope.kind} {scope.id!r}")
        if what == "trades":
            return paper.history_trades(s.runs_dir, scope, frm, to, limit)
        if what == "equity":
            return paper.history_equity(s.runs_dir, scope, frm, to)
        return paper.history_metrics(s.runs_dir, scope, frm, to)

    return await asyncio.to_thread(run)


@router.get("/paper/plugins/{plugin_id}/trades")
async def paper_plugin_trades(
    request: Request, plugin_id: str, from_: _From = None, to: _To = None, limit: _Limit = 500
) -> dict[str, Any]:
    scope = paper.Scope("plugin", _plugin_id(plugin_id))
    return await _history(request, scope, "trades", from_, to, limit)


@router.get("/paper/plugins/{plugin_id}/equity")
async def paper_plugin_equity(
    request: Request, plugin_id: str, from_: _From = None, to: _To = None
) -> dict[str, Any]:
    scope = paper.Scope("plugin", _plugin_id(plugin_id))
    return await _history(request, scope, "equity", from_, to, 1)


@router.get("/paper/plugins/{plugin_id}/metrics")
async def paper_plugin_metrics(
    request: Request, plugin_id: str, from_: _From = None, to: _To = None
) -> dict[str, Any]:
    scope = paper.Scope("plugin", _plugin_id(plugin_id))
    return await _history(request, scope, "metrics", from_, to, 1)


@router.get("/paper/accounts/{account_id}/trades")
async def paper_account_trades(
    request: Request, account_id: str, from_: _From = None, to: _To = None, limit: _Limit = 500
) -> dict[str, Any]:
    scope = paper.Scope.account(_account_id(account_id))
    return await _history(request, scope, "trades", from_, to, limit)


@router.get("/paper/accounts/{account_id}/equity")
async def paper_account_equity(
    request: Request, account_id: str, from_: _From = None, to: _To = None
) -> dict[str, Any]:
    scope = paper.Scope.account(_account_id(account_id))
    return await _history(request, scope, "equity", from_, to, 1)


@router.get("/paper/accounts/{account_id}/metrics")
async def paper_account_metrics(
    request: Request, account_id: str, from_: _From = None, to: _To = None
) -> dict[str, Any]:
    scope = paper.Scope.account(_account_id(account_id))
    return await _history(request, scope, "metrics", from_, to, 1)


@router.get("/paper/total/trades")
async def paper_total_trades(
    request: Request, from_: _From = None, to: _To = None, limit: _Limit = 500
) -> dict[str, Any]:
    return await _history(request, paper.Scope("total"), "trades", from_, to, limit)


@router.get("/paper/total/equity")
async def paper_total_equity(
    request: Request, from_: _From = None, to: _To = None
) -> dict[str, Any]:
    return await _history(request, paper.Scope("total"), "equity", from_, to, 1)


@router.get("/paper/total/metrics")
async def paper_total_metrics(
    request: Request, from_: _From = None, to: _To = None
) -> dict[str, Any]:
    return await _history(request, paper.Scope("total"), "metrics", from_, to, 1)


class ResetIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


def _lock_held(live_state: Path) -> bool:
    """Probe the runner lock: take it and release it at once (non-blocking flock)."""
    from fse.live.host.lock import RunnerLock, RunnerLockHeld

    lock = RunnerLock(live_state)
    try:
        lock.acquire()
    except RunnerLockHeld:
        return True
    lock.release()
    return False


@router.post("/paper/accounts/{account_id}/reset")
async def reset_account(request: Request, account_id: str, body: ResetIn) -> dict[str, Any]:
    if not paper.PLUGIN_ID_RE.fullmatch(account_id):
        raise HTTPException(status_code=422, detail=f"invalid account id {account_id!r}")
    s = _s(request)
    if not (s.live_state_dir / "accounts" / account_id).is_dir():
        raise HTTPException(status_code=404, detail=f"no account {account_id!r}")
    state = (await _runner(request).status()).get("state")
    if state in ("running", "starting"):
        raise HTTPException(status_code=409, detail="Stop the runner first")
    if await asyncio.to_thread(_lock_held, s.live_state_dir):
        raise HTTPException(status_code=409, detail="Stop the runner first")
    audit.note(request, account=account_id, reason=body.reason)
    argv = account_reset_argv(s.fse_bin, s.live_state_dir, account_id, body.reason)
    result = await _quick(request, argv, ok_codes=(0, 3))
    if result["exit_code"] == 3:  # the lock was taken between the probe and the CLI
        raise HTTPException(status_code=409, detail=result)
    return result


@router.get("/live/cards")
async def live_cards_route(
    request: Request, plugin: str | None = None, limit: int = 300
) -> dict[str, Any]:
    """The Cards page: every plugin's cards (newest first), filtered by ``plugin``.

    Plugins whose ``cards`` switch is off in the runner config the console would
    start are hidden at once; the runner stops writing them at its next restart.
    """
    if plugin is not None and not PLUGIN_ID_RE.fullmatch(plugin):
        raise HTTPException(status_code=422, detail="invalid plugin id")
    s = _s(request)

    def read() -> dict[str, Any]:
        name, publish = card_publish(request.app.state.db, _store(request))
        return live_cards(
            s.runs_dir,
            publish=publish,
            plugin=plugin,
            limit=max(1, min(limit, 1000)),
            runner_config=name,
        )

    return await asyncio.to_thread(read)


@router.get("/live/plugins/{plugin_id}/raw")
async def live_plugin_raw(request: Request, plugin_id: str) -> dict[str, Any]:
    """The plugin's raw saved state (book and plugin halves): the Live page's Details."""
    found = await asyncio.to_thread(plugin_raw, _s(request).live_state_dir, plugin_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such plugin state")
    return found


# ---------------------------------------------------------------- secrets and system


@router.get("/secrets")
async def list_secrets(request: Request) -> dict[str, Any]:
    secrets: SecretStore = request.app.state.secrets
    statuses = await asyncio.to_thread(secrets.statuses)
    return {
        "store": secrets.kind,
        "secrets": [{"name": n, "status": statuses.get(n, "missing")} for n in SECRET_NAMES],
    }


class SecretIn(BaseModel):
    value: str = Field(max_length=4096, repr=False)


@router.put("/secrets/{name}")
async def put_secret(request: Request, name: str, body: SecretIn) -> dict[str, Any]:
    audit.note(request, name=name)  # never the value
    secrets: SecretStore = request.app.state.secrets
    try:
        await asyncio.to_thread(secrets.put, name, body.value)
    except SecretError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"name": name, "status": "set"}


@router.get("/system")
async def system_info(request: Request) -> dict[str, Any]:
    s = _s(request)
    info = await asyncio.to_thread(
        system.system_info,
        data_dir=s.data_dir,
        cache_dir=s.cache_dir,
        live_state=s.live_state_dir,
        image_tag=s.image_tag,
        env=s.env,
    )
    info["executor"] = s.executor
    if s.executor == "ecs":
        info["aws"] = {"bucket": s.data_bucket, "cluster": s.ecs_cluster, "region": s.aws_region}
    return info


@router.get("/health/daily")
async def daily(request: Request) -> dict[str, Any]:
    s = _s(request)
    reader = request.app.state.alarms  # CloudWatch alarms: AWS only
    return {
        "reports": system.daily_reports(s.data_dir / "daily"),
        "alarms": await reader.read() if reader is not None else None,
    }


@router.get("/health/daily/{day}")
async def daily_one(request: Request, day: str) -> dict[str, Any]:
    found = system.daily_report(_s(request).data_dir / "daily", day)
    if found is None:
        raise HTTPException(status_code=404, detail="no daily check for that day")
    return found


# ---------------------------------------------------------------- WebSockets


@ws_router.websocket("/api/ws/jobs/{job_id}")
async def ws_job(ws: WebSocket, job_id: str) -> None:
    if websocket_principal(ws) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    jobs = _jobs(ws)
    job = jobs.get(job_id)
    if job is None:
        await ws.close(code=4404)
        return
    queue = jobs.subscribe(job_id)
    try:
        await ws.send_json({"type": "status", "job": job})
        for line in jobs.log_tail(job_id):
            await ws.send_json({"type": "line", "line": line})
        if queue is None:
            await ws.send_json({"type": "end"})
            return
        while True:
            event = await queue.get()
            await ws.send_json(event)
            if event["type"] == "end":
                return
    except WebSocketDisconnect:
        return
    finally:
        if queue is not None:
            jobs.unsubscribe(job_id, queue)
        with contextlib.suppress(Exception):
            await ws.close()


@ws_router.websocket("/api/ws/live")
async def ws_live(ws: WebSocket) -> None:
    if websocket_principal(ws) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    last = ""
    runner: dict[str, Any] | None = None
    runner_at = 0.0
    try:
        while True:
            # Every second for the live P&L; the runner status (CloudWatch reads on
            # AWS) only every RUNNER_REFRESH_S.
            snap = await asyncio.to_thread(_snapshot, ws.app)
            if runner is None or time.monotonic() - runner_at >= RUNNER_REFRESH_S:
                runner = await ws.app.state.runner.status()
                runner_at = time.monotonic()
            snap["runner"] = runner
            digest = live_digest(snap)
            if digest != last:
                await ws.send_json(snap)
                last = digest
            await asyncio.sleep(LIVE_INTERVAL_S)
    except WebSocketDisconnect, RuntimeError:
        return
