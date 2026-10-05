# Skylit Futures Strategy Engine

- Discord username: alantiix
- Project identifier: skylit-futures-strategy-engine
- Kind: agent

Status: every command is built and tested against mocked services. No backtest result on real Skylit history is published here, and no Practice or Combine order has been placed.

## What it does

A deterministic, parameterized strategy engine for the Skylit Heatseeker playbook in [skylit-academy-playbook-skill](../skylit-academy-playbook-skill/README.md). It reads ES and NQ levels from Heatseeker GEX and VEX maps and Flowseeker dark-pool prints, and plans MES and MNQ trades during RTH. Code makes every decision, so the same inputs always give the same result.

One engine code path runs in two places:

- Backtester: pulls Skylit history into a local cache once, then replays it with no look-ahead. It measures each rule (gate-rejection funnel, shadow trades, ablation), maps win rate against reward:risk, and estimates the Topstep 50K Trading Combine pass probability.
- Live_Runner: runs the same engine on live Skylit data. It trades on an in-process paper broker by default. Practice and Combine accounts on ProjectX (TopstepX) need an explicit opt-in.

Every playbook rule is a toggleable Pattern, Gate, Exit_Mode or Kill_Switch in a versioned YAML Strategy_Config. The project reports measured results and makes no win-rate or profit claim.

## Skylit surface

- Access: REST
- Products: Heatseeker, Flowseeker, Atlas

| Product | Endpoints | Used for |
| --- | --- | --- |
| Heatseeker | `/v1/historical/range`, `/v1/historical`, `/v1/heatmap`, `/v1/stream`, `/v1/gex/levels` | GEX and VEX history and live maps. `/v1/gex/levels` is logged for comparison only |
| Flowseeker | `/v1/dark-pool/trades` | Dark-pool prints, fetched only when the `dark_pool_confluence` gate is enabled |
| Atlas | `/v1/history`, `/v1/config`, `/v1/search` | Futures and VIX OHLCV bars |

The client also calls `/v1/account` for the key's rate limits and `/v1/symbols` to check configured symbols.

Docs: [REST API](https://docs.skylit.ai/api-reference/introduction).

## Setup

Needs Python 3.14 and git. Run these from this folder.

1. Create and activate a virtual environment:

   ```sh
   python3.14 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the pinned, hashed dependencies, then the `fse` command:

   ```sh
   python -m pip install --require-hashes -r requirements.lock
   python -m pip install --no-deps -e .
   ```

3. Copy the example environment file. `.env` is gitignored; never commit it. Every `fse` command reads `.env` from this Project folder (the folder holding `pyproject.toml`), whichever directory you run it from; a `.env` in the current directory is never read.

   ```sh
   cp .env.example .env
   ```

4. Set `SKYLIT_API_KEY` in `.env`. Create the key in the Developer tab of the Skylit app. A non-blank shell variable with the same name takes precedence over the `.env` value.
5. Set the other variables only for the commands and Order_Modes that need them (see the table below).
6. Before each commit, run `fse scan-secrets`. It prints the path of every tracked file that contains a Secret_Variable value and exits non-zero on any match.

The Data_Cache and every run output default to `~/.skylit-fse/` (`cache/`, `runs/`, `recordings/`, `live-state/`, `logs/`), outside the repository. A configured path inside the repository that git does not ignore is refused.

## How to run

Run these from this folder with the virtual environment active. Dates are New York session dates, both inclusive. Every command prints its options with `fse <command> --help`.

1. Pull Skylit history, MES and MNQ bars, VIX daily values and dark-pool prints into the Data_Cache:

   ```sh
   fse pull --start 2025-10-06 --end 2026-10-02 --instruments MES,MNQ --dark-pool --label-sample-minutes 15
   ```

   The estimate (requests, credits, disk) is printed before the first request. A 364-day pull at full resolution takes hours. Rerun the same command to resume. MES and MNQ bars need `projectx_contract_id` set for each contract in `calendars/roll_calendar.yaml`. `--label-sample-minutes` stores Skylit `nodeType` labels, which the report's King and Gatekeeper agreement needs.

2. Backtest the Playbook_Baseline on the cached sessions, with no network request:

   ```sh
   fse backtest --config configs/playbook_baseline.yaml --start 2025-10-06 --end 2026-10-02 --offline --seed 1
   ```

   The shipped config exits 2 until the Operator sets the commission and exchange fee per instrument and `regime.min_abs_value`. The command prints the run id.

3. Write the report of that run:

   ```sh
   fse report --run <run id>
   ```

4. Run today's session on live data in Paper Order_Mode. No order is sent to ProjectX:

   ```sh
   fse paper --config configs/playbook_baseline.yaml
   ```

   The shipped config exits 2 until the Operator sets the commission and exchange fee per instrument and `regime.min_abs_value`.

5. Before each commit, check that no tracked file holds a secret value:

   ```sh
   fse scan-secrets
   ```

Other commands: `fse experiment` (ablation, sweep, pass estimate, holdout, walk-forward, cadence), `fse drafts`, `fse skilldocs`, `fse calibrate`, `fse import-vix`, `fse halt`, `fse clear` and `fse live`.

## Environment variables

Every variable in `.env.example` is listed below. Values come only from the shell environment or `.env`; no command takes a credential or account id as an argument.

Order_Modes: Paper (in-process paper broker, the default), Practice (the ProjectX practice account) and Combine (the 50K Combine account). `fse paper` always runs Paper. `fse live` uses the config's `order_mode`, and falls back to Paper when a mode's conditions are not met. Combine also needs the `--live-orders` flag.

| Variable | Purpose | Secret_Variable | Needed by |
| --- | --- | --- | --- |
| `SKYLIT_API_KEY` | Skylit API key for Heatseeker, Flowseeker and Atlas requests | Yes | `fse pull`, `fse paper`, and `fse live` in every Order_Mode. Not needed by `fse backtest --offline` or `fse report` |
| `PROJECTX_USERNAME` | ProjectX (TopstepX) user name, sent with `PROJECTX_API_KEY` to get a session token | Yes | `fse paper` and `fse live` in every Order_Mode (live 1-minute bars; Practice and Combine also place orders). `fse pull` when it uses ProjectX bars: Atlas does not serve an instrument, or `--bar-interval 5` |
| `PROJECTX_API_KEY` | ProjectX API key | Yes | Same as `PROJECTX_USERNAME` |
| `PRACTICE_ACCOUNT_ID` | ProjectX practice account id | Only when listed in `FSE_SECRET_VARS` | `fse live` in Practice Order_Mode. Must differ from `COMBINE_ACCOUNT_ID` |
| `COMBINE_ACCOUNT_ID` | Topstep 50K Trading Combine account id | Only when listed in `FSE_SECRET_VARS` | `fse live --live-orders` in Combine Order_Mode, where it must equal the account ProjectX resolves. Practice Order_Mode reads it to confirm the practice id differs |
| `NOTIFIER_WEBHOOK_URL` | Webhook URL that receives Finding_Cards | Yes | `fse paper` and `fse live` in any Order_Mode, only when the webhook Notifier sink is configured. Console and file sinks need nothing |
| `NARRATOR_API_KEY` | API key for the optional LLM Narrator that rewrites Finding_Cards as prose | Yes | `fse paper` and `fse live` in any Order_Mode, only when the Narrator is enabled (default off) |
| `FSE_SECRET_VARS` | Comma-separated names of extra variables to treat as Secret_Variables, for example `PRACTICE_ACCOUNT_ID,COMBINE_ACCOUNT_ID` | No (it holds names, not values) | Optional. Read by every command for redaction, and by `fse scan-secrets` |

- Every command replaces each non-blank Secret_Variable value with `[REDACTED]` in logs, reports, manifests, recordings, Finding_Cards and error output.
- `fse scan-secrets` searches tracked files for every non-blank Secret_Variable value. It exits with an error when all of them are blank.
- Short values such as account ids match unrelated text more often, so a scanner hit on one may be a false positive.

## Account rules

The Account_Simulator applies the Topstep 50K Trading Combine rules listed in [docs/account-rules.md](docs/account-rules.md), with each value's source and the date it was checked. **The Operator verifies every account rule value against the [Topstep help center](https://help.topstep.com/en/) before enabling Combine Order_Mode.**

## Practice and Combine Order_Modes

`fse paper` never sends an order to ProjectX. Before `fse live` in Practice or Combine Order_Mode:

- **TopstepX API terms.** The Operator confirms the current TopstepX API terms, including the device and VPN rules, before enabling Combine Order_Mode.
- **Auto OCO Brackets (OQ7).** Practice and Combine need the ProjectX account in Auto OCO Brackets mode. In Position Brackets mode every bracket is rejected with `errorCode 2`; the engine then closes the position and blocks new entries until `fse clear`.
- **Practice checklist.** Work through the design's [Practice checklist before Combine Order_Mode](../../../.kiro/specs/skylit-futures-strategy-engine/design.md#practice-checklist-before-combine-order_mode) before any Combine use.
