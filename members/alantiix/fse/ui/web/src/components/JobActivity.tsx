import { useEffect, useRef, useState } from 'react';
import { StatusDot } from '../ds/index.js';
import { useNow } from '../lib/hooks';
import { duration } from '../lib/format';
import type { Job } from '../lib/types';

/** Seconds of silence after which the dot turns to a warning (the API heartbeat uses 60 s too). */
export const QUIET_S = 60;

/** The API's own "task <id>: still RUNNING, no new output for 1m00s" line (ui/api fse_ui/aws/ecs.py). */
const HEARTBEAT_RE = /^task \S+: still /;

/** When the job's log last gained a real line (ms), or null before the first one.
 *  Works for both the WebSocket and the polling fallback of `useJobStream`: any change to the
 *  count or text of the last real line counts as new output. API heartbeat lines are not output,
 *  so they never move the clock (else the silence would reset every heartbeat). */
export function useLastLineAt(jobId: string, lines: string[]): number | null {
  const [at, setAt] = useState<number | null>(null);
  const seen = useRef<{ jobId: string; count: number; last: string | undefined }>({ jobId, count: 0, last: undefined });
  let end = lines.length;
  while (end > 0 && HEARTBEAT_RE.test(lines[end - 1])) end -= 1;
  const count = end;
  const last = lines[end - 1];
  useEffect(() => {
    const s = seen.current;
    if (s.jobId !== jobId) {
      seen.current = { jobId, count: 0, last: undefined };
      setAt(null);
    }
    if (count !== seen.current.count || last !== seen.current.last) {
      seen.current = { jobId, count, last };
      if (count > 0) setAt(Date.now());
    }
  }, [jobId, count, last]);
  return at;
}

/** A ticking "still alive" row for a running job: elapsed time and how long since the last line.
 *  Long S3 syncs and Spot waits can be silent for minutes; this shows the console is still following. */
export function JobActivity({ job, lastLineAt }: { job: Job; lastLineAt: number | null }) {
  const now = useNow(1000);
  if (job.status !== 'running') return null;
  const nowS = now / 1000;
  const quiet = lastLineAt !== null && nowS - lastLineAt / 1000 >= QUIET_S;
  const last = lastLineAt === null
    ? 'waiting for the first output'
    : `last output ${duration(lastLineAt / 1000, nowS)} ago`;
  // aria-live off: LogView already announces new lines; a ticking clock would spam screen readers.
  return (
    <div className="row small muted" aria-live="off" data-testid="job-activity">
      <StatusDot status={quiet ? 'warning' : 'live'}
        label={`Running · ${duration(job.started_at, nowS)} elapsed · ${last}`} />
    </div>
  );
}
