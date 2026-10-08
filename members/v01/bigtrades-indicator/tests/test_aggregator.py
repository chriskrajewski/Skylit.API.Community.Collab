# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
from __future__ import annotations

import pytest

from bigtrades.aggregator import SweepAggregator, Thresholds
from bigtrades.models import Side

ES_200 = Thresholds({"ES": 200, "NQ": 100})


def test_split_prints_same_timestamp_become_one_trade(make_print):
    agg = SweepAggregator(ES_200)
    out = []
    for i, (size, price) in enumerate([(120, 7830.0), (86, 7830.0), (140, 7829.75), (155, 7829.5)]):
        out += agg.on_print(make_print(size, price=price, ts_ms=0), now_ms=i)
    assert out == []  # final mode waits for the sweep to finish
    (trade,) = agg.flush(now_ms=10_000)
    assert trade.size == 501
    assert trade.prints == 4
    assert (trade.first_price, trade.last_price) == (7830.0, 7829.5)
    assert (trade.low, trade.high) == (7829.5, 7830.0)
    assert trade.avg_price == pytest.approx((120 * 7830 + 86 * 7830 + 140 * 7829.75 + 155 * 7829.5) / 501)
    assert trade.mode == "final"


def test_side_flip_closes_sweep(make_print):
    agg = SweepAggregator(ES_200)
    agg.on_print(make_print(150, Side.SELL), now_ms=0)
    agg.on_print(make_print(60, Side.SELL), now_ms=1)
    out = agg.on_print(make_print(5, Side.BUY), now_ms=2)
    assert [(t.side, t.size) for t in out] == [(Side.SELL, 210)]
    assert agg.flush(now_ms=10_000) == []  # the 5-lot buy never qualifies


def test_quiet_close_publishes_last_sweep(make_print):
    agg = SweepAggregator(ES_200, quiet_ms=300)
    agg.on_print(make_print(250), now_ms=1_000)
    assert agg.flush(now_ms=1_299) == []
    (trade,) = agg.flush(now_ms=1_300)
    assert trade.size == 250


def test_timestamp_gap_closes_when_join_is_zero(make_print):
    agg = SweepAggregator(ES_200, join_ms=0)
    agg.on_print(make_print(150, ts_ms=0), now_ms=0)
    out = agg.on_print(make_print(150, ts_ms=1), now_ms=1)  # 1 ms later: a new sweep
    assert out == []  # neither half reaches 200
    assert agg.flush(now_ms=10_000) == []


def test_join_tolerance_merges_close_timestamps(make_print):
    agg = SweepAggregator(ES_200, join_ms=5)
    agg.on_print(make_print(150, ts_ms=0), now_ms=0)
    agg.on_print(make_print(100, ts_ms=4), now_ms=1)
    agg.on_print(make_print(10, ts_ms=8), now_ms=2)  # 4 ms after the previous print: still joins
    out = agg.on_print(make_print(10, ts_ms=20), now_ms=3)  # 12 ms gap: closes
    assert [t.size for t in out] == [260]


def test_first_qualify_publishes_once_at_crossing(make_print):
    agg = SweepAggregator(ES_200, publish_mode="first_qualify")
    assert agg.on_print(make_print(120), now_ms=0) == []
    (first,) = agg.on_print(make_print(86), now_ms=1)
    assert (first.size, first.mode) == (206, "first_qualify")
    assert agg.on_print(make_print(295), now_ms=2) == []
    assert agg.flush(now_ms=10_000) == []  # nothing more for the same sweep


def test_final_reports_full_size_where_first_qualify_reports_partial(make_print):
    sizes = [120, 86, 140, 155]
    final, first = SweepAggregator(ES_200), SweepAggregator(ES_200, publish_mode="first_qualify")
    got_first = []
    for i, size in enumerate(sizes):
        final.on_print(make_print(size), now_ms=i)
        got_first += first.on_print(make_print(size), now_ms=i)
    assert [t.size for t in got_first] == [206]
    assert [t.size for t in final.flush(now_ms=10_000)] == [501]


def test_roots_are_independent(make_print):
    agg = SweepAggregator(ES_200)
    agg.on_print(make_print(150, root="ES"), now_ms=0)
    agg.on_print(make_print(120, root="NQ", contract_id="CON.F.US.ENQ.Z26", price=31200.0), now_ms=1)
    agg.on_print(make_print(60, root="ES"), now_ms=2)
    roots = sorted((t.root, t.size) for t in agg.flush(now_ms=10_000))
    assert roots == [("ES", 210), ("NQ", 120)]


def test_unknown_root_without_catch_all_never_publishes(make_print):
    agg = SweepAggregator(ES_200)
    agg.on_print(make_print(10_000, root="CL", contract_id="CON.F.US.CLE.X26"), now_ms=0)
    assert agg.flush(now_ms=10_000) == []
    catch_all = SweepAggregator(Thresholds({"*": 50}))
    catch_all.on_print(make_print(60, root="CL", contract_id="CON.F.US.CLE.X26"), now_ms=0)
    assert [t.size for t in catch_all.flush(now_ms=10_000)] == [60]


def test_close_all_on_shutdown(make_print):
    agg = SweepAggregator(ES_200)
    agg.on_print(make_print(300), now_ms=0)
    assert [t.size for t in agg.close_all()] == [300]
    assert agg.close_all() == []


def test_thresholds_parse_and_validation():
    t = Thresholds.parse(" es:200 , NQ:100,")
    assert (t.for_root("ES"), t.for_root("nq"), t.for_root("CL")) == (200, 100, None)
    with pytest.raises(ValueError):
        Thresholds.parse("ES200")
    with pytest.raises(ValueError):
        SweepAggregator(t, publish_mode="sometimes")
