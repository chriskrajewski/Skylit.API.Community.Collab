import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Banner, Button, Input, Select, Switch } from '../ds/index.js';
import { HintSwitch } from './HintSwitch';
import { api, errorText } from '../lib/api';
import type { ArgSpec, CommandSpec, ConfigSummary, Job, RunSummary } from '../lib/types';

export type Values = Record<string, unknown>;

const RUN_DESTS = new Set(['run']);

function isList(a: ArgSpec): boolean {
  return a.kind === 'append' || a.nargs === '+' || a.nargs === '*' || typeof a.nargs === 'number';
}

/** Form text to the value the API expects (numbers, lists, booleans). */
export function cleanValues(spec: CommandSpec, raw: Values): Values {
  const out: Values = {};
  for (const a of spec.args) {
    const v = raw[a.dest];
    if (v === undefined || v === null || v === '') continue;
    if (a.kind === 'flag') {
      if (v === true) out[a.dest] = true;
      continue;
    }
    if (typeof v === 'object' && !Array.isArray(v)) {
      out[a.dest] = { upload: (v as { upload?: string }).upload }; // an uploaded file
      continue;
    }
    if (isList(a)) {
      const items = (Array.isArray(v) ? v.map(String) : String(v).split(','))
        .map((s) => s.trim()).filter(Boolean);
      if (items.length) out[a.dest] = items;
      continue;
    }
    if (a.type === 'int' || a.type === 'float') {
      const n = Number(v);
      out[a.dest] = Number.isFinite(n) && String(v).trim() !== '' ? n : v;
      continue;
    }
    out[a.dest] = v;
  }
  return out;
}

function label(a: ArgSpec): string {
  return a.flag ?? a.metavar ?? a.dest;
}

function hint(a: ArgSpec): string {
  const parts = [a.help];
  if (isList(a)) parts.push('several values: separate with commas');
  return parts.filter(Boolean).join(' · ');
}

function ArgField({ a, value, onChange, configs, runs, onUpload }: {
  a: ArgSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  configs: ConfigSummary[];
  runs: RunSummary[];
  onUpload?: (file: File) => void;
}) {
  const req = a.required ? ' *' : '';
  if (a.kind === 'flag') {
    return <HintSwitch label={label(a)} hint={a.help} checked={value === true}
      onChange={(e) => onChange(e.target.checked)} />;
  }
  if (a.choices) {
    const opts = [...(a.required ? [] : [{ value: '', label: 'default' }]),
      ...a.choices.map((c) => ({ value: c, label: c }))];
    return <Select label={label(a) + req} hint={a.help} options={opts}
      value={String(value ?? (a.required ? a.choices[0] : ''))}
      onChange={(e) => onChange(e.target.value)} />;
  }
  if (onUpload) {
    const up = value && typeof value === 'object' ? (value as { name?: string }).name : null;
    return (
      <Input label={label(a) + req} type="file" accept=".csv,text/csv"
        hint={up ? `uploaded: ${up}` : 'a .csv file, at most 50 MB'}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); }} />
    );
  }
  const ck = a.config_kind;
  if (a.type === 'path' && ck && configs.length) {
    const options = configs.filter((c) => c.kind === ck.kind && (!ck.prefix || c.name.startsWith(ck.prefix)))
      .map((c) => ({ value: c.path, label: `${ck.prefix ? c.name.slice(ck.prefix.length) : c.name} (${c.current})` }));
    const known = options.some((o) => o.value === value);
    return (
      <div className="stack-sm">
        <Select label={label(a) + req} hint={a.help}
          options={[{ value: '', label: '— pick a config —' }, ...options,
            { value: '__custom', label: 'another path…' }]}
          value={known ? String(value) : value ? '__custom' : ''}
          onChange={(e) => onChange(e.target.value === '__custom' ? ' ' : e.target.value)} />
        {!known && value !== undefined && value !== '' && (
          <Input size="sm" value={String(value).trim()} placeholder="/path/to/file.yaml"
            onChange={(e) => onChange(e.target.value)} />
        )}
      </div>
    );
  }
  if (RUN_DESTS.has(a.dest) && runs.length) {
    return <Select label={label(a) + req} hint={a.help}
      options={[{ value: '', label: '— pick a run —' }, ...runs.map((r) => ({ value: r.name, label: `${r.name} · ${r.kind}` }))]}
      value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />;
  }
  const type = a.type === 'int' || a.type === 'float' ? 'number' : a.type === 'date' ? 'date' : 'text';
  const placeholder = a.default !== null && a.default !== undefined && a.default !== false
    ? `default: ${Array.isArray(a.default) ? a.default.join(',') : String(a.default)}`
    : (a.metavar ?? undefined);
  return <Input label={label(a) + req} hint={hint(a)} type={type}
    step={a.type === 'float' ? 'any' : undefined} placeholder={placeholder}
    value={value === undefined || value === null ? '' : Array.isArray(value) ? value.join(', ') : String(value)}
    onChange={(e) => onChange(e.target.value)} />;
}

/**
 * A form for any CLI command, generated from its argparse spec. ``primary`` lists
 * the fields shown first; the rest sit under "All options". The API checks the
 * values with the command's own parser before anything runs.
 */
export function CommandForm({ spec, initial = {}, primary, uploads = [], submitLabel = 'Run',
  title, onSubmitted, extraValues = {} }: {
  spec: CommandSpec;
  initial?: Values;
  primary?: string[];
  uploads?: string[];
  submitLabel?: string;
  title?: string;
  onSubmitted?: (job: Job) => void;
  extraValues?: Values;
}) {
  const navigate = useNavigate();
  const [values, setValues] = useState<Values>(initial);
  const [all, setAll] = useState(!primary);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const configs = useQuery({ queryKey: ['configs'], queryFn: () => api.get<ConfigSummary[]>('/api/configs') });
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => api.get<RunSummary[]>('/api/runs') });
  const submit = useMutation({
    mutationFn: () => api.post<Job>('/api/jobs', {
      command: spec.id, values: { ...cleanValues(spec, values), ...extraValues }, title,
    }),
    onSuccess: (job) => (onSubmitted ? onSubmitted(job) : navigate(`/jobs/${job.id}`)),
  });

  const shown = useMemo(() => {
    const first = primary ?? [];
    const head = first.map((d) => spec.args.find((a) => a.dest === d)).filter(Boolean) as ArgSpec[];
    const rest = spec.args.filter((a) => !first.includes(a.dest) && !(a.dest in extraValues));
    const required = rest.filter((a) => a.required);
    return { head: [...head, ...required], rest: rest.filter((a) => !a.required) };
  }, [spec, primary, extraValues]);

  const set = (dest: string) => (v: unknown) => setValues((prev) => ({ ...prev, [dest]: v }));
  const upload = (dest: string) => async (file: File) => {
    setUploadError(null);
    const form = new FormData();
    form.append('file', file);
    try {
      const saved = await api.upload<{ id: string; name: string }>('/api/uploads', form);
      set(dest)({ upload: saved.id, name: saved.name });
    } catch (err) {
      setUploadError(errorText(err));
    }
  };
  const field = (a: ArgSpec) => (
    <ArgField key={a.dest} a={a} value={values[a.dest]} onChange={set(a.dest)}
      configs={configs.data ?? []} runs={runs.data ?? []}
      onUpload={uploads.includes(a.dest) ? upload(a.dest) : undefined} />
  );
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit.mutate();
  };

  if (spec.disabled_reason) {
    return <Banner tone="warning" title="Not available here">{spec.disabled_reason}</Banner>;
  }
  return (
    <form className="stack" onSubmit={onSubmit}>
      {spec.description && <div className="small muted mono-wrap">{spec.description.split('\n\n')[0]}</div>}
      <div className="form-grid">{shown.head.map(field)}</div>
      {shown.rest.length > 0 && (
        <Switch label={`All options (${shown.rest.length} more)`} checked={all}
          onChange={(e) => setAll(e.target.checked)} />
      )}
      {all && shown.rest.length > 0 && <div className="form-grid">{shown.rest.map(field)}</div>}
      {spec.groups.length > 0 && (
        <div className="small muted">
          {spec.groups.map((g) => `Use at most one of: ${g.dests.join(', ')}${g.required ? ' (one is required)' : ''}`).join(' · ')}
        </div>
      )}
      {uploadError && <Banner tone="warning" title="Upload refused">{uploadError}</Banner>}
      {submit.isError && <Banner tone="warning" title="Not started">{errorText(submit.error)}</Banner>}
      <div className="row">
        <Button type="submit" variant="primary" icon="play" disabled={submit.isPending}>{submitLabel}</Button>
        <span className="small muted">
          {spec.placement === 'batch' ? 'Runs as a batch job.' : 'Runs next to the console.'}
        </span>
      </div>
    </form>
  );
}
