"""The ``retrieveBars`` subscription probe ``auto | live | sim`` (design 4.4).

Real :class:`~fse.projectx.bars.ProjectXBars` over the suite's respx router
(``assert_all_mocked``): only ``loginKey`` and ``retrieveBars`` are mocked.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import respx

from fse.live.host.dataplane import SubscriptionProbeError
from fse.logio import Redactor
from fse.projectx.bars import ProjectXBars
from fse.projectx.session import ProjectXSession
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE
from tests.conftest import FakeEnv
from tests.fakes.bar_api import BASE, RespxBarsGateway
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import ny
from tests.fakes.skylit_plane import Plane, make_dataplane, plane_config

MON = date(2026, 10, 12)
M = NS_PER_MINUTE


@pytest_asyncio.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def rig(
    tmp_path: Path,
    clock: FakeClock,
    env: EnvView,
    http: httpx.AsyncClient,
    mode: str,
) -> tuple[Plane, list[bool]]:
    session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
    made: list[bool] = []

    def bars(live: bool) -> ProjectXBars:
        made.append(live)
        return ProjectXBars(session, clock, random.Random(1), live=live)

    cfg = plane_config(projectx={"subscription": mode}, skylit={"enabled": False})
    return make_dataplane(tmp_path, clock, bars, cfg), made


@pytest.fixture
def env(fake_secrets: FakeEnv) -> EnvView:
    return EnvView(dict(fake_secrets.values), {})


def probes(plane: Plane) -> list[dict[str, object]]:
    return [e for e in plane.logs if e["kind"] == "subscription_probe"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("live_ok", "live_has_bars", "expected"),
    [(True, True, "live"), (True, False, "sim"), (False, True, "sim")],
)
async def test_auto_prefers_live_when_it_returns_bars(
    tmp_path: Path,
    env: EnvView,
    http: httpx.AsyncClient,
    respx_router: respx.MockRouter,
    live_ok: bool,
    live_has_bars: bool,
    expected: str,
) -> None:
    clock = FakeClock(ny(MON, 10, 0, 30))
    gw = RespxBarsGateway(clock, live_ok=live_ok, live_has_bars=live_has_bars)
    gw.install(respx_router)
    plane, made = rig(tmp_path, clock, env, http, "auto")
    assert await clock.run(plane.plane.probe()) == expected
    assert plane.plane.subscription == expected
    assert made == [True, False]
    # one request each, for the last 5 closed MNQ minutes
    assert [(b["live"], b["contractId"]) for b in gw.bodies] == [
        (True, "CON.F.US.MNQ.Z26"),
        (False, "CON.F.US.MNQ.Z26"),
    ]
    _, lo, hi = next(iter(gw.windows()))
    assert (lo, hi) == (ny(MON, 9, 55), ny(MON, 10, 0))
    logged = probes(plane)
    assert [p["live"] for p in logged] == [True, False]
    assert logged[1]["bars"] == 5
    assert logged[1]["newest_lag_ms"] == 30_000
    warned = "projectx_sim_subscription" in plane.kinds()
    assert warned is (expected == "sim")


@pytest.mark.asyncio
async def test_explicit_live_fails_start_when_the_probe_fails(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 10, 0, 30))
    RespxBarsGateway(clock, live_ok=False).install(respx_router)
    plane, _ = rig(tmp_path, clock, env, http, "live")
    with pytest.raises(SubscriptionProbeError) as info:
        await clock.run(plane.plane.probe())
    assert info.value.exit_code == 3


@pytest.mark.asyncio
async def test_explicit_live_passes_and_sim_sends_nothing(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 10, 0, 30))
    gw = RespxBarsGateway(clock)
    gw.install(respx_router)
    plane, _ = rig(tmp_path, clock, env, http, "live")
    assert await clock.run(plane.plane.probe()) == "live"
    assert [b["live"] for b in gw.bodies] == [True]

    gw.bodies.clear()
    sim, made = rig(tmp_path / "sim", clock, env, http, "sim")
    assert await clock.run(sim.plane.probe()) == "sim"
    assert gw.bodies == []
    assert made == []


@pytest.mark.asyncio
async def test_market_closed_keeps_live_when_both_are_empty(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 17, 30, 30))  # the daily break: no bar on either subscription
    RespxBarsGateway(clock, has_bar=lambda _cid, _t: False).install(respx_router)
    plane, _ = rig(tmp_path, clock, env, http, "auto")
    assert await clock.run(plane.plane.probe()) == "live"
