# garma_agent_video_summary_and_findings.md

- **Title:** Executive Summary & Technical Findings — Garma's Agent Architecture, Obsidian Brain & IB Playbook (`2026-10-17` Session)
- **Scope:** Structured synthesis of all technical findings, quantitative node thresholds, agent architectures, API/broker disclosures, and trade walkthroughs from `garmaagent.mp4` (`51:45`), indexed by video timestamp[cite: 3].
- **Sources Used:** `garmaagent.mp4` / `compressed_output.mp4` (`SRC-32`, recorded Oct 17, 2026)[cite: 3].
- **Prerequisites:** `00_index.md`, `garma_agent_video_transcript.md`
- **Last Updated:** 2026-10-17

---

## 1. Core Philosophy: Why "Find Me an Edge" Fails (`00:53–05:48`)

1. **Agents Require a Pre-Existing Rule-Based System (`01:50–02:27`, `06:48–07:26`):**
   - Prompting an LLM (Claude or ChatGPT) to *"find an edge in the market and make no mistakes"* always fails[cite: 3].
   - A builder must first codify a deterministic, rule-based human trading strategy (such as Garma's **Initial Balance + Liquidity Sweep + Heatseeker Node Deflection** system) and feed it to the agent like teaching a newborn baby from scratch[cite: 3].
2. **Why Options Agents Are Easier to Calibrate Than Futures Agents (`04:30–05:21`):**
   - Garma started with **0DTE Options** rather than Futures (`ES`/`NQ`) because futures stop-loss sizing is notoriously difficult to make purely mechanical: a `40-point` NQ stop yields a high win rate but poor R:R, whereas a `20-point` NQ stop improves R:R but suffers frequent stop-outs[cite: 3].

---

## 2. Knowledge Base Architecture: `polaris_brain` & Obsidian (`03:20–04:29`, `08:31–19:50`)

1. **The `polaris_brain` Repository Structure (`03:20–04:29`, `08:35–16:35`):**
   - Built in VS Code and synced via a central **GitHub repository** to an **Obsidian** vault (`OverLord`), allowing **7 concurrent Claude accounts** to read and update the same shared "memory bank" simultaneously[cite: 3].
   - **Layer 1 — Options Greeks (`04_options_greeks/`):** Dedicated `.md` files for `delta.md`, `gamma.md`, `theta.md`, `vega.md`, `vanna_charm.md`, `options_order_greeks.md`, `capital_framework.md`, and `cheat_sheet.md`[cite: 3].
   - **Layer 2 — Chart & Indicator Primitives:** EMAs, VWAP (`GEX VWAP`, `VEX VWAP`, `Flow VWAP`), Doji candles, Trendlines, Fibonacci retracements, Session Levels (`ASH/ASL`, `LOH/LOL`, `PDH/PDL`, `ONH/ONL`, `WH/WL`, `SWH/SWL`), Order Blocks, Initial Balance (`IB`), and **Timmy Trades** (CME futures aggressive match-event bubbles)[cite: 3].
   - **Layer 3 — Skylit Heatseeker, Atlas & Flow (`skylit/heat_seeker/`):** Modular `.md` files for every node concept (`absolute_value_rule.md`, `air_pockets.md`, `deflection_zones.md`, `node_lifecycle.md`, `node_tap_probability.md`, `rolling_structure.md`, etc.) and every pattern (`rug_setup.md`, `reverse_rug.md`, `beach_ball.md`, `pika_clouds.md`, `whipsaw.md`, `rainbow_road.md`)[cite: 3].
2. **The `ELI5` Retrieval Pattern (`09:39–10:02`):**
   - At the bottom of every `.md` file, Garma includes an **`ELI5` ("Explain Like I'm 5")** summary block so the agent can quickly scan high-level rules without consuming full-file context unless deep lookup is needed[cite: 3].

---

## 3. Quantitative Doctrinal Rules from *"The Garma Playbook: A to Z"* (`37:48–39:53`)

In his October 2026 PDF playbook (**`The Garma Playbook: A to Z`**), Garma codifies exact numerical definitions and 12 hard rules[cite: 3]:

### 3.1 Exact Node Size Classification (`% of King Node`, `38:36`)
- **King Node (`100%`):** Largest absolute exposure on the map (center of gravity; even a huge negative King Node can act as a floor, ceiling, or springboard)[cite: 3].
- **Gatekeeper / Meaningful Node (`20% to 30%` of King Node):** A meaningful node sitting between price and the King Node (or next major level) that acts as a real reaction level (e.g., *"A 700 node shows 30% of the King's size; it's a real level"*)[cite: 3].
- **Air Pocket (`Under 20%` of King Node):** Low dealer exposure where price travels rapidly without much friction (e.g., *"Between 702 and 705 the nodes are all under 20%... that's an air pocket up to 705"*)[cite: 3].
- **Tug of War:** Two nodes of similar size on opposite sides of price, pulling price back and forth[cite: 3].

### 3.2 Garma's 12 Unbreakable Agent Rules (`37:56–38:35`)
1. **`Rule 1 — Charts first, Heatseeker second:`** Structure creates the trade; the node confirms it. A random node in the middle of nowhere is a no-trade[cite: 3].
2. **`Rule 2 — The sweep isn't the trade:`** A liquidity sweep is the setup. Wait for price to clear the stops and then **fail to keep going**[cite: 3].
3. **`Rule 3 — Get 2 of 3:`** `SPX`, `SPY`, and `QQQ`. At least two of the three have to tell the same story before conviction[cite: 3].
4. **`Rule 4 — 1:3 or no trade:`** If the realistic target isn't at least three times the stop (`3:1` R:R), skip it. Don't stretch targets to make the math work[cite: 3].
5. **`Rule 5 — Demand displacement:`** A good reversal moves away from the entry fast. If it creeps back to the entry level and sits there, cut it[cite: 3].
6. **`Rule 6 — Don't chase:`** Act as it taps, not a point later. If the move already left without you, let it go[cite: 3].
7. **`Rule 7 — Trade the edges:`** Only take trades at the outer boundary of the Initial Balance or range, never in the middle[cite: 3].
8. **`Rule 8 — Stale levels die:`** Fresh nodes work best. A tested node is weaker. A delivered node is done for the day[cite: 3].
9. **`Rule 9 — Never short a reverse rug:`** When a positive floor sits under a negative node, dealers buy the dip and fuel the bounce. Don't step in front of that, no matter how extended it looks[cite: 3].
10. **`Rule 10 — Pinned means stay out:`** Surrounded by yellow nodes on both sides = 1:1 at best. Close the terminal[cite: 3].
11. **`Rule 11 — A loss isn't a revenge trade:`** If stopped out cleanly, wait for the next A+ setup[cite: 3].
12. **`Rule 12 — Protect the account first:`** Size so a normal stop doesn't hurt[cite: 3].

### 3.3 The 5 Core Tools in Garma's Stack (`38:28–38:31`)
1. **`Atlas`:** 5-minute and 1-minute candles, Session Levels (`PDH/PDL`, `ONH/ONL`, `ASH/ASL`, `LOH/LOL`), Initial Balance (`30m` NY default), and Timmy Trades[cite: 3].
2. **`Heatseeker`:** GEX/VEX nodes, floors, ceilings, Gatekeepers, and air pockets[cite: 3].
3. **`Trinity`:** `SPX`, `SPY`, and `QQQ` side by side (`2/3` minimum rule)[cite: 3].
4. **`Tempest Sigma Bands`:** Expected range + sigma strip to judge whether a move is already stretched (`> 1.5σ–2σ`)[cite: 3].
5. **`Timmy Trades`:** Plots a diamond on the futures chart when a burst of aggressive same-timestamp exchange match events hits the tape—used as **confirmation that the stop hunt / liquidity sweep just happened** (while remembering Heatseeker = dealer positioning, Timmy Trades = aggressive futures order flow)[cite: 3].

---

## 4. Multi-Agent Fleet Design & The Friday Oct 16, 2026 Trade (`26:40–33:25`)

### 4.1 Garma's Three Specialized Agents
- **`Overlord` (Conservative 0DTE Bot):** Only fires when `2/3` or `3/3` Trinity node confluence + Liquidity Sweep + Fib Retracement align (`97%` 1-min backtest win rate at `1:3` R:R; lower in 1-sec live forward testing)[cite: 3].
- **`Skylord` (Aggressive Co-Pilot Bot):** Continuously scans `SPX`, `SPY`, and `QQQ` left-hand chart history to detect every Double Top and Double Bottom that a human misses while multi-tasking, pairing them with `2/3` node confluence[cite: 3].
- **`IBSniper` (Initial Balance Bot):** Waits until **after `10:00 AM ET`** (30-min NY IB close), then trades IB high/low sweeps or IB break-and-hold setups with Golden Zone (`50%–61.8%`) and Heatseeker node confluence[cite: 3].

### 4.2 Step-by-Step Breakdown of the Oct 16, 2026 "+900% 0DTE" Short Trade (`31:10–33:25`)
1. **Time Filter:** Waited until after **`10:00 AM ET`** (30m NY Initial Balance closed)[cite: 3].
2. **Liquidity Sweep:** `SPX` (`~7755`) and `SPY` (`~772.50`) swept above **Previous Day High (`PDH`)**[cite: 3].
3. **IB Golden Zone:** `SPX`, `SPY`, and `QQQ` all entered their **IB Golden Zone** (`50%–61.8%` retracement)[cite: 3].
4. **Trinity Node Confluence (`2/3` King Nodes + `QQQ` Rug):** `SPX` had its King Node at **`7750`**, `SPY` had its King Node at **`772`**, and `QQQ` had a **Rug Setup at `723`**[cite: 3].
5. **Precision Deflection Zone Trigger (`772.50` SPY):** Because `SPY` nodes have a $\pm\$0.50$ deflection zone, the agent waited while `SPY` overshot `772` slightly and fired the short entry the moment `SPY` re-entered the `50-cent` zone at **`772.50`** (`~10:30–10:35 AM ET`)[cite: 3].
6. **Target 1 (`Trim`) & Runner Management:** Trimmed at **`QQQ 717`**, held runners because `SPX` kept popping new downside nodes (active downside growth), and exited 100% at the **`SPY 761` Floor node** (backed by a positive gamma stack on `SPX`)[cite: 3].

---

## 5. Platform, API, Backtesting & Roadmap Disclosures (`17:14–26:18`, `33:40–51:45`)

1. **92 New Atlas Docs Added Oct 16, 2026 (`05:50–08:25`, `22:16`):** Complete reference pages for every Atlas Drawing Tool (including ICT Order Blocks, Gann, Elliott Wave, Harmonics) and every Indicator (including `Initial Balance`, `Session Levels`, `Killzones`, `Timmy Trades`, `GEX/VEX/Flow VWAP`, `Footprint`, `TPO`, `Volume Profile`, `CVD`), all with a `Copy page` / `Add docs to Claude/Cursor/VS Code` button[cite: 3].
2. **Backtest (1-Min) vs. Live Forward Test (1-Sec) Gap (`26:35–27:05`, `47:50–48:23`):** Historical backtests run on **1-minute replay snapshots** produce inflated win rates (`93%–97%`) because intrabar node/price noise is smoothed out; live forward testing runs on **1-second streaming data**, requiring daily post-market (`16:00 ET`) trade-by-trade review and prompt refinement[cite: 3].
3. **Ticker Universe Finding (`49:25–49:55`):** Agents perform strongly on **Indices (`SPX`, `SPY`, `QQQ`)** and **Mag 7**, but fail to generalize across random individual small/mid-cap tickers due to idiosyncratic breakout behavior[cite: 3].
4. **Forge & Live Broker Integration (`34:45–37:05`, `48:24–49:10`):**
   - **Forge:** Upcoming Skylit in-house sandbox and marketplace for building, backtesting, forward-testing, and selling trading agents[cite: 3].
   - **Live Broker Linking (Closed Beta):** `Webull` cash account option execution is currently in closed beta with `Falcon`; `Tastytrade` (options) and `Rithmic` / `Tradovate` (futures) are coming soon[cite: 3].

---

# kb_vs_garma_video_comparison_analysis.md

- **Title:** Knowledge Base vs. Garma Video (`2026-10-17`) — Contradictions, New Specifications & Outdated Items Analysis
- **Scope:** Systematic audit comparing our 19-file Skylit Knowledge Base (built from `academy-export.json`, `skylit.ai/docs`, and `skylit.ai/changelog` through Oct 14, 2026) against Garma's October 17, 2026 video walkthrough and *"The Garma Playbook: A to Z"*[cite: 1, 2, 3].
- **Sources Used:** Existing 19-file Knowledge Base (`SRC-01` to `SRC-31`) vs. `garmaagent.mp4` (`SRC-32`, Oct 17, 2026)[cite: 1, 2, 3].
- **Prerequisites:** All 19 KB files + `garma_agent_video_transcript.md` + `garma_agent_video_summary_and_findings.md`
- **Last Updated:** 2026-10-17

---

## Executive Overview

Comparing the 19-file Knowledge Base we generated from the Academy and Docs against Garma's live engineering walkthrough reveals:
1. **5 Contradictions / Doctrinal Tensions** where live agent engineering reality or Garma's playbook differs from (or sharpens) the textbook Academy lessons[cite: 1, 3].
2. **11 Brand-New Rules & Quantitative Thresholds** (including the resolution of several `[NOT SPECIFIED]` parameters from `open_questions.md`)[cite: 3].
3. **6 Outdated / Superseded Items** in our earlier files caused by the **92 new documentation pages** and **Nexus Trades API / Forge** updates pushed on October 15–16, 2026[cite: 3].

---

## 1. Contradictions & Doctrinal Tensions (Academy Textbook vs. Garma's Agent Reality)

| # | Topic & Affected KB File | What Our KB Stated (From Academy / Docs) | What Garma's Video (`2026-10-17`) States | How Agents Should Reconcile Both |
| :--- | :--- | :--- | :--- | :--- |
| **C-01** | **SPX vs. SPY Alignment Frequency**<br>(`trinity_and_cross_index.md`, Sec. 1.2) | **Flowseeker Ch. 7, Sec. 4:** States `SPX` and `SPY` track the same S&P 500 index, so their agreement is expected and counts as ~1 piece of evidence[cite: 1]. | **Video (`12:40–12:58`):** Garma warns that on the **Heatseeker GEX node maps**, *"SPY and SPX are most of the time NOT going to line up together, because they're going to be hedged against each other... you might have a Rug going against a Reverse Rug."*[cite: 3] | **Crucial Distinction:** In *directional price & flow*, SPX and SPY move together[cite: 1]; however, in *strike-level dealer GEX structure*, dealers often hedge institutional SPX positions against SPY ETF options, creating opposing node patterns[cite: 3]. Never assume SPX and SPY heatmaps look identical! |
| **C-02** | **Entry Timing on a Liquidity Sweep / Overshoot**<br>(`entries_stops_targets_and_rr.md`, Sec. 1.3 & `chart_structure_fundamentals.md`, Sec. 5.1) | **Heatseeker Ch. 6, Sec. 3:** *"We enter at the direct tap of major nodes. Not before. Not after."*[cite: 1] | **Video (`32:22–32:40`, `37:58` Rule 2):** *"The sweep isn't the trade: Wait for price to clear the stops and then fail to keep going."* On the Oct 16 `SPY 772` King Node trade, when price overshot `772`, the agent **waited to fire until price dropped back inside the $\pm\$0.50$ deflection zone (`772.50`)**[cite: 3]. | For automated agents, **re-entry into the $\pm\$0.50$ ($\pm\$5$ SPX) deflection zone after an overshoot/sweep** prevents getting run over by runaway momentum while still capturing the node deflection[cite: 3]. |
| **C-03** | **Initial Balance (`IB`) Breakout vs. "We Do NOT Trade Breakouts"**<br>(`setup_catalog_beach_ball_and_pika_cloud.md`, Sec. 1.3 & `risk_sizing_and_discipline.md`) | **Heatseeker Ch. 5, Sec. 2.IV & Ch. 7, Sec. 2:** *"We do NOT trade breakouts... Continuation chasing = not condoned. We fade extremes."*[cite: 1] | **Video (`35:50–36:25`):** Garma's **`IBSniper`** agent in Forge explicitly includes an **`IBSniper Break Long`** play: a 1-minute close above the IB High (`7100`) after holding a Golden Zone + `7095` node floor underneath, targeting the `0.5x` IB extension (`7120`)[cite: 3]. | While Heatseeker *nodes* are never traded as blind breakout triggers[cite: 1], an **Initial Balance (`IB`) range break** *is* a valid play in Garma's IB system **if and only if** price first bounced off an underlying Heatseeker node floor + Golden Zone and then closed a qualified 1m candle above IB High with `>= 3:1` R:R[cite: 3]. |
| **C-04** | **Role of Flowseeker in 0DTE vs. Swing Trading**<br>(`flowseeker_options_flow.md`, Sec. 1 & `charts_first_workflow.md`) | **Flowseeker Ch. 1–10:** Treats Flowseeker options flow as Layer 3 across all timeframes (including 2-DTE and 0DTE index examples)[cite: 1]. | **Video (`23:09–23:20` & `38:28`):** Garma states that for **0DTE index scalping**, options flow (`Flowseeker`) is *"not really needed"* (he uses **Timmy Trades** aggressive futures match events instead for 0DTE stop-hunt confirmation), whereas **Flowseeker is primarily powerful for Swing trading**[cite: 3]. | Weight **Timmy Trades** + **Session Level Sweeps** + **Heatseeker GEX** for 0DTE index agents, and weight **Flowseeker + Next-Day OI + VEX** for multi-day Swing agents[cite: 1, 3]. |
| **C-05** | **Single-Stock Generalization vs. Index/Mag-7 Specialization**<br>(`platform_reference_atlas_heatseeker_talon.md`) | **Academy & Docs:** Present Heatseeker and Flowseeker rules as universal across all optionable equities (using `AAPL`, `ACN`, `LLY`, `NFLX`, `MSFT`, `NVDA` alongside indices)[cite: 1]. | **Video (`49:25–49:55`):** Garma reveals that in live agent testing, his agents were consistently profitable on **Indices (`SPX`, `SPY`, `QQQ`)** and **Mag 7**, but **could not find a profitable automated edge on smaller individual tickers** due to idiosyncratic breakout behavior[cite: 3]. | Restrict autonomous execution agents by default to `{SPX, SPXW, SPY, QQQ}` and `Mag 7` (`AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA`), requiring ticker-specific parameter tuning for non-Mag-7 single stocks[cite: 3]. |

---

## 2. Brand-New Rules & Resolved `[NOT SPECIFIED]` Parameters (`open_questions.md` Updates)

The video directly resolves **4 major `[NOT SPECIFIED]` items** from `open_questions.md` and introduces **7 new operational rules**:

### 2.1 Resolved `[NOT SPECIFIED]` Items from `open_questions.md`
1. **Resolves `OQ-06` (`Gatekeeper_Relative_Size_Bounds`):**
   - *Previous KB Status:* `[NOT SPECIFIED]` (We estimated `15%–60%` of King Node).
   - *New `[DOCTRINE: Garma Playbook p. 6, 38:36]` Value:* **`20% to 30%` of the King Node's size** defines a meaningful secondary / Gatekeeper node[cite: 3].
2. **Resolves `OQ-07` (`Air_Pocket_Max_Threshold`):**
   - *Previous KB Status:* `[NOT SPECIFIED]` (We estimated `< 15%` of King Node).
   - *New `[DOCTRINE: Garma Playbook p. 6, 38:36]` Value:* **`Under 20%` (`< 20%`) of the King Node's size** across adjacent strikes defines an **Air Pocket**[cite: 3].
3. **Resolves `OQ-15` (`Early_Session_Opening_Wait_Cutoff` for IB System):**
   - *Previous KB Status:* `[NOT SPECIFIED]` (We estimated waiting `10–15 minutes` after the `09:30 ET` open based on Ch. 12 Case Study #1)[cite: 1].
   - *New `[DOCTRINE: Garma Video 31:53–32:04]` Value:* For the 30-minute New York Initial Balance (`IB`) agent, **no trades are allowed before `10:00 AM ET`** (hard lock until the `09:30–10:00 ET` IB window closes)[cite: 3].
4. **Resolves `Discrepancy 1` (`Minimum Acceptable R:R: 2:1 vs. 3:1`):**
   - *Previous KB Status:* Noted conflict between Heatseeker Ch. 6 (`2:1 acceptable`) and Flowseeker Ch. 10 (`3:1 minimum`)[cite: 1].
   - *New `[DOCTRINE: Garma Playbook p. 4 Rule 4, 38:00]` Value:* Strictly enforces **`1:3 or no trade`** (`Reward >= 3 * Risk`, or `~3.5R` in `IBSniper`), confirming `3:1` as the hard agent gate[cite: 3].

### 2.2 Seven Brand-New Concepts & Rules to Add to the Knowledge Base
1. **Rule 5 — Demand Displacement (Time-Stop / Stagnation Exit, `38:00`):**
   - *"A good reversal moves away from the entry fast. If it creeps back to the entry level and sits there, cut it."* Agents should implement a **time-displacement stop** that exits early if price fails to displace away from the entry node within $N$ bars[cite: 3].
2. **Rule 9 — Never Short a Reverse Rug (`38:05`):**
   - Explicit directional prohibition: when a positive gamma floor sits directly beneath a negative gamma node, never short into it regardless of how overextended the chart looks[cite: 3].
3. **"The Holy Trinity" (`3/3` King Node + Air Pocket Confluence = `~95%` Deflection Heuristic, `13:18–13:51`):**
   - Legacy Feb/March 2026 Skylit rule: when `SPX`, `SPY`, and `QQQ` simultaneously hit their **King Nodes** with aligned air pockets, deflection probability reaches a heuristic **~95%**[cite: 3].
4. **Specific Power Setup: `Double Bottom + Reverse Rug` (`10:48–11:15`):**
   - Codified in `reverse_rug.md` as a named high-conviction multiplier (`Chart Double Bottom + Heatseeker Reverse Rug`)[cite: 3].
5. **`Timmy Trades` Indicator (CME Futures Aggressive Match Events, `20:01–20:30`, `38:30`):**
   - Marks the largest aggressive single-timestamp match events on CME futures (`ES`, `NQ`, `MES`, `MNQ`, `CL`, `GC`, `RTY`, `YM`, `SI`), sized by lots and colored by side (up-candle color = buyer aggressor, down-candle color = seller aggressor, grey = undetermined; merges same-side bursts on one bar; intraday `1m` to `4h` only)[cite: 3]. Used by Garma to confirm that a stop hunt / liquidity sweep at a Session Level or IB boundary has completed[cite: 3].
6. **1-Minute Backtest vs. 1-Second Live Forward-Test Divergence (`26:35–27:05`, `47:50–48:23`):**
   - Critical agent engineering rule: 1-minute bar backtests overstate win rates (`93%–97%`) relative to 1-second streaming execution (`~80%`) because intrabar node wiggles and tick overshoots only appear on the 1-second feed[cite: 3].
7. **`ELI5` Summary Footer + Obsidian Graph Linking (`09:39–10:02`, `17:56–19:48`):**
   - Structural design pattern for agent `.md` files: include an `ELI5` block at the end of every file for low-token retrieval and link related concepts via Obsidian/GitHub markdown links[cite: 3].

---

## 3. Outdated / Superseded Items in Our Previous Knowledge Base Files

Because our initial browse of `skylit.ai/docs` and `skylit.ai/changelog` captured updates through **October 14, 2026**, the video (recorded **October 17, 2026**) revealed **6 areas where Skylit pushed major updates on October 15–16, 2026**:

| # | Outdated / Incomplete Item in Earlier KB Files | What Changed on Oct 15–17, 2026 (Shown in Video) | Files to Update |
| :--- | :--- | :--- | :--- |
| **U-01** | **Atlas Documentation Scope:** Our KB listed only the main Atlas Field Guide and the Oct 13–14 changelog entries (`Initial Balance`, `Killzones`, `Session Levels`). | On **Oct 16, 2026**, Garma published **92 new documentation pages** under `skylit.ai/docs/atlas/*` covering **every Drawing Tool** (`Essentials`, `Lines & Channels`, `Fibonacci`, `Gann`, `Cycles & Regression`, `Patterns & Harmonics`, `Elliott Wave`, `Shapes`, `Measure`, `Smart Money and ICT` [Order Blocks, FVG, BOS, CHoCH, Breaker/Mitigation blocks, BPR, Volume Imbalance, NWOG/NDOG,ote, SMT], `Order Flow`, `Futures & Sessions`, `Projection`, `Annotate`, `Drawing Presets`) and **every Indicator** (`EMA`, `SMA`, `HMA`, `Bollinger Bands`, `RSI + Bollinger`, `VWAP`, `GEX VWAP`, `VEX VWAP`, `Flow VWAP`, `Footprint`, `Timmy Trades`, `Dealer Levels`, `Dark Pool`, `Session Levels`, `Volume Profile`, `TPO`, `Killzones`, `Initial Balance`, `CPR`, `RSI`, `Flow`, `Net Premium`, `Volume`, `CVD`, `Stochastic`, `MACD`)[cite: 3]. | `00_index.md`, `platform_reference_atlas_heatseeker_talon.md`, `chart_structure_fundamentals.md` |
| **U-02** | **Session Levels Indicator Details:** Our KB noted only that IB was split out of `Session Levels` on Oct 13, 2026 (`OQ-15`). | The new `Session Levels` doc page (`07:28–07:50`, updated Oct 16, 2026) explicitly defines all **11 level families and their exact default ET/local time windows**: `Asia` (`ASH/ASL`, `20:00–02:00 ET`), `London` (`LOH/LOL`, `08:00–16:30 London`), `Prev Day` (`PDH/PDL/PDC/PDO`, RTH `09:30–16:00 ET` or ETH), `Week` (`WH/WL`), `Prev Week` (`PWH/PWL`), `Month` (`MH/ML`), `Prev Month` (`PMH/PML`), `Opens` (`MO` `00:00 ET`, `WO` Sun `18:00 ET`, `MO` 1st session), `Swing` (`SWH/SWL`, highest/lowest over last `N` completed sessions, default `20`), `Premarket` (`PMH/PML`, `04:00–09:30 ET`), and `Overnight` (`ONH/ONL`, `18:00 ET` prior evening to `09:30 ET`)[cite: 3]. | `session_timing.md`, `chart_structure_fundamentals.md`, `open_questions.md` |
| **U-03** | **Initial Balance (`IB`) Indicator Settings:** Our KB noted `15/30/60m` for `NY/London/Asia`, but flagged retracement ratios and session times as `[NOT SPECIFIED]`. | The new `Initial Balance` doc page (`06:33–06:48`, updated Oct 16, 2026) specifies: Default = `30m`; NY (`09:30 ET`), London (`08:00 London`), Asia (`09:00 Tokyo`); `50% line` (`On` by default); `Retracements` (`On` by default: **`38.2%`, `61.8%` [golden zone shaded], `78.6%`**, plus up to 3 custom levels); `Fib direction` (`Auto` locks when IB window closes based on whether close is in upper or lower half); `Extension ladder` (`Off` by default, up to `10` steps at `0.5x` or `1.0x` IB range multiples)[cite: 3]. | `chart_structure_fundamentals.md`, `session_timing.md`, `open_questions.md` |
| **U-04** | **Skylit API Key Architecture & Endpoints:** Our KB summarized the REST API and Nexus Trades routes at a high level. | The video (`17:14–17:28`, `33:44–34:15`) reveals the exact two-API architecture and rate/window caps:<br>1. **Market Data API** (`https://api.skylit.ai`, key `sk_live_...`, read-only): `/api/v1/history` OHLCV caps (`1-min`: 30 trading days/req; `5-min`: 60 days; `1-hour`: 730 days; `1-day`: 3650 days). **Does NOT serve CME futures (`ES`/`NQ`) OHLCV** due to CME licensing[cite: 3].<br>2. **Nexus Trades API** (`https://app.skylit.ai/api/nexus/v1`, key `sk_alg_...`): Full paper order routing (`/trading/capabilities`, `/trades`, `/trades/{id}/trim`, `/trades/{id}/exit`, `/trading/wallets/{book}`, `/orders/working`, `/trading/accounts/{id}/close`) with `"account": "agent"` support[cite: 3]. | `platform_reference_atlas_heatseeker_talon.md` |
| **U-05** | **Live Broker Execution Status:** Our KB treated Nexus strictly as a paper-trading simulator. | While Nexus Trades API itself remains simulated, Skylit is now in **closed beta testing for live broker mirroring** (`Webull` Individual Cash connected to `Falcon` for long calls/puts, with `Tastytrade` and `Rithmic`/`Tradovate` coming soon)[cite: 3]. | `platform_reference_atlas_heatseeker_talon.md` |
| **U-06** | **Upcoming Products (`Arc` & `Aethos` vs. `Forge`):** Our KB listed `Arc` and `Aethos` from the public footer. | The video introduces **`Forge`** (`35:50–36:27`, `48:24–49:10`)—Skylit's integrated agent-building sandbox, backtester/forward-tester, live signal feed (`IBSniper Break Long`), and agent marketplace[cite: 3]. | `00_index.md`, `glossary.md`, `platform_reference_atlas_heatseeker_talon.md` |

---

## Agent Checklist (`kb_vs_garma_video_comparison_analysis.md`)

1. **Update Config Constants Immediately:** Set `Gatekeeper_Min_King_Pct = 0.20`, `Gatekeeper_Max_King_Pct = 0.30`, `Air_Pocket_Max_King_Pct = 0.20`, `IB_Earliest_Entry_Time_ET = "10:00:00"`, and `Strict_Min_RR = 3.0`[cite: 3].
2. **Add `Demand_Displacement` & `Never_Short_Reverse_Rug` Interlocks:** Enforce an early exit if price creeps back to the entry node and stagnates (Rule 5), and hard-block any short signal when a positive floor sits directly beneath a negative node (Rule 9)[cite: 3].
3. **Separate SPX and SPY Heatmap Reads:** Do not expect `SPX` and `SPY` GEX node patterns to mirror each other strike-for-strike (`C-01`), and require `>= 2 of 3` across `{SPX, SPY, QQQ}`[cite: 3].
