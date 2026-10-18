import { useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge, Banner, Button, CodeBlock, DataTable, KeyValue, Panel } from '../ds/index.js';
import { api } from '../lib/api';
import { DASH, MINUS, etTime, fromNs, money, num, toNumber, usd } from '../lib/format';
import type {
  AccountStatus, EffectiveOrderMode, LivePlugin, OrderMode, OrderModePlugin, PaperAccount, PaperPlugin, PluginPhase, PluginPnl, PluginStatus, PositionPnl,
} from '../lib/types';

/** Phase -> badge and short label. Every plugin gets one from the host (status.json). */
export const PHASE: Record<PluginPhase, { label: string; variant: 'bull' | 'bear' | 'neutral' | 'warning' | 'accent-soft' | 'danger' | 'info' }> = {
  in_trade: { label: 'In a trade', variant: 'bull' },
  working: { label: 'Order working', variant: 'accent-soft' },
  waiting: { label: 'Waiting', variant: 'neutral' },
  blocked: { label: 'Entries blocked', variant: 'warning' },
  faulted: { label: 'Stopped (error)', variant: 'danger' },
  closed: { label: 'Market closed', variant: 'neutral' },
};

const SCHEDULE_BADGE: Record<string, 'bull' | 'neutral' | 'outline' | 'warning' | 'accent-soft'> = {
  entered: 'bull', done: 'neutral', skipped: 'warning', planned: 'outline',
};

/** "in 23 min", "in 2 h 5 min", "5 min ago" relative to now (ns). */
export function relTime(atNs: number | null | undefined, nowNs: number): string {
  if (!atNs) return '';
  const min = Math.round((atNs - nowNs) / 60e9);
  const abs = Math.abs(min);
  const text = abs < 1 ? 'less than a minute' : abs < 60 ? `${abs} min` : `${Math.floor(abs / 60)} h${abs % 60 ? ` ${abs % 60} min` : ''}`;
  if (abs < 1) return min >= 0 ? 'now' : 'just now';
  return min >= 0 ? `in ${text}` : `${text} ago`;
}

/** Ticking clock (ns) so countdowns and mark ages move between snapshots. */
export function useNow(seedNs?: number, intervalMs = 15_000): number {
  const [now, setNow] = useState(() => seedNs ?? Date.now() * 1e6);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() * 1e6), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** A quote mark is stale after 10 s, a 1-minute close after 120 s (as the API rules). */
export const QUOTE_STALE_S = 10;
export const BAR_STALE_S = 120;

/** Age (s) and stale state of a position's mark at `nowNs`; computed here because the
 *  live WebSocket only sends when something changed, not every second. */
export function markAge(p: PositionPnl | undefined, nowNs: number): { age: number | null; stale: boolean } {
  if (!p || p.mark_received_ns === null || p.mark_source === null) return { age: null, stale: true };
  const age = Math.max(0, (nowNs - p.mark_received_ns) / 1e9);
  return { age, stale: age > (p.mark_source === 'quote' ? QUOTE_STALE_S : BAR_STALE_S) };
}

/** "$1,234.50" / "−$12.48": dollars with a true minus and no plus sign. */
function dollars(v: string | null | undefined): string {
  const n = toNumber(v);
  if (n === null) return DASH;
  return `${n < 0 ? MINUS : ''}$${num(Math.abs(n))}`;
}

function pnlClass(v: string | null | undefined): string {
  const n = toNumber(v);
  return n === null || n === 0 ? '' : n > 0 ? 'text-bull' : 'text-bear';
}

const ACCOUNT_BADGE: Record<AccountStatus, 'bull' | 'warning' | 'danger' | 'accent'> = {
  active: 'bull', flat_locked: 'warning', breached: 'danger', passed: 'accent',
};

export function AccountStatusBadge({ status }: { status: AccountStatus | null | undefined }) {
  if (!status) return <Badge variant="outline">unknown</Badge>;
  return <Badge variant={ACCOUNT_BADGE[status] ?? 'outline'}>{status.replace('_', ' ')}</Badge>;
}

/** The plugin's paper account on its Live card: which account, its state, balance and the
 *  contracts in use (/api/ws/live ``paper`` and ``accounts``). The P&L stays on the Today line. */
function PaperAccountRows({ paper, account }: { paper: PaperPlugin; account: PaperAccount | undefined }) {
  // An unreadable book has no numbers: the API's "0.00" defaults are not a flat day.
  const bad = !!paper.error;
  return (
    <section aria-label="Paper account" className="stack-sm">
      {paper.error && <Banner tone="warning" title="Book unreadable">{paper.error}</Banner>}
      <KeyValue items={[
        ['Account', <span className="row">{paper.account}{bad && account?.implicit ? null : <AccountStatusBadge status={account?.status} />}</span>],
        ['Balance', bad && account?.implicit ? DASH : money(account?.balance, { signed: false })],
        ['Contracts', !bad && paper.caps ? `${paper.caps.used} of ${paper.caps.max_contracts}` : DASH],
      ]} />
    </section>
  );
}

/** The order-mode badge: what the runner uses for this plugin's orders now.
 *  ``configured`` is the selected runner config's order_mode (null when unknown);
 *  ``effective`` is the runner's last order_mode log line in this run. */
export function orderModeBadge(configured: OrderMode | null | undefined, effective: EffectiveOrderMode | null | undefined):
  { text: string; variant: 'danger-solid' | 'warning' | 'accent-soft' | 'neutral' } {
  if (effective?.mode === 'combine') return { text: 'Combine in use', variant: 'danger-solid' };
  if (effective?.mode === 'practice') return { text: 'Practice in use', variant: 'danger-solid' };
  if (effective && (effective.requested === 'practice' || effective.requested === 'combine')
    && effective.mode === 'paper' && configured !== 'paper') {
    const why = effective.failures.length ? effective.failures.join('; ') : 'no reason logged';
    const want = effective.requested === 'combine' ? 'combine' : 'practice';
    return { text: `Requested ${want}, running Paper: ${why}`, variant: 'warning' };
  }
  if (configured === 'combine') return { text: 'Requested combine, waiting for the runner', variant: 'accent-soft' };
  if (configured === 'practice') return { text: 'Requested practice, waiting for the runner', variant: 'accent-soft' };
  return { text: 'Paper', variant: 'neutral' };
}

/** Rows with a time first, newest first; rows without one keep their order, last. */
export function newestFirst<T>(rows: readonly T[], at: (r: T) => number | null | undefined): T[] {
  const timed = rows.filter((r) => typeof at(r) === 'number');
  const rest = rows.filter((r) => typeof at(r) !== 'number');
  return [...timed.sort((a, b) => (at(b) as number) - (at(a) as number)), ...rest];
}

/** "closed $X + open $Y = $Z · N entries · M closed". */
function TodayLine({ today, pnl, open: hasOpen }: {
  today: NonNullable<PluginStatus['today']>; pnl: PluginPnl | null | undefined; open: boolean;
}) {
  const closed = pnl?.day.closed ?? today.net;
  // Without live P&L, an open position has no value yet: show a dash, not $0.00.
  const open = pnl ? pnl.day.open : hasOpen ? null : '0';
  const total = pnl ? pnl.day.total : hasOpen ? null : today.net;
  const entries = pnl?.day.entries ?? today.entries ?? today.trades;
  const closedN = pnl?.day.closed_trades ?? today.closed ?? null;
  return (
    <span data-testid="today-line">
      closed <span className={pnlClass(closed)}>{dollars(closed)}</span>
      {' + '}open <span className={pnlClass(open)}>{dollars(open)}</span>
      {' = '}<strong className={pnlClass(total)}>{dollars(total)}</strong>
      <span className="muted"> · {entries} entr{entries === 1 ? 'y' : 'ies'}{closedN !== null ? ` · ${closedN} closed` : ''}</span>
    </span>
  );
}

/** Open positions with their live mark and open P&L; ages the marks on a 1 s clock. */
function Positions({ positions, pnl, seedNs }: {
  positions: PluginStatus['positions']; pnl: PluginPnl | null | undefined; seedNs: number;
}) {
  const nowNs = useNow(seedNs, 1_000);
  const rows = newestFirst(positions, (p) => p.opened_ns);
  return (
    <DataTable dense rows={rows} rowKey="tag" columns={[
      { key: 'side', label: 'Side', width: 70, render: (v: string) => <Badge side={v as 'long' | 'short'}>{v}</Badge> },
      { key: 'qty', label: 'Qty', width: 50 },
      { key: 'instrument', label: 'Contract', width: 80 },
      { key: 'entry', label: 'Entry', render: pts },
      { key: '_pnl', label: 'Open P&L', render: (_: unknown, row: PluginStatus['positions'][number]) => {
        const p = pnl?.positions[row.tag];
        if (!p || p.open_net === null) return DASH;
        return (
          <span className={pnlClass(p.open_net)} data-testid={`open-pnl-${row.tag}`}>
            {usd(p.open_net)}{p.ticks !== null && <span className="small"> ({p.ticks > 0 ? '+' : p.ticks < 0 ? MINUS : ''}{Math.abs(p.ticks)} t)</span>}
          </span>
        );
      } },
      { key: 'tag', label: 'Mark', render: (tag: string) => {
        const p = pnl?.positions[tag];
        const { stale } = markAge(p, nowNs);
        if (!p || p.mark === null) return <span className="muted">no mark yet</span>;
        return (
          <>
            <div>{pts(p.mark)}</div>
            <div className="small muted">
              as of {etTime(fromNs(p.mark_received_ns))}
              {p.mark_source === '1m_close' && <> <Badge variant="outline">last 1m close</Badge></>}
              {stale && <> <Badge variant="warning">stale</Badge></>}
            </div>
          </>
        );
      } },
      { key: 'stop', label: 'Stop', render: pts },
      { key: 'targets', label: 'Target', render: (v: number[]) => (v.length ? v.map(pts).join(' / ') : DASH) },
    ]} />
  );
}

function pts(v: number | null | undefined): string {
  return v === null || v === undefined ? DASH : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Details({ id, log }: { id: string; log: LivePlugin['log'] }) {
  const raw = useQuery({ queryKey: ['plugin-raw', id], queryFn: () => api.get<Record<string, any>>(`/api/live/plugins/${id}/raw`) });
  return (
    <div className="stack-sm">
      <div className="micro">plugin log (newest first)</div>
      {log.length === 0 ? <span className="small muted">no log lines in this run</span> : (
        <DataTable dense maxHeight={220} rows={[...log].reverse().slice(0, 50).map((e, i) => ({ ...e, _k: i }))} rowKey="_k" columns={[
          { key: 't_ns', label: 'Time', muted: true, width: 90, render: (v: number) => (v ? etTime(fromNs(v)) : DASH) },
          { key: 'say', label: 'What happened', render: (v: any, e: any) => <span className={v?.severity === 'error' ? 'text-bear' : v?.severity === 'warning' ? 'text-warning' : ''}>{v?.text ?? e.kind}</span> },
        ]} />
      )}
      <div className="micro">raw saved state (plugin.json and status.json)</div>
      {raw.isLoading ? <span className="small muted">Loading…</span>
        : raw.isError ? <span className="small muted">no saved state yet</span>
          : <CodeBlock code={JSON.stringify(raw.data, null, 2)} maxHeight={320} />}
    </div>
  );
}

export function PluginStatusCard({ plugin, nowNs, blocked, paper, accounts, orderMode, onOrderMode, onHalt, onClear, onResetDayLoss }: {
  plugin: LivePlugin; nowNs: number; blocked: string | null;
  /** Today of a host-booked plugin and every account (paper analytics); absent otherwise. */
  paper?: PaperPlugin; accounts?: Record<string, PaperAccount>;
  /** This plugin's entry in GET /api/order-modes (broker plugins only). */
  orderMode?: OrderModePlugin;
  /** Opens the Live page's order-mode dialog for this plugin. */
  onOrderMode?: () => void;
  onHalt: () => void; onClear: () => void;
  /** Zero today's paper day_net for sizing (host-booked plugins with a max_daily_loss). */
  onResetDayLoss?: () => void;
}) {
  const showOrderMode = !!orderMode || !!plugin.order_mode;
  const om = orderModeBadge(orderMode?.order_mode ?? null, plugin.order_mode);
  const [open, setOpen] = useState(false);
  const s = plugin.status;
  const ps = s?.plugin_status ?? null;
  const phase: PluginPhase = plugin.faulted ? 'faulted' : s?.phase ?? 'waiting';
  const badge = PHASE[phase];
  const next = ps?.next ?? (s?.next_wake_ns ? { at_ns: s.next_wake_ns, text: 'Next scheduled check' } : null);
  const last = ps?.last ?? (s?.recent.length ? { at_ns: s.recent[s.recent.length - 1].at_ns, text: s.recent[s.recent.length - 1].text } : null);
  const schedule = newestFirst(ps?.schedule ?? [], (r) => r.at_ns);
  const working = s ? newestFirst(s.working, (w) => w.placed_ns) : [];
  return (
    <Panel title={s?.label ?? plugin.id} subtitle={s ? `${plugin.id} · ${s.brokerage === 'self' ? 'books its own orders' : 'paper book'}` : plugin.id}
      icon="puzzle"
      actions={<>
        <Badge variant={badge.variant} dot={phase === 'in_trade' || phase === 'working'}>{badge.label}</Badge>
        {/* Keep Halt while blocked: Clear alone is easy to confuse with flatten, and Halt
            still cancels resting entries. Neither button closes a ProjectX position. */}
        {blocked && <Button size="sm" icon="rotate-ccw" onClick={onClear}>Clear</Button>}
        {onResetDayLoss && (
          <Button size="sm" variant="ghost" icon="rotate-ccw" onClick={onResetDayLoss}
            title="Zero today's paper day net so sizing uses the full max_daily_loss again">
            Reset day loss
          </Button>
        )}
        <Button size="sm" variant="danger-outline" icon="octagon-x" onClick={onHalt}>Halt</Button>
      </>}>
      <div className="stack-sm plugin-status">
        {blocked && <Banner tone="alarm" title="Entries blocked">{blocked}</Banner>}
        {blocked && (phase === 'in_trade' || (s?.positions.length ?? 0) > 0) && (
          <Banner tone="warning" title="Open position is not closed by Halt or Clear">
            Flatten in TopstepX (Combine/Practice). Clear only lifts the entry block; if the book
            still expects a working order the broker does not have, reconciliation blocks again
            on the next decision.
          </Banner>
        )}
        {showOrderMode && (
          <div className="row" aria-label="Order mode">
            <span className="micro">order mode</span>
            <span data-testid={`order-mode-${plugin.id}`}><Badge variant={om.variant}>{om.text}</Badge></span>
            {orderMode && onOrderMode && <Button size="sm" variant="ghost" icon="sliders-horizontal" onClick={onOrderMode}>Change…</Button>}
          </div>
        )}
        {paper && <PaperAccountRows paper={paper} account={accounts?.[paper.account]} />}
        {!s ? <span className="small muted">No status yet. It appears once the runner has started this plugin.</span> : <>
          <div className="plugin-summary">{ps?.summary ?? s.phase_text}</div>
          <KeyValue items={([
            ['Now', ps?.now ?? s.phase_text],
            ['Last', last ? <>{last.text}{last.at_ns ? <span className="muted"> · {etTime(fromNs(last.at_ns))}</span> : null}</> : DASH],
            ['Next', next ? <>{next.text}{next.at_ns ? <span className="muted"> · {etTime(fromNs(next.at_ns))} ({relTime(next.at_ns, nowNs)})</span> : null}</> : DASH],
            ['Today', s.today ? <TodayLine today={s.today} pnl={plugin.pnl} open={s.positions.length > 0} /> : 'not booked by the host (no host positions)'],
          ] as [string, ReactNode][]).concat(
            paper?.caps?.max_daily_loss != null
              ? [['Daily loss budget', (
                <span data-testid={`day-loss-${plugin.id}`}>
                  {money(paper.caps.day_net)} of −{money(paper.caps.max_daily_loss, { signed: false })}
                </span>
              )]]
              : [],
          )} />
          {s.positions.length > 0 && <>
            <div className="micro">open positions (newest first)</div>
            <Positions positions={s.positions} pnl={plugin.pnl} seedNs={nowNs} />
          </>}
          {working.length > 0 && <>
            <div className="micro">working orders</div>
            <DataTable dense rows={working.map((w, i) => ({ ...w, _k: i }))} rowKey="_k" columns={[
              { key: 'side', label: 'Side', width: 60 },
              { key: 'qty', label: 'Qty', width: 50 },
              { key: 'instrument', label: 'Contract', width: 80 },
              { key: 'role', label: 'Role', width: 70 },
              { key: 'kind', label: 'Type', width: 70 },
              { key: 'price', label: 'Price', render: (v: number | null) => (v === null ? 'market' : pts(v)) },
            ]} />
          </>}
          {(ps?.facts?.length ?? 0) > 0 && <KeyValue items={ps!.facts!.map(([k, v]) => [k, v])} />}
          {schedule.length > 0 && <>
            <div className="micro">today&apos;s plan (newest first)</div>
            <ul className="plugin-schedule" aria-label={`${s.label} schedule`}>
              {schedule.map((r, i) => (
                <li key={i} data-state={r.state}>
                  <Badge variant={SCHEDULE_BADGE[r.state] ?? 'outline'}>{r.state}</Badge>
                  <span>{r.label}</span>
                </li>
              ))}
            </ul>
          </>}
          {s.recent.length > 0 && <>
            <div className="micro">recent orders</div>
            <ul className="plugin-recent">
              {[...s.recent].reverse().slice(0, 6).map((r, i) => (
                <li key={i}><span className="muted">{etTime(fromNs(r.at_ns))}</span> {r.text}</li>
              ))}
            </ul>
          </>}
        </>}
        <div className="row">
          <Button size="sm" variant="ghost" icon={open ? 'chevron-up' : 'chevron-down'} onClick={() => setOpen(!open)}>
            {open ? 'Hide details' : 'Details'}
          </Button>
          {s && <span className="small muted">updated {etTime(fromNs(s.updated_ns))}</span>}
        </div>
        {open && <Details id={plugin.id} log={plugin.log} />}
      </div>
    </Panel>
  );
}
