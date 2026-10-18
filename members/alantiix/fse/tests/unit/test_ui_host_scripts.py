"""Console host scripts and documents (infra/modules/ui_host) with stubs on PATH.

Stub ``aws``, ``curl`` and ``docker`` record one JSON line per call and answer
from environment variables. The scripts take FSE_RUN_DIR / FSE_ENV_DIR /
FSE_STATE_FILE overrides, so nothing touches /run, /var/lib or AWS. Scripts run
under /bin/bash when it exists (bash 3.2 on macOS). The secrets below are test
values, not real credentials.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

UI_HOST = Path(__file__).resolve().parents[2] / "infra" / "modules" / "ui_host"
FILES = UI_HOST / "files"
TEMPLATES = UI_HOST / "templates"
FETCH = FILES / "fse-ui-fetch-env.sh"
UI_START = FILES / "fse-ui-start.sh"
EDGE_START = FILES / "fse-edge-start.sh"
RUNNER_MODE = FILES / "fse-runner-mode.sh"
CONSOLE_START = FILES / "fse-console-start.sh"
PHASE1_START = UI_HOST.parent / "runner" / "files" / "fse-start.sh"
RUNNER_STATE = FILES / "fse-runner-state.sh"
SYNC_CACHE = FILES / "fse-sync-cache.sh"
OFFLOAD = FILES / "fse-offload-recordings.sh"
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"

CLIENT_SECRET = "testclientsecret0123456789abcdefghijklmnopqrstuv"
ORIGIN_SECRET = "TestOriginSecret0123456789abcdefghijklmnopqrstuv"
INSTANCE_ID = "i-0123456789abcdef0"
UI_REPO = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse-ui"
EDGE_REPO = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse-edge"

_STUB_AWS = """#!{python}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(["aws", *argv]) + "\\n")
fail = os.environ.get("STUB_AWS_FAIL")
if fail and fail in argv:
    sys.stderr.write("An error occurred (AccessDenied): stub-stderr-marker\\n")
    sys.exit(255)
def name():
    return argv[argv.index("--name") + 1] if "--name" in argv else ""
def stored(key):
    return os.path.join(os.environ["STUB_S3_DIR"], key.replace("/", "__"))
if argv[:2] == ["s3", "cp"] and not argv[2].startswith("s3://"):
    key = argv[3].split("/", 3)[3]  # s3://bucket/<key>
    with open(argv[2], "rb") as src, open(stored(key), "wb") as out:
        out.write(src.read())
elif argv[:2] == ["s3", "cp"]:
    with open(argv[3], "w") as out:
        out.write("catalog")
elif "head-object" in argv and "ContentLength" in argv:
    key = argv[argv.index("--key") + 1]
    if os.environ.get("STUB_HEAD_LENGTH"):
        print(os.environ["STUB_HEAD_LENGTH"])
    elif "STUB_S3_DIR" in os.environ and os.path.exists(stored(key)):
        print(os.path.getsize(stored(key)))
    else:
        sys.stderr.write("An error occurred (404) when calling HeadObject: Not Found\\n")
        sys.exit(255)
elif "head-object" in argv:
    print(os.environ.get("STUB_CATALOG_ETAG", '"etag-1"'))
elif "describe-user-pool-client" in argv:
    print(os.environ.get("STUB_CLIENT_SECRET", "None"))
elif "get-parameters" in argv:
    print(os.environ.get("STUB_RUNNER_PARAMS", json.dumps({{"Parameters": []}})))
elif "get-parameter" in argv and name().endswith("/image_tag"):
    print(os.environ.get("STUB_TAG", "abc123"))
elif "get-parameter" in argv and name().endswith("/ui/origin_secret"):
    print(os.environ.get("STUB_ORIGIN_SECRET", "PLACEHOLDER"))
"""

_STUB_CURL = """#!{python}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(["curl", *argv]) + "\\n")
if os.environ.get("STUB_CURL_EXIT"):
    sys.exit(int(os.environ["STUB_CURL_EXIT"]))
if "PUT" in argv:
    sys.stdout.write("stub-imds-token")
elif any(a.endswith("/meta-data/instance-id") for a in argv):
    sys.stdout.write(os.environ.get("STUB_INSTANCE_ID", "{instance}"))
"""

_STUB_LOGGER = """#!{python}
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + "\\n")
"""

# Non-root stand-in for install(1): only the -d form the control document uses.
_STUB_INSTALL = """#!/bin/sh
for last; do :; done
mkdir -p "$last"
"""


class Host:
    """A temporary PATH with stubs and stand-ins for /run and /var/lib/fse-host."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        py = sys.executable
        stubs = {
            "aws": _STUB_AWS.format(python=py),
            "curl": _STUB_CURL.format(python=py, instance=INSTANCE_ID),
            "docker": _STUB_LOGGER.format(python=py),
            "systemctl": _STUB_LOGGER.format(python=py),
            "install": _STUB_INSTALL,
        }
        for name, body in stubs.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        (self.bin / "python3").symlink_to(py)
        self.run_dir = tmp_path / "run"
        self.host_dir = tmp_path / "var-lib-fse-host"
        self.control_dir = tmp_path / "run-fse-control"
        self.log = tmp_path / "calls.jsonl"

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("FSE_", "AWS_", "STUB_"))
            and k not in ("EXIT_STATUS", "SERVICE_RESULT")
        }
        env.update(
            PATH=f"{self.bin}{os.pathsep}/usr/bin:/bin",
            STUB_LOG=str(self.log),
            AWS_REGION="us-east-1",
            FSE_RUN_DIR=str(self.run_dir),
            FSE_ENV_DIR=str(self.run_dir),
            FSE_CONTROL_DIR=str(self.control_dir),
            FSE_STATE_FILE=str(self.host_dir / "runner-state.json"),
            FSE_UI_COGNITO_POOL_ID="us-east-1_TEST",
            FSE_UI_OIDC_CLIENT_ID="testclientid0123456789",
            FSE_EDGE_SITE="origin.example.org",
            FSE_EDGE_ZONE_ID="Z0000000000TEST",
            FSE_EDGE_AWS_REGION="us-east-1",
            FSE_UI_IMAGE_REPO=UI_REPO,
            FSE_EDGE_IMAGE_REPO=EDGE_REPO,
            FSE_UI_LOG_GROUP="/fse/ui",
        )
        env.update(extra)
        return env

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, str(script), *args],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def run_text(self, script: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, "-c", script],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def calls(self, program: str | None = None) -> list[list[str]]:
        if not self.log.exists():
            return []
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [c[1:] for c in calls if program is None or c[0] == program]


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ------------------------------------------------------- bundle and control document


def test_bundle_clears_the_start_limit_before_restarting_the_ui() -> None:
    bundle = (TEMPLATES / "bundle.sh.tftpl").read_text()
    assert "systemctl reset-failed fse-ui.service fse-edge.service || true" in bundle
    assert bundle.index("reset-failed fse-ui.service fse-edge.service") < bundle.index(
        "systemctl restart --no-block fse-ui.service fse-edge.service"
    )


def _control_script(action: str, host_dir: Path) -> str:
    doc = yaml.safe_load((TEMPLATES / "runner-control.yaml").read_text())
    assert doc["parameters"]["action"]["allowedValues"] == ["start", "stop", "restart", "status"]
    (step,) = doc["mainSteps"]
    assert step["action"] == "aws:runShellScript"
    script = "\n".join(step["inputs"]["runCommand"])
    return script.replace("{{ action }}", action).replace("/var/lib/fse-host", str(host_dir))


def test_control_stop_writes_the_hold_marker(host: Host) -> None:
    result = host.run_text(_control_script("stop", host.host_dir))

    assert result.returncode == 0, result.stderr
    assert (host.host_dir / "runner-held").exists()
    systemctl = host.calls("systemctl")
    assert ["--no-block", "stop", "fse-runner.service"] in systemctl
    assert systemctl[-1][:2] == ["show", "fse-runner.service"]


@pytest.mark.parametrize("action", ["start", "restart"])
def test_control_start_removes_the_hold_marker(host: Host, action: str) -> None:
    host.host_dir.mkdir()
    (host.host_dir / "runner-held").touch()
    result = host.run_text(_control_script(action, host.host_dir))

    assert result.returncode == 0, result.stderr
    assert not (host.host_dir / "runner-held").exists()
    systemctl = host.calls("systemctl")
    assert systemctl[0] == ["reset-failed", "fse-runner.service"]
    assert ["--no-block", action, "fse-runner.service"] in systemctl


def test_control_status_changes_nothing(host: Host) -> None:
    host.host_dir.mkdir()
    (host.host_dir / "runner-held").touch()
    result = host.run_text(_control_script("status", host.host_dir))

    assert result.returncode == 0, result.stderr
    assert (host.host_dir / "runner-held").exists()
    assert [c[0] for c in host.calls("systemctl")] == ["show"]


# ------------------------------------------------------------ deploy workflow

# The workflows are not part of this copy; add yours at .github/workflows/fse-deploy.yml.
DEPLOY_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "fse-deploy.yml"
needs_deploy_workflow = pytest.mark.skipif(
    not DEPLOY_WORKFLOW.is_file(), reason="no fse-deploy workflow in this copy"
)


def _restart_script() -> str:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    (step,) = [s for s in steps if s.get("name") == "Restart services"]
    return str(step["run"])


def _restart_commands() -> tuple[str, str]:
    """The runner and UI host commands the deploy's "Restart services" step sends."""
    script = _restart_script()
    runner = re.search(r"^\s*runner_cmd='([^']*)'$", script, re.M)
    ui = re.search(r"^\s*ui_cmd='([^']*)'$", script, re.M)
    assert runner
    assert ui
    return runner.group(1), ui.group(1)


@needs_deploy_workflow
def test_deploy_ui_restart_clears_the_start_limit_first() -> None:
    _, ui = _restart_commands()
    assert "systemctl is-enabled --quiet fse-ui.service" in ui
    assert "reset-failed fse-ui.service fse-edge.service" in ui
    assert ui.index("reset-failed fse-ui.service fse-edge.service") < ui.index(
        "systemctl restart fse-ui.service fse-edge.service"
    )


@needs_deploy_workflow
def test_deploy_runner_restart_checks_the_hold_marker() -> None:
    runner, _ = _restart_commands()
    assert "[ -e /var/lib/fse-host/runner-held ]" in runner
    assert runner.index("runner-held") < runner.index("systemctl restart fse-runner")


@needs_deploy_workflow
def test_deploy_sends_the_runner_command_only_when_not_ui_only() -> None:
    script = _restart_script()
    ui_only, other = script.split('if [ "$UI_ONLY" = true ]; then', 1)[1].split("else", 1)
    assert "$runner" not in ui_only.split("fi", 1)[0]
    assert "[$runner, $ui]" in other.split("fi", 1)[0]


@needs_deploy_workflow
def test_deploy_runner_command_skips_a_held_runner(host: Host) -> None:
    runner, _ = _restart_commands()
    host.host_dir.mkdir()
    (host.host_dir / "runner-held").touch()
    result = host.run_text(runner.replace("/var/lib/fse-host", str(host.host_dir)))

    assert result.returncode == 0, result.stderr
    assert "held by the console" in result.stdout
    assert host.calls("systemctl") == []


@needs_deploy_workflow
def test_deploy_runner_command_restarts_a_free_runner(host: Host) -> None:
    runner, _ = _restart_commands()
    result = host.run_text(runner.replace("/var/lib/fse-host", str(host.host_dir)))

    assert result.returncode == 0, result.stderr
    # reset-failed first: a runner that hit its start limit refuses a plain restart
    # ("Start request repeated too quickly"), which failed a deploy.
    assert host.calls("systemctl") == [
        ["reset-failed", "fse-runner"],
        ["restart", "fse-runner"],
        ["is-active", "fse-runner"],
    ]


# ------------------------------------------------------------------ start scripts


def _docker_run(host: Host) -> list[str]:
    runs = [c for c in host.calls("docker") if c[:1] == ["run"]]
    assert len(runs) == 1
    return runs[0]


def test_ui_start_runs_the_exact_container(host: Host) -> None:
    host.run_dir.mkdir()
    (host.run_dir / "image_tag").write_text("abc123\n")
    result = host.run(UI_START)

    assert result.returncode == 0, result.stderr
    assert host.calls("docker")[0] == ["pull", "--quiet", f"{UI_REPO}:abc123"]
    assert _docker_run(host) == [
        "run", "--rm", "--name", "fse-ui",
        "--network", "host",
        "--user", "10001:10001",
        "--env-file", "/etc/fse/ui.env",
        "--env-file", f"{host.run_dir}/env",
        "-v", "/data:/data",
        "-v", "/var/lib/fse-host:/data/host:ro",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "768m",
        "--pids-limit", "256",
        "--log-driver", "awslogs",
        "--log-opt", "awslogs-region=us-east-1",
        "--log-opt", "awslogs-group=/fse/ui",
        "--log-opt", "awslogs-stream=ui",
        f"{UI_REPO}:abc123",
    ]  # fmt: skip


def test_ui_start_mounts_host_files_read_only_after_data(host: Host) -> None:
    host.run_dir.mkdir()
    (host.run_dir / "image_tag").write_text("abc123\n")
    host.run(UI_START)
    argv = _docker_run(host)

    assert argv.index("/data:/data") < argv.index("/var/lib/fse-host:/data/host:ro")
    assert "NET_BIND_SERVICE" not in argv


def test_edge_start_runs_the_exact_container(host: Host) -> None:
    host.run_dir.mkdir()
    (host.run_dir / "image_tag").write_text("abc123\n")
    result = host.run(EDGE_START)

    assert result.returncode == 0, result.stderr
    assert _docker_run(host) == [
        "run", "--rm", "--name", "fse-edge",
        "--network", "host",
        "--user", "0:0",
        "--env-file", f"{host.run_dir}/env",
        "-v", "/var/lib/fse-edge:/data",
        "--read-only",
        "--tmpfs", "/tmp",
        "--tmpfs", "/config",
        "--cap-drop", "ALL",
        "--cap-add", "NET_BIND_SERVICE",
        "--security-opt", "no-new-privileges",
        "--memory", "128m",
        "--pids-limit", "128",
        "--log-driver", "awslogs",
        "--log-opt", "awslogs-region=us-east-1",
        "--log-opt", "awslogs-group=/fse/ui",
        "--log-opt", "awslogs-stream=edge",
        f"{EDGE_REPO}:abc123",
    ]  # fmt: skip


# ------------------------------------------------------------- fse-ui-fetch-env.sh


def _env_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def test_fetch_ui_writes_private_env_and_never_prints_the_secret(host: Host) -> None:
    result = host.run(FETCH, "ui", STUB_CLIENT_SECRET=CLIENT_SECRET)

    assert result.returncode == 0, result.stderr
    env_file = host.run_dir / "env"
    assert _mode(env_file) == 0o600
    assert _env_lines(env_file) == [
        f"FSE_UI_OIDC_CLIENT_SECRET={CLIENT_SECRET}",
        f"FSE_UI_RUNNER_INSTANCE_ID={INSTANCE_ID}",
        "FSE_UI_IMAGE_TAG=abc123",
    ]
    assert (host.run_dir / "image_tag").read_text() == "abc123\n"
    assert not (host.run_dir / "env.tmp").exists()
    assert CLIENT_SECRET not in result.stdout + result.stderr
    describe = next(c for c in host.calls("aws") if "describe-user-pool-client" in c)
    assert describe[describe.index("--user-pool-id") + 1] == "us-east-1_TEST"
    assert describe[describe.index("--client-id") + 1] == "testclientid0123456789"
    token_call, id_call = host.calls("curl")
    assert "PUT" in token_call
    assert "X-aws-ec2-metadata-token: stub-imds-token" in id_call


@pytest.mark.parametrize("role", ["ui", "edge"])
@pytest.mark.parametrize("tag", ["none", ""])
def test_fetch_exits_1_when_no_image_is_deployed(host: Host, role: str, tag: str) -> None:
    result = host.run(
        FETCH,
        role,
        STUB_TAG=tag,
        STUB_CLIENT_SECRET=CLIENT_SECRET,
        STUB_ORIGIN_SECRET=ORIGIN_SECRET,
    )

    assert result.returncode == 1
    assert "no image deployed yet" in result.stderr
    assert not (host.run_dir / "env").exists()
    assert not (host.run_dir / "image_tag").exists()


@pytest.mark.parametrize("secret", ["None", "", "two words"])
def test_fetch_ui_rejects_a_missing_client_secret(host: Host, secret: str) -> None:
    result = host.run(FETCH, "ui", STUB_CLIENT_SECRET=secret)

    assert result.returncode == 1
    assert "no usable client secret" in result.stderr
    assert not (host.run_dir / "env").exists()


@pytest.mark.parametrize("instance_id", ["i-xyz", "", "i-0123456789abcdef0\nextra"])
def test_fetch_ui_rejects_a_bad_instance_id(host: Host, instance_id: str) -> None:
    result = host.run(FETCH, "ui", STUB_CLIENT_SECRET=CLIENT_SECRET, STUB_INSTANCE_ID=instance_id)

    assert result.returncode == 1
    assert "invalid instance id" in result.stderr
    assert not (host.run_dir / "env").exists()


def test_fetch_ui_fails_when_imds_is_unreachable(host: Host) -> None:
    result = host.run(FETCH, "ui", STUB_CLIENT_SECRET=CLIENT_SECRET, STUB_CURL_EXIT="7")

    assert result.returncode == 1
    assert "IMDSv2 token failed" in result.stderr


def test_fetch_edge_writes_the_origin_secret_privately(host: Host) -> None:
    result = host.run(FETCH, "edge", STUB_ORIGIN_SECRET=ORIGIN_SECRET)

    assert result.returncode == 0, result.stderr
    env_file = host.run_dir / "env"
    assert _mode(env_file) == 0o600
    assert _env_lines(env_file) == [
        f"ORIGIN_SECRET={ORIGIN_SECRET}",
        "FSE_EDGE_SITE=origin.example.org",
        "FSE_EDGE_ZONE_ID=Z0000000000TEST",
        "FSE_EDGE_AWS_REGION=us-east-1",
    ]
    assert ORIGIN_SECRET not in result.stdout + result.stderr
    get = next(c for c in host.calls("aws") if "/fse/ui/origin_secret" in c)
    assert "--with-decryption" in get
    assert host.calls("curl") == []


@pytest.mark.parametrize(
    "secret",
    ["PLACEHOLDER-set-by-terraform", "Short0123456789", "a" * 31, ORIGIN_SECRET + "!"],
)
def test_fetch_edge_rejects_a_placeholder_or_short_secret(host: Host, secret: str) -> None:
    result = host.run(FETCH, "edge", STUB_ORIGIN_SECRET=secret)

    assert result.returncode == 1
    assert "origin secret is not set" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert not (host.run_dir / "env").exists()


@pytest.mark.parametrize(
    ("role", "failing", "named"),
    [
        ("ui", "describe-user-pool-client", "aws cognito-idp describe-user-pool-client failed"),
        ("ui", "get-parameter", "aws ssm get-parameter /fse/image_tag failed"),
        ("edge", "--with-decryption", "aws ssm get-parameter /fse/ui/origin_secret failed"),
    ],
)
def test_fetch_names_only_the_failed_command(
    host: Host, role: str, failing: str, named: str
) -> None:
    result = host.run(
        FETCH,
        role,
        STUB_AWS_FAIL=failing,
        STUB_CLIENT_SECRET=CLIENT_SECRET,
        STUB_ORIGIN_SECRET=ORIGIN_SECRET,
    )

    assert result.returncode == 1
    assert named in result.stderr
    assert "stub-stderr-marker" not in result.stderr + result.stdout
    assert not (host.run_dir / "env").exists()


def test_fetch_rejects_an_unknown_role(host: Host) -> None:
    result = host.run(FETCH, "runner")

    assert result.returncode == 2
    assert host.calls() == []


# ---------------------------------------------------------------- fse-runner-mode.sh

PHASE1_ENV = ["PROJECTX_API_KEY=px=with=equals", "SKYLIT_API_KEY=sk-test-123"]


def _params(**values: str) -> str:
    return json.dumps(
        {
            "Parameters": [
                {"Name": f"/fse/{k}", "Value": v, "Type": "String"} for k, v in values.items()
            ]
        }
    )


@pytest.fixture
def runner_env(host: Host) -> Path:
    host.run_dir.mkdir()
    env_file = host.run_dir / "env"
    env_file.write_text("\n".join(PHASE1_ENV) + "\n")
    env_file.chmod(0o600)
    return env_file


def test_runner_mode_defaults_when_parameters_are_missing(host: Host, runner_env: Path) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_params())

    assert result.returncode == 0, result.stderr
    assert _env_lines(runner_env) == [
        *PHASE1_ENV,
        "FSE_RUNNER_MODE=run",
        "FSE_RUNNER_CONFIG=configs/runner.yaml",
    ]
    (call,) = host.calls("aws")
    assert call[:2] == ["ssm", "get-parameters"]
    assert call[call.index("--names") + 1 : call.index("--names") + 4] == [
        "/fse/runner_mode",
        "/fse/runner_config",
        "/fse/control/plugin_orders",
    ]


def test_runner_mode_accepts_paper(host: Host, runner_env: Path) -> None:
    config = "/home/fse/.skylit-fse/configs/paper-es.yaml"
    result = host.run(
        RUNNER_MODE, STUB_RUNNER_PARAMS=_params(runner_mode="paper", runner_config=config)
    )

    assert result.returncode == 0, result.stderr
    assert _env_lines(runner_env)[-2:] == ["FSE_RUNNER_MODE=paper", f"FSE_RUNNER_CONFIG={config}"]


@pytest.mark.parametrize(
    "params",
    [
        {"runner_mode": "live"},
        {"runner_mode": "run", "runner_config": "configs/../../etc/passwd"},
        {"runner_mode": "run", "runner_config": ".."},
        {"runner_mode": "run", "runner_config": "configs/runner yaml"},
        {"runner_mode": "run", "runner_config": "c" * 301},
        {"runner_mode": "run\npaper"},
    ],
    ids=["live", "dotdot", "bare-dotdot", "space", "too-long", "newline"],
)
def test_runner_mode_rejects_invalid_values(
    host: Host, runner_env: Path, params: dict[str, str]
) -> None:
    before = runner_env.read_text()
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_params(**params))

    assert result.returncode == 1
    assert runner_env.read_text() == before
    assert not list(host.run_dir.glob("*.tmp"))


def test_runner_mode_rewrites_the_env_atomically(host: Host, runner_env: Path) -> None:
    inode = runner_env.stat().st_ino
    # A hard link keeps the original inode allocated: on ext4 (CI) a freed inode is
    # reused at once, so without it a rename could land on the same number.
    keep = runner_env.with_name(runner_env.name + ".keep")
    os.link(runner_env, keep)
    host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_params(runner_mode="paper"))
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_params(runner_mode="run"))

    assert result.returncode == 0, result.stderr
    assert runner_env.stat().st_ino != inode  # replaced by rename, not edited in place
    assert keep.stat().st_ino == inode  # the original file was never rewritten
    keep.unlink()
    assert _mode(runner_env) == 0o600
    assert _env_lines(runner_env) == [
        *PHASE1_ENV,
        "FSE_RUNNER_MODE=run",
        "FSE_RUNNER_CONFIG=configs/runner.yaml",
    ]
    assert sorted(p.name for p in host.run_dir.iterdir()) == ["env"]


def test_runner_mode_fails_without_the_phase1_env(host: Host) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_params())

    assert result.returncode == 1
    assert host.calls("aws") == []


def test_runner_mode_fails_when_ssm_fails(host: Host, runner_env: Path) -> None:
    before = runner_env.read_text()
    result = host.run(RUNNER_MODE, STUB_AWS_FAIL="get-parameters")

    assert result.returncode == 1
    assert "aws ssm get-parameters failed" in result.stderr
    assert runner_env.read_text() == before


# ------------------------------------------------- plugin-orders switch (control file)

SWITCH_OFF = "fse-runner-mode: plugin_orders is not enabled|disabled; plugin orders off"


def _switch(value: str, **values: str) -> str:
    return _params(**values, **{"control/plugin_orders": value})


def _control(host: Host) -> str:
    path = host.control_dir / "plugin_orders"
    assert _mode(host.control_dir) == 0o755
    assert _mode(path) == 0o644
    assert sorted(p.name for p in host.control_dir.iterdir()) == ["plugin_orders"]
    return path.read_text()


def test_runner_mode_writes_disabled_when_the_switch_is_missing(
    host: Host, runner_env: Path
) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_params())

    assert result.returncode == 0, result.stderr
    assert _control(host) == "disabled\n"
    assert result.stderr == ""


def test_runner_mode_writes_disabled_for_disabled(host: Host, runner_env: Path) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_switch("disabled"))

    assert result.returncode == 0, result.stderr
    assert _control(host) == "disabled\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    "value",
    ["ENABLED", "true", "enabled ", "", "enabled\nx"],
    ids=["upper", "true", "trailing-space", "empty", "newline"],
)
def test_runner_mode_writes_disabled_and_warns_for_other_values(
    host: Host, runner_env: Path, value: str
) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_switch(value, runner_mode="run"))

    assert result.returncode == 0, result.stderr
    assert _control(host) == "disabled\n"
    assert SWITCH_OFF in result.stderr
    assert _env_lines(runner_env)[-2:] == [
        "FSE_RUNNER_MODE=run",
        "FSE_RUNNER_CONFIG=configs/runner.yaml",
    ]


def test_runner_mode_writes_enabled_only_for_enabled(host: Host, runner_env: Path) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_switch("enabled"))

    assert result.returncode == 0, result.stderr
    assert _control(host) == "enabled\n"
    assert result.stderr == ""


def test_runner_mode_strips_the_plugin_orders_secret_line(host: Host, runner_env: Path) -> None:
    runner_env.write_text(
        "\n".join([PHASE1_ENV[0], "FSE_RUN_PLUGIN_ORDERS=enabled", PHASE1_ENV[1]]) + "\n"
    )
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_switch("enabled"))

    assert result.returncode == 0, result.stderr
    assert _env_lines(runner_env) == [
        *PHASE1_ENV,
        "FSE_RUNNER_MODE=run",
        "FSE_RUNNER_CONFIG=configs/runner.yaml",
    ]


def test_runner_mode_reads_the_switch_under_the_ssm_prefix(host: Host, runner_env: Path) -> None:
    params = json.dumps(
        {
            "Parameters": [
                {"Name": "/other/control/plugin_orders", "Value": "enabled", "Type": "String"},
                {"Name": "/fse/control/plugin_orders", "Value": "disabled", "Type": "String"},
            ]
        }
    )
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=params, FSE_SSM_PREFIX="/other")

    assert result.returncode == 0, result.stderr
    assert _control(host) == "enabled\n"
    (call,) = host.calls("aws")
    assert "/other/control/plugin_orders" in call


def test_runner_mode_switch_is_idempotent_and_follows_the_parameter(
    host: Host, runner_env: Path
) -> None:
    for value, expected in [
        ("enabled", "enabled\n"),
        ("enabled", "enabled\n"),
        ("disabled", "disabled\n"),
    ]:
        result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_switch(value))
        assert result.returncode == 0, result.stderr
        assert _control(host) == expected
    assert _env_lines(runner_env) == [
        *PHASE1_ENV,
        "FSE_RUNNER_MODE=run",
        "FSE_RUNNER_CONFIG=configs/runner.yaml",
    ]


def test_runner_mode_invalid_mode_writes_no_control_file(host: Host, runner_env: Path) -> None:
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=_switch("enabled", runner_mode="live"))

    assert result.returncode == 1
    assert not host.control_dir.exists()


_STUB_FSE = """#!{python}
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    flag = os.environ.get("FSE_RUN_PLUGIN_ORDERS", "<unset>")
    fh.write(json.dumps(["fse", flag, *sys.argv[1:]]) + "\\n")
"""


@pytest.mark.parametrize("keep_secret_line", [False, True], ids=["stripped", "secret-kept"])
@pytest.mark.parametrize("switch", ["missing", "disabled", "garbage"])
def test_a_plugin_orders_secret_never_enables_orders_while_the_switch_is_off(
    host: Host, runner_env: Path, tmp_path: Path, switch: str, keep_secret_line: bool
) -> None:
    # /fse/secrets/FSE_RUN_PLUGIN_ORDERS=enabled reaches the env file through
    # fse-fetch-secrets.sh (left unchanged: plan 1.4 deviation). fse-runner-mode.sh
    # strips it and writes the control file; the entrypoint ignores the variable
    # either way ("secret-kept" forces it back into the container env).
    runner_env.write_text("\n".join([*PHASE1_ENV, "FSE_RUN_PLUGIN_ORDERS=enabled"]) + "\n")
    params = {
        "missing": _params(runner_mode="run"),
        "disabled": _switch("disabled", runner_mode="run"),
        "garbage": _switch("ENABLED", runner_mode="run"),
    }[switch]
    result = host.run(RUNNER_MODE, STUB_RUNNER_PARAMS=params)
    assert result.returncode == 0, result.stderr
    assert _control(host) == "disabled\n"

    container_env = dict(line.split("=", 1) for line in _env_lines(runner_env))
    assert "FSE_RUN_PLUGIN_ORDERS" not in container_env
    if keep_secret_line:
        container_env["FSE_RUN_PLUGIN_ORDERS"] = "enabled"
    fse = host.bin / "fse"
    fse.write_text(_STUB_FSE.format(python=sys.executable))
    fse.chmod(0o755)
    entrypoint = tmp_path / "entrypoint.sh"
    source = (UI_HOST.parents[2] / "docker" / "entrypoint.sh").read_text()
    entrypoint.write_text(
        source.replace("/run/fse-control/plugin_orders", str(host.control_dir / "plugin_orders"))
    )
    home = tmp_path / "home"
    home.mkdir()
    run = subprocess.run(
        [BASH, str(entrypoint), "runner"],
        env=host.env(
            **container_env,
            HOME=str(home),
            FSE_DATA_DIR=str(tmp_path / "data"),
            FSE_LOCAL_ROOT=str(home / ".skylit-fse"),
            FSE_FAULT_BACKOFF_S="0",
            FSE_LOCK_WAIT_S="1",
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    (call,) = host.calls("fse")
    assert call[:2] == ["<unset>", "run"]
    assert "--plugin-orders" not in call
    assert "--live-orders" not in call


def test_console_start_is_phase1_start_plus_the_read_only_control_mount(host: Host) -> None:
    host.run_dir.mkdir()
    (host.run_dir / "image_tag").write_text("abc123\n")
    repo = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse"
    phase1 = host.run(PHASE1_START, FSE_IMAGE_REPO=repo)
    assert phase1.returncode == 0, phase1.stderr
    phase1_argv = _docker_run(host)
    host.log.unlink()
    console = host.run(CONSOLE_START, FSE_IMAGE_REPO=repo)
    assert console.returncode == 0, console.stderr
    console_argv = _docker_run(host)

    mount = ["-v", f"{host.control_dir}:/run/fse-control:ro"]
    at = phase1_argv.index("/data:/data") + 1
    assert console_argv == [*phase1_argv[:at], *mount, *phase1_argv[at:]]
    assert phase1_argv[-2:] == [f"{repo}:abc123", "runner"]
    assert sum(1 for a in console_argv if "/run/fse-control" in a) == 1


def test_console_start_mounts_run_fse_control_by_default() -> None:
    text = CONSOLE_START.read_text()
    assert 'FSE_CONTROL_DIR="${FSE_CONTROL_DIR:-/run/fse-control}"' in text
    assert '-v "$FSE_CONTROL_DIR:/run/fse-control:ro"' in text


# --------------------------------------------------------------- fse-runner-state.sh


def _state(host: Host) -> dict[str, object]:
    path = host.host_dir / "runner-state.json"
    assert _mode(path) == 0o644
    return json.loads(path.read_text())


def test_runner_state_started_writes_running_and_clears_the_hold(host: Host) -> None:
    host.host_dir.mkdir()
    (host.host_dir / "runner-held").touch()
    before = int(time.time())
    result = host.run(RUNNER_STATE, "started")

    assert result.returncode == 0, result.stderr
    state = _state(host)
    assert state["state"] == "running"
    assert isinstance(state["at"], int)
    assert before <= state["at"] <= int(time.time()) + 1
    assert state["exit_status"] == ""
    assert state["result"] == ""
    assert not (host.host_dir / "runner-held").exists()
    assert not (host.host_dir / "runner-state.json.tmp").exists()


def test_runner_state_stopped_keeps_the_hold_and_records_the_exit(host: Host) -> None:
    host.host_dir.mkdir()
    (host.host_dir / "runner-held").touch()
    result = host.run(RUNNER_STATE, "stopped", EXIT_STATUS="3", SERVICE_RESULT="exit-code")

    assert result.returncode == 0, result.stderr
    assert {k: v for k, v in _state(host).items() if k != "at"} == {
        "state": "stopped",
        "exit_status": "3",
        "result": "exit-code",
    }
    assert (host.host_dir / "runner-held").exists()


@pytest.mark.parametrize(
    "value",
    ['3", "state": "running', "x" * 33, "a b", "1\n2", "$(id)"],
    ids=["quote", "too-long", "space", "newline", "subst"],
)
def test_runner_state_sanitizes_systemd_values(host: Host, value: str) -> None:
    result = host.run(RUNNER_STATE, "stopped", EXIT_STATUS=value, SERVICE_RESULT=value)

    assert result.returncode == 0, result.stderr
    state = _state(host)
    assert state["state"] == "stopped"
    assert state["exit_status"] == ""
    assert state["result"] == ""


def test_runner_state_rejects_an_unknown_event(host: Host) -> None:
    result = host.run(RUNNER_STATE, "crashed")

    assert result.returncode == 2
    assert not (host.host_dir / "runner-state.json").exists()


# ---------------------------------------------------------------------- bash 3.2


@pytest.mark.parametrize("path", sorted(FILES.glob("*.sh")), ids=lambda p: p.name)
def test_scripts_avoid_bash4_features(path: Path) -> None:
    text = path.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    for pattern in (
        r"\bwait -n\b",
        r"\bdeclare -A\b",
        r"\bmapfile\b",
        r"\breadarray\b",
        r"\$\{\w+,,\}",
        r"\$\{\w+\^\^\}",
    ):
        assert not re.search(pattern, text), (path.name, pattern)


# ------------------------------------------------------------------ cache sync


def _sync(host: Host, **extra: str) -> subprocess.CompletedProcess[str]:
    return host.run(
        SYNC_CACHE,
        FSE_CACHE_DIR=str(host.tmp / "data" / "cache"),
        FSE_CACHE_OWNER="",
        FSE_LOCK_FILE=str(host.tmp / "sync.lock"),
        **extra,
    )


def _aws(host: Host, *prefix: str) -> list[list[str]]:
    return [c for c in host.calls("aws") if c[: len(prefix)] == list(prefix)]


BARS_PREFIXES = ["bars", "equity_bars", "derived", "darkpool", "vix"]


def test_cache_sync_copies_one_prefix_at_a_time_then_the_catalog(host: Host) -> None:
    result = _sync(host, FSE_DATA_BUCKET="skylit-test-bucket")

    assert result.returncode == 0, result.stderr
    cache = host.tmp / "data" / "cache"
    syncs = _aws(host, "s3", "sync")
    assert [c[2:4] for c in syncs] == [
        [f"s3://skylit-test-bucket/cache/{p}/", f"{cache}/{p}/"] for p in BARS_PREFIXES
    ]
    # The catalog comes last, so it never lists a session whose file has not arrived.
    kinds = [c[0] for c in host.calls("aws")]
    assert kinds == ["s3"] * 5 + ["s3api", "s3"]
    assert _aws(host, "s3", "cp")[0][2] == "s3://skylit-test-bucket/cache/catalog.sqlite"
    assert (cache / "catalog.sqlite").read_text() == "catalog"
    assert not (cache / ".catalog.sqlite.part").exists()


def test_cache_sync_never_lists_all_of_cache_and_never_touches_heatmaps(host: Host) -> None:
    assert _sync(host, FSE_DATA_BUCKET="skylit-test-bucket").returncode == 0

    for call in host.calls("aws"):
        joined = " ".join(call)
        assert "--delete" not in call
        assert "heatmaps" not in joined
        assert "plugin_books" not in joined
        if call[:2] == ["s3", "sync"]:
            assert call[2] != "s3://skylit-test-bucket/cache/"  # a prefix, not the 167k objects


def test_cache_sync_fetches_the_catalog_only_when_its_etag_changed(host: Host) -> None:
    assert _sync(host, FSE_DATA_BUCKET="skylit-test-bucket").returncode == 0
    assert len(_aws(host, "s3", "cp")) == 1

    assert _sync(host, FSE_DATA_BUCKET="skylit-test-bucket").returncode == 0
    assert len(_aws(host, "s3", "cp")) == 1  # same ETag: not downloaded again

    result = _sync(host, FSE_DATA_BUCKET="skylit-test-bucket", STUB_CATALOG_ETAG='"etag-2"')
    assert result.returncode == 0
    assert len(_aws(host, "s3", "cp")) == 2


def test_cache_sync_refetches_a_missing_catalog_even_with_the_same_etag(host: Host) -> None:
    assert _sync(host, FSE_DATA_BUCKET="skylit-test-bucket").returncode == 0
    (host.tmp / "data" / "cache" / "catalog.sqlite").unlink()

    assert _sync(host, FSE_DATA_BUCKET="skylit-test-bucket").returncode == 0
    assert len(_aws(host, "s3", "cp")) == 2


def test_cache_sync_reads_the_bucket_from_ui_env(host: Host) -> None:
    env_file = host.tmp / "ui.env"
    env_file.write_text("FSE_UI_ENV=aws\nFSE_UI_DATA_BUCKET=skylit-from-env\n")

    result = _sync(host, FSE_UI_ENV_FILE=str(env_file))

    assert result.returncode == 0, result.stderr
    assert _aws(host, "s3", "sync")[0][2] == "s3://skylit-from-env/cache/bars/"


def test_cache_sync_without_a_bucket_exits_1(host: Host) -> None:
    result = _sync(host, FSE_UI_ENV_FILE=str(host.tmp / "missing.env"))

    assert result.returncode == 1
    assert "no data bucket" in result.stdout
    assert host.calls("aws") == []


def test_cache_sync_rejects_a_bucket_that_is_not_a_bucket_name(host: Host) -> None:
    result = _sync(host, FSE_DATA_BUCKET="bad bucket; rm -rf /")

    assert result.returncode == 1
    assert host.calls("aws") == []


def test_cache_sync_failure_exits_1_and_leaves_the_catalog_alone(host: Host) -> None:
    result = _sync(host, FSE_DATA_BUCKET="skylit-test-bucket", STUB_AWS_FAIL="sync")

    assert result.returncode == 1
    assert "sync from s3://skylit-test-bucket/cache/ failed" in result.stdout
    assert _aws(host, "s3", "cp") == []  # no catalog download after a failed data sync
    assert not (host.tmp / "data" / "cache" / "catalog.sqlite").exists()


def test_cache_sync_catalog_failure_keeps_the_old_catalog_and_retries(host: Host) -> None:
    assert _sync(host, FSE_DATA_BUCKET="skylit-test-bucket").returncode == 0
    old = (host.tmp / "data" / "cache" / "catalog.sqlite").read_text()

    result = _sync(
        host, FSE_DATA_BUCKET="skylit-test-bucket", STUB_CATALOG_ETAG='"etag-2"', STUB_AWS_FAIL="cp"
    )

    assert result.returncode == 1
    assert (host.tmp / "data" / "cache" / "catalog.sqlite").read_text() == old
    assert not (host.tmp / "data" / "cache" / ".catalog.sqlite.part").exists()
    # The ETag was not recorded, so the next run tries the new catalog again.
    assert (
        _sync(host, FSE_DATA_BUCKET="skylit-test-bucket", STUB_CATALOG_ETAG='"etag-2"').returncode
        == 0
    )
    assert len(_aws(host, "s3", "cp")) == 3


def test_runner_drop_in_syncs_the_cache_after_the_mode_and_never_blocks_start() -> None:
    lines = (FILES / "20-console.conf").read_text().splitlines()
    pre = [ln for ln in lines if ln.startswith("ExecStartPre")]

    assert pre == [
        "ExecStartPre=/usr/local/bin/fse-runner-mode.sh",
        # "-": a failed sync never stops the runner
        "ExecStartPre=-/usr/local/bin/fse-sync-cache.sh",
    ]


def test_cache_sync_timer_runs_every_half_hour_before_the_globex_open() -> None:
    timer = (FILES / "fse-cache-sync.timer").read_text()
    service = (FILES / "fse-cache-sync.service").read_text()

    assert "OnCalendar=*:00,30" in timer  # 17:30 ET is the last run before the 18:00 ET open
    assert "Persistent=true" in timer
    assert "Unit=fse-cache-sync.service" in timer
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/local/bin/fse-sync-cache.sh" in service
    assert "TimeoutStartSec=700" in service  # > 2 x FSE_SYNC_TIMEOUT_S (300) + chown


# ------------------------------------------------------------ recordings offload

TODAY = "2026-10-12"
OLD = time.time() - 2 * 3600  # older than FSE_OFFLOAD_MIN_AGE_MIN (30)


def _recordings(host: Host, *names: str, mtime: float = OLD) -> Path:
    rec = host.tmp / "data" / "recordings" / "dataplane"
    rec.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = rec / name
        path.write_bytes(name.encode() * 3)
        os.utime(path, (mtime, mtime))
    return rec


def _offload(host: Host, **extra: str) -> subprocess.CompletedProcess[str]:
    store = host.tmp / "s3"
    store.mkdir(exist_ok=True)
    env = {
        "FSE_DATA_BUCKET": "skylit-test-bucket",
        "FSE_RECORDINGS_DIR": str(host.tmp / "data" / "recordings" / "dataplane"),
        "FSE_LOCK_FILE": str(host.tmp / "offload.lock"),
        "FSE_TODAY": TODAY,
        "FSE_DISK_USE_PCT": "10",
        "STUB_S3_DIR": str(store),
    }
    env.update(extra)
    return host.run(OFFLOAD, **env)


def _in_s3(host: Host) -> set[str]:
    store = host.tmp / "s3"
    return {p.name.removeprefix("recordings__dataplane__") for p in store.iterdir()}


def _local(rec: Path) -> set[str]:
    return {p.name for p in rec.iterdir()}


def test_offload_uploads_closed_days_and_verifies_them(host: Host) -> None:
    rec = _recordings(
        host, "2026-10-11.jsonl.gz", "2026-10-11.quotes.jsonl.gz", f"{TODAY}.jsonl.gz"
    )
    result = _offload(host)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _in_s3(host) == {"2026-10-11.jsonl.gz", "2026-10-11.quotes.jsonl.gz"}
    assert (host.tmp / "s3" / "recordings__dataplane__2026-10-11.jsonl.gz").read_bytes() == (
        rec / "2026-10-11.jsonl.gz"
    ).read_bytes()
    assert _local(rec) == {
        "2026-10-11.jsonl.gz",
        "2026-10-11.quotes.jsonl.gz",
        f"{TODAY}.jsonl.gz",
    }  # within the keep window
    cp = _aws(host, "s3", "cp")
    assert [c[3] for c in cp] == [
        "s3://skylit-test-bucket/recordings/dataplane/2026-10-11.jsonl.gz",
        "s3://skylit-test-bucket/recordings/dataplane/2026-10-11.quotes.jsonl.gz",
    ]
    heads = _aws(host, "s3api", "head-object")
    assert len(heads) == 4  # before and after each upload
    assert all(c[c.index("--query") + 1] == "ContentLength" for c in heads)
    assert "uploaded 2026-10-11.jsonl.gz" in result.stdout


def test_offload_deletes_only_days_beyond_the_keep_window(host: Host) -> None:
    days = ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"]
    rec = _recordings(host, *[f"{d}.jsonl.gz" for d in days], f"{TODAY}.jsonl.gz")
    result = _offload(host)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _local(rec) == {"2026-10-10.jsonl.gz", "2026-10-11.jsonl.gz", f"{TODAY}.jsonl.gz"}
    assert _in_s3(host) == {f"{d}.jsonl.gz" for d in days}
    assert _aws(host, "s3", "rm") == []
    assert _aws(host, "s3api", "delete-object") == []


def test_offload_keeps_today_and_a_recently_written_file(host: Host) -> None:
    rec = _recordings(host, f"{TODAY}.jsonl.gz", "2026-10-13.jsonl.gz")
    _recordings(host, "2026-10-09.jsonl.gz", mtime=time.time())
    result = _offload(host, FSE_RECORDINGS_KEEP_DAYS="0", FSE_DISK_USE_PCT="99")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _local(rec) == {f"{TODAY}.jsonl.gz", "2026-10-13.jsonl.gz", "2026-10-09.jsonl.gz"}
    assert _in_s3(host) == set()
    assert "skip 2026-10-09.jsonl.gz: written in the last 30 min" in result.stdout
    assert "keep 2026-10-09.jsonl.gz: not verified in s3" in result.stdout


def test_offload_never_deletes_when_the_verify_mismatches(host: Host) -> None:
    rec = _recordings(host, "2026-10-01.jsonl.gz", "2026-10-02.jsonl.gz")
    result = _offload(host, FSE_RECORDINGS_KEEP_DAYS="0", STUB_HEAD_LENGTH="1")

    assert result.returncode == 1
    assert _local(rec) == {"2026-10-01.jsonl.gz", "2026-10-02.jsonl.gz"}
    assert "verify of 2026-10-01.jsonl.gz failed" in result.stdout
    assert "2 file(s) not offloaded" in result.stdout


def test_offload_failed_upload_keeps_the_file_and_exits_1(host: Host) -> None:
    rec = _recordings(host, "2026-10-01.jsonl.gz")
    result = _offload(host, FSE_RECORDINGS_KEEP_DAYS="0", STUB_AWS_FAIL="cp")

    assert result.returncode == 1
    assert _local(rec) == {"2026-10-01.jsonl.gz"}
    assert "upload of 2026-10-01.jsonl.gz failed" in result.stdout


def test_offload_disk_threshold_prunes_kept_days_oldest_first(host: Host) -> None:
    rec = _recordings(host, "2026-10-10.jsonl.gz", "2026-10-11.jsonl.gz", f"{TODAY}.jsonl.gz")
    result = _offload(host, FSE_DISK_USE_PCT="90")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _local(rec) == {f"{TODAY}.jsonl.gz"}
    out = result.stdout
    assert out.index("deleted 2026-10-10.jsonl.gz") < out.index("deleted 2026-10-11.jsonl.gz")
    assert "disk use 90% > 70%" in out


def test_offload_below_the_disk_threshold_keeps_the_kept_days(host: Host) -> None:
    rec = _recordings(host, "2026-10-10.jsonl.gz", "2026-10-11.jsonl.gz")
    result = _offload(host, FSE_DISK_USE_PCT="70")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _local(rec) == {"2026-10-10.jsonl.gz", "2026-10-11.jsonl.gz"}


def test_offload_skips_the_upload_when_s3_has_the_same_size(host: Host) -> None:
    rec = _recordings(host, "2026-10-01.jsonl.gz")
    store = host.tmp / "s3"
    store.mkdir()
    (store / "recordings__dataplane__2026-10-01.jsonl.gz").write_bytes(
        (rec / "2026-10-01.jsonl.gz").read_bytes()
    )
    result = _offload(host)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _aws(host, "s3", "cp") == []
    assert "skip upload of 2026-10-01.jsonl.gz: in s3 with the same size" in result.stdout
    assert _local(rec) == {"2026-10-01.jsonl.gz"}  # 1 closed day, within the keep window


def test_offload_without_a_bucket_exits_1(host: Host) -> None:
    rec = _recordings(host, "2026-10-01.jsonl.gz")
    result = _offload(host, FSE_DATA_BUCKET="", FSE_UI_ENV_FILE=str(host.tmp / "missing.env"))

    assert result.returncode == 1
    assert "no data bucket" in result.stdout
    assert host.calls("aws") == []
    assert _local(rec) == {"2026-10-01.jsonl.gz"}


def test_offload_reads_the_bucket_from_ui_env(host: Host) -> None:
    _recordings(host, "2026-10-01.jsonl.gz")
    env_file = host.tmp / "ui.env"
    env_file.write_text("FSE_UI_DATA_BUCKET=skylit-from-env\n")
    result = _offload(host, FSE_DATA_BUCKET="", FSE_UI_ENV_FILE=str(env_file))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _aws(host, "s3", "cp")[0][3].startswith("s3://skylit-from-env/recordings/dataplane/")


def test_offload_handles_part_files_and_ignores_other_names(host: Host) -> None:
    rec = _recordings(
        host,
        "2026-10-01.part1.jsonl.gz",
        "2026-10-01.jsonl.gz",
        "notes.jsonl.gz",
        "2026-10-01.jsonl",
    )
    result = _offload(host, FSE_RECORDINGS_KEEP_DAYS="0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _in_s3(host) == {"2026-10-01.part1.jsonl.gz", "2026-10-01.jsonl.gz"}
    assert _local(rec) == {"notes.jsonl.gz", "2026-10-01.jsonl"}


def test_offload_timer_runs_hourly_and_the_unit_runs_the_script() -> None:
    timer = (FILES / "fse-offload-recordings.timer").read_text()
    service = (FILES / "fse-offload-recordings.service").read_text()

    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer
    assert "Unit=fse-offload-recordings.service" in timer
    assert "WantedBy=timers.target" in timer
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/local/bin/fse-offload-recordings.sh" in service
    assert "RequiresMountsFor=/data" in service
    assert "TimeoutStartSec=3700" in service  # > FSE_SYNC_TIMEOUT_S (3600)
