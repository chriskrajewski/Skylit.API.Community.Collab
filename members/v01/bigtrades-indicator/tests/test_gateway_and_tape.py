# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
from __future__ import annotations

from bigtrades.aggregator import SweepAggregator, Thresholds
from bigtrades.format import format_big_trade
from bigtrades.gateway import parse_gateway_trade, parse_timestamp_us
from bigtrades.models import Side
from bigtrades.tape import TapeRecorder, replay


def test_parse_single_and_batched_payloads():
    row = {"symbolId": "F.US.EP", "price": 7830.25, "timestamp": "2026-10-08T14:32:05Z", "type": 0, "volume": 2}
    (p,) = parse_gateway_trade(["CON.F.US.EP.Z26", row])
    assert (p.root, p.side, p.size, p.price) == ("ES", Side.BUY, 2, 7830.25)
    batch = parse_gateway_trade(["CON.F.US.ENQ.Z26", [row, {**row, "type": 1}]])
    assert [x.root for x in batch] == ["NQ", "NQ"]
    assert [x.side for x in batch] == [Side.BUY, Side.SELL]


def test_bad_rows_are_dropped():
    good = {"price": 1.0, "timestamp": "2026-10-08T14:32:05Z", "type": 1, "volume": 1}
    rows = [
        {**good, "type": 7},  # unknown side
        {**good, "price": None},
        {**good, "volume": 0},
        {**good, "timestamp": "not a time"},
        "garbage",
    ]
    assert parse_gateway_trade(["CON.F.US.EP.Z26", rows]) == []


def test_timestamp_with_seven_fraction_digits():
    a = parse_timestamp_us("2026-10-08T14:32:05.1234567+00:00")
    b = parse_timestamp_us("2026-10-08T14:32:05.123456Z")
    assert a == b


def test_replay_fixture_reports_finished_size(fixture_lines):
    agg = SweepAggregator(Thresholds({"ES": 200}))
    (trade,) = replay(fixture_lines("tape_es_sweep.jsonl"), agg)
    assert (trade.size, trade.prints, trade.side) == (501, 6, Side.SELL)
    text = format_big_trade(trade)
    assert text.startswith("ES big SELL 501 @ 7830.00→7829.50 | avg fill ")
    assert "| 6 prints | 14:32:05.123 UTC" in text


def test_replay_first_qualify_reports_crossing_size(fixture_lines):
    agg = SweepAggregator(Thresholds({"ES": 200}), publish_mode="first_qualify")
    (trade,) = replay(fixture_lines("tape_es_sweep.jsonl"), agg)
    assert trade.size == 206
    assert format_big_trade(trade).endswith("first qualify (may still be filling)")


def test_recorder_writes_jsonl_that_replays(tmp_path, fixture_lines):
    recorder = TapeRecorder(tmp_path / "tape")
    import json

    for line in fixture_lines("tape_es_sweep.jsonl"):
        record = json.loads(line)
        recorder.record(record["arguments"], record["recv_ms"])
    (day_file,) = (tmp_path / "tape").iterdir()
    trades = replay(day_file.read_text().splitlines(), SweepAggregator(Thresholds({"ES": 200})))
    assert [t.size for t in trades] == [501]


def test_recorder_is_off_by_default():
    from bigtrades.config import Settings

    assert Settings.from_env({}).tape_dir == ""
