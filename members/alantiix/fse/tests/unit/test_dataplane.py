"""``DataPlane``: always-on recording, bar store, Skylit plane, rotation (design 4.1, 4.5).

Bars come from :class:`~tests.fakes.bar_api.MemoryBars`, Skylit from
:class:`~tests.fakes.skylit_plane.FakeSkylit`; time is virtual. The headline
property: every feed is recorded although the only plugin consumes MNQ bars
and nothing else.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fse.calendars import load_calendars
from fse.engine.types import Snapshot
from fse.live.host.dataplane import (
    credit_projection_for,
    dataplane_quotes_path,
    dataplane_recording_files,
    dataplane_recording_path,
)
from fse.live.host.dayclock import globex_day
from fse.live.host.host import (
    FeedStatusEvent,
    HostPaths,
    HostResources,
    InlineBlockingRunner,
    MarketEvent,
    PluginHost,
    QuoteEvent,
)
from fse.live.plugins.api import Quote, StrategyPlugin
from fse.live.recorder import Recorder, decode_bar, read_recording
from fse.logio import LogWriter, Redactor
from fse.projectx.session import ProjectXAuthError
from fse.secrets.env import EnvView
from fse.skylit.client import SkylitStoppedError
from fse.skylit.endpoints import Host
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant
from tests.fakes.bar_api import MemoryBars
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import (
    CALENDAR_DIR,
    PROJECT_DIR,
    host_entry,
    make_bar,
    ny,
    runner_config,
)
from tests.fakes.plugins import MinimalPlugin, RecordingPlugin
from tests.fakes.skylit_plane import FakeSkylit, Plane, make_dataplane, plane_config

S = NS_PER_SECOND
M = NS_PER_MINUTE
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)


def host_over(
    tmp_path: Path, rig: Plane, clock: FakeClock, cls: type[StrategyPlugin] = MinimalPlugin
) -> PluginHost:
    cfg = runner_config(
        [host_entry("p1", module="tests.fakes.plugins:MinimalPlugin")],
        data_plane={"instruments": ["MNQ", "MES"]},
    )
    return PluginHost(
        cfg,
        {"p1": cls},
        dataplane=rig.plane,
        clock=clock,
        writer=rig.writer,
        paths=HostPaths(
            state_root=tmp_path / "live-state",
            run_dir=tmp_path / "run",
            cache_dir=tmp_path / "cache",
            calendar_dir=CALENDAR_DIR,
            project_dir=PROJECT_DIR,
        ),
        calendars=load_calendars(CALENDAR_DIR, MON, date(2026, 10, 20)),
        resources=HostResources(env=EnvView({}, {}), skylit_levels=rig.plane.levels),
        blocking=InlineBlockingRunner(),
    )


def recording(tmp_path: Path, d: date) -> list[Any]:
    return list(read_recording(dataplane_recording_path(tmp_path / "rec", d)).entries)


@pytest.mark.asyncio
async def test_every_feed_is_recorded_with_no_plugin_consuming_it(tmp_path: Path) -> None:
    clock = FakeClock(ny(MON, 9, 0))
    api = MemoryBars(clock, publish_delay_ns=S)
    rig = make_dataplane(tmp_path, clock, lambda live: api)
    host = host_over(tmp_path, rig, clock)
    assert await clock.run(host.run(until_ns=ny(MON, 9, 3))) == 0
    entries = recording(tmp_path, MON)
    kinds = {e.kind for e in entries}
    assert {"bar", "snapshot", "dark_pool"} <= kinds
    bars = [decode_bar(e.payload) for e in entries if e.kind == "bar"]
    mes = [b.open_ns for b in bars if b.instrument == "MES"]  # no plugin needs MES
    assert mes == list(range(globex_day(MON).open_ns, ny(MON, 9, 2), M))
    snaps = [e.payload for e in entries if e.kind == "snapshot"]
    assert {(s["symbol"], s["metric"]) for s in snaps} == {  # type: ignore[index]
        ("QQQ", "gamma"),
        ("QQQ", "vanna"),
        ("SPY", "gamma"),
        ("SPY", "vanna"),
    }
    dark = [e.payload for e in entries if e.kind == "dark_pool"]
    assert dark
    assert dark[0] == {"ticker": "QQQ", "prints": []}
    # the bar store has every recorded bar, MES included
    view = rig.plane.bar_store
    assert [b.open_ns for b in view.bars("MES", MON)] == mes
    # the day's Skylit client logs to the data plane's Fetch_Log
    assert rig.skylit[0].fetch_log is not None
    assert rig.skylit[0].fetch_log.path == tmp_path / "run" / "dataplane" / MON.isoformat() / (
        "fetch_log.jsonl"
    )
    credits = [e for e in rig.logs if e["kind"] == "credit_projection"]
    assert credits
    assert credits[0]["day"] == MON.isoformat()
    assert credits[0]["total_credits"] > 0
    assert host.plugin("p1") is not None


@pytest.mark.asyncio
async def test_recorded_bars_read_back_and_seed_a_restart(tmp_path: Path) -> None:
    clock = FakeClock(ny(MON, 9, 0))
    api = MemoryBars(clock)
    rig = make_dataplane(tmp_path, clock, lambda live: api)
    await clock.run(host_over(tmp_path, rig, clock).run(until_ns=ny(MON, 9, 2, 30)))
    day = globex_day(MON)
    recorded = rig.plane.recorded_bars(day)
    assert recorded
    assert recorded[-1][0].close_ns == ny(MON, 9, 2)

    clock2 = FakeClock(ny(MON, 9, 10))
    api2 = MemoryBars(clock2)
    rig2 = make_dataplane(tmp_path, clock2, lambda live: api2)
    again = rig2.plane.recorded_bars(day)
    assert [(b.instrument, b.open_ns, r) for b, r in again] == [
        (b.instrument, b.open_ns, r) for b, r in recorded
    ]
    await clock2.run(host_over(tmp_path, rig2, clock2).run(until_ns=ny(MON, 9, 11)))
    # the cursor starts at the newest recorded bar, not at the day open
    first = next(start for _, i, start, _ in api2.calls if i == "MNQ")
    assert first == ny(MON, 9, 2)


def test_recorded_bars_read_split_part_files_then_the_day_file(tmp_path: Path) -> None:
    clock = FakeClock(ny(MON, 9, 0))
    api = MemoryBars(clock)
    rig = make_dataplane(tmp_path, clock, lambda live: api)
    day = globex_day(MON)
    main = dataplane_recording_path(tmp_path / "rec", MON)
    main.parent.mkdir(parents=True)

    def write(path: Path, minutes: list[int]) -> None:
        with Recorder(rig.writer, path) as r:
            for m in minutes:
                t = day.open_ns + m * M
                r.bar(make_bar("MNQ", t, 80_000 + m), t + M + S)
                r.snapshot(snapshot_line(t), t + M + S)  # skipped unparsed

    part1 = main.with_name(f"{MON}.part1-20261012T130000Z.jsonl.gz")
    part2 = main.with_name(f"{MON}.part2-20261012T150000Z.jsonl.gz")
    write(part2, [2])
    write(part1, [0, 1])
    write(main, [3])
    main.with_name(f"{MON}.quotes.jsonl.gz").write_bytes(b"")  # not a bar file
    assert dataplane_recording_files(tmp_path / "rec", MON) == [part1, part2, main]
    got = rig.plane.recorded_bars(day)
    assert [b.open_ns for b, _ in got] == [day.open_ns + m * M for m in range(4)]
    # a bad file keeps the bars read before the problem and logs it
    with gzip.open(part2, "ab") as fh:
        fh.write(b"not json\n")
    write(part2.with_name(f"{MON}.part3-x.jsonl.gz"), [4])
    got = rig.plane.recorded_bars(day)
    assert [b.open_ns for b, _ in got] == [day.open_ns + m * M for m in (0, 1, 2, 4, 3)]
    assert [e["kind"] for e in rig.logs if e["kind"] == "recording_unreadable"] == [
        "recording_unreadable"
    ]


def snapshot_line(t: Instant) -> Snapshot:
    return Snapshot(
        symbol="QQQ",
        metric="gamma",
        view_id="v",
        as_of_ns=t,
        as_of_raw=str(t),
        spot=500.0,
        previous_close=None,
        strikes=(500.0,),
        values=(1.0,),
        node_types=None,
        expirations=("2026-10-12",),
        resolution="1s",
        source_endpoint="stream",
        extra_json="{}",
    )


@pytest.mark.asyncio
async def test_live_bars_carry_the_roll_calendar_contract_code(tmp_path: Path) -> None:
    # range api-request R3.6: Bar.contract is the roll-calendar code (MNQZ6), not the
    # ProjectX contract id (CON.F.US.MNQ.Z26), in dispatched, recorded and stored bars
    clock = FakeClock(ny(MON, 9, 0))
    api = MemoryBars(clock, publish_delay_ns=S)
    rig = make_dataplane(tmp_path, clock, lambda live: api)
    host = host_over(tmp_path, rig, clock, RecordingPlugin)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    seen: list[str] = []
    plugin.handlers["on_bar"] = lambda bar, *a: seen.append(bar.contract)
    assert await clock.run(host.run(until_ns=ny(MON, 9, 3))) == 0
    recorded = [
        decode_bar(e.payload).contract for e in recording(tmp_path, MON) if e.kind == "bar"
    ]
    stored = [b.contract for b in rig.plane.bar_store.bars("MNQ", MON)]
    assert seen
    assert stored
    for contract in [*seen, *recorded, *stored]:
        assert re.fullmatch(r"(MNQ|MES)[HMUZ]\d", contract), contract
        assert not contract.startswith("CON.F.US.")
    assert set(seen) == set(stored) == {"MNQZ6"}


@pytest.mark.asyncio
async def test_rotation_closes_the_day_and_records_the_next(tmp_path: Path) -> None:
    clock = FakeClock(ny(MON, 16, 58))
    api = MemoryBars(clock)
    rig = make_dataplane(tmp_path, clock, lambda live: api)
    await clock.run(host_over(tmp_path, rig, clock).run(until_ns=ny(MON, 18, 3)))
    mon = read_recording(dataplane_recording_path(tmp_path / "rec", MON))
    assert not mon.truncated  # closed at rotation
    tue = recording(tmp_path, TUE)
    tue_bars = [decode_bar(e.payload) for e in tue if e.kind == "bar"]
    assert tue_bars
    assert min(b.open_ns for b in tue_bars) == ny(MON, 18, 0)
    # no bar request during the 17:02-18:00 pause
    assert not [t for t, *_ in api.calls if ny(MON, 17, 2) < t < ny(MON, 18, 0)]
    days = [e["day"] for e in rig.logs if e["kind"] == "credit_projection"]
    assert days == [MON.isoformat(), TUE.isoformat()]
    assert (tmp_path / "bars" / "MNQ" / "60s" / f"{TUE}.parquet").is_file()


def quote(instrument: str, received: Instant, bid: int) -> QuoteEvent:
    return QuoteEvent(
        Quote(instrument, f"CON.F.US.{instrument}.Z26", received, received, bid, bid + 1, bid)
    )


def quote_lines(tmp_path: Path) -> list[dict[str, Any]]:
    path = dataplane_quotes_path(tmp_path / "rec", MON)
    if not path.exists():
        return []
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("sampled_1s", [11, 20, 30]), ("all", [10, 11, 20, 30]), ("off", [])],
)
def test_quote_file_modes(tmp_path: Path, mode: str, expected: list[int]) -> None:
    t = ny(MON, 10, 0)
    clock = FakeClock(t)
    rig = make_dataplane(
        tmp_path,
        clock,
        lambda live: MemoryBars(clock),
        plane_config(projectx={"record_quotes": mode}),
    )
    plane = rig.plane
    for received, bid in ((t + 100_000_000, 10), (t + 900_000_000, 11), (t + S + 5, 20)):
        plane.record(quote("MNQ", received, bid))
    plane.record(quote("MES", t + 2 * S, 30))
    plane.close()
    # sampled_1s: the last quote of each instrument-second (11 replaces 10)
    got = sorted(line["payload"]["bid_t"] for line in quote_lines(tmp_path))
    assert got == expected
    if mode == "off":
        assert not dataplane_quotes_path(tmp_path / "rec", MON).exists()


def test_contracts_fall_back_to_expected_ids_and_report_missing(tmp_path: Path) -> None:
    d = date(2026, 3, 23)  # no ProjectX ids in the roll calendar then
    clock = FakeClock(ny(d, 10, 0))
    cals = load_calendars(CALENDAR_DIR, d, d)
    rig = make_dataplane(
        tmp_path,
        clock,
        lambda live: MemoryBars(clock),
        plane_config(projectx={"expected_contracts": {"MNQ": "CON.F.US.MNQ.M26"}}),
        calendars=cals,
    )
    found, missing = rig.plane.contracts(d)
    assert [(f.instrument, f.contract, f.contract_id) for f in found] == [
        ("MNQ", "MNQM6", "CON.F.US.MNQ.M26")
    ]
    assert missing == ["MES: no ProjectX contract id"]


@pytest.mark.asyncio
async def test_missing_contract_is_skipped_with_a_bars_down_status(tmp_path: Path) -> None:
    d = date(2026, 3, 23)
    clock = FakeClock(ny(d, 10, 0))
    rig = make_dataplane(
        tmp_path,
        clock,
        lambda live: MemoryBars(clock),
        plane_config(
            projectx={"expected_contracts": {"MNQ": "CON.F.US.MNQ.M26"}}, skylit={"enabled": False}
        ),
        calendars=load_calendars(CALENDAR_DIR, d, d),
    )
    events: list[MarketEvent] = []

    async def main() -> None:
        import asyncio

        task = asyncio.ensure_future(rig.plane.run(events.append))
        await clock.sleep(10 * S)
        task.cancel()

    await clock.run(main())
    down = [e for e in events if isinstance(e, FeedStatusEvent)]
    assert down
    assert down[0].feed == "bars"
    assert not down[0].up
    assert "MES" in down[0].detail
    assert "bar_feed_skipped" in rig.kinds()


@pytest.mark.asyncio
async def test_a_projectx_auth_failure_is_fatal_with_exit_3(tmp_path: Path) -> None:
    clock = FakeClock(ny(MON, 10, 0))
    api = MemoryBars(clock, raise_on_call=ProjectXAuthError("refused with HTTP 403"))
    rig = make_dataplane(tmp_path, clock, lambda live: api, plane_config(skylit={"enabled": False}))
    events: list[MarketEvent] = []

    async def main() -> None:
        import asyncio

        task = asyncio.ensure_future(rig.plane.run(events.append))
        await clock.sleep(10 * S)
        assert not task.done()  # the data plane idles; the host decides to stop
        task.cancel()

    await clock.run(main())
    assert [code for code, _ in rig.fatal] == [3]
    assert any(isinstance(e, FeedStatusEvent) and e.feed == "bars" and not e.up for e in events)


@pytest.mark.asyncio
async def test_a_skylit_refusal_stops_only_the_skylit_plane(tmp_path: Path) -> None:
    class Refusing(FakeSkylit):
        async def heatmap(
            self, symbols: Any, *, metric: str, view: Any, layout: Any = "net"
        ) -> Any:
            raise SkylitStoppedError(401, None, Host.API, "/v1/heatmap")

    clock = FakeClock(ny(MON, 9, 0))
    api = MemoryBars(clock)
    rig = make_dataplane(tmp_path, clock, lambda live: api)
    rig.plane._skylit_factory = lambda fl: Refusing(clock, fl)
    events: list[MarketEvent] = []

    async def main() -> None:
        import asyncio

        task = asyncio.ensure_future(rig.plane.run(events.append))
        await clock.sleep(2 * M)
        task.cancel()

    await clock.run(main())
    status = [(e.feed, e.up) for e in events if isinstance(e, FeedStatusEvent)]
    assert ("skylit", False) in status
    assert not rig.fatal
    assert any(type(e).__name__ == "BarsEvent" for e in events)  # bars keep coming


def test_credit_projection_matches_the_live_runner_formula() -> None:
    cfg = plane_config(
        skylit={
            "mode": "stream",
            "window": {"start": "09:00", "end": "16:15"},
            "dark_pool": {"enabled": False, "tickers": []},
        }
    )
    out = credit_projection_for(cfg, globex_day(MON))
    assert out["map_credits"] == 2 * 2 * (435 + 1) == 1744
    assert out["total_credits"] == 1744
    holiday = replace(globex_day(MON), exchange_session=False)
    assert credit_projection_for(cfg, holiday)["total_credits"] == 0  # no Skylit request


def test_no_plane_line_holds_a_secret(tmp_path: Path) -> None:
    redactor = Redactor()
    redactor.add("fake-secret-value-123")
    writer = LogWriter(redactor, stdout=io.StringIO(), stderr=io.StringIO())
    clock = FakeClock(ny(MON, 10, 0))
    rig = make_dataplane(tmp_path, clock, lambda live: MemoryBars(clock), writer=writer)
    rig.plane.record(FeedStatusEvent("bars", False, "token fake-secret-value-123 refused"))
    rig.plane.close()
    with gzip.open(dataplane_recording_path(tmp_path / "rec", MON), "rt") as fh:
        text = fh.read()
    assert "fake-secret-value-123" not in text
    assert "feed_status" in text
