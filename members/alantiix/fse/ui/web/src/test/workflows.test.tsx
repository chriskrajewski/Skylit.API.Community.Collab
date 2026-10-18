import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import { CommandForm } from '../components/CommandForm';
import { CoverageTable, buildOverridePlugins, fieldsFromEntry } from '../components/PluginBacktest';
import type { CommandSpec, Session } from '../lib/types';

const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};
const CONFIGS = [
  { kind: 'research', name: 'plugin_d-plugin_c', origin: null, current: 'aaa', revisions: 1, updated_at: null, path: '/c/plugin_c.yaml' },
  { kind: 'research', name: 'plugin_a-plugin_a', origin: null, current: 'bbb', revisions: 1, updated_at: null, path: '/c/plugin_a.yaml' },
  { kind: 'strategy', name: 'playbook_baseline', origin: null, current: 'ccc', revisions: 1, updated_at: null, path: '/c/pb.yaml' },
  { kind: 'runner', name: 'runner.paper', origin: null, current: 'ddd', revisions: 1, updated_at: null, path: '/c/runner.paper.yaml' },
];
const PLUGIN_C: CommandSpec = {
  id: 'research.plugin_d.run backtest', program: 'research.plugin_d.run', path: ['backtest'], help: 'simulate Plugin C',
  description: '', placement: 'batch', heavy: true, disabled_reason: null, groups: [],
  args: [
    { dest: 'config', flag: '--config', positional: false, kind: 'value', type: 'path', nargs: null, choices: null, default: null, required: false, help: 'a Plugin C config file', metavar: null, config_kind: { kind: 'research', prefix: 'plugin_d-' } },
    { dest: 'dry', flag: '--dry', positional: false, kind: 'flag', type: 'string', nargs: 0, choices: null, default: false, required: false, help: 'print the plan only', metavar: null, config_kind: null },
  ],
};
const RUNNER_YAML = 'version: 1\nplugins:\n  - {id: plugin_a, module: "research.plugin_a.plugin:PluginA"}\n  - {id: off, module: "x:Y", enabled: false}\n  - {id: plugin_c, module: "research.plugin_c.plugin:PluginC"}\n';
const BP_SPEC: CommandSpec = {
  id: 'fse backtest-plugin', program: 'fse', path: ['backtest-plugin'], help: 'backtest runner plugins',
  description: '', placement: 'batch', heavy: true, disabled_reason: null, groups: [],
  args: [
    { dest: 'config', flag: '--config', positional: false, kind: 'value', type: 'path', nargs: null, choices: null, default: null, required: true, help: 'the runner config whose plugins are used', metavar: 'FILE', config_kind: { kind: 'runner' } },
    { dest: 'allow_gaps', flag: '--allow-gaps', positional: false, kind: 'flag', type: 'string', nargs: 0, choices: null, default: false, required: false, help: 'run even when an input is missing', metavar: null, config_kind: null },
  ],
};

const LIVE = { run_dir: null, host_log: [], heartbeat: null, faults: [], plugins: [],
  blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false } };
function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}
function wrap(ui: JSX.Element, path = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <TestSession session={SESSION}><ToastProvider>{ui}</ToastProvider></TestSession>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('command form', () => {
  it('offers only the configs the command reads, and shows flag help as text', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      json(String(input).startsWith('/api/configs') ? CONFIGS : []));
    wrap(<CommandForm spec={PLUGIN_C} />);
    await screen.findByRole('option', { name: /plugin_c/ });
    const names = screen.getAllByRole('option').map((o) => o.textContent);
    expect(names).toContain('plugin_c (aaa)');
    expect(names.some((n) => n?.includes('plugin_a'))).toBe(false);
    expect(names.some((n) => n?.includes('playbook_baseline'))).toBe(false);
    expect(screen.getByText('print the plan only')).toBeInTheDocument(); // visible, not a tooltip
    f.mockRestore();
  });
});

describe('coverage table', () => {
  it('marks each day and input by where it comes from', () => {
    wrap(<CoverageTable cov={{
      ok: true, problems: [], complete: false, needs: ['bars MNQ', 'skylit SPXW (matrix)'],
      days: [
        { day: '2026-10-12', exchange_session: true, sources: { 'bars MNQ': 'recording', 'skylit SPXW (matrix)': 'recording' }, missing: [] },
        { day: '2026-10-09', exchange_session: true, sources: { 'bars MNQ': 'cache', 'skylit SPXW (matrix)': 'missing' }, missing: ['skylit SPXW (matrix)'] },
      ],
    }} />);
    expect(screen.getAllByText('recorded live')).toHaveLength(2);
    expect(screen.getByText('Data_Cache')).toBeInTheDocument();
    expect(screen.getByText('missing')).toBeInTheDocument();
  });
});

describe('backtest plugin screen', () => {
  it('lists the enabled plugins, checks the data, and runs only when it is complete', async () => {
    const posts: { url: string; body: any }[] = [];
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'POST') posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.startsWith('/api/catalog')) return json({ commands: [BP_SPEC], errors: {} });
      if (url.startsWith('/api/configs?kind=runner')) return json(CONFIGS.filter((c) => c.kind === 'runner'));
      if (url.startsWith('/api/configs/runner/')) return json({ kind: 'runner', name: 'runner.paper', text: RUNNER_YAML, sha: 'ddd' });
      if (url.startsWith('/api/backtest-plugin/coverage')) return json({
        ok: true, problems: [], complete: false, needs: ['bars MNQ'],
        days: [{ day: '2026-10-09', exchange_session: true, sources: { 'bars MNQ': 'missing' }, missing: ['bars MNQ'] }],
      });
      if (url.startsWith('/api/jobs')) return json({ id: 'job1' });
      if (url.startsWith('/api/live')) return json(LIVE);
      return json([]);
    });
    wrap(<AppRoutes />, '/workflows/plugin-backtest');
    expect(await screen.findByLabelText(/plugin_a · PluginA/)).toBeChecked();
    expect(screen.getByLabelText(/plugin_c · PluginC/)).toBeChecked();
    expect(screen.queryByLabelText(/off ·/)).not.toBeInTheDocument();
    expect(screen.getByText(/Live runner config is unchanged/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/plugin_a · PluginA/));
    await userEvent.type(screen.getByLabelText(/First day/), '2026-10-09');
    await userEvent.type(screen.getByLabelText(/Last day/), '2026-10-09');
    await userEvent.click(screen.getByRole('button', { name: /Check data/ }));
    expect(await screen.findByText('Some inputs are missing')).toBeInTheDocument();
    expect(posts[0].body).toEqual({ config: 'runner.paper', plugins: ['plugin_c'], first: '2026-10-09', last: '2026-10-09' });
    const run = screen.getByRole('button', { name: /Run backtest/ });
    expect(run).toBeDisabled();
    await userEvent.click(screen.getByLabelText('--allow-gaps'));
    expect(run).toBeEnabled();
    await userEvent.click(run);
    const job = posts.find((p) => p.url === '/api/jobs')!;
    expect(job.body.command).toBe('fse backtest-plugin');
    expect(job.body.values).toEqual({ config: '/c/runner.paper.yaml', plugin: ['plugin_c'], first: '2026-10-09', last: '2026-10-09', allow_gaps: true });
    f.mockRestore();
  });

  it('materializes an override copy then runs the job against that path', async () => {
    const posts: { url: string; body: any }[] = [];
    const richYaml = `version: 1
plugins:
  - id: plugin_a
    module: "research.plugin_a.plugin:PluginA"
    risk: {max_contracts: 16, max_open_trades: 1}
    fills: {trade_through_ticks: 1, slippage_ticks: 0, stop_fill: at_stop, commission_rt: {MNQ: "1.24"}}
`;
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'POST') posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.startsWith('/api/catalog')) return json({ commands: [BP_SPEC], errors: {} });
      if (url.startsWith('/api/configs?kind=runner')) return json(CONFIGS.filter((c) => c.kind === 'runner'));
      if (url.startsWith('/api/configs/runner/')) {
        return json({ kind: 'runner', name: 'runner.paper', text: richYaml, sha: 'ddd', path: '/c/runner.paper.yaml' });
      }
      if (url.startsWith('/api/backtest-plugin/coverage')) {
        return json({
          ok: true, problems: [], complete: true, needs: ['bars MNQ'],
          days: [{ day: '2026-10-09', exchange_session: true, sources: { 'bars MNQ': 'cache' }, missing: [] }],
        });
      }
      if (url.startsWith('/api/backtest-plugin/override-config')) {
        return json({
          validation: { ok: true, errors: [], warnings: [] },
          override: {
            storage: 'upload', path: '/uploads/aabbccddeeff0011/runner.override.yaml',
            name: null, source_sha: 'ddd', live_untouched: true, source: 'runner.paper',
          },
        });
      }
      if (url.startsWith('/api/jobs')) return json({ id: 'job2' });
      if (url.startsWith('/api/live')) return json(LIVE);
      return json([]);
    });
    wrap(<AppRoutes />, '/workflows/plugin-backtest');
    await screen.findByLabelText(/plugin_a · PluginA/);
    await userEvent.type(screen.getByLabelText(/First day/), '2026-10-09');
    await userEvent.type(screen.getByLabelText(/Last day/), '2026-10-09');
    await userEvent.click(screen.getByRole('switch', { name: /Use override settings for this run/ }));
    const loss = await screen.findByLabelText(/max_daily_loss/);
    await userEvent.clear(loss);
    await userEvent.type(loss, '800');
    await userEvent.click(screen.getByRole('button', { name: /Check data/ }));
    expect(await screen.findByText('Every input is there')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Run backtest with overrides/ }));
    const ov = posts.find((p) => p.url === '/api/backtest-plugin/override-config')!;
    expect(ov.body.source).toBe('runner.paper');
    expect(ov.body.keep).toBe(false);
    expect(ov.body.plugins.plugin_a.risk.max_daily_loss).toBe('800');
    const job = posts.find((p) => p.url === '/api/jobs')!;
    expect(job.body.values.config).toBe('/uploads/aabbccddeeff0011/runner.override.yaml');
    expect(job.body.values.config).not.toBe('/c/runner.paper.yaml');
    f.mockRestore();
  });
});

describe('override field helpers', () => {
  it('builds a plugins patch from form fields', () => {
    const entry = {
      id: 'plugin_a', module: 'research.plugin_a.plugin:PluginA',
      risk: { max_contracts: 16 }, fills: { slippage_ticks: 0, commission_rt: { MNQ: '1.24' } },
    };
    const fields = { ...fieldsFromEntry(entry), max_daily_loss: '800', max_trades_per_day: '30' };
    const patch = buildOverridePlugins(['plugin_a'], { plugin_a: entry }, { plugin_a: fields });
    expect(patch.plugin_a.risk).toMatchObject({ max_contracts: 16, max_daily_loss: '800', max_trades_per_day: 30 });
    expect(patch.plugin_a.fills?.commission_rt).toEqual({ MNQ: '1.24' });
  });
});

describe('backtest match', () => {
  it('marks each backtested value as matching or differing', async () => {
    const { BacktestMatch } = await import('../pages/Plugins');
    wrap(<BacktestMatch entry={{ id: 'plugin_a', fills: { slippage_ticks: 0, commission_rt: { MNQ: '1.24' } } }}
      bt={{ source: 'SLOTCUT.md', values: { 'fills.slippage_ticks': 2, 'fills.commission_rt.MNQ': '1.24' } }} />);
    expect(screen.getByText('matches backtest')).toBeInTheDocument();
    expect(screen.getByText('differs')).toBeInTheDocument();
    expect(screen.getByText('here 0 · backtest 2')).toBeInTheDocument();
    expect(screen.getByText('1 setting differs from the backtest')).toBeInTheDocument();
  });
});
