# flowseeker_options_flow.md

- **Title:** Flowseeker Options Flow — Complete Tape Reading, Scoring, View Units, Chart Subgraph & Conflict Resolution Specification
- **Scope:** Zero-assumption definitions, formulas, and procedures for every concept in the 10-chapter Flowseeker curriculum: contract fields, Spot at Execution, Premium vs. Size, Moneyness, DTE, Bid/Mid/Ask side matrix, Sweeps, Multi-Leg detection, Volume vs. Open Interest lifecycle, Flow Score ($-100$ to $+100$), Premium Percentile Rank / FlowBonus, Tab hygiene, Summary Bar vs. Filtered View, the 7 Flowseeker views, the Atlas Flow Pane, and the 5-explanation Price-vs-Flow Conflict Protocol.
- **Sources Used:** SRC-16 through SRC-25 (Academy Flowseeker Chapters 1–10)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 5 & 9)[cite: 1], SRC-29 (Docs Flowseeker Field Guide), SRC-30 (Changelog Oct 13–14, 2026).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. Role of Options Flow in the Skylit Read (`Flow After Structure`)

### 1.1 Definition
- **Plain sentence:** Options flow is the chronological stream of executed options trades (**prints**) across the market; in Skylit, flow is evaluated **third** (after the Price Chart and the Heatseeker Map) as supporting or conflicting evidence—never as a standalone trade signal `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 1–4]`[cite: 1].
- **Precise version:**
  - **What a Single Print CAN Tell You:** (1) Exact contract (`Underlying`, `Expiration`, `Strike`, `Call/Put`), (2) `Size` (contracts) and `Premium` (dollars), (3) Exact execution timestamp and `Spot at Execution`, and (4) `Side` (where the fill occurred relative to the bid and ask) `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 5; Ch. 2, Sec. 2–5]`[cite: 1].
  - **What a Single Print CANNOT Tell You:** (1) **Who** traded it (never proves "an institution" or "smart money"), (2) **Why** they traded it, (3) Whether it **opened** a new position or **closed** an existing one, (4) Whether it is **hedged** against stock or another leg elsewhere, and (5) Whether the trader will be **right** `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 5]`[cite: 1].
  - **Observation vs. Interpretation Rule:** Always record the raw factual **Observation** first (*"2,000 SPY 590 calls — 11:14 ET — $620K premium — filled at the ask"*) before and separately from the **Interpretation** (*"Possibly new bullish positioning — needs the chart and the map to say whether it fits"*) `[DOCTRINE: Academy Flowseeker Ch. 1, Sec. 5]`[cite: 1].

---

## 2. Deconstructing a Single Print (Contract, Premium, Moneyness, DTE & Side)

### 2.1 The Four Contract-Defining Fields & Spot at Execution
1. **Contract Identity Tuple:** An options contract $C$ is uniquely defined by four fields:
   $$C = (\text{Underlying Ticker}, \text{Expiration Date}, \text{Strike Price } K, \text{Option Type } \in \{\text{CALL}, \text{PUT}\})$$
   Changing **any one** of the four fields makes it a **different contract**; never sum or merge prints across different expirations or strikes as if they were the same position `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 2, Quiz Q2 & Q3]`[cite: 1].
2. **Spot at Execution ($S_{\text{exec}}$):** Every print must be evaluated against the underlying price **at the exact timestamp the print filled ($S_{\text{exec}}$)**, never against current spot price later in the day `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 2 & 7, Quiz Q4]`[cite: 1].
3. **Cancelled Prints (`CANCELLED` Badge):** If an exchange voids a trade after execution, Flowseeker displays the row **struck through with a `CANCELLED` badge**. Agents must filter out and ignore all `CANCELLED` rows `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 2, Quiz Q5]`[cite: 1].

### 2.2 Premium vs. Size (Lead With Premium)
- **Definitions & Formula:**
  - $\text{Size} =$ number of option contracts traded `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 3]`[cite: 1].
  - $\text{Premium} =$ total dollar value exchanged `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 3]`[cite: 1].
  - For a standard equity/ETF option ($100\text{ shares per contract}$):
    $$\text{Premium (\$)} = \text{Fill Price} \times \text{Contracts (Size)} \times 100$$
    `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 3, Quiz Q6 & Q7]`[cite: 1].
- **Rule:** Always prioritize **Premium over Size** (e.g., 500 contracts at $\$8.00 = \$400,000$ commits $16\times$ more money than 1,000 cheap OTM contracts at $\$0.25 = \$25,000$) `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 3]`[cite: 1]. Remember that **premium is not risk** (a short option, hedge, or spread leg has a completely different risk profile for the same dollar premium) `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 3]`[cite: 1].

### 2.3 Moneyness & DTE Buckets
1. **Moneyness (Flowseeker Sign Convention):**
   - Measures percentage distance between the strike $K$ and spot at execution $S_{\text{exec}}$ `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 4]`[cite: 1].
   - **Critical Sign Convention:** In Flowseeker, **Positive (`+%`) = Out of the Money (OTM)**, **Negative (`-%`) = In the Money (ITM)**, and **Near `0%` = At the Money (ATM / close to spot)** `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 4, Quiz Q11 & Q15]`[cite: 1]:
     $$\text{Moneyness}_{\%} = \begin{cases} \frac{K - S_{\text{exec}}}{S_{\text{exec}}} \times 100\% & \text{if Option Type} == \text{CALL} \\ \frac{S_{\text{exec}} - K}{S_{\text{exec}}} \times 100\% & \text{if Option Type} == \text{PUT} \end{cases} \quad \text{[INFERENCE matching Flowseeker +\% = OTM rule]}$$
2. **DTE (Days to Expiration) Horizon Buckets:**
   - **`0–7 DTE`:** This week — event activity and very short-term positioning `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 4]`[cite: 1].
   - **`8–30 DTE`:** The classic window for positioning ahead of a move `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 4]`[cite: 1].
   - **`31+ DTE`:** Longer horizons, where portfolio hedging becomes more common `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 4]`[cite: 1].
   - *Guardrail:* Moneyness and DTE are **filters that narrow possibilities, not labels** that identify the trader `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 4, Quiz Q13]`[cite: 1].

### 2.4 Side Classification Matrix (Bid / Mid / Ask × Call / Put)
- **Side Buckets:** Based on where $\text{Fill Price}$ lands relative to the prevailing National Best Bid and Offer ($\text{Bid}, \text{Ask}$, with $\text{Mid} = (\text{Bid}+\text{Ask})/2$) at execution, Flowseeker groups fills into three buckets `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 5]`[cite: 1]:
  - **`Bid Side`:** Filled below, at, or just above the bid (seller in a hurry hitting the bid) `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 5]`[cite: 1].
  - **`Mid`:** Filled at the midpoint (no clean urgency read) `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 5]`[cite: 1].
  - **`Ask Side`:** Filled just below, at, or above the ask (buyer in a hurry paying the ask) `[DOCTRINE: Academy Flowseeker Ch. 2, Sec. 5]`[cite: 1].
  - *(Exact cent/percentage boundary between "just above bid / just below ask" and "Mid" is `[NOT SPECIFIED]` in public docs; see `open_questions.md`.)*

| Option Type | Execution Side | First Read (Initiator Clue) | Implied Dealer Gamma Effect (If Opening) | Directional First Read | Source Tag |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`CALL`** | **`Ask Side`** | Possible **Call Buying**[cite: 1] | Leaves dealers **Short Gamma (`-GEX`)**; strike tends to **break**[cite: 1] | **Bullish** first read[cite: 1] | `[DOCTRINE: Flowseeker Ch. 2, Sec. 5; Talon Guide, Sec. 5]`[cite: 1] |
| **`CALL`** | **`Bid Side`** | Possible **Call Selling (Writing)**[cite: 1] | Leaves dealers **Long Gamma (`+GEX`)**; strike tends to **hold/pin**[cite: 1] | **Bearish / Capped** first read[cite: 1] | `[DOCTRINE: Flowseeker Ch. 2, Sec. 5; Talon Guide, Sec. 5]`[cite: 1] |
| **`PUT`** | **`Ask Side`** | Possible **Put Buying**[cite: 1] | Leaves dealers **Short Gamma (`-GEX`)**[cite: 1] | **Bearish** first read *(Ask side $\neq$ Bullish!)*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 2, Sec. 5, Quiz Q18 & Q19]`[cite: 1] |
| **`PUT`** | **`Bid Side`** | Possible **Put Selling (Writing)**[cite: 1] | Leaves dealers **Long Gamma (`+GEX`)**[cite: 1] | **Bullish / Floor-building** first read[cite: 1] | `[DOCTRINE: Flowseeker Ch. 2, Sec. 5]`[cite: 1] |
| **Either** | **`Mid`** | Unclear initiator[cite: 1] | Indeterminate from print alone | **Not enough for a clean read**[cite: 1] | `[DOCTRINE: Flowseeker Ch. 2, Sec. 5]`[cite: 1] |

---

## 3. Sweeps and Multi-Leg Trades (Seeing the Whole Trade)

### 3.1 Sweeps (Urgency, Not Intent)
- **Definition & Grouping Rule:** When a trader wants large size immediately and no single exchange offers enough contracts at the best price, the order splits and **sweeps** across multiple exchanges simultaneously `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 2]`[cite: 1]. Flowseeker automatically groups prints on the **exact same contract within `1 second`** into a single **Sweep** row displaying total contracts and total premium `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 2, Quiz Q3 & Q4]`[cite: 1].
- **Interpretation Rule:** A sweep proves **urgency** (someone did not want to wait), **not intent** `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 3]`[cite: 1]. An urgent sweep can be: (1) opening a new bet, (2) closing an existing position, (3) hedging another position, or (4) filling one leg of a multi-leg spread `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 3, Quiz Q8]`[cite: 1]. Treat a sweep as a stronger clue than a passive print, but never as proof of "smart money" `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 3, Quiz Q9 & Q10]`[cite: 1].

### 3.2 Multi-Leg Trades & Reconstruction Procedure
- **Definition:** Trading two or more options contracts simultaneously as one package `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4]`[cite: 1]. Reading one leg in isolation can invert or distort the true meaning of the trade `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4]`[cite: 1]:
  - **Vertical Spread (e.g., Bull Call Spread):** Buy lower-strike call ($K_1$) at the ask + sell equal-sized higher-strike call ($K_2 > K_1$, same expiry) at the bid in the same second `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4 & 5]`[cite: 1]. Read together: a **bullish trade with capped upside at $K_2$** and net cost $= \text{Premium}(K_1) - \text{Premium}(K_2)$ `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4 & 5]`[cite: 1].
  - **Long Straddle:** Buy a call and buy a put at the **same strike and same expiry** `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4, Quiz Q14]`[cite: 1]. Read together: a non-directional bet on a **large move either way (volatility)** `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4, Quiz Q14]`[cite: 1].
- **Agent Procedure Before Naming Any Large Print:**
  - Because exchange/platform multi-leg detection is helpful but **not 100% certain** (`"not every strategy can be identified with certainty"`) `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4, Quiz Q17]`[cite: 1], whenever a large print appears at timestamp $t_0$ on ticker $S$, the agent must scan all prints on $S$ within $[t_0 - 2\text{s}, t_0 + 2\text{s}]$ `[INFERENCE from "in the same moment / same second" in Flowseeker Ch. 3, Sec. 4 & 5]`[cite: 1] to check for matching contract sizes or opposing legs before classifying the trade `[DOCTRINE: Academy Flowseeker Ch. 3, Sec. 4, Quiz Q16]`[cite: 1].

---

## 4. Volume vs. Open Interest (OI) Lifecycle

### 4.1 Definitions ("The Parking Garage Analogy")
- **Volume:** Total contracts traded **today** (every trade increments volume, like cars driving through a garage gate today) `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 2]`[cite: 1].
- **Open Interest (OI):** Total contracts **still open** as of the last clearing cycle (like cars parked inside when counted **last night**) `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 2]`[cite: 1].
- **Critical Timing Rule:** Open interest **does not update live intraday**. The OI displayed during today's session is **yesterday's closing number**; today's trades only appear in OI **tomorrow morning** `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 2 & 4, Quiz Q3, Q4, Q12]`[cite: 1].

### 4.2 Why `Volume > OI` and `Size > OI` Are Clues, Not Proof
- Flowseeker provides two toggles: **`Volume > OI`** (today's cumulative volume exceeds yesterday's OI) and **`Size > OI`** (a single print's size exceeds yesterday's OI) `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 3, Quiz Q10]`[cite: 1].
- **Intraday Round-Trip Trap (`[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 3, Quiz Q8 & Q9]`):** Suppose a contract starts the day with $\text{OI}_{\text{yesterday}} = 100$. In the morning, 500 new contracts are opened (both buyer and seller opening). In the afternoon, those exact 500 contracts are closed (both buyer and seller closing)[cite: 1].
  - Today's Volume $= 1,000$ ($10\times\text{OI}$), and the afternoon close of 500 is $5\times\text{OI}$ (`Size > OI` = `TRUE`)[cite: 1].
  - Yet **tomorrow's Open Interest is still `100`** ($\Delta \text{OI} = 0$)—nothing new stayed open overnight[cite: 1].

### 4.3 Next-Day Open Interest Clearing Matrix
When OI updates the next morning, its net change $\Delta \text{OI} = \text{OI}_{t+1} - \text{OI}_t$ obeys the 4-state clearing table `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 4, Quiz Q13–Q17]`[cite: 1]:

| Buyer Action | Seller Action | Next-Day Open Interest ($\Delta \text{OI}$) | What It Supports |
| :--- | :--- | :--- | :--- |
| **Opening** | **Opening** | **Goes UP ($\Delta \text{OI} > 0$)**[cite: 1] | More positions were opened than closed and held overnight (supports new position building)[cite: 1]. |
| **Opening** | **Closing** | **No Change ($\Delta \text{OI} = 0$)**[cite: 1] | Existing holder transferred position to a new holder[cite: 1]. |
| **Closing** | **Opening** | **No Change ($\Delta \text{OI} = 0$)**[cite: 1] | Existing short closed against a new short[cite: 1]. |
| **Closing** | **Closing** | **Goes DOWN ($\Delta \text{OI} < 0$)**[cite: 1] | Existing positions unwound/exited[cite: 1]. |

- **Limits of Next-Day OI:** Even when $\Delta \text{OI} > 0$, OI is a net aggregate across all participants; it still does **not** prove *who* opened the position, whether the initiator was the buyer or the seller, or whether the position is a directional bet, a hedge, or a spread leg `[DOCTRINE: Academy Flowseeker Ch. 4, Sec. 4, Quiz Q16 & Q17]`[cite: 1].

---

## 5. Attention Sorting: Flow Score, Unusualness & Tab Hygiene

### 5.1 Flow Score vs. Unusualness (`Premium Rank`, `Vol/OI`, `FlowBonus`)
Flowseeker separates **"which way?"** from **"how unusual?"** `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 2]`[cite: 1]:
1. **`Flow Score` (Direction Classification: $-100$ to $+100$):**
   - Range: **$-100$ (strongly bearish) to $+100$ (strongly bullish)**; near $0 =$ unclear `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 2, Quiz Q1 & Q2]`[cite: 1].
   - Inputs: Side, sweeps, moneyness, DTE, premium, and implied volatility `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 2]`[cite: 1].
   - **Guardrail:** A Flow Score of `+80` means the print is classified strongly bullish—it does **not** mean an 80% probability of profit `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 3, Quiz Q6]`[cite: 1]. Furthermore, Flow Score evaluates **one leg at a time**, so a bull call spread will show a positive score on the bought leg and a negative score on the sold leg `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 3 & 6, Quiz Q9 & Q10]`[cite: 1].
2. **Unusualness (`Premium vs Ticker History` & `Vol/OI` / `FlowBonus`):**
   - `Premium vs Ticker History` ranks a print's dollar premium against **that specific ticker's own prints over the last 20 days** (because a $\$1\text{M}$ print is routine on `SPY` but rare on a quiet stock) `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 2]`[cite: 1].
   - `FlowBonus` is Skylit's "how unusual" metric quoted by Flow AI `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 2]`[cite: 1].
   - **Guardrail:** Unusualness is **directionless**; a print in the top 1% of its ticker's 20-day premium history is not automatically bullish `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 2, Quiz Q3]`[cite: 1].

| Flow Score | Unusualness (`Premium Rank` / `Vol/OI`) | Priority & Agent Interpretation | Source Tag |
| :--- | :--- | :--- | :--- |
| **Strong ($\vert{}\text{Score}\vert{}$ high)** | **High** | **Priority 1:** Directional **and** unusual — investigate first[cite: 1]. | `[DOCTRINE: Flowseeker Ch. 5, Sec. 2, Q4]`[cite: 1] |
| **Near Zero ($\approx 0$)** | **High** | **Priority 2:** Unusual size/activity with unclear direction — check for a **hedge or multi-leg spread**[cite: 1]. | `[DOCTRINE: Flowseeker Ch. 5, Sec. 2, Q5]`[cite: 1] |
| **Strong ($\vert{}\text{Score}\vert{}$ high)** | **Low** | **Priority 3:** Clearly directional, but routine size for this ticker[cite: 1]. | `[DOCTRINE: Flowseeker Ch. 5, Sec. 2]`[cite: 1] |
| **Near Zero ($\approx 0$)** | **Low** | **Priority 4:** Lowest priority (routine noise)[cite: 1]. | `[DOCTRINE: Flowseeker Ch. 5, Sec. 2]`[cite: 1] |

### 5.2 Tab Hygiene & The Market-Wide Summary Bar Trap
1. **Tab Rules:** Build each saved tab around **one question** (`Broad watch`, `Large prints`, `Ticker study`, `Package study`), change **one filter at a time**, and **always keep one unfiltered/broad tab** `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 4]`[cite: 1].
2. **Summary Bar vs. Filtered Rows:** The top **Summary Bar** in the Live Feed (`Directional Sentiment`, `Net Premium`, `FIR`, `Calls/Puts`, `Put/Call Ratio`, `RVOL`) is **market-wide** and **never changes when you filter a tab** `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 5, Quiz Q18]`[cite: 1]. Filtering a tab to "Ask-side Calls Only" will make 100% of the table rows look bullish because of your filter, even while the market-wide Summary Bar reads bearish `[DOCTRINE: Academy Flowseeker Ch. 5, Sec. 5, Quiz Q19]`[cite: 1].
3. **Single-Leg vs. All-Legs Scope Note:** In Talon V1 Beta, Talon's flow totals are **single-leg only**, so they will not tie out dollar-for-dollar with the all-legs `Underlying (Vol / $)` block on the Contract Drilldown chart `[DOCTRINE: Academy Talon Prompt Guide, Sec. 9]`[cite: 1].

---

## 6. The 7 Flowseeker Views, Row Units & Contract Drilldown

### 6.1 Unit Disambiguation Across the 7 Views
Never compare numbers across views without checking the row unit `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 2, Quiz Q1–Q5]`[cite: 1]:

| Flowseeker View | One Row / Item Represents | Volume / Size Unit | Primary Question Answered | Source Tag |
| :--- | :--- | :--- | :--- | :--- |
| **1. Live Feed** | **One print** (or 1-second sweep)[cite: 1] | Option contracts in that single trade[cite: 1] | *What just traded?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 2]`[cite: 1] |
| **2. Flow Scanner** | **One contract** (rolled up for the whole day)[cite: 1] | Cumulative daily option contracts on that strike/expiry[cite: 1] | *Which contracts are busiest today?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 2 & 3]`[cite: 1] |
| **3. Flow Compass** | **A ranked card**[cite: 1] | Aggregated ticker/contract metrics | *What stands out across the market?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 2]`[cite: 1] |
| **4. Contract Lookup** | **A ticker overview**[cite: 1] | Chain-wide option metrics for 1 symbol | *What is happening in this one name?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 2]`[cite: 1] |
| **5. Contract Drilldown** | **One contract in detail**[cite: 1] | Multi-pane time series & print list | *What exactly traded in this contract over time?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 2 & 4]`[cite: 1] |
| **6. Flow Tracker** | **Saved prints (`Tracked Flow`) or saved contracts (`Tracked Contracts`)**[cite: 1] | Mark-to-Mid P/L% vs. original fill price (`still in`, `partial`, `exited` estimate)[cite: 1] | *What has the contract done since that print?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 3, Q6 & Q8]`[cite: 1] |
| **7. Dark Feed** | **One off-exchange stock trade**[cite: 1] | **Shares of stock** (no option side/Greeks)[cite: 1] | *Where did large stock blocks print?*[cite: 1] | `[DOCTRINE: Flowseeker Ch. 6, Sec. 2 & 3]`[cite: 1] |

### 6.2 Contract Drilldown & The "Shared Tape" Independence Trap
- When investigating a contract in **Contract Drilldown**, six sub-views appear: `Contract Flow` (volume bars by bid/mid/ask over time—**not** a price candlestick chart), `Net Premium` (verify whether set to per-bar or running cumulative total), `Strike Distribution`, `Underlying (Vol / $)`, `Vol/OI History`, and `Flow Orders` `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 4, Quiz Q13 & Q17]`[cite: 1].
- **Critical Evidentiary Rule:** `Contract Flow`, `Net Premium`, and `Strike Distribution` are **all built from the exact same options tape** `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 4]`[cite: 1]. When all three point bullish after a large ask-side print, that counts as **only ONE piece of independent evidence seen three ways, not three confirmations** `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 4, Quiz Q15 & Q16]`[cite: 1]. Independent confirmation requires **Next-Day Open Interest**, **Chart Structure**, and the **Heatseeker Map** `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 4, Quiz Q16]`[cite: 1].
- Every Drilldown investigation must end with one of **three honest answers**: (1) **A hypothesis worth keeping** (with a concrete `"Weakens If"`), (2) **A conflict**, or (3) **Not enough evidence** `[DOCTRINE: Academy Flowseeker Ch. 6, Sec. 5, Quiz Q18 & Q19]`[cite: 1].

---

## 7. Flow on the Chart (The Atlas Flow Pane)

### 7.1 Mechanics & Pill-Group Filters
- The **Atlas Flow Pane** sits directly beneath the price chart on the same time axis `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 2]`[cite: 1]:
  1. **Bar Height = Premium in Dollars (Not Contracts):** A few expensive ITM contracts can create a taller bar than thousands of cheap OTM contracts `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 2, Quiz Q1 & Q2]`[cite: 1].
  2. **Calls and Puts Separated:** Calls plot above the zero line; Puts plot below the zero line (with an optional call-minus-put `Net` line). Never mentally add Call bars and Put bars together as "total bullish flow"—simultaneous tall Call and Put bars often represent a straddle or hedge `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 2, Quiz Q3, Q4, Q7]`[cite: 1].
  3. **7 Pill-Group Filters:** `Legs` (`Single · Multi`), `Moneyness` (`ITM · ATM · OTM`), `Side` (`Bid · Mid · Ask`), `Sweeps` (`Sweep · Non-Sweep`), `DTE` (`Min/Max`), `Premium` (`Min/Max`), and `Flow Score` (`Min strength`) `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 3]`[cite: 1]. At least **one pill per group always stays on**, and when **all pills in a group are on, that group applies no filter** `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 3, Quiz Q10 & Q11]`[cite: 1].
- **Proper Use:** Use the pane to ask *"When price reached my structural level, did meaningful premium arrive there?"*—never to buy a tall call bar at a midpoint or fall for the **hindsight trap** `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 4, Quiz Q8, Q17–Q24]`[cite: 1].

---

## 8. When Price and Flow Disagree: The 5-Explanation Conflict Protocol

### 8.1 Procedure for Resolving Price-vs-Flow Disagreements (`[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 2–5]`)
When chart structure + Heatseeker point one way (e.g., SPY rejecting twice at its `590` King Node ceiling) while options flow points the opposite way (e.g., `$4M` of 2-DTE `590` calls bought at the ask over 20 minutes)[cite: 1]:
1. **Step 1 — State the Mismatch as a Checkable Observation:** Record the exact ticker, strike, expiry, time window, dollar premium, side, and chart/map location without narrative labels `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 2, Quiz Q1 & Q2]`[cite: 1].
   - *First sanity check:* Verify the flow is in the **same instrument, same expiry horizon, and not one leg of a spread**. (Long-dated 3-month puts during a morning rally are often portfolio protection, not a disagreement with today's move; a bid-side call that is the sold leg of a bull call spread is actually bullish) `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 2, Quiz Q3, Q4, Q7]`[cite: 1].
2. **Step 2 — List All 5 Competing Explanations That Produce Identical Prints:**
   Before choosing an interpretation, generate all five hypotheses `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 3, Quiz Q9–Q16]`[cite: 1]:
   - **Explanation 1 (`New Directional Bets`):** Traders are genuinely betting on a breakout above `590`[cite: 1].
   - **Explanation 2 (`Protection / Hedging`):** Traders who are short the underlying stock are buying calls at `590` to cap their upside risk if resistance breaks[cite: 1].
   - **Explanation 3 (`Closing Existing Positions`):** Traders who previously sold/wrote those calls are buying them back at the ask to exit[cite: 1].
   - **Explanation 4 (`Part of a Multi-Leg Structure`):** The calls are one leg of a spread or volatility package whose other leg changes the directional read[cite: 1].
   - **Explanation 5 (`Weak / Misread Side`):** Fills occurred near the mid or in a thin contract where the ask-side classification is weak[cite: 1].
3. **Step 3 — Query the 6 Separating Evidence Checks:**
   - **Check A (Whole Structure in Drilldown):** Were matching legs printed in the same second? `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5]`[cite: 1].
   - **Check B (Side Quality):** Were fills right at the ask edge vs. near the mid? `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5]`[cite: 1].
   - **Check C (Expiry Horizon):** Is it 0–2 DTE right at the ceiling (breakout bet or short cover) vs. 90 DTE (protection)? `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5]`[cite: 1].
   - **Check D (Price Acceptance vs. Rejection):** Does price cleanly reject `590` while the call flow fades (supports short at reduced size), OR does price accept above `590`? `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5, Quiz Q21 & Q22]`[cite: 1].
   - **Check E (Map Reshuffle):** Does the `590` ceiling node hold firm, or does it shrink/unwind (supports breakout)? `[DOCTRINE: Academy Flowseeker Ch. 9, Sec. 4 & 5, Quiz Q22]`[cite: 1].
   - **Check F (Next-Day Open Interest):** Tomorrow morning, does OI drop (consistent with Explanation 3: closing) or rise (consistent with Explanations 1 or