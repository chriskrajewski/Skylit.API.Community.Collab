# Traceability: Skill_Document rules and the Playbook_Baseline

This file maps each rule in the three Skill_Documents to the Strategy_Config key paths and values in `configs/playbook_baseline.yaml` that codify it, or marks it "not codified" with a reason (Req 17.10). It then lists the rule pairs that cannot both be followed as written (Req 17.11). It records what the Playbook_Baseline does. It makes no claim that any rule has edge.

Sources, relative to `members/alantiix/skylit-academy-playbook-skill/`:

- SKILL = `SKILL.md`
- CW = `skill-fine-tune-wip/current_working_SKILL_100226.md`
- TASK = `skill-fine-tune-wip/TASK.md`

"Source" gives the document and its section heading, separated by "›". "opening" is the text above a document's first heading. TASK has no headings, so its sections are the labels that start its paragraphs. TASK's opening paragraph names the default Combine account. That name and id are left out of this file on purpose (Req 17.5, 17.10).

How to read "Codified as":

- Each `<key path> = <value>` is a Strategy_Config key path and its Playbook_Baseline value: the value written in `configs/playbook_baseline.yaml`, or the Config_Schema default where the file leaves the key out. Values use YAML syntax.
- "Approximate" marks a rule that the keys only approach.
- "not codified" gives the reason. These rows become Unmeasured in the Revised_Drafts (Req 26.12).
- `tests/unit/test_traceability.py` checks every key path and value in this file against the loaded Playbook_Baseline.

## Rules

### Opening

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| OP01 | Heatseeker is context, never a signal generator | SKILL › opening; CW › opening | `gates.chart_confluence.enabled = true`<br>A Candidate_Setup starts from a Node but is not A+ without a Chart_Level at the converted level. |
| OP02 | The knowledge base wins over the public Academy pages when they conflict; name the conflict | SKILL › opening; CW › opening | not codified: a rule for reading sources, with no trading decision. The conflicts it settles are listed under "Conflicts the Skill_Documents resolve". |
| OP03 | Bootcamp kill switches win over "keep hunting A+" | CW › opening | `gates.kill_switch_lockout.enabled = true` (rows KS01 to KS05) |
| OP04 | Never invent a node | SKILL › opening; CW › opening | not codified: no key. Nodes come only from Snapshot strikes (Node_Classifier, Req 6); `nodes.node_fraction = 0.2` sets which strikes count. |
| OP05 | Cite `asOf`, `spot` and metric on every heatmap read | SKILL › opening; CW › opening | not codified: a reporting rule with no key. Each Candidate_Setup records every `asOf`, the source spot and the Node metric (Req 10.12). |

### Charts first, futures where

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| CH01 | Draw structure on ES/NQ; SPX/SPY/QQQ are for gamma only | SKILL › Charts first, futures where; CW › Charts first, futures where | `gates.chart_confluence.enabled = true`<br>Chart_Levels come from futures bars (design §9); the Skylit symbols supply Nodes only. |
| CH02 | Form the thesis from hourly levels first; do not stare at 3-minute unless price is at a key hourly level | SKILL › Charts first, futures where; CW › Charts first, futures where | `chart.pivot_len = {"60": 3, "240": 3}`<br>Approximate: 1-hour and 4-hour swings are Chart_Levels; there is no 3-minute timeframe. |
| CH03 | Convert the Heatseeker node onto the futures chart: ES inherits SPX (±5 pts), NQ inherits QQQ (±$0.50 scaled by live NQ/QQQ) | SKILL › Charts first, futures where; CW › Charts first, futures where | `data.es_source_symbol = SPX`<br>`data.nq_source_symbol = QQQ`<br>`levels.es_half_width_pts = 5.0`<br>`levels.qqq_half_width_usd = 0.5` |
| CH04 | Confirm or kill with Heatseeker (Trinity + VEX); execute at the converted tap, not mid-node | SKILL › Charts first, futures where; CW › Charts first, futures where | `gates.trinity_agreement.enabled = true`<br>`gates.deflection_band.enabled = true` |
| CH05 | VIX into resistance favors ES longs; a VIX consolidation break up is a bearish trend day, a break down bullish | SKILL › Charts first, futures where; CW › Charts first, futures where | not codified: VIX is read only as a session value (the Regime VIX condition and the VIX gap); there are no VIX chart levels. |
| CH06 | When VIX is expanding, VEX overrides GEX | SKILL › Charts first, futures where; CW › Charts first, futures where | `regime.vanna_multiple = 2.0`<br>`regime.vix_condition.enabled = true`<br>`regime.vix_condition.pct = 5.0`<br>Vanna_Dominant needs normalized VEX at least 2× normalized GEX and VIX 5% above the prior close. |
| CH07 | Mark the London high/low at 8am as liquidity-hunt magnets | SKILL › Charts first, futures where; CW › Charts first, futures where | `gates.chart_confluence.enabled = true`<br>Approximate: the London high and low are Chart_Levels from 08:00 (design §9); "magnet" has no rule of its own. |
| CH08 | Timmy Hunt: wait for the wick through the level, enter the rejection back through | SKILL › Charts first, futures where; CW › Charts first, futures where | not codified: the Chart_Feature_Builder records liquidity sweeps (design §9), but no Pattern or Gate needs one before entry. |
| CH09 | SMT: NQ/ES diverge at the extreme, treat as exhaustion | SKILL › Charts first, futures where; CW › Charts first, futures where | not codified: no ES/NQ divergence feature. |

### Ten Commandments

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| TC01 | Protect capital first | SKILL › Ten Commandments; CW › Ten Commandments | not codified: a principle. Its concrete rules are the Kill_Switches (rows KS02 to KS05, TK20) and the account rules. |
| TC02 | Reversals only at floors or ceilings; no diddling in the middle | SKILL › Ten Commandments; CW › Ten Commandments | `gates.midpoint.enabled = true`<br>`gates.midpoint.lo = 0.33`<br>`gates.midpoint.hi = 0.67`<br>Fails an entry in the middle third between Floor and Ceiling unless the source Node is the King or a Gatekeeper. |
| TC03 | Asymmetric R:R only (minimum 3:1) | SKILL › Ten Commandments; CW › Ten Commandments | `gates.min_reward_risk.enabled = true`<br>`gates.min_reward_risk.min = 3.0` |
| TC04 | Always know where price was delivered from | SKILL › Ten Commandments; CW › Ten Commandments | not codified: no "delivered from" feature. |
| TC05 | Broader market / Trinity awareness | SKILL › Ten Commandments; CW › Ten Commandments | `gates.trinity_agreement.enabled = true`<br>`gates.trinity_agreement.min_agree = 2` |
| TC06 | Technical analysis before Heatseeker | SKILL › Ten Commandments; CW › Ten Commandments | `gates.chart_confluence.enabled = true` |
| TC07 | Confluence between nodes and price action | SKILL › Ten Commandments; CW › Ten Commandments | `gates.chart_confluence.enabled = true` |
| TC08 | Do not oversize; scale with conviction | SKILL › Ten Commandments; CW › Ten Commandments | `sizing.micro_equivalent_limit = 50`<br>`sizing.trinity_size_down.enabled = true`<br>Approximate: size steps down on 2-of-3 Trinity, after a big-win day and on a VIX gap; it never steps up. |
| TC09 | Long on red candles, short on green candles; never chase | SKILL › Ten Commandments; CW › Ten Commandments | `gates.candle_color.enabled = true`<br>`chart.candle_timeframe_s = 60` |
| TC10 | Do not let a green trade go red | SKILL › Ten Commandments; CW › Ten Commandments | `exits.breakeven.enabled = true`<br>`exits.breakeven.trigger_r = 1.0`<br>`exits.breakeven.offset_ticks = 1`<br>Approximate: a trade that peaks below +1R can still close red. |

### Vocabulary

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| VO01 | Pika (+GEX) is friction: buy dips, sell rips | SKILL › Vocabulary; CW › Vocabulary | `patterns.gatekeeper_fade.enabled = true`<br>Gatekeeper_Fade fades Pika Gatekeepers. |
| VO02 | Barney (-GEX) is fuel: pro-cyclical | SKILL › Vocabulary; CW › Vocabulary | not codified: a definition. Net GEX below 0 can make the Regime Negative_Gamma (Req 7). |
| VO03 | Absolute Value Rule: size first, sign second; the biggest `abs(value)` wins | SKILL › Vocabulary; CW › Vocabulary | not codified: built into the Node_Classifier, which ranks Nodes by absolute value (Req 6); no key. |
| VO04 | King: largest absolute node, one per session, magnetic into the cash close | SKILL › Vocabulary; CW › Vocabulary | `patterns.trend_follow.enabled = true`<br>Approximate: Trend_Follow trades toward the King; the King is taken per Snapshot, so it can change within a session. |
| VO05 | Floor / ceiling: largest node below / above spot, not the nearest | SKILL › Vocabulary; CW › Vocabulary | not codified: a definition built into the Node_Classifier; no key. |
| VO06 | Gatekeeper: checkpoint between spot and a larger node | SKILL › Vocabulary; CW › Vocabulary | `nodes.gatekeeper_fraction = 0.3` |
| VO07 | Air pocket: pathway, never a fade target | SKILL › Vocabulary; CW › Vocabulary | `nodes.air_pocket_min_width_pct = 0.5`<br>`gates.air_pocket_fade.enabled = true` (row HP05) |
| VO08 | Clear Skies: no overhead nodes; full-send continuation or reversal, not a gap to invent | SKILL › Vocabulary; CW › Vocabulary | `nodes.lookout_pct = 1.0`<br>Approximate: the label is codified; no Pattern uses it. |
| VO09 | Empty Basement: no nodes under a floor; hard floor even if SPX shows a lower magnet | SKILL › Vocabulary; CW › Vocabulary | `nodes.lookout_pct = 1.0`<br>`patterns.floor_ceiling_bounce_empty_basement.enabled = true`<br>`gates.trinity_agreement.empty_basement_exception = true` |
| VO10 | Zones, not ticks: SPX/ES ±5 to ±10 around a high-value node | SKILL › Vocabulary; CW › Vocabulary | `levels.es_half_width_pts = 5.0`<br>The band uses the low end of ±5 to ±10. |

### Session map

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| SM01 | Read the map in order: King, strongest floor and ceiling, Gatekeepers on the path, air pockets, where spot is, Trinity, VEX + VIX | SKILL › Session map (under two minutes); CW › Session map (under two minutes) | not codified: a reading order. The engine computes every item at each Decision_Time (`time.decision_cadence_s = 60`). |
| SM02 | Trinity for metals: GLD/SLV | SKILL › Session map (under two minutes); CW › Session map (under two minutes) | not codified: no metals are traded; `live.ignored_instruments = [MGC, SIL]`. |
| SM03 | Never trade the midpoint | SKILL › Session map (under two minutes); CW › Session map (under two minutes) | `gates.midpoint.enabled = true` (row TC02) |

### Live map

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| LM01 | Never trade, ping, or grade a frozen or stale Heatseeker map | SKILL › Live map (hard rule); CW › Live map (hard rule) | `gates.stale_map.enabled = true`<br>`gates.stale_map.max_snapshot_age_s = 90`<br>`live.live_max_snapshot_age_s = 30` |
| LM02 | Live copilot: fresh Heatseeker every cycle (5 min); cite that cycle's asOf | SKILL › Live map (hard rule); CW › Live map (hard rule) | `live.refresh_interval_s = 5`<br>`notify.interval_min = 5`<br>The map refreshes every 5 s; a Finding_Card goes out at least every 5 min. |
| LM03 | If king/floor/ceiling polarity or the working node is gone vs last cycle, the setup is dead | SKILL › Live map (hard rule); CW › Live map (hard rule) | `orders.cancel_triggers.king_flip = true`<br>`orders.cancel_triggers.source_node_gone = true`<br>`orders.cancel_triggers.sign_flip = true` |
| LM04 | Backtests: a fill is legal only on the map whose asOf is the latest at-or-before the fill bar | SKILL › Live map (hard rule); CW › Live map (hard rule) | not codified: built into the point-in-time layer (Req 5); no key. |
| LM05 | If the next snapshot drops that node, flips polarity, or moves the king to the other side, treat it as scratched, not a runner | SKILL › Live map (hard rule); CW › Live map (hard rule) | `orders.invalidation.king_flip = exit_market`<br>`orders.invalidation.source_node_gone = exit_market`<br>`orders.invalidation.sign_flip = exit_market` |
| LM06 | Do not replay a whole day off the 9:35 print | SKILL › Live map (hard rule); CW › Live map (hard rule) | `time.decision_cadence_s = 60`<br>Map_State is rebuilt at every Decision_Time. |
| LM07 | A+ still needs chart + live Heatseeker + execution; a 9:35 node gone by 10:05 is not A+ | SKILL › Live map (hard rule); CW › Live map (hard rule) | `gates.chart_confluence.enabled = true`<br>`gates.stale_map.enabled = true`<br>`orders.cancel_triggers.source_node_gone = true` |

### Regime

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| RG01 | +GEX, VIX calm: fade extremes | SKILL › Regime; CW › Regime; SKILL › Execution; CW › Execution | `gates.regime_match.allowed.gatekeeper_fade = [Positive_Gamma, Whipsaw]`<br>`gates.regime_match.allowed.floor_ceiling_bounce = [Positive_Gamma, Whipsaw]`<br>`gates.regime_match.allowed.beach_ball = [Positive_Gamma, Whipsaw]` |
| RG02 | +GEX: wider stops | SKILL › Regime; CW › Regime; SKILL › Execution; CW › Execution | not codified: Positive_Gamma uses the same stop rule as every other Regime, `exits.per_regime.Positive_Gamma.stop_rule = one_node_beyond`. See W1. |
| RG03 | +GEX: tighter targets (next node) | SKILL › Regime; CW › Regime; SKILL › Execution; CW › Execution | `exits.per_regime.Positive_Gamma.mode = next_node`<br>`exits.modes.next_node.enabled = true`<br>See conflicts C1 to C3. |
| RG04 | -GEX trend: follow | SKILL › Regime; CW › Regime; SKILL › Execution; CW › Execution | `gates.regime_match.allowed.trend_follow = [Negative_Gamma, Vanna_Dominant]`<br>No fade Pattern is allowed in Negative_Gamma. |
| RG05 | -GEX: tighter stops | SKILL › Regime; CW › Regime; SKILL › Execution; CW › Execution | not codified: Negative_Gamma uses the global stop rule, `exits.global.stop_rule = one_node_beyond`. See W2. |
| RG06 | -GEX: wider targets | SKILL › Regime; CW › Regime; SKILL › Execution; CW › Execution | not codified: Negative_Gamma has no per-Regime setting (`exits.per_regime.Negative_Gamma = null`), so it uses the 3R or opposition exit. See W2. |
| RG07 | VEX / Rainbow Road: VEX magnitude >> GEX, VIX expanding; GEX levels are unreliable; trade with the trend; do not sit it out | SKILL › Regime; CW › Regime | `regime.vanna_multiple = 2.0`<br>`regime.vix_condition.enabled = true`<br>`gates.regime_match.allowed.trend_follow = [Negative_Gamma, Vanna_Dominant]`<br>Approximate: only Trend_Follow is allowed in Vanna_Dominant, and it still reads gamma Nodes (`patterns.trend_follow.metric = gamma`). |
| RG08 | VEX: target distant VEX nodes that come back to life | SKILL › Regime; CW › Regime | not codified: Vanna_Dominant has no per-Regime setting (`exits.per_regime.Vanna_Dominant = null`), so it uses the 3R or opposition exit. See W3. |
| RG09 | Rainbow Road (Patternpedia): no coherent node structure at all; sit; label which Rainbow Road you mean | SKILL › Regime; CW › Regime | `gates.regime_match.enabled = true`<br>The two meanings are two Regimes, Vanna_Dominant (RG07) and Structureless. No Pattern lists Structureless in `gates.regime_match.allowed`. The Structureless threshold is row OV01. |
| RG10 | Whipsaw: index conflict or equal opposing nodes; fade extremes with limits; no directional chase | SKILL › Regime; CW › Regime | `regime.whipsaw_pct = 15.0`<br>`gates.regime_match.allowed.whipsaw_fade = [Whipsaw]`<br>Trend_Follow is not allowed in Whipsaw. |

### Scenario matrix

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| SX01 | +GEX +VEX below spot: full dealer control; fade dips, melt-up grind | SKILL › Scenario matrix; CW › Scenario matrix | not codified: no Regime or Gate reads the VEX sign on each side of spot. |
| SX02 | +GEX +VEX above spot: ceiling thickens; fade spikes | SKILL › Scenario matrix; CW › Scenario matrix | not codified: no Regime or Gate reads the VEX sign on each side of spot. |
| SX03 | -GEX -VEX below spot: panic / trapdoor; do not fade | SKILL › Scenario matrix; CW › Scenario matrix | not codified: no Regime or Gate reads the VEX sign on each side of spot. Fades are already barred in Negative_Gamma (row RG04). |
| SX04 | -GEX -VEX above spot: squeeze fuel; follow continuation | SKILL › Scenario matrix; CW › Scenario matrix | not codified: no Regime or Gate reads the VEX sign on each side of spot. |
| SX05 | -GEX +VEX below spot: volatile floor, bounce on vol crush | SKILL › Scenario matrix; CW › Scenario matrix | not codified: no Regime or Gate reads the VEX sign on each side of spot. |
| SX06 | +GEX -VEX above spot: sticky resistance | SKILL › Scenario matrix; CW › Scenario matrix | not codified: no Regime or Gate reads the VEX sign on each side of spot. |

### Patterns

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| PT01 | Rug: +GEX stacked above -GEX, no floor; short the yellow rejection into green | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.rug.enabled = true`<br>`patterns.rug.stack_pct = 0.5`<br>`gates.regime_match.allowed.rug = [Positive_Gamma, Negative_Gamma]` |
| PT02 | Rug: if purple grows on the break it is real; if it shrinks, liquidity hunt / fakeout | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: no Gate reads the Barney Node's velocity. The node_growth_divergence Gate reads the source Node of fade Patterns only. |
| PT03 | Rug: cover at next node or ride the air pocket | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `exits.per_regime.Positive_Gamma.mode = next_node`<br>Approximate: a Rug covers at the next Node in Positive_Gamma only; in Negative_Gamma the 3R or opposition exit applies. "Ride the air pocket" is not codified. |
| PT04 | Reverse Rug: -GEX above +GEX; long the yellow floor after purple rejects | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.reverse_rug.enabled = true`<br>`patterns.reverse_rug.stack_pct = 0.5` |
| PT05 | Reverse Rug: scale 30% first Gatekeeper, 40% King, 30% overshoot | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: no three-target exit. TP1_Partial_BE has two targets at most and is off (`exits.modes.tp1_partial_be.enabled = false`). See W4. |
| PT06 | Reverse Rug: Clear Skies above = slingshot | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: no Pattern or exit reads Clear Skies. |
| PT07 | Gatekeeper fade: fade tests 1-2 of a yellow node; the 3rd test flips to breakthrough, do not fade the 3rd | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.gatekeeper_fade.enabled = true`<br>`gates.third_gatekeeper_test.fail_at_test = 3` |
| PT08 | Gatekeeper fade: if the market opens above the King, high odds of a fade back through it | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: no rule compares the open with the King. |
| PT09 | Whipsaw: two opposing nodes within ~10-15% magnitude | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `regime.whipsaw_pct = 15.0` |
| PT10 | Whipsaw: limits only, lowball $0.10-$0.20 | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.whipsaw_fade.entry_offset_ticks = 0`<br>Approximate: every entry is a limit order at the converted level; the lowball is not applied. |
| PT11 | Whipsaw: long red into floor, short green into ceiling | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.whipsaw_fade.enabled = true`<br>`gates.candle_color.enabled = true` |
| PT12 | Whipsaw: if one node grows and the other shrinks, stop fading, follow | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `gates.node_growth_divergence.enabled = true`<br>`gates.node_growth_divergence.fail_at_pct = 20.0`<br>Approximate: the fade stops when the source Node grows 20%; "follow" is not codified. |
| PT13 | Beach Ball: price into a major -GEX pit; limit buy on red into the pit; never chase; 3:1 required | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.beach_ball.enabled = true`<br>`patterns.beach_ball.major_fraction = 0.5`<br>`gates.candle_color.enabled = true`<br>`gates.min_reward_risk.min = 3.0` |
| PT14 | Beach Ball: pit velocity +20% = magnet | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified as a magnet: a Beach Ball whose pit grows 20% fails the node_growth_divergence Gate instead (`gates.node_growth_divergence.fail_at_pct = 20.0`). See W5. |
| PT15 | Beach Ball: 3rd touch of the pit favors break, not bounce | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `gates.tap_count.max_tap_seq = 2` |
| PT16 | Overshoot snapback (the public Academy's "Beach Ball"): do not chase punch-throughs as breakouts | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `gates.deflection_band.enabled = true` (row HP06) |
| PT17 | Floor Bounce / Empty Basement: hard floor, nothing underneath; 2-of-3 Trinity is enough; if QQQ holds, SPX's lower magnet can be overruled | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.floor_ceiling_bounce_empty_basement.enabled = true`<br>`gates.trinity_agreement.min_agree = 2`<br>`gates.trinity_agreement.empty_basement_exception = true` |
| PT18 | Floor Bounce: scale 25-50% starter at ±5, full size on the deeper dip | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: one entry per Candidate_Setup, and the Order_Planner never adds to a position. |
| PT19 | Floor Bounce: after the bounce the floor node should shrink; if it grows, fake bounce | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `gates.node_growth_divergence.enabled = true`<br>`gates.node_growth_divergence.fail_at_pct = 20.0`<br>Approximate: the growth is measured before entry (Node_Velocity), not after the bounce. |
| PT20 | Trend: King far from spot, void on one side; directional only, no mean reversion | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `patterns.trend_follow.enabled = true`<br>`patterns.trend_follow.trend_min_pct = 1.0`<br>`patterns.trend_follow.max_intermediate = 1` |
| PT21 | Trend: do not chase the 3:30-4:00 ET unwind | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | `gates.late_session_chase.enabled = true`<br>`gates.late_session_chase.start = "15:30"`<br>`gates.late_session_chase.end = "16:00"` |
| PT22 | Pika Cloud: dense +GEX cluster; chop; size down on anything that has to punch through | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: clustering is off (`nodes.clustering.enabled = false`), and no size-down rule reads clusters. |
| PT23 | Stairstep: a fresh node delivers into the next growing untested node | SKILL › Patterns (magnitude still overrides); CW › Patterns (magnitude still overrides) | not codified: no Stairstep Pattern. |

### Node lifecycle

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| NL01 | Fresh → tested → delivered → decaying | SKILL › Node lifecycle; CW › Node lifecycle | `nodes.decay_fraction = 0.2`<br>The lifecycle labels are built in; the fraction sets Decaying. |
| NL02 | King / major-node reaction: 1st ~80% full size, 2nd ~66% size down, 3rd ~33% small or pass, 4th spent | SKILL › Node lifecycle; CW › Node lifecycle | `gates.tap_count.enabled = true`<br>`gates.tap_count.max_tap_seq = 2`<br>Approximate: the 3rd and later taps pass; the 2nd tap is not sized down. |
| NL03 | Gatekeeper fade vs break: 1st/2nd fade, 3rd do not fade | SKILL › Node lifecycle; CW › Node lifecycle | `gates.third_gatekeeper_test.enabled = true`<br>`gates.third_gatekeeper_test.fail_at_test = 3` |
| NL04 | Sloppy Seconds: after a tap, if node value drops, the level is dead; do not re-trade it | SKILL › Node lifecycle; CW › Node lifecycle | `gates.sloppy_seconds.enabled = true`<br>`nodes.sloppy_seconds.window_min = 15`<br>`nodes.sloppy_seconds.fraction = 0.1` |
| NL05 | Real nodes grow across sessions; hedge/decoy nodes are far OTM, fade and look huge; ignore bleeders | SKILL › Node lifecycle; CW › Node lifecycle | not codified: Decaying is labeled (`nodes.decay_fraction = 0.2`), but no Gate fails a Decaying Node. Far-OTM Nodes are row NL06. |
| NL06 | Far-OTM >3-4% from spot is dormant unless the VEX regime is on | SKILL › Node lifecycle; CW › Node lifecycle | `gates.dormant_node.enabled = true`<br>`nodes.dormant_distance_pct = 3.5`<br>No Node is Dormant in Vanna_Dominant. |
| NL07 | Label nodes Active / Warming / Dormant | SKILL › Node lifecycle; CW › Node lifecycle | not codified: Active and Warming are not labels; Dormant is (row NL06). |
| NL08 | Multiple Gatekeepers between spot and a far target kill the target | SKILL › Node lifecycle; CW › Node lifecycle | `gates.gatekeepers_on_path.enabled = true`<br>`gates.gatekeepers_on_path.fail_at = 2` |
| NL09 | V-shape fakeout: break of a node and the node value increases = hunt, fade the break | SKILL › Node lifecycle; CW › Node lifecycle | not codified: no Pattern fades a break. |

### Execution

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| EX01 | Enter only at a direct node tap, inside the deflection band: SPY/QQQ ±$0.50, SPX/ES ±5 points, NQ the QQQ band scaled by live NQ/QQQ | SKILL › Execution; CW › Execution | `gates.deflection_band.enabled = true`<br>`levels.es_half_width_pts = 5.0`<br>`levels.qqq_half_width_usd = 0.5` |
| EX02 | Long red, short green; limits; never market-chase | SKILL › Execution; CW › Execution | `gates.candle_color.enabled = true`<br>`patterns.gatekeeper_fade.entry_offset_ticks = 0`<br>Every entry is a resting limit order at the converted level. |
| EX03 | Session 2: entries at liquidity, fewer higher-quality trades, buy the red instead of waiting for a green confirmation | CW › Execution | `gates.candle_color.enabled = true`<br>`kill_switches.max_trades.max = 3` |
| EX04 | Stops: one node beyond invalidation | SKILL › Execution; CW › Execution | `exits.global.stop_rule = one_node_beyond`<br>`exits.per_regime.Positive_Gamma.stop_rule = one_node_beyond`<br>`patterns.gatekeeper_fade.fixed_stop_ticks = 8`<br>With no Node beyond within the lookout, the stop is 8 ticks beyond the band edge. |
| EX05 | Minimum 3:1 or pass; 50K house minimum 3:1; Bootcamp session 2 allows 2:1 or 3:1, keep 3:1 on the combine | SKILL › Execution; CW › Execution | `gates.min_reward_risk.min = 3.0`<br>`gates.min_reward_risk.alert_min = 2.0`<br>2:1 setups are alerts only (row TK14). |
| EX06 | Futures cannot size for zero; be pickier than options | SKILL › Execution; CW › Execution | not codified: a principle with no key. |
| EX07 | Prop: no news window 5 min either side of CPI/NFP if the firm forbids it | SKILL › Execution; CW › Execution | `gates.news_window.enabled = true`<br>`gates.news_window.minutes = 5`<br>`gates.news_window.event_types = [CPI, NFP, FOMC]` |
| EX08 | Consistency: the best day stays under ~$1,500 of the $3,000 target | SKILL › Execution; CW › Execution | `kill_switches.daily_profit_cap.cap_usd = "1200"`<br>`account.profit_target.value = "3000.00"`<br>`account.consistency_target.pct = 55.0`<br>Approximate: the day cap stops new entries once the day nets $1,200, but one trade can still carry the day past $1,500. The Account_Simulator applies the account's own 55% rule (`docs/account-rules.md`). |
| EX09 | Default 5 MES / 3 MNQ (~$375 risk) until an MLL buffer exists | SKILL › Execution; CW › Execution; TASK › Size | `sizing.mode = fixed_contracts`<br>`sizing.fixed.MES = 5`<br>`sizing.fixed.MNQ = 3`<br>`sizing.risk_usd = "375"`<br>Approximate: `sizing.risk_usd` applies only in `fixed_dollar_risk` mode, and size never grows with the MLL buffer. |
| EX10 | Do not average down unless the add was planned pre-trade (scale-in) and Heatseeker still confirms | SKILL › Execution; CW › Execution | not codified: no key. The Order_Planner never adds to a position, so neither a planned nor an unplanned add can happen (`orders.max_open = 1`). |
| EX11 | FOMO pass: if price has already traveled more than 60% of the expected 3R, do not chase the tap | SKILL › Execution; CW › Execution; TASK › Size | `gates.fomo_travel.enabled = true`<br>`gates.fomo_travel.max_fraction = 0.6`<br>Approximate: measured against the distance to TP1, which is 3R only when the target is 3R. |

### A+

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| AP01 | ES/NQ chart structure names the location (hourly, then 3m trigger) | SKILL › A+ (all three); CW › A+ (all three) | `gates.chart_confluence.enabled = true`<br>`chart.pivot_len = {"60": 3, "240": 3}`<br>Approximate: the trigger is the 1-minute candle (`chart.candle_timeframe_s = 60`), not 3 minutes. |
| AP02 | Heatseeker: a live high-magnitude node at the converted level (the asOf at the tap); a King or empty-basement floor beats a thin twice-tapped Gatekeeper | SKILL › A+ (all three); CW › A+ (all three) | `gates.stale_map.enabled = true`<br>`nodes.node_fraction = 0.2`<br>Approximate: Nodes are not ranked against each other; each Candidate_Setup is graded alone. |
| AP03 | Tap entry, correct stop, ≥3:1, regime-matched, commandment 9-10 | SKILL › A+ (all three); CW › A+ (all three) | `gates.deflection_band.enabled = true`<br>`exits.global.stop_rule = one_node_beyond`<br>`gates.min_reward_risk.min = 3.0`<br>`gates.regime_match.enabled = true`<br>`gates.candle_color.enabled = true`<br>`exits.breakeven.enabled = true` |
| AP04 | Missing one = B or pass | SKILL › A+ (all three); CW › A+ (all three) | `gates.min_reward_risk.alert_min = 2.0`<br>Approximate: there is no B grade. A setup that fails any enabled Gate is Pass, except one that fails only min_reward_risk at 2:1 or better, which is Alert_2R and gets no order. |
| AP05 | Full Trinity = full size; 2 of 3 = size down; divergence = wait | SKILL › A+ (all three); CW › A+ (all three) | `sizing.trinity_size_down.enabled = true`<br>`sizing.trinity_size_down.fraction = 0.5`<br>`gates.trinity_agreement.min_agree = 2` |
| AP06 | SPX as the outlier is more serious than QQQ lagging, except Empty Basement: a QQQ/SPY floor can overrule an SPX lower magnet | SKILL › A+ (all three); CW › A+ (all three) | `gates.trinity_agreement.empty_basement_exception = true`<br>Approximate: otherwise the three symbols weigh the same. |

### Hard passes

Each item of the "Hard passes" section is one row. The section is the same in SKILL and CW.

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| HP01 | No chart thesis | SKILL › Hard passes; CW › Hard passes | `gates.chart_confluence.enabled = true`<br>Approximate: a Chart_Level within the band stands in for a chart thesis. |
| HP02 | Midpoints | SKILL › Hard passes; CW › Hard passes | `gates.midpoint.enabled = true`<br>`gates.midpoint.lo = 0.33`<br>`gates.midpoint.hi = 0.67` |
| HP03 | Sloppy Seconds | SKILL › Hard passes; CW › Hard passes | `gates.sloppy_seconds.enabled = true` (row NL04) |
| HP04 | Fading a 3rd Gatekeeper test | SKILL › Hard passes; CW › Hard passes | `gates.third_gatekeeper_test.enabled = true`<br>`gates.third_gatekeeper_test.fail_at_test = 3` |
| HP05 | Fading inside an air pocket | SKILL › Hard passes; CW › Hard passes | `gates.air_pocket_fade.enabled = true`<br>`gates.air_pocket_fade.max_depth_pts = 0.0`<br>With baseline entries at the Node level (`patterns.gatekeeper_fade.entry_offset_ticks = 0`, `nodes.clustering.enabled = false`) this Gate should never fail; the Gate_Funnel shows whether it does. |
| HP06 | Chasing overshoots as breakouts | SKILL › Hard passes; CW › Hard passes | `gates.deflection_band.enabled = true`<br>Approximate: no Pattern trades a breakout, and the Gate fails a setup whose price is already through the band. |
| HP07 | Directional trades on a structureless map | SKILL › Hard passes; CW › Hard passes | `gates.regime_match.enabled = true`<br>No Pattern lists Structureless; the threshold is row OV01. |
| HP08 | Trinity divergence (unless Empty Basement exception) | SKILL › Hard passes; CW › Hard passes | `gates.trinity_agreement.enabled = true`<br>`gates.trinity_agreement.min_agree = 2`<br>`gates.trinity_agreement.empty_basement_exception = true` |
| HP09 | No 3:1 | SKILL › Hard passes; CW › Hard passes | `gates.min_reward_risk.enabled = true`<br>`gates.min_reward_risk.min = 3.0` |
| HP10 | Fighting a reshuffled map | SKILL › Hard passes; CW › Hard passes | `orders.cancel_triggers.king_flip = true`<br>`orders.cancel_triggers.source_node_gone = true`<br>`orders.cancel_triggers.sign_flip = true`<br>`orders.invalidation.king_flip = exit_market`<br>`orders.invalidation.source_node_gone = exit_market`<br>`orders.invalidation.sign_flip = exit_market` |
| HP11 | Letting a winner turn into a loser | SKILL › Hard passes; CW › Hard passes | `exits.breakeven.enabled = true`<br>`exits.breakeven.trigger_r = 1.0`<br>Approximate, as row TC10. |
| HP12 | 3:30-4:00 chase | SKILL › Hard passes; CW › Hard passes | `gates.late_session_chase.enabled = true`<br>`gates.late_session_chase.start = "15:30"`<br>`gates.late_session_chase.end = "16:00"`<br>Fails Trend_Follow, or any trade toward the King, in that window. |
| HP13 | Unplanned average-down | SKILL › Hard passes; CW › Hard passes; TASK › Size | not codified: no key. The Order_Planner never adds to a position, so the rule cannot be broken. |
| HP14 | 60% of the R already gone | SKILL › Hard passes; CW › Hard passes | `gates.fomo_travel.enabled = true`<br>`gates.fomo_travel.max_fraction = 0.6` |
| HP15 | Kill-switch lockout | SKILL › Hard passes; CW › Hard passes | `gates.kill_switch_lockout.enabled = true`<br>Lockouts come from the Kill_Switches, rows KS02 to KS05 and TK20. |
| HP16 | F map / ambiguous map | SKILL › Hard passes; CW › Hard passes | `gates.map_grade.enabled = true`<br>`gates.map_grade.fail_grades = [F_Map]`<br>`regime.grade.major_fraction = 0.5`<br>`regime.grade.floor_ceiling_ratio = 1.5` |
| HP17 | Opposition inside 3R | SKILL › Hard passes; CW › Hard passes | `gates.opposition_inside_target.enabled = true`<br>`gates.opposition_inside_target.fraction = 0.85`<br>`gates.opposition_inside_target.window_r = 3.0` |
| HP18 | Third test of the same node this week | SKILL › Hard passes; CW › Hard passes | `gates.weekly_node_tests.enabled = true`<br>`gates.weekly_node_tests.max_tests = 2` |
| HP19 | News/black-swan window | SKILL › Hard passes; CW › Hard passes | `gates.news_window.enabled = true`<br>`gates.news_window.minutes = 5`<br>`gates.news_window.event_types = [CPI, NFP, FOMC]`<br>Approximate: black swans have no calendar, so only scheduled releases are codified. |
| HP20 | VIX 15-20% gap until the map settles | SKILL › Hard passes; CW › Hard passes | `gates.vix_gap.enabled = true`<br>`gates.vix_gap.pct = 15.0`<br>`gates.vix_gap.until = "12:00"`<br>"Until the map settles" is taken as 12:00 ("often after noon", row BC07). |
| HP21 | Any setup graded off a frozen or stale map | SKILL › Hard passes; CW › Hard passes | `gates.stale_map.enabled = true`<br>`gates.stale_map.max_snapshot_age_s = 90`<br>`live.live_max_snapshot_age_s = 30` |

### How to speak a read

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| SP01 | Chart thesis → converted node → King / floor / ceiling → GEX vs VEX control → Trinity → tap count → A+ or pass; cite asOf; do not dump every strike; never present a Heatseeker print as an order | SKILL › How to speak a read; CW › How to speak a read | not codified: a format for the Finding_Card and the Narrator (Req 25), with no key. |

### Bootcamp kill switches

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| KS01 | Edge does not matter if you cannot survive; these override "there is another A+" | CW › Bootcamp kill switches (50K copilot) | `gates.kill_switch_lockout.enabled = true` |
| KS02 | Max 1-3 trades/day; after 3, done even if a king taps | CW › Bootcamp kill switches (50K copilot); TASK › Size | `kill_switches.max_trades.enabled = true`<br>`kill_switches.max_trades.max = 3` |
| KS03 | 2 losers on the day: lockout; no more take-it pings; map-only if the king flips | CW › Bootcamp kill switches (50K copilot); TASK › Size | `kill_switches.max_losers.enabled = true`<br>`kill_switches.max_losers.limit = 2`<br>`kill_switches.losing_trade_tolerance_usd = "0"` |
| KS04 | 3 losers in a row: rest of day off and the next session off | CW › Bootcamp kill switches (50K copilot); TASK › Size | `kill_switches.consecutive_losers.enabled = true`<br>`kill_switches.consecutive_losers.limit = 3` |
| KS05 | Red day rule: do not try to flip the day green; flatten and stop | CW › Bootcamp kill switches (50K copilot); TASK › Size | `kill_switches.red_day.enabled = true`<br>`kill_switches.red_day.threshold_usd = "0"` |
| KS06 | After a big win day (the ~$1,200 cap or a full 3R): the next session starts at 50% size (3 MES / 2 MNQ) | CW › Bootcamp kill switches (50K copilot); TASK › Size | `sizing.big_win.usd = "1200"`<br>`sizing.big_win.r = 3.0`<br>`sizing.big_win.reduced.MES = 3`<br>`sizing.big_win.reduced.MNQ = 2` |
| KS07 | Thesis required before any ping: location, trigger, stop, 3R | CW › Bootcamp kill switches (50K copilot) | `gates.min_reward_risk.min = 3.0`<br>Every Candidate_Setup carries an entry, a stop and a target, or it is skipped (Req 10.14). |
| KS08 | Session 3 Greeks explain why GEX/VEX exist; they are not 50K execution rules; no option strikes on the combine | CW › Bootcamp kill switches (50K copilot) | not codified: no options are traded. |

### Bootcamp sessions 5–10

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| BC01 | Map grade every read: an A+ map has 1-2 clear majors and an obvious side; an F map has 5+ lit nodes or a side you cannot say in one sentence; sit; if it confuses you, skip | CW › Bootcamp sessions 5–10 (50K overlay) | `gates.map_grade.fail_grades = [F_Map]`<br>`regime.grade.major_fraction = 0.5`<br>`regime.grade.floor_ceiling_ratio = 1.5`<br>Approximate: "an obvious side" is read as the larger of Floor and Ceiling being at least 1.5× the smaller. |
| BC02 | Do not tunnel-vision the king; check equal-pull opposition; if that node sits inside 3R, pass | CW › Bootcamp sessions 5–10 (50K overlay) | `gates.opposition_inside_target.fraction = 0.85`<br>`gates.opposition_inside_target.window_r = 3.0`<br>"Equal pull" is read as at least 85% of the source Node's absolute value. |
| BC03 | Stacked nodes in a ~5-pt cluster outweigh one bright isolated node | CW › Bootcamp sessions 5–10 (50K overlay) | not codified: clustering is off (`nodes.clustering.enabled = false`, width `nodes.clustering.es_width_pts = 5.0`), and clusters never change Node labels. |
| BC04 | Same node, max two tests per week; the third is 33% or a break, not a fresh A+ | CW › Bootcamp sessions 5–10 (50K overlay); TASK › Size | `gates.weekly_node_tests.enabled = true`<br>`gates.weekly_node_tests.max_tests = 2` |
| BC05 | Heatseeker is the compass, not the bible; charts first | CW › Bootcamp sessions 5–10 (50K overlay) | `gates.chart_confluence.enabled = true` |
| BC06 | Heatseeker has no match for earnings, CPI/NFP, tweets, or black swans; skip those windows | CW › Bootcamp sessions 5–10 (50K overlay) | `gates.news_window.event_types = [CPI, NFP, FOMC]`<br>Approximate: earnings, tweets and black swans have no calendar, so they are not codified. |
| BC07 | VIX gap of 15-20% or a noisy open: half size or sit until the map settles (often after noon) | CW › Bootcamp sessions 5–10 (50K overlay); TASK › Size | `sizing.vix_gap.enabled = true`<br>`sizing.vix_gap.pct = 15.0`<br>`gates.vix_gap.until = "12:00"`<br>The baseline does both: on a 15% gap the vix_gap Gate fails every setup before 12:00, and size is halved after. The noisy open is row BC08. |
| BC08 | The first 15-30 min shuffle; wait for a clean tap; do not force the 9:35 print | CW › Bootcamp sessions 5–10 (50K overlay) | `gates.open_shuffle.enabled = true`<br>`gates.open_shuffle.min_minutes = 15` |
| BC09 | 50K is a small account: no runners, base hits; flatten at 3R or at the next opposition node, whichever comes first; do not hold for the far king | CW › Bootcamp sessions 5–10 (50K overlay) | `exits.global.mode = opposition_or_fixed_r`<br>`exits.modes.opposition_or_fixed_r.r_multiple = 3.0`<br>`exits.modes.opposition_or_fixed_r.fraction = 0.85`<br>`exits.modes.trailing.enabled = false`<br>Positive_Gamma overrides this (conflict C3). |
| BC10 | Conflict: Friday 0DTE "no stops, lotto, hold to close" is options; futures still use a legal stop and flatten 15:55; ignore options-only rules | CW › Bootcamp sessions 5–10 (50K overlay) | `orders.flatten_time = "15:55"`<br>`exits.global.stop_rule = one_node_beyond` |
| BC11 | A London sweep of the lows is a long bias only if Heatseeker agrees | CW › Bootcamp sessions 5–10 (50K overlay) | not codified: no bias rule reads sweeps. |
| BC12 | The last hour can look mechanical; we still do not chase 3:30-4:00 | CW › Bootcamp sessions 5–10 (50K overlay) | `gates.late_session_chase.enabled = true` |

### TASK

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| TK01 | Trade the default Combine account (named in TASK; not copied here) | TASK › opening paragraph | not codified: the Config_Schema holds no account name or id (Req 17.5). |
| TK02 | Practice is sandbox only | TASK › opening paragraph | `order_mode = paper`<br>The baseline never sends a broker order; Practice and Combine are opt-in Order_Modes. |
| TK03 | Copilot during RTH, America/New_York | TASK › opening paragraph | `time.timezone = America/New_York` |
| TK04 | When a setup grades A+ (3:1+), take it end-to-end without waiting for "take it" | TASK › AUTONOMOUS A+ (3:1+) | `order_mode = paper`<br>`gates.min_reward_risk.min = 3.0`<br>In the baseline, A+ entries go to the in-process Paper_Broker. |
| TK05 | Prefer limit arming at the node with map color, a planned stop one node beyond and resting TPs; do not chase mid-air | TASK › AUTONOMOUS A+ (3:1+) | `patterns.gatekeeper_fade.arming.es_pts = 10.0`<br>`patterns.gatekeeper_fade.arming.nq_qqq_usd = 1.0`<br>`exits.global.stop_rule = one_node_beyond`<br>Every Pattern has the same arming distances. |
| TK06 | Arm at a Beach Ball, a stdev -2 to -2.5 zone or an aligned dark-pool print | TASK › AUTONOMOUS A+ (3:1+) | `patterns.beach_ball.enabled = true`<br>`gates.stdev_fib_zone.enabled = false`<br>`gates.dark_pool_confluence.enabled = false`<br>TASK lists std-dev and dark-pool levels as confluence layers, not hard passes, so their Gates are off but still measured. |
| TK07 | Cancel the resting limit if the king flips, the BoS/stdev leg invalidates, equal-pull opposition enters 3R, or price blows through without a tap | TASK › AUTONOMOUS A+ (3:1+) | `orders.cancel_triggers.king_flip = true`<br>`orders.cancel_triggers.stdev_leg_dropped = true`<br>`orders.cancel_triggers.opposition_in_target = true`<br>Approximate: "blows through without a tap" has no Cancel_Trigger. |
| TK08 | If already filled, manage: BE once working ~TP1 or clean hold | TASK › AUTONOMOUS A+ (3:1+); TASK › Each run | `exits.breakeven.enabled = true`<br>`exits.breakeven.trigger_r = 1.0`<br>Approximate: "~TP1 or clean hold" is taken as +1R. |
| TK09 | If already filled, trail | TASK › AUTONOMOUS A+ (3:1+) | not codified: Trailing is an Exit_Mode with no target (`exits.modes.trailing.enabled = false`), so it cannot run with the 3R target. See W6. |
| TK10 | Flatten 15:55 | TASK › AUTONOMOUS A+ (3:1+); TASK › Size; TASK › Each run | `orders.flatten_time = "15:55"` |
| TK11 | Still playbook-gated: long red / short green, no chase, no frozen maps, min 3:1 with no equal-pull opposition inside 3R, legal stop, not a 3rd-test GK fade, not a 3:30-4:00 chase | TASK › AUTONOMOUS A+ (3:1+) | `gates.candle_color.enabled = true`<br>`gates.stale_map.enabled = true`<br>`gates.min_reward_risk.min = 3.0`<br>`gates.opposition_inside_target.window_r = 3.0`<br>`gates.third_gatekeeper_test.enabled = true`<br>`gates.late_session_chase.enabled = true` |
| TK12 | No new entries 15:25 or later | TASK › AUTONOMOUS A+ (3:1+) | `gates.entry_cutoff.enabled = true`<br>`gates.entry_cutoff.cutoff = "15:25"` |
| TK13 | Only one armed A+ limit at a time unless the Operator says otherwise | TASK › AUTONOMOUS A+ (3:1+) | `orders.max_open = 1` |
| TK14 | Surface playbook-gated 2:1 setups as pings; do not auto-enter or arm limits on 2:1 | TASK › 2:1 ALERTS (ping only) | `gates.min_reward_risk.alert_min = 2.0`<br>`notify.alerts_2r = true` |
| TK15 | Send a short finding card on every run, even on a pass | TASK › EVERY-CYCLE FINDING UPDATES | `notify.interval_min = 5`<br>`notify.sinks = [console, file]` |
| TK16 | Heatseeker live kings/GKs; NQ = QQQ+NDX+NDXP scaled onto NQ1 | TASK › CONFLUENCE LAYERS | `data.nq_sources = [QQQ, NDX, NDXP]`<br>`levels.methods.NDX = ratio`<br>`levels.methods.NDXP = ratio` |
| TK17 | 1h and 4h S/R + IB30 high/low from Atlas | TASK › CONFLUENCE LAYERS | `chart.pivot_len = {"60": 3, "240": 3}`<br>`gates.chart_confluence.enabled = true`<br>The IB30 high and low are Chart_Levels from 10:00. |
| TK18 | Std-dev + fib recipe: fib levels only 0, 1, -2, -2.25, -2.5, -3.5, -4, -4.5; BoS on 1m/5m; -2 to -2.5 first zone; -3.5 to -4.5 max expansion | TASK › CONFLUENCE LAYERS | `gates.stdev_fib_zone.enabled = false`<br>`gates.stdev_fib_zone.zones = [{near: -2.0, far: -2.5}, {near: -3.5, far: -4.5}]`<br>`chart.bos_pivot_len = {"1": 3, "5": 3}`<br>The fib ratios are fixed in the Chart_Feature_Builder (design §9). |
| TK19 | Std-dev legs: prefer the current futures day from 18:00 ET, a prior-day leg only if the trend continues; at most 2 legs per direction; delete a leg when its 0/1 extreme is swept | TASK › CONFLUENCE LAYERS | `chart.sweep_ticks = 2`<br>Approximate: the 2-leg cap and the sweep drop are fixed in the Chart_Feature_Builder (design §9); no key chooses between current-day and prior-day legs. |
| TK20 | Day cap ~$1,200 | TASK › Size | `kill_switches.daily_profit_cap.enabled = true`<br>`kill_switches.daily_profit_cap.cap_usd = "1200"` |
| TK21 | Flowseeker dark pool: top prints on QQQ/SPY each cycle, scaled onto NQ1/ES1; confluence only | TASK › CONFLUENCE LAYERS | `gates.dark_pool_confluence.enabled = false`<br>`gates.dark_pool_confluence.min_notional_usd = 1000000.0`<br>`gates.dark_pool_confluence.lookback_sessions = 5`<br>`gates.dark_pool_confluence.es_ticker = SPY`<br>`gates.dark_pool_confluence.nq_ticker = QQQ` |
| TK22 | Heatseeker and Flowseeker via Skylit, Atlas for OHLCV, ProjectX only for accounts, positions and orders; never hang waiting on ProjectX for a map grade | TASK › Data | not codified: the data sources are fixed by the design, and the Strategy_Engine does no I/O, so a grade never waits on ProjectX. |
| TK23 | Each run: fresh Heatseeker + Atlas + dark-pool prints; rebuild kings; mark stdev-fib; IB30 + 1h/4h; grade | TASK › Each run | `live.refresh_interval_s = 5`<br>`time.decision_cadence_s = 60` |
| TK24 | Verify the front-month MNQ contract | TASK › Each run | `live.expected_contracts.MNQ = CON.F.US.MNQ.Z26`<br>`live.expected_contracts.MES = CON.F.US.MES.Z26`<br>A different resolved contract blocks orders for that instrument. Contract ids are public exchange symbols, not account data. |
| TK25 | Ignore leftover MGC/SIL book noise | TASK › Each run | `live.ignored_instruments = [MGC, SIL]` |
| TK26 | First run 09:00-09:29: open map + grade | TASK › Each run | `notify.premarket = "09:00"`<br>`live.run_window.start = "09:00"` |

### Values the Skill_Documents do not settle

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| OV01 | Structureless threshold: "no coherent node structure at all" needs a minimum King absolute value, and the Skill_Documents give none | SKILL › Regime; CW › Regime | Operator: required. `regime.min_abs_value` has no default (Req 7.4); the derivation is recorded below. |

## Conflicts

### Conflict pairs

Each pair below is two codified rules that cannot both be followed as written for at least one Candidate_Setup (Req 17.11). C1 and C2 are the two pairs Req 17.11 names.

| ID | Rule A | Rule B | Where both cannot hold | Playbook_Baseline outcome |
| --- | --- | --- | --- | --- |
| C1 | RG03 "+GEX: tighter targets (next node)": `exits.per_regime.Positive_Gamma.mode = next_node` | HP09 "No 3:1": `gates.min_reward_risk.min = 3.0` | A Positive_Gamma setup whose next Node beyond entry is less than 3R away | The min_reward_risk Gate fails and the setup is passed. A next-node target is traded only when it is at least 3R away. |
| C2 | RG03 "+GEX: tighter targets (next node)": `exits.per_regime.Positive_Gamma.mode = next_node` | HP17 "Opposition inside 3R": `gates.opposition_inside_target.window_r = 3.0`, `gates.opposition_inside_target.fraction = 0.85` | A Positive_Gamma setup whose next Node lies inside 3R and has at least 85% of the source Node's absolute value: it is both the target and an opposition Node inside 3R | The opposition_inside_target Gate fails and the setup is passed. |
| C3 | RG03 "+GEX: tighter targets (next node)": `exits.per_regime.Positive_Gamma.mode = next_node` | BC09 "Flatten at 3R or at the next opposition node, whichever comes first": `exits.global.mode = opposition_or_fixed_r`, `exits.modes.opposition_or_fixed_r.r_multiple = 3.0` | A Positive_Gamma setup whose next Node lies beyond 3R. After C1, every Positive_Gamma trade taken has its target at 3R or beyond | The per-Regime setting wins (Req 12.2): the trade holds for the next Node past 3R. |

### Conflicts with one side not codified

| ID | Codified side | Not codified side | Where they disagree |
| --- | --- | --- | --- |
| W1 | EX04 "Stops: one node beyond invalidation": `exits.per_regime.Positive_Gamma.stop_rule = one_node_beyond` | RG02 "+GEX: wider stops" | Every Positive_Gamma setup gets the same stop rule as every other Regime. |
| W2 | EX04 and BC09: `exits.global.stop_rule = one_node_beyond`, `exits.global.mode = opposition_or_fixed_r` | RG05, RG06 "-GEX: tighter stops, wider targets" | A Negative_Gamma trade uses the global setting (`exits.per_regime.Negative_Gamma = null`): stop one Node beyond, flatten at 3R or the opposition Node. |
| W3 | BC09 "Do not hold for the far king": `exits.per_regime.Vanna_Dominant = null` | RG08 "Target distant VEX nodes that come back to life" | A Vanna_Dominant trade exits at 3R or the opposition Node, never at a distant VEX Node. |
| W4 | BC09 "No runners": `exits.global.mode = opposition_or_fixed_r`, `exits.modes.tp1_partial_be.enabled = false` | PT05 Reverse Rug scale-out 30% / 40% / 30% | A Reverse Rug exits all contracts at one target. |
| W5 | PT19 "If it grows, fake bounce": `gates.node_growth_divergence.fail_at_pct = 20.0` | PT14 "Pit velocity +20% = magnet" | A Beach Ball whose pit grows 20% fails the Gate, though the Beach Ball text reads that growth as a magnet. The text does not say whether a magnet pit should pass. |
| W6 | BC09 "No runners": `exits.modes.trailing.enabled = false` | TK09 "trail" | An open trade's stop never trails; it moves only to breakeven (row TK08). |

### Conflicts the Skill_Documents resolve

The documents name the winner in these cases, and the Playbook_Baseline follows it:

- 3:1 house minimum against Bootcamp session 2's 2:1 (CW › Execution): 3:1 wins for entries, and 2:1 setups are alerts only (rows EX05, TK14).
- Kill switches against "keep hunting A+" (CW › opening; CW › Bootcamp kill switches (50K copilot)): the kill switches win (rows OP03, KS01).
- Friday 0DTE options rules against a futures stop and flatten (CW › Bootcamp sessions 5–10 (50K overlay)): the futures rules win (row BC10).
- Two meanings of "Rainbow Road" (SKILL › Regime): the VEX trend is Vanna_Dominant and the structureless map is Structureless (rows RG07, RG09).
- Two meanings of "Beach Ball" (SKILL › Patterns (magnitude still overrides)): the knowledge base's -GEX pit is the `beach_ball` Pattern, and the Academy's overshoot snapback is never traded (rows PT13, PT16).
- Gatekeeper fade against King reaction counts (SKILL › Node lifecycle): the knowledge base's rule wins for intermediate yellows, so a Gatekeeper_Fade fails on its 3rd weekly test (row NL03), and any Pattern fails on its 3rd tap in a session (row NL02).

## `regime.min_abs_value` derivation

Row OV01. The Skill_Documents give no Structureless threshold, and the Config_Schema has no default (Req 7.4), so the shipped Playbook_Baseline does not load until the Operator sets it.

1. Run `fse calibrate regime-min-abs --start <date> --end <date> --percentile <P>` over the cached sessions.
2. Write the chosen value into `configs/playbook_baseline.yaml` as `regime: {min_abs_value: <value>}`.
3. Fill in the table below, and write the key path and chosen value into OV01's "Codified as" cell in the same form as the other rows.

| Field | Value |
| --- | --- |
| Command and arguments | _Operator: fill in_ |
| Printed sessions line | _Operator: fill in_ |
| Printed snapshots line | _Operator: fill in_ |
| Percentile and printed value | _Operator: fill in_ |
| Chosen `regime.min_abs_value` | _Operator: fill in_ |
| Reason for the choice | _Operator: fill in_ |
| Date set (YYYY-MM-DD) | _Operator: fill in_ |
