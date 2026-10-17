# heatseeker_advanced_mechanics_and_agent_guardrails_gaps.md

- **Title:** Gap Analysis & Audit of `heatseeker_advanced_mechanics_and_agent_guardrails.md` (Generated as `heatseeker_advanced_mechanics.md`)
- **Scope:** Line-by-line audit of the third previously generated reference file identifying summarized modules (Flowseeker, Dark Pool, Tempest, Playbooks) that lacked full operational procedures, missing epistemic tags, and links to the new comprehensive files that fill each gap[cite: 1].
- **Sources Used:** `heatseeker_advanced_mechanics.md`, SRC-01 through SRC-31[cite: 1].
- **Prerequisites:** `00_index.md`, `heatseeker_advanced_mechanics.md`
- **Last Updated:** 2026-10-17

---

## 1. Summarized Modules That Required Full Standalone Specifications

Because `heatseeker_advanced_mechanics.md` focused primarily on how other tools touch Heatseeker nodes, it compressed four major Skylit disciplines into brief bullet points. Each has now been expanded into a complete, programmer-ready specification:

| Summarized Topic in Original File | What Was Missing for an Autonomous Agent | Where the Complete Specification Now Lives |
| :--- | :--- | :--- |
| **Customer Flow Initiation $\rightarrow$ Dealer Gamma & Reshuffles** (Section 4) | Covered the Talon Guide Sec. 5 rule (customer call buying $\rightarrow$ dealer `-GEX` break; customer call writing $\rightarrow$ dealer `+GEX` hold) and the 5 causes of map reshuffles[cite: 1], but omitted the entire **10-chapter Flowseeker curriculum**: contract identity, `Spot at Execution`, `CANCELLED` prints, `Premium = Fill * Size * 100`, `Moneyness (+% = OTM)`, `DTE` buckets, the `Bid/Mid/Ask × Call/Put` matrix, `1-second` Sweep grouping, Multi-Leg spread/straddle reconstruction, the `4-row Next-Day OI Matrix`, `Flow Score (-100 to +100)` vs. `20-day Premium Rank`, the `Market-Wide Summary Bar` vs. `Filtered Tab` trap, the `7 Views & Row Units`, the `Atlas Flow Pane` pill groups, and the **5-Explanation Price-vs-Flow Conflict Protocol**[cite: 1]. | **`flowseeker_options_flow.md`** (Sections 1–8). |
| **Dark Pool Confluence** (Section 6) | Mentioned the `$1M` default floor and `Top N` (`1, 2, 3, 5`) / `Lookback` (`30, 45, 90, 180D`) settings[cite: 1], but omitted the 6-field schema, the mathematical definition of `Notional`, why each Atlas line is a single print rather than a volume profile, and the `MSFT $1.35B` and `SPY 755` walkthroughs[cite: 1]. | **`dark_pool.md`** (Sections 1–4) & **`case_studies.md`** (Case Study 1). |
| **Volatility & Timeframe Layers** (Section 8) | Mentioned `Auto-hide` and `VEX` on Scalp/Swing boards, but omitted the entire **Tempest Volatility** platform (`SVX`, `Expected Move` 72.3% 1-SD calibration, `IV Rank`, `Term 9-30` event inversion, `Curve`, `Skew`, `Sigma` 60-session strip, `Radar`, and `Tempest Levels` hover modes). | **`tempest_volatility.md`** (Sections 1–5). |
| **Playbooks, Sizing, Review & Abstain Rules** (Not covered in File 3) | Omitted the **7-field Playbook schema**, the **12 Boolean Abstain Gates**, the **Ten Commandments**, **Nexus `Edge` and `Proven Edge`**, the **5-stage NY Time Daily Schedule**, and the **2x2 Decision-vs-Outcome Review Matrix**[cite: 1]. | **`no_trade_conditions.md`**, **`session_timing.md`**, & **`risk_sizing_and_discipline.md`**. |

---

## 2. Procedural & Mathematical Refinements Added

1. **Dynamic Cross-Asset Conversion Formula (`"The Wiggle"` in Section 2):**
   - *Gap in Original File:* Explained why fixed multipliers are prohibited (`Flowseeker Ch. 7, Sec. 2`)[cite: 1], but did not write out the live spot-ratio formula $K_{\text{derived}}(t) = K_{\text{source}} \times \frac{P_{\text{target}}(t)}{P_{\text{source}}(t)}$ or the requirement to log $(P_{\text{source}}(t), P_{\text{target}}(t), t)$ on every cross-asset mapping.
   - *Filled In:* **`trinity_and_cross_index.md`** (Section 2.2).
2. **Complete Talon Slash Command & Basket Registry (Section 7):**
   - *Gap in Original File:* Listed the slash commands briefly, but did not enumerate the exact curated sector baskets (`semiconductors`/`semis`, `energy`, `financials`, `healthcare`, `consumer`, `industrials`), cross-sector theme baskets (`ai`, `crypto`, `china`), and market basket (`SPXW`, `SPY`, `QQQ`, `IWM`, `RUT`, `VIX`, `SMH`, `SOXX`), nor the rule that Talon refuses to invent custom baskets[cite: 1].
   - *Filled In:* **`platform_reference_atlas_heatseeker_talon.md`** (Section 4.2).
3. **Complete Historical & Pedagogical Test Suite:**
   - *Gap in Original File:* Referenced examples in passing without providing a self-contained test suite of all 13 worked case studies from the sources[cite: 1].
   - *Filled In:* **`case_studies.md`** (Case Studies 1–13).

---

## Agent Checklist (`heatseeker_advanced_mechanics_and_agent_guardrails_gaps.md`)

1. **Use the Full 19-File Knowledge Base for Production Agents:** Treat the three original files (`heatseeker_node_core_attributes.md`, `heatseeker_node_relationships.md`, `heatseeker_advanced_mechanics.md`) together with these 16 new files (`00_index.md` through `open_questions.md` plus the 3 `*_gaps.md` audit bridges) as a single unified specification.
2. **Validate Against `open_questions.md` Before Deployment:** Ensure every `[NOT SPECIFIED]` parameter (`OQ-01` to `OQ-15`) and inter-source discrepancy (`Discrepancies 1–4`) is explicitly configured in the agent's runtime environment[cite: 1].