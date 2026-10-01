# The Greeks Bible — Skylit Edition

> A complete, page-structured textbook for understanding Greeks, dealer microstructure, and how to trade Heatseeker (GEX/VEX) maps.
> 

---

## 🚧 **Disclaimer — Educational Use Only** 🚧

The information provided in this guide (“The Greeks Bible”) and any related Skylit, Inc. products, services, or communications is for educational purposes only.

It is not financial advice, nor an offer, solicitation, or recommendation to buy, sell, or hold any security, derivative, or investment instrument.

Options and derivatives trading involve substantial risk, including the potential loss of your entire investment.

Past performance, market behavior, or model outputs (e.g., GEX/VEX/Heatseeker data) do not guarantee future results.

Skylit, Inc., its affiliates, and contributors do not guarantee accuracy or completeness of any data or analysis and assume no liability for losses or damages resulting from the use of this material.

Always conduct your own due diligence and consult with a licensed financial professional before making any trading decisions.

By using this material, you agree to assume full responsibility for your own trading outcomes and acknowledge that you do so at your own risk.

> 🧠 TL;DR: This is not advice. It’s education.
> 
> 
> Markets are risky. Trade responsibly.
> 

# How to Use This Book

**Who this is for:** Active traders using Heatseeker to time entries and exits on indices and single names.

**Outcome:** By the end, you’ll think like a **dealer (casino)**, understand **hedging flows**, and translate **GEX/VEX heatmaps** into actionable trade plans.

### Three Golden Rules

1. **GEX defines potential; VIX defines reality.**
2. **0–5 DTE = Gamma world. 7–180 DTE = Vanna world.**
3. **Trade where Greeks *and* the vol regime agree.**

---

# The Casino of Markets (Dealer vs Player)

**Dealer (Market Maker) = Casino:** Hedged, earns from flow, manages risk through Greeks.

**Player (Customer/Fund) = Gambler:** Directional, expresses bias through options, creating dealer hedging flows.

> 💡 Core Idea: Each Greek is a dimension of risk the dealer must neutralize. When crowd exposure shifts, dealers rebalance — their hedges feed back into price, volatility, and liquidity.
> 

**ELI5:** The ocean (market) moves by tides (positioning), wind (volatility), and time (theta). Dealers are lifeguards using jets (hedges) to keep waves in check.

---

# Master Greek Index

## Primary Greeks

> The table below shows how option premium and other Greeks change when the underlying (S), implied volatility (IV, σ), or time (t) moves. It also notes where each Greek is largest (ATM/OTM) and how it scales with tenor (T).
> 

| Greek | What it measures | If **S ↑** (spot up) | If **IV ↑** (σ up) | As **time passes** (t →) | Where it’s biggest | Tenor scaling | Knock‑on impact on other Greeks | Trading / premium impact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Delta (Δ)** | $ change in option for $1 move in S | **Calls:** Δ ↑; **Puts:** Δ ↓. Premium follows direction. | Small indirect effect via vanna (if σ changes Δ). | Drifts toward 1 (calls) / 0 (OTM) or 0 / –1 (puts) as expiry nears. | ATM (steepest), saturates near 0/1/–1 far ITM/OTM | Higher Γ (short T) → Δ moves more; long T → Δ more stable | Changes feed into hedge size; affected by Γ, Vanna, Charm | Premium tracks S; hedging flow immediate (dealers buy/sell stock) |
| **Gamma (Γ)** | Change of Δ per $1 in S (curvature) | Higher Γ → Δ jumps more on S moves; increases pinning if +GEX | Γ generally **falls** if σ ↑ and T fixed (for ATM); structure shifts with smile | Γ **rises** sharply into expiry (0DTE); **decays** when far from expiry | Max **ATM**, low ITM/OTM | ≈ **1/√T** (short‑dated explode, long‑dated small) | Higher Γ → faster Δ re‑hedging; interacts with Zomma (Γ vs σ) | High +GEX pins → premium decay dominates; –GEX accelerates moves and widens spreads |
| **Vega (ν)** | Change in option for 1‑pt change in IV | Minor (via smile/spot‑vol coupling) | Premium **expands** as IV ↑; contracts as IV ↓ | Time decay reduces **dollar vega** closer to expiry | Highest **ATM** | ≈ **√T** (long‑dated big vega; 0DTE ~ 0) | Feeds Vanna (Δ vs σ) and Vomma (ν vs σ) | Event runs/hedges: IV ↑ lifts all premiums; IV crush post‑event hurts long options |
| **Theta (Θ)** | Change in option per unit **time** (decay) | N/A directly; path via Δ/Γ | Higher IV → higher absolute Θ ($ terms) | **Premium decays**; Δ drifts via **Charm** | Greatest near **ATM** short‑dated |  | Θ interacts with Charm (Δ vs t) and Color (Γ vs t) | Short options collect Θ (if hedged); long options pay Θ |
| **Vanna** | **Δ** change for **IV** change (also **ν** change for **S** change) | If S ↑ with skew, can reduce IV (spot‑vol coupling) → Δ shifts | **IV ↓**: +VEX → Δ ↓ (dealers buy); **IV ↑**: +VEX → Δ ↑ (dealers sell) | Vanna **fades** into expiry (via ν) | Largest **slightly OTM** options | ≈ **√T** (long‑dated strong; 0DTE near 0) | Moves **Δ** and thus hedge; links ν to Δ; works with Zomma | Calm IV ↓: **supportive drift** toward +VEX; IV spikes can flip flows (rug) |
| **Charm** | **Δ** change with **time** (holding S, σ) | N/A directly | N/A directly | As time passes, **call Δ ↓** if OTM, **put Δ ↑** (less negative) → dealers rebalance | Strongest **near ATM** close to expiry | High short‑dated; small long‑dated | Shifts Δ intraday → changes hedge even without S/σ moves | Afternoon **pin/drift** toward OI maxima in low‑VIX regimes |
| **Vomma (Volga)** | **ν** change for **IV** change (vol convexity) | N/A directly | High Vomma → ν itself accelerates as σ moves; IV reprices **non‑linearly** | Slower decay than Γ; persists in medium/long tenors | ATM to slightly OTM in long tenors | Grows with **T** | Amplifies vega P&L; interacts with **Ultima** | During shocks, IV gaps higher; long volga structures benefit |
| **Vera** | **Rho** change for **IV** change (rates–vol cross) | Higher S may correlate with rates in indices | If σ ↑ with rate moves, Vera matters (macro regimes) | Time changes discounting effect | Rate‑sensitive underlyings | With **T** | Adjusts price via rates channel; niche | Matters in high‑rate volatility cycles, calendars |
| **Speed** | **Γ** change for **S** change | As S nears key strikes, Γ can rise fast | Via Zomma (Γ vs σ) | Builds into expiry | Near **strikes** with large OI | Short‑dated | Alters how quickly Δ becomes unstable | Explains **air pockets** around nodes |
| **Color** | **Γ** change with **time** | N/A | N/A | Γ ramps into expiry (pin risk) then disappears | ATM near expiry | Short‑dated | Drives late‑day hedging intensity | Fuels last‑hour **pinning** |
| **Zomma** | **Γ** change for **IV** change | N/A | σ ↑ can lift Γ (esp. near ATM), increasing re‑hedge speed |  | ATM | Mixed | Couples vol shocks to Δ curvature | VIX spikes trigger **bigger hedge swings** |
| **Ultima** | Second derivative of Vomma (vol convexity of convexity) | N/A | Governs tail behavior of IV; large in wings/long tenors |  | Far OTM wings, long T | Large with **T** | Extreme convexity risk | Matters in tail‑hedge structures |

### Cross‑Greek Mechanics (explicit dealer hedges)

- **If dealer is long delta → they sell stock** to neutralize; **short delta → they buy stock**.
- **Long gamma (+GEX)** makes those buy/sell actions **contrarian** to price (stabilizing). **Short gamma (–GEX)** makes them **pro‑cyclical** (amplifying).
- **Vanna ties IV moves to delta:** with **+VEX** below spot, **IV ↓ ⇒ dealer buys**, **IV ↑ ⇒ dealer sells**; signs reverse for –VEX.
- **Charm changes delta with time:** even if underlying price and IV are flat, dealers **rebalance intraday**.

### Time & Cross‑Expiry Interactions

- **0DTE:** Gamma and Charm dominate; **Vanna ~ 0**. Same‑day pins/breaks rule intraday.
- **1–7 DTE:** Transition — Gamma still large, **Vanna** begins to matter.
- **>30 DTE:** **Vanna/Vega world** — IV path drives price drift via hedging.
- **Vol Spike with front‑end +GEX:** Longer‑dated **Vanna flows can overwhelm** same‑day +GEX, forcing dealers to **sell** even inside a nominal support zone.

---

# Delta (Δ) — The Line of Fire

**Definition:** Change in option price per $1 move in the underlying.

| Role | Exposure | Hedge | Behavior |
| --- | --- | --- | --- |
| Dealer | Long Delta | Sells stock to stay neutral | Synthetically short stock |
| Dealer | Short Delta | Buys stock to stay neutral | Synthetically long stock |
| Player | Directional | Buys or sells calls/puts | Drives dealer flow |

> 💡 ELI5: Delta is the steering wheel position right now.
> 

**Market Influence:** Immediate hedging flow drives liquidity and short-term price action.
**Vol Regimes:** Delta’s behavior depends on gamma and vanna.
**Example:** Dealers short calls buy stock into squeezes.

---

# Gamma (Γ) — The Shock Absorber

**Definition:** Rate of change of delta per $1 in spot. Controls mean reversion vs acceleration.

| Condition | Dealer Hedge | Market Behavior |
| --- | --- | --- |
| +GEX (Long Γ) | Buys dips, sells rips | Stability, mean reversion |
| –GEX (Short Γ) | Sells dips, buys rips | Volatility amplification |

> 💡 ELI5: Gamma is a car suspension — smooth in calm, unstable on ice.
> 

**Vol Behavior:** Vol ↑ amplifies –GEX; Vol ↓ strengthens +GEX pins.

**Above/Below Spot:** +GEX below = support, +GEX above = resistance.

**Example:** SPX 0DTE fades in +GEX; trends in –GEX.

---

# Vega (ν) — The Breath of the Market

**Definition:** Sensitivity to implied volatility.

| Role | Behavior |
| --- | --- |
| Dealer | Usually short vol, hedges with options |
| Player | Buys vol in fear, sells vol in calm |

> 💡 ELI5: Vega is the market’s breathing — inhale (IV ↑), exhale (IV ↓).
> 

**Market Impact:** Governs vol regime changes. Long vega → benefits from vol rise; short vega → benefits from vol crush.

---

# Theta (Θ) — The Silent Tax

**Definition:** Time decay — option value lost each day.

| Role | Behavior |
| --- | --- |
| Dealer | Harvests theta when hedged |
| Player | Pays theta to rent convexity |

> 💡 ELI5: Theta is a parking meter — time costs you money.
> 

**Vol Behavior:** Vol ↑ inflates theta costs; Vol ↓ eases them.

**Heatseeker:** When +GEX and +VEX align, expect strong daily pinning.

---

# Vanna — The Vol–Spot Coupler

**Definition:** Delta sensitivity to IV; the vol–spot feedback loop.

| Exposure | Vol ↓ | Vol ↑ | Dealer Flow | Market Bias |
| --- | --- | --- | --- | --- |
| +VEX Below Spot | Buys stock | Sells stock | Supportive in calm | Pin & drift |
| –VEX Below Spot | Sells stock | Buys stock | Fragile floor | Whipsaw |
| +VEX Above Spot | Buys stock | Sells stock | Resistance melts | Breakout in calm |
| –VEX Above Spot | Sells stock | Buys stock | Elastic ceiling | Squeeze in stress |

> 💡 ELI5: Vanna is a tailwind you only notice when it stops blowing.
> 

**Time Interaction:** Even when today’s expiry dominates GEX, **vanna from future expirations** can overpower it during vol spikes — as vol rises, back-end options create hedging pressure.

---

# Charm — The Invisible Drift

**Definition:** Delta sensitivity to time.

**Effect:** Dealers rebalance deltas as options decay, producing afternoon drift.

| Role | Behavior |
| --- | --- |
| Dealer | Buys stock as calls decay |
| Player | Sees grind-up into OI max |

> 💡 ELI5: Charm is cruise control adding slow throttle.
> 

**Interaction:** Charm and Vanna combine in calm sessions to produce steady upward “melt.”

---

# Vomma / Volga — The Convexity of Vega

**Definition:** Vega’s sensitivity to IV (vol convexity).

**Effect:** Determines how fast IV re-prices.

| Role | Behavior |
| --- | --- |
| Dealer | Hedges with calendars and flies |
| Player | Buys convexity ahead of catalysts |

> 💡 ELI5: Volga is a turbocharger for volatility.
> 

---

# Vanna & Charm Drift — Why Markets Float

- **Vanna Drift:** IV compression → dealers buy back hedges (from +VEX) → price drifts up.
- **Charm Drift:** Time decay reduces deltas → dealers rebalance by buying → afternoon upward drift.

**Cross-Expiry Note:** Even when 0DTE gamma dominates, **longer-dated vanna** adds background buy flow during vol compression. Conversely, in vol spikes, future expiry vanna becomes **sell flow**, overriding short-term GEX support.

---

# Heatseeker Reading — King Nodes, Flip Zones & Real Scenarios

> 💡 Core Idea: Dealers are forced participants — they hedge every time delta or vega shifts.
> 
> 
> Their flow is mechanical, and that flow *either dampens or amplifies* price moves depending on whether they’re long or short gamma/vanna.
> 

---

## Understanding the Map

- **+GEX (Positive Gamma):** Dealers long gamma → their hedges oppose the market’s move → *stabilizing / mean-reverting behavior*.
- **–GEX (Negative Gamma):** Dealers short gamma → their hedges chase the move → *trending / volatile behavior*.
- **+VEX (Positive Vanna):** Dealer deltas increase as vol rises → they **sell when vol ↑**, **buy when vol ↓**.
    
    → supportive when vol is falling, suppressive when vol is rising.
    
- **–VEX (Negative Vanna):** Dealer deltas decrease as vol rises → they **buy when vol ↑**, **sell when vol ↓**.
    
    → destabilizing when vol compresses, cushioning when vol explodes.
    

---

## Dealer Hedge Logic Simplified

| Exposure | IV ↓ | IV ↑ | Dealer Hedge Behavior | Market Feedback |
| --- | --- | --- | --- | --- |
| **+VEX** | Dealers buy → support under spot | Dealers sell → suppress rallies | Supportive in calm, heavy in storms | Market stabilizes until vol spikes |
| **–VEX** | Dealers sell → weak floor | Dealers buy → violent bounce | Fragile in calm, reflexive in stress | Market unstable until vol spikes |
| **+GEX** | Dealers buy dips / sell rips | Dealers still stabilizing (until vol overwhelms) | Mean reversion, range-bound | Dips fade, rips fade |
| **–GEX** | Dealers sell dips / buy rips | Both legs accelerate | Trend and breakout expansion | Dips deepen, rallies overshoot |

---

## Core Scenario Matrix — With Full Reasoning

| Setup | Dealer Flow | Volatility Feedback | Market Behavior | Trader Playbook |
| --- | --- | --- | --- | --- |
| **+GEX +VEX Below Spot** | Dealers buy dips (Gamma) + buy as vol falls (Vanna) | Calm vol regime (VIX ↓) reinforces both flows | Tight range, upward drift (“melt-up”) | Fade extremes or long pullbacks. Ideal for mean-reversion systems. |
| **+GEX +VEX Above Spot** | Dealers sell rips (Gamma) + sell more as vol rises (Vanna) | Rising vol amplifies suppression | Ceiling thickens; repeated rejections | Fade breakouts; short spikes into node. |
| **–GEX –VEX Below Spot** | Dealers sell dips (Gamma) + sell as vol falls (Vanna) | Both feedbacks pro-cyclical | Sharp breakdowns and low-liquidity slides | Momentum short — ride trend continuation. |
| **–GEX –VEX Above Spot** | Dealers buy rips (Gamma) + buy more as vol rises (Vanna) | Reflexive feedback loop | Violent short squeezes; volatility spikes | Follow breakout continuation on vol expansion. |
| **+VEX Below Spot** | Dealers buy as vol drops → support | Dealers sell if vol spikes → rug risk | Strong base in calm, rug-pull in stress | Buy dips in calm; cut fast if VIX ↑. |
| **–VEX Below Spot** | Dealers sell in calm → weak floor | Dealers buy in stress → fast bounces | Fragile in calm, reflexive in panic | Short rallies during calm; scalp long bounces in panic. |
| **+VEX Above Spot** | Dealers buy as vol drops (supportive to breakouts) | Dealers sell if vol ↑ (cap returns) | Breakouts sustain in vol compression; fail when vol expands | Buy breakout **only if vol falling**; fade if VIX surges. |
| **–VEX Above Spot** | Dealers sell in calm (cap); buy in stress (fuel squeeze) | Vol expansion flips flow → squeezes | Soft ceiling in calm; launches during vol spike | Short pops in calm, flip long on vol breakout. |
| **+GEX Below / –GEX Above** | Dealers buy low, chase high (unstable transition) | Crossing flip zone → polarity shift | Rug-pull if vol ↑, slingshot if vol ↓ | Trade the break — fade range until it flips. |
| **–GEX Below / +GEX Above** | Dealers sell low, fade high | Compression zone | Chop until breakout | Wait for vol expansion; breakout confirms shift. |

---

## Deep Explanation of the “Buy Breakout” Paradox

> “If dealers sell when vol ↑, why buy the breakout?”
> 

Because **the dealer flow is reactive, not predictive**.

When you see “+VEX above spot,” it means dealers will *sell into rising vol* — but the first leg of that vol expansion *is the breakout itself.*

- The breakout **creates** the vol spike.
- Dealers’ **selling comes slightly after**, providing resistance.
- If that selling flow fails to cap price (absorbed by real demand), you get a **vol-crush continuation** — the hallmark of a “breakout that sticks.”

✅ **So you buy breakouts only in vol compression**, where +VEX resistance is *melting*, not strengthening.

❌ **You don’t buy breakouts** if vol is expanding — that’s when +VEX resistance *hardens.*

---

## Time and Cross-Expiry Interplay

- **Front-end (0–2 DTE):** Gamma dominates → mechanical hedging, fast reversion or breakouts.
- **Back-end (>7 DTE):** Vanna dominates → volatility regime steering; drives directional drift days later.
- **Vol Spikes:** When front-end +GEX looks supportive, far-expiry +VEX can **flip it bearish** — dealers suddenly selling stock to re-hedge long vol exposure.
- **Vol Crush:** Reverse happens — far-dated +VEX reinforces +GEX, producing **multi-day pin and melt-up**.

> 💡 ELI5: Gamma is the brake pedal, Vanna is the slope of the hill.
> 
> 
> You can tap the brakes intraday, but if the hill is steep (vol regime), you still roll in that direction.
> 

---

## Practical Rules for Heatseeker Trading

| Signal | Interpretation | Tactical Bias |
| --- | --- | --- |
| **+GEX and +VEX stack under spot** | Dealer net long gamma + long vanna = full control | Fade dips, expect compression |
| **–GEX and –VEX stack under spot** | Dealer short gamma + short vanna = panic zone | Trend following; avoid fading |
| **Crossing a flip zone (+ over –)** | Polarity change in dealer flow | Expect rug-pull or slingshot |
| **Large King Node** | Max exposure → reversion magnet | Trade around, not through it |
| **Wide Air Pocket** | Sparse exposure → fast price zone | Expect acceleration |
| **Vol Crush + +VEX** | Supportive → rallies sustain | Ride melt-up |
| **Vol Spike + +VEX** | Dealer selling → resistance zone | Fade rally or reduce longs |
| **Vol Spike + –VEX** | Dealer buying → squeeze risk | Ride upside but expect whipsaws |

---

## Mental Model: The Casino Table

- **Dealer = Casino** → Neutral over time, but short-term reactive.
- **Player = Crowd** → Pushes price into zones that flip dealer behavior.
- **GEX = Table Shape** → Determines how balls (price) roll.
- **VEX = Gravity** → Determines whether rolls accelerate or slow.
- **Vol = Energy** → Determines how far momentum carries.

When you trade Heatseeker, you’re not betting on *direction* — you’re betting on *who will be forced to hedge next*.

# Between Nodes, Stacked Nodes & Alignment Dynamics

---

## Spot Between Nodes — “The Tug-of-War Zone”

When spot trades **between two large nodes** (e.g., a +GEX shelf below and a –GEX pocket above, or mixed VEX clusters), price action becomes a contest of opposing dealer hedges.

This is the **most confusing zone** for new Heatseeker users — because it’s where *flow polarity flips back and forth intraday*.

| Configuration | Dealer Positioning | Vol Behavior | Market Character | Tactical Bias |
| --- | --- | --- | --- | --- |
| **Between +GEX below and –GEX above** | Dealers buy on dips (below) and chase higher (above) | Stable vol → calm oscillation; vol ↑ → air-pocket breaks | Compression that can rug or sling fast | Fade edges until one side absorbs; breakout = strong directional follow |
| **Between –GEX below and +GEX above** | Dealers sell on dips and fade rallies | Vol ↓ → repeated fakeouts; Vol ↑ → large breaks | Choppy, deceptive range | Avoid chop; enter only on vol expansion confirmation |
| **Between +VEX and –VEX clusters** | Dealers flip from buying to selling as vol shifts | Small vol shifts swing control | IV spikes = directional chaos | Trade with vol regime; don’t fight it |
| **Spot trapped between two King Nodes** | Massive opposing dealer hedges at adjacent strikes | Vol flat → sticky magnetism | Ranges shrink intraday | Expect pinning and fake breaks near expiry |

> 💡 ELI5: When spot sits between two magnets of opposite charge, it vibrates — not trends. It only escapes when one magnet weakens (IV shift or OI decay).
> 

---

## Stacked Nodes — “The Volatility Springs”

When you see **stacked exposures of opposite sign**, such as a **major +GEX node directly above a –GEX pocket**, or a **+VEX node above a –VEX cluster**, think of it as a **spring under tension**.

Crossing that stack triggers sudden shifts in dealer hedging direction.

| Stack Type | Dealer Transition | Volatility Response | Market Reaction | Trade Setup |
| --- | --- | --- | --- | --- |
| **+GEX over –GEX** | From buying dips → selling dips | Vol ↑ magnifies change | “Rug pull” down through the flip | Short breakdown once under stack; stop back above |
| **–GEX over +GEX** | From selling dips → buying dips | Vol ↓ stabilizes | “Slingshot” up through the flip | Long breakout through stack; stop below |
| **+VEX over –VEX** | From selling in stress → buying in stress | As IV compresses, flip softens | Relief rally through resistance | Long if IV contracting |
| **–VEX over +VEX** | From buying in stress → selling in stress | Vol expansion amplifies reversal | Sharp rejection or whipsaw | Fade strength if vol spiking |

> 💡 ELI5: Stacks are like loaded springs — when price pushes through, dealer hedges flip polarity and release stored energy.
> 

---

## GEX/VEX Alignment vs. Misalignment

Heatseeker’s power is showing when **Gamma (GEX)** and **Vanna (VEX)** exposures agree (aligned) or disagree (misaligned).

Alignment tells you if **dealers’ directional hedges** and **vol-spot coupling** point the same way — or fight each other.

| Alignment State | What It Means | Dealer Flow | Market Behavior | Example |
| --- | --- | --- | --- | --- |
| **Aligned (+GEX +VEX)** | Dealers long gamma *and* long vanna | Buy dips, sell rips; buy on vol drops | Market stable, supportive drift | Classic low-VIX melt-up; SPX pinning days |
| **Aligned (–GEX –VEX)** | Dealers short gamma *and* short vanna | Sell dips, buy rips; sell on vol drops | Pro-cyclical acceleration, high vol | Trend days, gap-and-go selloffs |
| **Misaligned (+GEX –VEX)** | Dealers long gamma but short vanna | Buy dips (gamma) yet sell when vol drops | Conflicting hedges → range + sudden breaks | Choppy regime where calm fades fail |
| **Misaligned (–GEX +VEX)** | Dealers short gamma but long vanna | Sell dips (gamma) but buy when vol drops | Short-term volatility, long-term support | Panic lows that bounce violently next session |

> 💡 ELI5: GEX is the hand brake; VEX is the slope of the hill.
> 
> 
> When both point the same way, you roll steadily. When they fight, you jerk back and forth.
> 

---

## Why Misalignment Happens

Misalignment occurs because **Gamma and Vanna exposures are drawn from different expiries and strikes**:

1. **Time Skew:**
    - 0DTE & 1DTE dominate **GEX** (immediate delta hedging).
    - 5–30DTE dominate **VEX** (volatility-spot coupling).
    - Hence, short-term vs mid-term flows can diverge.
2. **Skew Asymmetry:**
    - Puts have much higher vanna (steeper skew).
    - So downside +VEX below spot can persist even when GEX flips negative (dealers short gamma intraday but long vanna overall).
3. **OI Rotation:**
    - As expiries roll off, GEX collapses while VEX persists (longer tenor).
    - That creates brief **misaligned days** where intraday price motion disagrees with multi-day vol drift.
4. **Vol Regime Change:**
    - A sudden IV spike (news, CPI, FOMC) alters vanna sign faster than GEX updates.
    - Dealers instantly change vol hedges, but OI-based GEX still shows prior positioning → apparent mismatch.

---

## Reading Heatseeker for Alignment Context

| Observation | Interpretation | Expected Flow | Trade Implication |
| --- | --- | --- | --- |
| +GEX and +VEX clusters in same region | Dealer control, long vol compression | Pin & drift | Fade extremes; buy-the-dip bias |
| –GEX but +VEX stacking below spot | Gamma short, vanna supportive | Vol crush rebound likely | Long into panic lows |
| +GEX but –VEX above spot | Gamma pins but vanna suppresses | Ceiling holds until vol collapses | Short rips in calm, flip long if vol crushes |
| GEX flat, VEX dominant | Low delta convexity, vol-driven market | Price follows VIX flow | Trade the vol regime, not direction |
| GEX/VEX polarity shift at same strike | Flip zone confirmed | Directional acceleration | Use as breakout trigger |

> 💡 ELI5: When GEX and VEX disagree, the dealer’s two hands are pulling in opposite directions.
> 
> 
> Whichever Greek dominates (Gamma intraday, Vanna across days) decides the winner.
> 

---

## Practical Playbook Summary

| Situation | Environment | What to Expect | Trade Bias |
| --- | --- | --- | --- |
| **Spot between opposite GEX nodes** | Mixed dealer flows | Range compression → breakout | Fade until breakout; then follow |
| **Spot between VEX nodes** | Vol regime indecision | Choppy → fast once VIX shifts | Wait for vol signal before entry |
| **Stacked opposite nodes** | Tension / polarity zone | Sudden directional snap | Trade the flip momentum |
| **Aligned +GEX +VEX** | Calm, low-vol | Mean reversion / drift | Buy dips, sell rips |
| **Aligned –GEX –VEX** | Volatility expansion | Trend acceleration | Ride breakout / momentum |
| **Misaligned** | Mixed signals | Whipsaw / indecision | Scale smaller, fade extremes only |

---

## Cross-Expiry Insight: “The Hidden Hands”

- The **front expiry (0DTE–2DTE)** explains today’s *speed* (gamma hedging).
- The **middle expiries (7DTE–30DTE)** explain the *drift* (vanna hedging).
- The **back expiries (45DTE–90DTE+)** explain the *gravity* (vol regime).

> When front-end GEX and mid-end VEX disagree, you get intraday whipsaws inside multi-day trends — the classic “grind higher but chop intraday” behavior.
> 

# Approaching Major Exposure Levels — Casino vs. Gambler Dynamics

> 💡 Core Idea: Price doesn’t react to exposure levels because they exist —
> 
> 
> it reacts because **crowd behavior + dealer hedging** converge at those points.
> 
> The “loop” only continues as long as deltas *still change*.
> 
> Once contracts go ITM and deltas saturate, the loop **dies**, and flow **reverses**.
> 

---

## Two Players at the Table

| Role | Objective | Tools | Limitation |
| --- | --- | --- | --- |
| **Dealer (Casino)** | Stay delta-neutral, extract premium from flow | Hedging stock/futures, vol trades | Must react mechanically to crowd exposure |
| **Gambler (Customer/Fund/Trader)** | Profit from direction or volatility | Buys/sells calls and puts | Runs out of gamma or delta leverage once ITM |

---

## The Life Cycle of a Strike

Every major exposure level — whether GEX or VEX — represents a **cluster of open options**.

Those contracts go through three phases as spot approaches:

| Phase | Option Status | Deltas | Dealer Hedge | Market Impact |
| --- | --- | --- | --- | --- |
| **1. Far OTM (Out-of-the-Money)** | Speculative chips | Δ ≈ 0 | Dealer does almost nothing | Price ignores the node |
| **2. ATM (At-the-Money)** | Maximum sensitivity | Δ ≈ 0.5 | Dealer must adjust hedge rapidly | Volatility explodes; feedback loop active |
| **3. Deep ITM (In-the-Money)** | Deltas saturated | Δ → 1 (call) / –1 (put) | Hedge fully sized; no more adjustment | Flow stops; feedback collapses |

This means **the danger zone is the *approach*** —

not once we’re “through it.” The hedging feedback loop has a lifecycle.

---

## What Happens as Price Approaches a Major Negative GEX Node (Downside)

Let’s walk step-by-step from both perspectives:

### 🧑‍🎲 The Gambler (Players)

- They **bought puts** earlier (insurance, directional bets).
- As spot drops **toward their strikes**, those puts go from **OTM → ATM → ITM**.
- Their **delta increases**, so the position’s profit explodes.
- Once ITM, their payoff curve flattens — additional drop adds less profit.
- Many **cash out or roll down**, removing pressure.

### 🏦 The Dealer (Casino)

- Dealers **sold those puts**, so they’re long **delta** as price falls.
- To stay neutral, they **sell stock** → this accelerates the drop.
- But once puts go **deep ITM**, delta ≈ –1 — it *stops changing*.
- Now the dealer **stops selling** (no further hedge needed).
- When gamblers **close or roll**, dealers **buy back hedges** → reversal.

> 💡 Analogy: Think of a beach ball underwater.
> 
> 
> As you push it deeper (toward a negative GEX pit), the pressure builds (dealer selling).
> 
> But once it hits bottom (ITM saturation), the pressure releases — and the ball pops upward.
> 

---

## Why Price Often Reverses at Major Negative Exposure

1. **Dealer Hedge Exhaustion:**
    - Dealers have already sold enough stock to hedge all open puts.
    - Once deltas saturate, no incremental hedge flow remains.
    - Selling pressure **suddenly disappears**, leaving vacuum liquidity.
2. **Gambler Profit-Taking:**
    - Option buyers **sell to close** → dealers **buy back stock** → reversal force.
3. **Charm & Theta Effects:**
    - As time passes, decaying OTM options lose delta → dealers **buy stock** back.
    - This adds *passive upward drift* (especially near expiry).
4. **Vanna Support:**
    - If IV starts falling after the move (vol crush), +VEX below spot kicks in →
        
        dealers **buy to re-hedge**, accelerating the rebound.
        

**Result:**

A cascading **sell → stop → buy** sequence — creating what looks like a “mysterious” bottom.

---

## Why Positive Exposure Appears After a Reversal

You often see, only AFTER a reversal, Heatseeker shows **positive GEX / VEX clusters** forming just above.

This isn’t coincidence — it’s structural behavior.

| Step | What Happens | Interpretation |
| --- | --- | --- |
| 1 | Puts closed, calls opened | New exposure on the other side |
| 2 | Dealers flip from short delta to slightly long delta | Hedging polarity flips |
| 3 | New +GEX cluster forms | Dealer control zone returns |
| 4 | Vol crush confirms | Reversion / range re-establishes |

> 💡 ELI5: After a storm, lifeguards (dealers) rebuild the sandbar closer to shore.
> 
> 
> That new sandbar becomes another magnet node — the new +GEX shelf.
> 

---

## Why Price Ignores Some Large Exposure Levels Far Away

Not every big exposure level matters — the market only “feels” it when its **gamma and vanna sensitivities** overlap with current volatility conditions.

**Reasons price may not gravitate to distant nodes:**

1. **Low Sensitivity (Far OTM):**
    - Deltas are too small to matter; dealer hedge flow negligible.
    - The “magnet” isn’t active until spot moves closer.
2. **Volatility Regime Mismatch:**
    - If vol is rising, long-dated vanna dominates — far nodes represent hedges for another timeframe.
    - Dealers don’t adjust intraday to distant exposures.
3. **OI Decay / Expiry Misalignment:**
    - If that node is mostly from an expiry weeks away, today’s gamma flow is driven by nearer maturities.
    - Until rollover, that far node is “offline.”
4. **Flow Saturation:**
    - If many traders already rolled or hedged around that level, incremental delta change ≈ 0.
    - No new flow = no reaction.

> 💡 Analogy: Imagine a huge magnet far away behind a wall — it’s powerful, but the ball only feels it once it’s close enough.
> 
> 
> Distance = delta sensitivity, not just notional size.
> 

---

## Mindmap of Dealer–Gambler Interaction

| Stage | Gambler Action | Dealer Reaction | Feedback Loop | Visual / Analogy |
| --- | --- | --- | --- | --- |
| 1. Calm regime | Gamblers sell vol (short puts/calls) | Dealers long vol, long gamma | Mean reversion, compression | Calm water, easy buoyancy |
| 2. Shock / vol spike | Gamblers buy vol (puts/calls) | Dealers short vol, short gamma | Pro-cyclical selling, sharp moves | Water churns, pressure builds |
| 3. Strike approached | Gamblers in profit | Dealers hedge fully | Pressure peaks | Balloon pushed underwater |
| 4. Saturation | Deltas stop moving | Hedging stops | Flow vacuum | Balloon at bottom |
| 5. Profit-taking / vol crush | Gamblers close, IV ↓ | Dealers buy back hedges | Counter-move / reversal | Balloon shoots upward |
| 6. New exposure builds | New OI forms at new levels | Dealers reposition | Cycle resets | Surface calm, new waves forming |

---

# Asymmetric Setups — “Where the Casino’s Edge Flips”

> 💡 Core Idea:
> 
> 
> The market turns not when someone *decides* to buy or sell —
> 
> it turns when one side of the positioning **runs out of hedge capacity/need**.
> 
> Once the casino (dealers) can’t or don’t need to hedge anymore,
> 
> price stops accelerating in that direction — and the next imbalance takes over.
> 

---

## 🎰 What Asymmetry Actually Means

**Asymmetry** = when one side’s feedback loop (dealer or gambler) is exhausted while the other side still has room to act.

Think of the market as a tug-of-war between two algorithms:

- The **dealer** who must *hedge mechanically* (to stay delta- and vega-neutral).
- The **crowd** who *bets emotionally or strategically* (directional bias).

When either side reaches its hedge or delta saturation, the flow **flips**.

---

## ⚙️ Updated Scenario Matrix — Explicit Cause–Effect Logic

| Setup | Positioning Condition | Dealer Behavior | Gambler Behavior | Market Dynamic | Trade Implication |
| --- | --- | --- | --- | --- | --- |
| **–GEX Cluster (Negative Gamma Zone)** | Dealers short gamma → hedge pro-cyclically (sell on drop, buy on rally) | Forced sellers into weakness until deltas saturate | Put holders cash out as contracts go ITM | Selling loop slows → price violently bounces | Don’t fade early; **long reversal after dealer hedge exhaustion** (when flow dries up). |
| **+GEX Shelf (Positive Gamma Zone)** | Dealers long gamma → hedge contra-cyclically (buy dips, sell rips) | Continuous dampening of volatility | Players fade moves; sell premium | Compression intensifies → “melt-up” drift | **Long low-vol trend**, fade late overextensions. |
| **Mixed GEX/VEX Stack (e.g., +GEX above, –VEX below)** | Dealers hedging gamma vs vanna in opposite directions | Hedging flows fight each other intraday | Traders caught in fake breaks both ways | Range compression → violent breakout once one side dominates | Wait for **volatility confirmation** (VIX surge or crush) before following break. |
| **OTM VEX Cluster Far Below Spot** | Deep OTM puts with big vanna exposure | Deltas ≈ 0, no active hedging | Crowd hasn’t triggered those deltas yet | Dormant “gravity well” — only activates if IV expands *and* price drifts toward it | Ignore in calm markets — activates **only when vol expands**, not compresses. |

---

## Again on Repeat…

### 🧠 Why “–GEX Pit” = Trapdoor, Not a Hole

When we say “pit,” it’s metaphorical — not an empty space, but a **feedback loop crater**.

- In a **–GEX pit**, dealers are short gamma, so every tick lower **forces more selling**.
- But that only continues **until delta stops changing** — i.e., when puts go deep ITM.
- Once deltas ≈ –1, hedging need **stops**, and the pit **collapses upward**.

> 💡 Analogy:
> 
> 
> A –GEX pit is like sand collapsing under your feet — the faster you fall, the more the sand gives way, until it hardens at the bottom and slings you upward.
> 

---

### 🧨 Hedge Exhaustion Explained

“Hedge exhaustion” = the moment dealer deltas are **fully neutralized** and **no longer changing**.

1. Dealers sold enough stock/futures to match all put delta exposure.
2. Deltas of those puts saturate (–1).
3. Price stops creating new hedging demand.
4. Liquidity vacuum forms → smallest buy spark triggers a snapback.

This is why markets often **reverse sharply off major –GEX lows** even though nothing “fundamental” changed —

the mechanical sellers finished their job.

---

### 💫 The Balloon Underwater Analogy (Reversal Physics)

- Imagine a balloon (price) under water (vol).
- **–GEX / short gamma** pushes the balloon deeper (dealers selling into weakness).
- But the deeper it goes, the stronger the upward pressure.
- Once it hits maximum compression (ITM saturation + hedge exhaustion),
    
    the trapped air explodes upward.
    
- That’s your violent short-covering rally.

> Dealers didn’t decide to buy — they ran out of things left to sell.
> 

---

### 📊 Why You Often See +GEX / +VEX Appear *After* the Reversal

That’s structural rebirth:

1. The crowd closes puts (reducing short gamma).
2. Dealers unwind short stock (buying pressure).
3. Call interest builds as traders flip long or write covered calls.
4. New +GEX shelf forms → the next “magnet” gets built.

It’s a **feedback inversion**:

the same mechanics that caused the dump now build the base for the next pin.

---

### 📉 Why Some Massive Exposure Levels Do *Nothing*

A huge node far from spot isn’t “wrong” — it’s just **inactive for now**.

Now this doesn’t mean it will never hit (it might!)… just that the pull won’t be as strong unless/until price starts to approach it.

Exposure doesn’t matter as much until delta sensitivity (Γ × OI × proximity) gets large enough to generate flow.

**Reasons price ignores a distant node:**

| Reason | Explanation | Analogy |
| --- | --- | --- |
| **Deltas too small** | Far OTM → Δ near 0 → no hedge flow | “A huge magnet far away — can’t feel it yet.” |
| **Wrong vol regime** | IV too low → VEX dormant; or IV too high → dealers already hedged | “The wind’s blowing the wrong way.” |
| **Different expiry** | That node lives on a later maturity — different players, different book | “Different poker table.” |
| **OI already rolled** | Traders already exited → ghost exposure | “Empty chairs at the table.” |

---

## 🧠 How the Gambler–Casino Loop Evolves

| Phase | Gambler Behavior | Dealer Response | Flow Effect | Market Outcome |
| --- | --- | --- | --- | --- |
| 1. Vol Low, Calm | Gamblers sell options | Dealers long vol, long gamma | Buy dips / sell rips | Stability, compression |
| 2. Vol Rising | Gamblers buy protection | Dealers short vol, short gamma | Sell dips / buy rips | Trend acceleration |
| 3. Strike Approached | Gamblers in profit | Dealers hedge aggressively | Pro-cyclical feedback | Capitulation |
| 4. Deltas Saturate | Gamblers cash in | Dealers unwind hedges | Buyback flow | V-shaped reversal |
| 5. Vol Crush | New longs, less fear | Dealers long gamma again | Range pin | Melt-up drift |

> 💡 Simplified:
> 
> - Casino controls volatility.
> - Gamblers control *direction* until they run out of leverage.
> - Reversals happen when the casino’s risk engine flips polarity.

---

### 🧩 Summary — The “Table Tilt” Framework

- **GEX defines potential**, **VIX defines reality**.
    
    → GEX shows where the tilt *could* occur; VIX decides *if* it will.
    
- **Loops die at saturation.**
    
    → Once deltas stop changing, hedge pressure vanishes → reversal.
    
- **Vol shifts rewrite polarity.**
    
    → Vol crush restores pins; vol spikes break them.
    
- **Distance ≠ influence.**
    
    → Exposure only matters when delta/vol/time converge.
    

> 💡 Final Analogy:
> 
> 
> The market is a casino table balanced on a hinge.
> 
> GEX and VEX are weights on each side.
> 
> As players (gamblers) pile on one side, the casino keeps tilting the table back to stay level —
> 
> until they run out of counterweights.
> 
> That’s when gravity (vol) takes over… and the ball rolls fast.
> 

---

## 🧭 9. The Big Picture: Price as a Flow Ecosystem

- **Gamma (GEX)** defines *short-term reflexes* — immediate hedging.
- **Vanna (VEX)** defines *vol-spot coupling* — the slope of the terrain.
- **Charm & Theta** define *time decay* — how fast gravity changes.

When you understand **where the loops die** (ITM saturation, time decay, profit-taking)

and **where new ones form** (new OI, vol shifts),

you stop guessing direction —

and start predicting **who is forced to act next**.

> 💡 Analogy:
> 
> 
> The market is a lake.
> 
> Gamma are the waves, Vanna is the wind, Charm is the tide, Theta is the sun drying the water.
> 
> The casino doesn’t control the lake — it just keeps moving sandbags to keep itself dry.
> 
> Your edge is seeing where the next sandbag must be moved.
> 

# **Climbing Out of a –GEX Pit (Reject vs Squeeze)**

> 💡 Scenario: Price rebounds from a –GEX zone. Overhead you see new –GEX pockets (“purples”) and a larger +GEX “yellow” higher up.
> 

### **Why new –GEX pockets form**

- **OI rotation/rolls:** gamma redistributes as puts close and calls open.
- **0–2 DTE flows:** new short-gamma exposure can spawn overhead pockets.
- **Skew shifts:** surface tilt creates temporary short-γ islands.

---

### **Two Paths at Overhead –GEX**

### **1️⃣ Rejection at Purple**

- **Clues:** rejection wick, VIX curl ↑, no acceptance.
- **Cause:** IV flat→up → dealers sell (+VEX above spot activates).
- **Tactic:** short the **first failed retest** into pocket; stop above; target lower GEX/VEX shelf.

### **2️⃣ Blow-Through → Squeeze to Yellow**

- **Clues:** closes above pocket, VIX curl ↓, breadth confirms.
- **Cause:** vol compression → dealers buy (+VEX below spot fuel) → thin air to yellow.
- **Tactic:** wait for acceptance above, then **buy first pullback** into top of old pocket; trail to yellow.

---

### **Reject vs Run Checklist**

| Signal | Outcome | Reason |
| --- | --- | --- |
| VIX ↓ from peak | Run to yellow | +VEX below supports |
| VIX ↑ on tag | Reject | Selling in stress |
| Close above pocket | Run | Resistance flipped support |
| Wick + no acceptance | Reject | Short-γ still active |
| Big gap to yellow | Run | Air pocket = fuel |
| New +GEX shelf above pocket | Reject | Fresh supply cap |
| Broad market strength | Run | External flows |
| Heavy 0DTE call selling | Reject | Instant ceiling |

---

### **Execution Playbooks**

| Setup | Entry | Invalidation | Exit |
| --- | --- | --- | --- |
| **Reject-at-Purple** | Failed retest into pocket | Acceptance above / VIX ↓ | Lower shelf |
| **Blow-Through-to-Yellow** | Pullback into old pocket (top) | Re-accept below / VIX ↑ | Scale into yellow / trail HLs |

> Ladder of Purples: multiple small –GEX islands can cascade once one flips; each acceptance fuels the next.
> 

# Heatseeker: Flow Tactics & Asymmetry (No-Chase Edition)

> Goal: trade forced flows with asymmetric risk/reward. We fade, we ride reactions, and we scale only when the microstructure agrees. No chasing.
> 

---

## Core Principles

- **Flows, not calls:** We trade *who must hedge next*, not narratives.
- **No chasing:** Entries come from **edges** (node tests, failed pushes, pullback to losing side), not from fresh range breaks.
- **Asymmetry > accuracy:** If distance to the next likely **exhaustion area** is large, we can **ride** with great risk to reward.
- **Size with conviction:** **Speculative = small size**; **stacked confluence = medium size**; **king-node confluence + vol regime alignment = aggressive size**.

---

## 1) Approaching Exposure Levels — What Actually Moves Price

### Lifecycle of a strike (why reactions cluster)

- **Far OTM:** Δ ≈ 0 → dealers don’t hedge → node “dormant.”
- **ATM:** Δ ≈ 0.5 → hedging explodes → feedback active.
- **Deep ITM:** Δ → ±1 → **delta change stops** → hedging stops → **loop dies** → reversals common.

**Read:** The **approach** to a node is dangerous; *through* the node often calms as deltas saturate.

### Negative GEX (downside) behavior

- **Players:** Put buyers’ deltas rise OTM→ATM→ITM; many **cash/roll** once ITM (profit locked, less convexity left).
- **Dealers:** Short puts → **sell stock** into the drop; once puts are **deep ITM**, **no incremental hedge** → selling **stops**.
- **Outcome:** Vacuum → small buy sparks **reversal**. Often followed by **+GEX/+VEX** building just above as flows flip.

> Analogy: Beach ball underwater. Push deeper (–GEX), pressure builds. Hit the “bottom” (ITM saturation), pressure releases upward.
> 

---

## 2) Why Big Nodes Far Away Sometimes “Do Nothing”—and When They Matter

**Inactive (for now) if:**

- Δ tiny (far OTM), **vol regime misaligned** (e.g., VEX dormant), or **different expiry** (flow lives on another table).

**High-R/R opportunity:**

- If you’re **on a GEX floor** and there’s a **far OTM VEX/GEX target**:
    - The **distance to next exhaustion** is big → **trend rides** can be excellent *if* vol path agrees.
    - Trade with **tight invalidation** and **smaller size** until mid-curve vanna/realized vol confirm.

**Guideline:** We **don’t ignore** far nodes; we **classify** them:

- **Dormant:** No near-term Δ sensitivity.
- **Warming:** Spot drifts closer or IV path starts to engage vanna.
- **Active:** Δ sensitivity + IV path + tenor alignment → **flows “turn on.”**

---

## 3) Between Nodes — The Tug-of-War Zone

When spot sits between opposing nodes (e.g., **+GEX below / –GEX above** or **+VEX vs –VEX**), hedges pull both ways.

**Tactics (no chasing):**

- **Edge probes:** Fade **first touch** of either edge with **tight stop just beyond** the node.
- **Failed push = signal:** If price pierces an edge and **snaps back** as VIX **curls opposite**, re-enter **with the move** (it’s the losing side tapping out).
- **Vol decides:** In chop, wait for **VIX direction** to pick the winner, then **ride pullbacks** *toward* the winning side’s node.

---

## 4) Stacked Nodes — The Volatility Spring

Stacks (opposite sign, close together) store potential energy. Crossing flips dealer flow abruptly.

| Stack | Flow Flip (Dealer) | What to Trade (No Chase) |
| --- | --- | --- |
| **+GEX over –GEX** | Buy-dips → Sell-dips | **Short the first failed retest from below** (pullback into the stack), stop just above. |
| **–GEX over +GEX** | Sell-dips → Buy-dips | **Long the first failed retest from above**, stop just below. |
| **+VEX over –VEX** | Sell-in-stress → Buy-in-stress | Enter **on pullback** when **IV compresses** after flip. |
| **–VEX over +VEX** | Buy-in-stress → Sell-in-stress | **Fade strength** only **with IV expanding**; reduce if IV curls down. |

> We trade failed tests and pullbacks, not fresh breaks.
> 

---

## 5) Alignment vs Misalignment (GEX ↔ VEX)

**Aligned** = flows agree → easier trades.

**Misaligned** = gamma vs vanna fight → chop, fakeouts, **then** decisive move **when one side runs out of hedge**.

| State | What It Means | How We Trade (No Chase) |
| --- | --- | --- |
| **+GEX +VEX** | Dealer control + vanna support | Fade extremes at the **+GEX edges**; add on **VIX bleed**; ride **pullbacks** toward king node. |
| **–GEX –VEX** | Pro-cyclical both ways | **Sell rips / buy dips** only at **known pullback levels** toward the next exhaustion. Tight stops; let distance work for you. |
| **+GEX –VEX** | Pin vs suppression | Fade rips **into +GEX**; if IV starts **compressing**, reduce short bias. |
| **–GEX +VEX** | Acceleration vs support | Look for **panic extension → vanna buy response**; **buy the retest** when IV curls down. |

**Why misalignment happens:** different expiries drive each Greek (0–2DTE for GEX, ~7–30DTE for VEX), skew asymmetry, OI rotation, or a sudden vol regime change.

---

## 6) Entries, Exits, Invalidation (Playbooks You Can Implement)

**A) –GEX Cluster (downside) — Reversal Play (no chase)**

- **Intent:** Catch **hedge exhaustion** + **profit-taking** snap.
- **Entry:** Wait for **extension into the pit**, then a **failed continuation** (wick/absorption) **+ VIX curl down**. Enter on **pullback** toward the failed level.
- **Invalidation:** New lows **with** VIX **still rising** (loop alive).
- **Exit:** Next **+GEX shelf** or **first strong supply node**; trail if VIX keeps bleeding.

**B) +GEX Shelf (support) — Drift/Ride**

- **Intent:** Ride mean-reversion and charm/vanna drift.
- **Entry:** **First rejection wick** into the shelf, or **pullback** after a higher low **with VIX flat→down**.
- **Invalidation:** Two closes **below** the shelf **with VIX rising**.
- **Exit:** King node / next ceiling; trim into **IV floor** or late-day pin.

**C) Stacked Flip (–GEX → +GEX or reverse) — Pullback Only**

- **Intent:** Trade the new polarity **after** the break.
- **Entry:** **Wait** for the break to happen, then take the **first pullback into the flipped stack** (no chasing the break).
- **Invalidation:** Clean acceptance back on the original side.
- **Exit:** Next exposure shelf; if **distance to exhaustion** is large, trail behind higher lows / lower highs.

**D) Misaligned (–GEX +VEX) — Panic & Bounce**

- **Intent:** Let gamma impulse exhaust, then ride vanna response.
- **Entry:** Look for **capitulation wick** + **VIX stall**; enter on **pullback** toward the wick top.
- **Invalidation:** VIX makes **new highs** and price **ignores** the wick (loop still on).
- **Exit:** To **mid-curve node** or first +GEX shelf; trail if VIX continues to curl lower.

---

## 7) Position Sizing Framework (R/R First)

- **Speculative (dormant far node, early misalignment):** **¼–⅓ size**, wide target, **tight invalidation**. You’re paid by **distance to exhaustion**, not win rate.
- **Aligned flows (e.g., +GEX+VEX under spot, VIX ↓):** **Standard size**; fade extremes, recycle P&L.
- **Stacked confluence (flip + vol confirmation):** Size on **pullback** entry only; never on the initial push.

---

## 8) Quick Signal Map (What We Actually Watch)

- **At an edge:** wick/absorption + **VIX curl** opposite the prior impulse.
- **At a stack:** clean flip → **wait** → pullback **into** the stack → enter.
- **Between nodes:** fading edges until one side **fails**; then ride **toward** the victor’s node.
- **Far node rides:** GEX floor + vol path aligned + big runway to next exhaustion → **ride with partials**, keep a **tight invalidation**.

---

## 9) What We *Don’t* Do

- We **don’t** chase fresh breaks.
- We **don’t** pre-empt vol regime shifts without **VIX path** evidence.
- We **don’t** size up on speculative “maybe it wakes up” plays; we **size small** and let **distance** pay us if it does.

---

### One-liner Mental Models

- **GEX = brakes. VEX = slope. VIX = weather.**
- **Loops die at saturation** (Δ stops changing) → that’s your reversal fuel.
- **Distance to exhaustion = your edge** — ride it, but only from pullbacks, never from chases.