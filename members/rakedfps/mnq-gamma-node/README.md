# MNQ gamma-node

- Discord username: rakedfps
- Project identifier: mnq-gamma-node
- Kind: agent

Kind is `agent`. The folder path for this project is `members/rakedfps/mnq-gamma-node/`.

## What it does

Raked's MNQ gamma-node strategy, written so an agent can grade the setup and so a person can read the research it came from.

The trade is Entry B. Price taps a strong Heatseeker node (floor, ceiling, or king on QQQ or NDX 0DTE), sweeps through it, and a 3-minute candle closes back on the original side within 15 minutes. The entry is that close. The stop sits beyond the sweep. Entry A, a limit at the node, is off: in the Jul 13–Sep 30 2026 study the node was often right and the limit was swept before the move.

Four map-reading rules sit on top of B: do not trade away from the king, stand aside on a fast approach, stop trading a node after two failures the same day, and size down when QQQ net gamma is negative. Together those rules were 53 trades, 68% wins, about +2.7R, max drawdown $267, on the same window. September helped choose the rules, so they still need a blind test on new data. [SKILL.md](SKILL.md) is the playbook. [JOURNAL.md](JOURNAL.md) is the study, including the sample-size limits.

## Skylit surface

- Access: REST and MCP
- Products: Heatseeker

The study read gamma on QQQ, NDXP, SPY, and SPXW (0DTE) from `GET /v1/historical/range`. A live read uses the same Heatseeker boards, over REST or the MCP server. QQQ supplies the price path, scaled onto NQ. SPY and SPXW are confluence only.

Docs: [REST API](https://docs.skylit.ai/api-reference/introduction) and [MCP server](https://docs.skylit.ai/mcp/overview) (`https://mcp.skylit.ai/mcp`).

## How to run

1. Load [SKILL.md](SKILL.md) when the task is this strategy: grading an MNQ gamma-node setup, or deciding whether a tap is a B entry, a pass, or a half-size trade.
2. Read [JOURNAL.md](JOURNAL.md) when you need the evidence, the failed trade-management tests, or the open questions. [parameters.json](parameters.json) is the same rule set in one object.
3. Copy `.env.example` to `.env` only if you call the API yourself. Set `SKYLIT_API_KEY` there. Create the key in the Developer tab of the Skylit app.
4. The backtester, the response cache, and the spreadsheets are not in this share. Do not invent fills from a stale board. A live decision uses the current Heatseeker print.

## Environment variables

- `SKYLIT_API_KEY` — Skylit API key. Set it in `.env` on your machine. Leave it out of git. The playbook and the journal do not need it.
