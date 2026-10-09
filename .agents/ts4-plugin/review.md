# TS4 as a host-brokered plugin with frozen `ts4` and Option-B `ts4_v2` configs

Review date 2026-10-09, worktree `kiro/ts4-plugin` at `be9718a`, all changes uncommitted.

TS4 ships as `research.ts4.plugin:Ts4Plugin`, a `StrategyPlugin` subclass that trades 1m MNQ through the committed `PluginHost` and per-plugin paper broker. The live code is a small subpackage (`strategy.py`, `rules.py`, `warmup.py`, `paper_io.py`). It reuses the research package's config schema, sizing (`qty_for`, `target_ticks_for`), EMA coefficients, Globex calendars and range back-adjust offsets instead of re-deriving them. Two presets exist: `ts4` (default, the frozen YAML, byte-identical to the research copy) and `ts4_v2`, which is Option B and equals `VARIANTS["c1b_nobe"]` with breakeven and trailing off. Full-year real-data parity is exact at zero cost and at tt 1 + $1.48: 880/880 for ts4 and 959/959 for ts4_v2, alone or both in one host. No file under `src/fse/live/**` changed, and the plugin-runner worktree was not touched.

Watch for: the inert-BE/trail rule for frozen `ts4` holds only with `fills.trade_through_ticks <= 1`, which the plugin cannot check at this base (confirmed, documented). Paper P&L at the example's realistic fills runs about $1.8/trade below the research because brackets come from the unslipped open (confirmed, documented). Two git-crypt `__init__.py` files show as modified in the worktree (confirmed, environment artifact).

**Verdict**: APPROVED

## High-level view

The plugin follows the committed API conventions without bypassing them. It imports the host API only from `fse.live.plugins`, declares `DataNeeds(bars=(BarNeed("MNQ"),), hours="eth", brokerage="host", hold_through_break=False)`, returns `EnterMarket` with one `tp1` leg and `freeze_from_ns` at the session close, and returns `Flatten(tag, "session_close", "last_close", stamp)`. Start checks go in the constructor, so the loader turns them into `PluginLoadError`. The loader test resolves the dotted path. Config arrives through the plugin `config` block as a closed, strict pydantic model.

The TS4 rules come from the research code. Sizing is `floor(600 / (base × $0.50))`. Target ticks use `target_ticks_for`, which rounds half up, matching NinjaTrader's `Math.Round(..., AwayFromZero)` in `HTSTimeSlotEMA.cs`. It differs from ceil only for x.25 fractions, and no configured stop produces one. The progressive add-on uses the sim's reset order. EMA 692 on 30s becomes 346 on 1m and is bit-equal to `ema_series` in the back-adjusted frame. The plugin covers four slots, a `utc-4`/`ny` clock, one position at a time (checks `open_tags` and `working_tags`), and a calendar session-close flatten with a 90 s wake fallback.

Option A is deferred cleanly. No stop-modify intent or BE/trail path exists in core or the plugin. Any config whose BE/trail could fire is refused with a "ModifyStop (Option A, deferred)" error. TODOs for `ts4_v2_be.yaml` remain in `ts4_v2.yaml` and the README. Frozen `ts4` keeps BE/trail enabled in its YAML. The plugin runs them off under the inert rule (target + 1 ≤ trigger, 40 ≤ 40). The claim is verified: over the full year the sim with them on has 0 stop moves and equals the sim with them off. A control test shows the comparison would catch a firing rule on ts4_v2.

The evidence has parity match counts, an explanation of the realistic-cost mismatches (every entry matches; exits shift with the bracket reference), the paper-broker vs research cost table, a three-plugin coexistence test (ts4, ts4_v2 and an opposing `RecordingPlugin`), and the README risk note. Caps are per plugin, with no account-level cap, and ts4 + ts4_v2 together double MNQ exposure. The example ships ts4_v2 disabled for that reason. The remaining gaps are operational and do not block.

<details>
<summary>Issues (5)</summary>

1. **Inert-BE/trail depends on unreadable fills**: confirmed. Frozen `ts4` is safe to run with BE/trail off only when `fills.trade_through_ticks <= 1`, and the plugin cannot see the fills block at `be9718a`. After rebasing onto `f36b6ed`, read `services.fills` and refuse `trade_through_ticks > 1` when the inert rewrite applied.
2. **Restored EMA kept across a missed session**: likely. `EMA_STALE_NS` is 4 days, so after a restart that follows a missed trading session, the EMA has no bars from that session and the first slots can take a different side than the research. Re-warm from the cache whenever the gap since `last_open_ns` is longer than the scheduled break or weekend.
3. **Calendar/offset expiry is quiet**: likely. After 2026-12-31 every slot skips `no_calendar`, and that is logged only as an info-level `ts4_skip`. Log it as an error once, the way `unknown_contract` is logged. The README's "add MNQH7 offsets and extend calendars before Dec 2026" note is the only other guard.
4. **Realistic-fill gap vs research**: confirmed, documented. With the example's slip 1, brackets from the unslipped open cost about $1.76 (ts4) and $1.82 (ts4_v2) per trade compared with the research. Expect paper results below the research figures until the proposed `EnterMarket.levels_from: "fill"` lands.
5. **Git-crypt artifacts in the worktree**: confirmed. `research/edge_study/tests/__init__.py` and `research/matrix_heatseeker/tests/__init__.py` show as modified (0 bytes vs a 22-byte encrypted empty blob, mtime at worktree setup). `verification.md` claims no tracked file changed. Leave these two files unstaged; restore them or ignore them when committing.

</details>

<details>
<summary>Details</summary>

## Registration and hook contract

```
runner YAML ── module: research.ts4.plugin:Ts4Plugin, config: {ts4_config: ts4|ts4_v2|path, overrides, late_bar_s, warmup_sessions}
   │
loader ─► Ts4Plugin.__init__ ── parse_params ─► resolve_plugin_config (YAML → 1m overlay → overrides → _check → inert rewrite)
   │                                              ValueError ⇒ PluginLoadError (exit 2)
PluginHost ─► on_start (config.resolved.yaml, ts4_start log, EMA warm-up via run_blocking)
          ─► on_day_open (slot instants, missed_start) / urgent_bar_closes
          ─► on_bar ── EMA update ─► session-close Flatten ─► signal bar ⇒ EnterMarket | skip row
          ─► on_order_update (accepted / rejected / expired / filled_entry / closed ⇒ trade row, progressive)
          ─► next_wake/on_wake (warm-up apply, missing close-bar flatten)
          ─► snapshot/restore (version 1, ValueError on bad shape)
```

`JsonValue` comes from `fse.logio.canonical_json`, the same source the committed API and `tests/fakes/plugins.py` use. The AST import test allows it because it bans only `fse.live.*` outside `fse.live.plugins`. The additive `PaperFills`/`recordings_root` change on `f36b6ed` does not break any hook this plugin implements.

## The inert-BE/trail rule and its dependency on fills

`be_trail_inert` checks `max(target) + 1 <= min(trigger)`. A bar closing `trigger` ticks in profit has an extreme at least `target + 1` beyond entry, so the target (or, stop-first, the stop) fills on that bar. With slippage 1 the host's target sits one tick closer than the research's, so the margin grows, and the realistic-cost evidence also shows 0 stop moves. The rule breaks at `trade_through_ticks >= 2`. Then a bar can close past the trigger without filling the target, and the research bot would have moved the stop while the plugin does not. That is a silent divergence. The example config and README both say to keep it at ≤ 1. Enforcement waits on `services.fills` (issue 1).

## EMA warm-up and restore

`_maybe_warm` keeps a restored EMA if its last bar is within 4 days of `now`. The plan's rule was "current or previous Globex day". Either rule keeps an EMA that is missing a whole session when the runner was down for that session and the bar store has no recordings to replay as catch-up. With a 346-bar span (about 6 hours of 1m bars), the morning slots after such a restart can take a different side than the research would (likely; issue 2). A cache re-warm costs one `run_blocking` job, so re-warming on any gap longer than the scheduled break is the cheaper fix.

## Session close, calendars and contract rolls

The session close comes from `GlobexSessions.build` per Globex day, cached in `_closes`. A day outside the repo calendars returns `no_calendar`, and every slot is skipped with an info-level row. A contract missing from `range.yaml` offsets is logged as an error once and skips `unknown_contract`. Both expire around December 2026: the calendars end 2026-12-31, and `MNQH7` has no offset yet. Only the contract case gets an error-level log (issue 3).

## Parity and the cost-model gap

The harness (`plugin/tests/replay.py`, `realdata.py`) drives the committed `PluginHost` with `tests.fakes` and compares 15 fields per trade against `simulate` on the plugin's declared config. For ts4 this means BE/trail enabled, so the inert claim is tested and not assumed. For ts4_v2 the declared config is the YAML that `test_variants.py` asserts equals `c1b_nobe`. Every case is exact at zero cost and at tt 1 + $1.48. The realistic case (slip 1) shows 0 exact rows by design. Entries agree on every trade. Exits shift by one tick because the host prices brackets from the unslipped open. That changes the exit bar on 33/25 trades and the progressive add-on on 3/2. The year-level effect is ts4 $2.54 vs $4.30 per trade and ts4_v2 $17.97 vs $19.79 (issue 4). Session-close flattens carry no slippage in the host, the smaller second difference. Both are in the README and the verification fill-model table.

</details>

<details>
<summary>File map</summary>

- `research/ts4/plugin/__init__.py`: exports `Ts4Plugin`
- `research/ts4/plugin/strategy.py`: the plugin hooks, bookkeeping, outputs, snapshot/restore
- `research/ts4/plugin/rules.py`: config resolution and start checks, slot instants, `Ema`, `Progressive`, `bracket`
- `research/ts4/plugin/warmup.py`: EMA warm-up job from the cache
- `research/ts4/plugin/paper_io.py`: CSV headers and atomic writes
- `research/ts4/plugin/README.md`: configs, risk note, rules, cost-model differences, operations
- `research/ts4/plugin/tests/`: replay/parity harness and 90 tests (1 localcache)
- `configs/runner.ts4.example.yaml`: ts4 enabled, ts4_v2 disabled, realistic fills
- `research/ts4/ts4_v2.yaml`: Option B (BE/trail off), Option A TODO
- `research/ts4/market1m.py`: `extra_bars` passthrough
- `research/ts4/tests/test_config.py`, `test_market1m.py`, `test_variants.py`: v2 = c1b_nobe, `extra_bars`
- Full diff: `git -C <worktree> status --short` (all new files untracked; research-only modules sit outside `plugin/`)

</details>
