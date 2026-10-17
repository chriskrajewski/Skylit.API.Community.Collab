# heatseeker_node_core_attributes_gaps.md

- **Title:** Gap Analysis & Audit of `heatseeker_node_core_attributes.md`
- **Scope:** Line-by-line audit of the first previously generated reference file (`heatseeker_node_core_attributes.md`) identifying undefined trading terms, un-stepped procedures, unsourced numbers, and October 2026 platform updates, with direct links to the new knowledge-base files that resolve each gap[cite: 1, 2].
- **Sources Used:** `heatseeker_node_core_attributes.md`[cite: 2], SRC-01 through SRC-31[cite: 1].
- **Prerequisites:** `00_index.md`, `heatseeker_node_core_attributes.md`[cite: 2]
- **Last Updated:** 2026-10-17

---

## 1. Terms Used Without Operational Definitions

In `heatseeker_node_core_attributes.md`, several foundational chart and market-structure concepts were referenced as preconditions without explaining to a naive agent how to compute or detect them:

| Undefined Term in Original File | Why an Agent Could Not Execute It | Where It Is Now Fully Defined & Operationalized |
| :--- | :--- | :--- |
| **"Charts First Doctrine: Always establish chart structure (support/resistance, range extremes, swing points)"** (Agent Execution Rule #1) | Did not define what a candlestick is, which timeframe to inspect (`1D` vs `1H` vs `5m`), how many bars confirm a swing high/low, how to grade fresh vs. degraded S/R levels, or the two exceptions (**Double Bottoms** and **S/R Flips**)[cite: 1]. | **`chart_structure_fundamentals.md`** (Sections 1–4) & **`charts_first_workflow.md`** (Steps 1–2). |
| **"Midpoint: The equilibrium space roughly halfway between a major floor and ceiling"** (Section 3) | Did not define the mathematical boundaries ($L_{\%} \in (0.25, 0.75)$) that separate a tradeable "Extreme" from an untradeable "Midpoint"[cite: 1]. | **`chart_structure_fundamentals.md`** (Section 3.3) & **`no_trade_conditions.md`** (Gate 1). |
| **"Break and hold of 1 strike/node above a ceiling or below a floor"** (Agent Execution Rule #3) | Did not define the difference between an intrabar wick ("Beach Ball overshoot") and a confirmed "break and hold" ("price acceptance")[cite: 1]. | **`entries_stops_targets_and_rr.md`** (Section 3.3) & **`open_questions.md`** (`OQ-11`). |
| **"GEX vs. VEX"** (Section 7) | Defined the concepts qualitatively but did not include the October 2026 UI mechanics: the pinned **`"Entry"` column** on Swing/Position maps, VEX on Scalp maps labeled as context, or **VEX pull `"armed"`** status in Talon. | **`gex_vs_vex_and_expirations.md`** (Sections 1–3) & **`platform_reference_atlas_heatseeker_talon.md`**. |

---

## 2. Procedures Stated Without Step-by-Step Algorithms

| Incomplete Procedure in Original File | Missing Procedural Steps | Where the Full Step-by-Step Procedure Now Lives |
| :--- | :--- | :--- |
| **Identifying Node Hierarchy (`King`, `Floor`, `Ceiling`, `Gatekeeper`)** (Section 3) | Listed definitions, but omitted the 6-step sequential map-reading workflow from Academy Ch. 3, Sec. 9 and the quantitative bounds separating a Gatekeeper from an Air Pocket[cite: 1]. | **`charts_first_workflow.md`** (Step 3), **`setup_catalog_fade_gatekeeper_and_vex_swing.md`** (Section 2), & **`open_questions.md`** (`OQ-06`, `OQ-07`). |
| **Distinguishing `Real Nodes` from `Hedge Nodes`** (Section 6) | Stated that Real Nodes grow and Hedge Nodes decay, but lacked the step-by-step target validation algorithm (checking `d(Exposure)/dt > 0` + intermediate structural pathway + macro event calendar check for FOMC/CPI/NFP/earnings)[cite: 1]. | **`gex_vs_vex_and_expirations.md`** (Section 3) & **`session_timing.md`** (Section 4). |
| **Executing Composite Patterns (`Rug`, `Reverse Rug`, `Beach Ball`, `Pika Clouds`)** (Composite Patterns Section) | Summarized each pattern in 2 sentences without preconditions, triggers, entry tolerances, invalidation stops, or friction interaction checks[cite: 1]. | **`setup_catalog_rug_and_reverse_rug.md`** & **`setup_catalog_beach_ball_and_pika_cloud.md`**. |

---

## 3. Numbers & Probabilities Lacking Epistemic Context or Source Tags

1. **Tap Reaction Probabilities (`~80%`, `~66%`, `~33%` in Section 4):**
   - *Gap in Original File:* Presented `~80%`, `~66%`, and `~33%` without citing `Academy Ch. 6, Sec. 6` or `Ch. 13, Q11`, and without explaining whether they are empirical backtest statistics or doctrinal heuristics, or cross-referencing the qualitative table in `skylit.ai/docs/core-concepts`[cite: 1].
   - *Filled In:* **`entries_stops_targets_and_rr.md`** (Section 2.1).
2. **Deflection Zone Widths ($\pm\$0.50$ SPY/QQQ, $\pm\$5.00$ SPX in Rule #2):**
   - *Gap in Original File:* Did not cite `Academy Ch. 6, Sec. 3`[cite: 1], did not note the `5–10 point` SPX margin mentioned in the Skylit Learn 0DTE SPX article, and did not address non-Trinity symbols (`OQ-10`).
   - *Filled In:* **`entries_stops_targets_and_rr.md`** (Section 1.1 & 1.4) & **`open_questions.md`** (`OQ-10`, `Discrepancy 3`).
3. **Minimum R:R Standard (`3:1` vs. `2:1` in Rule #4):**
   - *Gap in Original File:* Did not document the inter-source tension between `Heatseeker Ch. 6, Sec. 9` (`2:1 = acceptable but not ideal`) and `Flowseeker Ch. 10, Sec. 3 & 6` (`2:1 is below the 3:1 minimum -> wait for pullback or pass`)[cite: 1].
   - *Filled In:* **`entries_stops_targets_and_rr.md`** (Section 4.2) & **`open_questions.md`** (`Discrepancy 1`).

---

## 4. Newer Platform Details & Time-of-Day Mechanics Added

- **Early-Session `"Drives Away"` vs. Late-Session `"Pin Jobs"` at the King Node:** The original file stated only that the King Node pins price into the close[cite: 1], omitting the `skylit.ai/docs/core-concepts` distinction that reaching the King Node **early in the session** often triggers a **"Drive Away"**, and that **Gatekeepers** are most active early in the day. *Resolved in `session_timing.md` (Section 2).*
- **Heatseeker UI Presets (`Off`, `Focus`, `King`) & Velocity Intervals (`1m` to `1d`):** Omitted in the original file. *Resolved in `platform_reference_atlas_heatseeker_talon.md` (Section 2).*

---

## Agent Checklist (`heatseeker_node_core_attributes_gaps.md`)

1. **Bind `heatseeker_node_core_attributes.md` to Its Companion Files:** Whenever an agent loads `heatseeker_node_core_attributes.md`, automatically link `chart_structure_fundamentals.md` (for Charts First definitions), `entries_stops_targets_and_rr.md` (for tap probabilities and stop/target math), and `open_questions.md` (for `[NOT SPECIFIED]` thresholds)[cite: 1, 2].