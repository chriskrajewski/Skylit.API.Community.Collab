# entries_stops_targets_and_rr.md

- **Title:** Execution Mechanics — Deflection Zones, Cheap Fills, Tap Probabilities, Stops, Targets & R:R Math
- **Scope:** Exact mathematical formulas, dollar tolerances, tap degradation probabilities, stop-loss placement rules, node-to-node targeting, and Nexus trade management controls (`Trim`, `BE`).
- **Sources Used:** SRC-05 (Academy Ch. 3, Sec. 6)[cite: 1], SRC-08 (Academy Ch. 6, Sec. 1–16 & Quiz Q1–Q25)[cite: 1], SRC-11 (Academy Ch. 9, Sec. 2–6)[cite: 1], SRC-15 (Academy Ch. 13, Quiz Q3, Q4, Q5, Q11, Q12)[cite: 1], SRC-25 (Academy Flowseeker Ch. 10, Sec. 2, 3, 6)[cite: 1], SRC-27 (Docs Core Concepts: Retests), SRC-30 (Changelog Oct 13–14, 2026), SRC-31 (Learn: Best GEX Tools for 0DTE SPX).
- **Prerequisites:** `00_index.md`, `glossary.md`, `chart_structure_fundamentals.md`, `charts_first_workflow.md`
- **Last Updated:** 2026-10-17

---

## 1. The Direct-Tap Entry & Deflection Zone Tolerances

### 1.1 Definition
- **Plain sentence:** Skylit traders do not front-run a node early and do not chase a move after it has already reversed; they enter at the **direct tap** of a major node within a fixed dollar tolerance band called the **Deflection Zone**, capturing a **"Cheap Fill"** by absorbing emotional retail panic liquidity at the extreme[cite: 1].
- **Precise version:** Given a target structural node at strike $K$, a valid **Direct-Tap Entry** occurs when underlying spot price $P_{\text{spot}}$ enters the interval $[K - \delta_{\text{deflect}}, K + \delta_{\text{deflect}}]$:

| Instrument | Deflection Zone Tolerance ($\pm \delta_{\text{deflect}}$) | Total Zone Width | Source & Epistemic Tag |
| :--- | :--- | :--- | :--- |
| **`SPY`** | **$\pm \$0.50$** | $\$1.00$ (e.g., $659.50\text{ to }660.50$ for a $660$ node) | `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1] |
| **`QQQ`** | **$\pm \$0.50$** | $\$1.00$ (e.g., $589.50\text{ to }590.50$ for a $590$ node) | `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1] |
| **`SPX` / `SPXW`** | **$\pm \$5.00$** *(Academy Ch. 6)* <br> **5 to 10 points** *(Knowledge Hub 0DTE SPX article)* | $\$10.00$ (e.g., $6895\text{ to }6905$ for a $6900$ node) <br> *or $\pm 5\text{--}10\text{ pts}$* | `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1] vs. `[DOCTRINE: Learn: Best GEX Tools for 0DTE SPX]` *(Both preserved; see Section 1.4)* |
| **`ES` / `NQ` / Individual Stocks** | Exact dollar/tick tolerance not stated in sources | — | `[NOT SPECIFIED]` *(See `open_questions.md`; can be scaled proportionally by Spot or ATR `[INFERENCE]`)* |

### 1.2 Inputs
- Node strike $K$, instrument ticker $S$, live spot price $P_{\text{spot}}$, node sign $\text{sign}(\text{GEX}_K)$, and node tap count $N_{\text{taps}}$.

### 1.3 Procedure (Entry Execution)
1. **Verify Setup Preconditions:** Chart structure extreme + Heatseeker node + Trinity $\ge 2/3$ + $\text{R:R} \ge 3.0$[cite: 1].
2. **Wait for Price to Enter $[K - \delta_{\text{deflect}}, K + \delta_{\text{deflect}}]$:**
   - Never enter before $P_{\text{spot}}$ reaches $K \mp \delta_{\text{deflect}}$ (`"Why is front-running a node discouraged in Chapter 6? Because traders often get trapped before the real deflection forms"`) `[DOCTRINE: Academy Ch. 6, Quiz Q8]`[cite: 1].
   - Never enter after price has already deflected and completed a large portion of the move (`"You are paid for being precise — and precision means being there when the node gets tapped, not showing up after the move already started."`) `[DOCTRINE: Academy Ch. 6, Sec. 15]`[cite: 1].
3. **Branch by Node Gamma Sign:**
   - **If $\text{GEX}(K) > 0$ (`+GEX` Yellow):** Execute limit/market entry inside $[K - \delta_{\text{deflect}}, K + \delta_{\text{deflect}}]$ as price taps the zone and begins to deflect (`"Cheap Fill"`: buying puts into a Rug ceiling as retail chases calls, or buying calls into a Reverse Rug floor as retail panic-sells) `[DOCTRINE: Academy Ch. 6, Sec. 3 & 4]`[cite: 1].
   - **If $\text{GEX}(K) < 0$ (`-GEX` Purple):** Expect a **Beach Ball overshoot** past $K \pm \delta_{\text{deflect}}$; wait for the overshoot candle to stall and print a rejection wick before executing `[DOCTRINE: Academy Ch. 6, Sec. 5 & 8; Quiz Q9 & Q10]`[cite: 1].

### 1.4 Inter-Source Comparison on SPX Deflection Margin
- **Source A (`Academy Ch. 6, Sec. 3`):** States explicitly: *"Deflection zones are +/- 50 cents on QQQ and SPY and +/- 5 dollars on SPX in either direction. Example: If SPX has a king node at 6900, our deflection zone and entry trigger ranges from 6895 to 6905."* `[DOCTRINE: Academy Ch. 6, Sec. 3]`[cite: 1].
- **Source B (`Learn: Best GEX Tools for 0DTE SPX Trading`):** Notes that on 0DTE SPX, traders allow a **5–10 point margin** around key strikes for deflection reactions `[DOCTRINE: Learn: Best GEX Tools for 0DTE SPX]`.
- **Agent Implementation:** Default $\delta_{\text{deflect, SPX}} = 5.0$ points for standard positive-gamma direct taps, and allow up to $10.0$ points in negative-gamma / high-velocity overshoot conditions `[INFERENCE]`.

---

## 2. Node Tap Probability & Retest Degradation Schedule

### 2.1 Definition & Epistemic Status of the Percentages
- **Plain sentence:** Every time price touches a node, resting orders and dealer hedging liquidity are consumed, reducing the probability that the node will hold on the next touch[cite: 1].
- **Source Quote & Nature of the Numbers:**
  - In **Academy Chapter 6, Section 6 ("Node Tap Probability (CRITICAL)")**, Skylit states `[DOCTRINE: Academy Ch. 6, Sec. 6]`[cite: 1]:
    - **`1st Tap of a major node → ~80% reaction probability`** (`First test = highest quality fade`)[cite: 1]
    - **`2nd Tap of a major node → ~66% reaction probability`** (`Second test = still tradable`)[cite: 1]
    - **`3rd Tap of a major node → ~33% reaction probability`** (`Third test = low-quality setup. If you are trading the third tap, you are trading a weakening level`)[cite: 1]
  - This exact schedule (`1st: 80%, 2nd: 66%, 3rd: 33%`) is tested as a factual rule in **Chapter 6 Quiz Q15** and **Chapter 13 Final Exam Q11** `[DOCTRINE: Academy Ch. 6, Quiz Q15; Ch. 13, Quiz Q11]`[cite: 1].
  - In **Skylit Docs (`skylit.ai/docs/core-concepts`)**, the retest table presents the same rule qualitatively without hard percentages: `1st Test = High (~full node strength) -> Trade the reaction with normal size`; `2nd Test = Moderate (weakened) -> Trade smaller, or wait for extra confirmation`; `3rd+ Test = Low (exhausted) -> Stand aside or look for the break` `[DOCTRINE: Docs Core Concepts]`.
  - **Epistemic Classification:** Because the Academy uses the tilde (`~80%`, `~66%`, `~33%`) without citing a specific backtest sample size or date range, agents should treat these numbers as **doctrinal heuristic benchmarks / rules of thumb** rather than audited empirical frequencies, while strictly enforcing the operational action attached to each tap count `[INFERENCE]`.

### 2.2 Tap Accrual Mechanics (Resting vs. Leaving)
- **How Taps Are Counted:** In the **Talon Prompt Guide (Section 4: "Is That Level Still Good?")**, Skylit specifies a critical mechanical rule for tap counting `[DOCTRINE: Academy Talon Prompt Guide, Sec. 4]`[cite: 1]:
  1. Talon tracks the node lifecycle across four states: **`fresh → tested → delivered → spent`**[cite: 1].
  2. **Continuous Accrual While Resting at the Strike:** *"Taps accrue while price is at the level, not only when it leaves and comes back. Price resting on a strike keeps accumulating taps, so a level can read as worked-over without ever having traded away from it."* `[DOCTRINE: Academy Talon Prompt Guide, Sec. 4]`[cite: 1].
  3. **No False Zero Rule:** *"Talon will never report a false zero. If the tap data can't be fetched it says the reading is unavailable — 'untested' is the most actionable state, and therefore the most dangerous thing to get wrong."* `[DOCTRINE: Academy Talon Prompt Guide, Sec. 4]`[cite: 1].
- **What Remains `[NOT SPECIFIED]`:** The exact time interval or tick/bar rule by which the backend increments the tap counter while price rests on a strike (e.g., whether 1 tap = 1 five-minute bar touching the strike, or N seconds spent within $\delta_{\text{deflect}}$) is `[NOT SPECIFIED]` in the public docs (logged in `open_questions.md`).

### 2.3 Procedure (Agent Tap Filter)
1. Query the node's tap count $N_{\text{taps}}$ and lifecycle state from the Skylit API / Talon context.
2. **If `N_taps == NULL` or fetch fails:** Flag `TAP_STATE = UNKNOWN` (never default to `0` / `fresh`) `[DOCTRINE: Academy Talon Prompt Guide, Sec. 4]`[cite: 1].
3. **If `N_taps == 0` (`fresh`):** Full conviction (~80% heuristic); allow standard position size `[DOCTRINE: Academy Ch. 6, Sec. 6; Docs Core Concepts]`[cite: 1].
4. **If `N_taps == 1` (`tested`):** Moderate conviction (~66% heuristic); allow trade at reduced size or require extra confirmation (such as a Double Bottom chart structure or supporting Flowseeker print) `[DOCTRINE: Academy Ch. 1, Sec. 4.2; Ch. 6, Sec. 6; Docs Core Concepts]`[cite: 1].
5. **If `N_taps >= 2` (3rd+ tap / `delivered` / `spent`):** **Suppress fade entry** (~33% heuristic; high break risk) `[DOCTRINE: Academy Ch. 6, Sec. 6 & 12; Docs Core Concepts]`[cite: 1].

---

## 3. Stop-Loss Placement ("One Node Beyond Invalidation")

### 3.1 Definition
- **Plain sentence:** When trading a deflection off a node, the stop loss is placed **one node beyond** the node being traded, and is triggered when price **breaks and holds (accepts)** beyond that stop node[cite: 1].
- **Precise version:** Given an entry off node $K_{\text{entry}}$ and the ordered set of strikes on the active Heatseeker map:
  - For a **Short (Put / Ceiling Deflection)** entry at $K_{\text{entry}}$:
    $$K_{\text{stop}} = \min \{ K_i > K_{\text{entry}} \}$$
    (the next strike/node immediately above $K_{\text{entry}}$), triggered on **break + hold** above $K_{\text{stop}}$ `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10]`[cite: 1].
  - For a **Long (Call / Floor Deflection)** entry at $K_{\text{entry}}$:
    $$K_{\text{stop}} = \max \{ K_i < K_{\text{entry}} \}$$
    (the next strike/node immediately below $K_{\text{entry}}$), triggered on **break + hold** below $K_{\text{stop}}$ `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10; Ch. 10, Sec. 12]`[cite: 1].

### 3.2 Concrete Examples from Doctrine
1. **SPY Short Example (`[DOCTRINE: Academy Ch. 6, Sec. 3]`):** Playing puts off a deflection of the **`660` SPY** Gatekeeper node $\rightarrow$ stopped out on a **break and hold above `661`** (1 strike/node above `660`, $\text{Risk} = \$1.00$ from strike)[cite: 1].
2. **SPY/QQQ Short Example (`[DOCTRINE: Academy Ch. 6, Sec. 10]`):** Node = **`603`**, Entry = deflection off `603` $\rightarrow$ Stop = **Above `604` (break + hold)**[cite: 1].
3. **SPX Long Example (`[DOCTRINE: Academy Ch. 10, Sec. 12]`):** Playing calls off an SPX Reverse Rug at **`6775`** $\rightarrow$ Stop loss (`SL`) = **below `6770`** (1 SPX 5-point node below `6775`, $\text{Risk} = 5\text{ points}$)[cite: 1].
4. **SPY Playbook Long Example (`[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`):** Floor node at **`578`** SPY; weakens if price accepts below `578` $\rightarrow$ hard invalidation at **`577`** (1 strike below `578`)[cite: 1].

### 3.3 Edge Cases
- **What Constitutes "Break + Hold" (Acceptance):** Skylit contrasts a quick overshoot wick (Beach Ball) with a "break and hold" / "price accepts below/above" `[DOCTRINE: Academy Ch. 6, Sec. 3 & 10; Flowseeker Ch. 7, Sec. 5]`[cite: 1]. However, the exact candle timeframe required to confirm a "hold" (e.g., a 5-minute candle close beyond $K_{\text{stop}}$ vs. two consecutive 1-minute closes) is `[NOT SPECIFIED]` in the text. **Default `[GENERAL TA]` Rule for Agents:** Require either a `5m` candle close beyond $K_{\text{stop}}$ OR price trading $> 0.5 \times \Delta K_{\text{node}}$ past $K_{\text{stop}}$ to protect against catastrophic runaway loss.

---

## 4. Target Selection, Reward-to-Risk (R:R) Math & Trade Management

### 4.1 Target Selection ("Node-to-Node Delivery")
- **Rule:** *"Price is delivered from node to node... Can play node-to-node, From floor to ceiling, Key level to key level."* `[DOCTRINE: Academy Ch. 6, Sec. 11; Ch. 9, Sec. 4]`[cite: 1].
- **Procedure:**
  1. Identify the next high-accumulation **Fresh** or **Growing (`Real`)** node in the direction of the deflection `[DOCTRINE: Academy Ch. 6, Sec. 5 Step 4; Ch. 9, Sec. 2 & 5]`[cite: 1].
  2. **Do Not Target Used Levels:** *"We do not target used levels. We target fresh positioning."* If a node earlier in the day has already been tapped and delivered from, do not use it as a primary magnet `[DOCTRINE: Academy Ch. 9, Sec. 2 & 3]`[cite: 1].
  3. **Respect Intervening Friction:** If a Gatekeeper or Pika Cloud sits between $P_{\text{entry}}$ and the opposite King Node/Ceiling, set `Target_1` at the Gatekeeper/Pika Cloud `[DOCTRINE: Academy Ch. 10, Sec. 6, Step 4]`[cite: 1].

### 4.2 Reward-to-Risk (R:R) Formula & Standards
Given planned entry price $P_{\text{entry}}$, stop invalidation level $P_{\text{stop}}$, and structural target $P_{\text{target}}$:
$$\text{Risk} = \vert{}P_{\text{entry}} - P_{\text{stop}}\vert{}, \qquad \text{Reward} = \vert{}P_{\text{target}} - P_{\text{entry}}\vert{}, \qquad \text{R:R} = \frac{\text{Reward}}{\text{Risk}}$$

- **Minimum Standard:**
  - **`R:R >= 3.0` (`3:1` reward-to-risk, also written `1:3` risk-to-reward by Glitch):** The Skylit gold standard and doctrinal minimum for an A+ trade `[DOCTRINE: Academy Ch. 3, Sec. 6; Ch. 6, Sec. 9 & Quiz Q4; Ch. 13, Quiz Q12; Flowseeker Ch. 10, Sec. 3 & Quiz Q7]`[cite: 1].
  - **`2.0 <= R:R < 3.0` (`2:1`):** Described in Heatseeker Ch. 6 as *"acceptable but not ideal / acceptable but below standard"* `[DOCTRINE: Academy Ch. 6, Sec. 9 & Quiz Q16]`[cite: 1], whereas Flowseeker Ch. 10 states that at ~2:1 (`Entry 581.40, Target 590, Stop 577 -> R:R = 8.60 / 4.40 = 1.95:1`) the trader must **wait for a better entry or pass** `[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 3 & 6, Quiz Q9 & Q10]`[cite: 1].
  - **`R:R < 2.0` (including `1:1` midpoint trades):** **Strictly avoid / No Trade** `[DOCTRINE: Academy Ch. 3, Sec. 6; Ch. 6, Sec. 9]`[cite: 1].
- **Calculate Required Limit Price for 3:1 R:R:**
  If a setup is valid but current spot $P_{\text{spot}}$ yields $\text{R:R} < 3.0$, the agent computes the exact pullback entry price $P_{\text{limit}}$ needed to achieve $3:1$:
  - For a Long trade: $P_{\text{limit}} = P_{\text{stop}} + \frac{P_{\text{target}} - P_{\text{stop}}}{4}$ `[INFERENCE]`.
  - For a Short trade: $P_{\text{limit}} = P_{\text{stop}} - \frac{P_{\text{stop}} - P_{\text{target}}}{4}$ `[INFERENCE]`.

### 4.3 Active Trade Management (`Trim`, `BE`, and Reshuffle Exits)
1. **Partial Profit (`Trim` Button on Futures Paper/Replay):** Closes **50% (half)** of the open position at market (disabled when position size is 1 contract) `[DOCTRINE: Changelog Oct 14, 2026]`.
2. **Break-Even Stop (`BE` Tag on Atlas Futures Desk):** Clicking the `BE` tag on a live position moves the stop loss directly to the entry price (break-even) `[DOCTRINE: Changelog Oct 13, 2026]`.
3. **Dynamic Reshuffle Exit:** If while in a trade the target node shrinks/unwinds rapidly, or a new opposing floor/ceiling rolls in front of price, or price accepts beyond the entry floor/ceiling, exit immediately rather than holding for the stale target `[DOCTRINE: Academy Ch. 7, Sec. 3 & 6; Flowseeker Ch. 7, Quiz Q24]`[cite: 1].

---

## 5. Worked Example (`[DOCTRINE: Academy Flowseeker Ch. 10, Sec. 6]`)
- **Setup:** SPY Floor node at `578`, Invalidation Stop at `577` ($1\text{ node below }578$), King Node Target at `590`[cite: 1].
- **Formula for Max Entry Price to Achieve $\ge 3:1$ R:R:**
  $$P_{\text{limit}} = 577 + \frac{590 - 577}{4} = 577 + \frac{13}{4} = 580.25$$
- At $P_{\text{spot}} = 581.40$: $\text{R:R} = (590 - 581.40)/(581.40 - 577) = 8.60 / 4.40 = 1.95 < 3.0 \implies$ **Wait**[cite: 1].
- At $P_{\text{spot}} = 579.00$ ($\le 580.25$): $\text{R:R} = (590 - 579)/(579 - 577) = 11 / 2 = 5.50 \ge 3.0 \implies$ **Execute Long**[cite: 1].

---

## 6. What It Is NOT
- A stop loss is **not** placed directly *at* the entry node strike (`"Where should a stop generally be placed on a node deflection trade? One node beyond where the deflection is invalidated"`, not `"Directly at the node"` or `"At a fixed dollar amount below entry"`) `[DOCTRINE: Academy Ch. 6, Quiz Q5]`[cite: 1].

---

## 7. Related Concepts
- See `no_trade_conditions.md` for all hard rejection gates.

---

## Agent Checklist (`entries_stops_targets_and_rr.md`)

1. **Enforce Deflection Band:** Only trigger direct-tap entries when $\vert{}P_{\text{spot}} - K_{\text{entry}}\vert{} \le \delta_{\text{deflect}}$ ($\pm\$0.50$ SPY/QQQ; $\pm\$5.00$ SPX)[cite: 1].
2. **Filter by Tap Count:** Reject fade entries where $N_{\text{taps}} \ge 2$ (3rd+ tap)[cite: 1].
3. **Set Stop 1 Node Beyond:** Compute $K_{\text{stop}}$ as the adjacent strike beyond $K_{\text{entry}}$[cite: 1].
4. **Enforce R:R $\ge 3.0$:** Require $\vert{}P_{\text{target}} - P_{\text{entry}}\vert{} / \vert{}P_{\text{entry}} - P_{\text{stop}}\vert{} \ge 3.0$[cite: 1].