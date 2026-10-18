"""Strategy cards: a post, a sealed spec, a backtest job, and a Rule 9.1 grade.

The backtest is ``fse backtest-plugin`` on a generated runner config. The judge
does not read the market cache; it reads outcomes the run wrote, or trades the
caller posts.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import secrets
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, assert_never

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse, Response
from fse.analytics.card_judge import (
    BaselineObservation,
    CheckResult,
    JudgeReport,
    JudgeThresholds,
    Observation,
    judge,
)
from fse.engine.sizing import POINT_VALUE_USD
from fse.live.host.config import RunnerConfigError, load_runner_config
from fse.logio.canonical_json import dumps as canonical_dumps
from fse.strategy.spec import (
    EXAMPLE_SPEC,
    StrategySpec,
    ValidationReport,
    describe_rules,
    limitations,
    spec_hash,
    validate_document,
)
from fse.strategy.warmup import history_sessions
from pydantic import BaseModel, ConfigDict, Field

from fse_ui import audit
from fse_ui.auth import Principal
from fse_ui.aws.errors import AWS_ERRORS
from fse_ui.bar_gaps import (
    catalog_local,
    catalog_s3,
    clamp_warning,
    gap_detail,
    local_sessions,
    missing_ranges,
    s3_sessions,
    session_days,
    sessions_before,
    warmup_detail,
)
from fse_ui.catalog import ArgvError, Catalog, build_args, full_argv
from fse_ui.db import Db
from fse_ui.jobs import JobManager, JobRequest, log_line_limit
from fse_ui.runs import MAX_READ_BYTES, RUN_RE, ArtifactStore, RunError
from fse_ui.settings import Settings

router = APIRouter()

PLUGIN_ID = "rules"
PLUGIN_MODULE = "fse.live.plugins.rules:RulesPlugin"
_CARD_ID = r"^[A-Za-z0-9_-]{1,80}$"
_HASH = r"^[0-9a-f]{64}$"


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)
    author: str = Field(min_length=1, max_length=80)
    post_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=8000)


class DesignIn(BaseModel):
    """Sealed with the card. Later edits are a new card."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=12)
    timeframe: Literal["1m", "2m", "3m", "5m", "15m", "30m", "60m", "1d"]
    design_range: tuple[date, date]
    holdout_range: tuple[date, date] | None = None
    judged_at: str = Field(default="2R", pattern=r"^[0-9]+(\.[0-9]+)?R$")
    k: int = Field(default=2, ge=1, le=100)


class CardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceIn
    spec: dict[str, Any]
    hypothesis: str = Field(min_length=1, max_length=2000)
    design: DesignIn


class SpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]


class CardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: float
    name: str
    author: str
    post_id: str
    hypothesis: str
    spec_hash: str
    design_hash: str
    grade: Literal["PASS", "FAIL", "DRAFT"]
    symbols: list[str]
    timeframe: str


class CardList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[CardSummary]


class CardCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    spec_hash: str
    design_hash: str
    duplicates: list[str]
    validation: ValidationReport


class SourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    author: str
    post_id: str
    text: str


class ProbOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    pct: float | None


class EquityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: int | None = None
    net: str | None = None
    expectancy_r: str | None = None


class CardView(BaseModel):
    """What the Strategy card and the HTML render show."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    hypothesis: str
    grade: Literal["PASS", "FAIL", "DRAFT"]
    source: SourceOut
    rules: list[str]
    probabilities: list[ProbOut]
    judge: list[CheckResult]
    equity: EquityOut
    side: str
    symbols: list[str]
    timeframe: str
    spec_hash: str
    design_hash: str
    created_at: float
    job_id: str | None = None
    run_dir: str | None = None


class PhaseJob(BaseModel):
    """One backtest phase. ``none`` means that phase has not been launched."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["design", "holdout"]
    status: Literal["none", "queued", "running", "failed", "done"]
    job_id: str | None = None
    error: str | None = None
    run_dir: str | None = None


class CardRecord(CardView):
    spec: dict[str, Any]
    design: DesignIn
    backtests: list[PhaseJob]


class BacktestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    which: Literal["design", "holdout"] = "design"
    allow_gaps: bool = False
    history: int | None = Field(
        default=None,
        ge=0,
        le=1200,
        description="1m sessions of warmup before the range. Omit to size it from the spec.",
    )
    lag_s: int = Field(
        default=2,
        ge=0,
        le=120,
        description="Receipt lag for a cached bar. The replay clock jumps; it does not sleep.",
    )
    cache_dir: str | None = None


class JudgeIn(BaseModel):
    """Trades and a clock baseline. Omit both to judge the last backtest's outcomes.

    An omitted baseline fails the lift check. This route does not invent one.
    """

    model_config = ConfigDict(extra="forbid")

    trades: list[Observation] | None = None
    baseline: list[BaselineObservation] | None = None
    outside_symbols: list[str] = Field(default_factory=list)
    outside_card_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Other cards whose outcomes are the outside group.",
    )
    thresholds: JudgeThresholds | None = None


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    report: JudgeReport


def _db(request: Request) -> Db:
    db: Db = request.app.state.db
    return db


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _who(request: Request) -> Principal:
    who: Principal = request.state.principal
    return who


def _jobs(request: Request) -> JobManager:
    jobs: JobManager = request.app.state.jobs
    return jobs


def _design_hash(design: DesignIn) -> str:
    payload = canonical_dumps(design.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _check_design(spec: StrategySpec, design: DesignIn) -> list[str]:
    errors: list[str] = []
    if design.timeframe != spec.timeframe.bar:
        errors.append("design.timeframe does not match spec.timeframe.bar")
    for symbol in design.symbols:
        if symbol not in POINT_VALUE_USD:
            errors.append(f"design.symbols: {symbol} has no point value")
    if spec.symbols[0] not in design.symbols:
        errors.append("design.symbols must include the spec symbol")
    if design.design_range[0] > design.design_range[1]:
        errors.append("design.design_range is backwards")
    holdout = design.holdout_range
    if holdout is not None and holdout[0] > holdout[1]:
        errors.append("design.holdout_range is backwards")
    return errors


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _since(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="since must be unix seconds or an ISO time"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _load(row: Any) -> tuple[StrategySpec, DesignIn]:
    spec = StrategySpec.model_validate(json.loads(row["spec_json"]))
    design = DesignIn.model_validate(json.loads(row["design_json"]))
    return spec, design


def _one(db: Db, card_id: str) -> Any:
    row = db.one("SELECT * FROM strategy_cards WHERE id = ?", (card_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="no strategy card with that id")
    return row


def _under_data(settings: Settings, raw: str) -> Path:
    root = settings.data_dir.resolve()
    path = Path(raw).expanduser().resolve()
    if path != root and root not in path.parents:
        raise HTTPException(
            status_code=422, detail="path must stay under the console data directory"
        )
    return path


def _probs(outcomes: list[dict[str, Any]]) -> list[ProbOut]:
    n = len(outcomes)
    keys = (("1R", "hit_1"), ("2R", "hit_2"), ("3R", "hit_3"), ("4R+", "hit_4"))
    if n == 0:
        return [ProbOut(label=label, pct=None) for label, _key in keys]
    return [
        ProbOut(label=label, pct=round(100 * sum(1 for row in outcomes if row.get(key)) / n, 1))
        for label, key in keys
    ]


def _runs(request: Request) -> ArtifactStore:
    store: ArtifactStore = request.app.state.runs.store
    return store


def _read_text(run_dir: str | None, name: str, store: ArtifactStore | None) -> str | None:
    """The local file when the run folder is on disk, else the same path in the store."""
    if not run_dir:
        return None
    path = Path(run_dir) / name
    try:
        if path.is_file() and path.stat().st_size <= MAX_READ_BYTES:
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    if store is None:
        return None
    run = Path(run_dir).name
    if not RUN_RE.fullmatch(run):
        return None
    try:
        if not store.exists(run, name):
            return None
        return store.read_bytes(run, name).decode("utf-8")
    except RunError, OSError, UnicodeError:
        return None


def _read_outcomes(run_dir: str | None, store: ArtifactStore | None = None) -> list[dict[str, Any]]:
    text = _read_text(run_dir, f"run/plugins/{PLUGIN_ID}/outcomes.jsonl", store)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _equity(run_dir: str | None, store: ArtifactStore | None = None) -> EquityOut:
    if not run_dir:
        return EquityOut()
    text = _read_text(run_dir, "trades.json", store)
    if text is None:
        return EquityOut()
    payload = json.loads(text)
    if not isinstance(payload, list) or not payload:
        return EquityOut(trades=0, net="0", expectancy_r=None)
    nets = [Decimal(str(item.get("net", "0"))) for item in payload if isinstance(item, dict)]
    rs = [
        Decimal(str(item.get("r_multiple", item.get("r", "0"))))
        for item in payload
        if isinstance(item, dict)
    ]
    expectancy = None if not rs else str(sum(rs, Decimal(0)) / Decimal(len(rs)))
    return EquityOut(trades=len(nets), net=str(sum(nets, Decimal(0))), expectancy_r=expectancy)


def _judge_checks(raw: str | None) -> list[CheckResult]:
    if not raw:
        return []
    report = JudgeReport.model_validate(json.loads(raw))
    return list(report.checks)


def _map_status(raw: str) -> Literal["queued", "running", "failed", "done"]:
    if raw == "queued":
        return "queued"
    if raw == "running":
        return "running"
    if raw == "succeeded":
        return "done"
    return "failed"


def _log_text(line: str) -> str:
    text = line.strip()
    if text.startswith("fse-entrypoint:"):
        text = text.removeprefix("fse-entrypoint:").strip()
    return text


def _stderr_tail(lines: list[str]) -> str:
    """The last few log lines that are not the argv prompt or the exit summary."""
    kept: list[str] = []
    for line in lines:
        text = _log_text(line)
        if not text or text.startswith("$ ") or text.startswith("exit "):
            continue
        kept.append(text)
    return " | ".join(kept[-4:])


def _phase_error(jobs: JobManager, job: dict[str, Any]) -> str | None:
    if job["status"] in {"queued", "running", "succeeded"}:
        return None
    try:
        lines = jobs.log_tail(str(job["id"]), lines=40)
    except KeyError:
        lines = []
    for line in reversed(lines):
        text = _log_text(line)
        if text.startswith(("error:", "plugin ", "mkdir:")):
            return text[:500]
    code = job.get("exit_code")
    if code is not None:
        meaning = job.get("exit_meaning") or job["status"]
        tail = _stderr_tail(lines)
        shown = f"exit {code}: {meaning}" if not tail else f"exit {code}: {meaning}: {tail}"
        return shown[:500]
    return str(job["status"])


def _one_phase(
    phase: Literal["design", "holdout"],
    job_id: str | None,
    run_dir: str | None,
    jobs: JobManager,
) -> PhaseJob:
    if not job_id:
        return PhaseJob(phase=phase, status="none", run_dir=run_dir)
    found = jobs.get(str(job_id))
    if found is None:
        return PhaseJob(
            phase=phase,
            status="failed",
            job_id=str(job_id),
            error="job not found",
            run_dir=run_dir,
        )
    return PhaseJob(
        phase=phase,
        status=_map_status(str(found["status"])),
        job_id=str(job_id),
        error=_phase_error(jobs, found),
        run_dir=run_dir or found.get("run_dir"),
    )


def _backtests(row: Any, jobs: JobManager) -> list[PhaseJob]:
    design_id = row["design_job_id"]
    holdout_id = row["holdout_job_id"]
    design_dir = row["design_run_dir"]
    holdout_dir = row["holdout_run_dir"]
    legacy = row["job_id"]
    if design_id is None and holdout_id is None and legacy:
        found = jobs.get(str(legacy))
        title = "" if found is None else str(found["title"])
        if title.endswith("holdout"):
            holdout_id = legacy
            holdout_dir = holdout_dir or row["run_dir"]
        else:
            design_id = legacy
            design_dir = design_dir or row["run_dir"]
    return [
        _one_phase("design", design_id, design_dir, jobs),
        _one_phase("holdout", holdout_id, holdout_dir, jobs),
    ]


def _view(row: Any, store: ArtifactStore | None = None) -> CardView:
    spec, design = _load(row)
    grade = row["grade"]
    if grade not in {"PASS", "FAIL", "DRAFT"}:
        grade = "DRAFT"
    return CardView(
        id=row["id"],
        name=row["name"],
        hypothesis=row["hypothesis"],
        grade=grade,
        source=SourceOut(
            url=row["source_url"],
            author=row["author"],
            post_id=row["post_id"],
            text=row["source_text"],
        ),
        rules=describe_rules(spec),
        probabilities=_probs(_read_outcomes(row["run_dir"], store)),
        judge=_judge_checks(row["judge_json"]),
        equity=_equity(row["run_dir"], store),
        side=spec.side,
        symbols=list(design.symbols),
        timeframe=design.timeframe,
        spec_hash=row["spec_hash"],
        design_hash=row["design_hash"],
        created_at=float(row["created_at"]),
        job_id=row["job_id"],
        run_dir=row["run_dir"],
    )


def _summary(row: Any) -> CardSummary:
    _spec, design = _load(row)
    grade = row["grade"] if row["grade"] in {"PASS", "FAIL", "DRAFT"} else "DRAFT"
    return CardSummary(
        id=row["id"],
        created_at=float(row["created_at"]),
        name=row["name"],
        author=row["author"],
        post_id=row["post_id"],
        hypothesis=row["hypothesis"],
        spec_hash=row["spec_hash"],
        design_hash=row["design_hash"],
        grade=grade,
        symbols=list(design.symbols),
        timeframe=design.timeframe,
    )


def _runner(spec: StrategySpec) -> dict[str, Any]:
    legs = max(1, len(spec.scale_outs)) + len(spec.add_backs)
    return {
        "version": 1,
        "data_plane": {"instruments": list(spec.symbols), "skylit": {"enabled": False}},
        "plugins": [
            {
                "id": PLUGIN_ID,
                "module": PLUGIN_MODULE,
                "enabled": True,
                "config": spec.model_dump(mode="json"),
                "fills": {
                    "trade_through_ticks": 0,
                    "slippage_ticks": 0,
                    "stop_fill": "at_stop",
                    "commission_rt": {spec.symbols[0]: "0.00"},
                },
                "risk": {
                    "max_contracts": min(100, max(spec.sizing.max_contracts, 1)),
                    "max_open_trades": min(10, legs),
                },
                "hold_through_break": False,
            }
        ],
    }


def _range(design: DesignIn, which: Literal["design", "holdout"]) -> tuple[date, date]:
    if which == "holdout":
        if design.holdout_range is None:
            raise HTTPException(status_code=422, detail="this card has no holdout_range")
        return design.holdout_range
    return design.design_range


def _outcomes_as_trades(rows: list[dict[str, Any]], design_symbols: set[str]) -> list[Observation]:
    trades: list[Observation] = []
    for row in rows:
        symbol = str(row["symbol"])
        group: Literal["design", "outside"] = "design" if symbol in design_symbols else "outside"
        trades.append(
            Observation(
                symbol=symbol,
                day=date.fromisoformat(str(row["day"])),
                clock=str(row["clock"]),
                side=row["side"],
                mfe_r=Decimal(str(row["mfe_r"])),
                group=group,
            )
        )
    return trades


def _html(view: CardView) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value))

    rules = "".join(f"<li>{esc(line)}</li>" for line in view.rules)
    probs = "".join(
        f"<li>{esc(prob.label)}: {esc('—' if prob.pct is None else f'{prob.pct}%')}</li>"
        for prob in view.probabilities
    )
    checks = "".join(
        f"<tr><td>{esc('pass' if check.passed else 'fail')}</td>"
        f"<td>{esc(check.name)}</td><td>{esc(check.detail)}</td></tr>"
        for check in view.judge
    )
    equity = view.equity
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(view.name)}</title>
<style>
body{{font:14px/1.45 ui-monospace,monospace;background:#10140f;color:#e7efe4;margin:24px}}
.grade{{font-weight:700;letter-spacing:.08em}}
.PASS{{color:#8fd18a}}.FAIL{{color:#e07a72}}.DRAFT{{color:#c7c1a8}}
article{{max-width:760px}} h1{{font-size:20px}} table{{border-collapse:collapse;width:100%}}
td{{border-top:1px solid #2a3328;padding:6px 8px;vertical-align:top}}
</style></head><body><article>
<p class="grade {esc(view.grade)}">{esc(view.grade)}</p>
<h1>{esc(view.name)}</h1>
<p>{esc(view.hypothesis)}</p>
<p>Source: {esc(view.source.author)} · {esc(view.source.post_id)}<br>{esc(view.source.text)}</p>
<p><a href="{esc(view.source.url)}">{esc(view.source.url)}</a></p>
<h2>Rules</h2><ul>{rules}</ul>
<h2>P(R before stop)</h2><ul>{probs}</ul>
<h2>Judge</h2><table>{checks}</table>
<p>Trades {esc(equity.trades)} · net {esc(equity.net)} · expectancy R {esc(equity.expectancy_r)}</p>
<p>spec {esc(view.spec_hash)} · design {esc(view.design_hash)}</p>
<p>Paper research. A grade is not a profit claim.</p>
</article></body></html>
"""


def _markdown(view: CardView) -> str:
    lines = [
        f"# {view.name}",
        "",
        f"**{view.grade}**",
        "",
        view.hypothesis,
        "",
        f"Source: {view.source.author} `{view.source.post_id}`",
        "",
        view.source.text,
        "",
        view.source.url,
        "",
        "## Rules",
        "",
    ]
    lines.extend(f"- {line}" for line in view.rules)
    lines.extend(["", "## P(R before stop)", ""])
    for prob in view.probabilities:
        shown = "—" if prob.pct is None else f"{prob.pct}%"
        lines.append(f"- {prob.label}: {shown}")
    lines.extend(["", "## Judge", ""])
    if not view.judge:
        lines.append("Not judged.")
    for check in view.judge:
        mark = "pass" if check.passed else "fail"
        lines.append(f"- {mark} `{check.name}`: {check.detail}")
    equity = view.equity
    lines.extend(
        [
            "",
            "## Equity",
            "",
            f"- trades: {equity.trades}",
            f"- net: {equity.net}",
            f"- expectancy R: {equity.expectancy_r}",
            "",
            f"spec `{view.spec_hash}`",
            f"design `{view.design_hash}`",
            "",
            "Paper research. A grade is not a profit claim.",
            "",
        ]
    )
    return "\n".join(lines)


@router.get("/schemas/card")
async def card_schema() -> dict[str, Any]:
    """The card contract: spec schema, request schema, judge schema, and the example."""
    return {
        "spec": StrategySpec.model_json_schema(),
        "card": CardIn.model_json_schema(),
        "judge": JudgeReport.model_json_schema(),
        "limitations": limitations(),
        "example": EXAMPLE_SPEC,
    }


@router.post("/cards/validate", response_model=ValidationReport)
async def validate_card(body: SpecIn) -> ValidationReport:
    """Validate a spec without storing it. Unsupported rules are listed, not dropped."""
    return validate_document(body.spec)


@router.post("/cards", response_model=CardCreated)
async def create_card(body: CardIn, request: Request) -> CardCreated:
    """Store a card and seal the design hash. The same spec may exist more than once."""
    report = validate_document(body.spec)
    if not report.ok or report.spec_hash is None:
        raise HTTPException(status_code=422, detail=report.model_dump(mode="json"))
    spec = StrategySpec.model_validate(body.spec)
    sealed = spec_hash(spec)
    problems = _check_design(spec, body.design)
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    digest = _design_hash(body.design)
    db = _db(request)
    duplicates = [
        str(row["id"])
        for row in db.all("SELECT id FROM strategy_cards WHERE spec_hash = ?", (sealed,))
    ]
    card_id = "c" + secrets.token_hex(8)
    now = time.time()
    db.execute(
        "INSERT INTO strategy_cards ("
        "id, created_at, author, post_id, source_url, source_text, hypothesis, "
        "spec_json, spec_hash, design_json, design_hash, name, grade"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT')",
        (
            card_id,
            now,
            body.source.author,
            body.source.post_id,
            body.source.url,
            body.source.text,
            body.hypothesis,
            json.dumps(spec.model_dump(mode="json")),
            sealed,
            json.dumps(body.design.model_dump(mode="json")),
            digest,
            spec.name,
        ),
    )
    audit.note(request, card=card_id, spec_hash=sealed, design_hash=digest)
    return CardCreated(
        id=card_id,
        spec_hash=sealed,
        design_hash=digest,
        duplicates=duplicates,
        validation=validate_document(spec.model_dump(mode="json")),
    )


@router.get("/cards", response_model=CardList)
async def list_cards(
    request: Request,
    q: Annotated[str | None, Query(max_length=200)] = None,
    hash: Annotated[str | None, Query(pattern=_HASH)] = None,  # noqa: A002
    since: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CardList:
    """Newest first. ``hash`` is the normalized spec hash. ``since`` is unix or ISO."""
    where: list[str] = []
    params: list[object] = []
    if hash:
        where.append("spec_hash = ?")
        params.append(hash)
    if since:
        stamp = _since(since)
        where.append("(created_at >= ? OR (judged_at IS NOT NULL AND judged_at >= ?))")
        params.extend([stamp, stamp])
    if q:
        like = _like(q)
        where.append(
            "(name LIKE ? ESCAPE '\\' OR hypothesis LIKE ? ESCAPE '\\' "
            "OR author LIKE ? ESCAPE '\\' OR source_text LIKE ? ESCAPE '\\' "
            "OR post_id LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like, like])
    sql = (
        "SELECT id, created_at, name, author, post_id, hypothesis, spec_hash, "
        "design_hash, grade, spec_json, design_json FROM strategy_cards"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = _db(request).all(sql, params)
    return CardList(cards=[_summary(row) for row in rows])


@router.get("/cards/{card_id}", response_model=CardRecord)
async def get_card(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)], request: Request
) -> CardRecord:
    row = _one(_db(request), card_id)
    spec, design = _load(row)
    view = _view(row, _runs(request))
    return CardRecord(
        **view.model_dump(),
        spec=spec.model_dump(mode="json"),
        design=design,
        backtests=_backtests(row, _jobs(request)),
    )


def _refuse(
    request: Request,
    card_id: str,
    which: Literal["design", "holdout"],
    detail: Any,
) -> NoReturn:
    """Record a failed phase job for this 422, then raise it.

    The previous run directory stays, so an older backtest can still be opened.
    The phase status follows this refusal instead of that older failure.
    """
    if isinstance(detail, dict):
        raw = detail.get("message", detail)
        message = raw if isinstance(raw, str) else str(raw)
    else:
        message = str(detail)
    job = _jobs(request).record_rejection(
        program="fse",
        command=("backtest-plugin",),
        argv=(),
        placement="batch",
        title=f"card {card_id} {which}",
        user=_who(request).email,
        message=message,
    )
    if which == "design":
        _db(request).execute(
            "UPDATE strategy_cards SET job_id = ?, design_job_id = ? WHERE id = ?",
            (job["id"], job["id"], card_id),
        )
    elif which == "holdout":
        _db(request).execute(
            "UPDATE strategy_cards SET job_id = ?, holdout_job_id = ? WHERE id = ?",
            (job["id"], job["id"], card_id),
        )
    else:
        assert_never(which)
    raise HTTPException(status_code=422, detail=detail)


async def _symbol_bars(
    request: Request,
    symbol: str,
    executor: str,
    body: BacktestIn,
) -> tuple[set[date], bool]:
    settings = _settings(request)
    if executor == "ecs":
        aws = request.app.state.aws
        bucket = settings.data_bucket
        if aws is None or not bucket:
            raise HTTPException(
                status_code=409, detail="a data bucket is required to backtest on ECS"
            )
        try:
            present = await asyncio.to_thread(s3_sessions, aws.s3, bucket, symbol)
            catalog = await asyncio.to_thread(catalog_s3, aws.s3, bucket)
        except AWS_ERRORS as exc:
            raise HTTPException(
                status_code=422,
                detail=f"cannot list cache/bars/{symbol}/60s/: {type(exc).__name__}",
            ) from None
        return present, catalog
    cache = settings.cache_dir if body.cache_dir is None else _under_data(settings, body.cache_dir)
    return local_sessions(cache, symbol), catalog_local(cache)


async def _prepare_bars(
    request: Request,
    symbols: list[str],
    first: date,
    last: date,
    history: int,
    executor: str,
    body: BacktestIn,
) -> tuple[int, str | None]:
    """History to pass, and a warning when warmup was clamped to bars that exist.

    Every spec symbol is checked the same way. A missing design-window session
    or a missing catalog is a 422. The warmup object on that 422 says how many
    sessions before ``first`` exist; it is not why the request was refused.
    When every design window is complete and only warmup is short, history is
    the smallest count present and the backtest runs.
    """
    settings = _settings(request)
    sessions = await asyncio.to_thread(session_days, settings.project_dir, first, last)
    if not sessions:
        raise HTTPException(
            status_code=422,
            detail=f"no exchange session between {first.isoformat()} and {last.isoformat()}",
        )
    before = await asyncio.to_thread(sessions_before, settings.project_dir, first, history)
    notes: list[str] = []
    chosen = history
    for symbol in symbols:
        present, catalog = await _symbol_bars(request, symbol, executor, body)
        ranges = missing_ranges(sessions, present)
        if ranges or not catalog:
            raise HTTPException(
                status_code=422,
                detail=gap_detail(
                    symbol,
                    ranges,
                    catalog_missing=not catalog,
                    warmup=warmup_detail(history, before, present),
                ),
            )
        available = sum(day in present for day in before)
        if available < history:
            chosen = min(chosen, available)
            notes.append(clamp_warning(symbol, first, history, available))
    if notes:
        return chosen, " ".join(notes)
    return history, None


@router.post("/cards/{card_id}/backtest")
async def backtest_card(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)],
    request: Request,
    body: BacktestIn | None = None,
) -> dict[str, Any]:
    """Write a runner config and submit ``fse backtest-plugin``. Holdout only if asked."""
    body = body or BacktestIn()
    row = _one(_db(request), card_id)
    spec, design = _load(row)
    first, last = _range(design, body.which)
    settings = _settings(request)
    # Second resolution collides when the same card is submitted twice in one
    # UTC second: both tasks upload runs/card-<id>-<stamp>/ and the later sync
    # replaces the earlier run. Microseconds plus a nonce are unique before the
    # job id exists.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    nonce = secrets.token_hex(3)
    leaf = f"{card_id}-{stamp}-{nonce}"
    config_dir = settings.configs_dir / "cards"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{leaf}.yaml"
    config_path.write_text(yaml.safe_dump(_runner(spec), sort_keys=False), encoding="utf-8")
    try:
        load_runner_config(config_path)
    except RunnerConfigError as exc:
        _refuse(request, card_id, body.which, str(exc))
    out_dir = settings.runs_dir / f"card-{leaf}"
    cat: Catalog = request.app.state.catalog
    try:
        cmd = cat.get("fse backtest-plugin")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    executor = "ecs" if settings.executor == "ecs" and cmd.placement == "batch" else "local"
    history = history_sessions(spec) if body.history is None else body.history
    note: str | None = None
    if not body.allow_gaps:
        try:
            history, note = await _prepare_bars(
                request, list(spec.symbols), first, last, history, executor, body
            )
        except HTTPException as exc:
            if exc.status_code == 422:
                _refuse(request, card_id, body.which, exc.detail)
            raise
    values: dict[str, Any] = {
        "config": str(config_path),
        "plugin": [PLUGIN_ID],
        "first": first.isoformat(),
        "last": last.isoformat(),
        "out": str(out_dir),
        "history": history,
        "lag_s": body.lag_s,
        "allow_gaps": body.allow_gaps,
    }
    if executor == "ecs":
        if not settings.data_bucket:
            raise HTTPException(
                status_code=409, detail="a data bucket is required to backtest on ECS"
            )
        values["recordings_s3"] = f"s3://{settings.data_bucket}/"
    else:
        cache = (
            settings.cache_dir if body.cache_dir is None else _under_data(settings, body.cache_dir)
        )
        values["cache_dir"] = str(cache)
        values["recordings_root"] = str(settings.data_dir)
    try:
        args = build_args(cmd, values)
    except ArgvError as exc:
        _refuse(request, card_id, body.which, str(exc))
    argv = full_argv(
        cmd,
        args,
        fse_bin=settings.fse_bin,
        python_bin=settings.python_bin,
        module=cat.program(cmd.program).module,
    )
    audit.note(request, card=card_id, command="fse backtest-plugin", args=args)
    job = _jobs(request).submit(
        JobRequest(
            program=cmd.program,
            command=cmd.path,
            argv=tuple(argv),
            placement=cmd.placement,
            title=f"card {card_id} {body.which}",
            heavy=cmd.heavy,
            executor=executor,
            args=tuple(args),
            note=note,
        ),
        user=_who(request).email,
    )
    if body.which == "design":
        _db(request).execute(
            "UPDATE strategy_cards SET job_id = ?, run_dir = ?, "
            "design_job_id = ?, design_run_dir = ? WHERE id = ?",
            (job["id"], str(out_dir), job["id"], str(out_dir), card_id),
        )
    else:
        _db(request).execute(
            "UPDATE strategy_cards SET job_id = ?, run_dir = ?, "
            "holdout_job_id = ?, holdout_run_dir = ? WHERE id = ?",
            (job["id"], str(out_dir), job["id"], str(out_dir), card_id),
        )
    body_out: dict[str, Any] = {
        "card_id": card_id,
        "which": body.which,
        "config": str(config_path),
        "out_dir": str(out_dir),
        "job": job,
    }
    if note is not None:
        body_out["warmup_warning"] = note
    return body_out


@router.post("/cards/{card_id}/judge", response_model=JudgeOut)
async def judge_card(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)],
    request: Request,
    body: JudgeIn | None = None,
) -> JudgeOut:
    """Rule 9.1. Missing baseline fails that check; it is not treated as a pass."""
    body = body or JudgeIn()
    if card_id in body.outside_card_ids:
        raise HTTPException(status_code=422, detail="outside_card_ids cannot include this card")
    row = _one(_db(request), card_id)
    _spec, design = _load(row)
    design_symbols = list(design.symbols)
    store = _runs(request)
    if body.trades is not None:
        trades = list(body.trades)
    else:
        trades = _outcomes_as_trades(_read_outcomes(row["run_dir"], store), set(design_symbols))
    for other_id in body.outside_card_ids:
        other = _one(_db(request), other_id)
        twin = _outcomes_as_trades(_read_outcomes(other["run_dir"], store), set())
        trades.extend(item.model_copy(update={"group": "outside"}) for item in twin)
    baseline = [] if body.baseline is None else body.baseline
    if body.thresholds is None:
        thresholds = JudgeThresholds(judged_r=Decimal(design.judged_at[:-1]), k=design.k)
    else:
        thresholds = body.thresholds
    report = judge(
        trades,
        baseline,
        design_symbols=design_symbols,
        outside_symbols=body.outside_symbols,
        thresholds=thresholds,
    )
    now = time.time()
    _db(request).execute(
        "UPDATE strategy_cards SET judge_json = ?, grade = ?, judged_at = ? WHERE id = ?",
        (report.model_dump_json(), report.grade, now, card_id),
    )
    audit.note(request, card=card_id, grade=report.grade)
    return JudgeOut(id=card_id, report=report)


@router.get("/cards/{card_id}/render", response_model=None)
async def render_card(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)],
    request: Request,
    format: Literal["json", "html"] = "json",  # noqa: A002
) -> CardView | HTMLResponse:
    view = _view(_one(_db(request), card_id), _runs(request))
    if format == "html":
        return HTMLResponse(_html(view))
    return view


@router.get("/cards/{card_id}/report", response_model=None)
async def report_card(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)], request: Request
) -> Response:
    view = _view(_one(_db(request), card_id), _runs(request))
    return Response(_markdown(view), media_type="text/markdown")


def _latest_card_job(request: Request, card_id: str) -> dict[str, Any]:
    row = _one(_db(request), card_id)
    job_id = row["job_id"]
    if not job_id:
        raise HTTPException(status_code=404, detail="this card has no backtest job")
    job = _jobs(request).get(str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


@router.get("/cards/{card_id}/job")
async def card_job(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)], request: Request
) -> dict[str, Any]:
    """The card's latest backtest job. Same payload as ``GET /api/jobs/{id}``."""
    return _latest_card_job(request, card_id)


@router.get("/cards/{card_id}/job/log")
async def card_job_log(
    card_id: Annotated[str, ApiPath(pattern=_CARD_ID)],
    request: Request,
    lines: int | None = None,
    limit: int | None = None,
    tail: int | None = None,
) -> dict[str, Any]:
    """Same cap as ``GET /api/jobs/{id}/log``: ``lines``, ``limit`` or ``tail``."""
    job = _latest_card_job(request, card_id)
    return {
        "lines": _jobs(request).log_tail(str(job["id"]), lines=log_line_limit(lines, limit, tail))
    }
