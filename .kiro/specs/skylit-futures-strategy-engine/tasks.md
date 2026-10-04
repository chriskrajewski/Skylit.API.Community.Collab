# Implementation Plan: Skylit Futures Strategy Engine

## Overview

The plan builds the Python 3.14 package `fse` (design D1) in `members/alantiix/skylit-futures-strategy-engine/`. Paths below are relative to that folder unless they start with `members/`.

The unlimited Skylit key is temporary, so the data path comes first. By task 7 the Operator can start the long history pull and leave it running while tasks 8 onward are built. After the data path the plan builds:

1. The pure engine.
2. The Fill_Simulator, the Account_Simulator and the Backtester.
3. The Config_Loader and Config_Printer, the Playbook_Baseline and the traceability table.
4. Measurement. Task 23 is a checkpoint where the Operator runs the baseline funnel, which answers "why isn't the bot trading".
5. The Revised_Drafts, after the holdout evaluation.
6. Live, paper and broker work.

The Monte_Carlo_Simulator comes before ablation because the ablation report needs Combine_Pass probability (Req 19.14).

## Tasks

- [x] 1. Project skeleton, core types and calendars
  - [x] 1.1 Create the Project package, pinned dependencies and test harness
    - `pyproject.toml`: `requires-python = ">=3.14"`, console script `fse = "fse.cli:main"`, the design's runtime pins exactly (`httpx==0.28.1`, `httpx-sse==0.4.3`, `pydantic==2.13.5`, `pyarrow==25.0.1`, `numpy==2.5.3`, `PyYAML==6.0.3`, `python-dotenv==1.2.4`, `tzdata==2026.4`) and a `dev` extra (`pytest==9.1.1`, `hypothesis==6.168.3`, `respx==0.23.1`, `pytest-asyncio==1.4.0`, `ruff==0.16.10`, `mypy==2.4.0`). Generate a hashed `requirements.lock` for exactly these pins. If a pin does not install on Python 3.14, stop and ask the Operator; do not substitute a version
    - Create the `src/fse/` tree from the design's module layout with empty `__init__.py` files. Make `src/fse/config/schema/` a package with one module per config section (the root model is assembled in task 20.1). Add `src/fse/commands/` for one module per CLI command
    - `tests/conftest.py`:
      - Hypothesis profiles `dev` (100 examples) and `ci` (200)
      - a session-wide respx router with `assert_all_mocked=True`
      - a fixture that sets fake Secret_Variable values (for example `fake-skylit-key-0000`) and a temporary `HOME`, so no test touches the real `~/.skylit-fse`
    - Add the pytest markers `perf` and `integration`, plus ruff and mypy settings
    - _Requirements: 1.1_

  - [x] 1.2 Add `.gitignore`, `.env.example` and the members-table row
    - `.gitignore`: `.env`, `cache/`, `runs/`, `recordings/`, `live-state/`, `logs/`
    - `.env.example`: `SKYLIT_API_KEY=`, `PROJECTX_USERNAME=`, `PROJECTX_API_KEY=`, `PRACTICE_ACCOUNT_ID=`, `COMBINE_ACCOUNT_ID=`, `NOTIFIER_WEBHOOK_URL=`, `NARRATOR_API_KEY=` and `FSE_SECRET_VARS=`, with nothing after any `=`
    - Add exactly one `skylit-futures-strategy-engine` row (link to the Project README, kind `agent`, one-line description) to the Projects table in `members/alantiix/README.md`
    - _Requirements: 1.4, 1.5, 1.12_

  - [x] 1.3 Write the README skeleton
    - Write these sections:
      - the identity lines
      - `What it does`
      - `Skylit surface`: Access `REST`; Heatseeker, Flowseeker and Atlas
      - the setup steps
      - the `.env.example` variable table: purpose, whether it is a Secret_Variable, and the commands and Order_Modes that need it
    - Use placeholders only. Write no key, account name or account id
    - _Requirements: 1.2, 1.17_

  - [x] 1.4 Write `LICENSE` from the Operator's choice
    - Operator step: ask the Operator which license to use. Do not choose one. Write the full license text with no template placeholder text
    - _Requirements: 1.3_

  - [x] 1.5 Implement the time model and core value types
    - `src/fse/timekit.py`, per design "Time model":
      - `Instant` (int ns UTC)
      - RFC3339 and Unix-seconds parsing for the adapters
      - `SessionCalendar`: RTH, early close, the 18:00 trading-day start, Pull_Window, Flatten_Time and Flat_Deadline
      - trading-day assignment of an instant
      - the Decision_Time grid
    - `src/fse/engine/types.py`: `Snapshot`, `Bar`, `DarkPoolPrint`, `VixState` and the sentinels `Unavailable`, `MissingInput`, `MissingPrice`, `NotApplicable` and `NoTarget`
    - `tests/unit/test_engine_imports.py`: parse every `src/fse/engine` module. Fail on any import outside the design's allowed set, and on any use of `time`, `datetime.now`, `random`, `os.environ` or file I/O
    - _Requirements: 4.7, 5.8, 12.15, 15.14, 15.15, 18.1_

  - [x] 1.6 Implement calendar files and validation
    - `src/fse/calendars.py`: parse `calendars/exchange_calendar.yaml`, `calendars/economic_events.yaml` and `calendars/roll_calendar.yaml`. Each file has `covers: {first, last}`. Before the first Decision_Time, stop with exit 2 and an error that names the file and the first invalid entry or uncovered session
    - Populate the three files from 2023-03-28 through at least the end of the current year, using the published exchange, BLS and Federal Reserve schedules:
      - exchange holidays and early closes
      - CPI, NFP and FOMC release dates and times
      - the ES, NQ, MES and MNQ quarterly roll schedule: contract, optional `atlas_symbol` and `projectx_contract_id`
    - Start each file with a comment that asks the Operator to verify it
    - _Requirements: 4.5, 4.14, 4.15, 4.16_

  - [x] 1.7 Write property test for the session-time model
    - **Property 16: Session-time model**
    - **Validates: Requirements 5.8, 15.14, 15.15, 18.1**

  - [x] 1.8 Write unit tests for calendar validation errors
    - Missing file, parse error, invalid date or time, uncovered session. Each error names the file and the first bad entry
    - _Requirements: 4.16_

- [x] 2. Secrets, redaction, path guard and Secret_Scanner
  - [x] 2.1 Implement environment loading and default paths
    - `src/fse/secrets/env.py`: `load_env` and `EnvView`. A non-blank shell value wins, then the `.env` value, else blank. The module holds the Secret_Variable list plus the names in `FSE_SECRET_VARS`
    - `src/fse/settings.py`: default directories under `~/.skylit-fse/` (`cache/`, `runs/`, `recordings/`, `live-state/`, `logs/`)
    - _Requirements: 1.6, 1.10_

  - [x] 2.2 Implement the Redactor, Log_Writer and canonical JSON
    - `src/fse/logio/redact.py`, `src/fse/logio/log_writer.py` and `src/fse/logio/canonical_json.py`, per design §1:
      - redact each value in raw and URL-encoded form, longest value first
      - `add()` registers ProjectX session tokens
      - cover every sink, with a `logging.Filter` and the `sys`, `threading` and asyncio exception hooks
      - cap the `httpx` and `httpcore` loggers at WARNING
    - _Requirements: 1.9_

  - [x] 2.3 Implement the cache and output path guard
    - `src/fse/data/path_guard.py`: for a resolved path inside the working tree that `git check-ignore -q` does not ignore, print an error naming the path, write no file and exit 2
    - _Requirements: 1.10, 1.11_

  - [x] 2.4 Implement the CLI entry point and `fse scan-secrets`
    - `src/fse/cli.py`: an argparse entry point that discovers `fse.commands.*` modules exposing `register(subparsers)`, sends all output through the Log_Writer, and maps errors to the design's exit codes
    - `src/fse/secrets/scanner.py` and `src/fse/commands/scan_secrets.py`: list files with `git ls-files -z --cached`, search the working-tree bytes, and print matching paths and status lines only. Exit 0 when no file matches, 1 when files match, 5 when the scan cannot run
    - _Requirements: 1.13, 1.14, 1.15, 1.16_

  - [x] 2.5 Write property test for redaction completeness
    - **Property 2: Redaction completeness**
    - **Validates: Requirements 1.9, 23.8, 25.10**

  - [x] 2.6 Write property test for the cache path guard
    - **Property 3: Cache path guard**
    - **Validates: Requirements 1.10, 1.11**

  - [x] 2.7 Write property test for the Secret_Scanner
    - **Property 4: Secret_Scanner reports exactly the matching files**
    - **Validates: Requirements 1.13, 1.14, 1.16**

- [x] 3. Skylit_Client pacing and retries
  - [x] 3.1 Implement the rate limiter, retry policy and Fetch_Log
    - `src/fse/skylit/ratelimit.py`: one rolling 60 s limiter shared by every host, plus the low-water pause
    - `src/fse/skylit/retry.py`: the outcome table in design §2
    - `src/fse/skylit/fetch_log.py`: one JSONL line per attempt through the Log_Writer, recording `X-Credits-Remaining` and never the key or the `Authorization` header
    - Inject `Clock` and `rng`
    - _Requirements: 2.3, 2.5, 2.6, 2.7, 2.8, 2.9, 2.11_

  - [x] 3.2 Implement `SkylitClient`, endpoints and response models
    - `src/fse/skylit/client.py`, `src/fse/skylit/endpoints.py` and `src/fse/skylit/models.py`:
      - a blank-key check before the first request (exit 3)
      - the `GET /v1/account` limits bootstrap, with fallbacks 60 and 1
      - a semaphore on historical replays in flight
      - stop on 401, 402 or 403 (exit 3)
    - Response models: account, symbols, historical, range frames with shared axes, heatmap, stream, gex/levels, dark pool, and Atlas config, search and history
    - _Requirements: 1.7, 2.1, 2.2, 2.4, 2.10_

  - [x] 3.3 Write property test for environment precedence
    - **Property 1: Environment precedence**
    - **Validates: Requirements 1.6, 1.7**

  - [x] 3.4 Write property test for pacing invariants
    - **Property 5: Pacing invariants**
    - **Validates: Requirements 2.3, 2.4, 2.5**

  - [x] 3.5 Write property test for the retry policy
    - **Property 6: Retry policy**
    - **Validates: Requirements 2.2, 2.6, 2.7, 2.8, 2.9**

  - [x] 3.6 Write integration test for rate limits with a fake clock
    - Drive these sequences through the real client against respx mocks:
      - 429 with `Retry-After`, with `X-RateLimit-Reset` only, and with neither
      - a low-water pause
      - 5xx responses
    - _Requirements: 2.3, 2.5, 2.6, 2.7, 2.8, 2.10_

- [x] 4. Data_Cache
  - [x] 4.1 Implement the catalog and window storage
    - `src/fse/data/catalog.py` (SQLite tables `windows`, `bars_coverage` and `pulls`) and `src/fse/data/cache.py` (`HeatmapView.view_id`, `CacheWindowKey`, and `DataCache` status, write, read and mark-incomplete), per design §3 and "Storage schemas"
    - Window write protocol:
      1. Mark the window `incomplete`.
      2. Write the Parquet file to a temp path, fsync it, and rename it.
      3. In one transaction, set `complete` or `no_data` with the row count, `source_endpoint` and sha256.
    - The storage-interval filter keeps one Snapshot per boundary, at Pull_Window start + k × interval
    - Add the bar, VIX and dark-pool stores. Run the path guard before the first write
    - _Requirements: 1.11, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x] 4.2 Write property test for the cache round-trip
    - **Property 9: Cache round-trip**
    - **Validates: Requirements 3.10**

  - [x] 4.3 Write property test for the storage-interval filter
    - **Property 10: Storage-interval filter**
    - **Validates: Requirements 3.7**

- [x] 5. History pull, futures and VIX bars, coverage report
  - [x] 5.1 Implement the pull planner
    - `src/fse/data/planner.py`:
      - validate the range from the `GET /v1/symbols` result: start ≤ end, end ≤ today, and every configured symbol listed
      - take sessions from the exchange calendar and tile each Pull_Window into 15-minute Cache_Windows
      - skip sessions before each symbol's first history date
      - serve the `complete` and `no_data` windows of completed sessions from the cache
      - use `/v1/historical/range` for sessions under 365 days old (up to 5 symbols per call), else `/v1/historical` at each sample-interval instant (up to 10 symbols per call)
      - order sessions newest first, and support the optional `--label-sample-minutes`
    - Add a shared helper that splits a date range into contiguous, non-overlapping spans of at most N days (Atlas `max_days`, or 31 days for dark pool)
    - _Requirements: 3.3, 3.4, 3.5, 3.8, 3.14, 4.4, 4.12_

  - [x] 5.2 Implement the pull estimate and size confirmation
    - `src/fse/data/estimate.py`: compute request count, credits (at the configured credits per endpoint) and disk GB, in total and per symbol and metric, from the plan. Recalibrate the compression ratio from bytes already written
    - Over the size limit: prompt in an interactive run. A non-interactive run aborts with exit 2 and sends no Replay_Request
    - _Requirements: 3.1, 3.2_

  - [x] 5.3 Implement the coverage report
    - `src/fse/data/coverage.py`: write `coverage_{pull_id}.md` and `.json` from a `finally` block
    - Per symbol and metric: sessions requested, stored and skipped, `meta.resolution`, `no_data` gaps and incomplete windows
    - Per instrument: the Atlas probe result, the contract and source per session, and missing RTH minutes with their cause
    - VIX and dark-pool gaps
    - _Requirements: 3.11, 4.2, 4.6, 4.8, 4.11, 4.13_

  - [x] 5.4 Implement the ProjectX session and bar history
    - `src/fse/projectx/session.py`: log in with `PROJECTX_USERNAME` and `PROJECTX_API_KEY` from `EnvView` only, register the session token with the Redactor, and exit 3 when login fails
    - `src/fse/projectx/bars.py` and `src/fse/projectx/models.py`: `retrieveBars`, spaced under 50 requests per 30 s, with second bars for `--bar-interval 5` (OQ9)
    - _Requirements: 1.9, 4.3, 24.7_

  - [x] 5.5 Implement the Bar_Source, VIX data and `fse import-vix`
    - `src/fse/data/bar_source.py`:
      - Atlas `GET /v1/history` with `extended=true`, `max_days` from `GET /v1/config`, and `atlas_symbol` resolved through `GET /v1/search` when the roll calendar leaves it blank
      - a probe of the first and last sessions, falling back to ProjectX for every session of the pull
      - one roll-calendar contract per session, including the bars from 18:00 the prior evening
      - normalization to open and close instants plus ticks, with no synthesized bars
      - the OQ10 cross-source timestamp check
    - `src/fse/data/vix.py`: the daily open from the 09:30 bar (Observation_Time 09:31), the prior-session close, optional intraday bars, and gaps
    - `src/fse/commands/import_vix.py`: load an Operator CSV into the cache (OQ2)
    - _Requirements: 4.1, 4.2, 4.3, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11_

  - [x] 5.6 Implement the dark-pool fetch
    - `src/fse/data/darkpool.py`: per ticker (default SPY and QQQ), fetch spans of at most 31 days with `limit=5000` and paging by `offset`. Split a span that would pass offset 50,000. Record per-session gaps with their cause
    - _Requirements: 4.12, 4.13_

  - [x] 5.7 Implement the puller and `fse pull`
    - `src/fse/data/puller.py` and `src/fse/commands/pull.py`, with `--start` and `--end` required. Flags with the requirement defaults: symbols, Heatmap_View, Pull_Window, sample interval in minutes, storage interval in seconds, size limit, credits per endpoint, instruments (ES and NQ), bar interval, dark-pool tickers and `--label-sample-minutes`
    - The pull:
      - prints the estimate before the first Replay_Request
      - runs the window write protocol
      - stops before the next Replay_Request on a cache write failure (exit 4)
      - on an interrupt, leaves windows incomplete and writes the coverage report (exit 130)
    - `--dark-pool` fetches dark-pool prints for a config that enables `dark_pool_confluence`. Task 20.4 adds `--config`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.9, 3.11, 3.14, 3.15, 4.1, 4.2, 4.3, 4.12_

  - [x] 5.8 Write property test for pull plan correctness
    - **Property 7: Pull plan correctness**
    - **Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.8**

  - [x] 5.9 Write property test for interrupted windows
    - **Property 8: Interrupted windows stay incomplete**
    - **Validates: Requirements 3.9**

  - [x] 5.10 Write property test for request window partition
    - **Property 11: Request window partition**
    - **Validates: Requirements 4.4, 4.12**

  - [x] 5.11 Write property test for bar normalization
    - **Property 12: Bar normalization**
    - **Validates: Requirements 4.5, 4.7, 4.8**

  - [x] 5.12 Write integration test for the pull end to end
    - Use respx mocks for `/v1/account`, `/v1/symbols`, both replay endpoints, Atlas `/v1/config`, `/v1/search` and `/v1/history`, `/v1/dark-pool/trades` and ProjectX bars. The pull writes to a temp Data_Cache and produces a coverage report
    - An Atlas probe that returns `no_data` switches the pull to ProjectX bars and is recorded in coverage
    - _Requirements: 3.1, 3.3, 3.11, 3.14, 4.2, 4.3, 4.6_

- [x] 6. Skill document fixes
  - [x] 6.1 Implement `fse skilldocs check`
    - `src/fse/skilldocs/check.py` and `src/fse/commands/skilldocs.py`. For each Skill_Document and Revised_Draft, check:
      - exactly one front-matter block, starting on line 1
      - the block parses as a YAML mapping
      - `name` and `description` are non-empty
      - the file is UTF-8 with no U+FFFD
      - the file holds no Secret_Scanner value
    - _Requirements: 26.1, 26.2, 26.3, 26.7, 26.17_

  - [x] 6.2 Apply the one-time fixes to the Skill_Documents
    - `src/fse/skilldocs/fixes.py`, applied once:
      - `current_working_SKILL_100226.md`: replace the two stacked blocks with one block holding the union of keys, using the second block's values
      - `SKILL.md` line 107: replace each `?` between steps with `→`
      - `SKILL.md` line 144: `?3:1` → `≥3:1`
      - `SKILL.md` line 154: replace each `?` between chain steps with `→`
    - Leave real question marks (for example on lines 59 and 61) unchanged. Assert that every other byte is unchanged
    - Folder name: the working tree has `skill-fine-tune-wip/`, an uncommitted rename of `skill-fine-tune/` (the path the spec uses). Keep the folder path in one constant in `fse.skilldocs` and ask the Operator which name is final. Do not rename folders
    - _Requirements: 26.4, 26.5, 26.6, 26.8_

  - [x] 6.3 Write property test for the front-matter checker
    - **Property 83: Front-matter checker**
    - **Validates: Requirements 26.1, 26.2, 26.3, 26.7**

  - [x] 6.4 Write property test for the fixer
    - **Property 84: Fixer changes only target spans**
    - **Validates: Requirements 26.4, 26.8**

  - [x] 6.5 Write smoke tests for the fixed Skill_Documents
    - `fse skilldocs check` passes on the three Skill_Documents, and the expected strings appear at `SKILL.md` lines 107, 144 and 154
    - _Requirements: 26.1, 26.5, 26.6, 26.7_

- [x] 7. Checkpoint - Data path runnable; Operator starts the long pull
  - Ensure all tests pass, ask the user if questions arise.
  - Operator: set `SKYLIT_API_KEY` in the shell or the Project `.env`, then run `fse scan-secrets` before any commit.
  - Operator: run `fse pull --start <today minus 364 days> --end <last completed session>` for full-resolution range history. Add `--dark-pool` to keep dark-pool prints for later ablation, because the Playbook_Baseline disables that Gate. Add `--storage-interval-s N` if the printed disk estimate is too large. The pull runs for hours; `caffeinate -i` keeps macOS awake. Rerunning the same command resumes it.
  - Operator: if the key is still active afterward, run `fse pull --start 2023-03-28 --end <365 days ago>` for sampled history, then check the coverage report: Atlas or ProjectX bars, VIX and dark-pool gaps.
  - Operator: `TASK.md` (untracked) names the Combine account. Before committing it, add the account id variable to `FSE_SECRET_VARS` so `fse scan-secrets` flags it, and decide how to remove it. Requirement 26.8 limits automated edits to the listed fixes.

- [x] 8. Point-in-time layer
  - [x] 8.1 Implement `MarketView`, as-of indexes and the data and time config sections
    - `src/fse/pit/protocols.py`, `src/fse/pit/asof.py` and `src/fse/pit/market_view.py` (historical view over truncated arrays):
      - Map_State keyed on the returned `asOf`, with `Unavailable` markers
      - Snapshot_Age and Futures_Price
      - `available_at` per design D8
    - `src/fse/config/schema/data.py` and `src/fse/config/schema/time.py`: symbols, Heatmap_View, Regime_Symbol, source symbols, NQ_Sources, instruments, velocity window and Decision_Cadence
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 8.2 Write property test for Map_State selection
    - **Property 13: Map_State selection**
    - **Validates: Requirements 5.1, 5.2**

- [x] 9. Node_Classifier, Taps and lifecycle
  - [x] 9.1 Implement node labels and Node_Velocity
    - `src/fse/config/schema/nodes.py` and `src/fse/engine/nodes.py`:
      - `classify`: design §6 rules 1 to 8, the tie rule, and empty labels for empty or all-zero Snapshots
      - memoize labels per (Snapshot identity, params hash)
      - `velocity`, which never reads the live `velocityPct`
    - _Requirements: 5.9, 5.10, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.19, 6.20, 6.21_

  - [x] 9.2 Implement the Tap tracker
    - `src/fse/engine/taps.py`:
      - count overlaps of RTH 1-minute bars with Node bands
      - keep session and weekly counters per (symbol, metric, strike)
      - mark a Tap's end at its last consecutive overlapping bar
    - _Requirements: 6.14_

  - [x] 9.3 Implement lifecycle, Sloppy_Seconds and Dormant labels
    - `src/fse/engine/lifecycle.py`:
      - lifecycle state by first match: Decaying, Delivered, Tested, Fresh
      - Sloppy_Seconds from the Tap reference value, kept to session end
      - Dormant from a Regime input
    - _Requirements: 6.15, 6.16, 6.17, 6.18_

  - [x] 9.4 Write property test for Node_Velocity
    - **Property 17: Node_Velocity**
    - **Validates: Requirements 5.9, 5.10**

  - [x] 9.5 Write property test for node labels against the reference model
    - **Property 18: Node labels match the reference model**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11**

  - [x] 9.6 Write property test for node label invariants
    - **Property 19: Node label invariants**
    - **Validates: Requirements 6.19, 6.20, 6.21**

  - [x] 9.7 Write property test for Tap counting
    - **Property 21: Tap counting**
    - **Validates: Requirements 6.14**

  - [x] 9.8 Write property test for node state labels
    - **Property 22: Node state labels match the reference model**
    - **Validates: Requirements 6.15, 6.16, 6.17, 6.18**

- [x] 10. Regime_Classifier
  - [x] 10.1 Implement Regime and Map_Grade
    - `src/fse/config/schema/regime.py`: `min_abs_value` is required with no default; also the VIX condition and the grade parameters
    - `src/fse/engine/regime.py`: `regime` and `map_grade` in the order of design §7, with `MissingInput` results
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12_

  - [x] 10.2 Implement the trailing-median precompute
    - `src/fse/data/medians.py`: for each session, the median raw magnitude per metric over all RTH Regime_Symbol Snapshots of the 20 most recent earlier sessions. Store it as derived Parquet keyed by (view_id, params hash). Mark sessions with fewer than 20 prior sessions, or a median of 0, so the Regime returns `MissingInput`
    - _Requirements: 7.2, 7.12_

  - [x] 10.3 Implement `fse calibrate regime-min-abs`
    - `src/fse/commands/calibrate.py`: `fse calibrate regime-min-abs --start --end --percentile P` prints the P-th percentile of cached Regime_Symbol gamma King absolute values and the session count. It writes no config file; the Operator chooses the value
    - _Requirements: 7.4_

  - [x] 10.4 Write property test for Regime and Map_Grade
    - **Property 23: Regime and Map_Grade**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12**

  - [x] 10.5 Write unit tests for missing Regime inputs
    - Missing gamma (Regime and Map_Grade missing), missing vanna, a trailing median covering fewer than 20 sessions, and a median of 0
    - _Requirements: 7.11, 7.12_

- [x] 11. Level_Converter
  - [x] 11.1 Implement conversion, tick rounding and Deflection_Bands
    - `src/fse/config/schema/levels.py` and `src/fse/engine/levels.py`:
      - the offset or ratio method per symbol
      - pair each Snapshot with the latest bar of the session's contract that closed at or before its `asOf`
      - round half up to the tick
      - ES and NQ bands
      - return `MissingPrice` for a missing, non-positive or stale price, with no reuse of an earlier factor
    - _Requirements: 5.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.10_

  - [x] 11.2 Implement Node clustering
    - `src/fse/engine/clusters.py`: optional clusters over converted levels (ES width, and the NQ width scaled by NQ ÷ ES at t), ranked by summed absolute value. Node labels stay unchanged
    - _Requirements: 6.12, 6.13_

  - [x] 11.3 Write property test for conversion formulas and pairing
    - **Property 24: Conversion formulas and pairing**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.6, 8.7, 8.10**

  - [x] 11.4 Write property test for tick rounding
    - **Property 25: Tick rounding**
    - **Validates: Requirements 8.5**

  - [x] 11.5 Write property test for the conversion round-trip
    - **Property 26: Conversion round-trip**
    - **Validates: Requirements 8.8**

  - [x] 11.6 Write property test for conversion monotonicity
    - **Property 27: Conversion is monotonic**
    - **Validates: Requirements 8.9**

  - [x] 11.7 Write property test for clusters
    - **Property 20: Clusters partition the Nodes**
    - **Validates: Requirements 6.12, 6.13**

- [x] 12. Chart_Feature_Builder
  - [x] 12.1 Implement the incremental chart state machine
    - `src/fse/config/schema/chart.py` and `src/fse/engine/chart.py` (`ChartState`), per design §9:
      - session windows
      - 5-minute, 1-hour and 4-hour aggregation aligned to 18:00
      - swings, breaks of structure and BOS_Legs
      - the eight Fibonacci ratios and leg pruning
      - candle labels and liquidity sweeps
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13, 9.14, 9.15, 9.16, 9.17_

  - [x] 12.2 Write property test for session windows
    - **Property 28: Session windows**
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.5, 9.6**

  - [x] 12.3 Write property test for structure features
    - **Property 29: Structure features match the reference model**
    - **Validates: Requirements 9.7, 9.8, 9.9, 9.11, 9.12, 9.13, 9.14**

  - [x] 12.4 Write property test for mirror symmetry
    - **Property 30: Mirror symmetry of chart features**
    - **Validates: Requirements 9.10, 9.15, 9.16, 9.17**

- [x] 13. Decision types, targets and Setup_Detector
  - [x] 13.1 Add the decision value types
    - Extend `src/fse/engine/types.py` with `SetupKey`, `CandidateSetup`, `DetectionSkip`, `SourceNodeRef`, `SetupInputs`, `Order`, `Fill` and `Trade` from design "Data Models"
    - _Requirements: 10.11, 10.12, 13.10, 13.11_

  - [x] 13.2 Implement target planning
    - `src/fse/config/schema/exits.py`: Exit_Modes, per-Regime overrides and breakeven settings
    - `src/fse/engine/targets.py`: `plan_targets`, shared by the detectors, the min_reward_risk Gate and the Order_Planner:
      - round toward entry, keeping each target at least 1 tick beyond entry
      - TP1 and TP2 quantities
      - `no_target_node`, `tp2_not_beyond_tp1`, and `NoTarget` for Trailing
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.9, 12.12_

  - [x] 13.3 Implement the eight detectors
    - `src/fse/config/schema/patterns.py`, `src/fse/engine/setups/registry.py` and the modules `gatekeeper_fade.py`, `floor_ceiling.py`, `beach_ball.py`, `rug.py`, `whipsaw.py` and `trend.py`, per the design §10 table:
      - a frozen `DetectContext`, with detectors run in registry order
      - arming, entry, the stop rules, targets, and validation to `DetectionSkip`
      - Setup_Key `tap_seq` and the attached inputs
    - When the source has `MissingPrice`, emit the Setup_Key with a `None` level so `no_conversion_price` fails it
    - _Requirements: 8.11, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12, 10.13, 10.14_

  - [x] 13.4 Write property test for target computation
    - **Property 37: Target computation**
    - **Validates: Requirements 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.9, 12.12**

  - [x] 13.5 Write property test for detector isolation
    - **Property 31: Detector isolation**
    - **Validates: Requirements 10.2, 10.3, 10.4**

  - [x] 13.6 Write property test for setup construction
    - **Property 32: Setup construction**
    - **Validates: Requirements 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12**

  - [x] 13.7 Write property test for price order or skip
    - **Property 33: Price order or skip**
    - **Validates: Requirements 10.13, 10.14**

  - [x] 13.8 Write smoke test for the detector catalog
    - 8 detectors, each with an id, an enabled flag and numeric parameters in the schema
    - _Requirements: 10.1_

- [x] 14. Position_Sizer and Risk_Manager
  - [x] 14.1 Implement the sizing pipeline
    - `src/fse/config/schema/sizing.py` and `src/fse/engine/sizing.py`: run base size, big-win reduction, Trinity size-down, VIX-gap halving and the Micro_Equivalent cap in that order
    - Reject with `size_zero` naming the step, or `data_unavailable` naming the missing input
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9_

  - [x] 14.2 Implement Kill_Switches, Lockouts and the order pre-check
    - `src/fse/config/schema/kill_switches.py` and `src/fse/engine/risk.py`:
      - `RiskState` and `Lockout`, with `on_fill` and `on_minute_close` (the internal daily loss stop)
      - Loss_Streak across sessions; consecutive_losers covers the next non-holiday weekday
    - `precheck(intent)` for the live order router:
      - an opening order checks the position cap and the internal loss stop
      - a reducing or closing order always passes
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 24.23, 24.24, 24.25_

  - [x] 14.3 Write property test for the sizing pipeline
    - **Property 44: Sizing pipeline**
    - **Validates: Requirements 14.2, 14.3, 14.5, 14.6, 14.7, 14.8, 14.9**

  - [x] 14.4 Write property test for kill switches
    - **Property 48: Kill switches match the reference model**
    - **Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.8, 16.9**

  - [x] 14.5 Write property test for the risk pre-check
    - **Property 79: Risk pre-check**
    - **Validates: Requirements 24.23, 24.24, 24.25**

- [x] 15. Gate_Evaluator
  - [x] 15.1 Implement the 27-Gate catalog, evaluation and Grade
    - `src/fse/config/schema/gates.py`, `src/fse/engine/gates/catalog.py` and `src/fse/engine/gates/registry.py`, per the design §11 table:
      - evaluate every Gate in the configured order and record each result
      - record `disabled` results, excluded from the Grade
      - turn typed `Unavailable` inputs into `data_unavailable`, with no broad exception catching
      - assign the Grade
    - Include the Playbook_Baseline allowed-Regime sets for `regime_match`
    - _Requirements: 5.7, 8.11, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 16.9, 19.2_

  - [x] 15.2 Write property test for Gate evaluation and Grade
    - **Property 35: Gate evaluation completeness and Grade**
    - **Validates: Requirements 11.2, 11.3, 11.4, 11.12, 19.2**

  - [x] 15.3 Write property test for Gate measurements
    - **Property 36: Gate measurements**
    - **Validates: Requirements 5.7, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11**

  - [x] 15.4 Write smoke test for the Gate catalog
    - The 27 Gate ids, in the Requirement 11.1 order
    - _Requirements: 11.1_

- [x] 16. Order_Planner and Engine.step
  - [x] 16.1 Implement order planning and lifecycle management
    - `src/fse/config/schema/orders.py` and `src/fse/engine/planner.py` (`manage`, `place`), per design §12:
      - entries for A_Plus only, with `tighten_only` stops
      - breakeven, TP1 to Breakeven_Price, and node-to-node or fixed-ticks trailing, each effective from the next bar
      - Flatten_Time and `max_open`
      - Cancel_Triggers checked against the values at the setup's Decision_Time
      - invalidation precedence: exit at market, then breakeven, then hold. A breakeven action with the close already past Breakeven_Price becomes a market exit
      - the resting-entry max age
    - A Setup_Key is never placed twice
    - _Requirements: 12.1, 12.8, 12.10, 12.11, 12.13, 12.14, 12.15, 12.16, 12.17, 12.18, 12.19, 12.20, 12.21, 12.22, 12.23, 25.9_

  - [x] 16.2 Implement EngineState and `Engine.step`
    - `src/fse/engine/state.py`: a frozen `EngineState` tree with an exact canonical-JSON round-trip
    - `src/fse/engine/step.py`: `Engine.step(state, view, t, events, external_blocks)` with the fixed phase order (map, management, setup, log payload), plus the bar-phase hooks the runners call: stop updates, Tap and chart consumption, and the risk minute close
    - _Requirements: 5.3, 10.15, 16.7, 23.5_

  - [x] 16.3 Write property test for stops only tightening
    - **Property 38: Stops only tighten**
    - **Validates: Requirements 12.8, 12.10, 12.11, 12.13, 12.14**

  - [x] 16.4 Write property test for Flatten_Time
    - **Property 39: Flatten_Time**
    - **Validates: Requirements 12.15, 12.16, 12.17**

  - [x] 16.5 Write property test for cancel and invalidation triggers
    - **Property 41: Cancel and invalidation triggers**
    - **Validates: Requirements 12.19, 12.20, 12.21, 12.22, 12.23**

  - [x] 16.6 Write unit tests for planner edge cases
    - Next_Node with no Node beyond entry, Trailing with no target, and breakeven past the close
    - _Requirements: 11.7, 12.12, 12.22_

- [x] 17. Checkpoint - Engine units pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Fill_Simulator and Account_Simulator
  - [x] 18.1 Implement the Fill_Simulator
    - `src/fse/config/schema/fills.py`: trade-through, slippage, and per-instrument commission and exchange fee. Commission and fee are required with no default, range $0.00 to $25.00
    - `src/fse/sim/fills.py`: `on_bar` per design §13, with exact tick and Decimal accounting and the missing-bar flag
    - _Requirements: 5.5, 5.6, 13.1, 13.3, 13.4, 13.5, 13.6, 13.7, 13.9, 13.10, 13.11, 13.12_

  - [x] 18.2 Implement the Account_Simulator and account-rule documentation
    - `src/fse/config/schema/account.py`: the defaults and ranges of Requirements 15.1 and 15.4
    - `src/fse/sim/account.py`: `AccountSim` per design §15, with sequential Combine_Attempts and disabled-rule labels
    - `docs/account-rules.md`:
      - each default value, with source "third-party summary dated July 2026"
      - a "date checked" column for the Operator to fill in. Do not invent a date
      - the statement that the Operator verifies every value against the Topstep help center before enabling Combine Order_Mode
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11, 15.12, 15.13, 15.14, 15.15, 15.16, 15.17, 15.18, 15.19, 15.20_

  - [x] 18.3 Write property test for fill timing
    - **Property 15: Fill timing**
    - **Validates: Requirements 5.5, 5.6**

  - [x] 18.4 Write property test for fill rules
    - **Property 42: Fill rules match the reference model**
    - **Validates: Requirements 13.1, 13.3, 13.4, 13.5, 13.6, 13.12**

  - [x] 18.5 Write property test for the accounting invariant
    - **Property 43: Accounting invariant**
    - **Validates: Requirements 13.7, 13.10, 13.11**

  - [x] 18.6 Write property test for the trailing MLL_Floor
    - **Property 45: Trailing MLL_Floor**
    - **Validates: Requirements 15.6, 15.7**

  - [x] 18.7 Write property test for loss-limit liquidation
    - **Property 46: Loss-limit liquidation**
    - **Validates: Requirements 15.8, 15.9, 15.10, 15.11, 15.12**

  - [x] 18.8 Write property test for consistency target and pass timing
    - **Property 47: Consistency target and pass timing**
    - **Validates: Requirements 15.13, 15.16, 15.17**

  - [x] 18.9 Write property test for exposure caps and A_Plus-only entries
    - **Property 40: Exposure caps and A_Plus-only entries**
    - **Validates: Requirements 12.18, 14.4, 15.5, 25.9**

  - [x] 18.10 Write unit tests for the worked examples
    - MLL_Floor: $51,200 → $49,200 and $52,000 → $50,000
    - Consistency target: $1,650 → $3,000 and $2,000 → $3,636.37
    - R_Multiple of −1 at the stop with zero costs, the contract values, and MLL and DLL on the same bar
    - _Requirements: 13.9, 13.11, 15.7, 15.12, 15.13_

- [x] 19. Backtester and decision log
  - [x] 19.1 Implement the decision log and Run_Manifest
    - `src/fse/backtest/decision_log.py`: one canonical JSONL entry per Decision_Time with every Requirement 18.7 field
    - `src/fse/backtest/manifest.py`: the Run_Manifest fields, with `code_version` from `git describe --always --dirty`, written in `finally` with status `completed` or `aborted`
    - _Requirements: 18.4, 18.6, 18.7_

  - [x] 19.2 Implement `run_backtest`
    - `src/fse/backtest/runner.py`:
      - validate the range and calendars
      - in offline mode, build no `SkylitClient` and print sessions with absent or incomplete windows
      - skip sessions missing Snapshots or bars, and record them
      - load one session at a time into `AsOfIndex` objects
    - Per Decision_Time, run the bar phase (fills, then account, then risk, then stop updates, then Taps and chart), then `Engine.step`. Process bars through the Flat_Deadline
    - Also:
      - run sequential Combine_Attempts
      - record each session's net P&L and intraday equity low for the Monte_Carlo_Simulator
      - use seeded RNG, and merge parallel precompute in session order
      - write the trade list as CSV and JSON
    - _Requirements: 1.8, 3.12, 3.13, 5.5, 5.6, 15.9, 15.17, 15.18, 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

  - [x] 19.3 Write property test for no look-ahead
    - **Property 14: No look-ahead**
    - **Validates: Requirements 5.3, 5.4, 5.11, 9.1**

  - [x] 19.4 Write property test for backtest determinism
    - **Property 34: Backtest determinism**
    - **Validates: Requirements 10.15, 18.5**

  - [x] 19.5 Write property test for decision-log completeness
    - **Property 53: Decision-log completeness**
    - **Validates: Requirements 18.3, 18.6, 18.7**

  - [x] 19.6 Write property test for restart parity
    - **Property 49: Restart parity**
    - **Validates: Requirements 16.7**

  - [x] 19.7 Write integration test for the offline backtest
    - Call `run_backtest` in offline mode on a synthetic cache with `SKYLIT_API_KEY` unset. Assert zero network calls, no key error, and the gap printout
    - _Requirements: 1.8, 3.12, 3.13_

- [x] 20. Config_Loader, Config_Printer, Playbook_Baseline and traceability
  - [x] 20.1 Implement the root schema, loader, printer, warnings and hash
    - `src/fse/config/schema/__init__.py`: the root `StrategyConfig`, assembling every section module, with `order_mode` default `paper`
    - New section modules `live.py`, `notify.py`, `reporting.py` and `experiments.py`, per design "Strategy_Config shape"
    - `src/fse/config/loader.py`:
      - `UniqueKeySafeLoader`, with the path and the syntax-error line in errors
      - pydantic strict mode with unknown keys forbidden
      - every error reported with its key path, kind and expected value
    - Cross-field checks: Requirements 15.4 and 13.8 are errors; Requirement 17.6 cases are warnings
    - `printer.py`, `warnings.py` and `hashing.py`
    - `tests/unit/test_schema_no_credentials.py` walks every field name, with the Narrator `base_url` allow-listed
    - _Requirements: 13.8, 15.4, 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8_

  - [x] 20.2 Write the Playbook_Baseline with Operator placeholders
    - `configs/playbook_baseline.yaml`, from the design's codification table:
      - Paper Order_Mode
      - `dark_pool_confluence` and `stdev_fib_zone` disabled; the other 25 Gates enabled
    - Leave `fills.costs.<instrument>.commission`, `fills.costs.<instrument>.exchange_fee` and `regime.min_abs_value` absent, each marked `# OPERATOR: required`. Do not invent values
    - `tests/unit/test_playbook_baseline.py`:
      - the shipped file fails with exactly those missing-required key paths
      - with a test-only overlay of fake values, it loads with no error in Paper Order_Mode
    - _Requirements: 7.4, 13.8, 17.9_

  - [x] 20.3 Write the traceability table
    - `docs/traceability.md`: one row per Skill_Document rule (each "Hard passes" item is one row). Each row gives:
      - the source document and section
      - the key path and Playbook_Baseline value, or "not codified" with a reason
    - Add:
      - every conflict pair, including the two Requirement 17.11 pairs
      - a `regime.min_abs_value` row whose derivation the Operator fills in
    - Copy no account name, account id or credential. `TASK.md` names the Combine account; leave that out
    - `tests/unit/test_traceability.py`
    - _Requirements: 17.10, 17.11_

  - [x] 20.4 Implement `fse backtest` and `fse pull --config`
    - `src/fse/commands/backtest.py`: `fse backtest --config --start --end [--offline] [--seed] [--out]`
    - Update `src/fse/commands/pull.py` so `--config` sets dark-pool fetching from `gates.dark_pool_confluence.enabled`. `--dark-pool` still forces fetching
    - _Requirements: 4.12, 18.1, 18.2_

  - [x] 20.5 Write property test for the config round-trip
    - **Property 50: Config round-trip**
    - **Validates: Requirements 17.7, 17.8**

  - [x] 20.6 Write property test for validation errors
    - **Property 51: Validation reports every violation**
    - **Validates: Requirements 13.8, 15.4, 17.4**

  - [x] 20.7 Write property test for defaults and contradiction warnings
    - **Property 52: Defaults and contradiction warnings**
    - **Validates: Requirements 17.1, 17.2, 17.6**

- [ ] 21. Checkpoint - Operator sets the required config values
  - Ensure all tests pass, ask the user if questions arise.
  - Operator: enter the current commission and exchange fee per traded instrument (MES, MNQ) in `configs/playbook_baseline.yaml`.
  - Operator: run `fse calibrate regime-min-abs --start <date> --end <date> --percentile <P>`, choose a value, set `regime.min_abs_value`, and record the derivation in `docs/traceability.md`.
  - Operator: fill in "date checked" in `docs/account-rules.md` after checking the Topstep help center. This is required before Combine Order_Mode.

- [ ] 22. Shadow trades, Gate_Funnel, metrics and reports
  - [ ] 22.1 Implement the ShadowBook
    - `src/fse/backtest/shadow.py`, per design §19:
      - use the governing evaluation, with one shadow per rejected Setup_Key at base contracts
      - follow the same lifecycle rules
      - apply no account, Kill_Switch, `max_open` or sizing rule
    - Wire the ShadowBook into the log phase of `src/fse/backtest/runner.py`
    - _Requirements: 19.2, 19.8, 19.9_

  - [ ] 22.2 Implement the Gate_Funnel
    - `src/fse/analytics/funnel.py`:
      - final statuses
      - per-Gate fail, only-fail and first-fail counts
      - the co-rejection matrix
      - only-rejected shadow statistics beside the accepted statistics
      - the "no measured edge" and "insufficient sample" flags
      - the per-session top 3
    - _Requirements: 19.1, 19.3, 19.4, 19.5, 19.6, 19.7, 19.10, 19.11, 19.12, 19.17_

  - [x] 22.3 Implement run metrics
    - `src/fse/analytics/metrics.py` (`summarize`):
      - counts, R and dollar expectancy, and profit factor
      - drawdowns in dollars and R
      - the three win rates and the break-even win rate
      - MAE and MFE quantiles
      - `NotApplicable` and low-sample labels
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.13, 20.16_

  - [x] 22.4 Implement the Holdout_Period computation
    - `src/fse/experiments/holdout.py`: the newest ceil(fraction × n) sessions with data, fraction from 0.05 to 0.50
    - _Requirements: 22.1_

  - [ ] 22.5 Implement the Report_Generator core and `fse report`
    - `src/fse/reports/markdown.py`, `src/fse/reports/tables.py` and `src/fse/commands/report.py` (`fse report --run <run_id>`), with Markdown plus CSV or JSON output:
      - the Requirement 20.15 header, including whether any Holdout_Period session is included
      - the "touch fills (optimistic)" label, not-applicable rendering and low-sample labels
      - metrics, Gate_Funnel tables and the co-rejection matrix
      - King and Gatekeeper agreement rates
      - inter-decision Tap counts per session
      - the per-trade MAE and MFE file
    - _Requirements: 6.22, 6.23, 13.2, 18.11, 19.6, 19.7, 19.10, 19.17, 20.1, 20.6, 20.13, 20.15, 20.16_

  - [ ] 22.6 Write property test for funnel accounting
    - **Property 56: Funnel accounting**
    - **Validates: Requirements 19.1, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8, 19.17**

  - [ ] 22.7 Write property test that shadow trades do not leak
    - **Property 57: Shadow trades do not leak**
    - **Validates: Requirements 19.9, 14.5**

  - [ ] 22.8 Write property test for shadow edge flags
    - **Property 58: Shadow edge flags**
    - **Validates: Requirements 19.10, 19.11, 19.12**

  - [x] 22.9 Write property test for metrics
    - **Property 60: Metrics match the reference model**
    - **Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.16**

  - [ ] 22.10 Write property test for the inter-decision Tap count
    - **Property 55: Inter-decision Tap count**
    - **Validates: Requirements 18.11**

  - [ ] 22.11 Write unit test for a zero-trade report
    - Not-applicable metrics, trade count 0, and the report finishes
    - _Requirements: 20.16_

- [ ] 23. Checkpoint - Operator runs the Playbook_Baseline backtest and Gate_Funnel
  - Ensure all tests pass, ask the user if questions arise.
  - Operator: run `fse backtest --config configs/playbook_baseline.yaml --start <first cached session> --end <last session before the Holdout_Period> --offline`, then `fse report --run <run_id>`. If the report header says Holdout_Period sessions are included, rerun with an earlier end date.
  - Operator: read the funnel: status counts, first-failing Gates, only-failing shadow trades, and the per-session top 3 Gates. These show why the bot rarely trades.

- [ ] 24. Monte_Carlo_Simulator
  - [ ] 24.1 Implement the Combine pass estimate
    - `src/fse/analytics/montecarlo.py`, per design §21:
      - session outcomes from a completed run
      - vectorized seeded paths with the account rules
      - input validation
      - a generated seed when none is given, recorded in the Run_Manifest
      - the `truncated_by_run_account` count and the "insufficient sample" label
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 21.8, 21.9, 21.10, 21.11, 21.12_

  - [ ] 24.2 Write property test for the path model
    - **Property 65: Path model matches the scalar reference**
    - **Validates: Requirements 21.1, 21.2, 21.4, 21.5**

  - [ ] 24.3 Write property test for estimate accounting
    - **Property 66: Estimate accounting**
    - **Validates: Requirements 21.6, 21.7, 21.8**

  - [ ] 24.4 Write unit tests for estimate edge cases
    - No passing paths, fewer than the minimum sessions, and out-of-range inputs
    - _Requirements: 21.7, 21.11, 21.12_

- [ ] 25. Experiment_Runner and ablation
  - [ ] 25.1 Implement the shared experiment runner and ablation
    - `src/fse/experiments/runner.py` (shared by every experiment):
      - the same session list and seed for every configuration
      - Holdout_Period sessions excluded, with their dates in every manifest
      - a process pool, merged in definition order
      - failed configurations marked
      - "insufficient sample" labels and distinct-configuration counts
    - `src/fse/experiments/variants.py` and `src/fse/experiments/ablation.py`: N + 1 configurations and their deltas
    - _Requirements: 19.13, 19.14, 19.15, 19.16, 22.2, 22.3, 22.15, 22.16_

  - [ ] 25.2 Write property test for ablation variants
    - **Property 59: Ablation variants**
    - **Validates: Requirements 19.13, 19.14, 19.15, 19.16**

- [ ] 26. Bootstrap, frontier sweep and ranking
  - [x] 26.1 Implement bootstrap confidence intervals
    - `src/fse/analytics/bootstrap.py`: 95% percentile intervals for Primary_Win_Rate and expectancy, from a seeded PCG64 with 1,000 to 100,000 resamples. Record the seed in the Run_Manifest
    - _Requirements: 20.12_

  - [ ] 26.2 Implement the frontier sweep, Pareto set and ranking
    - `src/fse/experiments/sweep.py`, `src/fse/experiments/ranking.py` and `src/fse/analytics/frontier.py`:
      - sweep validation and configuration generation
      - the frontier table rows
      - the Pareto set and the reference win-rate list
      - ranking with the tie rules
    - _Requirements: 20.7, 20.8, 20.9, 20.10, 20.11, 20.14_

  - [ ] 26.3 Write property test for frontier sweep generation
    - **Property 61: Frontier sweep generation**
    - **Validates: Requirements 20.7, 20.8**

  - [ ] 26.4 Write property test for the Pareto set
    - **Property 62: Pareto set**
    - **Validates: Requirements 20.10**

  - [ ] 26.5 Write property test for ranking and the reference list
    - **Property 63: Ranking and reference list**
    - **Validates: Requirements 20.11, 20.13, 20.14**

  - [ ] 26.6 Write property test for seeded analytics determinism
    - **Property 64: Seeded analytics determinism**
    - **Validates: Requirements 20.12, 21.9**

- [ ] 27. Holdout evaluation, walk-forward and cadence comparison
  - [ ] 27.1 Implement the holdout evaluation and Holdout_Log
    - Extend `src/fse/experiments/holdout.py`:
      - validate the config and read the Holdout_Log
      - warn with the overlap count and record the warning in the Run_Manifest
      - run on Holdout_Period sessions only
      - append one entry (`O_APPEND` plus fsync)
    - A failed request leaves the log byte-identical
    - _Requirements: 22.3, 22.4, 22.5, 22.6, 22.7, 22.8_

  - [ ] 27.2 Implement walk-forward tests
    - `src/fse/experiments/walkforward.py`:
      - window pairs
      - selection by the walk-forward objective among configurations not labeled "insufficient sample"
      - "no selection" pairs
      - concatenated out-of-sample results beside pooled in-sample results
    - _Requirements: 22.9, 22.10, 22.11, 22.12, 22.13, 22.14, 22.15, 22.16_

  - [ ] 27.3 Implement the cadence comparison
    - `src/fse/experiments/cadence.py`:
      - select the eligible sessions by stored Snapshot and bar interval
      - report an error when no session qualifies
      - compute per-cadence metrics and pairwise differences (shorter minus longer)
    - _Requirements: 18.8, 18.9, 18.10_

  - [ ] 27.4 Write property test for holdout isolation
    - **Property 67: Holdout isolation**
    - **Validates: Requirements 22.1, 22.2, 22.4**

  - [ ] 27.5 Write property test for the append-only Holdout_Log
    - **Property 68: Holdout_Log is append-only**
    - **Validates: Requirements 22.5, 22.6, 22.7**

  - [ ] 27.6 Write property test for walk-forward windows and selection
    - **Property 69: Walk-forward windows and selection**
    - **Validates: Requirements 22.9, 22.10, 22.11, 22.16**

  - [ ] 27.7 Write property test for the cadence comparison
    - **Property 54: Cadence comparison**
    - **Validates: Requirements 18.8, 18.10**

- [ ] 28. Experiment commands, reports and performance
  - [ ] 28.1 Wire `fse experiment` and the experiment reports
    - `src/fse/commands/experiment.py`: `fse experiment {ablation,sweep,montecarlo,holdout,walkforward,cadence}`
    - Extend `src/fse/reports/markdown.py` with Markdown plus CSV or JSON output for:
      - the ablation table
      - the frontier table with the Pareto set and the reference list
      - the pass estimate
      - walk-forward results
      - the cadence comparison
      - holdout results
    - _Requirements: 18.10, 19.14, 20.9, 20.10, 20.11, 21.6, 21.7, 21.11, 22.5, 22.14, 22.15_

  - [ ] 28.2 Write the backtest performance test
    - `tests/perf/test_backtest_budget.py` (marker `perf`, run manually): run the Playbook_Baseline, with fake cost overlays, on a synthetic 250-session cache at 60 s cadence. The Run_Manifest must be written within 10 minutes
    - _Requirements: 18.12_

- [ ] 29. Checkpoint - Measurement complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 30. Revised_Drafts
  - [ ] 30.1 Implement the Revised_Drafts builder
    - `src/fse/skilldocs/drafts.py`, templates under `src/fse/skilldocs/templates/` (holding the Narrator restatement rule and the engine-decides rule), and `src/fse/commands/drafts.py` (`fse drafts build --chosen <config>`). The builder:
      - labels each rule Kept, Changed or Removed
      - runs one comparison config per rule on the pre-holdout sessions
      - shows every figure with its trade count, session count, labeled date range, config hash and path count
      - marks rules Unmeasured from the "not codified" rows
      - writes one front-matter block, and writes new files only
    - Extend `src/fse/skilldocs/check.py` with the draft lint
    - _Requirements: 26.9, 26.10, 26.11, 26.12, 26.13, 26.14, 26.15, 26.16, 26.17_

  - [ ] 30.2 Implement draft approval and promotion
    - `src/fse/skilldocs/approvals.py` (sha256 approvals in `approvals.yaml`) and `fse drafts promote` in `src/fse/commands/drafts.py`. Promotion is the only code path that writes `SKILL.md`
    - _Requirements: 26.18_

  - [ ] 30.3 Write property test for draft labels and comparison configs
    - **Property 85: Draft labels and comparison configs**
    - **Validates: Requirements 26.10, 26.11**

  - [ ] 30.4 Write property test for promotion
    - **Property 86: Promotion requires approval**
    - **Validates: Requirements 26.18**

  - [ ] 30.5 Write smoke test for the draft templates
    - The templates pass the lint and hold the Narrator restatement rule and the engine-decides rule
    - _Requirements: 26.13, 26.14, 26.16_

- [ ] 31. Checkpoint - Operator runs the holdout evaluation and builds the Revised_Drafts
  - Ensure all tests pass, ask the user if questions arise.
  - Operator: choose a config, run `fse experiment holdout --config <chosen>`, then run `fse drafts build --chosen <chosen>`. Review the drafts and run `fse skilldocs check` and `fse scan-secrets`. Record an approval only after the review.

- [ ] 32. Live data feeds, recorder and replay
  - [ ] 32.1 Add the live ring-buffer MarketView
    - Extend `src/fse/pit/market_view.py` with a view over append-only ring buffers, using `available_at = max(Observation_Time, receipt_time)`
    - _Requirements: 5.1, 5.2, 5.3, 23.9_

  - [ ] 32.2 Implement the map feed
    - `src/fse/live/map_feed.py`:
      - polling sends two multi-symbol `GET /v1/heatmap` requests per interval
      - stream mode opens two `GET /v1/stream` connections
      - reconnect with `Last-Event-ID` after a close or 60 s without an event, at most once per 5 s
      - after a failed or slow refresh, keep the prior Map_State
    - _Requirements: 23.2, 23.3, 23.4, 23.14_

  - [ ] 32.3 Implement the bar feed, dark-pool feed and levels comparison
    - `src/fse/live/bar_feed.py`: ProjectX closed 1-minute bars
    - `src/fse/live/darkpool_feed.py`: runs when the Gate is enabled
    - `src/fse/live/levels_compare.py`: compares `GET /v1/gex/levels` every 60 s in Paper and Practice mode. The comparison is logged and used in no decision
    - _Requirements: 4.12, 23.10_

  - [ ] 32.4 Implement the live recorder
    - `src/fse/live/recorder.py`: writes `recordings/{session}.jsonl.gz` through the Log_Writer, covering every input kind with its receipt time, never headers or keys
    - _Requirements: 23.8_

  - [ ] 32.5 Implement replay mode
    - `src/fse/backtest/replay.py`, plus `mode="replay"` in `src/fse/backtest/runner.py`: the recorded receipt times become `available_at`, and replay uses the recorded Decision_Times and guard events
    - _Requirements: 23.9_

- [ ] 33. Paper_Broker, Live_Runner, guards and Finding_Cards
  - [ ] 33.1 Implement the Paper_Broker
    - `src/fse/sim/paper_broker.py`: an in-process broker using the Fill_Simulator rules
    - _Requirements: 23.15_

  - [ ] 33.2 Implement live state, persistent blocks, `fse halt` and `fse clear`
    - `src/fse/live/state_store.py`:
      - write EngineState after every Decision_Time (temp file, fsync, rename)
      - restore it on start
      - an unreadable state sets a `restore_failed` block
    - `live-state/blocks.json` holds the kinds `halt_command`, `bracket_failure`, `reconciliation` and `restore_failed`. An unreadable blocks file counts as blocked
    - `src/fse/commands/halt.py`: `fse halt` and `fse clear`
    - _Requirements: 16.10, 24.10, 24.26, 24.27_

  - [ ] 33.3 Implement live guards
    - `src/fse/live/guards.py`:
      - the stale-map guard: at 30 s, cancel resting entries within 1 s and block entries until fresh
      - a halt-file check at every Decision_Time
      - persistent blocks passed as `external_blocks` and recorded as `guard_event`
    - _Requirements: 23.7, 24.26, 24.27_

  - [ ] 33.4 Implement Finding_Cards and the Notifier
    - `src/fse/notify/finding_card.py`: `build_card` and `should_send`, the premarket card, and 2R alerts
    - `src/fse/notify/notifier.py`:
      - console, file and webhook sinks, with `NOTIFIER_WEBHOOK_URL` read from `EnvView`
      - delivery in a separate task with a timeout
      - a delivery failure is logged and changes no order logic
    - _Requirements: 25.1, 25.2, 25.3, 25.4, 25.5, 25.6, 25.7, 25.8, 25.14_

  - [ ] 33.5 Implement the Live_Runner, order router and `fse paper`
    - `src/fse/live/runner.py`, `src/fse/live/order_router.py` and `src/fse/commands/paper.py` (`fse paper --config`, which always runs in Paper Order_Mode):
      - the run window
      - refuse a refresh interval under 5 s (exit 2)
      - the start log: mode, config hash, code version and credit projection
      - the decision loop wakes on a tick or a new input and runs `Engine.step` synchronously, with the latency logged
      - intents are dispatched before the card is built
      - the Order_Mode is frozen at start
      - in Paper mode, the Broker_Adapter has no order methods
    - _Requirements: 23.1, 23.2, 23.5, 23.6, 23.11, 23.12, 23.13, 23.15, 24.2, 25.13_

  - [ ] 33.6 Write property test for paper isolation
    - **Property 71: Paper isolation**
    - **Validates: Requirements 23.1, 23.15**

  - [ ] 33.7 Write property test for the stale-map guard
    - **Property 72: Stale-map guard**
    - **Validates: Requirements 23.7**

  - [ ] 33.8 Write property test for replay parity
    - **Property 70: Replay parity**
    - **Validates: Requirements 23.5, 23.9**

  - [ ] 33.9 Write property test for card completeness
    - **Property 80: Card completeness**
    - **Validates: Requirements 25.1, 25.2, 25.3, 25.4**

  - [ ] 33.10 Write property test for the send schedule
    - **Property 81: Send schedule**
    - **Validates: Requirements 25.5, 25.6, 25.8**

  - [ ] 33.11 Write integration tests for the Live_Runner with a fake clock
    - Cover:
      - polling request shape and stream reconnect
      - refresh errors, the stale guard and the credit projection log
      - `Engine.step` latency under 1 s on a fixture session
      - a card emitted while a broker request is pending
      - a restart from saved state
      - an unreadable state that blocks entries until clear
    - _Requirements: 16.7, 16.10, 23.2, 23.3, 23.4, 23.6, 23.11, 23.12, 23.14_

- [ ] 34. Checkpoint - Paper run ready
  - Ensure all tests pass, ask the user if questions arise.
  - Operator (optional): run `fse paper --config configs/playbook_baseline.yaml` during RTH. Paper Order_Mode sends no broker orders.

- [ ] 35. Broker_Adapter and order safety (ProjectX)
  - [ ] 35.1 Implement the Broker_Adapter
    - `src/fse/projectx/broker.py`, extending `src/fse/projectx/models.py`:
      - `resolve_accounts` and `resolve_contract`
      - `place_bracketed`, using the design §24 payload
      - `modify`, `cancel`, `broker_state` and `find_by_client_id`, with the OQ8 journal fallback
      - credentials and account ids from `EnvView` only
      - spacing under 200 requests per 60 s
    - _Requirements: 24.7, 24.8, 24.13, 24.14, 24.15_

  - [ ] 35.2 Implement Order_Mode resolution, routing and `fse live`
    - Update `src/fse/live/order_router.py` and `src/fse/live/runner.py`:
      - Combine only when all three Combine_Opt_In conditions hold
      - Practice only when the practice id is set, resolved and different from `COMBINE_ACCOUNT_ID`
      - otherwise Paper, with every failed condition named in the first Finding_Card
      - each mode sends only to its own account id
      - `RiskManager.precheck` runs before the adapter
      - ignored instruments are never submitted, modified or cancelled
    - `src/fse/commands/live.py`: `fse live --config [--live-orders]` honors `order_mode`; `fse paper` stays Paper-only
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5, 24.6, 24.20, 24.23, 24.24, 24.25_

  - [ ] 35.3 Implement protective safety, reconciliation and outage handling
    - `src/fse/live/broker_safety.py`, wired through `src/fse/live/runner.py` and `src/fse/live/guards.py`:
      - stop confirmation within the configured time; on a bracket rejection or a missing stop, market-close the instrument and set a persistent block with a card
      - after a fill, modify the bracket legs to the planned prices, plus the TP1_Partial_BE resize and the TP2 order
      - the contract check at start and at each session start
      - client ids `fse-{run_uuid8}-{seq}`, with a persisted sequence and an append-only journal
      - reconciliation at every Decision_Time, listing ignored instruments
      - the outage block, lifted only after a clean reconciliation
    - _Requirements: 24.9, 24.10, 24.11, 24.12, 24.13, 24.16, 24.17, 24.18, 24.19, 24.21, 24.22_

  - [ ] 35.4 Write property test for Order_Mode resolution and routing
    - **Property 74: Order_Mode resolution and routing**
    - **Validates: Requirements 24.1, 24.3, 24.4, 24.5, 24.6**

  - [ ] 35.5 Write property test for the bracket payload
    - **Property 75: Bracket payload**
    - **Validates: Requirements 24.8**

  - [ ] 35.6 Write property test for protective close and persistent blocks
    - **Property 76: Protective close and persistent blocks**
    - **Validates: Requirements 24.9, 24.10, 24.27, 16.10**

  - [ ] 35.7 Write property test for client-id uniqueness
    - **Property 77: Client-id uniqueness and safe resubmission**
    - **Validates: Requirements 24.13, 24.14, 24.15**

  - [ ] 35.8 Write property test for reconciliation
    - **Property 78: Reconciliation**
    - **Validates: Requirements 24.18, 24.19, 24.20**

  - [ ] 35.9 Write integration tests for broker flows against mocked ProjectX
    - Use respx mocks with fake account and contract ids. Cover:
      - the opt-in matrix
      - bracket rejection with `errorCode 2` and a missing stop after a fill
      - outage and recovery, and reconciliation differences
      - ignored MGC and SIL positions
      - a contract mismatch
      - halt within one cadence, and a restart with persisted blocks
    - _Requirements: 24.1, 24.3, 24.9, 24.10, 24.12, 24.18, 24.19, 24.21, 24.22, 24.26, 24.27_

- [ ] 36. Optional Narrator
  - [ ] 36.1 Implement the Narrator and its prompt
    - `src/fse/notify/narrator.py` and `prompts/narrator.md`:
      - Narrator input comes from `to_narrator_fields()`, an allow-list with no account ids, passed through the Redactor
      - `NARRATOR_API_KEY` is read from `EnvView`
      - calls run in a separate task, with a 10 s timeout and a 1,500-character cap
      - on any failure, send the card without prose and a note
    - Wire the Narrator into `src/fse/notify/notifier.py` only. Leave `src/fse/live/runner.py` unchanged
    - _Requirements: 25.10, 25.11, 25.12, 25.13, 25.15, 26.13, 26.14_

  - [ ] 36.2 Write property test for Narrator input and fallback
    - **Property 82: Narrator input and fallback**
    - **Validates: Requirements 25.10, 25.12**

  - [ ] 36.3 Write property test for observer independence
    - **Property 73: Observer independence**
    - **Validates: Requirements 23.10, 25.14, 25.15**

- [ ] 37. README completion and repository checks
  - [ ] 37.1 Complete the README
    - Add:
      - one complete command line for `fse pull`, `fse backtest`, `fse report`, `fse paper` and `fse scan-secrets`
      - the account-rule verification notice
      - the TopstepX API terms notice, covering the device and VPN rules
      - the Auto OCO Brackets requirement (OQ7)
      - a pointer to the design's Practice checklist before any Combine use
    - _Requirements: 1.2, 1.17, 15.20, 24.28_

  - [ ] 37.2 Write smoke tests for the repository layout
    - Check:
      - the README entries and sections, and a `LICENSE` with no placeholders
      - `.env.example` names with empty values, and the `.gitignore` entries
      - exactly one row in `members/alantiix/README.md`
      - `docs/account-rules.md` fields
      - no credential arguments in the CLI
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.12, 1.17, 15.19, 15.20, 24.7, 24.28_

- [ ] 38. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Operator: run `fse scan-secrets`. No task enables Practice or Combine Order_Mode. Before any broker orders, work through the design's "Practice checklist before Combine Order_Mode".

## Notes

- Sub-tasks marked `*` are optional. These test sub-tasks have no `*` and are required, because they guard secrets in a public repository and order safety: 2.5, 2.6, 2.7, 3.3, 14.5, 33.6, 35.4–35.8, plus the import-rule test in 1.5, the schema credential test in 20.1 and the baseline test in 20.2.
- Public repository: tests and fixtures use fake values only (for example `fake-skylit-key-0000` and `FAKE-ACCOUNT-1`). Real values live only in the shell environment or the gitignored `.env`. No task writes a real key, credential, account name or account id to any file.
- Only the Operator sets these values; tasks leave them as Operator steps or required placeholders:
  - the LICENSE choice (1.4)
  - commission and exchange fee per instrument (21)
  - `regime.min_abs_value` (10.3, 21)
  - the account-rule "date checked" (21)
- No task places orders on a Practice or Combine account. Every broker test runs against respx mocks. Combine Order_Mode stays behind the three Combine_Opt_In conditions (Property 74).
- Property tests write only their own file, `tests/property/test_pNN_<topic>.py`, plus new helper modules named for the property under `tests/strategies/` or `tests/reference/`. They import helpers from earlier waves but never edit them, and no task after 1.1 edits `tests/conftest.py`.
- CLI commands live in `src/fse/commands/*.py` and are discovered by `src/fse/cli.py`, so command tasks can run in parallel. Config sections live in `src/fse/config/schema/*.py` for the same reason. Both are small layout refinements of the design's single `cli.py` and `schema.py`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4"] },
    { "id": 1, "tasks": ["1.5", "2.1", "2.3"] },
    { "id": 2, "tasks": ["1.6", "2.2", "4.1", "2.6"] },
    { "id": 3, "tasks": ["1.7", "1.8", "2.4", "2.5", "3.1", "4.2", "4.3", "5.1", "5.3", "5.4"] },
    { "id": 4, "tasks": ["2.7", "3.2", "5.2", "5.10", "6.1"] },
    { "id": 5, "tasks": ["3.3", "3.4", "3.5", "3.6", "5.5", "5.6", "5.8", "6.2", "6.3"] },
    { "id": 6, "tasks": ["5.7", "5.11", "6.4", "6.5"] },
    { "id": 7, "tasks": ["5.9", "5.12", "8.1", "13.1", "22.4"] },
    { "id": 8, "tasks": ["8.2", "9.1", "11.1", "12.1", "14.1", "14.2", "18.1", "18.2"] },
    { "id": 9, "tasks": ["9.2", "10.1", "11.2", "13.2", "9.4", "9.5", "9.6", "11.3", "11.4", "11.5", "11.6", "12.2", "12.3", "12.4", "14.3", "14.4", "14.5", "10.3", "18.3", "18.4", "18.5", "18.6", "18.7", "18.8", "18.10", "22.3"] },
    { "id": 10, "tasks": ["9.3", "9.7", "10.2", "10.4", "10.5", "11.7", "13.4", "22.9", "26.1"] },
    { "id": 11, "tasks": ["9.8", "13.3"] },
    { "id": 12, "tasks": ["13.5", "13.6", "13.7", "13.8", "15.1"] },
    { "id": 13, "tasks": ["15.2", "15.3", "15.4", "16.1"] },
    { "id": 14, "tasks": ["16.2", "16.3", "16.4", "16.5", "16.6", "18.9", "20.1"] },
    { "id": 15, "tasks": ["19.1", "20.2", "20.5", "20.6", "20.7"] },
    { "id": 16, "tasks": ["19.2", "20.3"] },
    { "id": 17, "tasks": ["19.3", "19.4", "19.5", "19.6", "19.7", "20.4", "22.1", "24.1", "28.2"] },
    { "id": 18, "tasks": ["22.2", "22.7", "24.2", "24.3", "24.4", "25.1", "26.6"] },
    { "id": 19, "tasks": ["22.5", "22.6", "22.8", "25.2", "26.2", "27.1", "27.3"] },
    { "id": 20, "tasks": ["22.10", "22.11", "26.3", "26.4", "26.5", "27.2", "27.4", "27.5", "27.7"] },
    { "id": 21, "tasks": ["27.6", "28.1"] },
    { "id": 22, "tasks": ["30.1", "32.1", "32.4", "33.1", "33.2", "33.4"] },
    { "id": 23, "tasks": ["30.2", "30.3", "30.5", "32.2", "32.3", "32.5", "33.3", "33.9", "33.10"] },
    { "id": 24, "tasks": ["30.4", "33.5"] },
    { "id": 25, "tasks": ["33.6", "33.7", "33.8", "33.11", "35.1"] },
    { "id": 26, "tasks": ["35.2", "35.5"] },
    { "id": 27, "tasks": ["35.3", "35.4", "36.1"] },
    { "id": 28, "tasks": ["35.6", "35.7", "35.8", "35.9", "36.2", "36.3", "37.1"] },
    { "id": 29, "tasks": ["37.2"] }
  ]
}
```
