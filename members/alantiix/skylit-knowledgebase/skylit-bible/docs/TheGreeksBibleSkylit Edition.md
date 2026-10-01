# TheGreeksBibleSkylit Edition


## Page 1

The Greeks Bible — Skylit EditionA complete, page-structured textbook for understanding Greeks, dealer microstructure, and how to trade Heatseeker (GEX/VEX) maps.  Disclaimer — Educational Use Only 
🚧
🚧
🚧
🚧The information provided in this guide (“The Greeks Bible”) and any related Skylit, Inc. products, services, or communications is for educational purposes only.It is not financial advice, nor an offer, solicitation, or recommendation to buy, sell, or hold any security, derivative, or investment instrument.Options and derivatives trading involve substantial risk, including the potential loss of your entire investment.Past performance, market behavior, or model outputs (e.g., GEX/VEX/Heatseeker data) do not guarantee future results.Skylit, Inc., its affiliates, and contributors do not guarantee accuracy or completeness of any data or analysis and assume no liability for losses or damages resulting from the use of this material.Always conduct your own due diligence and consult with a licensed financial professional before making any trading decisions.By using this material, you agree to assume full responsibility for your own trading outcomes and acknowledge that you do so at your own risk. TL;DR: This is not advice. It’s education.
🧠Markets are risky. Trade responsibly.The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
1 of 392/3/26, 11:16 PM

## Page 2

How to Use This Book Active traders using Heatseeker to time entries and exits on indices and single names.Who this is for: By the end, you’ll think like a , understand , and translate  into actionable trade plans.Outcome:dealer (casino)hedging flowsGEX/VEX heatmapsThree Golden Rules1.GEX defines potential; VIX defines reality.2.0–5 DTE = Gamma world. 7–180 DTE = Vanna world.3.Trade where Greeks and the vol regime agree.The Casino of Markets (Dealer vs Player) Hedged, earns from flow, manages risk through Greeks.Dealer (Market Maker) = Casino: Directional, expresses bias through options, creating dealer hedging flows.Player (Customer/Fund) = Gambler: Core Idea: Each Greek is a dimension of risk the dealer must neutralize. When crowd exposure shifts, dealers rebalance — their hedges feed back into price, volatility, and liquidity.
💡 The ocean (market) moves by tides (positioning), wind (volatility), and time (theta). Dealers are lifeguards using jets (hedges) to keep waves in check.ELI5:Master Greek IndexThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
2 of 392/3/26, 11:16 PM

## Page 3

Master Greek IndexPrimary GreeksThe table below shows how option premium and other Greeks change when the underlying (S), implied volatility (IV, σ), or time (t) moves. It also notes where each Greek is largest (ATM/OTM) and how it scales with tenor (T).GreekWhat it measuresIf  (spot up)S ↑If  (σ up)IV ↑As  (t →)time passesWhere it’s biggestDelta (Δ)$ change in option for $1 move in S Δ ↑;  Δ ↓. Premium follows direction.Calls:Puts:Small indirect effect via vanna (if σ changes Δ).Drifts toward 1 (calls) / 0 (OTM) or 0 / –1 (puts) as expiry nears.ATM (steepest), saturates near 0/1/–1 far ITM/OTMGamma (Γ)Change of Δ per $1 in S (curvature)Higher Γ → Δ jumps more on S moves; increases pinning if +GEXΓ generally  if σ ↑ and T fixed (for ATM); structure shifts with smilefallsΓ  sharply into expiry (0DTE); when far from expiryrisesdecaysMax , low ITM/OTMATMVega (ν)Change in option for 1-pt change in IVMinor (via smile/spot-vol coupling)Premium  as IV ↑; contracts as IV ↓expandsTime decay reduces  closer to expirydollar vegaHighest ATMTheta (Θ)Change in option per unit (decay)timeN/A directly; path via Δ/ΓHigher IV → higher absolute Θ ($ terms); Δ drifts via Premium decaysCharmGreatest near short-datedATMVanna change for change (also change for ΔIVνSIf S ↑ with skew, can reduce IV (spot-vol coupling) → Δ shifts: +VEX → Δ ↓ (dealers buy); : +VEX → Δ ↑ (dealers IV ↓IV ↑Vanna  into expiry (via ν)fadesLargest optionsslightly OTMThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
3 of 392/3/26, 11:16 PM

## Page 4

change for change)Scoupling) → Δ shifts+VEX → Δ ↑ (dealers sell)Charm change with (holding S, σ)ΔtimeN/A directlyN/A directlyAs time passes,  if OTM, (less negative) → dealers rebalancecall Δ ↓put Δ ↑Strongest close to expirynear ATMVomma (Volga) change for change (vol convexity)νIVN/A directlyHigh Vomma → ν itself accelerates as σ moves; IV reprices non-linearlySlower decay than Γ; persists in medium/long tenorsATM to slightly OTM in long tenorsVera change for change (rates–vol cross)RhoIVHigher S may correlate with rates in indicesIf σ ↑ with rate moves, Vera matters (macro regimes)Time changes discounting effectRate-sensitive underlyingsSpeed change for changeΓSAs S nears key strikes, Γ can rise fastVia Zomma (Γ vs σ)Builds into expiryNear  with large OIstrikesColor change with ΓtimeN/AN/AΓ ramps into expiry (pin risk) then disappearsATM near expiryZomma change for changeΓIVN/Aσ ↑ can lift Γ (esp. near ATM), increasing re-hedge speedATMUltimaSecond derivative of Vomma (vol convexity of convexity)N/AGoverns tail behavior of IV; large in wings/long tenorsFar OTM wings, long TCross-Greek Mechanics (explicit dealer hedges)The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
4 of 392/3/26, 11:16 PM

## Page 5

Cross-Greek Mechanics (explicit dealer hedges)• to neutralize; .If dealer is long delta → they sell stockshort delta → they buy stock• makes those buy/sell actions  to price (stabilizing).  makes them  (amplifying).Long gamma (+GEX)contrarianShort gamma (–GEX)pro-cyclical• with  below spot, , ; signs reverse for –VEX.Vanna ties IV moves to delta:+VEXIV ↓ ⇒⇒ dealer buysIV ↑ ⇒⇒ dealer sells• even if underlying price and IV are flat, dealers .Charm changes delta with time:rebalance intradayTime & Cross-Expiry Interactions• Gamma and Charm dominate; . Same-day pins/breaks rule intraday.0DTE:Vanna ~ 0• Transition — Gamma still large,  begins to matter.1–7 DTE:Vanna• — IV path drives price drift via hedging.>30 DTE:Vanna/Vega world• Longer-dated  same-day +GEX, forcing dealers to  even inside a nominal support zone.Vol Spike with front-end +GEX:Vanna flows can overwhelmsellDelta (Δ) — The Line of Fire Change in option price per $1 move in the underlying.Definition:RoleExposureHedgeBehaviorDealerLong DeltaSells stock to stay neutralSynthetically short stockDealerShort DeltaBuys stock to stay neutralSynthetically long stockPlayerDirectionalBuys or sells calls/putsDrives dealer flowThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
5 of 392/3/26, 11:16 PM

## Page 6

ELI5: Delta is the steering wheel position right now.
💡 Immediate hedging flow drives liquidity and short-term price action. Delta’s behavior depends on gamma and vanna. Dealers short calls buy stock into squeezes.Market Influence:Vol Regimes:Example:Gamma (Γ) — The Shock Absorber Rate of change of delta per $1 in spot. Controls mean reversion vs acceleration.Definition:ConditionDealer HedgeMarket Behavior+GEX (Long Γ)Buys dips, sells ripsStability, mean reversion–GEX (Short Γ)Sells dips, buys ripsVolatility amplification ELI5: Gamma is a car suspension — smooth in calm, unstable on ice.
💡 Vol ↑ amplifies –GEX; Vol ↓ strengthens +GEX pins.Vol Behavior: +GEX below = support, +GEX above = resistance.Above/Below Spot: SPX 0DTE fades in +GEX; trends in –GEX.Example:Vega (ν) — The Breath of the Market Sensitivity to implied volatility.Definition:RoleBehaviorThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
6 of 392/3/26, 11:16 PM

## Page 7

RoleBehaviorDealerUsually short vol, hedges with optionsPlayerBuys vol in fear, sells vol in calm ELI5: Vega is the market’s breathing — inhale (IV ↑), exhale (IV ↓).
💡 Governs vol regime changes. Long vega → benefits from vol rise; short vega → benefits from vol crush.Market Impact:Theta (Θ) — The Silent Tax Time decay — option value lost each day.Definition:RoleBehaviorDealerHarvests theta when hedgedPlayerPays theta to rent convexity ELI5: Theta is a parking meter — time costs you money.
💡 Vol ↑ inflates theta costs; Vol ↓ eases them.Vol Behavior: When +GEX and +VEX align, expect strong daily pinning.Heatseeker:Vanna — The Vol–Spot CouplerThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
7 of 392/3/26, 11:16 PM

## Page 8

Delta sensitivity to IV; the vol–spot feedback loop.Definition:ExposureVol ↓Vol ↑Dealer FlowMarket Bias+VEX Below SpotBuys stockSells stockSupportive in calmPin & drift–VEX Below SpotSells stockBuys stockFragile floorWhipsaw+VEX Above SpotBuys stockSells stockResistance meltsBreakout in calm–VEX Above SpotSells stockBuys stockElastic ceilingSqueeze in stress ELI5: Vanna is a tailwind you only notice when it stops blowing.
💡 Even when today’s expiry dominates GEX,  can overpower it during vol spikes — as vol rises, back-end options create hedging pressure.Time Interaction:vanna from future expirationsCharm — The Invisible Drift Delta sensitivity to time.Definition: Dealers rebalance deltas as options decay, producing afternoon drift.Effect:RoleBehaviorDealerBuys stock as calls decayPlayerSees grind-up into OI max ELI5: Charm is cruise control adding slow throttle.
💡The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
8 of 392/3/26, 11:16 PM

## Page 9

Charm and Vanna combine in calm sessions to produce steady upward “melt. ”Interaction:Vomma / Volga — The Convexity of Vega Vega’s sensitivity to IV (vol convexity).Definition: Determines how fast IV re-prices.Effect:RoleBehaviorDealerHedges with calendars and fliesPlayerBuys convexity ahead of catalysts ELI5: Volga is a turbocharger for volatility.
💡Vanna & Charm Drift — Why Markets Float• IV compression → dealers buy back hedges (from +VEX) → price drifts up.Vanna Drift:• Time decay reduces deltas → dealers rebalance by buying → afternoon upward drift.Charm Drift: Even when 0DTE gamma dominates,  adds background buy flow during vol compression. Conversely, in vol spikes, future expiry vanna becomes , overriding short-term GEX support.Cross-Expiry Note:longer-dated vannasell flowHeatseeker Reading — King Nodes, Flip Zones & Real The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
9 of 392/3/26, 11:16 PM

## Page 10

Heatseeker Reading — King Nodes, Flip Zones & Real Scenarios Core Idea: Dealers are forced participants — they hedge every time delta or vega shifts.
💡Their flow is mechanical, and that flow  price moves depending on whether they’re long or short gamma/vanna.either dampens or amplifiesUnderstanding the Map• Dealers long gamma → their hedges oppose the market’s move → .+GEX (Positive Gamma):stabilizing / mean-reverting behavior• Dealers short gamma → their hedges chase the move → .–GEX (Negative Gamma):trending / volatile behavior• Dealer deltas increase as vol rises → they , .+VEX (Positive Vanna):sell when vol ↑buy when vol ↓→ supportive when vol is falling, suppressive when vol is rising.• Dealer deltas decrease as vol rises → they , .–VEX (Negative Vanna):buy when vol ↑sell when vol ↓→ destabilizing when vol compresses, cushioning when vol explodes.Dealer Hedge Logic SimplifiedExposureIV ↓IV ↑Dealer Hedge BehaviorMarket Feedback+VEXDealers buy → support under spotDealers sell → suppress ralliesSupportive in calm, heavy in stormsMarket stabilizes until voThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
10 of 392/3/26, 11:16 PM

## Page 11

–VEXDealers sell → weak floorDealers buy → violent bounceFragile in calm, reflexive in stressMarket unstable until vol+GEXDealers buy dips / sell ripsDealers still stabilizing (until vol overwhelms)Mean reversion, range-boundDips fade, rips fade–GEXDealers sell dips / buy ripsBoth legs accelerateTrend and breakout expansionDips deepen, rallies overCore Scenario Matrix — With Full ReasoningSetupDealer FlowVolatility FeedbackMarket Behavior+GEX +VEX Below SpotDealers buy dips (Gamma) + buy as vol falls (Vanna)Calm vol regime (VIX ↓) reinforces both flowsTight range, upward drift (“melt-up”)+GEX +VEX Above SpotDealers sell rips (Gamma) + sell more as vol rises (Vanna)Rising vol amplifies suppressionCeiling thickens; repeated rejections–GEX –VEX Below SpotDealers sell dips (Gamma) + sell as vol falls (Vanna)Both feedbacks pro-cyclicalSharp breakdowns and low-liquidity slides–GEX –VEX Above SpotDealers buy rips (Gamma) + buy more as vol rises (Vanna)Reflexive feedback loopViolent short squeezes; volatility spikes+VEX Below SpotDealers buy as vol drops → supportDealers sell if vol spikes → rug riskStrong base in calm, rug-pull in stress–VEX Below SpotDealers sell in calm → weak floorDealers buy in stress → fast bouncesFragile in calm, reflexive in panic+VEX Above SpotDealers buy as vol drops (supportive to breakouts)Dealers sell if vol ↑ (cap returns)Breakouts sustain in vol compression; fail when vol expandsThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
11 of 392/3/26, 11:16 PM

## Page 12

–VEX Above SpotDealers sell in calm (cap); buy in stress (fuel squeeze)Vol expansion flips flow → squeezesSoft ceiling in calm; launches during vol spike+GEX Below / –GEX AboveDealers buy low, chase high (unstable transition)Crossing flip zone → polarity shiftRug-pull if vol ↑, slingshot if vol ↓–GEX Below / +GEX AboveDealers sell low, fade highCompression zoneChop until breakoutDeep Explanation of the “Buy Breakout” Paradox“If dealers sell when vol ↑, why buy the breakout?”Because .the dealer flow is reactive, not predictiveWhen you see “+VEX above spot,” it means dealers will  — but the first leg of that vol expansion sell into rising volis the breakout itself.•The breakout  the vol spike.creates•Dealers’ , providing resistance.selling comes slightly after•If that selling flow fails to cap price (absorbed by real demand), you get a  — the hallmark of a “breakout that sticks.”vol-crush continuation, where +VEX resistance is , not strengthening.
✅So you buy breakouts only in vol compressionmelting if vol is expanding — that’s when +VEX resistance 
❌You don’t buy breakoutshardens.Time and Cross-Expiry Interplay• Gamma dominates → mechanical hedging, fast reversion or breakouts.Front-end (0–2 DTE):The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
12 of 392/3/26, 11:16 PM

## Page 13

• Vanna dominates → volatility regime steering; drives directional drift days later.Back-end (>7 DTE):• When front-end +GEX looks supportive, far-expiry +VEX can  — dealers suddenly selling stock to re-hedge long vol exposure.Vol Spikes:flip it bearish• Reverse happens — far-dated +VEX reinforces +GEX, producing .Vol Crush:multi-day pin and melt-up ELI5: Gamma is the brake pedal, Vanna is the slope of the hill.
💡You can tap the brakes intraday, but if the hill is steep (vol regime), you still roll in that direction.Practical Rules for Heatseeker TradingSignalInterpretationTactical Bias+GEX and +VEX stack under spotDealer net long gamma + long vanna = full controlFade dips, expect compression–GEX and –VEX stack under spotDealer short gamma + short vanna = panic zoneTrend following; avoid fadingCrossing a flip zone (+ over –)Polarity change in dealer flowExpect rug-pull or slingshotLarge King NodeMax exposure → reversion magnetTrade around, not through itWide Air PocketSparse exposure → fast price zoneExpect accelerationVol Crush + +VEXSupportive → rallies sustainRide melt-upVol Spike + +VEXDealer selling → resistance zoneFade rally or reduce longsVol Spike + –VEXDealer buying → squeeze riskRide upside but expect whipsawsThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
13 of 392/3/26, 11:16 PM

## Page 14

Mental Model: The Casino Table• → Neutral over time, but short-term reactive.Dealer = Casino• → Pushes price into zones that flip dealer behavior.Player = Crowd• → Determines how balls (price) roll.GEX = Table Shape• → Determines whether rolls accelerate or slow.VEX = Gravity• → Determines how far momentum carries.Vol = EnergyWhen you trade Heatseeker, you’re not betting on  — you’re betting on .directionwho will be forced to hedge nextBetween Nodes, Stacked Nodes & Alignment DynamicsSpot Between Nodes — “The Tug-of-War Zone”When spot trades  (e.g., a +GEX shelf below and a –GEX pocket above, or mixed VEX clusters), price action becomes a contest of opposing dealer hedges.between two large nodesThis is the  for new Heatseeker users — because it’s where .most confusing zoneflow polarity flips back and forth intradayConfigurationDealer PositioningVol BehaviorMarket CharacterBetween +GEX below and –GEX aboveDealers buy on dips (below) and chase higher (above)Stable vol → calm oscillation; vol ↑ → air-pocket breaksCompression that can rug or sling fastBetween –GEX below and +GEX aboveDealers sell on dips and fade ralliesVol ↓ → repeated fakeouts; Vol ↑ → large breaksChoppy, deceptive rangeThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
14 of 392/3/26, 11:16 PM

## Page 15

aboverallies→ large breaksBetween +VEX and –VEX clustersDealers flip from buying to selling as vol shiftsSmall vol shifts swing controlIV spikes = directional chaosSpot trapped between two King NodesMassive opposing dealer hedges at adjacent strikesVol flat → sticky magnetismRanges shrink intraday ELI5: When spot sits between two magnets of opposite charge, it vibrates — not trends. It only escapes when one magnet weakens (IV shift or OI decay).
💡Stacked Nodes — “The Volatility Springs”When you see , such as a , or a , think of it as a .stacked exposures of opposite signmajor +GEX node directly above a –GEX pocket+VEX node above a –VEX clusterspring under tensionCrossing that stack triggers sudden shifts in dealer hedging direction.Stack TypeDealer TransitionVolatility ResponseMarket ReactionTrade Setup+GEX over –GEXFrom buying dips → selling dipsVol ↑ magnifies change“Rug pull” down through the flipShort breakdown once undestack; stop back above–GEX over +GEXFrom selling dips → buying dipsVol ↓ stabilizes“Slingshot” up through the flipLong breakout through stacstop below+VEX over –VEXFrom selling in stress → buying in stressAs IV compresses, flip softensRelief rally through resistanceLong if IV contracting–VEX over +VEXFrom buying in stress → selling in stressVol expansion amplifies reversalSharp rejection or whipsawFade strength if vol spikingThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
15 of 392/3/26, 11:16 PM

## Page 16

ELI5: Stacks are like loaded springs — when price pushes through, dealer hedges flip polarity and release stored energy.
💡GEX/VEX Alignment vs. MisalignmentHeatseeker’s power is showing when  and  exposures agree (aligned) or disagree (misaligned).Gamma (GEX)Vanna (VEX)Alignment tells you if  and  point the same way — or fight each other.dealers’ directional hedgesvol-spot couplingAlignment StateWhat It MeansDealer FlowMarket BehaviorAligned (+GEX +VEX)Dealers long gamma  long vannaandBuy dips, sell rips; buy on vol dropsMarket stable, supportive driftAligned (–GEX –VEX)Dealers short gamma  short vannaandSell dips, buy rips; sell on vol dropsPro-cyclical acceleration, high volMisaligned (+GEX –VEX)Dealers long gamma but short vannaBuy dips (gamma) yet sell when vol dropsConflicting hedges → range + sudden breaksMisaligned (–GEX +VEX)Dealers short gamma but long vannaSell dips (gamma) but buy when vol dropsShort-term volatility, long-term support ELI5: GEX is the hand brake; VEX is the slope of the hill.
💡When both point the same way, you roll steadily. When they fight, you jerk back and forth.Why Misalignment HappensThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
16 of 392/3/26, 11:16 PM

## Page 17

Why Misalignment HappensMisalignment occurs because :Gamma and Vanna exposures are drawn from different expiries and strikes1.Time Skew:•0DTE & 1DTE dominate  (immediate delta hedging).GEX•5–30DTE dominate  (volatility-spot coupling).VEX•Hence, short-term vs mid-term flows can diverge.2.Skew Asymmetry:•Puts have much higher vanna (steeper skew).•So downside +VEX below spot can persist even when GEX flips negative (dealers short gamma intraday but long vanna overall).3.OI Rotation:•As expiries roll off, GEX collapses while VEX persists (longer tenor).•That creates brief  where intraday price motion disagrees with multi-day vol drift.misaligned days4.Vol Regime Change:•A sudden IV spike (news, CPI, FOMC) alters vanna sign faster than GEX updates.•Dealers instantly change vol hedges, but OI-based GEX still shows prior positioning → apparent mismatch.Reading Heatseeker for Alignment ContextObservationInterpretationExpected FlowTrade ImplicationThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
17 of 392/3/26, 11:16 PM

## Page 18

+GEX and +VEX clusters in same regionDealer control, long vol compressionPin & driftFade extremes; buy-the-dip bias–GEX but +VEX stacking below spotGamma short, vanna supportiveVol crush rebound likelyLong into panic lows+GEX but –VEX above spotGamma pins but vanna suppressesCeiling holds until vol collapsesShort rips in calm, flip long if vol crushesGEX flat, VEX dominantLow delta convexity, vol-driven marketPrice follows VIX flowTrade the vol regime, not directionGEX/VEX polarity shift at same strikeFlip zone confirmedDirectional accelerationUse as breakout trigger ELI5: When GEX and VEX disagree, the dealer’s two hands are pulling in opposite directions.
💡Whichever Greek dominates (Gamma intraday, Vanna across days) decides the winner.Practical Playbook SummarySituationEnvironmentWhat to ExpectTrade BiasSpot between opposite GEX nodesMixed dealer flowsRange compression → breakoutFade until breakout; then followSpot between VEX nodesVol regime indecisionChoppy → fast once VIX shiftsWait for vol signal before entryStacked opposite nodesTension / polarity zoneSudden directional snapTrade the flip momentumAligned +GEX +VEXCalm, low-volMean reversion / driftBuy dips, sell ripsAligned –GEX –VEXVolatility expansionTrend accelerationRide breakout / momentumThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
18 of 392/3/26, 11:16 PM

## Page 19

Aligned –GEX –VEXVolatility expansionTrend accelerationRide breakout / momentumMisalignedMixed signalsWhipsaw / indecisionScale smaller, fade extremes onlyCross-Expiry Insight: “The Hidden Hands”•The  explains today’s  (gamma hedging).front expiry (0DTE–2DTE)speed•The  explain the  (vanna hedging).middle expiries (7DTE–30DTE)drift•The  explain the  (vol regime).back expiries (45DTE–90DTE+)gravityWhen front-end GEX and mid-end VEX disagree, you get intraday whipsaws inside multi-day trends — the classic “grind higher but chop intraday” behavior.Approaching Major Exposure Levels — Casino vs. Gambler Dynamics Core Idea: Price doesn’t react to exposure levels because they exist —
💡it reacts because  converge at those points.crowd behavior + dealer hedgingThe “loop” only continues as long as deltas .still changeOnce contracts go ITM and deltas saturate, the loop , and flow .diesreversesTwo Players at the TableRoleObjectiveToolsLimitationThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
19 of 392/3/26, 11:16 PM

## Page 20

Dealer (Casino)Stay delta-neutral, extract premium from flowHedging stock/futures, vol tradesMust react mechanically to crowd exposureGambler (Customer/Fund/Trader)Profit from direction or volatilityBuys/sells calls and putsRuns out of gamma or delta leverage once ITMThe Life Cycle of a StrikeEvery major exposure level — whether GEX or VEX — represents a .cluster of open optionsThose contracts go through three phases as spot approaches:PhaseOption StatusDeltasDealer HedgeMarket Impact1. Far OTM (Out-of-the-Money)Speculative chipsΔ ≈ 0Dealer does almost nothingPrice ignores the node2. ATM (At-the-Money)Maximum sensitivityΔ ≈ 0.5Dealer must adjust hedge rapidlyVolatility explodes; feedback loop active3. Deep ITM (In-the-Money)Deltas saturatedΔ → 1 (call) / –1 (put)Hedge fully sized; no more adjustmentFlow stops; feedback collapsesThis means  —the danger zone is the approachnot once we’re “through it.” The hedging feedback loop has a lifecycle.What Happens as Price Approaches a Major Negative GEX Node (Downside)Let’s walk step-by-step from both perspectives:The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
20 of 392/3/26, 11:16 PM

## Page 21

Let’s walk step-by-step from both perspectives:
🧑
🧑
🎲
🎲 The Gambler (Players)•They  earlier (insurance, directional bets).bought puts•As spot drops , those puts go from .toward their strikesOTM → ATM → ITM•Their , so the position’s profit explodes.delta increases•Once ITM, their payoff curve flattens — additional drop adds less profit.•Many , removing pressure.cash out or roll down The Dealer (Casino)
🏦
🏦•Dealers , so they’re long as price falls.sold those puts delta•To stay neutral, they  → this accelerates the drop.sell stock•But once puts go , delta ≈ –1 — it .deep ITMstops changing•Now the dealer  (no further hedge needed).stops selling•When gamblers , dealers  → reversal.close or rollbuy back hedges Analogy: Think of a beach ball underwater.
💡As you push it deeper (toward a negative GEX pit), the pressure builds (dealer selling).But once it hits bottom (ITM saturation), the pressure releases — and the ball pops upward.Why Price Often Reverses at Major Negative Exposure1.Dealer Hedge Exhaustion:•Dealers have already sold enough stock to hedge all open puts.The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
21 of 392/3/26, 11:16 PM

## Page 22

•Once deltas saturate, no incremental hedge flow remains.•Selling pressure , leaving vacuum liquidity.suddenly disappears2.Gambler Profit-Taking:•Option buyers  → dealers  → reversal force.sell to closebuy back stock3.Charm & Theta Effects:•As time passes, decaying OTM options lose delta → dealers  back.buy stock•This adds  (especially near expiry).passive upward drift4.Vanna Support:•If IV starts falling after the move (vol crush), +VEX below spot kicks in →dealers , accelerating the rebound.buy to re-hedgeResult:A cascading  sequence — creating what looks like a “mysterious” bottom.sell → stop → buyWhy Positive Exposure Appears After a ReversalYou often see, only AFTER a reversal, Heatseeker shows  forming just above.positive GEX / VEX clustersThis isn’t coincidence — it’s structural behavior.StepWhat HappensInterpretation1Puts closed, calls openedNew exposure on the other side2Dealers flip from short delta to slightly long deltaHedging polarity flips3New +GEX cluster formsDealer control zone returnsThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
22 of 392/3/26, 11:16 PM

## Page 23

3New +GEX cluster formsDealer control zone returns4Vol crush confirmsReversion / range re-establishes ELI5: After a storm, lifeguards (dealers) rebuild the sandbar closer to shore.
💡That new sandbar becomes another magnet node — the new +GEX shelf.Why Price Ignores Some Large Exposure Levels Far AwayNot every big exposure level matters — the market only “feels” it when its  overlap with current volatility conditions.gamma and vanna sensitivitiesReasons price may not gravitate to distant nodes:1.Low Sensitivity (Far OTM):•Deltas are too small to matter; dealer hedge flow negligible.•The “magnet” isn’t active until spot moves closer.2.Volatility Regime Mismatch:•If vol is rising, long-dated vanna dominates — far nodes represent hedges for another timeframe.•Dealers don’t adjust intraday to distant exposures.3.OI Decay / Expiry Misalignment:•If that node is mostly from an expiry weeks away, today’s gamma flow is driven by nearer maturities.•Until rollover, that far node is “offline.”4.Flow Saturation:•If many traders already rolled or hedged around that level, incremental delta change ≈ 0.The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
23 of 392/3/26, 11:16 PM

## Page 24

If many traders already rolled or hedged around that level, incremental delta change ≈ 0.•No new flow = no reaction. Analogy: Imagine a huge magnet far away behind a wall — it’s powerful, but the ball only feels it once it’s close enough.
💡Distance = delta sensitivity, not just notional size.Mindmap of Dealer–Gambler InteractionStageGambler ActionDealer ReactionFeedback Loop1. Calm regimeGamblers sell vol (short puts/calls)Dealers long vol, long gammaMean reversion, compression2. Shock / vol spikeGamblers buy vol (puts/calls)Dealers short vol, short gammaPro-cyclical selling, sharp moves3. Strike approachedGamblers in profitDealers hedge fullyPressure peaks4. SaturationDeltas stop movingHedging stopsFlow vacuum5. Profit-taking / vol crushGamblers close, IV ↓Dealers buy back hedgesCounter-move / reversal6. New exposure buildsNew OI forms at new levelsDealers repositionCycle resetsAsymmetric Setups — “Where the Casino’s Edge Flips” Core Idea:
💡The market turns not when someone  to buy or sell —decidesThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
24 of 392/3/26, 11:16 PM

## Page 25

it turns when one side of the positioning .runs out of hedge capacity/needOnce the casino (dealers) can’t or don’t need to hedge anymore,price stops accelerating in that direction — and the next imbalance takes over. What Asymmetry Actually Means
🎰
🎰 = when one side’s feedback loop (dealer or gambler) is exhausted while the other side still has room to act.AsymmetryThink of the market as a tug-of-war between two algorithms:•The  who must  (to stay delta- and vega-neutral).dealerhedge mechanically•The  who  (directional bias).crowdbets emotionally or strategicallyWhen either side reaches its hedge or delta saturation, the flow .flips Updated Scenario Matrix — Explicit Cause–Effect Logic
⚙
⚙SetupPositioning ConditionDealer BehaviorGambler Behavior–GEX Cluster (Negative Gamma Zone)Dealers short gamma → hedge pro-cyclically (sell on drop, buy on rally)Forced sellers into weakness until deltas saturatePut holders cash out as contracts go ITM+GEX Shelf (Positive Gamma Zone)Dealers long gamma → hedge contra-cyclically (buy dips, sell rips)Continuous dampening of volatilityPlayers fade moves; sell premiumMixed GEX/VEX Stack (e.g., +GEX above, –VEX below)Dealers hedging gamma vs vanna in opposite directionsHedging flows fight each other intradayTraders caught in fake breaks both waysThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
25 of 392/3/26, 11:16 PM

## Page 26

OTM VEX Cluster Far Below SpotDeep OTM puts with big vanna exposureDeltas ≈ 0, no active hedgingCrowd hasn’t triggered those deltas yetAgain on Repeat… Why “–GEX Pit” = Trapdoor, Not a Hole
🧠
🧠When we say “pit,” it’s metaphorical — not an empty space, but a .feedback loop crater•In a , dealers are short gamma, so every tick lower .–GEX pitforces more selling•But that only continues  — i.e., when puts go deep ITM.until delta stops changing•Once deltas ≈ –1, hedging need , and the pit .stopscollapses upward Analogy:
💡A –GEX pit is like sand collapsing under your feet — the faster you fall, the more the sand gives way, until it hardens at the bottom and slings you upward. Hedge Exhaustion Explained
🧨
🧨“Hedge exhaustion” = the moment dealer deltas are  and .fully neutralizedno longer changing1.Dealers sold enough stock/futures to match all put delta exposure.2.Deltas of those puts saturate (–1).3.Price stops creating new hedging demand.4.Liquidity vacuum forms → smallest buy spark triggers a snapback.The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
26 of 392/3/26, 11:16 PM

## Page 27

4.Liquidity vacuum forms → smallest buy spark triggers a snapback.This is why markets often  even though nothing “fundamental” changed —reverse sharply off major –GEX lowsthe mechanical sellers finished their job. The Balloon Underwater Analogy (Reversal Physics)
💫
💫•Imagine a balloon (price) under water (vol).• pushes the balloon deeper (dealers selling into weakness).–GEX / short gamma•But the deeper it goes, the stronger the upward pressure.•Once it hits maximum compression (ITM saturation + hedge exhaustion),the trapped air explodes upward.•That’s your violent short-covering rally.Dealers didn’t decide to buy — they ran out of things left to sell. Why You Often See +GEX / +VEX Appear  the Reversal
📊
📊AfterThat’s structural rebirth:1.The crowd closes puts (reducing short gamma).2.Dealers unwind short stock (buying pressure).3.Call interest builds as traders flip long or write covered calls.4.New +GEX shelf forms → the next “magnet” gets built.It’s a :feedback inversionthe same mechanics that caused the dump now build the base for the next pin.The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
27 of 392/3/26, 11:16 PM

## Page 28

Why Some Massive Exposure Levels Do 
📉
📉NothingA huge node far from spot isn’t “wrong” — it’s just .inactive for nowNow this doesn’t mean it will never hit (it might!)… just that the pull won’t be as strong unless/until price starts to approach it.Exposure doesn’t matter as much until delta sensitivity (Γ × OI × proximity) gets large enough to generate flow.Reasons price ignores a distant node:ReasonExplanationAnalogyDeltas too smallFar OTM → Δ near 0 → no hedge flow“A huge magnet far away — can’t feel it yet. ”Wrong vol regimeIV too low → VEX dormant; or IV too high → dealers already hedged“The wind’s blowing the wrong way.”Different expiryThat node lives on a later maturity — different players, different book“Different poker table. ”OI already rolledTraders already exited → ghost exposure“Empty chairs at the table. ” How the Gambler–Casino Loop Evolves
🧠
🧠PhaseGambler BehaviorDealer ResponseFlow EffectMarket Outcome1. Vol Low, CalmGamblers sell optionsDealers long vol, long gammaBuy dips / sell ripsStability, compression2. Vol RisingGamblers buy protectionDealers short vol, short gammaSell dips / buy ripsTrend accelerationThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
28 of 392/3/26, 11:16 PM

## Page 29

3. Strike ApproachedGamblers in profitDealers hedge aggressivelyPro-cyclical feedbackCapitulation4. Deltas SaturateGamblers cash inDealers unwind hedgesBuyback flowV-shaped reversal5. Vol CrushNew longs, less fearDealers long gamma againRange pinMelt-up drift Simplified:
💡•Casino controls volatility.•Gamblers control  until they run out of leverage.direction•Reversals happen when the casino’s risk engine flips polarity. Summary — The “Table Tilt” Framework
🧩
🧩•, .GEX defines potentialVIX defines reality→ GEX shows where the tilt  occur; VIX decides  it will.couldif•Loops die at saturation.→ Once deltas stop changing, hedge pressure vanishes → reversal.•Vol shifts rewrite polarity.→ Vol crush restores pins; vol spikes break them.•Distance ≠ influence.→ Exposure only matters when delta/vol/time converge. Final Analogy:
💡The market is a casino table balanced on a hinge.GEX and VEX are weights on each side.The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
29 of 392/3/26, 11:16 PM

## Page 30

GEX and VEX are weights on each side.As players (gamblers) pile on one side, the casino keeps tilting the table back to stay level —until they run out of counterweights.That’s when gravity (vol) takes over… and the ball rolls fast. 9. The Big Picture: Price as a Flow Ecosystem
🧭
🧭• defines  — immediate hedging.Gamma (GEX)short-term reflexes• defines  — the slope of the terrain.Vanna (VEX)vol-spot coupling• define  — how fast gravity changes.Charm & Thetatime decayWhen you understand  (ITM saturation, time decay, profit-taking)where the loops dieand  (new OI, vol shifts),where new ones formyou stop guessing direction —and start predicting .who is forced to act next Analogy:
💡The market is a lake.Gamma are the waves, Vanna is the wind, Charm is the tide, Theta is the sun drying the water.The casino doesn’t control the lake — it just keeps moving sandbags to keep itself dry.Your edge is seeing where the next sandbag must be moved.Climbing Out of a –GEX Pit (Reject vs Squeeze) Scenario: Price rebounds from a –GEX zone. Overhead you see new –GEX pockets (“purples”) 
💡The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
30 of 392/3/26, 11:16 PM

## Page 31

Scenario: Price rebounds from a –GEX zone. Overhead you see new –GEX pockets (“purples”) and a larger +GEX “yellow” higher up.
💡Why new –GEX pockets form• gamma redistributes as puts close and calls open.OI rotation/rolls:• new short-gamma exposure can spawn overhead pockets.0–2 DTE flows:• surface tilt creates temporary short-γ islands.Skew shifts:Two Paths at Overhead –GEX Rejection at Purple
1⃣
1⃣  • rejection wick, VIX curl ↑, no acceptance.Clues:• IV flat→up → dealers sell (+VEX above spot activates).Cause:• short the  into pocket; stop above; target lower GEX/VEX shelf.Tactic:first failed retest Blow-Through → Squeeze to Yellow
2⃣
2⃣  • closes above pocket, VIX curl ↓, breadth confirms.Clues:• vol compression → dealers buy (+VEX below spot fuel) → thin air to yellow.Cause:• wait for acceptance above, then  into top of old pocket; trail to yellow.Tactic:buy first pullbackReject vs Run ChecklistSignalOutcomeReasonVIX ↓ from peakRun to yellow+VEX below supportsThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
31 of 392/3/26, 11:16 PM

## Page 32

VIX ↑ on tagRejectSelling in stressClose above pocketRunResistance flipped supportWick + no acceptanceRejectShort-γ still activeBig gap to yellowRunAir pocket = fuelNew +GEX shelf above pocketRejectFresh supply capBroad market strengthRunExternal flowsHeavy 0DTE call sellingRejectInstant ceilingExecution PlaybooksSetupEntryInvalidationExitReject-at-PurpleFailed retest into pocketAcceptance above / VIX ↓Lower shelfBlow-Through-to-YellowPullback into old pocket (top)Re-accept below / VIX ↑Scale into yellow / trail HLsLadder of Purples: multiple small –GEX islands can cascade once one flips; each acceptance fuels the next.Heatseeker: Flow Tactics & Asymmetry (No-Chase Edition)Goal: trade forced flows with asymmetric risk/reward. We fade, we ride reactions, and we scale only The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
32 of 392/3/26, 11:16 PM

## Page 33

Goal: trade forced flows with asymmetric risk/reward. We fade, we ride reactions, and we scale only when the microstructure agrees. No chasing.Core Principles• We trade , not narratives.Flows, not calls:who must hedge next• Entries come from  (node tests, failed pushes, pullback to losing side), not from fresh range breaks.No chasing:edges• If distance to the next likely  is large, we can  with great risk to reward.Asymmetry > accuracy:exhaustion arearide•; ; .Size with conviction:Speculative = small sizestacked confluence = medium sizeking-node confluence + vol regime alignment = aggressive size1) Approaching Exposure Levels — What Actually Moves PriceLifecycle of a strike (why reactions cluster)• Δ ≈ 0 → dealers don’t hedge → node “dormant. ”Far OTM:• Δ ≈ 0.5 → hedging explodes → feedback active.ATM:• Δ → ±1 →  → hedging stops →  → reversals common.Deep ITM:delta change stopsloop dies The  to a node is dangerous;  the node often calms as deltas saturate.Read:approachthroughNegative GEX (downside) behavior• Put buyers’ deltas rise OTM→ATM→ITM; many  once ITM (profit locked, less convexity left).Players:cash/roll• Short puts →  into the drop; once puts are  → Dealers:sell stockdeep ITMno incremental hedgeThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
33 of 392/3/26, 11:16 PM

## Page 34

• Short puts →  into the drop; once puts are ,  → selling .Dealers:sell stockdeep ITMno incremental hedgestops• Vacuum → small buy sparks . Often followed by  building just above as flows flip.Outcome:reversal+GEX/+VEXAnalogy: Beach ball underwater. Push deeper (–GEX), pressure builds. Hit the “bottom” (ITM saturation), pressure releases upward.2) Why Big Nodes Far Away Sometimes “Do Nothing”—and When They MatterInactive (for now) if:•Δ tiny (far OTM),  (e.g., VEX dormant), or  (flow lives on another table).vol regime misaligneddifferent expiryHigh-R/R opportunity:•If you’re  and there’s a :on a GEX floorfar OTM VEX/GEX target◦The  is big →  can be excellent  vol path agrees.distance to next exhaustiontrend ridesif◦Trade with  and  until mid-curve vanna/realized vol confirm.tight invalidationsmaller size We  far nodes; we  them:Guideline:don’t ignoreclassify• No near-term Δ sensitivity.Dormant:• Spot drifts closer or IV path starts to engage vanna.Warming:• Δ sensitivity + IV path + tenor alignment → Active:flows “turn on.”3) Between Nodes — The Tug-of-War ZoneThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
34 of 392/3/26, 11:16 PM

## Page 35

3) Between Nodes — The Tug-of-War ZoneWhen spot sits between opposing nodes (e.g.,  or ), hedges pull both ways.+GEX below / –GEX above+VEX vs –VEXTactics (no chasing):• Fade  of either edge with  the node.Edge probes:first touchtight stop just beyond• If price pierces an edge and  as VIX , re-enter  (it’s the losing side tapping out).Failed push = signal:snaps backcurls oppositewith the move• In chop, wait for  to pick the winner, then  the winning side’s node.Vol decides:VIX directionride pullbackstoward4) Stacked Nodes — The Volatility SpringStacks (opposite sign, close together) store potential energy. Crossing flips dealer flow abruptly.StackFlow Flip (Dealer)What to Trade (No Chase)+GEX over –GEXBuy-dips → Sell-dips (pullback into the stack), stop just above.Short the first failed retest from below–GEX over +GEXSell-dips → Buy-dips, stop just below.Long the first failed retest from above+VEX over –VEXSell-in-stress → Buy-in-stressEnter  when  after flip.on pullbackIV compresses–VEX over +VEXBuy-in-stress → Sell-in-stress only ; reduce if IV curls down.Fade strengthwith IV expandingWe trade failed tests and pullbacks, not fresh breaks.5) Alignment vs Misalignment (GEX ↔ VEX)The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
35 of 392/3/26, 11:16 PM

## Page 36

= flows agree → easier trades.Aligned = gamma vs vanna fight → chop, fakeouts,  decisive move .Misalignedthenwhen one side runs out of hedgeStateWhat It MeansHow We Trade (No Chase)+GEX +VEXDealer control + vanna supportFade extremes at the ; add on ; ride  toward king node.+GEX edgesVIX bleedpullbacks–GEX –VEXPro-cyclical both ways only at  toward the next exhaustion. Tight stops; let distancework for you.Sell rips / buy dipsknown pullback levels+GEX –VEXPin vs suppressionFade rips ; if IV starts , reduce short bias.into +GEXcompressing–GEX +VEXAcceleration vs supportLook for ;  when IV curls down.panic extension → vanna buy responsebuy the retest different expiries drive each Greek (0–2DTE for GEX, ~7–30DTE for VEX), skew asymmetry, OI rotation, or a sudden vol regime change.Why misalignment happens:6) Entries, Exits, Invalidation (Playbooks You Can Implement)A) –GEX Cluster (downside) — Reversal Play (no chase)• Catch  +  snap.Intent:hedge exhaustionprofit-taking• Wait for , then a  (wick/absorption) . Enter on  toward the failed level.Entry:extension into the pitfailed continuation+ VIX curl downpullback• New lows  VIX  (loop alive).Invalidation:withstill rising• Next  or ; trail if VIX keeps bleeding.Exit:+GEX shelffirst strong supply nodeB) +GEX Shelf (support) — Drift/Ride• Ride mean-reversion and charm/vanna drift.Intent:The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
36 of 392/3/26, 11:16 PM

## Page 37

Ride mean-reversion and charm/vanna drift.Intent:• into the shelf, or  after a higher low .Entry:First rejection wickpullbackwith VIX flat→down• Two closes  the shelf .Invalidation:belowwith VIX rising• King node / next ceiling; trim into  or late-day pin.Exit:IV floorC) Stacked Flip (–GEX → +GEX or reverse) — Pullback Only• Trade the new polarity  the break.Intent:after• for the break to happen, then take the  (no chasing the break).Entry:Waitfirst pullback into the flipped stack• Clean acceptance back on the original side.Invalidation:• Next exposure shelf; if  is large, trail behind higher lows / lower highs.Exit:distance to exhaustionD) Misaligned (–GEX +VEX) — Panic & Bounce• Let gamma impulse exhaust, then ride vanna response.Intent:• Look for  + ; enter on  toward the wick top.Entry:capitulation wickVIX stallpullback• VIX makes  and price  the wick (loop still on).Invalidation:new highsignores• To  or first +GEX shelf; trail if VIX continues to curl lower.Exit:mid-curve node7) Position Sizing Framework (R/R First)•, wide target, . You’re paid by , not win rate.Speculative (dormant far node, early misalignment):¼–⅓ sizetight invalidationdistance to exhaustion•; fade extremes, recycle P&L.Aligned flows (e.g., +GEX+VEX under spot, VIX ↓):Standard size• Size on  entry only; never on the initial push.Stacked confluence (flip + vol confirmation):pullbackThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
37 of 392/3/26, 11:16 PM

## Page 38

8) Quick Signal Map (What We Actually Watch)• wick/absorption +  opposite the prior impulse.At an edge:VIX curl• clean flip →  → pullback  the stack → enter.At a stack:waitinto• fading edges until one side ; then ride  the victor’s node.Between nodes:failstoward• GEX floor + vol path aligned + big runway to next exhaustion → , keep a .Far node rides:ride with partialstight invalidation9) What We  DoDon’t•We  chase fresh breaks.don’t•We  pre-empt vol regime shifts without  evidence.don’tVIX path•We  size up on speculative “maybe it wakes up” plays; we  and let  pay us if it does.don’tsize smalldistanceOne-liner Mental Models•GEX = brakes. VEX = slope. VIX = weather.• (Δ stops changing) → that’s your reversal fuel.Loops die at saturation• — ride it, but only from pullbacks, never from chases.Distance to exhaustion = your edgeThe Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
38 of 392/3/26, 11:16 PM

## Page 39

The Greeks Bible — Skylit Editionhttps://alpine-source-9ed.notion.site/The-Greeks-Bible-Skylit-Edition-2a503ffa0822800faf81f...
39 of 392/3/26, 11:16 PM