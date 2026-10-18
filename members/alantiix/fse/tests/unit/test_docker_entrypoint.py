"""docker/entrypoint.sh: runner and batch modes, run with stub ``fse`` and ``aws`` on PATH.

The stubs are small Python scripts that append one JSON line per call to a shared
log, so the tests can check the argv, the environment fse saw and the call order.
No real fse, aws or network is used. The lock cases need ``flock(1)`` and skip
without it (macOS); CI on Linux runs them. Each harness runs a copy of the
entrypoint with the literal control path ``/run/fse-control/plugin_orders``
rewritten to a file under the test's tmp dir.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

PROJECT = Path(__file__).resolve().parents[2]
ENTRYPOINT = PROJECT / "docker" / "entrypoint.sh"
CONTROL_FILE = "/run/fse-control/plugin_orders"

_STUB = """#!{python}
import hashlib, json, os, signal, sqlite3, sys, tempfile, time

def _catalog_bytes():
    fd, path = tempfile.mkstemp(prefix="catalog-")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()
    with open(path, "rb") as fh:
        data = fh.read()
    os.remove(path)
    return data

prog = os.path.basename(sys.argv[0])
log_path = os.environ["STUB_LOG"]
keys = ("SKYLIT_API_KEY", "PROJECTX_USERNAME", "PROJECTX_API_KEY", "HOME",
        "FSE_RUN_PLUGIN_ORDERS")

def record(event):
    entry = {{"prog": prog, "event": event, "argv": sys.argv[1:],
             "env": {{k: os.environ[k] for k in keys if k in os.environ}}}}
    with open(log_path, "a") as fh:
        fh.write(json.dumps(entry) + "\\n")

if prog == "aws":
    record("call")
    if sys.argv[1:3] == ["s3", "ls"]:
        sys.stdout.write(
            os.environ.get("STUB_AWS_LS", "Total Objects: 3\\n   Total Size: 3072\\n")
        )
        sys.exit(0)
    sync_sleep = float(os.environ.get("STUB_AWS_SYNC_SLEEP", "0"))
    if sys.argv[1:3] == ["s3", "sync"] and sync_sleep:
        if not sys.argv[3].startswith("s3://"):
            with open(os.path.join(sys.argv[3], "part-1.bin"), "wb") as fh:
                fh.write(b"x" * 2048)
        time.sleep(sync_sleep)
    if sys.argv[1:3] == ["s3api", "head-object"]:
        key = ""
        if "--key" in sys.argv:
            key = sys.argv[sys.argv.index("--key") + 1]
        # Catalog checks must succeed even when a holdout head is stubbed 404.
        if key == "cache/catalog.sqlite":
            payload = _catalog_bytes()
            digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
            sys.stdout.write(json.dumps({{
                "ContentLength": len(payload),
                "ETag": '"' + digest + '"',
            }}) + "\\n")
            sys.exit(0)
        reasons = {{"404": "Not Found", "403": "Forbidden"}}
        head = os.environ.get("STUB_AWS_HEAD", "ok")
        if head in reasons:
            sys.stderr.write(
                f"An error occurred ({{head}}) when calling the HeadObject operation: "
                f"{{reasons[head]}}\\n"
            )
            sys.exit(254)
        sys.exit(0)
    if sys.argv[1:3] == ["s3api", "get-object"]:
        body = os.environ.get("STUB_AWS_LOCK_BODY", "")
        if not body:
            sys.stderr.write("An error occurred (NoSuchKey)\\n")
            sys.exit(254)
        with open(sys.argv[-1], "w") as fh:
            fh.write(body)
        sys.exit(0)
    if sys.argv[1:3] == ["s3api", "put-object"]:
        state = os.environ["STUB_LOG"] + ".puts"
        n = 0
        if os.path.exists(state):
            n = int(open(state).read() or "0")
        open(state, "w").write(str(n + 1))
        lock = os.environ.get("STUB_AWS_PUT_LOCK", "ok")
        if lock == "held" or (lock == "expired" and n == 0):
            sys.stderr.write(
                "An error occurred (PreconditionFailed) when calling the PutObject "
                "operation: At least one of the pre-conditions you specified did not hold\\n"
            )
            sys.exit(254)
        if lock == "error":
            sys.stderr.write("An error occurred (InternalError)\\n")
            sys.exit(255)
        sys.exit(0)
    if sys.argv[1:3] == ["s3api", "delete-object"]:
        sys.exit(0)
    dest = sys.argv[-2] if sys.argv[-1] == "--only-show-errors" else sys.argv[-1]
    if os.environ.get("STUB_AWS_FAIL_UPLOAD") == "1" and dest.startswith("s3://"):
        sys.exit(1)
    fail_down = os.environ.get("STUB_AWS_FAIL_DOWNLOAD", "")
    if fail_down and not dest.startswith("s3://") and fail_down in sys.argv[3]:
        sys.exit(1)
    if sys.argv[1:3] == ["s3", "cp"] and not dest.startswith("s3://"):
        blob = b"{{}}\\n"
        if dest.endswith("catalog.sqlite.part") or dest.endswith("/catalog.sqlite"):
            state = os.environ["STUB_LOG"] + ".catalog"
            n = 0
            if os.path.exists(state):
                n = int(open(state).read() or "0")
            open(state, "w").write(str(n + 1))
            if n < int(os.environ.get("STUB_CATALOG_FAILS", "0")):
                blob = b"not sqlite"
            else:
                blob = _catalog_bytes()
        with open(dest, "wb") as fh:
            fh.write(blob)
    sys.exit(0)

code = int(os.environ.get("STUB_FSE_EXIT", "0"))

def on_term(signum, frame):
    record("sigterm")
    sys.exit(code)

signal.signal(signal.SIGTERM, on_term)
record("call")
time.sleep(float(os.environ.get("STUB_FSE_SLEEP", "0")))
sys.exit(code)
"""

needs_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="flock(1) not installed")


class Harness:
    """A temporary PATH with stub fse/aws, a data dir and a fake HOME."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name in ("fse", "aws", "python"):
            stub = self.bin / name
            stub.write_text(_STUB.format(python=sys.executable))
            stub.chmod(0o755)
        self.log = tmp_path / "calls.jsonl"
        self.data = tmp_path / "data"
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.local_root = self.home / ".skylit-fse"
        self.sysbin = tmp_path / "sysbin"
        self.path = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
        self.control = tmp_path / "fse-control" / "plugin_orders"
        text = ENTRYPOINT.read_text()
        assert CONTROL_FILE in text
        self.entrypoint = tmp_path / "entrypoint.sh"
        self.entrypoint.write_text(text.replace(CONTROL_FILE, str(self.control)))

    def without_flock(self) -> None:
        """Use a PATH with only the tools the entrypoint needs, so flock is never found."""
        self.sysbin.mkdir(exist_ok=True)
        for tool in ("bash", "mkdir", "dirname", "ln", "sleep", "chmod", "env", "head"):
            found = shutil.which(tool)
            assert found, tool
            link = self.sysbin / tool
            if not link.exists():
                link.symlink_to(found)
        self.path = f"{self.bin}{os.pathsep}{self.sysbin}"

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        for name in ("AWS_CONFIG_FILE", "AWS_REGION", "AWS_PROFILE"):
            env.pop(name, None)
        env.update(
            PATH=self.path,
            HOME=str(self.home),
            STUB_LOG=str(self.log),
            FSE_DATA_DIR=str(self.data),
            FSE_LOCAL_ROOT=str(self.local_root),
            FSE_FAULT_BACKOFF_S="0",
            # Unit tests have no EFS mount; production task def sets efs.
            FSE_CACHE_BACKEND="s3",
        )
        env.update(extra)
        return env

    def with_efs_mount(self) -> None:
        """Stub ``mountpoint`` so ``FSE_CACHE_BACKEND=efs`` treats cache as mounted."""
        script = self.bin / "mountpoint"
        script.write_text(
            "#!/bin/sh\n"
            f'path="{self.local_root}/cache"\n'
            'if [ "$1" = "-q" ] && [ "$2" = "$path" ]; then exit 0; fi\n'
            "exit 1\n"
        )
        script.chmod(0o755)
        (self.local_root / "cache").mkdir(parents=True, exist_ok=True)

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.entrypoint), *args],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def popen(self, *args: str, **extra: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            ["bash", str(self.entrypoint), *args],
            env=self.env(**extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def all_calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def calls(self) -> list[dict[str, Any]]:
        """All calls except the progress listing (``aws s3 ls``), which runs in parallel."""
        return [
            c
            for c in self.all_calls()
            if not (c["prog"] == "aws" and c["argv"][:2] == ["s3", "ls"])
        ]

    def fse_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls() if c["prog"] == "fse" and c["event"] == "call"]


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# ---------------------------------------------------------------- runner


@pytest.mark.parametrize("code", [0, 1])
def test_runner_passes_exit_code_through(h: Harness, code: int) -> None:
    result = h.run("runner", STUB_FSE_EXIT=str(code), FSE_FAULT_BACKOFF_S="30")
    assert result.returncode == code
    assert len(h.fse_calls()) == 1


@pytest.mark.parametrize("code", [2, 3])
def test_runner_backs_off_then_exits_with_fault_code(h: Harness, code: int) -> None:
    start = time.monotonic()
    result = h.run("runner", STUB_FSE_EXIT=str(code), FSE_FAULT_BACKOFF_S="1")
    elapsed = time.monotonic() - start
    assert result.returncode == code
    assert elapsed >= 1.0
    assert f"before exiting {code}" in result.stderr


def test_runner_blanks_placeholder_secrets(h: Harness) -> None:
    result = h.run(
        "runner",
        SKYLIT_API_KEY="PLACEHOLDER-set-by-hand",
        PROJECTX_USERNAME="fake-projectx-user-0000",
    )
    assert result.returncode == 0
    env = h.fse_calls()[0]["env"]
    assert env["SKYLIT_API_KEY"] == ""
    assert env["PROJECTX_USERNAME"] == "fake-projectx-user-0000"


def test_runner_passes_data_dir_flags_and_links_local_root(h: Harness) -> None:
    result = h.run("runner")
    assert result.returncode == 0
    d = str(h.data)
    assert h.fse_calls()[0]["argv"] == [
        "run",
        "--config",
        "configs/runner.yaml",
        "--state-dir",
        f"{d}/live-state",
        "--recordings-root",
        d,
        "--bar-store",
        f"{d}/live-bars",
        "--cache-dir",
        f"{d}/cache",
    ]
    for sub in ("live-state", "live-bars", "recordings", "runs", "logs", "cache"):
        assert (h.data / sub).is_dir()
    assert h.local_root.is_symlink()
    assert os.readlink(h.local_root) == d


def test_runner_keeps_an_existing_local_root(h: Harness) -> None:
    h.local_root.mkdir()
    assert h.run("runner").returncode == 0
    assert not h.local_root.is_symlink()


def test_runner_replaces_image_placeholder_dirs_with_the_data_volume(h: Harness) -> None:
    for rel in ("cache", "runs", "configs", "ui/uploads", "research"):
        (h.local_root / rel).mkdir(parents=True)
    result = h.run("runner")
    assert result.returncode == 0, result.stderr
    assert h.local_root.is_symlink()
    assert os.readlink(h.local_root) == str(h.data)
    assert "replacing image placeholder" in result.stderr


def test_runner_keeps_a_local_root_that_already_holds_a_file(h: Harness) -> None:
    cache = h.local_root / "cache"
    cache.mkdir(parents=True)
    (cache / "catalog.sqlite").write_text("x", encoding="utf-8")
    assert h.run("runner").returncode == 0
    assert not h.local_root.is_symlink()
    assert (cache / "catalog.sqlite").read_text(encoding="utf-8") == "x"


def test_ui_image_replaces_baked_home_before_linking_data() -> None:
    text = (Path(__file__).resolve().parents[2] / "ui" / "Dockerfile").read_text(encoding="utf-8")
    remove = text.index("rm -rf /home/fse/.skylit-fse")
    link = text.index("ln -s /data /home/fse/.skylit-fse")
    assert remove < link


def _hold_lock(path: Path, seconds: float) -> threading.Thread:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a")
    fcntl.flock(fh, fcntl.LOCK_EX)

    def release() -> None:
        time.sleep(seconds)
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()

    thread = threading.Thread(target=release)
    thread.start()
    return thread


@needs_flock
def test_runner_waits_for_a_held_lock(h: Harness) -> None:
    holder = _hold_lock(h.data / "live-state" / "runner.lock", 1.0)
    start = time.monotonic()
    result = h.run("runner", FSE_LOCK_WAIT_S="10")
    holder.join()
    assert result.returncode == 0
    assert time.monotonic() - start >= 0.9
    assert len(h.fse_calls()) == 1


@needs_flock
def test_runner_lock_timeout_exits_2_without_fse(h: Harness) -> None:
    holder = _hold_lock(h.data / "live-state" / "runner.lock", 3.0)
    result = h.run("runner", FSE_LOCK_WAIT_S="1")
    holder.join()
    assert result.returncode == 2
    assert "runner lock still held after 1s" in result.stderr
    assert h.fse_calls() == []


@needs_flock
def test_runner_stop_during_lock_wait_exits_143_without_fse(h: Harness) -> None:
    holder = _hold_lock(h.data / "live-state" / "runner.lock", 5.0)
    proc = h.popen("runner", FSE_LOCK_WAIT_S="30")
    time.sleep(1.0)
    start = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    _, stderr = proc.communicate(timeout=10)
    elapsed = time.monotonic() - start
    holder.join()
    assert proc.returncode == 143
    assert elapsed < 3.0
    assert "stopped before fse started" in stderr
    assert h.fse_calls() == []


# ---------------------------------------------------------------- batch

BARS_DIRS = ("bars", "equity_bars", "derived", "darkpool", "vix")
HEAT_DIRS = (*BARS_DIRS, "heatmaps")
PULL_UP_DIRS = ("bars", "darkpool", "vix", "heatmaps")


def _cache_down(
    root: str, dirs: tuple[str, ...] = BARS_DIRS, *, catalog: bool = True
) -> list[list[str]]:
    calls = [_sync(f"s3://bkt/cache/{d}/", f"{root}/cache/{d}/") for d in dirs]
    if catalog:
        calls.append(
            [
                "s3",
                "cp",
                "s3://bkt/cache/catalog.sqlite",
                f"{root}/cache/catalog.sqlite.part",
                "--only-show-errors",
            ]
        )
        calls.append(
            [
                "s3api",
                "head-object",
                "--bucket",
                "bkt",
                "--key",
                "cache/catalog.sqlite",
                "--output",
                "json",
            ]
        )
    return calls


def _cache_up(root: str, dirs: tuple[str, ...], *, catalog: bool = True) -> list[list[str]]:
    calls = [_sync(f"{root}/cache/{d}/", f"s3://bkt/cache/{d}/") for d in dirs]
    if catalog:
        calls.append(
            [
                "s3",
                "cp",
                f"{root}/cache/catalog.sqlite",
                "s3://bkt/cache/catalog.sqlite",
                "--only-show-errors",
            ]
        )
    return calls


def test_batch_syncs_down_runs_fse_then_syncs_up(h: Harness) -> None:
    result = h.run("batch", "backtest", "--config", "x.yaml", DATA_BUCKET="bkt", STUB_FSE_EXIT="1")
    assert result.returncode == 1
    root = str(h.local_root)
    calls = [(c["prog"], c["argv"]) for c in h.calls()]
    expected = [("aws", argv) for argv in _cache_down(root, HEAT_DIRS)]
    expected.append(("fse", ["backtest", "--config", "x.yaml"]))
    expected.append(("aws", _sync(f"{root}/runs/", "s3://bkt/runs/")))
    assert calls == expected
    assert all("--delete" not in argv for _, argv in calls)
    assert not any(
        prog == "aws" and argv[:3] == ["s3", "sync", "s3://bkt/cache/"] for prog, argv in calls
    )


def test_batch_accounts_skips_cache_sync(h: Harness) -> None:
    result = h.run("batch", "accounts", "--json", DATA_BUCKET="bkt")
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    calls = [(c["prog"], c["argv"]) for c in h.calls()]
    assert calls == [
        ("fse", ["accounts", "--json"]),
        ("aws", _sync(f"{root}/runs/", "s3://bkt/runs/")),
    ]
    assert not any("cache" in " ".join(argv) for prog, argv in calls if prog == "aws")


def test_batch_pull_uploads_written_prefixes_and_runs(h: Harness) -> None:
    result = h.run("batch", "pull", "--start", "2026-01-02", DATA_BUCKET="bkt")
    assert result.returncode == 0
    root = str(h.local_root)
    aws = _aws_calls(h)
    assert aws[: len(_cache_down(root, HEAT_DIRS))] == _cache_down(root, HEAT_DIRS)
    assert aws[-len(_cache_up(root, PULL_UP_DIRS)) - 1 :] == [
        *_cache_up(root, PULL_UP_DIRS),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]
    assert not any(argv[2:4] == [f"{root}/cache/", "s3://bkt/cache/"] for argv in aws)


@pytest.mark.parametrize(
    "args",
    [
        ("report", "--run", "run-20260102-abcd1234"),
        ("experiment", "montecarlo", "--run=run-20260102-abcd1234", "--config", "x.yaml"),
    ],
)
def test_batch_fetches_the_named_run_before_fse(h: Harness, args: tuple[str, ...]) -> None:
    result = h.run("batch", *args, DATA_BUCKET="bkt")
    assert result.returncode == 0
    root = str(h.local_root)
    run = "runs/run-20260102-abcd1234/"
    cmd = args[0]
    dirs = BARS_DIRS if cmd == "report" else HEAT_DIRS
    calls = [(c["prog"], c["argv"]) for c in h.calls()]
    expected = [("aws", argv) for argv in _cache_down(root, dirs)]
    expected.append(("aws", _sync(f"s3://bkt/{run}", f"{root}/{run}")))
    expected.append(("fse", list(args)))
    expected.append(("aws", _sync(f"{root}/runs/", "s3://bkt/runs/")))
    assert calls == expected


@pytest.mark.parametrize(
    "args",
    [
        ("report", "--run", "/tmp/some/run"),
        ("report", "--run", "../x"),
        ("report", "--run", "abc", "--runs-dir", "/elsewhere"),
    ],
)
def test_batch_does_not_fetch_paths_or_custom_runs_dirs(h: Harness, args: tuple[str, ...]) -> None:
    assert h.run("batch", *args, DATA_BUCKET="bkt").returncode == 0
    downloads = [c["argv"][2] for c in h.calls() if c["prog"] == "aws"]
    assert not any(src.startswith("s3://bkt/runs/") for src in downloads)


def test_batch_requires_data_bucket(h: Harness) -> None:
    result = h.run("batch", "pull")
    assert result.returncode == 2
    assert "DATA_BUCKET" in result.stderr
    assert h.calls() == []


def test_batch_upload_failure_after_success_exits_1(h: Harness) -> None:
    result = h.run("batch", "pull", DATA_BUCKET="bkt", STUB_AWS_FAIL_UPLOAD="1")
    assert result.returncode == 1
    assert "upload to s3://bkt failed" in result.stderr


def test_batch_sigterm_is_forwarded_and_results_uploaded(h: Harness) -> None:
    proc = h.popen("batch", "pull", DATA_BUCKET="bkt", STUB_FSE_SLEEP="30", STUB_FSE_EXIT="0")
    deadline = time.monotonic() + 20
    while not h.fse_calls():
        assert time.monotonic() < deadline, "stub fse never started"
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=20)
    assert proc.returncode == 0
    events = [(c["prog"], c["event"]) for c in h.calls()]
    root = str(h.local_root)
    assert ("fse", "sigterm") in events
    sig_at = events.index(("fse", "sigterm"))
    after = [c for c in h.calls()[sig_at + 1 :] if c["event"] == "call"]
    assert [c["argv"] for c in after if c["prog"] == "aws"] == [
        *_cache_up(root, PULL_UP_DIRS),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]


# ---------------------------------------------------------------- console sync (Phase B)

UPLOAD_ID = "0123456789abcdef"


def _sync(src: str, dest: str) -> list[str]:
    return ["s3", "sync", src, dest, "--only-show-errors"]


def _aws_calls(h: Harness) -> list[list[str]]:
    return [c["argv"] for c in h.calls() if c["prog"] == "aws"]


@pytest.mark.parametrize(
    ("args", "down_dirs", "up_dirs", "fetch"),
    [
        (("pull", "--start", "2026-01-02"), HEAT_DIRS, PULL_UP_DIRS, None),
        (("backtest", "--config", "x.yaml"), HEAT_DIRS, (), None),
        (
            ("experiment", "montecarlo", "--run", "run-1", "--config", "x.yaml"),
            HEAT_DIRS,
            (),
            "run-1",
        ),
        (("report", "--run=run-1"), BARS_DIRS, (), "run-1"),
    ],
)
def test_batch_without_sync_variables_matches_phase_1(
    h: Harness,
    args: tuple[str, ...],
    down_dirs: tuple[str, ...],
    up_dirs: tuple[str, ...],
    fetch: str | None,
) -> None:
    assert h.run("batch", *args, DATA_BUCKET="bkt").returncode == 0
    root = str(h.local_root)
    expected = _cache_down(root, down_dirs)
    if fetch:
        expected.append(_sync(f"s3://bkt/runs/{fetch}/", f"{root}/runs/{fetch}/"))
    if up_dirs:
        expected.extend(_cache_up(root, up_dirs))
    expected.append(_sync(f"{root}/runs/", "s3://bkt/runs/"))
    assert _aws_calls(h) == expected


def test_batch_syncs_configs_and_uploads_before_fse_and_makes_configs_read_only(
    h: Harness,
) -> None:
    result = h.run(
        "batch",
        "backtest",
        "--config",
        "x.yaml",
        DATA_BUCKET="bkt",
        FSE_SYNC_CONFIGS="1",
        FSE_SYNC_UPLOADS=UPLOAD_ID,
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    up = f"uploads/{UPLOAD_ID}/"
    calls = [(c["prog"], c["argv"]) for c in h.calls()]
    expected = [("aws", argv) for argv in _cache_down(root, HEAT_DIRS)]
    expected.extend(
        [
            ("aws", _sync("s3://bkt/configs/", f"{root}/configs/")),
            ("aws", _sync(f"s3://bkt/{up}", f"{root}/ui/{up}")),
            ("fse", ["backtest", "--config", "x.yaml"]),
            ("aws", _sync(f"{root}/runs/", "s3://bkt/runs/")),
        ]
    )
    assert calls == expected
    assert (h.local_root / "configs").stat().st_mode & 0o222 == 0


@pytest.mark.parametrize(
    "ids", ["abc", "0123456789ABCDEF", f"{UPLOAD_ID} ../etc", "0123456789abcdef0"]
)
def test_batch_invalid_upload_id_exits_2(h: Harness, ids: str) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", FSE_SYNC_UPLOADS=ids)
    assert result.returncode == 2
    assert "invalid upload id" in result.stderr
    assert h.fse_calls() == []


def test_batch_configs_download_failure_exits_1(h: Harness) -> None:
    result = h.run(
        "batch",
        "backtest",
        DATA_BUCKET="bkt",
        FSE_SYNC_CONFIGS="1",
        STUB_AWS_FAIL_DOWNLOAD="configs/",
    )
    assert result.returncode == 1
    assert "cannot download s3://bkt/configs/" in result.stderr
    assert h.fse_calls() == []


def test_batch_import_vix_uploads_only_vix_prefix(h: Harness) -> None:
    assert h.run("batch", "import-vix", "/x/vix.csv", DATA_BUCKET="bkt").returncode == 0
    root = str(h.local_root)
    assert _aws_calls(h) == [
        *_cache_down(root, ("vix",)),
        *_cache_up(root, ("vix",)),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]


def test_batch_pull_equity_bars_uploads_only_equity_bars_prefix(h: Harness) -> None:
    result = h.run("batch", "pull-equity-bars", "--start", "2026-01-02", DATA_BUCKET="bkt")
    assert result.returncode == 0
    root = str(h.local_root)
    assert _aws_calls(h) == [
        *_cache_down(root, ("equity_bars",)),
        *_cache_up(root, ("equity_bars",)),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]


def test_batch_sync_state_copies_the_holdout_log_down_and_up(h: Harness) -> None:
    args = ("experiment", "holdout", "--config", "x.yaml")
    assert h.run("batch", *args, DATA_BUCKET="bkt", FSE_SYNC_STATE="1").returncode == 0
    root = str(h.local_root)
    log = f"{root}/holdout_log.jsonl"
    key = "state/holdout_log.jsonl"
    calls = [(c["prog"], c["argv"]) for c in h.calls()]
    expected = [("aws", argv) for argv in _cache_down(root, HEAT_DIRS)]
    expected.extend(
        [
            ("aws", ["s3api", "head-object", "--bucket", "bkt", "--key", key]),
            ("aws", ["s3", "cp", f"s3://bkt/{key}", log, "--only-show-errors"]),
            ("fse", list(args)),
            ("aws", _sync(f"{root}/runs/", "s3://bkt/runs/")),
            ("aws", ["s3", "cp", log, f"s3://bkt/{key}", "--only-show-errors"]),
        ]
    )
    assert calls == expected


def test_batch_sync_state_tolerates_a_missing_holdout_log(h: Harness) -> None:
    result = h.run(
        "batch", "experiment", "holdout", DATA_BUCKET="bkt", FSE_SYNC_STATE="1", STUB_AWS_HEAD="404"
    )
    assert result.returncode == 0, result.stderr
    assert not any(
        argv[:2] == ["s3", "cp"] and "holdout_log.jsonl" in " ".join(argv) for argv in _aws_calls(h)
    )
    assert len(h.fse_calls()) == 1


def test_batch_sync_state_other_head_failure_exits_1(h: Harness) -> None:
    result = h.run(
        "batch", "experiment", "holdout", DATA_BUCKET="bkt", FSE_SYNC_STATE="1", STUB_AWS_HEAD="403"
    )
    assert result.returncode == 1
    assert "cannot download state/holdout_log.jsonl" in result.stderr
    assert h.fse_calls() == []


def test_batch_sync_state_upload_failure_after_success_exits_1(h: Harness) -> None:
    result = h.run(
        "batch",
        "experiment",
        "holdout",
        DATA_BUCKET="bkt",
        FSE_SYNC_STATE="1",
        STUB_AWS_FAIL_UPLOAD="1",
    )
    assert result.returncode == 1
    assert "upload to s3://bkt failed" in result.stderr


def test_batch_profile_shim_writes_hts_profile(h: Harness) -> None:
    assert h.run("batch", "backtest", DATA_BUCKET="bkt", AWS_REGION="eu-west-1").returncode == 0
    config = h.home / ".aws" / "config"
    assert config.read_text() == "[profile default]\nregion = eu-west-1\n"
    assert config.stat().st_mode & 0o777 == 0o600


def test_batch_profile_shim_default_region_and_existing_file(h: Harness) -> None:
    assert h.run("batch", "backtest", DATA_BUCKET="bkt").returncode == 0
    config = h.home / ".aws" / "config"
    assert config.read_text() == "[profile default]\nregion = us-east-1\n"
    config.write_text("[default]\n")
    assert h.run("batch", "backtest", DATA_BUCKET="bkt").returncode == 0
    assert config.read_text() == "[default]\n"


def test_batch_profile_shim_skipped_with_aws_config_file(h: Harness) -> None:
    result = h.run(
        "batch-py", "research.example.run", DATA_BUCKET="bkt", AWS_CONFIG_FILE="/nonexistent"
    )
    assert result.returncode == 0, result.stderr
    assert not (h.home / ".aws").exists()


# ---------------------------------------------------------------- batch-py


def test_batch_py_order_and_uploads(h: Harness) -> None:
    result = h.run(
        "batch-py",
        "research.example.run",
        "--grid",
        "g.yaml",
        DATA_BUCKET="bkt",
        FSE_SYNC_CONFIGS="1",
        STUB_FSE_EXIT="0",
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    calls = [(c["prog"], c["argv"]) for c in h.calls()]
    expected = [("aws", argv) for argv in _cache_down(root, BARS_DIRS)]
    expected.extend(
        [
            ("aws", _sync("s3://bkt/configs/", f"{root}/configs/")),
            ("aws", _sync("s3://bkt/research/", f"{root}/research/")),
            ("python", ["-m", "research.example.run", "--grid", "g.yaml"]),
            ("aws", _sync(f"{root}/research/", "s3://bkt/research/")),
            ("aws", _sync(f"{root}/runs/", "s3://bkt/runs/")),
        ]
    )
    assert calls == expected
    assert all("--delete" not in argv for _, argv in calls)
    assert not any(prog == "aws" and "heatmaps" in " ".join(argv) for prog, argv in calls)


@pytest.mark.parametrize(
    "module",
    ["research.example.run"],
)
def test_batch_py_allows_listed_modules(h: Harness, module: str) -> None:
    assert h.run("batch-py", module, DATA_BUCKET="bkt").returncode == 0
    assert [c["argv"] for c in h.calls() if c["prog"] == "python"] == [["-m", module]]


@pytest.mark.parametrize("module", ["os", "research.other.run", "research.example.run;id", ""])
def test_batch_py_rejects_other_modules(h: Harness, module: str) -> None:
    result = h.run("batch-py", module, DATA_BUCKET="bkt")
    assert result.returncode == 2
    assert "unknown module" in result.stderr
    assert h.calls() == []


def test_batch_py_exit_codes(h: Harness) -> None:
    failed = h.run("batch-py", "research.example.run", DATA_BUCKET="bkt", STUB_FSE_EXIT="4")
    assert failed.returncode == 4
    result = h.run("batch-py", "research.example.run", DATA_BUCKET="bkt", STUB_AWS_FAIL_UPLOAD="1")
    assert result.returncode == 1
    assert "upload to s3://bkt failed" in result.stderr


def test_batch_py_requires_data_bucket(h: Harness) -> None:
    assert h.run("batch-py", "research.example.run").returncode == 2
    assert h.calls() == []


# ---------------------------------------------------------------- progress lines


def _assert_in_order(text: str, parts: list[str]) -> None:
    pos = 0
    for part in parts:
        found = text.find(part, pos)
        assert found >= 0, f"{part!r} missing (in order) from:\n{text}"
        pos = found + len(part)


def test_batch_logs_each_stage_and_the_fse_exit(h: Harness) -> None:
    result = h.run("batch", "pull", "--start", "2026-01-02", DATA_BUCKET="bkt")
    assert result.returncode == 0, result.stderr
    _assert_in_order(
        result.stderr,
        [
            "downloading cache/bars from s3://bkt/cache/bars/ ...",
            "downloaded cache/bars in ",
            "downloading cache/catalog.sqlite from s3://bkt/cache/catalog.sqlite ...",
            "downloaded cache/catalog.sqlite in ",
            "running: fse pull --start 2026-01-02",
            "fse pull exited 0 after ",
            "uploading cache/bars to s3://bkt/cache/bars/ ...",
            "uploaded cache/bars in ",
            "uploading cache/catalog.sqlite to s3://bkt/cache/catalog.sqlite ...",
            "uploaded cache/catalog.sqlite in ",
            "uploading runs to s3://bkt/runs/ ...",
            "uploaded runs in ",
        ],
    )


def test_batch_logs_a_non_zero_fse_exit_and_keeps_it(h: Harness) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", STUB_FSE_EXIT="5")
    assert result.returncode == 5
    assert "fse backtest exited 5 after " in result.stderr


def test_batch_sync_prints_progress_while_it_runs(h: Harness) -> None:
    # One prefix only so the sleep stays short; LIST must be prefix-scoped.
    result = h.run(
        "batch",
        "backtest",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars",
        FSE_SYNC_PROGRESS_S="1",
        STUB_AWS_SYNC_SLEEP="2.5",
    )
    assert result.returncode == 0, result.stderr
    assert "s3://bkt/cache/bars/ holds 3 objects (3 KB)" in result.stderr
    ticks = [
        line
        for line in result.stderr.splitlines()
        if line.startswith("fse-entrypoint: downloading cache/bars: ")
    ]
    assert ticks, result.stderr
    assert all("of 3 files" in t and "elapsed" in t and "%" in t for t in ticks), ticks
    assert "still uploading runs, " in result.stderr
    assert ["s3", "ls", "s3://bkt/cache/bars/", "--recursive", "--summarize"] in [
        c["argv"] for c in h.all_calls() if c["prog"] == "aws"
    ]
    assert not any(
        c["argv"][:3] == ["s3", "ls", "s3://bkt/cache/"]
        for c in h.all_calls()
        if c["prog"] == "aws"
    )


def test_batch_progress_before_totals_are_known(h: Harness) -> None:
    result = h.run(
        "batch",
        "backtest",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars",
        FSE_SYNC_PROGRESS_S="1",
        STUB_AWS_SYNC_SLEEP="1.5",
        STUB_AWS_LS="",
    )
    assert result.returncode == 0, result.stderr
    assert "fse-entrypoint: downloading cache/bars: " in result.stderr
    assert " files, " in result.stderr
    assert " holds " not in result.stderr


def test_batch_progress_off_with_zero(h: Harness) -> None:
    result = h.run(
        "batch",
        "pull",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars",
        FSE_SYNC_PROGRESS_S="0",
        STUB_AWS_SYNC_SLEEP="1.2",
    )
    assert result.returncode == 0, result.stderr
    assert not any(c["argv"][:2] == ["s3", "ls"] for c in h.all_calls() if c["prog"] == "aws")
    assert "downloading cache/bars: " not in result.stderr
    assert "still uploading" not in result.stderr
    _assert_in_order(result.stderr, ["downloading cache/bars from ", "downloaded cache/bars in "])


def test_batch_invalid_progress_interval_warns_and_continues(h: Harness) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", FSE_SYNC_PROGRESS_S="soon")
    assert result.returncode == 0, result.stderr
    assert "FSE_SYNC_PROGRESS_S=soon is not a whole number of seconds; using 30" in result.stderr


def test_batch_ticker_does_not_outlive_the_sync(h: Harness) -> None:
    start = time.monotonic()
    result = h.run(
        "batch",
        "pull",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars",
        STUB_AWS_SYNC_SLEEP="0.3",
    )
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - start < 5.0


def test_batch_download_failure_logs_the_failed_stage(h: Harness) -> None:
    result = h.run("batch", "pull", DATA_BUCKET="bkt", STUB_AWS_FAIL_DOWNLOAD="cache/bars")
    assert result.returncode == 1
    _assert_in_order(
        result.stderr,
        [
            "downloading cache/bars failed after ",
            "(aws exit 1)",
            "cannot download s3://bkt/cache/bars/",
        ],
    )
    assert h.fse_calls() == []


def test_batch_upload_failure_logs_the_failed_stage(h: Harness) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", STUB_AWS_FAIL_UPLOAD="1")
    assert result.returncode == 1
    _assert_in_order(
        result.stderr,
        ["uploading runs failed after ", "(aws exit 1)", "upload to s3://bkt failed (aws exit 1)"],
    )


def test_batch_py_logs_the_module_and_its_exit(h: Harness) -> None:
    result = h.run(
        "batch-py", "research.example.run", "--grid", "g.yaml", DATA_BUCKET="bkt", STUB_FSE_EXIT="4"
    )
    assert result.returncode == 4
    _assert_in_order(
        result.stderr,
        [
            "downloading cache/bars from s3://bkt/cache/bars/ ...",
            "downloaded cache/bars in ",
            "downloading cache/catalog.sqlite from s3://bkt/cache/catalog.sqlite ...",
            "downloading research/ from s3://bkt/research/ ...",
            "running: python -m research.example.run --grid g.yaml",
            "research.example.run exited 4 after ",
            "uploading research to s3://bkt/research/ ...",
            "uploading runs to s3://bkt/runs/ ...",
        ],
    )


def test_card_backtest_prefixes_skip_heatmaps_and_darkpool(h: Harness) -> None:
    result = h.run(
        "batch",
        "backtest-plugin",
        "--config",
        "x.yaml",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars,catalog.sqlite",
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    assert _aws_calls(h) == [
        *_cache_down(root, ("bars",)),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]
    joined = " ".join(" ".join(argv) for argv in _aws_calls(h))
    assert "heatmaps" not in joined
    assert "darkpool" not in joined
    assert "--cache-dir" not in h.fse_calls()[0]["argv"]


def test_batch_s3_on_a_mounted_cache_uses_a_private_copy(h: Harness) -> None:
    """Card tasks set the s3 backend while the task definition still mounts EFS."""
    h.with_efs_mount()
    result = h.run(
        "batch",
        "backtest-plugin",
        "--config",
        "x.yaml",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars,catalog.sqlite",
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    private = f"{root}/job-cache"
    assert _aws_calls(h) == [
        *_cache_down(private, ("bars",)),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]
    assert h.fse_calls()[0]["argv"][-2:] == ["--cache-dir", f"{private}/cache"]
    assert "private cache" in result.stderr
    assert not any(f"{root}/cache/" in " ".join(argv) for argv in _aws_calls(h))


def test_batch_cache_prefixes_override(h: Harness) -> None:
    result = h.run(
        "batch",
        "accounts",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars,catalog.sqlite",
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    assert _aws_calls(h) == [
        *_cache_down(root, ("bars",)),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]


def test_batch_empty_cache_prefixes_skips_cache(h: Harness) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", FSE_CACHE_PREFIXES="")
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    assert _aws_calls(h) == [_sync(f"{root}/runs/", "s3://bkt/runs/")]


def test_batch_retries_a_bad_catalog_then_keeps_the_verified_file(h: Harness) -> None:
    result = h.run(
        "batch",
        "backtest-plugin",
        "--config",
        "x.yaml",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars,catalog.sqlite",
        STUB_CATALOG_FAILS="1",
    )
    assert result.returncode == 0, result.stderr
    assert "failed verification (attempt 1/3)" in result.stderr
    aws = _aws_calls(h)
    downloads = [
        argv
        for argv in aws
        if argv[:2] == ["s3", "cp"] and argv[2] == "s3://bkt/cache/catalog.sqlite"
    ]
    heads = [
        argv
        for argv in aws
        if argv[:2] == ["s3api", "head-object"] and "cache/catalog.sqlite" in argv
    ]
    assert len(downloads) == 2
    assert len(heads) == 2
    catalog = h.local_root / "cache" / "catalog.sqlite"
    assert catalog.read_bytes().startswith(b"SQLite format 3")
    assert not (h.local_root / "cache" / "catalog.sqlite.part").exists()


def test_batch_exits_4_when_the_catalog_never_verifies(h: Harness) -> None:
    result = h.run(
        "batch",
        "backtest-plugin",
        "--config",
        "x.yaml",
        DATA_BUCKET="bkt",
        FSE_CACHE_PREFIXES="bars,catalog.sqlite",
        STUB_CATALOG_FAILS="9",
    )
    assert result.returncode == 4
    assert "not a usable Data_Cache catalog after 3 attempts" in result.stderr
    assert h.fse_calls() == []
    assert not (h.local_root / "cache" / "catalog.sqlite").exists()
    assert not (h.local_root / "cache" / "catalog.sqlite.part").exists()


def test_batch_never_syncs_full_cache_tree_or_plugin_books(h: Harness) -> None:
    assert h.run("batch", "backtest", DATA_BUCKET="bkt").returncode == 0
    aws = _aws_calls(h)
    assert not any(argv[2:4] == ["s3://bkt/cache/", f"{h.local_root}/cache/"] for argv in aws)
    assert not any("plugin_books" in " ".join(argv) for argv in aws)


# ---------------------------------------------------------------- FSE_CACHE_BACKEND=efs


def test_batch_efs_skips_cache_download_but_writers_still_upload(h: Harness) -> None:
    h.with_efs_mount()
    # Simulate what `fse pull` writes on EFS (stub fse does not create dirs).
    for sub in PULL_UP_DIRS:
        (h.local_root / "cache" / sub).mkdir(parents=True, exist_ok=True)
    (h.local_root / "cache" / "catalog.sqlite").write_text("{}\n")
    result = h.run(
        "batch", "pull", "--start", "2026-01-02", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="efs"
    )
    assert result.returncode == 0, result.stderr
    assert "skipping cache S3 download" in result.stderr
    root = str(h.local_root)
    aws = _aws_calls(h)
    assert not any(
        argv[:3] == ["s3", "sync"] and argv[2].startswith("s3://bkt/cache/") for argv in aws
    )
    assert not any(
        argv[:3] == ["s3", "cp"] and argv[2] == "s3://bkt/cache/catalog.sqlite" for argv in aws
    )
    assert aws[-len(_cache_up(root, PULL_UP_DIRS)) - 1 :] == [
        *_cache_up(root, PULL_UP_DIRS),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]


def test_batch_efs_read_only_skips_cache_sync_entirely(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run(
        "batch", "backtest", "--config", "x.yaml", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="efs"
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    assert _aws_calls(h) == [_sync(f"{root}/runs/", "s3://bkt/runs/")]
    assert "skipping cache S3 download" in result.stderr


def test_batch_efs_missing_mount_exits_76(h: Harness) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="efs")
    assert result.returncode == 76
    assert "not a mountpoint" in result.stderr
    assert "error:" in result.stderr
    assert h.calls() == []


def test_seed_efs_missing_mount_exits_76(h: Harness) -> None:
    result = h.run("seed-efs-cache", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="efs")
    assert result.returncode == 76
    assert "not a mountpoint" in result.stderr


def test_batch_py_efs_skips_cache_download(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run(
        "batch-py",
        "research.example.run",
        "--grid",
        "g.yaml",
        DATA_BUCKET="bkt",
        FSE_CACHE_BACKEND="efs",
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    aws = _aws_calls(h)
    assert not any("cache/" in " ".join(argv) for argv in aws)
    assert aws == [
        _sync("s3://bkt/research/", f"{root}/research/"),
        _sync(f"{root}/research/", "s3://bkt/research/"),
        _sync(f"{root}/runs/", "s3://bkt/runs/"),
    ]


def test_batch_invalid_cache_backend_exits_2(h: Harness) -> None:
    result = h.run("batch", "backtest", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="disk")
    assert result.returncode == 2
    assert "invalid FSE_CACHE_BACKEND" in result.stderr


def test_seed_efs_cache_syncs_full_tree_under_lock(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run("seed-efs-cache", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="efs")
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    aws = _aws_calls(h)
    assert aws[0][:2] == ["s3api", "put-object"]
    assert "locks/pull.lock" in " ".join(aws[0])
    assert ["s3", "sync", "s3://bkt/cache/", f"{root}/cache/", "--only-show-errors"] in aws
    assert aws[-1][:2] == ["s3api", "delete-object"]
    assert "S3→EFS cache reconcile done" in result.stderr


def test_seed_efs_cache_external_lock_skips_acquire(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run(
        "seed-efs-cache",
        DATA_BUCKET="bkt",
        FSE_CACHE_BACKEND="efs",
        FSE_SEED_LOCK_EXTERNAL="1",
    )
    assert result.returncode == 0, result.stderr
    assert "FSE_SEED_LOCK_EXTERNAL=1" in result.stderr
    aws = _aws_calls(h)
    assert not any(argv[:2] == ["s3api", "put-object"] for argv in aws)
    assert not any(argv[:2] == ["s3api", "delete-object"] for argv in aws)
    assert ["s3", "sync", "s3://bkt/cache/", f"{h.local_root}/cache/", "--only-show-errors"] in aws


def test_seed_efs_cache_skips_when_lock_held(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run(
        "seed-efs-cache", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="efs", STUB_AWS_PUT_LOCK="held"
    )
    assert result.returncode == 0, result.stderr
    assert "skipping S3→EFS reconcile" in result.stderr
    aws = _aws_calls(h)
    assert [argv[:2] for argv in aws] == [["s3api", "put-object"], ["s3api", "get-object"]]
    assert not any(argv[:2] == ["s3", "sync"] for argv in aws)


def test_seed_efs_cache_replaces_an_expired_pull_lock(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run(
        "seed-efs-cache",
        DATA_BUCKET="bkt",
        FSE_CACHE_BACKEND="efs",
        STUB_AWS_PUT_LOCK="expired",
        STUB_AWS_LOCK_BODY="user=seed-efs-cache\nhost=old\nstarted=2020-01-01T00:00:00Z\n",
    )
    assert result.returncode == 0, result.stderr
    assert "past its" in result.stderr
    assert "S3→EFS cache reconcile done" in result.stderr
    aws = _aws_calls(h)
    puts = [argv for argv in aws if argv[:2] == ["s3api", "put-object"]]
    assert len(puts) == 2
    assert any(argv[:2] == ["s3", "sync"] for argv in aws)


def test_seed_efs_cache_keeps_a_lock_without_a_started_time(h: Harness) -> None:
    h.with_efs_mount()
    result = h.run(
        "seed-efs-cache",
        DATA_BUCKET="bkt",
        FSE_CACHE_BACKEND="efs",
        STUB_AWS_PUT_LOCK="held",
        STUB_AWS_LOCK_BODY="user=seed-efs-cache\nhost=old\nstarted=not-a-time\n",
    )
    assert result.returncode == 0, result.stderr
    assert "skipping S3→EFS reconcile" in result.stderr
    assert "past its" not in result.stderr
    aws = _aws_calls(h)
    assert not any(argv[:2] == ["s3", "sync"] for argv in aws)
    assert not any(argv[:2] == ["s3api", "delete-object"] for argv in aws)


def test_batch_places_out_under_the_runs_dir(h: Harness) -> None:
    result = h.run(
        "batch",
        "backtest-plugin",
        "--config",
        "/data/configs/card.yaml",
        "--out",
        "/data/runs/card-abc",
        DATA_BUCKET="bkt",
    )
    assert result.returncode == 0, result.stderr
    argv = h.fse_calls()[0]["argv"]
    assert argv[argv.index("--out") + 1] == f"{h.local_root}/runs/card-abc"
    assert "placing --out /data/runs/card-abc" in result.stderr


def test_batch_leaves_an_out_path_that_is_already_under_runs(h: Harness) -> None:
    out = f"{h.local_root}/runs/card-abc"
    result = h.run(
        "batch",
        "backtest-plugin",
        "--out",
        out,
        DATA_BUCKET="bkt",
    )
    assert result.returncode == 0, result.stderr
    argv = h.fse_calls()[0]["argv"]
    assert argv[argv.index("--out") + 1] == out
    assert "placing --out" not in result.stderr


def test_batch_refuses_when_runs_cannot_be_created(h: Harness) -> None:
    h.local_root.mkdir(parents=True)
    (h.local_root / "runs").write_text("not a directory", encoding="utf-8")
    result = h.run("batch", "backtest-plugin", DATA_BUCKET="bkt")
    assert result.returncode == 1
    assert "error: cannot create" in result.stderr
    assert str(h.local_root / "runs") in result.stderr
    assert h.fse_calls() == []


def test_seed_efs_cache_requires_efs_backend(h: Harness) -> None:
    result = h.run("seed-efs-cache", DATA_BUCKET="bkt", FSE_CACHE_BACKEND="s3")
    assert result.returncode == 2
    assert "requires FSE_CACHE_BACKEND=efs" in result.stderr


def test_batch_unset_cache_backend_defaults_to_s3_sync(h: Harness) -> None:
    """Unwired task defs must not assume EFS (entrypoint default is s3)."""
    env = h.env(DATA_BUCKET="bkt")
    env.pop("FSE_CACHE_BACKEND", None)
    result = subprocess.run(
        ["bash", str(h.entrypoint), "batch", "backtest", "--config", "x.yaml"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    root = str(h.local_root)
    assert "skipping cache S3 download" not in result.stderr
    assert _aws_calls(h)[: len(_cache_down(root, HEAT_DIRS))] == _cache_down(root, HEAT_DIRS)


# ---------------------------------------------------------------- runner modes

_PAPER_STUB = """#!{python}
import json, os, signal, sys, time

log_path = os.environ["STUB_LOG"]
count_path = log_path + ".count"
n = int(open(count_path).read()) + 1 if os.path.exists(count_path) else 1
open(count_path, "w").write(str(n))
entry = {{"prog": "fse", "event": "call", "argv": sys.argv[1:], "env": {{}}}}
with open(log_path, "a") as fh:
    fh.write(json.dumps(entry) + "\\n")
codes = os.environ.get("STUB_PAPER_CODES", "0,2").split(",")
if n <= len(codes):
    sys.exit(int(codes[n - 1]))
signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
os.kill(os.getppid(), signal.SIGTERM)
time.sleep(30)
sys.exit(9)
"""


def _paper_harness(h: Harness) -> Harness:
    h.without_flock()
    stub = h.bin / "fse"
    stub.write_text(_PAPER_STUB.format(python=sys.executable))
    stub.chmod(0o755)
    return h


def _paper_argv(h: Harness) -> list[str]:
    d = str(h.data)
    return [
        "paper",
        "--config",
        "configs/runner.yaml",
        "--state-dir",
        f"{d}/live-state",
        "--recordings-root",
        d,
        "--cache-dir",
        f"{d}/cache",
    ]


def test_runner_paper_loops_between_sessions_until_stopped(h: Harness) -> None:
    _paper_harness(h)
    result = h.run("runner", FSE_RUNNER_MODE="paper", FSE_PAPER_RETRY_S="0")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("retrying in 0s") == 2
    assert [c["argv"] for c in h.fse_calls()] == [_paper_argv(h)] * 3


def test_runner_paper_auth_failure_fault_exits_3(h: Harness) -> None:
    _paper_harness(h)
    result = h.run("runner", FSE_RUNNER_MODE="paper", FSE_PAPER_RETRY_S="0", STUB_PAPER_CODES="3")
    assert result.returncode == 3
    assert "fse paper exited 3" in result.stderr
    assert "before exiting 3" in result.stderr
    assert len(h.fse_calls()) == 1


def test_runner_paper_rc_1_exits_without_retry(h: Harness) -> None:
    _paper_harness(h)
    result = h.run("runner", FSE_RUNNER_MODE="paper", FSE_PAPER_RETRY_S="0", STUB_PAPER_CODES="1")
    assert result.returncode == 1
    assert "retrying" not in result.stderr
    assert len(h.fse_calls()) == 1


def test_runner_paper_bad_retry_interval_exits_2(h: Harness) -> None:
    _paper_harness(h)
    result = h.run("runner", FSE_RUNNER_MODE="paper", FSE_PAPER_RETRY_S="abc")
    assert result.returncode == 2
    assert "FSE_PAPER_RETRY_S=abc" in result.stderr
    assert h.fse_calls() == []


@pytest.mark.parametrize("mode", ["live", "garbage", "RUN"])
def test_runner_rejects_other_modes(h: Harness, mode: str) -> None:
    start = time.monotonic()
    result = h.run("runner", FSE_RUNNER_MODE=mode, FSE_FAULT_BACKOFF_S="1")
    assert result.returncode == 2
    assert time.monotonic() - start >= 1.0
    assert f"FSE_RUNNER_MODE={mode} is not allowed (run or paper)" in result.stderr
    assert h.fse_calls() == []


def test_runner_explicit_run_mode_is_unchanged(h: Harness) -> None:
    assert h.run("runner", FSE_RUNNER_MODE="run").returncode == 0
    assert h.fse_calls()[0]["argv"][:3] == ["run", "--config", "configs/runner.yaml"]


# ---------------------------------------------------------------- plugin orders gate


def _write_control(h: Harness, content: str) -> None:
    h.control.parent.mkdir(exist_ok=True)
    h.control.write_text(content)


def _plugin_call(h: Harness, result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr
    (call,) = h.fse_calls()
    return call


def _assert_paper_orders(call: dict[str, Any]) -> None:
    assert "--plugin-orders" not in call["argv"]
    assert "--live-orders" not in call["argv"]
    assert "FSE_RUN_PLUGIN_ORDERS" not in call["env"]


def test_plugin_orders_new_image_old_infra_stays_paper(h: Harness) -> None:
    # (a) No control file (old host scripts never mount it); the env var is ignored.
    result = h.run("runner", FSE_RUN_PLUGIN_ORDERS="enabled", FSE_RUNNER_MODE="run")
    call = _plugin_call(h, result)
    _assert_paper_orders(call)
    assert "plugin orders" not in result.stderr


def test_plugin_orders_switch_false_stays_paper(h: Harness) -> None:
    # (b) New infra with plugin_orders_enabled = false writes `disabled`.
    _write_control(h, "disabled\n")
    result = h.run("runner", FSE_RUN_PLUGIN_ORDERS="enabled", FSE_RUNNER_MODE="run")
    call = _plugin_call(h, result)
    _assert_paper_orders(call)


@pytest.mark.parametrize(
    "content",
    ["ENABLED\n", "true\n", "enabled \n", "", "en\nabled\n", " enabled\n"],
    ids=["upper", "true", "trailing-space", "empty", "split", "leading-space"],
)
def test_plugin_orders_other_content_stays_paper(h: Harness, content: str) -> None:
    # (c) Only exactly `enabled` (with or without the newline) qualifies.
    _write_control(h, content)
    call = _plugin_call(h, h.run("runner", FSE_RUNNER_MODE="run"))
    _assert_paper_orders(call)


def test_plugin_orders_symlinked_control_file_stays_paper(h: Harness, tmp_path: Path) -> None:
    # (c) A symlink is refused even when its target holds `enabled`.
    target = tmp_path / "elsewhere"
    target.write_text("enabled\n")
    h.control.parent.mkdir()
    h.control.symlink_to(target)
    call = _plugin_call(h, h.run("runner", FSE_RUNNER_MODE="run"))
    _assert_paper_orders(call)


def test_plugin_orders_need_an_explicit_runner_mode(h: Harness) -> None:
    # (d) FSE_RUNNER_MODE unset (no console drop-in): the default run stays paper.
    _write_control(h, "enabled\n")
    call = _plugin_call(h, h.run("runner"))
    _assert_paper_orders(call)


def test_plugin_orders_never_reach_paper(h: Harness) -> None:
    # (e) fse paper never gets the flag.
    _paper_harness(h)
    _write_control(h, "enabled\n")
    result = h.run("runner", FSE_RUNNER_MODE="paper", FSE_PAPER_RETRY_S="0", STUB_PAPER_CODES="1")
    assert result.returncode == 1
    assert [c["argv"] for c in h.fse_calls()] == [_paper_argv(h)]
    assert "plugin orders" not in result.stderr


@pytest.mark.parametrize("content", ["enabled\n", "enabled"], ids=["newline", "bare"])
def test_plugin_orders_enabled_by_the_control_file_in_run_mode(h: Harness, content: str) -> None:
    # (f) The only path: file `enabled` + FSE_RUNNER_MODE=run.
    # --live-orders travels with --plugin-orders so Combine can resolve.
    _write_control(h, content)
    result = h.run("runner", FSE_RUNNER_MODE="run")
    call = _plugin_call(h, result)
    d = str(h.data)
    assert call["argv"] == [
        "run",
        "--config",
        "configs/runner.yaml",
        "--state-dir",
        f"{d}/live-state",
        "--recordings-root",
        d,
        "--bar-store",
        f"{d}/live-bars",
        "--cache-dir",
        f"{d}/cache",
        "--plugin-orders",
        "--live-orders",
    ]
    assert call["env"]["FSE_RUN_PLUGIN_ORDERS"] == "enabled"
    assert "plugin orders: enabled by control/plugin_orders" in result.stderr


def test_plugin_orders_env_override_of_the_control_path_is_ignored(
    h: Harness, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.write_text("enabled\n")
    call = _plugin_call(h, h.run("runner", FSE_RUNNER_MODE="run", FSE_CONTROL_FILE=str(other)))
    _assert_paper_orders(call)


# ---------------------------------------------------------------- passthrough


def test_other_commands_run_as_given(h: Harness) -> None:
    result = h.run("fse", "--help")
    assert result.returncode == 0
    assert h.fse_calls()[0]["argv"] == ["--help"]
