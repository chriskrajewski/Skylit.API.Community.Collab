"""Unit tests for the decision log (design "Decision log", Req 18.6-18.7).

Payloads are built by hand from the engine's own types: one Decision_Time
with every Req 18.7 field filled (a present and a missing Snapshot, a priced
and an unavailable Futures_Price, a rejected and a placed Candidate_Setup,
orders, fills and bar events), and one with Map_State missing every Snapshot.

**Validates: Requirements 18.5, 18.6, 18.7**
"""

from __future__ import annotations

import json
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from fse.backtest.decision_log import (
    DECISION_LOG_FILE_NAME,
    DecisionLog,
    decision_entry,
    map_key,
)
from fse.engine.gates.catalog import DataUnavailable
from fse.engine.gates.registry import GateEvaluation, GateResult, RejectionReason
from fse.engine.planner import (
    CancelOrder,
    EntryCancelled,
    OrderFill,
    PlaceBracket,
    StopMoved,
)
from fse.engine.regime import RegimeResult
from fse.engine.risk import Lockout
from fse.engine.sizing import SIZING_STEPS, StepOutcome
from fse.engine.step import (
    CardTriggers,
    DecisionPayload,
    ExternalBlock,
    FuturesLog,
    MapEntryLog,
    SetupDecision,
    Sized,
)
from fse.engine.types import (
    CandidateSetup,
    DetectionSkip,
    Fill,
    MissingInput,
    Order,
    OrderKind,
    OrderRole,
    SetupInputs,
    SetupKey,
    Side,
    SnapshotAsOf,
    SourceNodeRef,
    Unavailable,
)
from fse.logio import REDACTED, LogWriter, Redactor
from fse.logio.canonical_json import dumps, ny_iso, to_jsonable
from fse.timekit import NS_PER_SECOND, ny_instant

SESSION = date(2026, 3, 5)
T1 = ny_instant(SESSION, time(10, 0))
T2 = T1 + 60 * NS_PER_SECOND
AS_OF = T1 - 3 * NS_PER_SECOND
SECRET = "fake-decision-log-secret-0000"


def key(strike: float) -> SetupKey:
    return SetupKey("MES", "floor_ceiling_bounce", strike, "long", SESSION, 1)


def candidate(strike: float, t: int = T1) -> CandidateSetup:
    level = int(strike * 4)
    inputs = SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", AS_OF),),
        source_spot=5800.0,
        futures_price=23008,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=5.0,
        regime="Positive_Gamma",
        map_grade="Neutral_Map",
        stop_rule="fixed_ticks",
    )
    source = SourceNodeRef("SPX", "gamma", strike, 3.0e9, level, (strike - 5.0, strike + 5.0))
    return CandidateSetup(
        key(strike), "floor_ceiling_bounce", t, level, level - 20, (level + 60,), "fixed_r",
        source, inputs,
    )  # fmt: skip


def order(
    client_id: str, role: OrderRole, side: Side, kind: OrderKind, price: int, qty: int = 2
) -> Order:
    return Order(client_id, key(5750.0), "MES", side, kind, qty, price, T1, role)


REJECTED = candidate(5750.0)
PLACED = candidate(5760.0)
UNAVAILABLE = DataUnavailable("no midpoint at t")
REJECTED_EVAL = GateEvaluation(
    REJECTED.key,
    T1,
    (
        GateResult(REJECTED.key, T1, "stale_map", "pass", 3.0, 90),
        GateResult(REJECTED.key, T1, "midpoint", "fail", UNAVAILABLE, 0.5),
        GateResult(REJECTED.key, T1, "map_grade", "disabled", "Neutral_Map", ("A_Plus_Map",)),
    ),
    (RejectionReason("midpoint", UNAVAILABLE, 0.5),),
    "Pass",
)
PLACED_EVAL = GateEvaluation(
    PLACED.key, T1, (GateResult(PLACED.key, T1, "stale_map", "pass", 3.0, 90),), (), "A_Plus"
)
SIZED = Sized(2, tuple(StepOutcome(s, 2, s == "base_contracts") for s in SIZING_STEPS))
BRACKET = PlaceBracket(
    order("c1", "entry", "buy", "limit", 23040),
    order("c2", "stop", "sell", "stop", 23020),
    (order("c3", "tp1", "sell", "limit", 23100),),
)
CANCEL = CancelOrder("c0", T1)
EXIT_FILL = OrderFill(
    order("c9", "tp1", "sell", "limit", 23060, qty=1),
    Fill("c9", T1 - 60 * NS_PER_SECOND, 23060, 1, Decimal("0.37")),
)
STOP_MOVED = StopMoved(key(5750.0), T1, 23020, 23040, ("breakeven",))
CANCELLED = EntryCancelled(key(5740.0), T1, ("max_age",))
SKIP = DetectionSkip(key(5770.0), T1, "no_target")
LOCKOUT = Lockout("max_trades", T1 - 600 * NS_PER_SECOND, SESSION)
BLOCK = ExternalBlock("halt", "MES", "halted by the exchange")
REGIME = RegimeResult("Positive_Gamma", 4.0e9, 1.0e9, 2.0, 0.5, 3.5e9, False)
NO_CARDS = CardTriggers((), (), order_event=True, first_alerts=())


def full_payload(t: int = T1, block: ExternalBlock = BLOCK) -> DecisionPayload:
    return DecisionPayload(
        session=SESSION,
        t=t,
        map_entries=(
            MapEntryLog("SPX", "gamma", False, AS_OF, 5750.0, 5700.0, 5850.0),
            MapEntryLog("NDXP", "vanna", True, None, None, None, None),
        ),
        snapshot_age_ns=t - AS_OF,
        futures=(FuturesLog("MES", 23008), FuturesLog("MNQ", Unavailable("no bar at t"))),
        regime=REGIME,
        map_grade="Neutral_Map",
        setups=(
            SetupDecision(REJECTED, REJECTED_EVAL, None, None),
            SetupDecision(PLACED, PLACED_EVAL, SIZED, BRACKET),
        ),
        skips=(SKIP,),
        intents=(CANCEL, BRACKET),
        management=(CANCELLED,),
        fills=(EXIT_FILL,),
        bar_events=(STOP_MOVED,),
        external_blocks=(block,),
        lockouts=(LOCKOUT,),
        cards=NO_CARDS,
    )


def empty_map_payload(t: int) -> DecisionPayload:
    missing = MissingInput(("SPX/gamma",))
    return DecisionPayload(
        session=SESSION,
        t=t,
        map_entries=(MapEntryLog("SPX", "gamma", True, None, None, None, None),),
        snapshot_age_ns=Unavailable("no Snapshot"),
        futures=(FuturesLog("MES", Unavailable("no bar at t")),),
        regime=missing,
        map_grade=missing,
        setups=(),
        skips=(),
        intents=(),
        management=(),
        fills=(),
        bar_events=(),
        external_blocks=(),
        lockouts=(),
        cards=CardTriggers((), (), order_event=False, first_alerts=()),
    )


def writer(*secrets: str) -> LogWriter:
    return LogWriter(Redactor(secrets))


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------- the entry


def test_entry_records_every_req_18_7_field() -> None:
    entry = decision_entry(full_payload())

    assert entry["session"] == "2026-03-05"
    assert (entry["t"], entry["t_ny"]) == (T1, "2026-03-05T10:00:00-05:00")
    assert entry["map"] == {
        "SPX/gamma": {
            "asOf": AS_OF,
            "asOf_ny": ny_iso(AS_OF),
            "king": 5750.0,
            "floor": 5700.0,
            "ceiling": 5850.0,
        },
        "NDXP/vanna": {"missing": True},
    }
    assert entry["snapshot_age_ns"] == 3 * NS_PER_SECOND
    assert entry["futures"] == {"MES": 23008, "MNQ": {"reason": "no bar at t"}}
    assert entry["regime"] == "Positive_Gamma"
    assert entry["regime_measures"] == {
        "raw_gex": 4.0e9,
        "raw_vex": 1.0e9,
        "normalized_gex": 2.0,
        "normalized_vex": 0.5,
        "net_gex": 3.5e9,
        "vanna_withheld": False,
    }
    assert entry["map_grade"] == "Neutral_Map"
    assert entry["skips"] == [to_jsonable(SKIP)]
    assert entry["fills"] == [to_jsonable(EXIT_FILL)]
    assert entry["lockouts"] == [to_jsonable(LOCKOUT)]
    assert entry["external_blocks"] == [
        {"kind": "halt", "instrument": "MES", "detail": BLOCK.detail}
    ]
    assert entry["cards"] == to_jsonable(NO_CARDS)


def test_entry_records_each_setup_with_gates_rejections_and_grade() -> None:
    entry = decision_entry(full_payload())
    setups = entry["setups"]
    assert isinstance(setups, list)
    rejected, placed = setups

    expected: dict[str, object] = {
        "key": to_jsonable(REJECTED.key),
        "detector_id": "floor_ceiling_bounce",
        "entry": REJECTED.entry,
        "stop": REJECTED.stop,
        "targets": list(REJECTED.targets),
        "exit_mode": "fixed_r",
        "source": to_jsonable(REJECTED.source),
        "inputs": to_jsonable(REJECTED.inputs),
        "gates": [
            {"id": "stale_map", "result": "pass", "measured": 3.0, "threshold": 90},
            {
                "id": "midpoint",
                "result": "fail",
                "measured": {"kind": "data_unavailable", "reason": "no midpoint at t"},
                "threshold": 0.5,
            },
            {
                "id": "map_grade",
                "result": "disabled",
                "measured": "Neutral_Map",
                "threshold": ["A_Plus_Map"],
            },
        ],
        "rejections": [
            {
                "reason": "midpoint",
                "measured": {"kind": "data_unavailable", "reason": "no midpoint at t"},
                "threshold": 0.5,
            }
        ],
        "grade": "Pass",
        "sizing": None,
        "placement": None,
    }
    assert rejected == expected
    assert isinstance(placed, dict)
    assert placed["grade"] == "A_Plus"
    assert placed["sizing"] == {"type": "Sized", **to_jsonable_dict(SIZED)}
    assert placed["placement"] == {"type": "PlaceBracket", **to_jsonable_dict(BRACKET)}


def test_entry_tags_orders_and_events_with_their_type() -> None:
    entry = decision_entry(full_payload())
    assert entry["orders"] == [
        {"type": "CancelOrder", "client_id": "c0", "at": T1},
        {"type": "PlaceBracket", **to_jsonable_dict(BRACKET)},
    ]
    assert entry["management"] == [{"type": "EntryCancelled", **to_jsonable_dict(CANCELLED)}]
    assert entry["bar_events"] == [{"type": "StopMoved", **to_jsonable_dict(STOP_MOVED)}]


def test_entry_with_no_snapshot_records_missing_markers() -> None:
    entry = decision_entry(empty_map_payload(T1))
    assert entry["map"] == {"SPX/gamma": {"missing": True}}
    assert entry["snapshot_age_ns"] == {"reason": "no Snapshot"}
    assert entry["futures"] == {"MES": {"reason": "no bar at t"}}
    assert entry["regime"] == {"names": ["SPX/gamma"]}
    assert entry["regime_measures"] is None
    assert entry["map_grade"] == {"names": ["SPX/gamma"]}
    for field in ("setups", "skips", "orders", "management", "fills", "bar_events"):
        assert entry[field] == []


def test_map_key() -> None:
    assert map_key("NDXP", "vanna") == "NDXP/vanna"


def to_jsonable_dict(obj: object) -> dict[str, object]:
    out = to_jsonable(obj)
    assert isinstance(out, dict)
    return dict(out)


# ---------------------------------------------------------------- the file


def test_log_writes_one_canonical_line_per_decision_time(tmp_path: Path) -> None:
    path = tmp_path / DECISION_LOG_FILE_NAME
    payloads = (full_payload(T1), empty_map_payload(T2))
    with DecisionLog(writer(), path) as log:
        for p in payloads:
            log.write(p)
        assert (log.count, log.last_t) == (2, T2)

    written = lines(path)
    assert written == [dumps(decision_entry(p)) for p in payloads]
    assert [json.loads(line)["t"] for line in written] == [T1, T2]


def test_log_refuses_a_decision_time_not_after_the_previous(tmp_path: Path) -> None:
    path = tmp_path / DECISION_LOG_FILE_NAME
    with DecisionLog(writer(), path) as log:
        log.write(empty_map_payload(T2))
        with pytest.raises(ValueError, match="not after"):
            log.write(empty_map_payload(T1))
        with pytest.raises(ValueError, match="not after"):
            log.write(empty_map_payload(T2))
        assert log.count == 1
    assert len(lines(path)) == 1


def test_reruns_give_byte_identical_logs(tmp_path: Path) -> None:
    outputs = []
    for run in ("a", "b"):
        path = tmp_path / run / DECISION_LOG_FILE_NAME
        with DecisionLog(writer(), path) as log:
            log.write(full_payload(T1))
            log.write(empty_map_payload(T2))
        outputs.append(path.read_bytes())
    assert outputs[0] == outputs[1]
    assert outputs[0].endswith(b"\n")


def test_log_lines_are_redacted(tmp_path: Path) -> None:
    path = tmp_path / DECISION_LOG_FILE_NAME
    with DecisionLog(writer(SECRET), path) as log:
        log.write(full_payload(block=ExternalBlock("broker", None, f"token {SECRET}")))
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert json.loads(text)["external_blocks"][0]["detail"] == f"token {REDACTED}"


def test_log_refuses_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / DECISION_LOG_FILE_NAME
    path.write_text("earlier run\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        DecisionLog(writer(), path)
    assert path.read_text(encoding="utf-8") == "earlier run\n"


def test_closed_log_refuses_writes_and_closes_once(tmp_path: Path) -> None:
    log = DecisionLog(writer(), tmp_path / DECISION_LOG_FILE_NAME)
    log.close()
    log.close()
    with pytest.raises(ValueError, match="closed"):
        log.write(empty_map_payload(T1))
    assert log.path.read_bytes() == b""
