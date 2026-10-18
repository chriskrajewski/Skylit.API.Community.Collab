import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import { CARDS_EMPTY_TEXT } from '../pages/Live';
import { CARDS_RESTART_TEXT } from '../pages/Plugins';
import type { CardRow, CardsResponse, DecisionCard, Session } from '../lib/types';

const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};
const S = 1e9;
const NOW = 1_791_800_000 * S;

function card(over: Partial<DecisionCard> = {}): DecisionCard {
  return {
    id: 'a1', title: 'Long MNQ at the open', as_of_ns: NOW, action: 'enter', instrument: 'MNQ', symbol: null,
    side: 'long', qty: 1, entry: 20000.25, stop: 19990, targets: [20010], reason: 'above the EMA', ref: null,
    tag: 'a1', result: null, fields: [['EMA', '21000.5']], ...over,
  };
}

function decision(plugin: string, t: number, c: DecisionCard, result: CardRow['result'] = null): CardRow {
  return { plugin, t_ns: t, format: 'decision', kind: 'decision', card: c, result, raw: { t_ns: t, plugin, card: c } };
}

const ALL: CardsResponse = {
  run_dir: 'run-x', runner_config: 'runner',
  plugins: [
    { id: 'alpha', publish: true, count: 1, hidden: false },
    { id: 'beta', publish: true, count: 1, hidden: false },
    { id: 'gamma', publish: false, count: 4, hidden: true },
  ],
  cards: [
    decision('beta', NOW + 60 * S, card({ id: 'b1', title: 'Skip MES: no setup', action: 'skip', side: null })),
    decision('alpha', NOW, card(), {
      t_ns: NOW + 120 * S, raw: { kind: 'result' },
      card: card({ id: 'a1-r', title: 'Stopped out', action: 'result', ref: 'a1', result: { net: '-12.40', exit_reason: 'stop', r: '-1.00', ticks: -8 } }),
    }),
  ],
};

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <TestSession session={SESSION}><ToastProvider><AppRoutes /></ToastProvider></TestSession>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SNAP = { run_dir: null, host_log: [], heartbeat: null, faults: [], plugins: [], blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false } };

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('cards page', () => {
  it('lists cards from every plugin newest first, filters by plugin, shows the result and raw JSON', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/live') return json(SNAP);
      if (url.startsWith('/api/live/cards')) {
        const plugin = new URL(url, 'http://x').searchParams.get('plugin');
        return json(plugin ? { ...ALL, cards: ALL.cards.filter((c) => c.plugin === plugin) } : ALL);
      }
      return json([]);
    });
    renderAt('/cards');
    const [list] = await screen.findAllByText('Skip MES: no setup');
    const rows = [...list.closest('.filelist')!.querySelectorAll('button')].map((b) => b.textContent);
    expect(rows[0]).toMatch(/^beta.*Skip MES: no setup.*skip/);
    expect(rows[1]).toMatch(/^alpha.*Long MNQ at the open.*enter/);
    expect(screen.getByText(/Cards hidden: gamma/)).toBeInTheDocument();
    expect(screen.getByText(/runner config runner/)).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole('button', { name: /Long MNQ at the open/ })[0]);
    // Decision cards mount FindingCard (rich .fse-tcard), not the old KeyValue panel.
    expect(screen.getByText('MNQ')).toBeInTheDocument();
    expect(screen.getAllByText(/above the EMA/).length).toBeGreaterThan(0);
    expect(screen.getByText('Why this decision')).toBeInTheDocument();
    expect(screen.getByText('−$12.40')).toHaveClass('text-bear');
    expect(screen.queryByText(/"plugin": "alpha"/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show raw JSON' }));
    expect(screen.getByText(/"plugin": "alpha"/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Plugin'), 'beta');
    await waitFor(() => expect(screen.queryByText('Long MNQ at the open')).not.toBeInTheDocument());
    expect(screen.getAllByText('Skip MES: no setup').length).toBeGreaterThan(0);
    expect(f.mock.calls.some(([u]) => String(u) === '/api/live/cards?plugin=beta')).toBe(true);
    f.mockRestore();
  });

  it('mounts rich Pass/skip chrome for unpriced skip decision cards with why', async () => {
    const skip = decision('beta', NOW, card({
      id: 's1', title: 'Slot 09:45 skipped', action: 'skip', side: null, qty: null,
      entry: null, stop: null, targets: [], reason: 'late_bar (800 ms)',
      fields: [['slot', '09:45']], spot: 20100.25,
      bars: Array.from({ length: 40 }, (_, i) => ({ o: 20090 + i * 0.25, h: 20091 + i * 0.25, l: 20089 + i * 0.25, c: 20090.5 + i * 0.25 })),
    }));
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => json(String(input) === '/api/live' ? SNAP : { ...ALL, plugins: [], cards: [skip] }));
    renderAt('/cards');
    expect(await screen.findByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('Skip — no order armed')).toBeInTheDocument();
    expect(screen.getByText('Why this skipped')).toBeInTheDocument();
    expect(screen.getAllByText(/late bar/).length).toBeGreaterThan(0);
    expect(screen.getByText(/last 40 × 1-min/)).toBeInTheDocument();
    f.mockRestore();
  });

  it('mounts FindingCard for finding_card rows, shows rich Pass chrome, and keeps raw JSON behind a toggle', async () => {
    const finding: CardRow = {
      plugin: 'gamma', t_ns: NOW, format: 'finding_card', title: 'Finding_Card', source: 'live-x/cards.jsonl',
      raw: {
        narration: 'Already used the allowed taps; reward to risk was too thin.',
        card: {
          kind: 'decision',
          decision_time_ny: '2026-10-14T10:20:00-04:00',
          order_mode: 'paper',
          regime: 'Positive_Gamma',
          map_grade: 'B',
          trinity: { SPX: 'below', SPY: 'below', QQQ: 'below' },
          map: [{ symbol: 'ES', metric: 'gamma', king: { level: 5900 }, ceiling: { level: 5925 }, floor: { level: 5850 } }],
          spot: { ES: 5884.25 },
          setups: [{
            grade: 'Pass',
            key: 'MES gatekeeper_fade short 5910 tap 1',
            reasons: ['tap_count', 'min_reward_risk'],
            why: ['Already used the allowed taps at this node', 'Reward:risk is below the minimum gate'],
          }],
          trade: {
            instrument: 'MES', name: 'Micro E-mini S&P', direction: 'short', grade: 'Pass',
            pattern: 'gatekeeper_fade', node: 'ES 5910 gatekeeper', tap: 1, spot: 5884.25,
            dayPct: -0.18, rangePct: 0.62, tick: 0.25, tickValue: 1.25, qty: 5, rr: 2.4, gates: '13/15',
            entry: 5910.25, stop: 5914.5,
            targets: [{ label: 'TP1', price: 5900, qty: 3 }, { label: 'TP2', price: 5880, qty: 2 }],
            plan: { if: 'Rallies into 5910', out: 'Stop 5914.50', tgt: '5900 then 5880' },
            action: 'PASS · no order', actionLabel: 'Pass', bars: [],
            odds: { label: 'gatekeeper_fade · n=212', items: [{ pct: 64, label: 'fill · this session' }] },
          },
        },
      },
    };
    let body: CardsResponse = { ...ALL, plugins: [], cards: [finding] };
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => json(String(input) === '/api/live' ? SNAP : body));
    const { unmount } = renderAt('/cards');
    expect(await screen.findByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('Pass — no order armed')).toBeInTheDocument();
    expect(screen.getByText('Why this passed')).toBeInTheDocument();
    expect(screen.getByText(/Already used the allowed taps/)).toBeInTheDocument();
    expect(screen.queryByText(/"gatekeeper_fade"/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show raw JSON' }));
    expect(screen.getByText(/"gatekeeper_fade"/)).toBeInTheDocument();
    unmount();
    body = { run_dir: null, runner_config: null, plugins: [], cards: [] };
    renderAt('/cards');
    expect(await screen.findByText(CARDS_EMPTY_TEXT)).toBeInTheDocument();
    f.mockRestore();
  });

  it('shows compact premarket summary without a trade', async () => {
    const finding: CardRow = {
      plugin: 'gamma', t_ns: NOW, format: 'finding_card', title: 'Premarket', source: 'live-x/cards.jsonl',
      raw: {
        card: {
          kind: 'premarket',
          decision_time_ny: '2026-10-14T09:20:00-04:00',
          order_mode: 'paper',
          regime: 'Whipsaw',
          map_grade: 'C',
          summary: 'Premarket map grade C · Whipsaw — no A+ / 2R setup yet; waiting for the open.',
          map: [{ symbol: 'ES', metric: 'gamma', king: { level: 5925 }, ceiling: { level: 5925 }, floor: { level: 5850 } }],
          spot: { ES: 5871 },
          setups: 'none',
        },
      },
    };
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => json(String(input) === '/api/live' ? SNAP : { ...ALL, plugins: [], cards: [finding] }));
    renderAt('/cards');
    expect(await screen.findByText(/waiting for the open/)).toBeInTheDocument();
    expect(screen.getAllByText(/Premarket/).length).toBeGreaterThan(0);
    expect(screen.queryByText('Pass — no order armed')).not.toBeInTheDocument();
    f.mockRestore();
  });
});

describe('plugins page cards switch', () => {
  it('toggles plugins[].cards in the runner YAML and says when it takes effect', async () => {
    const text = 'version: 1\nplugins:\n  - id: alpha\n    module: pkg.mod:Alpha\n';
    const posts: string[] = [];
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/live') return json(SNAP);
      if (url === '/api/configs/runner/runner' && init?.method === 'POST') {
        const sent = JSON.parse(String(init.body)).text as string;
        posts.push(sent);
        return json({ validation: { ok: true, problems: [], warnings: [] }, config: { kind: 'runner', name: 'runner', sha: 'b', current: 'b', origin: null, text: sent, revisions: [], path: 'x' } });
      }
      if (url.startsWith('/api/configs/runner/runner')) return json({ kind: 'runner', name: 'runner', sha: 'a', current: 'a', origin: null, text, revisions: [], path: 'x' });
      if (url.startsWith('/api/schemas/runner')) {
        return json({ $defs: { PluginEntry: { properties: { cards: { description: "Publish this plugin's decision cards to the Finding Cards page." } } } } });
      }
      if (url.startsWith('/api/plugins/backtested')) return json({ runners: {} });
      // Save runs the plugin check first (paper accounts: problems shown inline).
      if (url === '/api/plugins/check') return json({ ok: true, problems: [], plugins: [] });
      return json([]);
    });
    renderAt('/plugins');
    const sw = await screen.findByRole('switch', { name: 'Publish Finding Cards' });
    expect(sw).toBeChecked();
    const panel = sw.closest('.hint-switch') as HTMLElement;
    expect(within(panel).getByText(new RegExp(CARDS_RESTART_TEXT.replace(/[.;]/g, '.')))).toBeInTheDocument();
    expect(within(panel).getByText(/decision cards to the Finding Cards page/)).toBeInTheDocument();
    await userEvent.click(sw);
    expect(sw).not.toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: 'Save revision' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatch(/cards: false/);
    f.mockRestore();
  });

  it('edits host risk caps and saves them on the live runner path', async () => {
    const text = `version: 1
plugins:
  - id: plugin_a
    module: research.plugin_a.plugin:PluginA
    fills: {trade_through_ticks: 1, slippage_ticks: 0, stop_fill: at_stop, commission_rt: {MNQ: "1.24"}}
    risk: {max_contracts: 16, max_open_trades: 1}
`;
    const posts: { url: string; body: any }[] = [];
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/live') return json(SNAP);
      if (init?.method === 'POST') posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.startsWith('/api/configs?kind=runner')) {
        return json([{ kind: 'runner', name: 'runner', current: 'a', revisions: 1, path: 'x' }]);
      }
      if (url === '/api/configs/runner/runner' && init?.method === 'POST') {
        return json({
          validation: { ok: true, problems: [], warnings: [] },
          config: {
            kind: 'runner', name: 'runner', sha: 'b', current: 'b', origin: null,
            text: JSON.parse(String(init.body)).text, revisions: [], path: 'x',
          },
        });
      }
      if (url.startsWith('/api/configs/runner/runner')) {
        return json({ kind: 'runner', name: 'runner', sha: 'a', current: 'a', origin: null, text, revisions: [], path: 'x' });
      }
      if (url.startsWith('/api/plugins/known')) {
        return json([{ module: 'research.plugin_a.plugin:PluginA', label: 'Plugin A', brokerage: 'host', note: 'n', has_schema: true }]);
      }
      if (url.startsWith('/api/schemas/plugin')) return json({ type: 'object', properties: {} });
      if (url.startsWith('/api/schemas/runner')) {
        return json({
          $defs: {
            RiskBlock: { properties: { max_daily_loss: { description: 'Day P&L halt and stop-risk budget.' } } },
            FillsBlock: { properties: {} },
            PluginEntry: { properties: { hold_through_break: { description: 'Hold through RTH break.' }, cards: { description: 'cards' } } },
          },
        });
      }
      if (url.startsWith('/api/plugins/backtested')) return json({ runners: {} });
      if (url === '/api/plugins/check') return json({ ok: true, problems: [], plugins: [] });
      return json([]);
    });
    renderAt('/plugins');
    expect(await screen.findByText(/risk \(host paper book\)/)).toBeInTheDocument();
    const loss = await screen.findByLabelText(/max_daily_loss/);
    await userEvent.clear(loss);
    await userEvent.type(loss, '800');
    const trades = screen.getByLabelText(/max_trades_per_day/);
    await userEvent.clear(trades);
    await userEvent.type(trades, '30');
    await userEvent.click(screen.getByRole('button', { name: 'Save revision' }));
    await waitFor(() => expect(posts.some((p) => p.url === '/api/configs/runner/runner')).toBe(true));
    const save = posts.find((p) => p.url === '/api/configs/runner/runner')!;
    expect(save.body.create).toBeUndefined();
    expect(save.body.text).toMatch(/max_daily_loss: ['"]?800['"]?/);
    expect(save.body.text).toMatch(/max_trades_per_day: 30/);
    f.mockRestore();
  });

  it('Save as creates a new runner name without create:false overwrite', async () => {
    const text = 'version: 1\nplugins:\n  - id: alpha\n    module: pkg.mod:Alpha\n';
    const posts: { url: string; body: any }[] = [];
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/live') return json(SNAP);
      if (init?.method === 'POST') posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.startsWith('/api/configs?kind=runner')) {
        return json([{ kind: 'runner', name: 'runner', current: 'a', revisions: 1, path: 'x' }]);
      }
      if (url === '/api/configs/runner/bt-plugin_a-ab' && init?.method === 'POST') {
        return json({
          validation: { ok: true, problems: [], warnings: [] },
          config: {
            kind: 'runner', name: 'bt-plugin_a-ab', sha: 'c', current: 'c', origin: null,
            text, revisions: [], path: 'y',
          },
        });
      }
      if (url.startsWith('/api/configs/runner/')) {
        return json({ kind: 'runner', name: 'runner', sha: 'a', current: 'a', origin: null, text, revisions: [], path: 'x' });
      }
      if (url.startsWith('/api/schemas/runner')) return json({ $defs: {} });
      if (url.startsWith('/api/plugins/backtested')) return json({ runners: {} });
      if (url === '/api/plugins/check') return json({ ok: true, problems: [], plugins: [] });
      return json([]);
    });
    renderAt('/plugins');
    await screen.findByRole('button', { name: /Save as/ });
    await userEvent.click(screen.getByRole('button', { name: /Save as/ }));
    const nameField = await screen.findByLabelText(/New name/);
    await userEvent.type(nameField, 'bt-plugin_a-ab');
    await userEvent.click(screen.getByRole('button', { name: /Create copy/ }));
    await waitFor(() => expect(posts.some((p) => p.url === '/api/configs/runner/bt-plugin_a-ab')).toBe(true));
    const create = posts.find((p) => p.url === '/api/configs/runner/bt-plugin_a-ab')!;
    expect(create.body.create).toBe(true);
    expect(posts.filter((p) => p.url === '/api/configs/runner/runner')).toHaveLength(0);
    f.mockRestore();
  });
});
