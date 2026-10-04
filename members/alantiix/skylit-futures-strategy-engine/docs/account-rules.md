# Account rules: Topstep 50K Trading Combine

**The Operator verifies every account rule value below against the [Topstep help center](https://help.topstep.com/en/) before enabling Combine Order_Mode.** Until a row has a date in "Date checked", treat its value as unverified.

The Account_Simulator (`src/fse/sim/account.py`) reads these values from the `account` section of the Strategy_Config (`src/fse/config/schema/account.py`). The defaults are below. This file documents rule values only. It makes no claim about whether any strategy passes the Combine.

## Default values

| Rule | Config key | Default | Source | Date checked (YYYY-MM-DD) |
| --- | --- | --- | --- | --- |
| Starting balance | `account.starting_balance.value` | $50,000.00 | Third-party summary dated July 2026 | |
| Profit target | `account.profit_target` | enabled, $3,000.00 | Third-party summary dated July 2026 | |
| Maximum Loss Limit | `account.maximum_loss_limit` | enabled, $2,000.00 (first MLL_Floor $48,000.00; trails end-of-day balances; stops at the starting balance) | Third-party summary dated July 2026 | |
| Daily Loss Limit | `account.daily_loss_limit` | enabled, $1,000.00 | Third-party summary dated July 2026 | |
| Consistency target | `account.consistency_target` | enabled, 55% | Third-party summary dated July 2026 | |
| Position cap | `account.position_cap` | enabled, 50 Micro_Equivalents (MES or MNQ = 1, ES or NQ = 10) | Third-party summary dated July 2026 | |
| Flat_Deadline | `account.flat_deadline` | 16:10 New York (3:10 PM CT) | Third-party summary dated July 2026 | |
| Early-close offset | `account.early_close_offset_min` | 15 minutes before an early close | Third-party summary dated July 2026 | |
| Trading day start | fixed in `fse.timekit` | 18:00 New York (5:00 PM CT) on the day before the session | Third-party summary dated July 2026 | |

The Operator fills in "Date checked" after comparing a row with the help center, and updates "Source" to the help-center article used. When a value differs, change it in the Strategy_Config (and here), not in code.

**Reminder: the Operator verifies every value in this table against the Topstep help center before enabling Combine Order_Mode.**

## Operator checks

A web search during implementation found the help-center articles below. Their pages render with JavaScript, so only search-result excerpts could be read, not the full articles. These notes are pointers, not a verification, and no date is recorded for them. Content was rephrased for compliance with licensing restrictions.

Values that need attention first:

1. **Daily Loss Limit may not apply.** [Daily Loss Limit in the Trading Combine and Express Funded Account](https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account) describes the DLL as an option chosen at checkout. [What is the Responsible Trading Program?](https://help.topstep.com/en/articles/13620045-what-is-the-responsible-trading-program) lists −$1,000 for 50K accounts. Check whether the Combine account has a DLL, then set `daily_loss_limit.enabled` to match.
2. **Consistency target boundary.** [Trading Combine® Parameters](https://help.topstep.com/en/articles/8284197-trading-combine-parameters) says the best day should stay below 55% of the profit target to avoid raising the target. The simulator follows Req 15.13, where a best day of exactly $1,650.00 (55% of $3,000.00) leaves the target at $3,000.00. [Consistency at Topstep](https://help.topstep.com/en/articles/8284208-consistency-at-topstep) gives an example that divides the best day by total profit. Check how Topstep treats exactly 55%, and how it computes the raised target.
3. **Maximum Loss Limit in the Combine.** The excerpt of [What is the Maximum Loss Limit?](https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit) covered the Express Funded Account, not the Combine. The $2,000 distance, the $48,000.00 first floor and end-of-day trailing were seen only on third-party pages. Check all three for the Combine, and whether a breach is measured in real time on unrealized P&L.
4. **Profit target and starting balance.** $3,000.00 and $50,000.00 were seen only on third-party pages. Check both in the Trading Combine Parameters article.
5. **Position cap.** [Dynamic Live Risk Expansion](https://help.topstep.com/en/articles/11748475-dynamic-live-risk-expansion) mentions 5 lots for $50K accounts, in a funded-account context. The 10-to-1 micro ratio was seen only on a third-party page. Check the Combine's limit and how micros count.

Values the excerpts matched:

- Flat by 3:10 PM CT on weekdays, trading resumes at 5:00 PM CT: [When and What Products Can I Trade?](https://help.topstep.com/en/articles/8284206-when-and-what-products-can-i-trade). This matches the 16:10 Flat_Deadline and the 18:00 trading-day start in New York time.
- Positions closed 15 minutes before an early close: [Topstep Holiday Trading Hours](https://help.topstep.com/en/articles/13350348-topstep-holiday-trading-hours).
- The DLL trading day runs 5:00 PM CT to 3:10 PM CT: the Daily Loss Limit article above.

These matches still need the Operator's own check and date.

## How the simulator applies the rules

These are modelling choices from Requirement 15, not Topstep values:

- The MLL and DLL are checked on every bar at each position's worst price: the low for a long, the high for a short.
- A liquidation sets the balance to the MLL_Floor (or the day's P&L to minus the DLL). When the bar's open is already past that level, the open-price value is used instead.
- At the Flat_Deadline, open positions close at the open of the first bar at or after the deadline, with commission and exchange fees.
- After a pass or a fail, a new Combine_Attempt starts on the next trading day, so every session is still measured.
