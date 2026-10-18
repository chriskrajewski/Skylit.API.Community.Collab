# fse console

The web console for the engine: every `fse` command and research CLI, runner control,
plugin configuration, configs, secrets, runs and analytics. The operator never needs a
terminal.

- `api/`: FastAPI package `fse_ui`. It imports the engine in-process (schemas, validators,
  metrics) and runs commands as child processes (argv lists, never a shell).
- `web/`: Vite + React 18 + TypeScript. `web/src/ds/` is a copy of `design_system/`
  (see its README for the two console changes).

## Run it locally

1. Create the API venv (once):

   ```sh
   cd ui/api
   uv venv --python 3.14 .venv
   uv pip install --python .venv/bin/python --require-hashes -r ../../requirements.lock
   uv pip install --python .venv/bin/python --no-deps -e ../..
   uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
   uv pip install --python .venv/bin/python --no-deps -e .
   ```

2. Build the web app: `cd ui/web && npm ci && npm run build`
3. Start the API serving it (dev login, loopback only):

   ```sh
   cd ui/api
   FSE_UI_DEV_AUTH=1 FSE_UI_PUBLIC_URL=http://127.0.0.1:8080 FSE_UI_STATIC_DIR=../web/dist .venv/bin/fse-ui
   ```

4. Open http://127.0.0.1:8080

For live front-end edits, run `npm run dev` in `ui/web` (port 5173, proxies `/api` and
`/auth` to 8080) and start the API with `FSE_UI_PUBLIC_URL=http://localhost:5173`.

### Installable PWA (Live-first)

The same SPA is installable as a standalone PWA (`vite-plugin-pwa` in `web/`). Manifest
name is `fse`, `start_url` is `/live`, `display: standalone`. Icons live under
`web/public/icons/` (192 and 512).

- **Scope:** phone layout reshapes the shell (drawer nav) and the Live page only.
  Research / Configs / other pages stay desktop-oriented behind the drawer.
- **Service worker:** caches the app shell and static assets. `/api/*`, `/auth/*`, and
  WebSocket upgrades are network-only — no offline Live snapshot or offline trading.
- **Auth:** Cognito Hosted UI still redirects out of the standalone window for login /
  MFA, then returns same-origin with the session cookie. Install after the first browser
  login, or expect a brief leave-app redirect when the 8 h session expires.
- **Add to Home Screen:** open `https://fse.example.com` (or your console URL) in
  Safari / Chrome, install, then launch — it opens on Live.

Data lives under `<FSE_UI_HOME or $HOME>/.skylit-fse/`: the engine's usual folders plus
`ui/` (SQLite, job logs, uploads, local secrets) and `configs/` (versioned configs, seeded
from `configs/*.yaml` and `research/*/*.yaml` on first start). The console image replaces
the engine image's real `~/.skylit-fse` directory with a symlink to `/data` before it
starts, because that container's root is read-only and `/data` is the host volume.

## Settings (`FSE_UI_*`)

| variable | default | meaning |
|---|---|---|
| `ENV` | `local` | `local` or `aws` (`aws` needs an https `PUBLIC_URL`) |
| `BIND_HOST`, `PORT` | `127.0.0.1`, `8080` | listen address |
| `PUBLIC_URL` | `http://localhost:5173` | the browser origin (callback URL, WebSocket Origin check) |
| `DEV_AUTH` | `0` | `1`: one-click login; refused unless `ENV=local` and a loopback bind |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`, `OIDC_JWKS_URL`, `OIDC_LOGOUT_URL` | | Cognito Hosted UI login (code flow + PKCE) |
| `ENABLE_LIVE` | `false` | `fse live` (Practice / Combine). Off until the TopstepX terms are verified |
| `SECRET_STORE` | `local` | `local` (0600 file) or `ssm` (`/fse/secrets/*`) |
| `STATIC_DIR` | | the built SPA |
| `HOME`, `PROJECT_DIR` | `$HOME`, the engine folder | data root and engine Project folder |
| `HEAVY_JOBS` | `1` | batch-type jobs that run at once on the local executor (1 to 4) |

## Run on AWS

With `enable_ui = true` (see "Console" in the engine README) the console runs on the
runner host, behind CloudFront and Cognito, at `https://<ui_subdomain>.<domain_name>`.

Unit layout on the host (written by the `fse-ui-host-bundle` SSM document):

- `fse-ui.service` runs the `fse-ui` container (the API and the built SPA) as uid 10001,
  read-only, 768 MiB, with `/data` mounted and `/var/lib/fse-host` mounted read-only at
  `/data/host` (runner state and the hold marker).
- `fse-edge.service` runs the `fse-edge` container (Caddy, 128 MiB) on port 443. It
  takes a Route 53 DNS certificate for the origin name and accepts only requests that
  carry the CloudFront origin secret (`/fse/ui/origin_secret`).
- `fse-runner.service` keeps running `fse run` or `fse paper`. The drop-in
  `fse-runner.service.d/20-console.conf` reads the mode and config the console set in
  `/fse/runner_mode` and `/fse/runner_config`, and reports the runner state to
  `/var/lib/fse-host/runner-state.json`.
- `/etc/fse/ui.env` holds the non-secret settings below. `fse-ui-fetch-env.sh` reads the
  Cognito client secret, the instance id and the image tag at each start; no secret is
  stored in a file on disk outside `/run`.

AWS settings (`FSE_UI_*`; the bundle sets them, values in `/etc/fse/ui.env`):

| variable | default | meaning |
|---|---|---|
| `EXECUTOR` | `local` | `local` or `ecs`; `ecs` sends batch commands to Fargate and needs `SECRET_STORE=ssm` |
| `DATA_BUCKET` | | the data bucket; required with `ecs` |
| `ECS_CLUSTER` | `fse` | ECS cluster for batch tasks |
| `ECS_TASK_FAMILY` | `fse-batch` | task definition family |
| `ECS_CONTAINER` | `fse` | container name in the task definition |
| `ECS_SUBNETS` | | comma list of 1 to 16 subnet ids; required with `ecs` |
| `ECS_SECURITY_GROUP` | | security group id for tasks; required with `ecs` |
| `BATCH_LOG_GROUP` | `/fse/batch` | log group the job log is read from |
| `ECS_MAX_TASKS` | `4` | Fargate tasks that run at once (1 to 16) |
| `RUNNER_CONTROLLER` | `process` | `process` (child process) or `ssm` (`fse-runner` systemd unit); `ssm` needs `ENABLE_LIVE=false` |
| `RUNNER_INSTANCE_ID` | | runner host instance id; required with `ssm` |
| `RUNNER_DOCUMENT` | `fse-runner-control` | SSM document for start, stop and restart |
| `RUNNER_LOG_GROUP` | `/fse/runner` | log group of the runner log tail |
| `ALARM_NAMES` | | comma list of at most 20 CloudWatch alarms shown on Health |
| `AWS_REGION` | `us-east-1` | region of every AWS call |

A missing or invalid value stops the API at start (exit 2). The unit restarts and gives
up after 5 tries; the cause is in `/fse/ui`.

How jobs map to Fargate:

- A command with placement `batch` runs as an `fse-batch` task on Fargate Spot (one task
  per job, `startedBy fse-ui_<job id>`). `fse` commands run as `batch <command>`,
  research CLIs as `batch-py <module> <command>`. The job log streams from
  `/fse/batch`, and outputs land in `s3://<data bucket>/runs/`.
- Config files and uploads named on the command line are copied to S3 before the task
  starts. `fse pull` and `fse import-vix` hold `locks/pull.lock`, `fse experiment holdout`
  holds `locks/holdout.lock`, and a card backtest holds `locks/pull.lock` while it
  uploads cache files S3 is missing. It holds `locks/recordings.lock` only when the
  host has a closed recording day to upload, and releases both before the task starts.
  A held pull or holdout lock ends that job with exit 75. A lock whose `started=`
  time is at least `FSE_LOCK_TTL_S` seconds old (default 7200) is replaced. A held
  recordings lock only skips the recordings upload and names the holder. Unlock
  on the Jobs page deletes a stale lock when no ECS job holds it.
- `fse backtest-plugin` (strategy-card backtests) is one of those Fargate Spot tasks,
  overridden to 1 vCPU / 8 GiB / 40 GiB ephemeral. Several can run at once, up to
  `ECS_MAX_TASKS`. The task sets `FSE_CACHE_BACKEND=s3` and downloads
  `cache/bars/` and `catalog.sqlite` only (not the EFS mount, not heatmaps).
  `POST /api/cards/{id}/backtest` returns 422 with the missing design-window
  ranges when those objects, or the catalog, are not in the bucket
  (`allow_gaps: true` skips the check). That 422 writes a failed phase job, so
  the card shows this refusal instead of an older failure. The warmup object on
  the 422 is informational. Every spec symbol is checked the same way. When
  every design window is present and only warmup is short, history is clamped
  to the fewest sessions that exist and the response includes `warmup_warning`.
  Seed bars from the machine that holds the Atlas pull: `scripts/aws-seed-bars.sh --yes`.
  Exit code 1 on `backtest-plugin` is a failure (the card shows the last log
  lines). Exit code 3 on this command means a needed input is missing, not a
  blank secret. Exit code 76 means the EFS cache is not mounted (other batch
  tasks; card tasks override the backend to s3). A pull lock older than
  `FSE_LOCK_TTL_S` (default 7200s) is replaced; clear one now with
  `scripts/aws-batch.sh unlock --yes` or `scripts/aws-seed-efs-cache.sh unlock --yes`.
  Job logs take `lines`, `limit` or `tail` (default 2000, max 20000). Omit history
  on the card backtest and the card sizes warmup from the spec. Coverage lines
  collapse a run of identical days into one range.
- A console restart does not stop running tasks. The console re-attaches to them on
  start.
- `halt`, `clear`, `scan-secrets` and `skilldocs check` run in the UI container, because
  their inputs are on the host volume. Host-local research commands you add share the one
  heavy local slot. `fse drafts build` and `fse drafts promote` are
  disabled: they edit project files in the image. The host `SeedMissingCache` and task
  `ReadRecordings` statements are applied by `fse-infra`, not by the image deploy.

Logs: the API and SPA log to the `/fse/ui` log group (stream `ui`), Caddy to the same
group (stream `edge`), 30 days. Read them in CloudWatch Logs or with
`aws logs tail /fse/ui --follow`. Requests reach the host from CloudFront edge
addresses, not the operator's; the uvicorn access log is off and Caddy writes none.

## Security

- Every route except `/api/health` and `/auth/*` needs a session. Sessions are server-side
  (only a SHA-256 of the cookie token is stored), 8 h absolute.
- Mutations need the `X-FSE-CSRF` header. WebSockets check the session and `Origin`.
- Every mutation is written to the audit log (Audit page). Secret values are write-only:
  never returned, logged or audited, and redacted from command output.
- CSP: `script-src 'self'`, no third-party requests (fonts and icons are bundled).
- What is disabled and why: `fse live` stays unavailable in the console. Practice or
  Combine for one broker plugin can be set (Live → Order mode, below). `paper` refuses a
  Strategy_Config whose `order_mode` is not `paper`.
- Kill switches: see "Kill switch scope" under "Console Practice and Combine orders (AWS)" below.

## Console Practice and Combine orders (AWS)

`fse live` stays unavailable. Live → Order mode can set Practice or Combine for one broker plugin when the plugin-orders switch is on.

Precondition: the `fse-production` GitHub environment has a required reviewer and its deployment branches are limited to your release branch.

Turn on Practice or Combine for one broker plugin (a plugin you wrote):

1. Set the repository variable `FSE_PLUGIN_ORDERS_ENABLED=true`, then run the fse-infra workflow from your release branch with apply. Deploy order does not matter for safety: the runner stays paper until the new image and the new host control file `/run/fse-control/plugin_orders` both exist and the switch is enabled.
2. Live → Runner → Restart, so the host rewrites `/run/fse-control/plugin_orders`. The runner then passes `--plugin-orders` and `--live-orders` (Combine needs both; Practice ignores `--live-orders`).
3. Settings → Secrets: set `PROJECTX_USERNAME` and `PROJECTX_API_KEY`, then use Find my ProjectX accounts. For Practice, Use as Practice on your PRAC account; for Combine, Use as Combine on the Combine account. The two must differ, and the id is the number, not the account name. Then restart the runner.
4. Combine also needs the plugin on a paper account with the flat deadline on (Plugins page → account → `topstep_50k_combine` preset).
5. Live → <plugin> → Order mode → Practice or Combine. Enter the max daily loss and max trades per day, then confirm the restart.
6. Check the plugin badge says `Practice in use` or `Combine in use`. `Requested …, running Paper: ...` names each failed condition.

Find my ProjectX accounts (Settings → Secrets, and Workflows → ProjectX accounts; the Live order-mode dialog links to it when the practice account id is unset, not numeric or matches no account):
- `POST /api/workflows/accounts-lookup` starts the batch job `fse accounts --json` (ECS RunTask on AWS, a local subprocess otherwise). The ProjectX credentials stay in the job env.
- `GET /api/jobs/{id}/accounts` returns the rows (id, name, can trade), each row's role and the account id problems. Roles and problems are recomputed from the secret store as booleans on every read; no secret value leaves the store.
- `POST /api/accounts/use` sets `PRACTICE_ACCOUNT_ID` or `COMBINE_ACCOUNT_ID` from a row of a finished lookup. Practice on a name that does not start with `PRAC` needs an explicit confirm (409 `confirm_required` otherwise). The same id for both roles is refused (409 `same_account`). An id the log redactor hid cannot be picked.
- After a set, the panel offers Restart runner behind the restart confirm; the runner reads the ids when it starts.

The TopstepX terms are already marked verified: `topstep_terms_verified_on` defaults to the owner's 2026-10-13 check (`infra/variables.tf`), and the repository variable `FSE_TOPSTEP_TERMS_VERIFIED_ON` overrides it.

Caps: edits to `risk.max_daily_loss`, `risk.max_trades_per_day` or `hold_through_break` of a Practice/Combine plugin on the Configs page are saved without the confirm and apply at the next runner start or restart. Change them through Live → <plugin> → Order mode to get the confirm.

Turn it off: Halt blocks new entries at once. An open position and its working brackets stay at the broker until the plugin exits them; to stop now, flatten in TopstepX. Then apply `false` and use Live → Runner → Restart (it refuses while a position or working order is visible).

Kill switch scope: The switch bounds the console application and its users: no console code path can enable plugin orders or `fse live` when the switch is off. It does not bound a principal holding the instance-role credentials. That principal can read the ProjectX key and account ids (ReadFseParameters) and can launch a batch task with command and env overrides (EcsRunBatch). This is pre-existing and accepted (decision c). The IAM narrowing only removes the `/secrets/*` write path.

## Finding cards (rich detail)

`/cards` mounts the design-system `FindingCard` for every row
(`ui/web/src/lib/findingCard.ts` → `cardRowToFindingCardData`):

- **`format: finding_card`** (Playbook / `fse.notify` `cards.jsonl`): `toFindingCardData`.
  A+ / 2R / Pass with a priced `trade` render the rich `.fse-tcard` layout (chart, plan,
  odds, bracket, map, GEX LevelLadder). Every card shows a why/narration panel (Narrator
  or deterministic fallback from `setups[].why` / reasons). Premarket stays compact with
  `summary`.
- **`format: decision`** (host `decision_cards.jsonl` from your plugins): `toFindingCardDataFromDecision` always builds a rich trade — enters with brackets,
  **skips/holds/info with PASS/Skip chrome** and “Why this skipped” prose (not compact).
  The host attaches last ~40×1m OHLC from the live bar store when present, plus
  dayPct/rangePct and pattern odds when known.
- The API merges all `run-*` host runs on the **same UTC day** as the newest run so a
  runner restart does not hide earlier plugins’ cards; the plugin filter lists every
  plugin from the runner config (count may be 0).
- Engine Finding_Card sources: 1-min bars from the live bar store, `dayPct`/`rangePct`,
  `gates` from `GateEvaluation`, odds from `configs/pattern_odds.json`, GEX ladder from
  map snapshot values.
- Enable Narrator with `notify.narrator` for polished prose; it stays off by
  default and must never invent prices or Grades (see `prompts/narrator.md`). Deterministic
  English fallback always ships so the why pane is never empty.

## Checks

```sh
cd ui/api && .venv/bin/ruff check src tests && .venv/bin/mypy && .venv/bin/python -m pytest -q
cd ui/web && npx tsc -b && npm run lint && npm test && npm run build
cd ui/web && npx tsc -p tsconfig.e2e.json && npx playwright install chromium && npm run e2e
```

`npm run e2e` runs the Playwright smoke suite in `web/e2e/` against `web/dist` (build
first). It starts `api/tests/e2e_server.py` on 127.0.0.1:18765 with a temp data folder,
dev login and a stub `fse`, and fails on any CSP violation or page error.
`E2E_PYTHON` sets the interpreter (default `../api/.venv/bin/python`).

`npm run lint` applies `design_system/_adherence.oxlintrc.json` through ESLint (oxlint has
no `no-restricted-syntax`); see `web/eslint.config.js` for the two adjustments.
