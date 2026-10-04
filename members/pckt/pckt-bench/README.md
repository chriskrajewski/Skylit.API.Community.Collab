# pckt-bench

- Discord username: pckt
- Project identifier: pckt-bench
- Kind: agent

A small benchmark for Skylit trading agents. Each slice is a JSON file of human-reviewed "arrows": per symbol (SPX, SPY, QQQ) a start time and price and an end time and price for a trade idea based on Heatseeker nodes. An agent is replayed from 20 minutes before each arrow starts until it ends, may place as many trades as it likes, and is force-closed 15 minutes after the arrow ends. Scoring is PnL plus recall (how many signals it traded), bias, entry quality and exit quality.

**The benchmark ships no market data and does not require Skylit.** The signals only mark when we believe there was a long/short idea; you bring your own agent, data and 1-minute bars from any vendor. A Skylit API key is needed in one place: VIX for options-mode scoring (`run-eval/fetch_vix.py`, about 22 credits for slice 001, cost shown before anything is spent).

## Skylit surface

- Access: REST (optional)
- Products: Atlas (VIX for scoring); Heatseeker optional for your agent

Docs: [REST API](https://docs.skylit.ai/api-reference/introduction) and the Atlas bars API (`https://atlas-api.skylit.ai/v1/history`).

## Layout

```text
slices/001_2026-07-07_to_2026-08-11/   signals.json  manifest.json
agents/pckt-bench-orchestrator.md     helper agent: guides you through running the backtest with YOUR agent
run-eval/                              START HERE: README.md (output format + workflow), rules.md, score.py, validate_trades.py,
                                       baselines.py, options_model.py, data pullers (fetch_bars.py, fetch_vix.py, fetch_skylit.py), tests
```

Slices are frozen folders so results stay comparable. New batches arrive as `slices/002_.../` and so on; run any slice on its own.

| Slice | Days | Signals | Long / short | Source of the heatmap data |
|---|---|---|---|---|
| 001 | 2026-07-07 to 2026-08-11 (22 days) | 81 | 42 / 39 | Skylit API |

## Quick start

Tell your AI: **"Run pckt-bench on my agent."** Nothing to install or set up. Your AI spawns the [orchestrator agent](agents/pckt-bench-orchestrator.md) (prompt: *Read `members/pckt/pckt-bench/agents/pckt-bench-orchestrator.md` and follow it exactly*), which asks you which slice(s) to run on, what data your agent needs, how often it wakes, options or equity, then guides you to a leakage-free walk-forward replay and gets the output scored. The repo ships no runner on purpose. Output format and rules: [run-eval/README.md](run-eval/README.md), [run-eval/rules.md](run-eval/rules.md).

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

## How the signals were made

Drafted by an AI annotator (Claude) from Heatseeker node reactions, then every idea was reviewed by pckt; only approved ideas are in the slices. Signals are hindsight-selected, so a trade in the arrow's direction tends to have worked: always compare an agent with the included baselines (oracle, always long, always short, random).

## How to run

Ask your AI to run pckt-bench (see Quick start); the orchestrator agent walks you through every step by asking questions. Python 3.10+ is the only requirement (the scripts use the standard library). The one credential, `SKYLIT_API_KEY` (Developer tab of the Skylit app), is only needed to pull VIX for options-mode scoring; the orchestrator tells you the credit cost first and asks before spending anything. Equity mode needs no key.

## Environment variables

- `SKYLIT_API_KEY` — Skylit API key, only for `run-eval/fetch_vix.py` (options-mode VIX) and the optional Atlas/heatmap fetchers. Set it in `.env` on your machine. Leave it out of git.

## License

MIT, see [LICENSE](LICENSE).
