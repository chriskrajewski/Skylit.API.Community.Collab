import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Badge, Banner, Button, Checkbox, CodeBlock, DataTable, Input, KeyValue, MeterBar, Panel, Segmented, Select, Stat, Tabs } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { EquityChart, Histogram } from '../components/Charts';
import { api, qs } from '../lib/api';
import { DASH, UNAVAILABLE, etDateTime, fromNs, isNotApplicable, num, pct, signedR, tone, usd } from '../lib/format';
import type { Equity, RunDetail, RunSummary } from '../lib/types';

const KINDS = ['', 'backtest', 'plugin_backtest', 'pull', 'runner', 'session', 'ablation', 'sweep', 'montecarlo', 'holdout',
  'walkforward', 'cadence', 'drafts', 'other'];
export const EXPERIMENT_KINDS = ['ablation', 'sweep', 'montecarlo', 'holdout', 'walkforward', 'cadence', 'drafts'];

export function RunsTable({ kinds, selectable }: { kinds?: string[]; selectable?: boolean }) {
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const navigate = useNavigate();
  const runs = useQuery({ queryKey: ['runs', kind, q], queryFn: () => api.get<RunSummary[]>(`/api/runs${qs({ kind, q })}`) });
  const rows = (runs.data ?? []).filter((r) => !kinds || kinds.includes(r.kind));
  const toggle = (name: string) => setPicked((p) => (p.includes(name) ? p.filter((x) => x !== name) : [...p, name].slice(-4)));
  return (
    <div className="stack">
      <div className="row">
        {!kinds && <Select size="sm" options={KINDS.map((k) => ({ value: k, label: k || 'every kind' }))}
          value={kind} onChange={(e) => setKind(e.target.value)} />}
        <div className="grow"><Input size="sm" icon="search" placeholder="Filter by folder name" value={q}
          onChange={(e) => setQ(e.target.value)} /></div>
        {selectable && <Button size="sm" icon="git-compare" disabled={picked.length < 2}
          onClick={() => navigate(`/runs/compare?runs=${picked.map(encodeURIComponent).join(',')}`)}>
          Compare {picked.length || ''}</Button>}
      </div>
      <Panel flush>
        {runs.isLoading ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No runs yet.</Empty> : (
          <DataTable dense rows={rows} rowKey="name" onRowClick={(r: RunSummary) => navigate(`/runs/${encodeURIComponent(r.name)}`)}
            columns={[
              ...(selectable ? [{ key: 'pick', label: '', width: 32, render: (_: unknown, r: RunSummary) => (
                <span onClick={(e) => e.stopPropagation()}><Checkbox aria-label={`compare ${r.name}`}
                  checked={picked.includes(r.name)} onChange={() => toggle(r.name)} /></span>) }] : []),
              { key: 'name', label: 'Run' },
              { key: 'kind', label: 'Kind', width: 110, render: (v: string) => <Badge variant="outline">{v}</Badge> },
              { key: 'data_range', label: 'Range', width: 210, muted: true,
                render: (v: RunSummary['data_range']) => (v ? `${v.start} → ${v.end}` : DASH) },
              { key: 'config_hash', label: 'Config', width: 90, muted: true, render: (v: string | null) => v ?? DASH },
              { key: 'status', label: 'Status', width: 100, render: (v: string | null) => v ?? DASH },
              { key: 'mtime', label: 'Updated', width: 190, muted: true, render: (v: number) => etDateTime(v) },
            ]} />
        )}
      </Panel>
    </div>
  );
}

export function RunsPage() {
  return (
    <div>
      <PageHead title="Runs" sub="Every output folder: backtests, experiments, pulls, runner and paper sessions." />
      <div className="page"><RunsTable selectable /></div>
    </div>
  );
}

function metric(m: Record<string, unknown> | null, k: string): unknown {
  if (!m) return null;
  return m[k];
}

function Summary({ run }: { run: string }) {
  const [unit, setUnit] = useState<'r' | 'usd'>('r');
  const metrics = useQuery({ queryKey: ['metrics', run], queryFn: () => api.get<{ source: string | null; metrics: Record<string, unknown> | null }>(`/api/runs/${encodeURIComponent(run)}/metrics`) });
  const equity = useQuery({ queryKey: ['equity', run], queryFn: () => api.get<Equity>(`/api/runs/${encodeURIComponent(run)}/equity`), retry: false });
  const m = metrics.data?.metrics ?? null;
  if (metrics.isLoading) return <Empty>Loading…</Empty>;
  if (!m) return <Banner tone="info" title="Metrics unavailable">This run has no report.json and no trades.json.</Banner>;
  const stats: [string, string, ReturnType<typeof tone>, string?][] = [
    ['Trades', num(metric(m, 'trade_count'), 0), undefined, `${num(metric(m, 'session_count'), 0)} sessions`],
    ['Expectancy', signedR(metric(m, 'expectancy_r')), tone(metric(m, 'expectancy_r')), usd(metric(m, 'expectancy_usd'))],
    ['Win rate', pct(metric(m, 'primary_win_rate_pct')), undefined, `break-even ${pct(metric(m, 'break_even_win_rate_pct'))}`],
    ['Profit factor', num(metric(m, 'profit_factor')), undefined],
    ['Max drawdown', signedR(metric(m, 'max_drawdown_r') === undefined ? null : -Math.abs(Number(metric(m, 'max_drawdown_r')))), 'neg', usd(-Math.abs(Number(metric(m, 'max_drawdown_usd') ?? 0)))],
    ['Longest losing streak', num(metric(m, 'longest_losing_streak'), 0), undefined],
  ];
  return (
    <div className="stack">
      {m.low_sample === true && <Banner tone="warning" title="Low sample">Fewer trades than the minimum sample; treat the figures as indicative.</Banner>}
      <div className="stats">{stats.map(([l, v, t, s]) => <Stat key={l} label={l} value={v} tone={t} sub={s} />)}</div>
      <Panel title="Equity" subtitle="closed trades in exit order, drawdown shaded" icon="chart-line"
        actions={<Segmented options={[{ value: 'r', label: 'R' }, { value: 'usd', label: 'USD' }]} value={unit}
          onChange={(v: string) => setUnit(v as 'r' | 'usd')} />}>
        {equity.data ? <EquityChart equity={equity.data} unit={unit} /> : <Empty>{equity.isError ? UNAVAILABLE : 'Loading…'}</Empty>}
      </Panel>
      <div className="cols">
        <Panel title="R distribution" icon="chart-candlestick">
          {equity.data ? <Histogram bins={equity.data.histogram} /> : <Empty>{UNAVAILABLE}</Empty>}
        </Panel>
        <Panel title="All metrics" subtitle={`from ${metrics.data?.source}`} flush>
          <DataTable dense rows={Object.entries(m).map(([k, v]) => ({ k, v }))} rowKey="k" columns={[
            { key: 'k', label: 'Metric', muted: true },
            { key: 'v', label: 'Value', align: 'right', render: (v: unknown) =>
              isNotApplicable(v) ? 'not applicable' : typeof v === 'object' && v !== null
                ? Object.entries(v).map(([a, b]) => `${a} ${num(b)}`).join(' · ') : typeof v === 'boolean' ? String(v) : num(v, 2) },
          ]} />
        </Panel>
      </div>
    </div>
  );
}

function Trades({ run, shadow }: { run: string; shadow?: boolean }) {
  const [offset, setOffset] = useState(0);
  const data = useQuery({
    queryKey: ['trades', run, shadow, offset],
    queryFn: () => api.get<{ total: number; rows: any[] }>(`/api/runs/${encodeURIComponent(run)}/trades${qs({ offset, limit: 100, shadow })}`),
    retry: false,
  });
  if (data.isError) return <Empty>trades.json {UNAVAILABLE}</Empty>;
  const rows = (data.data?.rows ?? []).map((t, i) => ({ ...t, _k: offset + i }));
  return (
    <Panel flush footer={<div className="row row--between">
      <span>{data.data ? `${offset + 1}–${offset + rows.length} of ${data.data.total}` : ''}</span>
      <span className="row">
        <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</Button>
        <Button size="sm" disabled={!data.data || offset + 100 >= data.data.total} onClick={() => setOffset(offset + 100)}>Next</Button>
      </span></div>}>
      {rows.length === 0 ? <Empty>none</Empty> : (
        <DataTable dense rows={rows} rowKey="_k" maxHeight="60vh" columns={[
          { key: 'session', label: 'Session', width: 100, render: (_: unknown, t: any) => t.setup_key.session },
          { key: 'exit', label: 'Exit', width: 200, muted: true, render: (_: unknown, t: any) => etDateTime(fromNs(Math.max(...t.exits.map((f: any) => f.bar_open_ns)))) },
          { key: 'instrument', label: 'Instr', width: 60, render: (_: unknown, t: any) => t.setup_key.instrument },
          { key: 'side', label: 'Side', width: 70, render: (_: unknown, t: any) => <Badge side={t.setup_key.direction} /> },
          { key: 'pattern', label: 'Pattern', render: (_: unknown, t: any) => t.setup_key.pattern },
          { key: 'qty_at_entry', label: 'Qty', align: 'right', width: 50 },
          { key: 'r_multiple', label: 'R', align: 'right', width: 90, render: (v: string) => <span className={tone(v) === 'neg' ? 'text-bear' : ''}>{signedR(v)}</span> },
          { key: 'net', label: 'Net', align: 'right', width: 100, render: (v: string) => usd(v) },
          { key: 'mae_r', label: 'MAE R', align: 'right', width: 70, muted: true, render: (v: string) => num(v) },
          { key: 'mfe_r', label: 'MFE R', align: 'right', width: 70, muted: true, render: (v: string) => num(v) },
        ]} />
      )}
    </Panel>
  );
}

function FileView({ run, name }: { run: string; name: string }) {
  const file = useQuery({
    queryKey: ['file', run, name],
    queryFn: () => api.get<any>(`/api/runs/${encodeURIComponent(run)}/file${qs({ name, limit: 500 })}`),
    retry: false,
  });
  if (file.isLoading) return <Empty>Loading…</Empty>;
  if (file.isError || !file.data) return <Empty>{name} {UNAVAILABLE}</Empty>;
  const d = file.data;
  if (d.type === 'csv') {
    return (
      <Panel flush subtitle={`${d.total} rows`} title={name}>
        {d.rows.length === 0 ? <Empty>none</Empty> : <DataTable dense maxHeight="60vh"
          rows={d.rows.map((r: any, i: number) => ({ ...r, _k: i }))} rowKey="_k"
          columns={d.columns.map((c: string) => ({ key: c, label: c }))} />}
      </Panel>
    );
  }
  const text = d.type === 'json' ? JSON.stringify(d.data, null, 2) : d.type === 'lines' ? d.lines.join('\n') : d.text;
  return <Panel title={name} flush><CodeBlock code={text} plain={d.type !== 'json' && !name.endsWith('.yaml')} maxHeight="65vh" /></Panel>;
}

function GateFunnel({ run }: { run: string }) {
  const file = useQuery({ queryKey: ['file', run, 'gate_funnel.json'], queryFn: () => api.get<any>(`/api/runs/${encodeURIComponent(run)}/file?name=gate_funnel.json`), retry: false });
  const g = file.data?.data;
  if (file.isError) return <Empty>gate_funnel.json {UNAVAILABLE}</Empty>;
  if (!g) return <Empty>Loading…</Empty>;
  const gates: any[] = Array.isArray(g.gates) ? g.gates : [];
  const max = Math.max(1, ...gates.map((x) => Number(x.failing ?? 0)));
  return (
    <div className="stack">
      {g.accepted && <div className="stats">
        <Stat label="Accepted, filled" value={num(g.accepted.filled, 0)} />
        <Stat label="Win rate" value={pct(g.accepted.win_rate_pct)} />
        <Stat label="Mean R" value={signedR(g.accepted.mean_r)} tone={tone(g.accepted.mean_r)} />
        <Stat label="Not filled" value={num(g.accepted.not_filled, 0)} />
      </div>}
      <Panel title="Gate funnel" subtitle="setups each gate rejected" icon="filter">
        {gates.length === 0 ? <Empty>none</Empty> : (
          <div className="stack-sm">{gates.map((x) => (
            <MeterBar key={x.gate_id} label={x.gate_id} value={Number(x.failing ?? 0)} max={max} labelWidth={240}
              tone={x.flag ? 'warning' : 'ink'}
              display={`${num(x.failing, 0)} · only ${num(x.only_failing, 0)} · shadow ${signedR(x.only_rejected_shadows?.mean_r)}`} />
          ))}</div>
        )}
      </Panel>
      <FileView run={run} name="gate_funnel.json" />
    </div>
  );
}

export function RunPage() {
  const { run = '' } = useParams();
  const name = decodeURIComponent(run);
  const [tab, setTab] = useState('summary');
  const [file, setFile] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ['run', name], queryFn: () => api.get<RunDetail>(`/api/runs/${encodeURIComponent(name)}`), retry: false });
  const d = detail.data;
  const files = d?.files ?? [];
  const has = (n: string) => files.some((f) => f.name === n);
  // fse report writes report/report.md; an older run may have it at the root (root wins).
  const reportMd = ['report.md', 'report/report.md'].find(has);
  const tabs = [
    { value: 'summary', label: 'Summary' },
    ...(has('trades.json') ? [{ value: 'trades', label: 'Trades' }, { value: 'shadow', label: 'Shadow trades' }] : []),
    ...(has('gate_funnel.json') ? [{ value: 'funnel', label: 'Gate funnel' }] : []),
    ...(reportMd ? [{ value: 'report', label: 'Report' }] : []),
    { value: 'manifest', label: 'Manifest' },
    { value: 'files', label: 'Files', count: files.length },
  ];
  return (
    <div>
      <PageHead title={name} sub={d ? <>{d.kind} · {d.data_range ? `${d.data_range.start} → ${d.data_range.end}` : 'no data range'} · <Link to="/runs">all runs</Link></> : undefined}
        actions={d && !d.has_report && d.has_trades ? <Link to={`/commands/${encodeURIComponent('fse report')}`}><Button size="sm" icon="file-text">Write report</Button></Link> : undefined} />
      <div className="page">
        {detail.isError && <Banner tone="warning">No run {name}.</Banner>}
        {d && <>
          <div className="small muted">{d.disclaimer}</div>
          <Tabs items={tabs} value={tab} onChange={(v: string) => { setTab(v); setFile(null); }} />
          {tab === 'summary' && <Summary run={name} />}
          {tab === 'trades' && <Trades run={name} />}
          {tab === 'shadow' && <Trades run={name} shadow />}
          {tab === 'funnel' && <GateFunnel run={name} />}
          {tab === 'report' && reportMd && <FileView run={name} name={reportMd} />}
          {tab === 'manifest' && (d.manifest ? <Panel title="Run_Manifest"><KeyValue items={Object.entries(d.manifest).filter(([, v]) => typeof v !== 'object' || v === null).map(([k, v]) => [k, v === null ? 'none' : String(v)])} /><CodeBlock code={JSON.stringify(d.manifest, null, 2)} maxHeight="50vh" /></Panel> : <Empty>run_manifest.json {UNAVAILABLE}</Empty>)}
          {tab === 'files' && (
            <div className="split">
              <Panel flush><div className="filelist">{files.map((f) => (
                <button key={f.name} type="button" aria-current={file === f.name} onClick={() => setFile(f.name)}>
                  <span className="grow">{f.name}</span><span className="muted">{num(f.size / 1024, 1)} KB</span>
                </button>))}</div></Panel>
              {file ? <FileView run={name} name={file} /> : <Empty>Pick a file.</Empty>}
            </div>
          )}
        </>}
      </div>
    </div>
  );
}

export function ComparePage() {
  const [params] = useSearchParams();
  const runs = params.get('runs') ?? '';
  const data = useQuery({ queryKey: ['compare', runs], queryFn: () => api.get<{ runs: string[]; rows: any[] }>(`/api/compare${qs({ runs })}`), enabled: !!runs });
  return (
    <div>
      <PageHead title="Compare runs" sub="Deltas are against the first run." />
      <div className="page">
        {!data.data ? <Empty>Loading…</Empty> : (
          <Panel flush>
            <DataTable dense rows={data.data.rows} rowKey="metric" columns={[
              { key: 'metric', label: 'Metric', muted: true },
              ...data.data.runs.map((r, i) => ({
                key: `c${i}`, label: r, align: 'right' as const,
                render: (_: unknown, row: any) => {
                  const c = row.cells[i];
                  const v = isNotApplicable(c.value) ? 'not applicable' : typeof c.value === 'object' && c.value !== null ? DASH : num(c.value);
                  return i === 0 || c.delta === null ? v : `${v} (${Number(c.delta) > 0 ? '+' : ''}${num(c.delta)})`;
                },
              })),
            ]} />
          </Panel>
        )}
      </div>
    </div>
  );
}

export function ExperimentsPage() {
  return (
    <div>
      <PageHead title="Experiments" sub="Ablation, sweep, Monte Carlo, holdout, walk-forward, cadence and Skill drafts. Start one from Workflows."
        actions={<Link to="/workflows/experiment"><Button variant="primary" icon="flask-conical">New experiment</Button></Link>} />
      <div className="page"><RunsTable kinds={EXPERIMENT_KINDS} /></div>
    </div>
  );
}
