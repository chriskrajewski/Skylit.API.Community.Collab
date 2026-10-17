# chart_structure_fundamentals.md

- **Title:** Chart Structure Fundamentals — Zero-Assumption Technical Definitions & Detectors
- **Scope:** Complete, beginner-to-programmer definitions and step-by-step detection procedures for candlesticks, timeframes, swing highs/lows, market trend vs. consolidation, support/resistance zones, level grading (freshness, double bottoms, S/R flips), range extremes vs. midpoints, liquidity sweeps, Initial Balance (IB), and Killzones.
- **Sources Used:** SRC-03 (Academy Ch. 1: Charts First)[cite: 1], SRC-06 (Academy Ch. 4)[cite: 1], SRC-08 (Academy Ch. 6)[cite: 1], SRC-12 (Academy Ch. 10)[cite: 1], SRC-25 (Academy Flowseeker Ch. 10)[cite: 1], SRC-29 (Docs Atlas Field Guide), SRC-30 (Changelog Oct 13–14, 2026).
- **Prerequisites:** `00_index.md`, `glossary.md`
- **Last Updated:** 2026-10-17

---

## Why This File Exists

Skylit's #1 rule is the **Charts First Doctrine**: *"Before a trader ever looks at a Heatseeker™ map, they must first understand how to read price structure on a chart. Heatseeker is not a signal generator. Heatseeker is a confirmation tool. Charts create the initial thesis... Without chart structure, exposure data becomes noise."* `[DOCTRINE: Academy Ch. 1, Sec. 1]`[cite: 1].

However, Skylit's Academy assumes the reader already knows what a candlestick, a swing high, or a support zone is, stating: *"If you do not have a basic understanding of market structure, technical analysis, support/resistance or liquidity, your experience trading with Skylit will be subpar."* `[DOCTRINE: Academy Ch. 1, Sec. 3]`[cite: 1].

This file supplies the exact mathematical and procedural definitions so an agent with **zero prior trading knowledge** can compute every chart-structure primitive deterministically.

---

## 1. Candlesticks (OHLCV Bars) & Timeframe Hierarchy

### 1.1 Definition
- **Plain sentence:** A candlestick (bar) summarizes how an asset's price moved during a fixed slice of time using four prices: where it started (Open), the highest price reached (High), the lowest price reached (Low), and where it finished (Close).
- **Precise version:** For a discrete time interval $[t_{\text{start}}, t_{\text{end}})$, a candlestick $C_i$ is a 6-tuple $(t_i, O_i, H_i, L_i, C_i, V_i)$ where:
  - $O_i =$ first traded price at or after $t_{\text{start}}$ `[GENERAL TA]`
  - $H_i = \max(\text{price})$ during $[t_{\text{start}}, t_{\text{end}})$ `[GENERAL TA]`
  - $L_i = \min(\text{price})$ during $[t_{\text{start}}, t_{\text{end}})$ `[GENERAL TA]`
  - $C_i =$ last traded price before $t_{\text{end}}$ `[GENERAL TA]`
  - $\text{Body}_i = |C_i - O_i|$ (Green/Bullish if $C_i > O_i$; Red/Bearish if $C_i < O_i$) `[GENERAL TA]`
  - $\text{Upper Wick}_i = H_i - \max(O_i, C_i)$ `[GENERAL TA]`
  - $\text{Lower Wick}_i = \min(O_i, C_i) - L_i$ `[GENERAL TA]`
  - $\text{Range}_i = H_i - L_i$ `[GENERAL TA]`

### 1.2 Timeframe Hierarchy & Atlas RTH Alignment
- **The Highway Analogy:** Skylit defines a strict top-down timeframe hierarchy for marking levels `[DOCTRINE: Academy Ch. 1, Sec. 4.3]`[cite: 1]:
  1. **Daily / Weekly (`1D`, `1W`) = "Major Highways":** Most significant structural levels; always mark first `[DOCTRINE: Academy Ch. 1, Sec. 4.3]`[cite: 1].
  2. **Hourly (`1H`, `4H`) = "Local Roads":** Intermediate structure (e.g., NQ hourly double bottom in Ch. 1; NQ hourly resistance in Ch. 4) `[DOCTRINE: Academy Ch. 1, Sec. 4.3 & 5; Ch. 4, Sec. 5]`[cite: 1].
  3. **5-Minute (`5m`, plus `1m`/`15m`) = "Side Streets":** Intraday execution timeframe (e.g., SPY 5-minute Atlas chart in Flowseeker Ch. 8) `[DOCTRINE: Academy Ch. 1, Sec. 4.3; Flowseeker Ch. 8, Sec. 5]`[cite: 1].
- **Atlas RTH Hourly Candle Alignment Rule:** When Regular Trading Hours (`Extended hours: Off`) is active on Atlas, hourly (`1H`) and multi-hour candles start at **09:30 ET** (reading `09:30`, `10:30`, `11:30`, etc.), rather than `09:00` or `10:00` `[DOCTRINE: Changelog Oct 13, 2026]`.

### 1.3 Inputs
- Raw trade ticks or aggregated OHLCV series for symbol $S$.
- Session mode (`RTH_ONLY = true/false`, where US equities RTH is `09:30:00` to `16:00:00` America/New_York) `[DOCTRINE: Changelog Oct 13, 2026]`.
- Target timeframes: `1W`, `1D`, `1H`, `15m`, `5m`, `1m`.

### 1.4 Procedure
1. Ingest OHLCV bars for `1D`, `1H`, and `5m`.
2. If `RTH_ONLY == true` and timeframe is `1H` or multi-hour (`< 1D`), anchor bar boundaries to `09:30 ET` (`09:30–10:30`, `10:30–11:30`, ..., `15:30–16:00`) `[DOCTRINE: Changelog Oct 13, 2026]`.
3. Process structure in top-down order: `1D` $\rightarrow$ `1H` $\rightarrow$ `5m` `[DOCTRINE: Academy Ch. 1, Sec. 4.3]`[cite: 1].

### 1.5 Edge Cases
- **Final RTH Hour (`15:30–16:00 ET`):** Because RTH is 6.5 hours (`09:30–16:00`), a `1H` chart anchored at `09:30 ET` has six 60-minute bars and one 30-minute bar (`15:30–16:00 ET`) `[INFERENCE]`.
- **Overnight Gaps:** When `RTH_ONLY == true`, $O_{\text{today, 09:30}} \neq C_{\text{yesterday, 16:00}}$. The price space between yesterday's RTH close (or high/low) and today's RTH open is an **overnight gap** (used in the SPY 755 gap-fill setup in `SRC-01`) `[DOCTRINE: Academy Dark Pool Course, Sec. 6]`[cite: 1].

### 1.6 Worked Example (Illustrative)
- Symbol: `SPY`, Timeframe: `5m`, Bar `09:30–09:35 ET`: Open = `580.00`, High = `581.20`, Low = `579.50`, Close = `580.80`.
- Calculation: Bullish candle ($580.80 > 580.00$); Body = $\$0.80$; Upper Wick = $581.20 - 580.80 = \$0.40$; Lower Wick = $580.00 - 579.50 = \$0.50$; Total Bar Range = $\$1.70$.

### 1.7 What It Is NOT
- A candlestick is **not** a Contract Flow bar in Flowseeker Drilldown (which measures option contract volume by bid/mid/ask) `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 4]`[cite: 1], nor is it an Atlas Flow Pane bar (which measures option dollar premium) `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 2]`[cite: 1].

### 1.8 Related Concepts
- See `session_timing.md` for RTH, Extended Hours, and Killzone windows.

---

## 2. Swing Highs and Swing Lows (Structural Building Blocks)

### 2.1 Definition
- **Plain sentence:** A **swing high** is a local peak where price moved up and then reversed lower; a **swing low** is a local trough where price moved down and then reversed higher `[DOCTRINE: Academy Ch. 1, Sec. 4.1]`[cite: 1].
- **Precise version:**
  - Skylit defines swing points qualitatively: *"A swing high is a peak where price reversed lower. A swing low is a trough where price reversed higher. These are your building blocks. Mark the ones that are obvious — if you have to squint to see it, it probably doesn't matter... The best levels are the ones you can spot in under five seconds."* `[DOCTRINE: Academy Ch. 1, Sec. 4.1 & 4.5]`[cite: 1].
  - Because Skylit does not specify the exact bar-count $k$ for automated code `[NOT SPECIFIED]`, a software agent implements a parameterized $k$-bar fractal + minimum reversal magnitude filter `[GENERAL TA]`:
    - Bar $i$ is a **Confirmed Swing High** of order $k$ if $H_i = \max_{j \in [i-k, i+k]} H_j$, $H_i > H_{i+k}$, and the subsequent drop from $H_i$ within $m$ bars is at least $\theta_{\text{ATR}} \times \text{ATR}_{14}$ `[GENERAL TA]`.
    - Bar $i$ is a **Confirmed Swing Low** of order $k$ if $L_i = \min_{j \in [i-k, i+k]} L_j$, $L_i < L_{i+k}$, and the subsequent bounce from $L_i$ within $m$ bars is at least $\theta_{\text{ATR}} \times \text{ATR}_{14}$ `[GENERAL TA]`.

### 2.2 Inputs
- OHLCV bar series on timeframe $TF \in \{\text{1D}, \text{1H}, \text{15m}, \text{5m}\}$.
- Fractal lookback/lookforward window $k$ (Default `[GENERAL TA]`: $k = 3$ to $5$ bars for minor swings; $k = 10$ to $20$ bars for major "obvious" swings; exact value `[NOT SPECIFIED]` in Skylit docs).
- Average True Range $\text{ATR}_{14}$ on timeframe $TF$ to measure reversal magnitude `[GENERAL TA]`.

### 2.3 Procedure (Major vs. Minor Swing Detection & Deduplication)
1. **Compute ATR(14):** For each bar $i$, compute True Range $TR_i = \max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|)$ and $\text{ATR}_{14, i} = \text{SMA}_{14}(TR)$ `[GENERAL TA]`.
2. **Identify Candidate Swing Highs/Lows:**
   - Scan closed bars up to current bar $t - k$ (a swing point at $i$ is only confirmed once $k$ bars have closed after $i$, preventing lookahead bias) `[GENERAL TA]`.
3. **Filter for "Obvious" (Major) vs. Minor Swings:**
   - Skylit states: *"levels created by large, dramatic reversals carry more weight than those formed by shallow ones, because a bigger reaction signals stronger conviction from market participants"* and *"sharp reversals carry more weight than slow, grinding reactions"* `[DOCTRINE: Academy Ch. 1, Sec. 4]`[cite: 1].
   - Compute **Reversal Magnitude** $\Delta_{\text{rev}}$: the maximum price excursion away from $H_i$ (or $L_i$) before $H_i$ (or $L_i$) is breached `[GENERAL TA]`.
   - Compute **Reversal Speed** $v_{\text{rev}} = \Delta_{\text{rev}} / N_{\text{bars}}$ over the first 3–5 bars after $i$ `[GENERAL TA]`.
   - Classify as **Major Swing** if $\Delta_{\text{rev}} \ge 1.5 \times \text{ATR}_{14}$ and $k \ge 5$; classify as **Minor Swing** if $0.75 \times \text{ATR}_{14} \le \Delta_{\text{rev}} < 1.5 \times \text{ATR}_{14}$; discard as noise if $\Delta_{\text{rev}} < 0.75 \times \text{ATR}_{14}$ `[GENERAL TA]` *(exact multipliers `[NOT SPECIFIED]` in Skylit docs)*.
4. **Merge Nearby Candidates:** If two swing highs (or two swing lows) sit within a cluster tolerance $\epsilon_{\text{zone}}$ (e.g., $\le \$0.50$ on SPY/QQQ or $\le \$5.00$ on SPX, matching Skylit's deflection zone width `[INFERENCE]`), merge them into a single **Structural Zone** $[P_{\text{low}}, P_{\text{high}}]$ rather than drawing two separate lines `[DOCTRINE: Academy Ch. 1, Sec. 4: "Support is a price zone... Keep It Simple"]`[cite: 1].

### 2.4 Edge Cases
- **Equal Highs / Equal Lows (Ties):** If $H_i == H_{i+1}$ at a peak, treat the pair as a single swing high anchored at the later timestamp $i+1$ `[GENERAL TA]`. If two distinct swing lows occur separated by an intermediate swing high and sit within $\epsilon_{\text{zone}}$ of each other, classify the structure as a **Double Bottom** (see Section 4.4) `[DOCTRINE: Academy Ch. 1, Sec. 4.2]`[cite: 1].
- **Unconfirmed Right-Hand Side:** At the live right edge of the chart (fewer than $k$ bars since the high/low), a peak is an *unconfirmed intraday extreme*, not yet a historical swing point `[GENERAL TA]`.

### 2.5 Worked Example (Illustrative)
- On a `1H` QQQ chart ($\text{ATR}_{14} = \$2.00$), bar $i=50$ reaches $H_{50} = 612.00$. The preceding 5 bars and following 5 bars all have highs $< 612.00$. Over the next 4 bars, QQQ drops to $607.20$ ($\Delta_{\text{rev}} = \$4.80 = 2.4 \times \text{ATR}_{14}$).
- Result: $612.00$ is confirmed at bar $55$ as a **Major `1H` Swing High**.

### 2.6 What It Is NOT
- A single tall candle wick inside a choppy midday range that reverses by only $0.2 \times \text{ATR}$ is **not** a major swing point ("if you have to squint to see it, it probably doesn't matter") `[DOCTRINE: Academy Ch. 1, Sec. 4.1]`[cite: 1].

### 2.7 Related Concepts
- Used directly to build **Support and Resistance Zones** (Section 4) and **Trend vs. Range State** (Section 3).

---

## 3. Market Structure State: Trend vs. Range (Consolidation)

### 3.1 Definition
- **Plain sentence:** A market is **trending up** when it makes a sequence of higher swing highs and higher swing lows, **trending down** when it makes lower swing highs and lower swing lows, and **ranging (consolidating)** when it oscillates horizontally between a fixed upper boundary (Range High) and lower boundary (Range Low) `[DOCTRINE: Academy Ch. 1, Sec. 2 & 4]`[cite: 1].
- **Precise version:** Given the last two confirmed Major Swing Highs ($SH_{n-1}, SH_n$) and last two confirmed Major Swing Lows ($SL_{n-1}, SL_n$) on timeframe $TF$:
  - **Uptrend (`TREND_UP`):** $SH_n > SH_{n-1} + \epsilon_{\text{zone}}$ AND $SL_n > SL_{n-1} + \epsilon_{\text{zone}}$ (`Higher Highs + Higher Lows`) `[DOCTRINE: Academy Ch. 1, Sec. 2]`[cite: 1].
  - **Downtrend (`TREND_DOWN`):** $SH_n < SH_{n-1} - \epsilon_{\text{zone}}$ AND $SL_n < SL_{n-1} - \epsilon_{\text{zone}}$ (`Lower Highs + Lower Lows`) `[DOCTRINE: Academy Ch. 1, Sec. 2]`[cite: 1].
  - **Range-Bound (`RANGE`):** Swing highs stall near a common ceiling ($|SH_n - SH_{n-1}| \le \epsilon_{\text{zone}}$) and/or price oscillates inside a bounded horizontal channel $[R_{\text{low}}, R_{\text{high}}]$ without establishing progressive higher/lower swings `[DOCTRINE: Academy Ch. 1, Sec. 4 & 5]`[cite: 1].

### 3.2 Inputs
- Ordered sequence of confirmed Major Swing Highs and Swing Lows on timeframe $TF$.
- Current spot price $P_{\text{spot}}$.

### 3.3 Procedure (Range Geometry & Extreme vs. Midpoint Classification)
When market structure is in a `RANGE` (or bounded between two structural levels $R_{\text{low}}$ and $R_{\text{high}}$):
1. **Define Range Boundaries:**
   - $\text{Range High } (R_{\text{high}}) = \text{upper structural resistance boundary}$ `[DOCTRINE: Academy Ch. 1, Sec. 4]`[cite: 1].
   - $\text{Range Low } (R_{\text{low}}) = \text{lower structural support boundary}$ `[DOCTRINE: Academy Ch. 1, Sec. 4]`[cite: 1].
   - $\text{Total Width } (W) = R_{\text{high}} - R_{\text{low}}$.
   - $\text{Midpoint } (R_{\text{mid}}) = \frac{R_{\text{high}} + R_{\text{low}}}{2}$ `[DOCTRINE: Academy Ch. 1, Sec. 4 & 5]`[cite: 1].
2. **Compute Relative Location $L_{\%} \in [0, 1]$:**
   $$L_{\%} = \frac{P_{\text{spot}} - R_{\text{low}}}{R_{\text{high}} - R_{\text{low}}}$$
3. **Classify Price Location Zone:**
   - Skylit states: *"A range consists of three primary zones: 1. Range High, 2. Range Low, 3. Midpoint. Range highs and lows represent extremes. The midpoint represents equilibrium... We trade extremes. We avoid midpoints."* `[DOCTRINE: Academy Ch. 1, Sec. 4]`[cite: 1].
   - Because Skylit requires a **minimum 3:1 Reward-to-Risk** to the opposite side of the range `[DOCTRINE: Academy Ch. 3, Sec. 6; Ch. 6, Sec. 9]`[cite: 1], mathematically price must be within the outer **25%** of the range (or within the node Deflection Zone of $R_{\text{low}}$ / $R_{\text{high}}$) to achieve $\ge 3:1$ R:R across the range `[INFERENCE]`:
     - **`AT