# TS4 Option A (`ts4_v2_be` = c1b) — verification

Branch `kiro/ts4-be-trail`, base `6688bf9`, HEAD `3a646d9`. Not merged, not pushed.

## Final verification step (supersedes the coder's record below where they differ)

No parity mismatch was a plugin bug: no plugin code changed. One commit added tests:
`3a646d9 test(ts4): BE/trail on stop/target/gap bars, broker rejections, ts4_v2_be next to
other plugins`. Scratch scripts (not committed): `.agents/ts4-be-trail/verify_parity.py`,
`.agents/ts4-be-trail/explain_real.py`. `$W`, `$E`, `$P`, `$V` and the script commands are
in the environment block of the coder's record below.

### Independent IS-year parity (Atlas 1m, Globex days 2025-10-13..2026-10-02, 319,774 bars)

Harness: the same replay as `realdata.py` (PluginHost + PluginPaperBroker, one host per case,
EMA warm-up 60 sessions). Two differences make it independent of the coder's run:

- The research config comes from `research.ts4.variants` (`baseline`, `c1b_nobe`, `c1b` on
  the `is1m` dataset patch: ema 346, stop_first, 1m bars), not from the plugin's declared
  config. Asserted equal to the declared config (+ host costs) for every case: true.
- The sim's per-trade stop-move sequence is recorded by wrapping the `Counter` that
  `simulate()` hands to `_scan_exit` (reads `f`, `j`, `new` at each `stop_moves` increment;
  `sim.py` unchanged). Compared per trade with the plugin's `ts4_stop_move` entries as
  `(effective bar open ns, stop price back-adjusted)`. The sim's effective time is the close
  of the move bar j (applies from bar j+1).

Zero cost (`verify_parity.py --set zero`, 185-189 s per case):

| case | research | rows exact | stop moves sim / plugin | moved trades | per-trade stop sequences equal | win rate | net | $/trade |
|---|---|---|---|---|---|---|---|---|
| ts4 | baseline | **880/880** | 0 / 0 | 0 | 880/880 | 85.23% | $18,844.50 | 21.41 |
| ts4_v2 | c1b_nobe | **959/959** | 0 / 0 | 0 | 959/959 | 87.28% | $32,710.00 | 34.11 |
| ts4_v2_be | c1b | **959/959** | **77 / 77** | 75 / 75 | **959/959** | 87.49% | $31,583.00 | 32.93 |

ts4_v2_be detail: 73 trades moved once, 2 twice (both sides identical). Moved trades: 47
target and 28 stop exits, 100% winners, $7,220.50 ($96.27/trade) in both. Every moved trade's
row is exact (75/75). Plugin log: 0 `stop_move_skipped`, 0 `stop_move_rejected`,
0 `stop_desync`. `Trade.stop_moved` agrees with the recorded sequence on every sim trade, and
`ts4_trade.stop_moves` agrees with the `ts4_stop_move` count on every plugin trade. Exit 0,
no faults, 0 break backstops in all three hosts.

Zero mismatches at zero cost, so there is nothing to explain or fix.

### Realistic costs (slip 1, tt 1, $1.48 RT): ts4_v2_be vs research c1b

`verify_parity.py --set real`, 196 s.

| | trades | win rate | net | $/trade | worst | best | stop moves | moved trades |
|---|---|---|---|---|---|---|---|---|
| research c1b (sim) | 959 | 86.65% | $17,068.94 | 17.80 | -$904.90 | $137.60 | 81 | 76 |
| ts4_v2_be (plugin) | 959 | 86.76% | $15,054.94 | 15.70 | -$907.40 | $135.10 | 77 | 72 |

Per trade (959 paired by `entry_open_ns`, 0 sim-only, 0 plugin-only):

- Plugin minus sim net: mean -$2.10, median -$2.50, min -$3.00, max +$159.00.
  926 trades lower, 28 equal, 5 higher.
- Exit reason equal on 954. Flips: 4 stop -> target, 1 session_close -> target. 1 win/loss flip.
- Fields that differ: `target_t` and `stop_t` 959 (all), `exit_t`/`gross_ticks`/`net_cents`
  931, `exit_close_ns` 24, `reason` 5. Rows identical: 0/959.
- Stop-move sequences equal on 955/959. All 72 trades the plugin moved have the same
  sequence as the sim (same bars, same levels). The 4 others are moved by the sim only.

Why (expected, by design, the documented cost-model row): the paper broker measures brackets
from the unslipped fill-bar open, the research from the slipped fill. The plugin's long target
is therefore 1 tick closer and its stop 1 tick further. BE and trail use the slipped fill
`entry_t` in both, so the move levels and bars agree wherever both trades are still open.

The 4 sequence mismatches are trades where the plugin's closer target fills first.
`explain_real.py` checked all 76 sim-moved trades. It found exactly these 4. On each, the
plugin target (+ 1 tick trade-through) is touched on the sim's move bar or earlier, and the
bar's high equals the sim target exactly, 1 tick short of the sim's fill:

| entry_open_ns | side | sim exit | sim target / plugin target | sim move bar | plugin target bar |
|---|---|---|---|---|---|
| 1769100000000000000 | long | target | 105980 / 105979 | f+0 | f+0 |
| 1777988700000000000 | long | stop (BE) | 114770 / 114769 | f+0 | f+0 |
| 1778507100000000000 | long | stop (BE) | 120040 / 120039 | f+3 | f+3 |
| 1779921300000000000 | long | target | 122607 / 122606 | f+1 | f+1 |

The plugin closes these at its target on that bar, so no move is due. That accounts for
81 - 77 = 4 moves and 76 - 72 = 4 moved trades. The two sim BE-stop trades among them are 2 of
the 4 stop -> target reason flips. This corrects the coder's note below, which put the 81/77 gap down to stop levels
changing when the trigger is reached. The trigger and levels match on every shared trade.

### Synthetic scenarios (all with sim parity unless noted)

| required scenario | test |
|---|---|
| BE fire | `test_be_trail_flow.py::test_a_*` (move to entry+4, `effective_ns` = next bar open, BE stop counts as a win); `test_parity_synth.py::test_case_d_*` (3 seeds x zero / tt1+$1.48, move count = sim) |
| trail steps | `test_b_*` (+10/+20/+25, a +43 close sends nothing: step < 4) |
| trail never loosens | `test_b_*` (retreat sends nothing); `test_trail_step.py` unit cases; **new** `test_k_*`: a scripted loosening ModifyStop is refused by the real paper broker (`stop_loosen`) and the stop stays |
| ModifyStop rejected | **new** `test_k_*`: real broker `not_open` (sent with the entry, still working: info `stop_move_skipped`, entry fills normally) and `stop_loosen` (error `stop_move_rejected`, `cur_stop` stays +10, the trade stops out at +10 = sim); `test_f_*`: every answer fed directly (`stop_loosen`, `invalid_order`, unknown, `not_open`, accepted, unchanged, desync), status/flatten untouched |
| stop move on the same bar as a target/stop fill | `test_c_*` (target on the would-move bar: no move); **new** `test_h_*` (moved stop hit on a bar that closes +55, which would trail: exit at the moved stop, 1 move); **new** `test_i_*` (target and moved stop on one bar: stop first); **new** `test_j_*` (gap through the moved stop: fills at the open, `gap_fill` 1) |
| restart mid-trade with BE state | `test_plugin_restart.py::test_restart_after_a_stop_move_continues_the_trail` (snapshot holds `cur_stop`/`stop_moves`, one move before and one after the restart, trades = uninterrupted run); `test_a_snapshot_from_before_be_trail_restores_*` |

### Multi-strategy

- **New** `test_multi_strategy.py::test_ts4_v2_be_moves_its_own_stop_next_to_ts4_v2_and_an_opposing_plugin`
  (replay host: ts4_v2 + ts4_v2_be + a short RecordingPlugin). ts4_v2_be trades and stop moves
  equal its solo run, the moves are non-empty, and ts4_v2 sends none. Existing coexistence and
  halt tests pass.
- **New** `test_fse_run_example.py::test_fse_run_ts4_v2_be_next_to_matrix_and_playbook`
  (offline `fse run`, respx allow-list, the Matrix + Playbook day from
  `tests/integration/test_fse_run_one_globex_day.py` plus the example's ts4_v2_be entry
  enabled). All three run with no faults and exit 0. ts4_v2_be starts with `be_trail_live`
  true and trades its own book. Matrix ledger and Playbook run dir exist. The fake ProjectX
  bars never close above 24097 and the 09:45 long fills near 24059, so BE cannot trigger in
  this run. The test asserts no move and no stop errors, and the replay test above covers
  moves next to other plugins.

### Suites (final, at or after the last commit; plugin/lint/research rerun after 3a646d9)

| command | result | time |
|---|---|---|
| `"$V/python" -m pytest tests -q -p no:cacheprovider` (full engine suite) | **3512 passed, 1 failed**, 6 deselected. Failure: `tests/unit/test_harness.py::test_installed_versions_match_pins`, boto3 installed 1.43.111 vs pinned 1.43.108 in `requirements.lock`. Environment drift in `$W/.venv`; the branch changes no `src/`, `tests/`, `requirements.lock` or `pyproject.toml`. Not fixed (needs a network reinstall: `pip install boto3==1.43.108`). | 1083 s |
| `"$V/python" -m pytest research/ts4/plugin/tests -q -p no:cacheprovider` | **155 passed**, 1 deselected (localcache) | 146 s |
| `"$V/python" -m pytest research/ts4/plugin/tests/test_parity_localcache.py -m localcache -q -p no:cacheprovider` | **1 passed** (ts4, ts4_v2, ts4_v2_be in one host, exact, moves equal) | 279 s |
| `"$V/python" -m pytest research/ts4/tests -q -p no:cacheprovider` | **202 passed**, 4 deselected | 3 s |
| `"$V/python" -m pytest research -q -p no:cacheprovider` | **976 passed**, 6 skipped (ES_AUDIT_RUN unset), 33 deselected | 373 s |
| plugin-host unit files (`test_plugin_api`, `_loader`, `_paper_broker`, `_host_dispatch`, `_host_persistence`, `_host_rotation`, `test_runner_config`, `test_runner_paper_only`) | **117 passed** | 12 s |
| `"$V/ruff" check research/ts4 --extend-exclude research/ts4/handoff` | All checks passed | <1 s |
| `"$V/ruff" format --check research/ts4 --extend-exclude research/ts4/handoff` | 72 files already formatted | <1 s |
| `"$V/mypy" research/ts4 --exclude '^research/ts4/handoff/'` | Success: no issues found in 53 source files | 1 s |
| `git -C $W diff --stat 6688bf9 -- $P/src $P/tests $P/research/ts4/sim.py` | empty (core, fakes and sim untouched) | |
| `git -C $W status --short` | only `?? .agents/` | |

---

## Coder's record (iteration 1)

Environment for every command:

```sh
W=/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-be-trail
E=$W/members/alantiix/skylit-futures-strategy-engine
P=members/alantiix/skylit-futures-strategy-engine
V=$W/.venv/bin
cd "$E" && export PYTHONPATH="$E/src:$E"
# verification scripts:
"$V/python" "$W/.agents/ts4-be-trail/verify_parity.py" --set zero --out /tmp/zero.json
"$V/python" "$W/.agents/ts4-be-trail/verify_parity.py" --set real --out /tmp/real.json
"$V/python" "$W/.agents/ts4-be-trail/explain_real.py"
```

## Commits

| commit | what |
|---|---|
| f4bc2f0 | feat(ts4): pure `trail_step`, proven equal to `sim._scan_exit` |
| 5e7ba44 | feat(ts4): track the broker's stop and route ModifyStop answers safely |
| ea1941e | feat(ts4): run breakeven/trailing live through ModifyStop at each bar close |
| 1032cbb | feat(ts4): `ts4_v2_be` config (Option A = c1b) and preset |
| 0276523 | feat(ts4): opt-in `bracket_reference: signal_close` via `EnterMarket.reference_t` |
| 3dfab74 | test(ts4): ts4_v2_be parity against c1b, synthetic and real in-sample year |
| 031e6b2 | feat(ts4): disabled ts4_v2_be entry in the runner example |
| b803eee | docs(ts4): ts4_v2_be (Option A), breakeven/trailing rules, reference_t evaluation |
| 3a646d9 | test(ts4): BE/trail on stop/target/gap bars, broker rejections, ts4_v2_be next to other plugins (verification step) |

18 files changed vs `6688bf9` (`git diff --stat 6688bf9 HEAD`), all under
`research/ts4/**` plus `configs/runner.ts4.example.yaml`. No git-crypt artifacts
(`research/edge_study/tests/__init__.py`, `research/matrix_heatseeker/tests/__init__.py`)
in any commit. `.agents/` is the workflow's scratch folder and is not committed.

## Final runs (all after the last code commit)

| command | result | time |
|---|---|---|
| `"$V/python" -m pytest research/ts4/plugin/tests -q -p no:cacheprovider` | 149 passed, 1 deselected (localcache) | 111 s |
| `"$V/python" -m pytest research/ts4/plugin/tests/test_parity_localcache.py -m localcache -q -p no:cacheprovider` | 1 passed (ts4, ts4_v2, ts4_v2_be in one host, all exact, stop moves equal) | 310 s |
| `"$V/python" -m pytest research/ts4/tests -q -p no:cacheprovider` | 202 passed, 4 deselected | 3 s |
| `"$V/python" -m pytest research -q -p no:cacheprovider` | 970 passed, 6 skipped (ES_AUDIT_RUN unset), 33 deselected | 389 s |
| `"$V/python" -m pytest tests/unit/test_plugin_api.py tests/unit/test_plugin_loader.py tests/unit/test_plugin_paper_broker.py tests/unit/test_plugin_host_dispatch.py tests/unit/test_plugin_host_persistence.py tests/unit/test_plugin_host_rotation.py tests/unit/test_runner_config.py tests/unit/test_runner_paper_only.py -q -p no:cacheprovider` | 117 passed | 16 s |
| `"$V/ruff" check research/ts4 --extend-exclude research/ts4/handoff` | All checks passed | <1 s |
| `"$V/ruff" format --check research/ts4 --extend-exclude research/ts4/handoff` | 72 files already formatted | <1 s |
| `"$V/mypy" research/ts4 --exclude '^research/ts4/handoff/'` | Success: no issues found in 53 source files | not timed |
| `git -C $W diff --stat 6688bf9 -- .../src .../tests` | empty (core untouched) | |
| `git -C $W diff --stat 6688bf9 -- .../research/ts4/sim.py` | empty (sim untouched) | |
| `git -C $W status --short` | only `?? .agents/` | |

Baseline before any change: plugin suite 107 passed, 1 deselected (83 s).

## What the tests prove

- `test_trail_step.py`: unit cases (long/short, below trigger, BE only, trail only, step
  3 vs 4, BE then trail on one close with the trail compared to the post-BE stop, retreat
  never loosens, BE level looser than the current stop) and an equivalence test:
  `sim._scan_exit` vs a bar-by-bar walk using `trail_step`, 3 seeds, >1000 (fill bar, side)
  cases each, same `(exit bar, reason, exit price, moved)` and `stop_moves`; >50 moved cases.
- `test_be_trail_flow.py` (host + paper broker, each with sim parity): (a) BE move to
  entry+4 with `effective_ns` = next bar open, stop at BE counts as a win for the
  progressive add-on; (b) trail levels +10/+20/+25, +43 close sends nothing (step), retreat
  sends nothing; (c) target fill on the would-move bar sends no ModifyStop; (d) profit on
  the 17:00 close bar only flattens; (e) a moved trade stays `open` (next slot skipped
  `position_open`) and still flattens at the close; (f) `stop_loosen`/`invalid_order`/unknown
  reason -> error, `not_open` -> info, none touch status/flatten/flatten_failed/cur_stop;
  accepted / unchanged / desync answers; (g) frozen ts4 and ts4_v2: `be_trail_live` False,
  zero ModifyStop.
- `test_plugin_restart.py`: restart mid-trade after a BE/trail move continues the trail
  (moves before and after the restart, trades equal the uninterrupted run); a snapshot
  without the new `_Open` fields restores (cur_stop = ref_open - side*stop_ticks); unknown,
  partial or mistyped fields are refused.
- `test_parity_synth.py`: case A (ts4) and B (ts4_v2) unchanged, plus zero ModifyStop;
  new case D: ts4_v2_be exact vs c1b on 3 seeds x {zero, tt1+$1.48}, stop moves happen,
  plugin `ts4_stop_move` count == sim `stop_moves`, no rejects/desyncs.
- `test_rules.py` / `test_fills_check.py`: formerly refused BE/trail configs now resolve
  with `be_trail_live=True`; frozen ts4 stays inert-rewritten and its tt>1 refusal tests are
  unchanged; a BE/trail-live config starts with tt 2.
- research `test_variants.py::test_ts4_v2_be_yaml_is_the_c1b_variant_on_the_ny_clock`
  and `test_config.py::test_ts4_v2_be_is_ts4_v2_with_be_trail_on`; the ts4_v2 tests pass
  unchanged.

## Real data: in-sample year (Atlas 1m, Globex days 2025-10-13..2026-10-02, 319,774 bars)

`"$V/python" -m research.ts4.plugin.tests.realdata --set <set> --out /tmp/ts4be-<set>.json`
(one host per case, EMA warm-up 60 sessions). /tmp files deleted afterwards.

| set | case | sim vs plugin | trades | win rate | net | $/trade | worst | stop moves sim / plugin | rejects |
|---|---|---|---|---|---|---|---|---|---|
| zero | ts4 | exact 880/880 | 880 | 85.23% | $18,844.50 | 21.41 | -$956.00 | 0 / 0 | 0 |
| zero | ts4_v2 | exact 959/959 | 959 | 87.28% | $32,710.00 | 34.11 | -$895.00 | 0 / 0 | 0 |
| zero | **ts4_v2_be** | **exact 959/959** | 959 | 87.49% | $31,583.00 | 32.93 | -$895.00 | **77 / 77** | 0 |
| tt1 (+$1.48) | ts4_tt1 | exact 880/880 | 880 | 85.11% | $6,676.26 | 7.59 | -$967.84 | 0 / 0 | 0 |
| tt1 | ts4_v2_tt1 | exact 959/959 | 959 | 86.65% | $20,217.94 | 21.08 | -$902.40 | 0 / 0 | 0 |
| tt1 | **ts4_v2_be_tt1** | **exact 959/959** | 959 | 86.86% | $18,608.94 | 19.40 | -$902.40 | **90 / 90** | 0 |
| real (slip 1) | ts4_real, sim | 0/880 by design | 880 | 84.77% | $3,782.76 | 4.30 | -$971.84 | | |
| real | ts4_real, plugin | | 880 | 85.11% | $2,237.76 | 2.54 | -$975.84 | 0 / 0 | 0 |
| real | ts4_v2_real, sim | 0/959 by design | 959 | 86.44% | $18,975.44 | 19.79 | -$904.90 | | |
| real | ts4_v2_real, plugin | | 959 | 86.65% | $17,228.94 | 17.97 | -$907.40 | 0 / 0 | 0 |
| real | ts4_v2_be_real, sim (c1b) | 0/959 by design | 959 | 86.65% | $17,068.94 | 17.80 | -$904.90 | 81 | |
| real | ts4_v2_be_real, plugin | | 959 | 86.76% | $15,054.94 | 15.70 | -$907.40 | 77 | 0 |

All cases: exit code 0, no faults, 0 host break backstops. ts4 880/880 and ts4_v2 959/959
are still exact at zero cost and tt1 (unchanged from the prior verification).

Realistic set (1-tick slippage): every row differs by design (documented cost-model row:
the paper broker measures brackets from the unslipped fill-bar open, the research from the
slipped fill), so target_t/stop_t differ on every trade and exits follow. For ts4_v2_be
the 81 vs 77 moves are 4 trades where the plugin's 1-tick-closer target fills before the sim's
move bar (superseded explanation: see "Final verification step" above).
Gap: ts4_v2_be -$2.10/trade, ts4_v2 -$1.82, ts4 -$1.76.

In-sample, BE/trail cost money vs ts4_v2 (sim realistic $17.80 vs $19.79/trade); OOS
(TS4_improvements.md) c1b was $1.14 better than c1b_nobe. Neither is significant.

## Item 7 (optional): `bracket_reference: signal_close` (EnterMarket.reference_t)

Implemented as a plugin param, default `fill_open` (today's behaviour, byte-identical
EnterMarket with `reference_t=None`, covered by
`test_fill_open_reference_is_the_default_and_sends_no_reference_t`). Example config and
presets do not set it. `signal_close` needs `services.broker_terms` (refused otherwise).

`--set real_ref` (realistic fills + `signal_close`):

| case | rows identical to sim | plugin $/trade | sim $/trade | plugin net | stop moves sim / plugin | reference_gap drops |
|---|---|---|---|---|---|---|
| ts4_v2_real_ref | 249/959 | 22.17 | 19.79 | $21,260.44 | 0 / 0 | 0 |
| ts4_v2_be_real_ref | 249/959 | 20.13 | 17.80 | $19,307.44 | 81 / 82 | 0 |

Result: not a clean fit. The approximation (signal close + slip ~ slipped next-open fill)
holds on only 26% of trades and overshoots the research by about +$2.35/trade, where
`fill_open` undershoots by about -$1.8..-2.1. It stays opt-in for evaluation and is
documented as such in the README; reverting commit 0276523 (plus the `real_ref` set in
realdata.py) removes it cleanly if the reviewer prefers not to ship it.

## Decisions

- Trade-through safety check: kept only for the inert rewrite (frozen ts4, tt <= 1, byte-
  identical behaviour and messages). BE/trail-live configs (ts4_v2_be) have no limit: the
  plugin applies the rule itself at every close, so the divergence the check guarded
  against cannot arise; tt1 parity above confirms it. Running ts4's own BE/trail live would
  lift its limit too, but changes a frozen config: documented, not done.
- `trades.csv` header unchanged; stop moves go to `plugin_log.jsonl` (`ts4_stop_move`) and
  `ts4_trade.stop_moves`. `STATE_VERSION` stays 1; new `_Open` fields are optional on restore.
- `ts4_start` gains `be_trail_live` and `bracket_reference`; `ts4_entry` gains `reference_t`
  (log-only additions, also for ts4/ts4_v2).

## Core proposals (not implemented)

1. `ModifyStop` docs: note the same-return `Flatten(next_open)` + `ModifyStop` caveat (TS4
   unaffected: it flattens with `last_close` and never sends both for one tag in one return).
2. `FillsBlock.levels_from: fill` (brackets from the slipped fill) would close the
   realistic-cost gap exactly; `reference_t` cannot, because the fill price is unknown when
   the entry is placed.
3. An optional `detail["current_stop"]` on `stop_loosen` rejections would let a plugin
   resync after a desync instead of only logging it.
