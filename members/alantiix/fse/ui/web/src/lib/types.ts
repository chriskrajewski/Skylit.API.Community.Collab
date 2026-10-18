// Shapes of the API responses the console reads.

export interface Session {
  user: { sub: string; email: string };
  csrf: string;
  env: 'local' | 'aws';
  enable_live: boolean;
  dev_auth: boolean;
  secret_store: 'local' | 'ssm';
  executor: 'local' | 'ecs';
  runner_controller: 'process' | 'ssm';
  /** The deployment's plugin-orders switch (FSE_UI_PLUGIN_ORDERS). */
  plugin_orders: boolean;
  /** YYYY-MM-DD the owner verified the TopstepX terms, or null. */
  topstep_verified_on: string | null;
}

export interface ArgSpec {
  dest: string;
  flag: string | null;
  positional: boolean;
  kind: 'flag' | 'value' | 'append';
  type: 'int' | 'float' | 'path' | 'date' | 'string';
  nargs: string | number | null;
  choices: string[] | null;
  default: unknown;
  required: boolean;
  help: string;
  metavar: string | null;
  /** The config-store kind a path argument takes (the catalog's per-program table). */
  config_kind?: { kind: ConfigKind; prefix?: string } | null;
}

export interface CommandSpec {
  id: string;
  program: string;
  path: string[];
  help: string;
  description: string;
  placement: 'runner' | 'local' | 'batch';
  heavy: boolean;
  disabled_reason: string | null;
  args: ArgSpec[];
  groups: { required: boolean; dests: string[] }[];
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';

export interface Job {
  id: string;
  program: string;
  command: string[];
  argv: string[];
  placement: string;
  executor: string;
  status: JobStatus;
  exit_code: number | null;
  exit_meaning: string | null;
  title: string;
  user: string;
  created_at: number;
  started_at: number | null;
  stopped_at: number | null;
  run_dir: string | null;
  /** The ECS task ARN of a batch job on AWS; null for local jobs. */
  external_id: string | null;
}

export interface Problem {
  key_path: string;
  message: string;
  line: number | null;
  kind: string;
}

export interface Validation {
  ok: boolean;
  errors: Problem[];
  warnings: Problem[];
}

export type ConfigKind = 'runner' | 'strategy' | 'sweep' | 'research';

export interface ConfigSummary {
  kind: ConfigKind;
  name: string;
  origin: string | null;
  current: string;
  revisions: number;
  updated_at: number | null;
  path: string;
}

export interface Revision {
  sha: string;
  at: number;
  author: string;
  message: string;
}

export interface ConfigDoc {
  kind: ConfigKind;
  name: string;
  sha: string;
  current: string;
  origin: string | null;
  text: string;
  revisions: Revision[];
  path: string;
}

export interface RunSummary {
  name: string;
  kind: string;
  run_id: string;
  config_hash: string | null;
  config_path: string | null;
  data_range: { start: string; end: string } | null;
  seed: number | string | null;
  status: string | null;
  has_report: boolean;
  has_trades: boolean;
  file_count: number;
  mtime: number;
}

export interface RunDetail extends RunSummary {
  files: { name: string; size: number }[];
  manifest: Record<string, unknown> | null;
  disclaimer: string;
}

export interface EquityPoint {
  t_ns: number;
  session: string;
  instrument: string;
  net: string;
  r: string;
  cum_usd: string;
  cum_r: string;
  dd_usd: string;
  dd_r: string;
}

export interface Equity {
  points: EquityPoint[];
  max_drawdown_usd: string;
  max_drawdown_r: string;
  net_usd: string;
  net_r: string;
  histogram: { from: string; to: string; count: number }[];
}

export interface BlockInfo {
  halt_file: boolean;
  blocks: { kind: string; instrument: string | null; detail: string; set_at_ns: number | null }[];
}

/** Money is a plain decimal string ("12.50"); never parsed to a float for arithmetic. */
export type Money = string;

export interface PaperPosition {
  /** Open P&L at the live mark (the Live page's live_pnl); null without a mark. */
  tag: string; instrument: string; side: 'long' | 'short'; qty: number; entry_price: string; open_pnl: Money | null;
}

export interface PaperOrder {
  tag: string; leg: string; type: string; side: 'buy' | 'sell'; qty: number; price: string | null;
}

/** Today for one plugin (design §6.1 PluginToday). */
export interface PaperPlugin {
  id: string;
  account: string;
  realized: Money;
  /** Open P&L at the live mark (fse_ui.runner.live_pnl); null when a position has no mark. */
  unrealized: Money | null;
  open_positions: PaperPosition[];
  working_orders: PaperOrder[];
  day_trades: number;
  win_rate_today: number | null;
  cumulative_net: Money;
  caps: { max_contracts: number; used: number; max_daily_loss: Money | null; day_net: Money } | null;
  blocks: string[];
  price_source: Record<string, { source: 'quote' | '1m_close'; t_ns: number }>;
  journal_failed: boolean;
  error?: string;
}

/** The total over every plugin (design §6.1 Today). */
export interface PaperTotal {
  realized: Money; unrealized: Money | null; day_trades: number; win_rate_today: number | null; cumulative_net: Money;
}

export interface PaperToday {
  day: string | null;
  plugins: Record<string, PaperPlugin>;
  total: PaperTotal;
}

export type AccountStatus = 'active' | 'flat_locked' | 'breached' | 'passed';

/** One account (design §6.1 AccountToday); rule fields are null on implicit accounts. */
export interface PaperAccount {
  id: string;
  implicit: boolean;
  unused: boolean;
  plugins: string[];
  preset: string | null;
  starting_balance: Money | null;
  balance: Money | null;
  realized: Money | null;
  unrealized: Money | null;
  equity: Money | null;
  hwm: Money | null;
  eod_high: Money | null;
  drawdown: Money | null;
  mll_floor: Money | null;
  day_net: Money | null;
  trading_days: number | null;
  profit_target: Money | null;
  status: AccountStatus | null;
  breach_reason: string | null;
  breached_at: number | null;
  passed_at: number | null;
  rules_changed_at: number | null;
  view: { distance?: Record<string, Money | number | null>; blocks?: string[];
          exposure_micros?: number; disabled_rules?: string[] } | null;
  nexus: Record<string, any> | null;
  error?: string;
}

/** A journal `trade` line (design §4.2), as the /api/paper/.../trades routes return it. */
export interface JournalTrade {
  line_id: string;
  trade_id: string;
  plugin: string;
  account: string | null;
  tag: string;
  instrument: string;
  side: 'long' | 'short';
  qty: number;
  qty_at_entry: number;
  entry_ns: number;
  entry_price: string;
  exit_ns: number;
  exit_price: string;
  gross: Money;
  fees: Money;
  net: Money;
  exit_reason: string;
  leg: string;
  r: string | null;
  globex_day: string;
  day_source: 'open' | 'after_close';
}

/** A log line's plain-English rendering (fse_ui.events.describe). */
export interface Said { text: string; severity: 'info' | 'warning' | 'error'; category: string }
export type LogLine = Record<string, any> & { say?: Said };
export type PluginPhase = 'faulted' | 'blocked' | 'in_trade' | 'working' | 'waiting' | 'closed';
export interface When { at_ns: number | null; text: string }
/** status.json: written by the host for every plugin (fse.live.host.status). */
export interface PluginStatus {
  version: number;
  plugin: string;
  label: string;
  module: string;
  brokerage: 'host' | 'self';
  updated_ns: number;
  day: string | null;
  phase: PluginPhase;
  phase_text: string;
  blocks: string[];
  next_wake_ns: number | null;
  positions: { tag: string; instrument: string; side: 'long' | 'short'; qty: number; entry: number | null;
               stop: number | null; targets: number[]; opened_ns: number;
               /** For live P&L (absent in an older status.json). Money is a decimal string. */
               entry_t?: number; open_qty?: number; realized?: string; fees?: string;
               tick_value?: string | null; exit_fee?: string | null }[];
  working: { tag: string; instrument: string; side: string; kind: string; role: string; qty: number;
             price: number | null; placed_ns: number }[];
  /** entries: accepted entries today; closed: trades closed today; trades: deprecated alias of entries. */
  today: { net: string; entries?: number; closed?: number; trades: number } | null;
  recent: { at_ns: number; kind: string; tag: string; text: string; net?: string }[];
  last_log: { at_ns: number; kind: string | null } | null;
  plugin_status: {
    summary?: string; now?: string; next?: When; last?: When;
    schedule?: { at_ns: number | null; label: string; state: string }[];
    facts?: [string, string][];
  } | null;
}
/** marks.json: the host's latest quote and 1-minute close per instrument (fse.live.host.marks). */
export interface Marks {
  version: number;
  updated_ns: number;
  marks: Record<string, {
    quote: { price_t: number; price: number; basis: 'last' | 'mid' | 'bid' | 'ask'; t_ns: number; received_ns: number; contract_id: string } | null;
    bar: { price_t: number; price: number; close_ns: number; contract: string } | null;
  }>;
}
/** One open position valued at its mark (fse_ui.runner.live_pnl). Money is a 2-decimal string. */
export interface PositionPnl {
  mark: number | null;
  mark_t: number | null;
  mark_source: 'quote' | '1m_close' | null;
  mark_received_ns: number | null;
  age_s: number | null;
  stale: boolean;
  ticks: number | null;
  open_gross: string | null;
  open_net: string | null;
}
export interface PluginPnl {
  positions: Record<string, PositionPnl>;
  day: { closed: string | null; open: string | null; total: string | null; entries: number | null; closed_trades: number | null };
  as_of_ns: number;
}
export interface LivePlugin {
  id: string;
  status: PluginStatus | null;
  log: LogLine[];
  faulted: boolean;
  has_raw: boolean;
  updated_ns?: number;
  /** null for a plugin the host does not book. */
  pnl?: PluginPnl | null;
  /** The last order_mode line of the plugin's log in this run; null before one. */
  order_mode?: EffectiveOrderMode | null;
}
export interface SkylitPlan {
  day: string | null; symbols: string[] | null; credits_per_day: number | null; refresh_interval_s: number | null;
  window_start: string | null; window_end: string | null; exchange_session: boolean | null; open: boolean;
}
export interface LiveSnapshot {
  run_dir: string | null;
  now_ns?: number;
  host_log: LogLine[];
  heartbeat: Record<string, any> | null;
  skylit?: SkylitPlan | null;
  faults: Record<string, any>[];
  plugins: LivePlugin[];
  marks?: Marks | null;
  blocks: { root: BlockInfo; plugins: Record<string, BlockInfo>; halted: boolean };
  runner?: RunnerStatus;
  paper?: PaperToday;
  accounts?: Record<string, PaperAccount>;
}
export type CardAction = 'enter' | 'skip' | 'hold' | 'exit' | 'result' | 'info';
/** A plugin decision card (v1, cleaned by the host); prices in futures points. */
export interface DecisionCard {
  id: string;
  title: string;
  as_of_ns: number;
  action: CardAction;
  instrument: string | null;
  symbol: string | null;
  side: 'long' | 'short' | null;
  qty: number | null;
  entry: number | null;
  stop: number | null;
  targets: number[];
  reason: string | null;
  ref: string | null;
  tag: string | null;
  result: { net?: string; exit_reason?: string; r?: string; ticks?: number } | null;
  fields: [string, string][];
  /** Optional rich-card enrichments (host / plugin). */
  spot?: number | null;
  dayPct?: number | null;
  rangePct?: number | null;
  gates?: string | null;
  bars?: Array<{ o: number; h: number; l: number; c: number }>;
  odds?: { label: string; items: Array<{ pct: number; label: string }> } | null;
  ladder?: Array<{
    kind: 'king' | 'ceiling' | 'floor' | 'gatekeeper' | 'air' | 'wall';
    level: number;
    strike?: number | string;
    value?: number;
    label?: string;
  }>;
  narration?: string | null;
}
/** One row of the Cards page: a decision card (with its result joined) or a Finding_Card. */
export interface CardRow {
  plugin: string | null;
  t_ns: number;
  format: 'decision' | 'finding_card';
  kind?: string | null;
  card?: DecisionCard;
  result?: { card: DecisionCard; t_ns: number; raw: Record<string, any> } | null;
  title?: string;
  source?: string;
  raw: Record<string, any>;
}
export interface CardPlugin { id: string; publish: boolean; count: number; hidden: boolean }
export interface CardsResponse {
  run_dir: string | null;
  runner_config: string | null;
  plugins: CardPlugin[];
  cards: CardRow[];
}

export type RunnerState = 'running' | 'stopped' | 'starting' | 'stopping' | 'failed' | 'unknown';

export interface RunnerCommand {
  action: string;
  status: string;
  at: number;
  output: string[];
}

export interface RunnerStatus {
  /** process (a child of the API) or systemd (the fse-runner unit on AWS). */
  controller: string;
  state: RunnerState;
  pid: number | null;
  started_at: number | null;
  last_exit_code: number | null;
  last_exit_meaning: string | null;
  settings: { mode: 'run' | 'paper'; runner_config: string; strategy_config: string };
  /** Oldest first from every controller; the Live page shows it newest first. */
  log: string[];
  enable_live?: boolean;
  /** Stopped in the console: deploys leave it stopped (systemd only). */
  held?: boolean;
  pending?: string | null;
  last_command?: RunnerCommand | null;
  error?: string | null;
}

export interface AlarmRow {
  name: string;
  state: string;
  reason: string | null;
  updated_at: number | null;
}

export interface DailyList {
  reports: { day: string; has_md: boolean; partial?: boolean }[];
  /** null off AWS; an error entry when CloudWatch cannot be read. */
  alarms: AlarmRow[] | { error: string } | null;
}

export interface BatchLock {
  name: 'pull' | 'holdout' | 'recordings';
  held: boolean;
  holder: string[] | null;
  job: string | null;
  error?: string;
}

export interface BatchLocks {
  enabled: boolean;
  locks: BatchLock[];
}
export type OrderMode = 'paper' | 'practice' | 'combine';
/** What the runner did with a plugin's requested order mode (its order_mode log line). */
export interface EffectiveOrderMode {
  requested: OrderMode;
  mode: OrderMode;
  failures: string[];
  t_ns: number | null;
}
export interface OrderModePlugin {
  id: string;
  label: string;
  order_mode: OrderMode;
  max_daily_loss: string | null;
  max_trades_per_day: number | null;
  hold_through_break: boolean;
  /** Reasons Practice is not available for this plugin (on top of the global ones). */
  practice_blocked: string[];
  /** Reasons Combine is not available for this plugin (on top of combine_disabled_reasons). */
  combine_blocked: string[];
  effective: EffectiveOrderMode | null;
  busy: boolean;
}
/** GET /api/order-modes: the broker plugins of the selected runner config. */
export interface OrderModes {
  runner_config: string;
  sha: string;
  runner_state: RunnerState;
  held: boolean;
  plugin_orders: boolean;
  /** Reasons Practice is not available for any plugin. */
  disabled_reasons: string[];
  /** Reasons Combine is not available for any plugin. */
  combine_disabled_reasons: string[];
  switch_pending: boolean;
  plugins: OrderModePlugin[];
}
export interface OrderModeRequest {
  plugin_id: string;
  order_mode: 'paper' | 'practice' | 'combine';
  /** A decimal string, e.g. "375.50". */
  max_daily_loss?: string;
  max_trades_per_day?: number;
  expected_sha: string;
  confirm_restart?: boolean;
}
export interface OrderModeResult extends OrderModes {
  saved: string;
  restarted: boolean;
  message: string;
  note: string | null;
}

export type StrategyGrade = 'PASS' | 'FAIL' | 'DRAFT';

export interface StrategyCardSummary {
  id: string;
  created_at: number;
  name: string;
  author: string;
  post_id: string;
  hypothesis: string;
  spec_hash: string;
  design_hash: string;
  grade: StrategyGrade;
  symbols: string[];
  timeframe: string;
}

export interface StrategyCardList {
  cards: StrategyCardSummary[];
}

export interface StrategyCardView {
  id: string;
  name: string;
  hypothesis: string;
  grade: StrategyGrade;
  source: { url: string; author: string; post_id: string; text: string };
  rules: string[];
  probabilities: Array<{ label: string; pct: number | null }>;
  judge: Array<{ name: string; passed: boolean; detail: string }>;
  equity: { trades: number | null; net: string | null; expectancy_r: string | null };
  side: string;
  symbols: string[];
  timeframe: string;
  spec_hash: string;
  design_hash: string;
  created_at: number;
  job_id?: string | null;
  run_dir?: string | null;
}
