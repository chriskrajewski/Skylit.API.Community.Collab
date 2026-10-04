---
name: MNQ gamma-node
description: >-
  Use when grading or taking Raked's MNQ gamma-node trade: a Heatseeker floor,
  ceiling, or king tap that sweeps and reclaims on a 3-minute close. Entry A
  (limit at the node) is off. Cite the board time. This playbook is for this
  strategy; the Academy playbook still covers every other Heatseeker read.
---

# MNQ gamma-node (Raked)

Use this when the task is Raked's MNQ gamma-node strategy. The evidence is [JOURNAL.md](JOURNAL.md) (2026-07-13 through 2026-09-30, 57 sessions). Constants are in [parameters.json](parameters.json).

This is one member's researched playbook. It does not replace the Academy playbook for other setups. Where the two disagree, name the disagreement and follow this file only for this strategy.

Heatseeker is the map. MNQ is where the order goes. A fill is only legal on the board whose timestamp is the latest at or before the decision. If the next print drops the node, flips it, or moves the king to the other side, the setup is dead.

## The trade

**Entry B only.** Entry A (limit resting at the node) is off. In the study, 209 of A's 378 stops later reached the target: the read was right and the limit sat inside the sweep.

1. Live, use the latest board at the decision. In the study, the setup was taken from the **previous** minute's board so the tap itself could not leak into the level. Either way the node has to be on the board you are using, and a later print that drops it kills the trade.
2. Candidates, per QQQ and per NDXP: the floor (strongest node below spot) is a long, the ceiling (strongest above) is a short, and the king is long or short from the side it sits on. A node must be at least **50% of that symbol's king** (`STRONG`).
3. QQQ and NDX levels within **6 NQ points** are one level, source "both".
4. A tap is price touching the node from the right side. Price must travel **0.5R** away before the next tap counts.
5. After the tap, price must **sweep** through the node. A **3-minute** candle must then **close back** on the original side within **15 minutes**. Enter at that close.
6. Stop is beyond the sweep **plus 1 NQ point**. On a tap that the structural filters would have rejected (independent B), the stop is still beyond the sweep plus 1, and at least **5 NQ points** from the node. Target is **1 NQ point in front of** the target node, measured from the B entry. Skip a stop wider than **50 NQ** or a reward:risk under **3**.
7. Flat by **15:55 ET**. No new entry at or after 15:55.
8. Risk about **$100**. MNQ is $2 a point, so contracts are `floor(100 / (stop points × 2))`. Account sim used in the study: **3 trades/day** and a **−$250** day stop. Several B trades may be open together; the 3/day cap is the account rule, not a one-at-a-time rule.

Independent B is in. Taps rejected for a third touch, a sub-3R path from the node, or a wide structural stop are still B candidates. The sweep-based stop is what makes them tradable. That path was 63 of the 92 B trades and carried the result.

A tight-stop retry (node at least 80% of the king, with confluence, stop 5 points beyond the node) helped B in the study. Leave it on for B. The velocity filter fired 3 times in 57 days; leave it on, do not expect it to change the day.

## Before the open

1. **QQQ net gamma.** Positive: nodes tended to hold, full size. Negative: B was only slightly positive in September (+0.12R), so **half size**. Do not take A.
2. **King location.** Trade the node only when the king **is** that node, or the king sits **in the profit direction** (it pulls price toward the target). **King behind the trade: skip (R1).**

## As price arrives

3. **Approach speed**, last 5 minutes, in NQ points. **40 or less:** valid. Wait for the sweep and the 3-minute reclaim.
4. **Over 40 NQ points into the node: stand aside (R2).** The first hit tends to go through. The **second tap** was the best B trade in both halves of the study (about +2.3R to +3.0R, ~70% wins). Let the first fast hit go and wait for the retest.

## During the day

5. **Two failures on the same node today: that node is dead (R3)** until the map moves it. This is "the node is dead today", not a magic tap count. On 2026-08-17 the same nodes failed again and again.
6. **SPY or SPXW pinned at its own floor or ceiling** (within 0.10% of that symbol's level): follow-through was **worse** in both halves. **Half size.** This is the opposite of "confluence means bigger". Say so when you size the trade.
7. Half size does not stack. If gamma is negative and SPY/SPX is pinned, the journal gives each condition its own half-size cut and does not say to quarter. Use one half-size cut.

## Do not "improve" the stop

Re-simulated on the real 1-second path, every change to B was worse in both July–August and September: breakeven at +1R, breakeven at +2R, and a 1.5× or 2× wider stop. Keep the stop beyond the sweep.

B's leak is winners given back (24 stops had reached a median +1.7R first). A partial at +2R is the **next test**, not a rule. Do not invent it in a live call.

## What the study actually showed

| Book | Trades | Win | Avg R | Net with 3/day and −$250 | Max DD |
| --- | --- | --- | --- | --- | --- |
| B, no extra rules | 92 | 47% | +1.34 | +$8,265 | $471 |
| B + R1–R4 | 53 | 68% | +2.7 | +$9,874 | $267 |
| Entry A | 438 | 13% | −0.24 | −$3,104 | $5,396 |

B out of sample, before R1–R4: July–August +1.60R (61 trades), September +0.83R (31 trades). With R1–R4, September is **12 trades**. R1 and R2 rest on a handful of losses. August 11 was a large piece of the earlier B profit. Treat R1–R4 as a checklist to apply and then check, not as a finished law.

## Hard passes

- Entry A as the position.
- King behind the trade (R1).
- Fast approach, first hit (R2). The retest can still be B.
- A node that has already lost twice today (R3).
- New entry at or after 15:55 ET, or a hold through 15:55.
- Stop wider than 50 NQ, or reward:risk under 3 from the B entry.
- No target node.
- A board that is not the latest print at the decision.
- Moving the stop to breakeven because the trade is green.
- Quarter-size because two half-size flags fired.
- Quoting R1–R4 as confirmed. Point at the September sample.

## How to speak a read

Board time, spot, QQQ net gamma, king side, the node (floor / ceiling / king, and whether QQQ and NDX match), tap number, approach in NQ points over 5 minutes, whether SPY/SPX is pinned, then B / half size / pass. One sentence on the stop (beyond the sweep) and the target (1 point in front of the next strong node). Do not dump the strike ladder. Do not present the print as an order that already happened.
