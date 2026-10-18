"""``fse account reset`` (paper-analytics design 3.7): exit codes 0 / 1 / 2 / 3.

Every run goes through ``fse.cli.run`` with a temporary Project folder, an
explicit empty environment and a temporary live-state directory.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from fse import cli
from fse.live.host.lock import RunnerLock


@dataclass(frozen=True)
class Outcome:
    code: int
    out: str
    err: str


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """A live-state dir holding account ``acct``: its state and a Nexus outbox."""
    root = tmp_path / "live-state"
    acct = root / "accounts" / "acct"
    acct.mkdir(parents=True)
    (acct / "account.json").write_text('{"version": 1}\n', encoding="utf-8")
    (acct / "nexus_outbox.jsonl").write_text("{}\n", encoding="utf-8")
    return root


def reset(project: Path, state_dir: Path, *args: str) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    argv = ["account", "reset", *args, "--state-dir", str(state_dir)]
    code = cli.run(argv, project_dir=project, environ={}, stdout=out, stderr=err)
    return Outcome(code, out.getvalue(), err.getvalue())


def test_success_archives_every_file_and_logs_the_reset(project: Path, state_dir: Path) -> None:
    result = reset(project, state_dir, "acct", "--reason", "breached in week 1")
    assert result.code == 0, result.err
    acct = state_dir / "accounts" / "acct"
    assert sorted(p.name for p in acct.iterdir()) == ["archive"]
    [archive] = (acct / "archive").iterdir()
    assert sorted(p.name for p in archive.iterdir()) == ["account.json", "nexus_outbox.jsonl"]
    assert (archive / "account.json").read_text(encoding="utf-8") == '{"version": 1}\n'
    [line] = [
        json.loads(ln) for ln in (state_dir / "accounts" / "resets.jsonl").read_text().splitlines()
    ]
    assert set(line) == {"kind", "account", "reason", "archived_to", "t_ns"}
    assert (line["kind"], line["account"], line["reason"]) == (
        "account_reset",
        "acct",
        "breached in week 1",
    )
    assert line["archived_to"] == str(archive)
    assert "starts it fresh" in result.out


def test_a_second_reset_archives_into_its_own_dir(project: Path, state_dir: Path) -> None:
    assert reset(project, state_dir, "acct", "--reason", "one").code == 0
    acct = state_dir / "accounts" / "acct"
    (acct / "account.json").write_text("{}\n", encoding="utf-8")
    assert reset(project, state_dir, "acct", "--reason", "two").code == 0
    archives = sorted((acct / "archive").iterdir())
    assert len(archives) == 2
    lines = (state_dir / "accounts" / "resets.jsonl").read_text().splitlines()
    assert [json.loads(ln)["reason"] for ln in lines] == ["one", "two"]


def test_runner_lock_held_exits_3_and_moves_nothing(project: Path, state_dir: Path) -> None:
    with RunnerLock(state_dir):
        result = reset(project, state_dir, "acct", "--reason", "x")
    assert result.code == 3
    assert "the runner is running; stop it before resetting account acct" in result.err
    assert (state_dir / "accounts" / "acct" / "account.json").exists()
    assert not (state_dir / "accounts" / "resets.jsonl").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("acct",), "--reason must be 1 to 200 characters"),
        (("acct", "--reason", ""), "--reason must be 1 to 200 characters"),
        (("acct", "--reason", "x" * 201), "--reason must be 1 to 200 characters"),
        (("implicit:p1", "--reason", "x"), "is not an account id"),
        (("Acct", "--reason", "x"), "is not an account id"),
        (("other", "--reason", "x"), "account 'other' has no state in"),
    ],
)
def test_invalid_input_exits_2(
    project: Path, state_dir: Path, args: tuple[str, ...], message: str
) -> None:
    result = reset(project, state_dir, *args)
    assert result.code == 2
    assert message in result.err
    assert (state_dir / "accounts" / "acct" / "account.json").exists()


def test_a_200_character_reason_is_accepted(project: Path, state_dir: Path) -> None:
    assert reset(project, state_dir, "acct", "--reason", "x" * 200).code == 0


def test_config_must_name_the_account(project: Path, state_dir: Path, tmp_path: Path) -> None:
    config = tmp_path / "runner.yaml"
    config.write_text(
        "version: 1\n"
        "plugins:\n"
        "  - id: p1\n"
        "    module: tests.fakes.plugins:RecordingPlugin\n"
        "    risk: {max_contracts: 1}\n"
        "    account: other\n"
        "accounts:\n"
        "  - {id: other, preset: none}\n",
        encoding="utf-8",
    )
    result = reset(project, state_dir, "acct", "--reason", "x", "--config", str(config))
    assert result.code == 2
    assert f"account 'acct' is not in {config}" in result.err
    config.write_text(config.read_text().replace("other", "acct"), encoding="utf-8")
    assert reset(project, state_dir, "acct", "--reason", "x", "--config", str(config)).code == 0


def test_an_archive_error_exits_1_and_names_the_file(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Path.replace

    def failing(self: Path, target: Path) -> Path:
        if self.name == "nexus_outbox.jsonl":
            raise OSError("read-only file system")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", failing)
    result = reset(project, state_dir, "acct", "--reason", "x")
    assert result.code == 1
    assert "nexus_outbox.jsonl" in result.err
    # the file moved before the failure stays archived; the next fse run starts fresh
    acct = state_dir / "accounts" / "acct"
    assert not (acct / "account.json").exists()
    assert not (state_dir / "accounts" / "resets.jsonl").exists()
