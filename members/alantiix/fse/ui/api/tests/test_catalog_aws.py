"""The AWS placement table (design.md 10.9): placement, heavy, disabled reason, hidden flags."""

from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from fse_ui.catalog import (
    DRAFTS_AWS_REASON,
    LIVE_DISABLED_REASON,
    RUNNER_REASON,
    ArgvError,
    Catalog,
    build_args,
)
from tests.aws_support import Aws
from tests.conftest import PROJECT, Console

if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))  # the research packages


@pytest.fixture(scope="module")
def on_aws() -> Catalog:
    return Catalog(target="aws")


@pytest.fixture(scope="module")
def local() -> Catalog:
    return Catalog(target="local")


# (command, placement, heavy, disabled_reason) on AWS
AWS_TABLE: list[tuple[str, str, bool, str | None]] = [
    ("fse pull", "batch", True, None),
    ("fse import-vix", "batch", True, None),
    ("fse pull-equity-bars", "batch", True, None),
    ("fse experiment holdout", "batch", True, None),
    ("fse backtest", "batch", True, None),
    ("fse backtest-plugin", "batch", True, None),
    ("fse calibrate regime-min-abs", "batch", True, None),
    ("fse experiment ablation", "batch", True, None),
    ("fse experiment sweep", "batch", True, None),
    ("fse experiment montecarlo", "batch", True, None),
    ("fse experiment walkforward", "batch", True, None),
    ("fse experiment cadence", "batch", True, None),
    ("fse report", "batch", True, None),
    ("fse export-aws", "batch", True, None),
    ("fse accounts", "batch", False, None),
    ("research.example.run backtest", "batch", True, None),
    ("research.example.run host-diff", "local", True, None),
    ("research.example.check", "local", True, None),
    ("fse drafts build", "batch", True, DRAFTS_AWS_REASON),
    ("fse drafts promote", "local", False, DRAFTS_AWS_REASON),
    ("fse halt", "local", False, None),
    ("fse clear", "local", False, None),
    ("fse scan-secrets", "local", False, None),
    ("fse skilldocs check", "local", False, None),
    ("fse run", "runner", False, RUNNER_REASON),
    ("fse paper", "runner", False, RUNNER_REASON),
    ("fse live", "runner", False, LIVE_DISABLED_REASON),
]


@pytest.mark.parametrize(("command", "placement", "heavy", "disabled"), AWS_TABLE)
def test_aws_placement(
    on_aws: Catalog, command: str, placement: str, heavy: bool, disabled: str | None
) -> None:
    cmd = on_aws.get(command)
    assert (cmd.placement, cmd.heavy, cmd.disabled_reason) == (placement, heavy, disabled)


def test_every_research_command_but_the_host_ones_is_batch_on_aws(on_aws: Catalog) -> None:
    host_local = {"research.example.run host-diff", "research.example.check"}
    for cmd in on_aws.commands():
        if cmd.program != "fse":
            assert cmd.placement == ("local" if cmd.id in host_local else "batch"), cmd.id


def test_local_target_keeps_the_phase_a_placements(local: Catalog, on_aws: Catalog) -> None:
    assert on_aws.errors() == {} and local.errors() == {}
    assert local.get("research.example.run host-diff").placement == "batch"
    assert local.get("research.example.check").placement == "batch"
    assert local.get("fse drafts build").disabled_reason is None
    assert local.get("fse drafts promote").placement == "local"
    assert local.get("fse live").disabled_reason == LIVE_DISABLED_REASON
    default = Catalog()
    assert {c.id: (c.placement, c.heavy, c.disabled_reason) for c in default.commands()} == {
        c.id: (c.placement, c.heavy, c.disabled_reason) for c in local.commands()
    }


def _flags(catalog: Catalog, command: str) -> set[str]:
    return {a["flag"] for a in catalog.get(command).args}


def test_hidden_flags_on_aws_only(on_aws: Catalog, local: Catalog) -> None:
    assert "--holdout-log" not in _flags(on_aws, "fse experiment holdout")
    assert "--holdout-log" in _flags(local, "fse experiment holdout")
    for flag in ("--bucket", "--prefix", "--glue-database"):
        assert flag not in _flags(on_aws, "fse export-aws")
        assert flag in _flags(local, "fse export-aws")
    with pytest.raises(ArgvError, match="unknown argument"):
        build_args(on_aws.get("fse export-aws"), {"bucket": "elsewhere"})
    assert build_args(local.get("fse export-aws"), {"bucket": "elsewhere"}) == [
        "--bucket=elsewhere"
    ]


# ---------------------------------------------------------------- enforced by the API


@pytest.fixture
def aws_catalog_console(aws_console: Callable[..., Console], on_aws: Catalog) -> Console:
    # FSE_UI_ENV=aws needs OIDC, so the logged-in test console swaps in the AWS catalog.
    console = aws_console()
    console.app.state.catalog = on_aws
    return console


def test_drafts_build_is_refused_on_aws(aws_catalog_console: Console, aws: Aws) -> None:
    r = aws_catalog_console.post(
        "/api/jobs",
        json={
            "command": "fse drafts build",
            "values": {"chosen": "x", "start": "2026-10-06", "end": "2026-10-07"},
        },
    )
    assert r.status_code == 409 and r.json()["detail"] == DRAFTS_AWS_REASON
    listed = {c["id"]: c for c in aws_catalog_console.get("/api/catalog").json()["commands"]}
    assert listed["fse drafts build"]["disabled_reason"] == DRAFTS_AWS_REASON


def test_hidden_flag_is_422_on_aws(aws_catalog_console: Console, aws: Aws) -> None:
    r = aws_catalog_console.post(
        "/api/jobs", json={"command": "fse export-aws", "values": {"bucket": "elsewhere"}}
    )
    assert r.status_code == 422 and "unknown argument" in r.json()["detail"]
    listed = {c["id"]: c for c in aws_catalog_console.get("/api/catalog").json()["commands"]}
    assert "--bucket" not in {a["flag"] for a in listed["fse export-aws"]["args"]}


def test_host_local_research_runs_on_the_local_executor(
    aws_catalog_console: Console, aws: Aws
) -> None:
    r = aws_catalog_console.post(
        "/api/jobs",
        json={"command": "research.example.check", "values": {"date": "2026-10-09"}},
    )
    assert r.status_code == 201 and r.json()["executor"] == "local"


def test_pull_equity_bars_takes_the_pull_lock() -> None:
    from fse_ui.aws.ecs import PULL_LOCK_KEY, lock_key

    assert lock_key("fse", ("pull-equity-bars",)) == PULL_LOCK_KEY
