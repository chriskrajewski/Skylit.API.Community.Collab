import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import { PluginStatusCard, orderModeBadge, relTime } from '../components/PluginStatusCard';
import { LEGACY_PAPER_TEXT, PAPER_NOTE, PRACTICE_ORDERS_TEXT, RESTART_CONFIRM_TEXT, STOPPED_CONFIRM_TEXT } from '../pages/Live';
import type { LivePlugin, OrderModePlugin, OrderModes, PaperPlugin, PluginPnl, PluginStatus, PositionPnl, RunnerState, Session } from '../lib/types';

const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};
const MIN = 60e9;
const NOW = 1_791_800_000 * 1e9;

function status(over: Partial<PluginStatus> = {}): PluginStatus {
  return {
    version: 1, plugin: 'plugin_c', label: 'Plugin C', module: 'research.plugin_c.plugin:PluginC', brokerage: 'host',
    updated_ns: NOW, day: '2026-10-12', phase: 'waiting', phase_text: 'Waiting for its next setup', blocks: [],
    next_wake_ns: null, positions: [], working: [], today: { net: '0', trades: 0 }, recent: [], last_log: null,
    plugin_status: {
      summary: 'Waiting for slot T2 at 11:40',
      next: { at_ns: NOW + 23 * MIN, text: 'Slot T2 11:40: enter long if price is above the EMA, else short' },
      last: { at_ns: NOW - 5 * MIN, text: 'T1 09:45: skipped: ema_not_ready' },
      schedule: [
        { at_ns: NOW - 5 * MIN, label: 'T1 09:45: skipped: ema_not_ready', state: 'skipped' },
        { at_ns: NOW + 23 * MIN, label: 'T2 11:40: planned', state: 'planned' },
      ],
      facts: [['Preset', 'plugin_c']],
    },
    ...over,
  };
}

function plugin(s: PluginStatus | null, over: Partial<LivePlugin> = {}): LivePlugin {
  return { id: 'plugin_c', status: s, log: [], faulted: false, has_raw: true, ...over };
}

function wrap(ui: JSX.Element) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ToastProvider>{ui}</ToastProvider></QueryClientProvider>);
}

describe('relTime', () => {
  it('counts down and up in words', () => {
    expect(relTime(NOW + 23 * MIN, NOW)).toBe('in 23 min');
    expect(relTime(NOW + 125 * MIN, NOW)).toBe('in 2 h 5 min');
    expect(relTime(NOW - 5 * MIN, NOW)).toBe('5 min ago');
    expect(relTime(NOW + 10e9, NOW)).toBe('now');
    expect(relTime(null, NOW)).toBe('');
  });
});

describe('plugin status card', () => {
  it('shows the phase, what it did, what is next with a countdown, and today\'s plan', () => {
    wrap(<PluginStatusCard plugin={plugin(status())} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getByText('Waiting')).toBeInTheDocument();
    expect(screen.getByText('Waiting for slot T2 at 11:40')).toBeInTheDocument();
    expect(screen.getByText(/Slot T2 11:40: enter long/)).toBeInTheDocument();
    expect(screen.getByText(/in 23 min/)).toBeInTheDocument();
    const plan = screen.getByRole('list', { name: 'Plugin C schedule' });
    expect(within(plan).getAllByRole('listitem')).toHaveLength(2);
    expect(within(plan).getByText('skipped')).toBeInTheDocument();
    expect(screen.queryByText(/"book"/)).not.toBeInTheDocument(); // no raw JSON by default
  });

  it('prints each Last, Next and updated time with a single ET', () => {
    const { container } = wrap(<PluginStatusCard plugin={plugin(status())} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/ET\s+ET/);
    expect(text).toMatch(/· \d\d:\d\d:\d\d ET \(in 23 min\)/); // Next
    expect(text).toMatch(/updated \d\d:\d\d:\d\d ET/);
  });

  it('lists open positions with entry, stop and target', () => {
    const s = status({
      phase: 'in_trade',
      positions: [{ tag: 'T2-1', instrument: 'MNQ', side: 'long', qty: 6, entry: 20000, stop: 19955, targets: [20011], opened_ns: NOW }],
      today: { net: '-12.48', trades: 1 },
    });
    wrap(<PluginStatusCard plugin={plugin(s)} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getByText('In a trade')).toBeInTheDocument();
    expect(screen.getByText('20,000.00')).toBeInTheDocument();
    expect(screen.getByText('19,955.00')).toBeInTheDocument();
    expect(screen.getByText('20,011.00')).toBeInTheDocument();
    // no live P&L from the API: the open value is unknown, not $0.00
    expect(screen.getByTestId('today-line')).toHaveTextContent('closed −$12.48 + open — = — · 1 entry');
  });

  const POS = { instrument: 'MNQ', qty: 2, entry: 20000, stop: 19990, targets: [] as number[], opened_ns: NOW - 10 * MIN,
    entry_t: 80000, open_qty: 2, realized: '-1.24', fees: '1.24', tick_value: '0.50', exit_fee: '0.62' };
  function mark(over: Partial<PositionPnl> = {}): PositionPnl {
    return { mark: 20002.5, mark_t: 80010, mark_source: 'quote', mark_received_ns: NOW - 2e9, age_s: 2, stale: false,
      ticks: 10, open_gross: '10.00', open_net: '7.52', ...over };
  }
  function pnl(positions: Record<string, PositionPnl>, day: Partial<PluginPnl['day']> = {}): PluginPnl {
    return { positions, day: { closed: '-12.48', open: '1.28', total: '-11.20', entries: 3, closed_trades: 1, ...day }, as_of_ns: NOW };
  }

  it('shows live open P&L per position, green and red, and closed + open = today', () => {
    const s = status({
      phase: 'in_trade',
      positions: [
        { ...POS, tag: 'old', side: 'long', opened_ns: NOW - 20 * MIN },
        { ...POS, tag: 'new', side: 'short', opened_ns: NOW - 5 * MIN },
      ],
      today: { net: '-12.48', entries: 3, closed: 1, trades: 3 },
    });
    const p = pnl({ old: mark(), new: mark({ ticks: -10, open_gross: '-5.00', open_net: '-6.24' }) });
    wrap(<PluginStatusCard plugin={plugin(s, { pnl: p })} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    const win = screen.getByTestId('open-pnl-old');
    expect(win).toHaveTextContent('+$7.52 (+10 t)');
    expect(win).toHaveClass('text-bull');
    const loss = screen.getByTestId('open-pnl-new');
    expect(loss).toHaveTextContent('−$6.24 (−10 t)');
    expect(loss).toHaveClass('text-bear');
    expect(screen.getByTestId('today-line')).toHaveTextContent('closed −$12.48 + open $1.28 = −$11.20 · 3 entries · 1 closed');
    expect(screen.getAllByText(/as of/)).toHaveLength(2);
    expect(screen.queryByText('stale')).not.toBeInTheDocument();
    // open positions newest first
    const rows = screen.getAllByTestId(/^open-pnl-/).map((e) => e.dataset.testid);
    expect(rows).toEqual(['open-pnl-new', 'open-pnl-old']);
  });

  it('marks a quote older than 10 s as stale and labels a 1-minute close', () => {
    const s = status({ phase: 'in_trade', positions: [{ ...POS, tag: 'a', side: 'long' }, { ...POS, tag: 'b', side: 'long', opened_ns: NOW - 30 * MIN }] });
    const p = pnl({
      a: mark({ mark_received_ns: NOW - 11e9, stale: false }), // the browser ages it
      b: mark({ mark_source: '1m_close', mark_received_ns: NOW - 30e9 }),
    });
    wrap(<PluginStatusCard plugin={plugin(s, { pnl: p })} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getAllByText('stale')).toHaveLength(1);
    expect(screen.getByText('last 1m close')).toBeInTheDocument();
  });

  it('lists today\'s plan newest first, rows without a time last', () => {
    const s = status({
      plugin_status: {
        schedule: [
          { at_ns: null, label: 'Close: whenever', state: 'planned' },
          { at_ns: NOW - 30 * MIN, label: 'T1 09:45', state: 'done' },
          { at_ns: NOW + 60 * MIN, label: 'T3 13:00', state: 'planned' },
          { at_ns: NOW + 5 * MIN, label: 'T2 11:40', state: 'planned' },
        ],
      },
    });
    wrap(<PluginStatusCard plugin={plugin(s)} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    const plan = screen.getByRole('list', { name: 'Plugin C schedule' });
    expect(within(plan).getAllByRole('listitem').map((li) => li.textContent?.replace(/^(planned|done)/, '')))
      .toEqual(['T3 13:00', 'T2 11:40', 'T1 09:45', 'Close: whenever']);
  });

  it('shows faulted and blocked states', () => {
    wrap(<PluginStatusCard plugin={plugin(status(), { faulted: true })} nowNs={NOW} blocked="HALT file" onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getByText('Stopped (error)')).toBeInTheDocument();
    expect(screen.getByText('HALT file')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Clear/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Halt$/ })).toBeInTheDocument();
  });

  it('keeps Halt when blocked in a trade and warns Clear does not flatten', () => {
    const s = status({
      phase: 'in_trade',
      phase_text: 'In a trade',
      positions: [{ tag: '20261015T0856', side: 'short', qty: 9, instrument: 'MNQ', entry: 1, stop: 0, targets: [], opened_ns: NOW }],
    });
    wrap(<PluginStatusCard plugin={plugin(s)} nowNs={NOW}
      blocked="reconciliation: MNQ order plugin_a:20261015T0856:entry: expected working, not at the broker"
      onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getByRole('button', { name: /Clear/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Halt$/ })).toBeInTheDocument();
    expect(screen.getByText(/Flatten in TopstepX/)).toBeInTheDocument();
  });

  it('works for a plugin with no wording of its own', () => {
    const s = status({ label: 'New plugin', plugin_status: null, next_wake_ns: NOW + 10 * MIN });
    wrap(<PluginStatusCard plugin={plugin(s)} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getAllByText('Waiting for its next setup').length).toBeGreaterThan(0);
    expect(screen.getByText(/Next scheduled check/)).toBeInTheDocument();
  });

  it('opens the raw details on demand', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify({ id: 'plugin_c', state: { seq: 3 } }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    wrap(<PluginStatusCard plugin={plugin(status())} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: /Details/ }));
    expect(await screen.findByText(/"seq": 3/)).toBeInTheDocument();
    expect(String(fetch.mock.calls[0][0])).toBe('/api/live/plugins/plugin_c/raw');
    fetch.mockRestore();
  });

  function paperWithLoss(over: Partial<PaperPlugin> = {}): PaperPlugin {
    return {
      id: 'plugin_c', account: 'paper', realized: '-120.00', unrealized: null, open_positions: [], working_orders: [],
      day_trades: 2, win_rate_today: null, cumulative_net: '-120.00',
      caps: { max_contracts: 16, used: 0, max_daily_loss: '800.00', day_net: '-120.00' },
      blocks: [], price_source: {}, journal_failed: false, ...over,
    };
  }

  it('shows Reset day loss and the daily loss budget when the host books max_daily_loss', async () => {
    const onReset = vi.fn();
    wrap(<PluginStatusCard plugin={plugin(status())} nowNs={NOW} blocked={null}
      paper={paperWithLoss()} onHalt={() => {}} onClear={() => {}} onResetDayLoss={onReset} />);
    expect(screen.getByTestId('day-loss-plugin_c')).toHaveTextContent(/−\$120\.00 of −\$800\.00/);
    await userEvent.click(screen.getByRole('button', { name: 'Reset day loss' }));
    expect(onReset).toHaveBeenCalledOnce();
  });

  it('hides Reset day loss when the card has no reset handler', () => {
    wrap(<PluginStatusCard plugin={plugin(status())} nowNs={NOW} blocked={null}
      paper={paperWithLoss()} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.queryByRole('button', { name: 'Reset day loss' })).not.toBeInTheDocument();
  });
});

describe('live page', () => {
  const SNAP = {
    run_dir: 'run-x', now_ns: NOW, heartbeat: { connected: true, transport: 'stream', subscription: 'sim', t_ns: NOW },
    skylit: { day: '2026-10-12', symbols: ['QQQ'], credits_per_day: 3488, refresh_interval_s: 15, window_start: '09:00', window_end: '16:15', exchange_session: true, open: false },
    host_log: [
      { t_ns: NOW - MIN, kind: 'plugin_loaded', level: 'info', plugin: 'plugin_c', say: { text: 'Plugin plugin_c loaded', severity: 'info', category: 'lifecycle' } },
      { t_ns: NOW, kind: 'feed_status', level: 'warning', say: { text: 'quotes feed down: lost', severity: 'warning', category: 'data' } },
    ],
    faults: [], plugins: [plugin(status())],
    blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false },
  };
  function mock(mode: 'run' | 'paper', log: string[] = []) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const body = url.startsWith('/api/live') ? SNAP
        : url.startsWith('/api/runner') ? { controller: 'process', state: 'running', pid: 1, started_at: null, last_exit_code: null, last_exit_meaning: null, settings: { mode, runner_config: 'runner.paper', strategy_config: 'playbook_baseline' }, log }
          : [];
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
  }
  function renderLive() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/live']}>
          <TestSession session={SESSION}><ToastProvider><AppRoutes /></ToastProvider></TestSession>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('shows Skylit credits, the plugin card and filters the activity to problems', async () => {
    const f = mock('run');
    renderLive();
    expect(await screen.findByText('Plugin plugin_c loaded')).toBeInTheDocument();
    expect(screen.getByText(/3,488 credits\/day/)).toBeInTheDocument();
    expect(screen.getByText('Waiting for slot T2 at 11:40')).toBeInTheDocument();
    expect(screen.queryByText('Strategy_Config')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Warnings and errors' }));
    expect(screen.queryByText('Plugin plugin_c loaded')).not.toBeInTheDocument();
    expect(screen.getByText(/quotes feed down/)).toBeInTheDocument();
    f.mockRestore();
  });

  it('keeps Live primary actions available and exposes collapsed activity/log affordances', async () => {
    const f = mock('run');
    const { container } = renderLive();
    expect(await screen.findByRole('button', { name: 'Halt all' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Start' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restart' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Halt' })).toBeInTheDocument(); // plugin Halt
    expect(container.querySelector('.live-page')).not.toBeNull();
    expect(container.querySelector('.live-activity-collapse')).not.toBeNull();
    expect(container.querySelector('.runner-log-collapse')).not.toBeNull();
    expect(screen.getByText('Runner activity · run-x')).toBeInTheDocument();
    expect(screen.getByText('Runner log')).toBeInTheDocument();
    f.mockRestore();
  });

  it('shows the runner log newest first', async () => {
    const f = mock('run', ['$ fse run --config runner.yaml', 'first line', 'second line', 'runner exited 0 (ok)']);
    const { container } = renderLive();
    expect(await screen.findByText('runner exited 0 (ok)')).toBeInTheDocument();
    const lines = [...container.querySelectorAll('pre.logview > div')].map((d) => d.textContent);
    expect(lines).toEqual(['runner exited 0 (ok)', 'second line', 'first line', '$ fse run --config runner.yaml']);
    f.mockRestore();
  });

  it('explains the legacy Playbook-only mode', async () => {
    const f = mock('paper');
    renderLive();
    expect(await screen.findByText(LEGACY_PAPER_TEXT)).toBeInTheDocument();
    f.mockRestore();
  });

  it('confirms Reset day loss and posts the plugin reset endpoint', async () => {
    const calls: { url: string; method: string; body: unknown }[] = [];
    const snap = {
      ...SNAP,
      paper: {
        day: '2026-10-12',
        plugins: {
          plugin_c: {
            id: 'plugin_c', account: 'paper', realized: '-120.00', unrealized: null,
            open_positions: [], working_orders: [], day_trades: 2, win_rate_today: null,
            cumulative_net: '-120.00',
            caps: { max_contracts: 16, used: 0, max_daily_loss: '800.00', day_net: '-120.00' },
            blocks: [], price_source: {}, journal_failed: false,
          },
        },
        total: { realized: '-120.00', unrealized: null, day_trades: 2, win_rate_today: null, cumulative_net: '-120.00' },
      },
    };
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method !== 'GET') {
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
        return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      const body = url.startsWith('/api/live') ? snap
        : url.startsWith('/api/runner') ? { controller: 'process', state: 'running', pid: 1, started_at: null, last_exit_code: null, last_exit_meaning: null, settings: { mode: 'run', runner_config: 'runner.paper', strategy_config: 'playbook_baseline' }, log: [] }
          : url.startsWith('/api/order-modes') ? { runner_config: 'runner', sha: 'x', runner_state: 'running', held: false, plugin_orders: false, disabled_reasons: [], combine_disabled_reasons: [], switch_pending: false, plugins: [] }
            : [];
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderLive();
    await userEvent.click(await screen.findByRole('button', { name: 'Reset day loss' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Reset today's max loss for plugin_c/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reset day loss' }));
    expect(calls).toEqual([{
      url: '/api/plugins/plugin_c/reset-day-loss',
      method: 'POST',
      body: { reason: 'reset today max loss from Live' },
    }]);
    expect(await screen.findByText(/Day loss reset for plugin_c/)).toBeInTheDocument();
    f.mockRestore();
  });
});

describe('order mode badge', () => {
  const eff = (requested: 'paper' | 'practice' | 'combine', mode: 'paper' | 'practice' | 'combine', failures: string[] = []) => ({ requested, mode, failures, t_ns: NOW });
  it('names the four states', () => {
    expect(orderModeBadge('practice', eff('practice', 'practice')).text).toBe('Practice in use');
    expect(orderModeBadge('practice', eff('practice', 'paper', ['PRACTICE_ACCOUNT_ID is not set'])).text)
      .toBe('Requested practice, running Paper: PRACTICE_ACCOUNT_ID is not set');
    expect(orderModeBadge('practice', null).text).toBe('Requested practice, waiting for the runner');
    expect(orderModeBadge('practice', eff('paper', 'paper')).text).toBe('Requested practice, waiting for the runner');
    expect(orderModeBadge('combine', null).text).toBe('Requested combine, waiting for the runner');
    expect(orderModeBadge('combine', eff('combine', 'combine')).text).toBe('Combine in use');
    expect(orderModeBadge('paper', eff('paper', 'paper')).text).toBe('Paper');
    expect(orderModeBadge(null, null).text).toBe('Paper');
  });

  it('shows on the plugin card with the live order_mode line', () => {
    wrap(<PluginStatusCard plugin={plugin(status(), { order_mode: eff('practice', 'paper', ['plugin orders are off']) })}
      nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.getByTestId('order-mode-plugin_c')).toHaveTextContent('Requested practice, running Paper: plugin orders are off');
  });

  it('is absent for a plugin that sends no orders', () => {
    wrap(<PluginStatusCard plugin={plugin(status())} nowNs={NOW} blocked={null} onHalt={() => {}} onClear={() => {}} />);
    expect(screen.queryByTestId('order-mode-plugin_c')).not.toBeInTheDocument();
  });
});

describe('live page order mode', () => {
  const SNAP = {
    run_dir: 'run-x', now_ns: NOW, heartbeat: null, skylit: null, host_log: [], faults: [],
    plugins: [plugin(status({ plugin: 'plugin_a', label: 'Plugin A' }), { id: 'plugin_a' })],
    blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false },
  };
  function om(over: Partial<OrderModePlugin> = {}): OrderModePlugin {
    return { id: 'plugin_a', label: 'Plugin A', order_mode: 'paper', max_daily_loss: null, max_trades_per_day: null,
      hold_through_break: true, practice_blocked: [], combine_blocked: [], effective: null, busy: false, ...over };
  }
  function modes(over: Partial<OrderModes> = {}): OrderModes {
    return { runner_config: 'runner', sha: 'abcdef123456', runner_state: 'running', held: false, plugin_orders: true,
      disabled_reasons: [], combine_disabled_reasons: [], switch_pending: false, plugins: [om()], ...over };
  }
  type Call = { url: string; method: string; body: any };
  function mock(m: OrderModes, state: RunnerState = 'running', post?: (c: Call) => { status: number; body: unknown }) {
    const calls: Call[] = [];
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const call = { url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined };
      if (method !== 'GET') calls.push(call);
      const res = method !== 'GET' && post ? post(call)
        : { status: 200, body: url.startsWith('/api/live') ? SNAP
          : url.startsWith('/api/order-modes') ? m
            : url.startsWith('/api/runner') ? { controller: 'systemd', state, pid: 1, started_at: null, last_exit_code: null, last_exit_meaning: null, settings: { mode: 'run', runner_config: 'runner', strategy_config: 'playbook_baseline' }, log: [] }
              : [] };
      return new Response(JSON.stringify(res.body), { status: res.status, headers: { 'Content-Type': 'application/json' } });
    });
    return { f, calls };
  }
  function renderLive() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/live']}>
          <TestSession session={SESSION}><ToastProvider><AppRoutes /></ToastProvider></TestSession>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }
  async function openChange() {
    await userEvent.click(await screen.findByRole('button', { name: 'Change order mode of Plugin A' }));
    return screen.getByRole('dialog');
  }

  it('lists global and per-plugin reasons, disables Practice and Combine when blocked', async () => {
    const { f, calls } = mock(modes({
      disabled_reasons: ['plugin orders are off for this deployment'],
      combine_disabled_reasons: ['plugin orders are off for this deployment'],
      plugins: [om({
        practice_blocked: ['another plugin (plugin_b) already uses a non-paper order mode'],
        combine_blocked: ['combine needs a paper account with the flat deadline on'],
      })],
    }));
    renderLive();
    const dialog = await openChange();
    const practice = within(dialog).getByRole('radio', { name: /Practice/ });
    const combine = within(dialog).getByRole('radio', { name: /Combine/ });
    expect(practice).toBeDisabled();
    expect(combine).toBeDisabled();
    expect(within(dialog).getAllByRole('radio')).toHaveLength(3);
    const practiceList = within(dialog).getByRole('list', { name: 'Why Practice is not available' });
    expect(within(practiceList).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'plugin orders are off for this deployment', 'another plugin (plugin_b) already uses a non-paper order mode']);
    const combineList = within(dialog).getByRole('list', { name: 'Why Combine is not available' });
    expect(within(combineList).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'plugin orders are off for this deployment', 'combine needs a paper account with the flat deadline on']);
    expect(within(dialog).getByText(PAPER_NOTE)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled(); // already paper
    expect(calls).toHaveLength(0);
    f.mockRestore();
  });

  it('posts Combine with caps after confirm', async () => {
    const { f, calls } = mock(modes(), 'running', () => ({ status: 200, body: { ...modes({ plugins: [om({ order_mode: 'combine' })] }),
      saved: '123456abcdef', restarted: true, message: 'saved revision 123456abcdef; the runner is restarting with it', note: null } }));
    renderLive();
    const dialog = await openChange();
    await userEvent.click(within(dialog).getByRole('radio', { name: /Combine/ }));
    await userEvent.type(within(dialog).getByLabelText(/Max daily loss/), '800');
    await userEvent.type(within(dialog).getByLabelText(/Max trades per day/), '30');
    expect(within(dialog).getByText(/Combine also sets hold_through_break: false/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(RESTART_CONFIRM_TEXT)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Save and restart' }));
    expect(await screen.findByText(/the runner is restarting with it/)).toBeInTheDocument();
    expect(calls).toEqual([{ url: '/api/order-mode', method: 'POST', body: {
      plugin_id: 'plugin_a', order_mode: 'combine', expected_sha: 'abcdef123456', confirm_restart: true,
      max_daily_loss: '800', max_trades_per_day: 30 } }]);
    f.mockRestore();
  });

  it('links to Find my ProjectX accounts when the practice account id matches no account', async () => {
    const { f } = mock(modes({ plugins: [om({ practice_blocked: ['PRACTICE_ACCOUNT_ID matches no account the Broker_Adapter resolved'] })] }));
    renderLive();
    const dialog = await openChange();
    expect(within(dialog).getByRole('link', { name: 'Find my ProjectX accounts' })).toHaveAttribute('href', '/secrets');
    f.mockRestore();
  });
  it('has no account lookup link for other reasons', async () => {
    const { f } = mock(modes({ disabled_reasons: ['plugin orders are off for this deployment'] }));
    renderLive();
    const dialog = await openChange();
    expect(within(dialog).queryByRole('link', { name: 'Find my ProjectX accounts' })).not.toBeInTheDocument();
    f.mockRestore();
  });
  it('confirms the restart before it posts Practice with the caps', async () => {
    const { f, calls } = mock(modes(), 'running', () => ({ status: 200, body: { ...modes({ plugins: [om({ order_mode: 'practice' })] }),
      saved: '123456abcdef', restarted: true, message: 'saved revision 123456abcdef; the runner is restarting with it', note: null } }));
    renderLive();
    const dialog = await openChange();
    await userEvent.click(within(dialog).getByRole('radio', { name: /Practice/ }));
    const save = within(dialog).getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled(); // caps are required
    await userEvent.type(within(dialog).getByLabelText(/Max daily loss/), '375.50');
    await userEvent.type(within(dialog).getByLabelText(/Max trades per day/), '4');
    await userEvent.click(save);
    expect(await screen.findByText(RESTART_CONFIRM_TEXT)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Save and restart' }));
    expect(await screen.findByText(/the runner is restarting with it/)).toBeInTheDocument();
    expect(calls).toEqual([{ url: '/api/order-mode', method: 'POST', body: {
      plugin_id: 'plugin_a', order_mode: 'practice', expected_sha: 'abcdef123456', confirm_restart: true,
      max_daily_loss: '375.50', max_trades_per_day: 4 } }]);
    f.mockRestore();
  });

  it('says Practice starts at the next Start when the runner is stopped, and re-confirms a 409 confirm_required', async () => {
    let n = 0;
    const { f, calls } = mock(modes({ runner_state: 'stopped' }), 'stopped', () => (n++ === 0
      ? { status: 409, body: { detail: { code: 'confirm_required', message: RESTART_CONFIRM_TEXT } } }
      : { status: 200, body: { ...modes(), saved: '123456abcdef', restarted: true, message: 'saved revision 123456abcdef; the runner is restarting with it', note: null } }));
    renderLive();
    const dialog = await openChange();
    await userEvent.click(within(dialog).getByRole('radio', { name: /Practice/ }));
    await userEvent.type(within(dialog).getByLabelText(/Max daily loss/), '375');
    await userEvent.type(within(dialog).getByLabelText(/Max trades per day/), '3');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(STOPPED_CONFIRM_TEXT)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    const confirm = screen.getByText(STOPPED_CONFIRM_TEXT).closest('[role=dialog]') as HTMLElement;
    await userEvent.click(within(confirm).getByRole('button', { name: 'Save' }));
    // the runner started meanwhile: the restart confirm comes back before a second POST
    expect(await screen.findByText(RESTART_CONFIRM_TEXT)).toBeInTheDocument();
    expect(calls.map((c) => c.body.confirm_restart)).toEqual([false]);
    await userEvent.click(screen.getByRole('button', { name: 'Save and restart' }));
    expect(await screen.findByText(/the runner is restarting with it/)).toBeInTheDocument();
    expect(calls.map((c) => c.body.confirm_restart)).toEqual([false, true]);
    f.mockRestore();
  });

  it('confirms Start when the selected config has a Practice plugin', async () => {
    const { f, calls } = mock(modes({ runner_state: 'stopped', plugins: [om({ order_mode: 'practice', max_daily_loss: '375.00', max_trades_per_day: 3 })] }),
      'stopped', () => ({ status: 200, body: {} }));
    renderLive();
    await screen.findByRole('button', { name: 'Change order mode of Plugin A' });
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(screen.getByText(new RegExp(PRACTICE_ORDERS_TEXT.replace('.', '\\.')))).toBeInTheDocument();
    expect(screen.getByText(/uses a non-paper order mode/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Start runner' }));
    await vi.waitFor(() => expect(calls.map((c) => c.url)).toEqual(['/api/runner/start']));
    f.mockRestore();
  });

  it('starts without a confirm when every plugin is paper', async () => {
    const { f, calls } = mock(modes({ runner_state: 'stopped' }), 'stopped', () => ({ status: 200, body: {} }));
    renderLive();
    await screen.findByRole('button', { name: 'Change order mode of Plugin A' });
    await userEvent.click(await screen.findByRole('button', { name: 'Start' }));
    await vi.waitFor(() => expect(calls.map((c) => c.url)).toEqual(['/api/runner/start']));
    f.mockRestore();
  });

  it('shows the switch_pending banner and confirms its Restart', async () => {
    const { f, calls } = mock(modes({ plugin_orders: false, switch_pending: true, disabled_reasons: ['plugin orders are off for this deployment'],
      plugins: [om({ order_mode: 'practice', effective: { requested: 'practice', mode: 'practice', failures: [], t_ns: NOW } })] }),
    'running', () => ({ status: 200, body: {} }));
    renderLive();
    expect(await screen.findByText('plugin orders are off for this deployment, but plugin_a is still in a broker mode until the runner restarts')).toBeInTheDocument();
    expect(screen.getAllByText('Practice in use').length).toBeGreaterThan(0);
    const banner = screen.getByText(/is still in a broker mode until/).closest('.fse-banner') as HTMLElement;
    await userEvent.click(within(banner).getByRole('button', { name: 'Restart' }));
    expect(await screen.findByText('Restart the runner?')).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Restart runner' }));
    await vi.waitFor(() => expect(calls.map((c) => c.url)).toEqual(['/api/runner/restart']));
    f.mockRestore();
  });

  it('disables Restart and Order mode Save while the runner is starting', async () => {
    const { f } = mock(modes({ runner_state: 'starting' }), 'starting');
    renderLive();
    expect(await screen.findByRole('button', { name: 'Restart' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Change order mode of Plugin A' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/Runner is changing state/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();
    f.mockRestore();
  });
});
