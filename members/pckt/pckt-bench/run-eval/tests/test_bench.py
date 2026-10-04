import math, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import options_model as om
from bench_common import Bars, session, window
import score as sc

D = "2026-07-07"; O, C = session(D)


def flat_bars(price=100.0, vix=16.0, drift=0.0):
    rows = lambda f: [[t, f(t), f(t) + 0.05, f(t) - 0.05, f(t)] for t in range(O - 3600, C + 60, 60)]
    return Bars({"SPY": rows(lambda t: price + drift * (t - O) / 60), "QQQ": rows(lambda t: price), "SPX": rows(lambda t: price * 10), "VIX": rows(lambda t: vix)})


def sig(direction="long", start_off=45 * 60, dur=30 * 60):
    s = O + start_off
    return {"id": "x", "date": D, "direction": direction, "start": s, "end": s + dur, "legs": {"SPY": {"t0": s, "p0": 100.0, "t1": s + dur, "p1": 101.0}}}


class T(unittest.TestCase):
    def test_bs_put_call_parity(self):
        for spot, k, secs, iv in [(100, 100, 3600, .2), (745, 750, 20000, .17), (100, 95, 600, .3)]:
            c, p = om.bs(spot, k, secs, iv, True)[0], om.bs(spot, k, secs, iv, False)[0]
            self.assertAlmostEqual(c - p, spot - k, places=6)

    def test_bs_known_value(self):      # ATM, r=0: price = S * (2N(s/2)-1) with s = iv*sqrt(T)
        s = 0.2 * math.sqrt(1 / 365); self.assertAlmostEqual(om.bs(100, 100, 86400, .2, True)[0], 100 * (2 * om._n(s / 2) - 1), places=8)

    def test_window_clips_to_session(self):
        w = window(sig(start_off=5 * 60, dur=10 * 60)); self.assertEqual(w["open_t"], O)           # start-20 would be pre-open
        w = window(sig(start_off=380 * 60, dur=9 * 60)); self.assertEqual(w["force_t"], C)         # end+15 clipped at 16:00

    def _run(self, trades, s=None, bars=None, mode="equity", **kw):
        s = s or sig(); w = window(s, 20, 15); ok, rej = sc.structural({"trades": trades}, s, w)
        p = {"cap": 500.0, "max_exposure": 1500.0, "cap_equity": 10000.0, "max_exposure_equity": 30000.0, "vix_daily": False, "slip": 0.01, "k": 1.0, "delta": .3, "spread_floor": .03, "spread_pct": .03, "commission": 0.0}; p.update(kw)
        return sc.simulate(ok, D, bars or flat_bars(), mode, p), rej

    def test_forced_close_and_window_rules(self):
        s = sig(); w = window(s, 20, 15)
        (fills, _), rej = self._run([{"symbol": "SPY", "side": "long", "open_time": s["start"], "close_time": None},
                                     {"symbol": "SPY", "side": "long", "open_time": s["end"] + 120, "close_time": None},          # after end: rejected
                                     {"symbol": "SPX", "side": "long", "open_time": s["start"], "close_time": None}], s)          # not tradable
        self.assertEqual(len(fills), 1); self.assertTrue(fills[0]["forced"]); self.assertEqual(fills[0]["close_t"], w["force_t"])
        self.assertEqual(rej["outside_window"], 1); self.assertEqual(rej["bad_symbol_or_side"], 1)

    def test_direction_pnl_equity(self):
        s = sig(); b = flat_bars(drift=0.1)                                        # price rises 0.1/min
        up = self._run([{"symbol": "SPY", "side": "long", "open_time": s["start"], "close_time": s["start"] + 600}], s, b)[0][0][0]["pnl"]
        dn = self._run([{"symbol": "SPY", "side": "short", "open_time": s["start"], "close_time": s["start"] + 600}], s, b)[0][0][0]["pnl"]
        self.assertGreater(up, 0); self.assertLess(dn, 0); self.assertAlmostEqual(up, -dn, delta=6.0)

    def test_options_cap_and_affordability(self):
        s = sig(); (fills, rej), _ = self._run([{"symbol": "SPY", "side": "long", "open_time": s["start"], "close_time": s["start"] + 600}], s, mode="options")
        self.assertEqual(len(fills), 1); self.assertLessEqual(fills[0]["cost"], 500.0); self.assertGreaterEqual(fills[0]["qty"], 1)
        (fills, rej), _ = self._run([{"symbol": "SPY", "side": "long", "open_time": s["start"], "close_time": s["start"] + 600}], s, bars=flat_bars(price=20000.0), mode="options")
        self.assertEqual(len(fills), 0); self.assertEqual(rej["unaffordable"], 1)

    def test_exposure_cap(self):
        s = sig(); t = [{"symbol": "SPY", "side": "long", "open_time": s["start"] + 60 * i, "close_time": None} for i in range(5)]
        (fills, rej), _ = self._run(t, s); self.assertEqual(len(fills), 3); self.assertEqual(rej["exposure_cap"], 2)       # 3 x $500 = $1,500 cap

    def test_empty_log_and_recall(self):
        s = sig(); doc = {"slice": "t", "window": {"lead_min": 20, "close_after_min": 15}, "signals": [s]}
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"bars_{D}.json").write_text(json.dumps({"date": D, "bars": flat_bars().b}))
            r = sc.score(doc, {}, d); self.assertEqual(r["behaviour"]["recall"], 0.0); self.assertEqual(r["modes"]["options"]["trades"], 0)
            r = sc.score(doc, {"x": {"trades": [{"symbol": "SPY", "side": "short", "open_time": s["start"], "close_time": None}]}}, d)
            self.assertEqual(r["behaviour"]["recall"], 0.0); self.assertEqual(r["behaviour"]["bias"]["wrong_way_trades"], 1)       # wrong direction is not a capture

    def test_refuses_options_without_vix(self):
        import tempfile, json
        b = flat_bars().b; b.pop("VIX")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"bars_{D}.json").write_text(json.dumps({"date": D, "bars": b}))
            with self.assertRaises(SystemExit): sc.score({"slice": "t", "window": {"lead_min": 20, "close_after_min": 15}, "signals": [sig()]}, {}, d)


if __name__ == "__main__": unittest.main()
