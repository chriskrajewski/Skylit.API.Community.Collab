# TS4 plugin: report

TS4 now runs as a host-brokered `fse run` plugin (`module: research.ts4.plugin:Ts4Plugin`),
paper only, with two named configs: `ts4` (default, the frozen original) and `ts4_v2`
(Option B = research variant `c1b_nobe`). Driven through the committed `PluginHost` and
paper broker over the full in-sample year of Atlas 1m MNQ data, it reproduces the research
simulator's trades exactly: **880/880 (ts4) and 959/959 (ts4_v2)**, alone or both in one
host. All work is uncommitted in the `kiro/ts4-plugin` worktree, now fast-forwarded to
`f36b6ed` (see "Update to f36b6ed" below); no engine core file changed. Evidence:
`verification.md` next to this file.

## Update to f36b6ed (Matrix and Playbook plugins, `fse run` README)

- **Example config**: `configs/runner.ts4.example.yaml` now ships **`ts4_v2` enabled and
  `ts4` disabled** (never both). Block-style YAML with explicit `enabled`, header and
  per-entry comments, as `configs/runner.yaml` is laid out. Fills stay research-realistic
  (slippage 1, trade-through 1, $1.48 RT). The header says why: ts4 lost out of sample on its
  `utc-4` clock (-$6,402 over 725 trades), ts4_v2 is the paper-trade candidate, and both
  together double MNQ exposure. The README and this report say the same.
- **Fills safety check (review issue 1)**: the constructor reads `services.fills` (the new
  `PaperFills`). With the inert-BE/trail rewrite applied (frozen `ts4`) and
  `trade_through_ticks > 1` it raises `Ts4PluginError` (a `ValueError`), which the host turns
  into `PluginLoadError` "plugin 'ts4': cannot start: Ts4PluginError: fills.trade_through_ticks
  is N, ...", exit 2, the same path Matrix uses for its fills checks. A missing fills block is
  refused as Matrix does. `ts4_v2` (BE/trail off in the YAML) has no limit. The `ts4_start` log
  entry now carries `fills: {slippage_ticks, trade_through_ticks, stop_fill, commission_rt,
  symbol}`.
- **Review issue 2 (done, ~10 lines)**: a restored EMA is kept only while the restart is in
  the same Globex day as its last bar or in the weekend right after it
  (`strategy.ema_is_current`); any longer gap re-warms from the cache. Replaces the 4-day rule.
- **Review issue 3 (done, ~12 lines)**: the first `no_calendar` skip logs one error-level
  `no_calendar` entry, like `unknown_contract`.
- **`fse run` verified offline**: `fse run` has no dry-run/validate mode and checks
  credentials and logs in to ProjectX before the host starts, so the CLI itself was not run.
  `tests/test_fse_run_example.py` drives `fse.commands.run._run` (the command's async core,
  the entry point the engine's own `test_fse_run_one_globex_day.py` uses) over one virtual
  Globex day with the example's `plugins` block verbatim plus the shipped `matrix` entry,
  ProjectX behind respx (`assert_all_mocked`), a fake market hub, Skylit off and broker
  adapters refused: exit 0, no fault, ts4_v2 trades on the example fills, ts4 never loads,
  Matrix writes its ledger in the same host.
- Not changed: `configs/runner.yaml` and the engine README's plugin list (engine files; the
  TS4 entries can be pasted into `runner.yaml`, the plugin README says how). Option A
  (`ModifyStop`) stays a TODO.

## What was built

Engine dir `E = members/alantiix/skylit-futures-strategy-engine`.

| path | what |
|---|---|
| `E/research/ts4/plugin/__init__.py` | the plugin package; exports `Ts4Plugin` |
| `E/research/ts4/plugin/strategy.py` | `Ts4Plugin(StrategyPlugin)`: needs, start, day open/close, bars, order updates, wakes, snapshot/restore, outputs |
| `E/research/ts4/plugin/rules.py` | pure logic: `Ts4PluginConfig`, `resolve` (YAML + live overlay + overrides + start checks + inert-BE/trail rewrite), `slot_instants`, `Ema` (bit-equal to `ema_series`), `Progressive`, `bracket` |
| `E/research/ts4/plugin/warmup.py` | the EMA warm-up job (`run_blocking`): `load_market_1m` + `ema_series` over cached sessions |
| `E/research/ts4/plugin/paper_io.py` | `trades.csv` / `skips.csv` headers and atomic writes |
| `E/research/ts4/plugin/README.md` | plugin README: configs, risk note, rules, cost-model differences, multi-strategy notes, operations |
| `E/research/ts4/plugin/tests/` | 107 default tests + 1 localcache test (at f36b6ed); `replay.py` (host replay harness) and `realdata.py` (real-data parity runner) live here, not in engine core |
| `E/configs/runner.ts4.example.yaml` | runner example: `ts4_v2` enabled, `ts4` disabled, research-realistic fills |
| `E/research/ts4/ts4_v2.yaml` | switched to Option B (BE/trail off), header rewritten; `ts4.yaml` unchanged |
| `E/research/ts4/market1m.py` | `load_market_1m(..., extra_bars=None)` passed to `load_series` (default unchanged) |
| `E/research/ts4/tests/` | `test_config.py` (+ ts4_v2 = ts4 with ny clock, x1.5 stops, BE/trail off), `test_market1m.py` (+ `extra_bars` reaches `load_series`), `test_variants.py` (ts4_v2 now compared with `c1b_nobe`) |

Reuse, not rewrite: the plugin uses the research package's config schema and overrides
(`research.ts4.config`), clock zones and range-config path (`research.ts4.market`), the 1m
loader (`research.ts4.market1m`), `research.matrix.sizing.{qty_for,target_ticks_for}`,
`research.matrix.ema` coefficients and `ema_series`, `research.matrix.clock`
(`GlobexSessions`, `globex_hours.yaml`) and `research.range.config` offsets. Research-only
code (NT parsing, inference, validation, the research matrix/variants, stats, reports, the
CLI, the handoff reference, the simulator) stays out of the plugin package;
`tests/test_plugin_imports.py` checks every plugin module's imports by AST and, in a fresh
interpreter, that importing the plugin loads none of them (nor the network guard,
`fse.skylit` or `fse.projectx`).

TS4 rules as implemented: sizing `floor(600 / (base stop x $0.50))`; target = 0.25 x base
stop rounded half up (39/30/30/35, 58/45/45/53; equal to rounding up for these stops);
progressive stop = base + target ticks of consecutive same-label-date winners, reset by a
loss or a new date, qty fixed from the base stop; EMA(692) on 30s becomes EMA(346) on 1m
(`BAR_INTERVAL_S = 60`, 1m bars only); buy above / sell below; slots 09:45, 11:40, 12:45,
18:35 on the `utc-4` (ts4) or `ny` (ts4_v2) clock; one position at a time; session-close
flatten at the calendar close (wake fallback 90 s later; host backstop at 18:00). Every TS4
parameter is configurable through `config.overrides`; plugin params `ts4_config`,
`late_bar_s`, `warmup_sessions`.

## Plugin API used (committed at `be9718a`)

`fse.live.plugins` only (no `fse.live.host.*` in plugin code): `StrategyPlugin`,
`DataNeeds(bars=(BarNeed("MNQ"),), hours="eth", brokerage="host", hold_through_break=False)`,
`EnterMarket(tag, ..., stop_ticks, targets=(TargetLeg("tp1", qty, ticks),), placed_at=T,
exact_bar_open=False, freeze_from_ns=session close)`, `Flatten(tag, "session_close",
"last_close", stamp_ns=close)`, `OrderUpdate` (`accepted`/`rejected`/`filled_entry`/
`closed`/`expired`/`cancelled`), `PluginContext.open_tags/working_tags/entries_blocked/log`,
`PluginServices.run_blocking/out_dir/cache_dir/bar_store/project_dir`,
`globex_day_at`/`next_globex_day`, `urgent_bar_closes`, `next_wake`/`on_wake`,
`snapshot`/`restore`. Registered by dotted path `research.ts4.plugin:Ts4Plugin`; the
loader's `resolve_plugin_class` loads it (tested).

Deviations from `PLAN.md` (recorded there): the plugin is a subpackage
`research/ts4/plugin/` (module path unchanged), tests live in its own `tests/`; calendars are
loaded in the constructor (repo YAML, milliseconds) instead of a background task;
`ts4_config` takes preset names; YAML `costs.*` / `exits.target_trade_through_ticks` must be 0
(the runner fills block is the cost model); real-data parity covers the full in-sample year
instead of January 2025; nothing is committed (this step's instruction).

## Parity

Full in-sample year, Globex days 2025-10-13..2026-10-02 (319,774 bars, rolls Z5 -> H6 -> M6
-> U6 -> Z6), EMA warmed from the cache by the plugin's own warm-up, zero-cost fills
(slippage 0, trade-through 0, $0.00):

| | sim trades | plugin trades | matched | win rate | net | $/trade | worst |
|---|---|---|---|---|---|---|---|
| ts4 vs research baseline (ts4.yaml, 1m overlay) | 880 | 880 | 880 | 85.2% | $18,844.50 | $21.41 | -$956.00 |
| ts4_v2 vs `c1b_nobe` | 959 | 959 | 959 | 87.3% | $32,710.00 | $34.11 | -$895.00 |
| ts4, tt 1 + $1.48 RT | 880 | 880 | 880 | 85.1% | $6,676.26 | $7.59 | -$967.84 |
| ts4_v2, tt 1 + $1.48 RT | 959 | 959 | 959 | 86.7% | $20,217.94 | $21.08 | -$902.40 |

Compared fields per trade: label time, side, qty, entry/target/stop/exit price, stop
ticks, target ticks, add-on, outcome, gap fill, entry bar, exit bar, gross ticks, net cents.
No mismatch to explain at zero cost. With 1-tick slippage the entries still match but the
exits differ by design (next section). Synthetic random-walk parity (3 seeds, both configs,
zero cost and tt 1 + commission) and 20 ported rule scenarios are exact too.

Frozen ts4 "BE/trail never fire at defaults": verified, not assumed. Over the full year the
sim with ts4.yaml's BE/trail enabled has 0 stop moves and equals the sim with them off; the
plugin (BE/trail off) matches it 880/880. A control shows BE/trail do change ts4_v2's trades
when enabled (targets 58 > triggers 40), so the check would catch a firing rule.

## Multi-strategy result

ts4 and ts4_v2 in one host over the full year: each exact against the sim, identical to its
solo run. In `test_multi_strategy.py`, ts4 + ts4_v2 + the committed `RecordingPlugin`
scripted SHORT MNQ hold +7 / +5 / -2 MNQ at the same time in three separate books, and a
halt file in `live-state/plugins/ts4_v2/` blocks only ts4_v2. At `f36b6ed`,
`test_fse_run_example.py` also runs ts4_v2 next to the real Matrix plugin under `fse run`'s
core (one Globex day, offline): both trade their own books, no fault. The Playbook plugin was
not added to that run (it needs the Skylit stream and a fee-complete strategy config).

How the host isolates plugins: each host-brokered plugin has its own `PluginPaperBroker`
(book, fills, caps, day P&L, `plugin.json`), client ids `<id>:<tag>:<leg>`, and
`ctx.position()` sees only its own book; the `EntryGate` applies the root halt file / `fse
halt` to all and a plugin's own `HALT`/`blocks.json` to it alone; the runner lock
(`<live-state>/runner.lock`) allows one runner per live-state directory. Shared: one data
plane, one dispatcher, the 18:00 backstop. Conflicts: opposite positions on one instrument
are fine in paper (separate books) but would net at a real account (out of scope);
`ts4` + `ts4_v2` double MNQ exposure on the same slots (the example ships ts4 disabled);
caps are per plugin, there is no account-level cap.

## Paper broker vs research cost/fill model

| | paper broker | research realistic |
|---|---|---|
| entry | next open +- slippage | same |
| bracket levels | from the **unslipped** open | from the **slipped** fill (NinjaTrader) |
| stop | stop or gap open, -+ slippage | same |
| target | `trade_through_ticks` beyond the limit | same rule |
| stop + target on one bar | stop, always | `exits.ambiguous` (overlay `stop_first`) |
| session close | last close, no slippage | close - slippage |
| commission | `commission_rt` per contract | per contract RT |
| session end | calendar close | > 15 min data gap |

Effect at research-realistic costs (slip 1, tt 1, $1.48), in-sample year, same 880/959
entries: ts4 $2.54/trade on the paper broker vs $4.30 in the sim; ts4_v2 $17.97 vs $19.79.
Out of sample (2025-01-02..10-13, sim): ts4_v2 $17.11/trade (= the research's c1b_nobe
figure), frozen ts4 on its `utc-4` clock -$8.83/trade (-$6,402 over 725). Risk facts
(ts4_v2, realistic): 59% of trades risk more than $600, worst trade -$904.90.

## Proposed core changes (for the plugin-runner agent; none made here)

1. **`ModifyStop(tag, stop_price: Ticks)` intent (Option A, wanted next).** Replace the
   resting protective stop of an open trade from the next bar on (a new `WorkingOrder`
   version at `now`, as `SimBook.modify` already supports), refuse a loosening move
   (`rejected: stop_loosen`) and a tag without an open trade (`rejected: not_open`), report
   `accepted` with role `stop`. About 40 lines in `broker.py` plus routing in `host.py`
   (`_intents`/`_route_one` accept the type) and tests. Then, in this plugin: a pure
   `trail_step` extracted from `sim._scan_exit` (sim untouched), the BE/trail path at each
   bar close, `ts4_v2_be.yaml` (= `c1b`, BE 40/4, trail 40/30 step 4, research OOS
   $18.25/trade), and parity against `c1b`. Until then the plugin refuses any config whose
   BE/trail could fire.
2. **`services.risk` (read-only).** `services.fills` landed in `f36b6ed` and TS4 now uses it
   (refuses `trade_through_ticks > 1` under the inert rule, logs the cost model). A `risk`
   view would let it assert `max_contracts >= max qty` at start.
3. `FillsBlock.ambiguous: stop_first | target_first` (the bot's own rule was `target_first`).
4. `EnterMarket.levels_from: "open" | "fill"`: NinjaTrader-style brackets from the slipped
   fill; closes the realistic-cost gap above (~$1.8/trade).
5. Optional: slippage on `Flatten last_close` (matches the research session-close exit), an
   account-level risk group across plugins, `BarNeed.interval_s = 30` (TS4's native bars;
   low priority, 1m is validated).

## How to run

```bash
cd members/alantiix/skylit-futures-strategy-engine
export PYTHONPATH=$PWD/src:$PWD
# plugin tests (107, ~1.5 min)
.venv/bin/python -m pytest research/ts4/plugin/tests -q -p no:cacheprovider
# real-data parity, full in-sample year, both configs in one host (~4 min, needs ~/.skylit-fse/cache)
.venv/bin/python -m pytest research/ts4/plugin/tests -m localcache -q -p no:cacheprovider
# parity report for any window / cost set: zero | tt1 | real | together
.venv/bin/python -m research.ts4.plugin.tests.realdata --set real --first 2025-10-13 --last 2026-10-02 --out /tmp/ts4.json
```

Paper run on live data (needs `PROJECTX_USERNAME`, `PROJECTX_API_KEY`, and `SKYLIT_API_KEY`
while `data_plane.skylit.enabled` is true): `fse run --config configs/runner.ts4.example.yaml`.
It runs ts4_v2 only. Before 2026-12: add `MNQH7` to `range.yaml` offsets; the repo calendars
end 2026-12-31 (the first `no_calendar` skip now logs an error).
