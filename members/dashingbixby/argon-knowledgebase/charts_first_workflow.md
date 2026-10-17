# charts_first_workflow.md

- **Title:** The Charts First Workflow — End-to-End Decision State Machine
- **Scope:** Complete sequential workflow from opening a blank chart to forming a structural thesis, validating on Heatseeker and Trinity, weighing Flowseeker/Dark Pool/Tempest evidence, filling out the 7-field Playbook, and executing or abstaining.
- **Sources Used:** SRC-03 (Academy Ch. 1)[cite: 1], SRC-06 (Academy Ch. 4)[cite: 1], SRC-08 (Academy Ch. 6)[cite: 1], SRC-12 (Academy Ch. 10)[cite: 1], SRC-13 (Academy Ch. 11)[cite: 1], SRC-14 (Academy Ch. 12)[cite: 1], SRC-16 (Flowseeker Ch. 1)[cite: 1], SRC-25 (Flowseeker Ch. 10)[cite: 1].
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`
- **Last Updated:** 2026-10-17

---

## 1. The Extended Doctor & X-Ray Analogy

### 1.1 Definition
- **Plain sentence:** A Skylit trader works like a doctor: the price chart is the physical examination that forms the initial hypothesis, the Heatseeker map is the X-ray that confirms or challenges the internal structure, and options flow is what the patient tells you—useful evidence that must be weighed against both `[DOCTRINE: Academy Ch. 1, Sec. 3; Flowseeker Ch. 1, Sec. 2]`[cite: 1].
- **Precise version:** An agent must evaluate market layers in a strict, non-commutative sequence (`Layer 1: Chart` $\rightarrow$ `Layer 2: Heatseeker & Trinity Map` $\rightarrow$ `Layer 3: Flow & Companion Context` $\rightarrow$ `Layer 4: Playbook & R:R Gate`). Reversing the order (e.g., starting with a big options print or a Heatseeker node without a chart location) is a doctrinal violation `[DOCTRINE: Academy Ch. 1, Sec. 5; Flowseeker Ch. 1, Sec. 3 & 7]`[cite: 1].

| Step | Layer | Medical Analogy | Core Question Answered | Role in Trade Decision |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Price Chart** (`chart_structure_fundamentals.md`) | Physical Examination & Medical History | *Where is price in the structure—at a fresh extreme, or in the middle?* | **Creates the initial thesis** (and defines entry trigger & invalidation level) `[DOCTRINE: Academy Ch. 1, Sec. 1 & 3; Flowseeker Ch. 8, Sec. 4]`[cite: 1]. |
| **2** | **Heatseeker & Trinity** (`heatseeker_node_core_attributes.md`, `trinity_and_cross_index.md`) | The X-Ray | *Does dealer positioning across strikes and across SPX/SPY/QQQ confirm or challenge that thesis?* | **Confirms or denies the thesis** (and defines how price will behave and deliver node-to-node) `[DOCTRINE: Academy Ch. 1, Sec. 3; Ch. 10, Sec. 6]`[cite: 1]. |
| **3** | **Flowseeker & Dark Pool** (`flowseeker_options_flow.md`, `dark_pool.md`) | What the Patient Tells You | *What are traders actually doing right now—and does it fit, or challenge, the first two layers?* | **Supporting or conflicting evidence**—never the standalone trade trigger `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 2 & 3; Ch. 10, Sec. 2]`[cite: 1]. |
| **4** | **Playbook & Execution Gate** (`entries_stops_targets_and_rr.md`) | Diagnosis & Treatment Plan | *Is the reward at least 3x the risk, what weakens the read, and has price tapped the node?* | **Governs execution, position size, or "No Trade"** `[DOCTRINE: Academy Ch. 6, Sec. 9; Flowseeker Ch. 10, Sec. 2 & 3]`[cite: 1]. |

---

## 2. Master 9-Step Real-Time Workflow

This procedure synthesizes **Heatseeker Chapter 11 ("The Real-Time Thought Process", Steps 1–9)**[cite: 1], **Heatseeker Chapter 4 ("Your Process", Steps 1–5)**[cite: 1], and **Flowseeker Chapter 10 ("A Playbook Organises a Hypothesis")**[cite: 1] into one unified state machine.

### Inputs
- Multi-timeframe OHLCV chart data (`1D`, `1H`, `5m`) for the target instrument (and `SPX`, `SPY`, `QQQ` if trading indices/futures).
- Live Heatseeker GEX and VEX matrices (with Velocity Mode `% change` and tap counts).
- Live Trinity maps (`SPXW`, `SPY`, `QQQ`).
- Optional: Flowseeker Live Feed / Scanner / Drilldown / Atlas Flow Pane, Dark Pool levels, and Tempest `Expected Move` / `SVX`.

### Procedure (Step-by-Step State Machine)

#### Step 1 — Start With Price Action (`[DOCTRINE: Academy Ch. 11, Sec. 3, Step 1]`)[cite: 1]
1. Query the `1D`, `1H`, and `5m` charts.
2. Determine the structural state (`TREND_UP`, `TREND_DOWN`, or `RANGE`).
3. **Gate Check:** *"If price doesn't make sense, nothing else matters."* `[DOCTRINE: Academy Ch. 11, Sec. 3, Step 1]`[cite: 1]. If price action is erratic/choppy with no identifiable swing structure, output `PASS_NO_CHART_CLARITY`.

#### Step 2 — Anchor to Chart Structure & Form Initial Thesis (`[DOCTRINE: Academy Ch. 1, Sec. 5; Ch. 11, Sec. 3, Step 2]`)[cite: 1]
1. Locate current spot price $P_{\text{spot}}$ relative to higher-timeframe (`1D`/`1H`) Support/Resistance zones, Range High ($R_{\text{high}}$), Range Low ($R_{\text{low}}$), and Range Midpoint ($R_{\text{mid}}$).
2. **Gate Check (The Midpoint Filter):** Is $P_{\text{spot}}$ sitting in the middle of the range (`AT_MIDPOINT`)?
   - If **Yes** $\rightarrow$ **ABORT IMMEDIATELY** (`NO_TRADE_MIDPOINT`). Do not check Heatseeker or Flowseeker to justify a midpoint trade `[DOCTRINE: Academy Ch. 1, Sec. 4 & 5; Flowseeker Ch. 1, Sec. 3]`[cite: 1].
3. **Form Structural Thesis:**
   - If $P_{\text{spot}}$ is approaching or testing a fresh `1D`/`1H` support, range low, double bottom, or bullish S/R flip $\rightarrow$ `THESIS = BULLISH_REVERSAL` `[DOCTRINE: Academy Ch. 1, Sec. 4 & 5]`[cite: 1].
   - If $P_{\text{spot}}$ is approaching or testing a fresh `1D`/`1H` resistance, range high, double top, or bearish S/R flip $\rightarrow$ `THESIS = BEARISH_REJECTION` `[DOCTRINE: Academy Ch. 1, Sec. 1 & 4]`[cite: 1].

#### Step 3 — Open Heatseeker & Locate Key Structural Nodes (`[DOCTRINE: Academy Ch. 3, Sec. 9; Ch. 11, Sec. 3, Step 3]`)[cite: 1]
1. Select the expiration scope matching the trade horizon (`Current` for 0DTE/scalps; `Current + Next N` / `Entry` column for swings) `[DOCTRINE: Academy Atlas Sec. 5 & 9; Changelog Oct 13, 2026]`[cite: 1].
2. Execute the 6-step map scan `[DOCTRINE: Academy Ch. 3, Sec. 9]`[cite: 1]:
   - Locate **Spot Price**.
   - Identify the **King Node** (largest absolute exposure value).
   - Identify the **Floor** (largest node below spot).
   - Identify the **Ceiling** (largest node above spot).
   - Identify **Air Pockets** (low-exposure pathways) and **Gatekeepers** (intermediary checkpoints).
3. **Confluence Check:** Does a major node (Floor, Ceiling, King Node, Rug, or Reverse Rug) align with the chart level from Step 2?
   - If **No** (e.g., chart shows a floor, but Heatseeker shows only an open air pocket below with no floor node) $\rightarrow$ **ABORT** (`NO_HEATSEEKER_CONFLUENCE`) `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 2, Q5]`[cite: 1].

#### Step 4 — Evaluate Node Quality & Lifecycle (`[DOCTRINE: Academy Ch. 9, Sec. 2 & 5; Ch. 11, Sec. 3, Step 4]`)[cite: 1]
1. Check the node's **Magnitude** (is it a dominant anchor on the board, or a tiny minor node?) `[DOCTRINE: Academy Ch. 2, Sec. 5]`[cite: 1].
2. Check the node's **Tap Count / Lifecycle Stage** (`fresh` = 1st tap ~80%, `tested` = 2nd tap ~66%, `delivered` = 3rd tap ~33%, `spent`) `[DOCTRINE: Academy Ch. 6, Sec. 6; Talon Prompt Guide, Sec. 4]`[cite: 1].
   - If the node is on its **3rd+ tap** or already **delivered/spent** $\rightarrow$ downgrade or abort (`NODE_EXHAUSTED`) `[DOCTRINE: Academy Ch. 6, Sec. 6 & 12; Ch. 9, Sec. 2]`[cite: 1].
3. Check **Rate of Change (Velocity Mode)**: Is the node holding/growing (`Real Node`), or is it rapidly unwinding/melting while an opposing floor/ceiling rolls against our thesis? `[DOCTRINE: Academy Ch. 7, Sec. 3; Ch. 9, Sec. 5]`[cite: 1].

#### Step 5 — Anticipate the Reaction Type (`[DOCTRINE: Academy Ch. 6, Sec. 8; Ch. 11, Sec. 3, Step 5]`)[cite: 1]
1. Check the **Sign (Gamma Regime)** of the target node:
   - If **Positive Gamma (`+GEX` / Yellow / Pika):** Expect a cleaner **direct tap deflection** within the standard Deflection Zone ($\pm\$0.50$ SPY/QQQ; $\pm\$5.00$ SPX) `[DOCTRINE: Academy Ch. 6, Sec. 3 & 8; Ch. 11, Sec. 3, Step 5]`[cite: 1].
   - If **Negative Gamma (`-GEX` / Purple / Barney):** *"It is safer to assume overshoot first."* `[DOCTRINE: Academy Ch. 6, Sec. 8]`[cite: 1]. Prepare for a **Beach Ball overshoot** through the strike, followed by a stall and rejection wick before entering `[DOCTRINE: Academy Ch. 6, Sec. 5 & 8, Q9 & Q10]`[cite: 1].

#### Step 6 — Check the Broader Gamma Regime & Day Type (`[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 11, Sec. 3, Step 6]`)[cite: 1]
1. Zoom out across the strikes surrounding spot:
   - **Positive Gamma Pocket (`Type 1: Range / Choppy Day`):** Expect slower rotations, pinning, and theta burn; plan to take profits quickly at the nearest structural node `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 11, Sec. 3, Step 6]`[cite: 1].
   - **Negative Gamma Zone (`Type 2: Trend Day` or `Type 3: Whipsaw`):** Expect fast, expanding candles and larger swings `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 11, Sec. 3, Step 6]`[cite: 1].
   - **Undefined Chaos (`Rainbow Road`):** If multiple positive/negative nodes are scattered with no clear Floor or Ceiling $\rightarrow$ **ABORT** (`NO_TRADE_RAINBOW_ROAD`) `[DOCTRINE: Academy Ch. 5, Sec. 3.VI]`[cite: 1].

#### Step 7 — Check the Delivery Path to the Target (`[DOCTRINE: Academy Ch. 8, Sec. 3; Ch. 9, Sec. 6; Ch. 11, Sec. 3, Step 7]`)[cite: 1]
1. Inspect the strikes between the entry node and the proposed target node:
   - **Open Air Pocket:** Clear, low-friction pathway for rapid delivery to the target node `[DOCTRINE: Academy Ch. 8, Sec. 3; Ch. 11, Sec. 3, Step 7]`[cite: 1].
   - **Gatekeeper or Pika Cloud in the Path:** Friction exists between entry and target `[DOCTRINE: Academy Ch. 5, Sec. 4; Ch. 10, Sec. 6; Ch. 11, Sec. 3, Step 7]`[cite: 1]. Either set the primary take-profit at the Gatekeeper/Pika Cloud or verify that `3:1 R:R` is still achieved *before* hitting that friction `[INFERENCE from Academy Ch. 10, Sec. 6]`[cite: 1].

#### Step 8 — Confirm Cross-Index Alignment (Trinity Check) & Weigh Flow Evidence (`[DOCTRINE: Academy Ch. 10, Sec. 6; Ch. 11, Sec. 3, Step 8; Flowseeker Ch. 7 & 9]`)[cite: 1]
1. **Trinity Check (`SPX`, `SPY`, `QQQ`):**
   - **3 of 3 Aligned:** Full confluence $\rightarrow$ eligible for standard position size `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5]`[cite: 1].
   - **2 of 3 Aligned (1 lags or conflicts):** Partial confluence (bare minimum) $\rightarrow$ **reduce position size** `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5]`[cite: 1]. *(Remember: SPX and SPY track the same index and count as ~1 piece of evidence; if QQQ opposes SPX+SPY at a ceiling/floor, treat with high caution)* `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 4 & 6]`[cite: 1].
   - **1 of 3 or 0 of 3 Aligned (Total Misalignment / Divergence):** **ABORT** (`NO_TRADE_TRINITY_DIVERGENCE`) `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5]`[cite: 1].
2. **Weigh Flowseeker & Dark Pool Evidence (Layer 3):**
   - Check if a Dark Pool level sits at the node (adds structural weight) `[DOCTRINE: Academy Dark Pool Course, Sec. 5]`[cite: 1].
   - Check Flowseeker / Atlas Flow Pane as price reaches the level:
     - **Supporting Flow:** Directional flow aligns with the deflection (e.g., ask-side call buying at a Floor, or call flow fading / put buying at a Ceiling) `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 5; Ch. 10, Sec. 2]`[cite: 1].
     - **Conflicting Flow:** Heavy opposing flow prints right at the level (e.g., $4M of 2-DTE ask-side calls printing into a Ceiling, or heavy ask-side puts at a Floor) `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 5; Ch. 10, Sec. 2]`[cite: 1]. Do **not** ignore it; classify as a **Conflict**, list the 5 explanations, and **wait** for price acceptance/rejection or reduce size `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 3 & 4]`[cite: 1].

#### Step 9 — Complete Playbook, Verify 3:1 R:R & Execute Decision (`[DOCTRINE: Academy Ch. 6, Sec. 9 & 10; Ch. 11, Sec. 3, Step 9; Flowseeker Ch. 10, Sec. 2 & 3]`)[cite: 1]
1. Populate all 7 fields of the **Skylit Playbook**:
   - `Question`, `Location`, `Map`, `Flow`, `Target`, `Weakens If`, `Decision` `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 2]`[cite: 1].
2. **Calculate Reward-to-Risk Ratio:**
   - $\text{Invalidation Stop } (P_{\text{stop}}) =$ one node beyond the entry node (break and hold) or beyond the chart swing structure `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10]`[cite: 1].
   - $\text{Target } (P_{\text{target}}) =$ next structural node/level before major friction `[DOCTRINE: Academy Ch. 6, Sec. 11; Flowseeker Ch. 10, Sec. 2]`[cite: 1].
   - $\text{R:R} = \frac{\vert{}P_{\text{target}} - P_{\text{entry}}\vert{}}{\vert{}P_{\text{entry}} - P_{\text{stop}}\vert{}}$.
   - **Gate Check:**
     - If $\text{R:R} \ge 3.0 \implies$ **Qualifies as A+ Standard** `[DOCTRINE: Academy Ch. 6, Sec. 9; Flowseeker Ch. 10, Sec. 3]`[cite: 1].
     - If $2.0 \le \text{R:R} < 3.0 \implies$ *"Acceptable but not ideal"* in Heatseeker Ch. 6 `[DOCTRINE: Academy Ch. 6, Sec. 9]`[cite: 1], whereas Flowseeker Ch. 10 and Heatseeker Ch. 3 state *"Skylit doctrine requires a 3:1 minimum R:R for any trade to qualify"* and at ~2:1 the trader should **wait for a pullback to a better entry price or pass** `[DOCTRINE: Academy Ch. 3, Sec. 6; Flowseeker Ch. 10, Sec. 3 & 6]`[cite: 1]. *(Both rules preserved; strict agent default = require $\ge 3.0$ or wait for pullback.)*
     - If $\text{R:R} < 2.0 \implies$ **ABORT / WAIT FOR BETTER ENTRY** `[DOCTRINE: Academy Ch. 6, Sec. 9]`[cite: 1].
3. **Execute:** Enter upon the **direct tap** of the node (or post-overshoot stall if `-GEX`) `[DOCTRINE: Academy Ch. 6, Sec. 3 & 8]`[cite: 1].

---

## 3. Edge Cases in the Workflow
- **Price Never Reaches the Node:** *"If the setup doesn't materialize or price never reaches the node, there is no trade."* `[DOCTRINE: Academy Ch. 6, Sec. 12]`[cite: 1]. Never front-run a node early `[DOCTRINE: Academy Ch. 6, Sec. 13 & Quiz Q8]`[cite: 1].
- **Map Reshuffles While Waiting:** If the target node shrinks/unwinds or floors/ceilings roll against the thesis before price taps the level, re-run Step 3 and Step 4 immediately and cancel the pending setup if invalidated `[DOCTRINE: Academy Ch. 7, Sec. 3; Flowseeker Ch. 10, Sec. 4]`[cite: 1].

---

## 4. Worked Example (`[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6 Case Study]`)

1. **Question:** Is SPY holding its week-long range low for a rotation back to the ceiling?[cite: 1]
2. **Location (Step 1–2):** SPY has ranged between `578` and `590` for a week. Price pulls back toward the `578` range low (Extreme, not midpoint) `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`[cite: 1].
3. **Map (Step 3–7):** Large Positive Gamma Floor node at `578`; King Node Ceiling at `590`; Positive Gamma regime `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`[cite: 1].
4. **Flow & Trinity (Step 8):** Bull call spread (`590/600`) with rising open interest supports upside `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`[cite: 1]. (If QQQ diverges at its ceiling as in Ch. 7, Trinity = `2/3`, requiring reduced size `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 6]`[cite: 1].)
5. **Target & Weakens If (Step 9):** Target = `590` (King Node ceiling). Weakens if price accepts below `578` $\rightarrow$ hard invalidation stop at `577` `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`[cite: 1].
6. **R:R Gate Check:**
   - **Check 1 at Spot = `581.40`:** $\text{Reward} = 590 - 581.40 = 8.60$; $\text{Risk} = 581.40 - 577.00 = 4.40$; $\text{R:R} = 8.60 / 4.40 = 1.95:1$ (about `2:1`). Below the `3:1` minimum $\rightarrow$ **Decision: WAIT** `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`[cite: 1].
   - **Check 2 when Price Pulls Back to `579.00`:** $\text{Reward} = 590 - 579 = 11.00$; $\text{Risk} = 579 - 577 = 2.00$; $\text{R:R} = 11 / 2 = 5.5:1$ ($\ge 3:1$) $\rightarrow$ **Decision: EXECUTE LONG** `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`[cite: 1].

---

## 5. What It Is NOT
- The Charts First Workflow is **not** a screen-scanner where an agent scans Flowseeker for `$1M+` sweeps and then draws a line on a chart to justify buying `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 7; Ch. 10, Sec. 2, Q3]`[cite: 1].

---

## 6. Related Concepts
- See `setup_catalog_rug_and_reverse_rug.md`, `setup_catalog_beach_ball_and_pika_cloud.md`, and `setup_catalog_fade_gatekeeper_and_vex_swing.md` for specific pattern triggers inside Step 3–5.
- See `entries_stops_targets_and_rr.md` for exact entry, stop, and target calculations.
- See `no_trade_conditions.md` for all boolean abort gates.

---

## Agent Checklist (`charts_first_workflow.md`)

1. **Enforce Sequential Gate Order:** Confirm `Step 1 (Price)` and `Step 2 (Chart Extreme)` return `TRUE` before querying `Step 3 (Heatseeker)`[cite: 1].
2. **Require All 7 Playbook Fields:** Refuse to emit an `EXECUTE` signal if any of the 7 Playbook fields (`Question`, `Location`, `Map`, `Flow`, `Target`, `Weakens If`, `Decision`) is null or empty[cite: 1].
3. **Verify `Flow` Is Not the Trigger:** Confirm that the entry trigger is a structural price event (direct tap of node/level or post-overshoot stall), never a standalone options print[cite: 1].
4. **Enforce R:R $\ge 3.0$ Gate:** If $\text{R:R} < 3.0$ at current spot, emit `WAIT_FOR_BETTER_ENTRY` with the exact limit price required to achieve $3.0 \times \text{Risk}$[cite: 1].