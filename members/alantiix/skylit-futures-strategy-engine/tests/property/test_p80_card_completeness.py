"""Property 80: Card completeness.

*For any* Decision_Time state (missing Snapshots and zero setups included),
the Finding_Card has every map, setup, order and position field, with
missing data marked unavailable or none rather than omitted; it lists at most
5 Candidate_Setups ordered A_Plus, Alert_2R, Pass with at most 3
Rejection_Reasons each in Gate order, plus the count not listed; and the watch
levels are the nearest converted Node levels above and below the futures
price.

Each example drives :class:`~fse.live.runner.LiveSession` over generated
sessions with gaps (a session can miss a symbol and metric), drawn receipt
delays and refresh outages, and builds the Finding_Card of every
Decision_Time with :func:`~fse.notify.finding_card.build_card` from that
Decision_Time's payload, MarketView and EngineState. Each card's JSON is
checked against the payload and the MarketView; the watch levels against a
brute-force scan of every Snapshot's Nodes converted to the instrument.

**Validates: Requirements 25.1, 25.2, 25.3, 25.4**
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema import StrategyConfig
from fse.engine.levels import ConvertedMap, convert_map, ticks_to_points
from fse.engine.nodes import classify
from fse.engine.planner import CancelOrder, PlaceBracket
from fse.engine.types import Snapshot, Unavailable
from fse.live.runner import LiveDecision, LiveSession
from fse.logio.canonical_json import ny_iso
from fse.notify.finding_card import (
    CARD_REASON_LIMIT,
    CARD_SETUP_LIMIT,
    FLAT,
    NONE,
    TRINITY_SYMBOLS,
    UNAVAILABLE,
    CardLedger,
    build_card,
    setup_key_text,
)
from tests.strategies.backtest_inputs import MarketInputs, market_inputs, strategy_configs
from tests.strategies.live_sessions import Driver, LivePlan, live_plans

CARD_KEYS: Final = {
    "kind",
    "decision_time",
    "decision_time_ny",
    "order_mode",
    "map",
    "spot",
    "king_flips",
    "regime",
    "map_grade",
    "trinity",
    "setups",
    "setups_not_listed",
    "working_orders",
    "armed",
    "cancelled",
    "positions",
    "watch",
    "notes",
}
_RANK: Final = {"A_Plus": 0, "Alert_2R": 1, "Pass": 2}


@st.composite
def configs(draw: st.DrawFn) -> StrategyConfig:
    data = draw(strategy_configs()).model_dump(mode="python", by_alias=True)
    data["live"]["live_max_snapshot_age_s"] = draw(st.sampled_from((30, 900)), label="max age")
    return StrategyConfig.model_validate(data)


def _listed(value: Any) -> list[Any]:
    """A card list: ``none`` for empty, never a missing key."""
    if value == NONE:
        return []
    assert isinstance(value, list), value
    assert value, value
    return value


def _check(live: LiveSession, decision: LiveDecision, seen: dict[str, bool]) -> None:
    payload = decision.result.payload
    view = live.inputs.view(payload.t)
    p = live.engine.params
    ledger = CardLedger()
    ledger.record(payload)
    card = build_card(
        payload,
        view,
        decision.result.state,
        instruments=p.instruments,
        node_params=p.node_params,
        level_params=p.level_params,
        working=live.routing.paper.working_orders(),
        order_mode=live.routing.mode,
        since=ledger,
    )
    j: Any = card.to_jsonable()
    assert set(j) == CARD_KEYS
    assert j["decision_time"] == payload.t
    assert j["decision_time_ny"] == ny_iso(payload.t)
    assert j["order_mode"] == "paper"
    # Map fields: one per configured (symbol, metric), in Map_State order.
    ms = view.map_state()
    assert [(m["symbol"], m["metric"]) for m in j["map"]] == list(ms.entries)
    for m, e in zip(j["map"], payload.map_entries, strict=True):
        assert set(m) == {"symbol", "metric", "as_of", "king", "floor", "ceiling",
                          "gatekeepers", "air_pockets"}  # fmt: skip
        if e.missing:
            seen["missing"] = True
            assert m["as_of"] == UNAVAILABLE
            for name in ("king", "floor", "ceiling"):
                assert m[name] == {"strike": UNAVAILABLE, "level": UNAVAILABLE}
        else:
            assert m["as_of"] == ny_iso(e.as_of_ns or 0)
            for name, strike in (("king", e.king), ("floor", e.floor), ("ceiling", e.ceiling)):
                assert m[name]["strike"] == (UNAVAILABLE if strike is None else strike)
                assert "level" in m[name]
    symbols = list(dict.fromkeys(s for s, _ in ms.entries))
    assert list(j["spot"]) == symbols
    for symbol in symbols:
        snaps = [x for x in ms.snapshots() if x.symbol == symbol]
        assert (j["spot"][symbol] == UNAVAILABLE) == (not snaps)
    assert list(j["trinity"]) == list(TRINITY_SYMBOLS)
    assert j["regime"]
    assert j["map_grade"]
    # Setups.
    listed = _listed(j["setups"])
    decisions = payload.setups
    order = sorted(range(len(decisions)), key=lambda i: (_RANK[decisions[i].evaluation.grade], i))
    expected = order[:CARD_SETUP_LIMIT]
    assert len(listed) == len(expected) <= CARD_SETUP_LIMIT
    assert j["setups_not_listed"] == len(decisions) - len(expected)
    for item, i in zip(listed, expected, strict=True):
        d = decisions[i]
        assert item["key"] == setup_key_text(d.setup.key)
        assert item["grade"] == d.evaluation.grade
        reasons = [r.reason for r in d.evaluation.rejections][:CARD_REASON_LIMIT]
        assert _listed(item["reasons"]) == reasons
    seen["zero setups" if not decisions else "setups"] = True
    if len(decisions) > CARD_SETUP_LIMIT:
        seen["more than 5 setups"] = True
    # Orders: armed and cancelled since the previous sent card (here: this Decision_Time).
    armed = [i.entry.client_id for i in payload.intents if isinstance(i, PlaceBracket)]
    assert [o["client_id"] for o in _listed(j["armed"])] == armed
    cancelled = [i.client_id for i in payload.intents if isinstance(i, CancelOrder)]
    assert _listed(j["cancelled"]) == cancelled
    working = [o.client_id for o in live.routing.paper.working_orders()]
    assert [o["client_id"] for o in _listed(j["working_orders"])] == working
    # Positions: per instrument, flat or one line per open plan.
    plans = [x for x in decision.result.state.book.plans if x.status == "open"]
    got = j["positions"]
    for instrument in p.instruments:
        mine = [x for x in got if x["instrument"] == instrument]
        open_plans = [x for x in plans if x.instrument == instrument]
        if not open_plans:
            assert mine == [{"instrument": instrument, "direction": FLAT, "contracts": NONE,
                             "entry": NONE, "stop": NONE, "unrealized_r": NONE}]  # fmt: skip
        else:
            seen["open position"] = True
            assert [x["contracts"] for x in mine] == [x.open_qty for x in open_plans]
            assert [x["stop"] for x in mine] == [ticks_to_points(x.stop_price) for x in open_plans]
    # Watch levels against a brute-force scan.
    converted = convert_map(view, p.level_params)
    prices = {f.instrument: f.price for f in payload.futures}
    assert [w["instrument"] for w in j["watch"]] == list(p.instruments)
    for w in j["watch"]:
        price = prices[w["instrument"]]
        if isinstance(price, Unavailable):
            assert (w["price"], w["above"], w["below"]) == (UNAVAILABLE,) * 3
            continue
        levels = set()
        for key, entry in ms.entries.items():
            conv = converted.get(key)
            if not isinstance(entry, Snapshot) or not isinstance(conv, ConvertedMap):
                continue
            if conv.conversion.instrument != w["instrument"]:
                continue
            levels |= {conv.level_of(n) for n in classify(entry, p.node_params).nodes}
        above = [lv for lv in levels if lv > price]
        below = [lv for lv in levels if lv < price]
        assert w["price"] == ticks_to_points(price)
        assert w["above"] == (ticks_to_points(min(above)) if above else NONE)
        assert w["below"] == (ticks_to_points(max(below)) if below else NONE)
        if above or below:
            seen["watch level"] = True


# Feature: skylit-futures-strategy-engine, Property 80: Card completeness
@given(market=market_inputs(gaps=True), cfg=configs(), plan=live_plans(halts=False))
def test_every_card_holds_every_field_and_marks_missing_data(
    market: MarketInputs, cfg: StrategyConfig, plan: LivePlan
) -> None:
    plan = LivePlan(plan.seed, 300, plan.max_delay_s, plan.outage, 0.02, ())
    seen: dict[str, bool] = {}
    with TemporaryDirectory(prefix="fse-p80-") as tmp:
        driver = Driver(Path(tmp), market, cfg)
        driver.run_market([plan], hook=lambda live, d: _check(live, d, seen), every_session=True)
    for name in sorted(seen):
        event(name)
