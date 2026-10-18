# Skylit Futures Strategy Engine

- Project identifier: skylit-futures-strategy-engine
- Kind: agent

Status: every command is built and tested against mocked services. No backtest result on real Skylit history is published here, and no Practice or Combine order has been placed.

## What it does

A deterministic, parameterized strategy engine for the Skylit Heatseeker playbook. It reads ES and NQ levels from Heatseeker GEX and VEX maps and Flowseeker dark-pool prints, and plans MES and MNQ trades during RTH. Code makes every decision, so the same inputs always give the same result.

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
| Atlas | `/v1/history`, `/v1/config`, `/v1/search` | Futures and VIX OHLCV bars; dedicated QQQ/SPY 60-second RTH equity bars |

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

### Pull QQQ/SPY equity bars from Atlas

Use the dedicated command below for timestamped QQQ/SPY OHLCV research bars. Dates are inclusive New York exchange-session dates. Version 1 always requests exact `[09:30, session close)` RTH windows at 60-second resolution with `extended=false`; the exchange calendar supplies early-close boundaries.

Preflight a one-day QQQ smoke pull without credentials, network, credits, or files:

```sh
fse pull-equity-bars \
  --start 2026-03-05 --end 2026-03-05 --symbols QQQ --interval 60 \
  --cache-dir ~/.skylit-fse/cache --out ~/.skylit-fse/runs/equity-smoke --dry-run
```

After reviewing the estimate, a live multi-session QQQ+SPY pull is:

```sh
fse pull-equity-bars \
  --start 2025-10-06 --end 2026-10-02 --symbols QQQ,SPY --interval 60 \
  --cache-dir ~/.skylit-fse/cache --out ~/.skylit-fse/runs/equity-2025
```

Every successful Atlas `/v1/history` request costs one credit. The command prints the `/v1/account` and `/v1/config` free bootstrap requests, estimated history request count, expected RTH rows, and estimated credits before the first live request. Reruns skip only complete, readable, hash-verified sessions and retry incomplete or `no_data` sessions oldest-first. Coverage is written as `equity_coverage_<pull_id>.json` and `.md`; attempts go to `fetch_log.jsonl`. Bars live under `equity_bars/{QQQ|SPY}/60s/YYYY-MM-DD.parquet` with explicit symbol/session fields, and equity tick fields are intentionally `NULL`.

The `equity_bars` dataset is separate from futures `bars`; the main futures backtester does not consume these rows automatically. Do not substitute `fse pull --instruments QQQ,SPY` or `fse pull --symbols QQQ,SPY`: those existing flags mean futures roll-calendar instruments and Heatseeker symbols, respectively. Tests and the implementation verification made no live request and consumed no Atlas credits.

2. Backtest the Playbook_Baseline on the cached sessions, with no network request:

   ```sh
   fse backtest --config configs/playbook_baseline.yaml --start 2025-10-06 --end 2026-10-02 --offline --seed 1
   ```

   The shipped config exits 2 until the Operator sets the commission and exchange fee per instrument and `regime.min_abs_value`. The command prints the run id.

   Sessions listed under `mixed_sessions` in `calendars/roll_calendar.yaml` (their overnight bars mix the old and the new contract) are skipped for each configured instrument and listed in the Run_Manifest as `mixed_roll_session`; experiments and the Holdout_Period leave them out too. After each quarterly roll, add the new mixed session.

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

6. List the ProjectX accounts the stored credentials can see (read-only, no order is sent):

   ```sh
   fse accounts [--json]
   ```

   Each row shows the id, name, canTrade and role (which of `PRACTICE_ACCOUNT_ID` / `COMBINE_ACCOUNT_ID` it matches). `PRACTICE_ACCOUNT_ID` must be the numeric id, not the account name.

Other commands: `fse run` (see below), `fse experiment` (ablation, sweep, pass estimate, holdout, walk-forward, cadence), `fse drafts`, `fse skilldocs`, `fse calibrate`, `fse import-vix`, `fse export-aws` (see below), `fse halt`, `fse clear` and `fse live`.

## `fse run`: the plugin runner (paper only)

`fse run` is a long-running process: it does not stop at the Flat_Deadline or after one session, and runs across the daily 17:00-18:00 break, weekends and holidays until SIGINT/SIGTERM or `--until`:

```sh
fse run --config configs/runner.yaml
fse run --config configs/runner.yaml --until 2026-10-12T18:10:00-04:00   # a shadow run that stops itself
```

It has three layers:

- **Data plane** (always on, whatever the plugins need): ProjectX MNQ/MES 1-minute bars over REST (triggered by the ProjectX market hub's trades when the hub connects, else 5 s polling), the hub's quotes, and the Skylit stream (gamma and vanna) plus optional dark-pool prints. Everything is recorded per Globex day under `<recordings_root>/recordings/dataplane/`, and closed bars go to the bar store (`~/.skylit-fse/live-bars`, `<INSTR>/60s/<day>.parquet`), which a plugin's own tooling can read (for example through an `--extra-bars` option). Today's file survives restarts: a restart reads it back and extends it, never truncates it. The bar store has no volume column, so bars read back through `services.bar_store` have `v = 0.0`.
- **Plugin host**: loads the plugins named in the runner YAML and dispatches the data to each one. Each host-brokered plugin trades its own paper book.
- **Plugins**: `template` (`fse.live.plugins.template:TemplatePlugin`, a runnable example: one paper entry a day) and `playbook` (`fse.live.plugins.playbook:PlaybookPlugin`, the Skylit Playbook's `LiveSession`, unchanged). `configs/runner.yaml` enables `template` only; `configs/runner.paper.yaml` is the paper forward-test config ([`docs/plugin-runner.md`](docs/plugin-runner.md) "Paper forward test": `scripts/run_paper.sh`, `scripts/launchd/`). A new strategy is one plugin class plus one `plugins` entry; copy `template` to start, and see [`research/README.md`](research/README.md). No concrete strategy plugin ships with this copy.

### `configs/runner.yaml`

- `paths`: state, recordings, run, bar-store, cache and calendar directories. The `--state-dir`, `--recordings-root`, `--out`, `--bar-store`, `--cache-dir` and `--calendar-dir` flags override them. Every directory must be outside the repository.
- `data_plane.instruments`: the instruments recorded (default MNQ and MES).
- `data_plane.projectx`: `transport` (`auto`: the market hub when it connects, else polling), `subscription` (`auto` probes the live and sim data subscriptions at start and logs the bar lag), poll intervals, `record_quotes`, `backfill_sessions` (past days fetched into the bar store at start) and `expected_contracts` (contract id fallback per instrument).
- `data_plane.skylit`: `mode`, `refresh_interval_s`, `symbols`, `heatmap_view`, `window` (NY times; no Skylit request outside exchange sessions or outside the window) and `dark_pool`. The start log carries the day's credit projection.
- `plugins[]`: `id`, `module` (a dotted path inside this Project or the installed `fse` package), `enabled`, the plugin's own `config` block, and for host-brokered plugins `fills` (trade-through, slippage, `stop_fill`, `commission_rt`) and `risk` (`max_contracts` required; `max_open_trades`, `max_daily_loss`, `max_trades_per_day`). `hold_through_break` can only restrict what the plugin declares; otherwise the host flattens any open paper position at the 18:00 rotation.
- The `playbook` entry ships disabled: its `strategy_config` (`configs/playbook_baseline.yaml`) exits 2 until the fees are set. Its config also takes `out_root` (run directories `live-<session>-<hex8>/`, default `~/.skylit-fse/runs`), `open_lead_s` (default 300: the session opens that long before the earlier of `live.run_window.start` and `notify.premarket`) and `prep_lead_s` (default 120). The data plane's Skylit window must start at or before `live.run_window.start` and end at or after `live.run_window.end`; in polling mode its start must be a whole number of refresh intervals before `live.run_window.start`.

`fse run` needs `PROJECTX_USERNAME` and `PROJECTX_API_KEY`, and `SKYLIT_API_KEY` when `data_plane.skylit.enabled` is true. Exit status: 0 stopped cleanly, 1 after a feed stop or a plugin fault, 2 invalid input (config, plugin load, needs, calendars, paths, lock held), 3 a blank credential or a ProjectX auth failure, 130 a second SIGINT.

### Write a new plugin in 5 steps

[`src/fse/live/plugins/template.py`](src/fse/live/plugins/template.py) is a runnable example: one market entry per day at `entry_time`, flattened at `flatten_time`, with every hook written out and commented. Architecture, results and the first-day checklist are in [`docs/plugin-runner.md`](docs/plugin-runner.md).

1. Copy `template.py` into this Project (for example `research/<name>/plugin.py`) and rename the class. Plugins load only from this Project or the `fse` package.
2. Declare `needs()`: the bar instruments, `skylit=SkylitNeed(...)` and `quotes=True` if wanted, `brokerage="host"` for a paper book.
3. Put the strategy in the hooks: warm-up in `on_start`, per-day reset in `on_day_open`, decisions in `on_bar` (and `on_snapshots`/`on_quote`), fills in `on_order_update`, time exits in `next_wake`/`on_wake`, state in `snapshot`/`restore`. Intents: `EnterMarket` (set `reference_t` to measure the stop and targets from a price you choose, such as the signal close, instead of the fill bar's open), `EnterResting`, `Flatten`, `Cancel`, and `ModifyStop(tag, stop_price)` to tighten an open trade's stop (breakeven, trailing; it applies from the next bar).
4. Add a `plugins` entry to `configs/runner.yaml` (copy the `template` entry: `module`, `config`, `fills`, `risk`). The plugin can read these terms from `__init__` on: `services.fills` (`PaperFills`), `services.risk` (`PaperRisk`) and `services.broker_terms` (`BrokerTerms`, both in one flat view); all three are `None` for a self-brokered plugin.
5. Test it like `tests/unit/test_plugin_template.py` (loader + `FakeDataPlane`, no network), then run `fse run --config configs/runner.yaml`.

### Paper by default, opt-in orders, and the kill switches

By default no order request is sent to ProjectX from `fse run`. Host-brokered plugins trade an in-process paper book; the Playbook plugin always builds its session in Paper Order_Mode and faults if the session is anything else.

**Opt-in orders.** One host-brokered plugin whose `needs()` declares `broker_orders=True` (a plugin you write; none ships with this copy) may set `plugins[].execution.order_mode` to `practice` or `combine`. It keeps its paper book (cards, analytics, accounts, `plugin.json` unchanged) and also sends its intents to ProjectX as market entries with a stop-loss and one take-profit bracket in ticks. Every other plugin stays paper. All of these must hold, or the plugin runs paper and its first Finding_Card names each failed condition (by flag or variable name only):

- the start command has `--plugin-orders` **and** the environment has `FSE_RUN_PLUGIN_ORDERS=enabled` (checked before any ProjectX account call);
- practice: `PRACTICE_ACCOUNT_ID` is set, resolves, and differs from `COMBINE_ACCOUNT_ID`;
- combine: the `fse live` three-way gate, unchanged: `order_mode: combine`, `COMBINE_ACCOUNT_ID` equal to a resolved account, and `--live-orders`.

Local enable command: `FSE_RUN_PLUGIN_ORDERS=enabled fse run --plugin-orders --config ...`. An unreadable `<live-state>/plugins/<id>/order_journal.jsonl` exits 4.

The config is refused (exit 2) unless the non-paper entry has a `risk` block with `max_daily_loss` and `max_trades_per_day`, `hold_through_break: false`, is the only non-paper entry (disabled ones count), declares no instrument the broker path ignores (`MGC`, `SIL`), and, for combine, names a paper account with the flat deadline on. A self-brokered plugin or one without `broker_orders` exits 2 too.
A practice or combine plugin may sit on an account with `nexus.enabled`: orders go to ProjectX and the plugin's paper-book fills are mirrored to Nexus (paper prices, so Nexus can differ from the ProjectX account). A Nexus outage or error never blocks, delays or changes a ProjectX order or exit.

Rules for a broker run:

- The account named by `PRACTICE_ACCOUNT_ID`/`COMBINE_ACCOUNT_ID` must be dedicated to this one plugin: no `fse live`, other runner or manual trading may use it while `fse run` sends orders. The 18:00 backstop `Flatten(None)` closes the whole account position per instrument, and a `broker_note` card flags foreign orders or positions at each day's first sync.
- `risk.max_daily_loss` is measured on the paper book's net, not the broker's realized P&L; a divergence beyond the paper result is caught only by reconciliation (a persistent `reconciliation` block in the plugin dir; `fse clear --state-dir <live-state>/plugins/<id>` after checking). It also caps per-trade stop dollar risk: a plugin that sizes by risk can size qty down so stop risk fits the remaining budget (`max_daily_loss + day_net`), or skip when even one contract would exceed it; the host rejects an oversized `EnterMarket` before a ProjectX place.
- The plugin's own session-close flatten is the primary close. The host backstop runs at 18:00 NY (after the break), so it is a last resort and does not make a practice account flat by the close.
- Open broker positions are not flattened on stop: stop flat. At stop, queued exits get up to 10 s; entries are never sent.

Practice first: put the practice account in Auto OCO Brackets mode, set `data_plane.projectx.expected_contracts` and check the roll, run several sessions locally, and check the first card, `<live-state>/plugins/<id>/order_journal.jsonl` and `client_seq.json`, and the daily diff. ProjectX's handling of a type-2 market order with brackets is not verified until then; do this before any combine use.

AWS stays blocked: `FSE_UI_ENABLE_LIVE=false`, the SSM controller refuses `fse live`, and the AWS runner service runs a fixed `fse run` command (`docker/entrypoint.sh`, `runner` mode) with no `--plugin-orders`; neither SSM nor the console's runner settings can add arguments to it. An `FSE_RUN_PLUGIN_ORDERS` parameter under `/fse/secrets/` therefore does not turn orders on. `tests/unit/test_aws_runner_paper.py` fails the build if either order flag appears in the entrypoint. Known gap, unchanged by this feature: the image's `batch` and pass-through modes take arbitrary arguments, and the runner role may `ecs:RunTask` the batch task with command and environment overrides; that path already allows `fse live` today. Closing it (an IAM condition or an entrypoint allow-list) is a separate follow-up.

Kill switches (each also stops new broker orders: the safety re-checks the host's block reasons before every send and resend; exits, cancels and modifies are never gated, as in `fse live`; a market entry already sent is not cancelled):

- `<live-state>/HALT` or `fse halt` (no flag) blocks new entries of every plugin; `fse clear` lifts the blocks. The Playbook keeps its own guards on the live-state root, exactly as under `fse paper`.
- `fse halt --state-dir <live-state>/plugins/<id>` (or a `HALT` file there) blocks one plugin; `fse clear --state-dir <live-state>/plugins/<id>` lifts it (also a `restore_failed` block after an unreadable `plugin.json`).
- The per-plugin `risk` caps, a plugin fault (the plugin gets no more calls until a restart; its open paper trades stay under the 18:00 backstop) and SIGINT/SIGTERM. Open paper positions are not flattened on stop; they resume on restart.


### Console Practice orders (AWS)

Practice first. Combine and `fse live` stay unavailable on AWS and in the console.

Precondition: the `fse-production` GitHub environment has a required reviewer and its deployment branches are limited to your release branch.

Turn on Practice for one broker plugin (a plugin you wrote):

1. Set the repository variable `FSE_PLUGIN_ORDERS_ENABLED=true`, then run the fse-infra workflow from your release branch with apply (`gh workflow run fse-infra.yml --ref <release branch>`). Deploy order does not matter for safety: the runner stays paper until the new image and the new host control file `/run/fse-control/plugin_orders` both exist and the switch is enabled.
2. Live → Runner → Restart, so the host rewrites `/run/fse-control/plugin_orders`.
3. Settings → Secrets: set `PROJECTX_USERNAME` and `PROJECTX_API_KEY`, then use Find my ProjectX accounts and Use as Practice on your PRAC account (it must differ from `COMBINE_ACCOUNT_ID`; the id is the number, not the account name), then restart the runner.
4. Live → <plugin> → Order mode → Practice. Enter the max daily loss and max trades per day, then confirm the restart.
5. Check the plugin badge says `Practice in use`. `Requested practice, running Paper: ...` names each failed condition.

The TopstepX terms are already marked verified: `topstep_terms_verified_on` defaults to 2026-10-13 (`infra/variables.tf`); verify the terms yourself and set your own date, and the repository variable `FSE_TOPSTEP_TERMS_VERIFIED_ON` overrides it.

Caps: edits to `risk.max_daily_loss`, `risk.max_trades_per_day` or `hold_through_break` of a Practice plugin on the Configs page are saved without the Practice confirm and apply at the next runner start or restart. Change them through Live → <plugin> → Order mode → Practice to get the confirm.

Turn it off: Halt blocks new entries at once. An open position and its working brackets stay at the broker until the plugin exits them; to stop now, flatten in TopstepX. Then apply `false` and use Live → Runner → Restart (it refuses while a position or working order is visible).

Kill switch scope: The switch bounds the console application and its users: no console code path can enable plugin orders, Combine or `fse live`. It does not bound a principal holding the instance-role credentials. That principal can read the ProjectX key and account ids (ReadFseParameters) and can launch a batch task with command and env overrides (EcsRunBatch). This is pre-existing and accepted (decision c). The IAM narrowing only removes the `/secrets/*` write path.

Rollback hazard: a `plugin.json` written while a market entry is pending holds `MarketBracket.reference_t` (also when it is `null`). A build without `EnterMarket.reference_t` rejects that file ("unknown fields"): it renames it `plugin.json.bad-<stamp>`, starts the plugin fresh and blocks it with `restore_failed`. Stop the runner with no market entry pending before you roll back (check the plugin's state dir first), and run `fse clear --state-dir <live-state>/plugins/<id>` to lift the block if it happens.

`fse run`, `fse paper` and `fse live` take `<live-state>/runner.lock`: while one of them runs, starting another on the same live-state directory exits 2 with "another runner holds <path>".

### The Playbook under `fse run`

Given the same bar, snapshot and dark-pool receipt times, the Playbook plugin makes the same decisions and writes the same state files, Finding_Cards, live log and recording (`<recordings_root>/recordings/<session>.jsonl.gz`, so the Backtester's replay works unchanged) as `fse paper` started at the plugin's open instant. The differences:

- **Receipt times.** Bars arrive on the data plane's schedule: with `transport: stream`, or when another plugin asks for fast polls, a closed bar can arrive up to about 4 s earlier than under `fse paper`'s 5 s poll, so Decision_Times can shift. In stream mode the first snapshot at `run_start` is the data plane's latest pre-`run_start` snapshot rather than the one a fresh connection would deliver, so map content at `run_start` can also differ.
- **Fetch_Log.** The Skylit requests (the LevelsCompare ones included) are logged in the data plane's `<run dir>/dataplane/<day>/fetch_log.jsonl`; the Playbook run directory has no `fetch_log.jsonl`.

## Export to AWS (Athena and SageMaker)

`fse export-aws` copies the Data_Cache into a private S3 bucket as compacted, Hive-partitioned Parquet (zstd) and creates one Glue table per output table with partition projection, so Athena and SageMaker can query it with no crawler. The cache is only read. boto3 (the `aws` extra) is installed by the Setup step `python -m pip install --require-hashes -r requirements.lock`.

1. Check that the AWS profile resolves to the expected account (for an SSO profile, run `aws sso login --profile <name>` first):
   ```sh
   aws sts get-caller-identity --profile default
   ```
2. Look at the plan (writes nothing; makes only `sts:GetCallerIdentity`, `s3:HeadBucket` and `glue:GetDatabase`), stage locally (no AWS call), then run the export:
   ```sh
   fse export-aws --dry-run
   fse export-aws --stage-only
   fse export-aws
   ```
   Defaults: profile `default`, region `us-east-1`, bucket `skylit-fse-data-<account-id>-us-east-1` (account from STS), prefix `fse`, Glue database `skylit_fse`, staging directory `~/.skylit-fse/aws-export`. A rerun only restages and uploads what changed. `--datasets`, `--symbols` and `--months` select whole partitions.
3. The bucket holds licensed Skylit API data. It is created private (public access block, SSE-S3, `BucketOwnerEnforced`, a deny-insecure-transport policy, a 7-day abort-incomplete-multipart rule) and must never be shared publicly.
4. IAM permissions the Operator's principal needs for a real run:
   - `sts:GetCallerIdentity`;
   - on the bucket: `s3:CreateBucket`, `s3:PutBucketPublicAccessBlock`, `s3:PutEncryptionConfiguration`, `s3:PutBucketOwnershipControls`, `s3:PutBucketPolicy`, `s3:PutLifecycleConfiguration`, `s3:GetBucketPublicAccessBlock`, `s3:GetEncryptionConfiguration`, `s3:GetBucketOwnershipControls`, `s3:GetBucketPolicy`, `s3:GetLifecycleConfiguration`, `s3:ListBucket`;
   - on `<bucket>/<prefix>/*`: `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:AbortMultipartUpload`;
   - Glue: `glue:GetDatabase`, `glue:CreateDatabase`, `glue:GetTable`, `glue:CreateTable`, `glue:UpdateTable`.
5. An existing bucket is never modified. If a run warns that it has no `DenyInsecureTransport` policy or no `AbortIncompleteMultipartUpload` rule, apply these by hand (replace `<bucket>`):
   ```json
   {"Version": "2012-10-17", "Statement": [{"Sid": "DenyInsecureTransport", "Effect": "Deny",
     "Principal": "*", "Action": "s3:*",
     "Resource": ["arn:aws:s3:::<bucket>", "arn:aws:s3:::<bucket>/*"],
     "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}
   ```
   ```json
   {"Rules": [{"ID": "fse-abort-incomplete-mpu", "Filter": {}, "Status": "Enabled",
     "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]}
   ```
6. SageMaker execution-role policy for the snippets below:
   ```json
   {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
     "Resource": "arn:aws:s3:::<bucket>"},
    {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::<bucket>/fse/*"},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts"], "Resource": "arn:aws:s3:::<bucket>/athena-results/*"},
    {"Effect": "Allow", "Action": ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable",
      "glue:GetTables", "glue:GetPartitions"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["athena:StartQueryExecution", "athena:GetQueryExecution",
      "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"],
     "Resource": "*"}
   ]}
   ```
7. Queries and snippets. Tables: `heatmaps` (partitions `symbol`, `metric`, `month`), `bars` and `vix_bars` (`instrument`), `vix_daily`, `darkpool` (`ticker`), `darkpool_fetched_dates`, `regime_medians`. Always filter on `month` for partition pruning.
   ```sql
   -- 0. Discover the views in a month (run this first; pick a view_id for the queries below)
   SELECT DISTINCT symbol, view_id, view_json
   FROM skylit_fse.heatmaps WHERE month = '2026-10';

   -- 1. GEX per strike on one SPX snapshot minute
   SELECT from_unixtime_nanos(as_of_ns) AT TIME ZONE 'America/New_York' AS t_ny, spot, strike, value
   FROM skylit_fse.heatmaps
   CROSS JOIN UNNEST(strikes, strike_values) AS u(strike, value)
   WHERE symbol = 'SPX' AND metric = 'gamma' AND month = '2025-10'
     AND view_id = '29edd099d4c38876'          -- one view = one strike grid; see query 0
     AND session = DATE '2025-10-06' AND source_endpoint = 'range'
     AND as_of_ns BETWEEN 1759757400000000000 AND 1759757459999999999;

   -- 2. Snapshot coverage per view and session
   SELECT symbol, metric, view_id, session, count(*) AS snapshots
   FROM skylit_fse.heatmaps WHERE month BETWEEN '2025-10' AND '2025-12'
   GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

   -- 3. MES 1-minute bars with VIX daily open
   SELECT b.session, from_unixtime_nanos(b.open_ns) AS t, b.o, b.h, b.l, b.c, v.open AS vix_open
   FROM skylit_fse.bars b JOIN skylit_fse.vix_daily v ON v.session = b.session
   WHERE b.instrument = 'MES' AND b.session = DATE '2025-10-06';
   ```
   ```python
   # SageMaker, via Athena (awswrangler is in SageMaker Distribution images; else pip install awswrangler)
   import awswrangler as wr
   BUCKET = "skylit-fse-data-<account-id>-us-east-1"  # printed by: fse export-aws --dry-run
   VIEW_ID = "29edd099d4c38876"                       # from query 0; never mix views
   df = wr.athena.read_sql_query(
       "SELECT * FROM heatmaps WHERE symbol='SPX' AND metric='gamma' AND month='2025-10' "
       f"AND view_id = '{VIEW_ID}' AND session = DATE '2025-10-06'",
       database="skylit_fse",
       s3_output=f"s3://{BUCKET}/athena-results/",
       ctas_approach=False,  # True is faster for big results but needs glue:CreateTable
   )

   # SageMaker, straight from S3 with pyarrow (no Athena cost; row groups pruned by session stats)
   import datetime as dt
   import pyarrow.dataset as ds
   import pyarrow.fs as pafs
   data = ds.dataset(f"{BUCKET}/fse/heatmaps/",
                     filesystem=pafs.S3FileSystem(region="us-east-1"),
                     format="parquet", partitioning="hive")
   table = data.to_table(filter=(ds.field("symbol") == "SPX") & (ds.field("metric") == "gamma")
                         & (ds.field("month") == "2025-10")
                         & (ds.field("view_id") == VIEW_ID)
                         & (ds.field("session") == dt.date(2025, 10, 6)))
   df = table.to_pandas()
   ```
   Notes:
   - Rows from different views are separate strike grids: always filter or group by `view_id` (query 0 lists them).
   - `strike_values[i]` belongs to `strikes[i]` (the cache column `values` is renamed because `VALUES` is reserved in Athena).
   - `source_endpoint = 'historical'` rows are label samples that carry `node_types`.
   - Time columns are int64 ns UTC; use `from_unixtime_nanos`.
   - While a partition is being replaced, a query can briefly see a mix of old and new parts; rerun it after the export finishes.
   - In a Lake Formation-governed account, `CreateTable` can succeed while Athena reads fail until the reading role has Lake Formation grants on the database and tables.

## Run on AWS

`fse run` (paper only) runs 24/7 on one EC2 `t4g.small` (container `fse-runner`, data on an encrypted EBS volume at `/data`). `fse pull`, `fse backtest`, `fse experiment` and `fse report` run on demand as Fargate Spot tasks (ARM64, 8 vCPU, 32 GB) that share the Data_Cache through the data bucket. Terraform lives in `infra/bootstrap/` (state bucket and GitHub OIDC roles, applied once with local state) and `infra/` (everything else). GitHub Actions tests, builds and deploys (`.github/workflows/fse-deploy.yml` and `fse-infra.yml` at the repository root).

### Prerequisites

- Terraform 1.11 or later (the secret parameters use write-only arguments).
- AWS CLI v2 with the `default` profile (`aws sso login --profile default` for SSO). `scripts/aws-batch.sh` needs a CLI release that supports `aws s3api put-object --if-none-match` (added in 2024).
- The AWS Session Manager plugin, for `aws ssm start-session`.
- Docker, only to build the image locally (`docker build --platform linux/arm64 -t fse:local .`).
- `gh` (GitHub CLI) for the GitHub steps below, or the repository settings page.

### First setup

Run these from this folder with `export AWS_PROFILE=default`.

1. Apply the bootstrap stack. Its state is the local file `infra/bootstrap/terraform.tfstate` (gitignored); keep a copy somewhere safe. It creates the CI apply role `fse-gha-apply` (`AdministratorAccess`) by default so CI can run `terraform apply`. Set `-var create_apply_role=false` only if you want to apply from your machine instead; that destroys the role if it already exists.

   ```sh
   cd infra/bootstrap
   terraform init
   terraform apply
   terraform output
   ```

2. Configure the main stack from the examples (both copies are gitignored). To run in an existing VPC instead of the dedicated one (`10.40.0.0/16`), set `existing_vpc_id` and `existing_subnet_ids` (public subnets routed to an internet gateway, no NAT); then only the `fse-runner` and `fse-batch` security groups are created in that VPC, and S3 traffic uses the internet gateway instead of a gateway endpoint. Put the bootstrap output `state_bucket_name` in `backend.hcl` and the alert address in `terraform.tfvars`. `adopt_existing_data_bucket = true` (the default) adopts the bucket `fse export-aws` created; set it to `false` if that bucket does not exist yet.

   ```sh
   cd ..
   cp backend.hcl.example backend.hcl
   cp terraform.tfvars.example terraform.tfvars
   ```

3. Apply the main stack, then confirm the subscription in the "AWS Notification" email sent to `alert_email`:

   ```sh
   terraform init -backend-config=backend.hcl
   terraform apply
   ```

4. Set the secrets by hand. Terraform creates each `/fse/secrets/<NAME>` parameter with the value `PLACEHOLDER-set-by-hand` and never overwrites it; a placeholder reaches `fse` as a blank value. Set `SKYLIT_API_KEY`, `PROJECTX_USERNAME` and `PROJECTX_API_KEY` (and `FSE_SECRET_VARS`, `NOTIFIER_WEBHOOK_URL`, `NARRATOR_API_KEY` when used). The value is read from a prompt so it stays out of shell history:

   ```sh
   name=SKYLIT_API_KEY
   printf '%s: ' "$name"; stty -echo; read -r value; stty echo; echo
   aws ssm put-parameter --name "/fse/secrets/$name" --type SecureString --overwrite --value "$value"
   unset value
   ```

   The runner reads the secrets at every start (`sudo systemctl restart fse-runner`); batch tasks read them at task start.

   The parameters use the write-only `value_wo`, so the secret values never enter `infra/terraform.tfstate`. The plan-push role (`ReadOnlyAccess`) can still read `/fse/secrets/*` directly, because a Terraform plan refreshes those parameters; treat every job that assumes it as able to read the secrets.

5. Configure GitHub. The secret is piped straight into `gh`, so the key is never printed:

   ```sh
   gh variable set AWS_PLAN_PUSH_ROLE_ARN --body "<bootstrap output plan_push_role_arn>"
   gh variable set AWS_DEPLOY_ROLE_ARN --body "<bootstrap output deploy_role_arn>"
   gh variable set AWS_APPLY_ROLE_ARN --body "<bootstrap output apply_role_arn>"   # skip with create_apply_role=false
   gh variable set TF_STATE_BUCKET --body "<bootstrap output state_bucket_name>"
   gh variable set FSE_ALERT_EMAIL --body "<alert_email>"
   # only when using an existing VPC (same values as terraform.tfvars):
   gh variable set FSE_EXISTING_VPC_ID --body "<vpc id>"
   gh variable set FSE_EXISTING_SUBNET_IDS --body '["<subnet id>"]'
   ```

   Create the environment `fse-production` (Settings, Environments) with yourself as required reviewer, and limit its deployment branches to `<release branch>`. Every job that gets AWS credentials beyond the image push runs in it, so nothing deploys, plans or applies without an approval. The OIDC roles trust only pushes to `<release branch>` and the `fse-production` environment (bootstrap variables `github_branch` and `github_environment`). Both workflows run only on `<release branch>`; pull requests into it get the offline infra checks, never a plan. The repository is shared, so protect `<release branch>` with a ruleset that only you can push to: a push to it is enough to run the image push with AWS credentials.

6. Seed the cloud Data_Cache from this Mac once (refuses while a local `fse pull` runs; prints the size first):

   ```sh
   scripts/aws-seed-cache.sh --yes
   ```

7. Apply the main stack so `cache_efs` and the batch EFS volume exist (`gh workflow run fse-infra.yml --ref <release branch> -f apply=true`, or `terraform apply` in `infra/`). Then run `fse-deploy` (or wait for the next push deploy) so the registered `fse-batch` revision gets `mountPoints` + `FSE_CACHE_BACKEND=efs` — Terraform’s `ignore_changes` on container defs alone will not plant mounts on an existing family.

8. Seed the Fargate EFS cache from S3 once (or after a big Mac→S3 upload). Uses **Fargate On-Demand** (not Spot) because a full sync is 30–60+ minutes. EventBridge reconciles every 6 hours on On-Demand under the pull lock. `aws s3 sync` is resume-friendly — re-run after interrupt continues from what is already on EFS.

   ```sh
   scripts/aws-seed-efs-cache.sh --yes
   # If a prior seed/pull left locks/pull.lock stuck:
   scripts/aws-seed-efs-cache.sh --yes --force
   # or: scripts/aws-batch.sh unlock --yes
   ```

   The script owns `locks/pull.lock` (releases on exit/Ctrl-C), refuses to RunTask if the active `fse-batch` revision lacks the EFS volume/mount, and polls `describe-tasks` until STOPPED (no short ECS waiter timeout).

### Deploy

The GitHub Actions workflows (`fse-deploy`, `fse-infra`) are not part of this copy: add your own workflows with these jobs. The description below is the pipeline they implement.

A push to `<release branch>` that touches this folder outside `infra/`, `docs/` and `README.md` runs `fse-deploy`: `ruff` and `pytest`, then `scripts/docker-smoke-card.sh` (image build, uid 10001, `FSE_CACHE_BACKEND=s3`, one MNQ session through `backtest-plugin`). The arm64 image is pushed to ECR as `fse:<commit sha>` only after that smoke passes, then the `deploy` job waits for the `fse-production` approval. It sets `/fse/image_tag` to the sha, registers a new `fse-batch` task definition revision with the new image and restarts `fse-runner` through SSM Run Command. Infra-only and docs-only commits skip it on purpose, since every restart carries the rollback hazard; force a deploy by hand with `gh workflow run fse-deploy.yml --ref <release branch>`.

Until the first deploy sets `/fse/image_tag`, `fse-runner.service` fails by design ("no image deployed yet" mail, at most one per hour). Watch the runner after a deploy:

```sh
aws logs tail /fse/runner --follow
```

Changes under `infra/` run `fse-infra`: offline `terraform fmt`, `validate` and `test` for both stacks on every pull request into `<release branch>` and every push to it, then a plan after the `fse-production` approval (resource addresses and actions in the job summary). Apply happens only from `gh workflow run fse-infra.yml --ref <release branch> -f apply=true` with `AWS_APPLY_ROLE_ARN` set; without it, run `terraform apply` from your machine.

### Operate the runner

1. Open a shell on the host (no SSH, no open port). `terraform -chdir=infra output -raw runner_instance_id` prints the id:

   ```sh
   aws ssm start-session --target <runner_instance_id>
   ```

2. Halt or clear new entries (defaults point at `/data/live-state`; add `--state-dir /data/live-state/plugins/<id>` for one plugin):

   ```sh
   sudo docker exec fse-runner fse halt
   sudo docker exec fse-runner fse clear
   ```

3. Stop or restart the runner with `sudo systemctl stop fse-runner` and `sudo systemctl restart fse-runner`. A stop gives `fse` 60 s to exit cleanly.

Exit 2 (config or lock) and 3 (blank credential or auth failure) wait `fault_backoff_s` (default 900 s) before systemd restarts the container; after 6 failed starts in 2 hours systemd gives up and mails. Fix the cause, then `sudo systemctl reset-failed fse-runner && sudo systemctl start fse-runner`.

### Batch commands

Run from this folder. The script reads the cluster, subnets, security group and bucket from `terraform output` in `infra/`, streams the task log and exits with `fse`'s exit code:

```sh
scripts/aws-batch.sh pull --start 2025-10-06 --end 2026-10-02 --instruments MES,MNQ --dark-pool
scripts/aws-batch.sh backtest --config configs/playbook_baseline.yaml --start 2025-10-06 --end 2026-10-02 --offline --seed 1
scripts/aws-batch.sh experiment <fse experiment args...>
scripts/aws-batch.sh report --run <run id>
```

- A pull that exits 2 (invalid input), 3 (API key rejected) or 4 (cache write error) needs a person; rerun the same command to resume after the fix.
- One pull at a time: the lock is `s3://<data bucket>/locks/pull.lock` and a held lock exits 75. Ctrl-C stops the task and releases the lock. A lock whose `started=` time is at least `FSE_LOCK_TTL_S` seconds old (default 7200, two hours) is replaced on the next acquire. A missing or unreadable `started=` line is not. Delete one immediately (only when no pull or seed is running) with `scripts/aws-batch.sh unlock --yes` or `scripts/aws-seed-efs-cache.sh unlock --yes`. The Jobs page Unlock does the same when no ECS job holds the lock.
- Every task downloads `cache/` first; `--run <run id>` also downloads `runs/<run id>/` (a path or `--runs-dir` is not downloaded). Run outputs land in `s3://<data bucket>/runs/`. Only `pull` uploads `cache/`; the other commands read it.
- Fargate Spot can reclaim a task; rerun it. A reclaimed task has 120 s (the Fargate maximum `stopTimeout`) for `fse` to stop and for the uploads to finish. After a long pull the `cache/` upload may not finish in time, and the rerun fetches the sessions that did not reach S3 from the API again.

### Quarterly roll and rollback

A contract roll (for example `MNQZ6` to `MNQH7`: the new `mixed_sessions` entry in `calendars/roll_calendar.yaml`, plus any roll table your own plugins keep) is a code change, so on AWS it is a deploy: push to `<release branch>`, approve `fse-production`, and the runner restarts on the new image. Deploy it before the roll day.

Rollback hazard (see "Paper by default, opt-in orders, and the kill switches"): an older build can reject a `plugin.json` written by a newer one. To roll back, first check the plugin log that no market entry is pending, then set the previous sha and restart:

```sh
aws ssm put-parameter --name /fse/image_tag --type String --overwrite --value <previous sha>
aws ssm start-session --target <runner_instance_id>   # then: sudo systemctl restart fse-runner
```

Opt-in broker orders add only new files to the plugin dir (`order_journal.jsonl`, `client_seq.json`); older builds ignore them, and `plugin.json` keeps its format. A `bracket_failure` or `reconciliation` block written there by the broker path also blocks that plugin under an older build (fails closed); `fse clear --state-dir <live-state>/plugins/<id>` lifts it. The runner YAML is the one exception: an older build refuses a `plugins[].execution` block as an unknown key, and `fse run` exits 2 at load. Remove (or comment out) every `execution:` block before you roll back; the shipped configs carry it only as a comment.

Batch tasks keep the latest `fse-batch` revision; deregister a bad one with `aws ecs deregister-task-definition --task-definition fse-batch:<revision>`.

### Alerts

All alerts go to the `fse-alerts` SNS topic (the `alert_email` subscription):

- `fse-runner` exited non-zero, with the meaning (1 feed or plugin fault, 2 config or lock, 3 credentials, 130 SIGINT); at most one mail per exit code per hour. A deliberate stop (deploy restart, patch reboot) sends nothing.
- systemd gave up restarting `fse-runner`, or no image is deployed yet.
- EC2 system status check failed (the instance is recovered) or instance status check failed (it is rebooted).
- No `/fse/runner` log events for 15 minutes. This can fire on quiet weekends and holidays.
- Every stopped `fse-batch` task, with its exit code and stop reason (exit 0 included).

### Costs

About $20 a month with no batch use (us-east-1): `t4g.small` about $12, the 30 GB data volume and 20 GB root volume (gp3) about $4, the Elastic IP about $3.65, snapshots, S3 and logs about $1-3. Batch on Fargate Spot ARM64 (8 vCPU, 32 GB, 100 GB ephemeral storage) costs about $0.11-0.15 an hour while a task runs. The `t4g.small` uses unlimited CPU credits, so sustained CPU above its baseline bills extra.

### Teardown

1. Copy what you want to keep: `aws s3 sync s3://<data bucket>/ <local dir>` and the runner's `/data` (a snapshot of the data volume is kept 7 days).
2. Remove the `prevent_destroy = true` lines from `infra/modules/runner/main.tf` (data volume) and `infra/modules/data_bucket/main.tf`, empty the data bucket and the `fse` ECR repository, then run `terraform destroy` in `infra/`. It fails until both steps are done.
3. Only after that, remove `prevent_destroy` from `infra/bootstrap/main.tf`, empty the state bucket (every object version) and run `terraform destroy` in `infra/bootstrap/`.

### Console

The fse console (`ui/`, see `ui/README.md`) runs on the runner host behind CloudFront and Cognito at `https://<ui_subdomain>.<domain_name>` (default `https://fse.example.com`). After the setup below, every step runs from the console or from GitHub Actions; no shell is needed.

1. Check the quota first: Service Quotas → Amazon VPC → "Inbound or outbound rules per security group" (`L-0EA8095F`) must be 60 or more. Request an increase if it is lower. A plan with the UI on fails while it is lower, because the CloudFront prefix list counts as about 55 rules.
2. Re-apply the bootstrap stack with the apply role. This adds ECR push and layer pull on the new `fse-ui` and `fse-edge` repositories, and the CI apply role `fse-gha-apply`. Then set the `AWS_APPLY_ROLE_ARN` repo variable from its `apply_role_arn` output. This is the one local step left (the bootstrap state is local). Origin secret rotation, a new invitation and turning the UI off run through `fse-infra` with `apply = true`, which needs this role; without it, those steps are a local `terraform apply`.

   ```sh
   cd infra/bootstrap
   terraform apply
   gh variable set AWS_APPLY_ROLE_ARN --body "$(terraform output -raw apply_role_arn)"
   ```

3. Set `enable_ui = true` and `operator_email` in `infra/terraform.tfvars`, and the matching repo variables, then apply (`fse-infra` with `apply = true`, or `terraform apply` in `infra/`). Apply on a Saturday: the instance type changes to `ui_runner_instance_type` (`t4g.medium` when unset), which stops and starts the host. `runner_instance_type` is ignored while the UI is on; if you raised it, set `ui_runner_instance_type` to the same size.

   ```sh
   gh variable set FSE_ENABLE_UI --body true
   gh variable set FSE_OPERATOR_EMAIL --body "<operator_email>"
   ```

4. Run the `fse-deploy` workflow (`gh workflow run fse-deploy.yml --ref <release branch>`) to build the `fse-ui` and `fse-edge` images and start `fse-ui` and `fse-edge`.
5. First login: open `https://fse.example.com`, sign in with the temporary password Cognito mailed to `operator_email`, set a new password, scan the TOTP QR code and enter the code. The Elastic IP refuses direct requests; only CloudFront reaches the host.

First-enable order: the apply in step 3 installs `fse-ui.service` and `fse-edge.service` before their images exist, so they fail until step 4. The deploy runs `systemctl reset-failed` on both before it starts them, which clears the start limit.

#### Secrets and the runner

- Enter secrets on the Secrets page. Values are write-only: the console stores them in SSM (`/fse/secrets/<NAME>`) and never shows, logs or audits them. Batch tasks read them at task start, the runner at its next start.
- Restart the runner once from the Live runner page after the first enable. The console's mode and config drop-in (`20-console.conf`) applies at the next runner start; the bundle never restarts the runner.
- A runner stopped in the console stays stopped across deploys until it is started in the console. Deploys do not restart it until you start it here; a host reboot (patch window) does.
- A console Stop puts `fse-runner-log-silence` in ALARM after 15 minutes and mails once. This is expected; the Health page shows the alarm.
- Commits that change only `ui/` do not restart the runner; the deploy restarts `fse-ui` and `fse-edge` only.
- Data cache on the runner: the runner reads `/data/cache` on its own disk, not S3 (never mounts EFS). `fse-sync-cache.sh` copies the bars part of `s3://<data bucket>/cache/` there (`bars/`, `equity_bars/`, `derived/`, `darkpool/`, `vix/` and `catalog.sqlite`, about 70 MB; never the 20 GB of `heatmaps/` or `plugin_books/`) before every runner start and every 30 minutes (`fse-cache-sync.timer`), so anything a pull dual-writes or `scripts/aws-seed-cache.sh` puts in S3 reaches the runner within half an hour. Plugins reopen the cache at the 18:00 ET Globex day open, so the 17:30 ET sync is the one they see each day. Check it with `journalctl -u fse-cache-sync -n 20` on the host. Sessions newer than the cache come from the bars the runner records itself (`/data/live-bars`).
- Data cache on batch (Fargate): the task def sets `FSE_CACHE_BACKEND=efs` and mounts Regional EFS at `~/.skylit-fse/cache`, so jobs **skip** the cold S3 cache download. Writers (`pull`, `pull-equity-bars`, `import-vix`) still **dual-write** their prefixes to S3 so the runner bars sync stays warm. The image creates `~/.skylit-fse/{cache,runs,configs,ui/uploads,research}` owned by uid 10001 before the process starts. ECS otherwise creates the mount's parent as root, and `mkdir runs` exits 1. An `--out` path outside `~/.skylit-fse/runs` is moved there so the runs upload is the folder the API reads by name. Seed/reconcile S3→EFS with `scripts/aws-seed-efs-cache.sh --yes` after infra apply + deploy (see setup step 7–8) on **Fargate On-Demand**; EventBridge runs the same `seed-efs-cache` entrypoint every 6 hours on On-Demand. The manual seed script holds the pull lock itself (`FSE_SEED_LOCK_EXTERNAL=1` in the task) so an interrupt cannot leave it stuck. The in-task lock skips a fresh lock and replaces one older than `FSE_LOCK_TTL_S` (default 7200s). Clear a stuck lock with `scripts/aws-seed-efs-cache.sh unlock --yes`, `--yes --force`, or `scripts/aws-batch.sh unlock --yes`. Unset/`s3` is scoped per-prefix sync (rollback / local). Override prefixes with `FSE_CACHE_PREFIXES`. Ephemeral stays 100 GiB while s3 rollback exists. Card tasks set `FSE_CACHE_BACKEND=s3` and still need those writable directories.
- Paper mode on AWS: each session runs, then the runner waits and logs a retry line every 5 minutes until the next session. An invalid config shows as an `fse paper` error above each retry line, not as an alert.

#### What runs where, and what is disabled

- Batch commands (`fse pull`, `backtest`, `backtest-plugin`, `experiment`, `report`, `import-vix`, `calibrate`, `export-aws`, and the research modules you allowlist in `docker/entrypoint.sh` for `batch-py`) run as Fargate Spot tasks. `halt`, `clear`, `scan-secrets`, `skilldocs check` and any host-local research commands you add run in the UI container on the host, because their inputs are on the host volume. `fse backtest-plugin` (a strategy-card backtest uses this command) stays on the `fse-batch` family and is overridden to 1 vCPU, 8 GiB, and 40 GiB of ephemeral storage. Up to `FSE_UI_ECS_MAX_TASKS` (4 in production) of those tasks run at once. Host-local research commands share the one heavy slot in the UI container.
- A card backtest omits `--history` unless the caller sets it. The card sizes warmup from the spec's longest lookback (a daily DEMA of length 50 needs 100 sessions; the old cap of 60 could not warm it). The rules plugin replays those sessions with entries off before the design range. Preflight checks every spec symbol the same way. A missing design-window session or a missing catalog is a 422, and that 422 records a failed phase job so the card shows this refusal instead of an older failure. The warmup object on the 422 is informational. When every design window is complete and only the sessions before it are short, history is clamped to the fewest bars that exist and the job log starts with a warning; the backtest still runs. Exit codes are per command: `backtest-plugin` 1 is a failure (the card shows the last log lines), not "secrets found". The job log prints a line per Globex day (`replay i/n`, bars, trades, bars/s). `--lag-s` is a receipt stamp, not a real sleep.
- Before RunTask the console uploads cache files and closed recording days that S3 does not have yet, under `locks/pull.lock` and `locks/recordings.lock`, and releases both before the task starts. Today's recording is not uploaded until the New York day closes. If `s3://<data bucket>/cache/` is empty, seed it once with `scripts/aws-seed-cache.sh` or `fse pull`, then `scripts/aws-seed-efs-cache.sh --yes` so EFS matches S3. The new IAM (host `SeedMissingCache` on the named cache prefixes, task `ReadRecordings` on `recordings/*`) is applied by `fse-infra` with `apply = true`, not by `fse-deploy`.
- Holdout runs belong in the console on AWS: the holdout log lives in `state/holdout_log.jsonl` in the data bucket, and each `fse experiment holdout` task copies it down before the run and back after it. One holdout runs at a time (`locks/holdout.lock`).
- Hidden on AWS: `experiment holdout --holdout-log` (it points outside the synced holdout log) and `export-aws --bucket`, `--prefix` and `--glue-database` (they point outside the granted `fse/*` prefix and `skylit_fse` database). The defaults apply.
- Disabled: `fse live` (Practice and Combine orders) until the TopstepX VPS and VPN terms are verified; `fse paper` refuses a Strategy_Config whose `order_mode` is not `paper`; `fse drafts build` and `fse drafts promote` on AWS, because they edit project files in the image (run them from a checkout).

#### Rotation, invitations and access

- Origin secret rotation, with no terminal (needs the apply role from step 2): bump the `FSE_ORIGIN_SECRET_VERSION` repo variable (`origin_secret_version`), run `fse-infra` with `apply = true`. The bundle re-runs and restarts `fse-ui` and `fse-edge`, not the runner. CloudFront answers 403 for about 5 minutes while it deploys, so rotate outside market hours.
- An expired invitation (needs the apply role from step 2): bump the `FSE_OPERATOR_INVITE_VERSION` repo variable (`operator_invite_version`), run `fse-infra` with `apply = true`. Cognito mails a new temporary password; the old TOTP enrollment is dropped.
- Requests reach the host from CloudFront edge addresses, not the operator's.
- Accepted risk: the `fse-edge` and `fse-ui` containers can use the instance role, the same as the runner. Both run read-only with all capabilities dropped (`fse-edge` keeps only `NET_BIND_SERVICE`).

#### Turning the UI off

No shell is needed. Steps 1 and 3 run through `fse-infra` with `apply = true` and need the apply role from step 2 above; without it they are local applies.

1. Apply with `ui_cognito_deletion_protection = "INACTIVE"` (repo variable `FSE_UI_COGNITO_DELETION_PROTECTION`).
2. In the Systems Manager console, Run Command → document `fse-ui-host-remove` → the runner instance. It stops and removes the UI units and the runner drop-in. Run it before step 3, which deletes the document.
3. Apply with `enable_ui = false` (repo variable `FSE_ENABLE_UI`). The instance type returns to `runner_instance_type`, which stops and starts the host, so apply outside market hours. The user pool and its TOTP enrollment are deleted. The runner comes back in `run` mode with `configs/runner.yaml`.

#### Console costs

`t4g.small` → `t4g.medium` adds about $12 a month. CloudFront (`PriceClass_100`) is small at one operator's traffic. Cognito Essentials is in the free tier (10,000 monthly active users). ACM and SSM parameters are free. The `/fse/ui` logs cost pennies.

### Practice and Combine stay off AWS

AWS runs `fse run` and batch commands only. The account owner verified the TopstepX VPS, VPN and device terms on 2026-10-13 (the `topstep_terms_verified_on` default). One broker plugin may run Practice on AWS, set from the console (see "Console Practice orders (AWS)"). Combine and `fse live` stay unavailable on AWS and in the console.

### Phase 2 seams

- `enable_ui` (default `false`) puts the fse console on the host (see "Console").
- The console runs as `fse-ui.service` and `fse-edge.service` next to `fse-runner.service` on the same host.
- The Elastic IP gives the host a stable address; with the UI on, its security group admits port 443 from CloudFront only.

## Environment variables

Every variable in `.env.example` is listed below. Values come only from the shell environment or `.env`; no command takes a credential or account id as an argument.

Order_Modes: Paper (in-process paper broker, the default), Practice (the ProjectX practice account) and Combine (the 50K Combine account). `fse paper` always runs Paper. `fse live` uses the config's `order_mode`, and falls back to Paper when a mode's conditions are not met. Combine also needs the `--live-orders` flag.

| Variable | Purpose | Secret_Variable | Needed by |
| --- | --- | --- | --- |
| `SKYLIT_API_KEY` | Skylit API key for Heatseeker, Flowseeker and Atlas requests | Yes | `fse pull`, `fse paper`, and `fse live` in every Order_Mode; `fse run` when `data_plane.skylit.enabled` is true. Not needed by `fse backtest --offline` or `fse report` |
| `PROJECTX_USERNAME` | ProjectX (TopstepX) user name, sent with `PROJECTX_API_KEY` to get a session token | Yes | `fse paper`, `fse run` and `fse live` in every Order_Mode (live 1-minute bars; Practice and Combine also place orders). `fse pull` when it uses ProjectX bars: Atlas does not serve an instrument, or `--bar-interval 5` |
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
- **Practice checklist.** Run Practice orders (see "Console Practice orders" above) for several sessions and check each card, order journal and reconciliation before any Combine use.
