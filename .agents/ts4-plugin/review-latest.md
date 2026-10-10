# TS4 plugin adapted to f36b6ed: fills safety check, ts4_v2-first example, follow-ups 2 and 3

Review date 2026-10-09. Worktree `kiro/ts4-plugin` at `f36b6ed` (the `kiro/plugin-runner` tip). All changes are uncommitted.

The update moves the TS4 plugin from `be9718a` onto the Matrix/Playbook API at `f36b6ed`. The main addition is the start-time fills check from the prior review's issue 1. The constructor now reads `services.fills` and refuses frozen `ts4` when the inert-BE/trail rewrite applied and `trade_through_ticks > 1`. It raises `Ts4PluginError`, which the host reports as `PluginLoadError` (exit 2). The active cost model goes into the `ts4_start` log. The example runner config now ships `ts4_v2` enabled and `ts4` disabled, in the same block style as `configs/runner.yaml`. Both cheap follow-ups landed: the EMA re-warm rule and a one-time error-level `no_calendar` log. Recorded evidence shows full-year parity unchanged (ts4 880/880, ts4_v2 959/959 exact at zero cost). No tracked file under `src/` or `tests/` changed.

Watch for: two git-crypt `__init__.py` files still show as `M` in `git diff --name-status`, though `git status` is clean. That contradicts verification.md's statement that they "no longer show as modified" (confirmed, environment artifact, not blocking). The ~$1.8/trade realistic-fill gap against the research is still there, carried over and documented (confirmed).

**Verdict**: APPROVED

## High-level view

The API adaptation stays additive. Nothing under `src/fse/live/**` or `tests/` differs from `f36b6ed`, and the plugin's existing 89 tests passed unchanged on the new base before any edit. Conventions match the shipped plugins: `module: research.ts4.plugin:Ts4Plugin`, explicit `enabled`, and per-entry `config`/`fills`/`risk`/`hold_through_break` blocks in the same style as the `matrix` entry. Matrix and Playbook ship no plugin README. The TS4 README keeps its own sections and now documents the fills check, the ts4_v2-first example, and pasting the entries into `runner.yaml` next to `matrix` and `playbook`.

The fills check sits in `rules.check_fills`, called from the constructor, so a failure takes the loader's start-failure path. The error message names the bad value and both fixes (tt 0/1, or `ts4_config: ts4_v2`). The check covers only configs where the inert rewrite actually applied, so `ts4_v2` at tt 2 still starts, which is correct. Configs whose BE/trail could fire are refused earlier under the Option A "ModifyStop" error. A missing `fills` block is refused too. Tests use the real host through `make_host`: refuse at tt 2 and 3 with the exact message prefix, accept at tt 0 and 1, accept ts4_v2 at tt 2, refuse `None`. The start-log `fills` dict is asserted end to end.

The example config and README match the task. ts4_v2 is enabled, ts4 is disabled, both use slip 1 / tt 1 / `$1.48` RT, and the header says to keep `trade_through_ticks <= 1` and that ts4 refuses to start otherwise. It also covers the doubled-exposure risk and the ts4 out-of-sample loss. REPORT.md's "Update to f36b6ed" section matches the files.

Follow-up 2 replaces the 4-day staleness window with `ema_is_current`. A restored EMA is kept only while `now` falls before the next Globex day's open after its last bar, so a missed session always forces a re-warm. The seven parametrized cases cover the daily break, a missed session and both edges of the weekend. Follow-up 3 logs a single error-level `no_calendar` entry, mirroring `unknown_contract`, with a test asserting exactly one. The Option A TODO is intact in `ts4_v2.yaml` and the README, and HEAD is still `f36b6ed`.

<details>
<summary>Issues (2)</summary>

1. **Git-crypt artifacts misreported**: confirmed, non-blocking. `research/edge_study/tests/__init__.py` and `research/matrix_heatseeker/tests/__init__.py` still show as `M` in `git diff --name-status` (22 bytes -> 0), while verification.md U.0 says they no longer do. Leave them unstaged when committing (stage only the TS4 paths), and correct the U.0 line.
2. **Realistic-fill gap vs research (carried)**: confirmed, documented. At slip 1, brackets priced from the unslipped open leave paper P&L about $1.76 (ts4) and $1.82 (ts4_v2) per trade below the research. Expect this until `EnterMarket.levels_from: "fill"` lands in core.

</details>

<details>
<summary>Details</summary>

## Fills check placement and failure path

```
runner entry ─► loader ─► Ts4Plugin.__init__
                           parse_params ─► resolve_plugin_config (inert_rewrite flag)
                           check_fills(resolved, services.fills)
                             fills None                         ─► Ts4PluginError
                             inert_rewrite and tt > 1           ─► Ts4PluginError
                           ─► host: PluginLoadError "plugin 'ts4': cannot start: Ts4PluginError: ..." (exit 2)
on_start ─► ts4_start {..., fills: {slippage_ticks, trade_through_ticks, stop_fill, commission_rt, symbol}}
```

`Resolved.inert_rewrite` is set only when the declared config had BE or trailing enabled and passed `be_trail_inert`, so the tt limit applies exactly where the plugin's behavior depends on it. `fills_summary` reports `commission_rt: null` when the symbol has no rate, which keeps a missing commission visible in the log.

## EMA restore rule

`ema_is_current(last, now)` is `last <= now < next_globex_day(globex_day_at(last)).open_ns`. A last bar that falls outside any Globex day returns "not current" and re-warms. The rule is conservative: a restart at 18:05, five minutes into the next session, re-warms instead of keeping the EMA. Re-warming costs one `run_blocking` job, and buffered live bars are applied after it, so the extra cost is small.

## Evidence read

verification.md "Update to f36b6ed" records 107 plugin tests, 12 multi-strategy/coexistence/fills tests, the localcache parity test, `realdata --set together` (the JSON in `evidence/parity_full_year_together_f36b6ed.json` shows ts4 880/880 and ts4_v2 959/959 exact, BE/trail inert facts 0/0/identical, no faults, 0 backstops), 921 research tests, 3,436 engine tests in 10 shards, and clean TS4 ruff/format/mypy strict. Engine-wide format and mypy findings sit in committed, untouched files. The run logged 2 `hook_latency` warnings for ts4, against 0 at `be9718a`. The recorded explanation is a concurrent full-year run on the same machine, which is plausible because the counts and P&L are identical. The `fse run` CLI process was not run against live services. Only its async core ran offline, next to Matrix. The Playbook plugin was not co-run. Both gaps are stated.

</details>

<details>
<summary>File map</summary>

- `research/ts4/plugin/rules.py`: `Ts4PluginError`, `check_fills`, `fills_summary`, `INERT_MAX_TRADE_THROUGH`, `Resolved.inert_rewrite`
- `research/ts4/plugin/strategy.py`: constructor fills check, `fills` in `ts4_start`, `ema_is_current`, one-time `no_calendar` error log
- `research/ts4/plugin/README.md`: ts4_v2-first example, fills check, EMA restore and calendar notes, Option A TODO
- `research/ts4/plugin/tests/test_fills_check.py` (new): refuse/accept through the host, missing fills, start-log model
- `research/ts4/plugin/tests/test_fse_run_example.py` (new): `fse run` core on the example config, offline, next to Matrix
- `research/ts4/plugin/tests/{test_warmup,test_plugin_flow,test_runner_example,test_plugin_restart,replay}.py`: EMA gap cases, `no_calendar` log, example assertions, harness fills
- `configs/runner.ts4.example.yaml`: ts4_v2 enabled, ts4 disabled, slip 1 / tt 1 / $1.48, explanatory header
- Full diff: `git -C <worktree> status --short` (TS4 files untracked; no tracked file under `src/` or `tests/` changed)

</details>
