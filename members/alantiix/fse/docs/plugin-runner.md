# Plugin runner (`fse run`): report

`fse run` is a paper-only, 24-hour Globex process that keeps
the market data flowing (Skylit stream, ProjectX bars and market-hub quotes/trades, all
recorded) and runs any number of strategy plugins side by side, each with its own paper
book. The Skylit Playbook and `template` ship as plugins; `template` is the starting point
for your own.

## 1. Architecture

```
fse run --config configs/runner.yaml
  RunnerLock -> RunnerConfig -> calendars -> load plugins + check needs()
  data plane (always on)   ProjectX bars (REST) + market hub (SignalR) + Skylit stream
                           -> record -> bar store -> events queue
  dispatcher (one task)    per event: each plugin's paper book processes the bar,
                           then the plugin hook runs; intents are routed; state persisted
  plugins                  playbook | template | <your plugins>
```

### Plugin API (`src/fse/live/plugins/`)

- `StrategyPlugin` (ABC, `api_version = 1`): only `needs()` is abstract; every other hook
  is a no-op by default. Hooks: `on_start`, `on_day_open`, `on_bar`, `on_snapshots`,
  `on_dark_pool`, `on_quote`, `on_order_update`, `on_feed_status`, `next_wake`/`on_wake`,
  `urgent_bar_closes`, `on_day_close`, `snapshot`/`restore`, `on_stop`.
- Hooks are synchronous, return intents (`EnterMarket`, `EnterResting`, `Flatten`,
  `Cancel`) and must finish in 1 s (`hook_latency` warning otherwise). Slow work goes
  through `services.run_blocking` (worker thread, sqlite objects confined to it) or
  `services.spawn`; the finished task wakes the plugin once.
- `DataNeeds`: bar instruments, optional Skylit (`SkylitNeed`) and quotes, `brokerage`
  (`host` = own paper book, `self` = the Playbook), `hold_through_break`. Checked against
  the data plane at start; every mismatch of every plugin is listed, exit 2.
- Loader: `module: "pkg.mod:Class"` from the runner YAML, only from this Project or the
  `fse` package, API version checked. Each plugin gets `<live-state>/plugins/<id>/`
  (state, halt file, blocks) and `<run dir>/plugins/<id>/plugin_log.jsonl`. Orders are
  tagged `<plugin id>:<tag>:<leg>`.
- Decision cards (v1): a plugin publishes an entry, skip, hold, exit, trade result or
  info card by logging `ctx.log({"kind": ..., "level": "info", "card": decision_card(...)})`
  (`fse.live.plugins.decision_card`). Card keys: `id` (stable per decision, ≤ 80 chars),
  `title` (≤ 120), `as_of_ns`, `action` (`enter|skip|hold|exit|result|info`), optional
  `instrument`, `symbol`, `side` (`long|short`), `qty`, `entry`, `stop`, `targets` (≤ 4,
  futures points), `reason` (≤ 300), `ref` (the `id` a `result` card closes), `tag`,
  `result` (`net`, `exit_reason`, `r`, `ticks`) and `fields` (≤ 24 `[label, value]` pairs,
  values ≤ 300 chars). The host cleans the card (`fse.live.host.cards.clean_card`: unknown
  keys dropped, invalid `id`/`title`/`as_of_ns`/`action` → dropped with one `card_invalid`
  host warning per plugin) and appends `{"t_ns", "plugin", "kind", "card"}` to
  `<run dir>/plugins/<id>/decision_cards.jsonl`. The console's Cards page lists every
  plugin's cards (and the Playbook's `cards.jsonl` Finding_Cards), newest first.
- `cards` (runner YAML `plugins[].cards`, default `true`; the Plugins page switch "Publish
  Finding Cards"): `false` stops the host writing `decision_cards.jsonl` for that plugin
  from the next runner restart, and the Cards page hides its cards as soon as the config
  is saved. `plugin_log.jsonl` keeps the card lines either way; trading is unaffected.

### Data plane (`src/fse/live/host/dataplane.py`, `sources.py`, `projectx/realtime.py`)

- Runs whether or not a plugin consumes a feed (a feed is off only when its `enabled` is
  false), so the recordings are there for a future plugin.
- ProjectX bars: REST `retrieveBars` is the only bar source (the Gateway has no bar push).
  With the market hub connected, the first trade after a minute boundary (or +1.5 s)
  triggers the fetch, with retries to +10 s; a 5 s safety poll always runs. Without the
  hub it polls every 5 s, plus 1 s hot polls for bars a plugin marks urgent.
  `transport: auto` switches both ways and logs each switch.
- ProjectX market hub: SignalR JSON over `websockets` (`wss://rtc.topstepx.com/hubs/market`),
  quotes and trades only, ping 15 s, reconnect backoff 1-30 s, make-before-break token
  rotation. The URL (it carries the token) is never logged.
- `subscription: auto` probes `retrieveBars` with `live=true` and `live=false` at start,
  logs `subscription_probe` (bars, newest lag) and picks live when it returns bars.
- Skylit: the existing map feed (stream or polling, gamma + vanna) and optional dark-pool
  prints, only inside exchange sessions and `skylit.window`. A credit projection is logged
  at each day open.
- Recorder: `<recordings_root>/recordings/dataplane/<day>.jsonl.gz` per Globex day (bars,
  snapshots, prints, feed status), sampled quotes in `<day>.quotes.jsonl.gz`, closed bars
  in the bar store (`~/.skylit-fse/live-bars/<INSTR>/60s/<day>.parquet`, readable by a
  plugin's own tooling). Order per bar: record, bar store, then dispatch.
- Backfill: missing past days (default 20) go into the bar store before `on_start`.

### 24 h Globex host (`dayclock.py`, `host.py`)

- Globex day `D` = `[D-1 18:00, D 18:00)` NY. At 18:00 the host rotates recordings,
  reloads calendars, calls `on_day_close`, then flattens positions of plugins that may not
  hold through the break (backstop), then `on_day_open` of the next day. Weekends and
  holidays are handled by the same clock.
- Session-close flatten is plugin policy: Playbook 16:10,
  `template` at its `flatten_time`.
- Restart: `plugin.json` (book + plugin state) after each dispatch round; on start the
  host restores it, replays the recorded bars since, and calls `on_day_open` again
  (idempotent). Unusable state is renamed `plugin.json.bad-<stamp>`, the plugin starts
  fresh with a `restore_failed` block.

### Paper broker (`broker.py`, `sim/fills.py`)

- One `SimBook` per host-brokered plugin. `EnterMarket` is a market-entry bracket: it
  fills at the open of the bar after `placed_at` (± slippage); stop and targets are set
  from the unslipped open and checked from the fill bar on, stop first. This is covered
  by the market-bracket property tests. `stop_fill: at_stop` fills
  stops at the stop price, as NinjaTrader does.
- Per-plugin `fills` (trade-through, slippage, commission) and `risk` caps
  (`max_contracts`, `max_open_trades`, `max_daily_loss`, `max_trades_per_day`). The plugin
  reads them, from `__init__` on, as `services.fills` (`PaperFills`), `services.risk`
  (`PaperRisk`) and `services.broker_terms` (`BrokerTerms`: both in one flat, frozen view;
  `commission_rt` is read-only). All three are `None` for a self-brokered plugin.
  `max_daily_loss` is a day P&L halt **and** a per-trade stop-risk budget: a plugin that
  sizes by risk can size qty so `stop_ticks * tick_value * qty <= max_daily_loss + day_net`
  (and <= `max_contracts`), else skip; the paper/combine path rejects an oversized
  `EnterMarket` before ProjectX place.
- `EnterMarket.reference_t` (optional, raw ticks): the stop and targets are measured from
  this price (for example the signal bar's close) instead of the fill bar's unslipped open; the
  entry still fills at `open ± slippage`. If the fill bar's open or slipped fill is at or
  past the stop or a target, the entry is dropped with no fill (`expired`/`reference_gap`).
- `ModifyStop(tag, stop_price)` tightens the resting stop of `tag`'s open trade (breakeven,
  trailing). It applies from the next bar the book has not processed (last bar open
  + 60 s, `detail["effective_ns"]`), not from `ctx.now`, which is already past that
  bar's open in live; two modifies before that bar: the last wins. The same price is an
  accepted no-op (`unchanged: True`). Rejections (role `stop`): `not_open` (unknown tag,
  pending entry, closed or flattened trade), `invalid_order` (`stop_price` not an `int`),
  `stop_loosen` (lower for a long, higher for a short). Not gated by the halt file,
  blocks or caps. A stop at or through the market fills on the next bar by the usual
  `stop_fill` rule, then slippage. Targets and `Trade.initial_stop` (so R) are unchanged.
  In one return, `Flatten(next_open)` + `ModifyStop` lets the modified stop fill on the
  next bar before the market exit (that exit is placed at `ctx.now`); do not tighten the
  stop in the same hook if "exit next open" must win.

### Safety

- Paper by default: only `src/fse/live/host/live_orders.py` may import the broker
  modules, and only `host.py` and `commands/run.py` may import it; a static scan of
  `live/host`, `live/plugins`, `commands/run.py` and every `research/*/plugin*.py` (when
  present) enforces both (`tests/unit/test_runner_paper_only.py`). No broker
  adapter is built unless a plugin opts in (below), the start command has
  `--plugin-orders` and `FSE_RUN_PLUGIN_ORDERS=enabled` is set. The Playbook plugin
  faults unless its session is in Paper mode.
- AWS: the runner service runs a fixed `fse run` command (`docker/entrypoint.sh`,
  `runner` mode) with no `--plugin-orders`, and neither SSM nor the console's runner
  settings can add arguments to it (`tests/unit/test_aws_runner_paper.py` enforces the
  first). The image's `batch` and pass-through modes take arbitrary arguments and the
  runner role may `ecs:RunTask` the batch task with overrides; that path already allows
  `fse live` today and is an out-of-scope follow-up.
- Kill switches: root `HALT` / `fse halt` block every plugin; `fse halt --state-dir
  <live-state>/plugins/<id>` blocks one. Root block kinds the gate does not know fail
  closed (`root_block_unknown`). Plugin faults quarantine the plugin only. Console
  Practice switch: see "Kill switch scope" under "Console Practice orders (AWS)".
- `RunnerLock` on `<live-state>/runner.lock` is shared with `fse paper` and `fse live`.
- Credentials come from the environment only; a blank one exits 3 naming the variable,
  never its value.

### Order execution (opt-in)

- `plugins[].execution.order_mode`: `paper` (default), `practice` or `combine`. Only a
  host-brokered plugin whose `needs()` sets `DataNeeds.broker_orders=True` may be
  non-paper (no plugin in this copy declares it), and only one per config.
- Gates: `--plugin-orders` and `FSE_RUN_PLUGIN_ORDERS=enabled` (both, checked first),
  then the account rules of `fse live` (`PRACTICE_ACCOUNT_ID`; combine also
  `COMBINE_ACCOUNT_ID` and `--live-orders`). Any failure runs paper; the plugin's first
  card names each failed condition.
- The paper book stays the plugin's view and is the expected book a per-Globex-day
  `BrokerSafety` reconciles against (state in `<live-state>/plugins/<id>/`). Entries are
  market orders with a tick stop-loss and one tick take-profit bracket; exits are market
  orders sized on a fresh broker read, so an exit never opens a reverse position.
- The account must be dedicated to the plugin. See the README section "Paper by
  default, opt-in orders, and the kill switches" for the validation rules, kill
  switches and the practice-first procedure.

### Console Practice orders (AWS)

Practice first. Combine and `fse live` stay unavailable on AWS and in the console.

Precondition: the `fse-production` GitHub environment has a required reviewer and its deployment branches are limited to your release branch.

Turn on Practice for one broker plugin (a plugin you wrote):

1. Set the repository variable `FSE_PLUGIN_ORDERS_ENABLED=true`, then run the fse-infra workflow from your release branch with apply. Deploy order does not matter for safety: the runner stays paper until the new image and the new host control file `/run/fse-control/plugin_orders` both exist and the switch is enabled.
2. Live → Runner → Restart, so the host rewrites `/run/fse-control/plugin_orders`.
3. Settings → Secrets: set `PRACTICE_ACCOUNT_ID` (it must differ from `COMBINE_ACCOUNT_ID`), `PROJECTX_USERNAME` and `PROJECTX_API_KEY`.
4. Live → <plugin> → Order mode → Practice. Enter the max daily loss and max trades per day, then confirm the restart.
5. Check the plugin badge says `Practice in use`. `Requested practice, running Paper: ...` names each failed condition.

The TopstepX terms are already marked verified: `topstep_terms_verified_on` defaults to 2026-10-13 (`infra/variables.tf`); verify the terms yourself and set your own date, and the repository variable `FSE_TOPSTEP_TERMS_VERIFIED_ON` overrides it.

Caps: edits to `risk.max_daily_loss`, `risk.max_trades_per_day` or `hold_through_break` of a Practice plugin on the Configs page are saved without the Practice confirm and apply at the next runner start or restart. Change them through Live → <plugin> → Order mode → Practice to get the confirm.

### Plugins page (risk / fills) and backtest overrides

**Live edits:** Plugins → pick the runner config the Live runner uses → each host-brokered
plugin card exposes `fills` (`trade_through_ticks`, `slippage_ticks`, `stop_fill`,
`commission_rt`), `risk` (`max_contracts`, `max_open_trades`, `max_trades_per_day`,
`max_daily_loss`), and `hold_through_break`. **Save revision** writes
`configs/runner/<name>/current.yaml` (the same path `fse run` / SSM `/fse/runner_config`
reads). Restart the runner to load. Secrets stay on Settings → Secrets, not here.

**Save as…:** copies the YAML on screen to a **new** runner name without overwriting the
selected config. Use this to keep an experimental risk set beside prod.

**Backtest without touching prod:** Workflows → Backtest plugin. Optionally enable
**Use override settings for this run**, edit risk/fills per plugin, then run. The console
`POST /api/backtest-plugin/override-config` materializes a paperized copy under
`ui/uploads/<id>/runner.override.yaml` (one-shot) or a new `configs/runner/<draft>/`
name when **Keep as named draft** is on. The job's `--config` points at that copy. The
live/prod source SHA is unchanged — each combo is a separate job with its own override.
Do **not** hand-edit the Live runner YAML just to A/B a backtest.

Turn it off: Halt blocks new entries at once. An open position and its working brackets stay at the broker until the plugin exits them; to stop now, flatten in TopstepX. Then apply `false` and use Live → Runner → Restart (it refuses while a position or working order is visible).

Kill switch scope: The switch bounds the console application and its users: no console code path can enable plugin orders, Combine or `fse live`. It does not bound a principal holding the instance-role credentials. That principal can read the ProjectX key and account ids (ReadFseParameters) and can launch a batch task with command and env overrides (EcsRunBatch). This is pre-existing and accepted (decision c). The IAM narrowing only removes the `/secrets/*` write path.

## 2. Write a new plugin

`src/fse/live/plugins/template.py` runs as-is (one entry per day at `entry_time`, flatten
at `flatten_time`) and comments every hook. `configs/runner.yaml` has it as the enabled
`template` entry; `tests/unit/test_plugin_template.py` loads it through the loader and
runs it on the fake data plane.

1. Copy `template.py` into this Project (e.g. `research/<name>/plugin.py`), rename the class.
2. Declare `needs()`: bars, optional Skylit/quotes, `brokerage="host"`.
3. Fill the hooks: warm-up in `on_start`, reset in `on_day_open`, decisions in `on_bar`,
   fills in `on_order_update`, time exits in `next_wake`/`on_wake`, state in
   `snapshot`/`restore`.
4. Add a `plugins` entry (copy the `template` one: `module`, `config`, `fills`, `risk`).
5. Copy `test_plugin_template.py` for the new class, then `fse run --config configs/runner.yaml`.

## 3. Test coverage

Run with the engine venv: `pytest -q`, `ruff check src tests` and `mypy`.

- Playbook equivalence (`test_playbook_plugin_matches_live_runner.py`): the plugin under
  `fse run` writes the same decisions, state files, Finding_Cards, live log and recording as
  `fse paper`.
- Restart (`tests/unit/test_plugin_host_persistence.py`): fills are redelivered once and tags
  stay used across restarts and crashes.
- Template (`test_plugin_template.py`): loads through the loader, trades on the fake data
  plane (target and time exit), state round trip, the shipped entry is valid.
- Paper only (`test_runner_paper_only.py`): a static scan that no module outside the order
  gate imports the broker modules.

## 4. Run a plugin on paper

Start (credentials in the environment: `SKYLIT_API_KEY`, `PROJECTX_USERNAME`,
`PROJECTX_API_KEY`), from the engine dir:

```sh
fse run --config configs/runner.yaml                                    # runs until SIGINT
fse run --config configs/runner.yaml --until 2026-10-19T18:10:00-04:00  # or stop itself
```

## 5. Known gaps and first-day checklist

- ProjectX subscription: `subscription: sim` skips the probe for a login without a live data
  subscription (sim bars are real time). First-day checklist (host log
  `<out_root>/run-<stamp>-<hex8>/host_log.jsonl`):
  1. `subscription_probe` lines: both `succeeded`, `bars > 0`, and `newest_lag_ms` per
     subscription; `subscription` says which one was chosen (a `projectx_sim_subscription`
     warning means live returned nothing).
  2. `market_hub_connected` once, no repeating `market_hub_connect_failed`; `heartbeat`
     every 5 min with `transport: stream`, `connected: true`, `rt_dropped` near 0.
  3. Bar lag: `heartbeat.lag.MNQ.p50_ms`/`p95_ms`/`max_ms`. Expect p95 under about 3 s
     with the hub and under about 6 s polling; a plugin with a late-bar guard may skip
     near 20 s.
  4. A quote file `recordings/dataplane/<day>.quotes.jsonl.gz` that grows, and `bar`
     lines in `<day>.jsonl.gz` for every minute.
- The Playbook entry ships disabled. `configs/playbook_baseline.yaml` needs your
  `regime.min_abs_value` and fees before it loads.
- Account-level risk across plugins is opt-in: with no `accounts:` (the committed
  `runner.paper.yaml`) each plugin has only its own caps. A paper account (section 7) adds
  a shared position cap, MLL, DLL and flat-by deadline. Paper accounts are still a model of
  the prop-firm rules, not a broker gate: never route a paper config to a real account.
- The analytics journal (`trades.jsonl`) starts with this feature; earlier days are not on
  the Analytics page (section 7).
- Nexus mirror: whether Skylit Nexus offers MNQ and MES is unverified (no live check has
  been run). A ticker it does not offer is acked `failed` with `contract_not_offered`
  (section 7).

## 6. Paper forward test

Config `configs/runner.paper.yaml`: the plugins enabled in one `fse run`, each with its own
fills/risk block, paper book, state dir `<live-state>/plugins/<id>/` and out dir. It ships
with `template` enabled and `playbook` disabled; add your own plugins as
`research/<name>/plugin.py` entries. Paper only: the committed config has no paper account,
so the caps are per plugin; an account-level cap across plugins is opt-in through
`accounts:` (section 7). A plugin that needs a different Skylit view (layout, strikes,
expirations) needs the data plane's `skylit` block changed first.

### Start and stop

Preconditions, once: `chmod 600 .env` (the wrapper refuses any other mode) and the engine
`.venv` with `fse` installed. Run from the engine dir.

```sh
# by hand, in a terminal (Ctrl-C stops; state is kept)
scripts/run_paper.sh --until 2026-10-16T17:10:00-04:00

# unattended (launchd, restart on crash). Dry run first: prints the rendered plists.
scripts/launchd/install.sh --until 2026-10-16T17:10:00-04:00
scripts/launchd/install.sh --until 2026-10-16T17:10:00-04:00 --apply  # starts it
launchctl print gui/$UID/com.skylit.fse-run | head -30               # status
scripts/launchd/uninstall.sh --apply                                 # stops it
```

- `scripts/run_paper.sh` sources `.env` without echoing it and execs
  `.venv/bin/fse run --config configs/runner.paper.yaml "$@"`. It exits 78 before starting
  when `.env` is missing or not mode 600, `.venv/bin/fse` or the config is missing; it exits
  0 without starting when `--until` is already past. Logs: `~/.skylit-fse/logs/fse-run.out.log`
  and `fse-run.err.log`, rotated by size at each start (50 MiB, 5 kept); the host log stays
  in the run dir.
- `install.sh` renders `scripts/launchd/*.plist.template` with absolute paths into
  `~/.skylit-fse/launchd/` and runs `launchctl bootstrap gui/$UID`. It never writes to
  `~/Library/LaunchAgents`, so nothing starts at login. Default is a dry run.
- KeepAlive `SuccessfulExit: false`, `ThrottleInterval: 60`: launchd restarts the run after
  any non-zero exit, at most once a minute, and leaves it stopped after exit 0 (`--until`
  reached, or the SIGTERM from `uninstall.sh`). `SuccessfulExit` implies RunAtLoad, so
  `--apply` starts the run. A refusal (78) or a config/credential exit (2, 3) also repeats
  once a minute: read `fse-run.err.log` and run `uninstall.sh --apply`.
- Restart after a crash: the host restores each plugin's `plugin.json`, replays the recorded
  bars since and calls `on_day_open` again (section 1). Covered by
  `tests/unit/test_plugin_host_persistence.py` (fills are redelivered once, tags stay used).
  Each restart opens a new run dir.
- The runner lock cannot wedge on a stale pid: `RunnerLock` is `fcntl.flock(LOCK_EX |
  LOCK_NB)` on `<live-state>/runner.lock` (`src/fse/live/host/lock.py`); the kernel drops
  the lock when the process dies, and the pid in the file is only for the error message.
  `tests/unit/test_runner_lock.py::test_a_stale_pid_in_the_file_does_not_wedge_the_lock`
  and `::test_a_killed_holder_releases_the_lock` (SIGKILL, no cleanup) show it.

## 7. Paper accounts and analytics

Everything here is opt-in and paper only. A runner YAML without `accounts:` (every
committed config) runs exactly as before: each plugin has its own caps, its own book and
an implicit, unrestricted account `implicit:<plugin>` that exists only for display.

### Accounts in the runner YAML

```yaml
accounts:                                   # up to 8; ids ^[a-z][a-z0-9_]{0,31}$
  - id: topstep_a
    preset: topstep_50k_combine             # topstep_50k_combine | none
    rules:                                  # only what differs from the preset
      starting_balance: {value: "100000.00"}
      maximum_loss_limit: {value: "3000.00", mode: intraday_trailing}
      no_trade_windows:
        - {start: "09:25", end: "09:35"}
        - {start: "23:00", end: "01:00"}   # wraps past midnight
    nexus: {enabled: false}                 # section "Nexus mirror" below
plugins:
  - id: template
    account: topstep_a                      # optional; omitted = implicit account
    ...
```

- `rules` uses the Strategy_Config `account:` field names and nesting
  (`src/fse/config/schema/account.py`): `starting_balance.value`, `profit_target`,
  `maximum_loss_limit`, `daily_loss_limit`, `consistency_target.pct`,
  `position_cap.micro_equivalents`, `flat_deadline`, plus the paper-only
  `maximum_loss_limit.mode` / `trail_lock`, `flat_deadline_enabled`, `no_trade_windows`
  (`HH:MM` New York, half-open `[start, end)`, `end < start` wraps past midnight, equal or
  overlapping windows refused) and `min_trading_days`. Money is a decimal string with at
  most two places.
- The preset is deep-merged under `rules` at leaf level, so
  `maximum_loss_limit: {value: "2500.00"}` keeps the preset's `enabled` and `mode`.
  `topstep_50k_combine` is the `AccountConfig()` default (the Topstep 50K Combine).
  `none` turns every rule off and keeps $50,000.00 (overridable). Its disabled MLL has the
  value $0.01, because the "MLL below the starting balance" check runs even when the MLL is
  off; if you re-enable the MLL on a `none` account, set its value too (a $0.01 MLL is legal
  and breaches on the first losing tick).
- Sharing: several plugins may name the same account. Its balance, daily loss, MLL and
  position cap (micro-equivalents over every member's positions and working entries) are
  shared; each plugin keeps its own book and its own `max_contracts` / `max_daily_loss`.
- Only host-brokered plugins (with a `risk:` block) can have an account; `playbook` cannot.
- Validation runs in two stages. Field errors come first, each with its YAML path
  (`accounts[0].rules.maximum_loss_limit.value: …`). Once they are fixed, the
  cross-reference problems are reported together: duplicate id, unknown account, an account
  on a self-brokered plugin, and the Nexus netting rules. `fse run` refuses to start on
  either, and the Plugins page shows both kinds.
- Unused accounts are valid (`account_unused` in the host log).

### MLL modes

| `mode` | Floor |
|---|---|
| `eod_trailing` (default) | `start − mll`, raised at each 18:00 rotation to `max(start, highest end-of-day equity) − mll` |
| `intraday_trailing` | follows the best intrabar equity (open P&L included); never moves down |
| `static` | `start − mll`, never moves |

With `trail_lock: true` (default) the floor never rises above the starting balance. A bar is
checked against the floor before its own high can raise it. Both peaks are tracked in every
mode, so a mode change takes effect at the next start from the peak that mode uses.

### Breach behaviour

| Event | Status | New entries | Positions and working orders |
|---|---|---|---|
| MLL breach | `breached` until reset | refused, `account_breached` | flattened |
| DLL breach | `flat_locked` until the 18:00 rotation | refused, `account_daily_loss` | flattened |
| `flat_by` deadline | unchanged | refused, `account_flat_by`, until the rotation | flattened |
| No-trade window | unchanged | refused, `account_no_trade_window` | untouched |
| Position cap | unchanged | that entry refused, `account_max_position` | untouched |

- Flatten is a `next_open` market exit for every member through the normal fill model
  (slippage, commission), so the realized loss can go past the limit by the next bar's move.
  The backtest's `AccountSim` clamps the balance at the limit instead.
- Exits, stop modifies and cancels are never blocked.
- One transition logs exactly one `account_status` line and one `account_flatten` line,
  however many bars stay past the limit. A `flat_locked` account that then crosses the MLL
  logs one more pair (`flat_locked` → `breached`). `flat_by` logs one `account_flatten` per
  Globex day and no `account_status`.
- Refusals are logged in the plugin's `plugin_log.jsonl` as `intent_rejected` with the
  reason above.

### State, restart, rule changes, reset

- State: `<live-state>/accounts/<id>/account.json`, written before the plugins' state files
  so a crash between them never loses a trade (the replay's re-close is skipped by trade id).
  `plugin.json` is unchanged.
- Restart keeps the status: a breached or flat-locked account still blocks. A flat-locked
  account restarted on a later Globex day comes up unlocked; on the same day (even during the
  17:00-18:00 break) it stays locked until the 18:00 rotation. If the runner is stopped
  across an 18:00 rotation, the closing day's end-of-day equity is taken at the next start
  (from the last marks), so `eod_high` can differ from a run that saw the rotation live.
- An unreadable `account.json` is renamed `account.json.bad-<UTC>` and the account starts
  `breached` with `restore_failed` (entries refused, nothing flattened). Reset it.
- Changed rules apply at the next start and keep balance, peaks and status
  (`account_rules_changed`, with the old and new floor when the MLL changed; a new floor at or
  above equity breaches on the next bar). A changed `starting_balance` only takes effect on a
  reset (`account_start_changed`).
- Reset (the runner must be stopped):

  ```sh
  .venv/bin/fse account reset topstep_a --state-dir ~/.skylit-fse/live-state --reason "new combine"
  ```

  Exit 0 done, 2 bad input or no such account, 3 the runner is running, 1 an archive error.
  It moves the account's files to `accounts/<id>/archive/<UTC>/` and appends a line to
  `accounts/resets.jsonl`; the next `fse run` starts the account fresh from the current rules.
  The Analytics page has the same action (Reset… on a breached account, reason required;
  "Stop the runner first" while it runs).
- Deleting an account from the YAML leaves its state on disk. Re-adding the same id resumes
  that `account.json`; reset it first for a fresh start. Renaming an account in the YAML
  starts a fresh state under the new id, without the reset's archive or audit line.

### Trade journal

The host writes `<run dir>/plugins/<id>/trades.jsonl` for every host-brokered plugin (created
at startup, so its presence marks a journal-enabled run). One line per fill (`kind: fill`) and
one per exit leg of a closed trade (`kind: trade`), `v: 1`, money as plain decimal strings.

- `trade` lines carry `line_id` (dedup key), `trade_id` (groups legs), plugin, account (`null`
  for an implicit one), instrument, side, qty / `qty_at_entry`, entry and exit time and price,
  `gross`, `fees` (entry fees allocated pro rata, so legs sum to the trade), `net`, `r`,
  `exit_reason`, `entry_day`, `globex_day`, `day_source`, and the engine fills so the
  analytics rebuild engine `Trade`s.
- `exit_reason`: `stop`, `target`, the flatten reason (`account_mll`, `account_dll`,
  `account_flat_by`, `host_break_backstop`, `restart_stale` or the plugin's own), the
  `close_setup` prefix, else `market`. A pending flatten's reason survives a restart through
  `<live-state>/plugins/<id>/exit_reasons.json`; an exit that was working at a restart whose
  reason could not be restored is labelled `pending_exit_restart`.
- `globex_day` is never null. `day_source: "open"` is the plugin's Globex day at the close;
  `"after_close"` is a close in the weekend gap (a held position closed by `restart_stale` on
  a Saturday restart), dated to the last closed day and booked to no day's daily loss.
- A write failure logs `journal_write_failed` once per plugin and never affects trading;
  `journal_write_recovered` follows the next good line. The plugin CSVs are unchanged.

### Analytics page and Live

Console → Monitor → Analytics (`/analytics`): cards for the total, each account (named first,
then implicit) and each plugin with today's net, open P&L, cumulative net, trades and win rate
(net > 0); account cards add balance, equity, drawdown, distance to each limit and a text
status. A plugin's cumulative net is its journal trades since the forward-test start day; a named account's
is its own ledger since it started or was reset. Below: an equity chart with scope and date
range, the trades table with plugin/account/date filters (newest 500), a Nexus block per
mirrored account.
REST: `/api/paper/{plugins,accounts,total}/…` (trades, equity, metrics), behind the console's
normal auth. Open P&L is one computation on both pages: the Live page's live P&L
(`fse_ui.runner.live_pnl`: realized + ticks x tick value x qty - exit fee x qty at the
latest quote or 1-minute close in `marks.json`); a position without a mark shows no open
P&L. An account card's balance, equity and distance to limits are the account rule
engine's (`account.json`, valued at each 1-minute close before exit fees: what MLL and DLL
are checked on), so its equity can differ from balance plus its plugins' live open P&L.
The Live page's plugin card (`status.json`) adds the plugin's account, its status, the
balance and the contracts in use; the raw saved state stays behind Details. The status bar
reads `Paper idle`, `Paper · N breached`, `Paper · N accounts` or `Paper · N plugins`.

On Plugins, each host-brokered plugin has an Account select, and the Paper accounts panel adds,
edits (preset plus overrides; only overrides are written) and deletes accounts. Deleting a
referenced account moves its plugins to implicit accounts.

### Runs Report tab

`fse report` writes `<run>/report/report.{json,md}`; the Runs page finds the report there as
well as at the run root (the root wins), so the Report tab and metrics appear for those runs.

### Nexus mirror (off by default)

An account can mirror its trades to Skylit Nexus paper trading
(`https://app.skylit.ai/api/nexus/v1`). Nothing is sent unless `nexus.enabled: true` and
`SKYLIT_API_KEY` is set; without the key the mirror reports `disabled: no api key` and the
runner trades normally.

```yaml
    nexus:
      enabled: true
      account: practice          # practice | evaluation | funded | agent | env:<VAR>
      test_only: false           # true: every order is sent with "test": true (dry run)
```

- `account` is a Nexus account type or `env:<VAR>` (for example `env:SKYLIT_NEXUS_ACCT`),
  resolved at `fse run`; never write an account id in the YAML. A missing variable disables
  that mirror (`env var <VAR> not set`) and its queue waits for a start that has it.
- Netting: a mirrored account takes one enabled plugin, and two accounts cannot mirror to the
  same Nexus account (two `env:` refs that resolve to the same value disable both at start).
- Tickers are sent as the engine instrument (`MNQ`, `MES`, …). There is no `instrument_map`.
  Whether Nexus offers MNQ and MES is unverified; a ticker it does not offer is acked
  `failed` with `contract_not_offered` and shown on the Analytics page.
- What is sent: an entry fill → a market order; a partial exit → a `reduceOnly` order; the
  exit that flattens the plugin → a close of that ticker. Each order has a `clientOrderId`
  `fse:<day>:<plugin>:<tag>:<leg>`, so a resend after a restart is idempotent.
- Delivery never blocks trading: items go to `<live-state>/accounts/<id>/nexus_outbox.jsonl`
  and a background task sends them in order (≤ 5 per 5 s, ≤ 30 per 60 s). Unsent items are
  resent on the next start; an exit waits for its entry and is skipped if the entry failed.
- Errors: a bad key or access denied disables the mirror; `account_locked` stops opens until
  the 18:00 rotation (closes still go); `account_ended` stops the mirror; `market_closed`
  holds until the next open; transient errors retry with backoff for up to 23 h. Each result
  is a `nexus_result` host-log line, and `nexus_status.json` (queue depth, last success, last
  error, local vs Nexus net) feeds the Analytics page.
- A reset archives the account's outbox and status with `account.json`.
- Practice and combine plugins mirror too. Nexus gets the plugin's paper-book fills (the
  same fills its cards, analytics and account record), never the ProjectX fills, so Nexus
  prices are paper prices and can differ from the ProjectX account. The mirror cannot place,
  change or cancel a ProjectX order; a Nexus error, lock or hang never blocks, delays or
  changes a ProjectX order or exit, and a ProjectX problem never stops the mirror. At
  shutdown the broker exits are sent before the ≤ 2 s Nexus drain.
