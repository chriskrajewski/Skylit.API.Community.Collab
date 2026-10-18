import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Banner, Button, DataTable, Input, KeyValue, MeterBar, Panel } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { DailyCheck } from '../components/DailyCheck';
import { ProjectXAccounts } from '../components/ProjectXAccounts';
import { useToast } from '../components/Toasts';
import { api, errorText } from '../lib/api';
import { useSession } from '../lib/session';
import { DASH, bytes, etDateTime } from '../lib/format';
import type { AlarmRow, DailyList } from '../lib/types';

interface SecretRow { name: string; status: 'set' | 'placeholder' | 'missing' }

const SECRET_NOTES: Record<string, string> = {
  SKYLIT_API_KEY: 'pull, backtest, run, paper',
  PROJECTX_USERNAME: 'run, paper (bars and market hub)',
  PROJECTX_API_KEY: 'run, paper',
  PRACTICE_ACCOUNT_ID: 'Live → Order mode → Practice · numeric id, use Find my ProjectX accounts',
  COMBINE_ACCOUNT_ID: 'Live → Order mode → Combine · numeric id, use Find my ProjectX accounts',
  NOTIFIER_WEBHOOK_URL: 'notify.sinks webhook',
  NARRATOR_API_KEY: 'notify.narrator',
  FSE_SECRET_VARS: 'extra variable names to redact',
};

function SecretEditor({ row }: { row: SecretRow }) {
  const [value, setValue] = useState('');
  const qc = useQueryClient();
  const toast = useToast();
  const put = useMutation({
    mutationFn: () => api.put(`/api/secrets/${row.name}`, { value }),
    onSuccess: () => {
      setValue('');
      void qc.invalidateQueries({ queryKey: ['secrets'] });
      toast('ok', `${row.name} set`, 'Restart the runner for it to read the new value.');
    },
  });
  return (
    <div className="secret-row">
      <div><div>{row.name}</div><div className="small muted">{SECRET_NOTES[row.name]}</div></div>
      <div>{row.status === 'set' ? <Badge variant="bull" dot>set</Badge>
        : row.status === 'placeholder' ? <Badge variant="warning">placeholder</Badge> : <Badge variant="outline">missing</Badge>}</div>
      <Input type="password" autoComplete="off" spellCheck={false} placeholder={row.status === 'set' ? 'enter a new value to replace it' : 'value'}
        value={value} onChange={(e) => setValue(e.target.value)} error={put.isError ? errorText(put.error) : undefined} />
      <Button icon="save" disabled={!value.trim() || put.isPending} onClick={() => put.mutate()}>Set</Button>
    </div>
  );
}

export function SecretsPage() {
  const secrets = useQuery({ queryKey: ['secrets'], queryFn: () => api.get<{ store: string; secrets: SecretRow[] }>('/api/secrets') });
  return (
    <div>
      <PageHead title="Secrets" sub="Write-only. The console shows whether each value is set and never shows a value back." />
      <div className="page">
        <Banner tone="info" title={secrets.data?.store === 'ssm' ? 'Stored in SSM Parameter Store' : 'Stored on this host'}>
          {secrets.data?.store === 'ssm'
            ? 'Values are SecureString parameters under /fse/secrets/. The runner reads them when it starts.'
            : 'Values are kept in the console data folder (mode 0600) and passed to the commands it runs.'}
        </Banner>
        <Panel title="Find my ProjectX accounts" icon="search">
          <ProjectXAccounts />
        </Panel>
        <Panel>
          {secrets.data ? secrets.data.secrets.map((r) => <SecretEditor key={r.name} row={r} />) : <Empty>Loading…</Empty>}
        </Panel>
      </div>
    </div>
  );
}

export function SystemPage() {
  const session = useSession();
  const sys = useQuery({ queryKey: ['system'], queryFn: () => api.get<any>('/api/system'), refetchInterval: 30000 });
  const d = sys.data;
  return (
    <div>
      <PageHead title="System" sub="The host the console and the runner use." />
      <div className="page">
        {!d ? <Empty>Loading…</Empty> : <>
          <div className="cols">
            <Panel title="Versions" icon="server">
              <KeyValue items={[
                ['Environment', d.env], ['Image tag', d.image_tag ?? 'unavailable'],
                ['Console', d.versions.fse_ui ?? DASH], ['Engine', d.versions.engine ?? DASH],
                ['Secrets', session.secret_store], ['fse live', session.enable_live ? 'enabled' : 'disabled (TopstepX terms)'],
                ['Plugin orders', session.plugin_orders ? 'enabled' : 'disabled'],
                ['TopstepX terms verified', session.topstep_verified_on ?? 'no'],
              ]} />
            </Panel>
            <Panel title="Disk" icon="hard-drive">
              <div className="stack">
                <MeterBar label="data volume" value={d.disk.used} max={d.disk.total} labelWidth={120}
                  tone={d.disk.used / d.disk.total > 0.9 ? 'bear' : d.disk.used / d.disk.total > 0.75 ? 'warning' : 'ink'}
                  display={`${bytes(d.disk.used)} of ${bytes(d.disk.total)}`} />
                <KeyValue items={[
                  ['Data folder', d.data_dir], ['Free', bytes(d.disk.free)],
                  ['Data_Cache', d.cache.exists ? `${bytes(d.cache.bytes)} in ${d.cache.files} files${d.cache.complete ? '' : ' (partial count)'}` : 'none'],
                ]} />
              </div>
            </Panel>
            <Panel title="Runner lock" icon="shield-check">
              <KeyValue items={[
                ['Path', d.runner_lock.path],
                ['Lock file', d.runner_lock.exists ? `present, modified ${etDateTime(d.runner_lock.modified)}` : 'none'],
              ]} />
              <div className="small muted">The lock is held while fse run or fse paper runs; a second runner exits 2.</div>
            </Panel>
          </div>
        </>}
      </div>
    </div>
  );
}

const ALARM_BADGE: Record<string, 'bear' | 'bull' | 'outline'> = { ALARM: 'bear', OK: 'bull' };

/** CloudWatch alarm states (AWS only): hidden off AWS, `unavailable` when they cannot be read. */
export function AlarmsPanel({ alarms }: { alarms: DailyList['alarms'] | undefined }) {
  if (!alarms) return null;
  return (
    <Panel title="Alarms" subtitle="CloudWatch" icon="bell" flush={Array.isArray(alarms)}>
      {!Array.isArray(alarms) ? <div className="small muted">unavailable: {alarms.error}</div>
        : alarms.length === 0 ? <Empty>none</Empty> : (
          <DataTable dense rows={alarms} rowKey="name" columns={[
            { key: 'name', label: 'Alarm', width: 260 },
            { key: 'state', label: 'State', width: 170, render: (v: string) => <Badge variant={ALARM_BADGE[v] ?? 'outline'}>{v}</Badge> },
            { key: 'reason', label: 'Reason', muted: true, render: (v: string | null) => v ?? DASH },
            { key: 'updated_at', label: 'Updated', muted: true, width: 190, render: (v: number | null, a: AlarmRow) => (a.updated_at ? etDateTime(v) : DASH) },
          ]} />
        )}
    </Panel>
  );
}

export function HealthPage() {
  // Same query key as DailyCheck's day list: one request feeds both.
  const list = useQuery({ queryKey: ['daily'], queryFn: () => api.get<DailyList>('/api/health/daily') });
  return (
    <div>
      <PageHead title="Health" sub="Daily paper checks (faults, feed outages, bar lag, credits) and alarms." />
      <div className="page">
        <AlarmsPanel alarms={list.data?.alarms} />
        <DailyCheck />
      </div>
    </div>
  );
}

export function AuditPage() {
  const audit = useQuery({ queryKey: ['audit'], queryFn: () => api.get<any[]>('/api/audit'), refetchInterval: 10000 });
  return (
    <div>
      <PageHead title="Audit log" sub="Every change made through the console: who, what, and the result. Secret values are never recorded." />
      <div className="page">
        <Panel flush>
          {!audit.data ? <Empty>Loading…</Empty> : audit.data.length === 0 ? <Empty>none</Empty> : (
            <DataTable dense rows={audit.data} columns={[
              { key: 'at', label: 'Time', muted: true, width: 190, render: (v: number) => etDateTime(v) },
              { key: 'user', label: 'By', width: 200, muted: true },
              { key: 'action', label: 'Action', width: 260 },
              { key: 'result', label: 'Result', width: 90, render: (v: string) => <span className={v === 'ok' ? '' : 'text-bear'}>{v}</span> },
              { key: 'detail', label: 'Detail', render: (v: unknown) => <span className="small muted">{Object.keys(v as object).length ? JSON.stringify(v) : DASH}</span> },
            ]} />
          )}
        </Panel>
      </div>
    </div>
  );
}
