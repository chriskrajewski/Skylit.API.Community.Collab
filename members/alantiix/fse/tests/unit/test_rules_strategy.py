"""The declarative rules plugin: the example spec, and a host backtest."""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from pathlib import Path

import yaml

from fse.data.cache import DataCache
from fse.strategy.book import RBar, RulesBook
from fse.strategy.spec import EXAMPLE_SPEC, StrategySpec, example_spec, validate_document
from fse.strategy.warmup import feed_warmup, history_sessions, indicator_warmup_bars
from fse.timekit import NS_PER_MINUTE, ny_datetime, ny_instant
from tests.fakes.dataplane import make_bar
from tests.unit.test_backtest_plugin import WED, fse, history_days, session_bars

DAY = date(2026, 10, 14)


def _bar(minute: int, o: int, h: int, low: int, c: int) -> RBar:
    total = 9 * 60 + 30 + minute
    hh, mm = divmod(total, 60)
    return RBar(
        open_ns=minute,
        close_ns=minute + 60,
        o=o / 4,
        h=h / 4,
        low=low / 4,
        c=c / 4,
        v=10,
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        day=DAY,
        wall=time(hh, mm),
    )


def test_example_validates_and_names_rules_it_cannot_express() -> None:
    report = validate_document(EXAMPLE_SPEC)
    assert report.ok, report.errors
    assert report.spec_hash
    denied = dict(EXAMPLE_SPEC)
    denied["ichimoku"] = {"length": 9}
    denied["options"] = {"premium": 1}
    bad = validate_document(denied)
    assert not bad.ok
    named = " ".join(f"{item.path} {item.rule}" for item in bad.unsupported)
    assert "ichimoku" in named
    assert "options" in named
    both = dict(EXAMPLE_SPEC)
    both["side"] = "both"
    assert any("short_when_all" in line for line in validate_document(both).errors)


def test_example_pullback_plans_four_legs_and_catch_up_does_not_enter() -> None:
    book = RulesBook(example_spec(), "MNQ")
    for minute in range(40):
        assert book.on_bar(_bar(minute, 80000, 80000, 80000, 80000)) == []
    spike = _bar(40, 80000, 80120, 79990, 80080)
    quiet = book.on_bar(spike, live=False)
    assert quiet == []
    book2 = RulesBook(example_spec(), "MNQ")
    for minute in range(40):
        book2.on_bar(_bar(minute, 80000, 80000, 80000, 80000))
    plans = book2.on_bar(spike)
    assert plans, book2.take_logs()
    assert {plan.action for plan in plans} == {"enter_limit"}
    assert len(plans) == 4
    assert {plan.qty for plan in plans}
    assert sum(plan.qty for plan in plans) >= 1
    entry = plans[0].price_t
    stop = plans[0].stop_t
    assert entry is not None
    assert stop is not None
    assert stop < entry < spike.c_t
    targeted = [plan for plan in plans if plan.target_t is not None]
    assert len(targeted) == 1
    assert targeted[0].target_t == entry + 4 * (entry - stop)


def test_two_closes_under_the_fast_ema_scale_out_that_leg() -> None:
    book = RulesBook(example_spec(), "MNQ")
    for minute in range(40):
        book.on_bar(_bar(minute, 80000, 80000, 80000, 80000))
    plans = book.on_bar(_bar(40, 80000, 80120, 79960, 80080))
    assert plans
    for plan in plans:
        assert plan.price_t is not None
        book.on_fill(plan.tag, plan.price_t)
    # Keep printing lower lows so the fast EMA is above the close for two bars.
    flat = plans[0].price_t or 80000
    exited: list[str] = []
    for minute, close in enumerate((flat - 80, flat - 120, flat - 160), start=41):
        for plan in book.on_bar(_bar(minute, close, close + 4, close - 4, close)):
            if plan.action == "flatten":
                exited.append(plan.tag)
    assert exited, book.take_logs()


def test_daily_bar_does_not_enter_until_the_next_session() -> None:
    spec = StrategySpec.model_validate(
        {
            "name": "daily-market",
            "side": "long",
            "symbols": ["MES"],
            "timeframe": {"bar": "1d"},
            "session": {"start": "09:30:00", "end": "16:00:00"},
            "indicators": [],
            "conditions": [
                {"id": "up", "op": "close_above", "left": "close", "right": "open"}
            ],
            "entry": {"kind": "market", "when_all": ["up"]},
            "stop": {"kind": "ticks", "ticks": 8},
            "scale_outs": [{"pct": 100, "at_r": 2}],
            "sizing": {"mode": "fixed_risk", "risk_usd": "100", "max_contracts": 2},
        }
    )
    book = RulesBook(spec, "MES")
    day = date(2026, 10, 14)
    nxt = day + timedelta(days=1)

    def one(d: date, hh: int, mm: int) -> RBar:
        return RBar(
            open_ns=0,
            close_ns=60,
            o=5000,
            h=5001,
            low=4999,
            c=5000.5,
            v=1,
            o_t=20000,
            h_t=20004,
            l_t=19996,
            c_t=20002,
            day=d,
            wall=time(hh, mm),
        )

    assert book.on_bar(one(day, 10, 0)) == []
    assert book.on_bar(one(day, 15, 50)) == []
    plans = book.on_bar(one(nxt, 10, 0))
    assert [plan.action for plan in plans] == ["enter_market"]


def test_daily_dema_warmup_is_one_hundred_sessions() -> None:
    doc = json.loads(json.dumps(EXAMPLE_SPEC))
    doc["timeframe"] = {"bar": "1d"}
    doc["indicators"].append({"id": "slow", "kind": "dema", "length": 50})
    spec = StrategySpec.model_validate(doc)
    assert indicator_warmup_bars(spec) >= 99
    assert history_sessions(spec) == 100
    assert history_sessions(example_spec()) == 1


def test_feed_warmup_warms_a_daily_ema_and_does_not_enter(tmp_path: Path) -> None:
    spec = StrategySpec.model_validate(
        {
            "name": "daily-ema",
            "side": "long",
            "symbols": ["MNQ"],
            "timeframe": {"bar": "1d"},
            "session": {"start": "09:30:00", "end": "16:00:00"},
            "indicators": [{"id": "slow", "kind": "ema", "length": 2}],
            "conditions": [{"id": "up", "op": "close_above", "left": "close", "right": "open"}],
            "entry": {"kind": "market", "when_all": ["up"]},
            "stop": {"kind": "ticks", "ticks": 8},
            "scale_outs": [{"pct": 100, "at_r": 2}],
            "sizing": {"mode": "fixed_risk", "risk_usd": "100", "max_contracts": 2},
        }
    )
    days = [*history_days(WED, 2), WED]
    cache = tmp_path / "cache"
    with DataCache(cache) as store:
        for d in days:
            bars = [
                make_bar("MNQ", bar.open_ns, 80000, 80004, 79996, 80002) for bar in session_bars(d)
            ]
            store.bars.write_session("MNQ", 60, d, bars, contract="MNQZ6", source="projectx")
    (cache / "warmup.json").write_text(
        json.dumps({"MNQ": [d.isoformat() for d in sorted(days)]}), encoding="utf-8"
    )
    book = RulesBook(spec, "MNQ")
    fed = feed_warmup({"MNQ": book}, cache)
    assert fed > 0
    assert book.take_logs() == []
    assert book._values.get("slow") is not None


def test_example_runs_through_backtest_plugin(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    days = [*history_days(WED, 2), WED]
    script = [(80000, 80000, 80000, 80000)] * 30
    script.append((80000, 80120, 79990, 80080))
    script.append((80040, 80050, 80005, 80030))
    with DataCache(cache) as store:
        for d in days:
            bars = []
            rth = 0
            for bar in session_bars(d, base=80000):
                ny = ny_datetime(bar.open_ns)
                if d == WED and time(9, 30) <= ny.time() < time(16, 0) and rth < len(script):
                    o, h, low, c = script[rth]
                    rth += 1
                    bars.append(make_bar("MNQ", bar.open_ns, o, h, low, c))
                else:
                    bars.append(make_bar("MNQ", bar.open_ns, 80000, 80000, 80000, 80000))
            store.bars.write_session("MNQ", 60, d, bars, contract="MNQZ6", source="projectx")
    cfg = tmp_path / "runner.yaml"
    spec = example_spec()
    cfg.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "data_plane": {"instruments": ["MNQ"], "skylit": {"enabled": False}},
                "plugins": [
                    {
                        "id": "rules",
                        "module": "fse.live.plugins.rules:RulesPlugin",
                        "enabled": True,
                        "config": spec.model_dump(mode="json"),
                        "fills": {
                            "trade_through_ticks": 0,
                            "slippage_ticks": 0,
                            "stop_fill": "at_stop",
                            "commission_rt": {"MNQ": "0.00"},
                        },
                        "risk": {"max_contracts": 8, "max_open_trades": 4},
                        "hold_through_break": False,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--plugin",
        "rules",
        "--first",
        str(WED),
        "--last",
        str(WED),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--recordings-root",
        str(tmp_path / "none"),
        "--history",
        "2",
        "--lag-s",
        "2",
    )
    assert code == 0, text
    trades = json.loads((out / "trades.json").read_text(encoding="utf-8"))
    assert trades, text
    outcomes = out / "run" / "plugins" / "rules" / "outcomes.jsonl"
    # The spike is the cross. A later touch of the limit can fill; the file exists
    # once a position finishes. The run itself is the contract.
    assert (out / "summary.json").is_file()
    if outcomes.is_file():
        assert outcomes.read_text(encoding="utf-8").strip()


def _trend_closes() -> list[int]:
    """Rise, break, rally back through the averages, then pull back."""
    px = 80_000
    out: list[int] = []
    for step, count in ((2, 30), (-80, 8), (90, 12), (-15, 8), (40, 6)):
        for _ in range(count):
            px += step
            out.append(px)
    return out


def _trading_days(start: date, n: int) -> list[date]:
    out: list[date] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def test_sixty_minute_pullback_trades_a_synthetic_trend(tmp_path: Path) -> None:
    """The example spec on 60m bars fills at least one long on a built MNQ path.

    A 1m host replays the whole Globex day (``hours="eth"``). The book drops
    minutes outside 09:30-16:00 before it resamples, so this cache stores only
    the regular session. The cross is one completed hour; the pullback stays
    armed and the limit fills when that hour already traded to the 10 EMA.
    """
    doc = json.loads(json.dumps(example_spec().model_dump(mode="json")))
    doc["timeframe"] = {"bar": "60m"}
    spec = StrategySpec.model_validate(doc)
    days = _trading_days(date(2026, 9, 8), 6)
    path = _trend_closes()
    scripted: dict[tuple[date, int], tuple[int, int]] = {}
    prev = 80_000
    index = 0
    for day in days:
        for hour in (10, 11, 12, 13, 14):
            if index >= len(path):
                break
            scripted[(day, hour)] = (prev, path[index])
            prev = path[index]
            index += 1
    cache = tmp_path / "cache"
    with DataCache(cache) as store:
        for day in days:
            bars = []
            opened = ny_instant(day, time(9, 30))
            closed = ny_instant(day, time(16, 0))
            while opened < closed:
                ny = ny_datetime(opened)
                slot = scripted.get((day, ny.hour))
                if slot is None:
                    bars.append(make_bar("MNQ", opened, 80_000, 80_020, 79_980, 80_000))
                else:
                    open_t, close_t = slot
                    frac = ny.minute / 59
                    price = round(open_t + (close_t - open_t) * frac)
                    low = min(open_t, close_t, price) - 40
                    high = max(open_t, close_t, price) + 20
                    bars.append(make_bar("MNQ", opened, price, high, low, price))
                opened += NS_PER_MINUTE
            store.bars.write_session("MNQ", 60, day, bars, contract="MNQZ6", source="projectx")
    cfg = tmp_path / "runner.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "data_plane": {"instruments": ["MNQ"], "skylit": {"enabled": False}},
                "plugins": [
                    {
                        "id": "rules",
                        "module": "fse.live.plugins.rules:RulesPlugin",
                        "enabled": True,
                        "config": spec.model_dump(mode="json"),
                        "fills": {
                            "trade_through_ticks": 0,
                            "slippage_ticks": 0,
                            "stop_fill": "at_stop",
                            "commission_rt": {"MNQ": "0.00"},
                        },
                        "risk": {"max_contracts": 8, "max_open_trades": 4},
                        "hold_through_break": False,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--plugin",
        "rules",
        "--first",
        str(days[0]),
        "--last",
        str(days[-1]),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--recordings-root",
        str(tmp_path / "none"),
        "--history",
        "0",
        "--lag-s",
        "2",
    )
    assert code == 0, text
    trades = json.loads((out / "trades.json").read_text(encoding="utf-8"))
    assert trades, text
    assert all(trade["setup_key"]["instrument"] == "MNQ" for trade in trades)
    assert all(trade["setup_key"]["direction"] == "long" for trade in trades)
