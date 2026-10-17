# dark_pool.md

- **Title:** Dark Pool Intelligence — Off-Exchange Equity Blocks, The Dark Feed, Atlas Dark Pool Levels & Confluence
- **Scope:** Zero-assumption definitions, the 6 honest data fields, why dark pool prints are strictly directionless, Dark Feed filtering ($1M default floor), Atlas Dark Pool horizontal level overlays (`Top N`, `Lookback`), Talon's 5-session window, and confluence rules with Heatseeker nodes and chart gaps.
- **Sources Used:** SRC-01 (Academy Course: Reading The Dark Pool Prints, Sec. 1–6 & Quiz Q1–Q6)[cite: 1], SRC-02 (Academy Atlas Course, Sec. 2)[cite: 1], SRC-21 (Academy Flowseeker Ch. 6, Sec. 2 & 3, Quiz Q3, Q5, Q7, Q10)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 6 & 9)[cite: 1], SRC-30 (Changelog Oct 14, 2026).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `heatseeker_node_core_attributes.md`
- **Last Updated:** 2026-10-17

---

## 1. What a Dark Pool Print Actually Is (And The #1 Rule: Directionlessness)

### 1.1 Definition
- **Plain sentence:** A dark pool print is a large block of **common stock (equity shares)** that traded off-exchange between two parties and was reported afterward; it has **no directional side** (it is neither a "buy" nor a "sell"—every share had both a buyer and a seller at that exact price), so it identifies **where** large dollar conviction transacted, never whether institutions are bullish or bearish `[DOCTRINE: Academy Dark Pool Course, Sec. 1 & 2]`[cite: 1].
- **Precise version:** An off-exchange equity print $DP_k$ is a 6-tuple of factual fields `[DOCTRINE: Academy Dark Pool Course, Sec. 2 & Quiz Q1]`[cite: 1]:
  $$DP_k = (t_{\text{report}}, \text{Ticker}, P_{\text{print}}, \text{Size}_{\text{shares}}, \text{Notional}_{$}, \text{Sector}_{\text{GICS}})$$
  where:
  1. **`Date/Time` ($t_{\text{report}}$):** Timestamp when the off-exchange trade was reported[cite: 1].
  2. **`Ticker`:** The underlying stock or ETF symbol[cite: 1].
  3. **`Price` ($P_{\text{print}}$):** The exact share price at which the block transacted[cite: 1].
  4. **`Size` ($\text{Size}_{\text{shares}}$):** Number of **equity shares** traded (distinct from option contracts in Flowseeker!) `[DOCTRINE: Academy Dark Pool Course, Sec. 2; Flowseeker Ch. 6, Sec. 2, Quiz Q3 & Q5]`[cite: 1].
  5. **`Notional` ($\text{Notional}_{$}$):** Total dollar value of the block, calculated as:
     $$\text{Notional (\$)} = \text{Size}_{\text{shares}} \times P_{\text{print}}$$
     `[DOCTRINE: Academy Dark Pool Course, Sec. 2]`[cite: 1].
  6. **`Sector` ($\text{Sector}_{\text{GICS}}$):** The GICS sector classification of the ticker[cite: 1].

### 1.2 Why Dark Pool Prints Never Carry Flow Score, Sweep Tags, Greeks, or Sentiment
- Off-exchange equity blocks do **not** carry options bid/ask quotes, aggressor side, expiration dates, or option structure data `[DOCTRINE: Academy Dark Pool Course, Sec. 1 & Quiz Q5; Flowseeker Ch. 6, Sec. 3, Quiz Q7]`[cite: 1].
- **Agent Guardrail:** *"No dark pool print carries a side. If a tool tells you a specific dark pool print was 'bullish' or 'a buy,' it's inventing information that doesn't exist in the data. Treat that as a red flag about the tool, not a signal about the stock."* `[DOCTRINE: Academy Dark Pool Course, Sec. 1]`[cite: 1]. Whether a print occurs above spot or below spot does **not** make it bullish or bearish `[DOCTRINE: Academy Dark Pool Course, Quiz Q1]`[cite: 1].

### 1.3 What You CAN Read From a Dark Pool Print
1. **`Notional` = Conviction Scale:** A $\$2\text{B}$ block and a $\$50\text{M}$ block represent vastly different levels of institutional dollar commitment `[DOCTRINE: Academy Dark Pool Course, Sec. 2]`[cite: 1].
2. **`Price` = A Level That Mattered:** Real institutional size was willing to transact at $P_{\text{print}}$, making that exact price a potential soft support or resistance reaction zone when revisited `[DOCTRINE: Academy Dark Pool Course, Sec. 2 & 4, Quiz Q3]`[cite: 1].
3. **`Ticker` & `Sector` = Concentration of Attention:** A cluster of large blocks in one symbol or sector shows where large capital is active `[DOCTRINE: Academy Dark Pool Course, Sec. 2]`[cite: 1].

---

## 2. The Three Dark Pool Interfaces in Skylit

| Interface | Location in Platform | Scope & Controls | What It Displays | Source Tag |
| :--- | :--- | :--- | :--- | :--- |
| **1. Dark Feed** | Flowseeker (`Dark Feed` tab) & Talon (`Live Feed`'s `Dark pool tape` view as of Oct 14, 2026) | Defaults to **`$1,000,000` notional minimum** ("whale tape by default"); supports date/time range, notional min/max, share size min/max, share price min/max, sector, `!TICKER` exclusion, pause/resume queue, CSV/image export[cite: 1]. | Time-ordered live or historical scrolling tape of individual blocks[cite: 1]. *(Lowering the $\$1\text{M}$ floor risks turning the size-focused tape into a noisy firehose `[DOCTRINE: Dark Pool Course, Sec. 3 & Quiz Q2]`[cite: 1].)* | `[DOCTRINE: Academy Dark Pool Course, Sec. 3; Changelog Oct 14, 2026]`[cite: 1] |
| **2. Atlas Dark Pool Overlay** | Atlas Chart Settings (`Dark Pool` toggle) | **`Top N`:** `1`, `2`, `3`, or `5` largest prints.<br>**`Lookback`:** `30`, `45`, `90`, or `180` days[cite: 1]. | Renders the `Top N` **largest** prints found within the `Lookback` window as **horizontal dashed lines** across the entire chart at their exact transaction price, labeled with notional and date (e.g., `DP $2.2B · 5/15`)[cite: 1]. | `[DOCTRINE: Academy Dark Pool Course, Sec. 4 & Quiz Q4, Q6]`[cite: 1] |
| **3. Talon Chat Dark Pool Read** | Talon Chat (`any dark pool prints?` / `any block trades in this name?`) | **Fixed 5-session lookback window** (no custom lookback argument; asking for 3 months returns a polite refusal rather than a guess)[cite: 1]. | Summarizes recent off-exchange block size over the trailing 5 sessions[cite: 1]. | `[DOCTRINE: Academy Talon Prompt Guide, Sec. 6 & 9]`[cite: 1] |

### 2.1 Critical Geometry Rules for the Atlas Dark Pool Overlay
- **Single Real Print, Not Volume Profile:** Each horizontal dashed line on Atlas represents **one single real block print at its exact transaction price**—it is **not** a volume-weighted average price (VWAP), **not** a price band, and **not** accumulated volume across multiple trades at that level `[DOCTRINE: Academy Dark Pool Course, Sec. 4]`[cite: 1].
- **Why the Line Spans Horizontally:** The line spans horizontally across the entire chart because it represents a **price level**, not a timestamp; the date of the print is encoded in the text label (`DP $2.2B · 5/15`), which also distinguishes two large blocks that printed on different dates near the same price `[DOCTRINE: Academy Dark Pool Course, Sec. 4 & Quiz Q6]`[cite: 1].
- **`Top N` + `Lookback` Selection Logic:** Setting `Top N = 3` and `Lookback = 90D` plots the **three largest notional prints found within the last 90 days** (not the 3 most recent prints, and not the 3 prints closest to spot) `[DOCTRINE: Academy Dark Pool Course, Sec. 4 & Quiz Q4]`[cite: 1].

---

## 3. Procedure: Combining Dark Pool with Atlas Charts & Heatseeker Nodes

### 3.1 Inputs
- Live Dark Feed stream (`Notional >= $1,000,000`).
- Atlas Dark Pool Overlay levels (`Top N` in `{1, 2, 3, 5}`, `Lookback` in `{30, 45, 90, 180}` days).
- Atlas Price Chart structure (S/R zones, gap-fill levels) and Heatseeker GEX/VEX node map.

### 3.2 Step-by-Step Confluence Workflow (`[DOCTRINE: Academy Dark Pool Course, Sec. 5 & 6]`)
1. **Monitor the Dark Feed for Fresh Whales:** Spot an unusually large notional block printing in a target ticker (e.g., `MSFT` printing `3,200,000` shares at `421.50` for `$1.35B` notional) `[DOCTRINE: Academy Dark Pool Course, Sec. 5 & Quiz Q3]`[cite: 1].
2. **Map Spatial Context on Atlas:** Open the ticker on Atlas with the Dark Pool overlay enabled. Check where the fresh print (or historical `Top N` line) sits relative to current spot price and existing historical block levels: is it printing right on an old block level that the market repeatedly respects, or in new territory? `[DOCTRINE: Academy Dark Pool Course, Sec. 5]`[cite: 1].
3. **Evaluate Heatseeker Node Confluence (The Decisive Step):**
   - Compare the Dark Pool price level $P_{\text{DP}}$ against the active Heatseeker GEX/VEX node map `[DOCTRINE: Academy Dark Pool Course, Sec. 5]`[cite: 1]:
     - **Case A — Dark Pool Level in "Structural No-Man's-Land" (No Node Nearby):** Low actionability on its own; treat merely as background context `[DOCTRINE: Academy Dark Pool Course, Sec. 5]`[cite: 1].
     - **Case B — Dark Pool Level Aligned with a Major Heatseeker Node (King Node / Floor / Ceiling) + Chart Technical Level:** **High-Probability Confluence Zone.** The Dark Pool level confirms that massive institutional equity size transacted at that exact price, while the Heatseeker node confirms that options dealers are mechanically forced to hedge there `[DOCTRINE: Academy Dark Pool Course, Sec. 5 & 6; Atlas Course, Sec. 2]`[cite: 1].

---

## 4. Worked Examples
1. **Reading a Single Row (`[DOCTRINE: Academy Dark Pool Course, Quiz Q3]`):**
   - **Input Row:** `Ticker: MSFT | Price: 421.50 | Size: 3,200,000 | Notional: $1.35B | Sector: Technology`[cite: 1].
   - **Correct Agent Read:** *"Large off-exchange size transacted at 421.50; that price may become a level to watch"* (never *"MSFT has confirmed institutional buying at 421.50"*)[cite: 1].
2. **Chart + Dark Pool + Heatseeker Triple Confluence (`[DOCTRINE: Academy Dark Pool Course, Sec. 6 Case Study]`):**
   - **Layer 1 (Chart TA):** `SPY` chart shows a gap fill into **`755`**[cite: 1].
   - **Layer 2 (Dark Pool):** A large dark pool block prints directly into the `755` gap fill[cite: 1].
   - **Layer 3 (Heatseeker):** Heatseeker shows a **Floor node** being put in directly in line with `755`[cite: 1].
   - **Result:** Triple confluence (`TA Gap Fill + Dark Pool Level + Heatseeker Floor`) creates a high-conviction bullish deflection floor at `755` `[DOCTRINE: Academy Dark Pool Course, Sec. 6]`[cite: 1].

---

## 5. What It Is NOT
- A Dark Pool level is **not** a directional buy/sell signal, **not** an options trade, and **not** a cumulative volume-at-price histogram `[DOCTRINE: Academy Dark Pool Course, Sec. 1, 2, 4]`[cite: 1].

---

## 6. Related Concepts
- See `flowseeker_options_flow.md` (Section 6.1) and `charts_first_workflow.md`.

---

## Agent Checklist (`dark_pool.md`)

1. **Strip All Directional Labels from Dark Pool Data:** Never tag a dark pool print as `BULLISH`, `BEARISH`, `BUY`, or `SELL`[cite: 1].
2. **Keep Share Units Separate from Contract Units:** Never add Dark Feed `Size` (equity shares) to Flowseeker `Size` (option contracts)[cite: 1].
3. **Boost Confluence Score When $\vert{}P_{\text{DP}} - K_{\text{Node}}\vert{} \le \delta_{\text{deflect}}$:** When a `Top N` Dark Pool level falls inside the Deflection Zone of a major Heatseeker Floor, Ceiling, or King Node, flag the node as `HAS_DARK_POOL_CONFLUENCE = TRUE`[cite: 1].