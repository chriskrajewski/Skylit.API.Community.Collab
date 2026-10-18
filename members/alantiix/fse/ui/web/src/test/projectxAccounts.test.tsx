import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import {
  NOT_PRACTICE_TEXT, ProjectXAccounts, RESTART_RUNNER_TEXT, SAME_ACCOUNT_TEXT,
  type AccountDiagnostic, type AccountsLookup, type ProjectXAccountRow,
} from '../components/ProjectXAccounts';
import { RESTART_CONFIRM_TEXT } from '../pages/Live';
import type { Job, Session } from '../lib/types';
const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};
const JOB: Job = {
  id: 'job1', program: 'fse', command: ['accounts'], argv: ['fse', 'accounts', '--json'], placement: 'batch', executor: 'local',
  status: 'succeeded', exit_code: 0, exit_meaning: 'success', title: 'ProjectX accounts', user: 'operator@localhost',
  created_at: 1, started_at: 1, stopped_at: 2, run_dir: null, external_id: null,
};
function row(id: string, name: string, over: Partial<ProjectXAccountRow> = {}): ProjectXAccountRow {
  return { id, name, can_trade: true, role: '', redacted: false, ...over };
}
const LIVE = { run_dir: null, host_log: [], heartbeat: null, faults: [], plugins: [],
  blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false } };
const PRAC = row('101', 'PRAC-FAKE-1');
const FUNDED = row('202', '50KTC-FAKE-2');
type Call = { url: string; body: any };
type Reply = { status: number; body: unknown };
/** Fetch for the lookup: POST starts job1, GET returns `lookup`, POST use answers `use` (default 200). */
function mock(accounts: ProjectXAccountRow[], diagnostics: AccountDiagnostic[] = [], use?: (c: Call) => Reply) {
  const calls: Call[] = [];
  const lookup: AccountsLookup = { job: JOB, accounts, diagnostics, error: null };
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const call = { url, body: init?.body ? JSON.parse(String(init.body)) : undefined };
    if (method !== 'GET') calls.push(call);
    const res: Reply = url === '/api/workflows/accounts-lookup' ? { status: 201, body: { ...JOB, status: 'queued' } }
      : url === '/api/jobs/job1/accounts' ? { status: 200, body: lookup }
        : url === '/api/accounts/use' ? (use?.(call) ?? { status: 200, body: {
          name: call.body.role === 'practice' ? 'PRACTICE_ACCOUNT_ID' : 'COMBINE_ACCOUNT_ID', status: 'set',
          restart: RESTART_RUNNER_TEXT } })
          : url.startsWith('/api/live') ? { status: 200, body: LIVE }
            : url.startsWith('/api/secrets') ? { status: 200, body: { store: 'local', secrets: [] } }
              : { status: 200, body: {} };
    return new Response(JSON.stringify(res.body), { status: res.status, headers: { 'Content-Type': 'application/json' } });
  });
  return { f, calls };
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
async function find() {
  await userEvent.click(screen.getByRole('button', { name: 'Find my ProjectX accounts' }));
  await screen.findByRole('table');
}
const useBody = (calls: Call[]) => calls.filter((c) => c.url === '/api/accounts/use').map((c) => c.body);
describe('find my ProjectX accounts', () => {
  it('starts the lookup job and lists the rows with their role badges', async () => {
    const { calls } = mock([row('101', 'PRAC-FAKE-1', { role: 'practice' }), row('202', '50KTC-FAKE-2', { role: 'combine', can_trade: false })]);
    wrap(<ProjectXAccounts />);
    await find();
    expect(calls[0]).toEqual({ url: '/api/workflows/accounts-lookup', body: {} });
    const table = screen.getByRole('table');
    expect(within(table).getByRole('cell', { name: '101' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'PRAC-FAKE-1' })).toBeInTheDocument();
    expect(within(table).getByText('Practice')).toBeInTheDocument();
    expect(within(table).getByText('Combine')).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'no' })).toBeInTheDocument();
  });
  it('sets a PRAC row as Practice without a confirm', async () => {
    const { calls } = mock([PRAC, FUNDED]);
    wrap(<ProjectXAccounts />);
    await find();
    await userEvent.click(screen.getByRole('button', { name: 'Use PRAC-FAKE-1 as Practice' }));
    expect(await screen.findByText(RESTART_RUNNER_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(useBody(calls)).toEqual([{ job_id: 'job1', account_id: '101', role: 'practice', confirm_not_practice: false }]);
  });
  it('asks before it sets a Combine/funded row as Practice', async () => {
    const { calls } = mock([PRAC, FUNDED]);
    wrap(<ProjectXAccounts />);
    await find();
    await userEvent.click(screen.getByRole('button', { name: 'Use 50KTC-FAKE-2 as Practice' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Use 50KTC-FAKE-2 as Practice?')).toBeInTheDocument();
    expect(within(dialog).getByText(NOT_PRACTICE_TEXT, { exact: false })).toBeInTheDocument();
    expect(useBody(calls)).toEqual([]);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use as Practice' }));
    await screen.findByText(RESTART_RUNNER_TEXT);
    expect(useBody(calls)).toEqual([{ job_id: 'job1', account_id: '202', role: 'practice', confirm_not_practice: true }]);
  });
  it('opens the same confirm on a 409 confirm_required from the server', async () => {
    const { calls } = mock([row('303', 'PRACX-ODD')], [], (c) => (c.body.confirm_not_practice
      ? { status: 200, body: { name: 'PRACTICE_ACCOUNT_ID', status: 'set', restart: RESTART_RUNNER_TEXT } }
      : { status: 409, body: { detail: { code: 'confirm_required', message: NOT_PRACTICE_TEXT } } }));
    wrap(<ProjectXAccounts />);
    await find();
    await userEvent.click(screen.getByRole('button', { name: 'Use PRACX-ODD as Practice' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use as Practice' }));
    await screen.findByText(RESTART_RUNNER_TEXT);
    expect(useBody(calls).map((b) => b.confirm_not_practice)).toEqual([false, true]);
  });
  it('disables the row\'s own role, the other role and redacted ids', async () => {
    mock([row('101', 'PRAC-FAKE-1', { role: 'practice' }), row('[redacted]', 'PRAC-HIDDEN', { redacted: true }), FUNDED]);
    wrap(<ProjectXAccounts />);
    await find();
    expect(screen.getByRole('button', { name: 'Use PRAC-FAKE-1 as Practice' })).toBeDisabled();
    const other = screen.getByRole('button', { name: 'Use PRAC-FAKE-1 as Combine' });
    expect(other).toBeDisabled();
    expect(other).toHaveAttribute('title', SAME_ACCOUNT_TEXT);
    expect(screen.getByRole('button', { name: 'Use PRAC-HIDDEN as Practice' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Use PRAC-HIDDEN as Combine' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Use 50KTC-FAKE-2 as Combine' })).toBeEnabled();
  });
  it('shows a same_account refusal as a warning', async () => {
    mock([PRAC], [], () => ({ status: 409, body: { detail: { code: 'same_account', message: SAME_ACCOUNT_TEXT } } }));
    wrap(<ProjectXAccounts />);
    await find();
    await userEvent.click(screen.getByRole('button', { name: 'Use PRAC-FAKE-1 as Combine' }));
    expect(await screen.findByText(SAME_ACCOUNT_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(RESTART_RUNNER_TEXT)).not.toBeInTheDocument();
  });
  it('offers Restart runner after a set, behind a confirm', async () => {
    const { calls } = mock([PRAC]);
    wrap(<ProjectXAccounts />);
    await find();
    await userEvent.click(screen.getByRole('button', { name: 'Use PRAC-FAKE-1 as Practice' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Restart runner' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(RESTART_CONFIRM_TEXT)).toBeInTheDocument();
    expect(calls.some((c) => c.url === '/api/runner/restart')).toBe(false);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Restart runner' }));
    await screen.findByText('Runner restart sent');
    expect(calls.filter((c) => c.url === '/api/runner/restart')).toHaveLength(1);
  });
  it('lists the account id problems', async () => {
    mock([PRAC], [{ code: 'not_numeric', variable: 'PRACTICE_ACCOUNT_ID',
      message: 'PRACTICE_ACCOUNT_ID is not a numeric account id; use the numeric id, not the account name' }]);
    wrap(<ProjectXAccounts />);
    await find();
    const list = screen.getByRole('list', { name: 'Account id problems' });
    expect(within(list).getByText(/use the numeric id, not the account name/)).toBeInTheDocument();
  });
  it('is on Settings → Secrets and Workflows → ProjectX accounts', async () => {
    mock([PRAC]);
    const a = wrap(<AppRoutes />, '/secrets');
    expect(await screen.findByRole('button', { name: 'Find my ProjectX accounts' })).toBeInTheDocument();
    a.unmount();
    wrap(<AppRoutes />, '/workflows/projectx-accounts');
    expect(await screen.findByRole('button', { name: 'Find my ProjectX accounts' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'ProjectX accounts' })).toBeInTheDocument();
  });
});
