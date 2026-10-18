"""Unit tests for rich Finding_Card trade payloads and Pass why English."""

from __future__ import annotations

from datetime import date

import pytest

from fse.config.schema.gates import GATE_IDS
from fse.engine.gates.registry import GateEvaluation, GateResult, RejectionReason
from fse.engine.step import SetupDecision
from fse.engine.types import (
    Bar,
    CandidateSetup,
    SetupInputs,
    SetupKey,
    SnapshotAsOf,
    SourceNodeRef,
)
from fse.notify.finding_trade import (
    build_trade,
    gates_passed_text,
    load_pattern_odds,
    odds_for_pattern,
    premarket_summary,
    reason_to_english,
    reasons_to_why,
    trade_bars,
)
from fse.timekit import NS_PER_SECOND

T0 = 1_791_800_000 * NS_PER_SECOND
SESSION = date(2026, 10, 14)
ENTRY = 23641  # 5910.25
STOP = 23658  # 5914.50
TP1 = 23600
TP2 = 23520


def _key(pattern: str = "gatekeeper_fade", instrument: str = "MES") -> SetupKey:
    return SetupKey(instrument, pattern, 5910.0, "short", SESSION, 1)


def _inputs(*, stop_rule: str | None = "fixed_ticks") -> SetupInputs:
    return SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", T0 - 3 * NS_PER_SECOND),),
        source_spot=5910.0,
        futures_price=ENTRY,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=2.0,
        regime="Positive_Gamma",
        map_grade="Neutral_Map",
        stop_rule=stop_rule,
    )


def _bar(open_ns: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        "MES",
        "MESH6",
        60,
        open_ns,
        open_ns + 60 * NS_PER_SECOND,
        o,
        h,
        low,
        c,
        0.0,
        int(o * 4),
        int(h * 4),
        int(low * 4),
        int(c * 4),
        "atlas",
    )


def _setup(*, priced: bool = True) -> CandidateSetup:
    if not priced:
        source = SourceNodeRef("SPX", "gamma", 5910.0, 300.0, None, None)
        return CandidateSetup(
            _key(),
            "gatekeeper_fade",
            T0,
            None,
            None,
            (),
            "fixed_r",
            source,
            _inputs(stop_rule=None),
        )
    source = SourceNodeRef("SPX", "gamma", 5910.0, 300.0, ENTRY, (5908.0, 5912.0))
    return CandidateSetup(
        _key(), "gatekeeper_fade", T0, ENTRY, STOP, (TP1, TP2), "fixed_r", source, _inputs()
    )


def _eval(grade: str, *codes: str) -> GateEvaluation:
    key = _key()
    order = ("tap_count", "min_reward_risk", "trinity_agreement")
    results = tuple(
        GateResult(key, T0, gid, "fail" if gid in codes else "pass", 1.0, 0.0) for gid in order
    )
    rejections = tuple(RejectionReason(c, 1.0, 0.0) for c in codes)
    return GateEvaluation(key, T0, results, rejections, grade)  # type: ignore[arg-type]


def test_reason_to_english_covers_every_gate_id() -> None:
    for gid in GATE_IDS:
        text = reason_to_english(gid)
        assert text
        assert text != gid or "_" not in gid
    assert "taps" in reason_to_english("tap_count").lower()
    assert "reward" in reason_to_english("min_reward_risk").lower()
    assert reason_to_english("custom_unknown_gate") == "Custom unknown gate"


FAKE_ODDS = {
    "gatekeeper_fade": {
        "n": 100,
        "items": [
            {"pct": 50, "label": "Fill"},
            {"pct": 40, "label": "TP1"},
            {"pct": 30, "label": "TP2"},
        ],
    }
}
"""A synthetic odds table; the shipped ``configs/pattern_odds.json`` ships empty."""


@pytest.fixture
def fake_odds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fse.notify.finding_trade.load_pattern_odds", lambda *_a, **_k: FAKE_ODDS)


def test_the_shipped_odds_table_is_empty() -> None:
    assert load_pattern_odds() == {}
    assert odds_for_pattern("gatekeeper_fade") is None


@pytest.mark.usefixtures("fake_odds")
def test_odds_for_known_and_unknown_pattern() -> None:
    odds = odds_for_pattern("gatekeeper_fade")
    assert odds is not None
    assert "n=" in str(odds["label"])
    assert len(odds["items"]) == 3  # type: ignore[index]
    assert odds_for_pattern("not_a_real_pattern") is None


def test_trade_bars_slice() -> None:
    bars = [
        _bar(T0 + i * 60 * NS_PER_SECOND, 100.0, 101.0, 99.0, 100.5 + i * 0.01)
        for i in range(5)
    ]
    out = trade_bars(bars, limit=3)
    assert len(out) == 3
    assert out[-1]["c"] == bars[-1].c


@pytest.mark.usefixtures("fake_odds")
def test_build_trade_for_pass_and_a_plus() -> None:
    setup = _setup()
    pass_d = SetupDecision(setup, _eval("Pass", "tap_count", "min_reward_risk"), None, None)
    trade = build_trade(
        pass_d,
        spot=5884.25,
        map_json=[
            {
                "symbol": "ES",
                "metric": "gamma",
                "king": {"strike": 5900, "level": 5900.0},
                "ceiling": {"strike": 5925, "level": 5925.0},
                "floor": {"strike": 5850, "level": 5850.0},
                "gatekeepers": [{"strike": 5910, "level": 5910.0}],
                "air_pockets": [
                    [{"strike": 5890, "level": 5887.0}, {"strike": 5893, "level": 5893.0}]
                ],
            }
        ],
        bars=[
            _bar(T0, 5890.0, 5892.0, 5880.0, 5884.25),
            _bar(T0 + 60 * NS_PER_SECOND, 5884.25, 5886.0, 5882.0, 5884.25),
        ],
        flip="5925 → 5905",
    )
    assert trade is not None
    assert trade["grade"] == "Pass"
    assert trade["gates"] == "1/3"
    assert trade["bars"]
    assert trade["dayPct"] is not None
    assert trade["odds"] is not None
    assert trade["actionLabel"] == "Pass"
    assert "no order" in str(trade["action"]).lower()
    assert trade["plan"]["if"]
    a_plus = SetupDecision(setup, _eval("A_Plus"), None, None)
    armed = build_trade(a_plus, spot=5884.25, map_json=[], bars=None)
    assert armed is not None
    assert armed["grade"] == "A_Plus"
    assert armed["actionLabel"] == "Armed"
    assert armed["bars"] == []


def test_premarket_summary_and_why() -> None:
    assert "waiting for the open" in premarket_summary(
        regime="Whipsaw", map_grade="C", setups_listed=0
    )
    why = reasons_to_why(("tap_count", "min_reward_risk"))
    assert len(why) == 2
    assert all(" " in line for line in why)


def test_gates_passed_text() -> None:
    ev = _eval("Pass", "tap_count")
    assert gates_passed_text(ev) == "2/3"


def test_unpriced_setup_yields_no_trade() -> None:
    setup = _setup(priced=False)
    key = _key()
    ev = GateEvaluation(
        key,
        T0,
        (GateResult(key, T0, "stale_map", "pass", 1.0, 0.0),),
        (RejectionReason("no_conversion_price", 0.0, 0.0),),
        "Pass",
    )
    d = SetupDecision(setup, ev, None, None)
    assert build_trade(d, spot=5884.25, map_json=[]) is None

