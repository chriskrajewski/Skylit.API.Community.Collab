# How to run pckt-bench

Python 3.10+, standard library only. Exact rules and pricing model: [rules.md](rules.md).

**The idea.** A slice (`../slices/<id>/signals.json`) lists moments when we believe a long/short idea existed. For each one you replay your agent through the market *walking forward in time*, starting 20 minutes before the idea begins, record every trade decision it makes, close whatever is still open 15 minutes after the idea ends, and hand the resulting `trades.json` to `score.py`. You choose the agent, its data and when it wakes up. Skylit is not required (see "Data").

## Quick start: just ask your AI
You do not install anything, copy any file, or run any setup command. Tell the AI you already use (Claude Code, Codex, Cursor, a custom agent, anything that can read files, run shell commands and, ideally, spawn a helper agent):

> **Run pckt-bench on my agent.** It is in `members/pckt/pckt-bench` of the Skylit community repo.

The AI should then spawn a helper agent with this one-line prompt (or follow the file itself if it cannot spawn helpers):

> Read `members/pckt/pckt-bench/agents/pckt-bench-orchestrator.md` and follow it exactly.

(`AGENTS.md` and `CLAUDE.md` in the `pckt-bench` folder say the same thing, so most AIs find it on their own.) The orchestrator is an interview, not a script. It asks you, one batch at a time: which slice(s) to run on, what your agent is and what it looks at, how often it wakes, options or equity, where your 1-minute bars come from, and your budget. It explains the leakage rules, helps you build the walk-forward replay in *your* environment, proves with a canary test that nothing leaks, asks before spending any Skylit credits, then validates, scores and reports with baselines.

Only two things are required: **a `trades.json` the scorer accepts** (below) and **a grading mode, options or equity**. Everything else is advice; the orchestrator tells you what each choice risks. The sections below are reference material for the AI and for people who want to read the rules.

## If your agent is built to run live: what adapting it means
Most trading agents are wired to live feeds (real-time quotes, a live charting tab, a real or paper broker, the machine clock, files or memory that persist from day to day). pckt-bench replays the past, so that wiring has to be swapped for a replay version. You do **not** port the agent into this repo and you do not rewrite its logic. You build a thin adapter around it, in **your own project**:

| Live wiring | Replay replacement (you build it, the orchestrator helps) |
|---|---|
| Machine clock | A clock your harness sets to the replay minute (`as_of`), stepped forward by your wake-up rule |
| Real-time quotes / bars / heatmaps | The same sources queried **as of** that minute: point-in-time files or API calls clamped to `as_of`, nothing later |
| Live chart tab or screenshots | Charts you render yourself from the as-of data (a live web tab cannot be rewound unless it has a true replay mode) |
| Broker or paper account | A simulated broker your harness controls: acknowledges orders, fills them at the next bar's open, reports positions. **Never a real account.** |
| Memory, notes, journals, state files, auto-summaries | Reset or isolated for every signal: each signal is a fresh session on a different day, and the agent should believe it is live |

Where it lives: run the agent and the adapter **from your own project folder, not from inside `pckt-bench`**. The harness may read `slices/<id>/signals.json` to know the windows; the agent must not be able to reach that file. Expect this to be the main effort of using the benchmark; a dry run on 2-3 signals (with the canary checks the orchestrator runs) is how you know the adapter is clean. Agents that are already stateless functions of "current data in, orders out" need almost no adapting.

## The workflow (per signal; repeat for every signal in the slice)

```
 t-20 min                signal start                    signal end             end + 15 min
    |-------- walk forward, minute by minute ---------------|-------- no new trades ----|
    ^ 1. build context   ^ (agent is not told)             ^ (agent is not told)       ^ 4. grader force-closes
      as of this minute    2./3. wake, decide, LOG trades, hold or close any time          anything still open
```

1. **Start at `open_t` (signal start − 20 min, never before 09:30 ET) and build your agent's context as of that minute.** Pull everything it should look at: daily/hourly levels, heatmaps, price bars, your own indicators. Only data stamped *before* this minute exists.
2. **Walk forward in time.** Step the clock; at each step your agent sees the new data and may act. You decide the granularity: wake it every minute, every N minutes, or only when your own criterion fires (see "When does the agent wake?").
3. **Log each decision.** A trade is `symbol` (SPY or QQQ), `side` (long or short), the minute it was opened, and the minute it was closed (or `null`). You can open several, close early, or do nothing.
4. **Trading stops at the signal end.** Anything still open is closed by the grader at *signal end + 15 min* (or 16:00 ET). Mark those with `close_time: null`. Do not close them yourself unless your agent wanted to.
5. **After all signals, write one `trades.json`**, validate it, and score it. Also score the baselines (random, always long/short, oracle) so your number has context.

## Expected output: `trades.json`

One file for the whole slice, **one entry in `runs` per signal** (use an empty `trades` list when the agent did nothing).

```json
{
  "slice": "001_2026-07-07_to_2026-08-11",
  "runs": [
    {
      "signal_id": "1044e4df-9585-4663-9a3c-8f17b7e2c0a3",
      "trades": [
        {"symbol": "SPY", "side": "long", "open_time": 1783435320, "close_time": 1783436520, "reason": "optional note"},
        {"symbol": "QQQ", "side": "short", "open_time": 1783435800, "close_time": null}
      ],
      "decisions": [
        {"time": 1783434120, "time_et": "2026-07-07 10:22 ET", "ops": [{"op": "hold"}], "reason": "optional"}
      ]
    }
  ]
}
```

| Field | Type | Meaning |
|---|---|---|
| `runs[].signal_id` | string | The `id` of a signal in `signals.json`. Each id at most once. A signal with no run scores as "no trade" (a recall miss). |
| `trades[].symbol` | `"SPY"` or `"QQQ"` | What the agent traded. SPX is information only. |
| `trades[].side` | `"long"` or `"short"` | Long = buy shares / a call. Short = short shares / buy a put. |
| `trades[].open_time` | integer, UTC epoch **seconds** | The minute the agent *decided* to open. It fills at the **open of the bar that starts at this minute**. Example: `1783435320` = 2026-07-07 10:42:00 ET. Must be `>= signal start − 20 min` (clipped to 09:30 ET) and `<= signal end`, or the trade is rejected. |
| `trades[].close_time` | integer or `null` | The minute the agent decided to close (same fill rule), or `null` to let the grader close at the forced close. Must be after `open_time`. Later than the forced close is treated as `null`. |
| `trades[].reason` | string, optional | Free text for your own audit. Ignored by scoring. |
| `decisions` | array, optional | Your decision log (every wake-up, including holds). Ignored by scoring; keep it so a result can be audited. |

Times are always **UTC epoch seconds**, aligned to the minute. `signals.json` also shows `start_et` / `end_et` so you can see the ET clock time of each signal.
Anything outside these rules is rejected and *counted* by the scorer (`rejected_structural`), never silently fixed. Check before scoring:

```bash
python validate_trades.py --signals ../slices/<slice>/signals.json --trades results/trades.json
```

## When does the agent wake? (you decide, within these rules)
- The smallest step is **one minute** (fills happen at 1-minute bar opens). Per-second inference is not supported.
- Wake it every minute, every N minutes (`--cadence`), or only when *your* criterion fires (price touched a level, volume spike, a node got hit). Between wake-ups the decision is "hold".
- The wake rule must be **fixed before you run, identical for every signal, and based only on data before the current minute**. Never tune it on the slice and then report the slice score as out-of-sample.
- Cost scales with wake-ups: slice 001 has about 1,760 decision points at a 3-minute cadence and about 5,200 at one minute (each costs one LLM call plus any data you fetch).

## Data (the benchmark ships none; Skylit is not required)
The scorer needs two things:
- **1-minute price bars for SPY and QQQ** from any vendor (SPX is not needed). Export CSVs with a timestamp and open/high/low/close and run
  `python fetch_bars.py --signals <signals.json> --csv SPX=spx.csv SPY=spy.csv QQQ=qqq.csv --out bars/ [--tz America/New_York] [--bar-label end]`
  (free; writes `bars/bars_YYYY-MM-DD.json`). Optional shortcut with a Skylit key: `fetch_bars.py --atlas --yes` (Atlas, 1 credit per call, 88 credits for slice 001).
- **VIX, only for options-mode scoring** (the headline). This is the one place the benchmark asks for a Skylit key:
  1. `cp ../.env.example ../.env` and set `SKYLIT_API_KEY` in `pckt-bench/.env` (git-ignored; never commit a key).
  2. `python fetch_vix.py --signals <signals.json> --out bars/` shows the credit cost and stops.
  3. Add `--yes` to spend it. **Cost: 1 credit ($0.001) per day of VIX, so 22 credits (about $0.02) for slice 001**; cached days are free.

  No key? Supply VIX from any source (`fetch_bars.py --csv VIX=vix.csv`), or one value per day and score with `--vix-daily`. Equity mode (`--modes equity`) needs no VIX and no key.
- **Whatever your agent looks at** (levels, heatmaps, indicators, or only OHLC, or neither) is yours to choose and fetch. Skylit can supply some of it, always per signal, always showing the cost first and stopping until `--yes`:
  - `fetch_skylit.py balance` (free) and `fetch_skylit.py estimate --signals <signals.json> --signal 3 --cadence 3 --metrics gamma,vanna` (credits per signal and in total).
  - Heatmaps: `fetch_skylit.py pull --signals <signals.json> --signal 3 --cadence 3 --metrics gamma --out heatmaps/` writes `heatmaps/<signal_id>/<epoch>_<metric>.json` (latest snapshot at or before that epoch). **Credit warning:** 5 credits ($0.005) per snapshot per metric. Slice 001 is about 8,800 credits (~$9) for gamma at a 3-minute cadence and about 52,000 (~$52) for gamma + vanna every minute.
  - Daily or hourly bars before the open, for support/resistance levels: `fetch_bars.py --signal 3 --context D` (or `60`), 3 credits per day.
  - `fetch_bars.py`, `fetch_vix.py` also take `--signal N` to fetch one signal's day at a time.

## Leakage rules (the part that makes a score meaningful)
- **Fresh context for every signal.** Signal 2 is on a different day from signal 1. An LLM agent must start each signal in a brand-new session with no earlier messages, trades, notes, memory or summaries (switch off persistent memory, auto-compaction summaries, session resume and shared scratch files). Only fixed instructions and tools carry across signals.
- **It should believe everything is live.** Do not tell the agent it is in a benchmark, backtest or replay: present a live (paper or sandboxed) session whose clock is `as_of`, with a simulated broker that acknowledges and fills orders like a real one, and keep words like benchmark, replay, signal, slice and file paths of this repo out of anything it can see. The agent must never be connected to a real brokerage.
- Nothing at or after the current minute reaches the agent: bars cut at `as_of − 60 s`, Skylit calls made with `at = as_of` (`fetch_skylit.py` has an optional clamp that refuses later requests), levels computed from earlier data, no later web results.
- The agent never sees `signals.json`, a direction, arrow prices or the window end. (It does know the clock, so it knows the window opened; that is part of the design.)
- Same settings for every signal. Check the model's training cutoff against the slice dates.

## Score it
```bash
python validate_trades.py --signals <signals.json> --trades results/trades.json
python score.py --signals <signals.json> --trades results/trades.json --bars-dir bars/ --out results/agent/
python baselines.py --signals <signals.json> --bars-dir bars/ --out results/baselines/
for p in oracle always_long always_short random; do python score.py --signals <signals.json> --trades results/baselines/$p.trades.json --bars-dir bars/ --out results/$p/; done
```
`report.md` and `metrics.json` land in each `--out`. Publish your score with the four baselines, your cadence/wake rule, model, `k`, and the slice id.

## Tests
`python -m unittest discover -s tests`
