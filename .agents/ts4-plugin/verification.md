# TS4 plugin: verification evidence

Worktree `/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-plugin`,
branch `kiro/ts4-plugin`, HEAD `be9718a` (unchanged; nothing committed). Every command runs from
the engine dir `E = <worktree>/members/alantiix/skylit-futures-strategy-engine` with
`export PYTHONPATH=$PWD/src:$PWD` and `V=.venv/bin` (Python 3.14.6). Run date 2026-10-08/09.

## 0. Core untouched, everything uncommitted

```
$ git -C <worktree> log -1 --format=%h          -> be9718a
$ git -C <worktree> status --short
?? .agents/
?? members/alantiix/skylit-futures-strategy-engine/configs/runner.ts4.example.yaml
?? members/alantiix/skylit-futures-strategy-engine/research/ts4/
$ git -C <worktree> diff --stat be9718a -- <E>/src <E>/tests   -> (empty)
```

No file under `src/fse/live/**`, `tests/fakes/**` or any other tracked file changed. The
plugin-runner worktree was only read (`git log`/`git diff`), never written.

## 1. Test suites

| suite | command | result |
|---|---|---|
| plugin tests | `$V/python -m pytest research/ts4/plugin/tests -q -p no:cacheprovider` | **89 passed**, 1 deselected (localcache) in 54 s |
| plugin real-data parity | `$V/python -m pytest research/ts4/plugin/tests/test_parity_localcache.py -m localcache -q -p no:cacheprovider` | **1 passed** in 246 s (full in-sample year, see 2) |
| TS4 research (`T4`) | `$V/python -m pytest research/ts4/tests -q -p no:cacheprovider` | **200 passed**, 4 deselected (baseline 198 + 2 new: ts4_v2 config, `extra_bars`) |
| all research | `$V/python -m pytest research -q -p no:cacheprovider` | **855 passed**, 6 skipped (edge_study audit needs `ES_AUDIT_RUN`), 33 deselected |
| host core (`U`) | `$V/python -m pytest tests/unit/test_plugin_api.py tests/unit/test_plugin_loader.py tests/unit/test_plugin_paper_broker.py tests/unit/test_plugin_host_dispatch.py tests/unit/test_plugin_host_persistence.py tests/unit/test_plugin_host_rotation.py tests/unit/test_runner_config.py tests/unit/test_dayclock.py tests/unit/test_entry_gate.py tests/unit/test_runner_lock.py tests/unit/test_runner_paper_only.py -q -p no:cacheprovider` | **128 passed** (= baseline) |
| engine suite | all 229 `tests/**/test_*.py` files (testpaths `tests`, default markers), run as 10 parallel shards of `$V/python -m pytest -q -p no:cacheprovider <files>` (a serial run was too slow for this step) | **3,332 passed**, 4 deselected (perf/live/localcache), every shard exit 0, wall 4 min 52 s (shards: 263, 376, 339, 253, 292, 325, 326, 367, 483, 308) |

The "all research" figure is from the run after the last fix (the first run had 1 failure,
a too-narrow `match=` in `test_plugin_restart.py`, fixed and re-run: 4 passed).

## 2. Parity: plugin through `PluginHost` + paper broker vs `research.ts4.sim`

Harness: `research/ts4/plugin/tests/replay.py` (the committed `PluginHost`, each plugin's own
`PluginPaperBroker`, `tests.fakes.dataplane` `FakeDataPlane`/`MemoryBarStore`/
`InlineBlockingRunner`/`make_host`/`host_entry`/`make_bar`, `FakeClock`) and
`research/ts4/plugin/tests/realdata.py`. Nothing in engine core.

Data: the research's own Atlas 1m market (`research.ts4.market1m.load_market_1m`, the Operator's
`~/.skylit-fse/cache`, `range.yaml` back-adjust, mixed roll sessions dropped). Window: the
**full in-sample year**, Globex days 2025-10-13 .. 2026-10-02 (opens Sunday 2025-10-12 18:00),
319,774 1m bars, four contract rolls (MNQZ5 -> H6 -> M6 -> U6 -> Z6). Bars reach the host as
raw contract ticks (adjusted - offset) with their real contract codes; the plugin applies the
`range.yaml` offsets itself. The plugin warms its EMA from the cache (`warmup_sessions: 60`,
the real `run_blocking` warm-up path); the sim's EMA seeds at 2025-01-02. Sim: `simulate(cfg,
SimData(bars, market), 2025-10-12, 2026-10-02)` with the plugin's **declared** config (YAML +
live overlay; for ts4 BE/trail **enabled** as in the YAML). Compared row for row, keyed by
`entry_open_ns`: label_time, side, qty, entry/target/stop/exit price (plugin raw + offset),
stop_ticks, target_ticks, add_ticks, reason (outcome), gap_fill, entry_open_ns,
exit_close_ns, gross_ticks, net_cents.

Commands: `$V/python -m research.ts4.plugin.tests.realdata --set {zero|tt1|real|together} --out <json>`
(each about 6.5 min; `together` 4.5 min). The JSON summaries are copied to `evidence/`
(`parity_full_year_{zero,tt1_comm,realistic,together}.json`; plus a 2025-12-01..2026-01-09
window across the Z5 -> H6 roll, `parity_2025-12_roll_window.json`: 81/81 and 105/105 exact).
The `together` run was repeated after adding the `hook_latency` count: exact again, **0**
`hook_latency` warnings for either plugin over the year.

| case | host fills | sim costs | sim trades | plugin trades | matched |
|---|---|---|---|---|---|
| ts4 (frozen) vs research baseline | slip 0, tt 0, $0.00 | 0 / 0 / 0 | 880 | 880 | **880 (exact)** |
| ts4_v2 vs `c1b_nobe` | slip 0, tt 0, $0.00 | 0 / 0 / 0 | 959 | 959 | **959 (exact)** |
| ts4, target trade-through + commission | slip 0, tt 1, $1.48 RT | tt 1, $1.48 | 880 | 880 | **880 (exact)** |
| ts4_v2, same | slip 0, tt 1, $1.48 RT | tt 1, $1.48 | 959 | 959 | **959 (exact)** |
| ts4 + ts4_v2 in ONE host (`together`) | zero | zero | 880 / 959 | 880 / 959 | **880 / 959 (exact)** |
| ts4, research realistic | slip 1, tt 1, $1.48 | slip 1/1, tt 1, $1.48 | 880 | 880 | 0 (expected, see below) |
| ts4_v2, research realistic | slip 1, tt 1, $1.48 | same | 959 | 959 | 0 (expected) |

Zero-cost P&L (identical in sim and plugin): ts4 win rate 85.23%, net $18,844.50,
$21.41/trade, worst -$956.00; ts4_v2 87.28%, $32,710.00, $34.11/trade, worst -$895.00.
With tt 1 + $1.48: ts4 85.11%, $6,676.26 ($7.59/trade); ts4_v2 86.65%, $20,217.94
($21.08/trade). No faults, exit code 0, zero host break backstops (every trade was
flattened by the plugin's own session-close rule).

The localcache pytest (`test_parity_localcache.py`) runs the `together` case and asserts
exact parity for both plus the BE/trail-inert facts: passed (1 passed in 246 s).

**Realistic-cost mismatches, explained (not a bug; a documented model difference).** With
1-tick slippage the trade lists still agree on every entry (same 880/959 trades, same
label_time, side, qty, entry price, entry bar), but the host prices the stop and target from
the fill bar's **unslipped** open while the research (NinjaTrader ticks mode) prices them from
the **slipped** fill. So target_t, stop_t, exit_t, gross and net differ by 1 tick on every
trade, the exit bar differs on 33 (ts4) / 25 (ts4_v2) trades whose bar touched the
research's level but not the host's (or vice versa), and 3 trades carry a different
progressive add-on as a consequence (3 / 2 different outcomes). Net effect, in-sample year:
ts4 sim $3,782.76 ($4.30/trade) vs plugin $2,237.76 ($2.54); ts4_v2 sim $18,975.44
($19.79/trade) vs plugin $17,228.94 ($17.97). Core proposal (f.4 in the plan / REPORT):
`EnterMarket.levels_from: "fill"`.

Skip counts differ only in definition: the plugin logs `no_bar` for every slot without a
signal bar (ts4 140, ts4_v2 56: sessions absent from the cache, the dropped mixed roll
sessions, and for utc-4 the winter "18:35" = 17:35 ET slot), the sim counts only slots with
bars on both sides on the same label date (62 / 0). `position_open`: 5 = sim 5 (ts4_v2).

Synthetic parity (default suite, `test_parity_synth.py`, 17 tests): 3 seeds x 4 Globex days
of random walk (+-10 ticks a bar, 1% gap opens), frozen ts4 and ts4_v2, zero cost and
tt 1 + $1.48: every case exact. `test_plugin_flow.py` (20 tests) asserts parity in each ported
rule scenario as well.

## 3. Frozen ts4: "BE/trail never fire at defaults", verified by parity

- Full in-sample year: the sim with ts4.yaml's BE (40/4) and trail (40/30/4) **enabled**
  gives 880 trades, `stop_moved` on 0 of them, `stop_moves` counter 0, and is identical
  (`==` on every Trade field) to the sim with BE/trail off; the plugin (which runs them off)
  matches it 880/880. Same with tt 1 + commission.
- Synthetic: the same three facts on 3 seeds x {zero, tt1}; plus
  `test_case_a_a_trigger_close_exits_on_that_same_bar` (a fill bar closing +45 ticks, past
  both triggers, exits at the 39-tick target on that bar with `stop_moved` false, tt 0 and 1).
- Control: `test_control_be_trail_would_change_ts4_v2`: with BE/trail ON, ts4_v2's 58-tick
  targets exceed the 40-tick triggers, the sim moves stops and the trades differ from
  c1b_nobe, so the comparison would catch a firing BE/trail. The plugin refuses that config
  (start check, "ModifyStop (Option A, deferred)").

## 4. Multi-strategy

- Real data: `--set together` and the localcache test run ts4 and ts4_v2 in one host over the
  full year: each exact against the sim (880 / 959), identical to the solo runs.
- `test_multi_strategy.py::test_ts4_ts4_v2_and_an_opposing_plugin_coexist`: ts4 + ts4_v2 +
  the committed test plugin `tests.fakes.plugins:RecordingPlugin` scripted to go SHORT 2 MNQ
  at 09:45 while both TS4 configs go LONG. At 09:50 the three books hold +7, +5, -2 MNQ
  (opposite positions coexist); each TS4 plugin's trades equal its solo run; each plugin has
  its own `live-state/plugins/<id>/plugin.json`. No other strategy plugin is committed at
  `be9718a` (FEAT-005 not written), hence ts4_v2 as the second strategy and the test plugin
  as the third.
- `test_a_plugin_halt_file_blocks_only_that_plugin`: `live-state/plugins/ts4_v2/HALT` blocks
  ts4_v2 (skips `blocked: halt_file`), ts4 trades normally.

## 5. Ported TS4 unit tests

`test_plugin_flow.py` (host-driven, each also sim-parity-checked): next-bar-open entry and
target at the limit (qty 7, net 39 x $0.50 x 7), short below the EMA, stop at the stop,
gap-open stop, same-bar target+stop -> stop, target trade-through + commission, session-close
flatten at 17:00, missing 16:59 bar -> wake flatten at the 16:58 close, entry on the next
existing bar, position_open skip, entry allowed on the exit bar, no_bar, late_bar,
halt-file block, qty_zero and a runner-cap rejection, progressive: three winners widen the
evening stop to 239 (qty 8 stays), a loss resets (155/159/120/170), a new label date resets,
progressive off; outputs and needs. `test_rules.py` (33): config resolution, every start
check, sizing (39/30/30/35 + 7/10/10/8; 58/45/45/53 + 5/6/6/5), progressive state machine,
incremental EMA bit-equal to `ema_series` (3 seeds x 5000 bars), slot instants across DST
for both clocks. `test_plugin_restart.py` (4): restart mid-trade equals one uninterrupted run
(rows, skips, EMA, progressive; no duplicate rows), recorded bars replay as `catch_up`,
unusable state -> host `restore_failed` (file renamed, entries blocked), snapshot round trip.
`test_warmup.py` (3), `test_plugin_imports.py` (8), `test_runner_example.py` (2).

## 6. Lint and types

| check | command | result |
|---|---|---|
| ruff (TS4) | `$V/ruff check research/ts4 --extend-exclude research/ts4/handoff` | All checks passed |
| ruff format (TS4) | `$V/ruff format --check research/ts4 --extend-exclude research/ts4/handoff` | 68 files already formatted |
| mypy strict (TS4) | `$V/mypy research/ts4 --exclude '^research/ts4/handoff/'` | Success: no issues found in 49 source files |
| ruff (engine) | `$V/ruff check src tests configs` | All checks passed |
| ruff format (engine) | `$V/ruff format --check src tests` | 3 files would be reformatted: `src/fse/sim/fills.py`, `tests/property/test_market_bracket_vs_scan_exit.py`, `tests/unit/test_sim_market_brackets.py` (committed at `be9718a`, unchanged here: pre-existing, not touched per the no-core-edits rule) |
| mypy (engine) | `$V/mypy src tests` | 8 errors in 4 committed, unchanged test files (`test_awsexport_{source,staging,transform}.py`, `test_runner_lock.py`): pre-existing |
| ruff whole project | `$V/ruff check .` | 210 findings, all in `research/talon` (132) and `.agents/tasks` (78), neither touched |

`research/ts4/handoff/` (the research-only NinjaScript reference) keeps its 3 known ruff
findings; excluded as in the plan.

## 7. Paper broker fill/cost model vs the research realistic model

| | paper broker (`PluginPaperBroker` over `fse.sim.fills.SimBook`) | research `sim.py` (REALISTIC: $1.48 RT, slip 1, tt 1) |
|---|---|---|
| entry | next bar open +- `slippage_ticks` | next bar open +- `entry_slippage_ticks` |
| bracket levels | ticks from the fill bar's **unslipped** open | ticks from the **slipped** fill |
| stop fill | stop, or the open when the bar opened past it (`gap_open`), -+ `slippage_ticks` | same with `stop_slippage_ticks` (gap only after the fill bar; the fill bar opens at the entry) |
| target fill | the bar trades `trade_through_ticks` beyond the limit | `target_trade_through_ticks` |
| stop and target on one bar | the stop, always | `exits.ambiguous` (overlay: `stop_first`) |
| session close | `Flatten last_close` at the session-close bar's close, **no slippage** | close - entry slippage |
| commission | `commission_rt` / 2 per fill (= RT per contract) | `commission_per_contract_rt` x qty |
| session end | the calendar close (`globex_hours.yaml`) | last bar before a > 15 min gap |

With slippage 0 the models coincide (exact parity above, including tt 1 and commission).
With slippage 1 the unslipped-open brackets cost about 1 tick per winner and save 1 tick
per stop loser: -$1.76/trade (ts4) and -$1.82/trade (ts4_v2) vs the research on the
in-sample year. The session-close exit without slippage is the other, smaller difference.

## 8. Not verified

- `fse run` / the live data plane / `LiveBarStore`: not committed at `be9718a` (they are on
  `kiro/plugin-runner` `f36b6ed`, which this worktree does not contain); the plugin ran only
  under the test harness.
- The production `ThreadBlockingRunner` warm-up path with bars arriving before the warm-up
  finishes (buffering): the code path exists, but the tests use `InlineBlockingRunner`, which
  finishes the job before any bar.
- Live hook latency: the full-year replay logged 0 `hook_latency` warnings (the host times
  hooks with the real `perf_counter`), but that was on this machine, not under live feeds.

## Update to f36b6ed

Worktree fast-forwarded to `kiro/plugin-runner` `f36b6ed` (Matrix plugin `ed41257`, Playbook
plugin + `fse run` README `f36b6ed`). Same engine dir `E`, `export PYTHONPATH=$PWD/src:$PWD`,
`V=.venv/bin`, Python 3.14.6. Run date 2026-10-09. Nothing committed.

### U.0 Core untouched

```
$ git -C <worktree> log -1 --format=%h                    -> f36b6ed
$ git -C <worktree> status --short --untracked-files=no   -> (empty: no tracked file changed)
```

All changes are in untracked TS4 files: `research/ts4/plugin/{rules,strategy}.py`, the plugin
`README.md`, `tests/{test_fills_check,test_fse_run_example}.py` (new),
`tests/{test_plugin_flow,test_warmup,test_runner_example,test_plugin_restart,replay}.py`,
`configs/runner.ts4.example.yaml`, and `REPORT.md`/this file. The two git-crypt
`__init__.py` artifacts from review issue 5 no longer show as modified after the
fast-forward. Nothing under `src/fse/live/**` changed; the plugin-runner worktree was not
touched. Before any edit, the existing 89 plugin tests passed unchanged on `f36b6ed`
(89 passed, 1 deselected, 55 s): the API additions (`PaperFills`, `recordings_root`) are
additive.

### U.1 What changed and how it was checked

| change | test |
|---|---|
| Fills safety: inert rewrite + `fills.trade_through_ticks > 1` -> `Ts4PluginError` in the constructor -> host `PluginLoadError` ("plugin 'ts4': cannot start: Ts4PluginError: fills.trade_through_ticks is N ..."), exit 2; missing fills block refused | `test_fills_check.py`: refuse tt 2 and 3 through `make_host` (`PluginLoadError`, exact message prefix) and `check_fills`; accept tt 0 and 1 (host builds); ts4_v2 at tt 2 accepted (no rewrite); `fills=None` refused |
| Cost/fill model in `ts4_start` (`fills: {slippage_ticks, trade_through_ticks, stop_fill, commission_rt, symbol}`) | `test_fills_check.py::test_the_start_log_carries_the_active_cost_model`, `test_fse_run_example.py` |
| Example: ts4_v2 enabled, ts4 disabled, slip 1 / tt 1 / $1.48 on both, comments | `test_runner_example.py` (enabled == `["ts4_v2"]`, fills of both entries, caps fit max qty 6 / 10) |
| Issue 2: EMA re-warm when the gap exceeds the scheduled break/weekend (`ema_is_current`) | `test_warmup.py::test_a_restored_ema_is_kept_only_across_the_scheduled_break` (7 cases: same session, daily break, next session, missed session, weekend, Sunday 17:59 / 18:01) |
| Issue 3: one error-level `no_calendar` log | `test_plugin_flow.py::test_calendar_expiry_skips_no_calendar_and_logs_one_error` |

Both follow-ups were under 30 lines (issue 2: ~10, issue 3: ~12), so both were applied.

### U.2 `fse run` on the example config (offline)

`fse run` has no dry-run, validate or config-check mode. `run_command` checks the ProjectX
(and, with Skylit on, Skylit) credentials and then logs in to ProjectX before the host is
built, so the CLI was **not** run. Instead `test_fse_run_example.py` drives
`fse.commands.run._run`, the command's async core, the same way the engine's
`tests/integration/test_fse_run_one_globex_day.py` does: virtual time Sunday 2026-10-11 18:00
to `--until` Monday 18:10 NY; the example's `plugins` block verbatim plus the shipped
`configs/runner.yaml` `matrix` entry (out_root under tmp); the example's `data_plane` with
test-only `projectx.backfill_sessions: 0` and `skylit.enabled: false`; ProjectX REST behind
`respx.mock(assert_all_mocked=True)`; `FakeMarketHub` via the injected connect;
`BrokerAdapter.__init__`/`BrokerSafety.__init__` patched to raise. Result: exit 0,
`host_stopped` last, no `plugin_faulted`, no broker adapter constructed, only
`loginKey`/`retrieveBars` called; `ts4` never loads (no out or state dir); `ts4_v2` logs
`clock: ny` and the example fills, its only error is `ema_warmup_failed` (no Data_Cache under
tmp, falls back to live seeding), Monday has one trade (09:45, flattened at the 17:00 close)
and two `position_open` skips; Matrix wrote its ledger and `plugin.json` in the same host.

### U.3 Commands and results

| suite | command | result |
|---|---|---|
| plugin tests | `$V/python -m pytest research/ts4/plugin/tests -q -p no:cacheprovider` | **107 passed**, 1 deselected (localcache) in 98 s |
| multi-strategy + coexistence + fills | `$V/python -m pytest research/ts4/plugin/tests/test_multi_strategy.py research/ts4/plugin/tests/test_fse_run_example.py research/ts4/plugin/tests/test_fills_check.py -q -p no:cacheprovider` | **12 passed** in 45 s |
| localcache parity | `$V/python -m pytest research/ts4/plugin/tests -m localcache -q -p no:cacheprovider` (`~/.skylit-fse/cache` present) | **1 passed**, 107 deselected in 305 s |
| parity counts | `$V/python -m research.ts4.plugin.tests.realdata --set together --out /tmp/ts4v/together.json` (copied to `evidence/parity_full_year_together_f36b6ed.json`) | 319,774 bars, 2025-10-13..2026-10-02, zero cost, both in one host: **ts4 880/880 exact** vs the research baseline (85.23%, $18,844.50, $21.41/trade, worst -$956.00), **ts4_v2 959/959 exact** vs c1b_nobe (87.28%, $32,710.00, $34.11/trade, worst -$895.00); BE/trail inert facts 0 / 0 / identical; faults {} and 0 backstops for both. UNCHANGED from be9718a |
| all research | `$V/python -m pytest research -q -p no:cacheprovider` | **921 passed**, 6 skipped (edge_study audit needs `ES_AUDIT_RUN`), 33 deselected in 369 s, exit 0 |
| engine suite | all 243 `tests/**/test_*.py` files, 10 parallel shards of `$V/python -m pytest -q -p no:cacheprovider <files>` | **3,436 passed**, 6 deselected (perf/live/localcache), every shard exit 0, wall 12 min (shards 299, 291, 341, 571, 374, 305, 259, 309, 281, 406) |
| ruff (TS4) | `$V/ruff check research/ts4 --extend-exclude research/ts4/handoff` | All checks passed |
| ruff format (TS4) | `$V/ruff format --check research/ts4 --extend-exclude research/ts4/handoff` | 70 files already formatted |
| mypy strict (TS4) | `$V/mypy research/ts4 --exclude '^research/ts4/handoff/'` | Success: no issues found in 51 source files |
| ruff (engine) | `$V/ruff check src tests configs` | All checks passed |
| ruff format (engine) | `$V/ruff format --check src tests` | 5 files would be reformatted, all committed and untouched: `src/fse/sim/fills.py`, `tests/perf/test_matrix_decision_perf.py`, `tests/property/test_market_bracket_vs_scan_exit.py`, `tests/unit/test_plugin_host_dispatch.py`, `tests/unit/test_sim_market_brackets.py` (pre-existing) |
| mypy (engine) | `$V/mypy src tests` | 23 errors in 11 committed, untouched test files (`tests/fakes/market_hub.py`, `test_awsexport_{source,staging,transform}.py`, `test_dataplane.py`, `test_live_bar_store.py`, `test_playbook_scheduler.py`, `test_polling_source.py`, `test_projectx_realtime.py`, `test_runner_lock.py`, `test_streaming_source.py`): pre-existing on f36b6ed; `src` is clean |
| ruff whole project | `$V/ruff check .` | 213 findings: `research/talon` 132, `.agents/tasks` 78, `research/ts4/handoff/ts4_reference.py` 3 (the known, excluded research-only reference) |

`hook_latency` warnings in the `together` run: ts4 2, ts4_v2 0 (be9718a: 0 / 0). This run
shared the machine with the localcache pytest running the same year at the same time;
hook timing uses the real `perf_counter`, so this is load, not a plugin change.

### U.4 Not verified

- The `fse run` CLI process itself against live ProjectX/Skylit (by instruction: no network,
  no credentials). Only its async core ran, offline.
- The Playbook plugin next to TS4: not run (it needs the Skylit stream and a strategy config
  with fees set; the shipped one exits 2). Matrix coexistence was run.

### U.5 Commit-message-style note (not committed)

```
feat(ts4): adapt the TS4 plugin to f36b6ed (fills check, example ts4_v2, fse run test)

- refuse to start when the inert-BE/trail rewrite applied and
  fills.trade_through_ticks > 1 (Ts4PluginError -> PluginLoadError, exit 2);
  refuse a missing fills block; log the active fill/cost model in ts4_start
- configs/runner.ts4.example.yaml: ts4_v2 enabled, ts4 disabled, block style
  like configs/runner.yaml, realistic fills (slip 1, tt 1, $1.48 RT)
- re-warm a restored EMA when the restart gap exceeds the scheduled
  break/weekend (review issue 2); one error-level no_calendar log (issue 3)
- tests: test_fills_check.py, test_fse_run_example.py (fse run core,
  offline, ts4_v2 next to Matrix), EMA gap rule, no_calendar log

Verified (E = members/alantiix/skylit-futures-strategy-engine,
PYTHONPATH=$PWD/src:$PWD):
  pytest research/ts4/plugin/tests -q              107 passed, 1 deselected
  pytest research/ts4/plugin/tests -m localcache   1 passed (ts4 880/880,
                                                   ts4_v2 959/959 exact, unchanged)
  pytest research -q                               921 passed, 6 skipped
  pytest tests (10 shards)                         3436 passed, 6 deselected
  ruff check / format --check research/ts4         clean
  mypy research/ts4                                no issues (51 files)
  ruff check src tests configs                     clean
  ruff format --check src tests                    5 pre-existing, untouched
  mypy src tests                                   23 pre-existing errors in 11
                                                   untouched test files
```
