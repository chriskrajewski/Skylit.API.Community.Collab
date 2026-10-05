# Skylit Strategy Journal: MNQ gamma-node strategy (v3 + research)

*Last updated: 2026-10-03 · Backtest window: 2026-07-13 → 2026-09-30 · Script: `skylit_v3_backtest.py`*

---

## 1. TL;DR

- **Entry B (sweep + 3-min reclaim) is the edge. Entry A (limit at the node) is not.**
- Letting B run on taps that A rejects (**independent B**) tripled the sample: **92 trades, 47% win, +1.34R avg, +$10,949** (no limits) / **+$8,265** with 3 trades/day and −$250 daily stop, max drawdown **$471**.
- B held up out of sample: **Jul–Aug +1.60R/trade, Sep +0.83R/trade**.
- **Why A fails:** in 209 of A's 378 stops, price **reached the target after stopping us out**. The read was right; A just gets swept before the move. B is the same read, but it waits for that sweep.
- **Four map-reading rules** improved B in both periods (section 8). Together: **53 trades, 68% win, +2.7R avg, max DD $267**. They need confirmation on fresh data (section 10).

---

## 2. Data ingested

| Item | Detail |
|---|---|
| Source | Skylit API `GET /v1/historical/range` (gamma metric, 1-second frames) |
| Symbols | QQQ, NDXP (NDX 0DTE), SPY, SPXW: 0DTE expirations only, 40 strikes max |
| Per frame | timestamp, spot price, per-strike gamma values (strike axis can change during the day) |
| Window | 2026-07-13 → 2026-09-30, 09:30–16:00 ET |
| Days | **57 trading days** (Sep 7 Labor Day, no data) |
| Calls | 26 per day (15-min blocks) → **1,482 cached responses, 628 MB** in `skylit_cache/` |
| Completeness | all 57 days have all 26 blocks; ~899 frames per symbol per block |
| Cost | 25 credits per call = 650/day. **37,050 credits used in total, 67,940 left.** Re-running this window is free (cache). |
| Account check | `GET /v1/account` (free): 104,990 credits at start |

How the script reads it: QQQ 1-second spot = price path (converted to NQ points with the NDX/QQQ ratio). Each minute takes the first board per symbol. Setups are formed from the **previous** minute's board, so there's no look-ahead.

---

## 3. Strategy rules

### 3.1 v3 base rules (unchanged, "Strategy v3 parameters")

| Parameter | Value | Meaning |
|---|---|---|
| `STRONG` | 0.50 | a node must be ≥50% of its symbol's king to be traded / used as target |
| `SIG` | 0.10 | ≥10% of king counts as a node for the "one node beyond" stop |
| `STOP_PAD_NQ` | 1.0 | stop = next node beyond + 1 NQ pt |
| `TARGET_FRONT_NQ` | 1.0 | exit 1 NQ pt before the target node |
| `MAX_STOP_NQ` | 50 | wider stop = skip |
| `MIN_RR` | 3.0 | minimum reward:risk |
| `RISK_USD` | 100 | risk per trade; contracts = floor(100 / (stop pts × $2)) |
| `TICK_NQ` | 0.25 | slippage per side |
| `MATCH_NQ` | 6.0 | QQQ and NDX levels within 6 pts = source "both" |
| `MES_TOL` | 0.10% | SPY within 0.10% of its own floor/ceiling = MES confluence |
| `REARM_R` | 0.5 | price must move 0.5R away before the next tap counts |
| `RECLAIM_WINDOW_MIN` | 15 | B: max minutes from tap to the reclaim close |
| `EOD` | 15:55 | forced exit |
| `DAILY_MAX_TRADES` / `DAILY_STOP_USD` | 3 / −$250 | account rules (Builder sim only) |

**Setups:** per symbol (QQQ, NDXP), the floor (strongest node below spot) is a long, the ceiling (strongest above) is a short, and the king is long or short depending on its side. QQQ and NDX levels that match are merged (source "both").
**Skip filters (A):** no target, 3rd tap, stop too wide, R:R under 3, velocity.
**Entry A:** limit at the node, one A position at a time.
**Entry B:** after the tap, price sweeps past the node and a 3-min candle closes back on the right side within 15 min. Entry at that close; stop beyond the sweep + 1 pt.

### 3.2 Rules added during this research (all toggleable)

| Parameter | Current | What it does | Result |
|---|---|---|---|
| `TIGHT_STOP`, `TIGHT_MIN_PCT`, `TIGHT_STOP_NQ` | True, 0.80, 5.0 | If a setup would be skipped but the node is ≥80% of king and has confluence, retry with the stop 5 pts beyond the entry node | Hurts A (−0.34R), helps B (+1.74R) |
| `VELOCITY_MODE` | "both" | velocity filter blocks A+B / only A / off | No effect: fired 3 times in 57 days |
| `ENTRY_CUTOFF` | 15:55 | no new entries at or after 15:55 | Removed 10 A trades, all losers (+$351) |
| `ENTRY_A` | **False** | **Entry A dropped (2026-10-03).** A's filters still decide which B logic a tap uses, but no A trades are taken | B results unchanged (B never depended on A fills) |
| `B_INDEPENDENT` | True | B is also checked on taps A rejected, with its own stop (beyond the sweep +1, at least 5 pts from the node) and its own target from the B entry | **29 → 92 B trades, +$3,256 → +$10,949** |

---

## 4. Run log

| # | Run | Config | Credits | Output |
|---|---|---|---|---|
| 1 | 2026-09-22 | plain v3 | 650 | (overwritten) |
| 2 | 2026-09-22 | + tight stop | 0 (cache) | (overwritten) |
| 3 | Jul 13 – Sep 30 | + tight stop | 36,400 | `backtest_v3.xlsx`, `backtest_v3_tight_vel_on.xlsx` |
| 4–6 | same | velocity both / A only / off | 0 | `backtest_v3_vel_both/_Aonly/_off.xlsx` |
| 7 | same | + 15:55 cutoff | 0 | `backtest_v3_cutoff.xlsx` |
| 8 | same | + cutoff + independent B + context columns | 0 | **`backtest_v3_final.xlsx`** |
| 9 | same | run #8 with entry A off (B only) | 0 | `backtest_v3_B_only.xlsx` |

---

## 5. Results (final run #8)

| | Entry A | Entry B (all) | B from A setups | B independent |
|---|---|---|---|---|
| Trades | 438 | **92** | 29 | 63 |
| Win rate | 13% | **47%** | 45% | 48% |
| Avg R | −0.24 | **+1.34** | +1.25 | +1.39 |
| Profit factor | 0.73 | **3.49** | 3.24 | — |
| Net, no limits | −$9,789 | **+$10,949** | | |
| Net, 3/day & −$250 | −$3,104 | **+$8,265** | | |
| Max DD (limits) | $5,396 ❌ | **$471** ✅ | | |
| Worst point from start (no limits) | −$11,212 | **−$485** | | |

**Train vs test**

| | Jul–Aug | Sep |
|---|---|---|
| A | 310 trades, 11%, −0.32R | 128 trades, 18%, −0.04R |
| B | 61 trades, 46%, **+1.60R** | 31 trades, 48%, **+0.83R** |

**Where the independent B trades came from** (taps A rejected)

| A rejected it for | B trades | Win | Avg R |
|---|---|---|---|
| 3rd tap | 43 | 37% | +1.09 |
| R:R under 3 | 12 | 67% | +1.88 |
| stop too wide | 8 | 75% | +2.24 |

A's filters were throwing away good B setups. A wide structural stop or a low R:R from the node often turns into a good trade once you enter after the sweep with a stop beyond it.

---

## 6. Skipped taps (A filters)

| Reason | Count |
|---|---|
| 3rd tap | 947 |
| R:R under 3 | 695 |
| no target | 214 |
| stop too wide | 123 |
| after cutoff | 10 |
| velocity | 3 |

A "skip" is every tap of every candidate node (floor, ceiling and king, on both QQQ and NDX), which is why the counts are high.

---

## 7. Why trades fail

Each stopped trade was classified using its 1-second path after entry.

| Failure type | How it's detected | A (378 stops) | B (49 stops) |
|---|---|---|---|
| **Right read, stop too tight** | price hit the target later that day after stopping us out | **209 (55%)** | 7 |
| **Worked, then reversed** | reached +1R or more before the stop | 58 | **24** (median best +1.7R) |
| **Instant fail** | stopped in <60 s, node blown through | 78 | 0 |
| **Node failed (slow)** | never reached +1R, slow drift to the stop | 33 | 18 |

**What this means**
- **A's main problem is timing, not the read.** The node holds *after* a sweep, and A's stop sits inside the sweep zone. B waits for the sweep, so it almost never has this problem (7 cases).
- **A's 78 instant fails** are nodes that broke on contact. That's the "fast approach" case in section 8.
- **B's main leak is giving back winners** (24 trades reached a median +1.7R, then hit the stop). Moving the stop to breakeven made B worse (section 9). The next thing to test is a partial exit at +2R.

---

## 8. Reading the maps: what mattered

Each trade was tagged with what the map and tape showed **at the tap** (no look-ahead). A condition is only trusted if it pointed the **same way in Jul–Aug and in Sep**.

### 8.1 Consistent in both periods (usable)

| Condition at the tap | Effect on B | Jul–Aug | Sep |
|---|---|---|---|
| **King is behind the trade** (trading away from the magnet) | bad | 0/3 won | 0/2 won |
| **Fast approach** (>40 NQ pts toward the node in the last 5 min) | bad | 0/2 won | 1/4 won |
| **QQQ net gamma positive** | good | +1.89R (55) vs −1.01R neg (6) | +1.60R (15) vs +0.12R neg (16) |
| **2nd tap of the node** | best tap | +2.97R, 67% (9) | +2.28R, 71% (7) |
| **SPY/SPX also pinned at their own level** (MES confluence) | *worse* (surprising) | +1.07R vs +2.15R | +0.20R vs +1.97R |

For A, only these held in both periods: fast approach (−0.52R / −0.54R), floor/ceiling **rolling against** the trade (−0.31R / −0.87R), and the **13:30–15:30 window** (−0.47R / −0.66R).

### 8.2 Flipped between periods (do NOT use as rules)

- **Trend day** (>150 NQ pts from the open): B +2.16R in Jul–Aug, then −0.59R in Sep.
- **Node fading vs growing**, **long vs short**, **yellow vs purple**: the direction changed or the sample was too small.

### 8.3 Over-tapped nodes = one chop day

All 9 B trades on taps ≥13 lost, but **8 of the 9 were on 2026-08-17**, the same nodes failing again and again. So this is a "the node is dead today" pattern, not a magic tap number.

---

## 9. Trade management tests (re-simulated on the real 1-second path)

| Management | A Jul–Aug | A Sep | B Jul–Aug | B Sep |
|---|---|---|---|---|
| **As tested** | −0.32R | −0.04R | **+1.60R** | **+0.83R** |
| Breakeven at +1R | −0.28R | +0.07R | +1.12R | +0.70R |
| Breakeven at +2R | −0.34R | −0.01R | +1.30R | +0.80R |
| Stop 1.5× wider | −0.16R | −0.05R | +1.18R | +0.39R |
| Stop 2× wider | −0.16R | +0.05R | +0.87R | +0.37R |
| 2× wider + BE at +1.5R | −0.15R | +0.14R | +0.81R | +0.44R |

(R is measured on each version's own risk; contracts shrink with wider stops, so dollar risk stays about $100.)

- **B: keep it as is.** The stop beyond the sweep is already right, and every change made it worse.
- **A: a wider stop roughly halves the loss but is still negative in Jul–Aug.** A doesn't have an edge on its own.

---

## 10. Proposed dynamic playbook (read the map, then pick the trade)

These rules came from the data above. Tested together on B:

| Rule set (B) | Trades Jul–Aug | Win / avg R | Trades Sep | Win / avg R | Net (limits) | Max DD | Worst point |
|---|---|---|---|---|---|---|---|
| B as tested | 61 | 46% / +1.60 | 31 | 48% / +0.83 | +$8,265 | $471 | −$485 |
| + R1 king not behind | 58 | 48% / +1.74 | 29 | 52% / +0.96 | +$8,507 | $471 | −$391 |
| + R2 no fast approach | 59 | 47% / +1.69 | 27 | 52% / +1.04 | +$8,512 | $440 | −$388 |
| + R3 node not dead | 52 | 54% / +2.06 | 31 | 48% / +0.83 | +$8,326 | $471 | −$485 |
| + R4 QQQ gamma positive | 55 | 51% / +1.89 | 15 | 60% / +1.60 | +$9,474 | $361 | −$405 |
| **+ R1+R2+R3** | 47 | 60% / +2.38 | 25 | 56% / +1.21 | +$8,815 | $364 | −$294 |
| **+ R1+R2+R3+R4** | 41 | **68% / +2.88** | 12 | **67% / +2.11** | **+$9,874** | **$267** | **−$215** |

### The situational checklist

**Before the open: read the day**
1. **QQQ net gamma positive?** Then nodes tend to hold and B gets full size. If negative, B still worked slightly (+0.12R in Sep), so use **half size**. No A trades.
2. **Where's the king?** Only trade nodes where the king is the node itself or sits **in the profit direction** (it pulls price toward the target). **King behind you = skip (R1).**

**As price comes to the node: read the approach**

3. **Slow or normal approach** (≤40 NQ pts in 5 min): valid. Wait for the sweep and the 3-min reclaim (entry B).
4. **Fast approach** (>40 NQ pts in 5 min): **stand aside (R2).** The first hit tends to blow through, which is where A's 78 instant fails came from. **The 2nd tap is the best B trade in both periods**, so let the first one go and wait for the retest.

**During the day: adapt**

5. **A node that failed twice today is dead (R3).** Stop trading it until the map moves it (Aug 17 lost 8 times on the same nodes).
6. **SPY/SPX pinned at their own level too:** the data says *less* follow-through (the whole market is pinned, so price struggles to reach the target). **Half size** until confirmed. This is the opposite of the usual "confluence = better" idea; check it against your own reading.
7. **Don't use A as a standalone entry.** If you want an A, it's only a starter position in a slow approach, outside 13:30–15:30, with the floor/ceiling not rolling against you. The main position comes with the B reclaim.

### ⚠️ How much to trust this
- **September wasn't fully blind.** I chose rules that pointed the same way in both periods, so September helped pick them. A truly blind test needs **new data**.
- **Small samples:** R1/R2 are based on a handful of trades, and the full R1–R4 set has only 12 September trades.
- **Concentration:** Aug 11 alone was +$1,640 in the earlier B set.
- B has no one-at-a-time limit, so several B positions can be open together. The 3/day cap is applied in the Builder sim.

---

## 11. Next steps

1. **Blind validation on fresh data:** run June 2026 (~21 days ≈ **13,650 credits**, needs approval) and apply R1–R4 unchanged.
2. **Code R1–R4 into the script** as toggles (`B_KING_NOT_BEHIND`, `MAX_APPROACH_NQ`, dead-node after 2 losses, gamma-based sizing).
3. **Test a partial exit at +2R for B** (24 trades gave back a median +1.7R).
4. ~~Test dropping A~~ **Done: A dropped (`ENTRY_A = False`).** It could come back later only as a starter under the item 7 conditions.

---

## 12. Files

All in `C:\Users\rakedFPS\Documents\Skylit\`.

| File | Contents |
|---|---|
| `skylit_v3_backtest.py` | backtester (v3 parameters + toggles in section 3.2) |
| `Skylit_Key.env` | API key (loaded via python-dotenv; never printed) |
| `skylit_cache/` | 1,482 raw API responses; delete = pay again |
| **`backtest_v3_final.xlsx`** | final run: Trades A/B with all context and failure columns, Skipped, Summary, Builder sim (with and without limits) |
| `backtest_v3_cutoff.xlsx` | cutoff only (B not independent) |
| `backtest_v3_vel_*.xlsx` | velocity-filter versions |
| `backtest_v3.xlsx`, `backtest_v3_tight_vel_on.xlsx` | first full-range run |

**Columns added for research** (in Trades A/B): `node_trend`, `node_chg_15m`, `king_side`, `king_dist_nq`, `day_trend`, `day_move_nq`, `approach_5m_nq`, `ndx_regime`, `stop_type`, `b_origin`, `entry_time`, `sweep_nq`, `entry_px`, `stop_px`, `target_px`, `mfe_R`, `mae_R`, `secs_in_trade`, `target_hit_after_stop`.

---

## 13. Glossary

- **King:** the strike with the largest absolute gamma for a symbol.
- **Floor / ceiling:** the strongest node below / above spot.
- **Yellow / purple:** positive / negative gamma at the node.
- **Tap:** price touches the node coming from the right side (it has to move 0.5R away before the next tap counts).
- **Sweep:** price trades through the node after the tap.
- **Reclaim:** a 3-min candle closes back on the right side of the node.
- **R:** one unit of risk (about $100). +3R is about +$300.
- **Source both:** QQQ and NDX levels within 6 NQ pts of each other.
- **MES confluence:** SPY or SPXW is at its own floor/ceiling.
- **Trinity:** SPY and SPXW both at their own floor/ceiling.
- **Regime:** sign of the total QQQ gamma (positive = nodes tend to hold).
- **Rolling:** how the floor/ceiling moved over the last 30 min (with / against the trade).
- **MFE / MAE:** best / worst move during the trade, in R.
