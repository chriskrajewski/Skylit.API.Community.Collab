"""The versioned config store and engine-backed validation.

Layout under ``<data>/configs/<kind>/<name>/``:

- ``revisions/<sha>.yaml``: every saved text (``sha`` = first 12 hex of SHA-256)
- ``current.yaml``: a copy of the current revision (the path commands get)
- ``meta.json``: ``{"origin", "current", "revisions": [{"sha", "at", "author", "message"}]}``

Kinds: ``runner`` (RunnerConfig), ``strategy`` (Strategy_Config), ``sweep``
(``fse experiment sweep``), ``research`` (research package YAML, syntax only).
A save validates first; an invalid text is never stored.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from fse.config.loader import LoadErr, UniqueKeySafeLoader, load_text
from fse.live.host.config import RunnerConfigError, parse_runner_config

type Kind = Literal["runner", "strategy", "sweep", "research"]
KINDS: tuple[Kind, ...] = ("runner", "strategy", "sweep", "research")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_CONFIG_BYTES = 512 * 1024
BASELINE_STRATEGY = "playbook_baseline"

log = logging.getLogger(__name__)


class ConfigMirror(Protocol):
    """Receives every ``current.yaml`` after it is written (S3 on AWS)."""

    def put(self, path: Path) -> None: ...


class ConfigStoreError(ValueError):
    """Unknown kind, name or revision, or a bad request."""


class ConfigConflict(ConfigStoreError):
    """``expected_sha`` is not the current revision any more (HTTP 409)."""


class OrderModeRefused(ConfigStoreError):
    """A runner text changes a plugin's ``execution.order_mode`` outside Live → Order mode
    (HTTP 422)."""


COMBINE_REFUSED = "combine is not available in the console"
"""Kept for older callers; Live → Order mode now allows combine like practice."""
ORDER_MODE_ELSEWHERE = "change order_mode on Live → Order mode"


@dataclass(frozen=True, slots=True)
class Problem:
    key_path: str
    message: str
    line: int | None = None
    kind: str = "invalid"


@dataclass(slots=True)
class Validation:
    ok: bool
    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [asdict(p) for p in self.errors],
            "warnings": [asdict(p) for p in self.warnings],
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- YAML


@dataclass(slots=True)
class Parsed:
    data: object
    key_lines: dict[str, int]
    problems: list[Problem]


def parse_yaml(text: str) -> Parsed:
    """YAML with duplicate keys reported and key lines recorded (the engine's loader)."""
    loader = UniqueKeySafeLoader(text)
    try:
        data = loader.get_single_data()
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = None if mark is None else int(mark.line) + 1
        problem = getattr(exc, "problem", None) or str(exc)
        return Parsed(None, {}, [Problem("", f"invalid YAML: {problem}", line, "syntax_error")])
    finally:
        loader.dispose()
    problems = [
        Problem(
            d.key_path,
            f"{d.key_path} repeats an earlier key of the same mapping",
            d.line,
            "duplicate_key",
        )
        for d in loader.duplicates
    ]
    return Parsed(data, dict(loader.key_lines), problems)


def _line(path: str, lines: dict[str, int]) -> int | None:
    while path:
        if path in lines:
            return lines[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return None


def _split(problem: str) -> tuple[str, str]:
    head, sep, rest = problem.partition(": ")
    if not sep:
        return "", problem
    return ("" if head == "(top level)" else head), rest


# ---------------------------------------------------------------- validation


def validate(kind: Kind, text: str, *, baseline_text: str | None = None) -> Validation:
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        return Validation(False, [Problem("", f"the file is larger than {MAX_CONFIG_BYTES} bytes")])
    if kind == "strategy":
        result = load_text(text, source="config")
        if isinstance(result, LoadErr):
            return Validation(
                False,
                [Problem(e.key_path, _strategy_message(e), e.line, e.kind) for e in result.errors],
            )
        return Validation(
            True,
            warnings=[
                Problem(" / ".join(w.key_paths), w.message, None, w.kind) for w in result.warnings
            ],
        )
    parsed = parse_yaml(text)
    if parsed.problems:
        return Validation(False, parsed.problems)
    if not isinstance(parsed.data, dict):
        return Validation(False, [Problem("", "the document must be a mapping")])
    if kind == "runner":
        try:
            parse_runner_config(parsed.data, Path("runner.yaml"))
        except RunnerConfigError as exc:
            errors = []
            for p in exc.problems:
                key, message = _split(p)
                errors.append(Problem(key, message, _line(key, parsed.key_lines)))
            return Validation(False, errors)
        return Validation(True)
    if kind == "sweep":
        return _validate_sweep(parsed, baseline_text)
    return Validation(True)


def _strategy_message(e: Any) -> str:
    text = str(e.message)
    if e.value is not None:
        text += f" (configured {e.value})"
    if e.expected is not None:
        text += f"; expected {e.expected}"
    return text


def _validate_sweep(parsed: Parsed, baseline_text: str | None) -> Validation:
    from fse.experiments.sweep import SweepError, parse_sweep

    if baseline_text is None:
        return Validation(
            True, warnings=[Problem("", "no baseline Strategy_Config to check against")]
        )
    base = load_text(baseline_text, source="baseline")
    if isinstance(base, LoadErr):
        return Validation(True, warnings=[Problem("", "the baseline Strategy_Config is invalid")])
    try:
        parse_sweep(parsed.data, base.config)
    except SweepError as exc:
        problems = getattr(exc, "problems", None) or [str(exc)]
        return Validation(False, [Problem("", str(p)) for p in problems])
    return Validation(True)


# ---------------------------------------------------------------- order modes


def runner_order_modes(text: str | None) -> dict[str, tuple[int, str]]:
    """``{plugin id: (YAML index, execution.order_mode)}`` of a runner text.

    A missing ``execution`` or ``order_mode`` is ``paper``; ``None`` or an unparsable
    text has no entries (every plugin paper).
    """
    if text is None:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    entries = data.get("plugins") if isinstance(data, dict) else None
    out: dict[str, tuple[int, str]] = {}
    for i, entry in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        execution = entry.get("execution")
        mode = execution.get("order_mode", "paper") if isinstance(execution, dict) else "paper"
        out[entry["id"]] = (i, mode if isinstance(mode, str) else str(mode))
    return out


def order_mode_problem(
    current: str | None, new: str, *, allow_order_mode: bool = False
) -> Problem | None:
    """Why ``new`` may not replace ``current`` (``None``: all-paper baseline), else ``None``.

    Any ``execution.order_mode`` change (paper, practice or combine) is refused unless
    ``allow_order_mode`` (only the Live → Order mode endpoint passes it).
    """
    before = {pid: mode for pid, (_, mode) in runner_order_modes(current).items()}
    after = runner_order_modes(new)
    if allow_order_mode:
        return None
    for pid, (i, mode) in after.items():
        if mode != before.get(pid, "paper"):
            return Problem(f"plugins[{i}].execution.order_mode", ORDER_MODE_ELSEWHERE)
    for pid, mode in before.items():
        if pid not in after and mode != "paper":
            return Problem("plugins", ORDER_MODE_ELSEWHERE)
    return None


# ---------------------------------------------------------------- the store


def _seed_kind(rel: Path, text: str) -> Kind | None:
    if rel.parts[0] == "research":
        return "research"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if isinstance(data, dict) and "plugins" in data and "version" in data:
        return "runner"
    if isinstance(data, dict) and ("exit_modes" in data or "r_multiples" in data):
        return "sweep"
    return "strategy"


class ConfigStore:
    def __init__(self, root: Path, mirror: ConfigMirror | None = None) -> None:
        self._root = root
        self._mirror = mirror
        self._lock = threading.RLock()

    # ------------------------------------------------------------ paths

    def _dir(self, kind: str, name: str) -> Path:
        if kind not in KINDS:
            raise ConfigStoreError(f"unknown config kind {kind!r}")
        if not NAME_RE.fullmatch(name):
            raise ConfigStoreError(f"invalid config name {name!r}")
        return self._root / kind / name

    def current_path(self, kind: str, name: str) -> Path:
        """The file a command reads (``current.yaml``)."""
        path = self._dir(kind, name) / "current.yaml"
        if not path.exists():
            raise ConfigStoreError(f"no {kind} config {name!r}")
        return path

    def _meta(self, kind: str, name: str) -> dict[str, Any]:
        try:
            meta: dict[str, Any] = json.loads(
                (self._dir(kind, name) / "meta.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            raise ConfigStoreError(f"no {kind} config {name!r}") from None
        return meta

    # ------------------------------------------------------------ seed

    def seed(self, project_dir: Path) -> int:
        """Copy shipped configs in once (only names the store does not hold yet)."""
        added = 0
        sources = sorted(project_dir.glob("configs/*.yaml")) + sorted(
            project_dir.glob("research/*/*.yaml")
        )
        for src in sources:
            rel = src.relative_to(project_dir)
            try:
                text = src.read_text(encoding="utf-8")
            except OSError, UnicodeDecodeError:
                continue
            kind = _seed_kind(rel, text)
            if kind is None:
                continue
            name = src.stem if kind != "research" else f"{rel.parts[1]}-{src.stem}"
            if not NAME_RE.fullmatch(name) or (self._root / kind / name / "meta.json").exists():
                continue
            self._write_revision(
                kind, name, text, author="seed", message=f"from {rel}", origin=str(rel)
            )
            added += 1
        return added

    # ------------------------------------------------------------ reads

    def list_configs(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = []
        for k in KINDS if kind is None else (kind,):
            base = self._root / k
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if (d / "meta.json").exists():
                    meta = self._meta(k, d.name)
                    head = meta["revisions"][-1] if meta["revisions"] else {}
                    out.append(
                        {
                            "kind": k,
                            "name": d.name,
                            "origin": meta.get("origin"),
                            "current": meta["current"],
                            "revisions": len(meta["revisions"]),
                            "updated_at": head.get("at"),
                            "path": str(d / "current.yaml"),
                        }
                    )
        return out

    def get(self, kind: str, name: str, sha: str | None = None) -> dict[str, Any]:
        meta = self._meta(kind, name)
        sha = sha or meta["current"]
        return {
            "kind": kind,
            "name": name,
            "sha": sha,
            "current": meta["current"],
            "origin": meta.get("origin"),
            "text": self._text(kind, name, sha),
            "revisions": list(reversed(meta["revisions"])),
            "path": str(self._dir(kind, name) / "current.yaml"),
        }

    def _text(self, kind: str, name: str, sha: str) -> str:
        if not SHA_RE.fullmatch(sha):
            raise ConfigStoreError(f"invalid revision {sha!r}")
        try:
            return (self._dir(kind, name) / "revisions" / f"{sha}.yaml").read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigStoreError(f"no revision {sha} of {kind} config {name!r}") from None

    def diff(self, kind: str, name: str, a: str, b: str) -> str:
        left, right = self._text(kind, name, a), self._text(kind, name, b)
        return "".join(
            difflib.unified_diff(
                left.splitlines(keepends=True),
                right.splitlines(keepends=True),
                fromfile=f"{name}@{a}",
                tofile=f"{name}@{b}",
            )
        )

    def baseline_text(self) -> str | None:
        try:
            return str(self.get("strategy", BASELINE_STRATEGY)["text"])
        except ConfigStoreError:
            return None

    # ------------------------------------------------------------ writes

    def save(
        self,
        kind: str,
        name: str,
        text: str,
        *,
        author: str,
        message: str = "",
        create: bool = False,
        expected_sha: str | None = None,
        allow_order_mode: bool = False,
    ) -> tuple[Validation, dict[str, Any] | None]:
        """Validate, then store a new revision (no-op if the text is the current one).

        ``expected_sha``: compare-and-swap; :class:`ConfigConflict` unless it is still the
        current revision (checked, validated and written under one lock). A runner text
        may not set ``combine`` nor change any ``execution.order_mode`` unless
        ``allow_order_mode`` (Live → Order mode); a create compares with all-paper.
        """
        if expected_sha is not None and not SHA_RE.fullmatch(expected_sha):
            raise ConfigStoreError(f"invalid revision {expected_sha!r}")
        with self._lock:
            exists = (self._dir(kind, name) / "meta.json").exists()
            if not exists and not create:
                raise ConfigStoreError(f"no {kind} config {name!r}")
            if exists and create:
                raise ConfigStoreError(f"a {kind} config {name!r} exists already")
            current = self._meta(kind, name)["current"] if exists else None
            if expected_sha is not None and current != expected_sha:
                raise ConfigConflict(f"the {kind} config changed; reload")
            if not text.endswith("\n"):
                text += "\n"
            if kind == "runner":  # before the engine checks: combine has its own message
                before = None if current is None else self._text(kind, name, current)
                problem = order_mode_problem(before, text, allow_order_mode=allow_order_mode)
                if problem is not None:
                    return Validation(False, [problem]), None
            result = validate(kind, text, baseline_text=self.baseline_text())  # type: ignore[arg-type]
            if not result.ok:
                return result, None
            self._write_revision(kind, name, text, author=author, message=message)
            return result, self.get(kind, name)

    def restore(self, kind: str, name: str, sha: str, *, author: str) -> dict[str, Any]:
        with self._lock:
            text = self._text(kind, name, sha)
            if kind == "runner":
                current = self._meta(kind, name)["current"]
                problem = order_mode_problem(self._text(kind, name, current), text)
                if problem is not None:
                    raise OrderModeRefused(problem.message)
            self._write_revision(kind, name, text, author=author, message=f"restore {sha}")
            return self.get(kind, name)

    def _write_revision(
        self,
        kind: str,
        name: str,
        text: str,
        *,
        author: str,
        message: str,
        origin: str | None = None,
    ) -> str:
        with self._lock:
            d = self._dir(kind, name)
            sha = _sha(text)
            try:
                meta = self._meta(kind, name)
            except ConfigStoreError:
                meta = {"origin": origin, "current": None, "revisions": []}
            if meta["current"] == sha:
                return sha
            rev = d / "revisions" / f"{sha}.yaml"
            if not rev.exists():
                _atomic_write(rev, text)
            meta["revisions"].append(
                {"sha": sha, "at": time.time(), "author": author, "message": message}
            )
            meta["current"] = sha
            _atomic_write(d / "current.yaml", text)
            _atomic_write(d / "meta.json", json.dumps(meta, indent=1))
            self._mirror_put(kind, name, d / "current.yaml")
            return sha

    def _mirror_put(self, kind: str, name: str, path: Path) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.put(path)
        except Exception as exc:  # never fails the save: batch jobs re-stage their inputs
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") or type(exc).__name__
            log.warning("config mirror to s3 failed for %s/%s: %s", kind, name, code)
