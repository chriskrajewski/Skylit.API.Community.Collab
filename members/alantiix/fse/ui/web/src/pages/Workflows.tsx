import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Banner, Button, CodeBlock, Input, Panel, Select, Stat, Tabs } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { CommandForm } from '../components/CommandForm';
import { HintSwitch } from '../components/HintSwitch';
import { PluginBacktest } from '../components/PluginBacktest';
import { ProjectXAccounts } from '../components/ProjectXAccounts';
import { JobDetail } from './Jobs';
import { api, errorText } from '../lib/api';
import { isTerminal, useCatalog, useCommand } from '../lib/hooks';
import { num } from '../lib/format';
import type { Job } from '../lib/types';

const TABS = [
  { value: 'pull', label: 'Pull data' },
  { value: 'plugin-backtest', label: 'Backtest plugin' },
  { value: 'backtest', label: 'Backtest Playbook' },
  { value: 'experiment', label: 'Experiments' },
  { value: 'report', label: 'Report' },
  { value: 'calibrate', label: 'Calibrate' },
  { value: 'drafts', label: 'Skill drafts' },
  { value: 'import-vix', label: 'Import VIX' },
  { value: 'export-aws', label: 'Export to AWS' },
  { value: 'research', label: 'Research CLIs' },
  { value: 'maintenance', label: 'Checks' },
  { value: 'projectx-accounts', label: 'ProjectX accounts' },
];

function Launcher({ id, primary, uploads, initial, label }: {
  id: string; primary?: string[]; uploads?: string[]; initial?: Record<string, unknown>; label?: string;
}) {
  const spec = useCommand(id);
  if (!spec) return <Empty>Loading {id}…</Empty>;
  return (
    <Panel title={label ?? id} subtitle={spec.help} icon="play">
      <CommandForm key={id} spec={spec} primary={primary} uploads={uploads} initial={initial} title={id} />
    </Panel>
  );
}

function Picker({ ids, primary }: { ids: string[]; primary?: Record<string, string[]> }) {
  const [id, setId] = useState(ids[0]);
  return (
    <div className="stack">
      <Select label="Command" options={ids} value={id} onChange={(e) => setId(e.target.value)} />
      <Launcher key={id} id={id} primary={primary?.[id]} />
    </div>
  );
}

interface Estimate { job: Job; estimated_gb: number | null; stopped_at_limit: boolean; lines: string[];
  suggested_limit_gb: number | null }

/** Pull: estimate first (a probe that stops before any request), then confirm and run. */
function PullWizard() {
  const spec = useCommand('fse pull');
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, unknown>>({ symbols: 'SPX,SPY,QQQ,NDX,NDXP', instruments: 'ES,NQ' });
  const [probe, setProbe] = useState<string | null>(null);
  const [limit, setLimit] = useState('');
  const est = useQuery({
    queryKey: ['pull-estimate', probe],
    queryFn: () => api.get<Estimate>(`/api/jobs/${probe}/pull-estimate`),
    enabled: !!probe,
    refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.job.status) ? false : 1500),
  });
  const clean = () => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v === '' || v === undefined || v === false) continue;
      out[k] = typeof v === 'string' && ['symbols', 'instruments', 'dark_pool_tickers'].includes(k) ? v : v;
    }
    return out;
  };
  const start = useMutation({
    mutationFn: () => api.post<Job>('/api/workflows/pull-estimate', { values: clean() }),
    onSuccess: (j) => setProbe(j.id),
  });
  const run = useMutation({
    mutationFn: () => api.post<Job>('/api/jobs', {
      command: 'fse pull', title: 'pull', values: { ...clean(), size_limit_gb: Number(limit) || undefined },
    }),
    onSuccess: (j) => navigate(`/jobs/${j.id}`),
  });
  const e = est.data;
  useEffect(() => {
    if (e?.suggested_limit_gb) setLimit(String(e.suggested_limit_gb));
  }, [e?.suggested_limit_gb]);
  if (!spec) return <Empty>Loading…</Empty>;
  const set = (k: string) => (v: unknown) => setValues((p) => ({ ...p, [k]: v }));
  const done = e && isTerminal(e.job.status);
  const help = (dest: string) => spec.args.find((a) => a.dest === dest)?.help;
  return (
    <div className="stack">
      <Panel title="1. What to pull" icon="database">
        <div className="form-grid">
          <Input label="--start *" hint={help('start')} type="date" value={String(values.start ?? '')} onChange={(ev) => set('start')(ev.target.value)} />
          <Input label="--end *" hint={help('end')} type="date" value={String(values.end ?? '')} onChange={(ev) => set('end')(ev.target.value)} />
          <Input label="--symbols" hint={help('symbols')} value={String(values.symbols ?? '')}
            onChange={(ev) => set('symbols')(ev.target.value)} />
          <Input label="--instruments" hint={help('instruments')} value={String(values.instruments ?? '')}
            onChange={(ev) => set('instruments')(ev.target.value)} />
          <HintSwitch label="--dark-pool" hint={help('dark_pool')} checked={values.dark_pool === true} onChange={(ev) => set('dark_pool')(ev.target.checked)} />
          <Select label="--layout" hint={help('layout')} options={['net', 'matrix']}
            value={String(values.layout ?? 'net')} onChange={(ev) => set('layout')(ev.target.value)} />
          {values.layout === 'matrix' && (
            <Input label="--sample-interval-min" hint={help('sample_interval_min')} type="number" min={1}
              value={String(values.sample_interval_min ?? '')} placeholder="default: 1"
              onChange={(ev) => set('sample_interval_min')(ev.target.value === '' ? undefined : Number(ev.target.value))} />
          )}
        </div>
        {values.layout === 'matrix' && (
          <Banner tone="warning" title="Matrix layout costs Skylit credits">
            Every sample is a paid GET /v1/historical request per metric. Check the credits in the estimate before you confirm.
          </Banner>
        )}
        <div className="row">
          <Button variant="primary" icon="gauge" disabled={!values.start || !values.end || start.isPending}
            onClick={() => start.mutate()}>Estimate size</Button>
          <span className="small muted">The estimate run stops before any Replay_Request is sent.</span>
        </div>
        {start.isError && <Banner tone="warning" title="Not started">{errorText(start.error)}</Banner>}
      </Panel>
      {probe && (
        <Panel title="2. Estimate" icon="gauge">
          {!done ? <JobDetail jobId={probe} compact /> : e!.estimated_gb === null ? (
            <div className="stack">
              <Banner tone={e!.job.exit_code === 0 ? 'info' : 'warning'}
                title={e!.job.exit_code === 0 ? 'Nothing to fetch' : 'No estimate'}>
                {e!.job.exit_code === 0
                  ? 'Every window is already in the Data_Cache; the probe finished without a request.'
                  : `The probe exited ${e!.job.exit_code} (${e!.job.exit_meaning}). Its output is below.`}
              </Banner>
              <JobDetail jobId={probe} compact />
            </div>
          ) : (
            <div className="stack">
              <div className="stats"><Stat label="Estimated disk" value={num(e!.estimated_gb, 3)} unit="GB" /></div>
              {e!.lines.length > 0 && <CodeBlock plain numbers={false} code={e!.lines.join('\n')} />}
              <div className="small muted">The estimate above lists the Skylit credits this pull spends. Nothing has been requested yet.</div>
              <div className="row">
                <Input label="--size-limit-gb" type="number" step="any" value={limit}
                  hint="the pull stops if the estimate is above this" onChange={(ev) => setLimit(ev.target.value)} />
                <Button variant="primary" icon="play" disabled={!limit || run.isPending} onClick={() => run.mutate()}>
                  Confirm and pull
                </Button>
              </div>
              {run.isError && <Banner tone="warning" title="Not started">{errorText(run.error)}</Banner>}
            </div>
          )}
        </Panel>
      )}
      <Launcher id="fse pull" label="Pull with every option" primary={['start', 'end']} />
    </div>
  );
}

function ResearchPicker() {
  const { data } = useCatalog();
  const ids = (data?.commands ?? []).filter((c) => c.program.startsWith('research.')).map((c) => c.id);
  if (!ids.length) return <Empty>Loading…</Empty>;
  return <Picker ids={ids} />;
}

export function WorkflowsPage() {
  const { tab = 'pull' } = useParams();
  const navigate = useNavigate();
  const exp = ['ablation', 'sweep', 'montecarlo', 'holdout', 'walkforward', 'cadence'].map((k) => `fse experiment ${k}`);
  return (
    <div>
      <PageHead title="Workflows" sub="Task screens for every batch command. Each run becomes a job; finished jobs link to their run." />
      <div className="page">
        <Tabs items={TABS} value={tab} onChange={(v: string) => navigate(`/workflows/${v}`)} />
        {tab === 'pull' && <PullWizard />}
        {tab === 'plugin-backtest' && <PluginBacktest />}
        {tab === 'backtest' && <Launcher id="fse backtest" primary={['config', 'start', 'end', 'seed', 'offline']} />}
        {tab === 'experiment' && <Picker ids={exp} />}
        {tab === 'report' && <Launcher id="fse report" primary={['run']} />}
        {tab === 'calibrate' && <Launcher id="fse calibrate regime-min-abs" primary={['start', 'end', 'percentile', 'symbol']} />}
        {tab === 'drafts' && <Picker ids={['fse drafts build', 'fse drafts promote']} />}
        {tab === 'import-vix' && <Launcher id="fse import-vix" uploads={['csv']} />}
        {tab === 'export-aws' && <Launcher id="fse export-aws" initial={{ dry_run: true }} primary={['dry_run', 'stage_only', 'datasets', 'months']} />}
        {tab === 'research' && <ResearchPicker />}
        {tab === 'maintenance' && <Picker ids={['fse scan-secrets', 'fse skilldocs check']} />}
        {tab === 'projectx-accounts' && <Panel title="ProjectX accounts" icon="search"><ProjectXAccounts /></Panel>}
      </div>
    </div>
  );
}
