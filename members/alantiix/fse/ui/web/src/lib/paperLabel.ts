// The status bar's paper label (design §7.5), derived from the live snapshot.
import type { LiveSnapshot } from './types';

/** Two 5-minute heartbeats plus 1 minute of slack. */
export const HEARTBEAT_FRESH_MS = 11 * 60_000;

/**
 * Whether a runner is up. The console's own controller says so, or a fresh heartbeat
 * does: the launchd runner (scripts/run_paper.sh) is not the console's child, so the
 * controller reports `stopped` while it runs.
 */
export function brokerRunning(snap: LiveSnapshot | null | undefined, nowMs: number): boolean {
  if (!snap) return false;
  if (snap.runner?.state === 'running') return true;
  const hb = snap.heartbeat?.t_ns;
  if (typeof hb !== 'number') return false;
  // t_ns arrives as a JSON number (~1.8e18, so ~256 ns of float error): fine at ms scale
  if (nowMs - hb / 1e6 > HEARTBEAT_FRESH_MS) return false;
  // a host_stopped line after the heartbeat means the runner exited
  return !snap.host_log.some((e) => e.kind === 'host_stopped' && typeof e.t_ns === 'number' && e.t_ns > hb);
}

export interface PaperLabel {
  text: string;
  /** The ds StatusDot status. */
  status: 'idle' | 'halted' | 'paper';
}

/** `Paper idle`, `Paper · N breached`, `Paper · N accounts` or `Paper · N plugins`. */
export function paperLabel(snap: LiveSnapshot | null | undefined, nowMs: number): PaperLabel {
  if (!snap || !brokerRunning(snap, nowMs)) return { text: 'Paper idle', status: 'idle' };
  const named = Object.values(snap.accounts ?? {}).filter((a) => !a.implicit);
  const breached = named.filter((a) => a.status === 'breached').length;
  if (breached) return { text: `Paper · ${breached} breached`, status: 'halted' };
  if (named.length) return { text: `Paper · ${named.length} accounts`, status: 'paper' };
  const plugins = snap.paper ? Object.keys(snap.paper.plugins).length : snap.plugins.length;
  return { text: `Paper · ${plugins} plugins`, status: 'paper' };
}
