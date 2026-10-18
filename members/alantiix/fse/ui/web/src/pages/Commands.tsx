import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Badge, Banner, DataTable, Input, Panel } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { CommandForm } from '../components/CommandForm';
import { commandHref } from '../components/CommandPalette';
import { useCatalog } from '../lib/hooks';
import type { CommandSpec } from '../lib/types';

const UPLOAD_ARGS: Record<string, string[]> = { 'fse import-vix': ['csv'] };

export function CommandsPage() {
  const { data, isLoading } = useCatalog();
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  const rows = useMemo(() => (data?.commands ?? []).filter(
    (c) => !q || `${c.id} ${c.help}`.toLowerCase().includes(q.toLowerCase())), [data, q]);
  return (
    <div>
      <PageHead title="All commands"
        sub="Every command of fse and the research CLIs, read from their own parsers. Pick one to fill its form." />
      <div className="page">
        {data && Object.keys(data.errors).length > 0 && (
          <Banner tone="warning" title="Some CLIs could not be read">
            {Object.entries(data.errors).map(([k, v]) => `${k}: ${v}`).join(' · ')}
          </Banner>
        )}
        <Input icon="search" placeholder="Filter commands" value={q} onChange={(e) => setQ(e.target.value)} />
        <Panel flush>
          {isLoading ? <Empty>Loading…</Empty> : (
            <DataTable dense rows={rows} rowKey="id" onRowClick={(c: CommandSpec) => navigate(commandHref(c.id))}
              columns={[
                { key: 'id', label: 'Command', width: 300 },
                { key: 'placement', label: 'Runs as', width: 100,
                  render: (v: string, c: CommandSpec) => c.disabled_reason
                    ? <Badge variant="warning-outline">disabled</Badge> : <Badge variant="outline">{v}</Badge> },
                { key: 'help', label: 'What it does', muted: true },
              ]} />
          )}
        </Panel>
      </div>
    </div>
  );
}

export function CommandPage() {
  const { id = '' } = useParams();
  const commandId = decodeURIComponent(id);
  const { data } = useCatalog();
  const spec = data?.commands.find((c) => c.id === commandId);
  return (
    <div>
      <PageHead title={commandId} sub={spec?.help} />
      <div className="page">
        {!data ? <Empty>Loading…</Empty> : !spec ? <Banner tone="warning">No command {commandId}.</Banner> : (
          <Panel title="Options" icon="sliders-horizontal">
            <CommandForm key={spec.id} spec={spec} uploads={UPLOAD_ARGS[spec.id]} title={spec.id} />
          </Panel>
        )}
      </div>
    </div>
  );
}
