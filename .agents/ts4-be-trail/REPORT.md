# TS4 Option A report: live breakeven/trailing via ModifyStop (`ts4_v2_be` = research c1b)

- Branch: `kiro/ts4-be-trail`, HEAD `3a646d9`, base `6688bf9`
- Worktree: `/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-be-trail`
- Inputs: `/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-be-trail/.agents/ts4-be-trail/PLAN.md`,
  `.../review.md` (verdict APPROVED), `.../verification.md`
- Engine path below: `E=/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-be-trail/members/alantiix/skylit-futures-strategy-engine`

## Branch state (checked)

`git log --oneline kiro/futures-strategy-engine..kiro/ts4-be-trail`:

```
3a646d9 test(ts4): BE/trail on stop/target/gap bars, broker rejections, ts4_v2_be next to other plugins
b803eee docs(ts4): ts4_v2_be (Option A), breakeven/trailing rules, reference_t evaluation
031e6b2 feat(ts4): disabled ts4_v2_be entry in the runner example
3dfab74 test(ts4): ts4_v2_be parity against c1b, synthetic and real in-sample year
0276523 feat(ts4): opt-in bracket_reference: signal_close via EnterMarket.reference_t
1032cbb feat(ts4): ts4_v2_be config (Option A = c1b) and preset
ea1941e feat(ts4): run breakeven/trailing live through ModifyStop at each bar close
5e7ba44 feat(ts4): track the broker's stop and route ModifyStop answers safely
f4bc2f0 feat(ts4): pure trail_step, proven equal to sim._scan_exit
```

- Committed in 9 logical commits on `kiro/ts4-be-trail`. `git status`: clean except untracked `.agents/` (workflow scratch, not committed).
- Not merged: `kiro/ts4-be-trail` is not an ancestor of `kiro/futures-strategy-engine` (now `468821a`). That branch has moved past the base since then: the range plugin was merged at `468821a`. The merge base is still `6688bf9`.
- Not pushed: there is no upstream, and `git ls-remote --heads origin kiro/ts4-be-trail` returns nothing.
- Worktree left in place (`git worktree list` shows it at `3a646d9`).
- No git-crypt artifacts, caches or secrets committed. The 20 changed files are all under `research/ts4/**` plus `configs/runner.ts4.example.yaml`. No `.env`, key or cache files are included. The git-crypt `__init__.py` artifacts (`research/edge_study/tests/__init__.py`, `research/matrix_heatseeker/tests/__init__.py`) are in no commit. The only file name with "cache" in it is the test `test_parity_localcache.py`.
- Core untouched: `git diff --stat 6688bf9 -- $E/src $E/tests $E/research/ts4/sim.py` is empty.

## What changed (per plan item)

1. `rules.trail_step` (f4bc2f0): a pure function that copies the BE/trail block of `sim._scan_exit` line for line. `test_trail_step.py` has unit cases plus an equivalence walk against `_scan_exit` (3 seeds, >1000 cases each, >50 moved).
2. Stop tracking (5e7ba44): `_Open` gains `cur_stop`, `pending_stop` and `stop_moves`. Stop-role answers go to `_stop_update` and never touch status or flatten state. `accepted` commits the stop, `not_open` logs info, other rejections log error and keep the old stop. Old snapshots restore (`STATE_VERSION` stays 1). A moved-stop exit is priced from `cur_stop`.
3. Live BE/trail (ea1941e): `Resolved.be_trail_live`. `_trail` emits `ModifyStop` at each bar close, catch-up included, only when the stop tightens. `NO_MODIFY_STOP` is removed. Frozen `ts4` keeps its inert rewrite and its tt<=1 refusal, so it stays byte-identical. Live configs have no trade-through limit.
4. `ts4_v2_be.yaml` plus preset (1032cbb): this is `ts4_v2` with BE 40/4 and trail 40/30/4, equal to `VARIANTS["c1b"]` on the ny clock. `ts4_v2.yaml` changes by one comment only.
5. Parity (3dfab74): synth case D, plus `ts4_v2_be` in `realdata.py` and the localcache test.
6. Runner example and README (031e6b2, b803eee): the `ts4_v2_be` entry ships `enabled: false` with a warning never to run it next to `ts4_v2`. The README adds BE/trail rules, a risk note and the cost table.
7. Optional `bracket_reference` (0276523): see the reference_t section.
8. Verification tests (3a646d9): stop, target and gap on the move bar. Real-broker `not_open` and `stop_loosen` rejections. `ts4_v2_be` running next to other plugins (replay host and offline `fse run` with Matrix + Playbook).

## Parity: Atlas 1m in-sample year, 2025-10-13..2026-10-02, 319,774 bars

Zero cost, independent harness:

| plugin | research | rows exact | stop moves sim / plugin | win rate | net | $/trade |
|---|---|---|---|---|---|---|
| ts4 | baseline | 880/880 | 0 / 0 | 85.23% | $18,844.50 | 21.41 |
| ts4_v2 | c1b_nobe | 959/959 | 0 / 0 | 87.28% | $32,710.00 | 34.11 |
| ts4_v2_be | c1b | 959/959 | 77 / 77 | 87.49% | $31,583.00 | 32.93 |

- ts4_v2_be: 75 moved trades (73 moved once, 2 twice). The per-trade stop sequence is equal on 959/959 trades. There were 0 `stop_move_skipped`, 0 `stop_move_rejected` and 0 `stop_desync`.
- tt1 (+$1.48 RT): ts4_v2_be is exact 959/959 with 90/90 stop moves, $18,608.94 net, 86.86% win rate.

## Realistic costs (slip 1, tt 1, $1.48 RT)

| | trades | win rate | net | $/trade | worst | stop moves | moved trades |
|---|---|---|---|---|---|---|---|
| research c1b (sim) | 959 | 86.65% | $17,068.94 | 17.80 | -$904.90 | 81 | 76 |
| ts4_v2_be (plugin) | 959 | 86.76% | $15,054.94 | 15.70 | -$907.40 | 77 | 72 |

- The gap is -$2.10/trade (ts4_v2 -$1.82, ts4 -$1.76). It is by design: the paper broker measures brackets from the unslipped fill-bar open, the research from the slipped fill.
- All 72 trades the plugin moved have the sim's exact stop sequence. The 4 extra sim moves are trades where the plugin's target, 1 tick closer, fills on or before the sim's move bar.
- In-sample, BE/trail costs money against ts4_v2 (sim $17.80 vs $19.79/trade). Out of sample, c1b was $1.14/trade better than c1b_nobe (t 1.85). Neither difference is significant.

## EnterMarket.reference_t: added as an opt-in flag, default off

- Param `bracket_reference: fill_open | signal_close`, default `fill_open`. With the default, `EnterMarket` is byte-identical (`reference_t=None`, pinned by a test). No preset or example sets it. `signal_close` requires `services.broker_terms`.
- Realistic-cost evaluation (`--set real_ref`):

| case | rows identical to sim | plugin $/trade | sim $/trade | stop moves sim / plugin | reference_gap drops |
|---|---|---|---|---|---|
| ts4_v2, fill_open (default) | 0/959 | 17.97 | 19.79 | 0 / 0 | n/a |
| ts4_v2, signal_close | 249/959 | 22.17 | 19.79 | 0 / 0 | 0 |
| ts4_v2_be, fill_open (default) | 0/959 | 15.70 | 17.80 | 81 / 77 | n/a |
| ts4_v2_be, signal_close | 249/959 | 20.13 | 17.80 | 81 / 82 | 0 |

- Result: not a fit. `signal_close` matches the sim on only 26% of rows and overshoots by about +$2.35/trade, while `fill_open` undershoots by about -$1.8 to -$2.1. The exact fix needs a core change (proposal 2). Reviewer: harmless while off. Revert 0276523 (plus the `real_ref` set in `realdata.py`) if it should not ship.

## Proposed core changes to `src/fse/live/**` (proposals only, not implemented)

1. `ModifyStop` docs: add a caveat about sending `Flatten(next_open)` and `ModifyStop` in the same return. TS4 is not affected.
2. `FillsBlock.levels_from: fill`: measure brackets from the slipped fill. This would close the realistic-cost gap exactly, which `reference_t` cannot do because the fill price is unknown at entry.
3. Optional `detail["current_stop"]` on `stop_loosen` rejections, so a plugin can resync after a desync. This also resolves review issue 1 (a moved-stop exit row priced from a stale plugin `cur_stop`; not seen in any test). The plugin-side alternative is to prefer the broker's fill price in `_closed`.

## Test and suite results (final)

| suite | result |
|---|---|
| full engine `tests` | 3512 passed, 1 failed: `test_installed_versions_match_pins`, boto3 1.43.111 installed vs 1.43.108 pinned. This is venv drift, and the branch changes no `src/`, `tests/` or lock files. Fix: `pip install boto3==1.43.108` |
| `research/ts4/plugin/tests` | 155 passed, 1 deselected |
| localcache parity (`-m localcache`) | 1 passed (ts4, ts4_v2 and ts4_v2_be exact, moves equal) |
| `research/ts4/tests` | 202 passed |
| `research` | 976 passed, 6 skipped |
| plugin-host unit files | 117 passed |
| ruff check, ruff format, mypy (`research/ts4`) | clean |

## How to run

```sh
W=/Users/chriskrajewski/Documents/GitHub/skylit.api.community.collab/.worktrees/ts4-be-trail
E=$W/members/alantiix/skylit-futures-strategy-engine
V=$W/.venv/bin
cd "$E" && export PYTHONPATH="$E/src:$E"
"$V/python" -m pytest research/ts4/plugin/tests -q -p no:cacheprovider
"$V/python" -m pytest research/ts4/plugin/tests/test_parity_localcache.py -m localcache -q -p no:cacheprovider   # ~5 min
"$V/python" -m research.ts4.plugin.tests.realdata --set zero --out /tmp/ts4be-zero.json   # also: tt1, real, real_ref
```

To paper-trade it: in `$E/configs/runner.ts4.example.yaml`, set the `ts4_v2_be` entry to `enabled: true` and the `ts4_v2` entry to `enabled: false`. Never enable both, because they trade the same slots and double the exposure. It is paper only. To try the reference flag, add `bracket_reference: signal_close` to the entry's `config`.
