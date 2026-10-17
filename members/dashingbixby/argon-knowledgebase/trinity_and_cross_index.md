# trinity_and_cross_index.md

- **Title:** Cross-Index Confluence — The Trinity System (`SPX`, `SPY`, `QQQ`), Independence Weighting, Derived Orbs ("The Wiggle") & Cross-Asset Conversion
- **Scope:** Operational definitions, inputs, step-by-step evaluation procedures, edge cases, and worked examples for Trinity Mode cross-index alignment, statistical non-independence between SPX and SPY, Derived Orbs on futures/ETFs, and dynamic spot-ratio conversions.
- **Sources Used:** SRC-02 (Academy Atlas Course, Sec. 8)[cite: 1], SRC-06 (Academy Ch. 4, Sec. 3)[cite: 1], SRC-07 (Academy Ch. 5, Sec. 2.V)[cite: 1], SRC-12 (Academy Ch. 10, Sec. 1–14)[cite: 1], SRC-13 (Academy Ch. 11, Sec. 3 Step 8)[cite: 1], SRC-15 (Academy Ch. 13, Quiz Q7, Q8, Q16)[cite: 1], SRC-22 (Academy Flowseeker Ch. 7, Sec. 2, 4, 5, 6)[cite: 1], SRC-25 (Academy Flowseeker Ch. 10, Sec. 3)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 2 & 3)[cite: 1], SRC-29 (Docs Atlas & Heatseeker Field Guides).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. The Trinity System & The "Connected Springs" Mechanics

### 1.1 Definition
- **Plain sentence:** The US equity index market moves as a single interconnected dealer exposure system represented by three primary option books—**`SPX` (`SPXW`)**, **`SPY`**, and **`QQQ`** (collectively called **The Trinity**); a move on one index is only confirmed when dealer positioning across the system agrees[cite: 1].
- **Precise version:** The Trinity is a 3-element system $\mathcal{T} = \{ \text{SPX}, \text{SPY}, \text{QQQ} \}$ where each element represents a specialized expression of market-wide dealer hedging obligations `[DOCTRINE: Academy Ch. 10, Sec. 2]`[cite: 1]:
  1. **`SPX` (`SPXW`):** Institutional index hedging[cite: 1].
  2. **`SPY`:** S&P 500 ETF liquidity and options flow[cite: 1].
  3. **`QQQ`:** Nasdaq-100 mega-cap technology weighting[cite: 1].
  - **Connected Springs Principle:** Because the same mega-cap constituents and dealer books link all three instruments, one index cannot sustain a clean directional move if the others sit on opposing structural walls (e.g., if `SPY` and `QQQ` show downside setups, but `SPX` sits directly on a massive King Node floor, `SPX` acts as an unyielding spring that blocks the breakdown until `SPX` itself breaks and holds below its King Node) `[DOCTRINE: Academy Ch. 10, Sec. 2 & 4]`[cite: 1].

### 1.2 Statistical Independence Weighting (`SPX` + `SPY` vs. `QQQ`)
- **The Evidentiary Overlap Rule:** While Trinity Mode evaluates three boards (`3/3`, `2/3`, `1/3`), Flowseeker Chapter 7 establishes a critical statistical rule for weighting their agreement `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 4]`[cite: 1]:
  - **`SPX` and `SPY` track the exact same S&P 500 underlying index.** Therefore, when `SPX` and `SPY` agree in flow or positioning, their agreement counts as **close to one piece of independent evidence, not two** `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 4, Quiz Q14 & Q17]`[cite: 1].
  - **`QQQ` is the more independent leg** (tracking the Nasdaq-100), though mega-cap tech stocks still overlap both indices `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 4, Quiz Q15]`[cite: 1].
  - **True Independent Confirmation:** Real confirmation comes from combining **independent types of evidence** (Chart Structure + Heatseeker Dealer Map + Flow/Next-Day OI) AND verifying that `QQQ` does not oppose `SPX`/`SPY` `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 4, Quiz Q16]`[cite: 1].

### 1.3 Inputs
- Individual Chart Location reads (`AT_FLOOR_SUPPORT`, `AT_CEILING_RESISTANCE`, `AT_MIDPOINT`) for `SPX`, `SPY`, and `QQQ`[cite: 1].
- Individual Heatseeker node maps (`King Node`, `Floor`, `Ceiling`, `Gatekeeper`, `Air Pockets`, `Patterns`) for `SPX`, `SPY`, and `QQQ`[cite: 1].
- Individual Options Flow reads for `SPX`, `SPY`, and `QQQ` (each read strictly against its own map) `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 2 & 4, Quiz Q1 & Q18]`[cite: 1].

### 1.4 Procedure (Trinity Alignment Classification & Sizing Gate)
1. **Evaluate Each Index on Its Own Board First:**
   - Never plot or read an `SPX` print on `SPY`'s map or a `QQQ` print on `SPX`'s map; evaluate each symbol's chart, map, and flow on its own strike ladder `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 2 & 4, Quiz Q1 & Q18]`[cite: 1].
2. **Assign a Structural Bias Vector $B_m \in \{+1 \text{ (Bullish)}, 0 \text{ (Pinned/Neutral)}, -1 \text{ (Bearish)}\}$ for Each Member $m \in \{\text{SPX}, \text{SPY}, \text{QQQ}\}$:**
   - $B_m = +1$ if $m$ is deflecting off a Floor / Reverse Rug / King Node support with upside accumulation or air pocket above[cite: 1].
   - $B_m = -1$ if $m$ is deflecting off a Ceiling / Rug Setup / King Node resistance with downside accumulation or air pocket below[cite: 1].
   - $B_m = 0$ if $m$ is sandwiched inside a tight Pika Cloud, sitting at a midpoint, or blocked by an opposing Gatekeeper[cite: 1].
3. **Classify System Alignment & Enforce Execution Action:**

| Trinity State | Condition Across `{SPX, SPY, QQQ}` | Confidence & Environment | Mandatory Agent Action | Source Tag |
| :--- | :--- | :--- | :--- | :--- |
| **Scenario A: Full Alignment (`3 of 3`)** | All 3 indices share the same directional bias ($B_{\text{SPX}} == B_{\text{SPY}} == B_{\text{QQQ}} \in \{+1, -1\}$) and have open delivery paths[cite: 1]. | High-probability environment; cleanest node-to-node delivery[cite: 1]. | **Execute A+ setup at normal (100%) position size**[cite: 1]. | `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5]`[cite: 1] |
| **Scenario B: Partial Alignment (`2 of 3`)** | Two indices agree in bias while the third lags, is pinned, or sits at an opposing level (e.g., `SPX` + `SPY` bullish at floors, `QQQ` bearish at ceiling)[cite: 1]. | Reduced confidence; bare minimum confluence threshold[cite: 1]. | **Reduce position size** (e.g., 50% size `[INFERENCE]`, exact % `[NOT SPECIFIED]`) **OR wait** for the diverging index (`QQQ`) to resolve its ceiling/floor[cite: 1]. | `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5 & 6, Quiz Q19 & Q21]`[cite: 1] |
| **Scenario C: Divergence / Misalignment (`<= 1 of 3`)** | Indices actively conflict (e.g., 1 bullish, 1 bearish, 1 pinned = `Whipsaw`) or only 1 index shows a setup[cite: 1]. | Low confidence; structural trap / whipsaw environment[cite: 1]. | **No Trade (`ABSTAIN`)** (or for experienced range-faders in a defined Whipsaw, fade only the extreme outer boundary at reduced size)[cite: 1]. | `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 5, Sec. 2.V; Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5]`[cite: 1] |

---

## 2. Derived Orbs ("The Wiggle") & Cross-Asset Strike Mapping

### 2.1 Definition
- **Plain sentence:** Because futures contracts (`ES`, `NQ`) and cross-referenced index products (`SPY`, `SPXW`) share underlying exposure with primary options chains, Atlas can project **"Derived Orbs"** (borrowed GEX/VEX nodes) from the source options market onto the target chart; these borrowed levels shift slightly minute-by-minute (**"the wiggle"**) to stay accurately synced with live price spreads between the two instruments[cite: 1].
- **Precise version:** Let $S_{\text{target}}$ be the charted instrument and $S_{\text{source}}$ be the options chain providing the GEX/VEX nodes `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1]:
  - **`ES` (E-mini S&P 500 Futures)** borrows levels from **`SPXW` / `SPY`** `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].
  - **`NQ` (E-mini Nasdaq-100 Futures)** borrows levels from **`QQQ`** `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].
  - **`SPY`** can display derived levels from **`SPXW`** `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].
  - **`SPXW`** can display derived levels from **`SPY`** `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].

### 2.2 Mathematical Basis of "The Wiggle" & Why Fixed Multipliers Are Prohibited
1. **Prohibition on Fixed Multipliers:**
   - Flowseeker Chapter 7 explicitly warns: *"a QQQ strike is not an NQ price — any ETF-to-future mapping needs its own conversion, recorded with the time, never a fixed multiplier... The relationship changes over time — the conversion needs its inputs and time recorded, not a fixed multiplier"* `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 2, Quiz Q2 & Q5]`[cite: 1].
2. **Live Dynamic Conversion Formula:**
   - Because cost-of-carry (interest rates minus dividends to futures expiration) and ETF tracking basis fluctuate continuously throughout the session, the mapped strike $K_{\text{derived}}(t)$ on $S_{\text{target}}$ at timestamp $t$ is a function of the live price ratio (or live basis spread) at timestamp $t$ `[INFERENCE from Academy Atlas Sec. 8 & Flowseeker Ch. 7 Sec. 2]`[cite: 1]:
     $$K_{\text{derived}}(t) = K_{\text{source}} \times \frac{P_{\text{target}}(t)}{P_{\text{source}}(t)}$$
     *(Note: Whether Atlas internally uses the multiplicative ratio $P_{\text{target}}(t)/P_{\text{source}}(t)$ or rolling basis spread is `[NOT SPECIFIED]` in the public docs; both yield virtually identical values near spot, and the agent must record $(P_{\text{source}}(t), P_{\text{target}}(t), t)$ whenever converting a strike `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 2]`[cite: 1].)*
3. **Why "The Wiggle" Is Expected and Healthy:**
   - Minute-to-minute adjustments in $K_{\text{derived}}(t)$ ensure that when `ES` or `NQ` tags a derived orb on the futures chart, the source instrument (`SPXW`/`SPY` or `QQQ`) is simultaneously tagging that exact option strike `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].
4. **Source Data Dropout Rule:**
   - If the price or exposure feed for $S_{\text{source}}$ temporarily drops out, Atlas **hides the derived orb completely** until the source feed is reliable again, rather than displaying a frozen level at a stale price `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].

---

## 3. Edge Cases
- **Asymmetric Friction Across the Trinity:** All 3 indices can agree on direction (`3/3` bearish), yet one index (`SPY`) has a dense Pika Cloud in its downside path while `SPX` and `QQQ` have open air pockets `[DOCTRINE: Academy Ch. 10, Sec. 6 Step 4; Ch. 11, Sec. 3 Step 7]`[cite: 1]. In this case, `SPY`'s Pika Cloud acts as system-wide turbulence; the agent must take partial or full profits when `SPY` hits its Pika Cloud rather than assuming `QQQ` will effortlessly drag `SPY` through the yellow cluster `[DOCTRINE: Academy Ch. 10, Sec. 6 Step 4]`[cite: 1].
- **SPX King Node Blocking SPY/QQQ Breakdown:** In Chapter 10 Section 4, `SPY` shows downside and `QQQ` is below its King Node, but `SPX` is sitting directly on a King Node Floor `[DOCTRINE: Academy Ch. 10, Sec. 4]`[cite: 1]. Because `SPX` represents institutional hedging, `SPX` prevents `SPY` and `QQQ` from breaking lower until `SPX` breaks and holds below its own King Node `[DOCTRINE: Academy Ch. 10, Sec. 4]`[cite: 1].

---

## 4. Worked Example (`[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 6 Case Study]`)
- **Individual Reads:**
  - `SPX`: At Range Low, Floor node below, Bullish flow[cite: 1].
  - `SPY`: At Range Low (`581.40` near `578` floor), Floor node at `578`, King Node ceiling at `590`, Bullish `590/600` call spread with rising OI[cite: 1].
  - `QQQ`: At **Range High**, **Ceiling node above**, **Bearish flow**[cite: 1].
- **Trinity Synthesis:**
  - `SPX` and `SPY` agree (`Bullish`), which counts as ~1 independent index read because both track the S&P 500 `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 4 & 6]`[cite: 1].
  - `QQQ` disagrees at its ceiling (`Bearish`), yielding **2 of 3 Partial Alignment** `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 6]`[cite: 1].
- **Agent Decision:** Do **not** take full size. Either **reduce position size** or **wait** for `QQQ` to break or reject its ceiling; log `"Weakens if: SPY accepts below 578; the floor node shrinks or reshuffles lower; open interest at 590 falls back."` `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 6]`[cite: 1].

---

## 5. What It Is NOT
- Agreement between `SPX` and `SPY` is **not** "double confirmation" of two independent markets `[DOCTRINE: Academy Flowseeker Ch. 7, Quiz Q17]`[cite: 1].
- Cross-index divergence is **not** an opportunity to "pick the index that agrees with your bias and ignore the others" `[DOCTRINE: Academy Ch. 10, Sec. 4; Flowseeker Ch. 7, Sec. 5 & 7]`[cite: 1].

---

## 6. Related Concepts
- See `heatseeker_advanced_mechanics_and_agent_guardrails.md` and `no_trade_conditions.md` (Gate 3).

---

## Agent Checklist (`trinity_and_cross_index.md`)

1. **Scope Every Print/Node to Its Native Symbol:** Verify `Print.Symbol == Map.Symbol` before evaluating strike confluence[cite: 1].
2. **Discount SPX+SPY Redundancy:** When scoring cross-index agreement, treat `{SPX, SPY}` agreement as the S&P 500 leg and require `QQQ` (the Nasdaq-100 leg) for full `3/3` sizing[cite: 1].
3. **Timestamp All Cross-Asset Conversions:** Whenever mapping `QQQ` $\rightarrow$ `NQ` or `SPY`/`SPXW` $\rightarrow$ `ES`, compute the live ratio $P_{\text{target}}(t)/P_{\text{source}}(t)$ at timestamp $t$ and suppress derived levels if the source feed is stale or dropped[cite: 1].