import { Input, Select } from '../ds/index.js';
import { HintSwitch } from './HintSwitch';

type Schema = Record<string, any>;

function resolve(s: Schema, root: Schema): Schema {
  if (s?.$ref) {
    const name = String(s.$ref).split('/').pop()!;
    return resolve(root.$defs?.[name] ?? {}, root);
  }
  if (Array.isArray(s?.anyOf)) {
    const real = s.anyOf.filter((x: Schema) => x.type !== 'null');
    if (real.length === 1) return { ...resolve(real[0], root), nullable: true, default: s.default, description: s.description };
  }
  return s ?? {};
}

/**
 * Fields for the scalar properties of a pydantic JSON schema (strings, numbers,
 * booleans, enums). Nested objects and lists are left to the YAML view.
 */
export function SchemaFields({ schema, value, onChange }: {
  schema: Schema; value: Record<string, unknown>; onChange: (key: string, v: unknown) => void;
}) {
  const props: Record<string, Schema> = schema?.properties ?? {};
  const fields = Object.entries(props).map(([key, raw]) => {
    const s = resolve(raw, schema);
    const v = value[key];
    const hint = [s.description ?? raw.description, s.default !== undefined && s.default !== null ? `default ${JSON.stringify(s.default)}` : null]
      .filter(Boolean).join(' · ');
    if (s.type === 'boolean') {
      return <HintSwitch key={key} label={key} hint={hint} checked={(v ?? s.default) === true} onChange={(e) => onChange(key, e.target.checked)} />;
    }
    const choices: unknown[] | null = s.enum ?? (s.const !== undefined ? [s.const] : null);
    if (choices) {
      return <Select key={key} label={key} hint={hint}
        options={[{ value: '', label: 'default' }, ...choices.map((c) => ({ value: String(c), label: String(c) }))]}
        value={v === undefined || v === null ? '' : String(v)} onChange={(e) => onChange(key, e.target.value || undefined)} />;
    }
    if (s.type === 'integer' || s.type === 'number') {
      return <Input key={key} label={key} hint={hint} type="number" step={s.type === 'number' ? 'any' : 1}
        min={s.minimum ?? s.exclusiveMinimum} max={s.maximum}
        value={v === undefined || v === null ? '' : String(v)}
        onChange={(e) => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))} />;
    }
    if (s.type === 'string') {
      return <Input key={key} label={key} hint={hint} value={v === undefined || v === null ? '' : String(v)}
        placeholder={s.format === 'time' ? 'HH:MM' : undefined}
        onChange={(e) => onChange(key, e.target.value === '' ? undefined : e.target.value)} />;
    }
    return null;
  }).filter(Boolean);
  // Nested blocks (e.g. a plugin's rules or view): listed with their explanations; edit them in YAML.
  const nested = Object.entries(props).filter(([, raw]) => {
    const s = resolve(raw, schema);
    return s.type === 'object' || s.properties || s.type === 'array';
  }).map(([key, raw]) => {
    const s = resolve(raw, schema);
    const inner = Object.entries((s.properties ?? {}) as Record<string, Schema>)
      .map(([k, p]) => ({ k, d: resolve(p, schema).description ?? p.description, dflt: p.default }));
    return { key, desc: s.description ?? raw.description, inner };
  });
  if (!fields.length && !nested.length) return <div className="small muted">This plugin has no settings.</div>;
  return (
    <div className="stack-sm">
      {fields.length > 0 && <div className="form-grid">{fields}</div>}
      {nested.map((n) => (
        <div key={n.key} className="schema-nested">
          <div className="small"><code className="inline">{n.key}</code>{n.desc ? ` · ${n.desc}` : ''} <span className="muted">(edit in the YAML view)</span></div>
          {n.inner.length > 0 && (
            <ul className="schema-nested__list">
              {n.inner.map((f) => (
                <li key={f.k} className="small muted"><code className="inline">{f.k}</code>{f.d ? ` ${f.d}` : ''}
                  {f.dflt !== undefined && f.dflt !== null ? ` (default ${JSON.stringify(f.dflt)})` : ''}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
