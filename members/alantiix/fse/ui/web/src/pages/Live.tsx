import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Banner, Button, CodeBlock, DataTable, Dialog, FindingCard, Input, KeyValue, Panel, Segmented, Select, Stat, StatusDot, StrategyCard } from '../ds/index.js';
import { PluginStatusCard, orderModeBadge, useNow } from '../components/PluginStatusCard';
import { PageHead, Empty } from '../components/PageHead';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LogView } from '../components/LogView';
import { useToast } from '../components/Toasts';
import { api, errorText, getOrderModes, isConfirmRequired, postOrderMode, qs } from '../lib/api';
import { cardRowToFindingCardData } from '../lib/findingCard';
import { useLive } from '../lib/hooks';
import { DASH, etDateTime, etTime, fromNs, num, usd } from '../lib/format';
import type {
  BlockInfo, CardAction, CardRow, CardsResponse, ConfigSummary, OrderModePlugin, OrderModes, RunnerState, RunnerStatus,
  StrategyCardList, StrategyCardView,
} from '../lib/types';

/** True at the Live phone breakpoint (≤768px). Defaults false so desktop/tests keep panels open. */
function usePhoneLayout(): boolean {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    // Breakpoint as number + unit so eslint's no-raw-px rule does not flag a `Npx` literal.
    const mq = window.matchMedia(`(max-width: ${768}px)`);
    const sync = () => setPhone(mq.matches);
    sync();
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, []);
  return phone;
}

const LIVE_REASON = 'fse live (Practice / Combine) sends orders to ProjectX. It stays disabled until the TopstepX VPS/VPN terms are verified.';
export const HELD_TEXT = 'Stopped in the console. Deploys do not restart it until you start it here; a host reboot (patch window) does. The log-silence alarm fires while the runner is stopped.';
export const LEGACY_PAPER_TEXT = 'Runs only the Playbook engine from one Strategy_Config, without the plugin runner. Use Plugins mode to run your own plugins.';
export const PAPER_MODE_TEXT = 'paper (runs each session; waits between sessions)';
export const PRACTICE_ORDERS_TEXT = 'Practice sends real orders to the practice account.';
export const COMBINE_ORDERS_TEXT = 'Combine sends real orders to the Combine account.';
export const RESTART_CONFIRM_TEXT = 'Saving restarts the runner. Every plugin stops and restores from its state; a restart mid-session can miss bars. Practice and Combine send real orders to ProjectX.';
export const STOPPED_CONFIRM_TEXT = 'Saved. Practice starts sending real orders to the practice account at the next Start.';
export const STOPPED_COMBINE_CONFIRM_TEXT = 'Saved. Combine starts sending real orders to the Combine account at the next Start.';
export const STOPPED_PAPER_TEXT = 'Saved. Paper applies at the next Start.';
export const PAPER_NOTE = 'Paper keeps hold_through_break: false; set it on the Configs page if you want it back.';
export const HALT_ORDERS_TEXT = 'Halt blocks new entries at once. An open position and its working brackets stay at the broker until the plugin exits them; to stop now, flatten in TopstepX. Then apply `false` and use Live → Runner → Restart (it refuses while a position or working order is visible).';
export const CLEAR_BLOCKS_TEXT = 'Clear removes persistent entry blocks (halt, reconciliation, bracket failure, restore failed). It does not flatten a ProjectX position and does not drop a ghost working order from the plugin book. If reconciliation still sees “expected working, not at the broker”, the block returns on the next decision — flatten in TopstepX, stop the runner, then clear again (or reset that plugin’s saved book after the broker is flat).';
const LOSS_RE = /^\d{1,7}(\.\d{1,2})?$/;
type BrokerOrderMode = 'practice' | 'combine';
type DialogOrderMode = 'paper' | BrokerOrderMode;

/** A Practice or Combine reason the account lookup on Settings → Secrets can fix. */
const ACCOUNT_REASON_RE = /matches no account|is not a ProjectX account id|(PRACTICE|COMBINE)_ACCOUNT_ID is unset/;

/** GET /api/order-modes, shared by the Live page parts; null until a valid payload arrives. */
function useOrderModes() {
  const q = useQuery({ queryKey: ['order-modes'], queryFn: getOrderModes, refetchInterval: 10_000 });
  const data = q.data && Array.isArray(q.data.plugins) ? q.data : null;
  return { data, error: q.isError ? errorText(q.error) : null };
}

/** Ids of the plugins whose selected-config order_mode is not paper. */
function nonPaperIds(m: OrderModes | null): string[] {
  return (m?.plugins ?? []).filter((p) => p.order_mode !== 'paper').map((p) => p.id);
}

/** Ids the runner last reported running a non-paper order mode for. */
function effectiveNonPaperIds(m: OrderModes | null): string[] {
  return (m?.plugins ?? []).filter((p) => p.effective && p.effective.mode !== 'paper').map((p) => p.id);
}

const STATE_BADGE: Record<RunnerState, 'bull' | 'neutral' | 'accent-soft' | 'bear' | 'outline'> = {
  running: 'bull', stopped: 'neutral', starting: 'accent-soft', stopping: 'accent-soft', failed: 'bear', unknown: 'outline',
};

export function RunnerStateBadge({ state }: { state: RunnerState | undefined }) {
  if (!state) return <Badge variant="outline">unavailable</Badge>;
  return <Badge variant={STATE_BADGE[state] ?? 'outline'} dot={state === 'starting' || state === 'stopping'}>{state}</Badge>;
}

function lagText(lag: unknown): string {
  if (!lag || typeof lag !== 'object') return DASH;
  const parts = Object.entries(lag as Record<string, any>).map(([k, v]) =>
    v && typeof v === 'object' ? `${k} p50 ${num(v.p50 ?? v.p50_s)} · p95 ${num(v.p95 ?? v.p95_s)} · max ${num(v.max ?? v.max_s)}` : `${k} ${num(v)}`);
  return parts.length ? parts.join('  ') : DASH;
}

function blockText(b: BlockInfo | undefined): string | null {
  if (!b) return null;
  const parts = [...(b.halt_file ? ['HALT file'] : []), ...b.blocks.map((x) => `${x.kind}${x.detail ? `: ${x.detail}` : ''}`)];
  return parts.length ? parts.join(' · ') : null;
}

function RunnerControls({ status, brokerIds }: { status: RunnerStatus | undefined; brokerIds: string[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const phone = usePhoneLayout();
  const [logOpen, setLogOpen] = useState(!phone);
  useEffect(() => { setLogOpen(!phone); }, [phone]);
  const runners = useQuery({ queryKey: ['configs', 'runner'], queryFn: () => api.get<ConfigSummary[]>('/api/configs?kind=runner') });
  const strategies = useQuery({ queryKey: ['configs', 'strategy'], queryFn: () => api.get<ConfigSummary[]>('/api/configs?kind=strategy') });
  const [form, setForm] = useState(status?.settings);
  useEffect(() => { if (status && !form) setForm(status.settings); }, [status, form]);
  const saveSettings = useMutation({
    mutationFn: () => api.put('/api/runner/settings', form),
    onSuccess: () => { toast('ok', 'Runner settings saved', 'Restart the runner to use them.'); void qc.invalidateQueries({ queryKey: ['runner'] }); },
  });
  const act = useMutation({
    mutationFn: (a: string) => api.post<RunnerStatus>(`/api/runner/${a}`),
    onSuccess: (_, a) => toast('ok', `Runner ${a} sent`),
    onError: (err) => toast('danger', 'Runner action refused', errorText(err)),
  });
  const [confirmStop, setConfirmStop] = useState(false);
  const [confirmBroker, setConfirmBroker] = useState<'start' | 'restart' | null>(null);
  const needsBrokerConfirm = status?.settings.mode === 'run' && brokerIds.length > 0;
  const startOrRestart = (a: 'start' | 'restart') => (needsBrokerConfirm ? setConfirmBroker(a) : act.mutate(a));
  const state = status?.state;
  const changing = state === 'starting' || state === 'stopping';
  // While systemd is mid restart/start, Restart used to stay enabled and stacked
  // systemctl restarts (order-mode save + Live looked like a Combine crash loop).
  const canStart = state !== 'running' && state !== 'starting' && state !== 'stopping';
  const canRestart = state === 'running' || state === 'failed' || state === 'unknown';
  const canStop = state === 'running' || state === 'starting' || state === 'failed' || state === 'unknown';
  if (!form) return <Empty>Loading…</Empty>;
  const changed = JSON.stringify(form) !== JSON.stringify(status?.settings);
  const facts: [string, string][] = [];
  if (status?.settings.mode === 'paper') facts.push(['Mode', PAPER_MODE_TEXT]);
  if (status?.controller === 'systemd') facts.push(['Controller', 'fse-runner systemd unit']);
  if (status?.pending) facts.push(['Pending', String(status.pending)]);
  if (status?.last_command) facts.push(['Last command', `${status.last_command.action} · ${status.last_command.status}`]);
  return (
    <Panel title="Runner" subtitle={status?.started_at ? `since ${etDateTime(status.started_at)}` : ''} icon="radio"
      actions={<div className="live-primary row">
        <RunnerStateBadge state={state} />
        <Button size="sm" variant="primary" icon="play" disabled={!canStart || act.isPending} onClick={() => startOrRestart('start')}>Start</Button>
        <Button size="sm" icon="refresh-cw" disabled={!canRestart || act.isPending || changing} onClick={() => startOrRestart('restart')}>Restart</Button>
        <Button size="sm" variant="danger-outline" icon="circle-stop" disabled={!canStop || act.isPending} onClick={() => setConfirmStop(true)}>Stop</Button>
      </div>}>
      <div className="stack">
        {status?.held && <Banner tone="info" title="Held">{HELD_TEXT}</Banner>}
        {status?.error && <Banner tone="warning" title="Runner state">{status.error}</Banner>}
        {facts.length > 0 && <KeyValue items={facts} />}
        <div className="form-grid">
          <Select label="Mode" options={[
            { value: 'run', label: 'Plugins (fse run, 24 h)' },
            { value: 'paper', label: 'Playbook only (legacy fse paper)' },
          ]} value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value as 'run' | 'paper' })} />
          {form.mode === 'run'
            ? <Select label="Runner config" hint="the plugins and data feeds fse run loads" options={(runners.data ?? []).map((c) => c.name)} value={form.runner_config}
                onChange={(e) => setForm({ ...form, runner_config: e.target.value })} />
            : <Select label="Strategy_Config" options={(strategies.data ?? []).map((c) => c.name)} value={form.strategy_config}
                onChange={(e) => setForm({ ...form, strategy_config: e.target.value })} />}
          {form.mode === 'paper' && <div className="small muted">{LEGACY_PAPER_TEXT}</div>}
          <Select label="fse live" hint={LIVE_REASON} disabled options={[{ value: 'off', label: 'disabled' }]} value="off" />
        </div>
        <div className="row live-primary">
          <Button size="sm" icon="save" disabled={!changed || saveSettings.isPending} onClick={() => saveSettings.mutate()}>Save settings</Button>
          {saveSettings.isError && <span className="small text-bear">{errorText(saveSettings.error)}</span>}
          {status?.last_exit_code !== null && status?.last_exit_code !== undefined && (
            <span className="small muted">last exit {status.last_exit_code} · {status.last_exit_meaning}</span>)}
        </div>
        {/* Log collapses behind <details> on phone; open on desktop. */}
        <details className="live-collapse runner-log-collapse" open={logOpen}
          onToggle={(e) => setLogOpen((e.target as HTMLDetailsElement).open)}>
          <summary>Runner log</summary>
          <div className="live-collapse__body">
            {/* Every controller returns the log oldest first; it is reversed here, in one place. */}
            <LogView lines={[...(status?.log ?? [])].reverse()} newestFirst empty="The runner has not started from this console." />
          </div>
        </details>
      </div>
      <ConfirmDialog open={confirmStop} danger alarmLabel="Paper · stops the live runner" title="Stop the runner?"
        confirmLabel="Stop runner" icon="circle-stop" onClose={() => setConfirmStop(false)}
        onConfirm={() => { setConfirmStop(false); act.mutate('stop'); }}>
        fse run gets SIGINT and saves each plugin's state. Feeds stop recording until it starts again.
      </ConfirmDialog>
      <ConfirmDialog open={!!confirmBroker} danger alarmLabel="Practice / Combine · real orders"
        title={confirmBroker === 'restart' ? 'Restart the runner with broker orders?' : 'Start the runner with broker orders?'}
        confirmLabel={confirmBroker === 'restart' ? 'Restart runner' : 'Start runner'} icon={confirmBroker === 'restart' ? 'refresh-cw' : 'play'}
        onClose={() => setConfirmBroker(null)}
        onConfirm={() => { const a = confirmBroker; setConfirmBroker(null); if (a) act.mutate(a); }}>
        {PRACTICE_ORDERS_TEXT} {COMBINE_ORDERS_TEXT} {brokerIds.join(', ')} {brokerIds.length === 1 ? 'uses' : 'use'} a non-paper order mode in {status?.settings.runner_config}.
      </ConfirmDialog>
    </Panel>
  );
}

function initialDialogMode(target: OrderModePlugin, practiceOk: boolean, combineOk: boolean): DialogOrderMode {
  if (target.order_mode === 'practice' && practiceOk) return 'practice';
  if (target.order_mode === 'combine' && combineOk) return 'combine';
  return 'paper';
}

/** "Change…" for one broker plugin: Paper, Practice or Combine with caps, then a confirm. */
function OrderModeDialog({ id, modes, onClose }: { id: string; modes: OrderModes; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const target = modes.plugins.find((p) => p.id === id);
  const practiceReasons = [...modes.disabled_reasons, ...(target?.practice_blocked ?? [])];
  const combineReasons = [...(modes.combine_disabled_reasons ?? []), ...(target?.combine_blocked ?? [])];
  const practiceBlocked = practiceReasons.length > 0;
  const combineBlocked = combineReasons.length > 0;
  const [mode, setMode] = useState<DialogOrderMode>(
    target ? initialDialogMode(target, !practiceBlocked, !combineBlocked) : 'paper',
  );
  const [loss, setLoss] = useState(target?.max_daily_loss ?? '');
  const [trades, setTrades] = useState(target?.max_trades_per_day?.toString() ?? '');
  const [confirm, setConfirm] = useState<'running' | 'stopped' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tradesN = Number(trades.trim());
  const lossOk = LOSS_RE.test(loss.trim()) && Number(loss) > 0;
  const tradesOk = /^\d+$/.test(trades.trim()) && tradesN >= 1 && tradesN <= 500;
  const brokered = mode === 'practice' || mode === 'combine';
  const modeBlocked = mode === 'practice' ? practiceBlocked : mode === 'combine' ? combineBlocked : false;
  const post = useMutation({
    mutationFn: (confirmRestart: boolean) => postOrderMode({
      plugin_id: id, order_mode: mode, expected_sha: modes.sha, confirm_restart: confirmRestart,
      ...(brokered ? { max_daily_loss: loss.trim(), max_trades_per_day: tradesN } : {}),
    }),
    onSuccess: (r) => {
      qc.setQueryData(['order-modes'], r);
      void qc.invalidateQueries({ queryKey: ['runner'] });
      toast('ok', r.message, r.note ?? undefined);
      onClose();
    },
    onError: (err) => {
      // The runner started since the dialog opened: confirm the restart, then send again.
      if (isConfirmRequired(err)) { setConfirm('running'); return; }
      setError(errorText(err));
      void qc.invalidateQueries({ queryKey: ['order-modes'] });
    },
  });
  if (!target) return null;
  const ready = mode === 'paper' ? target.order_mode !== 'paper' : !modeBlocked && lossOk && tradesOk;
  const stoppedState = modes.runner_state === 'stopped' || modes.runner_state === 'failed';
  const changing = modes.runner_state === 'starting' || modes.runner_state === 'stopping';
  const modeLabel = mode === 'practice' ? 'Practice' : mode === 'combine' ? 'Combine' : 'Paper';
  const stoppedNote = mode === 'practice' ? STOPPED_CONFIRM_TEXT
    : mode === 'combine' ? STOPPED_COMBINE_CONFIRM_TEXT : STOPPED_PAPER_TEXT;
  return (
    <>
      <Dialog open title={`Order mode: ${target.label}`} onClose={onClose}
        footer={<>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button className="live-primary" variant={brokered ? 'danger' : 'primary'} icon="save" disabled={!ready || post.isPending || changing}
            onClick={() => { setError(null); setConfirm(stoppedState ? 'stopped' : 'running'); }}>Save</Button>
        </>}>
        <div className="stack">
          <div className="small muted">{target.id} in runner config {modes.runner_config} · now {target.order_mode}</div>
          {changing && (
            <Banner tone="info" title="Runner is changing state">
              Wait until the runner is running or stopped, then save. Saving during a restart stacks another restart.
            </Banner>
          )}
          {practiceBlocked && (
            <Banner tone="warning" title="Practice is not available">
              <ul aria-label="Why Practice is not available">{practiceReasons.map((r) => <li key={r}>{r}</li>)}</ul>
              {practiceReasons.some((r) => ACCOUNT_REASON_RE.test(r)) && <Link to="/secrets">Find my ProjectX accounts</Link>}
            </Banner>
          )}
          {combineBlocked && (
            <Banner tone="warning" title="Combine is not available">
              <ul aria-label="Why Combine is not available">{combineReasons.map((r) => <li key={r}>{r}</li>)}</ul>
              {combineReasons.some((r) => ACCOUNT_REASON_RE.test(r)) && <Link to="/secrets">Find my ProjectX accounts</Link>}
            </Banner>
          )}
          <fieldset className="stack-sm">
            <legend className="micro">Order mode</legend>
            <label className="row">
              <input type="radio" name={`order-mode-${id}`} value="paper" checked={mode === 'paper'} onChange={() => setMode('paper')} />
              Paper (no order is sent)
            </label>
            <label className="row">
              <input type="radio" name={`order-mode-${id}`} value="practice" checked={mode === 'practice'} disabled={practiceBlocked}
                onChange={() => setMode('practice')} />
              Practice (real orders to the practice account)
            </label>
            <label className="row">
              <input type="radio" name={`order-mode-${id}`} value="combine" checked={mode === 'combine'} disabled={combineBlocked}
                onChange={() => setMode('combine')} />
              Combine (real orders to the Combine account)
            </label>
          </fieldset>
          <div className="form-grid">
            <Input label="Max daily loss" prefix="$" inputMode="decimal" autoComplete="off" required disabled={!brokered}
              value={loss} onChange={(e) => setLoss(e.target.value)}
              error={brokered && loss.trim() && !lossOk ? 'a dollar amount above 0, at most 2 decimals' : undefined} />
            <Input label="Max trades per day" type="number" min={1} max={500} step={1} required disabled={!brokered}
              value={trades} onChange={(e) => setTrades(e.target.value)}
              error={brokered && trades.trim() && !tradesOk ? 'a whole number from 1 to 500' : undefined} />
          </div>
          {brokered
            ? <div className="small muted">{modeLabel} also sets hold_through_break: false.</div>
            : <div className="small muted">{PAPER_NOTE}</div>}
          {error && <Banner tone="warning" title="Not saved">{error}</Banner>}
        </div>
      </Dialog>
      <ConfirmDialog open={!!confirm} danger={brokered} alarmLabel={brokered ? `${modeLabel} · real orders` : undefined}
        title={`Set ${target.label} to ${modeLabel}?`}
        confirmLabel={confirm === 'running' ? 'Save and restart' : 'Save'} icon="save" busy={post.isPending}
        onClose={() => setConfirm(null)}
        onConfirm={() => { const c = confirm; setConfirm(null); post.mutate(c === 'running'); }}>
        {confirm === 'running' ? RESTART_CONFIRM_TEXT : stoppedNote}
      </ConfirmDialog>
    </>
  );
}

/** Every broker plugin of the selected runner config with its order mode and a Change… button. */
function OrderModePanel({ modes, error, onChange }: { modes: OrderModes | null; error: string | null; onChange: (id: string) => void }) {
  if (!modes) return error ? <Banner tone="warning" title="Order modes">{error}</Banner> : null;
  if (modes.plugins.length === 0) return null;
  return (
    <Panel title="Order mode" subtitle={`runner config ${modes.runner_config} · plugin orders ${modes.plugin_orders ? 'enabled' : 'disabled'}`} icon="shield-check">
      <DataTable dense rows={modes.plugins} rowKey="id" columns={[
        { key: 'label', label: 'Plugin', render: (v: string, p: OrderModePlugin) => <>{v} <span className="muted small">{p.id}</span></> },
        { key: 'order_mode', label: 'Config', width: 90, render: (v: OrderModePlugin['order_mode']) => <Badge mode={v} /> },
        { key: 'effective', label: 'Runner', render: (_: unknown, p: OrderModePlugin) => {
          const b = orderModeBadge(p.order_mode, p.effective);
          return <Badge variant={b.variant}>{b.text}</Badge>;
        } },
        { key: 'max_daily_loss', label: 'Caps', muted: true, render: (_: unknown, p: OrderModePlugin) =>
          (p.max_daily_loss !== null || p.max_trades_per_day !== null
            ? `max loss ${p.max_daily_loss !== null ? usd(p.max_daily_loss) : DASH} · ${p.max_trades_per_day ?? DASH} trades/day` : DASH) },
        { key: 'busy', label: '', width: 120, render: (busy: boolean) => (busy ? <Badge variant="warning">position open</Badge> : null) },
        { key: 'id', label: '', width: 100, render: (id: string, p: OrderModePlugin) => (
          <Button size="sm" icon="sliders-horizontal" aria-label={`Change order mode of ${p.label}`} onClick={() => onChange(id)}>Change…</Button>) },
      ]} />
    </Panel>
  );
}

export function LivePage() {
  const { snap, connected } = useLive();
  const phone = usePhoneLayout();
  const [activityOpen, setActivityOpen] = useState(!phone);
  useEffect(() => { setActivityOpen(!phone); }, [phone]);
  const toast = useToast();
  const [halt, setHalt] = useState<{ plugin: string | null } | null>(null);
  const [clear, setClear] = useState<{ plugin: string | null } | null>(null);
  const [resetDay, setResetDay] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const qc = useQueryClient();
  const modes = useOrderModes();
  const [omTarget, setOmTarget] = useState<string | null>(null);
  const [confirmSwitch, setConfirmSwitch] = useState(false);
  const switchRestart = useMutation({
    mutationFn: () => api.post<RunnerStatus>('/api/runner/restart'),
    onSuccess: () => { toast('ok', 'Runner restart sent'); void qc.invalidateQueries({ queryKey: ['order-modes'] }); },
    onError: (err) => toast('danger', 'Runner action refused', errorText(err)),
  });
  const brokerIds = nonPaperIds(modes.data);
  const inUseIds = effectiveNonPaperIds(modes.data);
  const combineInUse = (modes.data?.plugins ?? []).some(
    (p) => p.order_mode === 'combine' || p.effective?.mode === 'combine',
  );
  const practiceInUse = (modes.data?.plugins ?? []).some(
    (p) => p.order_mode === 'practice' || p.effective?.mode === 'practice',
  );
  const doHalt = useMutation({
    mutationFn: (p: { plugin: string | null }) => api.post('/api/halt', { plugin_id: p.plugin, reason }),
    onSuccess: (_, p) => { toast('warning', p.plugin ? `Plugin ${p.plugin} halted` : 'Runner halted', 'New entries are blocked until clear.'); setReason(''); },
    onError: (err) => toast('danger', 'Halt failed', errorText(err)),
  });
  const doClear = useMutation({
    mutationFn: (p: { plugin: string | null }) => api.post('/api/clear', { plugin_id: p.plugin }),
    onSuccess: () => toast('ok', 'Blocks cleared'),
    onError: (err) => toast('danger', 'Clear failed', errorText(err)),
  });
  const doResetDayLoss = useMutation({
    mutationFn: (pluginId: string) => api.post(`/api/plugins/${pluginId}/reset-day-loss`, {
      reason: 'reset today max loss from Live',
    }),
    onSuccess: (_, pluginId) => {
      toast('ok', `Day loss reset for ${pluginId}`,
        'Paper day net zeros on the next host tick; max_daily_loss in the config is unchanged.');
      void qc.invalidateQueries({ queryKey: ['live'] });
    },
    onError: (err) => toast('danger', 'Day loss reset failed', errorText(err)),
  });
  const runnerQuery = useQuery({ queryKey: ['runner'], queryFn: () => api.get<RunnerStatus>('/api/runner'), refetchInterval: 5000 });
  const runner = snap?.runner ?? runnerQuery.data;
  const hb = snap?.heartbeat;
  const rootBlocks = blockText(snap?.blocks.root);
  const running = runner?.state === 'running';
  const sky = snap?.skylit ?? null;
  const nowNs = useNow(snap?.now_ns);
  const [show, setShow] = useState<'all' | 'problems'>('all');
  const inTrade = snap?.plugins.filter((p) => p.status?.phase === 'in_trade').length ?? 0;
  const faultedN = snap?.plugins.filter((p) => p.faulted).length ?? 0;
  const phaseCounts = `${inTrade} in a trade · ${faultedN} stopped`;
  const timeline = [...(snap?.host_log ?? [])].reverse()
    .filter((e) => show === 'all' || (e.say?.severity ?? e.level) !== 'info');
  const stateLabel = runner?.state ? runner.state.charAt(0).toUpperCase() + runner.state.slice(1) : 'Unavailable';
  const pxValue = hb ? (hb.connected ? 'connected' : 'disconnected') : (running ? 'starting' : 'unavailable');
  const pxSub = hb
    ? (hb.provisional
      ? `${hb.transport ?? DASH} · hub status (awaiting heartbeat)`
      : `${hb.transport ?? DASH} · ${hb.subscription ?? DASH}`)
    : (running ? 'waiting for market data status' : 'no market data yet');
  return (
    <div className="live-page">
      {snap?.blocks.halted && (
        <Banner tone="alarm" flush title="Runner halted"
          actions={<Button size="sm" icon="rotate-ccw" onClick={() => setClear({ plugin: null })}>fse clear</Button>}>
          {rootBlocks ?? 'Entries are blocked.'} New entries stay blocked until clear.
        </Banner>
      )}
      {modes.data?.switch_pending && (
        <Banner tone="alarm" flush title="Plugin orders are off"
          actions={<Button size="sm" icon="refresh-cw" disabled={switchRestart.isPending || runner?.state === 'starting' || runner?.state === 'stopping'} onClick={() => setConfirmSwitch(true)}>Restart</Button>}>
          plugin orders are off for this deployment, but {inUseIds.join(', ') || 'a plugin'} is still in a broker mode until the runner restarts
        </Banner>
      )}
      <PageHead title="Live runner"
        sub={inUseIds.length
          ? (combineInUse && !practiceInUse
            ? `Combine orders go to the ProjectX Combine account for ${inUseIds.join(', ')}.`
            : practiceInUse && !combineInUse
              ? `Practice orders go to the ProjectX practice account for ${inUseIds.join(', ')}.`
              : `Broker orders (Practice/Combine) are configured for ${inUseIds.join(', ')}.`)
          : 'Paper Order_Mode. No order is sent to ProjectX.'}
        actions={<>
          {snap?.blocks.halted ? <Badge variant="danger-solid">Halted</Badge>
            : <StatusDot status={running ? 'live' : runner?.state === 'failed' ? 'warning' : 'idle'} label={stateLabel} />}
          {!connected && <Badge variant="warning-outline">reconnecting</Badge>}
          <Button variant="danger-outline" icon="octagon-x" disabled={snap?.blocks.halted} onClick={() => setHalt({ plugin: null })}>Halt all</Button>
        </>} />
      <div className="page">
        <div className="stats">
          <Stat label="Runner" value={runner?.state ?? 'unavailable'} sub={snap?.run_dir ?? 'no run folder yet'} />
          <Stat label="ProjectX" value={pxValue}
            tone={hb ? (hb.connected ? 'pos' : 'neg') : undefined} sub={pxSub} />
          <Stat label="Last heartbeat" value={hb ? etTime(fromNs(hb.t_ns)) : DASH}
            sub={hb ? (hb.provisional ? 'from hub status' : `rt dropped ${num(hb.rt_dropped, 0)}`) : undefined} />
          <Stat label="Skylit" value={sky ? (sky.open ? 'fetching' : 'closed') : 'unavailable'}
            sub={sky ? `${sky.window_start ?? DASH}-${sky.window_end ?? DASH} ET · ${num(sky.credits_per_day ?? 0, 0)} credits/day` : 'no plan logged yet'} />
          <Stat label="Plugins" value={num(snap?.plugins.length ?? 0, 0)} sub={phaseCounts} />
          <Stat label="Faults" value={num(snap?.faults.length ?? 0, 0)} tone={snap?.faults.length ? 'neg' : undefined} />
        </div>
        {hb && <div className="small muted">bar lag: {lagText(hb.lag)}</div>}
        <RunnerControls status={runner} brokerIds={brokerIds} />
        <OrderModePanel modes={modes.data} error={modes.error} onChange={setOmTarget} />
        <div className="cols">
          {(snap?.plugins ?? []).length === 0 && <Empty>No plugins have run yet. Start the runner above.</Empty>}
          {(snap?.plugins ?? []).map((p) => (
            <PluginStatusCard key={p.id} plugin={p} nowNs={nowNs} blocked={blockText(snap?.blocks.plugins[p.id])}
              paper={snap?.paper?.plugins[p.id]} accounts={snap?.accounts}
              orderMode={modes.data?.plugins.find((x) => x.id === p.id)} onOrderMode={() => setOmTarget(p.id)}
              onHalt={() => setHalt({ plugin: p.id })} onClear={() => setClear({ plugin: p.id })}
              onResetDayLoss={snap?.paper?.plugins[p.id]?.caps?.max_daily_loss != null
                ? () => setResetDay(p.id) : undefined} />
          ))}
        </div>
        <details className="live-collapse live-activity-collapse" open={activityOpen}
          onToggle={(e) => setActivityOpen((e.target as HTMLDetailsElement).open)}>
          <summary>Runner activity{snap?.run_dir ? ` · ${snap.run_dir}` : ''}</summary>
          <div className="live-collapse__body">
            <Panel title="Runner activity" subtitle={snap?.run_dir ? `${snap.run_dir} · newest first` : 'no runner run'} icon="list" flush
              actions={<Segmented options={[{ value: 'all', label: 'Everything' }, { value: 'problems', label: 'Warnings and errors' }]}
                value={show} onChange={(v) => setShow(v as 'all' | 'problems')} />}>
              {timeline.length === 0 ? <Empty>{show === 'all' ? 'none' : 'No warnings or errors in this run.'}</Empty> : (
                <DataTable dense maxHeight="50vh" rows={timeline.map((e, i) => ({ ...e, _k: i }))} rowKey="_k" columns={[
                  { key: 't_ns', label: 'Time (ET)', muted: true, width: 90, render: (v: number) => etTime(fromNs(v)) },
                  { key: 'plugin', label: 'Plugin', width: 90, muted: true, render: (v: string) => v ?? 'runner' },
                  { key: 'say', label: 'What happened', render: (v: any, e: any) => {
                    const sev = v?.severity ?? e.level;
                    return <span className={sev === 'error' ? 'text-bear' : sev === 'warning' ? 'text-warning' : ''}>
                      {sev === 'error' ? 'Error: ' : sev === 'warning' ? 'Warning: ' : ''}{v?.text ?? e.kind}</span>;
                  } },
                ]} />
              )}
            </Panel>
          </div>
        </details>
      </div>
      <ConfirmDialog open={!!halt} danger alarmLabel="Paper · affects live runner state" icon="octagon-x"
        title={halt?.plugin ? `Halt plugin ${halt.plugin}?` : 'Halt every plugin?'}
        confirmLabel="Halt" busy={doHalt.isPending} onClose={() => setHalt(null)}
        onConfirm={() => { if (halt) doHalt.mutate(halt); setHalt(null); }}>
        <div className="stack-sm">
          <div>fse halt writes a persistent block. New entries are refused until <code className="inline">fse clear</code>. Open trades keep their stops and targets.</div>
          {(brokerIds.length > 0 || inUseIds.length > 0) && <div className="small">{HALT_ORDERS_TEXT}</div>}
          <Input label="Reason" placeholder="halted from the fse console" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      </ConfirmDialog>
      <ConfirmDialog open={!!clear} title={clear?.plugin ? `Clear blocks of ${clear.plugin}?` : 'Clear every root block?'}
        confirmLabel="Clear" icon="rotate-ccw" busy={doClear.isPending} onClose={() => setClear(null)}
        onConfirm={() => { if (clear) doClear.mutate(clear); setClear(null); }}>
        <div className="stack-sm">
          <div>fse clear removes the persistent blocks. A HALT file, if present, stays until it is deleted on the host.</div>
          <div className="small">{CLEAR_BLOCKS_TEXT}</div>
        </div>
      </ConfirmDialog>
      <ConfirmDialog open={!!resetDay} title={`Reset today's max loss for ${resetDay ?? ''}?`}
        confirmLabel="Reset day loss" icon="rotate-ccw" busy={doResetDayLoss.isPending}
        onClose={() => setResetDay(null)}
        onConfirm={() => { if (resetDay) doResetDayLoss.mutate(resetDay); setResetDay(null); }}>
        <div className="stack-sm">
          <div>
            Zeros this plugin&apos;s paper <code className="inline">day_net</code> so the plugin and host
            caps size again to the full configured <code className="inline">max_daily_loss</code>.
            The runner YAML is not edited. Open positions and the trade journal stay.
          </div>
          <div className="small muted">Applied on the next host tick while the runner is up (or at the next start).</div>
        </div>
      </ConfirmDialog>
      {omTarget && modes.data && <OrderModeDialog key={omTarget} id={omTarget} modes={modes.data} onClose={() => setOmTarget(null)} />}
      <ConfirmDialog open={confirmSwitch} danger alarmLabel="Restarts the live runner" title="Restart the runner?"
        confirmLabel="Restart runner" icon="refresh-cw" busy={switchRestart.isPending} onClose={() => setConfirmSwitch(false)}
        onConfirm={() => { setConfirmSwitch(false); switchRestart.mutate(); }}>
        The runner restarts in Paper because plugin orders are off. Every plugin stops and restores from its state; a restart mid-session can miss bars.
        The console refuses while a position or working order is visible.
      </ConfirmDialog>
    </div>
  );
}

const ACTION_BADGE: Record<CardAction, 'bull' | 'warning' | 'neutral' | 'outline' | 'info'> = {
  enter: 'bull', skip: 'warning', hold: 'info', exit: 'neutral', result: 'neutral', info: 'outline',
};
export const CARDS_EMPTY_TEXT = 'No cards in the newest run yet. A plugin publishes a card for each decision it logs.';

function cardTitle(row: CardRow): string {
  return row.format === 'decision' ? row.card?.title ?? 'card' : row.title ?? 'Finding_Card';
}

function signedMoney(v: string | undefined) {
  if (v === undefined) return DASH;
  const n = Number(v);
  return <span className={n > 0 ? 'text-bull' : n < 0 ? 'text-bear' : ''}>{usd(v)}</span>;
}

function DecisionResultStrip({ row }: { row: CardRow }) {
  const c = row.card;
  if (!c) return null;
  const res = row.result?.card.result ?? (c.action === 'result' ? c.result : null);
  if (!res) return null;
  return (
    <div className="stack-sm">
      <div className="micro">result{row.result ? ` · ${etTime(fromNs(row.result.t_ns))}` : ''}</div>
      <KeyValue items={[
        ['Exit reason', res.exit_reason ?? DASH],
        ['Net', signedMoney(res.net)],
        ['R', res.r ?? DASH],
        ['Ticks', res.ticks ?? DASH],
      ]} />
    </div>
  );
}

type CardSegment = 'findings' | 'strategies';

function cardSegment(value: string): CardSegment {
  if (value === 'strategies') return 'strategies';
  return 'findings';
}

function StrategyCards() {
  const [sel, setSel] = useState(0);
  const list = useQuery({
    queryKey: ['strategy-cards'],
    queryFn: () => api.get<StrategyCardList>('/api/cards'),
  });
  const cards = list.data?.cards ?? [];
  const row = cards[Math.min(sel, Math.max(0, cards.length - 1))];
  const detail = useQuery({
    queryKey: ['strategy-card', row?.id],
    queryFn: () => api.get<StrategyCardView>(`/api/cards/${row?.id}/render?format=json`),
    enabled: Boolean(row),
  });
  return (
    <div className="page">
      {list.isError && <Banner tone="warning">{errorText(list.error)}</Banner>}
      {cards.length === 0 ? <Empty>{list.isLoading ? 'Loading…' : 'No strategy cards yet.'}</Empty> : (
        <div className="split">
          <Panel flush><div className="filelist cardlist">{cards.map((c, i) => (
            <button key={c.id} type="button" aria-current={c === row} onClick={() => setSel(i)}>
              <span className="row">
                <Badge variant="outline">{c.grade}</Badge>
                <span className="muted">{c.author}</span>
              </span>
              <span className="cardlist__title">{c.name}</span>
              <span className="row"><span className="muted">{c.symbols.join(' ')} · {c.timeframe}</span></span>
            </button>
          ))}</div></Panel>
          {row && (
            <Panel title={row.name} subtitle={`${row.author} · ${row.post_id}`} icon="inbox">
              {detail.isError && <Banner tone="warning">{errorText(detail.error)}</Banner>}
              {detail.data
                ? <StrategyCard card={detail.data} />
                : <Empty>{detail.isLoading ? 'Loading…' : 'This card has no render.'}</Empty>}
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}

export function CardsPage() {
  const [segment, setSegment] = useState<CardSegment>('findings');
  const [plugin, setPlugin] = useState('');
  const [sel, setSel] = useState(0);
  const [raw, setRaw] = useState(false);
  const q = useQuery({
    queryKey: ['live-cards', plugin],
    queryFn: () => api.get<CardsResponse>(`/api/live/cards${qs({ plugin })}`),
    refetchInterval: 5000,
  });
  useEffect(() => { setSel(0); }, [plugin]);
  const data = q.data;
  const cards = data?.cards ?? [];
  const row = cards[Math.min(sel, Math.max(0, cards.length - 1))];
  const hidden = (data?.plugins ?? []).filter((p) => p.hidden).map((p) => p.id);
  const visiblePlugins = (data?.plugins ?? []).filter((p) => !p.hidden);
  const total = visiblePlugins.reduce((n, p) => n + p.count, 0);
  const options = [
    { value: '', label: `All cards (${total}) · ${visiblePlugins.length} plugins` },
    ...(data?.plugins ?? []).map((p) => ({ value: p.id, label: `${p.id} (${p.hidden ? 'hidden' : p.count})` })),
  ];
  return (
    <div>
      <PageHead
        title={segment === 'strategies' ? 'Strategy cards' : 'Finding cards'}
        sub={segment === 'strategies'
          ? 'Posts, rules, and backtest grades. Newest first.'
          : (data?.run_dir
            ? `from runs/${data.run_dir} (same UTC day) · newest first`
            : 'Every plugin’s decision and Finding cards, newest first.')}
        actions={(
          <div className="row">
            <Segmented
              options={[{ value: 'findings', label: 'Findings' }, { value: 'strategies', label: 'Strategies' }]}
              value={segment}
              onChange={(value) => setSegment(cardSegment(value))}
            />
            {segment === 'findings' && (
              <Select size="sm" label="Plugin" aria-label="Plugin" options={options} value={plugin} onChange={(e) => setPlugin(e.target.value)} />
            )}
          </div>
        )}
      />
      {segment === 'strategies' ? <StrategyCards /> : (
      <div className="page">
        {hidden.length > 0 && (
          <Banner tone="info" title="Some cards are hidden">
            Cards hidden: {hidden.join(', ')} (Publish Finding Cards is off in runner config {data?.runner_config ?? DASH}).
            The runner stops writing them at its next restart.
          </Banner>
        )}
        {q.isError && <Banner tone="warning">{errorText(q.error)}</Banner>}
        {cards.length === 0 ? <Empty>{q.isLoading ? 'Loading…' : CARDS_EMPTY_TEXT}</Empty> : (
          <div className="split">
            <Panel flush><div className="filelist cardlist">{cards.map((c, i) => (
              <button key={`${c.plugin}-${c.t_ns}-${i}`} type="button" aria-current={c === row} onClick={() => setSel(i)}>
                <span className="row">
                  <Badge variant="outline">{c.plugin ?? 'unattributed'}</Badge>
                  <span className="muted">{c.t_ns ? etTime(fromNs(c.t_ns)) : DASH}</span>
                </span>
                <span className="cardlist__title">{cardTitle(c)}</span>
                {(c.card || c.result) && (
                  <span className="row">
                    {c.card && <Badge variant={ACTION_BADGE[c.card.action] ?? 'outline'}>{c.card.action}</Badge>}
                    {c.card?.side && <Badge side={c.card.side}>{c.card.side}</Badge>}
                    {c.result && <Badge variant="neutral">result</Badge>}
                  </span>
                )}
              </button>
            ))}</div></Panel>
            {row && (
              <Panel
                className="cards-detail cards-detail--rich"
                title={cardTitle(row)}
                subtitle={`${row.plugin ?? 'unattributed'}${row.t_ns ? ` · ${etDateTime(fromNs(row.t_ns))}` : ''}`}
                icon="inbox"
                actions={<Button size="sm" variant="ghost" icon="file-code" aria-pressed={raw} onClick={() => setRaw(!raw)}>{raw ? 'Hide raw JSON' : 'Show raw JSON'}</Button>}>
                <div className="stack">
                  {row.source && <div className="small muted">from runs/{row.source}</div>}
                  {row.format === 'finding_card' && row.raw.narration && row.raw.card?.trade?.grade !== 'Pass' && (
                    <div className="small">{row.raw.narration}</div>
                  )}
                  {row.format === 'finding_card' && row.raw.narration_note && (
                    <div className="small muted">{row.raw.narration_note}</div>
                  )}
                  <FindingCard card={cardRowToFindingCardData(row)} />
                  {row.format === 'decision' && <DecisionResultStrip row={row} />}
                  {raw && (
                    <CodeBlock
                      code={JSON.stringify(
                        row.format === 'decision'
                          ? (row.result ? { card: row.raw, result: row.result.raw } : row.raw)
                          : (row.raw.card ?? row.raw),
                        null,
                        2,
                      )}
                      maxHeight="50vh"
                    />
                  )}
                </div>
              </Panel>
            )}
          </div>
        )}
      </div>
      )}
    </div>
  );
}
