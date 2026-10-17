# setup_catalog_fade_gatekeeper_and_vex_swing.md

- **Title:** Setup Catalog — Fade at Structural Extreme (Direct-Tap Deflection), Gatekeeper Checkpoint & VEX / Stairstepping Swing
- **Scope:** Zero-assumption definitions, inputs, detection procedures, execution triggers, stop/target rules, edge cases, and worked examples for standard Floor/Ceiling/King Node Deflections, Gatekeeper interactions, and Multi-Expiration VEX / Stairstepping Swing setups.
- **Sources Used:** SRC-02 (Academy Atlas Course, Sec. 5, 9, 10)[cite: 1], SRC-05 (Academy Ch. 3, Sec. 4–8)[cite: 1], SRC-08 (Academy Ch. 6, Sec. 3–12)[cite: 1], SRC-11 (Academy Ch. 9, Sec. 6–8)[cite: 1], SRC-13 (Academy Ch. 11, Sec. 3 Step 6)[cite: 1], SRC-27 (Docs Core Concepts: King Nodes & Gatekeepers), SRC-28 (Docs Patternpedia: Gatekeeper & Trend), SRC-30 (Changelog Oct 13, 2026: Scalp VEX & Entry Column).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. Fade at Structural Extreme (Direct-Tap Node Deflection)

### 1.1 Definition
- **Plain sentence:** The core Skylit intraday setup: identifying a fresh, high-magnitude Floor, Ceiling, or King Node that aligns with a chart support or resistance extreme, waiting for price to reach that level, entering at the direct tap within the Deflection Zone, placing a stop one node beyond invalidation, and targeting the next high-accumulation node for at least a 3:1 reward-to-risk[cite: 1].
- **Precise version:** For symbol $S$ on the `Current` expiration column, a **Direct-Tap Node Deflection** setup is armed at node $K_{\text{extreme}}$ (where $K_{\text{extreme}}$ is the Floor, Ceiling, or King Node) when:
  1. $K_{\text{extreme}}$ coincides with a graded Chart Support (`AT_RANGE_LOW` / Double Bottom / Fresh Swing Low) or Chart Resistance (`AT_RANGE_HIGH` / Double Top / Fresh Swing High / S/R Flip) `[DOCTRINE: Academy Ch. 1, Sec. 4 & 5; Ch. 6, Sec. 9 & 10]`[cite: 1].
  2. $K_{\text{extreme}}$ is **Fresh** ($N_{\text{taps}} == 0$, ~80% reaction probability) or **Tested Once** ($N_{\text{taps}} == 1$, ~66% reaction probability) `[DOCTRINE: Academy Ch. 6, Sec. 6]`[cite: 1].
  3. Trinity alignment is $\ge 2/3$ (`SPX`, `SPY`, `QQQ`) `[DOCTRINE: Academy Ch. 10, Sec. 6]`[cite: 1].
  4. Distance to the target node $K_{\text{target}}$ vs. stop node $K_{\text{stop}} = K_{\text{extreme}} \mp \Delta K_{\text{node}}$ yields $\text{R:R} \ge 3.0$ `[DOCTRINE: Academy Ch. 6, Sec. 9 & 10]`[cite: 1].

### 1.2 Inputs
- Chart structure zones from `chart_structure_fundamentals.md`.
- Heatseeker `Current` expiration GEX nodes, tap counts, and Velocity Mode `% change`.
- Trinity maps (`SPX`, `SPY`, `QQQ`).
- Optional: Flowseeker / Atlas Flow Pane and Dark Pool overlay levels[cite: 1].

### 1.3 Procedure
1. **Verify Preconditions (Steps 1–4 above).**
2. **Calculate Deflection Zone Entry Band:**
   - For `SPY` or `QQQ`: $[K_{\text{extreme}} - \$0.50, K_{\text{extreme}} + \$0.50]$ `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1].
   - For `SPX`: $[K_{\text{extreme}} - \$5.00, K_{\text{extreme}} + \$5.00]$ `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1].
3. **Execute Entry:**
   - If $\text{GEX}(K_{\text{extreme}}) > 0$ (Yellow / Pika): Enter immediately upon the **direct tap** inside the Deflection Zone as panic liquidity floods the level (`"Cheap Fill"`) `[DOCTRINE: Academy Ch. 6, Sec. 3, 4, 8]`[cite: 1].
   - If $\text{GEX}(K_{\text{extreme}}) < 0$ (Purple / Barney): Apply the `Beach Ball` rule (wait for overshoot + stall + rejection wick if momentum is expanding rapidly) `[DOCTRINE: Academy Ch. 6, Sec. 8]`[cite: 1].
4. **Manage Stop & Target:**
   - **Stop Loss:** Break and hold beyond $K_{\text{stop}} = K_{\text{extreme}} \mp \Delta K_{\text{node}}$ (1 node beyond invalidation) `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10]`[cite: 1].
   - **Take Profit:** Deliver node-to-node into $K_{\text{target}}$ as the map reshuffles in favor of the trade `[DOCTRINE: Academy Ch. 6, Sec. 5 & 11]`[cite: 1].

---

## 2. Gatekeeper Checkpoint (Deflection vs. Reclaim/Break)

### 2.1 Definition
- **Plain sentence:** A Gatekeeper is a medium-sized node sitting between spot/floor and a larger target node (like the King Node); it acts like a "bouncer at a nightclub door" that either deflects price back into the previous range (especially early in the day) or, once cleared and reclaimed, opens the pathway through an air pocket to the King Node[cite: 1].
- **Precise version:** Given two major structural nodes $K_A$ and $K_B$ (e.g., Floor $K_A$ and King Node $K_B$) with $K_A < K_{\text{GK}} < K_B$, strike $K_{\text{GK}}$ is a **Gatekeeper Node** if:
  1. $0 < |\text{GEX}(K_{\text{GK}})| < \min(|\text{GEX}(K_A)|, |\text{GEX}(K_B)|)$, yet $|\text{GEX}(K_{\text{GK}})|$ stands out significantly above surrounding air-pocket strikes `[DOCTRINE: Academy Ch. 3, Sec. 7]`[cite: 1].
  2. **Dual Behavioral Modes:**
     - **Mode A — Gatekeeper Deflection (Rejection Checkpoint):** When price approaches a fresh Gatekeeper (particularly early in the session, or when backed by chart S/R), the Gatekeeper blocks continuation and deflects price back toward $K_A$ `[DOCTRINE: Academy Ch. 3, Sec. 7 & Quiz Q6; Ch. 6, Sec. 3 (660 SPY gatekeeper put example); Docs Core Concepts: Gatekeepers]`[cite: 1].
     - **Mode B — Gatekeeper Reclaim (Pathway Unlock):** If price clears and reclaims the Gatekeeper (especially when negative gamma nodes or an air pocket sit just beyond it), price transitions into the next exposure zone and accelerates toward $K_B$ `[DOCTRINE: Academy Ch. 3, Sec. 7; Ch. 11, Sec. 3 Step 6]`[cite: 1].

### 2.2 Inputs
- Heatseeker node array $\{ (K_i, \text{GEX}_i, \text{Taps}_i, \text{Velocity}_i) \}$.
- Time of day $t_{\text{now}}$ (Core Concepts notes Gatekeepers frequently reject price early in the session before price can reach the King Node `[DOCTRINE: Docs Core Concepts]`).

### 2.3 Procedure
1. **Identify Gatekeeper $K_{\text{GK}}$** sitting between current price/floor and the King Node/ceiling[cite: 1].
2. **Evaluate for Mode A (Gatekeeper Fade):**
   - If $K_{\text{GK}}$ is fresh ($N_{\text{taps}} == 0$), aligns with chart S/R, and Trinity peers are also hitting ceilings/floors: play the **direct-tap deflection** off $K_{\text{GK}}$ back toward $K_A$, with stop 1 node beyond $K_{\text{GK}}$ (e.g., shorting `660` SPY Gatekeeper with stop on a break and hold above `661`) `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1].
3. **Evaluate for Mode B (Gatekeeper Reclaim / Target Delivery):**
   - If $K_{\text{GK}}$ is tested repeatedly or reclaimed while Velocity Mode builds toward $K_B$ and negative gamma nodes / air pockets sit immediately beyond $K_{\text{GK}}$: *"If we reclaim those gatekeepers, we can see an aggressive bounce because of those negative gamma nodes justly above spot."* `[DOCTRINE: Academy Ch. 11, Sec. 3, Step 6]`[cite: 1]. Do **not** fade $K_{\text{GK}}$; use the reclaim/deflection on the new side to target $K_B$ `[INFERENCE from Academy Ch. 3, Sec. 7 & Ch. 11, Step 6]`[cite: 1].

---

## 3. VEX Swing & Stairstepping Trend Setup

### 3.1 Definition
- **Plain sentence:** A multi-day or multi-week swing setup where a trader uses multi-expiration **VEX (Vanna Exposure)** and week-over-week **Stairstepping** nodes to identify macro price targets, while using front-expiration **GEX** (the pinned `"Entry"` column) to time the intraday entry off a structural deflection[cite: 1].
- **Precise version:** For a swing horizon ($DTE \ge 5$ days `[DOCTRINE: Docs Heatseeker Field Guide]`), a **VEX / Stairstepping Swing Setup** exists when:
  1. Atlas/Heatseeker is configured to **`Current + Next N` expirations** (or Talon's `Swing` / `Position` board, which pins the nearest expiration as the `"Entry"` column) `[DOCTRINE: Academy Atlas Sec. 5 & 10; Changelog Oct 13, 2026]`[cite: 1].
  2. **Macro Target Validation:** A major VEX/GEX node across upcoming expirations is **growing in magnitude over time** (`Real Node`, not a static/decaying far-OTM `Hedge Node`) AND intermediate strikes form a **structural pathway** or week-over-week **Stairstepping** pattern ($K_{\text{week } 1} < K_{\text{week } 2} < K_{\text{week } 3}$ for bullish; descending for bearish) `[DOCTRINE: Academy Ch. 9, Sec. 5, 7, 8]`[cite: 1].
  3. **GEX + VEX Confluence Entry:** Intraday price pulls back to a fresh GEX Floor (or Ceiling) on the `Current` / `"Entry"` column that aligns with higher-timeframe (`1D`/`1H`) chart structure `[DOCTRINE: Academy Atlas Sec. 10: "1. GEX is used for intraday entries, 2. VEX is used for bigger picture price targets. It is typically good practice to trade when GEX and VEX are in confluence."]`[cite: 1].

### 3.2 Inputs
- Multi-expiration GEX and VEX matrices (`Current` + next 3–5 weekly/OPEX expirations) `[DOCTRINE: Academy Atlas Sec. 3–5, 10]`[cite: 1].
- Node growth history over trailing sessions (to verify `Real Node` growth vs. `Hedge Node` decay) `[DOCTRINE: Academy Ch. 9, Sec. 5 & 7]`[cite: 1].
- Talon `Bull vs Bear` VEX pull status (`armed` vs. not armed) `[DOCTRINE: Changelog Oct 13, 2026]`.

### 3.3 Procedure
1. **Scan Multi-Expiration Map (`Current + Next N`):**
   - Check if dominant nodes across sequential weekly columns step progressively higher (Bullish Stairstepping) or lower (Bearish Stairstepping) `[DOCTRINE: Academy Ch. 9, Sec. 8]`[cite: 1].
   - Check the VEX map for macro directional pressure and verify in Talon's `Bull vs Bear` tab whether a **VEX pull is armed** `[DOCTRINE: Academy Ch. 9, Sec. 7; Changelog Oct 13, 2026]`[cite: 1].
2. **Filter Out Hedge Node Traps:**
   - If a large far-OTM node on VEX/GEX is **not growing** (decaying or static) and has **no intermediate pathway nodes** between spot and the distant strike, classify it as a `HEDGE_NODE_TRAP` and discard it as a target `[DOCTRINE: Academy Ch. 9, Sec. 5, 6, 7 & Quiz Q5, Q9, Q11]`[cite: 1].
3. **Time the Intraday Entry via the `"Entry"` (`Current`) Column:**
   - Switch focus to the front expiration (`Current` / `"Entry"` column) GEX map and `1H`/`1D` chart structure `[DOCTRINE: Academy Atlas Sec. 10; Changelog Oct 13, 2026]`[cite: 1].
   - Enter on a direct-tap GEX Floor deflection (for bullish VEX/Stairstepping) or GEX Ceiling deflection (for bearish VEX/Stairstepping) with $\text{R:R} \ge 3.0$[cite: 1].
   - Deliver price step-by-step (`Node -> Air Pocket -> Node`) toward the multi-expiration VEX target `[DOCTRINE: Academy Ch. 9, Sec. 4 & 6]`[cite: 1].

---

## 4. Edge Cases
- **Expiration Mismatch Between 0DTE and Swing (`NVDA 185` Example):** NVDA may have a massive King Node at `185` three weeks out, while the `Current` expiration column shows only a tiny node at `185` and has its current King Node near spot `[DOCTRINE: Academy Atlas Sec. 4 & 5]`[cite: 1]. A 0DTE scalper must **not** trade off the 3-week-out `185` node, whereas a swing trader includes the 3-week-out expiration to see the larger positioning structure developing `[DOCTRINE: Academy Atlas Sec. 5]`[cite: 1].
- **Stale Far-OTM Targets Causing Theta Burn:** Entering short-dated options targeting a distant OTM node without checking intermediate Gatekeepers/Pika Clouds and node growth results in severe theta burn `[DOCTRINE: Academy Ch. 9, Sec. 9 & Quiz Q5]`[cite: 1].

---

## 5. Worked Examples
- **Gatekeeper Deflection (`[DOCTRINE: Academy Ch. 6, Sec. 3]`):** SPY approaches a Gatekeeper node at `660`. The trader enters puts inside the deflection zone (`659.50–660.50`) and places the stop loss on a break and hold above `661` (one node above the `660` Gatekeeper)[cite: 1].
- **Gatekeeper Bounce (`[DOCTRINE: Academy Ch. 11, Sec. 3, Step 3–5]`):** QQQ sells off into a Gatekeeper node at `593` while SPY downside is limited by a nearby King Node and SPX supports upside; QQQ overshoots `593` slightly and bounces back into the upside air pocket[cite: 1].
- **Stairstepping Trend (`[DOCTRINE: Academy Ch. 9, Sec. 8]`):** Across sequential weekly expiration columns on the heatmap, major nodes stack at progressively higher strikes each week while floors rise gradually and ceilings are reclaimed step-by-step, confirming structural uptrend formation[cite: 1].

---

## 6. What They Are NOT
- **VEX** is **not** an exact intraday entry trigger or stop-loss placement tool; it provides **directional context and bigger-picture price targets** (`[DOCTRINE: Academy Ch. 9, Quiz Q6]`)[cite: 1].
- **Stairstepping** is **not** price action teleporting to a distant strike; price must still be delivered through intermediate structure node-by-node `[DOCTRINE: Academy Ch. 9, Sec. 6 & 8]`[cite: 1].

---

## 7. Related Concepts
- See `gex_vs_vex_and_expirations.md` for full GEX vs. VEX math and expiration settings.
- See `entries_stops_targets_and_rr.md` for deflection zone and stop rules.

---

## Agent Checklist (`setup_catalog_fade_gatekeeper_and_vex_swing.md`)

1. **Match Expiration Column to Strategy Horizon:** Use `Current` for 0DTE scalps; use `Current + Next N` (with `Current` as the `"Entry"` column) for swings[cite: 1].
2. **Validate Distant Targets Before Arming Swing Trades:** Confirm `Node_Growth > 0` (`Real Node`) AND `Intermediate_Pathway_Exists == TRUE` before accepting any far-OTM VEX/GEX node as a target[cite: 1].
3. **Require GEX + VEX Confluence on Swings:** Only trigger a swing entry when macro VEX direction aligns with an intraday GEX node deflection at chart structure[cite: 1].