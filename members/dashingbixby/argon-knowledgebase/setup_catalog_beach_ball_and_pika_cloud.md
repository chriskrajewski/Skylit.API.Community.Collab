# setup_catalog_beach_ball_and_pika_cloud.md

- **Title:** Setup Catalog — Beach Ball (Overshoot Setup) & Pika Clouds (Positive Gamma Clusters)
- **Scope:** Zero-assumption definitions, inputs, step-by-step detection and execution procedures, stop/target rules, edge cases, and worked examples for Beach Ball overshoots and Pika Cloud compression zones.
- **Sources Used:** SRC-04 (Academy Ch. 2, Sec. 3 & 6)[cite: 1], SRC-06 (Academy Ch. 4, Sec. 2 & 3)[cite: 1], SRC-07 (Academy Ch. 5, Sec. 2.III, 2.IV & Quiz Q3, Q5, Q9)[cite: 1], SRC-08 (Academy Ch. 6, Sec. 5, 8 & Quiz Q2, Q9, Q10, Q19)[cite: 1], SRC-13 (Academy Ch. 11, Sec. 3, Step 5 & Quiz Q2)[cite: 1], SRC-14 (Academy Ch. 12, Case Study #1)[cite: 1], SRC-15 (Academy Ch. 13, Quiz Q0, Q4, Q15, Q17)[cite: 1].
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. Beach Ball (Overshoot Setup)

### 1.1 Definition
- **Plain sentence:** A Beach Ball setup occurs when price aggressively punches past a major Floor or Ceiling node (most commonly a purple Negative Gamma node) like a beach ball pushed underwater, traps retail traders chasing a breakout, stalls, and snaps back strongly into the prior range[cite: 1].
- **Precise version:** Given a major structural node $K_{\text{node}}$ (Floor or Ceiling, particularly where $\text{GEX}(K_{\text{node}}) < 0$), a **Beach Ball (Overshoot)** event occurs when:
  1. Price moves beyond $K_{\text{node}}$ outside its standard direct-tap tolerance ($P_{\text{spot}} < K_{\text{floor}} - \delta_{\text{deflect}}$ for a downside overshoot, or $P_{\text{spot}} > K_{\text{ceil}} + \delta_{\text{deflect}}$ for an upside overshoot) `[DOCTRINE: Academy Ch. 5, Sec. 2.IV; Ch. 6, Sec. 5]`[cite: 1].
  2. Price **fails to reach or hold** the next structural node beyond $K_{\text{node}}$ ($K_{\text{invalid}} = K_{\text{node}} \pm \Delta K_{\text{node}}$) `[INFERENCE from Academy Ch. 6, Sec. 3 & 10 Stop Rule]`[cite: 1].
  3. Momentum stalls and prints a **rejection wick**, followed by price reclaiming $K_{\text{node}}$ (`Overshoot -> Stall -> Reversal`) `[DOCTRINE: Academy Ch. 5, Sec. 2.IV; Ch. 6, Sec. 5 & Quiz Q9]`[cite: 1].
  - **Mechanical Reason:** In negative gamma (`-GEX`), dealers hedge *with* the move, accelerating price through the strike; retail traders mistake the extension for a breakout and pile in at the extreme just as dealer rebalancing and opposing Trinity structure force a violent snap-back (`"The deeper it goes: the stronger the release"`) `[DOCTRINE: Academy Ch. 5, Sec. 2.IV; Ch. 6, Sec. 8]`[cite: 1].

### 1.2 Inputs
- Intraday OHLCV candles (`1m`, `5m`) for symbol $S$.
- Heatseeker node array $\{ (K_i, \text{GEX}_i, \text{Taps}_i, \text{Velocity}_i) \}$.
- Trinity maps (`SPX`, `SPY`, `QQQ`) to verify whether peer indices hold their floors/ceilings during $S$'s overshoot `[DOCTRINE: Academy Ch. 11, Quiz Q2; Ch. 13, Quiz Q17]`[cite: 1].

### 1.3 Procedure (Detection & Execution)
1. **Pre-Trade Regime Recognition:**
   - Before price arrives at $K_{\text{node}}$, check the sign of $K_{\text{node}}$ and the surrounding gamma regime[cite: 1].
   - If $\text{GEX}(K_{\text{node}}) < 0$ (Purple / Barney node) or the board is in a fast Negative Gamma regime: **Set execution mode to `ASSUME_OVERSHOOT_FIRST`** (`"It is safer to assume overshoot first. Because this is where traders get run over."`) `[DOCTRINE: Academy Ch. 6, Sec. 8; Ch. 6 Quiz Q2 & Q10; Ch. 13 Quiz Q4 & Q15]`[cite: 1].
   - *(Note: Beach Ball overshoots can occur in either positive or negative gamma deflections, but are **more common in negative gamma** `[DOCTRINE: Academy Ch. 5, Sec. 2.IV]`[cite: 1].)*
2. **Monitor the Overshoot (Do Not Chase Breakout & Do Not Front-Run):**
   - **Rule 1:** *"We do NOT trade breakouts. What you are seeing is: Not a breakout... It is: Overshoot $\rightarrow$ reaction $\rightarrow$ potential reversion."* `[DOCTRINE: Academy Ch. 5, Sec. 2.IV; Ch. 5 Quiz Q5 & Q9]`[cite: 1].
   - **Rule 2:** Do not short/long the very first touch blindly if momentum is expanding violently into a purple node (`"A trader shorts the first touch of a negative gamma resistance and gets squeezed. What was the mistake? Ignoring overshoot risk and front-running the node."`) `[DOCTRINE: Academy Ch. 6, Quiz Q10]`[cite: 1].
   - **Rule 3:** When price pushes beyond $K_{\text{node}}$, recognize that *"the thesis is not invalidated — its actually strengthening. Retail is piling in on the wrong side"*—provided price does not break and hold 1 full node beyond $K_{\text{node}}$ and Trinity peers still support reversion `[DOCTRINE: Academy Ch. 6, Sec. 8]`[cite: 1].
3. **Identify the Entry Trigger (`Overshoot + Stall`):**
   - Wait for the candle pushing past $K_{\text{node}}$ to **stall and print a rejection wick** (or reclaim $K_{\text{node}}$) `[DOCTRINE: Academy Ch. 6, Sec. 5 Step 2a & 3; Ch. 6 Quiz Q9: "Price pushes into a purple node, breaks through it, then stalls and prints a rejection wick. What is the best interpretation? The overshoot may be complete and deflection is beginning"]`[cite: 1].
   - Enter at the extreme as the overshoot stalls, absorbing trapped retail liquidity `[DOCTRINE: Academy Ch. 6, Sec. 8]`[cite: 1].
4. **Stop-Loss & Targets:**
   - **Stop-Loss:** Placed beyond the overshoot wick high/low and/or **one node beyond $K_{\text{node}}$** (break and hold) `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10]`[cite: 1].
   - **Targets:** Reversion back across $K_{\text{node}}$ toward the opposite high-accumulation node or King Node ($\text{R:R} \ge 3:1$) `[DOCTRINE: Academy Ch. 6, Sec. 5 Step 4 & Sec. 9]`[cite: 1].

---

## 2. Pika Clouds (Positive Gamma Clusters)

### 2.1 Definition
- **Plain sentence:** Pika Clouds are dense clusters of multiple Positive Gamma (`+GEX`, yellow) nodes stacked across adjacent strikes that act like "gravity wells," slowing down price movement, halting trends, pinning price, and creating range-bound rotation[cite: 1].
- **Precise version:** For symbol $S$ and expiration scope $E$, a **Pika Cloud** is a contiguous strike sequence $\{K_m, K_{m+1}, \dots, K_{m+p}\}$ ($p \ge 1$, i.e., 2 or more adjacent or tightly grouped strikes) where:
  1. $\text{GEX}(K_i) > 0$ for all $K_i$ in the cluster `[DOCTRINE: Academy Ch. 5, Sec. 2.III]`[cite: 1].
  2. The combined and individual magnitudes $|\text{GEX}(K_i)|$ are large relative to the board `[DOCTRINE: Academy Ch. 5, Sec. 2.III]`[cite: 1].
  - **Key Doctrine:** Pika Clouds are **not bullish or bearish by default**; their influence is purely a function of **magnitude** and **location relative to spot** `[DOCTRINE: Academy Ch. 5, Sec. 2.III]`[cite: 1].

### 2.2 Inputs
- Heatseeker strike-by-strike GEX array $\{ (K_i, \text{GEX}_i) \}$.
- Current spot price $P_{\text{spot}}$.

### 2.3 Procedure (Classification & Execution Rules by Location)
An agent evaluates a Pika Cloud based on where $P_{\text{spot}}$ sits relative to the cluster:

1. **Case 1 — Spot is Inside a Pika Cloud ("Sandwiched" between 2+ Large Yellow Nodes):**
   - **Expected Behavior:** Price slows, sticks, rotates tightly, and struggles to trend; breakouts fail (`Type 1: Range / Choppy Day`) `[DOCTRINE: Academy Ch. 4, Sec. 2 & 3; Ch. 5, Sec. 2.III; Ch. 12, Sec. 2 Case Study #1]`[cite: 1].
   - **Execution Rule:** **Fade stretched extremes ONLY** (buy calls at the lower Pika boundary floor; buy puts at the upper Pika boundary ceiling), take profits quickly to avoid theta burn, and never chase breakouts or trade the midpoint between the Pika nodes `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 6, Sec. 8 & Quiz Q19; Ch. 11, Sec. 3 Step 6; Ch. 12, Sec. 2]`[cite: 1].
2. **Case 2 — Pika Cloud Sits in the Delivery Path Between Spot and a Distant Target:**
   - **Expected Behavior:** The Pika Cloud acts as **friction** (`"Gravity wells"`), slowing or halting a directional move even if another index has a Rug or Reverse Rug setup `[DOCTRINE: Academy Ch. 5, Sec. 3 & 4; Ch. 10, Sec. 6 Step 4]`[cite: 1].
   - **Execution Rule:** Take profits (or scale out) as price reaches the boundary of the Pika Cloud rather than holding blindly for a node on the far side of the cloud `[DOCTRINE: Academy Ch. 10, Sec. 6 Step 4]`[cite: 1].

---

## 3. Edge Cases (Beach Ball & Pika Clouds)
- **Velocity Override of a Pika Cloud:** In Chapter 12 Case Study #1 (Feb 25, 2026), SPY is initially sandwiched between two large Pika nodes at `690` and `692`[cite: 1]. Fading the edges works early, *until* each test weakens the `692` ceiling and QQQ/SPX experience rapid upside accumulation in Velocity Mode—blasting SPY above its `692` Pika range `[DOCTRINE: Academy Ch. 12, Sec. 2]`[cite: 1]. **Agent Rule:** Do not fade a Pika Cloud boundary on its 3rd+ tap when Velocity Mode shows aggressive accumulation in the breakout direction `[DOCTRINE: Academy Ch. 6, Sec. 6; Ch. 12, Sec. 2]`[cite: 1].
- **Distinguishing an Overshoot (Beach Ball) from a True Breakdown:** If price punches through $K_{\text{node}}$ and *keeps accelerating* through $K_{\text{node}} \pm \Delta K_{\text{node}}$ with all 3 Trinity indices aligned in the direction of the break and Velocity Mode expanding in that direction, it is **not** a Beach Ball reversion—it is invalidation / trend delivery `[INFERENCE from Academy Ch. 6, Sec. 10 & Ch. 8, Sec. 9]`[cite: 1].

---

## 4. Worked Examples

### 4.1 Beach Ball Overshoot (`[DOCTRINE: Academy Ch. 5, Sec. 2.IV; Ch. 11, Sec. 3 Step 5 & Quiz Q2]`)
- **Example 1 (Ch. 5, Sec. 2.IV):** QQQ has a major Floor at `600`. Price overshoots below the `600` floor, traps late bears, and reverts sharply back to the upside `[DOCTRINE: Academy Ch. 5, Sec. 2.IV]`[cite: 1].
- **Example 2 (Ch. 11, Step 5):** QQQ approaches a Gatekeeper node at `593` in a volatile zone. QQQ overshoots below `593`, stalls while SPX and SPY hold their floors, reclaims `593`, and bounces aggressively into the air pocket above `[DOCTRINE: Academy Ch. 11, Sec. 3, Steps 3–8]`[cite: 1].
- **Example 3 (Ch. 11, Quiz Q2):** Markets tank after a gap up and fill the gap. `SPX` sits on a Gatekeeper / Reverse Rug setup; `SPY` **overshoots the lower end of its range** (Beach Ball); `QQQ` is in proximity of its King Node[cite: 1]. Result: Price bounces and reverses into SPX's air pocket, targeting SPY's `680`, `682`, and `685` nodes `[DOCTRINE: Academy Ch. 11, Quiz Q2]`[cite: 1].

### 4.2 Pika Cloud Sandwich (`[DOCTRINE: Academy Ch. 12, Sec. 2 Case Study #1 — Feb 25, 2026]`)
- **Setup (12 minutes after open):** SPY is "sandwiched" between two high-value Positive Gamma (Pika) nodes: Floor at `690` and Ceiling at `692`, while SPX sits on a floor at `6920` and QQQ is heavily positive gamma[cite: 1].
- **Execution:** Because SPY and QQQ are in a tight Pika Cloud positive gamma regime, the morning game plan is to fade the extreme edges of the Pika range: **buy puts off `692` SPY and buy calls off `690` SPY** (until tap degradation + QQQ velocity shift occurs later in the session) `[DOCTRINE: Academy Ch. 12, Sec. 2]`[cite: 1].

---

## 5. What They Are NOT
- A **Beach Ball overshoot** is **not** a breakout continuation signal (`"A trader sees price push above a king node and assumes a breakout. What is the correct interpretation? Overshoot that may lead to reaction or reversion"`) `[DOCTRINE: Academy Ch. 5, Quiz Q9]`[cite: 1].
- A **Pika Cloud** is **not** a directional bias (it does not mean "bullish because yellow"); it means **high friction and compression** `[DOCTRINE: Academy Ch. 5, Sec. 2.III & Sec. 6]`[cite: 1].

---

## 6. Related Concepts
- See `entries_stops_targets_and_rr.md` for direct-tap vs. post-overshoot entry rules.
- See `no_trade_conditions.md` for when Pika Cloud midpoints or 3rd-tap fade attempts must be aborted.

---

## Agent Checklist (`setup_catalog_beach_ball_and_pika_cloud.md`)

1. **Set Overshoot Flag on `-GEX` Nodes:** Whenever `GEX(Target_Node) < 0` or `Regime == NEGATIVE_GAMMA`, set `Require_Stall_And_Wick = TRUE` before triggering a fade entry[cite: 1].
2. **Never Trigger Breakout Entries on Node Pierces:** If price pierces a King Node, Floor, or Ceiling, suppress any momentum-chasing orders and evaluate for `BEACH_BALL_OVERSHOOT` first[cite: 1].
3. **Detect Pika Cloud Sandwiches:** If `Spot` sits between two adjacent/nearby `+GEX` nodes ($K_{\text{floor}}, K_{\text{ceil}}$) with high magnitude, set `Session_Mode = PIKA_RANGE_FADE`, restrict entries strictly to $K_{\text{floor}}$ and $K_{\text{ceil}}$, and enforce quick profit-taking[cite: 1].