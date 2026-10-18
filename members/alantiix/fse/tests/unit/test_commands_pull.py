"""Unit tests for ``fse pull`` (``fse.commands.pull``): flags, defaults and early exits.

The pull itself is tested in ``test_data_puller.py`` and end to end in task
5.12. Here every run either stops before any network request or, for the size
limit, reaches only the free ``/v1/account`` and ``/v1/symbols`` mocks. The
key is fake; every directory is under ``tmp_path``. ``--config`` sets only
dark-pool fetching (task 20.4): the tickers follow ``--dark-pool``,
``--dark-pool-tickers`` and the ``dark_pool_confluence`` Gate, and a config
that does not load stops the pull before any request.

**Validates: Requirements 3.1, 3.2, 3.14, 4.12**
"""

from __future__ import annotations

import argparse
import io
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from fse import cli
from fse.commands import pull
from fse.commands._config import load_config
from fse.config.schema import StrategyConfig
from fse.data.cache import HeatmapView
from fse.data.planner import PullSpec
from fse.data.puller import PullOptions
from fse.logio import LogWriter, Redactor
from fse.skylit import endpoints as ep
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME
from tests.fakes.configs import MINIMAL_CONFIG_YAML, minimal_config_data

KEY = "fake-skylit-key-0000"
CALENDARS = Path(__file__).resolve().parents[2] / "calendars"
RANGE = ["--start", "2026-03-02", "--end", "2026-03-03"]


def run(args: list[str], tmp_path: Path, environ: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        [
            "pull",
            *args,
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(tmp_path / "out"),
        ],
        project_dir=None,
        environ=environ,
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def parse(*args: str) -> PullOptions:
    return pull.options_from_args(cli.build_parser().parse_args(["pull", *RANGE, *args]))


def test_command_is_discovered_and_help_lists_the_flags() -> None:
    assert pull.register in cli.discover_commands()
    out = io.StringIO()
    code = cli.run(["pull", "--help"], project_dir=None, environ={}, stdout=out)

    assert code == 0
    for flag in (
        "--start",
        "--end",
        "--symbols",
        "--max-strikes",
        "--pull-window",
        "--sample-interval-min",
        "--storage-interval-s",
        "--size-limit-gb",
        "--range-credits",
        "--historical-credits",
        "--instruments",
        "--bar-interval",
        "--dark-pool",
        "--dark-pool-tickers",
        "--label-sample-minutes",
    ):
        assert flag in out.getvalue()


def test_defaults_are_the_requirement_defaults() -> None:
    options = parse()

    assert options == PullOptions(
        spec=PullSpec(start=date(2026, 3, 2), end=date(2026, 3, 3)),
        instruments=("ES", "NQ"),
        bar_interval_s=60,
        storage_interval_s=None,
        size_limit_gb=20.0,
        range_credits=25,
        historical_credits=5,
        dark_pool_tickers=(),
    )
    assert options.spec.view == HeatmapView(max_strikes=92, max_expirations=5)
    assert options.spec.symbols == ("SPX", "SPY", "QQQ", "NDX", "NDXP")
    assert (options.spec.sample_interval_minutes, options.spec.label_sample_minutes) == (1, None)
    assert parse("--dark-pool").dark_pool_tickers == ("SPY", "QQQ")
    assert parse("--dark-pool", "--dark-pool-tickers", "IWM").dark_pool_tickers == ("IWM",)
    flags = parse("--storage-interval-s", "5", "--max-strikes", "all", "--bar-interval", "5")
    assert (flags.storage_interval_s, flags.spec.view.max_strikes, flags.bar_interval_s) == (
        5,
        "all",
        5,
    )


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ([*RANGE, "--storage-interval-s", "7"], "divide the 900 s Cache_Window"),
        ([*RANGE, "--pull-window", "10:00-09:00"], "must start before it ends"),
        ([*RANGE, "--bar-interval", "7"], "does not divide a minute"),
        ([*RANGE, "--dark-pool-tickers", "SPY"], "applies only with --dark-pool"),
        (["--start", "2026-03-03", "--end", "2026-03-02"], "is after the end date"),
        (["--start", "2026-03-02", "--end", "03/03/2026"], "is not a YYYY-MM-DD date"),
    ],
)
def test_invalid_input_exits_2_before_any_request(
    args: list[str], reason: str, tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    respx_router.reset()
    code, _, err = run(args, tmp_path, {"SKYLIT_API_KEY": KEY})

    assert code == 2
    assert reason in err
    assert not respx_router.calls
    assert not (tmp_path / "out").exists()


def config(dark_pool: bool, **gate: str) -> StrategyConfig:
    data = minimal_config_data()
    data["gates"] = {"dark_pool_confluence": {"enabled": dark_pool, **gate}}
    return StrategyConfig.model_validate(data)


def args(*flags: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["pull", *RANGE, *flags])


@pytest.mark.parametrize(
    ("cfg", "flags", "tickers"),
    [
        (None, (), ()),
        (None, ("--dark-pool",), ("SPY", "QQQ")),
        (config(False), (), ()),
        (config(False), ("--dark-pool",), ("SPY", "QQQ")),
        (config(True), (), ("SPY", "QQQ")),
        (config(True, es_ticker="SPX", nq_ticker="NDX"), (), ("SPX", "NDX")),
        (config(True), ("--dark-pool-tickers", "IWM"), ("IWM",)),
        (config(False), ("--dark-pool", "--dark-pool-tickers", "IWM,DIA"), ("IWM", "DIA")),
    ],
)
def test_dark_pool_tickers_follow_the_config_and_the_flags(
    cfg: StrategyConfig | None, flags: tuple[str, ...], tickers: tuple[str, ...]
) -> None:
    assert pull.dark_pool_tickers(args(*flags), cfg) == tickers
    assert pull.options_from_args(args(*flags), cfg).dark_pool_tickers == tickers


def test_dark_pool_tickers_without_fetching_is_a_usage_error() -> None:
    with pytest.raises(pull.PullUsageError, match="applies only with --dark-pool or a --config"):
        pull.dark_pool_tickers(args("--dark-pool-tickers", "IWM"), config(False))


def test_a_bad_config_exits_2_before_any_request(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    respx_router.reset()
    bad = tmp_path / "bad.yaml"
    bad.write_text("regime: {}\n", encoding="utf-8")
    code, _, err = run([*RANGE, "--config", str(bad)], tmp_path, {"SKYLIT_API_KEY": KEY})

    assert code == 2
    assert "regime.min_abs_value" in err
    assert not respx_router.calls
    assert not (tmp_path / "out").exists()


def test_a_config_enabling_dark_pool_confluence_sets_the_tickers(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text(
        MINIMAL_CONFIG_YAML + "gates:\n  dark_pool_confluence: {enabled: true}\n", encoding="utf-8"
    )
    parsed = cli.build_parser().parse_args(["pull", *RANGE, "--config", str(good)])
    cfg = load_config(parsed.config, LogWriter(Redactor([])))

    assert pull.options_from_args(parsed, cfg).dark_pool_tickers == ("SPY", "QQQ")


def test_an_end_date_after_today_exits_2(tmp_path: Path, respx_router: respx.MockRouter) -> None:
    respx_router.reset()
    later = (date.today() + timedelta(days=2)).isoformat()
    code, _, err = run(["--start", "2026-03-02", "--end", later], tmp_path, {"SKYLIT_API_KEY": KEY})

    assert code == 2  # after the current date, or outside the calendar files
    assert later in err
    assert not respx_router.calls


def test_a_blank_key_exits_3_before_any_request(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    respx_router.reset()
    code, _, err = run(RANGE, tmp_path, {})

    assert code == 3
    assert "SKYLIT_API_KEY is blank" in err
    assert not respx_router.calls


def test_a_non_interactive_pull_over_the_size_limit_exits_2(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    respx_router.reset()
    respx_router.route(method="GET", host=ep.ACCOUNT.host.value, path=ep.ACCOUNT.path).mock(
        return_value=httpx.Response(200, json={"data": {"limits": {}}})
    )
    listed = [
        {
            "symbol": s,
            "isIndex": False,
            "metrics": ["gamma", "vanna"],
            "history": {"from": "2023-03-28", "to": "2026-03-03"},
        }
        for s in ("SPX", "SPY", "QQQ", "NDX", "NDXP")
    ]
    respx_router.route(method="GET", host=ep.SYMBOLS.host.value, path=ep.SYMBOLS.path).mock(
        return_value=httpx.Response(200, json={"data": {"symbols": listed}})
    )

    code, out, err = run([*RANGE, "--size-limit-gb", "0.000001"], tmp_path, {"SKYLIT_API_KEY": KEY})

    assert code == 2
    assert "Pull estimate" in out
    assert "No Replay_Request was sent" in err
    assert [c.request.url.path for c in respx_router.calls] == ["/v1/account", "/v1/symbols"]
    assert (tmp_path / "out" / FETCH_LOG_FILE_NAME).exists()
    assert not list((tmp_path / "out").glob("coverage_*"))
