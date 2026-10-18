import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, wsUrl } from './api';
import type { CommandSpec, Job, LiveSnapshot } from './types';

export function useCatalog() {
  return useQuery({
    queryKey: ['catalog'],
    queryFn: () => api.get<{ commands: CommandSpec[]; errors: Record<string, string> }>('/api/catalog'),
    staleTime: Infinity,
  });
}

export function useCommand(id: string): CommandSpec | undefined {
  const { data } = useCatalog();
  return data?.commands.find((c) => c.id === id);
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
export const isTerminal = (status: string) => TERMINAL.has(status);

/** A job's status and log, live over the WebSocket, with HTTP polling as a fallback. */
export function useJobStream(jobId: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    setJob(null);
    setLines([]);
    let closed = false;
    let ws: WebSocket | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;

    const fallback = () => {
      if (poll || closed) return;
      const tick = async () => {
        try {
          const [j, l] = await Promise.all([
            api.get<Job>(`/api/jobs/${jobId}`),
            api.get<{ lines: string[] }>(`/api/jobs/${jobId}/log`),
          ]);
          setJob(j);
          setLines(l.lines);
          if (isTerminal(j.status) && poll) {
            clearInterval(poll);
            poll = null;
          }
        } catch {
          /* keep polling */
        }
      };
      void tick();
      poll = setInterval(tick, 1500);
    };

    try {
      ws = new WebSocket(wsUrl(`/api/ws/jobs/${jobId}`));
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'status') setJob(msg.job);
        else if (msg.type === 'line') setLines((prev) => (prev.length > 5000 ? [...prev.slice(-4000), msg.line] : [...prev, msg.line]));
      };
      ws.onclose = (ev) => {
        setConnected(false);
        if (!closed && ev.code !== 1000) fallback();
        else if (!closed) {
          // The stream ended; read the final status once.
          api.get<Job>(`/api/jobs/${jobId}`).then(setJob, () => undefined);
        }
      };
      ws.onerror = () => fallback();
    } catch {
      fallback();
    }
    return () => {
      closed = true;
      ws?.close();
      if (poll) clearInterval(poll);
    };
  }, [jobId]);

  return { job, lines, connected };
}

/** `Date.now()`, refreshed every `everyMs`, so time-based labels change without new data. */
export function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

/** The live runner snapshot, pushed every 2 s when it changes. */
export function useLive() {
  const [snap, setSnap] = useState<LiveSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const retry = useRef(0);

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const open = () => {
      ws = new WebSocket(wsUrl('/api/ws/live'));
      ws.onopen = () => {
        retry.current = 0;
        setConnected(true);
      };
      ws.onmessage = (ev) => setSnap(JSON.parse(ev.data));
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        retry.current = Math.min(retry.current + 1, 6);
        timer = setTimeout(open, 1000 * 2 ** retry.current);
      };
    };
    api.get<LiveSnapshot>('/api/live').then(setSnap, () => undefined);
    open();
    return () => {
      closed = true;
      ws?.close();
      if (timer) clearTimeout(timer);
    };
  }, []);

  return { snap, connected };
}
