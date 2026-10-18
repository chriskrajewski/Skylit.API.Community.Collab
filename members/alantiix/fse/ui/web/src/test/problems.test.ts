import { describe, expect, it } from 'vitest';
import { routeProblems } from '../lib/problems';

describe('routeProblems', () => {
  it('routes one string of each kind', () => {
    const acct = 'accounts[1].id: duplicate account id topstep_a';
    const field = 'accounts[0].rules.maximum_loss_limit.value: 0.001 should have at most 2 decimal places (whole cents)';
    const ref = "plugins[2].account: unknown account 'nope'";
    const plain = 'plugins[0].risk.max_contracts: Input should be greater than or equal to 1';
    const other = 'data_plane.instruments: MES is needed by plugin_b';
    const r = routeProblems([acct, field, ref, plain, other]);
    expect([...r.accounts.entries()]).toEqual([[1, [acct]], [0, [field]]]);
    expect([...r.pluginAccount.entries()]).toEqual([[2, [ref]]]);
    expect(r.rest).toEqual([plain, other]);
  });

  it('keeps every string of one index together, in order', () => {
    const a = 'accounts[0].nexus: two accounts mirror to one Nexus account';
    const b = 'accounts[0].id: duplicate account id x';
    expect(routeProblems([a, b]).accounts.get(0)).toEqual([a, b]);
    expect(routeProblems([]).rest).toEqual([]);
  });
});
