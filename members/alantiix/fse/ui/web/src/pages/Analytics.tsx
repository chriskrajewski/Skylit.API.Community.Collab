// Paper analytics (design §7.2): today from the live snapshot, history from /api/paper/*,
// and the daily check. Money arrives as decimal text and is shown as text (NF-1).
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Banner, Button, DataTable, Input, KeyValue, Panel, Select, Sparkline, Stat, StatusDot } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { DailyCheck } from '../components/DailyCheck';
import { EquityChart } from '../components/Charts';
import { useToast } from '../components/Toasts';
import { ApiError, api, errorText, qs } from '../lib/api';
import { useLive, useNow } from '../lib/hooks';
import { brokerRunning } from '../lib/paperLabel';
import { accountStatusText, moneyDiff, orderAccounts, tradeRow, tradesSubtitle, winRate, type TradeRow } from '../lib/paper';
import { DASH, etDateTime, etTime, fromNs, money, num } from '../lib/format';
import type { AccountStatus, Equity, JournalTrade, PaperAccount, PaperPlugin, PaperTotal } from '../lib/types';

/** `total`, `plugin:<id>` or `account:<id>` (account ids may be `implicit:<plugin>`). */
type ScopeKey = string;

function scopeBase(scope: ScopeKey): string {
  if (scope === 'total') return '/api/paper/total';
  const i = scope.indexOf(':');
  const kind = scope.slice(0, i);
  const id = encodeURIComponent(scope.slice(i + 1));
  return kind === 'plugin' ? `/api/paper/plugins/${id}` : `/api/paper/accounts/${id}`;
}

interface TradesResponse { trades: JournalTrade[]; total: number; truncated: boolean; skipped_lines: number }
type EquityResponse = Equity & { skipped_lines?: number };

function useEquity(scope: ScopeKey, from = '', to = '') {
  return useQuery({
    queryKey: ['paper-equity', scope, from, to],
    queryFn: () => api.get<EquityResponse>(`${scopeBase(scope)}/equity${qs({ from, to })}`),
    refetchInterval: 60_000,
  });
}

const negative = (v: string | null | undefined) => typeof v === 'string' && /^-/.test(v) && !/^-0*(\.0*)?$/.test(v);
const moneyTone = (v: string | null | undefined): 'pos' | 'neg' | undefined =>
  negative(v) ? 'neg' : typeof v === 'string' && /[1-9]/.test(v) ? 'pos' : undefined;

const ACCOUNT_BADGE: Record<AccountStatus, 'bull' | 'warning' | 'danger' | 'accent'> = {
  active: 'bull', flat_locked: 'warning', breached: 'danger', passed: 'accent',
};

function AccountBadge({ account }: { account: PaperAccount | undefined }) {
  const status = account?.status;
  return <Badge variant={status ? ACCOUNT_BADGE[status] ?? 'outline' : 'outline'}>
    {accountStatusText(status, account?.breach_reason)}
  </Badge>;
}

/** The last 30 cumulative-dollar points of a scope. */
function EquitySpark({ scope }: { scope: ScopeKey }) {
  const eq = useEquity(scope);
  const points = Array.isArray(eq.data?.points) ? eq.data.points.slice(-30) : [];
  if (points.length < 2) return <span className="small muted">No equity history yet.</span>;
  return <Sparkline responsive fill tone="auto" values={points.map((p) => Number(p.cum_usd))} />;
}

const JOURNAL_CUMULATIVE_SUB = 'journal trades since 2026-10-12';
/** A named account's cumulative is its own ledger (`realized`), which restarts at 0 on a reset. */
const LEDGER_CUMULATIVE_SUB = 'since the account started or was reset';

function TodayStats({ net, open, cumulative, cumulativeSub = JOURNAL_CUMULATIVE_SUB, trades, rate }: {
  net: string | null; open: string | null; cumulative: string | null; cumulativeSub?: string;
  trades: number | null; rate: number | null;
}) {
  return (
    <div className="stats">
      <Stat size="sm" label="Today net" value={money(net)} tone={moneyTone(net)} />
      <Stat size="sm" label="Open P&L" value={money(open)} tone={moneyTone(open)} />
      <Stat size="sm" label="Cumulative net" value={money(cumulative)} tone={moneyTone(cumulative)}
        sub={cumulativeSub} />
      <Stat size="sm" label="Trades today" value={trades === null ? DASH : num(trades, 0)} />
      <Stat size="sm" label="Win rate (net > 0)" value={winRate(rate)} />
    </div>
  );
}

function TotalCard({ total }: { total: PaperTotal }) {
  return (
    <Panel title="Total" subtitle="every plugin" icon="layers">
      <div className="stack-sm">
        <TodayStats net={total.realized} open={total.unrealized} cumulative={total.cumulative_net}
          trades={total.day_trades} rate={total.win_rate_today} />
        <EquitySpark scope="total" />
      </div>
    </Panel>
  );
}

function AccountCard({ account, members, onReset }: {
  account: PaperAccount; members: PaperPlugin[]; onReset: (id: string) => void;
}) {
  const trades = members.reduce((n, p) => n + p.day_trades, 0);
  const wins = members.reduce((n, p) => n + Math.round((p.win_rate_today ?? 0) * p.day_trades), 0);
  const d = account.view?.distance ?? {};
  const limits: [string, string][] = account.implicit ? [] : [
    ['MLL', d.mll === null || d.mll === undefined ? 'off' : `${money(String(d.mll), { signed: false })} above the floor ${money(account.mll_floor, { signed: false })}`],
    ['DLL', d.dll === null || d.dll === undefined ? 'off' : `${money(String(d.dll), { signed: false })} left today`],
    ['Profit target', d.profit_target === null || d.profit_target === undefined ? 'off' : `${money(String(d.profit_target), { signed: false })} to go`],
    ['Position', d.position === null || d.position === undefined ? 'off' : `${num(d.position, 0)} micros free`],
    ['Flat by', typeof d.flat_by_ns === 'number' ? etTime(fromNs(d.flat_by_ns)) : 'off'],
  ];
  return (
    <Panel title={account.id} subtitle={account.implicit ? 'implicit, unrestricted' : `preset ${account.preset ?? DASH}`}
      icon="shield-check"
      actions={<>
        <AccountBadge account={account} />
        {!account.implicit && account.status === 'breached' && (
          <Button size="sm" variant="danger-outline" icon="rotate-ccw" onClick={() => onReset(account.id)}>Reset…</Button>
        )}
      </>}>
      <div className="stack-sm">
        {account.error && <Banner tone="warning" title="Account unreadable">{account.error}</Banner>}
        <TodayStats net={account.day_net} open={account.unrealized}
          cumulative={account.implicit ? account.balance : account.realized}
          cumulativeSub={account.implicit ? JOURNAL_CUMULATIVE_SUB : LEDGER_CUMULATIVE_SUB}
          trades={trades} rate={trades ? wins / trades : null} />
        <KeyValue columns={2} items={[
          ['Balance', money(account.balance, { signed: false })],
          ['Equity', money(account.equity, { signed: false })],
          ['Drawdown', money(account.drawdown, { signed: false })],
          ['Plugins', account.plugins.length ? account.plugins.join(', ') : 'none'],
        ]} />
        {limits.length > 0 && (
          <section aria-label="Distance to limits" className="acct-limits">
            <div className="small muted">Distance to limits</div>
            <KeyValue items={limits} />
          </section>
        )}
        <EquitySpark scope={`account:${account.id}`} />
      </div>
    </Panel>
  );
}

function PluginCard({ plugin, account }: { plugin: PaperPlugin; account: PaperAccount | undefined }) {
  const bad = !!plugin.error;
  const caps = plugin.caps;
  return (
    <Panel title={plugin.id} subtitle={`account ${plugin.account}`} icon="puzzle"
      actions={<AccountBadge account={account} />}>
      <div className="stack-sm">
        {plugin.error && <Banner tone="warning" title="Book unreadable">{plugin.error}</Banner>}
        <TodayStats net={bad ? null : plugin.realized} open={bad ? null : plugin.unrealized}
          cumulative={plugin.cumulative_net} trades={plugin.day_trades} rate={plugin.win_rate_today} />
        <KeyValue columns={2} items={[
          ['Contracts', !bad && caps ? `${caps.used} of ${caps.max_contracts}` : DASH],
          ['Daily loss', !bad && caps?.max_daily_loss ? `${money(caps.day_net)} of −${money(caps.max_daily_loss, { signed: false })}` : DASH],
          ['Open positions', bad ? DASH : num(plugin.open_positions.length, 0)],
          ['Blocks', plugin.blocks.length ? plugin.blocks.join(' · ') : 'none'],
        ]} />
        <EquitySpark scope={`plugin:${plugin.id}`} />
      </div>
    </Panel>
  );
}

function NexusBlock({ account }: { account: PaperAccount }) {
  const n = account.nexus ?? {};
  const gap = moneyDiff(n.local_net, n.nexus_net);
  const state = n.error ? 'unreadable' : n.ended ? 'ended' : n.disabled ? `off: ${n.disabled_reason ?? 'disabled'}` : 'on';
  return (
    <Panel title={`Nexus mirror · ${account.id}`} subtitle="Skylit Nexus paper trading" icon="arrow-right-left">
      {n.error ? <Banner tone="warning">{n.error}</Banner> : (
        <KeyValue columns={2} items={[
          ['Mirror', state],
          ['Nexus account', n.nexus_account ?? DASH],
          ['Test orders only', n.test_only ? 'yes' : 'no'],
          ['Queue depth', num(n.queue_depth, 0)],
          ['Last success', typeof n.last_success_ns === 'number' ? etDateTime(fromNs(n.last_success_ns)) : DASH],
          ['Last error', n.last_error ? `${n.last_error.code ?? 'error'} at ${etDateTime(fromNs(n.last_error.t_ns))}` : 'none'],
          ['Local net', money(n.local_net)],
          ['Nexus net', money(n.nexus_net)],
          ['Gap (local − Nexus)', money(gap)],
          ['Opens blocked', n.opens_blocked ? 'yes' : 'no'],
        ]} />
      )}
    </Panel>
  );
}

function EquitySection({ scopes }: { scopes: { value: ScopeKey; label: string }[] }) {
  const [scope, setScope] = useState<ScopeKey>('total');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const eq = useEquity(scope, from, to);
  return (
    <Panel title="Equity" subtitle="closed journal trades, cumulative dollars" icon="chart-line">
      <div className="stack">
        <div className="form-grid">
          <Select label="Scope" options={scopes} value={scope} onChange={(e) => setScope(e.target.value)} />
          <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        {eq.isError ? <Banner tone="warning">{errorText(eq.error)}</Banner>
          : !eq.data ? <Empty>Loading…</Empty>
          : !Array.isArray(eq.data.points) ? <Empty>No closed trades.</Empty>
          : <EquityChart equity={eq.data} unit="usd" />}
      </div>
    </Panel>
  );
}

function TradesSection({ plugins, accounts }: { plugins: string[]; accounts: string[] }) {
  const [plugin, setPlugin] = useState('');
  const [account, setAccount] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const base = plugin ? scopeBase(`plugin:${plugin}`) : account ? scopeBase(`account:${account}`) : scopeBase('total');
  const trades = useQuery({
    queryKey: ['paper-trades', base, from, to],
    queryFn: () => api.get<TradesResponse>(`${base}/trades${qs({ from, to, limit: 500 })}`),
    refetchInterval: 60_000,
  });
  const list = Array.isArray(trades.data?.trades) ? trades.data.trades : [];
  // plugin and account both set: the plugin's route, narrowed to the account here
  const rows = list.filter((t) => !plugin || !account || (t.account ?? `implicit:${t.plugin}`) === account).map(tradeRow);
  return (
    <Panel title="Trades" icon="list" subtitle={tradesSubtitle({
      fetched: list.length, shown: rows.length, total: trades.data?.total ?? list.length,
      truncated: !!trades.data?.truncated, narrowedFrom: plugin && account ? plugin : null,
    })}>
      <div className="stack">
        <div className="form-grid">
          <Select label="Plugin" options={[{ value: '', label: 'All plugins' }, ...plugins]} value={plugin} onChange={(e) => setPlugin(e.target.value)} />
          <Select label="Account" options={[{ value: '', label: 'All accounts' }, ...accounts]} value={account} onChange={(e) => setAccount(e.target.value)} />
          <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        {trades.isError ? <Banner tone="warning">{errorText(trades.error)}</Banner>
          : !trades.data ? <Empty>Loading…</Empty>
          : rows.length === 0 ? <Empty>No closed trades in this range.</Empty> : (
            <DataTable dense maxHeight={480} rows={rows} rowKey="key" columns={[
              { key: 'exit', label: 'Exit time', muted: true, width: 190 },
              { key: 'plugin', label: 'Plugin' },
              { key: 'account', label: 'Account' },
              { key: 'instrument', label: 'Instrument' },
              { key: 'side', label: 'Side', render: (v: 'long' | 'short') => <Badge side={v}>{v}</Badge> },
              { key: 'qty', label: 'Qty', align: 'right' },
              { key: 'entry', label: 'Entry', align: 'right' },
              { key: 'exit_price', label: 'Exit', align: 'right' },
              { key: 'net', label: 'Net', align: 'right', render: (v: string, r: TradeRow) => <span className={r.negative ? 'text-bear' : undefined}>{v}</span> },
              { key: 'r', label: 'R', align: 'right' },
              { key: 'reason', label: 'Exit reason' },
            ]} />
          )}
      </div>
    </Panel>
  );
}

export function AnalyticsPage() {
  const { snap, connected } = useLive();
  const toast = useToast();
  const qc = useQueryClient();
  const nowMs = useNow(60_000);
  const [lastAt, setLastAt] = useState<number | null>(null);
  useEffect(() => { if (snap) setLastAt(Date.now()); }, [snap]);
  const [reset, setReset] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const doReset = useMutation({
    mutationFn: (id: string) => api.post(`/api/paper/accounts/${encodeURIComponent(id)}/reset`, { reason }),
    onSuccess: (_, id) => { toast('ok', `Account ${id} reset`, 'It starts fresh when the runner next starts.'); setReason(''); void qc.invalidateQueries({ queryKey: ['paper-equity'] }); },
    onError: (err) => toast('danger', err instanceof ApiError && err.status === 409 ? 'Stop the runner first' : 'Reset failed', errorText(err)),
  });
  const totalTrades = useQuery({
    queryKey: ['paper-trades', 'any'],
    queryFn: () => api.get<TradesResponse>(`/api/paper/total/trades${qs({ limit: 1 })}`),
    refetchInterval: 60_000,
  });

  const plugins = useMemo(() => Object.values(snap?.paper?.plugins ?? {}).sort((a, b) => (a.id < b.id ? -1 : 1)), [snap]);
  const accounts = useMemo(() => orderAccounts(snap?.accounts), [snap]);
  const named = accounts.filter((a) => !a.implicit);
  const failed = plugins.filter((p) => p.journal_failed);
  const scopes = [
    { value: 'total', label: 'Total' },
    ...accounts.map((a) => ({ value: `account:${a.id}`, label: `Account ${a.id}` })),
    ...plugins.map((p) => ({ value: `plugin:${p.id}`, label: `Plugin ${p.id}` })),
  ];
  const running = brokerRunning(snap, nowMs);
  return (
    <div>
      <PageHead title="Analytics" sub="Paper performance per plugin, per account and in total. Paper fills are a model, not a forecast."
        actions={<>
          <StatusDot status={connected ? 'live' : 'warning'} label={connected ? 'Live' : 'Reconnecting'} />
          <span className="small muted">{lastAt ? `updated ${etTime(lastAt / 1000)}` : 'waiting for the first update'}</span>
        </>} />
      <div className="page">
        {!running && <Banner tone="info">No runner is running. Values below are from the last run.</Banner>}
        {failed.map((p) => (
          <Banner key={p.id} tone="warning" title="Journal write failed">
            Journal write failed for {p.id}; analytics may miss trades from this run. a daily check that reads plugin CSVs still has its trades.
          </Banner>
        ))}
        {totalTrades.data && totalTrades.data.total === 0 && <Banner tone="info">No closed paper trades yet.</Banner>}
        {snap?.accounts && named.length === 0 && (
          <Banner tone="info">No paper accounts are configured. Plugins trade on implicit, unrestricted accounts.</Banner>
        )}
        <div className="analytics__grid">
          {snap?.paper && <TotalCard total={snap.paper.total} />}
          {accounts.map((a) => (
            <AccountCard key={a.id} account={a} members={plugins.filter((p) => p.account === a.id)} onReset={setReset} />
          ))}
          {plugins.map((p) => <PluginCard key={p.id} plugin={p} account={snap?.accounts?.[p.account]} />)}
        </div>
        {!snap?.paper && <Empty>No paper data in the live snapshot yet.</Empty>}
        <EquitySection scopes={scopes} />
        <TradesSection plugins={plugins.map((p) => p.id)} accounts={accounts.map((a) => a.id)} />
        {named.filter((a) => a.nexus).map((a) => <NexusBlock key={a.id} account={a} />)}
        <section aria-label="Daily check" className="stack">
          <h2 className="micro">Daily check</h2>
          <DailyCheck emptyBanner />
        </section>
      </div>
      <ConfirmDialog open={!!reset} danger alarmLabel="Paper · resets a paper account" icon="rotate-ccw"
        title={`Reset account ${reset ?? ''}?`} confirmLabel="Reset" busy={!reason.trim() || doReset.isPending}
        onClose={() => setReset(null)}
        onConfirm={() => { if (reset) doReset.mutate(reset); setReset(null); }}>
        <div className="stack-sm">
          <div>fse account reset archives the account&apos;s state. It starts again from its starting balance. The runner must be stopped.</div>
          <Input label="Reason (required)" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      </ConfirmDialog>
    </div>
  );
}
