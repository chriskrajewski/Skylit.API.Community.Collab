# pckt-bench rules (v1)

The scorer (`score.py`) implements exactly this. If you change a rule, you are no longer running pckt-bench v1: say so when you publish a number.

## Window (one independent run per signal)
- `open_t = max(signal.start - 20 min, 09:30 ET)`. The agent starts flat here.
- The agent may open trades from `open_t` through `signal.end` (inclusive, minute resolution) and close them whenever it likes.
- `force_t = min(signal.end + 15 min, 16:00 ET)`. Anything still open is closed here (`forced_close` in the metrics).
- `signal.start/end` = earliest leg `t0` / latest leg `t1`. A signal can have 1-3 legs (SPX, SPY, QQQ); the legs are the arrows.

## What the agent may see (no leakage; honor system)
The scorer only checks the output file (valid trades, inside the window). It cannot see what your agent saw, so everything in this section is a rule you follow and state, not something enforced. The orchestrator agent helps you meet it.
- Only data stamped **before** the decision minute `as_of`: complete 1-minute bars (start <= as_of - 60 s), Skylit snapshots at/before `as_of`
  (`fetch_skylit.AsOfClient` is an optional helper that enforces this), levels computed from earlier bars, prior days.
- Never: `signals.json`, the signal direction, its end time, the arrow prices, any bar or snapshot after `as_of`, or the outcome of any later minute.
- **Fresh context per signal:** every signal is its own run in a brand-new session (LLM agents: new conversation, no memory, no notes, no summaries, no carried positions or trades). Signal 2 is typically a different day from signal 1; carrying anything over scores memory, not reading the tape.
- **It should believe it is live:** do not tell the agent it is a benchmark, backtest or replay, and keep benchmark vocabulary and repo paths out of everything it can see. Orders go to a simulated broker that behaves like a live one; the agent must never touch a real brokerage account.
- Do not tune prompts on this slice and then report its score as out-of-sample.
- How often the agent wakes is your choice (every minute, every N minutes, or when your own trigger fires; one minute is the smallest step). Fix the rule before you run, use it for every signal, and state it when you publish a result.

## Orders and fills
- Tradable: **SPY and QQQ**, long or short. SPX is information only (one SPX 0DTE contract usually exceeds the $500 cap).
- A decision at minute `T` fills at the **open of the bar starting at `T`** (never the same bar the agent just saw).
- Unlimited trades per signal. Overlapping positions are allowed up to `max_concurrent_exposure` (default $1,500 of entry cost in options mode, $30,000 in equity mode, i.e. 3 trades at the per-trade cap); a trade that would exceed it is rejected and counted.
- **Max per trade: $500 of premium in options mode, $10,000 of notional in equity mode.**

## Grading modes
**Options (headline):** simulated 0DTE, same-day expiry. Long = buy a call, short = buy a put. The grader picks the strike on a $1 grid whose |delta| is closest to 0.30.
- Black-Scholes, r = q = 0, time in calendar years to 16:00 ET, `IV = max(0.10, VIX/100 * k)` with the VIX value of the fill bar. Default `k = 1.3`.
- Entry at ask, exit at bid, `half_spread = max($0.03, 3% of mid)`. Contracts = floor($500 / (ask * 100)); fewer than 1 = rejected as `unaffordable`.
- Commission is 0 by default (`--commission` per contract per side).
- Needs VIX. `fetch_vix.py` pulls 1-minute VIX from Skylit Atlas (needs your API key, 1 credit per day; it shows the cost first), or bring VIX from any source. If you only have one VIX value per day, pass `--vix-daily` (the day's opening VIX is used for every trade; on slice 001 this changed the random baseline by only about $0.3k). If VIX is missing entirely the scorer refuses options mode instead of guessing.

**Why k = 1.3, not 1.0:** VIX is a 30-day implied vol, not 0DTE vol, and these windows were picked in hindsight because the market moved. With `k = 1.0` the model prices options too cheaply for those moves, so even random-direction trades made money
(mean +$5.9k over 12 seeds on slice 001, sd $6.7k), which would reward buying convexity instead of reading the tape. `k = 1.3` makes the random baseline roughly neutral on slice 001 (+$0.2k mean, sd $4.6k). Run `baselines.py` on any slice and compare. The model is a known simplification: absolute dollars are not real fills; compare agents to each other and to the baselines.

**Equity (cross-check, model-free):** fractional shares of SPY/QQQ, $10,000 notional per trade, 1 cent slippage per share each way, no commission. Dollars are still modest (a 0.3% move on $10,000 is $30), so read `return_on_deployed`, recall and entry quality alongside PnL.

## Metrics (all reported, none alone)
- **PnL**: total, per mode, win rate, average win/loss, profit factor, return on deployed capital, max drawdown, forced-close rate, hold time, rejected orders by reason.
- **Recall**: share of signals where the agent opened at least one trade in the signal's direction with `open_t <= open <= end`.
- **Bias**: long share, share aligned with the signal direction, wrong-way trades, trades opened before `signal.start`.
- **Entry quality** (trades aligned with the signal): median distance from the arrow's start price (bps, positive = worse), median gap to the best price available in the window (bps), median lead (minutes before `signal.start`, negative = early).
- **Exit quality**: median capture ratio (agent's move / arrow move), median MFE and MAE (bps).
- **Overtrading**: trades per signal, signals with no trade.

## Caveats you must keep when you publish a number
1. Signals were drawn in hindsight, so the trade tends to have worked. Always publish the four baselines next to your score.
2. 81 signals is a small sample; with slice 001 the random baseline has sd of about $4.6k in options mode. Differences smaller than that are noise.
3. Option PnL is simulated. Report the `k`, delta and spread you used.
4. Arrow prices were snapped to IBKR 1-minute bars; Skylit Atlas bars differ by a few cents (up to about 36 cents on SPY/QQQ in a spot check), which does not change the scoring.
