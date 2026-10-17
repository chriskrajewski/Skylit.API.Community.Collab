# risk_sizing_and_discipline.md

- **Title:** Risk Management, Position Sizing, The Ten Commandments, Playbook Practice & Post-Trade Auditing
- **Scope:** Zero-assumption definitions, rules, and procedures for position sizing independence, the Ten Commandments of Using Heatseeker, the 7-field Playbook schema, Nexus performance metrics (`Edge`, `Proven Edge`), and the 2x2 Decision-vs-Outcome review methodology.
- **Sources Used:** SRC-03 (Academy Ch. 1)[cite: 1], SRC-08 (Academy Ch. 6, Sec. 3, 9, 10, 12)[cite: 1], SRC-12 (Academy Ch. 10, Sec. 6)[cite: 1], SRC-13 (Academy Ch. 11, Sec. 7)[cite: 1], SRC-22 (Academy Flowseeker Ch. 7, Sec. 5)[cite: 1], SRC-24 (Academy Flowseeker Ch. 9, Sec. 4)[cite: 1], SRC-25 (Academy Flowseeker Ch. 10, Sec. 1–8 & Quiz Q1–Q24)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 2 & 9)[cite: 1], SRC-28 (Docs Patternpedia: *The Ten Commandments of Using Heatseeker*), SRC-29 (Docs Nexus Field Guide), SRC-30 (Changelog Oct 13–14, 2026).
- **Prerequisites:** `00_index.md`, `glossary.md`, `charts_first_workflow.md`, `entries_stops_targets_and_rr.md`, `no_trade_conditions.md`
- **Last Updated:** 2026-10-17

---

## 1. Position Sizing Rules & Separation of Setup Quality from Size

### 1.1 Definition
- **Plain sentence:** Position size determines how many dollars you win or lose on a trade, but it is a completely separate decision from whether a setup qualifies—shrinking your size never turns a bad setup into a good one, and seeing a huge options print never justifies doubling your normal size[cite: 1].
- **Precise version:** Let $Q_{\text{base}}$ be the trader's baseline position risk per trade (note: exact account percentage risk per trade is `[NOT SPECIFIED]` in Skylit docs, and Talon Chat explicitly does **not** calculate position sizing in V1 Beta `[DOCTRINE: Academy Talon Prompt Guide, Sec. 9]`[cite: 1]). Skylit governs position size multiplier $m_{\text{size}} \in \{0, m_{\text{reduced}}, 1.0\}$ through five strict doctrinal rules `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 3, Quiz Q8, Q11, Q12]`[cite: 1]:

| Sizing Rule | Condition | Allowed Multiplier $m_{\text{size}}$ | Doctrinal Rationale | Source Tag |
| :--- | :--- | :--- | :--- | :--- |
| **Rule 1: Setup Qualification First** | Setup fails any mandatory gate (`Midpoint`, `R:R < 3:1`, `No Chart/Map Confluence`)[cite: 1]. | **`0.0` (`NO_TRADE`)** | *"Smaller size shrinks the dollars won or lost; it doesn't turn a bad setup good."* Never trade "small size" at a midpoint[cite: 1]. | `[DOCTRINE: Academy Ch. 6, Quiz Q11; Flowseeker Ch. 10, Sec. 3, Quiz Q8]`[cite: 1] |
| **Rule 2: Full Confluence Size** | A+ Setup (`R:R >= 3:1`, `1st Tap Fresh Node`, `Trinity 3/3 Aligned`, no unresolved flow conflict)[cite: 1]. | **`1.0` (Normal Size)** | All structural, cross-index, and risk gates are aligned[cite: 1]. | `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5; Docs Core Concepts]`[cite: 1] |
| **Rule 3: Partial Confluence / 2nd Tap Reduction** | Either `Trinity == 2/3 Aligned` (one index diverges/lags), OR `Node_Taps == 1` (2nd test of node), OR post-rejection entry after a resolved flow conflict[cite: 1]. | **`m_reduced` (`< 1.0`, Reduced Size)** | *"2 of 3 aligned — reduced confidence; the bare minimum, at reduced size"* and *"2nd Test: Trade smaller, or wait for extra confirmation."* *(Exact % reduction `[NOT SPECIFIED]`.)* | `[DOCTRINE: Academy Ch. 10, Sec. 6; Flowseeker Ch. 7, Sec. 5; Ch. 9, Sec. 5; Ch. 10, Quiz Q12; Docs Core Concepts]`[cite: 1] |
| **Rule 4: Prohibition on Flow-Driven Oversizing** | A massive options print or sweep appears in the direction of the trade[cite: 1]. | **Cap at `1.0` (Never double size)** | *"Strong flow doesn't earn a bigger position by itself... Size comes from the whole read and the risk — not from how big the flow is."*[cite: 1] | `[DOCTRINE: Academy Flowseeker Ch. 1, Quiz Q7; Ch. 2, Quiz Q20; Ch. 10, Sec. 3, Quiz Q11]`[cite: 1] |
| **Rule 5: Prohibition on Averaging Down Invalidated Trades** | Price breaks and accepts beyond the floor/ceiling invalidation level and the floor node shrinks[cite: 1]. | **`0.0` (Exit / Reassess; Never add)** | Adding to a losing trade after invalidation conditions hit violates playbook discipline[cite: 1]. | `[DOCTRINE: Academy Flowseeker Ch. 7, Quiz Q24]`[cite: 1] |

---

## 2. The Ten Commandments of Using Heatseeker

Published in Skylit's **Patternpedia (`skylit.ai/docs/patternpedia/the-ten-commandments-of-using-heatseeker`)** by Warren and John Wicks and reinforced across Chapters 1–12 of the Academy `[DOCTRINE: Docs Patternpedia: Ten Commandments; Academy Ch. 7, Sec. 6]`[cite: 1]:

1. **Charts First, Always:** Establish technical structure (support, resistance, trend, range extremes) on the price chart before consulting Heatseeker; Heatseeker is the X-ray that confirms the chart thesis `[DOCTRINE: Academy Ch. 1, Sec. 1 & 3; Docs Ten Commandments]`[cite: 1].
2. **Trade Reversals at Extremes, Not Continuation:** Focus on fading structural floors and ceilings at the edges of the map rather than chasing breakouts or continuation moves `[DOCTRINE: Academy Ch. 5, Sec. 2.IV; Ch. 7, Sec. 2 & 6; Docs Ten Commandments]`[cite: 1].
3. **Absolute Value Overrides Color:** Prioritize the magnitude (absolute dollar value) of a node over whether it is yellow (`+GEX`) or purple (`-GEX`) `[DOCTRINE: Academy Ch. 2, Sec. 5; Docs Ten Commandments]`[cite: 1].
4. **Never Trade the Midpoint ("Do Not Diddle in the Middle"):** Avoid entering trades in the dead equilibrium space halfway between major nodes where dealer hedging pressure is absent and R:R is 1:1 `[DOCTRINE: Academy Ch. 3, Sec. 6; Ch. 6, Sec. 7; Docs Ten Commandments]`[cite: 1].
5. **Respect the Trinity (`SPX`, `SPY`, `QQQ`):** Never trade a single chart or map in isolation; stay aware of the broader market system and stand aside when the three indices diverge `[DOCTRINE: Academy Ch. 7, Sec. 6; Ch. 10, Sec. 2 & 6; Docs Ten Commandments]`[cite: 1].
6. **Prioritize Fresh Nodes Over Tested/Delivered Nodes:** The first tap of a fresh node carries the highest reaction probability (~80%); each subsequent tap consumes liquidity and degrades the edge (~66% on 2nd, ~33% on 3rd) `[DOCTRINE: Academy Ch. 6, Sec. 6; Ch. 9, Sec. 2; Docs Ten Commandments]`[cite: 1].
7. **Adapt to the Active Gamma Regime:** Trade differently in Positive Gamma (slow, mean-reverting, pinned—take quick profits at levels) vs. Negative Gamma (fast, expanding—assume Beach Ball overshoots first) `[DOCTRINE: Academy Ch. 4, Sec. 2; Ch. 6, Sec. 8; Docs Ten Commandments]`[cite: 1].
8. **Monitor Dynamic Map Reshuffles & Velocity:** The map is not static; update your bias immediately when floors roll up, ceilings roll down, or Velocity Mode shows rapid accumulation/unwinding `[DOCTRINE: Academy Ch. 7, Sec. 1 & 6; Ch. 8, Sec. 6; Docs Ten Commandments]`[cite: 1].
9. **Enforce Asymmetric Risk-to-Reward ($\ge 3:1$) & Hard Invalidation Stops:** Only take trades offering at least 3:1 reward-to-risk with a pre-defined stop loss placed one node beyond invalidation `[DOCTRINE: Academy Ch. 6, Sec. 9 & 10; Docs Ten Commandments]`[cite: 1].
10. **When in Doubt, Sit Out (`No Trade` Is a Valid Position):** Stand down during `Rainbow Road`, `Whipsaw` divergence, unresolved price-flow conflicts, or when price never reaches your node `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 5, Sec. 2.VI; Ch. 6, Sec. 12; Flowseeker Ch. 10, Sec. 3; Docs Ten Commandments]`[cite: 1].

---

## 3. The 7-Field Playbook Schema & Pre-Trade Archival Protocol

### 3.1 The Mandatory 7-Field Playbook Structure (`[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 2]`)
Every trade hypothesis (whether executed or skipped) must be structured as a 7-field Playbook record where **Flow sits fourth and is never the trigger**[cite: 1]:

```json
{
  "1_question": "Testable question (e.g., 'Will SPY hold the 578 range low floor and rotate to the 590 ceiling?')",
  "2_location": "Chart structure location (e.g., 'SPY at 579.00, testing week-long 578-590 range low support')",
  "3_map": "Heatseeker & Trinity read (e.g., 'Fresh +GEX Floor node at 578, King Node Ceiling at 590, +GEX regime; Trinity 2/3 or 3/3')",
  "4_flow": "Flowseeker & Dark Pool evidence (e.g., 'Supports: 590/600 bull call spread with +1,900 next-day OI growth')",
  "5_target": "Next structural level (e.g., '590.00 King Node ceiling -> Reward = $11.00')",
  "6_weakens_if": "Concrete invalidation conditions (e.g., 'Price accepts below 578 [Stop = 577.00, Risk = $2.00]; 578 floor node shrinks; 590 OI drops')",
  "7_decision": "Action & Size (e.g., 'EXECUTE_LONG at 579.00, R:R = 5.5:1, Reduced Size [Trinity 2/3]')"
}
```

### 3.2 What Must Be Saved at the Moment of Decision (`[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 4]`)
To prevent **hindsight bias** (rewriting what you knew after seeing how the trade turned out) `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 4, Quiz Q17 & Q18]`[cite: 1], the agent or trader must snapshot and save **at the exact moment of the decision**—for **both trades taken AND setups skipped** `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 4, Quiz Q15]`[cite: 1]:
1. Contract identity and exact timestamp[cite: 1].
2. Visual/data snapshots of the **Chart**, the **Heatseeker/Trinity Map**, and the **Flowseeker Feed**[cite: 1].
3. Active tab filter settings[cite: 1].
4. The primary hypothesis **and** the list of alternative explanations[cite: 1].
5. Explicit log of **what was known at that exact moment** (e.g., yesterday's OI, not tomorrow's OI)[cite: 1].
6. Concrete `"Weakens If"` conditions[cite: 1].
7. Whether the setup was **traded** or **skipped/watched**[cite: 1].

---

## 4. Honest Post-Trade Auditing: The 2x2 Decision-vs-Outcome Matrix

### 4.1 Definition
- **Plain sentence:** A trade review must judge the **quality of the decision** (whether the playbook rules were followed based on information available at the time), **never the dollar outcome**—because a rule-breaking trade at a midpoint can get lucky and make money, while a textbook 5.5:1 floor reversal can hit its invalidation stop and lose money `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 5 & 6, Quiz Q19–Q23]`[cite: 1].
- **Precise version:** During the weekly review, every logged record (executed trades **and** skipped setups) is classified into one of four audit cells `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 5 & 6]`[cite: 1]:

| Decision Quality (Process Adherence) | Outcome (Market Result) | Audit Classification | Worked Example from Flowseeker Ch. 10, Sec. 6 | Agent Learning / Feedback Action |
| :--- | :--- | :--- | :--- | :--- |
| **Good Decision** *(All Playbook, Chart, Map, Trinity & $\ge 3:1$ R:R rules followed)* | **Good Outcome** *(Target hit / profitable)* | **`GOOD_DECISION_GOOD_OUTCOME`** | **Monday:** SPY floor reversal entered on pullback to `579` (`5.5:1` R:R) hit `590` target[cite: 1]. | Reinforce playbook template[cite: 1]. |
| **Good Decision** *(All rules followed, or disciplined wait/pass on conflict)* | **Bad Outcome / Missed Move** *(Clean stop-out on invalidation, or price broke out while waiting on conflict)* | **`GOOD_DECISION_BAD_OUTCOME`** or **`GOOD_DECISION_MISSED_MOVE`** | **Wednesday:** Waited on unresolved conflict at `590` ceiling; price broke out (*Good decision, missed move*)[cite: 1].<br>**Thursday:** Complete floor reversal stopped out cleanly when price accepted below the floor (*Good decision, bad outcome — `"weakens if"` did its job*)[cite: 1]. | **Do NOT alter rules or double size next time**; the stop/pass rule worked as designed `[DOCTRINE: Flowseeker Ch. 10, Sec. 6, Quiz Q22]`[cite: 1]. |
| **Bad Decision** *(Violated Charts First, traded midpoint, chased sweep without map, or $< 3:1$ R:R)* | **Good Outcome** *(Made money by luck)* | **`BAD_DECISION_GOOD_OUTCOME` (Process Mistake!)** | **Tuesday:** Bought calls off a mid-range QQQ sweep without checking the map, and it worked (*`"One mistake — and it was Tuesday's win"`*)[cite: 1]. | **Flag as an error/violation** despite positive PnL `[DOCTRINE: Flowseeker Ch. 10, Sec. 6, Quiz Q21]`[cite: 1]. |
| **Bad Decision** *(Violated rules)* | **Bad Outcome** *(Lost money)* | **`BAD_DECISION_BAD_OUTCOME`** | Chased a `$1.4M` QQQ call print into a range-high ceiling and lost as price rejected (Flowseeker Ch. 1, Sec. 6)[cite: 1]. | Identify which gate (`Gate 1–12`) was bypassed and enforce gate lock[cite: 1]. |

### 4.2 Nexus Platform Ranking & Evaluation Metrics (`Edge` and `Proven Edge`)
- In **Nexus** (Skylit's paper-trading and evaluation platform, which provides a `$100,000` paper wallet for options, stocks, and futures, plus an `Agent` tab for bot paper accounts via API key) `[DOCTRINE: Docs Nexus Field Guide; Changelog Oct 13–14, 2026]`:
  - Traders are evaluated on the **Rank Board** and **Season Board** using **`Edge`** and **`Proven Edge`** (with `Avg Return` as a secondary column; legacy `"Cred"` was removed on Oct 13, 2026) `[DOCTRINE: Changelog Oct 13, 2026]`.
  - Traders who have not yet qualified for the Rank Board appear under `"Not ranked yet"` (`Theta` and `Placement` tiers), and a progress card displays missing qualification requirements (`positions`, `trading days`, `average return`, and `net return`) `[DOCTRINE: Changelog Oct 13, 2026]`.

---

## Agent Checklist (`risk_sizing_and_discipline.md`)

1. **Decouple Position Size from Flow Magnitude:** Never increase `Position_Size` above `1.0x` base risk due to high `Flow_Score` or large `Premium`[cite: 1].
2. **Apply `m_reduced` on `2/3` Trinity or `2nd Tap`:** Automatically scale down position size when `Trinity_Aligned == 2` or `Node_Taps == 1`[cite: 1].
3. **Snapshot State at Decision Time $t_{\text{decision}}$:** Persist chart, map, flow, and playbook state before outcome is known, for both `EXECUTE` and `SKIP/WAIT` decisions[cite: 1].
4. **Grade Weekly Audit by Process, Not PnL:** Flag any profitable trade that bypassed `Gates 1–12` as `BAD_DECISION_GOOD_OUTCOME`[cite: 1].