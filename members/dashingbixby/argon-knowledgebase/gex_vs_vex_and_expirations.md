# gex_vs_vex_and_expirations.md

- **Title:** GEX vs. VEX Mechanics & Expiration Scoping (`0DTE` vs. `Swing`, `Current + Next N`, and the `Entry` Column)
- **Scope:** Zero-assumption definitions, Greek sensitivities, expiration selection rules, the pinned `Entry` column, VEX pull "armed" status, and multi-expiration Stairstepping rules.
- **Sources Used:** SRC-02 (Academy Atlas Course, Sec. 1, 3, 4, 5, 9, 10)[cite: 1], SRC-11 (Academy Ch. 9, Sec. 5–8)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 3 & 5)[cite: 1], SRC-29 (Docs Heatseeker & Atlas Field Guides), SRC-30 (Changelog Oct 13, 2026).
- **Prerequisites:** `00_index.md`, `glossary.md`, `heatseeker_node_core_attributes.md`
- **Last Updated:** 2026-10-17

---

## 1. GEX (Net Gamma Exposure) vs. VEX (Net Vanna Exposure)

### 1.1 Definitions
- **Plain sentence:** **GEX (Gamma Exposure)** measures how much dealers must buy or sell the underlying asset when **price** moves (making it the primary driver for same-day / 0DTE trading and intraday entries), while **VEX (Vanna Exposure)** measures how much dealers must adjust their hedges when **implied volatility (IV) or time** changes (making it the primary driver for multi-day swing targets and days when volatility spikes) `[DOCTRINE: Academy Atlas Course, Sec. 9 & 10; Docs Heatseeker Field Guide]`[cite: 1].
- **Precise version:**
  1. **GEX ($\Gamma$ Exposure):**
     - **Greek Sensitivity:** $\Gamma = \frac{\partial \Delta}{\partial S}$ (rate of change of option delta with respect to underlying spot price $S$) `[GENERAL TA]`.
     - **Tenor Dominance:** Gamma increases rapidly for near-the-money strikes as time to expiration approaches zero ($T \to 0$). Therefore, the **`Current` (0DTE / Front)** expiration column dominates intraday dealer hedging `[DOCTRINE: Academy Atlas Course, Sec. 5 & 9]`[cite: 1].
     - **Primary Role in Skylit:** Intraday support (Floors), resistance (Ceilings), pinning (King Node), and timing **intraday deflection entries** `[DOCTRINE: Academy Atlas Course, Sec. 9 & 10]`[cite: 1].
  2. **VEX (Vanna Exposure):**
     - **Greek Sensitivity:** $\text{Vanna} = \frac{\partial \Delta}{\partial \sigma} = \frac{\partial \mathcal{V}}{\partial S}$ (rate of change of option delta with respect to implied volatility $\sigma$, or vega with respect to spot $S$) `[GENERAL TA]`.
     - **Tenor Dominance:** Vanna is strongest on contracts with more time remaining (`~5 days and out`, weekly and monthly OPEX columns) and becomes highly active when implied volatility expands or contracts sharply `[DOCTRINE: Docs Heatseeker Field Guide; Academy Atlas Course, Sec. 10]`[cite: 1].
     - **Primary Role in Skylit:** Providing **macro directional context** and **bigger-picture swing price targets** `[DOCTRINE: Academy Atlas Course, Sec. 10; Ch. 9, Sec. 7 & Quiz Q6]`[cite: 1].

| Dimension | GEX (Net Gamma Exposure) | VEX (Net Vanna Exposure) | Source Tag |
| :--- | :--- | :--- | :--- |
| **Primary Driver** | Underlying Spot Price ($S$) movement | Implied Volatility ($\sigma$) & Multi-Day Time Decay | `[DOCTRINE: Docs Heatseeker Field Guide]` |
| **Dominant Horizon** | Intraday / 0DTE (`Current` / `Front` expiration)[cite: 1] | Swing / Position (`~5 DTE and out`, `Current + Next N`)[cite: 1] | `[DOCTRINE: Academy Atlas Sec. 9–10; Docs Heatseeker Guide]`[cite: 1] |
| **Role in Swing Trading** | **Used for intraday entries** (via the pinned `"Entry"` column)[cite: 1] | **Used for bigger-picture price targets & directional pressure**[cite: 1] | `[DOCTRINE: Academy Atlas Sec. 10; Changelog Oct 13, 2026]`[cite: 1] |
| **Role in Scalp / 0DTE** | Primary structural map for floors, ceilings, and King Nodes[cite: 1] | Can be overlaid on Scalp maps **labeled strictly as context** | `[DOCTRINE: Changelog Oct 13, 2026]` |

---

## 2. Expiration Scoping: `Current` vs. `Current + Next N` & The `"Entry"` Column

### 2.1 Why Expiration Selection Changes the Map
- On Heatseeker, each vertical column represents a distinct contract expiration date (`Current` / today, next expiry, two expiries out, monthly OPEX, etc.) `[DOCTRINE: Academy Atlas Course, Sec. 3]`[cite: 1].
- On Atlas, the **Expiration dropdown** controls which expiration columns are aggregated into the Orbs rendered on the price chart `[DOCTRINE: Academy Atlas Course, Sec. 3–5]`[cite: 1].
- **Crucial Rule:** *"Changing the expiration setting does not change the data. It changes which expiration data is being displayed on your chart... Do not assume every king node matters equally to every trade."* `[DOCTRINE: Academy Atlas Course, Sec. 5]`[cite: 1].
  - *Example 1:* This week's King Node orb may sit at the `200` strike, while July OPEX's King Node sits `$20` higher at `220` `[DOCTRINE: Academy Atlas Course, Sec. 1]`[cite: 1].
  - *Example 2 (`NVDA`):* NVDA may have a massive King Node at `185` three weeks out, while the `Current` expiration column has its King Node centered near spot price and shows only a tiny node at `185` `[DOCTRINE: Academy Atlas Course, Sec. 4 & 5]`[cite: 1].

### 2.2 Operational Mapping by Trading Style

| Trading Style | Expiration Setting on Atlas / Heatseeker | Node Count (`Top N`) | Operational Rationale | Source Tag |
| :--- | :--- | :--- | :--- | :--- |
| **0DTE Scalping** | **`Current`** (Front expiration only)[cite: 1] | **Top 3 to 5 nodes** recommended[cite: 1] | Gamma becomes dominant approaching expiration; dealers are actively hedging today's contracts, not a node 3 days or 3 weeks out[cite: 1]. | `[DOCTRINE: Academy Atlas Course, Sec. 5 & 9]`[cite: 1] |
| **Intraday Trading** | Start with **`Current`**, then compare nearby expirations if needed[cite: 1] | Top 3 to 5 nodes[cite: 1] | Anchors on today's active hedging while checking if next-day positioning reinforces key strikes[cite: 1]. | `[DOCTRINE: Academy Atlas Course, Sec. 5]`[cite: 1] |
| **Swing / Position Trading** | **`Current + Next N`** (e.g., up to 5 expirations on Heatseeker)[cite: 1] | Top N across multi-expiration structure | Larger institutional positioning builds several days or weeks out; Swing/Position maps always pin the nearest expiration as an **`"Entry"` column**[cite: 1]. | `[DOCTRINE: Academy Atlas Sec. 5 & 10; Docs Heatseeker Guide; Changelog Oct 13, 2026]`[cite: 1] |

### 2.3 The Oct 2026 Pinned `"Entry"` Column & VEX Pull Status
1. **Pinned `"Entry"` Column on Swing/Position Maps:**
   - In the October 13, 2026 update, Talon and Heatseeker Swing and Position maps **always show the nearest expiration column labeled as `"Entry"`** alongside the further-dated expiration columns `[DOCTRINE: Changelog Oct 13, 2026]`.
   - This enforces the Atlas Section 10 rule in the UI: swing traders read the further-dated VEX/GEX columns for their **Target**, and read the front `"Entry"` column for their **intraday GEX deflection entry** `[DOCTRINE: Academy Atlas Course, Sec. 10; Changelog Oct 13, 2026]`[cite: 1].
2. **Scalp VEX Context Toggle:**
   - The heatmap and Talon's `Hero's Map` tab can also display VEX on a `Scalp` board, where it is explicitly **labeled as context** so scalpers do not confuse a macro VEX node with an immediate 0DTE GEX wall `[DOCTRINE: Changelog Oct 13, 2026]`.
3. **VEX Pull `"Armed"` Status:**
   - On Talon's `Bull vs Bear` tab, VEX pull rows explicitly state **whether a VEX pull is armed** `[DOCTRINE: Changelog Oct 13, 2026]`. *(Note: The exact mathematical threshold that flips a VEX pull row from unarmed to `"armed"` is `[NOT SPECIFIED]` in public docs; agents reading Talon output should parse the boolean `armed` state directly; see `open_questions.md`.)*

---

## 3. Procedure: Validating a VEX Target vs. a Hedge Node Trap

In Chapter 9, Section 7 (`"VEX — Context and Confluence"`), Skylit warns against the #1 mistake traders make with VEX and multi-expiration maps: seeing a large far-OTM node, assuming price will teleport there, and getting burned by theta `[DOCTRINE: Academy Ch. 9, Sec. 7 & 9]`[cite: 1].

### Step-by-Step Target Validation Procedure
1. **Locate Candidate VEX / Multi-Expiry Target $K_{\text{VEX}}$:** Identify the largest node(s) on the VEX map or `Current + Next N` columns `[DOCTRINE: Academy Ch. 9, Sec. 7]`[cite: 1].
2. **Run the Growth Check (`Real Node` vs. `Hedge Node`):**
   - Monitor $\vert{}\text{Exposure}(K_{\text{VEX}}, t)\vert{}$ over time[cite: 1]:
     - If $\vert{}\text{Exposure}(K_{\text{VEX}}, t)\vert{}$ is **static or decaying/fading** $\implies$ Classify as a **`HEDGE_NODE` (Protection / Decoy)**. Do **not** use $K_{\text{VEX}}$ as a directional price target `[DOCTRINE: Academy Ch. 9, Sec. 5 & 7, Quiz Q3, Q4, Q9]`[cite: 1].
     - If $\vert{}\text{Exposure}(K_{\text{VEX}}, t)\vert{}$ is **growing / accumulating** $\implies$ Classify as a **`REAL_NODE` (Intent)** `[DOCTRINE: Academy Ch. 9, Sec. 5 & 7]`[cite: 1].
3. **Run the Structural Pathway Check (`Step-by-Step Delivery`):**
   - Check the intermediate strikes between $P_{\text{spot}}$ and $K_{\text{VEX}}$ across the `"Entry"` (`Current`) and intermediate expiration columns `[DOCTRINE: Academy Ch. 9, Sec. 6 & 8]`[cite: 1].
   - Verify that either:
     - Intermediate nodes are building a **Stairstepping** bridge ($K_1 \to K_2 \to K_{\text{VEX}}$) `[DOCTRINE: Academy Ch. 9, Sec. 8]`[cite: 1], OR
     - An open **Air Pocket** with positive Velocity Mode (`% change`) connects the current structure to $K_{\text{VEX}}$ without an opposing high-magnitude Pika Cloud blocking the way `[DOCTRINE: Academy Ch. 8, Sec. 6; Ch. 9, Sec. 6 & Quiz Q11, Q13]`[cite: 1].
4. **Execute Only on GEX + VEX Confluence:**
   - Wait for intraday price to tap a supportive GEX Floor (for a bullish VEX target) or GEX Ceiling (for a bearish VEX target) on the `"Entry"` (`Current`) column with $\text{R:R} \ge 3:1$ `[DOCTRINE: Academy Atlas Course, Sec. 10]`[cite: 1].

---

## 4. Edge Cases
- **Querying Strike Flow Without Naming an Expiration:** As noted in the Talon Prompt Guide (Section 5), *"A gamma node belongs to one expiry. Without a date, Talon scopes to the nearest expiry traded — a different chain, described with the same confidence."* `[DOCTRINE: Academy Talon Prompt Guide, Sec. 5]`[cite: 1]. Agents querying strike-level flow or exposure for a multi-week VEX node **must** explicitly pass the target expiration date (e.g., `Oct 17 expiry`) `[DOCTRINE: Academy Talon Prompt Guide, Sec. 5 & 8]`[cite: 1].

---

## 5. Worked Example (`[DOCTRINE: Academy Atlas Course, Sec. 4 & 5 — NVDA]`)
- **Inputs:** NVDA is trading intraday near spot. On the `Current` expiration column, the King Node is centered right near spot price, and strike `185` has only a tiny node `[DOCTRINE: Academy Atlas Course, Sec. 4 & 5]`[cite: 1]. Three expirations out (`3 weeks out`), NVDA has a massive King Node at `185` `[DOCTRINE: Academy Atlas Course, Sec. 5]`[cite: 1].
- **Agent Branching:**
  - **If `Strategy == 0DTE_SCALP`:** Scope to `Current`. Trade deflections around the near-spot King Node; ignore `185` today because dealers do not need to aggressively hedge a 3-week-out strike for today's close `[DOCTRINE: Academy Atlas Course, Sec. 5 & 9]`[cite: 1].
  - **If `Strategy == SWING`:** Scope to `Current + Next 3`. Use the 3-week-out `185` King Node (verified for growth and pathway) as the macro target, and use the `Current` (`"Entry"`) column's GEX floor near spot to time the entry `[DOCTRINE: Academy Atlas Course, Sec. 5 & 10; Changelog Oct 13, 2026]`[cite: 1].

---

## 6. What It Is NOT
- VEX is **not** a standalone entry trigger (`"What is the role of VEX in analysis? Directional context - bigger picture price targets"`, not `"Trade entry signal"` or `"Stop loss placement"`) `[DOCTRINE: Academy Ch. 9, Quiz Q6]`[cite: 1].

---

## 7. Related Concepts
- See `heatseeker_node_core_attributes.md` (Attribute 7) and `setup_catalog_fade_gatekeeper_and_vex_swing.md`.

---

## Agent Checklist (`gex_vs_vex_and_expirations.md`)

1. **Enforce Expiration Parameter on Every Query:** Never query a node or strike flow without explicitly binding `Expiration_Scope` (`Current` for 0DTE; specific `YYYY-MM-DD` or `Current + Next N` for swing)[cite: 1].
2. **Separate Entry Greek from Target Greek on Swings:** Bind `Entry_Trigger = GEX(Current_Expiry)` and `Macro_Target = VEX_or_GEX(Target_Expiry)`[cite: 1].
3. **Reject Static Far-OTM VEX Nodes:** Require `d(Exposure)/dt > 0` (`Real Node`) and a clear intermediate pathway before accepting a distant VEX node as a valid target[cite: 1].