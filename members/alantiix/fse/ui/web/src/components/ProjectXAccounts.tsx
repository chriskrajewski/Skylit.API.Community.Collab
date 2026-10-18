// "Find my ProjectX accounts": run `fse accounts --json` as a batch job, list the
// accounts it resolved, and set PRACTICE_ACCOUNT_ID / COMBINE_ACCOUNT_ID from a row.
// Roles and diagnostics come from the API as booleans; no secret value reaches the page.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Banner, Button, DataTable } from '../ds/index.js';
import { ConfirmDialog } from './ConfirmDialog';
import { useToast } from './Toasts';
import { JobDetail } from '../pages/Jobs';
import { RESTART_CONFIRM_TEXT } from '../pages/Live';
import { api, errorText, isConfirmRequired } from '../lib/api';
import { isTerminal } from '../lib/hooks';
import { DASH } from '../lib/format';
import type { Job } from '../lib/types';

/** One row of GET /api/jobs/{id}/accounts; role is recomputed from the secret store on each read. */
export interface ProjectXAccountRow {
  id: string;
  name: string;
  can_trade: boolean;
  /** '' | 'practice' | 'combine' | 'practice,combine' */
  role: string;
  /** The console log redactor hid the id (it equals a stored secret value). */
  redacted: boolean;
}
export interface AccountDiagnostic { code: string; variable: string; message: string }
export interface AccountsLookup {
  job: Job;
  accounts: ProjectXAccountRow[] | null;
  diagnostics: AccountDiagnostic[];
  error: string | null;
}
interface UseResult { name: string; status: string; restart: string }

type Role = 'practice' | 'combine';
const ROLE_LABEL: Record<Role, string> = { practice: 'Practice', combine: 'Combine' };
export const LOOKUP_NOTE = 'Runs as a batch job with the stored ProjectX credentials; on AWS it takes about a minute to start.';
export const SAME_ACCOUNT_TEXT = 'PRACTICE_ACCOUNT_ID and COMBINE_ACCOUNT_ID cannot be the same account';
export const NOT_PRACTICE_TEXT = 'this looks like a Combine/funded account; Practice mode would trade it';
export const RESTART_RUNNER_TEXT = 'Restart the runner for it to read the new value.';

function rolesOf(row: ProjectXAccountRow): Role[] {
  return row.role.split(',').filter((r): r is Role => r === 'practice' || r === 'combine');
}

/** Why a "Use as" button is disabled, or null when it is enabled. */
function blockedReason(row: ProjectXAccountRow, role: Role): string | null {
  const has = rolesOf(row);
  if (row.redacted) return 'the console hid this id; it is already stored as a secret';
  if (has.includes(role)) return `already the ${ROLE_LABEL[role]} account`;
  if (has.length > 0) return SAME_ACCOUNT_TEXT;
  return null;
}

export function ProjectXAccounts() {
  const qc = useQueryClient();
  const toast = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const [confirmRow, setConfirmRow] = useState<ProjectXAccountRow | null>(null);
  const [restartOffered, setRestartOffered] = useState(false);
  const [confirmRestart, setConfirmRestart] = useState(false);
  const lookup = useQuery({
    queryKey: ['projectx-accounts', jobId],
    queryFn: () => api.get<AccountsLookup>(`/api/jobs/${jobId}/accounts`),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.job.status) ? false : 1500),
  });
  const use = useMutation({
    mutationFn: (v: { row: ProjectXAccountRow; role: Role; confirm: boolean }) => api.post<UseResult>('/api/accounts/use', {
      job_id: jobId, account_id: v.row.id, role: v.role, confirm_not_practice: v.confirm,
    }),
    onSuccess: (r, v) => {
      toast('ok', `${v.row.name} set`, `${r.name} is ${v.row.id}`);
      void qc.invalidateQueries({ queryKey: ['secrets'] });
      void qc.invalidateQueries({ queryKey: ['projectx-accounts', jobId] });
      setRestartOffered(true);
    },
    // The server asks for the confirm too (a name the page read differently): same dialog.
    onError: (err, v) => { if (isConfirmRequired(err) && v.role === 'practice') setConfirmRow(v.row); },
  });
  const start = useMutation({
    mutationFn: () => api.post<Job>('/api/workflows/accounts-lookup', {}),
    onSuccess: (j) => { use.reset(); setRestartOffered(false); setJobId(j.id); },
  });
  const restart = useMutation({
    mutationFn: () => api.post('/api/runner/restart'),
    onSuccess: () => toast('ok', 'Runner restart sent'),
    onError: (err) => toast('danger', 'Runner action refused', errorText(err)),
  });
  const pick = (row: ProjectXAccountRow, role: Role) => {
    if (role === 'practice' && !row.name.toUpperCase().startsWith('PRAC')) setConfirmRow(row);
    else use.mutate({ row, role, confirm: false });
  };
  const data = lookup.data;
  const done = !!data && isTerminal(data.job.status);
  const running = !!jobId && !done;
  const failed = done && (data.job.status !== 'succeeded' || data.error !== null || data.accounts === null);
  const rows = (data?.accounts ?? []).map((r, i) => ({ ...r, _k: `${i}:${r.id}:${r.name}` }));
  return (
    <div className="stack">
      <div className="row">
        <Button variant="primary" icon="search" disabled={start.isPending || running} onClick={() => start.mutate()}>
          Find my ProjectX accounts
        </Button>
        <span className="small muted">Lists the accounts the stored ProjectX credentials can see. No order is sent.</span>
      </div>
      {start.isError && <Banner tone="warning" title="Not started">{errorText(start.error)}</Banner>}
      {lookup.isError && <Banner tone="warning" title="Lookup unavailable">{errorText(lookup.error)}</Banner>}
      {running && (
        <div className="stack-sm">
          <div className="small muted">{LOOKUP_NOTE}</div>
          <JobDetail jobId={jobId!} compact />
        </div>
      )}
      {failed && (
        <div className="stack">
          <Banner tone="warning" title="No account list">
            The lookup {data.job.status === 'succeeded' ? 'finished' : `exited ${data.job.exit_code ?? DASH} (${data.job.exit_meaning ?? data.job.status})`}
            {data.error ? `: ${data.error}` : ''}. Its output is below.
          </Banner>
          <JobDetail jobId={jobId!} compact />
        </div>
      )}
      {done && !failed && (
        <div className="stack">
          {data.diagnostics.length > 0 && (
            <Banner tone="warning" title="Account id problems">
              <ul aria-label="Account id problems">{data.diagnostics.map((d) => <li key={`${d.code}:${d.variable}`}>{d.message}</li>)}</ul>
            </Banner>
          )}
          {rows.length === 0 ? <div className="small muted">The credentials see no account.</div> : (
            <DataTable dense rows={rows} rowKey="_k" columns={[
              { key: 'id', label: 'ID', width: 140 },
              { key: 'name', label: 'Name' },
              { key: 'can_trade', label: 'Can trade', width: 90, render: (v: boolean) => (v ? 'yes' : 'no') },
              { key: 'role', label: 'Role', width: 170, render: (_: string, r: ProjectXAccountRow) => {
                const has = rolesOf(r);
                return has.length ? <>{has.map((m) => <Badge key={m} mode={m} />)}</> : DASH;
              } },
              { key: 'actions', label: '', width: 260, render: (_: unknown, r: ProjectXAccountRow) => (
                <div className="row">
                  {(['practice', 'combine'] as Role[]).map((role) => {
                    const why = blockedReason(r, role);
                    return (
                      <Button key={role} size="sm" aria-label={`Use ${r.name} as ${ROLE_LABEL[role]}`} title={why ?? undefined}
                        disabled={!!why || use.isPending} onClick={() => pick(r, role)}>Use as {ROLE_LABEL[role]}</Button>
                    );
                  })}
                </div>
              ) },
            ]} />
          )}
        </div>
      )}
      {use.isError && !isConfirmRequired(use.error) && <Banner tone="warning" title="Not set">{errorText(use.error)}</Banner>}
      {restartOffered && (
        <Banner tone="info" title={RESTART_RUNNER_TEXT}
          actions={<Button size="sm" icon="refresh-cw" disabled={restart.isPending} onClick={() => setConfirmRestart(true)}>Restart runner</Button>}>
          The runner reads PRACTICE_ACCOUNT_ID and COMBINE_ACCOUNT_ID when it starts.
        </Banner>
      )}
      <ConfirmDialog open={!!confirmRow} danger alarmLabel="Practice · real orders" title={`Use ${confirmRow?.name ?? ''} as Practice?`}
        confirmLabel="Use as Practice" icon="save" busy={use.isPending} onClose={() => setConfirmRow(null)}
        onConfirm={() => { const row = confirmRow; setConfirmRow(null); if (row) use.mutate({ row, role: 'practice', confirm: true }); }}>
        {NOT_PRACTICE_TEXT}.
      </ConfirmDialog>
      <ConfirmDialog open={confirmRestart} danger alarmLabel="Practice · real orders" title="Restart the runner?"
        confirmLabel="Restart runner" icon="refresh-cw" busy={restart.isPending} onClose={() => setConfirmRestart(false)}
        onConfirm={() => { setConfirmRestart(false); restart.mutate(); }}>
        {RESTART_CONFIRM_TEXT}
      </ConfirmDialog>
    </div>
  );
}
