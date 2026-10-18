import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { parseDocument } from 'yaml';
import { Badge, Banner, Button, Dialog, Input, Panel, Select, Switch, Tooltip } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SchemaFields } from '../components/SchemaFields';
import { HintSwitch, describeField } from '../components/HintSwitch';
import { Problems } from '../components/Problems';
import { useToast } from '../components/Toasts';
import { ApiError, api, errorText, qs } from '../lib/api';
import {
  NEXUS_DEFAULT, PRESET_NAMES, PRESET_RULES, accountUsers, formFromEntry, getLeaf, removeAccount,
  resolveRules, setLeaf, writeAccount, type AccountForm, type Rules,
} from '../lib/accountsYaml';
import { DASH } from '../lib/format';
import { routeProblems } from '../lib/problems';
import type { ConfigDoc, ConfigSummary, Validation } from '../lib/types';

interface Known { module: string; label: string; brokerage: 'host' | 'self'; note: string; has_schema?: boolean }
type Schema = Record<string, any>;
interface CheckResult { ok: boolean; problems: string[]; plugins: { id: string; problems: string[]; brokerage: string | null }[] }
type Entry = Record<string, any>;
const MAX_PLUGINS = 8;
const ID_RE = /^[a-z][a-z0-9_]{0,31}$/;
export const CARDS_RESTART_TEXT = 'The Cards page hides them as soon as you save; the runner stops writing them at its next restart.';

function csv(v: unknown): string {
  return Array.isArray(v) ? v.join(', ') : '';
}
function list(s: string): string[] {
  return s.split(',').map((x) => x.trim()).filter(Boolean);
}

interface Backtested { source?: string; values: Record<string, unknown> }

function valueAt(entry: Entry, dotted: string): unknown {
  return dotted.split('.').reduce<unknown>((node, k) => (node && typeof node === 'object' ? (node as Entry)[k] : undefined), entry);
}

/** Each backtested value next to this entry's: matches or differs (backtested.yaml). */
export function BacktestMatch({ entry, bt }: { entry: Entry; bt: Backtested }) {
  const rows = Object.entries(bt.values).map(([path, want]) => {
    const have = valueAt(entry, path);
    const same = have !== undefined && String(have) === String(want);
    return { path, want: String(want), have: have === undefined ? 'not set' : String(have), same };
  });
  const differs = rows.filter((r) => !r.same).length;
  return (
    <div className="stack-sm">
      <div className="micro">backtested values{bt.source ? ` · ${bt.source}` : ''}</div>
      {differs > 0 && <Banner tone="warning" title={`${differs} setting${differs > 1 ? 's differ' : ' differs'} from the backtest`}>
        Paper results are only comparable with the backtest when these match.</Banner>}
      <ul className="backtest-match">
        {rows.map((r) => (
          <li key={r.path}>
            <Badge variant={r.same ? 'bull' : 'warning'}>{r.same ? 'matches backtest' : 'differs'}</Badge>
            <code className="inline">{r.path}</code>
            <span className="muted">{r.same ? r.have : `here ${r.have} · backtest ${r.want}`}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SELF_BROKERED_ACCOUNT = 'Self-brokered plugins keep their own book; a paper account cannot apply.';

function PluginCard({ entry, known, problems, accountIds, accountProblems, checkedBrokerage, onChange, onRemove, rs, bt }: {
  entry: Entry; known: Known | undefined; problems: string[]; rs: Schema | undefined; bt?: Backtested;
  accountIds: string[]; accountProblems: string[]; checkedBrokerage: string | null | undefined;
  onChange: (path: (string | number)[], v: unknown) => void; onRemove: () => void;
}) {
  const schema = useQuery({
    queryKey: ['plugin-schema', entry.module],
    queryFn: () => api.get<Record<string, any>>(`/api/schemas/plugin${qs({ module: entry.module })}`),
    enabled: !!known && known.has_schema !== false, retry: false,
  });
  const selfBrokered = known?.brokerage === 'self';
  const accountLocked = (checkedBrokerage ?? known?.brokerage) === 'self';
  const current = typeof entry.account === 'string' ? entry.account : '';
  const accountOptions = [
    { value: '', label: 'none (implicit)' },
    ...accountIds.map((id) => ({ value: id, label: id })),
    // a reference to an id the YAML lacks stays visible (the check reports it)
    ...(current && !accountIds.includes(current) ? [{ value: current, label: `${current} (not defined)` }] : []),
  ];
  const accountHint = accountLocked ? SELF_BROKERED_ACCOUNT
    : describeField(rs, 'PluginEntry', 'account') ?? 'paper account rules for this plugin';
  const accountSelect = (
    <Select label="Account" options={accountOptions} value={current} disabled={accountLocked && current === ''}
      hint={accountHint}
      onChange={(e) => onChange(['account'], e.target.value === '' ? undefined : e.target.value)} />
  );
  const fills = entry.fills ?? {};
  const risk = entry.risk ?? {};
  return (
    <Panel title={entry.id} subtitle={entry.module} icon="puzzle"
      actions={<>
        {known && <Badge variant="outline">{known.brokerage === 'self' ? 'self-brokered' : 'host paper book'}</Badge>}
        {problems.length > 0 && <Badge variant="bear">{problems.length} problem{problems.length > 1 ? 's' : ''}</Badge>}
        <Switch label="enabled" title={describeField(rs, 'PluginEntry', 'enabled')} checked={entry.enabled !== false} onChange={(e) => onChange(['enabled'], e.target.checked)} />
        <Button size="sm" variant="ghost" icon="trash-2" onClick={onRemove}>Remove</Button>
      </>}>
      <div className="plugin-card">
        {known && <div className="small muted">{known.note}</div>}
        {problems.length > 0 && <Banner tone="warning" title="fse run would refuse this">{problems.join(' · ')}</Banner>}
        {bt && <BacktestMatch entry={entry} bt={bt} />}
        <div className="micro">account</div>
        <div className="form-grid">
          <div className="stack-sm">
            {accountLocked ? <Tooltip content={SELF_BROKERED_ACCOUNT}>{accountSelect}</Tooltip> : accountSelect}
            {accountProblems.map((p) => <div key={p} className="small text-bear">{p}</div>)}
          </div>
        </div>
        <div className="micro">config</div>
        {!known ? <div className="small muted">fse cannot load this module (see Check plugins); edit its config in the YAML view.</div>
          : schema.data ? <SchemaFields schema={schema.data} value={entry.config ?? {}}
            onChange={(k, v) => onChange(['config', k], v)} /> : <Empty>Loading the schema…</Empty>}
        {!selfBrokered && <>
          <div className="micro">fills</div>
          <div className="form-grid">
            <Input label="trade_through_ticks" hint={describeField(rs, 'FillsBlock', 'trade_through_ticks')} type="number" value={fills.trade_through_ticks ?? ''} onChange={(e) => onChange(['fills', 'trade_through_ticks'], e.target.value === '' ? undefined : Number(e.target.value))} />
            <Input label="slippage_ticks" hint={describeField(rs, 'FillsBlock', 'slippage_ticks')} type="number" value={fills.slippage_ticks ?? ''} onChange={(e) => onChange(['fills', 'slippage_ticks'], e.target.value === '' ? undefined : Number(e.target.value))} />
            <Select label="stop_fill" hint={describeField(rs, 'FillsBlock', 'stop_fill')} options={['gap_open', 'at_stop']} value={fills.stop_fill ?? 'gap_open'} onChange={(e) => onChange(['fills', 'stop_fill'], e.target.value)} />
            <Input label="commission_rt" hint={`${describeField(rs, 'FillsBlock', 'commission_rt') ?? ''} Write MNQ=1.24, MES=1.24.`}
              value={Object.entries(fills.commission_rt ?? {}).map(([k, v]) => `${k}=${v}`).join(', ')}
              onChange={(e) => onChange(['fills', 'commission_rt'], Object.fromEntries(list(e.target.value).map((p) => p.split('=').map((x) => x.trim())).filter((p) => p.length === 2 && p[0])))} />
          </div>
          <div className="micro">risk (host paper book)</div>
          <div className="small muted">
            Caps the live runner loads from this config. <code className="inline">max_daily_loss</code> also
            can size a plugin's stop risk. Save revision writes the live path; use Save as… or Workflows → Backtest
            plugin overrides to A/B without changing prod.
          </div>
          <div className="form-grid">
            <Input label="max_contracts *" hint={describeField(rs, 'RiskBlock', 'max_contracts')} type="number" min={1} max={100} value={risk.max_contracts ?? ''} onChange={(e) => onChange(['risk', 'max_contracts'], e.target.value === '' ? undefined : Number(e.target.value))} />
            <Input label="max_open_trades" hint={describeField(rs, 'RiskBlock', 'max_open_trades')} type="number" min={1} max={10} value={risk.max_open_trades ?? ''} onChange={(e) => onChange(['risk', 'max_open_trades'], e.target.value === '' ? undefined : Number(e.target.value))} />
            <Input label="max_trades_per_day" hint={describeField(rs, 'RiskBlock', 'max_trades_per_day')} type="number" value={risk.max_trades_per_day ?? ''} onChange={(e) => onChange(['risk', 'max_trades_per_day'], e.target.value === '' ? undefined : Number(e.target.value))} />
            <Input label="max_daily_loss" hint={`${describeField(rs, 'RiskBlock', 'max_daily_loss') ?? ''} Dollars, e.g. 800.`} value={risk.max_daily_loss ?? ''} onChange={(e) => onChange(['risk', 'max_daily_loss'], e.target.value === '' ? undefined : e.target.value)} />
          </div>
          <HintSwitch label="hold_through_break" hint={describeField(rs, 'PluginEntry', 'hold_through_break')} checked={entry.hold_through_break !== false}
            onChange={(e) => onChange(['hold_through_break'], e.target.checked)} />
        </>}
        <HintSwitch label="Publish Finding Cards"
          hint={`${describeField(rs, 'PluginEntry', 'cards') ?? ''} ${CARDS_RESTART_TEXT}`.trim()}
          checked={entry.cards !== false} onChange={(e) => onChange(['cards'], e.target.checked)} />
      </div>
    </Panel>
  );
}

type RuleKind = 'money' | 'bool' | 'int' | 'number' | 'time' | 'mode';
const RULE_FIELDS: { path: string[]; label: string; kind: RuleKind }[] = [
  { path: ['starting_balance', 'value'], label: 'Starting balance', kind: 'money' },
  { path: ['maximum_loss_limit', 'enabled'], label: 'MLL enabled', kind: 'bool' },
  { path: ['maximum_loss_limit', 'value'], label: 'MLL', kind: 'money' },
  { path: ['maximum_loss_limit', 'mode'], label: 'MLL mode', kind: 'mode' },
  { path: ['maximum_loss_limit', 'trail_lock'], label: 'MLL trail lock', kind: 'bool' },
  { path: ['daily_loss_limit', 'enabled'], label: 'DLL enabled', kind: 'bool' },
  { path: ['daily_loss_limit', 'value'], label: 'DLL', kind: 'money' },
  { path: ['profit_target', 'enabled'], label: 'Profit target enabled', kind: 'bool' },
  { path: ['profit_target', 'value'], label: 'Profit target', kind: 'money' },
  { path: ['consistency_target', 'enabled'], label: 'Consistency enabled', kind: 'bool' },
  { path: ['consistency_target', 'pct'], label: 'Consistency %', kind: 'number' },
  { path: ['position_cap', 'enabled'], label: 'Position cap enabled', kind: 'bool' },
  { path: ['position_cap', 'micro_equivalents'], label: 'Position cap (micros)', kind: 'int' },
  { path: ['flat_deadline_enabled'], label: 'Flat by enabled', kind: 'bool' },
  { path: ['flat_deadline'], label: 'Flat by (NY time)', kind: 'time' },
  { path: ['min_trading_days'], label: 'Min trading days', kind: 'int' },
];
const MLL_MODES = ['eod_trailing', 'intraday_trailing', 'static'];
const NEXUS_ACCOUNTS = ['practice', 'evaluation', 'funded', 'agent'];
const MAX_ACCOUNTS = 8;

/** Text from a number field: a number when it reads as one, else the text (the check says why). */
function numberOrText(v: string): number | string {
  return /^-?\d+(\.\d+)?$/.test(v.trim()) ? Number(v) : v;
}

function AccountEditor({ form, isNew, takenIds, problems, onChange, onApply, onCancel }: {
  form: AccountForm; isNew: boolean; takenIds: string[]; problems: string[];
  onChange: (f: AccountForm) => void; onApply: () => void; onCancel: () => void;
}) {
  const base = PRESET_RULES[form.preset];
  const effective = resolveRules(base, form.rules);
  const set = (path: string[], v: Rules[string] | undefined) => onChange({ ...form, rules: setLeaf(form.rules, path, v) });
  const windows = (Array.isArray(effective.no_trade_windows) ? effective.no_trade_windows : []) as { start?: string; end?: string }[];
  const setWindows = (w: { start?: string; end?: string }[]) => set(['no_trade_windows'], w as Rules[string]);
  const envVar = form.nexus.account.startsWith('env:') ? form.nexus.account.slice(4) : null;
  const idError = !ID_RE.test(form.id) ? 'a-z, 0-9, _; starts with a letter'
    : takenIds.includes(form.id) ? 'another account has this id' : undefined;
  return (
    <Panel title={isNew ? 'New account' : `Edit ${form.id}`} subtitle="only fields that differ from the preset are written under rules" icon="sliders-horizontal"
      actions={<>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button size="sm" variant="primary" icon="check" disabled={!!idError} onClick={onApply}>Apply to YAML</Button>
      </>}>
      <div className="stack">
        {problems.length > 0 && <Banner tone="warning" title="fse run would refuse this account">{problems.join(' · ')}</Banner>}
        <div className="form-grid">
          <Input label="id" value={form.id} error={form.id ? idError : undefined} onChange={(e) => onChange({ ...form, id: e.target.value })} />
          <Select label="preset" options={PRESET_NAMES} value={form.preset}
            onChange={(e) => onChange({ ...form, preset: e.target.value as AccountForm['preset'] })} />
        </div>
        <div className="micro">rules</div>
        <div className="form-grid">
          {RULE_FIELDS.map(({ path, label, kind }) => {
            const preset = getLeaf(base, path);
            const key = path.join('.');
            if (kind === 'bool') {
              return <Switch key={key} label={label} checked={getLeaf(effective, path) === true}
                onChange={(e) => set(path, e.target.checked)} />;
            }
            if (kind === 'mode') {
              return <Select key={key} label={label} options={MLL_MODES} value={String(getLeaf(effective, path) ?? preset)}
                onChange={(e) => set(path, e.target.value)} />;
            }
            const own = getLeaf(form.rules, path);
            return <Input key={key} label={label} value={own === undefined || own === null ? '' : String(own)}
              placeholder={preset === null || preset === undefined ? 'none' : String(preset)}
              hint={kind === 'money' ? 'dollars, e.g. 2500.00' : kind === 'time' ? 'HH:MM' : undefined}
              onChange={(e) => set(path, e.target.value === '' ? undefined
                : kind === 'int' || kind === 'number' ? numberOrText(e.target.value) : e.target.value)} />;
          })}
        </div>
        <div className="micro">no-trade windows (NY time, start inclusive, end exclusive)</div>
        <div className="stack-sm">
          {windows.length === 0 && <span className="small muted">none</span>}
          {windows.map((w, i) => (
            <div key={i} className="row">
              <Input label={`window ${i + 1} start`} placeholder="HH:MM" value={w.start ?? ''}
                onChange={(e) => setWindows(windows.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} />
              <Input label={`window ${i + 1} end`} placeholder="HH:MM" value={w.end ?? ''}
                onChange={(e) => setWindows(windows.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} />
              <Button size="sm" variant="ghost" icon="trash-2" onClick={() => setWindows(windows.filter((_, j) => j !== i))}>Remove</Button>
            </div>
          ))}
          <div><Button size="sm" icon="plus" onClick={() => setWindows([...windows, { start: '', end: '' }])}>Add window</Button></div>
        </div>
        <div className="micro">Skylit Nexus mirror</div>
        <div className="form-grid">
          <Switch label="Mirror to Nexus" checked={form.nexus.enabled}
            onChange={(e) => onChange({ ...form, nexus: { ...form.nexus, enabled: e.target.checked } })} />
          <Select label="Nexus account" options={[...NEXUS_ACCOUNTS, { value: 'env', label: 'env variable…' }]}
            value={envVar === null ? form.nexus.account : 'env'}
            onChange={(e) => onChange({ ...form, nexus: { ...form.nexus, account: e.target.value === 'env' ? 'env:' : e.target.value } })} />
          {envVar !== null && (
            <Input label="Variable name" placeholder="SKYLIT_NEXUS_ACCT" hint="the runner reads the account id from it"
              value={envVar} onChange={(e) => onChange({ ...form, nexus: { ...form.nexus, account: `env:${e.target.value}` } })} />
          )}
          <Switch label="Test orders only" checked={form.nexus.test_only}
            onChange={(e) => onChange({ ...form, nexus: { ...form.nexus, test_only: e.target.checked } })} />
        </div>
      </div>
    </Panel>
  );
}

function AccountsPanel({ accounts, users, problems, onEdit, onAdd, onDelete }: {
  accounts: Entry[]; users: (id: string) => string[]; problems: Map<number, string[]>;
  onEdit: (index: number) => void; onAdd: () => void; onDelete: (id: string) => void;
}) {
  return (
    <Panel title="Paper accounts" subtitle="prop-firm rules shared by the plugins that name them" icon="shield-check"
      actions={<Button size="sm" icon="plus" disabled={accounts.length >= MAX_ACCOUNTS} onClick={onAdd}>Add account</Button>}>
      <div className="stack-sm">
        {accounts.length === 0 && <span className="small muted">No accounts. Plugins trade on implicit, unrestricted accounts.</span>}
        {accounts.map((a, j) => {
          const id = String(a?.id ?? '');
          const used = users(id);
          return (
            <div key={`${j}-${id}`} className="stack-sm">
              <div className="row">
                <strong className="grow">{id || '(no id)'}</strong>
                <span className="small muted">preset {String(a?.preset ?? DASH)}</span>
                <span className="small muted">{used.length ? `used by ${used.join(', ')}` : 'unused'}</span>
                {a?.nexus?.enabled === true && <Badge variant="info">Nexus mirror on</Badge>}
                <Button size="sm" icon="sliders-horizontal" onClick={() => onEdit(j)}>Edit</Button>
                <Button size="sm" variant="ghost" icon="trash-2" onClick={() => onDelete(id)}>Delete</Button>
              </div>
              {(problems.get(j) ?? []).length > 0 && (
                <Banner tone="warning">{(problems.get(j) ?? []).join(' · ')}</Banner>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function DataPlane({ dp, onChange, rs }: { dp: Entry; onChange: (path: (string | number)[], v: unknown) => void; rs: Schema | undefined }) {
  const d = (model: string, field: string) => describeField(rs, model, field);
  const px = dp.projectx ?? {};
  const sky = dp.skylit ?? {};
  return (
    <Panel title="Data plane" subtitle="the feeds fse run keeps up, whether a plugin uses them or not" icon="radio">
      <div className="stack">
        <div className="form-grid">
          <Input label="instruments" hint={`${d('DataPlaneConfig', 'instruments') ?? ''} 1 to 4, e.g. MNQ, MES.`} value={csv(dp.instruments)} onChange={(e) => onChange(['instruments'], list(e.target.value))} />
          <Select label="projectx.transport" hint={d('ProjectXConfig', 'transport')} options={['auto', 'stream', 'polling']} value={px.transport ?? 'auto'} onChange={(e) => onChange(['projectx', 'transport'], e.target.value)} />
          <Select label="projectx.subscription" hint={d('ProjectXConfig', 'subscription')} options={['auto', 'live', 'sim']} value={px.subscription ?? 'auto'} onChange={(e) => onChange(['projectx', 'subscription'], e.target.value)} />
          <Select label="projectx.record_quotes" hint={d('ProjectXConfig', 'record_quotes')} options={['off', 'sampled_1s', 'all']} value={px.record_quotes ?? 'sampled_1s'} onChange={(e) => onChange(['projectx', 'record_quotes'], e.target.value)} />
          <Input label="projectx.backfill_sessions" hint={d('ProjectXConfig', 'backfill_sessions')} type="number" min={0} max={60} value={px.backfill_sessions ?? ''} onChange={(e) => onChange(['projectx', 'backfill_sessions'], Number(e.target.value))} />
        </div>
        <div className="form-grid">
          <HintSwitch label="skylit.enabled" hint={d('SkylitConfig', 'enabled')} checked={sky.enabled !== false} onChange={(e) => onChange(['skylit', 'enabled'], e.target.checked)} />
          <Select label="skylit.mode" hint={d('SkylitConfig', 'mode')} options={['stream', 'polling']} value={sky.mode ?? 'stream'} onChange={(e) => onChange(['skylit', 'mode'], e.target.value)} />
          <Input label="skylit.refresh_interval_s" hint={d('SkylitConfig', 'refresh_interval_s')} type="number" min={5} max={3600} value={sky.refresh_interval_s ?? ''} onChange={(e) => onChange(['skylit', 'refresh_interval_s'], Number(e.target.value))} />
          <Input label="skylit.symbols" hint={`${d('SkylitConfig', 'symbols') ?? ''} 1 to 10, e.g. QQQ, SPY.`} value={csv(sky.symbols)} onChange={(e) => onChange(['skylit', 'symbols'], list(e.target.value))} />
          <Input label="skylit.window.start" hint={d('SkylitConfig', 'window')} placeholder="09:00" value={sky.window?.start ?? ''} onChange={(e) => onChange(['skylit', 'window', 'start'], e.target.value)} />
          <Input label="skylit.window.end" hint={d('WindowConfig', 'end')} placeholder="16:15" value={sky.window?.end ?? ''} onChange={(e) => onChange(['skylit', 'window', 'end'], e.target.value)} />
          <HintSwitch label="skylit.dark_pool.enabled" hint={d('DarkPoolConfig', 'enabled')} checked={sky.dark_pool?.enabled === true} onChange={(e) => onChange(['skylit', 'dark_pool', 'enabled'], e.target.checked)} />
          <Input label="skylit.dark_pool.tickers" hint={d('DarkPoolConfig', 'tickers')} value={csv(sky.dark_pool?.tickers)} onChange={(e) => onChange(['skylit', 'dark_pool', 'tickers'], list(e.target.value))} />
        </div>
      </div>
    </Panel>
  );
}

export function PluginsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const configs = useQuery({ queryKey: ['configs', 'runner'], queryFn: () => api.get<ConfigSummary[]>('/api/configs?kind=runner') });
  const known = useQuery({ queryKey: ['plugins-known'], queryFn: () => api.get<Known[]>('/api/plugins/known') });
  const runnerSchema = useQuery({ queryKey: ['schema', 'runner'], queryFn: () => api.get<Schema>('/api/schemas/runner'), retry: false });
  const rs = runnerSchema.data;
  const backtested = useQuery({ queryKey: ['plugins-backtested'], queryFn: () => api.get<{ runners: Record<string, Record<string, Backtested>> }>('/api/plugins/backtested'), retry: false });
  const [name, setName] = useState('runner');
  const doc = useQuery({ queryKey: ['config', 'runner', name], queryFn: () => api.get<ConfigDoc>(`/api/configs/runner/${name}`) });
  const [text, setText] = useState('');
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [addModule, setAddModule] = useState('');
  const [addId, setAddId] = useState('');
  const [editing, setEditing] = useState<{ index: number | null; form: AccountForm } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState('');
  // a save reloads the doc; keep the check it ran so its problems stay inline
  const keepCheck = useRef(false);
  useEffect(() => {
    if (doc.data) {
      setText(doc.data.text);
      if (!keepCheck.current) { setCheck(null); setValidation(null); }
      keepCheck.current = false;
      setEditing(null);
    }
  }, [doc.data]);

  const parsed = useMemo(() => parseDocument(text), [text]);
  const data = (parsed.toJSON() ?? {}) as Entry;
  const plugins: Entry[] = Array.isArray(data.plugins) ? data.plugins : [];
  const accounts: Entry[] = Array.isArray(data.accounts) ? data.accounts : [];
  const accountIds = accounts.map((a) => a?.id).filter((x): x is string => typeof x === 'string');
  const edit = (path: (string | number)[], v: unknown) => {
    if (v === undefined) parsed.deleteIn(path); else parsed.setIn(path, parsed.createNode(v));
    setText(parsed.toString());
    setCheck(null);
  };
  const applyAccount = () => {
    if (!editing) return;
    writeAccount(parsed, editing.index, editing.form);
    setText(parsed.toString());
    setCheck(null);
    setEditing(null);
  };
  const deleteAccount = (id: string) => {
    removeAccount(parsed, id);
    setText(parsed.toString());
    setCheck(null);
    setEditing(null);
  };
  const runCheck = useMutation({ mutationFn: () => api.post<CheckResult>('/api/plugins/check', { text }), onSuccess: setCheck });
  const save = useMutation({
    // Check first so problems show inline; the save validates the schema itself.
    mutationFn: async () => {
      try {
        setCheck(await api.post<CheckResult>('/api/plugins/check', { text }));
      } catch {
        setCheck(null);
      }
      return api.post<{ validation: Validation; config: ConfigDoc }>(`/api/configs/runner/${name}`, { text, message: 'plugins' });
    },
    onSuccess: (r) => { keepCheck.current = true; qc.setQueryData(['config', 'runner', name], r.config); setValidation(r.validation); void qc.invalidateQueries({ queryKey: ['live-cards'] }); toast('ok', `Saved ${name}`, `revision ${r.config.sha}. Restart the runner to load it.`); },
    onError: (err) => { if (err instanceof ApiError && err.status === 422) setValidation((err as any).body.validation); },
  });
  /** Copy current YAML to a new runner name (does not overwrite the selected live config). */
  const saveAs = useMutation({
    mutationFn: async () => {
      const dest = saveAsName.trim();
      return api.post<{ validation: Validation; config: ConfigDoc }>(`/api/configs/runner/${dest}`, {
        text, create: true, message: `copy of ${name} from Plugins`,
      });
    },
    onSuccess: (r) => {
      const dest = saveAsName.trim();
      setSaveAsOpen(false);
      setSaveAsName('');
      void qc.invalidateQueries({ queryKey: ['configs', 'runner'] });
      keepCheck.current = true;
      setName(dest);
      qc.setQueryData(['config', 'runner', dest], r.config);
      setValidation(r.validation);
      toast('ok', `Created ${dest}`, `Live ${name} was not overwritten. Select it on Live → Runner if you intend to run it.`);
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 422) setValidation((err as any).body?.validation ?? null);
    },
  });
  const routed = routeProblems(check?.problems ?? []);

  const add = () => {
    const k = known.data?.find((x) => x.module === addModule);
    const entry: Entry = { id: addId, module: addModule, enabled: false, config: {} };
    if (k?.brokerage === 'host') {
      entry.fills = { trade_through_ticks: 1, slippage_ticks: 0, stop_fill: 'at_stop',
        commission_rt: Object.fromEntries((data.data_plane?.instruments ?? ['MNQ']).map((i: string) => [i, '1.24'])) };
      entry.risk = { max_contracts: 1, max_open_trades: 1 };
    }
    edit(['plugins', plugins.length], entry);
    setAddId('');
  };
  const problemsFor = (id: string) => check?.plugins.find((p) => p.id === id)?.problems ?? [];
  const dirty = !!doc.data && text !== doc.data.text;
  const idOk = ID_RE.test(addId) && !plugins.some((p) => p.id === addId);

  return (
    <div>
      <PageHead title="Plugins" sub="The strategy plugins fse run loads, their settings, fills and risk caps."
        actions={<>
          <Select size="sm" options={(configs.data ?? []).map((c) => c.name)} value={name} onChange={(e) => setName(e.target.value)} />
          <Button icon="list-checks" disabled={runCheck.isPending} onClick={() => runCheck.mutate()}>Check plugins</Button>
          <Button icon="copy" disabled={!doc.data || saveAs.isPending} onClick={() => setSaveAsOpen(true)}>Save as…</Button>
          <Button variant="primary" icon="save" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save revision</Button>
        </>} />
      <div className="page">
        {doc.isError && <Banner tone="warning" title="Could not load config">{errorText(doc.error)}</Banner>}
        {parsed.errors.length > 0 && <Banner tone="warning">The runner YAML does not parse. Fix it in <Link to={`/configs/runner/${name}`}>Configs</Link>.</Banner>}
        {check && (check.ok
          ? <Banner tone="info" title="Check passed">fse run would load every enabled plugin with this data plane.</Banner>
          : <Banner tone="warning" title={`${check.problems.length} problem(s)`}>{routed.rest.filter((p) => !check.plugins.some((x) => x.problems.includes(p))).join(' · ') || 'See the plugins and accounts below.'}</Banner>)}
        {runCheck.isError && <Banner tone="warning">{errorText(runCheck.error)}</Banner>}
        <Problems validation={validation} />
        {!doc.data && !doc.isError ? <Empty>Loading…</Empty>
          : !doc.data ? <Empty>No runner config loaded.</Empty> : <>
          <DataPlane dp={data.data_plane ?? {}} rs={rs} onChange={(p, v) => edit(['data_plane', ...p], v)} />
          <AccountsPanel accounts={accounts} users={(id) => accountUsers(parsed, id)} problems={routed.accounts}
            onAdd={() => setEditing({ index: null, form: { id: '', preset: 'topstep_50k_combine', rules: {}, nexus: { ...NEXUS_DEFAULT } } })}
            onEdit={(j) => setEditing({ index: j, form: formFromEntry(accounts[j]) })}
            onDelete={setDeleting} />
          {editing && (
            <AccountEditor form={editing.form} isNew={editing.index === null}
              takenIds={accountIds.filter((_, j) => j !== editing.index)}
              problems={editing.index === null ? [] : routed.accounts.get(editing.index) ?? []}
              onChange={(form) => setEditing({ ...editing, form })} onApply={applyAccount} onCancel={() => setEditing(null)} />
          )}
          {plugins.map((p, i) => (
            <PluginCard key={`${i}-${p.id}`} rs={rs} bt={backtested.data?.runners?.[name]?.[p.id]} entry={p} known={known.data?.find((k) => k.module === p.module)}
              problems={problemsFor(p.id)} accountIds={accountIds} accountProblems={routed.pluginAccount.get(i) ?? []}
              checkedBrokerage={check?.plugins.find((x) => x.id === p.id)?.brokerage}
              onChange={(path, v) => edit(['plugins', i, ...path], v)}
              onRemove={() => edit(['plugins', i], undefined)} />
          ))}
          <Panel title="Add a plugin" icon="plus">
            <div className="row">
              <Select label="module" options={[{ value: '', label: '— pick —' }, ...(known.data ?? []).map((k) => ({ value: k.module, label: `${k.label} · ${k.module}` }))]}
                value={addModule} onChange={(e) => setAddModule(e.target.value)} />
              <Input label="id" hint="a-z, 0-9, _; unique" value={addId} onChange={(e) => setAddId(e.target.value)} />
              <Button icon="plus" disabled={!addModule || !idOk || plugins.length >= MAX_PLUGINS} onClick={add}>Add (disabled)</Button>
            </div>
            {plugins.length >= MAX_PLUGINS && <div className="small text-warning">A runner config holds at most {MAX_PLUGINS} plugins.</div>}
          </Panel>
        </>}
      </div>
      <ConfirmDialog open={!!deleting} danger title={`Delete ${deleting ?? ''}?`} confirmLabel="Delete" icon="trash-2"
        onClose={() => setDeleting(null)}
        onConfirm={() => { if (deleting) deleteAccount(deleting); setDeleting(null); }}>
        {deleting && deleteText(deleting, accountUsers(parsed, deleting))}
      </ConfirmDialog>
      <Dialog open={saveAsOpen} title="Save as new runner config" onClose={() => setSaveAsOpen(false)} footer={<>
        <Button variant="ghost" onClick={() => setSaveAsOpen(false)}>Cancel</Button>
        <Button variant="primary" disabled={!saveAsName.trim() || saveAsName.trim() === name || saveAs.isPending}
          onClick={() => saveAs.mutate()}>Create copy</Button>
      </>}>
        <div className="stack">
          <Banner tone="info" title="Live config stays put">
            Creates <code className="inline">configs/runner/&lt;name&gt;</code> from the YAML on screen.
            The selected config ({name}) is not overwritten. Practice/Combine order_mode cannot be
            introduced on create — use Live → Order mode on the copy if needed, or Workflows →
            Backtest plugin overrides (paperizes for A/B).
          </Banner>
          <Input label="New name" hint="letters, digits, _ . -" value={saveAsName}
            onChange={(e) => setSaveAsName(e.target.value)} />
          {saveAs.isError && <Banner tone="warning" title="Not created">{errorText(saveAs.error)}</Banner>}
        </div>
      </Dialog>
    </div>
  );
}

/** "plugin_a and plugin_b move to implicit, unrestricted accounts." (or nothing for an unused account). */
function deleteText(id: string, users: string[]): string {
  if (users.length === 0) return `No plugin uses ${id}. Its state folder stays on the runner host.`;
  const names = users.length === 1 ? users[0] : `${users.slice(0, -1).join(', ')} and ${users[users.length - 1]}`;
  return `${names} ${users.length === 1 ? 'moves' : 'move'} to implicit, unrestricted accounts.`;
}
