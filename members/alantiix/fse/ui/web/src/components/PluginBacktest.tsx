import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { parse } from 'yaml';
import { Badge, Banner, Button, Checkbox, DataTable, Input, Panel, Select, Switch } from '../ds/index.js';
import { Empty } from './PageHead';
import { HintSwitch } from './HintSwitch';
import { Problems } from './Problems';
import { api, errorText, ApiError } from '../lib/api';
import { useCommand } from '../lib/hooks';
import type { ConfigDoc, ConfigSummary, Job, Validation } from '../lib/types';

export interface CoverageDay { day: string; exchange_session: boolean; sources: Record<string, string>; missing: string[] }
export interface CoverageResult {
  ok: boolean; problems: string[]; plugins?: string[]; complete?: boolean; needs?: string[];
  layout?: string; warnings?: string[]; days?: CoverageDay[];
}

interface PluginEntry {
  id: string; module: string; enabled?: boolean;
  risk?: Record<string, unknown>; fills?: Record<string, unknown>; hold_through_break?: boolean;
}

/** Per-plugin risk/fills edits for a backtest-only override (never written to live YAML). */
export interface OverrideFields {
  max_contracts: string;
  max_open_trades: string;
  max_trades_per_day: string;
  max_daily_loss: string;
  hold_through_break: boolean;
  trade_through_ticks: string;
  slippage_ticks: string;
  stop_fill: string;
  commission_rt: string;
}

export function fieldsFromEntry(p: PluginEntry): OverrideFields {
  const risk = p.risk ?? {};
  const fills = p.fills ?? {};
  const commission = fills.commission_rt && typeof fills.commission_rt === 'object'
    ? Object.entries(fills.commission_rt as Record<string, unknown>).map(([k, v]) => `${k}=${v}`).join(', ')
    : '';
  return {
    max_contracts: risk.max_contracts == null ? '' : String(risk.max_contracts),
    max_open_trades: risk.max_open_trades == null ? '' : String(risk.max_open_trades),
    max_trades_per_day: risk.max_trades_per_day == null ? '' : String(risk.max_trades_per_day),
    max_daily_loss: risk.max_daily_loss == null ? '' : String(risk.max_daily_loss),
    hold_through_break: p.hold_through_break !== false,
    trade_through_ticks: fills.trade_through_ticks == null ? '' : String(fills.trade_through_ticks),
    slippage_ticks: fills.slippage_ticks == null ? '' : String(fills.slippage_ticks),
    stop_fill: typeof fills.stop_fill === 'string' ? fills.stop_fill : 'gap_open',
    commission_rt: commission,
  };
}

function numOrUndef(s: string): number | undefined {
  const t = s.trim();
  if (t === '') return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

/** Build the plugins patch body for POST /api/backtest-plugin/override-config. */
export function buildOverridePlugins(
  chosen: string[],
  byId: Record<string, PluginEntry>,
  edits: Record<string, OverrideFields>,
): Record<string, {
  risk?: Record<string, unknown>; fills?: Record<string, unknown>; hold_through_break?: boolean;
}> {
  const out: Record<string, {
    risk?: Record<string, unknown>; fills?: Record<string, unknown>; hold_through_break?: boolean;
  }> = {};
  for (const id of chosen) {
    const f = edits[id] ?? (byId[id] ? fieldsFromEntry(byId[id]) : null);
    if (!f) continue;
    const risk: Record<string, unknown> = {};
    const mc = numOrUndef(f.max_contracts);
    if (mc !== undefined) risk.max_contracts = mc;
    const mo = numOrUndef(f.max_open_trades);
    if (mo !== undefined) risk.max_open_trades = mo;
    const mt = numOrUndef(f.max_trades_per_day);
    if (mt !== undefined) risk.max_trades_per_day = mt;
    if (f.max_daily_loss.trim() !== '') risk.max_daily_loss = f.max_daily_loss.trim();
    const fills: Record<string, unknown> = {};
    const tt = numOrUndef(f.trade_through_ticks);
    if (tt !== undefined) fills.trade_through_ticks = tt;
    const sl = numOrUndef(f.slippage_ticks);
    if (sl !== undefined) fills.slippage_ticks = sl;
    if (f.stop_fill) fills.stop_fill = f.stop_fill;
    if (f.commission_rt.trim()) {
      fills.commission_rt = Object.fromEntries(
        f.commission_rt.split(',').map((p) => p.trim()).filter(Boolean)
          .map((p) => p.split('=').map((x) => x.trim()))
          .filter((p) => p.length === 2 && p[0]),
      );
    }
    out[id] = { risk, fills, hold_through_break: f.hold_through_break };
  }
  return out;
}

const SOURCE_BADGE: Record<string, 'bull' | 'accent-soft' | 'bear' | 'neutral'> = {
  recording: 'bull', cache: 'accent-soft', missing: 'bear', 'no session': 'neutral',
};
const SOURCE_TEXT: Record<string, string> = {
  recording: 'recorded live', cache: 'Data_Cache', missing: 'missing', 'no session': 'no session',
};

/** The day x input table of a coverage check. */
export function CoverageTable({ cov }: { cov: CoverageResult }) {
  const needs = cov.needs ?? [];
  const rows = (cov.days ?? []).map((d) => ({ ...d, _k: d.day }));
  return (
    <DataTable dense maxHeight="45vh" rows={rows} rowKey="_k" columns={[
      { key: 'day', label: 'Day', width: 110 },
      ...needs.map((n) => ({
        key: n, label: n,
        render: (_: unknown, d: CoverageDay) => {
          if (!d.exchange_session) return <span className="muted small">no session</span>;
          const src = d.sources[n] ?? 'missing';
          return <Badge variant={SOURCE_BADGE[src] ?? 'neutral'}>{SOURCE_TEXT[src] ?? src}</Badge>;
        },
      })),
    ]} />
  );
}

function OverrideEditor({ id, fields, onChange }: {
  id: string; fields: OverrideFields; onChange: (f: OverrideFields) => void;
}) {
  const set = (k: keyof OverrideFields, v: string | boolean) => onChange({ ...fields, [k]: v });
  return (
    <div className="stack-sm">
      <div className="micro">{id} · risk & fills for this backtest only</div>
      <div className="form-grid">
        <Input label="max_contracts" type="number" min={1} max={100} value={fields.max_contracts}
          onChange={(e) => set('max_contracts', e.target.value)} />
        <Input label="max_open_trades" type="number" min={1} max={10} value={fields.max_open_trades}
          onChange={(e) => set('max_open_trades', e.target.value)} />
        <Input label="max_trades_per_day" type="number" value={fields.max_trades_per_day}
          onChange={(e) => set('max_trades_per_day', e.target.value)} />
        <Input label="max_daily_loss" hint="dollars, e.g. 800 — sizes stops to this budget"
          value={fields.max_daily_loss} onChange={(e) => set('max_daily_loss', e.target.value)} />
        <Input label="trade_through_ticks" type="number" value={fields.trade_through_ticks}
          onChange={(e) => set('trade_through_ticks', e.target.value)} />
        <Input label="slippage_ticks" type="number" value={fields.slippage_ticks}
          onChange={(e) => set('slippage_ticks', e.target.value)} />
        <Select label="stop_fill" options={['gap_open', 'at_stop']} value={fields.stop_fill}
          onChange={(e) => set('stop_fill', e.target.value)} />
        <Input label="commission_rt" hint="MNQ=1.24, MES=1.24" value={fields.commission_rt}
          onChange={(e) => set('commission_rt', e.target.value)} />
      </div>
      <Switch label="hold_through_break" checked={fields.hold_through_break}
        onChange={(e) => set('hold_through_break', e.target.checked)} />
    </div>
  );
}

/**
 * Backtest plugins of a runner config through `fse backtest-plugin`: the same host,
 * paper fills and risk caps as the live runner. Optional overrides write a copy under
 * uploads/ or a draft runner name — the live/prod YAML is never modified.
 */
export function PluginBacktest() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const spec = useCommand('fse backtest-plugin');
  const configs = useQuery({ queryKey: ['configs', 'runner'], queryFn: () => api.get<ConfigSummary[]>('/api/configs?kind=runner') });
  const [name, setName] = useState('runner.paper');
  const doc = useQuery({
    queryKey: ['config', 'runner', name], enabled: !!name,
    queryFn: () => api.get<ConfigDoc>(`/api/configs/runner/${name}`),
  });
  const enabled = useMemo(() => {
    try {
      const data = parse(doc.data?.text ?? '') as { plugins?: PluginEntry[] };
      return (data?.plugins ?? []).filter((p) => p.enabled !== false);
    } catch {
      return [];
    }
  }, [doc.data]);
  const byId = useMemo(() => Object.fromEntries(enabled.map((p) => [p.id, p])), [enabled]);
  const [picked, setPicked] = useState<string[] | null>(null);
  const chosen = picked ?? enabled.map((p) => p.id);
  const [first, setFirst] = useState('');
  const [last, setLast] = useState('');
  const [allowGaps, setAllowGaps] = useState(false);
  const [cov, setCov] = useState<CoverageResult | null>(null);
  const [useOverride, setUseOverride] = useState(false);
  const [keepDraft, setKeepDraft] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [edits, setEdits] = useState<Record<string, OverrideFields>>({});
  const [overrideNote, setOverrideNote] = useState<string | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const help = (dest: string) => spec?.args.find((a) => a.dest === dest)?.help;
  const summary = configs.data?.find((c) => c.name === name);

  // Prefill override editors from the loaded YAML when enabling or when picks change.
  useEffect(() => {
    if (!useOverride) return;
    setEdits((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const id of chosen) {
        if (next[id] || !byId[id]) continue;
        next[id] = fieldsFromEntry(byId[id]);
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [useOverride, chosen, byId]);

  const check = useMutation({
    mutationFn: () => api.post<CoverageResult>('/api/backtest-plugin/coverage', { config: name, plugins: chosen, first, last }),
    onSuccess: setCov,
  });

  const run = useMutation({
    mutationFn: async () => {
      setValidation(null);
      setOverrideNote(null);
      let configPath = summary?.path;
      if (useOverride) {
        const body: Record<string, unknown> = {
          source: name,
          keep: keepDraft,
          plugins: buildOverridePlugins(chosen, byId, edits),
        };
        if (keepDraft && draftName.trim()) body.name = draftName.trim();
        const r = await api.post<{ validation: Validation; override: {
          path: string; storage: string; name: string | null; source_sha: string; live_untouched: boolean;
        } | null }>('/api/backtest-plugin/override-config', body);
        if (!r.override) {
          setValidation(r.validation);
          throw new Error('override config failed validation');
        }
        configPath = r.override.path;
        setOverrideNote(
          r.override.storage === 'runner'
            ? `Using draft runner ${r.override.name}. Live ${name}@${r.override.source_sha} was not changed.`
            : `Using ephemeral upload. Live ${name}@${r.override.source_sha} was not changed.`,
        );
        if (r.override.storage === 'runner') void qc.invalidateQueries({ queryKey: ['configs', 'runner'] });
      }
      return api.post<Job>('/api/jobs', {
        command: 'fse backtest-plugin',
        title: `backtest ${chosen.join(', ')}${useOverride ? ' (override)' : ''}`,
        values: { config: configPath, plugin: chosen, first, last, allow_gaps: allowGaps || undefined },
      });
    },
    onSuccess: (j) => navigate(`/jobs/${j.id}`),
    onError: (err) => {
      if (!(err instanceof ApiError) || err.status !== 422) return;
      const body = (err as ApiError & { body?: { validation?: Validation } }).body;
      if (body?.validation) setValidation(body.validation);
    },
  });

  const toggle = (id: string) => {
    setCov(null);
    setPicked(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
  };
  const ready = !!summary && chosen.length > 0 && !!first && !!last && first <= last;
  if (!spec) return <Empty>Loading…</Empty>;
  return (
    <div className="stack">
      <Banner tone="info" title="Live runner config is unchanged">
        A backtest reads the selected YAML (or an override copy). Saving Plugins/Configs edits live;
        overrides here write only under uploads/ or a new draft runner name — never the live path the
        runner loads from SSM.
      </Banner>
      <Panel title="1. Pick the plugins" icon="puzzle"
        subtitle="Each runs with the same host fills and risk caps unless you add overrides below.">
        <div className="stack">
          {doc.isError && <Banner tone="warning" title="Config failed to load">{errorText(doc.error)}</Banner>}
          {!doc.data && !doc.isError && name && <Empty>Loading runner YAML…</Empty>}
          <div className="form-grid">
            <Select label="Runner config" hint={help('config')}
              options={(configs.data ?? []).map((c) => c.name)} value={name}
              onChange={(e) => {
                setName(e.target.value); setPicked(null); setCov(null); setEdits({}); setOverrideNote(null);
              }} />
            <Input label="First day *" hint={help('first')} type="date" value={first}
              onChange={(e) => { setFirst(e.target.value); setCov(null); }} />
            <Input label="Last day *" hint={help('last')} type="date" value={last}
              onChange={(e) => { setLast(e.target.value); setCov(null); }} />
          </div>
          {enabled.length === 0 ? <span className="small muted">This config has no enabled plugin.</span> : (
            <fieldset className="plugin-picks">
              <legend className="micro">plugins to backtest</legend>
              {enabled.map((p) => (
                <Checkbox key={p.id} label={`${p.id} · ${p.module.split(':')[1] ?? p.module}`}
                  checked={chosen.includes(p.id)} onChange={() => toggle(p.id)} />
              ))}
            </fieldset>
          )}
          <div className="row">
            <Button icon="list-checks" disabled={!ready || check.isPending} onClick={() => check.mutate()}>Check data</Button>
            <span className="small muted">Shows which inputs exist for each day before anything runs.</span>
          </div>
          {check.isError && <Banner tone="warning" title="Check failed">{errorText(check.error)}</Banner>}
        </div>
      </Panel>
      <Panel title="Overrides (optional)" icon="sliders-horizontal"
        subtitle="A/B risk or fills without editing the live Plugins YAML. Each combo is a separate job.">
        <div className="stack">
          <HintSwitch label="Use override settings for this run"
            hint="Writes a copy for this job only; the live Plugins YAML is not saved."
            checked={useOverride}
            onChange={(e) => { setUseOverride(e.target.checked); setOverrideNote(null); }} />
          {useOverride && <>
            <HintSwitch label="Keep as named draft runner config"
              hint="Off = one-shot file under uploads/. On = new configs/runner/<name> you can reuse."
              checked={keepDraft}
              onChange={(e) => setKeepDraft(e.target.checked)} />
            {keepDraft && (
              <Input label="Draft name" hint="letters, digits, _ . - · leave blank to auto-name bt-<source>-…"
                value={draftName} onChange={(e) => setDraftName(e.target.value)} />
            )}
            {chosen.length === 0
              ? <span className="small muted">Pick at least one plugin above.</span>
              : chosen.map((id) => {
                const fields = edits[id] ?? (byId[id] ? fieldsFromEntry(byId[id]) : null);
                if (!fields) return null;
                return (
                  <OverrideEditor key={id} id={id} fields={fields}
                    onChange={(f) => setEdits((prev) => ({ ...prev, [id]: f }))} />
                );
              })}
          </>}
          {overrideNote && <Banner tone="info">{overrideNote}</Banner>}
          <Problems validation={validation} />
        </div>
      </Panel>
      {cov && (
        <Panel title="2. Data for each day" icon="database"
          subtitle={cov.ok ? `needs: ${(cov.needs ?? []).join(', ')}` : undefined}>
          <div className="stack">
            {!cov.ok ? <Banner tone="warning" title="Cannot backtest">{cov.problems.join(' · ')}</Banner> : <>
              {cov.complete
                ? <Banner tone="info" title="Every input is there">Every needed input exists for every day.</Banner>
                : <Banner tone="warning" title="Some inputs are missing">
                    On the red days the plugin would get no data for that input. Record or pull it first, or run anyway.
                  </Banner>}
              {(cov.warnings ?? []).map((w) => <div key={w} className="small text-warning">{w}</div>)}
              <CoverageTable cov={cov} />
              <HintSwitch label="--allow-gaps" hint={help('allow_gaps')} checked={allowGaps}
                onChange={(e) => setAllowGaps(e.target.checked)} />
              <div className="row">
                <Button variant="primary" icon="play" disabled={!ready || run.isPending || (!cov.complete && !allowGaps)}
                  onClick={() => run.mutate()}>
                  {useOverride ? 'Run backtest with overrides' : 'Run backtest'}
                </Button>
                <span className="small muted">Runs as a job; the result appears on the Runs page with its equity curve.</span>
              </div>
              {run.isError && <Banner tone="warning" title="Not started">{errorText(run.error)}</Banner>}
            </>}
          </div>
        </Panel>
      )}
    </div>
  );
}
