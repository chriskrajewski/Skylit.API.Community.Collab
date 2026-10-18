"""Backfill of missing past Globex days into the bar store (design 4.6).

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

from fse.live.host.barstore import StoredBar
from fse.live.host.dayclock import globex_day
from fse.logio import Redactor
from fse.projectx.bars import ProjectXBars
from fse.projectx.session import ProjectXAuthError, ProjectXSession
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE
from tests.conftest import FakeEnv
from tests.fakes.bar_api import BASE, RespxBarsGateway, synthetic_bar
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import ny
from tests.fakes.skylit_plane import Plane, make_dataplane, plane_config
from tests.fakes.supplement import read_supplement as _read_supplement

M = NS_PER_MINUTE
MON = date(2026, 10, 12)
THU, FRI, WED = date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 7)


@pytest_asyncio.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def env(fake_secrets: FakeEnv) -> EnvView:
    return EnvView(dict(fake_secrets.values), {})


def rig(
    tmp_path: Path, clock: FakeClock, env: EnvView, http: httpx.AsyncClient, sessions: int
) -> Plane:
    session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
    cfg = plane_config(projectx={"backfill_sessions": sessions}, skylit={"enabled": False})
    return make_dataplane(
        tmp_path, clock, lambda live: ProjectXBars(session, clock, random.Random(1), live=live), cfg
    )


@pytest.mark.asyncio
async def test_missing_days_are_fetched_into_the_bar_store(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 10, 0))
    gw = RespxBarsGateway(clock)
    gw.install(respx_router)
    plane = rig(tmp_path, clock, env, http, 3)
    assert await clock.run(plane.plane.backfill()) == 6
    windows = sorted((cid, lo, hi) for cid, lo, hi in gw.windows())
    assert {lo for _, lo, _ in windows} == {globex_day(d).open_ns for d in (WED, THU, FRI)}
    assert {hi for _, _, hi in windows} == {ny(d, 17, 0) for d in (WED, THU, FRI)}
    for instrument in ("MNQ", "MES"):
        got = _read_supplement(tmp_path / "bars", instrument, FRI)
        assert got is not None
        assert got[0].open_ns == globex_day(FRI).open_ns
        assert got[-1].close_ns == ny(FRI, 17, 0)
        assert {b.contract for b in got} == {f"{instrument}Z6"}
    assert all(b["live"] is False for b in gw.bodies)  # the sim subscription (no probe run)


@pytest.mark.asyncio
async def test_days_in_the_cache_or_the_store_are_skipped(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 10, 0))
    gw = RespxBarsGateway(clock)
    gw.install(respx_router)
    plane = rig(tmp_path, clock, env, http, 3)
    store = plane.plane.store
    store.write_day("MNQ", FRI, "MNQZ6", [StoredBar(synthetic_bar("MNQ", ny(FRI, 9, 30)), 1)])
    cached = {("MES", THU), ("MNQ", WED)}
    assert await clock.run(plane.plane.backfill(lambda i, d: (i, d) in cached)) == 3
    fetched = {(cid.split(".")[3], lo) for cid, lo, _ in gw.windows()}
    assert fetched == {
        ("MNQ", globex_day(THU).open_ns),
        ("MES", globex_day(WED).open_ns),
        ("MES", globex_day(FRI).open_ns),
    }


@pytest.mark.asyncio
async def test_a_failed_day_leaves_a_gap_and_the_others_are_written(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 10, 0))
    RespxBarsGateway(clock, fail_starts={globex_day(THU).open_ns}).install(respx_router)
    plane = rig(tmp_path, clock, env, http, 2)
    assert await clock.run(plane.plane.backfill()) == 2  # Friday for both instruments
    failed = [e for e in plane.logs if e["kind"] == "backfill_failed"]
    assert {(e["instrument"], e["day"]) for e in failed} == {
        ("MNQ", THU.isoformat()),
        ("MES", THU.isoformat()),
    }
    assert _read_supplement(tmp_path / "bars", "MNQ", THU) is None


@pytest.mark.asyncio
async def test_zero_sessions_sends_nothing_and_auth_failures_propagate(
    tmp_path: Path, env: EnvView, http: httpx.AsyncClient, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(ny(MON, 10, 0))
    gw = RespxBarsGateway(clock, login_status=401)
    gw.install(respx_router)
    assert await clock.run(rig(tmp_path, clock, env, http, 0).plane.backfill()) == 0
    assert gw.logins == 0
    assert gw.bodies == []
    with pytest.raises(ProjectXAuthError):
        await clock.run(rig(tmp_path / "b", clock, env, http, 1).plane.backfill())
