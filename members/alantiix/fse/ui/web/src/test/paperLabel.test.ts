import { describe, expect, it } from 'vitest';
import { HEARTBEAT_FRESH_MS, brokerRunning, paperLabel } from '../lib/paperLabel';
import type { LiveSnapshot, PaperAccount } from '../lib/types';

const NOW = Date.UTC(2026, 9, 13, 15, 0, 0);
const ns = (ms: number) => ms * 1e6;

function snap(over: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    run_dir: 'run-x', host_log: [], heartbeat: { t_ns: ns(NOW - 60_000) }, faults: [], plugins: [],
    blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false },
    ...over,
  };
}

function account(id: string, status: PaperAccount['status'], implicit = false): PaperAccount {
  return {
    id, implicit, unused: false, plugins: [], preset: null, starting_balance: null, balance: null, realized: null,
    unrealized: null, equity: null, hwm: null, eod_high: null, drawdown: null, mll_floor: null, day_net: null,
    trading_days: null, profit_target: null, status, breach_reason: null, breached_at: null, passed_at: null,
    rules_changed_at: null, view: null, nexus: null,
  };
}

describe('brokerRunning', () => {
  it('is true with a fresh heartbeat', () => {
    expect(brokerRunning(snap(), NOW)).toBe(true);
  });

  it('is false with a 12-minute-old heartbeat', () => {
    expect(brokerRunning(snap({ heartbeat: { t_ns: ns(NOW - 12 * 60_000) } }), NOW)).toBe(false);
    expect(12 * 60_000).toBeGreaterThan(HEARTBEAT_FRESH_MS);
  });

  it('is false when host_stopped follows the heartbeat', () => {
    const s = snap({ host_log: [{ kind: 'host_stopped', t_ns: ns(NOW - 30_000) }] });
    expect(brokerRunning(s, NOW)).toBe(false);
    // a host_stopped before the heartbeat is an earlier run
    expect(brokerRunning(snap({ host_log: [{ kind: 'host_stopped', t_ns: ns(NOW - 120_000) }] }), NOW)).toBe(true);
  });

  it('is true when the console runner is running, with no heartbeat', () => {
    const runner = { state: 'running' } as LiveSnapshot['runner'];
    expect(brokerRunning(snap({ heartbeat: null, runner }), NOW)).toBe(true);
  });

  it('is false with no snapshot', () => {
    expect(brokerRunning(null, NOW)).toBe(false);
  });
});

describe('paperLabel', () => {
  it('says idle when no runner is up, whatever the accounts say', () => {
    expect(paperLabel(null, NOW)).toEqual({ text: 'Paper idle', status: 'idle' });
    const stale = snap({ heartbeat: null, accounts: { a: account('a', 'breached') } });
    expect(paperLabel(stale, NOW)).toEqual({ text: 'Paper idle', status: 'idle' });
  });

  it('counts breached named accounts first, then named accounts, then plugins', () => {
    const accounts = { a: account('a', 'active'), b: account('b', 'breached'), 'implicit:p': account('implicit:p', 'active', true) };
    expect(paperLabel(snap({ accounts }), NOW)).toEqual({ text: 'Paper · 1 breached', status: 'halted' });
    accounts.b = account('b', 'flat_locked');
    expect(paperLabel(snap({ accounts }), NOW)).toEqual({ text: 'Paper · 2 accounts', status: 'paper' });
    const implicitOnly = { 'implicit:p': account('implicit:p', 'active', true) };
    const plugins = [{ id: 'p', status: null, log: [], faulted: false, has_raw: true }, { id: 'q', status: null, log: [], faulted: false, has_raw: true }];
    expect(paperLabel(snap({ accounts: implicitOnly, plugins }), NOW)).toEqual({ text: 'Paper · 2 plugins', status: 'paper' });
  });
});
