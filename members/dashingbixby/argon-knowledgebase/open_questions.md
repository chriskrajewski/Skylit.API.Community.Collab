# open_questions.md

- **Title:** Open Questions, Unspecified Parameters & Inter-Source Discrepancies Registry
- **Scope:** Centralized registry of every `[NOT SPECIFIED]` parameter required by a software engineer to build deterministic trading detectors, alongside every inter-source tension or conflict discovered across the Skylit documentation and Academy export[cite: 1].
- **Sources Used:** SRC-01 through SRC-31[cite: 1, 2].
- **Prerequisites:** All files in Phases 1–4.
- **Last Updated:** 2026-10-17

---

## 1. Registry of `[NOT SPECIFIED]` Algorithmic Parameters

The table below lists every quantitative threshold, lookback window, or tie-breaking rule where the official Skylit sources provide a qualitative concept (e.g., "obvious swing high", "rapid rate of change", "large node") without specifying the exact number needed for deterministic code[cite: 1]. Software agents must expose each of these as a configurable variable rather than hardcoding an unverified constant.

| ID | Module / File | Parameter Name | What Is `[NOT SPECIFIED]` in the Sources | Recommended Configurable Default (`[GENERAL TA]` / `[INFERENCE]`) |
| :--- | :--- | :--- | :--- | :--- |
| **OQ-01** | `chart_structure_fundamentals.md` | `Swing_Fractal_Bars_K` | How many candles on the left and right confirm a "swing high" or "swing low" (Ch. 1 states only to mark "obvious" levels you can spot in under 5 seconds)[cite: 1]. | `k = 5` bars for major swings; `k = 3` bars for minor swings `[GENERAL TA]`. |
| **OQ-02** | `chart_structure_fundamentals.md` | `Min_Swing_Reversal_ATR` | The minimum price reversal distance required for a peak/trough to qualify as a "large, dramatic reversal" rather than shallow noise[cite: 1]. | `>= 1.5 * ATR(14)` on the active timeframe `[GENERAL TA]`. |
| **OQ-03** | `chart_structure_fundamentals.md` | `SR_Zone_Merge_Tolerance` | How close two swing highs/lows must be before they are merged into a single support/resistance zone rather than drawn as separate lines[cite: 1]. | Match Skylit's Deflection Zone width ($\pm\$0.50$ on SPY/QQQ; $\pm\$5.00$ on SPX) `[INFERENCE]`[cite: 1]. |
| **OQ-04** | `chart_structure_fundamentals.md` | `Midpoint_Band_Percentage` | The exact percentage width around the 50% midpoint of a range where the "Do NOT trade the midpoint" prohibition begins and ends[cite: 1]. | Middle 50% of the range ($L_{\%} \in (0.25, 0.75)$), derived from the 3:1 R:R requirement `[INFERENCE]`[cite: 1]. |
| **OQ-05** | `heatseeker_node_core_attributes.md` | `Tap_Accrual_Dwell_Rule` | The exact tick, bar, or time-dwell rule by which the backend increments a node's tap counter while price rests on a strike (Talon Guide Sec. 4 states taps accrue while resting at the level, not only when leaving and returning, but does not state the timer/bar interval)[cite: 1]. | Query tap count directly from the Skylit API/Talon endpoint; if computing locally, treat each closed `5m` bar touching $[K - \delta_{\text{deflect}}, K + \delta_{\text{deflect}}]$ as +1 tap `[INFERENCE]`. |
| **OQ-06** | `heatseeker_node_core_attributes.md` | `Gatekeeper_Relative_Size_Bounds` | The minimum and maximum `% of King Node` exposure value that qualifies an intermediate strike as a "Gatekeeper" vs. an "Air Pocket" strike vs. a primary Floor/Ceiling[cite: 1]. | `15% <= (% of King) <= 60%` sitting between spot/floor and a larger node `[INFERENCE]`. |
| **OQ-07** | `heatseeker_node_spatio_temporal_relationships.md` | `Air_Pocket_Max_Threshold` | The maximum `% of King Node` exposure below which a contiguous sequence of strikes is classified as an "Air Pocket" or "Liquidity Vacuum"[cite: 1]. | `< 15% of King Node` across $\ge 2$ adjacent strikes (Air Pocket) or $\ge 5$ strikes with no floor/ceiling (Liquidity Vacuum) `[INFERENCE]`. |
| **OQ-08** | `heatseeker_node_spatio_temporal_relationships.md` | `Velocity_High_Threshold_Pct` | The exact `% change` threshold in Velocity Mode (`1m`, `5m`, `15m`) that defines "rapid accumulation" (Ch. 8 Sec. 5 shows a `100%` increase example on SPX, but does not state the minimum cutoff)[cite: 1]. | Configurable threshold (e.g., `>= +25%` over `5m`/`15m` or `>= +50%` from open) `[INFERENCE]`[cite: 1]. |
| **OQ-09** | `heatseeker_node_core_attributes.md` | `Real_vs_Hedge_Growth_Window` | The exact lookback duration and `% growth` rate required to classify a far-OTM node as a growing `Real Node` vs. a static/decaying `Hedge Node`[cite: 1]. | Positive net exposure growth ($\Delta \vert{}\text{GEX}\vert{} > 0$) over the trailing `15m`–`1h` velocity window `[INFERENCE]`[cite: 1]. |
| **OQ-10** | `entries_stops_targets_and_rr.md` | `Deflection_Zone_Non_Trinity` | The dollar or percentage deflection zone tolerance ($\delta_{\text{deflect}}$) for instruments other than `SPY` ($\pm\$0.50$), `QQQ` ($\pm\$0.50$), and `SPX` ($\pm\$5.00$), such as `NVDA`, `TSLA`, `IWM`, `ES`, or `NQ`[cite: 1]. | Scale proportionally to underlying price (~$0.08\%\text{ to }0.10\%$ of spot, matching $\$0.50/\$580$ on SPY and $\$5/\$6800$ on SPX) `[INFERENCE]`[cite: 1]. |
| **OQ-11** | `entries_stops_targets_and_rr.md` | `Break_And_Hold_Confirmation` | The exact candle close or time duration beyond the stop node $K_{\text{stop}}$ required to confirm a "break and hold" (price acceptance) vs. a temporary negative-gamma overshoot wick[cite: 1]. | One closed `5m` bar beyond $K_{\text{stop}}$, OR an excursion $> 0.5 \times \Delta K_{\text{node}}$ past $K_{\text{stop}}$ `[GENERAL TA]`. |
| **OQ-12** | `risk_sizing_and_discipline.md` | `Base_Account_Risk_And_Reduced_Pct` | The baseline % of account equity risked on a `1.0x` normal-size trade, and the exact fractional multiplier $m_{\text{reduced}}$ used when reducing size for `2/3` Trinity alignment or a `2nd Tap`[cite: 1]. | Configurable user parameters: `Base_Risk_Pct = 1.0%`, `Reduced_Size_Multiplier = 0.50` (`50%` size) `[GENERAL TA]`. |
| **OQ-13** | `flowseeker_options_flow.md` | `Flow_Score_And_FIR_Formulas` | The exact mathematical weights inside Skylit's proprietary `Flow Score` ($-100$ to $+100$), `FlowBonus`, and the Summary Bar's `FIR` metric, as well as the exact cent boundary separating `Ask Side` / `Bid Side` from `Mid`[cite: 1]. | Consume `flowScore`, `side`, and `FIR` directly as pre-computed fields from the Skylit API rather than re-deriving `[DOCTRINE: Flowseeker Ch. 2 & 5]`[cite: 1]. |
| **OQ-14** | `gex_vs_vex_and_expirations.md` | `VEX_Pull_Armed_Rule` | The exact quantitative condition that causes Talon's `Bull vs Bear` tab to mark a VEX pull row as `"armed"` (Changelog Oct 13, 2026). | Read the boolean `armed` flag directly from Talon's read payload. |
| **OQ-15** | `session_timing.md` | `Early_vs_Late_Session_Cutoffs` | Exact clock cutoffs in ET separating "early in the session" (`Drives Away` / `Gatekeeper rejections`) from "late in the session" (`Pin Jobs` into the close), and default start/end times for the four `Killzones` (`ASIA`, `LNDN`, `NYAM`, `NYPM`). | Configurable windows: Early Session = `09:30–11:30 ET`; Late Session Pin Window = `14:30–16:00 ET` `[GENERAL TA]`. |

---

## 2. Side-by-Side Registry of Inter-Source Discrepancies & Tensions

Where two Skylit sources present different numbers, terminology, or actions for the same situation, both versions are preserved below without merging:

### Discrepancy 1: Minimum Acceptable Risk-to-Reward (`2:1` vs. Strict `3:1`)
- **Version A (`Academy Heatseeker Ch. 6, Sec. 9 & Quiz Q16`):**
  - States under `Risk/Reward`: **`"3:1 = standard, aim for higher"`**, **`"2:1 = acceptable but not ideal"`**, **`"Below = avoid"`**, and in Quiz Q16 marks `"Acceptable but below standard"` as the correct answer for a `2:1` setup[cite: 1].
- **Version B (`Academy Heatseeker Ch. 3, Sec. 6` & `Flowseeker Ch. 10, Sec. 3 & 6, Quiz Q7 & Q9`):**
  - Heatseeker Ch. 3 Sec. 6 states: **`"Skylit doctrine requires a 3:1 minimum R:R for any trade to qualify."`**[cite: 1]
  - Flowseeker Ch. 10 Sec. 3 & 6 states: **`"The Skylit minimum is 3 to 1 reward to risk"`**, and in the SPY `581.40` case study where R:R is **`~2 to 1`** ($8.60 / 4.40 = 1.95$), explicitly instructs the trader: **`"About 2 to 1 — below the minimum, so wait for a better entry or pass"`**[cite: 1].
- **Agent Resolution:** Expose a boolean config flag `Strict_3_To_1_Gate` (default = `TRUE`, enforcing $\text{R:R} \ge 3.0$ or emitting a limit pullback order to achieve $3.0:1$; if set to `FALSE`, allow $2.0 \le \text{R:R} < 3.0$ as a B-grade reduced-size trade)[cite: 1].

### Discrepancy 2: Action in a `Rainbow Road` Environment (`No-Trade` vs. `Micro-Deflections`)
- **Version A (`Academy Heatseeker Ch. 5, Sec. 2.VI & Quiz Q7` & `Docs Patternpedia: Rainbow Road`):**
  - Defines Rainbow Road as undefined chaos with no dominant nodes and no defined ceiling or floor, stating: **`"This is: No-trade conditions"`** and **`"Stand down and wait for clarity"`**[cite: 1].
- **Version B (`Academy Heatseeker Ch. 6, Quiz Q21`):**
  - Asks `"In a Rainbow Road environment, what is the correct approach?"` with options: `A. Fade every extension`, `B. Ignore structure`, `C. Wait for pullbacks and enter on micro-deflections`, `D. Chase the move immediately`[cite: 1].
- **Agent Resolution:** Default to **Version A (`NO_TRADE` / Abstain Gate 2)** in automated execution, as Ch. 5's dedicated pattern definition and Patternpedia explicitly classify Rainbow Road as no-trade conditions[cite: 1].

### Discrepancy 3: SPX Deflection Zone Width (`±$5.00` vs. `5–10 Points`)
- **Version A (`Academy Heatseeker Ch. 6, Sec. 3`):**
  - States: **`"Deflection zones are +/- 50 cents on QQQ and SPY and +/- 5 dollars on SPX in either direction."`**[cite: 1]
- **Version B (`Skylit Learn: Best GEX Tools for 0DTE SPX Trading`):**
  - Describes a **`5–10 point`** tolerance margin around SPX nodes on 0DTE.
- **Agent Resolution:** Use $\pm\$5.00$ as the primary entry trigger band for `+GEX` SPX nodes, and permit up to $\pm\$10.00$ excursion before invalidation when evaluating `-GEX` Beach Ball overshoots[cite: 1].

### Discrepancy 4: A+ Setup Gate Count (`3 Gates` vs. `Summer Bootcamp 4 Gates`)
- **Version A (`Academy Heatseeker Ch. 6, Sec. 9` & `Ch. 12, Sec. 1`):**
  - Lists 3 core requirements for an A+ setup: (1) Chart structure, (2) Heatseeker confluence, and (3) Asymmetric R:R ($\ge 3:1$) executed on deflection[cite: 1].
- **Version B (`Academy Flowseeker Ch. 10, Sec. 2 & 8`):**
  - Notes that the **Summer Bootcamp** stricter checklist adds a **4th mandatory gate**: a **mapped liquidity sweep** at the level (`"all four gates, or no trade"`)[cite: 1].
- **Agent Resolution:** Support both `Standard_3_Gate_Mode` and `Bootcamp_4_Gate_Mode` (requiring candle wick sweep + close back inside the level) as a strategy configuration toggle[cite: 1].

---

## Agent Checklist (`open_questions.md`)

1. **Load `OQ-01` through `OQ-15` from External Config:** Never bury any `[NOT SPECIFIED]` parameter inside compiled business logic; load all 15 parameters from an auditable configuration object.
2. **Log Active Discrepancy Mode:** Include the state of `Strict_3_To_1_Gate`, `Rainbow_Road_Strict_Abstain`, `SPX_Deflection_Margin`, and `Bootcamp_4_Gate_Mode` in every Playbook decision log[cite: 1].