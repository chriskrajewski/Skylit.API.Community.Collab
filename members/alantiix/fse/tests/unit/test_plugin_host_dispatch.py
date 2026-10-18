"""``PluginHost`` dispatch (design 2.2, 3.3, 13): order, the dispatch rule, draining and wakes,
intent rounds, the slow-hook warning and fault quarantine.

Every test drives the host with ``FakeClock`` and the in-memory
``FakeDataPlane``; no socket is opened.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fse.config.schema.data import HeatmapViewConfig
from fse.engine.types import Snapshot
from fse.live.host.host import (
    HOOK_LATENCY_WARN_NS,
    HOST_LOG_FILE_NAME,
    MAX_INTENT_ROUNDS,
    PLUGIN_STATE_FILE_NAME,
    BarsEvent,
    DarkPoolEvent,
    FeedStatusEvent,
    QuoteEvent,
    SnapshotsEvent,
)
from fse.live.plugins import (
    BarNeed,
    BrokerTerms,
    DarkPoolPrint,
    DataNeeds,
    EnterMarket,
    EnterResting,
    PaperFills,
    PaperRisk,
    Quote,
    SkylitNeed,
    TargetLeg,
)
from fse.live.plugins.loader import PluginLoadError
from fse.pit.market_view import heatmap_view
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin

WED = date(2026, 10, 14)
T0 = ny(WED, 10, 0)
VIEW_ID = heatmap_view(HeatmapViewConfig()).view_id()


def bars_at(minute: int, instrument: str = "MNQ", price: int = 80_000) -> BarsEvent:
    """The bar opening at ``10:minute`` received 2 s after its close."""
    bar = make_bar(instrument, T0 + minute * NS_PER_MINUTE, price, price + 4, price - 4, price)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def at(minute: int, second: int = 2) -> int:
    return T0 + minute * NS_PER_MINUTE + second * NS_PER_SECOND


def enter(tag: str, placed_at: int, instrument: str = "MNQ") -> EnterMarket:
    return EnterMarket(
        tag=tag,
        instrument=instrument,
        direction="long",
        qty=1,
        stop_ticks=40,
        targets=(TargetLeg("tp1", 1, 40),),
        placed_at=placed_at,
        exact_bar_open=True,
    )


def host_log(tmp_path: Path) -> list[dict[str, Any]]:
    return read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)


@pytest.mark.asyncio
async def test_order_record_store_book_update_hook(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0)), (at(2), bars_at(1))])
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    seen: list[tuple[str, list[str]]] = []

    def on_bar(bar: Any, received: int, catch_up: bool, ctx: Any) -> list[EnterMarket]:
        seen.append(("on_bar", list(plane.calls)))
        if bar.open_ns == T0:
            return [enter("t1", bar.close_ns)]
        return []

    def on_update(update: Any, ctx: Any) -> list[EnterMarket]:
        seen.append((f"update:{update.status}", list(plane.calls)))
        return []

    plugin.handlers["on_bar"] = on_bar
    plugin.handlers["on_order_update"] = on_update
    assert await clock.run(host.run(until_ns=at(3))) == 0
    assert [name for name, _ in seen] == [
        "on_bar",
        "update:accepted",
        "update:filled_entry",  # the book fills on the 10:01 bar before its on_bar
        "on_bar",
    ]
    # record, then store, before any plugin call for that event
    assert seen[0][1] == ["record:BarsEvent", "store"]
    assert seen[2][1] == ["record:BarsEvent", "store"] * 2
    fill = plugin.updates[1].fill
    assert fill is not None
    assert fill.price == 80_000
    assert fill.bar_open_ns == T0 + NS_PER_MINUTE
    assert fill.client_id == "p1:t1:entry"


class MnqOnly(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth")


class Everything(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(
        bars=(BarNeed("MNQ"), BarNeed("MES")),
        hours="eth",
        skylit=SkylitNeed(
            symbols=("SPY",),
            view_id=VIEW_ID,
            mode="stream",
            refresh_interval_s=15,
            window=(time(9, 30), time(16, 0)),
            dark_pool_tickers=("SPY",),
        ),
        quotes=True,
    )


def snapshot(symbol: str) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id=VIEW_ID,
        as_of_ns=T0,
        as_of_raw="2026-10-14T14:00:00Z",
        spot=600.0,
        previous_close=None,
        strikes=(600.0,),
        values=(1.0,),
        node_types=None,
        expirations=(),
        resolution="1m",
        source_endpoint="stream",
        extra_json="{}",
    )


DATA_PLANE: dict[str, Any] = {
    "instruments": ["MNQ", "MES"],
    "skylit": {"symbols": ["QQQ", "SPY"], "dark_pool": {"enabled": True, "tickers": ["SPY"]}},
}


@pytest.mark.asyncio
async def test_dispatch_rule(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    quote = Quote("MES", "CON.MES", at(1), at(1), 24_000, 24_001, 24_000)
    plane = FakeDataPlane(
        clock,
        [
            (at(1), bars_at(0, "MES", 24_000)),
            (at(1), bars_at(0, "MNQ")),
            (at(1), SnapshotsEvent((snapshot("SPY"), snapshot("QQQ")), at(1))),
            (at(1), SnapshotsEvent((snapshot("QQQ"),), at(1))),
            (at(1), QuoteEvent(quote)),
            (
                at(1),
                DarkPoolEvent("SPY", (DarkPoolPrint("SPY", T0, 600.0, 10, 6000.0, "D"),), at(1)),
            ),
            (at(1), DarkPoolEvent("QQQ", (), at(1))),
            (at(1), FeedStatusEvent("bars", True, "")),
            (at(1), FeedStatusEvent("quotes", False, "hub down")),
            (at(1), FeedStatusEvent("skylit", True, "")),
            (at(1), FeedStatusEvent("dark_pool", True, "")),
        ],
    )
    # a snapshot batch outside the 09:30-16:00 plugin window is not dispatched
    plane.at(ny(WED, 16, 5), SnapshotsEvent((snapshot("SPY"),), ny(WED, 16, 5)))
    host = make_host(
        tmp_path,
        {"mnq": MnqOnly, "all": Everything},
        [host_entry("mnq"), host_entry("all")],
        clock,
        plane,
        data_plane=DATA_PLANE,
    )
    await clock.run(host.run(until_ns=ny(WED, 16, 10)))
    mnq = host.plugin("mnq")
    every = host.plugin("all")
    assert isinstance(mnq, RecordingPlugin)
    assert isinstance(every, RecordingPlugin)
    got = [(h, d) for h, _, d in mnq.calls if h not in ("on_start", "on_day_open", "on_stop")]
    assert got == [("on_bar", ("MNQ", T0, False)), ("on_feed_status", ("bars", True))]
    got = [(h, d) for h, _, d in every.calls if h not in ("on_start", "on_day_open", "on_stop")]
    assert got == [
        ("on_bar", ("MES", T0, False)),
        ("on_bar", ("MNQ", T0, False)),
        ("on_snapshots", ("SPY",)),  # filtered by symbol; the QQQ-only batch is not dispatched
        ("on_quote", "MES"),
        ("on_dark_pool", "SPY"),
        ("on_feed_status", ("bars", True)),
        ("on_feed_status", ("quotes", False)),
        ("on_feed_status", ("skylit", True)),
        ("on_feed_status", ("dark_pool", True)),
    ]


@pytest.mark.asyncio
async def test_needs_mismatches_are_listed_together(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock)
    with pytest.raises(PluginLoadError) as info:
        make_host(
            tmp_path,
            {"all": Everything},
            [host_entry("all")],
            clock,
            plane,
            data_plane={"instruments": ["MNQ"], "skylit": {"symbols": ["QQQ"]}},
        )
    text = str(info.value)
    assert "'MES' are not in data_plane.instruments" in text
    assert "Skylit symbols ['SPY']" in text
    assert "dark_pool.enabled is false" in text
    assert info.value.exit_code == 2


@pytest.mark.asyncio
async def test_instrument_not_declared(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0))])
    host = make_host(
        tmp_path,
        {"p1": MnqOnly},
        [host_entry("p1")],
        clock,
        plane,
        data_plane={"instruments": ["MNQ", "MES"]},
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    plugin.handlers["on_bar"] = lambda bar, r, c, ctx: [enter("t1", bar.close_ns, "MES")]
    await clock.run(host.run(until_ns=at(2)))
    assert [(u.status, u.reason) for u in plugin.updates] == [
        ("rejected", "instrument_not_declared")
    ]


class Flagged(RecordingPlugin):
    """NIT 3: ``next_wake`` returns ``now`` while input is pending."""

    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"), BarNeed("MES")), hours="eth")

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.pending = False
        self.handlers["on_bar"] = self._bar
        self.handlers["on_wake"] = self._woken
        self.handlers["next_wake"] = lambda now: now if self.pending else None

    def _bar(self, *args: Any) -> list[Any]:
        self.pending = True
        return []

    def _woken(self, *args: Any) -> list[Any]:
        self.pending = False
        return []


@pytest.mark.asyncio
async def test_queue_drained_before_wakes_and_input_wake_once(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    mes = bars_at(0, "MES", 24_000)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0)), (at(1), mes), (at(3), bars_at(2))])
    host = make_host(
        tmp_path,
        {"p1": Flagged},
        [host_entry("p1")],
        clock,
        plane,
        data_plane={"instruments": ["MNQ", "MES"]},
    )
    await clock.run(host.run(until_ns=at(4)))
    plugin = host.plugin("p1")
    assert isinstance(plugin, Flagged)
    hooks = [h for h in plugin.hooks() if h in ("on_bar", "on_wake")]
    # the two events queued together are both dispatched, then one on_wake
    assert hooks == ["on_bar", "on_bar", "on_wake", "on_bar", "on_wake"]
    wakes = [t for h, t, _ in plugin.calls if h == "on_wake"]
    assert wakes == [at(1), at(3)]


@pytest.mark.asyncio
async def test_intent_loop_faults_only_that_plugin(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0)), (at(2), bars_at(1))])
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin, "p2": RecordingPlugin},
        [host_entry("p1"), host_entry("p2")],
        clock,
        plane,
    )
    p1, p2 = host.plugin("p1"), host.plugin("p2")
    assert isinstance(p1, RecordingPlugin)
    assert isinstance(p2, RecordingPlugin)
    bad = enter("bad:tag", T0 + NS_PER_MINUTE)
    p1.handlers["on_bar"] = lambda *a: [bad]
    p1.handlers["on_order_update"] = lambda *a: [bad]  # every rejection asks again
    assert await clock.run(host.run(until_ns=at(3))) == 1
    assert host.faults() == {"p1": "intent_loop"}
    assert len(p1.updates) == MAX_INTENT_ROUNDS
    assert all((u.status, u.reason) == ("rejected", "bad_tag") for u in p1.updates)
    assert [d for h, _, d in p2.calls if h == "on_bar"] == [
        ("MNQ", T0, False),
        ("MNQ", T0 + NS_PER_MINUTE, False),
    ]
    assert not any(h == "on_bar" and d[1] == T0 + NS_PER_MINUTE for h, _, d in p1.calls)
    (fault,) = [e for e in host_log(tmp_path) if e["kind"] == "plugin_faulted"]
    assert fault["plugin"] == "p1"
    assert fault["detail"] == "EnterMarket"


@pytest.mark.asyncio
async def test_slow_hook_warns_and_order_is_kept(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    perf = [0]
    plane = FakeDataPlane(clock, [(at(1), bars_at(0)), (at(2), bars_at(1))])
    host = make_host(
        tmp_path,
        {"slow": RecordingPlugin, "p2": RecordingPlugin},
        [host_entry("slow"), host_entry("p2")],
        clock,
        plane,
        perf_ns=lambda: perf[0],
    )
    slow, p2 = host.plugin("slow"), host.plugin("p2")
    assert isinstance(slow, RecordingPlugin)
    assert isinstance(p2, RecordingPlugin)
    order: list[str] = []

    def sleepy(bar: Any, *a: Any) -> list[Any]:
        perf[0] += 3 * NS_PER_SECOND  # the hook "sleeps" 3 s of real time
        order.append(f"slow:{bar.open_ns}")
        return []

    def quick(bar: Any, *a: Any) -> list[Any]:
        order.append(f"p2:{bar.open_ns}")
        return []

    slow.handlers["on_bar"] = sleepy
    p2.handlers["on_bar"] = quick
    assert await clock.run(host.run(until_ns=at(3))) == 0
    warnings = [e for e in host_log(tmp_path) if e["kind"] == "hook_latency"]
    assert [(w["plugin"], w["hook"], w["level"]) for w in warnings] == [
        ("slow", "on_bar", "warning"),
        ("slow", "on_bar", "warning"),
    ]
    assert warnings[0]["elapsed_ms"] * 1_000_000 > HOOK_LATENCY_WARN_NS
    assert order == [
        f"slow:{T0}",
        f"p2:{T0}",
        f"slow:{T0 + NS_PER_MINUTE}",
        f"p2:{T0 + NS_PER_MINUTE}",
    ]
    assert host.faults() == {}


@pytest.mark.asyncio
async def test_exception_quarantines_the_plugin(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0)), (at(2), bars_at(1)), (at(3), bars_at(2))])
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin, "p2": RecordingPlugin},
        [host_entry("p1"), host_entry("p2")],
        clock,
        plane,
    )
    p1, p2 = host.plugin("p1"), host.plugin("p2")
    assert isinstance(p1, RecordingPlugin)
    assert isinstance(p2, RecordingPlugin)

    def boom(bar: Any, *a: Any) -> list[Any]:
        if bar.open_ns == T0 + NS_PER_MINUTE:
            raise RuntimeError("a bug")
        return [enter("t1", bar.close_ns)] if bar.open_ns == T0 else []

    p1.handlers["on_bar"] = boom
    assert await clock.run(host.run(until_ns=at(4))) == 1
    assert host.faults() == {"p1": "exception"}
    p1_bars = [d[1] for h, _, d in p1.calls if h == "on_bar"]
    assert p1_bars == [T0, T0 + NS_PER_MINUTE]  # nothing after the fault
    assert "on_stop" not in p1.hooks()
    assert [d[1] for h, _, d in p2.calls if h == "on_bar"] == [
        T0,
        T0 + NS_PER_MINUTE,
        T0 + 2 * NS_PER_MINUTE,
    ]
    # the book still runs: the entry filled on the 10:01 bar and its trade stays open
    broker = host.broker("p1")
    assert broker is not None
    assert broker.open_tags() == ("t1",)
    (fault,) = [e for e in host_log(tmp_path) if e["kind"] == "plugin_faulted"]
    assert fault["hook"] == "on_bar"
    assert "RuntimeError: a bug" in fault["traceback"]
    text = (tmp_path / "live-state" / "plugins" / "p1" / PLUGIN_STATE_FILE_NAME).read_text()
    assert "fault" not in text


class SelfBrokered(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"),), hours="rth", brokerage="self")


@pytest.mark.asyncio
async def test_self_brokered_plugin_gets_the_root_and_no_routing(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(1), bars_at(0))])
    host = make_host(
        tmp_path, {"pb": SelfBrokered}, [{"id": "pb", "module": "x.y:Z"}], clock, plane
    )
    plugin = host.plugin("pb")
    assert isinstance(plugin, RecordingPlugin)
    assert plugin.services.state_dir == tmp_path / "live-state"
    assert host.broker("pb") is None
    plugin.handlers["on_bar"] = lambda bar, *a: [enter("t1", bar.close_ns)]
    await clock.run(host.run(until_ns=at(2)))
    assert plugin.updates == []
    assert any(e["kind"] == "intents_ignored" for e in host_log(tmp_path))
    assert not (tmp_path / "live-state" / PLUGIN_STATE_FILE_NAME).exists()


def test_services_carry_the_runner_fills_block(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    entry = host_entry("p1", fills={"commission_rt": {"MNQ": "1.24"}, "slippage_ticks": 2,
                                    "trade_through_ticks": 1, "stop_fill": "at_stop"})
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin, "pb": SelfBrokered},
        [entry, {"id": "pb", "module": "x.y:Z"}],
        clock,
        FakeDataPlane(clock),
    )
    assert host.plugin("p1").services.fills == PaperFills(
        trade_through_ticks=1, slippage_ticks=2, stop_fill="at_stop",
        commission_rt={"MNQ": Decimal("1.24")},
    )
    assert host.plugin("pb").services.fills is None


def test_services_carry_risk_and_broker_terms(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    entry = host_entry(
        "p1",
        fills={"commission_rt": {"MNQ": "1.24"}, "slippage_ticks": 2,
               "trade_through_ticks": 0, "stop_fill": "at_stop"},
        risk={"max_contracts": 4, "max_open_trades": 2, "max_trades_per_day": 6,
              "max_daily_loss": "250.00"},
    )
    defaults = {"id": "p2", "module": "x.y:Z", "risk": {"max_contracts": 3},
                "fills": {"commission_rt": {"MNQ": "1.24"}}}
    no_fills = {"id": "p3", "module": "x.y:Z", "risk": {"max_contracts": 3}}
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin, "p2": RecordingPlugin, "p3": NoBars, "pb": SelfBrokered},
        [entry, defaults, no_fills, {"id": "pb", "module": "x.y:Z"}],
        clock,
        FakeDataPlane(clock),
    )
    s = host.plugin("p1").services
    assert s.fills == PaperFills(
        trade_through_ticks=0, slippage_ticks=2, stop_fill="at_stop",
        commission_rt={"MNQ": Decimal("1.24")},
    )
    assert s.risk == PaperRisk(
        max_contracts=4, max_open_trades=2, max_trades_per_day=6,
        max_daily_loss=Decimal("250.00"),
    )
    assert s.broker_terms == BrokerTerms(
        trade_through_ticks=0, slippage_ticks=2, stop_fill="at_stop",
        commission_rt={"MNQ": Decimal("1.24")}, max_contracts=4, max_open_trades=2,
        max_trades_per_day=6, max_daily_loss=Decimal("250.00"),
    )
    assert s.broker_terms is not None
    assert s.fills is not None
    assert s.broker_terms.commission_rt["MNQ"] == Decimal("1.24")
    assert s.broker_terms.commission_rt is s.fills.commission_rt
    # host defaults when the fills block and the optional risk keys are omitted
    rt = {"MNQ": Decimal("1.24")}
    d = host.plugin("p2").services
    assert d.fills == PaperFills(1, 0, "gap_open", rt)
    assert d.risk == PaperRisk(3, 1, None, None)
    assert d.broker_terms == BrokerTerms(1, 0, "gap_open", rt, 3, 1, None, None)
    # no fills block at all (a plugin with no bar needs): FillsBlock() defaults
    n = host.plugin("p3").services
    assert n.fills == PaperFills(1, 0, "gap_open", {})
    assert n.broker_terms == BrokerTerms(1, 0, "gap_open", {}, 3, 1, None, None)
    sb = host.plugin("pb").services
    assert (sb.fills, sb.risk, sb.broker_terms) == (None, None, None)


class NoBars(RecordingPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(), hours="eth")


class ReadsTermsInInit(RecordingPlugin):
    seen: ClassVar[list[Any]] = []

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        terms = self.services.broker_terms
        type(self).seen.append((terms, self.services.risk))
        if terms is None or terms.stop_fill != "at_stop":
            raise ValueError("needs stop_fill at_stop")


def test_broker_terms_are_readable_in_init(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    ReadsTermsInInit.seen.clear()
    make_host(
        tmp_path,
        {"p1": ReadsTermsInInit},
        [host_entry("p1", fills={"commission_rt": {"MNQ": "1.24"}, "stop_fill": "at_stop"})],
        clock,
        FakeDataPlane(clock),
    )
    ((terms, risk),) = ReadsTermsInInit.seen
    assert terms.stop_fill == "at_stop"
    assert risk == PaperRisk(50, 5, None, None)
    with pytest.raises(PluginLoadError, match="cannot start: ValueError: needs stop_fill"):
        make_host(
            tmp_path / "b", {"p1": ReadsTermsInInit}, [host_entry("p1")], clock,
            FakeDataPlane(clock),
        )


@pytest.mark.asyncio
async def test_a_halt_file_cancels_working_entries(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [(at(m + 1), bars_at(m)) for m in range(4)])
    host = make_host(tmp_path, {"p1": RecordingPlugin}, [host_entry("p1")], clock, plane)
    plugin = host.plugin("p1")
    assert isinstance(plugin, RecordingPlugin)
    blocked: list[tuple[str, ...]] = []

    def on_bar(bar: Any, received: int, catch_up: bool, ctx: Any) -> list[Any]:
        blocked.append(ctx.entries_blocked())
        if bar.open_ns == T0:
            return [EnterResting("r1", "MNQ", "long", 1, "limit", 70_000, 69_000, ())]
        if bar.open_ns == T0 + NS_PER_MINUTE:
            (tmp_path / "live-state" / "HALT").write_text("")
        return []

    plugin.handlers["on_bar"] = on_bar
    await clock.run(host.run(until_ns=at(5)))
    assert blocked == [(), (), ("halt_file",), ("halt_file",)]
    assert [(u.tag, u.status, u.reason) for u in plugin.updates] == [
        ("r1", "accepted", None),
        ("r1", "cancelled", "halt_file"),
    ]
    broker = host.broker("p1")
    assert broker is not None
    assert broker.book.orders == {}
