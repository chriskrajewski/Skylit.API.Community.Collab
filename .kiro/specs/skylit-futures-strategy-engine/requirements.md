# Requirements Document

## Introduction

Chris (Discord `alantiix`) runs an LLM futures copilot on a 5-minute RTH schedule. The copilot reads Skylit Heatseeker GEX/VEX maps and Flowseeker dark-pool prints, follows a prose playbook (`SKILL.md`, the working copy `skill-fine-tune/current_working_SKILL_100226.md`, and `skill-fine-tune/TASK.md`), and trades MES/MNQ on a Topstep 50K Trading Combine through ProjectX. The copilot rarely takes a trade, and nothing measures which rules add edge.

This feature replaces LLM rule interpretation with a deterministic, parameterized Strategy_Engine. One code path runs in a no-look-ahead Backtester over Skylit history and in a fast-polling Live_Runner that defaults to paper trading. The Backtester measures each rule (gate-rejection funnel, shadow trades, ablation), maps the win-rate vs reward:risk trade-off, and estimates the Combine pass probability. The results feed revised, data-backed skill and task drafts.

The spec addresses six findings from the review of the current skill and task:

1. Conflicting rules: positive-gamma days target the next node, while every trade also needs 3:1 and no equal-pull opposition inside 3R.
2. Over-filtering: about 21 hard passes and 10 required confluences, with no measure of which gates add edge.
3. Cadence: resting limits are cancelled on every reshuffle, and a 5-minute poll misses taps that start and end between runs.
4. Inconsistency: an LLM reads prose rules differently each run and is told to pass when unsure.
5. Format defects: the working copy has two stacked YAML front-matter blocks, and `SKILL.md` shows `?` where `→` and `≥` belong.
6. Win-rate expectations: an 80% win rate at a 3:1 minimum implies about +2.2R per trade. The Backtester reports the measured trade-off. This spec makes no win-rate promise.

The unlimited Skylit key is temporary. Requirement 3 stores history locally so backtests keep running after the key is revoked.

Scope: ES/NQ levels traded as MES/MNQ during RTH on one account. Out of scope: GC/SL metals, options execution, and copying trades to other accounts.

### Open questions (resolve in design)

1. Atlas `/v1/history` coverage of ES/NQ futures is unconfirmed (TASK.md calls the source "Atlas REST for OHLCV NQ1/ES1"). ProjectX bar history is the fallback.
2. Historical depth of Flowseeker dark-pool prints and of intraday VIX bars is unconfirmed.
3. The `/v1/historical` docs show a `nodeType` per strike. Whether stored history carries the field on every date is unconfirmed. `/v1/gex/levels` is live only.
4. Topstep rule values change. The defaults in Requirement 15 come from a third-party summary dated July 2026 and need a check against the Topstep help center before Combine use.
5. Implementation language is open. Python is the working assumption.

## Glossary

- **Operator**: Chris (Discord `alantiix`), who runs the Project.
- **Project**: The code project at `members/alantiix/skylit-futures-strategy-engine/`, laid out per `CONTRIBUTING.md`.
- **Skylit_Client**: The component that calls Skylit REST endpoints on `api.skylit.ai` and `atlas-api.skylit.ai`.
- **attempt**: One send of a request by the Skylit_Client. Each retry is a new attempt of the same request.
- **Fetch_Log**: The Skylit_Client's record of every attempt (send time, endpoint, attempt number and outcome), written through the Log_Writer.
- **Replay_Request**: A `GET /v1/historical/range` or `GET /v1/historical` request (a historical replay request in Requirement 2).
- **Data_Cache**: The local on-disk store of fetched Skylit data and bars.
- **Pull_Window**: The span of each session that a pull covers: the configured start and end times (default 09:00–16:00, matching the Live_Runner run window). On early-close sessions it ends at the early-close time in the Project's exchange calendar.
- **Cache_Window**: One of the consecutive 15-minute spans that tile a session's Pull_Window from its start, for one symbol, metric and Heatmap_View. The last span ends at the Pull_Window end and may be shorter. A Cache_Window is complete once every Replay_Request for it has returned Snapshots or `no_data` and the Data_Cache has written every Snapshot it keeps. Otherwise it is incomplete.
- **Bar_Source**: The configured provider of futures and VIX OHLCV bars: Atlas `/v1/history` or ProjectX bar history.
- **Snapshot**: One Heatseeker per-strike heatmap for one symbol, one metric (`gamma` for GEX, `vanna` for VEX) and one `asOf`.
- **Heatmap_View**: The request settings that shape a Snapshot: `maxStrikes`, `maxExpirations` or `expirations`, and `includeEmpty` (default: the Skylit defaults 92, 5 and false).
- **session**: One RTH date.
- **trading day**: Topstep's day, from 18:00 on the prior evening to the firm's flat deadline.
- **RTH**: 09:30–16:00. Every clock time in this document is America/New_York.
- **Decision_Time**: An instant at which the Strategy_Engine evaluates market state.
- **Decision_Cadence**: The interval between consecutive Decision_Times, for example 5 s, 60 s or 300 s.
- **Map_State**: For each configured symbol and metric, the Snapshot with the latest `asOf` at or before a Decision_Time.
- **Snapshot_Age**: The Decision_Time minus the oldest `asOf` in Map_State.
- **Observation_Time**: The instant an input becomes usable by the Strategy_Engine: a Snapshot's `asOf`; a bar's close time (open time plus bar duration); the latest timestamp Skylit returns with a dark-pool print; 09:31 of the session for the VIX daily open; the prior session's close for the VIX prior close.
- **Trinity**: SPX, SPY and QQQ.
- **NQ_Sources**: The symbols that feed NQ levels (default QQQ, NDX and NDXP).
- **Node**: A strike whose absolute exposure meets the node threshold. A Pika has positive GEX. A Barney has negative GEX.
- **King**: The strike with the largest absolute value in a Snapshot (the Absolute Value Rule).
- **Floor** / **Ceiling**: The largest-absolute-value Node below / above spot.
- **Gatekeeper**: A Node between spot and a larger Node that meets the Gatekeeper fraction of the larger Node.
- **Air_Pocket**: A strike range without a Node, at least the configured width.
- **Clear_Skies** / **Empty_Basement**: No Node above spot / below the Floor within the configured lookout distance.
- **Sloppy_Seconds**: A Node whose value dropped after a Tap. The playbook treats the level as dead.
- **Node_Velocity**: The percent change in a strike's absolute value across the configured velocity window, computed from Snapshots as Requirement 5 specifies.
- **Dormant**: A Node label for a Node farther from spot than the configured Dormant distance while the Regime is not Vanna_Dominant.
- **Fresh** / **Tested** / **Delivered** / **Decaying**: The four Node lifecycle states, set from Tap history and from the change in a Node's absolute value.
- **Regime**: One label per Map_State: Positive_Gamma, Negative_Gamma, Vanna_Dominant (the bootcamp "Rainbow Road"), Whipsaw, or Structureless (the Patternpedia "Rainbow Road").
- **Regime_Symbol**: The configured symbol (default SPX) whose gamma and vanna Snapshots feed the Regime and Map_Grade rules. The Whipsaw King check also reads the other Trinity symbols.
- **Map_Grade**: A_Plus_Map, Neutral_Map or F_Map, from numeric map-quality rules.
- **Node_Classifier**: The component that labels Nodes, King, Floor, Ceiling, Gatekeepers, Air_Pockets and Node lifecycle states from raw strike values.
- **Regime_Classifier**: The component that assigns the Regime and the Map_Grade.
- **Level_Converter**: The component that maps index and ETF strikes to ES and NQ prices.
- **Futures_Price**: The close of the latest 1-minute Bar_Source bar of the ES or NQ contract (or MES or MNQ) that closed at or before the Decision_Time.
- **Deflection_Band**: The tolerance around a converted Node level inside which a Tap counts.
- **Tap**: Futures price trading inside the Deflection_Band of a converted Node. Requirement 6 criterion 14 defines how Taps are counted.
- **Chart_Feature_Builder**: The component that computes structure levels and candle labels from futures bars.
- **Chart_Level**: Any prior RTH, overnight, Asia, London or IB30 high or low, or any 1-hour or 4-hour swing support or resistance level, that the Chart_Feature_Builder exposes at a Decision_Time.
- **BOS_Leg**: The price range of one break of structure, from its origin (Fibonacci level 1) to its terminal (Fibonacci level 0).
- **Pattern**: A named playbook setup: Gatekeeper_Fade, Floor_Ceiling_Bounce, Beach_Ball, Rug, Reverse_Rug, Whipsaw_Fade or Trend_Follow.
- **Setup_Detector**: The component that turns Map_State, converted levels and chart features into Candidate_Setups for enabled Patterns.
- **Candidate_Setup**: A proposed trade: instrument, direction, entry, stop, targets, Pattern, source Node and Decision_Time.
- **Setup_Key**: The identity of one Candidate_Setup across Decision_Times: instrument, Pattern, source Node strike, direction, session date and Tap sequence number.
- **Detection_Skip**: A record that the Setup_Detector emits in place of a Candidate_Setup whose stop, entry and first target break the price order or that has no target price. It names the Setup_Key, the Decision_Time and the failed condition.
- **Gate**: An individually toggleable rule that accepts or rejects a Candidate_Setup.
- **Gate_Evaluator**: The component that evaluates Gates and assigns the Grade.
- **Rejection_Reason**: The Gate id, measured value and threshold of one failed Gate.
- **Grade**: A_Plus (every enabled Gate passes), Alert_2R (every enabled Gate passes except min_reward_risk, and reward:risk is at least 2), or Pass.
- **Exit_Mode**: A target and exit policy: Fixed_R, Next_Node, TP1_Partial_BE, Opposition_Or_Fixed_R or Trailing.
- **Order_Planner**: The component that turns an accepted Candidate_Setup into entry, stop and exit orders and manages those orders.
- **Breakeven_Price**: A trade's entry fill price moved in the trade's favor by the configured breakeven offset (default 1 tick, range 0 to 20 ticks): entry plus offset for a long, entry minus offset for a short.
- **Flatten_Time**: The session time at which the Order_Planner flattens: the configured time (default 15:55, range 09:30–16:00), or on an early-close session the configured lead before the close (default 30 minutes, range 15 to 120 minutes). It is the strategy's own flatten time and is distinct from the account's Flat_Deadline.
- **Cancel_Trigger**: An individually enabled condition, checked at each Decision_Time against the values at the Candidate_Setup's Decision_Time: King flip (a different strike is King in the source Node's Snapshot), source Node gone (the source strike no longer meets the node threshold), source Node sign flip (the source strike's value changed sign), stdev-fib leg dropped (the Chart_Feature_Builder dropped the leg the Candidate_Setup used), or opposition in target window (the opposition_inside_target Gate condition of Requirement 11 now holds).
- **R**: A trade's initial risk in dollars: |entry - stop| × point value × contracts.
- **R_Multiple**: A trade's net P&L divided by R.
- **Fill_Simulator**: The component that simulates fills against bars.
- **Position_Sizer**: The component that sets contracts per trade.
- **Micro_Equivalent**: The position-cap unit. One MES or MNQ contract counts as 1 Micro_Equivalent; one ES or NQ contract counts as 10.
- **Kill_Switch**: A playbook lockout rule, such as the trades-per-day limit or the two-loser lockout.
- **Risk_Manager**: The component that applies Kill_Switches and internal loss limits in the Backtester and the Live_Runner.
- **Lockout**: A Risk_Manager state, started by a Kill_Switch or the internal daily loss stop, that blocks new entries from its start through the last session it covers.
- **Losing_Trade**: A closed trade (its last exit has filled) whose net P&L across all its fills is below minus the configured losing-trade tolerance (default $0).
- **Loss_Streak**: The number of Losing_Trades closed in a row, counted across sessions. It resets to 0 when a trade that is not a Losing_Trade closes and when a consecutive_losers Lockout starts.
- **Account_Simulator**: The component that simulates Topstep 50K Trading Combine rules.
- **Combine_Attempt**: One simulated run of the Trading Combine. It starts at the starting balance and ends at a Combine_Pass, at a Maximum Loss Limit breach, or at the end of the data range.
- **Combine_Pass**: Ending a trading day with the balance at or above the starting balance plus the current profit target (after any consistency adjustment), before any Maximum Loss Limit breach. Requirement 15 criterion 17 defines when it is recorded.
- **MLL_Floor**: The balance level at or below which the Maximum Loss Limit is breached.
- **Flat_Deadline**: The time in a session by which the Account_Simulator requires flat positions (the account rule, default 16:10).
- **Strategy_Engine**: The Node_Classifier, Regime_Classifier, Level_Converter, Chart_Feature_Builder, Setup_Detector, Gate_Evaluator, Order_Planner, Position_Sizer and Risk_Manager, as one code path shared by the Backtester and the Live_Runner.
- **Strategy_Config**: A versioned file that sets the enabled flag and parameters of every Pattern, Gate, Exit_Mode, Kill_Switch and account rule.
- **Playbook_Baseline**: The Strategy_Config that codifies the current Skill_Documents as written.
- **Config_Loader** / **Config_Printer**: The components that parse and validate / serialize a Strategy_Config.
- **Config_Schema**: The Project's definition of each Strategy_Config key: its type, its allowed values or range, and either a default value or the mark "required". Where these requirements state a default for a setting, the Config_Schema default equals that value.
- **Backtester**: The component that runs the Strategy_Engine over historical data with the Fill_Simulator and the Account_Simulator.
- **Run_Manifest**: The record of one run: Strategy_Config hash, data range, code version, random seed and output files.
- **Shadow_Trade**: The simulated outcome of a rejected Candidate_Setup as if the Candidate_Setup had been accepted.
- **Gate_Funnel**: The report of Candidate_Setup outcomes and Gate rejections.
- **Experiment_Runner**: The component that runs ablations, sweeps, cadence comparisons and walk-forward tests.
- **Report_Generator**: The component that writes metrics and reports as Markdown plus CSV or JSON.
- **Primary_Win_Rate**: The win-rate definition used for the frontier table, the Pareto set, the reference-win-rate list and the bootstrap confidence intervals. It is configurable, with the default being definition (a) of Requirement 20 criterion 4: net P&L above $0.
- **Monte_Carlo_Simulator**: The component that estimates Combine_Pass probability from resampled sessions.
- **path**: One simulated Combine attempt in the Monte_Carlo_Simulator. It is a sequence of sessions drawn from a run, one per trading day, and ends at Combine_Pass, at a Maximum Loss Limit breach, or at the configured maximum trading days.
- **intraday equity low**: For one session, the lowest value on any bar of the trading day's net realized P&L plus the unrealized P&L of open positions, each valued at its bar's worst price (the low for a long, the high for a short), measured from the session's starting equity.
- **Holdout_Period**: The most recent sessions, reserved for out-of-sample evaluation; Requirement 22 logs every evaluation and warns when the period has been seen before.
- **Holdout_Log**: The append-only record of holdout evaluations, one entry per evaluation. The Experiment_Runner never edits or deletes an existing entry.
- **Live_Runner**: The component that runs the Strategy_Engine on live Skylit data and bars.
- **run window**: The configured daily span in which the Live_Runner runs (default 09:00 inclusive to 16:00 exclusive).
- **halt file** / **halt command**: The configured file path whose existence, or the Operator command, that tells the Live_Runner to cancel resting entries and block new entries.
- **clear command**: The Operator command that lifts an entry block the Live_Runner keeps until the Operator clears it, including a halt command block.
- **Order_Mode**: Paper (the in-process Paper_Broker, the default), Practice (the broker practice account), or Combine (the 50K Combine account).
- **Combine_Opt_In**: The three conditions for Combine Order_Mode: the Strategy_Config selects Combine, `COMBINE_ACCOUNT_ID` equals the account the Broker_Adapter resolves, and the start command includes the explicit live-orders flag.
- **Paper_Broker**: An in-process broker that fills orders with the Fill_Simulator rules.
- **Broker_Adapter**: The ProjectX (TopstepX) client for accounts, contracts, orders and positions.
- **Broker_State**: For one instrument on the broker account, the net position (side and contracts) and each working order (client id, side, contracts and price).
- **bracket**: The stop-loss order and target order attached to an entry order, as Requirement 24 criterion 8 defines.
- **Finding_Card**: A short summary of the map, setups, Gate results and orders.
- **Narrator**: An optional LLM that rewrites Finding_Card data as prose.
- **Notifier**: The Finding_Card destination: console, file, or a webhook URL read from an environment variable.
- **Log_Writer**: The component that writes logs, reports and manifests and redacts secrets.
- **Secret_Scanner**: A command that checks tracked repository files for configured secret values.
- **Secret_Variable**: An environment variable whose value the Log_Writer redacts and the Secret_Scanner searches for. The list is `SKYLIT_API_KEY`, each ProjectX credential variable, the Notifier webhook URL variable, each Narrator credential variable, and each variable the Operator adds to the configured secret list. Account id variables are Secret_Variables only when the Operator adds them.
- **blank**: An environment variable value that is unset, empty or whitespace only.
- **Skill_Documents**: `members/alantiix/skylit-academy-playbook-skill/SKILL.md`, `skill-fine-tune/current_working_SKILL_100226.md` and `skill-fine-tune/TASK.md`. Each file is a Skill_Document.
- **Revised_Drafts**: The data-backed revisions of the skill and task, written to `skill-fine-tune/`. Each file is a Revised_Draft.

## Requirements

### Requirement 1: Project layout and secret handling

**User Story:** As the Operator, I want the engine in my member folder with every credential kept out of this public repository, so that I can share the code without leaking keys.

#### Acceptance Criteria

1. THE Project SHALL reside in the folder `members/alantiix/skylit-futures-strategy-engine/`, with `README.md`, `LICENSE` and `.env.example` at the top level of that folder, as `CONTRIBUTING.md` specifies.
2. THE Project SHALL include these items in its README: the entries `Discord username: alantiix`, `Project identifier: skylit-futures-strategy-engine` and `Kind: agent`; a `What it does` section that replaces the template instruction with a description of the Project; and a `Skylit surface` section that sets Access to `REST` and names at least Heatseeker and Flowseeker as products.
3. THE Project SHALL include a `LICENSE` file that holds the full text of the license the Operator chose and none of the project template's placeholder text.
4. THE Project SHALL add exactly one row for `skylit-futures-strategy-engine` to the Projects table in `members/alantiix/README.md`, with a link to the Project README, the kind `agent` and a one-line description.
5. THE Project SHALL list these variables in `.env.example`, each as a `NAME=` line with nothing after the equals sign: `SKYLIT_API_KEY`, every ProjectX credential and account id variable (including `PRACTICE_ACCOUNT_ID` and `COMBINE_ACCOUNT_ID`), the Notifier webhook URL variable and every Narrator credential variable.
6. THE Project SHALL take the value of `SKYLIT_API_KEY` and of every other Secret_Variable only from the shell environment or the gitignored `.env` file in the Project folder, with a non-blank shell value taking precedence over the `.env` value.
7. IF `SKYLIT_API_KEY` is blank in both the shell environment and the Project `.env` file and the Skylit_Client has a request to send, THEN THE Skylit_Client SHALL send no request, print an error message that names `SKYLIT_API_KEY` and contains no secret value, and end the command with a non-zero exit status.
8. WHILE offline mode is set, THE Backtester SHALL complete each run from the Data_Cache with `SKYLIT_API_KEY` blank and report no error about the variable.
9. WHEN the Project writes, prints or sends a log line, report, Run_Manifest, recorded live input, Finding_Card or error message (including uncaught-error output), THE Log_Writer SHALL first replace with `[REDACTED]` every occurrence of each non-blank Secret_Variable value and of each session token the Broker_Adapter receives from ProjectX.
10. THE Data_Cache SHALL default to a directory whose resolved absolute path lies outside the repository working tree.
11. IF the configured Data_Cache directory resolves to a path inside the repository working tree that git does not ignore, THEN THE Data_Cache SHALL write no file, print an error message that names the path, and end the command with a non-zero exit status.
12. THE Project SHALL include a `.gitignore` file in the Project folder that lists `.env`, the Data_Cache directory name, and the default output directory of each run output that contains Snapshot values, bars or dark-pool prints, including decision logs and recorded live inputs.
13. WHEN the Operator runs the Secret_Scanner, THE Secret_Scanner SHALL search the working-tree content of every file in the repository's git index, including newly staged files, for each non-blank value of each Secret_Variable from the shell environment and from the Project `.env` file, and print the path of each file that contains at least one of those values.
14. WHEN the Secret_Scanner completes a search, THE Secret_Scanner SHALL exit with status 0 if no file matched and with a non-zero status if one or more files matched.
15. IF every Secret_Variable is blank in both the shell environment and the Project `.env` file, or the Secret_Scanner cannot list the repository's tracked files, THEN THE Secret_Scanner SHALL print an error message that states the cause and exit with a non-zero status.
16. THE Secret_Scanner SHALL limit each output line to a matching file path or a status message, and print no secret value and no content from a scanned file.
17. THE Project SHALL document these items in its README: the setup steps (dependency installation, copying `.env.example` to `.env`, and setting `SKYLIT_API_KEY`); a command line with every required argument for each of the pull, backtest, report, paper-run and Secret_Scanner commands; and, for each variable in `.env.example`, its purpose, whether it is a Secret_Variable, and the commands or Order_Modes that need it.

### Requirement 2: Skylit API access, pacing and retries

**User Story:** As the Operator, I want pulls to follow Skylit's published limits and recover from transient errors, so that long pulls finish without throttling.

#### Acceptance Criteria

1. WHEN the first network request after the Skylit_Client starts is due, THE Skylit_Client SHALL read `limits.requestsPerMinute` and `limits.historicalInFlight` from `GET /v1/account` before sending that request.
2. IF `GET /v1/account` fails on its last allowed attempt, or its response lacks a positive integer `limits.requestsPerMinute` or `limits.historicalInFlight`, THEN THE Skylit_Client SHALL use the configured fallback in place of each value it could not read (default 60 for `limits.requestsPerMinute` and 1 for `limits.historicalInFlight`).
3. THE Skylit_Client SHALL send at most `limits.requestsPerMinute` requests, retries included, in any rolling 60-second window.
4. THE Skylit_Client SHALL have at most `limits.historicalInFlight` `GET /v1/historical` and `GET /v1/historical/range` requests awaiting a response at any instant.
5. WHEN a response carries an `X-RateLimit-Remaining` value at or below the configured low-water mark (default 5), THE Skylit_Client SHALL send no request, retries included, until the reset time in that response's `X-RateLimit-Reset` header, or for 60 s if the response has no `X-RateLimit-Reset` header.
6. IF an attempt receives status 429 and the request has had fewer than 5 attempts, THEN THE Skylit_Client SHALL send no request for the `Retry-After` delay if the response has that header, else until the `X-RateLimit-Reset` time plus a uniform random 0–1 s jitter if the response has that header, else for 60 s plus a uniform random 0–1 s jitter, and then retry the request.
7. IF an attempt receives status 500, 502, 503 or 504, fails with a network error, or receives no complete response within the configured request timeout (default 60 s), and the request has had fewer than 5 attempts, THEN THE Skylit_Client SHALL retry the request after a uniform random wait from 0 s to min(30 s, 1 s × 2^n), where n is the number of attempts the request has had.
8. IF the 5th attempt of a request receives status 429, 500, 502, 503 or 504, fails with a network error, or receives no complete response within the request timeout, THEN THE Skylit_Client SHALL record the request as failed in the Fetch_Log with the last status code or error type and continue with the next request.
9. IF an attempt receives status 400, 404 or 422, or any other status from 400 to 599 that this requirement does not name, THEN THE Skylit_Client SHALL record the status code, any error code in the response, the endpoint path and the query parameters in the Fetch_Log and continue with the next request without a retry.
10. IF an attempt receives status 401, 402 or 403, THEN THE Skylit_Client SHALL send no further request and stop with an error for the Operator that names the status code and any error code in the response and contains no secret value.
11. WHEN a response carries an `X-Credits-Remaining` header, THE Skylit_Client SHALL record the header value in the Fetch_Log entry for that attempt.

### Requirement 3: Heatmap history pull and local cache

**User Story:** As the Operator, I want Heatseeker history pulled once and stored locally, so that backtests run offline after the unlimited key is revoked.

#### Acceptance Criteria

1. WHEN the Operator requests a pull for a date range (start and end dates inclusive), THE Skylit_Client SHALL print, before the first Replay_Request, the estimated Replay_Request count, credit cost (at the configured credits per request of each endpoint) and disk size in GB for the Cache_Windows that the pull will request from Skylit, in total and per symbol and metric.
2. IF the estimated disk size of the pull exceeds the configured limit (default 20 GB), THEN THE Skylit_Client SHALL send no Replay_Request unless the Operator confirms the pull at a prompt.
3. WHEN a pull processes a session dated less than 365 days before the date on which the pull started, THE Skylit_Client SHALL fetch every Snapshot in each of the session's Cache_Windows not served from the Data_Cache with `GET /v1/historical/range` requests that each span at most one Cache_Window, for the `gamma` and `vanna` metrics, each configured symbol (default SPX, SPY, QQQ, NDX and NDXP) and the configured Heatmap_View.
4. WHEN a pull processes a session dated 365 or more days before the date on which the pull started, THE Skylit_Client SHALL request `GET /v1/historical` at the Pull_Window start and at every multiple of the configured sample interval after it (default 60 s; a whole number of minutes from 1 to 15) before the Pull_Window end, in each of the session's Cache_Windows not served from the Data_Cache, for the `gamma` and `vanna` metrics, each configured symbol and the configured Heatmap_View.
5. THE Skylit_Client SHALL send no Replay_Request for a configured symbol on a session dated before that symbol's first history date in `GET /v1/symbols`.
6. THE Data_Cache SHALL key each stored Cache_Window by symbol, metric, Heatmap_View, session date and Cache_Window start time.
7. WHERE a storage interval is configured (a whole number of seconds from 1 to 300 that divides 900), THE Data_Cache SHALL store in each Cache_Window, for each interval boundary of that Cache_Window, only the Snapshot with the latest `asOf` at or before that boundary, with boundaries at the Cache_Window start and every multiple of the storage interval after it, up to and including the Cache_Window end.
8. WHEN a pull processes a Cache_Window of a completed session (a session whose Pull_Window ended before the pull started) that the Data_Cache holds marked complete under the same key, THE Skylit_Client SHALL serve the Cache_Window from the Data_Cache without a Replay_Request, including a Cache_Window for which Skylit returned `no_data`.
9. IF a pull stops or ends before a Cache_Window is complete, including after an Operator interrupt, a process crash, a Requirement 2 cancellation, a Replay_Request error other than `no_data`, or a Data_Cache write error, THEN THE Data_Cache SHALL keep that Cache_Window marked incomplete, so that the next pull requests the Cache_Window again.
10. THE Data_Cache SHALL return, for every Snapshot written and read back, a Snapshot equal in every field to the Snapshot that Skylit returned, including `asOf` and every strike value with no rounding (round-trip property).
11. WHEN a pull ends, including a pull stopped by an error or an Operator interrupt, THE Skylit_Client SHALL write a coverage report per symbol and metric over every requested session, whether fetched in that pull or served from the Data_Cache, listing sessions requested, sessions with at least one stored Snapshot, sessions skipped as dated before the symbol's first history date, `meta.resolution` per session, each `no_data` gap with its start and end times, and each Cache_Window left incomplete.
12. WHILE offline mode is set, THE Backtester SHALL read only from the Data_Cache, with no network request, including when `SKYLIT_API_KEY` is unset.
13. WHILE offline mode is set, THE Backtester SHALL print, before evaluating the first Decision_Time, each requested session that has an absent or incomplete Cache_Window, with the symbol, metric and number of absent or incomplete Cache_Windows.
14. IF the requested start date is after the end date, the end date is after the current date, `GET /v1/symbols` fails after any Requirement 2 retries, or a configured symbol is absent from `GET /v1/symbols`, THEN THE Skylit_Client SHALL stop the pull before the first Replay_Request with an error that names the invalid date, the failed call or the missing symbol.
15. IF a Data_Cache write fails, THEN THE Skylit_Client SHALL stop the pull before the next Replay_Request with an error that names the symbol, metric, session date and Cache_Window start time of the failed write.

### Requirement 4: Futures bars and auxiliary data

**User Story:** As the Operator, I want ES/NQ bars, VIX, dark-pool prints and event calendars on the same timeline as the heatmaps, so that chart and confluence rules can be tested.

#### Acceptance Criteria

1. THE Bar_Source SHALL provide 1-minute OHLCV bars for the configured futures instruments (default ES and NQ, or MES and MNQ where configured) for each requested session's trading day, from 18:00 on the prior calendar evening (Sunday 18:00 for a Monday session) to the firm's flat deadline (default 16:10).
2. WHEN a pull starts, THE Skylit_Client SHALL record in the coverage report whether Atlas `/v1/history` serves each configured futures instrument: served if 1-minute bar requests for the first and last sessions of the requested range each return at least one bar, otherwise not served, with the last error code if a request failed.
3. IF Atlas `/v1/history` does not serve a configured futures instrument at the start of a pull, THEN THE Bar_Source SHALL take that instrument's bars for every session of the pull from the configured alternate source (default ProjectX bar history).
4. THE Skylit_Client SHALL split each Atlas `/v1/history` request into contiguous, non-overlapping windows of at most the tier's `max_days` (default 90 trading days for 1-minute bars) that together cover the requested date range.
5. THE Bar_Source SHALL take every bar of a session, including the bars from 18:00 on the prior evening, from the one contract that the configured roll calendar assigns to that session.
6. THE Bar_Source SHALL record in the coverage report, for each session and futures instrument, the contract used and the source that supplied the bars.
7. THE Bar_Source SHALL stamp each futures and VIX bar from any source with its open and close instants (10:00:00 and 10:01:00 for the 1-minute bar covering 10:00–10:01), directly comparable with Snapshot `asOf` values and unambiguous across daylight-saving transitions, whatever the source's time zone or bar-labeling convention.
8. IF a configured futures instrument has no bar for one or more RTH minutes of a session (09:30–16:00, ending at the early-close time on early-close dates), THEN THE Bar_Source SHALL record in the coverage report the session, the instrument, the missing minute ranges and the cause (no contract in the roll calendar, no bars returned, or the last error code of a failed request), without synthesizing bars for the missing minutes.
9. THE Bar_Source SHALL provide, for each session, the VIX index daily open, stamped as known at 09:31 (the close of the session's first 1-minute VIX interval), and the VIX close of the most recent prior session (the Friday close for a Monday session).
10. WHERE an intraday VIX source is configured, THE Bar_Source SHALL provide 1-minute VIX index bars for each requested session's RTH (09:30–16:00, ending at the early-close time on early-close dates).
11. IF a session lacks the VIX daily open, the prior-session VIX close or, where an intraday VIX source is configured, a 1-minute VIX bar for any RTH minute, THEN THE Bar_Source SHALL record in the coverage report the session and each missing VIX item.
12. WHERE the dark_pool_confluence Gate is enabled, WHEN a pull starts, THE Skylit_Client SHALL fetch dark-pool prints for each configured ticker (default SPY and QQQ) over every session of the pull with `GET /v1/dark-pool/trades`, in contiguous, non-overlapping date spans of at most 31 days.
13. IF `GET /v1/dark-pool/trades` returns no prints for a configured ticker on a session, or the request fails after any retries, THEN THE Skylit_Client SHALL record in the coverage report the ticker, the session and the cause (no prints, or the last error code).
14. THE Strategy_Engine SHALL read scheduled economic events (CPI, NFP, FOMC and Operator-added events) from a calendar file in the Project that gives each event's type, release date and America/New_York release time, and states the first and last dates the file covers.
15. THE Strategy_Engine SHALL read exchange holidays and early-close times from a calendar file in the Project that states the first and last dates the file covers.
16. IF a calendar file is missing or fails to parse, has an entry with an invalid date or (for an event or early close) an invalid time, or does not cover every session of the run, THEN THE Strategy_Engine SHALL stop before the first Decision_Time with an error that names the file and the first invalid entry or uncovered session.

### Requirement 5: Point-in-time data (no look-ahead)

**User Story:** As the Operator, I want each backtest decision to use only data that existed at that moment, so that backtest results match what a live bot could have done.

#### Acceptance Criteria

1. WHEN the Strategy_Engine evaluates a Decision_Time, THE Strategy_Engine SHALL build Map_State, for each configured symbol and metric, from the Snapshot of the configured Heatmap_View with the latest `asOf` at or before that Decision_Time, using the `asOf` that Skylit returns with the Snapshot rather than the requested instant.
2. IF no Snapshot of the configured Heatmap_View for a configured symbol and metric has an `asOf` at or before the Decision_Time, THEN THE Strategy_Engine SHALL mark that symbol and metric unavailable in Map_State instead of using a Snapshot with a later `asOf`.
3. THE Strategy_Engine SHALL derive every value at a Decision_Time, including trailing medians, Tap counts, chart levels and the futures price, only from Snapshots, bars, VIX daily values and dark-pool prints whose Observation_Time is at or before that Decision_Time (for example, the 1-minute bar that opens at 09:30 is first usable at 09:31:00).
4. THE Level_Converter SHALL compute each offset or ratio from the close of the latest futures bar whose Observation_Time is at or before the Decision_Time and from the spot price in the source symbol's Map_State Snapshot.
5. THE Fill_Simulator SHALL fill an order only on bars that open at or after the order's placement time, where the placement time is the Decision_Time at which the Order_Planner issued the order.
6. WHEN the Order_Planner cancels a resting order or changes the order's price at a Decision_Time, THE Fill_Simulator SHALL apply the cancellation or the new price only to bars that open at or after that Decision_Time, so that a fill on an earlier bar stands.
7. WHERE the stale_map Gate is enabled, WHILE the Strategy_Engine runs in the Backtester, IF Snapshot_Age at a Decision_Time exceeds the configured backtest maximum (default 90 s), THEN THE Gate_Evaluator SHALL reject each Candidate_Setup evaluated at that Decision_Time with Rejection_Reason `stale_map`, whose measured value is the Snapshot_Age and whose threshold is the configured maximum.
8. THE Strategy_Engine SHALL evaluate every session-time rule (RTH, the 18:00 trading-day start, chart session windows, Gate time windows and the flatten time) in America/New_York local time with the UTC offset in effect on each date, so that 09:30 is 13:30 UTC under daylight time and 14:30 UTC under standard time, including on the trading day that starts at 18:00 on each daylight-saving transition date.
9. THE Strategy_Engine SHALL compute Node_Velocity for each strike in Map_State as 100 × (|v1| − |v0|) ÷ |v0|, in both the Backtester and the Live_Runner and in place of the live-only `velocityPct` field, where v1 is the strike's value in the Map_State Snapshot and v0 is the strike's value in the latest Snapshot of the same symbol, metric, Heatmap_View and session with `asOf` at or before the Decision_Time minus the configured velocity window (default 60 s).
10. IF no Snapshot of the same symbol, metric, Heatmap_View and session has an `asOf` at or before the Decision_Time minus the velocity window, or the strike is absent or has a value of 0 in that Snapshot, THEN THE Strategy_Engine SHALL mark the strike's Node_Velocity unavailable.
11. THE Backtester SHALL write identical decision-log entries for every Decision_Time at or before t in any two runs that use the same Strategy_Config and calendar files and whose inputs with an Observation_Time at or before t are identical, regardless of any input with a later Observation_Time (no-look-ahead property).

### Requirement 6: Node classification and lifecycle

**User Story:** As the Operator, I want nodes labeled by explicit numeric rules, so that King, Floor, Gatekeeper and Sloppy Seconds mean the same thing on every run.

#### Acceptance Criteria

1. WHEN the Node_Classifier receives a Snapshot with at least one strike whose absolute value is above 0, THE Node_Classifier SHALL mark as a Node each strike whose absolute value is at least the configured node fraction (default 20%, range 1–100%) of the King's absolute value in that Snapshot.
2. WHEN the Node_Classifier receives a Snapshot with at least one strike whose absolute value is above 0, THE Node_Classifier SHALL label exactly one strike King: the strike with the largest absolute value in that Snapshot.
3. IF two or more strikes tie for King, Floor or Ceiling in a Snapshot, THEN THE Node_Classifier SHALL give the label to the tied strike nearest the Snapshot's spot price, or to the lower strike when two tied strikes are equally near.
4. IF a Snapshot has no strikes or every strike's absolute value is 0, THEN THE Node_Classifier SHALL label no King, Node, Floor, Ceiling, Gatekeeper, Air_Pocket, Clear_Skies or Empty_Basement in that Snapshot, without stopping the run.
5. WHEN the Node_Classifier receives a Snapshot with at least one Node strictly below the Snapshot's spot price, THE Node_Classifier SHALL label as Floor the Node with the largest absolute value strictly below the spot price.
6. WHEN the Node_Classifier receives a Snapshot with at least one Node strictly above the Snapshot's spot price, THE Node_Classifier SHALL label as Ceiling the Node with the largest absolute value strictly above the spot price.
7. IF a Snapshot has no Node strictly below / above its spot price, THEN THE Node_Classifier SHALL label no Floor / no Ceiling in that Snapshot.
8. WHEN the Node_Classifier receives a Snapshot, THE Node_Classifier SHALL label as Gatekeeper each Node that lies strictly between the Snapshot's spot price and at least one Node on the same side with a larger absolute value, and whose absolute value is at least the configured Gatekeeper fraction (default 30%, range 1–99%) of that larger Node's absolute value.
9. WHEN the Node_Classifier receives a Snapshot, THE Node_Classifier SHALL label as an Air_Pocket the strike range strictly between each pair of strike-adjacent Nodes whose strikes differ by at least the configured minimum Air_Pocket width (default 0.5% of the Snapshot's spot price).
10. WHEN the Node_Classifier receives a Snapshot that has at least one Node, and no Node whose strike is above the spot price and within the configured lookout distance of it (default 1% of the spot price), THE Node_Classifier SHALL label the Snapshot Clear_Skies.
11. WHEN the Node_Classifier labels a Floor and no other Node has a strike below the Floor strike and within the configured lookout distance of it (default 1% of the Snapshot's spot price), THE Node_Classifier SHALL label that Floor Empty_Basement.
12. WHERE Node clustering is enabled, WHEN the Strategy_Engine evaluates a Decision_Time, THE Node_Classifier SHALL merge into one cluster each maximal run of a Map_State Snapshot's Nodes, ordered by converted level, in which every gap between consecutive levels is at most the configured cluster width (default 5 points for ES levels and 5 × NQ price ÷ ES price for NQ levels, at the Decision_Time), so that a Node with no neighbor within that width forms a cluster of one.
13. WHERE Node clustering is enabled, THE Node_Classifier SHALL rank each Snapshot's clusters by the summed absolute value of their Nodes, with rank 1 for the largest sum, without changing the King, Floor, Ceiling or Gatekeeper labels.
14. WHEN the high–low range of a 1-minute bar that opens within RTH overlaps a Node's Deflection_Band, using bars of the instrument the Level_Converter maps the Node's symbol to (ES or NQ) and the band from the Map_State at the bar's close, and that bar is the session's first RTH bar or follows a 1-minute bar that did not overlap the band, THE Node_Classifier SHALL add one Tap to the Node's session count (reset at each session's start) and weekly count (reset at the first session of each Monday–Friday week), identifying each Node by symbol, metric and strike.
15. WHEN the Strategy_Engine evaluates a Decision_Time, THE Node_Classifier SHALL give each Node in Map_State exactly one lifecycle state by the first rule that holds: Decaying, if the Node's absolute value is at least the configured decay fraction (default 20%, range 1–100%) below its highest absolute value since 09:30 that session; Delivered, if, after the Node's latest Tap that week ended (its last consecutive overlapping bar), a 1-minute RTH bar traded inside the Deflection_Band of another Node of the same symbol and metric whose band does not overlap this Node's band; Tested, if the Node's weekly Tap count is at least 1; otherwise Fresh.
16. WHEN a Snapshot whose `asOf` is after the close of a Tap's first bar, and at most the configured Sloppy_Seconds window (default 15 minutes, range 1–390 minutes) after it, shows the tapped strike's absolute value at least the configured Sloppy_Seconds fraction (default 10%, range 1–100%) below its value in the latest Snapshot at or before that close, THE Node_Classifier SHALL label the Node Sloppy_Seconds, in addition to its lifecycle state, from that `asOf` until the session ends.
17. WHILE the Map_State's Regime is not Vanna_Dominant, THE Node_Classifier SHALL label Dormant exactly those Nodes in the Map_State whose strike is more than the configured Dormant distance (default 3.5% of the Snapshot's spot price) from that spot price.
18. WHILE the Map_State's Regime is Vanna_Dominant, THE Node_Classifier SHALL label no Node in the Map_State Dormant.
19. THE Node_Classifier SHALL produce, for every Snapshot with at least one strike whose absolute value is above 0, exactly one King whose absolute value is at least the absolute value of every strike in that Snapshot (invariant property).
20. THE Node_Classifier SHALL produce, for every Snapshot, a Floor strike strictly below the spot price whenever a Floor exists and a Ceiling strike strictly above the spot price whenever a Ceiling exists (invariant property).
21. THE Node_Classifier SHALL produce, for every Snapshot, only Gatekeepers that lie strictly between the spot price and a Node on the same side with a larger absolute value (invariant property).
22. WHEN the Report_Generator writes a run report, THE Report_Generator SHALL report the King agreement rate as the share of the run's Snapshots carrying Skylit `nodeType` labels in which the strike Skylit labels King is the Node_Classifier's King, with the count of Snapshots compared and the count of Snapshots without `nodeType` labels, or "not available" when no Snapshot carries `nodeType` labels.
23. WHEN the Report_Generator writes a run report, THE Report_Generator SHALL report the Gatekeeper agreement rate as the number of strikes that both the Node_Classifier and Skylit label Gatekeeper divided by the number of strikes that at least one of them labels Gatekeeper, across the run's Snapshots carrying `nodeType` labels, with both counts, or "not available" when the second count is 0.

### Requirement 7: Regime and map grade

**User Story:** As the Operator, I want regime and map quality computed from numbers, so that "obvious side" and "F map" stop being judgment calls.

#### Acceptance Criteria

1. WHEN the Strategy_Engine builds a Map_State at a Decision_Time, THE Regime_Classifier SHALL assign exactly one Regime: the first of Vanna_Dominant, Structureless, Whipsaw, Negative_Gamma and Positive_Gamma (checked in that order) whose conditions hold. The only exception is a missing-input result returned in place of the Regime.
2. WHEN the normalized VEX magnitude is above 0 and at least the configured multiple (default 2.0) of the normalized GEX magnitude, THE Regime_Classifier SHALL assign Vanna_Dominant. Each raw magnitude is the largest absolute value among strikes at most the configured regime distance (default 1% of spot) from spot in the Regime_Symbol's vanna (VEX) or gamma (GEX) Snapshot, or 0 when no strike is that close. Each normalized magnitude is the raw magnitude divided by the median of the same quantity over all RTH Snapshots of the 20 most recent sessions before the current session that have Regime_Symbol Snapshots of that metric.
3. WHERE the VIX condition is enabled, IF the VIX value at the Decision_Time or the prior session's VIX close is unavailable, or the VIX value is less than the prior session's VIX close plus the configured percent (default 5%) of that close, THEN THE Regime_Classifier SHALL withhold Vanna_Dominant and check the remaining Regimes in order. The VIX value is the close of the latest 1-minute VIX bar that closed at or before the Decision_Time from the configured intraday VIX source. With no intraday VIX source, the VIX value is the session's VIX daily open, available from 09:31 onward.
4. WHEN no strike in the Regime_Symbol's gamma Snapshot has an absolute value of at least the configured minimum absolute value (a required Strategy_Config value with no default), THE Regime_Classifier SHALL assign Structureless.
5. WHEN the Regime_Symbol's gamma Snapshot has both a Floor and a Ceiling whose absolute values differ by at most the configured percent (default 15%) of the larger of the two, or the King of one Trinity symbol's gamma Snapshot lies above that symbol's spot while the King of another Trinity symbol's gamma Snapshot lies below that symbol's spot (counting only Trinity symbols present in the Map_State, with a King at spot on neither side), THE Regime_Classifier SHALL assign Whipsaw.
6. WHEN no earlier Regime applies and net GEX is below 0, THE Regime_Classifier SHALL assign Negative_Gamma. Net GEX is the sum of the signed values of all strikes at most the configured regime distance (default 1% of spot) from spot in the Regime_Symbol's gamma Snapshot.
7. WHEN no earlier Regime applies and net GEX is 0 or above, THE Regime_Classifier SHALL assign Positive_Gamma.
8. WHEN the Regime_Symbol's gamma Snapshot has fewer than 5 Nodes, 1 or 2 of those Nodes (counting the King) have an absolute value of at least the configured major fraction (default 50%) of the King's absolute value, and the larger of the Floor and Ceiling absolute values is above 0 and at least the configured ratio (default 1.5) times the smaller (a missing Floor or Ceiling counts as 0), THE Regime_Classifier SHALL grade the Map_State A_Plus_Map.
9. WHEN the Regime_Symbol's gamma Snapshot has 5 or more Nodes, or the larger of its Floor and Ceiling absolute values is either 0 or less than the configured ratio (default 1.5) times the smaller (a missing Floor or Ceiling counts as 0), THE Regime_Classifier SHALL grade the Map_State F_Map.
10. WHEN the Regime_Symbol's gamma Snapshot meets neither the A_Plus_Map conditions nor the F_Map conditions, THE Regime_Classifier SHALL grade the Map_State Neutral_Map.
11. IF the Map_State lacks the Regime_Symbol's gamma Snapshot, THEN THE Regime_Classifier SHALL return a missing-input result that names the Regime_Symbol and the gamma metric in place of both the Regime and the Map_Grade.
12. IF the Map_State lacks the Regime_Symbol's vanna Snapshot, or the trailing median behind either normalized magnitude covers fewer than 20 sessions or equals 0, THEN THE Regime_Classifier SHALL return a missing-input result that names the missing input in place of the Regime only.

### Requirement 8: Level conversion to ES and NQ

**User Story:** As the Operator, I want index and ETF nodes converted to ES/NQ prices with stated tolerances, so that entries rest at the right futures level.

#### Acceptance Criteria

1. WHEN the Strategy_Engine evaluates a Decision_Time, THE Level_Converter SHALL convert each strike of the SPX Snapshots in Map_State to an ES level, used for both ES and MES, as strike + (ES price - SPX spot) (offset method).
2. WHEN the Strategy_Engine evaluates a Decision_Time, THE Level_Converter SHALL convert each strike of the QQQ Snapshots in Map_State to an NQ level, used for both NQ and MNQ, as strike × (NQ price ÷ QQQ spot) (ratio method).
3. WHEN the Strategy_Engine evaluates a Decision_Time, THE Level_Converter SHALL convert each strike of the SPY Snapshots in Map_State to an ES level and each strike of the NDX and NDXP Snapshots to an NQ level with the method configured per symbol, offset as strike + (futures price - spot) or ratio as strike × (futures price ÷ spot) (default: ratio for SPY, NDX and NDXP).
4. THE Level_Converter SHALL pair each Snapshot's spot with the close of the latest 1-minute Bar_Source bar whose close time is at or before that Snapshot's `asOf`, from the contract the Bar_Source records for the session (ES or MES for ES levels, NQ or MNQ for NQ levels).
5. THE Level_Converter SHALL round each converted level to the nearest 0.25-point tick of ES, MES, NQ and MNQ, rounding a level exactly halfway between two ticks up to the higher tick.
6. THE Level_Converter SHALL set the Deflection_Band of each ES level to every futures price p with L - h ≤ p ≤ L + h, where L is the rounded ES level and h is the configured ES half-width (default 5 points, range 0.25 to 25 points).
7. THE Level_Converter SHALL set the Deflection_Band of each NQ level, from any source symbol, to every futures price p with L - h ≤ p ≤ L + h, where L is the rounded NQ level and h is the configured QQQ half-width (default $0.50, range $0.01 to $2.50) × (NQ price ÷ QQQ spot), unrounded, using the QQQ Snapshot with the same metric as L's source Snapshot.
8. THE Level_Converter SHALL return, for every strike greater than 0 converted to a rounded futures level L and back with the same offset (L - offset) or ratio (L ÷ ratio), a value within one futures tick of the original strike: 0.25 strike units for the offset method and 0.25 ÷ ratio strike units for the ratio method (round-trip property).
9. THE Level_Converter SHALL map any two strikes a < b of one Snapshot to rounded levels L(a) ≤ L(b) (monotonic property).
10. IF a spot or futures price that a conversion or a Deflection_Band needs is absent, at or below 0, or from a bar that closed more than the configured maximum price gap (default 120 s, range 60 to 600 s) before the paired Snapshot's `asOf`, THEN THE Level_Converter SHALL return a missing-price result that names the symbol or contract whose price is missing, in place of converted levels for the affected symbol and metric, without reusing an offset or ratio from an earlier Decision_Time.
11. IF the Level_Converter returns a missing-price result for the symbol and metric of a Candidate_Setup's source Node, THEN THE Gate_Evaluator SHALL reject that Candidate_Setup with Rejection_Reason `no_conversion_price`.

### Requirement 9: Chart structure features

**User Story:** As the Operator, I want the chart levels named in the skill and task computed from bars, so that "chart thesis" becomes a measurable confluence.

#### Acceptance Criteria

1. THE Chart_Feature_Builder SHALL compute every feature value it exposes at a Decision_Time only from Bar_Source bars that closed at or before that Decision_Time.
2. THE Chart_Feature_Builder SHALL compute, for each session and each configured instrument, the prior RTH high and low as the highest high and lowest low of the bars that open at or after 09:30 and close at or before 16:00 on the most recent earlier session date that has at least one such bar.
3. THE Chart_Feature_Builder SHALL compute, for each session and each configured instrument, the high and low of the overnight window (18:00 on the calendar day before the session date to 09:30 on the session date), the Asia window (19:00 on the calendar day before the session date to 02:00 on the session date) and the London window (02:00 to 08:00 on the session date), each from the bars that open at or after the window start and close at or before the window end, and SHALL expose each window's high and low only at Decision_Times at or after that window's end.
4. IF the prior RTH, overnight, Asia, London or IB30 window contains no bars for a session, or no earlier session with RTH bars exists in the loaded data, THEN THE Chart_Feature_Builder SHALL report that window's high and low as unavailable for that session and SHALL NOT substitute values from any other session or window.
5. WHEN a session's Decision_Time first reaches 10:00, THE Chart_Feature_Builder SHALL compute the IB30 high and low as the highest high and lowest low of the bars that open at or after 09:30 and close at or before 10:00, and SHALL expose them at every Decision_Time from 10:00 through 16:00 of that session.
6. WHILE a session's Decision_Time is before 10:00, THE Chart_Feature_Builder SHALL report that session's IB30 high and low as unavailable.
7. THE Chart_Feature_Builder SHALL mark a swing high on a bar whose high is strictly greater than the high of each of the N bars before it and each of the N bars after it, and a swing low on a bar whose low is strictly less than the low of each of those bars, where N is the configured pivot length for that bar timeframe (integer 1 to 20, default 3 bars on each side for 1-hour and 4-hour bars), and the 1-hour and 4-hour bar boundaries align to the 18:00 trading-day start, and each trading day's last 1-hour and 4-hour bar ends at the trading-day end (so the last 4-hour bar spans 3 hours on a 23-hour and 1 hour on a 25-hour daylight-saving trading day).
8. WHEN the Nth bar after a 1-hour or 4-hour swing bar closes, THE Chart_Feature_Builder SHALL confirm that swing and expose a swing high as a resistance level and a swing low as a support level, keeping only the configured number of most recent confirmed swing highs and swing lows per instrument and timeframe (integer 1 to 50, default 5 each).
9. WHEN a 1-minute or 5-minute bar closes strictly above the most recent swing high on that timeframe that was confirmed before that bar and has not yet produced a break of structure, THE Chart_Feature_Builder SHALL mark a bullish break of structure on that bar, where swings use the rule in criterion 7 with the configured break-of-structure pivot length for that timeframe (integer 1 to 20, default 3).
10. WHEN a 1-minute or 5-minute bar closes strictly below the most recent swing low on that timeframe that was confirmed before that bar and has not yet produced a break of structure, THE Chart_Feature_Builder SHALL mark a bearish break of structure on that bar, where swings use the rule in criterion 7 with the configured break-of-structure pivot length for that timeframe (integer 1 to 20, default 3).
11. WHEN a break of structure is marked, THE Chart_Feature_Builder SHALL create a BOS_Leg whose origin (level 1) is the lowest low for a bullish break, or the highest high for a bearish break, of the bars from the broken swing bar through the break bar, and whose terminal (level 0) is the break bar's high for a bullish break or low for a bearish break.
12. WHEN a BOS_Leg is created, THE Chart_Feature_Builder SHALL compute its standard-deviation Fibonacci levels at exactly the ratios 0, 1, -2, -2.25, -2.5, -3.5, -4 and -4.5, and no other ratios, with each level price equal to terminal + ratio × (origin − terminal), and SHALL expose them from the close of the break bar.
13. WHEN a new BOS_Leg is created while 2 BOS_Legs are already active for the same instrument, timeframe and direction, THE Chart_Feature_Builder SHALL drop the active BOS_Leg with the earliest break bar, so that at most 2 BOS_Legs stay active per instrument, timeframe and direction.
14. WHEN a bar on a BOS_Leg's timeframe that closes after the break bar opens strictly between the leg's 0 and 1 levels, its wick crosses the 0 level or the 1 level by at least the configured sweep ticks (integer 1 to 20, default 2) × the instrument tick size, and it closes strictly between the 0 and 1 levels, THE Chart_Feature_Builder SHALL drop that BOS_Leg and stop exposing its Fibonacci levels.
15. WHEN a bar on the configured candle timeframe (default 1 minute) closes, THE Chart_Feature_Builder SHALL assign that bar exactly one label: red when close < open, green when close > open, and doji when close = open.
16. WHEN a bar on the configured sweep timeframe (default 1 minute) opens strictly below an exposed Chart_Level, its high reaches at least that level plus the configured sweep ticks (integer 1 to 20, default 2) × the instrument tick size, and it closes strictly below that level, THE Chart_Feature_Builder SHALL flag a liquidity sweep above that Chart_Level on that bar and identify the Chart_Level.
17. WHEN a bar on the configured sweep timeframe (default 1 minute) opens strictly above an exposed Chart_Level, its low reaches at most that level minus the configured sweep ticks (integer 1 to 20, default 2) × the instrument tick size, and it closes strictly above that level, THE Chart_Feature_Builder SHALL flag a liquidity sweep below that Chart_Level on that bar and identify the Chart_Level.

### Requirement 10: Setup detection

**User Story:** As the Operator, I want each playbook pattern coded as a toggleable detector, so that I can test patterns one at a time.

#### Acceptance Criteria

1. THE Setup_Detector SHALL provide one detector for each Pattern (Gatekeeper_Fade, Floor_Ceiling_Bounce, Beach_Ball, Rug, Reverse_Rug, Whipsaw_Fade and Trend_Follow) and one detector for the Empty_Basement variant of Floor_Ceiling_Bounce. Each detector SHALL have an id, an enabled flag and numeric parameters in the Strategy_Config that set its source Node selection, its direction and its detection conditions.
2. THE Setup_Detector SHALL emit Floor_Ceiling_Bounce Candidate_Setups whose source Node is a Floor labeled Empty_Basement only from the Empty_Basement variant detector.
3. THE Setup_Detector SHALL emit Candidate_Setups only from detectors enabled in the Strategy_Config, so that a Strategy_Config with no detector enabled yields zero Candidate_Setups at every Decision_Time and no error.
4. THE Setup_Detector SHALL emit the same Candidate_Setups from each enabled detector for every combination of enabled flags on the other detectors (isolation property).
5. THE Setup_Detector SHALL take source Nodes for ES levels only from the configured ES source symbol (default SPX) and source Nodes for NQ levels only from the configured NQ source symbol (default QQQ).
6. WHERE a detector is enabled, WHEN a Decision_Time occurs at which the absolute difference between the Futures_Price and the converted level of a source Node that the detector selects is at most the configured arming distance (default 10 ES points for ES levels, and $1.00 times the NQ ÷ QQQ ratio at the Decision_Time for NQ levels), THE Setup_Detector SHALL emit one Candidate_Setup for that detector and source Node, in the direction the detector sets, on the configured instrument (default MES for ES levels and MNQ for NQ levels).
7. THE Setup_Detector SHALL set each Candidate_Setup's limit entry at the source Node's converted level moved by the detector's configured entry offset (default 0 ticks; a positive offset raises a long entry and lowers a short entry), capped at the Deflection_Band edge and rounded to the 0.25-point tick toward the converted level.
8. WHERE a detector's stop rule is one-Node-beyond (the default), THE Setup_Detector SHALL set the initial stop at the converted level of the nearest Node in the source Node's Snapshot beyond the detector's invalidation level (default: the source Node's converted level), below that level for a long and above it for a short.
9. WHERE a detector's stop rule is fixed-ticks, THE Setup_Detector SHALL set the initial stop the configured number of ticks (default 8) beyond the Deflection_Band edge, below the lower edge for a long and above the upper edge for a short, rounded to the 0.25-point tick away from the entry.
10. WHERE a detector's stop rule is one-Node-beyond, IF the source Node's Snapshot has no Node beyond the invalidation level within the configured lookout distance of that level, THEN THE Setup_Detector SHALL set the initial stop by the fixed-ticks rule.
11. THE Setup_Detector SHALL assign each Candidate_Setup a Setup_Key whose Tap sequence number equals 1 plus the number of the source Node's Taps in the session that ended before the Decision_Time.
12. THE Setup_Detector SHALL attach to each Candidate_Setup the detector id and the inputs used: the `asOf` of each Snapshot in Map_State, the source symbol's spot, the Futures_Price, the source Node's metric, strike and value, the converted level, the conversion offset or ratio, the Deflection_Band half-width, the Regime, the Map_Grade, the stop rule applied, and the value of each chart level that the detector's parameters reference.
13. THE Setup_Detector SHALL produce stop < entry < first target for every long Candidate_Setup and first target < entry < stop for every short Candidate_Setup, where the first target is the target price nearest to entry under the Exit_Mode that applies to the Candidate_Setup (invariant property).
14. IF a Candidate_Setup's computed stop, entry and first target break the order stop < entry < first target for a long or first target < entry < stop for a short, or the applicable Exit_Mode yields no target price (for example, Next_Node with no Node beyond entry in the trade direction), THEN THE Setup_Detector SHALL emit, in place of the Candidate_Setup, a Detection_Skip that names the Setup_Key, the Decision_Time and the failed condition.
15. THE Strategy_Engine SHALL produce Candidate_Setups, Detection_Skips, Gate results and orders that are equal field by field and in the same order for identical inputs (Snapshots, bars, calendar files and Decision_Times) and an identical Strategy_Config (determinism property).

### Requirement 11: Gate catalog and evaluation

**User Story:** As the Operator, I want each hard pass and confluence coded as a separate gate with an on/off switch, so that I can see which gates remove trades and which add edge.

#### Acceptance Criteria

1. THE Gate_Evaluator SHALL provide exactly these 27 Gates, each with its listed id, an enabled flag and the parameters that the Strategy_Config sets for it: stale_map, map_grade, midpoint, deflection_band, chart_confluence, stdev_fib_zone, dark_pool_confluence, trinity_agreement, candle_color, tap_count, third_gatekeeper_test, weekly_node_tests, sloppy_seconds, air_pocket_fade, min_reward_risk, opposition_inside_target, fomo_travel, open_shuffle, entry_cutoff, late_session_chase, news_window, vix_gap, regime_match, dormant_node, gatekeepers_on_path, node_growth_divergence and kill_switch_lockout.
2. WHEN the Setup_Detector emits a Candidate_Setup at a Decision_Time, THE Gate_Evaluator SHALL evaluate all 27 Gates for that Candidate_Setup at that Decision_Time in the configured Gate order (default: the order of criterion 1), including every Gate that follows a failed Gate.
3. WHEN the Gate_Evaluator evaluates a Gate for a Candidate_Setup, THE Gate_Evaluator SHALL record the Setup_Key, the Decision_Time, the Gate id, the result (`pass`, `fail` or `disabled`), the measured value and the threshold.
4. WHERE a Gate is disabled, THE Gate_Evaluator SHALL record the result `disabled` in place of `pass` or `fail`, with the Gate's measured value (`data_unavailable` for a missing input as criterion 11 defines), and exclude the Gate from the Grade.
5. THE Gate_Evaluator SHALL compute the min_reward_risk measured value, in price points before commissions and fees, as the position-weighted mean distance from entry to the target prices that the Order_Planner plans under the Exit_Mode that applies to the Candidate_Setup (for TP1_Partial_BE, the configured TP1 fraction at TP1 and the remainder at TP2), divided by the distance from entry to the initial stop.
6. IF the min_reward_risk measured value is below the configured minimum (default 3.0), THEN THE Gate_Evaluator SHALL fail min_reward_risk.
7. IF the Order_Planner plans no target price for a Candidate_Setup under the Exit_Mode that applies to it (for example, Trailing with no fixed target), THEN THE Gate_Evaluator SHALL fail min_reward_risk with the measured value `no_target`.
8. IF a Node in the source Node's Snapshot, other than the source Node, has an absolute value of at least the configured fraction (default 85%) of the source Node's absolute value and a converted level on the target side of entry (above entry for a long, below entry for a short) within the configured window (default 3.0 × the entry-to-stop distance, which is 3R, inclusive), THEN THE Gate_Evaluator SHALL fail opposition_inside_target.
9. IF fewer than the configured minimum (default 2 of 3) of the Trinity symbols for an ES or MES Candidate_Setup, or of the NQ_Sources for an NQ or MNQ Candidate_Setup, have a Node of the source Node's sign and metric whose level, converted by the Level_Converter to the Candidate_Setup's instrument, lies within the Deflection_Band of the source Node's converted level (edges inclusive), THEN THE Gate_Evaluator SHALL fail trinity_agreement.
10. WHERE the Empty_Basement exception is enabled (default: enabled), IF the Candidate_Setup is a long Floor_Ceiling_Bounce and the QQQ Floor and the SPY Floor in the source Node's metric are both Empty_Basement Floors whose converted levels lie within the Deflection_Band of the source Node's converted level (edges inclusive), THEN THE Gate_Evaluator SHALL pass trinity_agreement regardless of how many symbols agree.
11. IF an enabled Gate needs a Snapshot, bar or VIX value that does not exist at or before the Decision_Time (other than a price that the Level_Converter needs), or needs dark-pool prints that were not fetched for the session (for example, a session that the coverage report lists as missing dark-pool data), THEN THE Gate_Evaluator SHALL fail the Gate with a Rejection_Reason whose measured value is `data_unavailable`.
12. THE Gate_Evaluator SHALL assign exactly one Grade to each Candidate_Setup that it evaluates at a Decision_Time: A_Plus for a Candidate_Setup with no failing enabled Gate (including a Strategy_Config with every Gate disabled), Alert_2R for a Candidate_Setup whose only failing enabled Gate is min_reward_risk with a measured value of at least 2.0, and Pass for every other Candidate_Setup.

### Requirement 12: Order planning, exits and order lifecycle

**User Story:** As the Operator, I want targets, exits and resting-limit handling as explicit settings, so that I can measure how each choice changes win rate and expectancy.

#### Acceptance Criteria

1. THE Order_Planner SHALL apply exactly one Exit_Mode to each accepted Candidate_Setup: Fixed_R, Next_Node, TP1_Partial_BE, Opposition_Or_Fixed_R or Trailing, as set in the Strategy_Config.
2. WHERE per-Regime exit settings are configured, THE Order_Planner SHALL use the Exit_Mode and stop rule set for the Regime attached to the Candidate_Setup, and the global Exit_Mode and stop rule for any Regime that has no per-Regime setting.
3. THE Order_Planner SHALL compute each target price from the Map_State at the Candidate_Setup's Decision_Time and round the target to the 0.25-point tick toward entry, keeping the target at least 1 tick beyond entry.
4. WHERE Fixed_R is the active Exit_Mode, THE Order_Planner SHALL set the target beyond entry in the trade direction at the configured R multiple (default 3.0, range 0.5 to 10.0) times the entry-to-stop distance.
5. WHERE Next_Node is the active Exit_Mode, THE Order_Planner SHALL set the target at the converted level of the nearest Node, other than the source Node, beyond entry in the trade direction (above entry for a long, below entry for a short) in the source Node's Snapshot.
6. WHERE TP1_Partial_BE is the active Exit_Mode, THE Order_Planner SHALL set TP1 and TP2 by their configured rules, each either an R multiple computed as in Fixed_R or a Node, with a Node-based TP1 at the Node that Next_Node selects and a Node-based TP2 at the nearest Node beyond TP1.
7. WHERE TP1_Partial_BE is the active Exit_Mode, THE Order_Planner SHALL place the TP1 order for the configured fraction of the position's contracts (range 0.1 to 0.9), rounded down to whole contracts with a minimum of 1, and the TP2 order for the remaining contracts, so that a 1-contract position exits in full at TP1.
8. WHERE TP1_Partial_BE is the active Exit_Mode, WHEN the TP1 order fills and contracts remain open, THE Order_Planner SHALL move the stop for the remaining contracts to the Breakeven_Price.
9. WHERE Opposition_Or_Fixed_R is the active Exit_Mode, THE Order_Planner SHALL set the target at whichever is nearer to entry: the converted level of the nearest opposition Node (a Node other than the source Node, beyond entry in the trade direction, with at least the configured fraction of the source Node's absolute value, default 85%) or the configured R multiple target (default 3.0, range 0.5 to 10.0), and at the R multiple target when no opposition Node exists.
10. WHERE Trailing in node-to-node mode is the active Exit_Mode, WHEN a closed bar trades at or beyond the converted level of a Node beyond entry in the trade direction, THE Order_Planner SHALL move the stop to the converted level of the next Node back toward entry, or to the source Node's level when the reached Node is the first Node beyond entry.
11. WHERE Trailing in fixed-ticks mode is the active Exit_Mode, WHEN a bar closes after the entry fill, THE Order_Planner SHALL set the stop the configured distance (range 1 to 400 ticks) behind the most favorable price traded since the entry fill.
12. IF the active Exit_Mode needs a Node target (Next_Node, or TP1_Partial_BE with a Node-based TP1 or TP2) and no qualifying Node lies beyond entry or beyond TP1 in the trade direction, or a computed TP2 is not farther from entry than TP1, THEN THE Order_Planner SHALL place no order for the Candidate_Setup and record Rejection_Reason `no_target_node` or `tp2_not_beyond_tp1`.
13. WHERE the breakeven rule is enabled, WHEN a closed bar's most favorable price since the entry fill reaches the configured trigger distance from entry (default 1R, range 0.25R to 10R, where 1R is the initial entry-to-stop distance), THE Order_Planner SHALL move the stop to the Breakeven_Price.
14. THE Order_Planner SHALL never move an open position's stop against the trade: a long's stop never decreases, a short's stop never increases, and a stop update that would do either leaves the stop unchanged (invariant property).
15. WHEN the first Decision_Time at or after the session's Flatten_Time occurs, THE Order_Planner SHALL cancel every resting entry.
16. WHEN the first Decision_Time at or after the session's Flatten_Time occurs, THE Order_Planner SHALL close every open position at market.
17. WHILE the Decision_Time is at or after the session's Flatten_Time, THE Order_Planner SHALL place no new entry order for the rest of that session.
18. WHILE resting entries plus open positions across the configured instruments equal the configured maximum (default 1, range 1 to 10), counting a position with a TP2 remainder as one open position, THE Order_Planner SHALL place no new entry order and record Rejection_Reason `max_open` for each accepted Candidate_Setup it declines.
19. WHEN a Decision_Time shows an enabled Cancel_Trigger for a resting entry, THE Order_Planner SHALL cancel the entry at that Decision_Time and record the Setup_Key, the Decision_Time and every Cancel_Trigger that fired.
20. WHEN a Decision_Time's Map_State differs from the previous Decision_Time's Map_State and shows no enabled Cancel_Trigger for a resting entry, THE Order_Planner SHALL keep the resting entry working at its original entry, stop and target prices.
21. WHEN a Decision_Time shows a Cancel_Trigger that is enabled for open positions (an invalidation trigger), THE Order_Planner SHALL apply the action configured for that trigger (exit at market, move the stop to the Breakeven_Price, or hold), choose exit at market over breakeven and breakeven over hold when several triggers fire at the same Decision_Time, and record the Setup_Key, the triggers and the action taken.
22. IF the invalidation action to apply is move-to-breakeven and the latest closed bar's close is at or past the Breakeven_Price against the trade, THEN THE Order_Planner SHALL close the position at market in place of the stop move.
23. WHERE a resting-entry maximum age is configured (range 1 to 390 minutes), WHEN the first Decision_Time occurs at which a resting entry's time since placement reaches that age, THE Order_Planner SHALL cancel the entry and record the Setup_Key with the cause `max_age`.

### Requirement 13: Fill simulation and costs

**User Story:** As the Operator, I want conservative simulated fills with costs included, so that backtest results do not beat what live trading can do.

#### Acceptance Criteria

1. WHEN a bar trades through a resting limit order (entry or target) by at least the configured trade-through distance (default 1 tick, range 0 to 20 whole ticks), meaning the bar's low is at or below the limit price minus that distance for a buy limit or the bar's high is at or above the limit price plus that distance for a sell limit, THE Fill_Simulator SHALL fill the full order quantity at the limit price with no slippage, including when the bar opens past the limit price.
2. WHERE the trade-through distance is 0 ticks, THE Report_Generator SHALL label every report of the run "touch fills (optimistic)".
3. WHEN a bar reaches a stop order (initial, breakeven or trailing), meaning the bar's high is at or above a buy stop price or the bar's low is at or below a sell stop price, THE Fill_Simulator SHALL fill the full order quantity at the stop price, or at the bar's open if the bar opens past the stop price, moved against the order by the configured slippage (default 1 tick, range 0 to 20 whole ticks): higher for a buy and lower for a sell.
4. WHEN a market order is placed, THE Fill_Simulator SHALL fill the full order quantity at the open of the first bar that opens at or after the placement time, moved against the order by the configured slippage: higher for a buy and lower for a sell.
5. WHEN a limit entry fills during a bar, THE Fill_Simulator SHALL check the trade's stop order from the fill bar onward and every target from the next bar onward.
6. IF a single bar meets the fill conditions of both the stop order and a target of an open trade, THEN THE Fill_Simulator SHALL fill only the stop order, for all open contracts of the trade.
7. WHEN an order fills, THE Fill_Simulator SHALL charge the configured commission plus the configured exchange fee for the filled instrument, per contract per side, times the contracts in that fill (including each partial exit), so that a round trip of N contracts costs 2 × N × (commission + exchange fee).
8. IF the commission or exchange fee setting for an instrument that the Strategy_Config trades is missing, or is outside $0.00 to $25.00 per contract per side, THEN THE Config_Loader SHALL reject the Strategy_Config before the first Decision_Time with an error that names the setting and the instrument.
9. THE Fill_Simulator SHALL apply these contract values: MES $5 per point, MNQ $2 per point, ES $50 per point, NQ $20 per point, and a 0.25-point tick for all four (tick values $1.25, $0.50, $12.50 and $5.00).
10. WHEN a trade closes, THE Fill_Simulator SHALL compute the trade's net P&L as the sum over its exit fills of (exit fill price - entry fill price) for a long, or (entry fill price - exit fill price) for a short, × point value × contracts in that exit fill, minus all commissions and fees charged on the trade's fills (accounting invariant).
11. WHEN a trade closes, THE Fill_Simulator SHALL compute the trade's R_Multiple as net P&L ÷ R, with R taken from the entry fill price, the initial stop price and the contracts at entry, so that a trade that exits in full at the initial stop price with $0.00 commission and fees has an R_Multiple of exactly -1.
12. IF one or more 1-minute bars are missing during RTH while a trade is open, THEN THE Fill_Simulator SHALL apply the fill rules to the next available bar and flag the trade in the trade list with the number of missing bars.

### Requirement 14: Position sizing

**User Story:** As the Operator, I want the playbook's sizing rules as settings, so that risk per trade matches the account plan.

#### Acceptance Criteria

1. WHERE the Strategy_Config selects fixed-contract sizing (the default sizing mode), THE Position_Sizer SHALL set each Candidate_Setup's base contracts to the configured count for the Candidate_Setup's instrument (default 5 MES or 3 MNQ; a whole number of at least 1).
2. WHERE the Strategy_Config selects fixed-dollar-risk sizing, THE Position_Sizer SHALL set each Candidate_Setup's base contracts to floor(risk dollars ÷ (stop distance × point value)), where risk dollars is the configured amount (default $375; greater than $0), stop distance is |entry - initial stop| of the Candidate_Setup in points, point value is the instrument's Fill_Simulator contract value ($5 for MES, $2 for MNQ), and commissions, fees and slippage are excluded.
3. IF fixed-dollar-risk sizing yields 0 base contracts, or the remaining Micro_Equivalent capacity is less than 1 contract of the Candidate_Setup's instrument, THEN THE Position_Sizer SHALL reject the Candidate_Setup with Rejection_Reason `size_zero` that names the step (base size or Micro_Equivalent cap) that produced 0 contracts.
4. THE Position_Sizer SHALL limit each Candidate_Setup's contracts to the remaining Micro_Equivalent capacity: the configured Micro_Equivalent limit (default 50; a whole number of at least 1) minus the Micro_Equivalents of the Strategy_Engine's open positions and resting entry orders, counting 1 ES or NQ contract as 10 Micro_Equivalents and 1 MES or MNQ contract as 1, rounded down to whole contracts of the Candidate_Setup's instrument.
5. WHEN the Position_Sizer sizes a Candidate_Setup and the filled trades of the immediately preceding session (Shadow_Trades excluded; a session without filled trades counts as $0 and 0 R) total a net P&L of at least the configured dollar threshold (default $1,200; greater than $0) or R_Multiples summing to at least the configured R threshold (default 3; greater than 0), THE Position_Sizer SHALL set contracts to the lesser of the base contracts and the configured reduced size for the instrument (default 3 MES or 2 MNQ; a whole number of at least 1).
6. WHERE Trinity size-down is enabled, WHEN the Position_Sizer sizes a Candidate_Setup whose trinity_agreement measured value is exactly 2 of the 3 Trinity symbols (2 of the 3 NQ_Sources for an MNQ Candidate_Setup), THE Position_Sizer SHALL multiply contracts by the configured fraction (default 0.5; greater than 0 and at most 1), rounded down, with a minimum of 1.
7. WHERE VIX-gap sizing is enabled, WHEN the Position_Sizer sizes a Candidate_Setup in a session whose VIX gap, computed from the Bar_Source as (VIX daily open - VIX prior close) ÷ VIX prior close, is at least the configured percent (default 15%; greater than 0%), THE Position_Sizer SHALL multiply contracts by 0.5, rounded down, with a minimum of 1.
8. THE Position_Sizer SHALL compute each Candidate_Setup's contracts in this order, each step taking the previous step's result, a disabled or untriggered step leaving contracts unchanged, and the sequence stopping at the first step that yields 0 contracts: base contracts, big-win reduced size, Trinity size-down, VIX-gap halving, Micro_Equivalent cap.
9. WHERE Trinity size-down or VIX-gap sizing is enabled, IF an enabled rule's input is unavailable at the Decision_Time (the trinity_agreement measured value, or the session's VIX daily open or VIX prior close), THEN THE Position_Sizer SHALL reject the Candidate_Setup with Rejection_Reason `data_unavailable` that names the missing input.

### Requirement 15: Topstep 50K account rules

**User Story:** As the Operator, I want the 50K Combine rules simulated, so that backtests show whether a strategy passes the Combine and not only whether the strategy makes money.

#### Acceptance Criteria

1. THE Account_Simulator SHALL read each account rule value from the Strategy_Config and SHALL use these defaults when the Strategy_Config does not set a value: starting balance $50,000.00, profit target $3,000.00, Maximum Loss Limit $2,000.00, Daily Loss Limit $1,000.00, consistency target 55%, position cap 50 Micro_Equivalents, Flat_Deadline 16:10 (3:10 PM CT), and early-close offset 15 minutes.
2. THE Account_Simulator SHALL treat each of the profit target, Maximum Loss Limit, Daily Loss Limit, consistency target and position cap rules as enabled unless the Strategy_Config disables that rule.
3. WHERE an account rule is disabled in the Strategy_Config, THE Account_Simulator SHALL skip every check and action of that rule and SHALL label every Combine_Attempt result of the run with the ids of the disabled rules.
4. IF an account rule value in the Strategy_Config is outside its valid range (each dollar amount from $0.01 to $10,000,000.00, Maximum Loss Limit less than the starting balance, consistency target greater than 0% and at most 100%, position cap an integer from 1 to 1,000, early-close offset an integer from 0 to 60 minutes), THEN THE Account_Simulator SHALL refuse to start the run, start no Combine_Attempt, and report the rule id, the configured value and the valid range.
5. IF the position cap is enabled and a submitted order that opens or adds to a position would raise the total Micro_Equivalents of open contracts plus working entry-order contracts across all instruments, counted without regard to direction, above the position cap, THEN THE Account_Simulator SHALL reject that order, leave open positions and working orders unchanged, and record a rejection that names the position cap rule, the resulting total and the cap.
6. WHEN a Combine_Attempt starts, THE Account_Simulator SHALL set the balance to the starting balance and the MLL_Floor to the starting balance minus the Maximum Loss Limit (default $48,000.00), where balance means the starting balance plus the Combine_Attempt's cumulative net realized P&L.
7. WHEN a trading day ends, THE Account_Simulator SHALL set the MLL_Floor to the lesser of the starting balance and the greater of the current MLL_Floor and the end-of-day balance minus the Maximum Loss Limit, and SHALL change the MLL_Floor at no other time (with defaults, an end-of-day balance of $51,200.00 sets the MLL_Floor to $49,200.00, and an end-of-day balance of $52,000.00 or more holds the MLL_Floor at $50,000.00 for the rest of the Combine_Attempt).
8. IF, on any bar, the balance plus the unrealized P&L of all open positions, each valued at the worst price of its instrument's bar for that interval (the low for a long, the high for a short), is at or below the MLL_Floor, THEN THE Account_Simulator SHALL close every open position, cancel every working order, and set the post-liquidation balance to the MLL_Floor, or to the balance valued at that bar's open prices when that balance is already at or below the MLL_Floor.
9. WHEN a Maximum Loss Limit liquidation completes, THE Account_Simulator SHALL end the Combine_Attempt as failed, record the breach timestamp, the final balance and the number of trading days elapsed (counted from the Combine_Attempt's first trading day through the current trading day, inclusive), and reject every later order in that Combine_Attempt.
10. IF, on any bar, the trading day's net realized P&L plus the unrealized P&L of all open positions, each valued at the worst price of its instrument's bar for that interval, is at or below minus the Daily Loss Limit (default -$1,000.00), THEN THE Account_Simulator SHALL close every open position, cancel every working order, and set the trading day's net P&L to minus the Daily Loss Limit, or to the value at that bar's open prices when that value is already at or below minus the Daily Loss Limit.
11. WHILE trading is blocked by the Daily Loss Limit, THE Account_Simulator SHALL reject every entry order until the next trading day starts at 18:00 and SHALL keep the Combine_Attempt active.
12. IF a single bar meets both the Maximum Loss Limit condition in criterion 8 and the Daily Loss Limit condition in criterion 10, THEN THE Account_Simulator SHALL apply the Maximum Loss Limit liquidation and end the Combine_Attempt as failed.
13. WHERE the consistency target is enabled, WHEN a trading day ends, THE Account_Simulator SHALL set the current profit target to the greater of the configured profit target and the best trading day's net realized profit (the largest net realized P&L of any completed trading day in the Combine_Attempt) divided by the consistency target share, rounded up to the next $0.01 (with defaults, a best trading day of $1,650.00 or less leaves the current profit target at $3,000.00, and a best trading day of $2,000.00 raises it to $3,636.37).
14. THE Account_Simulator SHALL assign every fill and P&L change to the trading day that starts at 18:00 on the calendar day before the session date and ends at that session's Flat_Deadline.
15. THE Account_Simulator SHALL set each session's Flat_Deadline to the configured flat time (default 16:10), or, for a session date that the exchange calendar file of Requirement 4 lists as an early close, to the early-close offset (default 15 minutes) before that session's close.
16. WHEN a session's Flat_Deadline is reached, THE Account_Simulator SHALL close each open position at the open price of the first bar that starts at or after the Flat_Deadline, cancel every working order, and reject every entry order until the next trading day starts at 18:00.
17. WHERE the profit target rule is enabled, WHEN a trading day ends with an end-of-day balance at or above the starting balance plus the current profit target after the criterion 13 adjustment for that trading day, THE Account_Simulator SHALL record a Combine_Pass with the pass date and the number of trading days elapsed, end the Combine_Attempt, reject every later order in that Combine_Attempt, and record a Combine_Pass at no other time.
18. IF the backtest data range ends while a Combine_Attempt has neither passed nor failed, THEN THE Account_Simulator SHALL record the Combine_Attempt as incomplete with the final balance, the MLL_Floor, the current profit target and the number of trading days elapsed.
19. THE Project SHALL document each default account rule value with the value, the source (initially the third-party summary dated July 2026) and the date checked in YYYY-MM-DD form.
20. THE Project SHALL state, next to the documented rule values, that the Operator verifies every account rule value against the Topstep help center before enabling Combine Order_Mode.

### Requirement 16: Playbook kill switches

**User Story:** As the Operator, I want the bootcamp kill switches enforced in code, so that the bot stops after bad days the way the playbook says.

#### Acceptance Criteria

1. WHERE the max_trades Kill_Switch is enabled, WHEN an entry order's first fill brings the session's count of filled entry orders to the configured maximum (default 3, minimum 1), THE Risk_Manager SHALL start a Lockout for the rest of the session.
2. WHERE the max_losers Kill_Switch is enabled, WHEN a Losing_Trade closes and brings the session's Losing_Trade count to the configured limit (default 2, minimum 1), THE Risk_Manager SHALL start a Lockout for the rest of the session.
3. WHERE the consecutive_losers Kill_Switch is enabled, WHEN a Losing_Trade closes and brings the Loss_Streak to the configured limit (default 3, minimum 1), THE Risk_Manager SHALL start a Lockout for the rest of the session and all of the next weekday session that the exchange-holiday calendar does not list as a holiday.
4. WHERE the red_day Kill_Switch is enabled, WHEN a trade's last exit fills and leaves the session's net realized P&L (after commissions and fees) below the configured threshold (default $0), THE Risk_Manager SHALL start a Lockout for the rest of the session.
5. WHERE the daily_profit_cap Kill_Switch is enabled, WHEN an exit fill, including a partial exit, brings the session's net realized P&L to or above the configured cap (default $1,200, above $0), THE Risk_Manager SHALL start a Lockout for the rest of the session.
6. WHERE the internal daily loss stop is enabled, WHEN, at the close of a 1-minute bar, the session's net realized P&L plus the unrealized P&L of open positions marked at that bar's worst price is at or below minus the configured multiple (default 2, above 0) of the Position_Sizer's fixed-dollar risk setting (default $375, so minus $750 by default), THE Risk_Manager SHALL close each open position in the configured instruments at market and then start a Lockout for the rest of the session.
7. THE Risk_Manager SHALL produce identical Lockouts (starting rule, start time and last session covered) for the same fills, bars and Strategy_Config in the Backtester, in an uninterrupted Live_Runner run, and in a Live_Runner run restarted at any point from readable recorded state (parity property).
8. WHILE a Lockout is active, THE Risk_Manager SHALL withhold each new entry order and cancel each resting entry order, and leave the exit orders of open positions working under the active Exit_Mode.
9. WHILE a Lockout is active, THE Gate_Evaluator SHALL fail kill_switch_lockout for each Candidate_Setup, with a Rejection_Reason that names the rule that started each active Lockout and the last session that Lockout covers.
10. IF the Risk_Manager state recorded before a Live_Runner restart cannot be read, THEN THE Live_Runner SHALL block new entries until the Operator clears the block and report the failed restore in the first Finding_Card.

### Requirement 17: Strategy configuration

**User Story:** As the Operator, I want every rule and parameter in one editable file, so that I can change the strategy without editing code or prose.

#### Acceptance Criteria

1. WHEN the Backtester, the Experiment_Runner or the Live_Runner starts a run with a Strategy_Config file that parses and matches the Config_Schema, THE Config_Loader SHALL return a Strategy_Config with an id, an enabled flag and parameter values for each Pattern in Requirement 10, each Gate in Requirement 11, each Exit_Mode in Requirement 12, each account rule in Requirement 15 and each Kill_Switch in Requirement 16, and a value for the Order_Mode and every other setting that Requirements 5 to 16 describe as configured or enabled.
2. WHEN a Strategy_Config file omits a key that has a Config_Schema default, THE Config_Loader SHALL set that key to the Config_Schema default (Paper for the Order_Mode).
3. IF a Strategy_Config file is missing, cannot be read or does not parse in the Strategy_Config file format, THEN THE Config_Loader SHALL reject the file before any Decision_Time is evaluated, with an error that names the file path and, for a parse failure, the line of the first syntax error.
4. IF a Strategy_Config file holds a key or id that the Config_Schema does not define, repeats a Pattern, Gate, Exit_Mode, Kill_Switch or account rule id, lacks a key that the Config_Schema marks required, or holds a value that does not match the type, allowed values or range that the Config_Schema sets for its key, THEN THE Config_Loader SHALL reject the file before any Decision_Time is evaluated, with an error that lists every failing key path, the failure (unknown key, duplicate id, missing required key, wrong type or out of range) and, for wrong-type and out-of-range failures, the expected type, allowed values or range.
5. THE Config_Schema SHALL define no key that holds an API key, a credential, a webhook URL, an account name or an account id.
6. IF the min_reward_risk Gate is enabled and an enabled Fixed_R or Opposition_Or_Fixed_R Exit_Mode, including one set in a per-Regime exit setting, has an R multiple less than the min_reward_risk threshold, THEN THE Config_Loader SHALL return the Strategy_Config with one contradiction warning per such R multiple, naming both key paths and both values.
7. THE Config_Printer SHALL serialize a Strategy_Config to the file format that the Config_Loader parses, writing every key, including each key set from a Config_Schema default.
8. WHEN the Config_Loader parses a file that the Config_Printer wrote from a Strategy_Config that matches the Config_Schema, THE Config_Loader SHALL return, with no error, a Strategy_Config with the same ids, enabled flags and parameter values as the original, each numeric value exactly equal (round-trip property).
9. THE Project SHALL include the Playbook_Baseline Strategy_Config as a file that the Config_Loader loads with no error into a Strategy_Config with Paper Order_Mode.
10. THE Project SHALL include a traceability table with one row per rule in the Skill_Documents (each item of a "Hard passes" section counts as one rule), where each row gives the source Skill_Document and section heading and either the Strategy_Config key path and Playbook_Baseline value that codify the rule or the mark "not codified" with a reason, and no row copies an account name, account id or credential value.
11. THE Project SHALL list in the traceability table each pair of codified rules that cannot both be followed as written for at least one Candidate_Setup, with the key paths and values that the Playbook_Baseline uses for the pair, including at least Positive_Gamma Next_Node targets against the 3:1 min_reward_risk threshold and Positive_Gamma Next_Node targets against opposition_inside_target within 3R.

### Requirement 18: Backtest runs and decision log

**User Story:** As the Operator, I want reproducible backtests with a full decision log, so that I can audit any day and trust that reruns agree.

#### Acceptance Criteria

1. WHEN the Operator starts a backtest with a Strategy_Config and an inclusive date range, THE Backtester SHALL evaluate the Strategy_Engine at every Decision_Time of each session in the range. Decision_Times start at 09:30:00, repeat at whole multiples of the configured Decision_Cadence (default 60 s), and stop before 16:00:00. At the default cadence this gives 390 Decision_Times per session.
2. IF the Operator starts a backtest with a date range whose end date is before its start date, or with a range that contains no session, THEN THE Backtester SHALL show an error message indicating the invalid date range, evaluate no Decision_Time and write no output file.
3. IF the Data_Cache holds no Snapshot for a configured symbol and metric, or no bars for a configured instrument, on a session in the date range, THEN THE Backtester SHALL skip that session, record the session date and the missing data type (Snapshots or bars) in the Run_Manifest, and continue with the next session.
4. WHEN a backtest run ends, either by completing or by an error that stops the run, THE Backtester SHALL write one Run_Manifest. The Run_Manifest records the Strategy_Config hash, the data range, the code version, the random seed, the output files, the evaluated session dates, and an end status of completed or aborted.
5. WHEN the Operator runs two backtests with the same Run_Manifest inputs (Strategy_Config hash, data range, code version and random seed) against unchanged Data_Cache contents for that range, THE Backtester SHALL produce byte-identical trade lists, Gate_Funnels and decision logs (determinism property).
6. WHILE a backtest runs, THE Backtester SHALL write exactly one decision-log entry for each evaluated Decision_Time, in Decision_Time order. This includes Decision_Times with no Candidate_Setup and Decision_Times at which Map_State is missing a Snapshot.
7. THE Backtester SHALL include the following in each decision-log entry:
   - the session date and the Decision_Time
   - the `asOf` of each Snapshot in Map_State, plus a missing marker for each configured symbol and metric that has no Snapshot at or before the Decision_Time
   - the Futures_Price of each configured instrument
   - the King, Floor and Ceiling of each Snapshot
   - the Regime and the Map_Grade
   - each Candidate_Setup with its Setup_Key, plus the pass or fail result of every enabled Gate, the Rejection_Reason of each failed Gate, and the Grade
   - the orders the Order_Planner submitted, modified or cancelled at that Decision_Time
   - the fills and exits that occurred after the previous Decision_Time and at or before this one
8. WHEN the Operator runs a cadence comparison over a date range, THE Experiment_Runner SHALL run one Strategy_Config once at each configured cadence (default 5 s, 60 s and 300 s) over the same set of sessions. That set is every session in the range whose stored Snapshot interval and stored bar interval in the Data_Cache are each no longer than the shortest compared cadence.
9. IF no session in the cadence comparison's date range meets the stored-interval condition of criterion 8, THEN THE Experiment_Runner SHALL run no backtest and report an error indicating that no session supports the shortest compared cadence.
10. WHEN a cadence comparison finishes, THE Report_Generator SHALL report the following:
    - for each compared cadence: the count of distinct Setup_Keys among Candidate_Setups, the count of entry fills, the mean trades per session, and the expectancy (mean R_Multiple per trade, reported as not available when the cadence has zero trades)
    - for every pair of compared cadences: the difference in each of those four metrics, computed as the shorter cadence's value minus the longer cadence's value
    - the included and excluded session dates
11. WHEN the Report_Generator writes a backtest report, THE Report_Generator SHALL report two counts for each session whose stored bar interval is shorter than 300 s: the total Tap count, and the count of Taps whose start and end both fall strictly between two consecutive 300 s Decision_Times (09:30:00 plus whole multiples of 300 s). Both counts are measured on the stored bars regardless of the run's Decision_Cadence. Every other session is marked as not measurable.
12. WHEN the Operator runs a 250-session backtest of the Playbook_Baseline at 60 s cadence with every session already in the Data_Cache, THE Backtester SHALL write the Run_Manifest within 10 minutes of wall-clock time from the start of the run on the Operator's machine.

### Requirement 19: Gate funnel, shadow trades and ablation

**User Story:** As the Operator, I want to see which gates reject setups and whether the rejected setups would have won, so that I keep gates that add edge and drop gates that only remove trades.

#### Acceptance Criteria

1. THE Report_Generator SHALL count each Setup_Key in a run exactly once in the Gate_Funnel, with exactly one final status of filled, cancelled, rejected or untapped, so that the four status counts sum to the number of distinct Setup_Keys in the run.
2. WHEN a Setup_Key's first Tap occurs, THE Gate_Evaluator SHALL evaluate every enabled Gate for the Candidate_Setup, including Gates after the first failing Gate in the Strategy_Config Gate order, and record one Rejection_Reason per failing Gate.
3. IF one or more enabled Gates failed at a Setup_Key's first Tap, THEN THE Report_Generator SHALL assign the Setup_Key the final status rejected and list the ids of every failing Gate.
4. WHEN the Report_Generator assigns a final status to a Setup_Key whose enabled Gates all passed at its first Tap, THE Report_Generator SHALL assign filled if any part of the entry order filled, and otherwise assign cancelled with the trigger that cancelled or blocked the entry order (for example a reshuffle, a Kill_Switch id or the end of RTH).
5. IF a Setup_Key has no Tap before 16:00 on its session date, THEN THE Report_Generator SHALL assign the final status untapped and exclude the Setup_Key from the per-Gate counts and the co-rejection matrix.
6. THE Report_Generator SHALL report for each enabled Gate, over Setup_Keys with final status rejected, the number of Setup_Keys failing the Gate, the number where the Gate was the only failing Gate, and the number where the Gate was the first failing Gate in the Strategy_Config Gate order, so that the first-failing counts across all Gates sum to the rejected status count.
7. THE Report_Generator SHALL report a pairwise co-rejection matrix over all enabled Gates in the Strategy_Config Gate order, in which the cell for Gates A and B holds the number of rejected Setup_Keys that failed both A and B, the diagonal cell for Gate A equals Gate A's failing count from criterion 6, and the cell for A and B equals the cell for B and A.
8. WHEN one or more enabled Gates fail for a Candidate_Setup at its Setup_Key's first Tap, THE Backtester SHALL simulate exactly one Shadow_Trade for that Setup_Key, using the entry, stop and targets of the Candidate_Setup at that Tap, the Exit_Mode set in the Strategy_Config, and the Fill_Simulator rules used for accepted trades.
9. THE Backtester SHALL exclude every Shadow_Trade from the account balance, the Account_Simulator, Kill_Switch state, Combine_Pass evaluation and accepted-trade metrics.
10. THE Report_Generator SHALL report for each enabled Gate the count, win rate and mean R_Multiple of filled Shadow_Trades whose only failing Gate is that Gate, and the count of such Shadow_Trades whose entry did not fill, beside the count, win rate and mean R_Multiple of trades from Setup_Keys with final status filled in the same run, where a win is a filled trade with R_Multiple greater than 0 and win rate and mean R_Multiple are reported as not available when the filled count is 0.
11. IF a Gate's filled only-rejected Shadow_Trades number at least the configured minimum sample (an integer of at least 1, default 30) and their mean R_Multiple is greater than or equal to the mean R_Multiple of the run's filled accepted trades, THEN THE Report_Generator SHALL flag the Gate "no measured edge" in the Gate_Funnel without changing the Strategy_Config.
12. IF a Gate's filled only-rejected Shadow_Trades number fewer than the configured minimum sample, or the run has no filled accepted trades, THEN THE Report_Generator SHALL mark the Gate "insufficient sample" with its filled only-rejected Shadow_Trade count, in place of the "no measured edge" flag.
13. WHEN the Operator starts an ablation for a base Strategy_Config, THE Experiment_Runner SHALL run the base Strategy_Config and one variant per Gate enabled in the base, each variant identical to the base except that one Gate is disabled, for N + 1 runs where N is the number of Gates enabled in the base.
14. WHEN an ablation finishes, THE Experiment_Runner SHALL report for each variant the base value, the variant value and the change (variant minus base) of trades per day, win rate, expectancy, profit factor, maximum drawdown and Combine_Pass probability, reporting a change as not available where the base or variant value is undefined (for example profit factor with no losing trades).
15. THE Experiment_Runner SHALL evaluate every configuration in one comparison on the same list of session dates with the same Monte_Carlo_Simulator random seed, recording the session dates and each configuration's Strategy_Config hash in the comparison report.
16. IF any configuration in an ablation fails to complete, THEN THE Experiment_Runner SHALL mark that configuration failed with an error description, complete the remaining configurations, and report as not available every change value that depends on the failed configuration.
17. THE Report_Generator SHALL list for each session the number of distinct Setup_Keys and the three Gate ids that appear most often in the Rejection_Reasons of that session's rejected Setup_Keys (one count per failing Gate per Setup_Key), each with its count, breaking ties by Strategy_Config Gate order and listing fewer than three when fewer than three Gates failed in the session.

### Requirement 20: Metrics and the win-rate vs reward:risk frontier

**User Story:** As the Operator, I want win rate shown next to expectancy, drawdown and pass odds across exit choices, so that I choose a trade-off from data rather than from an 80% claim.

#### Acceptance Criteria

1. WHEN a Backtester run completes, THE Report_Generator SHALL report the trade count, trades per day (trade count divided by the run's session count, including sessions with no trade), the share of sessions with at least one trade as a percentage of the session count, and the longest losing streak (the largest number of consecutive trades, ordered by exit time, with net P&L below $0). For these counts, one trade is one accepted Candidate_Setup whose entry filled, tracked from the entry fill until the position is flat. Partial exits count once, and Shadow_Trades are excluded.
2. WHEN a Backtester run completes, THE Report_Generator SHALL report the average win in R (mean R_Multiple of trades with net P&L above $0), the average loss in R (mean R_Multiple of trades with net P&L below $0, so trades at exactly $0 fall in neither), expectancy in R (mean R_Multiple of all trades), expectancy in dollars (mean net P&L of all trades), and profit factor (gross profit divided by the absolute value of gross loss, in dollars). Every P&L figure SHALL be net of the configured commissions, fees and slippage.
3. WHEN a Backtester run completes, THE Report_Generator SHALL report maximum drawdown in dollars as the largest peak-to-trough decline of cumulative net P&L, sampled at each trade exit and starting from $0 at the run start. It SHALL report maximum drawdown in R as the same measure computed on cumulative R_Multiple.
4. WHEN a Backtester run completes, THE Report_Generator SHALL report three win rates side by side, each as a percentage of the trade count: (a) trades with net P&L above $0; (b) trades with net P&L at or above minus the configured win-rate scratch tolerance (default 0.1 × the trade's R), which counts breakeven as a win; and (c) trades whose price reached the first target (TP1) before the trade closed, as determined by the Fill_Simulator.
5. WHEN a Backtester run completes, THE Report_Generator SHALL report the break-even win rate as |average loss| ÷ (average win + |average loss|), using the after-cost average win and average loss in R from criterion 2, next to win rate (a) from criterion 4.
6. WHEN a Backtester run completes, THE Report_Generator SHALL measure maximum adverse excursion and maximum favorable excursion per trade, each as a non-negative R value from the entry fill to the final exit, using bar highs and lows at the run's data resolution. For each measure it SHALL report the minimum, 25th percentile, median, 75th percentile, 90th percentile and maximum across trades, and SHALL write the per-trade values to the run's CSV or JSON output.
7. WHEN the Operator runs a frontier sweep, THE Experiment_Runner SHALL evaluate one configuration for each combination of a configured Exit_Mode and a swept R multiple (default 0.5, 1, 1.5, 2 and 3), and one configuration for each configured Exit_Mode that takes no R-multiple parameter. Each configuration SHALL set the min_reward_risk threshold to the lower of its swept R multiple and the base Strategy_Config threshold; a configuration without an R multiple uses the base threshold. Every configuration SHALL keep every other Strategy_Config parameter, the data range, the Fill_Simulator and cost settings, and the random seed unchanged.
8. IF a frontier sweep definition lists no Exit_Mode, lists no R multiple, lists an R multiple that is not above 0 or is above 10, names a ranking objective other than Combine_Pass probability, expectancy or profit factor, or sets a bootstrap resample count outside 1,000 to 100,000, THEN THE Experiment_Runner SHALL reject the sweep before evaluating any configuration, report an error that names each invalid value, and write no frontier results.
9. WHEN a frontier sweep completes, THE Report_Generator SHALL write a frontier table as Markdown, plus a CSV or JSON file with the same rows. Each row is one configuration and SHALL show the Exit_Mode, the R multiple, the applied min_reward_risk threshold, trade count, Primary_Win_Rate, expectancy in R, profit factor, maximum drawdown in dollars, trades per day and Combine_Pass probability.
10. WHEN a frontier sweep completes, THE Report_Generator SHALL mark as members of the Pareto set the configurations for which no other configuration is at least equal on expectancy in R, Primary_Win_Rate and Combine_Pass probability while being higher on at least one of the three. Configurations with any of the three values not applicable SHALL be excluded from the Pareto set and labeled as excluded.
11. WHEN a frontier sweep completes, THE Report_Generator SHALL list each configuration whose Primary_Win_Rate is at or above the configured reference win rate (default 80%), together with that configuration's expectancy in R, profit factor and Combine_Pass probability. If no configuration is at or above the reference, it SHALL state that no configuration met the reference.
12. WHEN a Backtester run completes, THE Report_Generator SHALL report 95% percentile bootstrap confidence intervals for Primary_Win_Rate and for expectancy in R. The resamples SHALL draw the run's trades with replacement, the resample count SHALL be configurable (default 5,000; allowed 1,000 to 100,000), and the configured seed SHALL be recorded in the Run_Manifest, so that a rerun with the same seed, data range and Strategy_Config gives identical interval bounds.
13. IF a run has fewer than the configured minimum sample (default 30 trades), THEN THE Report_Generator SHALL label that run's win rates, expectancy and confidence intervals as low-sample next to each value, in every report and frontier table row where those values appear.
14. WHEN an Experiment_Runner experiment with two or more configurations completes, THE Experiment_Runner SHALL rank the configurations in descending order of the configured objective (default Combine_Pass probability; alternatives expectancy in R or profit factor). Ties SHALL be broken by the other two objectives in the order Combine_Pass probability, expectancy in R, profit factor, and then by the configurations' order in the experiment definition. Configurations whose objective value is not applicable SHALL be placed last.
15. WHEN the Report_Generator writes a Markdown report, THE Report_Generator SHALL include a header section before the first metric table that states: the date range (first and last session dates), the session count, the trade count (or, for a multi-configuration report, a pointer to the per-row trade counts), the data resolution (bar interval and Decision_Cadence), the Fill_Simulator and cost settings (slippage, commissions and fees), whether any Holdout_Period session is included, and a statement that the figures are measured backtest results and not a forecast of future results.
16. IF a run has zero trades, or a metric's denominator is zero (no winning trades, no losing trades, gross loss of $0, or a run whose Exit_Mode sets no first target for win rate (c)), THEN THE Report_Generator SHALL show each affected metric as not applicable rather than as 0 or infinity, report trade count and trades per day as 0 where the run has zero trades, and finish the report with all other metrics.

### Requirement 21: Combine pass probability

**User Story:** As the Operator, I want an estimate of how often a strategy passes the 50K Combine, so that I compare strategies by the outcome I care about.

#### Acceptance Criteria

1. WHEN the Operator requests a pass estimate for a completed Backtester run, THE Monte_Carlo_Simulator SHALL build the configured number of paths (default 10,000; allowed range 1,000 to 1,000,000) by drawing whole sessions from that run uniformly at random with replacement, one drawn session per trading day, with sessions without trades included in the draw pool.
2. WHEN the Monte_Carlo_Simulator builds a path, THE Monte_Carlo_Simulator SHALL stop drawing sessions at the first trading day on which the path reaches Combine_Pass or breaches the Maximum Loss Limit, or after the configured maximum trading days (default 60; allowed range 1 to 250), whichever comes first.
3. THE Monte_Carlo_Simulator SHALL start each path from the Account_Simulator's initial Combine balance and loss limits as set in the run's Strategy_Config.
4. THE Monte_Carlo_Simulator SHALL apply the Account_Simulator rules in the run's Strategy_Config to each path, checking the Maximum Loss Limit and the Daily Loss Limit on each trading day by applying the resampled session's intraday equity low, measured from that session's starting equity, to the path balance at the start of that trading day.
5. IF a resampled session's intraday equity low breaches the Maximum Loss Limit, THEN THE Monte_Carlo_Simulator SHALL record the path as a failure on that trading day, even when the session's closing balance reaches the profit target.
6. WHEN the Monte_Carlo_Simulator completes an estimate, THE Monte_Carlo_Simulator SHALL report, each as a fraction from 0 to 1: pass probability (passing paths ÷ total paths), failure probability (failing paths ÷ total paths), and the unresolved share (paths with neither Combine_Pass nor a Maximum Loss Limit breach after the configured maximum trading days ÷ total paths).
7. WHEN the Monte_Carlo_Simulator completes an estimate, THE Monte_Carlo_Simulator SHALL report the median and the nearest-rank 90th-percentile trading days to pass, computed over passing paths only. If no path reaches Combine_Pass, it SHALL report both values as not available, with no number.
8. THE Monte_Carlo_Simulator SHALL report pass, failure and unresolved path counts that sum to the number of paths built, so the three shares sum to 1 (invariant property).
9. THE Monte_Carlo_Simulator SHALL produce identical reported values for the same run, Strategy_Config, path count, maximum trading days and seed, using the seed recorded in the Run_Manifest (determinism property).
10. IF the Operator supplies no seed, THEN THE Monte_Carlo_Simulator SHALL generate a seed, use that seed for the estimate, and record it in the Run_Manifest.
11. IF the run has fewer sessions than the configured minimum (default 40; allowed range 1 to 1,000), THEN THE Monte_Carlo_Simulator SHALL label the estimate "insufficient sample" in every report output that shows the estimate, and SHALL still report the metrics in criteria 6 and 7.
12. IF the requested run has no Run_Manifest or contains zero sessions, or the path count, maximum trading days or minimum sessions setting is outside its allowed range, THEN THE Monte_Carlo_Simulator SHALL reject the request with an error message naming the failed check and SHALL produce no estimate.

### Requirement 22: Overfitting controls

**User Story:** As the Operator, I want out-of-sample checks built in, so that a strategy that only fits past data does not reach the account.

#### Acceptance Criteria

1. THE Experiment_Runner SHALL set the Holdout_Period to the most recent sessions with data in the Data_Cache. The number of sessions SHALL equal the configured holdout fraction (default 0.20, allowed 0.05 to 0.50) of all sessions with data, rounded up to a whole session.
2. THE Experiment_Runner SHALL exclude every Holdout_Period session from sweeps, ablations, cadence comparisons and walk-forward tests. This includes Holdout_Period sessions that fall inside a date range the Operator requests.
3. THE Experiment_Runner SHALL record the first and last session dates of the Holdout_Period in the Run_Manifest of every sweep, ablation, cadence comparison, walk-forward test and holdout evaluation.
4. WHEN the Operator requests a holdout evaluation for a named Strategy_Config, THE Experiment_Runner SHALL run that Strategy_Config, with no parameter changes, on the Holdout_Period sessions only.
5. WHEN a holdout evaluation completes, THE Experiment_Runner SHALL append one entry to the Holdout_Log with the Strategy_Config hash, the evaluation time, the first and last session dates of the Holdout_Period, the trade count, expectancy, win rate and Combine_Pass probability.
6. IF the named Strategy_Config is missing, the Strategy_Config fails Config_Loader validation, or the Holdout_Log cannot be read, THEN THE Experiment_Runner SHALL reject the holdout evaluation with an error naming the cause, run no backtest, and leave the Holdout_Log unchanged.
7. IF the Operator requests a holdout evaluation and the Holdout_Log holds one or more entries whose Holdout_Period shares at least one session with the current Holdout_Period, THEN THE Experiment_Runner SHALL warn the Operator, before the backtest starts, that the Holdout_Period is no longer unseen, give the count of those entries, and then run the evaluation.
8. WHEN the Experiment_Runner issues the warning in criterion 7, THE Experiment_Runner SHALL record the warning and the count of overlapping Holdout_Log entries in the Run_Manifest of that holdout evaluation.
9. WHEN the Operator runs a walk-forward test, THE Experiment_Runner SHALL split the sessions outside the Holdout_Period, oldest first, into window pairs. Each pair is a training window (default 60 sessions, allowed 10 to 500) followed directly by a test window (default 20 sessions, allowed 5 to 250). Each next pair starts one test window length later, so test windows never overlap. A final test window shorter than the configured length SHALL be dropped.
10. WHEN the Experiment_Runner evaluates a walk-forward training window, THE Experiment_Runner SHALL select the configuration with the highest value of the configured objective (default: expectancy, the mean R_Multiple per trade), set separately from the Requirement 20 ranking objective, because a single training window is too short for a stable Combine_Pass estimate, considering only configurations not labeled "insufficient sample". Ties SHALL go to the configuration listed first in the experiment definition.
11. WHEN the Experiment_Runner selects a configuration on a training window, THE Experiment_Runner SHALL run the selected configuration, with no parameter changes, on the test window that follows it.
12. IF every configuration on a training window is labeled "insufficient sample", THEN THE Experiment_Runner SHALL record that window pair as "no selection" and count the sessions of its test window as zero-trade sessions in the concatenated test results.
13. IF fewer sessions exist outside the Holdout_Period than one training window plus one test window (80 sessions with the defaults), THEN THE Experiment_Runner SHALL reject the walk-forward test with an error stating the number of sessions available and required, and write no walk-forward results.
14. WHEN a walk-forward test completes, THE Report_Generator SHALL report the concatenated test-window results beside the in-sample results (the trades of each selected configuration on its own training window, pooled across window pairs). The report SHALL cover trade count, expectancy, win rate and Combine_Pass probability, plus the count of "no selection" window pairs.
15. WHEN a sweep, ablation, cadence comparison or walk-forward test completes, THE Report_Generator SHALL report the number of distinct configurations evaluated, including configurations labeled "insufficient sample". For a walk-forward test, the report SHALL also give the number of configurations evaluated on each training window.
16. IF a configuration has fewer accepted trades than the configured minimum (default 30, allowed 1 to 1,000) over the sessions being ranked, THEN THE Experiment_Runner SHALL label the configuration "insufficient sample" and exclude it from ranking and from walk-forward selection. The sessions being ranked are the experiment's sessions, or a single training window in a walk-forward test. Shadow_Trades SHALL NOT count toward the minimum.

### Requirement 23: Live and paper runner

**User Story:** As the Operator, I want the bot to run the backtested engine on live data every few seconds, so that the bot catches taps that a 5-minute LLM cycle misses.

#### Acceptance Criteria

1. WHEN the Live_Runner starts without a configured Order_Mode, THE Live_Runner SHALL run in Paper Order_Mode.
2. WHILE the configured run window is open (default 09:00 inclusive to 16:00 exclusive), THE Live_Runner SHALL refresh Map_State for all configured symbols and both metrics (`gamma` and `vanna`) once per configured interval (default 5 s, minimum 5 s), using multi-symbol requests instead of one request per symbol.
3. WHERE stream mode is configured, THE Live_Runner SHALL update Map_State from Snapshots received on `GET /v1/stream` in place of sending polling refresh requests for Map_State.
4. IF the stream connection closes, or no stream event arrives for 60 s while the run window is open, THEN THE Live_Runner SHALL reconnect to `GET /v1/stream` with the id of the last event received, retrying a failed reconnect no more than once every 5 s until the stream resumes or the run window closes.
5. THE Live_Runner SHALL make every Candidate_Setup, Grade and order decision by calling the same Strategy_Engine code path and code version as the Backtester.
6. WHEN a new Map_State or a new closed bar arrives, THE Live_Runner SHALL complete the Strategy_Engine evaluation and emit the resulting Candidate_Setups, Grades and order decisions within 1 s of receiving that input, excluding Broker_Adapter response time.
7. IF Snapshot_Age exceeds the configured live maximum (default 30 s), THEN THE Live_Runner SHALL, within 1 s, cancel every resting entry order and block new entry orders until Snapshot_Age is at or below the maximum again, leaving the stop and exit orders of open positions in place.
8. WHEN the Live_Runner receives any live input, including Snapshots, bars and broker events, THE Live_Runner SHALL record the input and its receipt time for replay, with API key values excluded from the recording.
9. WHEN the Backtester replays a recorded live session with the same Strategy_Config and code version, THE Backtester SHALL produce, at every recorded Decision_Time, the same Candidate_Setups (Setup_Key, entry, stop and targets), Grades, Rejection_Reasons and order decisions that the Live_Runner logged (parity property).
10. WHILE the run window is open and Paper or Practice Order_Mode is active, THE Live_Runner SHALL fetch `GET /v1/gex/levels` once per configured interval (default 60 s) and log, for each returned level, whether the Node_Classifier assigns the same label at the same strike, using the fetched levels in no Strategy_Engine decision.
11. WHILE a Broker_Adapter request is pending, THE Live_Runner SHALL continue to compute the Map_Grade and emit the Finding_Card for each new Map_State within the 1 s limit of criterion 6.
12. WHEN the Live_Runner starts, THE Live_Runner SHALL log the active Order_Mode, the Strategy_Config hash, the code version and the projected Skylit credits per session, computed from the refresh mode (polling or stream), the configured interval, the run window, the symbol set, both metrics and the `GET /v1/gex/levels` interval.
13. IF the configured refresh interval is below 5 s, THEN THE Live_Runner SHALL refuse to start and log an error that names the interval setting and the 5 s minimum.
14. IF a Map_State refresh request returns an error or no response within 5 s, THEN THE Live_Runner SHALL log the failure, keep the prior Map_State, and send the next request at the next scheduled interval.
15. WHILE Paper Order_Mode is active, THE Live_Runner SHALL route every order to the Paper_Broker and send no order submit, modify or cancel request to the Broker_Adapter.

### Requirement 24: Broker integration and order safety

**User Story:** As the Operator, I want orders on the 50K Combine only after an explicit opt-in, with hard limits in code, so that a bug cannot overtrade the account.

#### Acceptance Criteria

1. WHEN the Live_Runner starts, THE Live_Runner SHALL select Combine Order_Mode only if all three Combine_Opt_In conditions hold: the Strategy_Config selects Combine Order_Mode, the `COMBINE_ACCOUNT_ID` environment variable is set and exactly equals the id of the account the Broker_Adapter resolves, and the start command includes the explicit live-orders flag.
2. THE Live_Runner SHALL keep the Order_Mode selected at start unchanged until the Live_Runner stops, including when the Strategy_Config file changes during the run.
3. IF the Strategy_Config selects Combine Order_Mode and any other Combine_Opt_In condition fails, THEN THE Live_Runner SHALL run in Paper Order_Mode and name every failed condition in the first Finding_Card after start.
4. WHILE the Live_Runner runs in Combine Order_Mode, THE Broker_Adapter SHALL send orders only to the account named by the `COMBINE_ACCOUNT_ID` environment variable.
5. WHILE the Live_Runner runs in Practice Order_Mode, THE Broker_Adapter SHALL send orders only to the account named by the `PRACTICE_ACCOUNT_ID` environment variable.
6. IF the Strategy_Config selects Practice Order_Mode and `PRACTICE_ACCOUNT_ID` is unset, matches no account the Broker_Adapter resolves, or equals `COMBINE_ACCOUNT_ID`, THEN THE Live_Runner SHALL run in Paper Order_Mode and name the failed condition in the first Finding_Card after start.
7. THE Broker_Adapter SHALL read ProjectX credentials and account ids only from environment variables, and never from the Strategy_Config or command-line arguments.
8. WHEN the Order_Planner sends an entry order in Practice or Combine Order_Mode, THE Broker_Adapter SHALL submit the entry with an attached stop-loss order and target order whose prices equal the stop and first target set by the Order_Planner and whose quantity equals the entry quantity.
9. IF the broker rejects a bracket, or no working stop-loss order covers the full open quantity within the configured stop-confirmation time after a fill (default 5 s, range 1–30 s), THEN THE Live_Runner SHALL send a market order that closes the full open position in that instrument.
10. IF a bracket rejection or a missing working stop-loss order occurs as defined in criterion 9, THEN THE Live_Runner SHALL block new entries on all configured instruments, send a Finding_Card that names the instrument and the failure, and keep the block in force, across Live_Runner restarts, until the Operator issues the clear command.
11. WHEN the Live_Runner starts or a new session begins, THE Broker_Adapter SHALL resolve and log the front-month contract id of each configured instrument (default MES and MNQ) before the Live_Runner submits any order for that instrument.
12. IF a resolved contract id differs from the configured expected id, or the Broker_Adapter cannot resolve a contract id, THEN THE Live_Runner SHALL block orders for that instrument for the rest of the session and send a Finding_Card that names the instrument, the expected id, and the resolved id or the resolution failure.
13. THE Broker_Adapter SHALL tag each order with a client id that no other order the Project has sent to the same account carries.
14. WHEN the Broker_Adapter resubmits an order, THE Broker_Adapter SHALL first look up working and filled orders by that order's client id and send the resubmission only if the lookup finds no working or filled order with that client id.
15. IF the client-id lookup fails or gets no response within the configured lookup timeout (default 5 s, range 1–30 s), THEN THE Broker_Adapter SHALL skip the resubmission for that cycle.
16. WHILE the Live_Runner runs in Practice or Combine Order_Mode, WHEN the Live_Runner starts, THE Live_Runner SHALL compare the Broker_State of each configured instrument with the positions and working orders the Strategy_Engine expects, before submitting any order.
17. WHILE the Live_Runner runs in Practice or Combine Order_Mode, WHEN each Decision_Time occurs, THE Live_Runner SHALL repeat the comparison defined in criterion 16.
18. IF Broker_State and the Strategy_Engine's expected positions and working orders differ in any compared field, including a working order on a configured instrument that carries no Project client id, THEN THE Live_Runner SHALL block new entries on all configured instruments, send a Finding_Card that names each difference, and keep the block until the Operator issues the clear command.
19. WHERE an instrument is on the ignored-instrument list (default MGC and SIL), THE Live_Runner SHALL list positions in that instrument in the Finding_Card and leave the instrument out of the comparison defined in criterion 16.
20. WHERE an instrument is on the ignored-instrument list, THE Live_Runner SHALL NOT submit, modify or cancel orders in that instrument.
21. IF the Broker_Adapter gets no successful response from ProjectX for the configured outage time (default 30 s, range 5–300 s), THEN THE Live_Runner SHALL block new entries, send a Finding_Card that names the outage, and leave broker-side stop-loss and target orders in place without cancel or modify requests.
22. WHEN the Broker_Adapter gets a successful ProjectX response after an outage block from criterion 21, THE Live_Runner SHALL run the comparison defined in criterion 16 and lift the outage block only if that comparison finds no difference.
23. THE Risk_Manager SHALL check each order against the position cap and the internal daily loss stop set in the Strategy_Config before the order reaches the Broker_Adapter.
24. IF an order would open or increase a position beyond the position cap, or the internal daily loss stop has been reached, THEN THE Risk_Manager SHALL withhold the order from the Broker_Adapter and report the failed check, its measured value and its limit in the Finding_Card.
25. THE Risk_Manager SHALL pass orders that only reduce or close a position, including stop-loss, target and market-close orders, without applying the position cap or the internal daily loss stop.
26. WHEN the Operator creates the halt file or issues the halt command, THE Live_Runner SHALL, within one Decision_Cadence, cancel every resting entry order and block new entries, leaving stop-loss and target orders on open positions in place.
27. WHILE the halt file exists, or a halt command has not been cleared by the Operator's clear command, THE Live_Runner SHALL keep new entries blocked, including across Live_Runner restarts.
28. THE Project README SHALL state that the Operator confirms the current TopstepX API terms, including the device and VPN rules, before enabling Combine Order_Mode.

### Requirement 25: Finding cards and optional LLM narrator

**User Story:** As the Operator, I want a short finding card built by code and optionally reworded by an LLM, so that I see what the bot sees while the LLM makes no trade decisions.

#### Acceptance Criteria

1. WHEN a Decision_Time completes in the Live_Runner, THE Live_Runner SHALL build a Finding_Card containing the map fields: the Decision_Time, the Order_Mode, the `asOf` of each Snapshot in Map_State, spot per configured symbol, the King per configured symbol and metric, each King flip since the previous sent Finding_Card (with the old and new King strike), the Floor and Ceiling per configured symbol with their converted ES or NQ levels, the Regime, the Map_Grade, and the Trinity status (for each of SPX, SPY and QQQ, whether that symbol's King is above or below its spot).
2. WHEN a Decision_Time completes in the Live_Runner, THE Live_Runner SHALL include in the Finding_Card up to 5 Candidate_Setups from that Decision_Time, ordered A_Plus first, then Alert_2R, then Pass. Each listed Candidate_Setup SHALL show its Setup_Key, its Grade and up to 3 Rejection_Reasons in Strategy_Config Gate order. The Finding_Card SHALL also show the count of Candidate_Setups that were not listed.
3. WHEN a Decision_Time completes in the Live_Runner, THE Live_Runner SHALL include in the Finding_Card the following order and position fields: the working orders, the orders armed or cancelled since the previous sent Finding_Card, and the open position status per instrument (instrument, direction, contracts, entry price, stop price and unrealized R_Multiple, or "flat" when there is no position). For each instrument it SHALL also include the next watch level, meaning the nearest converted Node level above the current futures price and the nearest converted Node level below it.
4. IF Map_State has no Snapshot for a configured symbol and metric, or a Decision_Time produces no Candidate_Setups, THEN THE Live_Runner SHALL keep the affected Finding_Card fields and mark them as unavailable or none, and SHALL NOT leave them out.
5. WHILE the Live_Runner is running, WHEN the configured interval (default 5 minutes, allowed range 1 to 60 minutes) has passed since the last sent Finding_Card, THE Live_Runner SHALL send the Finding_Card of the latest completed Decision_Time to the Notifier.
6. WHEN a Decision_Time completes and since the previous Decision_Time there has been a Grade change (the Grade of a Setup_Key differs from its Grade at the previous Decision_Time, with an absent Setup_Key counted as Pass), an order event (an order armed, modified, cancelled, filled, partially filled or rejected by the Paper_Broker or the Broker_Adapter) or a King flip (the King strike of any configured symbol and metric differs from its King strike at the previous Decision_Time), THE Live_Runner SHALL send that Decision_Time's Finding_Card to the Notifier. It SHALL send exactly one Finding_Card for that Decision_Time, however many triggers occurred and whether or not the configured interval also ran out at that Decision_Time.
7. WHEN the configured premarket time (default 09:00, set earlier than 09:30) arrives on a session date, THE Live_Runner SHALL send an opening Finding_Card to the Notifier with the Regime, the Map_Grade and a Map_State summary. The summary SHALL list, for each configured symbol and metric, the Snapshot `asOf`, King, Floor, Ceiling, Gatekeepers and Air_Pockets, with their converted ES or NQ levels.
8. WHERE 2R alerts are enabled, WHEN a Setup_Key receives the Alert_2R Grade for the first time, THE Live_Runner SHALL send one alert to the Notifier for that Setup_Key. The alert SHALL contain the instrument, direction, entry, stop, targets, reward:risk and Pattern.
9. WHERE 2R alerts are enabled, THE Live_Runner SHALL place no order for an Alert_2R Candidate_Setup.
10. WHERE the Narrator is enabled (default disabled), WHEN a Finding_Card is ready to send, THE Live_Runner SHALL pass only that Finding_Card's fields to the Narrator. The Narrator input SHALL contain no configured secret value (Skylit API keys, broker credentials or the Notifier webhook URL) and no broker account id.
11. WHERE the Narrator is enabled, WHEN the Narrator returns non-empty prose within the configured timeout, THE Live_Runner SHALL send the prose and the unchanged code-built Finding_Card to the Notifier as one message.
12. IF the Narrator returns an error, returns empty prose, returns prose longer than the configured maximum (default 1,500 characters), or does not respond within the configured timeout (default 10 s, allowed range 1 to 60 s), THEN THE Live_Runner SHALL send the code-built Finding_Card without prose, with a note that narration was unavailable, and continue the Decision_Time cycle.
13. THE Live_Runner SHALL start each Decision_Time on its Decision_Cadence. It SHALL finish that Decision_Time's order placement, modification and cancellation without waiting for a pending Narrator response or a pending Notifier delivery.
14. IF the Notifier fails to deliver a Finding_Card or an alert, THEN THE Live_Runner SHALL record the failure through the Log_Writer and continue the Decision_Time cycle. It SHALL place, keep and cancel orders exactly as it would if delivery had succeeded.
15. THE Live_Runner SHALL produce the same sequence of order submissions, modifications and cancellations, with the same instrument, side, order type, price, contracts and Decision_Time, for the same Strategy_Config, Map_State, bars and broker events, whether the Narrator is disabled, enabled, failing or timing out (independence property).

### Requirement 26: Skill and task documents

**User Story:** As the Operator, I want the skill files fixed now and revised from backtest data later, so that the published skill parses cleanly and every remaining rule has measured support.

#### Acceptance Criteria

1. WHERE a Skill_Document or a Revised_Draft carries YAML front matter, THE Skill_Document or Revised_Draft SHALL begin with exactly one front-matter block: a `---` line on line 1, YAML lines, and a closing `---` line. No second `---`-delimited block SHALL follow it with only blank lines in between.
2. WHERE a Skill_Document or a Revised_Draft carries YAML front matter, THE Skill_Document or Revised_Draft SHALL hold a front-matter block that parses as a YAML mapping without a parse error.
3. WHERE a Skill_Document or a Revised_Draft carries YAML front matter, THE Skill_Document or Revised_Draft SHALL hold a front-matter mapping that contains a `name` key and a `description` key, each set to a non-empty string.
4. THE `current_working_SKILL_100226.md` Skill_Document SHALL hold, in its single front-matter block, every distinct key that appears in the two stacked front-matter blocks the single block replaces.
5. THE `SKILL.md` Skill_Document SHALL read "Fresh → tested → delivered → decaying" in place of "Fresh ? tested ? delivered ? decaying", and SHALL show `→` in place of each `?` between the steps of the "How to speak a read" chain.
6. THE `SKILL.md` Skill_Document SHALL read "≥3:1" in place of "?3:1".
7. THE Skill_Documents and Revised_Drafts SHALL be UTF-8 encoded and SHALL contain no U+FFFD replacement character.
8. THE Project SHALL apply the fixes in criteria 1–7 without changing any Skill_Document text outside the front-matter block and the replaced `?` characters.
9. WHEN the Backtester completes the Holdout_Period evaluation of the Strategy_Config chosen by the Operator, THE Project SHALL add one Revised_Draft of the skill and one Revised_Draft of the task to `skill-fine-tune/` as new files, and SHALL leave every existing Skill_Document unchanged.
10. THE Revised_Drafts SHALL list every Pattern, Gate, Exit_Mode and Kill_Switch that the Playbook_Baseline sets, and SHALL mark each one with exactly one label:
    - Kept: enabled in the chosen Strategy_Config with the Playbook_Baseline parameter values.
    - Changed: enabled in the chosen Strategy_Config but either disabled in the Playbook_Baseline or set to at least one different parameter value. The old and new values are shown.
    - Removed: disabled in the chosen Strategy_Config.
11. THE Revised_Drafts SHALL state two figures for each rule marked Kept, Changed or Removed: the change in mean trades per session and the change in expectancy (mean R_Multiple per trade). Each figure SHALL equal the chosen Strategy_Config's value minus the value of a comparison Strategy_Config that differs only in that rule. The comparison disables the rule for Kept, and sets the rule to its Playbook_Baseline setting for Changed or Removed.
12. IF a Skill_Document rule is not codified by any Pattern, Gate, Exit_Mode or Kill_Switch in the Playbook_Baseline, THEN THE Revised_Drafts SHALL mark that rule Unmeasured, state the reason, and state no effect figure for that rule.
13. THE Revised_Drafts SHALL instruct the Narrator to restate Finding_Card data as prose, and to add no level, price, Grade or performance figure that the Finding_Card does not contain.
14. THE Revised_Drafts SHALL state that the Strategy_Engine makes every entry and exit decision. The Revised_Drafts SHALL contain no instruction for the Narrator to take, skip, size or exit a trade, to place, modify or cancel an order, or to change the Order_Mode.
15. THE Revised_Drafts SHALL show the following with every performance figure (win rate, expectancy, R_Multiple, trades per session, P&L, drawdown or Combine_Pass probability):
    - the trade count and the session count
    - the first and last session dates, labeled pre-holdout or Holdout_Period
    - the Strategy_Config hash from the Run_Manifest of the run that produced the figure
    - for a Combine_Pass probability, the number of Monte_Carlo_Simulator paths
16. THE Revised_Drafts SHALL contain no win-rate target, no forecast of a future win rate, R_Multiple, P&L or Combine_Pass, and no statement that a measured figure will recur in Practice or Combine trading.
17. THE Skill_Documents and Revised_Drafts SHALL contain no API key value and no other secret value configured for the Secret_Scanner.
18. IF a Revised_Draft lacks a recorded Operator approval of its current version, THEN THE Project SHALL keep that Revised_Draft's content out of `SKILL.md`.
