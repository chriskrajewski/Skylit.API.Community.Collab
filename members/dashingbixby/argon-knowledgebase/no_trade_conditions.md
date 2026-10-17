# no_trade_conditions.md

- **Title:** Mandatory No-Trade (Abstain) Conditions — Boolean Agent Gates
- **Scope:** Every condition under which Skylit doctrine requires a trader or agent to pass, stand down, or wait, formalized as testable boolean checks.
- **Sources Used:** SRC-03 through SRC-26[cite: 1], SRC-27 (Docs Core Concepts), SRC-28 (Docs Patternpedia: Rainbow Road, Whipsaw, Ten Commandments).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`, `entries_stops_targets_and_rr.md`
- **Last Updated:** 2026-10-17

---

## Core Principle

> *"No trade is a valid outcome — often the disciplined one."* `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 3 & Quiz Q24]`[cite: 1].
> *"No setup, no entry. We dont force trades — we let the map bring price to us. If the setup doesnt materialize or price never reaches the node, there is no trade."* `[DOCTRINE: Academy Ch. 6, Sec. 12]`[cite: 1].

An agent must evaluate the following **12 Boolean Abstain Gates** before any order can be emitted. If **any** gate evaluates to `TRUE`, the agent must suppress trade execution (`DECISION = NO_TRADE` or `WAIT`).

---

## The 12 Boolean Abstain Gates

### Gate 1: Midpoint Location (`IS_AT_MIDPOINT`)
- **Definition:** Spot price is sitting in the middle region between the active Floor and Ceiling (or Chart Range Low and Range High) rather than at a structural extreme `[DOCTRINE: Academy Ch. 1, Sec. 4; Ch. 3, Sec. 6; Ch. 6, Sec. 7; Docs Ten Commandments #4]`[cite: 1].
- **Inputs:** $P_{\text{spot}}$, $K_{\text{floor}}$, $K_{\text{ceil}}$.
- **Testable Check:**
  $$\text{Let } L_{\%} = \frac{P_{\text{spot}} - K_{\text{floor}}}{K_{\text{ceil}} - K_{\text{floor}}}. \quad \text{If } L_{\%} \in (0.25, 0.75) \text{ AND } P_{\text{spot}} \text{ is not at a valid Gatekeeper/S/R flip} \implies \text{ABSTAIN}.$$
- **Doctrine Override Rule:** Even if a `$1M+` options sweep or tall Flow Pane bar prints at the midpoint, **still abstain** (`"Price is sitting in the middle of a range when a large call print appears. What should the trader do? Wait for price to reach the top or bottom of the range."`) `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 3, Q7; Ch. 7, Q22; Ch. 8, Q20]`[cite: 1].

### Gate 2: Rainbow Road Map (`IS_RAINBOW_ROAD`)
- **Definition:** The Heatseeker map displays undefined chaos—multiple positive (yellow) and negative (purple) nodes scattered across strikes with no dominant King Node and no clear Floor or Ceiling `[DOCTRINE: Academy Ch. 5, Sec. 2.VI & Quiz Q7; Docs Patternpedia: Rainbow Road]`[cite: 1].
- **Inputs:** Strike-by-strike exposure array $\{ (K_i, \text{GEX}_i) \}$.
- **Testable Check:** If the top 4–6 nodes on the board have comparable absolute magnitudes (e.g., $\vert{}\text{GEX}_{\text{rank } 4}\vert{} / \vert{}\text{GEX}_{\text{King}}\vert{} > 0.70$ `[INFERENCE]`) and alternate in sign (`+GEX`, `-GEX`, `+GEX`, `-GEX`) without a dominant boundary node $\implies \text{ABSTAIN}$ `[DOCTRINE: Academy Ch. 5, Sec. 2.VI: "This is: No-trade conditions... Stand down and wait for clarity"]`[cite: 1].
- *(Note on Quiz Q21 in Ch. 6: One quiz question in Ch. 6 (`a0000006-...-21`) lists `"Wait for pullbacks and enter on micro-deflections"` as an option for Rainbow Road, whereas Ch. 5 Section 2.VI, Ch. 5 Quiz Q7, and Patternpedia explicitly state Rainbow Road is `"No-trade conditions / Stand down and wait for clarity"` `[DOCTRINE: Academy Ch. 5, Sec. 2.VI & Q7; Ch. 6, Q21]`[cite: 1]. Both are logged in `open_questions.md`; the safe agent default is **ABSTAIN**.)*

### Gate 3: Trinity Total Misalignment / Divergence (`IS_TRINITY_DIVERGENT`)
- **Definition:** Fewer than 2 of the 3 Trinity indices (`SPX`, `SPY`, `QQQ`) align with the trade thesis (e.g., SPX points up, QQQ points down, SPY is pinned = `Type 3: Whipsaw` or `Scenario C: Divergence`) `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 5, Sec. 2.V; Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5]`[cite: 1].
- **Inputs:** Directional/structural thesis read for `SPX`, `SPY`, and `QQQ`.
- **Testable Check:** If `Aligned_Trinity_Count <= 1` $\implies \text{ABSTAIN}$ (`"1 of 3 or fewer (total misalignment) — no trade"`) `[DOCTRINE: Academy Flowseeker Ch. 7, Sec. 5; Ch. 13, Quiz Q8]`[cite: 1]. (Exception: In a defined Whipsaw range, only experienced traders fading the extreme outer boundary when all 3 hit simultaneous range edges may participate; otherwise sit out `[DOCTRINE: Academy Ch. 4, Sec. 3 Type 3; Ch. 5, Sec. 2.V]`[cite: 1].)

### Gate 4: Missing Chart Structure (`NO_CHART_STRUCTURE`)
- **Definition:** A Heatseeker node or pattern exists, or a large options flow print occurs, but there is no supporting chart structure (Support, Resistance, Swing High/Low, Range Extreme, Double Bottom/Top, or S/R Flip) at that price `[DOCTRINE: Academy Ch. 1, Sec. 1 & 5; Ch. 5, Sec. 4 Step 1 & Sec. 5]`[cite: 1].
- **Testable Check:** If `Chart_Thesis_At_Level == NONE` $\implies \text{ABSTAIN}$[cite: 1].

### Gate 5: Missing Heatseeker Confluence (`NO_MAP_CONFLUENCE`)
- **Definition:** Chart structure shows a support or resistance level, and options flow may even look favorable, but the Heatseeker map shows no structural node (e.g., only an empty air pocket) at that level `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 2, Quiz Q5]`[cite: 1].
- **Testable Check:** If `Node_At_Chart_Level == NONE` $\implies \text{ABSTAIN}$[cite: 1].

### Gate 6: Exhausted / Delivered / 3rd+ Tap Node (`IS_NODE_EXHAUSTED`)
- **Definition:** The target node has already been tested twice ($N_{\text{taps}} \ge 2$, entering its 3rd+ tap at ~33% probability) or has already delivered a move earlier in the session (`delivered` / `spent`) `[DOCTRINE: Academy Ch. 6, Sec. 6 & 12; Ch. 9, Sec. 2 & 3; Talon Prompt Guide, Sec. 4]`[cite: 1].
- **Testable Check:** If `Node_Taps >= 2` OR `Lifecycle_Stage IN ('delivered', 'spent', 'decaying')` $\implies \text{ABSTAIN from fading the node}$[cite: 1].

### Gate 7: Fading Active Velocity in an Air Pocket (`IS_FADING_ACTIVE_VELOCITY`)
- **Definition:** Price is moving through an Air Pocket or Liquidity Vacuum while Velocity Mode (`% change`) shows rapid dealer accumulation in the direction of the move `[DOCTRINE: Academy Ch. 8, Sec. 9 & 11]`[cite: 1].
- **Testable Check:** If `In_Air_Pocket == TRUE` AND `Velocity_In_Move_Direction == HIGH` $\implies \text{ABSTAIN from counter-trend fade}$ (`"1. Do NOT Fade Velocity. If price is: In an air pocket AND accelerating, You are stepping in front of: Dealer flow"`) `[DOCTRINE: Academy Ch. 8, Sec. 9]`[cite: 1].

### Gate 8: Chasing Momentum / Missed Direct-Tap Entry (`IS_CHASING_MOVE`)
- **Definition:** Price already tapped the node and moved away outside the Deflection Zone, or is mid-flight inside an air pocket `[DOCTRINE: Academy Ch. 4, Sec. 3 Type 2; Ch. 6, Sec. 3 & 12; Ch. 8, Sec. 3 & 9]`[cite: 1].
- **Testable Check:** If $\vert{}P_{\text{spot}} - K_{\text{entry}}\vert{} > \delta_{\text{deflect}}$ (and not in a valid negative-gamma overshoot stall) $\implies \text{ABSTAIN / WAIT}$ (`"If missed entry point, sit out until clear pivot appears - like a king node price target getting hit."`) `[DOCTRINE: Academy Ch. 4, Sec. 3 Type 2]`[cite: 1].

### Gate 9: Fighting Rolling Structure (`IS_FIGHTING_ROLLING_MAP`)
- **Definition:** Attempting to hold or enter a bearish trade while Floors are rolling up (downside targets shrinking), or attempting to hold or enter a bullish trade while Ceilings are rolling down (upside targets shrinking) `[DOCTRINE: Academy Ch. 7, Sec. 1, 3, 6; Ch. 12, Sec. 3]`[cite: 1].
- **Testable Check:**
  - If `Thesis == BEARISH` AND `Floor_Rolling_Up == TRUE` $\implies \text{ABSTAIN}$[cite: 1].
  - If `Thesis == BULLISH` AND `Ceiling_Rolling_Down == TRUE` $\implies \text{ABSTAIN}$[cite: 1].

### Gate 10: Unresolved Price-vs-Flow Conflict (`HAS_UNRESOLVED_FLOW_CONFLICT`)
- **Definition:** Chart and Heatseeker point one way at a level, while significant same-expiry options flow at that level points the opposite way (e.g., $4M of 2-DTE ask-side calls printing into a twice-tested King Node ceiling), and no separating evidence has resolved which of the 5 explanations applies `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5; Ch. 10, Sec. 2 & 3]`[cite: 1].
- **Testable Check:** If `Price_Map_Bias != Flow_At_Level_Bias` AND `Conflict_Resolved_By_Price_Or_OI == FALSE` $\implies \text{WAIT / PASS}$ (or trade at reduced size only after a clean rejection with flow fading) `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5]`[cite: 1].

### Gate 11: Insufficient Reward-to-Risk (`IS_RR_BELOW_MINIMUM`)
- **Definition:** The distance from current price to the next structural target divided by the distance to the invalidation stop is below the required minimum (`3:1` standard) `[DOCTRINE: Academy Ch. 3, Sec. 6; Ch. 6, Sec. 9; Flowseeker Ch. 10, Sec. 3]`[cite: 1].
- **Testable Check:** If $\text{R:R} < 3.0$ $\implies \text{WAIT for pullback to } P_{\text{limit}} \text{ or PASS}$ `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 3 & 6]`[cite: 1].

### Gate 12: Missing Concrete Invalidation (`NO_WEAKENS_IF`)
- **Definition:** The Playbook hypothesis lacks a specific, checkable `"Weakens If"` condition (e.g., a specific price level lost, a node shrinking, or OI dropping) `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 5; Ch. 7, Sec. 5; Ch. 10, Sec. 3]`[cite: 1].
- **Testable Check:** If `Playbook.Weakens_If == NULL` or non-numeric/non-checkable $\implies \text{ABSTAIN}$[cite: 1].

---

## Worked Example (`[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 6 Case Study]`)
- **Event:** A `$1.4M` QQQ call print fills at the ask[cite: 1].
- **Gate Evaluation:**
  - Chart: QQQ is at its **Range High** (rejected twice this week) $\rightarrow$ Bearish extreme[cite: 1].
  - Map: Heavy Heatseeker node sits just above price acting as a **Ceiling**, with little exposure below until the midpoint $\rightarrow$ Challenges upside thesis[cite: 1].
  - Flow: Large call into a range high beneath a ceiling $\rightarrow$ **Unresolved Conflict** (`Gate 10` = `TRUE` for shorts; `Gate 4` & `Gate 5` = `TRUE` against buying calls into a ceiling)[cite: 1].
- **Decision:** **`NO_TRADE`** (Wait until acceptance above the ceiling or clean rejection) `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 6]`[cite: 1].

---

## Agent Checklist (`no_trade_conditions.md`)

1. **Run All 12 Gates as Pre-Execution Interlocks:** Evaluate `Gates 1–12` in series; if `ANY(Gate_1 .. Gate_12) == TRUE`, block order submission and log the exact gate ID that triggered the abstain[cite: 1].
2. **Archive Skipped Trades for Weekly Audit:** Whenever a setup is blocked by `Gate 10` (Conflict) or `Gate 11` (R:R), save the snapshot to the review log so the weekly review can verify whether the pass filter worked properly `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 4 & 5]`[cite: 1].