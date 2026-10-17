# tempest_volatility.md

- **Title:** Tempest Volatility Intelligence — Implied Volatility (`SVX`), Expected Move, IV Rank, Term Structure, Skew & Sigma Integration
- **Scope:** Zero-assumption definitions, inputs, interpretation rules, and integration procedures for Tempest's volatility metrics (`SVX`, `Expected Move`, `IV Rank`, `Term 9-30`, `Curve`, `Skew`, `Sigma` strip, `Radar`, and Atlas `Tempest Levels`).
- **Sources Used:** SRC-29 (Docs Tempest Field Guide: `skylit.ai/docs/guides/tempest`), SRC-30 (Changelog Oct 13, 2026: Tempest Auto-hide & Tempest Levels hover), SRC-31 (Learn: *How accurate is Tempest? Two years of volatility readings, checked*).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `entries_stops_targets_and_rr.md`
- **Last Updated:** 2026-10-17

---

## 1. What Tempest Measures & Core Metrics

### 1.1 Definition
- **Plain sentence:** Tempest measures how much price movement the options market is currently pricing in for each stock or index, whether options are historically cheap or expensive, whether near-term event risk is elevated, and how actual daily moves compare against implied moves `[DOCTRINE: Docs Overview & Tempest Field Guide]`.
- **Precise version:** Tempest computes six core volatility readings for each covered ticker `[DOCTRINE: Docs Tempest Field Guide; Learn: Tempest Accuracy]`:

| Metric | Definition & What It Measures | How Traders & Agents Use It | Source Tag |
| :--- | :--- | :--- | :--- |
| **1. `SVX` (Implied Volatility, e.g., `SVX30`)** | Tempest's standardized implied volatility index for a ticker (annualized % volatility priced by the options chain, e.g., `SVX30` for the 30-day horizon). | Measures overall option pricing level; feeds `Expected Move` and `IV Rank`. | `[DOCTRINE: Docs Tempest Field Guide; Learn: Tempest Accuracy]` |
| **2. `Expected Move`** | The dollar and percentage price range that options are pricing for the underlying over a specific horizon (daily/session, weekly, or event). | **Yardstick for targets, stops, and strike selection:** checks whether a distant Heatseeker node is reachable within today's priced range. *(Two-year audit Jul 2024–Sep 2026 across 20 liquid tickers & 10,879 stock-days showed actual moves landed inside the 1-SD band **72.3%** of the time vs. 68.3% theoretical normal benchmark `[DOCTRINE: Learn: Tempest Accuracy]`.)* | `[DOCTRINE: Docs Tempest Field Guide; Learn: Tempest Accuracy]` |
| **3. `IV Rank`** | Ranks where current `SVX` sits against the ticker's **own past-year range** ($0\text{ to }100$). | Identifies whether options premium is relatively cheap (low IV Rank) or expensive (high IV Rank) compared to the stock's trailing 52-week history. *(In the 2-year calibration audit, average subsequent 30-day realized vol rose monotonically across all 5 IV Rank buckets from low to high `[DOCTRINE: Learn: Tempest Accuracy]`.)* | `[DOCTRINE: Docs Tempest Field Guide; Learn: Tempest Accuracy]` |
| **4. `Term 9-30` & `Curve` (Term Structure)** | Compares short-dated 9-day implied volatility against 30-day implied volatility (`Term 9-30`), alongside the full expiration `Curve`. | **Near-Term Event Detector:** When `9-day IV > 30-day IV` (front-end inversion / backwardation), the market is pricing an imminent event or catalyst (e.g., earnings, FOMC, CPI). *(In the 2-year audit, next-day moves averaged **1.92%** when 9D > 30D vs. **1.44%** in normal contango `[DOCTRINE: Learn: Tempest Accuracy]`.)* | `[DOCTRINE: Docs Tempest Field Guide; Learn: Tempest Accuracy]` |
| **5. `Skew`** | Measures the relative implied volatility pricing between downside puts and upside calls across the strike chain. | Indicates whether protection demand is skewed toward downside crash risk or upside call chasing. | `[DOCTRINE: Docs Tempest Field Guide]` |
| **6. `Sigma` (Realized Move Strip)** | A session-by-session historical strip (**60 sessions** in Tempest; **30 sessions** in the Atlas **Aegis** sidebar) that expresses each daily close-to-close move in standard-deviation (`Sigma` / $\sigma$) units relative to what options had priced for that day. | Shows whether a stock has been consistently outrunning its implied move ($\vert{}\text{Sigma}\vert{} > 1.0\sigma$) or staying pinned inside its expected band ($\vert{}\text{Sigma}\vert{} \le 1.0\sigma$). | `[DOCTRINE: Docs Tempest Field Guide]` |

---

## 2. Tempest Views & Atlas Chart Integration

### 2.1 Tempest Workspace Views (`[DOCTRINE: Docs Tempest Field Guide]`)
1. **`Radar`:** Multi-ticker scanner/watch table displaying `SVX`, `Expected Move`, `IV Rank`, `Term 9-30`, and `Sigma` across your symbols so you can spot volatility outliers before opening a chart.
2. **`Market`:** Broad market and index volatility overview.
3. **Single-Ticker Detail (`Curve`, `Skew`, `Sigma`):** Deep-dive term structure curve across expirations, strike skew smile, and the 60-session `Sigma` bar strip.

### 2.2 Tempest Levels & Controls on Atlas Charts (`[DOCTRINE: Changelog Oct 13, 2026]`)
1. **`Tempest Levels` Display Modes on Atlas:**
   - Plots upper and lower volatility/expected-move bands directly on the price chart `[DOCTRINE: Changelog Oct 13, 2026]`.
   - **Three `Show` Options (Added Oct 13, 2026):**
     - `Line and label` *(Default)*
     - `Label with line on hover`
     - `Line with label on hover`
     - Hovering near any Tempest level displays its full line and a plain-English explanation `[DOCTRINE: Changelog Oct 13, 2026]`.
2. **Timeframe `Auto-hide` Layer Control (Added Oct 13, 2026):**
   - Under the chart's eye menu, `Auto-hide` (`Off`, `5m+`, `15m+`, `30m+`, `1H+`, `4H+`, `1D+`) provides separate visibility switches for **indicators**, **exposure**, and **drawings** per pane interval, saved locally in the browser `[DOCTRINE: Changelog Oct 13, 2026]`.

---

## 3. Procedure: Integrating Tempest Volatility with Heatseeker Nodes & Playbooks

### 3.1 Inputs
- Tempest `Expected Move` ($\pm \Delta EM_{\text{day}}$), `IV Rank`, `Term 9-30`, and trailing `Sigma` strip for symbol $S$.
- Candidate Heatseeker entry node $K_{\text{entry}}$ and target node $K_{\text{target}}$.

### 3.2 Step-by-Step Integration Procedure
1. **Target Reachability Gate (Expected Move vs. Node Distance):**
   - Before targeting a distant Heatseeker node $K_{\text{target}}$ on a 0DTE or intraday trade, compare the distance from spot/open to $K_{\text{target}}$ against Tempest's daily `Expected Move` ($\pm 1.0\sigma$ band) `[INFERENCE from Docs Tempest Field Guide: "Expected Move as a yardstick for targets, stops and strike selection"]`:
   - If $K_{\text{target}}$ sits well beyond the $1.0\sigma$ `Expected Move` **and** the map is in a Positive Gamma (`+GEX`) regime with low Velocity, the probability of reaching $K_{\text{target}}$ today is low (72.3% of sessions stay inside the 1-SD band `[DOCTRINE: Learn: Tempest Accuracy]`). Prefer taking profits at an intermediate node inside or near the Expected Move boundary `[INFERENCE]`.
2. **Extreme Fade Confluence (Tempest Level + Heatseeker Floor/Ceiling):**
   - When a major Heatseeker Floor or Ceiling node ($K_{\text{floor}}, K_{\text{ceil}}$) coincides with Tempest's upper or lower daily **Expected Move (`Tempest Level`)**, structural confluence increases: both dealer gamma positioning and the 1-standard-deviation options-priced boundary agree that the move is stretched `[INFERENCE combining Docs Tempest Field Guide & Atlas Course Sec. 2]`[cite: 1].
3. **Event Risk Check (`Term 9-30` Inversion):**
   - Check `Term 9-30` before holding trades across catalysts or fading early moves:
   - If `9-day IV > 30-day IV` (inverted term structure), flag `EVENT_RISK_ELEVATED = TRUE`. Expect larger average daily moves ($1.92\%$ vs. $1.44\%$ `[DOCTRINE: Learn: Tempest Accuracy]`) and verify via `Hedge Node` rules (`heatseeker_node_core_attributes.md`) whether far-OTM nodes are merely pre-event protection that will unwind once the event passes `[DOCTRINE: Docs Core Concepts: Hedge Nodes; Learn: Tempest Accuracy]`.
4. **Options Structure Selection (`IV Rank` Context):**
   - When `IV Rank` is high and the map is in a `Type 1: Range / Choppy Day` (`+GEX` Pika Cloud), long OTM options suffer severe theta and vega decay; experienced traders either take quick scalps at the extreme edges or use premium-selling / defined-risk spread structures `[DOCTRINE: Academy Ch. 4, Sec. 3 Type 1; Ch. 8, Sec. 8; Docs Tempest Field Guide]`[cite: 1].

---

## 4. Edge Cases
- **Multi-Sigma Expansion Days (`Negative Gamma + Velocity`):** Roughly 27.7% of stock-days close outside the 1-SD Expected Move band `[DOCTRINE: Learn: Tempest Accuracy]`. These tail days cluster in **Negative Gamma (`-GEX`) regimes with active Velocity Mode accumulation** or `Term 9-30` event inversions `[INFERENCE]`. Never blindly fade a Tempest 1-SD Expected Move band if price is accelerating through a `-GEX` air pocket without a Heatseeker Floor/Ceiling node (`Charts + Heatseeker` govern the fade, not the volatility band alone) `[INFERENCE from Academy Ch. 8, Sec. 9]`[cite: 1].

---

## 5. Worked Example (Illustrative Synthesis of `[DOCTRINE: Docs Tempest Field Guide & Learn: Tempest Accuracy]`)
- **Inputs:** `SPY` opens at `580.00`. Tempest shows a daily 1-SD `Expected Move` of $\pm \$4.20$ (`575.80` to `584.20`), normal contango (`9D IV < 30D IV`), and trailing `Sigma` readings averaging $0.7\sigma$.
- **Heatseeker Map:** Positive Gamma Floor at `576.00`, King Node Ceiling at `584.00`, and a static far-OTM node at `592.00`.
- **Agent Evaluation:**
  1. `592.00` is $\$12.00$ away ($2.85\sigma$, nearly $3\times$ the daily Expected Move) and static $\rightarrow$ reject `592.00` as an intraday target (`Hedge Node` / unreachable in `+GEX`).
  2. `576.00` Floor and `584.00` King Node align closely with Tempest's `575.80` and `584.20` 1-SD Expected Move boundaries $\rightarrow$ high-confluence intraday fade boundaries.

---

## 6. What It Is NOT
- Tempest's `Expected Move` is **not** a directional forecast (it is symmetric in standard deviation terms) and is **not** a hard ceiling that cannot be breached (roughly 1 in 4 sessions closes outside the 1-SD band) `[DOCTRINE: Learn: Tempest Accuracy]`.

---

## 7. Related Concepts
- See `gex_vs_vex_and_expirations.md`, `entries_stops_targets_and_rr.md`, and `session_timing.md`.

---

## Agent Checklist (`tempest_volatility.md`)

1. **Query `Expected Move` Before Validating Intraday Targets:** Flag any 0DTE target node whose distance from open exceeds $1.5 \times \text{Expected Move}$ unless `Regime == NEGATIVE_GAMMA` and `Velocity_Mode` is actively expanding.
2. **Check `Term 9-30` for Event Inversions:** If `IV_9D > IV_30D`, set `Event_Volatility_Warning = TRUE` and check `Talon` company context (`when do they report?` / `is there event risk this week?`) `[DOCTRINE: Academy Talon Prompt Guide, Sec. 7; Learn: Tempest Accuracy]`[cite: 1].
3. **Cross-Check `Tempest Levels` with Heatseeker Extremes:** Boost setup confidence when a fresh Heatseeker Floor/Ceiling sits within $\delta_{\text{deflect}}$ of the daily 1-SD `Expected Move` boundary.