import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { parseDocument, isMap, isScalar, type Document } from 'yaml';
import { Badge, Banner, Button, DataTable, Dialog, Input, Panel, Select, Switch, Tabs } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { YamlEditor } from '../components/YamlEditor';
import { Problems } from '../components/Problems';
import { useToast } from '../components/Toasts';
import { ApiError, api, errorText } from '../lib/api';
import { etDateTime } from '../lib/format';
import type { ConfigDoc, ConfigKind, ConfigSummary, Validation } from '../lib/types';

const KIND_LABEL: Record<ConfigKind, string> = {
  runner: 'Runner (fse run)', strategy: 'Strategy_Config', sweep: 'Sweep definitions', research: 'Research packages',
};
const TOGGLE_SECTIONS = ['patterns', 'gates', 'kill_switches'];

/** Toggles every ``<section>.<key>.enabled`` in the YAML document, keeping comments. */
function toggles(doc: Document): { section: string; key: string; enabled: boolean }[] {
  const out: { section: string; key: string; enabled: boolean }[] = [];
  for (const section of TOGGLE_SECTIONS) {
    const node = doc.get(section, true);
    if (!isMap(node)) continue;
    for (const item of node.items) {
      const key = isScalar(item.key) ? String(item.key.value) : String(item.key);
      if (isMap(item.value)) {
        const en = item.value.get('enabled');
        if (typeof en === 'boolean') out.push({ section, key, enabled: en });
      }
    }
  }
  return out;
}

function StrategyForm({ text, onChange }: { text: string; onChange: (t: string) => void }) {
  const doc = useMemo(() => parseDocument(text), [text]);
  if (doc.errors.length) return <Banner tone="warning">The YAML does not parse; fix it in the editor first.</Banner>;
  const items = toggles(doc);
  const top = ['config_id', 'order_mode'].map((k) => [k, doc.get(k)] as const);
  const set = (path: (string | number)[], v: unknown) => {
    doc.setIn(path, v);
    onChange(doc.toString());
  };
  return (
    <div className="stack">
      <div className="form-grid">
        <Input label="config_id" value={String(top[0][1] ?? '')} onChange={(e) => set(['config_id'], e.target.value)} />
        <Select label="order_mode" hint="practice and combine are not run from the console"
          options={['paper', 'practice', 'combine']} value={String(top[1][1] ?? 'paper')}
          onChange={(e) => set(['order_mode'], e.target.value)} />
      </div>
      {TOGGLE_SECTIONS.map((section) => {
        const rows = items.filter((i) => i.section === section);
        if (!rows.length) return null;
        return (
          <Panel key={section} title={section} subtitle={`${rows.filter((r) => r.enabled).length} of ${rows.length} enabled`}>
            <div className="form-grid">{rows.map((r) => (
              <Switch key={r.key} label={r.key} checked={r.enabled}
                onChange={(e) => set([section, r.key, 'enabled'], e.target.checked)} />
            ))}</div>
          </Panel>
        );
      })}
      <div className="small muted">Every other key is edited in the YAML view; both views edit the same text.</div>
    </div>
  );
}

function DiffView({ text }: { text: string }) {
  return (
    <pre className="diff">{text ? text.split('\n').map((l, i) => (
      <div key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'diff__add'
        : l.startsWith('-') && !l.startsWith('---') ? 'diff__del' : l.startsWith('@@') ? 'diff__hunk' : ''}>{l || ' '}</div>
    )) : <span className="muted">No difference.</span>}</pre>
  );
}

function Editor({ kind, name }: { kind: ConfigKind; name: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const doc = useQuery({ queryKey: ['config', kind, name], queryFn: () => api.get<ConfigDoc>(`/api/configs/${kind}/${name}`) });
  const [text, setText] = useState('');
  const [message, setMessage] = useState('');
  const [view, setView] = useState(kind === 'strategy' ? 'form' : 'yaml');
  const [validation, setValidation] = useState<Validation | null>(null);
  const [goto, setGoto] = useState<number | null>(null);
  const [diff, setDiff] = useState<{ a: string; b: string } | null>(null);
  useEffect(() => { if (doc.data) setText(doc.data.text); setValidation(null); }, [doc.data]);
  const dirty = !!doc.data && text !== doc.data.text;

  const check = useMutation({
    mutationFn: () => api.post<Validation>('/api/configs/validate', { kind, text }),
    onSuccess: setValidation,
  });
  const save = useMutation({
    mutationFn: () => api.post<{ validation: Validation; config: ConfigDoc }>(`/api/configs/${kind}/${name}`, { text, message }),
    onSuccess: (r) => {
      setValidation(r.validation);
      setMessage('');
      qc.setQueryData(['config', kind, name], r.config);
      void qc.invalidateQueries({ queryKey: ['configs'] });
      toast('ok', `Saved ${name}`, `revision ${r.config.sha}`);
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 422) setValidation((err as ApiError & { body: any }).body.validation);
    },
  });
  const restore = useMutation({
    mutationFn: (sha: string) => api.post<ConfigDoc>(`/api/configs/${kind}/${name}/restore`, { sha }),
    onSuccess: (d) => { qc.setQueryData(['config', kind, name], d); toast('ok', `Restored ${name}`, `revision ${d.sha}`); },
  });
  const diffText = useQuery({
    queryKey: ['diff', kind, name, diff?.a, diff?.b],
    queryFn: () => api.get<{ diff: string }>(`/api/configs/${kind}/${name}/diff?a=${diff!.a}&b=${diff!.b}`),
    enabled: !!diff,
  });

  if (!doc.data) return <Empty>Loading…</Empty>;
  const d = doc.data;
  return (
    <div className="stack">
      <Panel title={name} subtitle={`${KIND_LABEL[kind]} · revision ${d.current}${d.origin ? ` · from ${d.origin}` : ''}`} icon="file-code"
        actions={<>
          {dirty && <Badge variant="warning">unsaved</Badge>}
          <Button size="sm" icon="list-checks" onClick={() => check.mutate()} disabled={check.isPending}>Validate</Button>
          <Button size="sm" variant="ghost" icon="undo-2" disabled={!dirty} onClick={() => { setText(d.text); setValidation(null); }}>Revert</Button>
        </>}
        footer={<div className="row">
          <div className="grow"><Input size="sm" placeholder="What changed (optional)" value={message} maxLength={200}
            onChange={(e) => setMessage(e.target.value)} /></div>
          <Button variant="primary" icon="save" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save revision</Button>
        </div>}>
        <div className="stack">
          {kind === 'strategy' && <Tabs items={[{ value: 'form', label: 'Form' }, { value: 'yaml', label: 'YAML' }]}
            value={view} onChange={setView} />}
          {view === 'form' && kind === 'strategy'
            ? <StrategyForm text={text} onChange={setText} />
            : <YamlEditor value={text} onChange={setText} gotoLine={goto} />}
          <Problems validation={validation} onPick={(p) => { setView('yaml'); setGoto(p.line); }} />
          {save.isError && !(save.error instanceof ApiError && save.error.status === 422) &&
            <Banner tone="warning">{errorText(save.error)}</Banner>}
          <div className="small muted">Commands read <code className="inline">{d.path}</code>.</div>
        </div>
      </Panel>
      <Panel title="Revisions" icon="history" flush>
        <DataTable dense rows={d.revisions.map((r, i) => ({ ...r, _k: `${r.sha}-${i}` }))} rowKey="_k" columns={[
          { key: 'sha', label: 'Revision', width: 120, render: (v: string) => v === d.current ? <><code className="inline">{v}</code> <Badge variant="accent-soft">current</Badge></> : <code className="inline">{v}</code> },
          { key: 'at', label: 'Saved', width: 190, muted: true, render: (v: number) => etDateTime(v) },
          { key: 'author', label: 'By', width: 180, muted: true },
          { key: 'message', label: 'Message' },
          { key: 'x', label: '', align: 'right', width: 200, render: (_: unknown, r: any) => r.sha === d.current ? null : <span className="row row--end">
            <Button size="sm" variant="ghost" icon="git-compare" onClick={() => setDiff({ a: r.sha, b: d.current })}>Diff</Button>
            <Button size="sm" variant="ghost" icon="rotate-ccw" onClick={() => restore.mutate(r.sha)}>Restore</Button></span> },
        ]} />
      </Panel>
      <Dialog open={!!diff} title={diff ? `${diff.a} → ${diff.b}` : ''} onClose={() => setDiff(null)} width={900}>
        {diffText.data ? <DiffView text={diffText.data.diff} /> : <Empty>Loading…</Empty>}
      </Dialog>
    </div>
  );
}

function NewConfig({ kinds, onDone }: { kinds: ConfigSummary[]; onDone: (k: ConfigKind, n: string) => void }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ConfigKind>('strategy');
  const [name, setName] = useState('');
  const [from, setFrom] = useState('');
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: async () => {
      const base = from ? (await api.get<ConfigDoc>(`/api/configs/${kind}/${from}`)).text : '';
      return api.post(`/api/configs/${kind}/${name}`, { text: base, create: true, message: from ? `copy of ${from}` : 'new' });
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['configs'] }); setOpen(false); onDone(kind, name); },
  });
  const sources = kinds.filter((c) => c.kind === kind);
  return (
    <>
      <Button size="sm" icon="plus" onClick={() => setOpen(true)}>New</Button>
      <Dialog open={open} title="New config" onClose={() => setOpen(false)} footer={<>
        <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        <Button variant="primary" disabled={!name || create.isPending} onClick={() => create.mutate()}>Create</Button></>}>
        <div className="stack">
          <Select label="Kind" options={Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))} value={kind}
            onChange={(e) => { setKind(e.target.value as ConfigKind); setFrom(''); }} />
          <Input label="Name" hint="letters, digits, _ . -" value={name} onChange={(e) => setName(e.target.value)} />
          <Select label="Copy from" options={[{ value: '', label: '— empty —' }, ...sources.map((s) => ({ value: s.name, label: s.name }))]}
            value={from} onChange={(e) => setFrom(e.target.value)} />
          {create.isError && <Banner tone="warning" title="Not created">{errorText(create.error)}
            {create.error instanceof ApiError && (create.error as any).body?.validation &&
              <Problems validation={(create.error as any).body.validation} />}</Banner>}
        </div>
      </Dialog>
    </>
  );
}

export function ConfigsPage() {
  const { kind, name } = useParams();
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ['configs'], queryFn: () => api.get<ConfigSummary[]>('/api/configs') });
  const groups = (Object.keys(KIND_LABEL) as ConfigKind[]).map((k) => ({ k, items: (list.data ?? []).filter((c) => c.kind === k) }));
  return (
    <div>
      <PageHead title="Configs" sub="Every YAML the engine reads. Saves are validated by the engine and kept as revisions." />
      <div className="page">
        <div className="split">
          <Panel flush title="Files" actions={<NewConfig kinds={list.data ?? []} onDone={(k, n) => navigate(`/configs/${k}/${n}`)} />}>
            <div className="filelist">{groups.map((g) => (
              <div key={g.k}>
                <div className="filelist__group micro">{KIND_LABEL[g.k]}</div>
                {g.items.map((c) => (
                  <button key={c.name} type="button" aria-current={kind === c.kind && name === c.name}
                    onClick={() => navigate(`/configs/${c.kind}/${c.name}`)}>
                    <span className="grow">{c.name}</span><span className="muted">{c.revisions}</span>
                  </button>
                ))}
                {g.items.length === 0 && <div className="filelist__group small muted">none</div>}
              </div>
            ))}</div>
          </Panel>
          {kind && name ? <Editor key={`${kind}/${name}`} kind={kind as ConfigKind} name={name} /> : <Empty>Pick a file.</Empty>}
        </div>
      </div>
    </div>
  );
}
