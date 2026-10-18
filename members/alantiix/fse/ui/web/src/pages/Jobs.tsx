import { useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Banner, Button, DataTable, KeyValue, Panel } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LogView } from '../components/LogView';
import { JobActivity, useLastLineAt } from '../components/JobActivity';
import { useToast } from '../components/Toasts';
import { api, errorText } from '../lib/api';
import { isTerminal, useJobStream } from '../lib/hooks';
import { DASH, duration, etDateTime } from '../lib/format';
import type { BatchLock, BatchLocks, Job } from '../lib/types';

/** The task id at the end of an ECS task ARN. */
export function taskId(arn: string): string {
  return arn.split('/').pop() ?? arn;
}

const LOCK_USE: Record<BatchLock['name'], string> = {
  pull: 'fse pull and fse import-vix write the same cache/; two at once would corrupt it.',
  holdout: 'Each holdout run appends to one holdout log; two at once would lose an entry.',
  recordings: 'A card backtest uploads closed recording days under this lock. Unlock deletes a lock a finished job left behind.',
};

/** The S3 batch locks (AWS only): who holds each, and Unlock for a lock a dead task left. */
export function LocksPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const locks = useQuery({ queryKey: ['batch-locks'], queryFn: () => api.get<BatchLocks>('/api/batch/locks'), refetchInterval: 10000 });
  const [target, setTarget] = useState<BatchLock | null>(null);
  const unlock = useMutation({
    mutationFn: (name: string) => api.post<{ deleted: boolean }>('/api/batch/unlock', { lock: name, confirm: 'unlock' }),
    onSuccess: (r, name) => {
      toast('ok', r.deleted ? `The ${name} lock is deleted` : `There was no ${name} lock`);
      void qc.invalidateQueries({ queryKey: ['batch-locks'] });
    },
    onError: (err) => toast('danger', 'Unlock refused', errorText(err)),
  });
  if (!locks.data?.enabled) return null;
  return (
    <Panel title="Batch locks" subtitle="S3 locks that keep one pull and one holdout run at a time" icon="shield-check" flush>
      <DataTable dense rows={locks.data.locks} rowKey="name" columns={[
        { key: 'name', label: 'Lock', width: 100 },
        { key: 'held', label: 'State', width: 120, render: (v: boolean, l: BatchLock) => l.error
          ? <Badge variant="warning-outline">unavailable</Badge>
          : v ? <Badge variant="warning">held</Badge> : <Badge variant="outline">free</Badge> },
        { key: 'holder', label: 'Holder', muted: true, render: (v: string[] | null, l: BatchLock) => l.error ?? (v ? v.join(' · ') : 'none') },
        { key: 'action', label: '', width: 110, align: 'right', render: (_: unknown, l: BatchLock) => (
          <Button size="sm" icon="trash-2" disabled={!l.held || unlock.isPending} onClick={() => setTarget(l)}>Unlock</Button>
        ) },
      ]} />
      <ConfirmDialog open={!!target} title={`Delete the ${target?.name ?? ''} lock?`} confirmLabel="Unlock" icon="trash-2"
        busy={unlock.isPending} onClose={() => setTarget(null)}
        onConfirm={() => { if (target) unlock.mutate(target.name); setTarget(null); }}>
        <div className="stack-sm">
          <div>Unlock deletes the lock object. Use it only when no task holds it any more. {target ? LOCK_USE[target.name] : ''}</div>
          {target?.holder && <div className="small muted">Held by {target.holder.join(' · ')}</div>}
        </div>
      </ConfirmDialog>
    </Panel>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const variant = {
    queued: 'outline', running: 'accent-soft', succeeded: 'bull', failed: 'bear',
    cancelled: 'neutral', interrupted: 'warning',
  }[status] ?? 'neutral';
  return <Badge variant={variant as 'neutral'} dot={status === 'running'}>{status}</Badge>;
}

export function JobsPage() {
  const navigate = useNavigate();
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.get<Job[]>('/api/jobs'), refetchInterval: 3000 });
  return (
    <div>
      <PageHead title="Jobs" sub="Every command the console ran, newest first. Output is kept for each job." />
      <div className="page">
        <LocksPanel />
        <Panel flush>
          {jobs.data && jobs.data.length === 0 ? <Empty>No jobs yet. Start one from Workflows or ⌘K.</Empty> : (
            <DataTable dense rows={jobs.data ?? []} onRowClick={(j: Job) => navigate(`/jobs/${j.id}`)} columns={[
              { key: 'created_at', label: 'Started', muted: true, width: 190, render: (v: number) => etDateTime(v) },
              { key: 'title', label: 'Command' },
              { key: 'status', label: 'Status', width: 120, render: (v: string) => <StatusBadge status={v} /> },
              { key: 'exit_code', label: 'Exit', align: 'right', width: 60, render: (v: number | null) => v ?? DASH },
              { key: 'exit_meaning', label: 'Meaning', muted: true, render: (v: string | null) => v ?? DASH },
              { key: 'started_at', label: 'Duration', align: 'right', width: 110,
                render: (v: number | null, j: Job) => duration(v, j.stopped_at) },
            ]} />
          )}
        </Panel>
      </div>
    </div>
  );
}

export function JobDetail({ jobId, compact }: { jobId: string; compact?: boolean }) {
  const { job, lines } = useJobStream(jobId);
  const lastLineAt = useLastLineAt(jobId, lines);
  const cancel = useMutation({ mutationFn: () => api.post(`/api/jobs/${jobId}/cancel`) });
  if (!job) return <Empty>Loading the job…</Empty>;
  const done = isTerminal(job.status);
  return (
    <div className="stack">
      {job.status === 'failed' && job.exit_code === 3 && (job.exit_meaning ?? '').includes('credential') && (
        <Banner tone="warning" title="Credentials missing">
          The command needs a secret that is blank. Set it on <Link to="/secrets">Secrets</Link> and run again.
        </Banner>
      )}
      {job.status === 'failed' && job.exit_code === 3 && (job.exit_meaning ?? '').includes('inputs are missing') && (
        <Banner tone="warning" title="Needed input is missing">
          {job.exit_meaning}. The log names the days. Seed bars, then run the backtest again.
        </Banner>
      )}
      {job.status === 'failed' && job.exit_code === 76 && (
        <Banner tone="warning" title="EFS cache is not mounted">
          {job.exit_meaning}. The task started without an EFS volume at the cache path. Card backtests sync bars from S3. Other batch tasks need the fse-cache volume from fse-infra, or FSE_CACHE_BACKEND=s3.
        </Banner>
      )}
      <Panel title={job.title} subtitle={job.id} icon="terminal"
        actions={<>
          <StatusBadge status={job.status} />
          {!done && <Button size="sm" variant="danger-outline" icon="square" disabled={cancel.isPending}
            onClick={() => cancel.mutate()}>Cancel</Button>}
          {done && job.run_dir && <Link to={`/runs/${encodeURIComponent(job.run_dir)}`}>
            <Button size="sm" variant="primary" icon="chart-line">Open run</Button></Link>}
        </>}>
        <div className="stack">
          {!compact && <KeyValue columns={3} items={[
            ['Started', etDateTime(job.started_at)],
            ['Duration', duration(job.started_at, job.stopped_at)],
            ['Exit', job.exit_code === null ? DASH : `${job.exit_code} · ${job.exit_meaning}`],
            ['Placement', job.placement],
            ['Run folder', job.run_dir ?? DASH],
            ['By', job.user],
            ...(job.external_id ? [['ECS task', taskId(job.external_id)] as [string, string]] : []),
          ]} />}
          <JobActivity job={job} lastLineAt={lastLineAt} />
          <LogView lines={lines} tall={!compact} />
          {cancel.isError && <Banner tone="warning">{errorText(cancel.error)}</Banner>}
        </div>
      </Panel>
    </div>
  );
}

export function JobPage() {
  const { id = '' } = useParams();
  return (
    <div>
      <PageHead title="Job" sub={<Link to="/jobs">All jobs</Link>} />
      <div className="page"><JobDetail jobId={id} /></div>
    </div>
  );
}
