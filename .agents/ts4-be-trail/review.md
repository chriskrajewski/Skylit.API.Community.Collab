# TS4 Option A: live breakeven/trailing through ModifyStop (`ts4_v2_be`)

The TS4 plugin can now run breakeven and trailing stops live. Before this change, any config whose BE/trail could fire was refused at start. A pure `rules.trail_step` copies the per-close rule from `sim._scan_exit`. The plugin tracks the broker's stop (`_Open.cur_stop`) and sends a `ModifyStop` at each bar close when the stop tightens. A new `ts4_v2_be.yaml` preset (research variant c1b) ships disabled in the runner example. Frozen `ts4` keeps its inert-rewrite path and its trade-through check, so its config, sha and trades stay the same. `ts4_v2` changes by one comment only. `sim.py` and everything under `src/fse/live/**` are untouched. An extra opt-in `bracket_reference: signal_close` param is bundled in. It defaults to off.

Watch for: after a desync (restart or `stop_loosen`), the trade row's exit price is rebuilt from the plugin's own `cur_stop` and not from the broker's stop (possible). The bundled `signal_close` option matches the research on only 26% of trades in the evaluation (confirmed, opt-in, non-blocking).

**Verdict**: APPROVED

## High-level view

`trail_step` is a line-for-line copy of the BE/trail block in `sim._scan_exit`. Profit is measured from the actual slipped fill. BE moves the stop to entry ± offset when that is tighter. Trailing moves it to close ∓ distance only when that beats the post-BE stop by at least `step`. The stop never loosens. An equivalence test compares a bar-by-bar walk using `trail_step` against `_scan_exit` over three seeds with more than 1000 cases each. Because the rule is copied and not refactored out of the sim, the sim needs no proof of unchanged behavior.

The plugin runs `_trail` at each bar close from the fill bar on. It skips the session-close bar, trades that are flattening, and bars from another contract. It emits `ModifyStop` only when `trail_step` returns a different stop, so the plugin's move count equals the sim's `stop_moves`. That equality holds exactly in synthetic parity and on the real in-sample year (77/77 at zero cost, 90/90 at tt1). Stop-role answers go to `_stop_update` and never touch status or flatten state. `accepted` commits the new stop, `unchanged` is a no-op, `not_open` logs at info as a close race, and every other rejection logs at error while the old stop stays in place. All new `_Open` fields are in the snapshot. Older snapshots restore with `cur_stop` set to the initial stop.

Gating is scoped correctly. `resolve` now splits enabled BE/trail into an inert case (the frozen `ts4` rewrite, which keeps the tt ≤ 1 refusal) and a live case. Only the old `NO_MODIFY_STOP` refusal is removed, and it only ever applied to configs that now run live. The verification notes and README record the decision to drop the trade-through limit for live configs, and tt1 parity backs it.

Equality tests check that `ts4_v2_be.yaml` equals `VARIANTS["c1b"]` on the ny clock and equals `ts4_v2.yaml` with BE/trail turned on. The preset is registered. The runner entry ships `enabled: false`, and the comments and README warn against running it next to `ts4_v2`.

<details>
<summary>Issues (2)</summary>

1. **Exit price from plugin-side `cur_stop`** (possible): `_closed` rebuilds a moved-stop exit from `info.cur_stop`. If the plugin's view lags the broker (restart between broker accept and snapshot, or a `stop_loosen` that shows the broker is tighter), the trade row records the wrong exit. Before going live, use the broker's fill price for stop exits when it is available, or resync on `stop_loosen` (core proposal 3).
2. **`bracket_reference: signal_close` is a weak fit** (confirmed): 249/959 rows match the sim, and plugin results run about +$2.35/trade above the research. It defaults to off and is documented as evaluation-only. Revert 0276523 if it should not ship. Otherwise keep it out of presets.

</details>

<details>
<summary>Details</summary>

## trail_step vs `_scan_exit`

```
sim._scan_exit (unchanged)            rules.trail_step
prof = (c[j]-entry)*side              prof = (close-entry)*side
BE: max/min(new, entry±offset)        identical
TR: lvl = c[j] ∓ distance;            identical
    if (lvl-new)*side >= step: new=lvl
skip when j == e (session end)        plugin: bar.close_ns >= info.close_ns -> skip
```

The sim walks only after the first bar where `fav >= trig`. Before that bar, `trail_step` returns the input stop, so starting `_trail` on the fill bar does not create extra moves. The equivalence test and the 77/77 and 90/90 real-data move counts confirm this.

## Stop tracking and the exit-row reconstruction

`cur_stop` is set at fill time from the bracket reference (`ref_open` or `reference_t`) minus `side * stop_ticks`. That matches the broker's initial stop. Each accepted answer then commits either `detail["stop_price"]` or the price that was sent. A `previous_stop` mismatch logs `stop_desync`, after which the plugin adopts the broker's price.

The one soft spot is `_closed`. A moved-stop exit is rebuilt as `info.cur_stop`, with the gap check against the same value. That is right whenever the plugin and broker agree, which every test shows (zero desyncs and rejects in all parity runs). If they drift apart, nothing corrects `cur_stop` until the next accepted move. Drift can come from a crash between broker accept and plugin flush, or from a `stop_loosen` rejection, which by definition means the broker's stop is tighter than the plugin thinks. A stop hit in that window writes a trade row at the stale level. The verification notes already list this as core proposal 3 (`detail["current_stop"]` on `stop_loosen`). On the plugin side, a cheaper fix is to prefer the broker's fill price in `_closed` when it exists. Rated possible, because it needs a desync that the tests never produce.

## Unrelated scope: `bracket_reference`

The opt-in `signal_close` reference is optional item 7 in PLAN.md. It is behind a param that defaults to `fill_open`, and a test pins the default `EnterMarket` with `reference_t=None`. It needs `services.broker_terms` and is refused without it. The evaluation shows it does not close the realistic-cost gap: 26% of rows match, and results overshoot by about $2.35/trade. It is harmless while off, but it is extra surface area with no proven benefit.

## Test coverage

The coverage is thorough. It includes unit and equivalence tests for `trail_step`, host+broker flows for BE, trail steps, a target on the would-move bar, the session-close bar, every answer type, restart mid-trail with old-snapshot restore, and frozen `ts4` and `ts4_v2` emitting zero `ModifyStop`. Not tested: an explicit desync followed by a stop exit (the case in Issue 1), and `stop_fill: at_stop` with a moved stop. The README documents the at_stop case as optimistic.

</details>

<details>
<summary>File map</summary>

- `research/ts4/plugin/rules.py`: `trail_step`, `ts4_v2_be` preset, inert vs live split, `bracket_reference` param, scoped `check_fills` message.
- `research/ts4/plugin/strategy.py`: `_trail`, `_stop_update`, `_Open` stop fields plus snapshot compat, moved-stop exit price, `reference_t`.
- `research/ts4/ts4_v2_be.yaml` (new), `research/ts4/ts4_v2.yaml` (comment only).
- `configs/runner.ts4.example.yaml`: disabled `ts4_v2_be` entry and risk notes.
- `research/ts4/plugin/README.md`: BE/trail section, risk note, cost table.
- Tests: `test_trail_step.py`, `test_be_trail_flow.py` (new); updates to parity synth/localcache, restart, rules, fills check, runner example, flow, realdata; `research/ts4/tests/test_config.py`, `test_variants.py`.

Full diff: `git diff kiro/futures-strategy-engine...kiro/ts4-be-trail`.

</details>
