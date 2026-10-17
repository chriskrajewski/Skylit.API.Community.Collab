# setup_catalog_rug_and_reverse_rug.md

- **Title:** Setup Catalog — Rug Setup (Bearish) & Reverse Rug Setup (Bullish)
- **Scope:** Zero-assumption definitions, required inputs, step-by-step detection procedures, execution triggers, stop-loss and target calculation, no-trade conditions, edge cases, and worked examples for the Rug Setup and Reverse Rug Setup.
- **Sources Used:** SRC-06 (Academy Ch. 4)[cite: 1], SRC-07 (Academy Ch. 5, Sec. 2–5 & Quiz Q1, Q2, Q4)[cite: 1], SRC-08 (Academy Ch. 6, Sec. 3, 4 & Quiz Q17, Q18)[cite: 1], SRC-10 (Academy Ch. 8, Sec. 4)[cite: 1], SRC-12 (Academy Ch. 10, Sec. 6 & 12)[cite: 1], SRC-14 (Academy Ch. 12, Case Study #2)[cite: 1], SRC-28 (Docs Patternpedia: Rug Setup).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. Rug Setup (Bearish)

### 1.1 Definition
- **Plain sentence:** A Rug Setup is a bearish two-layer dealer exposure structure where a Positive Gamma (`+GEX`, yellow) node sits directly above one or more Negative Gamma (`-GEX`, purple) nodes while current price is below the positive gamma node; price rallies up into the positive gamma ceiling, gets rejected, and then accelerates rapidly downward through the negative gamma zone below[cite: 1].
- **Precise version:** For an instrument $S$ and active expiration scope $E$, a **Rug Setup** exists at strike pair $(K_{\text{ceil}}, K_{\text{fuel}})$ when:
  1. $K_{\text{ceil}} > K_{\text{fuel}}$ (typically adjacent strikes, or separated by no significant intervening nodes `[INFERENCE]` from `"directly below it"` `[DOCTRINE: Academy Ch. 5, Sec. 2.I]`[cite: 1]).
  2. $\text{GEX}(K_{\text{ceil}}) > 0$ (Positive Gamma / Yellow node acting as a ceiling) `[DOCTRINE: Academy Ch. 5, Sec. 2.I]`[cite: 1].
  3. $\text{GEX}(K_{\text{fuel}}) < 0$ (Negative Gamma / Purple node(s) stacked directly below $K_{\text{ceil}}$) `[DOCTRINE: Academy Ch. 5, Sec. 2.I]`[cite: 1].
  4. Spot price $P_{\text{spot}} < K_{\text{ceil}}$ during the setup formation and approaches $K_{\text{ceil}}$ from below `[DOCTRINE: Academy Ch. 5, Sec. 2.I]`[cite: 1].
  - **Mechanical Behavior:** The upper $+GEX$ node acts as a ceiling where dealers sell into the rally to hedge (causing rejection), while the lower $-GEX$ node forces dealers to sell with the downward move once price turns lower (fueling downside expansion) `[DOCTRINE: Academy Ch. 5, Sec. 2.I]`[cite: 1].

### 1.2 Inputs
- Higher-timeframe (`1D`, `1H`) and intraday (`5m`) OHLCV bars for symbol $S$.
- Heatseeker strike-by-strike GEX array $\{ (K_i, \text{GEX}_i, \text{Taps}_i, \text{Velocity}_i) \}$ for expiration scope $E$.
- Trinity heatmap arrays for `SPX`, `SPY`, and `QQQ`[cite: 1].
- Deflection tolerance $\delta_{\text{deflect}}$ ($\pm\$0.50$ for SPY/QQQ; $\pm\$5.00$ for SPX) `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1].

### 1.3 Procedure (Detection & Execution)
1. **Precondition 1 — Chart Structure Thesis:** Verify via `chart_structure_fundamentals.md` that $K_{\text{ceil}}$ aligns with a chart resistance zone (e.g., Range High, Swing High, Prior Day Low acting as S/R flip resistance) `[DOCTRINE: Academy Ch. 5, Sec. 4, Step 1; Ch. 10, Sec. 6]`[cite: 1]. If no chart structure supports downside, abort (`NO_CHART_STRUCTURE`) `[DOCTRINE: Academy Ch. 5, Sec. 4, Step 1]`[cite: 1].
2. **Precondition 2 — Node Stack Detection:**
   - Scan strikes above or near $P_{\text{spot}}$ for a high-magnitude positive node $K_{\text{ceil}}$ ($\text{GEX}(K_{\text{ceil}}) > 0$)[cite: 1].
   - Check the strike(s) immediately below $K_{\text{ceil}}$ ($K_{\text{fuel}} = K_{\text{ceil}} - \Delta K$). Confirm $\text{GEX}(K_{\text{fuel}}) < 0$ and that its absolute magnitude $|\text{GEX}(K_{\text{fuel}})|$ is significant relative to the board `[DOCTRINE: Academy Ch. 5, Sec. 2.I & Sec. 3]`[cite: 1].
   - *(Minimum absolute dollar threshold or ratio between $|\text{GEX}(K_{\text{ceil}})|$ and $|\text{GEX}(K_{\text{fuel}})|$ is `[NOT SPECIFIED]` in Skylit docs; see `open_questions.md`.)*
3. **Precondition 3 — Check Friction Below (Pattern Interaction Check):**
   - Scan strikes below $K_{\text{fuel}}$ down to the next major Floor node $K_{\text{floor}}$[cite: 1].
   - **Case A (Open Air Pocket / Liquidity Vacuum below $K_{\text{fuel}}$):** Maximum downside acceleration potential ("Price can go into free fall") `[DOCTRINE: Academy Ch. 8, Sec. 4]`[cite: 1].
   - **Case B (Pika Cloud / Dense Positive Gamma Cluster below $K_{\text{fuel}}$ on $S$ or a Trinity peer):** Downside pressure still exists, but movement will encounter friction, slowing into a partial move or chop `[DOCTRINE: Academy Ch. 5, Sec. 3 & 4; Ch. 5 Quiz Q4]`[cite: 1]. Adjust primary profit target to the top of the Pika Cloud `[DOCTRINE: Academy Ch. 10, Sec. 6, Step 4]`[cite: 1].
4. **Precondition 4 — Trinity Alignment Check:**
   - Verify that at least 2 of 3 (`SPX`, `SPY`, `QQQ`) confirm bearish positioning or ceiling rejection `[DOCTRINE: Academy Ch. 10, Sec. 6, Step 5]`[cite: 1]. Bonus confirmation: ceilings rolling down into the Rug Setup `[DOCTRINE: Academy Ch. 12, Sec. 3 Case Study #2]`[cite: 1].
5. **Trigger & Entry:**
   - Wait for price to rally into the Deflection Zone of $K_{\text{ceil}}$ ($[K_{\text{ceil}} - \delta_{\text{deflect}}, K_{\text{ceil}} + \delta_{\text{deflect}}]$) `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1].
   - **Entry Timing Note (Two Phrasings in Sources):**
     - In *Chapter 6, Section 3 & 4*, Skylit states: *"We step in at that moment — buying puts into rug setups as retail chases upside... We enter at the direct tap of major nodes"* `[DOCTRINE: Academy Ch. 6, Sec. 3 & 4]`[cite: 1].
     - In *Chapter 6, Quiz Question 17* (`"In a Rug setup, when is the correct short entry?"`), the four options are: `A. Before price reaches the node`, `B. After price fully reverses`, `C. As soon as price gets near resistance`, and `D. After price fails to continue higher and deflects` `[DOCTRINE: Academy Ch. 6, Quiz Q17]`[cite: 1].
     - **Synthesized Agent Rule:** Do not front-run before the node (`A`/`C`) and do not chase after the move is complete (`B`). Enter at the direct tap within the Deflection Zone once price touches $K_{\text{ceil}}$ and stalls/fails to continue higher into an immediate deflection (`D`) `[INFERENCE combining Ch. 6 Sec. 3 & Quiz Q17]`[cite: 1].
6. **Stop-Loss (Invalidation):**
   - Place stop loss **one node above $K_{\text{ceil}}$** ($K_{\text{stop}} = K_{\text{ceil}} + \Delta K_{\text{node}}$), triggered on a **break and hold (acceptance)** above $K_{\text{stop}}$ `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10]`[cite: 1].
7. **Targets & R:R Gate:**
   - Primary Target $P_{\text{target}} =$ the next high-accumulation structural Floor node below $K_{\text{fuel}}$ (or the top of an intervening Pika Cloud on a Trinity peer) `[DOCTRINE: Academy Ch. 6, Sec. 5 & 11; Ch. 10, Sec. 6]`[cite: 1].
   - Verify $\frac{P_{\text{entry}} - P_{\text{target}}}{P_{\text{stop}} - P_{\text{entry}}} \ge 3.0$ `[DOCTRINE: Academy Ch. 6, Sec. 9]`[cite: 1].

---

## 2. Reverse Rug Setup (Bullish) *(Synonym: Inverse Rug Setup)*

### 2.1 Definition
- **Plain sentence:** A Reverse Rug Setup is a bullish two-layer dealer exposure structure where a Negative Gamma (`-GEX`, purple) node sits directly on top of a Positive Gamma (`+GEX`, yellow) node while current price is above the positive gamma node; price dips into the positive gamma floor, holds and deflects upward, and the negative gamma node above fuels upward acceleration[cite: 1].
- **Precise version:** For an instrument $S$ and active expiration scope $E$, a **Reverse Rug Setup** exists at strike pair $(K_{\text{fuel}}, K_{\text{floor}})$ when:
  1. $K_{\text{fuel}} > K_{\text{floor}}$ (stacked directly over each other) `[DOCTRINE: Academy Ch. 5, Sec. 2.II]`[cite: 1].
  2. $\text{GEX}(K_{\text{fuel}}) < 0$ (Negative Gamma / Purple node on top) `[DOCTRINE: Academy Ch. 5, Sec. 2.II]`[cite: 1].
  3. $\text{GEX}(K_{\text{floor}}) > 0$ (Positive Gamma / Yellow node below) `[DOCTRINE: Academy Ch. 5, Sec. 2.II]`[cite: 1].
  4. Spot price $P_{\text{spot}} > K_{\text{floor}}$ and approaches the structure from above `[DOCTRINE: Academy Ch. 5, Sec. 2.II & Quiz Q2]`[cite: 1].
  - **Mechanical Behavior:** The lower $+GEX$ node acts as a supportive floor where dealers buy the dip to deflect price upward, and as price rebounds into the upper $-GEX$ node, dealers are forced to buy *with* the rising price, fueling pro-cyclical upward momentum (`"hold + bounce"`) `[DOCTRINE: Academy Ch. 5, Sec. 2.II]`[cite: 1].

### 2.2 Inputs
- Same as Section 1.2.

### 2.3 Procedure (Detection & Execution)
1. **Precondition 1 — Chart Structure Thesis:** Verify that $K_{\text{floor}}$ aligns with a chart support zone (e.g., Range Low, Double Bottom, Fresh Swing Low, or Gap Fill) `[DOCTRINE: Academy Ch. 10, Sec. 12; Ch. 11, Quiz Q2]`[cite: 1].
2. **Precondition 2 — Node Stack Detection:**
   - Locate a positive gamma Floor node $K_{\text{floor}}$ ($\text{GEX}(K_{\text{floor}}) > 0$) below or at $P_{\text{spot}}$[cite: 1].
   - Confirm a negative gamma node $K_{\text{fuel}}$ ($\text{GEX}(K_{\text{fuel}}) < 0$) sits directly above $K_{\text{floor}}$ `[DOCTRINE: Academy Ch. 5, Sec. 2.II]`[cite: 1].
3. **Precondition 3 — Check Pathway Above & Trinity Alignment:**
   - Check if an Air Pocket sits above $K_{\text{fuel}}$ and whether `SPX`, `SPY`, and `QQQ` show upside King Node accumulation or collective floors ($\ge 2/3$ alignment) `[DOCTRINE: Academy Ch. 10, Sec. 12; Ch. 11, Sec. 3, Step 7]`[cite: 1].
4. **Trigger & Entry:**
   - Wait for price to dip into the Deflection Zone of $K_{\text{floor}}$ ($[K_{\text{floor}} - \delta_{\text{deflect}}, K_{\text{floor}} + \delta_{\text{deflect}}]$)[cite: 1].
   - Confirm failure of breakdown followed by upward deflection (`[DOCTRINE: Academy Ch. 6, Quiz Q18: "Failure of breakdown followed by upward deflection"]` and `[DOCTRINE: Academy Ch. 6, Sec. 4: "When you buy calls at a reverse rug floor as price taps it, you are buying from people dumping in fear"]`[cite: 1]).
5. **Stop-Loss (Invalidation):**
   - Place stop loss **one node below the Reverse Rug floor** ($K_{\text{stop}} = K_{\text{floor}} - \Delta K_{\text{node}}$), triggered on a break and hold below $K_{\text{stop}}$ `[DOCTRINE: Academy Ch. 6, Sec. 10; Ch. 10, Sec. 12 Step 6]`[cite: 1].
6. **Targets & R:R Gate:**
   - Target the next high-accumulation upside node / King Node across the system, verifying $\text{R:R} \ge 3.0$ `[DOCTRINE: Academy Ch. 10, Sec. 12]`[cite: 1].

---

## 3. Edge Cases (Rug & Reverse Rug)
- **Tap Degradation on the Ceiling/Floor Node:** If $K_{\text{ceil}}$ or $K_{\text{floor}}$ has already been tested twice ($N_{\text{taps}} \ge 2$) and Velocity Mode shows rapid accumulation *beyond* the node, the Rug/Reverse Rug can fail and break through (as seen in Chapter 12 Case Study #1 where SPY broke above 692 due to rapid QQQ/SPX upside velocity) `[DOCTRINE: Academy Ch. 12, Sec. 2]`[cite: 1].
- **Conflicting Pika Clouds:** If QQQ flashes a Rug Setup, but SPY has a massive Pika Cloud directly underneath spot, do not expect a vertical crash; expect a slower, choppy descent or partial move `[DOCTRINE: Academy Ch. 5, Sec. 3 & 4]`[cite: 1].

---

## 4. Worked Examples

### 4.1 Bearish Rug Setup (`[DOCTRINE: Academy Ch. 10, Sec. 6 & Ch. 12, Sec. 3 Case Study #2]`)
- **Context (Ch. 10, Sec. 6):** SPX approaches resistance at Previous Day Low (`6775`)[cite: 1].
- **Map:** SPX and SPY both display **Rug Setups** (positive gamma ceiling stacked above negative gamma nodes), while QQQ points lower with a King Node to the downside[cite: 1]. However, SPY has positive gamma Pika clusters lower[cite: 1].
- **Execution:** Enter SPX puts directly in line with chart resistance (`6775`) on the Rug deflection; take profits into the downside SPY Pika clusters before attempting to hold all the way to QQQ's lower King Node `[DOCTRINE: Academy Ch. 10, Sec. 6]`[cite: 1].
- **Context (Ch. 12, Case Study #2 — Feb 12, 2026):** Upside nodes on SPX decay while ceilings roll down. SPY rolls its final ceiling down to `692`, placing all 3 indices (`SPX`, `SPY`, `QQQ`) in synchronized **Rug Setups**[cite: 1]. Downside node `6875` on SPX rapidly accumulates in Velocity Mode, serving as the downside delivery target upon deflection off the `692` SPY Rug ceiling `[DOCTRINE: Academy Ch. 12, Sec. 3]`[cite: 1].

### 4.2 Bullish Reverse Rug Setup (`[DOCTRINE: Academy Ch. 10, Sec. 12 Case Study]`)
- **Step 1 (Chart Structure):** SPX approaches key support and forms a **Double Bottom** at `6775` ($\text{Thesis} = \text{Bullish}$)[cite: 1].
- **Step 2 & 3 (Heatseeker & Trinity):**
  - `SPX`: **Inverse (Reverse) Rug Setup** at `6775` (negative gamma stacked over positive gamma floor at `6775`)[cite: 1].
  - `SPY`: King Node higher at `681` with rapid growth and a generous open Air Pocket above spot[cite: 1].
  - `QQQ`: King Node higher at `612` with rapid growth[cite: 1].
- **Step 4 & 5 (Execution):** Enter long (calls/futures) on the deflection off the `6775` SPX Reverse Rug, targeting the upside accumulation at `681` SPY / `612` QQQ through the SPY air pocket[cite: 1].
- **Step 6 (Invalidation Stop):** Stop loss placed one node below the Reverse Rug at **`6770` SPX** ($5\text{ points}$ risk) `[DOCTRINE: Academy Ch. 10, Sec. 12]`[cite: 1].

---

## 5. What They Are NOT
- A Rug Setup is **not** simply any negative gamma (purple) node; without the positive gamma node stacked above spot to cause the initial rejection (or a chart resistance zone), a lone purple node is just a volatility-expanding strike that price can overshoot upward `[DOCTRINE: Academy Ch. 2, Sec. 6; Ch. 5, Sec. 2.I]`[cite: 1].
- Neither setup is a standalone mechanical signal if Chart Structure or Trinity Alignment disagrees (`"If you isolate a pattern, you will misread the market"`) `[DOCTRINE: Academy Ch. 5, Sec. 1 & 5]`[cite: 1].

---

## 6. Related Concepts
- See `setup_catalog_beach_ball_and_pika_cloud.md` for Pika Cloud friction and Beach Ball overshoots.
- See `entries_stops_targets_and_rr.md` for stop and R:R math.

---

## Agent Checklist (`setup_catalog_rug_and_reverse_rug.md`)

1. **Verify Polarity Stack Order:**
   - `RUG_BEARISH`: Confirm `GEX(K_upper) > 0` AND `GEX(K_lower) < 0` with `Spot < K_upper`[cite: 1].
   - `REVERSE_RUG_BULLISH`: Confirm `GEX(K_upper) < 0` AND `GEX(K_lower) > 0` with `Spot > K_lower`[cite: 1].
2. **Confirm Chart S/R Alignment:** Require `K_upper` (for Rug) or `K_lower` (for Reverse Rug) to overlap a graded Chart Resistance or Support zone[cite: 1].
3. **Check Intervening Friction:** Scan the delivery path for Pika Clouds across all 3 Trinity boards; if present, cap `Target_1` at the Pika Cloud boundary[cite: 1].
4. **Enforce One-Node Stop:** Set `Stop_Loss` at `K_upper + 1_node` (Rug) or `K_lower - 1_node` (Reverse Rug) on a break-and-hold basis[cite: 1].