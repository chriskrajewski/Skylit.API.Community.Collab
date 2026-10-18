import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrategyCard } from '../ds/index.js';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import type { Session, StrategyCardView } from '../lib/types';

const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};

const VIEW: StrategyCardView = {
  id: 'c1',
  name: 'ema-cross-supertrend-pullback',
  hypothesis: 'The cross lifts the 2R hit rate.',
  grade: 'PASS',
  source: {
    url: 'https://x.com/trader/status/1',
    author: 'trader',
    post_id: '1',
    text: '10/21 EMA cross, buy the pullback.',
  },
  rules: ['long MNQ on 1m bars', 'scale out 25% at 4R'],
  probabilities: [
    { label: '1R', pct: 40 },
    { label: '2R', pct: 22 },
    { label: '3R', pct: 11 },
    { label: '4R+', pct: 6 },
  ],
  judge: [
    { name: 'lift_vs_baseline', passed: true, detail: 'lift 0.12' },
    { name: 'outside_group', passed: false, detail: 'no outside symbol group' },
  ],
  equity: { trades: 18, net: '240.00', expectancy_r: '0.35' },
  side: 'long',
  symbols: ['MNQ'],
  timeframe: '1m',
  spec_hash: 'abc',
  design_hash: 'def',
  created_at: 1,
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

const SNAP = {
  run_dir: null, host_log: [], heartbeat: null, faults: [], plugins: [],
  blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false },
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('strategy card', () => {
  it('shows the post, the rules, the R probabilities, and the judge', () => {
    render(<StrategyCard card={VIEW} />);
    expect(screen.getByText('10/21 EMA cross, buy the pullback.')).toBeInTheDocument();
    expect(screen.getByText('scale out 25% at 4R')).toBeInTheDocument();
    expect(screen.getByText('22.0%')).toBeInTheDocument();
    expect(screen.getByText('lift 0.12')).toBeInTheDocument();
    expect(screen.getByText('no outside symbol group')).toBeInTheDocument();
    expect(screen.getByText('0.35R')).toBeInTheDocument();
    expect(screen.getAllByText('PASS').length).toBeGreaterThan(0);
  });

  it('lists strategy cards on the Cards page without replacing findings', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/live') return json(SNAP);
      if (url.startsWith('/api/live/cards')) {
        return json({ run_dir: null, runner_config: null, plugins: [], cards: [] });
      }
      if (url.startsWith('/api/cards/') && url.includes('/render')) return json(VIEW);
      if (url.startsWith('/api/cards')) {
        return json({
          cards: [{
            id: 'c1', created_at: 1, name: VIEW.name, author: 'trader', post_id: '1',
            hypothesis: VIEW.hypothesis, spec_hash: 'abc', design_hash: 'def', grade: 'PASS',
            symbols: ['MNQ'], timeframe: '1m',
          }],
        });
      }
      return json([]);
    });
    renderAt('/cards');
    expect(screen.getByRole('button', { name: 'Findings' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: 'Strategies' }));
    expect(await screen.findByText('10/21 EMA cross, buy the pullback.')).toBeInTheDocument();
    expect(screen.getByText('P(2R)')).toBeInTheDocument();
  });
});
