// Pure helpers of the Analytics page: labels, money from cents and the trade table rows.
// Money stays text or integer cents; nothing here adds dollars as floats (NF-1).
import { DASH, MINUS, etDateTime, fromNs, money } from './format';
import type { AccountStatus, JournalTrade, PaperAccount } from './types';

/** "Win rate (net > 0)" value: a 0..1 ratio as a percent, `–` with no trades. */
export function winRate(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '\u2013';
  return `${(v * 100).toFixed(1)}%`;
}

/** The status badge text (D-F6: never colour only). */
export function accountStatusText(status: AccountStatus | null | undefined, breachReason?: string | null): string {
  switch (status) {
    case 'active': return 'Active';
    case 'passed': return 'Passed';
    case 'breached': return `Breached: ${breachReason ?? 'unknown'}`;
    case 'flat_locked': return 'Flat-locked until 18:00';
    default: return 'Unknown';
  }
}

/** Integer cents → "-12.34" (the API's money text), exactly. */
export function centsText(cents: number | null | undefined): string | null {
  if (cents === null || cents === undefined || !Number.isInteger(cents)) return null;
  const abs = Math.abs(cents);
  return `${cents < 0 ? '-' : ''}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`;
}

/** Money text → integer cents as a BigInt (`null` if it is not plain decimal text). */
function cents(v: string | null | undefined): bigint | null {
  const m = typeof v === 'string' ? /^([+-]?)(\d+)(?:\.(\d{1,2}))?$/.exec(v.trim()) : null;
  if (!m) return null;
  const n = BigInt(m[2]) * 100n + BigInt((m[3] ?? '').padEnd(2, '0') || '0');
  return m[1] === '-' ? -n : n;
}

/** `a - b` of two money strings, exact; `null` when either is not money. */
export function moneyDiff(a: string | null | undefined, b: string | null | undefined): string | null {
  const x = cents(a);
  const y = cents(b);
  if (x === null || y === null) return null;
  const d = x - y;
  const abs = d < 0n ? -d : d;
  return `${d < 0n ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
}

/** Named accounts first, then implicit ones, each by id (design §6.1). */
export function orderAccounts(accounts: Record<string, PaperAccount> | undefined): PaperAccount[] {
  return Object.values(accounts ?? {}).sort((a, b) =>
    Number(a.implicit) - Number(b.implicit) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/**
 * Trades panel subtitle. `fetched` is what the route returned, `shown` what the table holds after
 * the client-side account narrowing (plugin and account both set), `total` the route's total.
 */
export function tradesSubtitle(
  { fetched, shown, total, truncated, narrowedFrom }:
  { fetched: number; shown: number; total: number; truncated: boolean; narrowedFrom: string | null },
): string {
  if (narrowedFrom !== null) {
    const of = truncated ? `${fetched} of ${total}` : `${fetched}`;
    return `${shown} for this account, from ${narrowedFrom}'s newest ${of}`;
  }
  return truncated ? `showing ${shown} of ${total}` : 'newest first';
}

export interface TradeRow {
  key: string; exit: string; plugin: string; account: string; instrument: string; side: 'long' | 'short';
  qty: number; entry: string; exit_price: string; net: string; negative: boolean; r: string; reason: string;
}

/** One journal `trade` line → one row of the trades table. */
export function tradeRow(t: JournalTrade): TradeRow {
  const r = t.r === null || t.r === undefined ? DASH : t.r.startsWith('-') ? `${MINUS}${t.r.slice(1)}` : t.r;
  return {
    key: t.line_id,
    exit: etDateTime(fromNs(t.exit_ns)),
    plugin: t.plugin,
    account: t.account ?? 'implicit',
    instrument: t.instrument,
    side: t.side,
    qty: t.qty,
    entry: t.entry_price,
    exit_price: t.exit_price,
    net: money(t.net),
    negative: (cents(t.net) ?? 0n) < 0n,
    r,
    reason: t.exit_reason,
  };
}
