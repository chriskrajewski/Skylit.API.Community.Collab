// Number, time and missing-value formatting, per the design system's content rules:
// two decimals for prices / R / metrics, four for probabilities, signed R with a true
// minus, New York times ("10:35:00 ET"), ISO dates, and missing data shown, not hidden.

export const MINUS = '\u2212';
export const DASH = '\u2014';
export const UNAVAILABLE = 'unavailable';
export const NOT_APPLICABLE = 'not applicable';

/** The engine encodes NotApplicable as an empty object. */
export function isNotApplicable(v: unknown): boolean {
  return !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0;
}

export function toNumber(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function fixed(n: number, places: number): string {
  return Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });
}

/** 2 decimals, true minus for negatives, `—` / `unavailable` / `not applicable` when missing. */
export function num(v: unknown, places = 2, missing: string = DASH): string {
  if (isNotApplicable(v)) return NOT_APPLICABLE;
  const n = toNumber(v);
  if (n === null) return v === null || v === undefined ? missing : String(v);
  return (n < 0 ? MINUS : '') + fixed(n, places);
}

/** Signed R: +2.41 R, −1.00 R. */
export function signedR(v: unknown, missing: string = DASH): string {
  if (isNotApplicable(v)) return NOT_APPLICABLE;
  const n = toNumber(v);
  if (n === null) return missing;
  const sign = n > 0 ? '+' : n < 0 ? MINUS : '';
  return `${sign}${fixed(n, 2)} R`;
}

/** Signed dollars: +$903.75, −$120.00. */
export function usd(v: unknown, missing: string = DASH): string {
  if (isNotApplicable(v)) return NOT_APPLICABLE;
  const n = toNumber(v);
  if (n === null) return missing;
  const sign = n > 0 ? '+' : n < 0 ? MINUS : '';
  return `${sign}$${fixed(n, 2)}`;
}

/** Signed dollars from an API money string ("-120.50" → "−$120.50") without a float:
 * the digits are regrouped as text, so the cents shown are the cents sent. */
export function money(v: string | null | undefined, { signed = true, missing = DASH }: { signed?: boolean; missing?: string } = {}): string {
  const m = typeof v === 'string' ? /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(v.trim()) : null;
  if (!m) return v === null || v === undefined ? missing : String(v);
  const whole = m[2].replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const cents = (m[3] ?? '').padEnd(2, '0');
  const zero = /^0*$/.test(m[2]) && /^0*$/.test(cents);
  const sign = zero ? '' : m[1] === '-' ? MINUS : signed ? '+' : '';
  return `${sign}$${whole}.${cents}`;
}

export function pct(v: unknown, missing: string = DASH): string {
  if (isNotApplicable(v)) return NOT_APPLICABLE;
  const n = toNumber(v);
  return n === null ? missing : `${num(n, 2)}%`;
}

export function prob(v: unknown, missing: string = DASH): string {
  if (isNotApplicable(v)) return NOT_APPLICABLE;
  const n = toNumber(v);
  return n === null ? missing : num(n, 4);
}

export function tone(v: unknown): 'pos' | 'neg' | 'flat' | undefined {
  const n = toNumber(v);
  if (n === null) return undefined;
  return n > 0 ? 'pos' : n < 0 ? 'neg' : 'flat';
}

const NY_TIME = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
});
const NY_DATE = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
});

/** "10:35:00 ET" from epoch seconds. */
export function etTime(epochS: number | null | undefined): string {
  if (epochS === null || epochS === undefined) return DASH;
  return `${NY_TIME.format(new Date(epochS * 1000))} ET`;
}

/** "2026-10-06" (New York date) from epoch seconds. */
export function isoDate(epochS: number | null | undefined): string {
  if (epochS === null || epochS === undefined) return DASH;
  return NY_DATE.format(new Date(epochS * 1000));
}

export function etDateTime(epochS: number | null | undefined): string {
  if (epochS === null || epochS === undefined) return DASH;
  return `${isoDate(epochS)} ${etTime(epochS)}`;
}

export function fromNs(ns: number | string | null | undefined): number | null {
  if (ns === null || ns === undefined) return null;
  return Number(ns) / 1e9;
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

export function duration(startS: number | null, stopS: number | null): string {
  if (startS === null) return DASH;
  const end = stopS ?? Date.now() / 1000;
  const s = Math.max(0, Math.round(end - startS));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}
