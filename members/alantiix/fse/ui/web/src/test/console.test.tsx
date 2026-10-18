import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import { CommandForm, cleanValues } from '../components/CommandForm';
import { NAV } from '../components/Shell';
import { ICON_NAMES, hasIcon } from '../ds/components/brand/icons.js';
import { MINUS, num, pct, signedR, usd, etTime, isNotApplicable } from '../lib/format';
import { HELD_TEXT, PAPER_MODE_TEXT } from '../pages/Live';
import type { CommandSpec, Session } from '../lib/types';

const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};

const BACKTEST: CommandSpec = {
  id: 'fse backtest', program: 'fse', path: ['backtest'], help: 'run a backtest', description: '',
  placement: 'batch', heavy: true, disabled_reason: null, groups: [],
  args: [
    { dest: 'config', flag: '--config', positional: false, kind: 'value', type: 'path', nargs: null, choices: null, default: null, required: true, help: 'the Strategy_Config file', metavar: 'FILE' },
    { dest: 'start', flag: '--start', positional: false, kind: 'value', type: 'date', nargs: null, choices: null, default: null, required: true, help: 'first session', metavar: null },
    { dest: 'offline', flag: '--offline', positional: false, kind: 'flag', type: 'string', nargs: 0, choices: null, default: false, required: false, help: 'offline', metavar: null },
    { dest: 'workers', flag: '--workers', positional: false, kind: 'value', type: 'int', nargs: null, choices: null, default: 1, required: false, help: 'workers', metavar: null },
    { dest: 'candidate', flag: '--candidate', positional: false, kind: 'append', type: 'path', nargs: null, choices: null, default: null, required: false, help: 'candidates', metavar: null },
  ],
};

type Handler = (url: string, init?: RequestInit) => unknown;

function mockFetch(handler: Handler) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const body = handler(url, init);
    return new Response(body === undefined ? '[]' : JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

const defaults: Handler = (url) => {
  if (url.startsWith('/api/catalog')) return { commands: [BACKTEST], errors: {} };
  if (url.startsWith('/api/live')) return { run_dir: null, host_log: [], heartbeat: null, faults: [], plugins: [], blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false } };
  if (url.startsWith('/api/runner')) return { controller: 'process', state: 'stopped', pid: null, started_at: null, last_exit_code: null, last_exit_meaning: null, settings: { mode: 'run', runner_config: 'runner', strategy_config: 'playbook_baseline' }, log: [] };
  return [];
};

function renderAt(path: string, ui = <AppRoutes />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <TestSession session={SESSION}><ToastProvider>{ui}</ToastProvider></TestSession>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('shell', () => {
  it('renders the wordmark, nav and user, and routes between pages', async () => {
    mockFetch(defaults);
    renderAt('/live');
    expect(screen.getByLabelText('fse — futures strategy engine')).toBeInTheDocument();
    expect(screen.getByText('operator@localhost')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Live runner' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Jobs/ }));
    expect(await screen.findByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Audit log/ }));
    expect(await screen.findByRole('heading', { name: 'Audit log' })).toBeInTheDocument();
  });

  it('opens the drawer from the topbar menu and closes it on navigate', async () => {
    mockFetch(defaults);
    renderAt('/live');
    expect(document.querySelector('.shell__nav--open')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }));
    expect(document.querySelector('.shell__nav--open')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Close navigation' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Jobs/ }));
    expect(await screen.findByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    expect(document.querySelector('.shell__nav--open')).toBeNull();
  });

  it('has a page for every nav item', async () => {
    mockFetch(defaults);
    for (const item of NAV.flatMap((s) => s.items)) {
      const { unmount } = renderAt(`/${item.value}`);
      expect(screen.queryByText('No such page.')).not.toBeInTheDocument();
      unmount();
    }
  });

  it('toggles the theme on <html>', async () => {
    mockFetch(defaults);
    renderAt('/live');
    const before = document.documentElement.getAttribute('data-theme');
    await userEvent.click(screen.getByRole('button', { name: 'Toggle theme' }));
    expect(document.documentElement.getAttribute('data-theme')).not.toBe(before);
  });

  it('shows fse live as disabled with the TopstepX reason', async () => {
    mockFetch(defaults);
    renderAt('/live');
    expect(await screen.findByText(/TopstepX VPS\/VPN terms are verified/)).toBeInTheDocument();
  });
});

describe('command form', () => {
  it('converts form values to API values', () => {
    expect(cleanValues(BACKTEST, { config: '/c.yaml', start: '2026-01-02', offline: true, workers: '3', candidate: 'a.yaml, b.yaml', extra: 'x' }))
      .toEqual({ config: '/c.yaml', start: '2026-01-02', offline: true, workers: 3, candidate: ['a.yaml', 'b.yaml'] });
    expect(cleanValues(BACKTEST, { offline: false, workers: '' })).toEqual({});
  });

  it('renders required fields first and posts with the CSRF header', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url === '/api/jobs') return { id: 'abcdef0123456789', status: 'queued' };
      return [];
    });
    renderAt('/x', <CommandForm spec={BACKTEST} primary={['config', 'start']} onSubmitted={() => undefined} />);
    expect(screen.getByText('--config *')).toBeInTheDocument();
    expect(screen.getByText('--start *')).toBeInTheDocument();
    expect(screen.queryByText('--workers')).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(/All options/));
    expect(screen.getByText('--workers')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Run/ }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs')).toBe(true));
    const post = calls.find((c) => c.url === '/api/jobs')!;
    expect((post.init!.headers as Record<string, string>)['X-FSE-CSRF']).toBe('csrf-token');
  });

  it('shows the disabled reason instead of a form', () => {
    mockFetch(defaults);
    renderAt('/x', <CommandForm spec={{ ...BACKTEST, disabled_reason: 'not here' }} />);
    expect(screen.getByText('not here')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Run/ })).not.toBeInTheDocument();
  });
});

describe('format', () => {
  it('follows the content rules', () => {
    expect(signedR('2.41')).toBe('+2.41 R');
    expect(signedR(-1)).toBe(`${MINUS}1.00 R`);
    expect(usd('903.75')).toBe('+$903.75');
    expect(usd('-120')).toBe(`${MINUS}$120.00`);
    expect(num(null)).toBe('\u2014');
    expect(num({})).toBe('not applicable');
    expect(isNotApplicable({})).toBe(true);
    expect(pct('83.3333')).toBe('83.33%');
    expect(etTime(Date.UTC(2026, 9, 6, 14, 35, 0) / 1000)).toBe('10:35:00 ET');
  });
});

describe('icons', () => {
  it('bundles every icon name the console uses', () => {
    for (const n of ICON_NAMES) expect(hasIcon(n)).toBe(true);
    const used = new Set<string>();
    const walk = (dir: string) => {
      for (const f of readdirSync(dir)) {
        const p = join(dir, f);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(tsx|jsx)$/.test(f)) {
          const text = readFileSync(p, 'utf8');
          for (const m of text.matchAll(/\bicon(?:Right)?[=:]\s*["']([a-z0-9-]+)["']/g)) used.add(m[1]);
          for (const m of text.matchAll(/<Icon name="([a-z0-9-]+)"/g)) used.add(m[1]);
        }
      }
    };
    walk(join(import.meta.dirname, '..'));
    const missing = [...used].filter((n) => !hasIcon(n));
    expect(missing).toEqual([]);
  });
});

describe('aws', () => {
  const SYSTEMD = {
    controller: 'systemd', state: 'stopped', pid: null, started_at: null, last_exit_code: 0, last_exit_meaning: 'ok',
    settings: { mode: 'paper', runner_config: 'runner', strategy_config: 'playbook_baseline' }, log: [],
    held: true, pending: null, last_command: { action: 'stop', status: 'Success', at: 1, output: [] }, error: null,
  };

  it('shows the held banner, the paper mode note and the state badge', async () => {
    mockFetch((url) => (url.startsWith('/api/runner') ? SYSTEMD : defaults(url)));
    renderAt('/live');
    expect(await screen.findByText(HELD_TEXT)).toBeInTheDocument();
    expect(screen.getByText(PAPER_MODE_TEXT)).toBeInTheDocument();
    expect(screen.getByText('stop · Success')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start/ })).toBeEnabled();
  });

  it('shows the runner error and no held banner while running', async () => {
    mockFetch((url) => (url.startsWith('/api/runner')
      ? { ...SYSTEMD, state: 'unknown', held: false, error: 'the host has not reported the runner state yet' }
      : defaults(url)));
    renderAt('/live');
    expect(await screen.findByText('the host has not reported the runner state yet')).toBeInTheDocument();
    expect(screen.queryByText(HELD_TEXT)).not.toBeInTheDocument();
  });

  it('lists CloudWatch alarms with their state, and hides the panel off AWS', async () => {
    mockFetch((url) => (url.startsWith('/api/health/daily') ? {
      reports: [],
      alarms: [
        { name: 'fse-runner-log-silence', state: 'ALARM', reason: 'no logs for 15 min', updated_at: 1791300000 },
        { name: 'fse-runner-faults', state: 'OK', reason: null, updated_at: null },
      ],
    } : defaults(url)));
    const { unmount } = renderAt('/health');
    expect(await screen.findByText('fse-runner-log-silence')).toBeInTheDocument();
    expect(screen.getByText('ALARM')).toBeInTheDocument();
    expect(screen.getByText('no logs for 15 min')).toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();
    mockFetch((url) => (url.startsWith('/api/health/daily')
      ? { reports: [], alarms: { error: 'cannot read CloudWatch alarms: AccessDenied' } } : defaults(url)));
    const second = renderAt('/health');
    expect(await screen.findByText('unavailable: cannot read CloudWatch alarms: AccessDenied')).toBeInTheDocument();
    second.unmount();
    vi.restoreAllMocks();
    mockFetch((url) => (url.startsWith('/api/health/daily') ? { reports: [], alarms: null } : defaults(url)));
    renderAt('/health');
    expect(await screen.findByText('Pick a day.')).toBeInTheDocument();
    expect(screen.queryByText('Alarms')).not.toBeInTheDocument();
  });

  it('unlocks a held lock only after the confirm dialog', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url === '/api/batch/locks') return { enabled: true, locks: [
        { name: 'pull', held: true, holder: ['user=op@example.com', 'host=fse-console', 'job=a000000000000001'], job: 'a000000000000001' },
        { name: 'holdout', held: false, holder: null, job: null },
      ] };
      if (url === '/api/batch/unlock') return { deleted: true };
      return defaults(url);
    });
    renderAt('/jobs');
    expect(await screen.findByText('Batch locks')).toBeInTheDocument();
    const buttons = screen.getAllByRole('button', { name: /Unlock/ });
    expect(buttons[1]).toBeDisabled();  // the holdout lock is free
    await userEvent.click(buttons[0]);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Delete the pull lock?')).toBeInTheDocument();
    expect(calls.some((c) => c.url === '/api/batch/unlock')).toBe(false);
    await userEvent.click(within(dialog).getByRole('button', { name: /Unlock/ }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/batch/unlock')).toBe(true));
    const post = calls.find((c) => c.url === '/api/batch/unlock')!;
    expect(JSON.parse(String(post.init!.body))).toEqual({ lock: 'pull', confirm: 'unlock' });
  });

  it('shows no locks panel with the local executor, and the ECS task id on a job', async () => {
    mockFetch((url) => {
      if (url === '/api/batch/locks') return { enabled: false, locks: [] };
      if (url.startsWith('/api/jobs/abcdef0123456789')) return {
        id: 'abcdef0123456789', program: 'fse', command: ['backtest'], argv: [], placement: 'batch', executor: 'ecs',
        status: 'succeeded', exit_code: 0, exit_meaning: 'ok', title: 'fse backtest', user: 'op', created_at: 1,
        started_at: 1, stopped_at: 2, run_dir: null, external_id: 'arn:aws:ecs:us-east-1:123456789012:task/fse/0f1e2d3c',
        lines: [],
      };
      return defaults(url);
    });
    const { unmount } = renderAt('/jobs');
    expect(await screen.findByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Batch locks')).not.toBeInTheDocument());
    unmount();
    // No WebSocket in the test: the job page falls back to polling.
    vi.stubGlobal('WebSocket', class {
      onerror: (() => void) | null = null;
      constructor() { setTimeout(() => this.onerror?.(), 0); }
      close() {}
    });
    try {
      renderAt('/jobs/abcdef0123456789');
      expect(await screen.findByText('0f1e2d3c')).toBeInTheDocument();
      expect(screen.getByText('ECS task')).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
