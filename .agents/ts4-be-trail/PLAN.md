# TS4 Option A: breakeven + trailing via ModifyStop (`ts4_v2_be` = research c1b)

## Environment (every item)

```sh
W=/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-be-trail
E=$W/members/alantiix/skylit-futures-strategy-engine
V=$W/.venv/bin
cd "$E" && export PYTHONPATH="$E/src:$E"
"$V/python" -c "import fse, research; print(fse.__file__, research.__file__)"   # both under $E
```

- Absolute paths only. Relative paths land in the parent workspace.
- Branch `kiro/ts4-be-trail` at base `6688bf9`. Commit locally after each item (`feat(ts4): ...`,
  `test(ts4): ...`, `docs(ts4): ...`, the style of `259288e`). Stage by explicit path. Never push,
  never stash.
- Do not modify anything under `$E/src/**` or `$E/tests/**` (engine core and its fakes). Never
  write in `.worktrees/plugin-runner`, `.worktrees/plugin-api-r1`, `.worktrees/ts4-plugin`, or
  their branches. Any core need goes into "Core proposals" at the end of this plan.
- Never take `~/.skylit-fse/live-state/runner.lock`. No network, no `fse pull`, no `.env`.
  `~/.skylit-fse/cache` exists and is read-only (localcache tests read it).
- Test commands (from the prior TS4 verification.md):
  - plugin: `"$V/python" -m pytest research/ts4/plugin/tests -q -p no:cacheprovider`
  - plugin real data: `"$V/python" -m pytest research/ts4/plugin/tests/test_parity_localcache.py -m localcache -q -p no:cacheprovider`
  - research: `"$V/python" -m pytest research/ts4/tests -q -p no:cacheprovider`
  - lint: `"$V/ruff" check research/ts4 --extend-exclude research/ts4/handoff`,
    `"$V/ruff" format --check research/ts4 --extend-exclude research/ts4/handoff`,
    `"$V/mypy" research/ts4 --exclude '^research/ts4/handoff/'`

## Facts the plan relies on (read, not assumed)

- `ModifyStop(tag, stop_price)` (`src/fse/live/plugins/api.py:248`). Broker `_modify_stop`
  (`src/fse/live/host/broker.py:442`) answers with exactly these, all `role="stop"`:
  - `accepted`, `detail = {stop_price, previous_stop, effective_ns, unchanged: False}`
  - `accepted` with `unchanged: True` and `effective_ns: None` (same price)
  - `rejected` with reason `not_open` (no open trade, or the stop order is gone),
    `invalid_order` (not an int, or `SimBook.modify` raised), or `stop_loosen`
  - Effective time = `last_bar_open + 60 s`, the next bar the book has not processed. Not
    gated by halt, blocks or caps. `Trade.initial_stop` never changes.
- Host per bar (`host.py:_deliver_bar`, also inside a multi-bar `BarsEvent` and during
  `catch_up` replay): broker `on_bar(bar)` -> `on_order_update` rounds -> `on_bar`. A trade that
  exits on bar j is closed (and removed from `Ts4Plugin._open`) before `on_bar(j)` runs.
- Research rule (`research/ts4/sim.py:_scan_exit`). At the close of bar j (fill bar included,
  session-end bar `e` excluded) for a trade still open after bar j:
  `prof = (c[j] - entry) * side`; BE: `prof >= be.trigger` -> `new = tighter(new, entry + side*offset)`;
  trail: `prof >= tr.trigger` and `(c[j] - side*distance - new) * side >= step` -> `new = that level`
  (compared with the post-BE `new`); applies from bar j+1. The stop never loosens.
  `counters["stop_moves"]` counts each change. `entry` is the slipped fill.
- Plugin bugs this work must avoid in `strategy.py:on_order_update`:
  - `accepted` sets `info.status = "working"`, which would demote an open trade.
  - `rejected` with `info.status == "open"` sets `flatten_failed = True`, which would disable
    the session-close flatten.
  - Stop-role updates must be routed first.
- `_closed` computes `raw_exit`/gap from `trade.initial_stop`. With a moved stop that is wrong.
- `_open_from_json` requires the exact field set. Adding `_Open` fields would make every
  existing `plugin.json` (a running ts4_v2) fail restore unless missing new fields default.
- `replay.plugin_entry` uses `stop_fill: gap_open`, matching the sim's gap rule.
  `replay.compare` compares `stop_t` = initial stop, which is right for c1b too.

## Decisions

1. **`trail_step` lives in `research/ts4/plugin/rules.py`**, as a pure function over
   `research.ts4.config.Breakeven`/`Trailing`. `sim.py` stays untouched. `rules.py` may not
   import `research.ts4.sim` (`test_plugin_imports.py`). Equivalence with `_scan_exit` is
   proven by a test, not by sharing code.
2. **BE/trail in the plugin use the actual fill price (`entry_t`)** for `prof` and the BE
   level, as the research and NinjaTrader do. The initial stop is the broker's
   (`ref - side*stop_ticks`, ref = the unslipped fill-bar open). At zero slippage both equal
   the sim exactly. With slippage the initial stop already differs by design (documented
   cost-model row).
3. **Commit the stop on `accepted`, not on emit.** `pending_stop` holds the in-flight price.
   The accepted update arrives in the same host round, before the next bar, so the next
   evaluation always sees the broker's stop. Only send when `new != cur_stop`, so the plugin
   move count equals the sim's `stop_moves`.
4. **Emit during `catch_up` too.** The rule is per bar close and the effective time is
   bar-based, so a restart replay reproduces the uninterrupted run.
5. **Start checks.** BE/trail enabled and provably inert (frozen `ts4`): keep today's
   rewrite-to-off and the `fills.trade_through_ticks <= 1` refusal, byte-identical.
   - Rationale: `ts4`'s resolved config, sha, `config.resolved.yaml`, start log and trades
     stay identical, and it sends zero ModifyStop.
   - BE/trail enabled and not inert: previously refused (`NO_MODIFY_STOP`). Now run live
     (`Resolved.be_trail_live = True`) with **no trade-through limit**. The plugin applies the
     rule itself at every close, so the research divergence the limit guarded against cannot
     arise.
   - Lifting `ts4`'s tt>1 refusal by running its BE/trail live is possible now. It changes a
     frozen, disabled reference config, so it is documented as an option and not done.
6. **No `trades.csv` header change.** It would change ts4/ts4_v2 outputs and mixed-header day
   files on restart. Stop moves go to `plugin_log.jsonl` (`ts4_stop_move`), and `ts4_trade`
   gains `stop_moves`.
7. **`STATE_VERSION` stays 1.** The new `_Open` fields are optional on restore (defaults), so
   a running ts4_v2 `plugin.json` restores after the upgrade.
8. **reference_t (optional item 7)** is a plugin param `bracket_reference: fill_open | signal_close`,
   default `fill_open` (today). Evaluated on real data, but the default and the example
   never change.

# Implementation Plan

- [ ] 1. Add the pure `trail_step` and prove it equals `sim._scan_exit`.
      Add `trail_step(side: int, entry: int, stop: int, close: int, be: Breakeven, tr: Trailing) -> int`
      to `rules.py`, the sim's arithmetic line for line:
      ```python
      prof = (close - entry) * side
      new = stop
      if be.enabled and prof >= be.trigger_ticks:
          lvl = entry + side * be.offset_ticks
          new = max(new, lvl) if side == 1 else min(new, lvl)
      if tr.enabled and prof >= tr.trigger_ticks:
          lvl = close - side * tr.distance_ticks
          if (lvl - new) * side >= tr.step_ticks:
              new = lvl
      return new
      ```
      Export it in `__all__` and list it in the module docstring. Import `Breakeven, Trailing`
      from `research.ts4.config`.
      New test file `research/ts4/plugin/tests/test_trail_step.py`:
      - Unit cases: long and short; below trigger -> unchanged; BE only; trail only; trail step
        3 vs 4 ticks; BE then trail on the same close (trail compared to the post-BE stop); a
        retreating close never loosens; BE offset when the stop is already tighter.
      - Equivalence test (the test may import `research.ts4.sim`): bars from
        `replay.synth_sessions(DAYS, seed, step=10).research_bars()`, `SimData`, and the cfg =
        `resolve({"ts4_config": "ts4_v2"}).cfg` with BE 40/4 and trail 40/30/4 enabled
        (stop_first). For many fill bars f and both sides, use `entry = o[f]`, stop/target from
        a slot (180/45), `e = data.session_end(gap)[1][f]` and `trig` = the min enabled trigger.
        Call `sim._scan_exit(...)` with a `Counter`, and compare its `(j, reason, exit_px, moved)`
        and `stop_moves` with a reference walk that checks exit on bar k (current stop, stop
        first), breaks at `e`, then applies `trail_step` on `c[k]`. Require at least one moved
        case so the test is not vacuous.
      Files: `$E/research/ts4/plugin/rules.py`, `$E/research/ts4/plugin/tests/test_trail_step.py`
      Verify: `"$V/python" -m pytest research/ts4/plugin/tests/test_trail_step.py -q -p no:cacheprovider` passes. The full plugin suite and the lint commands pass.

- [ ] 2. Add stop-tracking state and safe handling of stop-role updates (nothing emits ModifyStop yet).
      In `strategy.py`:
      - `_Open` gains `cur_stop: int | None = None` (the broker's current stop, raw ticks),
        `pending_stop: int | None = None` and `stop_moves: int = 0`. Add them to `_OPEN_TYPES`.
      - `_open_from_json` accepts the full set, or the full set minus these three new keys (an
        old snapshot): missing keys take the dataclass defaults, any other difference is still
        refused. On restore, an `open` trade with `cur_stop is None` and `ref_open` set gets
        `ref_open - side*stop_ticks`.
      - `filled_entry`: after `ref_open`, set
        `cur_stop = (ref_open if ref_open is not None else entry_t) - side*stop_ticks`.
      - In `on_order_update`, route `update.role == "stop" and status in ("accepted", "rejected")`
        to a new `_stop_update(info, update, ctx)` **before** the existing branches:
        - accepted, `unchanged` False: `cur_stop = int(detail["stop_price"])` (fallback
          `pending_stop`) and `stop_moves += 1`. If `detail["previous_stop"] != the prior cur_stop`,
          log a warning `stop_desync`. Log info `ts4_stop_move {tag, stop_price, previous_stop, effective_ns, stop_moves}`.
        - accepted, `unchanged` True: nothing.
        - rejected `not_open`: info `stop_move_skipped` (race: closed or flattened this bar).
        - rejected `stop_loosen` / `invalid_order` / any other reason: error
          `stop_move_rejected {tag, reason, detail}`. `cur_stop` stays (the broker's stop is
          unchanged and still protective).
        - Every case clears `pending_stop`, never touches `status`/`flatten`/`flatten_failed`,
          and calls `_flush`.
      - `_closed`, role `stop`: when `info.stop_moves > 0` and `info.cur_stop is not None`, use
        `cur_stop` instead of `trade.initial_stop` for `raw_exit` and the gap check. The row's
        `stop_t` stays `trade.initial_stop` (the sim's `stop_t`). BE-off configs keep the old
        code path exactly. Add `"stop_moves": info.stop_moves` to the `ts4_trade` log entry.
      - Update the module docstring (step 4: stop-role updates).
      Add to `test_plugin_restart.py`: a snapshot with the three new keys removed from every
      `open` entry restores, and an extra unknown key is still refused.
      Files: `$E/research/ts4/plugin/strategy.py`, `$E/research/ts4/plugin/tests/test_plugin_restart.py`
      Verify: the full plugin suite passes (existing parity, flow and restart tests unchanged). Lint passes.

- [ ] 3. Run BE/trail live: start-check change plus ModifyStop at each bar close (needs 1, 2).
      `rules.py`:
      - `Resolved` gains `be_trail_live: bool = False`. In `resolve_plugin_config`:
        - BE/trail enabled and `be_trail_inert` -> rewrite as today (`inert_rewrite=True`).
        - Enabled and not inert -> no rewrite, `be_trail_live=True`, note
          `"be_trail_live: breakeven/trailing move the stop through ModifyStop at each bar close"`.
      - Delete `NO_MODIFY_STOP` and its raise.
      - `check_fills` logic is unchanged (refuses tt>1 only when `inert_rewrite`). Update its
        docstring and message to say BE/trail-live configs (e.g. `ts4_v2_be`) have no limit.
      - Update the module docstring.

      `strategy.py`:
      - Import `ModifyStop` and `trail_step`.
      - In `on_bar`, after `_close_flattens`, when `self.resolved.be_trail_live`, extend `out`
        with `self._trail(bar, ctx)`. For each `_Open` with `status == "open"`,
        `flatten is None`, `cur_stop`/`entry_t`/`entry_open_ns` set,
        `bar.open_ns >= entry_open_ns`, `bar.close_ns < close_ns`,
        `bar.contract == info.contract` and `bar.c_t is not None`: compute
        `new = trail_step(side, entry_t, cur_stop, bar.c_t, cfg.breakeven, cfg.trailing)`. If
        `new != cur_stop`, set `pending_stop = new` and emit `ModifyStop(tag, new)`.
      - Do not skip on `catch_up` (decision 4).
      - Add `"be_trail_live"` to the `ts4_start` log entry.
      - Module docstring step 3a: BE/trail.

      New `research/ts4/plugin/tests/test_be_trail_flow.py`. Use the `run_case`/`Synth`/`_only`
      pattern of `test_plugin_flow.py`, config `ts4_v2` with overrides BE/trail enabled, `T1` only,
      EMA off. Each case also asserts `compare(sim.trades, rep.trades("p")).exact` against
      `simulate(r.declared)`:
      - a. The fill bar closes +40: the plugin log has `ts4_stop_move` at `entry+4` with
        `effective_ns` = next bar open. The next bar dips to `entry+4` -> reason `stop`,
        `exit_t = entry+4`, and a later same-day slot gets the progressive add-on (BE exit
        counts as a win, as in the sim).
      - b. Closes +40, +43, +50, +60: trail levels match the sim; the +43 bar sends no move
        (step < 4); a retreat sends none.
      - c. The target fills on the bar that would have moved the stop: no ModifyStop, no
        `stop_move_*` entries.
      - d. Profit at the 17:00 close bar: only `Flatten`, no ModifyStop on that bar.
      - e. After an accepted move the trade stays `open` (a slot while open is skipped
        `position_open`) and the session-close flatten still happens.
      - f. `_stop_update` directly. Get the plugin instance the replay host built, or construct
        `Ts4Plugin` with the harness's services. Feed `OrderUpdate(tag, "rejected", "stop_loosen", None, "stop", None, None, at)`
        and `not_open`; assert the log kinds and levels and that `status`, `flatten`,
        `flatten_failed`, `cur_stop` are unchanged.
      - g. Frozen `ts4` and plain `ts4_v2` on the synth walks: zero `ts4_stop_move` entries
        and `be_trail_live` False.

      Other test updates:
      - `test_plugin_restart.py`: stop the host mid-trade after a BE move, restart from
        `plugin.json`; the trail continues and trades equal the uninterrupted run.
      - `test_parity_synth.py::test_control_be_trail_would_change_ts4_v2`: replace the
        `pytest.raises(ValueError, match="ModifyStop")` with
        `resolve(...).be_trail_live is True` and `inert_rewrite is False`, and update the
        module docstring.
      - `test_fills_check.py`: a BE/trail-live config with `trade_through_ticks: 2` starts.
        The frozen ts4 refusal tests stay as they are.
      Files: `$E/research/ts4/plugin/rules.py`, `$E/research/ts4/plugin/strategy.py`, `$E/research/ts4/plugin/tests/test_be_trail_flow.py`, `$E/research/ts4/plugin/tests/test_plugin_restart.py`, `$E/research/ts4/plugin/tests/test_parity_synth.py`, `$E/research/ts4/plugin/tests/test_fills_check.py`, `$E/research/ts4/plugin/tests/test_rules.py` (if it asserts the refusal)
      Verify: the full plugin suite passes, including the unchanged case A/B synth parity for ts4/ts4_v2. Lint passes.

- [ ] 4. Ship `ts4_v2_be.yaml` (= c1b on the ny clock) and the `ts4_v2_be` preset.
      - `research/ts4/ts4_v2_be.yaml`: a copy of `ts4_v2.yaml` with `breakeven: {enabled: true, trigger_ticks: 40, offset_ticks: 4}`
        and `trailing: {enabled: true, trigger_ticks: 40, distance_ticks: 30, step_ticks: 4}`.
        Header: Option A = research c1b, paper only, OOS $18.25/trade (t 1.85, 95% CI -$1.1..+$37.6)
        vs c1b_nobe $17.11, moves applied through ModifyStop from the next bar.
      - `ts4_v2.yaml`: only the TODO comment lines change (to "Option A ships as ts4_v2_be.yaml");
        no value changes.
      - `rules.PRESETS["ts4_v2_be"]` and its docstring.
      - Tests (mirror the ts4_v2 ones):
        - `research/ts4/tests/test_variants.py::test_ts4_v2_be_yaml_is_the_c1b_variant_on_the_ny_clock`:
          `load_config(ts4_v2_be.yaml) == VARIANTS["c1b"].apply(apply_overrides(frozen, {"clock": "ny"}))`.
        - `research/ts4/tests/test_config.py::test_ts4_v2_be_is_ts4_v2_with_be_trail_on`:
          equals `apply_overrides(v2, {"breakeven": {"enabled": True}, "trailing": {"enabled": True}})`
          and pins 40/4 and 40/30/4.
        - `research/ts4/plugin/tests/test_rules.py`: the preset resolves with
          `be_trail_live=True`, `inert_rewrite=False`, `cfg == declared`, targets 58/45/45/53,
          qty 5/6/6/5.
        - The existing `test_ts4_v2_*` tests still pass unchanged.
      Files: `$E/research/ts4/ts4_v2_be.yaml`, `$E/research/ts4/ts4_v2.yaml`, `$E/research/ts4/plugin/rules.py`, `$E/research/ts4/tests/test_variants.py`, `$E/research/ts4/tests/test_config.py`, `$E/research/ts4/plugin/tests/test_rules.py`
      Verify: `"$V/python" -m pytest research/ts4/tests -q -p no:cacheprovider` and the plugin suite pass. Lint passes.

- [ ] 5. Parity of `ts4_v2_be` against c1b (synthetic and the full real in-sample year) (needs 3, 4).
      - `test_parity_synth.py`: add `test_case_d_ts4_v2_be_option_a`, parametrized over seeds x
        `(tt, comm) in [(0, "0.00"), (1, "1.48")]`, through `_case(..., "ts4_v2_be")`. Assert:
        - `cmp.exact` and `cmp.sim >= 8`
        - `cfg` BE/trail on
        - `any(t.stop_moved for t in sim.trades)` (not vacuous)
        - the plugin's `ts4_stop_move` count (from the replay's `plugin_log.jsonl`, via
          `PLUGIN_LOG_FILE_NAME` / `read_jsonl`) == `sim.counters["stop_moves"]`
        - zero `stop_move_rejected`/`stop_desync`
      - `realdata.py`: add `Case("ts4_v2_be", "ts4_v2_be")` to ZERO_COST, plus the tt1 and real
        twins. Add `res["stop_moves"] = {"sim": ..., "plugin": ...}` and
        `res["stop_move_rejects"]` from the plugin log. Keep `be_trail_inert` for the inert case
        only (`r.inert_rewrite`).
      - `test_parity_localcache.py`: include `ts4_v2_be` in the together run. Assert exact,
        `stop_moves` equal and > 0, and zero rejects. ts4/ts4_v2 assertions stay as they are.
      - Run `"$V/python" -m research.ts4.plugin.tests.realdata --set real --out /tmp/ts4be-real.json`
        and `--set tt1`. Record trades, win rate, net, $/trade and worst for ts4_v2_be vs sim c1b
        in `$W/.agents/ts4-be-trail/verification.md`, then delete the /tmp files.
      - If a mismatch appears, explain it field by field in verification.md. Never relax a
        parity assertion to pass.
      Files: `$E/research/ts4/plugin/tests/test_parity_synth.py`, `$E/research/ts4/plugin/tests/realdata.py`, `$E/research/ts4/plugin/tests/test_parity_localcache.py`, `$W/.agents/ts4-be-trail/verification.md`
      Verify: the plugin suite passes, and the localcache command passes (about 6 min). ts4 880/880 and ts4_v2 959/959 are still exact; ts4_v2_be is exact against c1b at zero cost.

- [ ] 6. Runner example entry (disabled) and README.
      - `configs/runner.ts4.example.yaml`: append a `ts4_v2_be` entry with
        `enabled: false`, `config: {ts4_config: ts4_v2_be}`, the same fills as ts4_v2, risk
        `max_contracts: 6, max_open_trades: 1, max_trades_per_day: 4`, and
        `hold_through_break: false`. Comment: Option A = c1b, BE/trail through ModifyStop,
        paper only, enable it instead of ts4_v2, never next to it (same slots, doubled
        exposure). Update the header "Which TS4" bullets and the tt<=1 note (frozen ts4 only).
      - `test_runner_example.py`: the id->`ts4_config` map gains `ts4_v2_be`, `plugin_ids`
        gains it in file order, and the enabled list stays `["ts4_v2"]`.
        `test_fse_run_example.py` must still pass unchanged.
      - `research/ts4/plugin/README.md`:
        - Configs table: add a `ts4_v2_be` row; replace the "Option A ... TODO" paragraph.
        - Start-check paragraph: rewrite per decision 5, incl. the documented ts4 tt>1 option.
        - New "Breakeven and trailing" rules section: bar close, `entry_t` reference, next-bar
          effect, never loosens, catch-up, `ts4_stop_move` log, not gated by halt, under
          `at_stop` a through-market stop fills at the stop (paper-optimistic), rejections handling.
        - Risk note for ts4_v2_be: paper only; OOS t 1.85 (< 2); ~59% of trades risk more than
          $600 (same initial brackets as ts4_v2); worst trade about -$905. Do not run it next to
          ts4_v2.
        - Cost-model section: the ts4_v2_be IS figures from item 5. Note that
          `EnterMarket.reference_t` now exists (brackets from a given price) and how item 7
          uses it.
        - Tests section: the new test files.
      - Remove Option A from any "deferred/TODO" wording in the plugin package docstrings.
      Files: `$E/configs/runner.ts4.example.yaml`, `$E/research/ts4/plugin/tests/test_runner_example.py`, `$E/research/ts4/plugin/README.md`
      Verify: `"$V/python" -m pytest research/ts4/plugin/tests/test_runner_example.py research/ts4/plugin/tests/test_fse_run_example.py -q -p no:cacheprovider` passes; the full plugin suite passes.

- [ ] 7. OPTIONAL: opt-in `bracket_reference` using `EnterMarket.reference_t`; the default is unchanged.
      - `Ts4PluginConfig.bracket_reference: Literal["fill_open", "signal_close"] = "fill_open"`.
      - Start check: `signal_close` needs `services.broker_terms` (refuse with `Ts4PluginError`
        if `None`).
      - `_Open.reference_t: int | None = None`: optional on restore, like item 2.
      - In `_decide` with `signal_close`: `ref = bar.c_t + side * broker_terms.slippage_ticks`
        (approximates the research's slipped next-open fill), passed as
        `EnterMarket(..., reference_t=ref)` and stored.
      - `filled_entry` uses `info.reference_t` as `ref_open` when set (the target in `_closed`
        and the initial `cur_stop` follow).
      - `expired`/`reference_gap` already ends as an `expired:reference_gap` skip row (no trade,
        no add-on). Log it.
      - Tests in `test_plugin_flow.py`:
        - brackets measured from `c_t + slip`
        - a gapping open gives the `reference_gap` skip
        - `fill_open` emits `reference_t=None`
      - Evaluation:
        - `realdata.Case` gains `params: Mapping[str, Any]` (default empty, merged into the
          plugin config).
        - Add `--set real_ref` (ts4_v2 and ts4_v2_be with `bracket_reference: signal_close`,
          realistic fills).
        - Run on the IS year. Record in verification.md and the README cost-model table:
          $/trade for fill_open vs signal_close vs the sim (the ~$1.8/trade gap), plus the
          `reference_gap` count.
      - Keep the default and the example unchanged whatever the result. The README tells how
        to opt in. Skip this item only if items 1-6 consumed the loop; if skipped, say so in
        verification.md.
      Files: `$E/research/ts4/plugin/rules.py`, `$E/research/ts4/plugin/strategy.py`, `$E/research/ts4/plugin/tests/test_plugin_flow.py`, `$E/research/ts4/plugin/tests/realdata.py`, `$E/research/ts4/plugin/README.md`, `$W/.agents/ts4-be-trail/verification.md`
      Verify: the plugin suite passes, all synth/localcache parity is unchanged with the default, and the real_ref run completes with figures recorded.

- [ ] 8. Final verification and evidence.
      Run and record in `$W/.agents/ts4-be-trail/verification.md` (command, result, duration):
      - the plugin suite
      - localcache parity
      - `research/ts4/tests`
      - `"$V/python" -m pytest research -q -p no:cacheprovider`
      - the engine plugin-host unit files from the prior verification (`tests/unit/test_plugin_api.py`
        `test_plugin_loader.py` `test_plugin_paper_broker.py` `test_plugin_host_dispatch.py`
        `test_plugin_host_persistence.py` `test_plugin_host_rotation.py` `test_runner_config.py`
        `test_runner_paper_only.py`)
      - the three TS4 lint commands
      - `git -C $W diff --stat 6688bf9 -- members/alantiix/skylit-futures-strategy-engine/src members/alantiix/skylit-futures-strategy-engine/tests` -> empty (core untouched)
      - `git -C $W status --short` -> clean after commits
      Files: `$W/.agents/ts4-be-trail/verification.md`
      Verify: every command above exits 0 and the core diff is empty.

## Core proposals (not implemented here)

1. `ModifyStop` docstring and README: add the same-return `Flatten(next_open)` + `ModifyStop`
   caveat (plugin-api-r1 follow-up 1). TS4 is unaffected: it flattens with `last_close` and
   never sends both for one tag in one return.
2. `FillsBlock.ambiguous: stop_first | target_first` and `levels_from: fill` (slipped-fill
   brackets) would close the remaining realistic-cost gap without the signal-close
   approximation of item 7.
3. An optional `detail["current_stop"]` on `stop_loosen` rejections would let a plugin resync
   after a desync instead of only logging it.

## Gaps and assumptions

- Research OOS figures (t 1.85, $18.25, 59% > $600, worst ~-$905) come from
  `TS4_improvements.md` and the prior REPORT (the worst-trade figure is ts4_v2's; c1b shares
  its initial brackets). Item 5 adds the plugin's own IS figures and does not recompute the OOS.
- The session end: the sim uses a 15-min data gap, the plugin uses the calendar close. These
  already align on the IS year (959/959). BE/trail adds no new boundary.
