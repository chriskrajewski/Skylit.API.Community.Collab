import type { FindingCardData, FindingTrade, MapLevel } from '../ds/index.js';
import type { CardRow, DecisionCard } from './types';
import { etTime, fromNs } from './format';

/** Engine Finding_Card JSON (and optional narration envelope fields). */
export type EngineFindingCard = Record<string, unknown>;

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown): string | null {
  if (v == null) return null;
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function levelShown(field: unknown): string | null {
  const r = asRecord(field);
  if (!r) return str(field);
  const level = r.level;
  if (level === 'unavailable' || level == null) return null;
  return typeof level === 'number' ? level.toFixed(2) : str(level);
}

function nyTime(decisionTimeNy: unknown, fallback?: unknown): string {
  const raw = str(decisionTimeNy) ?? str(fallback) ?? '';
  // 2026-10-14T10:35:00-04:00 → 10:35:00 ET
  const m = raw.match(/T(\d{2}:\d{2}:\d{2})/);
  if (m) return `${m[1]} ET`;
  return raw || '—';
}

function trinityText(trinity: unknown): string | null {
  const r = asRecord(trinity);
  if (!r) return str(trinity);
  const vals = Object.values(r).map(String);
  if (!vals.length) return null;
  if (vals.every((v) => v === 'above') || vals.every((v) => v === 'below')) return 'agree';
  if (vals.some((v) => v === 'unavailable')) return 'unavailable';
  return 'mixed';
}

function modeOf(v: unknown): FindingCardData['mode'] | undefined {
  return v === 'paper' || v === 'practice' || v === 'combine' ? v : undefined;
}

function flipText(flips: unknown): string | undefined {
  if (!Array.isArray(flips) || !flips.length) return undefined;
  const first = asRecord(flips[0]);
  if (!first) return undefined;
  const old = first.old ?? first.before;
  const neu = first.new ?? first.after;
  if (old == null || neu == null) return undefined;
  return `${old} → ${neu}`;
}

function mapsOf(card: EngineFindingCard): FindingCardData['maps'] {
  const rows = Array.isArray(card.map) ? card.map : [];
  const spot = asRecord(card.spot) ?? {};
  const bySym = new Map<string, NonNullable<FindingCardData['maps']>[number]>();
  for (const row of rows) {
    const m = asRecord(row);
    if (!m) continue;
    const sym = str(m.symbol);
    if (!sym || bySym.has(sym)) continue;
    const spotVal = spot[sym];
    bySym.set(sym, {
      sym,
      king: levelShown(m.king),
      ceiling: levelShown(m.ceiling),
      floor: levelShown(m.floor),
      spot: typeof spotVal === 'number' ? spotVal.toFixed(2) : str(spotVal),
    });
  }
  return [...bySym.values()];
}

function setupsOf(card: EngineFindingCard): FindingCardData['setups'] {
  const rows = Array.isArray(card.setups) ? card.setups : [];
  return rows.flatMap((row) => {
    const s = asRecord(row);
    if (!s) return [];
    const grade = s.grade;
    if (grade !== 'A_Plus' && grade !== 'Alert_2R' && grade !== 'Pass') return [];
    const reasons = Array.isArray(s.reasons) ? s.reasons.map(String) : undefined;
    const why = Array.isArray(s.why) ? s.why.map(String) : undefined;
    return [{ grade, key: str(s.key) ?? '', reasons, why }];
  });
}

function orderText(card: EngineFindingCard): string | null {
  const working = Array.isArray(card.working_orders) ? card.working_orders : [];
  if (!working.length || working === ('none' as unknown)) return null;
  return working
    .map((o) => {
      const r = asRecord(o);
      if (!r) return null;
      return `${r.instrument} ${r.side} ${r.kind} ${r.price} ×${r.qty}`;
    })
    .filter(Boolean)
    .join(' · ') || null;
}

function positionText(card: EngineFindingCard): string | null {
  const rows = Array.isArray(card.positions) ? card.positions : [];
  const open = rows
    .map((p) => asRecord(p))
    .filter((p): p is Record<string, unknown> => !!p && p.direction !== 'flat');
  if (!open.length) return 'flat';
  return open
    .map((p) => `${p.direction} ${p.contracts} ${p.instrument} @ ${p.entry}`)
    .join(' · ');
}

function watchOf(card: EngineFindingCard): { up: string | null; down: string | null } {
  const rows = Array.isArray(card.watch) ? card.watch : [];
  const first = asRecord(rows[0]);
  if (!first) return { up: null, down: null };
  return { up: str(first.above), down: str(first.below) };
}

function ladderOf(raw: unknown): MapLevel[] | undefined {
  if (!Array.isArray(raw) || !raw.length || raw === ('none' as unknown)) return undefined;
  const rows = raw.flatMap((row) => {
    const r = asRecord(row);
    if (!r) return [];
    const kind = str(r.kind);
    const level = num(r.level);
    if (
      kind !== 'king' && kind !== 'ceiling' && kind !== 'floor'
      && kind !== 'gatekeeper' && kind !== 'air'
    ) {
      return [];
    }
    if (level == null) return [];
    const out: MapLevel = { kind: kind as MapLevel['kind'], level };
    if (r.strike != null) out.strike = typeof r.strike === 'number' ? r.strike : String(r.strike);
    const value = num(r.value);
    if (value != null) out.value = value;
    const label = str(r.label);
    if (label) out.label = label;
    return [out];
  });
  return rows.length ? rows : undefined;
}

function barsOf(raw: unknown): Array<{ o: number; h: number; l: number; c: number }> {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((b) => {
    const r = asRecord(b);
    if (!r) return [];
    const o = num(r.o), h = num(r.h), l = num(r.l), c = num(r.c);
    return o == null || h == null || l == null || c == null ? [] : [{ o, h, l, c }];
  });
}

function oddsOf(raw: unknown): FindingTrade['odds'] {
  const oddsRaw = asRecord(raw);
  if (!oddsRaw || !Array.isArray(oddsRaw.items)) return null;
  const items = oddsRaw.items.flatMap((item) => {
    const r = asRecord(item);
    if (!r) return [];
    const pct = num(r.pct);
    const label = str(r.label);
    return pct == null || !label ? [] : [{ pct, label }];
  });
  return items.length ? { label: str(oddsRaw.label) ?? '', items } : null;
}

function tradeOf(raw: unknown): FindingTrade | undefined {
  const t = asRecord(raw);
  if (!t) return undefined;
  const direction = t.direction === 'long' || t.direction === 'short' ? t.direction : null;
  const grade = t.grade === 'A_Plus' || t.grade === 'Alert_2R' || t.grade === 'Pass' ? t.grade : null;
  const entry = num(t.entry);
  const stop = num(t.stop);
  const spot = num(t.spot);
  const qty = num(t.qty);
  const rr = num(t.rr);
  if (!direction || !grade || entry == null || stop == null || spot == null || qty == null || rr == null) {
    return undefined;
  }
  const targets = Array.isArray(t.targets)
    ? t.targets.flatMap((x) => {
        const r = asRecord(x);
        if (!r) return [];
        const price = num(r.price);
        const tqty = num(r.qty);
        if (price == null || tqty == null) return [];
        return [{ label: str(r.label) ?? 'TP', price, qty: tqty, tag: str(r.tag) ?? undefined }];
      })
    : [];
  const plan = asRecord(t.plan);
  if (!plan || !str(plan.if) || !str(plan.out) || !str(plan.tgt)) return undefined;
  return {
    instrument: str(t.instrument) ?? '',
    name: str(t.name) ?? undefined,
    direction,
    grade,
    pattern: str(t.pattern) ?? '',
    node: str(t.node) ?? '',
    tap: num(t.tap) ?? 1,
    spot,
    dayPct: num(t.dayPct),
    rangePct: num(t.rangePct),
    tick: num(t.tick) ?? undefined,
    tickValue: num(t.tickValue) ?? undefined,
    qty,
    rr,
    gates: str(t.gates),
    entry,
    entryTag: str(t.entryTag) ?? undefined,
    stop,
    targets,
    levels: Array.isArray(t.levels)
      ? t.levels.flatMap((l) => {
          const r = asRecord(l);
          if (!r) return [];
          const kind = str(r.kind);
          const price = num(r.price);
          if (!kind || price == null) return [];
          return [{
            kind: kind as NonNullable<FindingTrade['levels']>[number]['kind'],
            price,
            to: num(r.to) ?? undefined,
            label: str(r.label) ?? undefined,
          }];
        })
      : undefined,
    plan: { if: str(plan.if)!, out: str(plan.out)!, tgt: str(plan.tgt)! },
    context: Array.isArray(t.context)
      ? t.context.flatMap((c) => {
          const r = asRecord(c);
          if (!r) return [];
          const icon = str(r.icon);
          const text = str(r.text);
          return icon && text ? [{ icon, text }] : [];
        })
      : undefined,
    odds: oddsOf(t.odds),
    action: str(t.action) ?? undefined,
    actionLabel: str(t.actionLabel) ?? undefined,
    bars: barsOf(t.bars),
  };
}

/** Deterministic Pass why when Narrator is off or failed. */
export function passWhyFallback(card: EngineFindingCard): string | null {
  const setups = Array.isArray(card.setups) ? card.setups : [];
  const pass = setups
    .map((s) => asRecord(s))
    .filter((s): s is Record<string, unknown> => !!s && s.grade === 'Pass');
  if (!pass.length) return null;
  const lines = pass.flatMap((s) => {
    if (Array.isArray(s.why) && s.why.length) return s.why.map(String);
    if (Array.isArray(s.reasons) && s.reasons.length) {
      return s.reasons.map((code) => String(code).replace(/_/g, ' '));
    }
    return [];
  });
  if (!lines.length) return 'Setup was passed; no order armed.';
  return `Passed — ${lines.join('; ')}.`;
}

/** Deterministic why prose for any Finding_Card grade when Narrator is off. */
export function decisionWhyFallback(card: EngineFindingCard): string | null {
  const pass = passWhyFallback(card);
  if (pass) return pass;
  const setups = Array.isArray(card.setups) ? card.setups : [];
  const primary = asRecord(setups[0]);
  if (!primary) return str(card.summary);
  const grade = str(primary.grade) ?? 'decision';
  const key = str(primary.key) ?? 'setup';
  if (Array.isArray(primary.why) && primary.why.length) {
    return `${grade.replace(/_/g, ' ')} — ${primary.why.map(String).join('; ')}.`;
  }
  if (Array.isArray(primary.reasons) && primary.reasons.length) {
    return `${grade.replace(/_/g, ' ')} — ${primary.reasons.map((c) => String(c).replace(/_/g, ' ')).join('; ')}.`;
  }
  return `${grade.replace(/_/g, ' ')} — ${key}.`;
}

/**
 * Unwrap API / jsonl payload (`{narration, card}` or bare card) into DS FindingCardData.
 */
export function toFindingCardData(
  raw: EngineFindingCard,
  opts?: { narration?: string | null },
): FindingCardData {
  const card = asRecord(raw.card) ?? raw;
  const kind = card.kind === 'premarket' ? 'premarket' : 'decision';
  const watch = watchOf(card);
  const trade = tradeOf(card.trade);
  const narration = opts?.narration ?? str(raw.narration);
  const whyPassed = narration && !str(raw.narration_note)
    ? narration
    : decisionWhyFallback(card);
  const notListed = num(card.setups_not_listed);
  const ladder = ladderOf(card.ladder);
  return {
    kind,
    time: nyTime(card.decision_time_ny, card.time),
    mode: modeOf(card.order_mode ?? card.mode),
    regime: str(card.regime),
    mapGrade: str(card.map_grade ?? card.mapGrade),
    trinity: trinityText(card.trinity),
    maps: mapsOf(card),
    flip: flipText(card.king_flips) ?? str(card.flip) ?? undefined,
    setups: setupsOf(card),
    notListed: notListed ?? undefined,
    orders: orderText(card) ?? undefined,
    position: positionText(card) ?? undefined,
    watchUp: watch.up,
    watchDown: watch.down,
    summary: str(card.summary),
    whyPassed,
    trade,
    ladder,
    ladderSpot: ladder && trade ? trade.spot : undefined,
  };
}

/** Extract the engine card object from a Cards API row.raw. */
export function engineCardFromRow(raw: EngineFindingCard): EngineFindingCard {
  return asRecord(raw.card) ?? raw;
}

function decisionGrade(action: DecisionCard['action']): FindingTrade['grade'] {
  switch (action) {
    case 'enter':
      return 'A_Plus';
    case 'skip':
    case 'hold':
    case 'info':
      return 'Pass';
    case 'exit':
    case 'result':
      return 'Alert_2R';
    default: {
      const _exhaustive: never = action;
      void _exhaustive;
      return 'Pass';
    }
  }
}

function decisionRr(c: DecisionCard): number | null {
  if (c.entry == null || c.stop == null) return null;
  const risk = Math.abs(c.entry - c.stop);
  if (!(risk > 0)) return null;
  const tgt = c.targets[0];
  if (tgt == null) return 1;
  return Math.abs(tgt - c.entry) / risk;
}

/** Dollars per 0.25-point tick (matches ``fse.sim.fills.TICK_VALUE_USD``). */
const TICK_VALUE_USD: Record<string, number> = {
  MES: 1.25,
  MNQ: 0.5,
  ES: 12.5,
  NQ: 5,
};

function tickValueFor(instrument: string | null | undefined): number {
  if (!instrument) return 1.25;
  const key = instrument.toUpperCase();
  if (key in TICK_VALUE_USD) return TICK_VALUE_USD[key]!;
  // Contract roots: MNQZ6, CON.F.US.MNQ.Z26, etc.
  for (const root of Object.keys(TICK_VALUE_USD)) {
    if (key.startsWith(root) || key.includes(`.${root}.`) || key.includes(`.${root}`)) {
      return TICK_VALUE_USD[root]!;
    }
  }
  return 1.25;
}

function fieldValue(c: DecisionCard, label: string): string | null {
  const hit = c.fields.find(([k]) => k.toLowerCase() === label.toLowerCase());
  return hit ? hit[1] : null;
}

function fieldNum(c: DecisionCard, label: string): number | null {
  const raw = fieldValue(c, label);
  if (raw == null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function reasonEnglish(reason: string | null | undefined): string {
  if (!reason?.trim()) return 'No further detail was recorded.';
  return reason.trim().replace(/_/g, ' ');
}

/** Deterministic plain-English why for a plugin decision card. */
export function decisionCardWhy(c: DecisionCard): string {
  if (c.narration?.trim()) return c.narration.trim();
  const reason = reasonEnglish(c.reason);
  const fieldBits = c.fields.slice(0, 6).map(([k, v]) => `${k} ${v}`).join('; ');
  switch (c.action) {
    case 'skip':
      return fieldBits
        ? `Skipped — ${reason} (${fieldBits}).`
        : `Skipped — ${reason}.`;
    case 'hold':
      return fieldBits
        ? `Held — ${reason} (${fieldBits}).`
        : `Held — ${reason}.`;
    case 'info':
      return fieldBits
        ? `${c.title}: ${reason} (${fieldBits}).`
        : `${c.title}: ${reason}`;
    case 'enter':
      return fieldBits
        ? `Entered — ${reason} (${fieldBits}).`
        : `Entered — ${reason}.`;
    case 'exit':
      return fieldBits
        ? `Exited — ${reason} (${fieldBits}).`
        : `Exited — ${reason}.`;
    case 'result':
      return fieldBits
        ? `Result — ${reason} (${fieldBits}).`
        : `Result — ${reason}.`;
    default: {
      const _exhaustive: never = c.action;
      void _exhaustive;
      return reason;
    }
  }
}

function skipActionChrome(action: DecisionCard['action']): { action: string; actionLabel: string } {
  switch (action) {
    case 'skip':
      return { action: 'SKIP · no order', actionLabel: 'Skip' };
    case 'hold':
      return { action: 'HOLD · no order', actionLabel: 'Hold' };
    case 'info':
      return { action: 'INFO · no order', actionLabel: 'Info' };
    case 'enter':
      return { action: 'ENTER', actionLabel: 'Armed' };
    case 'exit':
      return { action: 'EXIT', actionLabel: 'Exit' };
    case 'result':
      return { action: 'RESULT', actionLabel: 'Result' };
    default: {
      const _exhaustive: never = action;
      void _exhaustive;
      return { action: 'PASS · no order', actionLabel: 'Pass' };
    }
  }
}

function ladderFromDecision(c: DecisionCard, spot: number): MapLevel[] | undefined {
  if (c.ladder?.length) {
    return c.ladder.flatMap((row) => {
      const kind = row.kind === 'wall' ? 'ceiling' : row.kind;
      if (
        kind !== 'king' && kind !== 'ceiling' && kind !== 'floor'
        && kind !== 'gatekeeper' && kind !== 'air'
      ) {
        return [];
      }
      return [{
        kind,
        level: row.level,
        strike: row.strike,
        value: row.value,
        label: row.label,
      }];
    });
  }
  // Thin fallback from priced plan so Map is not empty text-only when levels exist.
  if (c.entry == null || c.stop == null) return undefined;
  const rows: MapLevel[] = [
    { kind: 'king', level: c.entry, label: 'entry', value: 100 },
    { kind: 'floor', level: c.stop, label: 'stop', value: 60 },
    ...c.targets.map((price, i) => ({
      kind: 'ceiling' as const,
      level: price,
      label: `TP${i + 1}`,
      value: 40 - i * 5,
    })),
  ];
  void spot;
  return rows;
}

function decisionTrade(c: DecisionCard): FindingTrade {
  const grade = decisionGrade(c.action);
  const passLike = grade === 'Pass';
  const direction = c.side === 'long' || c.side === 'short' ? c.side : 'long';
  const bars = c.bars?.length ? c.bars : [];
  const lastClose = bars.length ? bars[bars.length - 1]!.c : null;
  const spot = c.spot
    ?? fieldNum(c, 'spot')
    ?? c.entry
    ?? lastClose
    ?? 0;
  const entry = c.entry ?? spot;
  const stop = c.stop ?? spot;
  const qty = c.qty ?? 1;
  const rr = decisionRr(c) ?? 0;
  const targets = c.targets.map((price, i) => ({
    label: `TP${i + 1}`,
    price,
    qty: Math.max(1, Math.floor(qty / Math.max(1, c.targets.length))),
  }));
  const risk = Math.abs(entry - stop) || 1;
  const why = decisionCardWhy(c);
  const planIf = c.reason?.trim() || c.title;
  const planOut = passLike && c.stop == null
    ? 'No stop — order not armed'
    : `Stop ${stop.toFixed(2)}`;
  const planTgt = targets.length
    ? targets.map((t) => t.price.toFixed(2)).join(' then ')
    : (passLike ? 'No target — skipped' : `${(direction === 'long' ? entry + risk : entry - risk).toFixed(2)}`);
  const chrome = skipActionChrome(c.action);
  const gates = c.gates ?? fieldValue(c, 'gates');
  const instrument = c.instrument ?? c.symbol ?? '—';
  return {
    instrument,
    direction,
    grade,
    pattern: c.tag ?? c.action,
    node: c.title,
    tap: 1,
    spot,
    dayPct: c.dayPct ?? fieldNum(c, 'dayPct'),
    rangePct: c.rangePct ?? fieldNum(c, 'rangePct'),
    tick: 0.25,
    tickValue: tickValueFor(c.instrument ?? c.symbol),
    qty,
    rr,
    gates,
    entry,
    stop,
    targets,
    levels: [
      { kind: 'king' as const, price: entry, label: passLike ? 'spot' : 'entry' },
      ...(c.stop != null ? [{ kind: 'floor' as const, price: stop, label: 'stop' }] : []),
      ...targets.map((t) => ({ kind: 'wall' as const, price: t.price, label: t.label })),
    ],
    plan: { if: planIf, out: planOut, tgt: planTgt },
    context: why ? [{ icon: 'radar', text: why }] : undefined,
    action: chrome.action,
    actionLabel: chrome.actionLabel,
    bars,
    odds: c.odds ?? null,
  };
}

/**
 * Map a plugin decision-card row (`format: "decision"`) into FindingCardData so
 * Cards detail uses the same DS FindingCard / rich `.fse-tcard` as Finding_Cards.
 * Skips/holds/info always get Pass-grade rich chrome (not compact).
 */
export function toFindingCardDataFromDecision(row: CardRow): FindingCardData {
  const c = row.card;
  if (!c) {
    return { kind: 'decision', time: '—', summary: row.title ?? 'card' };
  }
  const trade = decisionTrade(c);
  const time = etTime(fromNs(c.as_of_ns ?? row.t_ns)) || '—';
  const whyPassed = decisionCardWhy(c);
  const ladder = ladderFromDecision(c, trade.spot);
  return {
    kind: 'decision',
    time,
    summary: undefined,
    whyPassed,
    trade,
    ladder,
    ladderSpot: ladder ? trade.spot : undefined,
    setups: [{
      grade: trade.grade,
      key: c.title,
      reasons: c.reason ? [c.reason] : undefined,
      why: [whyPassed],
    }],
  };
}

/** Cards detail payload: Finding_Card JSON or plugin decision card → DS FindingCardData. */
export function cardRowToFindingCardData(row: CardRow): FindingCardData {
  if (row.format === 'decision') return toFindingCardDataFromDecision(row);
  return toFindingCardData(row.raw, { narration: row.raw.narration as string | null | undefined });
}
