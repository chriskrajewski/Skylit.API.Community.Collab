// Paper-account edits of the runner YAML (design §7.6): presets, the preset-diff writer
// and removeAccount. Every edit goes through the yaml Document, so the rest of the text
// (comments, order, quoting) is left as it was.
import type { Document } from 'yaml';

export type PresetName = 'topstep_50k_combine' | 'none';
export const PRESET_NAMES: PresetName[] = ['topstep_50k_combine', 'none'];

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Rules = { [k: string]: Json };

/**
 * The resolved rules of each preset: `PaperAccountRules()` defaults with
 * `fse.live.host.accounts_config.PRESETS[name]` merged over them. Money is a decimal
 * string, as in the YAML. ui/api/tests/test_accounts_yaml_presets.py keeps this in step
 * with the engine.
 */
export const PRESET_RULES: Record<PresetName, Rules> = {
  topstep_50k_combine: {
    starting_balance: { value: '50000.00' },
    profit_target: { enabled: true, value: '3000.00' },
    maximum_loss_limit: { enabled: true, value: '2000.00', mode: 'eod_trailing', trail_lock: true },
    daily_loss_limit: { enabled: true, value: '1000.00' },
    consistency_target: { enabled: true, pct: 55.0 },
    position_cap: { enabled: true, micro_equivalents: 50 },
    flat_deadline: '16:10',
    early_close_offset_min: 15,
    flat_deadline_enabled: true,
    no_trade_windows: [],
    min_trading_days: null,
  },
  none: {
    starting_balance: { value: '50000.00' },
    profit_target: { enabled: false, value: '3000.00' },
    maximum_loss_limit: { enabled: false, value: '0.01', mode: 'eod_trailing', trail_lock: true },
    daily_loss_limit: { enabled: false, value: '1000.00' },
    consistency_target: { enabled: false, pct: 55.0 },
    position_cap: { enabled: false, micro_equivalents: 50 },
    flat_deadline: '16:10',
    early_close_offset_min: 15,
    flat_deadline_enabled: false,
    no_trade_windows: [],
    min_trading_days: null,
  },
};

export interface NexusForm { enabled: boolean; account: string; test_only: boolean }
export const NEXUS_DEFAULT: NexusForm = { enabled: false, account: 'practice', test_only: false };

/** One `accounts[]` entry as the form edits it: `rules` holds only the overrides. */
export interface AccountForm { id: string; preset: PresetName; rules: Rules; nexus: NexusForm }

function isMap(v: unknown): v is Rules {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/** `over` merged over `base` at leaf level (the engine's `_deep_merge`). */
export function resolveRules(base: Rules, over: Rules): Rules {
  const out: Rules = structuredClone(base);
  for (const [k, v] of Object.entries(over)) {
    const below = out[k];
    out[k] = isMap(below) && isMap(v) ? resolveRules(below, v) : structuredClone(v);
  }
  return out;
}

/** Decimal text compared as text: "2000" and "2000.00" are the same amount. */
function decimalKey(v: string): string | null {
  const m = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(v.trim());
  if (!m) return null;
  const whole = m[2].replace(/^0+(?=\d)/, '');
  const frac = (m[3] ?? '').replace(/0+$/, '');
  return `${m[1] === '-' ? '-' : ''}${whole}${frac ? `.${frac}` : ''}`;
}

function same(a: Json | undefined, b: Json | undefined): boolean {
  if (typeof a === 'string' && typeof b === 'string') {
    const ka = decimalKey(a);
    const kb = decimalKey(b);
    if (ka !== null && kb !== null) return ka === kb;
  }
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Only the leaves of `effective` that differ from `base` (the preset), nested as in the YAML. */
export function presetDiff(base: Rules, effective: Rules): Rules {
  const out: Rules = {};
  for (const [k, v] of Object.entries(effective)) {
    const b = base[k];
    if (isMap(v) && isMap(b)) {
      const sub = presetDiff(b, v);
      if (Object.keys(sub).length) out[k] = sub;
    } else if (!same(b, v)) {
      out[k] = structuredClone(v);
    }
  }
  return out;
}

export function getLeaf(obj: Rules, path: string[]): Json | undefined {
  let cur: Json | undefined = obj;
  for (const k of path) cur = isMap(cur) ? cur[k] : undefined;
  return cur;
}

/** A copy of `obj` with the leaf at `path` set, or removed (and empty parents pruned) for undefined. */
export function setLeaf(obj: Rules, path: string[], value: Json | undefined): Rules {
  const [head, ...rest] = path;
  const out: Rules = { ...obj };
  if (rest.length === 0) {
    if (value === undefined) delete out[head];
    else out[head] = value;
    return out;
  }
  const child = setLeaf(isMap(out[head]) ? (out[head] as Rules) : {}, rest, value);
  if (Object.keys(child).length) out[head] = child;
  else delete out[head];
  return out;
}

/** The YAML mapping the form writes: `rules` and `nexus` only where they differ. */
export function accountEntry(form: AccountForm): Rules {
  const base = PRESET_RULES[form.preset];
  const rules = presetDiff(base, resolveRules(base, form.rules));
  const nexus: Rules = {};
  for (const k of Object.keys(NEXUS_DEFAULT) as (keyof NexusForm)[]) {
    if (form.nexus[k] !== NEXUS_DEFAULT[k]) nexus[k] = form.nexus[k];
  }
  return {
    id: form.id,
    preset: form.preset,
    ...(Object.keys(rules).length ? { rules } : {}),
    ...(Object.keys(nexus).length ? { nexus } : {}),
  };
}

/** The form for an existing `accounts[]` entry (unknown presets read as topstep_50k_combine). */
export function formFromEntry(entry: Record<string, any>): AccountForm {
  const preset: PresetName = PRESET_NAMES.includes(entry?.preset) ? entry.preset : 'topstep_50k_combine';
  return {
    id: typeof entry?.id === 'string' ? entry.id : '',
    preset,
    rules: isMap(entry?.rules) ? (entry.rules as Rules) : {},
    nexus: { ...NEXUS_DEFAULT, ...(isMap(entry?.nexus) ? entry.nexus : {}) } as NexusForm,
  };
}

function list(doc: Document, key: string): Record<string, any>[] {
  const data = (doc.toJSON() ?? {}) as Record<string, any>;
  return Array.isArray(data[key]) ? data[key] : [];
}

/**
 * Write `form` at `accounts[index]` (`null`: append). A changed id is carried to every
 * `plugins[i].account` that named the old one, so no reference dangles.
 */
export function writeAccount(doc: Document, index: number | null, form: AccountForm): void {
  const accounts = list(doc, 'accounts');
  const at = index ?? accounts.length;
  const oldId = index === null ? undefined : accounts[index]?.id;
  doc.setIn(['accounts', at], doc.createNode(accountEntry(form)));
  if (oldId && oldId !== form.id) {
    list(doc, 'plugins').forEach((p, i) => {
      if (p?.account === oldId) doc.setIn(['plugins', i, 'account'], form.id);
    });
  }
}

/**
 * Remove account `id` and every `plugins[i].account` naming it; returns those plugins'
 * ids. The account's state dir is not touched.
 */
export function removeAccount(doc: Document, id: string): string[] {
  const accounts = list(doc, 'accounts');
  const j = accounts.findIndex((a) => a?.id === id);
  if (j >= 0) {
    if (accounts.length === 1) doc.deleteIn(['accounts']);
    else doc.deleteIn(['accounts', j]);
  }
  const cleared: string[] = [];
  list(doc, 'plugins').forEach((p, i) => {
    if (p?.account === id) {
      doc.deleteIn(['plugins', i, 'account']);
      cleared.push(String(p.id));
    }
  });
  return cleared;
}

/** The plugin ids whose `account` names `id`. */
export function accountUsers(doc: Document, id: string): string[] {
  return list(doc, 'plugins').filter((p) => p?.account === id).map((p) => String(p.id));
}
