// Daily paper-check reports (JSON + Markdown files under <data dir>/daily/, written by your
// own check script): a day picker over GET /api/health/daily and the selected report. Health and Analytics both render it.
import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge, Banner, CodeBlock, DataTable, KeyValue, Panel } from '../ds/index.js';
import { Empty } from './PageHead';
import { api } from '../lib/api';
import { DASH, money } from '../lib/format';
import { centsText, winRate } from '../lib/paper';
import type { DailyList } from '../lib/types';

export const CUMULATIVE_TITLE = 'Cumulative since forward-test start';
/** A check script's exit codes and its verdict words. */
const VERDICT: Record<number, string> = { 0: 'OK', 1: 'MISMATCH OR FAULT', 2: 'MISSING DATA' };
const STATUS_BADGE: Record<string, 'bull' | 'warning' | 'bear' | 'outline' | 'neutral'> = {
  OK: 'bull', SKIPPED: 'neutral', WARN: 'warning', MISSING: 'bear', FAIL: 'bear',
};

function StatusBadge({ status }: { status: unknown }) {
  const s = typeof status === 'string' ? status : 'unknown';
  return <Badge variant={STATUS_BADGE[s] ?? 'outline'}>{s}</Badge>;
}

const cents = (v: unknown) => money(centsText(typeof v === 'number' ? v : null));

/** The F section from its JSON: per plugin, per account and the go/no-go line. */
function Cumulative({ sec }: { sec: Record<string, any> }) {
  const plugins = Object.entries((sec.plugins ?? {}) as Record<string, any>).map(([id, p]) => ({ id, ...p }));
  const accounts = Object.entries((sec.accounts ?? {}) as Record<string, any>).map(([id, a]) => ({ id, ...a }));
  const go = sec.go_no_go ?? {};
  return (
    <section aria-label={CUMULATIVE_TITLE} className="stack-sm">
      <div className="row">
        <strong>{CUMULATIVE_TITLE}</strong>
        <StatusBadge status={sec.status} />
        <span className="small muted">since {sec.since ?? DASH}{sec.so_far ? ' (so far)' : ''}</span>
      </div>
      {plugins.length === 0 ? <span className="small muted">No plugin in the config or the run dirs.</span> : (
        <DataTable dense rows={plugins} rowKey="id" columns={[
          { key: 'id', label: 'Plugin' },
          { key: 'net_cents', label: 'Net', align: 'right', render: cents },
          { key: 'trades', label: 'Trades', align: 'right' },
          { key: 'win_rate', label: 'Win rate (net > 0)', align: 'right', render: (v: number | null) => winRate(v) },
          { key: 'max_dd_cents', label: 'Max drawdown', align: 'right', render: (v: number) => money(centsText(v), { signed: false }) },
          { key: 'sources', label: 'Days from journal / csv', align: 'right',
            render: (v: { journal?: number; csv?: number } | undefined) => `${v?.journal ?? 0} / ${v?.csv ?? 0}` },
        ]} />
      )}
      {accounts.length > 0 && (
        <DataTable dense rows={accounts} rowKey="id" columns={[
          { key: 'id', label: 'Account' },
          { key: 'status', label: 'Status', render: (v: string | null, a: any) => (a.started === false ? 'not started' : v ?? DASH) },
          { key: 'balance_cents', label: 'Balance', align: 'right', render: (v: number | null) => money(centsText(v), { signed: false }) },
          { key: 'ledger_net_cents', label: 'Ledger net', align: 'right', render: cents },
          { key: 'trades', label: 'Trades', align: 'right', render: (v: number | undefined) => v ?? DASH },
          { key: 'win_rate', label: 'Win rate (net > 0)', align: 'right', render: (v: number | null) => winRate(v) },
          { key: 'plugins', label: 'Plugins', render: (v: string[] | undefined) => (v?.length ? v.join(', ') : DASH) },
        ]} />
      )}
      <div className="small">
        Go/no-go: cumulative net {cents(go.net_cents)}, line {cents(go.line_cents)}: <StatusBadge status={go.status} />
      </div>
    </section>
  );
}

function Report({ day, data }: { day: string; data: { json: any; markdown: string | null } }) {
  const json = data.json && typeof data.json === 'object' ? data.json : null;
  const sections = Object.entries((json?.sections ?? {}) as Record<string, any>);
  const cumulative = json?.sections?.[CUMULATIVE_TITLE];
  const partial = json?.partial === true || day.endsWith('.partial');
  return (
    <Panel title={day} icon="file-text" actions={partial ? <Badge variant="warning">PARTIAL</Badge> : undefined}>
      <div className="stack">
        {json && (
          <KeyValue items={[
            ['Overall', `${VERDICT[json.exit as number] ?? 'unknown'} (exit ${json.exit ?? DASH})`],
            ...sections.map(([title, s]) => [title, <StatusBadge status={s?.status} />] as [string, ReactNode]),
          ]} />
        )}
        {cumulative && typeof cumulative === 'object' && <Cumulative sec={cumulative} />}
        {data.markdown ? <CodeBlock plain code={data.markdown} maxHeight="65vh" />
          : <CodeBlock code={JSON.stringify(data.json, null, 2)} maxHeight="65vh" />}
      </div>
    </Panel>
  );
}

/** The day list and the selected report. `emptyBanner`: say so in a banner when there are none. */
export function DailyCheck({ emptyBanner = false }: { emptyBanner?: boolean }) {
  const [day, setDay] = useState<string | null>(null);
  const list = useQuery({ queryKey: ['daily'], queryFn: () => api.get<DailyList>('/api/health/daily') });
  const one = useQuery({ queryKey: ['daily', day], queryFn: () => api.get<any>(`/api/health/daily/${day}`), enabled: !!day });
  const reports = Array.isArray(list.data?.reports) ? list.data.reports : [];
  return (
    <div className="stack">
      {emptyBanner && list.isSuccess && reports.length === 0 && (
        <Banner tone="info">No daily checks yet.</Banner>
      )}
      <div className="split">
        <Panel title="Daily checks" flush>
          {reports.length ? <div className="filelist">{reports.map((r) => (
            <button key={r.day} type="button" aria-current={day === r.day} onClick={() => setDay(r.day)}>
              <span className="grow">{r.day.replace(/\.partial$/, '')}</span>
              {(r.partial ?? r.day.endsWith('.partial')) && <Badge variant="warning">PARTIAL</Badge>}
            </button>
          ))}</div> : <Empty>none</Empty>}
        </Panel>
        {!day ? <Empty>Pick a day.</Empty> : !one.data ? <Empty>Loading…</Empty> : <Report day={day} data={one.data} />}
      </div>
    </div>
  );
}
