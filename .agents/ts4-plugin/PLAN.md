# TS4 as a plugin-engine plugin: plan (re-grounded on the committed API)

Worktree (absolute): `/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-plugin`
(branch `kiro/ts4-plugin`, HEAD `be9718a`, the same commit as `kiro/plugin-runner`; fast-forwarded
by the setup step). Relative paths land in the parent workspace, so always use absolute paths or `git -C`.
Engine dir `E` = `<worktree>/members/alantiix/skylit-futures-strategy-engine`.
Reviewer verdict file of the implement-and-review loop:
`/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-plugin/.agents/ts4-plugin/review.json`
(`{"verdict": "APPROVED" | "CHANGES_REQUESTED", ...}`).

## Status: implemented, uncommitted (iteration 1, 2026-10-09)

Items 0-5 are done in the worktree and left **uncommitted** (this step's instruction overrides
the per-item commits below). Evidence: `verification.md`; summary: `REPORT.md` (both next to this
file). Full-year real-data parity: ts4 880/880, ts4_v2 959/959 exact at zero cost.

As-built deviations from the plan text below (the plan text is kept for the reviewer):

- Layout: the plugin is the subpackage `E/research/ts4/plugin/` (`__init__.py` exports
  `Ts4Plugin`; `strategy.py` = the plan's `plugin.py`, `rules.py` = `live_rules.py`,
  `warmup.py` = the warm-up job, `paper_io.py`, `README.md`), so the plugin package holds live
  code only and research-only modules sit outside it. Module path unchanged:
  `research.ts4.plugin:Ts4Plugin`. Its tests and the replay/parity harness are in
  `E/research/ts4/plugin/tests/` (`replay.py`, `realdata.py`, `test_*.py`).
- `ts4_config` takes the preset names `ts4` / `ts4_v2` (or a path); default `ts4`.
- Calendars (`load_matrix_calendars`, repo YAML, milliseconds) load in the constructor;
  sessions are built lazily per Globex day. No calendar background task.
- Extra start check: YAML `costs.*` and `exits.target_trade_through_ticks` must be 0 (the runner
  fills block is the cost model).
- Skip reasons as built: `no_bar`, `catch_up`, `missed_start`, `late_bar`, `no_calendar`,
  `no_session`, `session_end`, `position_open`, `blocked`, `unknown_contract`, `ema_not_ready`,
  `qty_zero`, `rejected:<r>`, `expired:<r>`, `cancelled:<r>`.
- The EMA lives in the research's back-adjusted frame (adjusted close = `c_t + offset`), not a
  shifted raw frame: bit-equal to the research EMA across rolls without a shift step.
- Real-data parity (item 4 D) covers the full in-sample year 2025-10-13..2026-10-02 (four rolls),
  with the plugin's own 60-session cache warm-up, instead of January 2025.
- Multi-strategy: ts4 + ts4_v2 + the committed `RecordingPlugin` (opposing short); no other
  strategy plugin is committed at `be9718a`.
- `kiro/plugin-runner` moved to `f36b6ed` after setup (FEAT-004 `fse run`, Matrix and Playbook
  plugins, `PluginServices.fills`). This worktree stays at `be9718a` (no rebase); follow-ups in
  `REPORT.md`.

## Status before implementation: unblocked

Checked 2026-10-08 against the committed code in `E` (identical to the plugin-runner worktree):

| piece | state |
|---|---|
| `fse.live.plugins` (`api.py`, `__init__.py`, `loader.py`) | committed (FEAT-003, `be9718a`) |
| `fse.live.host` (`host.py`, `broker.py`, `config.py`, `dayclock.py`, `gate.py`, `lock.py`) | committed |
| test fakes `tests/fakes/{dataplane,plugins,clock}.py` | committed |
| `fse run` CLI, `LiveBarStore`, real data plane (FEAT-004) | **not committed**. The plugin is driven through `PluginHost` + the fakes |
| reference plugin `research/matrix/plugin.py` (FEAT-005) | not written. No in-repo pattern; follow `tests/fakes/plugins.py` |

Baseline in `E` (2026-10-08): `T4` 198 passed (4 deselected), host suites (`U`) 128 passed.
`ruff`/`mypy` findings exist only in the research-only `research/ts4/handoff/ts4_reference.py`,
so `L` and `M` below exclude `handoff/`.

Option B (user decision) removes the old blocker: `ts4_v2` ships with breakeven and trailing
**off**, so no stop-modify intent is needed.

## Hard rules for the implementer

1. **Do not modify any plugin-engine core file**: nothing under `E/src/fse/live/**`, and not
   `E/tests/fakes/**` or `E/tests/unit/test_plugin_*`/`test_runner_*`. If something core is
   missing, write it into section (f) as a proposal for the plugin-runner agent and work around
   it inside the plugin.
2. The replay/parity harness lives in `research/ts4/tests/`, not in engine core.
3. The plugin imports the host API **only** from `fse.live.plugins` (its docstring says
   "plugins import only from here"). Never `fse.live.host.*` in `plugin.py`/`live_rules.py`/
   `paper_io.py`. Tests may import `fse.live.host.*` and `tests.fakes.*`.
4. Research-only modules stay out of the plugin package and out of commits (see (c)).
5. No push, no stash, no `git add -A`. Stage files by name. Never stage `.agents/`, `.venv/`,
   `ts4_Trades.csv`, `nt_trades_parsed.csv` (trade prices) or any research-only file.

## Verification commands (from `E`, `V=.venv/bin`)

- `T4`: `PYTHONPATH=$PWD/src:$PWD $V/python -m pytest research/ts4/tests -q -p no:cacheprovider`
- `R`: `PYTHONPATH=$PWD/src:$PWD $V/python -m pytest research/matrix/tests research/range/tests -q -p no:cacheprovider`
- `L`: `$V/ruff check research/ts4 --extend-exclude research/ts4/handoff && $V/ruff format --check research/ts4 --extend-exclude research/ts4/handoff`
- `M`: `PYTHONPATH=$PWD/src:$PWD $V/mypy research/ts4 --exclude '^research/ts4/handoff/'`
- `U`: `PYTHONPATH=$PWD/src:$PWD $V/python -m pytest tests/unit/test_plugin_api.py tests/unit/test_plugin_loader.py tests/unit/test_plugin_paper_broker.py tests/unit/test_plugin_host_dispatch.py tests/unit/test_plugin_host_persistence.py tests/unit/test_plugin_host_rotation.py tests/unit/test_runner_config.py tests/unit/test_dayclock.py tests/unit/test_entry_gate.py tests/unit/test_runner_lock.py tests/unit/test_runner_paper_only.py -q -p no:cacheprovider`
  (must stay at 128 passed; proves core is untouched)
- `LC` (optional, needs the Operator's `~/.skylit-fse/cache`): `T4` with `-m localcache`.

## (a) The committed plugin API (confirmed against the code)

- **Base class** `StrategyPlugin(ABC)`, `api_version: ClassVar[int] = PLUGIN_API_VERSION` (1).
  `__init__(plugin_id, config: Mapping[str, object], services: PluginServices)`; only `needs()`
  is abstract. Hooks (all return `Sequence[Intent]` unless noted):
  `on_start(ctx)`, `on_day_open(day: GlobexDay, ctx)`, `on_bar(bar, received_ns, catch_up, ctx)`,
  `on_snapshots`, `on_dark_pool`, `on_quote`, `on_order_update(update, ctx)`,
  `on_feed_status(feed, up, detail, ctx)`, `next_wake(now) -> Instant | None`,
  `on_wake(now, ctx)`, `urgent_bar_closes(day) -> Sequence[tuple[str, Instant]]`,
  `on_day_close(day, ctx)`, `snapshot() -> JsonValue`, `restore(state)` (raise `ValueError` if
  unusable), `on_stop(ctx) -> None`. An uncaught exception faults the plugin for the process
  life. Hooks must return in < 1 s (`hook_latency` warning).
- **Intents** (`type Intent = EnterMarket | EnterResting | Flatten | Cancel`; there is no ModifyStop):
  - `EnterMarket(tag, instrument, direction, qty, stop_ticks, targets: tuple[TargetLeg, ...], placed_at, exact_bar_open=False, freeze_from_ns=None)`
  - `TargetLeg(role: "tp1"|"tp2", qty, ticks)` (ticks from the fill bar's **unslipped** open)
  - `Flatten(tag | None, reason [a-z_]{1,32}, price: "last_close"|"next_open", stamp_ns=None)`
  - `Cancel(tag)`, `EnterResting(...)` (unused by TS4)
- **Broker routing checks** (`broker.py`, in order): `bad_tag` (`TAG_RE ^[A-Za-z0-9_.-]{1,48}$`),
  `duplicate_tag` (working/open/used this Globex day; `used_tags` reset at day start),
  `instrument_not_declared`, `no_globex_day`, `stale_placement` (`placed_at` outside
  `[now-60s, now]` or `<=` the book's `last_bar_open`), `bad_freeze`
  (`freeze_from_ns < placed_at + 60s`), blocks, caps (`max_daily_loss`, `max_trades_per_day`,
  `max_open_trades`, `max_contracts`), `invalid_order`. `now` is `clock.now()` at dispatch, so
  a catch-up bar delivered > 60 s after its close can never enter.
- **Fills** (`fse.sim.fills`): market entry at `open ± slippage`; stop and targets live from the
  fill bar; a bar meeting stop and target fills **only the stop** (stop-first, not configurable);
  targets need `trade_through_ticks` beyond the limit; `stop_fill: gap_open` fills at the open
  when the bar opens past the stop. `Flatten last_close` closes at the newest ring bar with
  `close_ns <= stamp_ns`, **no slippage**. Setups with `freeze_from_ns` see no bar opening at or
  after it.
- **Updates** `OrderUpdate(tag, status, reason, fill, role, trade, bar, at, detail)`, statuses
  `accepted|rejected|filled_entry|filled_exit|closed|expired|cancelled`. On `closed`, `trade` is
  `fse.engine.types.Trade` (`entry_fill`, `exits`, `initial_stop`, `net: Decimal`, ...).
- **`PluginServices`** attributes: `writer`, `clock`, `calendar_dir`, `state_dir`, `out_dir`
  (`<run>/plugins/<id>`), `project_dir`, `bar_store: BarStoreView` (`root`, `bars(instrument, day)`),
  `cache_dir`, `env`, `http`, `skylit_levels`, `skylit_window`, property `calendars`, methods
  `run_blocking(name, fn, *args)` and `spawn(name, coro)`; each finished task gets one `on_wake`.
  **No `fills`/`risk` attribute**: the plugin cannot read its runner blocks (f.2).
- **`PluginContext`**: `now`, `position(instrument)`, `open_tags()`, `working_tags()`,
  `entries_blocked()`, `log(entry)` (appends to `<out_dir>/plugin_log.jsonl`).
- **`DataNeeds(bars: tuple[BarNeed, ...], hours: "eth"|"rth", skylit=None, quotes=False, brokerage="host", hold_through_break=False)`**,
  `BarNeed(instrument, interval_s=BAR_INTERVAL_S)`, `BAR_INTERVAL_S = 60`.
  `needs_problems` for a host-brokered plugin: every `BarNeed.instrument` in
  `data_plane.instruments` (default `MNQ, MES`), `interval_s == 60`, a `risk` block present,
  `fills.commission_rt` has every BarNeed instrument. Effective `hold_through_break` =
  needs AND YAML (`PluginEntry.hold_through_break` defaults to `True`).
- **Loader**: `module: "pkg.mod:Class"` (`MODULE_RE`); `project_dir` goes to `sys.path[0]`;
  the imported module file must lie inside `project_dir` or the `fse` package.
  `research/ts4/plugin.py` lies inside `E` (= `tests.fakes.dataplane.PROJECT_DIR`). A plugin
  constructor exception becomes `PluginLoadError` (exit 2), which is where start checks go.
- **Host** (`PluginHost`): per bar, the plugin's book processes the bar, then `on_order_update`s,
  then `on_bar`. Startup: restore, `on_start`, catch-up replay (`catch_up=True`), stale flatten,
  `on_day_open(today)`. At `rotate_ns` (18:00 NY): `on_day_close`, then the backstop
  `Flatten(None, "host_break_backstop", "last_close")` if `hold` is false. `plugin.json`
  persists `{"book", "plugin": snapshot()}` after every dispatch step that touched the plugin.
- **Test driver** (`tests/fakes/dataplane.py`): `FakeDataPlane(clock, script)` with
  `(instant, BarsEvent(bars, received_ns))` pairs, `MemoryBarStore`, `make_host(tmp_path,
  classes, plugins, clock, dataplane, ...)` (uses `InlineBlockingRunner`, calendars loaded for
  today-3..today+7), `host_entry(id, **extra)`, `make_bar(instrument, open_ns, o_t, h_t, l_t,
  c_t, contract=None)` (contract defaults `MNQZ6`), `runner_config`, `ny(d, hh, mm)`,
  `read_jsonl`. Drive with `FakeClock(start)` and `await clock.run(host.run(until_ns=...))`
  under `@pytest.mark.asyncio`.

## (b) TS4 rules mapped onto the hooks

| TS4 rule (`research/ts4/sim.py`) | plugin implementation |
|---|---|
| bars | `needs() = DataNeeds(bars=(BarNeed(cfg.instrument.symbol),), hours="eth", brokerage="host", hold_through_break=False)`. 1m bars only; the 30s EMA(692) becomes EMA(346) through the live overlay (d). |
| calendars | `on_start` starts `run_blocking("calendars", ...)` that builds `research.matrix.clock.GlobexSessions.build(load_matrix_calendars(symbol), today-3, today+10)` (17:00 NY, early closes and halts from `globex_hours.yaml`). Rebuilt from `on_day_open` when coverage ends within 3 days. The end date is clipped to the calendar covers (the repo calendars end 2026-12-31; a day outside them skips `calendar_not_ready` and logs an error, README says to extend `calendars/`). Until ready: skip `calendar_not_ready`. |
| slot instants | `on_day_open(day)`: for each enabled slot and each clock date in `{day-1, day}`, `T = datetime.combine(date, slot.time, tz)` with tz `research.ts4.market._ZONES[cfg.clock]` (`Etc/GMT+4` or `America/New_York`). Keep `T` if `day.open_ns < T <= day.rotate_ns`. Key `f"{slot.name}@{T}"`; existing states (restart) are kept. `urgent_bar_closes(day)` returns `(symbol, T)` for each. |
| signal bar | `on_bar` with `bar.close_ns == T` of a `queued` slot. Decided once. Skip reasons, in order: `catch_up`, `late_bar` (`received_ns > T + late_bar_s`), `session_end` (`bar.close_ns == sessions.close_ns(session)`), `position_open` (`ctx.open_tags()` or `ctx.working_tags()` non-empty), `blocked` (`ctx.entries_blocked()`, detail = reasons), `calendar_not_ready`, `ema_not_ready`, `unknown_contract`, `qty_zero`. A slot with no bar at `T` is marked `no_bar` at the next bar after `T`. |
| EMA side | adjusted close `x = (bar.c_t + offset_ticks[bar.contract]) * tick_size`, offsets from the range config's `price_adjust.offsets` (`research.ts4.market.range_config_path`), the same arithmetic as `sim.SimData.closes_pts`. Incremental EMA with `research.matrix.ema._coefficients(n)` (`v = x*k1 + k2*v`, seeded at the first value), bit-equal to `ema_series`. `side = long if (x > ema) == buy_above else short`; EMA disabled -> long. On a contract change, both contracts known: shift the stored EMA by the offset difference (EMA is linear). Unknown contract: `unknown_contract` skips and an error log once. |
| EMA warm-up | `warmup_sessions > 0`: `run_blocking("warmup", ...)` loads `market1m.load_market_1m(cfg, first, last, extra_bars=bar_store.root if it exists)` over the previous `warmup_sessions` Globex sessions and returns plain closes + last open_ns. Bars received while pending are buffered and applied after the seed (bars with `open_ns <=` the seed's last are dropped). `warmup_sessions = 0`: seed from the first live bar (tests, parity). A restored EMA whose last bar is in the current or previous Globex day is kept; otherwise re-warm. |
| sizing / target / progressive | `stop = base + add`, capped at `floor(base*cap_mult)` when set; `qty = floor(max_loss / (sizing_stop * tick_value))` (`research.matrix.sizing.qty_for` for `sizing: base`), clipped by `cfg.risk.max_contracts` when > 0; `target = target_ticks_for(base, target_factor)`: 39/30/30/35 (ts4), 58/45/45/53 (ts4_v2). Progressive state `(add, add_date)`: at a signal on label date `D`, `add_date != D` -> `add = 0`. On `closed` of a trade with key date `K` (`day_key: entry` -> entry label date, `exit` -> exit label date): `K != add_date` -> `add, add_date = 0, K`; then `add += target` if raw gross > 0 else `add = 0`. Raw gross = `(raw_exit - entry_fill.price) * side` with `raw_exit` = target level / stop level (or the gap open) / the session-close bar's `c_t` (the sim's `raw_exit` relative to the slipped entry). |
| fill = next bar open | `EnterMarket(tag=f"{slot.name}-{D:%Y%m%d}-{hhmm}", symbol, direction, qty, stop_ticks=stop, targets=(TargetLeg("tp1", qty, target),), placed_at=T, exact_bar_open=False, freeze_from_ns=session_close_ns)`. `exact_bar_open=False` = "the next existing bar", as in the sim. |
| breakeven / trailing | **Not implemented** (no ModifyStop, Option A deferred). Start check (d): BE/trail must be disabled, or provably inert. |
| session-close exit | `on_bar` with an open tag and `bar.close_ns == sessions.close_ns(session of the trade)`: `Flatten(tag, "session_close", "last_close", stamp_ns=bar.close_ns)`. Fallback `next_wake = close + 90 s` -> the same flatten from `on_wake`. Last resort: the host's 18:00 backstop. |
| updates | `accepted`: slot `accepted`. `rejected`/`expired`/`cancelled` (before a fill): skip row `rejected:<reason>` etc., progressive unchanged. `filled_entry`: record fill price, ref open `update.bar.o_t`, fill bar open_ns. `closed`: reason from the closing role (`stop` -> `stop`, `tp1` -> `target`, `exit` -> the plugin's pending flatten reason, else `host_flatten`); `gap_fill` = stop exit whose bar opened past the stop level; net cents = `trade.net * 100`; update progressive; append the row; rewrite outputs. |
| restart | `snapshot()` (version 1): `add`, `add_date`, slot states, open-trade bookkeeping by tag, EMA `{value: float.hex(), last_open_ns, contract, ready}`, pending flatten reasons, and the rows of the last 3 label dates. `restore` raises `ValueError` on another version or shape. Rows are keyed by tag, so a restart never writes a row twice. |
| outputs | under `services.out_dir/<label date D>/`: `trades.csv` (`paper_io.PAPER_TRADE_HEADER`), `skips.csv` (`label_time, slot, reason, detail`), `config.resolved.yaml` (`dump_config` of the effective config, written at start into `services.out_dir`). Whole-file rewrites via temp + `os.replace`. Events go to `ctx.log` (`plugin_log.jsonl`). |

Decision: `paper_io` writes its own `PAPER_TRADE_HEADER` (a subset of `sim.Trade` field names
plus `tag`: `tag, label_time, slot, side, qty, entry_t, target_t, stop_t, exit_t, stop_ticks,
target_ticks, add_ticks, reason, gap_fill, entry_open_ns, exit_close_ns, exit_label_time,
signal_close_pts, ema, gross_ticks, net_cents, contract`) instead of `sim.Trade`, because the
sim's bar-index fields have no live meaning. The parity tests compare exactly these shared fields.

Decision: outputs go to the host-given `services.out_dir`; the old `out_root` key is dropped, so
the host owns every path and tests write only under `tmp_path`.

**Known live-vs-backtest differences** (documented in the plugin README, not "fixed"):
- 1m bars and EMA 346 instead of 30s and 692.
- Stop-first on ambiguous bars (host rule). The frozen `ts4.yaml` says `target_first`; the overlay
  sets `stop_first`. Expected economics are the research's "1m stop-first" rows.
- Brackets from the unslipped open (NT uses the slipped fill). With `slippage_ticks: 1` the
  bracket sits 1 tick from NT's.
- Session-close flattens get no slippage in the host.
- Session end comes from the calendar (17:00 NY / early close / halt), not the sim's 15-minute
  data-gap rule. They differ only if a session has an intraday data gap > 15 min.

## (c) Package layout and module reuse (unchanged scope)

The plugin lives inside its research package as `research/ts4/plugin.py`.

| module | role | committed |
|---|---|---|
| `config.py` | reused as is (`Ts4Config`, `load_config`, `apply_overrides`, `dump_config`, `config_sha256`) | yes |
| `paths.py` | reused (`PACKAGE_DIR`, `DEFAULT_CONFIG`, `RANGE_CONFIG`, `MATRIX_CONFIG`, `default_data_cache`) | yes |
| `market.py` | reused (`_ZONES`, `range_config_path`) | yes |
| `market1m.py` | adapted: `load_market_1m(..., extra_bars: Path \| None = None)` passed to `load_series` (default unchanged) | yes |
| `sim.py` | reused for tests and the row arithmetic reference; the plugin never runs its loop | yes |
| `__init__.py`, `ts4.yaml` (frozen), `ts4_v2.yaml` (Option B) | configs | yes |
| `tests/{__init__,synth,test_config,test_market,test_market1m,test_progressive,test_sim_rules,test_no_network_imports,test_no_pandas}.py` | keep the reused modules tested | yes |
| new: `live_rules.py`, `plugin.py`, `paper_io.py`, `README.md`, `E/configs/runner.ts4.example.yaml`, new tests | the plugin | yes |
| `nt.py`, `infer.py`, `validate.py`, `research.py`, `variants.py`, `stats.py`, `report.py`, `run.py`, `handoff/`, `results/`, `*.md` except `README.md`, `*.csv`, `review.*`, `plan.md`, `verification.md`, and their tests (`test_cli, test_infer, test_localcache, test_nt, test_reference_parity, test_research, test_stats, test_variants, test_variants_sim`) | research only: out of the plugin, never staged | no |

`plugin.py`, `live_rules.py` and `paper_io.py` must not match
`fse\.skylit|fse\.projectx|fse\.data\.puller|httpx|dotenv|\.env`, must not import
`research.ts4.run` (it installs netguard, which would kill the host feeds), and must not import
any research-only module.

Decision: the plugin README is `research/ts4/README.md`, not a section of the engine README, so
it ships with the plugin and does not collide with the plugin-runner agent's README edits.

## (d) Configs and plugin params

Plugin `config` block, pydantic `Ts4PluginConfig` (`extra="forbid"`, `strict`, frozen):

| key | default | rule |
|---|---|---|
| `ts4_config` | `research/ts4/ts4.yaml` | resolved against `services.project_dir` when relative; must load with `load_config` |
| `overrides` | `{}` | `apply_overrides` after the overlay (user wins) |
| `late_bar_s` | 20 | 5-55 |
| `warmup_sessions` | 10 | 0-60 (0 = seed from the first live bar) |

Effective config = YAML, then the live overlay, then `overrides`. Overlay (only for a 30s YAML):
`data.bars: 1m`, `ema.period: round_half_up(period / 2)` (692 -> 346, `variants.EMA_1M`),
`exits.ambiguous: stop_first`. The overlay, the inert-BE/trail rewrite and `config_sha256` are
logged at start; the effective config is written to `config.resolved.yaml`.

Start checks (constructor, `ValueError` -> `PluginLoadError`, exit 2):
- `exits.ambiguous` must be `stop_first` after overrides (the host is stop-first).
- `exits.resolution != "tick"`; `entry.limit.slots == []`; `entry.split_offsets_min == [0]`.
- slot names match `^[A-Za-z0-9_.]{1,16}$` (tag-safe).
- **BE/trail**: if `breakeven.enabled` or `trailing.enabled`, they must be inert:
  `max(target_ticks of enabled slots) + 1 <= min(enabled trigger_ticks)`. Then the overlay
  turns them off in the effective config and logs `be_trail_inert`. Otherwise refuse:
  "breakeven/trailing can move the stop; the plugin API has no ModifyStop (Option A, deferred)".
  Why inert: a bar closing >= trigger ticks in profit has a high (low) >= target + 1, so with a
  host `trade_through_ticks <= 1` the target (or, stop-first, the stop) exits on that bar before
  any move takes effect. Frozen `ts4`: 39 + 1 = 40 <= 40, inert. README: run TS4 with
  `fills.trade_through_ticks <= 1` (the plugin cannot read it, f.2).

Configs:
- `ts4.yaml`: frozen original, **unchanged**. BE/trail enabled in the file, inert by the rule
  above, so it runs with BE/trail off. This is **verified by parity in item 4**, not assumed,
  and stated in the README.
- `ts4_v2.yaml`: **Option B = research variant `c1b_nobe`**: `clock: ny`, stops 232/180/180/210,
  targets 58/45/45/53 (derived), qty 5/6/6/5 (derived), progressive on,
  `breakeven.enabled: false`, `trailing.enabled: false`. Expected OOS $17.11/trade (research,
  1m stop-first, realistic costs). Parity reference is `c1b_nobe`, **not** `c1b`.
- TODO (Option A): `ts4_v2_be.yaml` (BE 40/4, trail 40/30 step 4, = `c1b`, OOS $18.25/trade),
  only after the plugin-runner agent ships ModifyStop (f.1). Not created now.

Runner example `E/configs/runner.ts4.example.yaml` (research "realistic" fills):

```yaml
version: 1
data_plane: {instruments: [MNQ, MES]}
plugins:
  - id: ts4
    module: research.ts4.plugin:Ts4Plugin
    config: {ts4_config: research/ts4/ts4.yaml}
    fills: {trade_through_ticks: 1, slippage_ticks: 1, stop_fill: gap_open, commission_rt: {MNQ: "1.48"}}
    risk: {max_contracts: 10, max_open_trades: 1, max_trades_per_day: 4}
    hold_through_break: false
  - id: ts4_v2
    enabled: false
    module: research.ts4.plugin:Ts4Plugin
    config: {ts4_config: research/ts4/ts4_v2.yaml}
    fills: {trade_through_ticks: 1, slippage_ticks: 1, stop_fill: gap_open, commission_rt: {MNQ: "1.48"}}
    risk: {max_contracts: 6, max_open_trades: 1, max_trades_per_day: 4}
    hold_through_break: false
```

`max_contracts` >= the largest qty: 10 for frozen (T2/T3 at 120 ticks), 6 for v2. `ts4_v2` ships
disabled only to avoid doubled MNQ exposure with `ts4` (e.2); it is not blocked. The clock
warning stays: frozen `ts4.yaml` is `clock: utc-4`, so outside DST (from 2026-11-01) "09:45"
fires at 08:45 ET and 18:35 falls in the break. `ts4_v2.yaml` is `ny`.

## (e) Multi-strategy coexistence

Host-provided isolation: each host-brokered plugin has its own `PluginPaperBroker` (book, fills,
caps, Globex-day P&L, `plugin.json`), client ids `f"{plugin_id}:{tag}:{leg}"`, and
`ctx.position()` sees only its own book. Shared: one data plane, one dispatcher (a slow hook
delays everyone), the halt file / `EntryGate` blocks, the rotation backstop.

Conflicts:
1. Same instrument, opposite positions (Matrix and TS4 on MNQ). Fine in paper; would net at a
   real broker. Out of scope (paper only).
2. `ts4` and `ts4_v2` together take the same side at the same instants: doubled exposure. Run one,
   or treat it as a paper A/B.
3. No account-level cap: `max_daily_loss`/`max_contracts` are per plugin (f.5).
4. Same urgent bar wanted by several plugins: the host sorts and de-duplicates `set_urgent`.

## (f) Core-change proposals for the plugin-runner agent (not made here)

1. **`ModifyStop(tag, stop_price: Ticks)` intent (Option A, deferred, wanted later).** Replace the
   resting stop's price from the next bar, reject loosening (`rejected: stop_loosen`), report
   `accepted` with role `stop`. About 40 lines in `broker.py` plus a `SimBook.replace_stop` helper
   and tests. Once it lands: add `ts4_v2_be.yaml` (BE 40/4, trail 40/30 step 4), a pure
   `live_rules.trail_step` extracted from `sim._scan_exit` (sim untouched), the BE/trail path in
   `plugin.py`, and parity vs `c1b`.
2. **`services.fills: FillsBlock | None` and `services.risk: RiskBlock | None`** (read-only), so
   the plugin can assert `max_contracts >= max qty`, `trade_through_ticks <= 1` for the inert
   rule, and log the cost model.
3. `FillsBlock.ambiguous: stop_first | target_first` (default `stop_first`): would let the frozen
   TS4 run its own rule.
4. `EnterMarket.levels_from: "open" | "fill"` (default `open`): NT-style brackets from the
   slipped fill.
5. `BarNeed.interval_s = 30` support: TS4's native resolution. Low priority (1m validated).
6. Optional account-level risk group across plugins.

## Implementation plan

Each item ends with a local commit (stage by name; message `feat(ts4): ...`/`test(ts4): ...`).
**Superseded for this run:** the workflow step said to leave all changes uncommitted, so no commits were made.
`U` must stay green after every item.

- [x] 0. Confirm the base. No code.
      `git -C <worktree> log -1 --format=%h` is `be9718a`; `git -C <worktree> status --short`
      shows only `.agents/` and `research/ts4/` untracked; `from fse.live.plugins import
      StrategyPlugin, EnterMarket, TargetLeg, Flatten` imports.
      Files: none. Verify: `U` 128 passed; `T4` 198 passed; `R` passes.

- [x] 1. Commit the reused TS4 core, switch `ts4_v2.yaml` to Option B, add `extra_bars`.
      a) `ts4_v2.yaml`: set `breakeven.enabled: false`, `trailing.enabled: false`; rewrite the header
         comment (Option B = `c1b_nobe`, OOS $17.11/trade; `ts4_v2_be` TODO).
      b) `market1m.load_market_1m`: keyword `extra_bars: Path | None = None`, passed to `load_series`.
      c) `tests/test_no_network_imports.py`: skip the `run.py` test when `run.py` is absent.
      d) `tests/test_config.py`: new test: `ts4_v2.yaml` equals `ts4.yaml` with `clock: ny`, stops
         232/180/180/210, BE/trail disabled, all else equal (explicit values, no `variants` import).
      e) Untracked research test `tests/test_variants.py::test_ts4_v2_yaml_is_the_c1b_variant_on_the_ny_clock`:
         compare against `VARIANTS["c1b_nobe"]` and rename it `..._c1b_nobe_variant_...` (not committed).
      f) `tests/test_market1m.py`: a test that `extra_bars` reaches `load_series` (monkeypatch
         `research.ts4.market1m.load_series`, assert the kwarg).
      Commit: `research/ts4/{__init__,config,paths,market,market1m,sim}.py`, `ts4.yaml`, `ts4_v2.yaml`,
      `tests/{__init__,synth,test_config,test_market,test_market1m,test_progressive,test_sim_rules,test_no_network_imports,test_no_pandas}.py`.
      Verify: `T4` passes (199+), `L`, `M`, `U`.

- [x] 2. `research/ts4/live_rules.py`: host-free pure logic, plus tests. Depends on 1.
      Contents: `Ts4PluginConfig` (d); `live_overlay(cfg) -> (Ts4Config, list[str])`;
      `be_trail_inert(cfg) -> bool` and `effective_config(plugin_cfg, project_dir) ->
      (Ts4Config, notes)` with every start check in (d) raising `ValueError`;
      `slot_instants(cfg, day_open_ns, rotate_ns) -> list[SlotInstant(key, slot, T_ns, label_date)]`;
      `Ema` (seed, update, contract shift, `to_json`/`from_json` with `float.hex`);
      `Progressive` (`at_signal(date)`, `on_close(key_date, target, won)`, JSON);
      `Sizing.for_slot(cfg, slot, add) -> (stop, target, qty)`; `make_tag(slot, T, label_date)`.
      Tests `research/ts4/tests/test_live_rules.py`: incremental EMA equals `ema_series` bit for bit
      on 3 random seeds; contract shift equals `ema_series` on the shifted history (within 1e-9);
      slot instants for `utc-4` and `ny` on 2026-03-06/09 and 2026-10-30/11-02 (DST edges), the
      18:35 slot lands in the next Globex day, Sunday open day; progressive reproduces the
      `test_progressive.py` scenarios (same synth bars, decisions fed from `simulate` exits);
      sizing gives targets 39/30/30/35 + qty 7/10/10/8 (ts4) and 58/45/45/53 + 5/6/6/5 (ts4_v2)
      (checked with `qty_for`/`target_ticks_for`); overlay gives
      1m / 346 / stop_first; `ts4.yaml` is inert, `ts4_v2.yaml` has BE/trail off, and
      `ts4_v2.yaml` + `{breakeven: {enabled: true}}` is refused; `target_first` override refused.
      Verify: `T4`, `L`, `M`.

- [x] 3. `research/ts4/plugin.py` (`Ts4Plugin`) and `research/ts4/paper_io.py`, per (b). Depends on 2.
      `paper_io`: `PAPER_TRADE_HEADER`, `SKIP_HEADER`, `write_csv_atomic(path, header, rows)`,
      `write_resolved_config(path, cfg)`.
      `Ts4Plugin`: constructor runs `effective_config` (start checks); `needs`, `on_start`
      (calendar + warm-up tasks, `config.resolved.yaml`, start log with sha and overlay notes),
      `on_day_open`, `urgent_bar_closes`, `on_bar`, `on_order_update`, `next_wake`/`on_wake`
      (task results + session-close fallback), `snapshot`/`restore`, `on_stop`.
      Imports from `fse.live.plugins` only (plus `research.ts4.{config,paths,market,market1m,live_rules,paper_io}`,
      `research.matrix.{clock,ema,sizing}`, `research.range.config`).
      Tests (all through `make_host` + `FakeDataPlane` + `FakeClock`, entries via `host_entry("ts4",
      module="research.ts4.plugin:Ts4Plugin", config={...}, risk={"max_contracts": 10, "max_open_trades": 1})`,
      `warmup_sessions: 0`, dates in 2026-10 inside the calendar coverage):
      - `tests/test_plugin_flow.py`: long and short entries at a slot, target, stop, gap-open stop,
        session-close flatten at 17:00 (and the `close + 90 s` wake fallback when the 16:59 bar is
        missing), `position_open`, `late_bar`, `catch_up`, `blocked` (halt file via `EntryGate`
        path `<tmp>/live-state`), `qty_zero`, progressive add/reset across label dates, CSV rows and
        `skips.csv` contents, needs validation passes.
      - `tests/test_plugin_restart.py`: run, stop mid-trade, second host with
        `FakeDataPlane(recorded=first.recorded)`: no duplicate rows, progressive and EMA carry over,
        `restore` of a bad version -> `restore_failed` path (host renames `plugin.json`).
      - `tests/test_plugin_imports.py`: AST check of `plugin.py`, `live_rules.py`, `paper_io.py`:
        no `fse.live.host`, no research-only module, no `research.ts4.run`, the network regex; and
        `resolve_plugin_class("ts4", "research.ts4.plugin:Ts4Plugin", project_dir=PROJECT_DIR)` loads.
      Verify: `T4`, `L`, `M`, `U`.

- [x] 4. Parity harness `research/ts4/tests/test_plugin_parity.py` (+ helper
      `research/ts4/tests/replay.py`). Depends on 3.
      `replay.py`: build continuous synthetic 1m MNQ sessions (18:00-17:00 NY, contract `MNQZ6`,
      offset 0) as both `fse` `Bar`s (`make_bar`) and a sim `research.range.bars.Bars(60, ...)`; play
      the `Bar`s through `PluginHost` + `Ts4Plugin` with batched `BarsEvent`s (a batch ends at every
      slot signal bar and at every session close, received 2 s after its last close, never across
      18:00) so `placed_at` stays within 60 s; return the plugin's `trades.csv` rows.
      Cases, each asserting the shared fields equal row for row (side, qty, entry_t, target_t,
      stop_t, exit_t, stop_ticks, target_ticks, add_ticks, reason, gap_fill, entry_open_ns,
      exit_close_ns, net_cents) against `simulate(effective_cfg_for_sim, SimData(bars))`:
      - A: frozen `ts4.yaml`, sim with BE/trail **as in the YAML (enabled)**, zero costs
        (host `slippage_ticks 0`, `trade_through_ticks 0`, `commission_rt 0.00`), 3 seeds x 4 Globex
        days with moves > 40 ticks in favour. Also assert `simulate(cfg) == simulate(cfg + NOBE)` and
        no sim trade has `stop_moved`. This is the required BE/trail-inert proof.
      - B: `ts4_v2.yaml` (Option B) vs sim of the same file (= `c1b_nobe`), zero costs, 3 seeds.
      - C: A and B with `trade_through_ticks 1` / `exits.target_trade_through_ticks 1` and
        commission $1.48 RT on both sides (slippage stays 0: known difference).
      - D (`@pytest.mark.localcache`, run once by hand with `LC`): real Atlas 1m sessions
        2025-01-02..2025-01-31 from `load_market_1m` fed as adjusted ticks on contract `MNQZ6`,
        cases A and B. Record trade counts and match result in the task notes.
      Keep the default (non-localcache) runtime under 60 s; pass `max_steps` to `clock.run` if needed.
      Verify: `T4` (new tests pass), `L`, `M`; `LC` once if the cache exists, else note it.

- [x] 5. Runner example, its test, and the plugin README. Depends on 3 (and 4 for the README claims).
      Files: `E/configs/runner.ts4.example.yaml` ((d) verbatim), `research/ts4/README.md`,
      `research/ts4/tests/test_runner_example.py`.
      Test: `load_runner_config` parses the file; `load_plugin_classes(cfg, project_dir=PROJECT_DIR)`
      loads both entries (`ts4_v2` via a copy with `enabled: true`); `PluginHost` construction via
      `make_host`-style paths succeeds (no `needs_problems`); `max_contracts` >= the plugin's max qty
      for each config; `fse.sim.fills.TICK_VALUE_USD["MNQ"] == 0.50` matches the TS4 YAMLs.
      README: what the plugin is (paper only); the two configs and Option B numbers
      (`c1b_nobe`, OOS $17.11/trade; `c1b` $18.25 is Option A, deferred, `ts4_v2_be` TODO);
      "frozen ts4 runs with BE/trail off; identical trades verified by `test_plugin_parity.py`
      case A"; the effective-config overlay; the start checks; `fills.trade_through_ticks <= 1`;
      the (b) known differences; the clock warning; the roll note (add the next contract, e.g.
      `MNQH7`, to `research/range/range.yaml` `price_adjust.offsets` before the 2026-12 roll or
      entries skip `unknown_contract`); the (e) coexistence notes; a **risk note**: paper results
      on a backtested strategy are not a forecast; the edge is small relative to costs; never route
      real orders. Running it needs FEAT-004's `fse run` (not committed yet); until then it runs
      only under the test harness.
      Verify: `T4`, `L`, `M`, `U`.

Estimate: about 8-10 h of coder time (item 1 ~1 h, 2 ~2.5 h, 3 ~3.5 h, 4 ~1.5 h, 5 ~1 h).

Restructuring: not done. Items 0-5 are one sequential feature (each depends on the previous), so
the existing implement-and-review loop runs this plan as is. Loop stop contract: the reviewer
writes `/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-plugin/.agents/ts4-plugin/review.json`
with top-level `"verdict": "APPROVED"` (no blocking findings) or `"CHANGES_REQUESTED"`.

## Decisions (user, 2026-10-08)

- **ts4_v2 ships as Option B: breakeven and trailing OFF** (research variant `c1b_nobe`): stops
  232/180/180/210, targets 58/45/45/53, contracts 5/6/6/5, NY clock, progressive on. Expected OOS
  $17.11/trade (vs $18.25 with BE/trail, TS4_improvements.md). No stop-modify intent needed. Parity
  compares against `c1b_nobe`, not `c1b`.
- **TODO, Option A (wanted later):** the plugin-runner agent adds `ModifyStop` (f.1). Do not add it
  to core here. Then add `ts4_v2_be` (BE 40/4, trail 40/30 step 4), parity-tested against `c1b`.
- **Frozen ts4:** at defaults BE/trail never fire (targets < 40 ticks), so it runs with BE/trail off
  and identical trades. Verified by parity (item 4, case A), not assumed, and stated in the README.
- FEAT-003 is committed and merged here (`be9718a`); FEAT-004 is not, so the plugin is driven by
  `PluginHost` + the committed test fakes until `fse run` lands.
