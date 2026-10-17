# session_timing.md

- **Title:** Session Timing, Intraday Regimes & Daily Operational Schedule
- **Scope:** Zero-assumption definitions, time-of-day mechanical rules (Pre-market prep, Opening scramble, Drives Away vs. Pin Jobs, Killzones, Initial Balance, RTH candle alignment, After-close, and Next-morning OI clearing), inputs, procedures, edge cases, and worked examples.
- **Sources Used:** SRC-03 (Academy Ch. 1)[cite: 1], SRC-05 (Academy Ch. 3, Sec. 4)[cite: 1], SRC-10 (Academy Ch. 8, Sec. 5)[cite: 1], SRC-14 (Academy Ch. 12, Case Study #1)[cite: 1], SRC-19 (Academy Flowseeker Ch. 4, Sec. 2 & 4)[cite: 1], SRC-21 (Academy Flowseeker Ch. 6, Sec. 3)[cite: 1], SRC-25 (Academy Flowseeker Ch. 10, Sec. 4)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 5, 7, 8)[cite: 1], SRC-27 (Docs Core Concepts: King Nodes & Gatekeepers), SRC-30 (Changelog Oct 13, 2026: RTH Hourly Candles, Killzones, Initial Balance).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. Intraday Time-of-Day Phases & Dealer Pinning Mechanics

### 1.1 Definition
- **Plain sentence:** Market Maker hedging behavior and data availability change systematically across the trading day—from pre-market chart preparation, to early-session map reshuffles and Gatekeeper rejections, to late-day King Node pinning into the 16:00 ET NYSE close, to overnight Open Interest clearing[cite: 1].
- **Precise version:** All Skylit session rules are anchored to **New York Time (`America/New_York`, ET)** `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 4; Changelog Oct 13, 2026]`[cite: 1]. An agent partitions the 24-hour cycle into five operational phases and modifies node behavioral expectations according to the time of day `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 4; Docs Core Concepts]`[cite: 1]:

| Phase (New York Time, ET) | Market Microstructure & Dealer Behavior | Mandatory Agent / Trader Workflow | Source Tag |
| :--- | :--- | :--- | :--- |
| **1. Pre-Market (`< 09:30 ET`)** | Overnight gaps form; today's Open Interest has updated from yesterday's clearing cycle[cite: 1]. | Mark higher-timeframe (`1D`, `1H`) support/resistance levels, read the initial Heatseeker maps, check next-day OI changes on tracked contracts, and write testable playbook questions before the bell[cite: 1]. | `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 4; Ch. 10, Sec. 4]`[cite: 1] |
| **2. Opening Scramble & Initial Balance (`09:30–10:30 ET`)** | Immediate map reshuffle right out of the gate as opening orders clear[cite: 1]. Rapid 100%+ node accumulation in Velocity Mode can trigger immediate directional dumps/rallies from the open[cite: 1]. | Allow the initial opening reshuffle to stabilize (e.g., 12 minutes after open in Ch. 12 Case Study #1)[cite: 1]. Track `Initial Balance` (`15m`, `30m`, or `60m`) and `NYAM` Killzone[cite: 1]. Expect **Gatekeeper rejections** and **"Drives Away"** if price hits the King Node too early. | `[DOCTRINE: Academy Ch. 8, Sec. 5; Ch. 12, Sec. 2; Docs Core Concepts; Changelog Oct 13, 2026]`[cite: 1] |
| **3. Midday / Main Session (`10:30–15:00 ET`)** | Structure stabilizes into the day's primary Gamma Regime (`Range/Choppy`, `Trend`, or `Whipsaw`)[cite: 1]. Taps accrue on tested nodes, degrading their stopping power over time[cite: 1]. | Watch flow strictly **at planned structural levels**; fill in Playbooks; re-evaluate bias whenever the map reshuffles or floors/ceilings roll[cite: 1]. Avoid midday midpoint chop and theta burn in `+GEX`[cite: 1]. | `[DOCTRINE: Academy Ch. 4, Sec. 3; Ch. 8, Sec. 8; Flowseeker Ch. 10, Sec. 4]`[cite: 1] |
| **4. Late Session / Into the Close (`15:00–16:00 ET`)** | Dealer gamma pull toward the **King Node** intensifies into the `16:00 ET` NYSE equity close (**"Pin Job"**)[cite: 1]. | Recognize that the King Node represents the strike where Market Makers are most likely to pin price when the NYSE closes[cite: 1]. | `[DOCTRINE: Academy Ch. 3, Sec. 4; Docs Core Concepts]`[cite: 1] |
| **5. Post-Close (`> 16:00 ET`) & Weekly Review** | Options tape closes; Flow Scanner rolls up daily contract totals (some data remains preliminary until overnight OCC clearing)[cite: 1]. | Review contract totals in Flow Scanner; note that intraday OI still reflects the prior clearing until tomorrow morning[cite: 1]. Run weekly audit of wins, losses, **and skipped setups**[cite: 1]. | `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 3; Ch. 10, Sec. 4 & 5]`[cite: 1] |

---

## 2. Early-Session "Drives Away" vs. Late-Session "Pin Jobs" at the King Node

### 2.1 Definition
- **Plain sentence:** How price behaves at the King Node depends on *when* price reaches it: early in the session, dealers often push price away from the King Node (**"Drives Away"**) or reject price at an intermediary **Gatekeeper**, whereas late in the session, dealers pull and hold price tightly around the King Node into the 16:00 ET close (**"Pin Job"**) `[DOCTRINE: Academy Ch. 3, Sec. 4; Docs Core Concepts]`[cite: 1].
- **Precise version:**
  1. **Early-Session King Node Interaction (`Drives Away` & `Gatekeeper Defense`):**
     - In `skylit.ai/docs/core-concepts`, Skylit explains that when price reaches the King Node **early in the session**, Market Makers generally cannot pin price at one strike for the entire 6.5-hour session; instead, price frequently experiences a **"Drive Away"** (pushing away from the King Node toward outer structure) `[DOCTRINE: Docs Core Concepts]`.
     - Similarly, **Gatekeeper Nodes** are most active **early in the day**, acting like bouncers that block price from reaching the King Node prematurely and deflecting price back into its opening range `[DOCTRINE: Docs Core Concepts]`.
  2. **Late-Session King Node Interaction (`Pin Job`):**
     - As time to expiration ($T \to 0$) decays toward the `16:00 ET` NYSE close, the King Node acts as the primary gravitational pin (`"The king node represents the strike where Market Makers are most likely to pin price at when the NYSE closes."`) `[DOCTRINE: Academy Ch. 3, Sec. 4; Docs Core Concepts]`[cite: 1].
  - *(Note: Exact clock timestamps separating "early in the session" from "late in the session / into the close" are `[NOT SPECIFIED]` in the Skylit text; see Section 2.3 for configurable agent defaults and `open_questions.md`.)*

### 2.2 Inputs
- Current timestamp $t_{\text{now}}$ in `America/New_York` (`ET`).
- Active Heatseeker map (`King Node` $K_{\text{King}}$, `Gatekeepers` $\{K_{\text{GK}}\}$, `Gamma Regime`).
- Spot price $P_{\text{spot}}$ and node tap count $N_{\text{taps}}$.

### 2.3 Procedure (Time-Weighted Node Evaluation)
1. **Check Opening Stabilization (`09:30–09:45 ET`):**
   - At `09:30 ET`, monitor the immediate opening map reshuffle and Velocity Mode (`1m` / `5m` `% change`) `[DOCTRINE: Academy Ch. 8, Sec. 5; Ch. 12, Sec. 2]`[cite: 1].
   - Wait for the initial opening structure to settle (e.g., `10–15 minutes` after open, as in Ch. 12 Case Study #1 at `09:42 ET`) before locking in a range-fade game plan `[INFERENCE from Academy Ch. 12, Sec. 2]`[cite: 1].
2. **Apply Early-Session Rules (`09:30–11:30 ET` `[GENERAL TA]` window for "early session"):**
   - Give full weight to fresh **Gatekeeper deflections** ($N_{\text{taps}} == 0$) `[DOCTRINE: Docs Core Concepts]`.
   - If $P_{\text{spot}}$ opens on or immediately taps $K_{\text{King}}$ early in the morning, do **not** assume price will sit dead at $K_{\text{King}}$ all day; watch for a **Drive Away** toward the outer Floor or Ceiling `[DOCTRINE: Docs Core Concepts]`.
3. **Apply Late-Session Pinning Rules (`14:30–16:00 ET` `[GENERAL TA]` window for "late session"):**
   - In a Positive Gamma (`+GEX`) regime, expect compression and magnetic pinning toward $K_{\text{King}}$ into `16:00 ET` (`Pin Job`) `[DOCTRINE: Academy Ch. 3, Sec. 4; Docs Core Concepts]`[cite: 1].
   - Avoid buying OTM options expecting a fresh breakout away from a positive-gamma King Node late in the session due to accelerated theta decay `[INFERENCE from Academy Ch. 3, Sec. 4 & Ch. 8, Sec. 8]`[cite: 1].

---

## 3. Atlas Time-Based Indicators & Candle Alignment Rules

### 3.1 RTH Hourly Candle Start Time (`09:30 ET` Rule)
- **Rule:** When `Extended hours` is turned **Off** on Atlas, hourly (`1H`) and multi-hour candles start at the **`09:30 ET` RTH open** (so a `1H` chart stamps bars at `09:30`, `10:30`, `11:30`, `12:30`, `13:30`, `14:30`, and `15:30 ET`) `[DOCTRINE: Changelog Oct 13, 2026]`.
- Charts with `Extended hours` turned **On**, and timeframes of `1D` and above, remain unchanged `[DOCTRINE: Changelog Oct 13, 2026]`.

### 3.2 Initial Balance (`IB`) & `Killzones` Indicators
1. **`Initial Balance` Indicator (`[DOCTRINE: Changelog Oct 13, 2026]`):**
   - Configurable for three global sessions: **New York**, **London**, and **Asia**.
   - Configurable opening range durations: **`15`**, **`30`**, or **`60` minutes**.
   - Plots: `IB High`, `IB Low`, `IB Midpoint`, retracement lines (`golden zone`), and an optional extension ladder within its own session.
2. **`Killzones` Indicator (`[DOCTRINE: Changelog Oct 13, 2026]`):**
   - Replaces the legacy `Session Zones` indicator on timeframes **`30m` and under**.
   - Draws session boxes with High and Low horizontal lines for four windows: **`ASIA`**, **`LNDN`**, **`NYAM`**, and **`NYPM`** (each with customizable color, label, and start/end times). *(Exact default clock start/end timestamps for `ASIA`, `LNDN`, `NYAM`, `NYPM` are user-configurable in the indicator cog and `[NOT SPECIFIED]` in the changelog text; see `open_questions.md`.)*

### 3.3 Time-Window Scoping in Flowseeker & Talon
- **Default Session Window vs. Bounded Window:** In Talon Chat, strike flow queries default to the **entire trading session** unless bounded by the user; to inspect immediate momentum, explicitly specify `"right now"` or `"in the last fifteen minutes"` `[DOCTRINE: Academy Talon Prompt Guide, Sec. 8]`[cite: 1].
- **Trade Date Filter Default:** On Atlas, the trade date filter defaults to **`Today`**, and clicking `Reset` returns the filter to `Today` rather than clearing it `[DOCTRINE: Changelog Oct 13, 2026]`.

---

## 4. Edge Cases
- **Pre-Announcement Macro Event Pinning:** Ahead of scheduled economic releases (`FOMC`, `CPI`, `JOLTS`, `NFP`) or earnings, price frequently sits pinned on a key level in a choppy range (`Type 1 Day`) while large far-OTM **Hedge Nodes** appear on the map `[DOCTRINE: Academy Ch. 4, Sec. 3 Type 1; Docs Core Concepts: Hedge Nodes]`[cite: 1]. Once the event prints, those Hedge Nodes rapidly unwind/decay `[DOCTRINE: Docs Core Concepts]`.
- **Earnings Timing Limit in Talon:** When querying company earnings (`when do they report?`), Talon provides the **calendar date** but **never claims before-the-bell (`BMO`) vs. after-the-bell (`AMC`)**, because session timing is not in the underlying feed `[DOCTRINE: Academy Talon Prompt Guide, Sec. 7 & 9]`[cite: 1].
- **Late-Day Open Interest Reads in Flow Scanner:** Even at `15:55 ET` or `16:30 ET`, the `Open Interest` column in Flow Scanner still reflects the **previous clearing cycle (yesterday's close)** until overnight OCC processing finishes for the next morning `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 2; Ch. 6, Sec. 3, Quiz Q9; Ch. 10, Sec. 4]`[cite: 1].

---

## 5. Worked Examples
1. **Opening Velocity Dump (`[DOCTRINE: Academy Ch. 8, Sec. 5]`):** At the `09:30 ET` open, `SPX` downside nodes show a rapid `100%` increase in Velocity Mode (`Rate of Change`), driving an immediate 15-point dump from the open[cite: 1].
2. **12-Minute Post-Open Range Plan (`[DOCTRINE: Academy Ch. 12, Sec. 2 Case Study #1 — Feb 25, 2026]`):** Following an immediate map reshuffle out of the gate, by `09:42 ET` (12 minutes after the open), `SPY` settles sandwiched between two positive-gamma Pika nodes at `690` (Floor) and `692` (Ceiling) while `SPX` holds a `6920` floor[cite: 1]. The trader establishes the morning plan: fade `692` with puts and `690` with calls[cite: 1].

---

## 6. What It Is NOT
- Time of day is **not** a determinant of node strength (`"Which factor determines node strength? Absolute value"`, not `"Time of day"`) `[DOCTRINE: Academy Ch. 2, Quiz Q3; Ch. 3, Quiz Q7; Ch. 13, Quiz Q10]`[cite: 1]. Rather, time of day governs *how* dealers manage pinning (`Drives Away` early vs. `Pin Job` late) and *when* data clears (OI overnight).

---

## 7. Related Concepts
- See `chart_structure_fundamentals.md` (Section 1.2 & 5) and `risk_sizing_and_discipline.md` (Daily Routine).

---

## Agent Checklist (`session_timing.md`)

1. **Anchor All Timestamps to `America/New_York` (`ET`):** Align RTH `1H` candles to `09:30 ET` when `Extended_Hours == FALSE`[cite: 1].
2. **Enforce the 5-Stage Daily Schedule:** Run pre-market level/map prep before `09:30 ET`, allow opening reshuffle stabilization, monitor flow only at planned levels during RTH, audit preliminary totals post-close, and verify $\Delta \text{OI}$ the next morning[cite: 1].
3. **Tag Intraday OI with `AS_OF_PRIOR_CLEARING`:** Never treat intraday OI changes as live during the same session[cite: 1].