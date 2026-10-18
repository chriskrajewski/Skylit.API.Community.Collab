import { describe, expect, it } from 'vitest';
import { MINUS } from '../lib/format';
import { accountStatusText, centsText, moneyDiff, orderAccounts, tradeRow, tradesSubtitle, winRate } from '../lib/paper';
import type { JournalTrade, PaperAccount } from '../lib/types';

const TRADE: JournalTrade = {
  line_id: 'plugin_a:s01:t', trade_id: 'plugin_a:s01', plugin: 'plugin_a', account: null, tag: 's01',
  instrument: 'MNQ', side: 'short', qty: 2, qty_at_entry: 2, entry_ns: 1_791_900_000_000_000_000,
  entry_price: '20000.25', exit_ns: Date.UTC(2026, 9, 13, 14, 35, 0) * 1e6, exit_price: '19990.00',
  gross: '41.00', fees: '2.48', net: '-1234.50', exit_reason: 'account_mll', leg: 't', r: '-0.4120',
  globex_day: '2026-10-13', day_source: 'open',
};

describe('trade table mapping', () => {
  it('maps every column from the journal line, money as sent', () => {
    expect(tradeRow(TRADE)).toEqual({
      key: 'plugin_a:s01:t', exit: '2026-10-13 10:35:00 ET', plugin: 'plugin_a', account: 'implicit',
      instrument: 'MNQ', side: 'short', qty: 2, entry: '20000.25', exit_price: '19990.00',
      net: `${MINUS}$1,234.50`, negative: true, r: `${MINUS}0.4120`, reason: 'account_mll',
    });
    const named = tradeRow({ ...TRADE, account: 'topstep_a', net: '3.10', r: null });
    expect([named.account, named.net, named.negative, named.r]).toEqual(['topstep_a', '+$3.10', false, '\u2014']);
  });
});

describe('win rate formatter', () => {
  it('shows a percent, and an en dash with no trades', () => {
    expect(winRate(0.5714)).toBe('57.1%');
    expect(winRate(1)).toBe('100.0%');
    expect(winRate(null)).toBe('\u2013');
    expect(winRate(undefined)).toBe('\u2013');
  });
});

describe('money text', () => {
  it('converts cents and subtracts exactly, never through a float', () => {
    expect(centsText(4126)).toBe('41.26');
    expect(centsText(-500000)).toBe('-5000.00');
    expect(centsText(5)).toBe('0.05');
    expect(centsText(null)).toBeNull();
    expect(moneyDiff('0.30', '0.10')).toBe('0.20'); // 0.3 - 0.1 is 0.19999… as floats
    expect(moneyDiff('12.5', '20.75')).toBe('-8.25');
    expect(moneyDiff('x', '1.00')).toBeNull();
  });
});

describe('accounts', () => {
  it('labels each status with text', () => {
    expect(accountStatusText('active')).toBe('Active');
    expect(accountStatusText('passed')).toBe('Passed');
    expect(accountStatusText('breached', 'account_mll')).toBe('Breached: account_mll');
    expect(accountStatusText('flat_locked')).toBe('Flat-locked until 18:00');
  });

  it('orders named accounts first, then implicit ones, each by id', () => {
    const a = (id: string, implicit: boolean) => ({ id, implicit }) as PaperAccount;
    const got = orderAccounts({ x: a('implicit:plugin_b', true), y: a('zeta', false), z: a('alpha', false), w: a('implicit:plugin_a', true) });
    expect(got.map((x) => x.id)).toEqual(['alpha', 'zeta', 'implicit:plugin_a', 'implicit:plugin_b']);
  });
});
describe('trades subtitle', () => {
  it('counts the rows the table shows when narrowing a plugin route to an account', () => {
    expect(tradesSubtitle({ fetched: 500, shown: 120, total: 900, truncated: true, narrowedFrom: 'plugin_a' }))
      .toBe("120 for this account, from plugin_a's newest 500 of 900");
    expect(tradesSubtitle({ fetched: 40, shown: 12, total: 40, truncated: false, narrowedFrom: 'plugin_a' }))
      .toBe("12 for this account, from plugin_a's newest 40");
  });
  it('keeps the unnarrowed wording', () => {
    expect(tradesSubtitle({ fetched: 500, shown: 500, total: 900, truncated: true, narrowedFrom: null })).toBe('showing 500 of 900');
    expect(tradesSubtitle({ fetched: 80, shown: 80, total: 80, truncated: false, narrowedFrom: null })).toBe('newest first');
  });
});
