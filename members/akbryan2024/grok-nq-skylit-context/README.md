# grok-nq-skylit-context

- Discord username: akbryan2024
- Project identifier: grok-nq-skylit-context
- Kind: agent

## What it does

An approach note, not a strategy. An LLM agent trades NQ futures; there is no NQ board, so before each
decision it pulls the QQQ Heatseeker board and 0DTE strike flow and converts every QQQ level to NQ
points with the live NQ/QQQ price ratio (median of recent pulls, jump-guarded). The agent gets the
levels as context — where price tends to react — not as a direction signal.

## How the build fits together

A sketch of the architecture, so you can borrow the shape for your own agent:

- **Decision service + NinjaTrader 8.** A Python (FastAPI) service runs the decision loop, the risk checks
  and a small web dashboard. It sends orders by webhook to a NinjaTrader 8 strategy, which places the
  bracket at the broker and posts fills back.
- **Market data.** We started on Databento (live futures bars) and later moved to bars pushed from the
  NinjaTrader chart itself, with yfinance as a cold-start fallback. Whatever the source, the agent only
  sees CLOSED 5-minute bars; partial bars produced bad volume and indicator readings.
- **The LLM call.** Every few minutes the loop builds one context: price/indicator summary, a time-series
  forecast model's read, the Skylit block (first, so it isn't buried), and the open position. The model
  returns JSON (action, confidence, stop, target, reason). Hard-coded gates and a risk engine sit AFTER
  the model and can veto it; the model never places orders itself.
- **Skylit pulls on demand.** A refresh happens only right before an LLM call, at most every couple of
  minutes, regular hours only, with a daily credit cap. Heatmap (1 credit) + strike flow (3) per pull.
- **Everything is logged** to a persistent event file: every decision, every gate that blocked it, the
  Skylit read at that moment and the model's full reasoning. Nightly scripts score blocked vs taken
  decisions per gate and score the levels themselves. Most of our improvements came from those logs.

Lessons from running it:
- Pull the board right before each decision instead of on a timer; it keeps credits low and the map fresh.
- Give the model levels, not labels. A regime/day-type label that disagrees with price action
  (e.g. "range" on a strong trend day) misleads it; we removed ours.
- Show the largest POSITIVE node separately from the largest absolute node; when the biggest node is
  negative gamma, say so explicitly.
- Ask the model to state the floor/ceiling and its room in its reasoning; log the full reasoning so
  you can check it actually uses the map.
- Score the levels against a random-level control before trusting a hold rate.

## Skylit surface

- Access: REST
- Products: Heatseeker, Flowseeker

## How to run

Not a runnable package; this is a write-up of the approach.

## Environment variables

- `SKYLIT_API_KEY` — never committed.
