"""Runner host scripts (infra/modules/runner/files) with a stub ``aws`` on PATH.

The stub records one JSON line per call and answers ``ssm get-parameters-by-path``,
``ssm get-parameter`` and ``sns publish`` from environment variables. The scripts
take FSE_ENV_DIR / FSE_STAMP_DIR overrides, so nothing touches /run or AWS.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

FILES = Path(__file__).resolve().parents[2] / "infra" / "modules" / "runner" / "files"
FETCH = FILES / "fse-fetch-secrets.sh"
ALERT = FILES / "fse-alert.sh"
TOPIC = "arn:aws:sns:us-east-1:123456789012:fse-alerts"

_STUB_AWS = """#!{python}
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("STUB_AWS_EXIT"):
    sys.exit(int(os.environ["STUB_AWS_EXIT"]))
if "get-parameters-by-path" in sys.argv:
    print(os.environ["STUB_PARAMS"])
elif "get-parameter" in sys.argv:
    print(os.environ.get("STUB_TAG", "v1"))
"""

_STUB_SYSTEMCTL = "#!/bin/sh\necho 3\n"


def _param(name: str, value: str) -> dict[str, str]:
    return {"Name": f"/fse/secrets/{name}", "Value": value, "Type": "SecureString"}


DEFAULT_PARAMS = [
    _param("SKYLIT_API_KEY", "sk-test-123"),
    _param("PROJECTX_USERNAME", "PLACEHOLDER-set-by-hand"),
    _param("PROJECTX_API_KEY", "px=with=equals"),
]


class Host:
    """A temporary PATH with stub aws/systemctl and the /run/fse stand-in."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (self.bin / "aws").write_text(_STUB_AWS.format(python=sys.executable))
        (self.bin / "systemctl").write_text(_STUB_SYSTEMCTL)
        (self.bin / "python3").symlink_to(sys.executable)
        for name in ("aws", "systemctl"):
            (self.bin / name).chmod(0o755)
        self.run_dir = tmp_path / "run-fse"
        self.log = tmp_path / "aws-calls.jsonl"

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("EXIT_STATUS", None)
        env.pop("EXIT_CODE", None)
        env.update(
            PATH=f"{self.bin}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}",
            STUB_LOG=str(self.log),
            STUB_PARAMS=json.dumps({"Parameters": DEFAULT_PARAMS}),
            FSE_ENV_DIR=str(self.run_dir),
            FSE_STAMP_DIR=str(self.run_dir),
            FSE_ALERT_TOPIC_ARN=TOPIC,
            AWS_REGION="us-east-1",
        )
        env.update(extra)
        return subprocess.run(
            ["bash", str(script), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def publishes(self) -> list[dict[str, Any]]:
        out = []
        for argv in self.calls():
            if argv[:2] == ["sns", "publish"]:
                out.append({argv[i]: argv[i + 1] for i in range(2, len(argv) - 1, 2)})
        return out


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


# ------------------------------------------------------------ fse-fetch-secrets.sh


def test_fetch_writes_private_env_with_placeholders_blank(host: Host) -> None:
    result = host.run(FETCH)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v1"
    env_file = host.run_dir / "env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert env_file.read_text().splitlines() == [
        "PROJECTX_API_KEY=px=with=equals",
        "PROJECTX_USERNAME=",
        "SKYLIT_API_KEY=sk-test-123",
    ]
    assert (host.run_dir / "image_tag").read_text() == "v1\n"
    assert not (host.run_dir / "not-deployed").exists()
    assert not (host.run_dir / "env.tmp").exists()
    by_path = host.calls()[0]
    assert by_path[:2] == ["ssm", "get-parameters-by-path"]
    assert "--with-decryption" in by_path
    assert by_path[by_path.index("--path") + 1] == "/fse/secrets/"


def test_fetch_exits_1_when_no_image_is_deployed(host: Host) -> None:
    result = host.run(FETCH, STUB_TAG="none")

    assert result.returncode == 1
    assert "image_tag is none" in result.stderr
    assert (host.run_dir / "not-deployed").exists()
    assert not (host.run_dir / "image_tag").exists()


def test_fetch_clears_not_deployed_after_a_deploy(host: Host) -> None:
    host.run(FETCH, STUB_TAG="none")
    result = host.run(FETCH, STUB_TAG="v2")

    assert result.returncode == 0, result.stderr
    assert not (host.run_dir / "not-deployed").exists()
    assert (host.run_dir / "image_tag").read_text() == "v2\n"


def test_fetch_rejects_multiline_value_without_printing_it(host: Host) -> None:
    params = json.dumps({"Parameters": [_param("FSE_SECRET_VARS", "line1\nsecret-line2")]})
    result = host.run(FETCH, STUB_PARAMS=params)

    assert result.returncode != 0
    assert "FSE_SECRET_VARS holds a newline" in result.stderr
    assert "secret-line2" not in result.stderr + result.stdout
    assert not (host.run_dir / "env").exists()
    assert not (host.run_dir / "env.tmp").exists()


def test_fetch_fails_when_ssm_fails(host: Host) -> None:
    result = host.run(FETCH, STUB_AWS_EXIT="255")

    assert result.returncode != 0
    assert not (host.run_dir / "env").exists()
    assert not (host.run_dir / "env.tmp").exists()


# ------------------------------------------------------------------- fse-alert.sh


def test_alert_publishes_once_per_code_per_hour(host: Host) -> None:
    first = host.run(ALERT, EXIT_STATUS="3")
    second = host.run(ALERT, EXIT_STATUS="3")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    publishes = host.publishes()
    assert len(publishes) == 1
    assert publishes[0]["--topic-arn"] == TOPIC
    assert publishes[0]["--message"] == "fse-runner exited 3 (blank credential or auth failure)"
    assert "already alerted" in second.stderr


def test_alert_dedupes_per_code(host: Host) -> None:
    host.run(ALERT, EXIT_STATUS="3")
    host.run(ALERT, EXIT_STATUS="1")

    messages = [p["--message"] for p in host.publishes()]
    assert messages == [
        "fse-runner exited 3 (blank credential or auth failure)",
        "fse-runner exited 1 (feed or plugin fault)",
    ]


def test_alert_publishes_again_after_an_hour(host: Host) -> None:
    host.run(ALERT, EXIT_STATUS="2")
    stamp = host.run_dir / "alert-exit-2.stamp"
    old = time.time() - 2 * 3600
    os.utime(stamp, (old, old))
    host.run(ALERT, EXIT_STATUS="2")

    assert len(host.publishes()) == 2


@pytest.mark.parametrize("extra", [{"EXIT_STATUS": "0"}, {}])
def test_alert_never_publishes_on_clean_exit(host: Host, extra: dict[str, str]) -> None:
    result = host.run(ALERT, **extra)

    assert result.returncode == 0, result.stderr
    assert host.calls() == []


@pytest.mark.parametrize("signal_name", ["TERM", "INT"])
def test_alert_ignores_a_deliberate_stop(host: Host, signal_name: str) -> None:
    # systemctl restart (deploy) or a patch reboot: the TERM after ExecStop can kill
    # `docker run` before it returns fse's 0; systemd reports EXIT_CODE=killed.
    result = host.run(ALERT, EXIT_CODE="killed", EXIT_STATUS=signal_name)

    assert result.returncode == 0, result.stderr
    assert host.calls() == []


def test_alert_reports_a_crash_signal(host: Host) -> None:
    host.run(ALERT, EXIT_CODE="killed", EXIT_STATUS="KILL")

    assert host.publishes()[0]["--message"] == "fse-runner exited KILL (unexpected exit)"


def test_alert_says_not_deployed(host: Host) -> None:
    host.run_dir.mkdir()
    (host.run_dir / "not-deployed").touch()
    host.run(ALERT, EXIT_STATUS="1")

    assert host.publishes()[0]["--message"] == (
        "fse-runner not started: /fse/image_tag is none (no image deployed yet)"
    )


def test_alert_gave_up_reports_last_exit(host: Host) -> None:
    result = host.run(ALERT, "gave-up")

    assert result.returncode == 0, result.stderr
    message = host.publishes()[0]["--message"]
    assert message.startswith("fse-runner gave up after repeated failures; last exit 3.")
