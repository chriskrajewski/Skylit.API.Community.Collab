# SirFartalot Backtest Study

*Every study the SirFartalot desk has run on dealer-positioning (GEX / VEX) trading logic, July → October 2026: what was tested, how often it won, and why it worked or didn't.*

*Compiled 2026-10-04. This document shares **logic and results only**. It does not describe how the data was collected, where it came from, or how it is stored. Data is described generically: "dealer-positioning snapshots" means one dated map of per-strike gamma/vanna exposure across many tickers (a "board"), and "price bars" means ordinary daily, minute or 5-minute OHLC bars.*

---

## How to read this

**Win rate.** Each study reports the hit rate that best fits the question it asked, and names the measure next to the number:

| term | meaning |
|---|---|
| **TP1 hit** | the trade filled at the entry, and price then reached the first target before the stop |
| **Direction right** | the predicted side (long/short) matched where price closed N sessions later |
| **Board coin** | the honest baseline for direction: always back the side the *majority* of names moved that day. In a trending tape this scores 60–70%, not 50%. A direction rule has to beat this baseline, not 50%. |
| **Touch / reach** | price traded at the level within the window |
| **Hold** | after touching the level, price turned back instead of going through it |
| **Symbols W/L** | a per-ticker sign test: how many tickers the rule beat the control on, and how many it lost on. **This is the unit that decides a verdict.** A pooled average can pass while the per-symbol count is a coin flip. |
| **$/1k** | dollars per $1,000 of stock per filled play |
| **R** | profit or loss measured in units of the planned risk |
| **Walker** | the grader that replays a plan against the bars. A daily walker is optimistic. A minute walker is honest. |

**Verdicts.** ✅ WORKED · 🟡 PARTIAL · ❌ FAILED · ⚪ NULL (no effect beyond the control) · 📊 DESCRIPTIVE (measured a fact; not a trading rule) · ⏳ OPEN

**House rules every study ran under**
- Pre-register the hypothesis and the pass bar before looking at the holdout data.
- Compare against a **control at the same distance from price**. Without one, "the level works" is usually just "the level was close".
- Count the **symbol** as the unit, not the trade.
- Correct for multiple testing (Benjamini–Hochberg) when many ideas are tried.
- A study's verdict never changes the trading engine automatically. At most, a winner ships as a display-only label.

---

## The five laws the studies converged on

1. **Dealer positioning does not tell you direction.** This was tested at every scale and in every form we could think of: more than 17,000 hypotheses, 20-agent and 80-agent swarms, machine-learning ceilings, and blind agents that never saw our rules. Nothing beat the board coin out of sample.
2. **Touch is proximity.** Almost every "this level attracts price" claim turned out to mean "this level was near". Only a few node attributes add touch odds beyond distance: growth, being the King, the front expiry, and wear. **None of them makes a level *hold* better.**
3. **The tail law.** The top 10% of winning trades carry roughly 40–45% of all gross winning dollars. Trimming, down-sizing or vetoing any subset cuts winners along with losers. A veto is the #1 winner-killer.
4. **Fill precision is the edge.** A perfect fill earned +69R in one test. With 0.25% slippage it was −14R. Use a limit order at the level and never chase.
5. **Volatility is forecastable; direction is not.** A stock's own implied volatility ranks tomorrow's range about twice as well as the VIX. Range, reach odds and size can be measured. The side cannot.

---

## Scoreboard

| # | Study | Win rate (what it measures) | Verdict |
|---|---|---|---|
| **A** | **Direction** | | |
| A1 | Fresh-eyes direction search (8 families) | direction right 48.0% pooled holdout; best 53.4% vs null 50.1%; a price-only control scored 55.2% | ⚪ |
| A2 | Path: range, skew, first touch | first touch on the predicted side 55.9% vs mirror side 58.2%; skew right 46.7% vs 49.1% base | 🟡 range only |
| A3 | "Direction-80" parameter library | best 58.6% vs coin 56.2%; 0 of 960 candidates beat the coin by +5pp | ⚪ |
| A4 | Intraday reader retrain | direction 49–53% vs coin 58–64% | ⚪ |
| A5 | Blind agent swarm | 10 of 19 agents positive, 0 of 19 significant per symbol | ⚪ |
| A6 | Monkey lens vs main engine | direction 49.85% vs engine 51.16% vs coin 55.64% | ❌ |
| A7 | Monkey multi-agent direction search | every formulation 7–13pp *below* coin | ⚪ |
| A8 | Monkey × lens combinations | 5–8pp below coin | ⚪ |
| A9 | Lens unification / cross-expiry lens | beat engine on 43 of 93 symbols | ⚪ |
| A10 | 80-agent lotto contest | option doubled 25.2% (median agent) vs 24.7% coin-flip benchmark | ⚪ |
| A11 | AI read of historical boards | regime label right 60–65% vs 79% for "always range" | 📊 |
| A12 | Sirfart 2.0 fresh engine | next-day direction ≈49.7% (0 of 21 models pass) | ❌ direction · ✅ vol range |
| A13 | Structural read (no search radius) | direction 46.1% vs engine 53.8%; TP1 41.1% vs 50.0% | ❌ |
| A14 | Vanna as side / approach direction | TP1 51.3% (vanna agrees) vs 47.8% (fights), but money inverted | ⚪ |
| A15 | Per-family constants + vote tally | tally direction 47.5% vs coin 63.9% | ⚪ |
| A16 | Index-tilt override | tilt's own short calls right 25% (1 of 4) | ⚪ |
| A17 | Regime router (trend-follow in −γ) | direction 42% vs fade 49% vs always-long 54% | ❌ |
| A18 | Tilt-aware sizing | tilt called the right side 6 of 11 weeks (55%) | ❌ |
| A19 | Index-gamma sleeve tilt | right sleeve 18 of 23 weeks (78%) | 🟡 forward test owed |
| A20 | Vanna drift / stair-step | direction 49.6% vs ~50% always-long | ⚪ |
| A21 | Projection as direction | 47.2% vs coin 62.8% (+5 sessions) | ❌ |
| A22 | Projection variants (5 reads) | 52.3% vs coin 64.2% | ⚪ |
| A23 | Projection parameter sweep | projection beat "no move" on 40.9% of symbols | ⚪ |
| A24 | Near-expiry floor vs ceiling | direction 50.6% | ❌ |
| A25 | Far-dated (LEAPS) book direction | lean right 49% vs flipped 50% | ❌ |
| A26 | −γ King at spot ("ghost" rows) | +6.6pp up-lean at +1 day, gone by +2 days | ⚪ / ⏳ |
| **B** | **Levels & nodes** | | |
| B1 | Node size as % of the King | push through 29.6% at a 33–50% node vs 30.4% elsewhere | ⚪ / ❌ forward |
| B2 | Floor/ceiling detection retrain | floor reached 32.3% vs random strike 22.1%; held 28.8% vs 33.9% | ⚪ (distance) |
| B3 | Overnight level on a dealer wall | touched 47% vs lone level 62% | ⚪ (negative) |
| B4 | "Room to the next level" | reach odds = random-walk value; random levels beat real ones | ❌ |
| B5 | Node growth → next-session touch | King touched 47.7% grown vs 34.6% flat | 🟡 touch only |
| B6 | Node-to-node route mapping | route lift +0.009 to +0.016 vs +0.03 bar | 🟡 attributes only |
| B7 | Growth as a ranking slot | best form 29W / 19L symbols (p 0.19) | ⚪ |
| B8 | How to measure node growth | daily ±10% rule is a 49–50% coin | 📊 |
| B9 | Growth · wear · tenor lenses | grown wall touched 9.4% vs shrunk 2.9% | 🟡 |
| B10 | Sweep into a +γ floor | held 60.0% vs −γ pocket 59.9% vs empty 62.2% | ⚪ |
| B11 | Near-expiry King as magnet | toward King 52.5% vs summed King 53.3% | ❌ |
| B12 | Outside educator's doctrine (16 claims) | "rugpull" shapes right 53/45/55% vs coin 64–65% | 🟡 2 of 16 |
| B13 | Stops at worn strikes | stopped 42% vs 40% at scale | ⚪ |
| B14 | Expiry-day inflation + weekly wall tag | grown wall touched 57.5% vs decayed 53.9% (distance-matched) | ⚪ |
| B15 | External newsletter's level taxonomy | TP1 at the King 35.0% vs other entries 43.3% | ⚪ |
| B16 | Futures levels kill-check | walls touched 25.6% vs 16.3% raw; −2.4pp distance-matched | ⏳ |
| B17 | Raw-ladder level constructors | current wall held 40.0% vs random 36.7%; King 29.6% | ⚪ |
| B18 | Tomorrow's expiry in the 0DTE map | held 60.0% vs 37.5% (tiny n) | 🟡 |
| B19 | Wider 0DTE map (2 expiries) | held 53% / 50% / 52% | ⚪ |
| B20 | Persistence · tenor · dark-pool prints | held 45–48% in every bucket | ⚪ |
| B21 | Per-expiry grid read vs summed book | held 46% vs 42%, but plays did worse | ⚪ |
| B22 | Gross vs signed-net walls | TP1 51% vs 46% | ⚪ |
| B23 | Calendar expiry windows | TP1 59% vs 46%, but less money | ⚪ |
| B24 | Summing conventions | King agreement net = gross 80% | 📊 |
| B25 | Rebuilding exposure from the chain | textbook sign right 14 of 26 strikes | 📊 |
| B26 | Independent open-source GEX engine | near-spot King sign disagreed 100% of the day | 📊 |
| B27 | Rescan when price touches the entry | growing King reached 64% vs shrinking 56% (p 0.18) | ⚪ edge · 📊 display |
| B28 | Intraday book freshness | afternoon turns caught 18% (midday map) vs 11% (morning map) | ✅ suggestive |
| **C** | **Geometry, stops, tenor** | | |
| C1 | Entry/ladder radius sweep (90 cells) | TP1 51–52% in every cell vs 52% shipped | ⚪ |
| C2 | Earlier radius sweep | n/a | ⚪ superseded |
| C3 | 132 toggle combinations | best: 176 / 148 symbols (p 0.13) | ⚪ |
| C4 | Floor under the stop-tightness reward | 13 of 22 boards (bar 9 of 12) | ❌ |
| C5 | Drop the nearest expiry | filled 50.2% vs 55.6%; 8 of 20 boards better | ❌ |
| C6 | Tight-book selection | money-positive 53.7% (tight) vs 58.1% (wide), all trailing vol | ⚪ |
| C7 | External 25-item tune battery | thin-book TP1 51.4% vs 43.9% (money flat) | 🟡 5 hygiene items |
| C8 | Per-mode autopsy | n/a | 📊 shipped |
| C9 | LEAPS mode | TP1 57% → 52% out of sample | ❌ |
| C10 | Re-grade of 22 months of old boards | direction 56%, TP1 53% | 📊 |
| C11 | Break-hold-retest entry | 66% as a replacement entry; 45% as an add-on | ❌ |
| C12 | King-guard + farther TP1 | vetoed set 30% (3W / 7L) | ❌ / ⚪ |
| C13 | 3:1 R:R gate | 3:1 target hit 0 of 12 | ❌ |
| C14 | Four doctrine tunes (candle, range fade…) | candle rule 52.6%; range fade worse on 86 of 133 boards | ❌ |
| C15 | Breakeven trail after +1R | saved 26 losers, killed 47 winners | ❌ |
| C16 | Wait for pullback vs enter next open | pullback better 349 vs 276; missed runners hit TP1 86% | 🟡 |
| C17 | Worn entry level (tap count) | TP1 52% vs 41% → later 49–51% vs 45–50% | ❌ retired |
| C18 | External newsletter's play construction | TP1 30.2% vs its own short mirror 38.9% | ❌ |
| C19 | External newsletter week vs engine | 1 of 5 predictions hit | 📊 |
| C20 | External setup-rule replica | TP1 41.3% vs engine 52.8% | ❌ |
| **D** | **Ranking & selection** | | |
| D1 | Can display data re-order the plays? | shipped score agreed with money on 42 of 111 boards | ❌ |
| D2 | P% calibration, EV, winner/loser profile | TP1 by P% bin 31% → 76% | ✅ P% · ❌ rest |
| D3 | From-scratch ranking score | TP1 29.3% (top half) vs 10.7% (bottom) | ✅ narrow |
| D4 | "PRIME" edge-points selector | 79% in sample → 59% forward | ❌ forward |
| D5 | Forward batch 1 + 6 doctrine claims | selector 59% vs board 60% | ❌ |
| D6 | Star-checklist promoter | starred 44% vs 31%, but zero lift on winners | ❌ |
| D7 | Eight-board replay read | P% ≥ 60 → TP1 87% (90% on unseen dates) | 🟡 |
| D8 | Eight tunes tested before building | TP1 58% (R:R 0.5–1.5) vs 5% (R:R > 3) | 🟡 |
| D9 | Grade/score refit | out-of-sample ρ −0.02 | ❌ |
| D10 | Trapdoor · neutral lean · veto audit | "fights acceleration" vetoes ran +1R 47% | 🟡 |
| D11 | Data-sweep ideas 51–58 | timing label reads "late" 89% of the time | 📊 |
| D12 | Three-read agreement chip | all-agree TP1 0% (n = 3) | ⚪ |
| D13 | Long-shots strip | not graded yet | ⏳ |
| **E** | **Money & vehicles** | | |
| E1 | Endorsed plays traded as options | SAFE green 41% / 37%; LOTTO 21% / 23% | ❌ |
| E2 | Trade only P% ≥ 60 | TP1 58% vs 37%, money no better | ❌ |
| E3 | ATM SAFE + OTM LOTTO picker | SAFE 41%, LOTTO 30% on real premiums | 🟡 (superseded by E1) |
| E4 | Breakeven-past-TP1 guard | 0DTE contract win 66% (inside) vs 44% (past) | 🟡 |
| E5 | Which strike to buy (design) | n/a | 📊 |
| E6 | Late-session 0DTE cutoff | modeled option P&L −14.9 → +8.1 | 🟡 model only |
| E7 | Lotto by regime | n/a ($ by regime) | 🟡 |
| E8 | No plays on a decaying ETN | 5 of 8 stopped | ✅ small n |
| E9 | Minute walker: are same-bar targets real? | 63% real, 37% not | ❌ book money |
| E10 | Slippage cost | +69.3R at 0% → −13.8R at 0.25% slip | ✅ finding |
| E11 | Earnings-in-window veto | stopped 22% (catalyst) vs 34% (clean) | ❌ veto refuted |
| **F** | **Volatility, range & time** | | |
| F1 | Implied-vol layer × dealer book | nearer bound reached first 73.0% vs coin 61.2%; direction 49.8% vs 66.6% | 🟡 |
| F2 | Own implied vol vs VIX for range | own IV won on 137 of 198 names (69%) | 🟡 bar missed |
| F3 | Index edge sweep (exploratory) | next-day up 52–58% after weakness (2000–26 only) | 🟡 |
| F4 | Index & futures registered study | inside day breaks its high 62% | 🟡 |
| F5 | VIX pivot zones | zone touched 83.8%; reversion after R2 58% | 🟡 |
| F6 | Pivots across 56 years | 0 of 66 direction tests tradeable; vol regime 27 of 28 | 🟡 |
| F7 | "Markets move in 5-minute cycles" | turns on a 5-min close 23.6% vs null 18–22% | ⚪ |
| F8 | σ-ladder tags | next-rung continuation ≈50%; stick +8.0pp | 🟡 |
| F9 | Unbroken first-hour range | break side 46–56%; close beyond 50.6% | ⚪ |
| F10 | Prior-day σ and levels / HAR σ | prior close +3.4pp beyond model, 10 of 10 names | 🟡 |
| F11 | Implied move budget / earnings crush | implied covered 77.8% of days; over-priced earnings 79.4% | 📊 |
| F12 | Gamma centroid stretch / pin | n/a (mean R +0.142 vs +0.280) | ⚪ |
| F13 | "Premium holds on a dip" | holdout 63 / 42 symbol-months, discovery 55 / 54 | ❌ |
| **G** | **Chart levels (no dealer data)** | | |
| G1 | Reach clock for prior-day high/low | 66% vs 61% same-distance control | 📊 |
| G2 | Gap fill | fill 79–81% vs mirror level 75–82% | 📊 |
| G3 | Liquidity-hunt sweeps | reversal 41–47%; re-broken within the hour 65–78% | ⚪ |
| G4 | Eleven chart reads | worn wall +11.2pp vs non-wall +12.5pp | ❌ / 📊 |
| G5 | Classic technical levels | longs at range lows stopped 41% | 🟡 |
| **H** | **Flow & outside systems** | | |
| H1 | Flow scanner confluence ledger | option green 9% (score ≥ 3) vs 41% (score ≤ 0) | ❌ inverted |
| H2 | Whale prints | next day right 46.9–52.7% vs 52.3% base | ❌ |
| H3 | Flow at fire time (0DTE) | +0.155R with flow vs +0.122R against | ⚪ |
| H4 | Flow agreement (swing) | TP1 44.3% vs 35.7%, R inverted | ⚪ |
| H5 | Call/put premium skew | TP1 61% vs 47% (p 0.44) | ⚪ |
| H6 | Can flow side be read correctly? | reliable side on 0.5% of findings | 📊 |
| H7 | Head-to-head vs external AI analyst | TP1 41% (engine) vs 32% (analyst) | ✅ core fade |
| H8 | Seven external fixes | blocked fires were losers 37.9% vs 41.9% base | ❌ |
| H9 | External trading-agent post | claimed 92.3% on ~13 trades | 📊 |
| H10 | External futures system audit | claimed 88.9% on 9 trades | 📊 unsupportable |
| H11 | Squeeze side-engine | 19% of fillable tickets | ❌ |
| H12 | Enrichment v3 (flags, levered ETFs) | levered-ETF vote 3 of 3 wrong together | 🟡 |
| **I** | **0DTE day-trader (Jul–Aug)** | | |
| I1 | Unit-of-account audit | 41–62% win from the alert price, ≈0R | ❌ edge was accounting |
| I2 | P&L anatomy | first minute favorable 62.8% vs adverse 37.9% | 📊 |
| I3 | Ten-tune shortlist | confirmed rejection +0.136R vs failed −0.948R | 🟡 |
| I4 | Drift veto | vetoed fires were losers 44% | ❌ |
| I5 | Full-week audit (7/17) | hard stops 3W / 16L (16%) | ❌ |
| I6 | Same week on the tuned engine | TP1 18 of 38 (47%) | ✅ in sample |
| I7 | First full scan post-mortem | 32 of 39 plans R:R < 1 | 📊 |
| **J** | **2026 canon re-test (253 names, Jan–Sep 2026 + 2023–25)** | | |
| J1 | Engine side vs board coin | 48.6–50.1% vs 61–71% | ⚪ (control) |
| J2 | Grown wall touched next session | 30.3% vs 20.5%; 40 of 40 and 205 of 213 symbols | ✅ touch · ⚪ hold |
| J3 | P% ranks hit rate | AUC 0.65–0.72 | ✅ |
| J4 | Thin-book veto | endorsed beat vetoed on 19 of 25 and 107 of 173 symbols | ✅ |
| J5 | Delivery tier ("stale" plays) | TP1 fresh 42% → tier 3 71% | 🟡 hit rate only |
| J6 | Tail law | top 10% = 37–47% of gross wins | ✅ |
| J7 | The King moves with price | 70–84% of King moves | ✅ |
| J8 | Slippage costs a quarter of the edge | worse on 132 of 209 symbols | ✅ (2026) |
| J9 | King-size gate | kept beat dropped on 21 of 38 symbols | ⚪ |
| J10 | Node classes beyond distance | within ±1pp of a blank strike | ⚪ |
| J11 | Worn entry level | TP1 49–51% vs 45–50% | ❌ |
| J12 | R:R band 0.5–1.5 vs > 3 | TP1 59–68% vs 19–25% | 🟡 hit rate only |
| J13 | Stop width | stopped 48% (tightest) vs 42% (widest), R flat | 📊 |
| J14 | Implied move covers the day | 77.8% of days | 📊 |

**Tally:** about 130 tests. Roughly 14 clean ✅, 35 🟡, and the rest ❌ / ⚪ / 📊. Every ✅ is about hit rate, range, reach, fill quality or a veto. **None of them calls direction.**

---

# Part A — Direction: can dealer positioning call the side?

### A1. Fresh-eyes direction search ⚪
- **Tested:** whether any feature of the gamma/vanna ladder predicts the close 1, 5, 10 or 21 sessions ahead. Eight families were tried: ladder shape and asymmetry, King-relative scaling, cross-expiry term structure, local gamma gradient, vanna, brute-force feature search, path/magnet, and methods from the published literature. Each family got one pre-registered holdout shot.
- **Sample:** 8,050 board-tickers, 133 boards, 104 symbols, Aug 2024 → Aug 2026. At least 2,646 hypotheses were documented.
- **Win rate:** pooled holdout direction 48.0% (4,906 of 10,220). The best large-sample candidate scored 53.4% against a 50.1% no-skill null. A price-only 1-day mean-reversion control scored **55.2% and beat every book-based candidate.**
- **Result:** the mean edge was +7.0pp in discovery and −2.8pp in holdout. Five families won 72–78% of discovery boards and then only 8–31% of holdout boards. Nothing positive survived multiple-testing correction.
- **Why:** six of the eight shots were secretly the same bet, the distance from spot to the ladder's centre of mass. That distance is a lagged record of where price has already been, so it behaves as momentum and flips sign when the regime changes.

### A2. Path study: range, skew, first touch 🟡
- **Tested:** whether the book predicts how far price travels (beyond trailing volatility), which way the path is skewed, and which side's level gets touched first.
- **Sample:** about 3,400 holdout rows per arm.
- **Win rate:** first touch on the predicted side 55.9% vs 58.2% for the equidistant mirror level (inverted). Skew right 46.7% vs a 49.1% base rate.
- **Result:** **range** is real but modest. It shows a holdout partial correlation of 0.18 after controlling for volatility, the same in both halves. That clears the "notable" bar (0.10) but misses the "success" bar (0.20), and it explains less than 3% of variance.
- **Why:** how tightly exposure is packed around spot predicts how far price moves. It does not say which way. Net-gamma sign died under control.

### A3. "Direction-80" parameter library ⚪
- **Tested:** 20 independent agent families tried to build next-session direction rules that reach 80%. The bar had five clauses: 80% on holdout, +5pp over the coin, per-symbol p < .05, the same sign in both halves, and survival after Benjamini–Hochberg correction.
- **Sample:** 1,023 registered candidates on about 19,000 name-boards, plus a 2026 check set.
- **Win rate:** best holdout 58.6% vs a 56.2% coin. **0 of 960** beat the coin by 5pp. 921 of 1,023 lost to the board majority per symbol.
- **Near miss:** a first-touch rule scored 79.8%. A book-blind control at the same distance scored 63.8%, and the rule did not replicate on 2026 data.
- **Why:** 64 points of that "80%" is proximity. The rest is touch attraction. Neither of those is direction.

### A4. Intraday (0DTE) reader retrain ⚪
- **Tested:** 20 agent families retuned the intraday reader (structure, regime, plan, entry zones, taps, market-on-close) to reach 80% direction on four definitions: plan walk, next hour, close, and next session.
- **Sample:** 1,287 intraday snapshots and 6,358 end-of-day boards; about 900 parameter cells.
- **Win rate:** direction 49–53% vs coins of 58–64%. Next session: 50.9% vs 64%, losing on 250 of 328 symbols.
- **Result:** 0 cells reach 80%. Two machine-learning ceilings landed at 48–51%. The plan walk "hits" 79%, but random near-target plans hit 66%.
- **Why:** the high hit rate comes from asymmetric geometry (a target nearer than the stop), not from reading direction.

### A5. Blind agent swarm ⚪
- **Tested:** 20 agents with **none** of our rules, code or doctrine each built their own strategy from raw snapshots and bars. One shared evaluator scored every play against a mirror (opposite-side) control.
- **Sample:** 19 agents scored, 6,264 out-of-sample plays, 3,656 fills.
- **Win rate:** 10 of 19 agents positive out of sample, **0 of 19 significant per symbol.** Per-agent play win rates ranged 25–72%. In-sample and out-of-sample signs agreed for 8 of 19 agents, which is what a coin produces.
- **Why:** the agents independently re-invented the same ideas (largest-gamma strike as a magnet, near-spot net gamma, RSI/Bollinger reversion). All of them were coin flips.

### A6–A9. The "Monkey" lens family ❌ / ⚪
- **A6, Monkey vs engine:** this alternative map lens was tested for direction and for a farther-target trade. On 3,840 paired name-boards it was right **49.85%** vs the engine's 51.16% vs a 55.64% coin. Targeting the farther node helped Monkey (73 of 99 symbols), but the main engine gains the same from a farther target, and like for like it is a coin (41 of 91). ❌
- **A7, multi-agent direction search:** about 1,565 hypotheses. Every formulation sat **7–13pp below the coin**, and a random coin scored the same as the best rule. ⚪
- **A8, lens combinations:** the map was bit-identical under every lens. Side flips (8–12%) came from leftover percentage bands, not new information. All arms were 5–8pp below the coin. ⚪
- **A9, lens unification / cross-expiry lens:** this lens beat the engine on 43 of 93 symbols (p 0.53). It was another flat point on the tenor-weighting axis. ⚪
- **Why:** the map carries no side information. "It grew" mostly means "price moved away from it".

### A10. 80-agent lotto contest ⚪
- **Tested:** 80 agents, each with its own node-reading method, picked one cheap OTM option per ticker (|Δ| < 0.20, 7–14 days out). A hit meant the option doubled. Agents were compared against a book-free benchmark: a coin-flip side and the contract nearest 0.19Δ at 14 days.
- **Sample:** 158,000 lottos graded on real option prices; a tuning month and a sealed month.
- **Win rate:** doubled **25.2%** for the median agent (best 28.2%), against **24.7%** for the coin-flip benchmark and 18.0% for the cheapest lotto.
- **Result:** return per $1 was −0.445 for the benchmark and −0.406 for the best agent. 0 of 80 cleared the best-of-80 luck line, and 0 won per symbol. 15 agents cleared it on the tuning month and 0 on the sealed month.
- **Why:** 80% of the spread between agents came from contract choice (delta and days to expiry), not from reading the map. Useful book-free fact: the 0.19Δ / 14-day lotto loses least.

### A11. AI read of historical boards 📊
- **Tested:** whether a language model's read of a dated historical board can be graded fairly, given that the model may remember what happened.
- **Win rate:** regime label right 60–65% vs **79%** for always answering "range". Four repeats on identical input produced 20%, 57%, 45% and 85% long, which is noise.
- **Result:** as designed, the test could produce a valid FAIL but never a valid PASS. Dates were removed from the AI payload and a live forward arm was added.
- **Why:** on a dated historical board, recall cannot be told apart from skill.

### A12. Sirfart 2.0, a fresh three-layer engine ❌ direction · ✅ vol range
- **Tested:** a from-scratch engine (dealer book + implied vol, price tape, macro) for index products and the 7 mega-caps. It covered direction, levels, range, touch probability and option-structure choice.
- **Sample:** about 1,560 book snapshots, 3.1M minute bars, 90,000 priced option entries. Developed on Apr 2023 → Dec 2025; sealed test Jan → Sep 2026.
- **Win rate:** next-day direction 44.6–55.4% (mean ≈ 49.7%); 0 of 21 models passed.
- **Result:** book levels did no better than random levels at the same distance. The volatility layer's range forecast held (sealed correlation 0.51 on SPY/SPX, 0.41 on QQQ).
- **Why:** range comes from the volatility complex. The dealer book adds nothing to direction or range out of sample.

### A13. Structural read with no search radius ❌
- **Tested:** replacing the engine's fixed search radius with a pure doctrine read: the largest +γ wall anywhere.
- **Sample:** 41 boards, 5,795 name-boards.
- **Win rate:** direction **46.1% vs 53.8%** for the engine; TP1 41.1% vs 50.0%.
- **Result:** total R fell from 165.6 to 53.3, and fills got worse on 28 of 35 boards.
- **Why:** the radius is load-bearing. Without it, entries land on far, dormant walls, fills collapse, and promised R:R balloons while hit rate falls.

### A14. Vanna as the side, and approach direction ⚪
- **Tested:** using vanna sign to confirm, filter or flip the engine's side, and whether walls deflect differently depending on the side price approaches from.
- **Sample:** 4,656 plays on 135 boards; 60,000+ level observations.
- **Win rate:** TP1 51.3% when vanna agrees vs 47.8% when it fights, **but money is inverted** (agrees −$2,833, fights +$2,373). Walls deflect 41.3% vs 41.8% for non-walls.
- **Why:** vanna knows the side no better than gamma. Walls attract touches but don't send price back, from either side and with either sign.

### A15. Per-family constants and the vote tally ⚪
- **Tested:** separate engine constants for index, ETF, mega/large cap and small cap; and a multi-source vote (engine + flow + technicals + projection) for direction.
- **Sample:** 16,072 name-boards.
- **Win rate:** vote direction 47.5% vs permutation 47.7% vs coin **63.9%**. Baseline TP1 by family: index 22%, ETF 48%, mega/large 55%, small 40%.
- **Result:** 0 of 110 per-family cells and 0 of 140 volatility-scaled cells cleared the bar.
- **Why:** families differ in geometry (strike step, distance to the King), not in exploitable outcomes. The study's real yield was three grading fixes, the most important being that the daily walker grants target hits on the fill bar.

### A16–A19. Index tilt as override, router, sizing and sleeve
- **A16, tilt override ⚪:** "when index gamma disagrees with the cards, follow the index." Its own independent short calls were right **25% (1 of 4)**, against 75% for always-long. The tilt read short on 96% of rows.
- **A17, regime router ❌:** fade in +γ books, follow the trend in −γ books. Direction in the −γ subset: **42%** for the router vs 49% for fading vs 54% for always-long, permutation p 0.97 (worse than random). Momentum into the scan date mean-reverts over 5–20 days.
- **A18, tilt-aware sizing ❌:** the tilt called the winning side in 6 of 11 weeks (55%). Half-sizing the plays that fought the tilt cost −50.9R, because those dip-buys were the best cell (+0.84R mean). This is the tail law at work.
- **A19, index-gamma sleeve tilt 🟡:** if index net gamma is negative, size shorts full and longs half. Picked the right sleeve **18 of 23 weeks (78%)**, 5 of 6 out of sample, permutation p ≈ 0.005. Provisional: registered for at least 4 forward live weeks before it can ship as a sizing note.

### A20. Vanna drift and stair-step ⚪
- **Tested:** whether net vanna sign, a vanna magnet or a cross-expiry "stair-step" predicts drift; whether book size predicts move size; and whether +γ walls deflect better than non-walls.
- **Sample:** 3,860 names on 57 boards; 82,828 level observations.
- **Win rate:** vanna-sign direction **49.6%** (bar 55%). Vanna magnet 46.2%. Wall deflection given a touch 36.4% vs 35.6% for non-walls.
- **Why:** the book says where levels are, not which way price goes. −γ pockets deflect as often as +γ walls.

### A21–A23. Projections as forecasts ❌ / ⚪
- **A21:** the projected centre as direction scored **47.2%** vs a 62.8% coin at +5 sessions, and 41.2% vs 64.2% at +10. The band width's raw correlation with range (0.55) fell to 0.02 on holdout, because it was trailing volatility.
- **A22:** five static projection reads (centroid, signed pull, proximity, King, vanna). Discovery 50.1% vs a 69.0% coin; holdout 52.3% vs 64.2%.
- **A23:** a 135-cell parameter sweep. The projection beat "no move" on only **40.9%** of symbols; deeper expiry baskets did worse.
- **Why:** a projected centre far from spot is a far wall, not a forecast.

### A24. Near-expiry floor vs ceiling ❌
- **Tested:** go long if the biggest near-expiry floor outweighs the biggest ceiling, and short otherwise; also walked as a play from wall to wall.
- **Sample:** 13,772 name-boards.
- **Win rate:** direction **50.6%**. As a play: TP1 38%, total −450.7R.
- **Why:** wall size carries no direction, and a one-node stop against a distant target gets stopped.

### A25. Far-dated (LEAPS) book direction ❌
- **Win rate:** 2 good weeks of 8. Persistent leans predicted 8-week direction **49%** vs flipped leans 50%.
- **Why:** far-horizon leans grade at a coin, and no contract choice fixes missing direction.

### A26. Negative-gamma King at spot ⚪ / ⏳
- **Tested:** when price sits within 0.30% of a −γ King, does it lean up?
- **Win rate:** **+6.6pp** over each board's up-rate at +1 day (48 of 74 boards), −5.4pp at +2 days, and gone by +5 days.
- **Why:** one significant cell out of nine looks, gone within a day, is what chance produces. The forward test is blocked.

---

# Part B — Levels & nodes: do dealer walls attract, hold or repel?

### B1. Node size as a share of the King ⚪ / ❌
- **Tested:** whether a node's size relative to the King (slices from 5% to 100%, including 25% and 50% cuts) changes reach, speed, bounce vs break, follow-through, pinning, blocking or money. The one survivor, "a 33–50% node makes pushes shallower", was then forward-tested.
- **Sample:** 5,628 end-of-day boards (200,977 levels) and 858 intraday snapshots, checked by 11 analysis lanes and 56 independent verifier runs. Forward test: 1,320 new books, 31,292 first touches.
- **Win rate:** forward test, broke through by 0.20 ATR within an hour: **29.6% at the 33–50% node vs 30.4% elsewhere** (permutation p 0.72).
- **Result:** of 25 verified claims, 15 were refuted and 10 weakened. Every fade and every break at every slice lost about the round-trip cost.
- **Why:** every "big node" effect dissolved into mechanics. Big nodes sit nearer spot, they sit on round strikes, comparing two touched levels selects the nearer one, and several effects lived in a single month. The study also found that one historical archive had stamped the prior close as "spot". On the true spot, earlier "walls are reached more and held less" findings died.

### B2. Floor/ceiling and target detection retrain ⚪
- **Tested:** requiring the bound to be ≥ 25/30/40/50% of the King; entering from the full multi-expiry map; 26 target rules; alternative stops. Everything was compared with a random strike at the same distance.
- **Sample:** 6,801 boards, 355 names, two blocks.
- **Win rate:** a detected floor was reached **32.3% vs 22.1%** for a random strike, but once touched it **held 28.8% vs 33.9%**. A "nearer significant bound" rule raised TP1 from 11.2% to 20.2%, and a naive random-nearer control did the same.
- **Result:** the King-ratio gate never re-picks a bound; it only drops plans, and dropping them loses money (134 vs 202 symbols). 0 of 26 target rules passed. Three default-OFF toggles shipped on request.
- **Why:** "better detection" was distance. A wall here behaves like a magnet, not a barrier: it is reached more and holds less.

### B3. Overnight level on a dealer wall ⚪
- **Tested:** whether an overnight high/low or prior close that sits on a gamma wall is touched more than either level alone.
- **Win rate:** **47%** for the confluent level vs 62% for the lone level. The raw "53% vs 27%" collapsed to −1.1pp once distance was matched.
- **Why:** overnight levels sit near the open, so the walls they line up with are just near walls.

### B4. "Don't buy until X" (room to the next level) ❌
- **Tested:** whether the room between price and the next chart level above or below predicts which one gets hit first.
- **Sample:** 787,368 entries, 349 symbols, 89 sessions.
- **Win rate:** reach-first odds equal the random-walk value (within 2–3 points) in every bucket. **Random "shadow" levels beat real levels** in 3 of 5 buckets.
- **Result:** expected value fell as R:R rose (R:R 0.25 → −0.16R; R:R 5.6 → −0.48R). One candidate remains: a stack of 2+ levels stops a move about 3.5pp more often.
- **Why:** level placement carries no directional information. Reach is a function of distance.

### B5. Node growth → next-session touch 🟡
- **Tested:** whether a King, floor or ceiling that grew since the prior session gets touched more the next session, and whether the close moves toward it.
- **Sample:** 24,993 node-states.
- **Win rate:** touched **King 47.7% vs 34.6%** flat, floor 46.2% vs 28.8%, ceiling 36.8% vs 22.0%. The close moved toward the node only 41–47% (grown) vs 47–55% (flat).
- **Why:** growth marks where price is active. Price visits the node and then leaves it. This was confirmed later on 2026 data (J2): touch only, never hold, never direction.

### B6. Node-to-node route mapping 🟡
- **Tested:** whether a rule for "which level price visits next" beats a distance-matched control, and which node attributes earn touch odds beyond distance.
- **Win rate:** best route lift +0.009 to +0.016 against a +0.03 bar; the best of 75 random routes scored +0.009. Node attributes did clear the bar: **King +0.042, front expiry +0.033, grown +0.043, worn (3+ tests) +0.053.**
- **Result:** the day-0 edge halves by day 1 and is gone by day 2. Shipped as drawing weights on a map tool, display only.

### B7. Growth as a ranking slot ⚪
- **Tested:** whether growth aligned with the play's direction should add points to the attention ranking, in six forms.
- **Win rate:** best form **29W / 19L** symbols (p 0.19). 26 of 28 ranking deltas fell below a random tie-break.
- **Why:** a grown node is mostly a near node, and the ranking already pays for nearness.

### B8. How to measure node growth 📊
- **Result:** a daily ±10% "grew" rule is exceeded by **49–50%** of matched cells one session apart, which is a coin. The noise floor depends on tenor: 0–3 days ±85%, 31–90 days ±40%, 90+ days ±15%. Summing across expiries disagrees with the per-expiry cell 19.2% of the time.
- **Why:** growth has to be judged per tenor on the same strike × expiry cell, never on a strike summed across expiries.

### B9. Growth, wear and tenor lenses 🟡
- **Growth:** grown walls were touched **9.4% vs 2.9%** for shrunk walls (+4.5pp after distance matching, p 0.018). They held no better.
- **Wear:** a worn target hit 34.1% vs 26.7% raw, but only +0.9pp after distance matching. Worn stops got run *more* often.
- **Tenor weighting:** the weighted King and the summed King both scored 53.6%. The tenor axis is closed.

### B10. Sweep into a +γ floor ⚪
- **Win rate:** held within 30 minutes: **floor 60.0%, −γ pocket 59.9%, empty price 62.2%.** Sweep-order pressure predicted the next minutes about 50% of the time.
- **Why:** everything holds about 60%. Order pressure describes the current minute, not the next one.

### B11. Near-expiry King as an intraday magnet ❌
- **Win rate:** moved toward the King **52.5%** (near-expiry) vs 53.3% (summed). Where they disagree: 27 vs 24.
- **Why:** the 55% in discovery came from one ticker.

### B12. Outside educator's GEX doctrine, 16 claims 🟡
- **Tested:** retest decay ("66% → 33%"), "rugpull" shapes as direction, "air pockets are fast", three-index agreement, a five-filter A+ checklist, and a deflection plan.
- **Sample:** 6,801 boards and 13.3M minute bars.
- **Win rate:** rugpull / reverse rugpull / double-stack as direction: **53% / 45% / 55% vs coins of 64–65%.** The five-filter checklist is **inverted**: NO-PLAY setups hit TP1 16% vs 3% for A+.
- **Result:** 2 of 16 claims survive. The educator's deflection plan beat the engine on more symbols (191 vs 110), while the engine made more per fill ($50.5 vs $43.4). Retest decay was null (hold 38% vs 36%). "Three indices aligned" paid less.
- **Why:** touch is proximity, worn nodes hold slightly *more*, and the checklist rewards the worst setups. A follow-up kept the doctrine's vocabulary but replaced every number with the measured one.

### B13. Stops placed at worn strikes ⚪
- **Win rate:** worn stops were hit 30.7% vs 18.1% in the first arm, then **42% vs 40%** at scale (10,970 plays). Moving the stop one node wider helped worn and fresh stops equally (TP1 +5pp each).
- **Why:** the first +12pp didn't replicate. The nudge's gain was a generic wider-stop effect.

### B14. Expiry-day inflation and the weekly wall tag ⚪
- **Result:** boards scanned on an expiry day carry **1.82×** the gamma (98 of 103 symbols). This is confirmed but harmless.
- **Win rate:** grown vs decayed walls week over week were touched 61.1% vs 49.5% raw, but **57.5% vs 53.9%** after distance matching; 45 of 92 symbols positive.
- **Why:** walls change about 80% week over week, which swamps everything else. Grown walls simply sat closer to price.

### B15. External newsletter's level taxonomy and filters ⚪
- **Win rate:** TP1 by entry location: **King 35.0%**, floor/ceiling 40.9%, other 43.3%. Grown target 23.1% vs decayed 22.3%.
- **Result:** the three filters passed a within-board permutation but failed per board and per symbol, and they cost 26–50% of total book dollars.
- **Why:** the filters choose which names to trade, not when a setup is good, and they discard winners.

### B16. Futures-level kill-check ⏳
- **Win rate:** walls touched 25.6% vs 16.3% raw, **−2.4pp** after distance matching. Only one session has been tested.
- **Result:** futures levels are the cash-index strikes times a ratio. The ratio drifts enough that they must be treated as ±0.05% zones.

### B17. Raw-ladder level constructors ⚪
- **Win rate:** held: **current wall 40.0%**, random strike 36.7%, cluster 35.5%, King 29.6%, gamma flip 25.2%.
- **Why:** the current wall is already the best constructor. The King and the flip are zones price travels through, so they work as anti-levels.

### B18–B21. Which book to read 🟡 / ⚪
- **B18, adding tomorrow's expiry to the 0DTE map:** held 60.0% (n = 5) vs 37.5% (n = 8). The structural difference is real, but the hold test is underpowered.
- **B19, a wider 0DTE map:** held 53% / 50% / 52% across 1 expiry, 0–5 days and 7 expiries. Tomorrow's book doesn't pin today's tape better.
- **B20, persistence, tenor and dark-pool prints:** held 45–48% in every persistence bucket, and 90% of entries are already multi-expiry walls. Dark-pool prints held intraday 37% vs 14%, but over 5 days 43% vs 63%: short-lived marks, not barriers.
- **B21, per-expiry grid read vs summed book:** held 46% vs 42%, but moving the wall moved the fills and the plays did worse.

### B22–B26. Building the book 📊 / ⚪
- **B22, gross vs signed-net walls:** TP1 **51% vs 46%**. The money was one crash board; the other five summed to −$94.
- **B23, calendar expiry windows:** the week window hit TP1 **59% vs 46%**, but made less money (+$199 vs +$437). Nearer walls give more hits and less pay.
- **B24, summing conventions:** the net King matches the gross King 80% of the time and the front-expiry King 57%. 53% of swing names have a strike where expiries cancel.
- **B25, rebuilding exposure from the option chain:** the textbook sign convention was right on **14 of 26** strikes. Magnitude follows open interest, but sign needs the dealer's inferred side on each print.
- **B26, an independent open-source GEX engine:** a naive open-interest × gamma calculation disagreed in sign on the near-spot King **100%** of the day.

### B27–B28. Map freshness
- **B27, rescan when price touches the entry ⚪/📊:** no node value predicted the outcome (best ρ −0.12, p 0.10). Intraday, two-thirds of the wall map turned over. Shipped as a "re-read the map" display, not a signal.
- **B28, intraday book freshness ✅ suggestive:** a midday 0DTE map caught **18%** of afternoon turns vs **11%** for the morning map (replication 9 of 10 vs 7 of 10), and replay +43.7R vs +6.1R. The wall map's half-life is about 2–3 hours.

---

# Part C — Geometry, stops & tenor

### C1–C2. Entry and ladder radius sweep ⚪
- **Tested:** 9 entry radii × 10 ladder radii = 90 cells in every mode.
- **Sample:** 137 boards, 13,818 name-boards, Aug 2024 → Sep 2026.
- **Win rate:** swing TP1 **51–52% in every cell vs 52% shipped.** The best cell beat shipped on 41 of 82 boards.
- **Result:** 0 of 90 cells survive correction in any mode. The discovery winner lost −$653 on holdout.
- **Why:** winner's curse from 90 tries on a flat surface. The shipped values already sit on top of it. An earlier 21-board sweep reached the same verdict.

### C3. All 132 toggle combinations ⚪
- **Win rate:** best 3-day combination TP1 17.4%, beating shipped on **176 vs 148** symbols (p 0.13). Best swing combination 153 vs 145.
- **Result:** 0 of 132 beat shipped. One option outside the menu, a raw one-strike stop, won per symbol (230 vs 93), but pooled $/fill fell and 2026 data did not replicate it.
- **Why:** most toggles are filters or coverage changes, not better geometry. **The best combination is the default.**

### C4. Floor under the stop-tightness reward ❌
- **Win rate:** the floored build beat the base on **13 of 22** changed boards; the bar was 9 of 12 on the committed set.
- **Why:** at scale, a tight stop is hit more often, but the smaller risk denominator cancels that out. Tight stops cost nothing in R.

### C5. Drop the nearest expiry ❌
- **Win rate:** fill **50.2% vs 55.6%**; 8 of 20 boards better; −0.05R per play.
- **Why:** dropping the front expiry flips the side on 39% of cards, which makes it a different book rather than a cleaner one.

### C6. Tight-book selection bias ⚪
- **Win rate:** money-positive 53.7% for the tightest tercile vs 58.1% for the widest. After controlling for trailing volatility: 56.5% / 52.8% / 56.6%.
- **Why:** the book's range prediction mostly restates yesterday's realized range.

### C7. External 25-item tune battery 🟡
- **Win rate:** thin-book plays hit TP1 51.4% vs 43.9% with flat money. Equal-risk sizing beat flat sizing on only 46% of dates.
- **Result:** five hygiene/display items shipped: chip retirement, a thin-book de-double-count, a flip-trust mark, a low-conviction band, and a study ledger.
- **Why:** items that relabel passed. Items that change size or risk trim the fat tail. 67–69% of dollar winners sit in the widest-stop tercile.

### C8. Per-mode autopsy 📊
- **Result:** the 3-day mode made +41R on the stock, but its 1–4-day options expired before the move matured.
- **Shipped:** 3-day plays now use swing-tenor contracts. The levels were right; the vehicle was too short.

### C9. LEAPS mode ❌
- **Win rate:** direction 64% and TP1 57% at first, then **direction 55% and TP1 52%** out of sample, at −24.8R.
- **Why:** the positive headline was bull drift. Longs made +32R and shorts −16R in a rising window.

### C10. Re-grade of 22 months of archived boards 📊
- **Sample:** 53 boards, 2,818 plays, Aug 2024 → May 2026, regime-balanced.
- **Win rate:** fill 72%, **direction 56%, TP1 53%.** Total +20.6R (+0.01R per play).
- **Result:** regime does not separate outcomes. Shorts in rallies hit 44% at −0.43R. This killed the LEAPS headline and the stop-width "consequence".

### C11. Break-hold-retest entry ❌
- **Win rate:** as a *replacement* for a missed pullback, **66%** (108 of 164), +28.9R. As an *add-on* to a filled play, **45%**, −323R.
- **Why:** as a replacement it is positive but too noisy per board (11 vs 6, p 0.33). As an add-on it doubles risk into a losing move.

### C12. King-guard and farther first target ❌ / ⚪
- **Win rate:** the King-guard's vetoed set went **3W / 7L (30%)**, but only 6% of fires had the configuration, and its gain did not beat a random-removal null.
- **Result:** moving TP1 to the 2nd/3rd node improved results monotonically (+0.06R per fire) but missed significance by a hair.

### C13. 3:1 R:R gate ❌
- **Win rate:** a 3:1 target hit **0 of 12**. Engine +16.2R vs gated −2.9R.
- **Why:** median R:R was 1.00 for both winners and losers. In a fade engine, low-R:R plays are paid for by hit rate.

### C14. Four doctrine tunes ❌
- **Candle tie-breaker** ("long on red, short on green"): direction **52.6%**, +17.6R, p 0.26.
- **Rolling floors as a tell:** −2.3pp, the wrong way.
- **Moving worn stops one node wider:** the same gain as widening fresh stops.
- **Trading neutral cards as a range fade:** worse on 86 of 133 boards, −250.7R.

### C15. Breakeven trail after +1R ❌
- **Result:** saved 26 losers (+52.4R), killed 47 winners (−107.8R), net **−55.4R**. Every arming threshold lost, in all five regimes.
- **Why:** fade entries routinely revisit the entry before running to target.

### C16. Wait for the pullback vs enter at the next open 🟡
- **Win rate:** the pullback entry was better head to head **349 vs 276**. Plays that never pulled back still hit TP1 **86%** when entered at the open.
- **Why:** the pullback gives a better price, but the plays that never pull back are disproportionately the runners.

### C17. Worn entry level (tap count) ❌ retired
- **Win rate:** in August, TP1 52% worn vs 41% fresh (+0.19R vs −0.05R, p 0.045). On 17,924 fills in 2026 and 2023–25: **49–51% vs 45–50%**, with money sign flipping by period.
- **Why:** the entry is a wall by construction, so price visits it anyway. The +0.19R claim was retired and removed from ranking.

### C18–C20. External systems' play construction ❌
- **C18, external newsletter's plays:** TP1 **30.2% vs 38.9%** for the same geometry shorted; −$5.08 per $1k vs the mirror, worse on 194 of 314 symbols. Our engine scored 44.8%. A raw-structure stop (median 0.86%) sits inside one day's noise.
- **C19, one newsletter week vs our engine:** 1 of 5 pre-registered predictions hit (R:R lower: 1.67 vs 3.92).
- **C20, an external setup-rule replica:** TP1 **41.3% vs 52.8%** for our engine; money-green 45.7% vs 60.2%. An R:R ≥ 3 gate selects far-target promises that rarely pay.

---

# Part D — Ranking & selection: can we order the plays?

### D1. Can display data re-order the plays? ❌
- **Win rate:** share of boards where the order agreed with money: **shipped score 42 of 111 (inverted)**, edge points 68 of 111, P% 71 of 111. The fitted combination reached 35 of 53 on holdout.
- **Result:** holdout mean ρ 0.052 against a 0.10 bar. The shipped score's ρ was −0.091.
- **Why:** the score rewards tight stops, so it runs against money. Money sits in a few far-target plays, and no single number can rank both the average play and the outliers.

### D2. P% calibration, EV ranking, winner/loser profiles ✅ P% · ❌ rest
- **Win rate:** TP1 by P% bin on holdout: **31% → 54% → 56% → 64% → 72% → 76%.** Higher P% meant a higher TP1 rate on 100 of 111 boards (AUC 0.677).
- **Result:** P% read 12–14 points too low and was recalibrated for display. EV ranking was inverted (ρ −0.11). The winner-profile selector scored 19 of 33 boards. The loser-drop rule scored 10 of 20 and cut 18 of the 135 top winners.
- **Why:** the top and bottom deciles share one profile (far target, wide stop, low P%). That profile is variance, not edge. **Cutting losers cuts winners.**

### D3. From-scratch ranking score ✅ narrow
- **Tested:** a new score built without the old score, grade or P%. The winner uses three inputs: target reach in units of the name's own daily volatility, a "fade" term, and log dollar volume.
- **Sample:** a sealed holdout of 827 + 998 plays.
- **Win rate:** TP1 **29.3% (top half) vs 10.7% (bottom half)** in 3-day mode and 32.1% vs 12.9% in swing, against a random score's 22.7% vs 21.5%. 150 / 101 and 154 / 107 symbols.
- **Caveats:** it was the only survivor of 926 hypotheses, at half its discovery size. It orders average plays and does not find the tail: the best play still lands at the 52nd–63rd percentile. Recommended only as a display sort with a forward test.

### D4–D6. Selectors that didn't transfer ❌
- **D4, "PRIME" edge-points selector:** in sample, the top 3 per board won **79%** of fills vs 60% for the board. A later check found −0.31R mean, and about −0.02R after two inputs were removed.
- **D5, forward batch 1:** on 10 new dates the selector won **59% vs the board's 60%** (−0.30R per pick). Doctrine addendum: "fuel gap" paths hit TP1 40% vs 54%, and a target parked on a strong vanna node was reached 35% vs 52%. **A wall defends before the touch.**
- **D6, star-checklist promoter:** starred plays won 44% vs 31%, but only 26% of top winners were starred, the same as the base rate. The only weak ranker was how much the name was already moving.

### D7–D10. Pooled reads and tune batches 🟡
- **D7, eight-board replay:** **P% ≥ 60 → TP1 87%** (+0.55R per fill), and 90% on dates never used for calibration. R:R 0.5–1.5 → 73%; R:R > 3 → 24%. Stale plays 72% vs fresh 37%. Entering one zone in front of the wall was significantly worse (−5.2R).
- **D8, eight tunes tested before building:** TP1 by R:R: < 0.5 69%, 0.5–1.5 **58%**, 1.5–3 29%, > 3 **5%**. By grade A+ to C: 38/31/42/43/38%, flat to inverted. The grade is decoration.
- **D9, grade/score refit:** out-of-sample ρ −0.02 vs +0.06 for the current formula. Which cells pay flips with the regime.
- **D10, trapdoor, neutral lean, veto audit:** trapdoor results flipped between corpora. Neutral leans made −0.07R per fill. Vetoes for "fights acceleration" ran +1R **47%** of the time (+0.26R per fill); the other five veto reasons protected.

### D11–D13
- **D11, data-sweep ideas 51–58 📊:** an external entry-timing label read "LATE" on 89% of rows, so it cannot beat a constant. 52-week position, days-to-cover and index membership shipped as dated display facts.
- **D12, three-read agreement chip ⚪:** "all three agree" TP1 was 0% (n = 3) vs 42.9% when split. One read abstains on 99.4% of cards.
- **D13, long-shots strip ⏳:** a second list of R:R > 3 tail candidates, graded against a random draw. Accruing.

---

# Part E — Money & vehicles

### E1. Endorsed plays traded as real options ❌
- **Tested:** every endorsed 2026 plan priced on real 5-minute option prints at the fill bar. SAFE is a near-the-money contract, LOTTO a cheap OTM contract, and the control is the same plan with the opposite option type.
- **Sample:** 18,796 resolved plans; about 23,000 priced trades.
- **Win rate:** green after costs: **SAFE 41% / 37%, LOTTO 21% / 23%** (core / wider universe).
- **Result:** mean net per trade: SAFE −9.6% / −8.9%, LOTTO −28.7% / −18.5%. Losing symbols outnumbered winners in every arm (p < 0.0001). The engine's side was no better than the mirror.
- **Why:** a play that hits its target returns about +43% on the option, but with a coin-flip side, time decay and spread take the rest.

### E2. Trade only the P% ≥ 60 subset ❌
- **Win rate:** TP1 **58% vs 37%** (and 58% vs 31%, 54% vs 32%), and the options subset 55–60% vs 29–37%. **Money was never better than the rest**; subset vs rest per symbol was a coin.
- **Why:** high P% mostly means a near target. Choosing by target distance overlaps the subset by 71–79% and earns the same. The subset excluded 96–99% of top-decile winners.

### E3. ATM SAFE + OTM LOTTO contract picker 🟡 (superseded by E1)
- **Win rate:** modeled ATM 10-DTE SAFE 54%. On real premiums, multi-day: **SAFE 41%, LOTTO 30%**. Same-day: SAFE 61%, LOTTO 7%.
- **Result:** SAFE was positive on that 11-week corpus. ATM beat a 0.30–0.40Δ band on 55 of 88 paired trades (p ≈ 0.006). The edge was thin: doubling the spread cut it by 72%. The larger 2026 test (E1) did not reproduce it.

### E4. Breakeven-past-TP1 guard 🟡
- **Win rate:** 0DTE contracts on plays that reached TP1 won **66%** when breakeven sat inside TP1 vs **44%** when it sat past TP1 (p 0.088).
- **Why:** on same-day options, a target smaller than the option's minimum time value cannot pay at any strike. On longer tenors, remaining time value rescues it.

### E5. Which strike to buy (model study) 📊
- **Result:** on a real fire, premium-R barely moves with strike (spread ≤ 0.06R). Theta on timeouts is the only separator, and it favours ATM. About 61% of 0DTE fires end at the timeout.

### E6–E8
- **E6, late-session 0DTE cutoff 🟡 (model only):** modeled option P&L went from −14.9 to **+8.1** with a 13:00 cutoff, keeping 77% of fires and 83% of raw R. Needs real option P&L before it counts.
- **E7, lotto by regime 🟡:** lottos made money in crash (+$3.9k), rally (+$3.8k) and whipsaw (+$4.3k) weeks and lost in pinned weeks (−$3.6k). Lottery tickets need movement.
- **E8, no plays on a structurally decaying volatility ETN ✅ (small n):** 5 of 8 trades stopped, −3.3R.

### E9. Minute walker: are same-bar target hits real? ❌ (book money)
- **Tested:** the daily grader credits TP1 whenever the fill bar's range contains both entry and target. Minute bars checked whether the target printed *after* the fill.
- **Win rate:** **≈ 63% of same-bar TP1s are real; ≈ 37% are not.**
- **Result:** book money per $1k per fill (shipped → minute-resolved → pessimistic): core +0.52 → −1.31 → −3.51; wider +2.17 → +0.10 → −1.30; 2023–25 −0.51 → −1.48 → −2.16.
- **Why:** the daily walker overstates the book by about $2 per $1k per fill. On an honest walker, the endorsed book is flat to slightly negative before costs.

### E10. Slippage ✅ finding
- **Result:** perfect fill **+69.3R → 0.10% slip +32.9R → 0.25% slip −13.8R → 1% slip −167.3R.** The 2026 re-test (J8) confirmed it.
- **Why:** the whole edge lives within about a quarter of a percent of the level.

### E11. Earnings / catalyst-in-window veto ❌ (veto refuted)
- **Win rate:** stop rate **22%** for catalyst plays vs **34%** for clean plays; +0.32R vs −0.01R per fill.
- **Why:** catalysts resolve levels, so catalyst tape helps the engine.

---

# Part F — Volatility, range & time

### F1. Implied-vol layer × dealer book 🟡
- **Tested:** about 14,600 formulations across ten lanes, with 250 registered candidates on a sealed Jun–Sep 2026 holdout (about 253 symbols): range, vol-next, band containment, direction, touch and plan outcome.
- **Win rate:** the nearer of floor/ceiling is reached first **73.0% vs a 61.2% coin** (205 / 19 symbols). Direction toward the King **49.8% vs a 66.6% coin.**
- **Result:**
  - Implied-vol-only claims: 53 PASS.
  - Book-only claims: 5 PASS, 59 FAIL.
  - Next-session touch by distance in implied moves: ≤ 0.25 → 72%, 0.5–0.75 → 43%, 1–1.5 → 17%, 2–3 → 2%. **A blank strike at the same distance does the same.**
  - No volatility state makes the plan book positive on the minute walker.
- **Why:** implied vol prices how far a stock moves, especially the earnings clock read against the term structure. Book levels matter only through their distance measured in implied moves. Shipped as a display-only "move budget".

### F2. Own implied vol vs VIX for next-day range 🟡
- **Win rate:** the stock's own-IV model beat a VIX + realized-vol model on **137 of 198 names (69%)**, in both halves and outside earnings.
- **Result:** median correlation gain +0.030 against a +0.05 bar (not met). Own 1-day IV alone ranks range at ρ 0.257 vs 0.125 for VIX alone.
- **Why:** a stock's own option market prices its own risk, which the VIX cannot see.

### F3–F4. Index edges 🟡
- **F3, exploratory:** next day up **52–58%** after weakness in 2000–2026, but negative in 1970–99. Range is consistent in every era: after a −2% day the next range is ×1.16–1.68; after the narrowest range in 7 days, ×0.79–0.95.
- **F4, registered (1983 → 2026):** an inside day breaks its high **62%** of the time and its low 51%. Range after a big down day: ×1.44 indices, ×1.33 mega caps. Overnight drift was positive in both eras, but failed as registered.
- **Why:** volatility clusters and is forecastable. Direction tilts are small and flip between eras.

### F5–F6. Pivots 🟡
- **F5, VIX pivot zones:** the zone was touched **83.8%** of the time; after VIX closes above R2, it reverts toward the pivot **58%** of the time. Equity leans were swamped by drift. Shipped as a context chip that never emits a short.
- **F6, pivots across 56 years (66,212 bars):** **0 of 66** direction tests were tradeable, while the vol-regime read passed 27 of 28. Below the pivot is the high-volatility side on every equity series.
- **Why:** pivots locate volatility, not direction.

### F7. "Markets move in 5-minute cycles" ⚪
- **Win rate:** swing turns on a 5-minute bar close **23.6% vs a null of 18–22%**. No spectral peak at 5 minutes. The most common retest came at 2–3 minutes.
- **Why:** chart conventions and order slicing cluster decisions on bar closes. That is an anchor, not a clock.

### F8. σ-ladder tags 🟡
- **Win rate:** continuation to the next half-rung ≈ **50%** at every rung (a coin). A tagged 1σ rung "sticks" (the close holds beyond it) **+8.0pp up / +6.8pp down** above null, on 25 of 25 instruments (p 6e-8).
- **Why:** a tagged rung is a hold level, not a fade level. Other effects were range arithmetic.

### F9. Unbroken first-hour range ⚪
- **Win rate:** break side **46–56%** (a coin); the close beyond the broken side **50.6%**. About 45% of breaks were false.
- **Bonus:** the study found that a shipped reach percentage was two-sided but displayed per side, so it read about 2× too high. That was fixed.

### F10. Prior-day σ and levels; a better σ estimate 🟡
- **Win rate:** the prior close was touched **+3.4pp** beyond the σ model on 10 of 10 mega caps, and the overnight low +4.7pp on 10 of 10 small caps. Prior-day high/low: ±2pp, pure distance.
- **Result:** a blend of 1-, 5- and 22-day volatility beat the 20-day σ on every instrument (p 0.002), and the printed "50%" level now hits 47–50% instead of 35–47%.

### F11. Implied move budget and earnings crush 📊
- **Result:** the 1-day implied move covered **77.8%** of daily moves (vs 68% expected). Implied exceeded the actual move on **79.4%** of 155 earnings reports (median actual ÷ implied 0.58).
- **Why:** this is the volatility risk premium, largest into earnings.

### F12. Gamma centroid stretch and pin ⚪
- **Result:** fades when price is stretched far from the centroid made +0.142R vs +0.280R when not stretched, leaning the wrong way. The pin test was underpowered.
- **Why:** a big stretch usually means a trend day escaping the book.

### F13. "Premium holds on a dip → reversal" ❌
- **Tested:** a community idea: when a short-dated OTM call holds its value during a ≥ 0.40% dip, the next hour reverses up.
- **Sample:** Mag 7 over 645 sessions (10,920 dips) plus an index arm.
- **Win rate:** holdout **63 / 42** symbol-months (p 0.050) after discovery **55 / 54**, a sign flip (−2.4bp → +4.0bp, bar 5bp). Index arm: wrong sign in both blocks. A literally flat premium on a dip happened 18 times in 10,920.
- **Why:** a "held" premium is ordinary vol rising as price falls.

---

# Part G — Chart levels without dealer data

| Study | Win rate | Verdict | Why |
|---|---|---|---|
| **G1. Reach clock** for an untouched prior-day high/low | reach by close 66% (0–0.25 ATR, full session) vs 61% same-distance control. An unfilled gap still open at 10:00 fills 40% of the time; at 11:00, 19%. | 📊 | reach = distance × time left |
| **G2. Gap fill** | gaps < 0.25 ATR fill 79–81% the same session vs a mirror level reached 75–82% | 📊 | mostly distance; the small index/VIX excess is post-hoc |
| **G3. Liquidity-hunt sweeps** (56,000+ events, 2016–26) | first breach closes back inside 41–47%; the sweep extreme is exceeded again within 60 minutes 65–78% of the time | ⚪ | every discovery winner died in confirmation; reversals are symmetric noise |
| **G4. Eleven chart reads** (skew, volume profile, pivots, taps, cone…) | worn wall +11.2pp touch, but non-wall +12.5pp; skew as direction 48% / 52% | ❌ / 📊 | revisits belong to any recently traded price, not to walls |
| **G5. Classic technical levels** | longs at range lows stopped 41%; shorts at range highs +1.65R (n = 9) | 🟡 | a short pays when price has already delivered up into the ceiling |

---

# Part H — Flow & outside systems

| Study | Win rate | Verdict | Why |
|---|---|---|---|
| **H1. Retired flow scanner, confluence ledger** | option green at +5 sessions: **9%** (score ≥ 3) vs **41%** (score ≤ 0); all findings 34% | ❌ inverted | flow direction is null; ranking pushed findings into decaying contracts |
| **H2. Whale prints** ($250k – $5M+) | next day right **46.9–52.7%** vs 52.3% base; a "64.3%" print-level figure was pseudo-replication | ❌ | big prints are hedges, rolls and overwrites, not smart money |
| **H3. Flow at fire time** (0DTE) | +0.155R with flow vs +0.122R against (n = 31) | ⚪ | day type dominates |
| **H4. Flow agreement** (swing, 450 plays) | TP1 44.3% agree vs 35.7% disagree, but mean R +0.455 vs +0.588 | ⚪ | premium tilt measures activity, not conviction |
| **H5. Call/put premium skew** | TP1 61% agree vs 47% disagree, +0.07R, p 0.44 | ⚪ | unsigned skew is activity |
| **H6. Can bought vs sold be read?** | reliable side on 0.5% of 1,725 findings; a sold contract read as 50–99% "bought" by quote-free methods | 📊 | without the quote at each print, side cannot be inferred |
| **H7. Head-to-head vs an external AI analyst** (11 weeks) | TP1 within 5 days **41% engine vs 32% analyst**; engine +180R, positive 11 of 11 weeks | ✅ core fade | both are level machines with coin-flip direction; the edge is structural stops plus taking TP1. Holding past ~5 days halves the book. |
| **H8. Seven externally proposed fixes** | squeeze veto blocked losers 37.9% vs a 41.9% base (it removes winners) | ❌ | rules that change the stop move R through the denominator |
| **H9. External trading-agent post** | claimed 92.3% on ~13 trades | 📊 | a test-tier sample, no criteria disclosed |
| **H10. External futures system** | claimed 88.9% on 9 trades; profit factor 140 rests on a single loss | 📊 | 9 trades cannot support a win rate |
| **H11. Squeeze side-engine** | **19%** of fillable tickets; −22R; negative in all 27 parameter cells | ❌ | at birth, price was already a median 7.4 ATR past the wall |
| **H12. Enrichment v3** | levered-ETF agreement vote: 3 of 3 wrong together | 🟡 | long and inverse levered ETFs hedge the same complex, so their agreement is correlation, not confirmation |

---

# Part I — The 0DTE day-trader (July–August 2026)

### I1. Unit-of-account audit ❌
- **Win rate:** scored from where the alert actually fired, win rate ran **41–62%** by distance from the wall, and **no bucket was profitable** (≈ 0.000R per fire). Scored from the wall it had shown +222R.
- **Why:** alerts fire up to 1.5 zones before the wall, but trades were scored from the wall, which handed every fire free R. **The documented edge was an accounting artifact,** and the scoring unit was changed.

### I2. P&L anatomy 📊
- **Win rate:** when the first minute after the fill was favorable, the trade won **62.8%** (+0.14R); when adverse, **37.9%** (−0.32R).
- **Result:** full stops cost −121.7R, 3.25× all other losses combined. The ticket quoted 1.00 R:R at the wall vs 0.28 at the real fill. A split entry was −28.3R.
- **Why:** losses concentrate in full stops at the wall. Wall fills are adversely selected.

### I3. Ten-tune shortlist 🟡
- **Result:** a confirmed 1-minute rejection made **+0.136R** per fill vs **−0.948R** for a failed rejection, the largest split in the corpus. It resolves a median of 7 minutes after the fire, though, so it can only drive sizing, not entry. The velocity veto missed by one notch; confidence tiers, late-fire cuts and a naive trail failed.

### I4. Drift veto ❌
- **Win rate:** of the fires vetoed at 1% drift, **44%** were losers (22 losers killed vs 28 winners). The veto beat only 32% of random nulls.
- **Why:** in a mean-reverting regime, fading the extension *is* the edge.

### I5–I7. Early full-scan audits
- **I5, week of 7/17 ❌:** hard stops went **3W / 16L (16%)**, ≈ −15R. Stops drawn in noise (median 0.30σ, about a 76% chance of being touched), uncalibrated targets and catalyst blindness produced the v2.6 fix list.
- **I6, same week on the tuned engine ✅ (in sample, one week):** TP1 **18 of 38 (47%)**, ≈ +26R vs −15R before.
- **I7, first full scan 📊:** 32 of 39 plans had R:R < 1, and some stops sat up to 54% from spot. This produced stop caps, no-trade gates and a minimum target distance.

---

# Part J — The 2026 canon re-test

*Every surviving claim re-tested through the shipped engine on two universes (40 core names and 213 more) × 184 sessions (Jan → Sep 2026) in three date blocks, plus 10 names over 2023–2025. The unit is the symbol, with multiple-testing correction.*

| Claim | Win rate | Verdict | Why |
|---|---|---|---|
| **J1. Engine side beats the board coin** | 48.6–50.1% vs coin 61–71%; 2023–25: 47–49% vs 80–87% | ⚪ (expected; proves the harness doesn't leak) | the book carries no direction |
| **J2. A grown wall gets touched next session** | **30.3% vs 20.5%** (decayed); +3.3–3.8pp distance-matched; **40 of 40** and **205 of 213** symbols; 10 of 10 in every history year. Hold: a coin. | ✅ touch · ⚪ hold | growth = intent; price visits but isn't turned back |
| **J3. P% ranks hit rate** | AUC **0.65–0.72** on every block and both universes; ranked TP1 correctly on 122 of 123 boards (wider universe) | ✅ (rank, never a selector) | P% is reachability |
| **J4. Thin-book veto** | endorsed beat vetoed on **19 of 25** and **107 of 173** symbols; vetoed −$6.8 vs endorsed +$2.1 per fill | ✅ | the one veto that earns its keep: thin books give unreliable levels |
| **J5. Delivery tier ("already partly delivered")** | TP1 **fresh 42% → tier 1 51% → tier 2 56% → tier 3 71%**; 147 vs 51 symbols. Money: null. | 🟡 hit rate only | the remaining target is nearer |
| **J6. Tail law** | top 10% of winners = **37–47%** of gross wins in every block and year | ✅ | fat-tailed winners: cutting losers cuts winners |
| **J7. The King moves with price** | **70–84%** of King moves follow spot; same strike next day only 33–60% | ✅ | the map follows price and decays fast, so rescan on touch |
| **J8. 0.25% slippage costs a quarter of the edge** | worse on 27 of 38 and 132 of 209 symbols; −$0.40 to −$0.55 per $1k per fill | ✅ (2026; not seen on 2023–25 liquid names) | the edge is the exact level |
| **J9. Drop plans whose bound is < 25% of the King** | kept beat dropped on only 21 of 38 symbols; the dropped half earned +$0.9 per fill | ⚪ | a coverage filter, not a quality filter |
| **J10. Labelled node classes beyond distance** | within ±1pp of a blank strike at the same distance; one strike-step of distance moves touch odds more than the whole "lift" | ⚪ | touch is proximity |
| **J11. Worn entry level** | TP1 **49–51% vs 45–50%** at ≥ 2/3/4 taps on 17,924 fills; money sign flips by block | ❌ (removed from ranking) | the entry is a wall, so it gets visited anyway |
| **J12. R:R band 0.5–1.5 vs > 3** | TP1 **59–68% vs 19–25%**; green share 61% vs 36%. Money depends on walker and regime: > 3 lost −$4.7 per fill each year 2023–25 and made +$5.3 in 2026 | 🟡 hit rate only | a near target is hit more; the convex pay lives in far targets |
| **J13. Stop width** | stopped 48% (tightest) → 42% (widest); mean R flat | 📊 | the smaller denominator cancels the higher hit rate |
| **J14. Implied move budget** | covered **77.8%** of daily moves; implied > actual on **79.4%** of earnings | 📊 | volatility risk premium |

**Bottom line of the canon:** on an honest minute-by-minute grader, the endorsed book is about flat before costs (−$1.31 / +$0.10 per $1k per fill on the two universes). What survives is a **reading aid**: where price is likely to *touch* (proximity, growth, the King's position), how far it is likely to *travel* (own implied vol), which plays will *hit* more often (P%, near targets, delivered moves), which books to *skip* (thin), and how much a *bad fill* costs. It does not say which way to bet.

---

## What survives, and what is buried

**✅ Kept (display and advisory only)**
- P% as a hit-rate rank (AUC 0.65–0.72), calibrated, never a selector
- R:R band label: 0.5–1.5 hits more; > 3 is a "promise"
- 🌱 growth = touched more (40 of 40 and 205 of 213 symbols), never held better
- Thin-book veto (19 of 25 and 107 of 173 symbols)
- Fill budget: a limit at the level, never chase; 0.25% slippage costs about a quarter of the edge
- The King moves with price 70–84% of the time, so rescan when price touches a level and weight the near King
- Own 1-day implied vol ranks the next-day range about 2× better than the VIX; move-budget cells show it
- σ-ladder stick, a better σ estimate, reach clocks, gap-fill and pivot vol-regime context
- Catalyst tape helps; the catalyst veto was refuted

**⚰️ Buried (do not re-attempt)**
- Direction from the book, in every form: ladders, vanna, projections, votes, routers, tilts, AI reads, swarms, lottos
- "Big node" levels: node share of the King, walls deflecting more than blanks, overnight × wall confluence
- Geometry and constant sweeps, lens combinations, tenor windows, dropping the front expiry, removing the search radius
- Score/grade ranking, EV ranking, winner profiles, loser avoidance, static weight refits, the high-P% subset
- Regime router, tilt as override or size, drift veto, VIX as a gate
- Flow confluence, whale prints, flow agreement, vanna/enrichment as side
- Premium caps, breakeven trails, fixed premium stops, add-on entries
- Options vehicles as a money engine: SAFE −9% and LOTTO −20 to −29% per trade on real prints

> **The one-sentence summary:** dealer positioning is a good **map of where price will visit** and a poor **compass for which way it will go**. Every study that used it as a map passed; every study that used it as a compass failed.
