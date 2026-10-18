import { Fragment, useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge, Banner, CodeBlock, KeyValue, Panel, Segmented, Select } from '../ds/index.js';
import { PageHead, Empty } from '../components/PageHead';
import { api, errorText, qs } from '../lib/api';
import { DASH, etDateTime } from '../lib/format';

export const API_LOGS_EMPTY = 'No Skylit or ProjectX calls in the last 24 hours.';
export const API_LOGS_NO_MATCH = 'No calls match these filters.';
export const API_LOGS_LOADING = 'Loading…';
export const API_LOGS_NO_BODY = 'no body captured';
export const API_LOGS_NO_HEADERS = 'no headers captured';
export const API_LOGS_TRUNCATED = 'Truncated.';

type ApiFilter = 'all' | 'skylit' | 'projectx';
type StatusFilter = 'errors' | 'all';

interface ApiCall {
  id: string;
  t: number;
  api: 'skylit' | 'projectx';
  method: string;
  path: string;
  status: number | null;
  latency_ms: number | null;
  error: string | null;
  caller: string | null;
  ok: boolean;
  url?: string | null;
  req_headers?: Record<string, string> | null;
  req_body?: string | null;
  req_truncated?: boolean;
  res_body?: string | null;
  res_truncated?: boolean;
  res_note?: string | null;
}

interface ApiLogsResponse {
  calls: ApiCall[];
  total: number;
  endpoints: string[];
}

const SECRET_HEADER = /authorization|token|secret|password|api-key|apikey|cookie/i;

function isLogs(data: unknown): data is ApiLogsResponse {
  return !!data && !Array.isArray(data) && typeof data === 'object' && Array.isArray((data as ApiLogsResponse).calls);
}

function latency(ms: number | null): string {
  if (ms === null || ms === undefined) return DASH;
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

function apiLabel(name: string): string {
  return name === 'projectx' ? 'ProjectX' : 'Skylit';
}

/** Display-time redaction. The ring already strips these; a poisoned row must not show them. */
function redactDisplay(text: string): string {
  return text
    .replace(/\bBearer\s+\S{6,}/gi, 'Bearer [REDACTED]')
    .replace(
      /"(token|apiKey|api_key|authorization|password|userName|username|secret)"\s*:\s*"(?:\\.|[^"\\])*"/gi,
      '"$1":"[REDACTED]"',
    );
}

function visibleHeaders(headers: Record<string, string> | null | undefined): Array<[string, string]> {
  if (!headers) return [];
  return Object.entries(headers)
    .filter(([name]) => !SECRET_HEADER.test(name))
    .map(([name, value]) => [name, redactDisplay(value)]);
}

function Body({
  text,
  truncated,
  note,
}: {
  text: string | null | undefined;
  truncated?: boolean;
  note?: string | null;
}) {
  const shown = text ? redactDisplay(text) : '';
  return (
    <div className="stack-sm">
      {shown ? <CodeBlock code={shown} plain numbers={false} /> : null}
      {note ? <p className="small">{note}</p> : null}
      {!shown && !note ? <p className="small">{API_LOGS_NO_BODY}</p> : null}
      {truncated ? <p className="small">{API_LOGS_TRUNCATED}</p> : null}
    </div>
  );
}

function CallDetail({ row }: { row: ApiCall }) {
  const headers = visibleHeaders(row.req_headers);
  return (
    <div className="stack">
      <section className="stack-sm">
        <p className="micro">Request</p>
        <KeyValue items={[
          ['Method', row.method],
          ['URL', row.url || row.path],
        ]} />
        {headers.length
          ? <KeyValue items={headers} />
          : <p className="small">{API_LOGS_NO_HEADERS}</p>}
        <Body text={row.req_body} truncated={row.req_truncated} />
      </section>
      <section className="stack-sm">
        <p className="micro">Response</p>
        <KeyValue items={[['Status', row.status ?? DASH]]} />
        <Body text={row.res_body} truncated={row.res_truncated} note={row.res_note} />
      </section>
    </div>
  );
}

function onRowKey(event: KeyboardEvent<HTMLTableRowElement>, toggle: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    toggle();
  }
}

/** Live and historical Skylit and ProjectX calls. The list polls; the file keeps the last 500. */
export function ApiLogsPage() {
  const [apiFilter, setApiFilter] = useState<ApiFilter>('all');
  const [status, setStatus] = useState<StatusFilter>('errors');
  const [endpoint, setEndpoint] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['api-logs', apiFilter, status, endpoint],
    queryFn: () => api.get<ApiLogsResponse>(`/api/api-logs${qs({
      api: apiFilter, status, endpoint: endpoint || undefined,
    })}`),
    refetchInterval: 3000,
  });
  const body = isLogs(q.data) ? q.data : null;
  const calls = body?.calls ?? [];
  const total = body?.total ?? 0;
  const endpoints = body?.endpoints ?? [];
  const endpointOptions = [
    { value: '', label: 'All endpoints' },
    ...endpoints.map((path) => ({ value: path, label: path })),
    ...(endpoint && !endpoints.includes(endpoint) ? [{ value: endpoint, label: endpoint }] : []),
  ];

  function toggle(id: string) {
    setOpenId((current) => (current === id ? null : id));
  }

  let content;
  if (q.isPending) content = <Empty>{API_LOGS_LOADING}</Empty>;
  else if (q.isError) {
    content = <Banner tone="warning" title="API logs unavailable">{errorText(q.error)}</Banner>;
  } else if (calls.length === 0) {
    const nothingLogged = total === 0 && apiFilter === 'all' && !endpoint;
    content = <Empty>{nothingLogged ? API_LOGS_EMPTY : API_LOGS_NO_MATCH}</Empty>;
  } else {
    content = (
      <div className="fse-table-wrap">
        <table className="fse-table fse-table--dense fse-table--hover">
          <thead>
            <tr>
              <th>Time</th>
              <th>API</th>
              <th>Method</th>
              <th>Path</th>
              <th>Status</th>
              <th>Latency</th>
              <th>Error</th>
              <th>Caller</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((row) => {
              const open = openId === row.id;
              return (
                <Fragment key={row.id}>
                  <tr
                    className={open ? 'is-selected' : undefined}
                    aria-expanded={open}
                    tabIndex={0}
                    onClick={() => toggle(row.id)}
                    onKeyDown={(event) => onRowKey(event, () => toggle(row.id))}
                  >
                    <td className="is-muted">{etDateTime(row.t)}</td>
                    <td>
                      <Badge variant={row.api === 'projectx' ? 'accent-soft' : 'outline'}>
                        {apiLabel(row.api)}
                      </Badge>
                    </td>
                    <td className="is-muted">{row.method}</td>
                    <td>{row.path}</td>
                    <td className={row.ok ? '' : 'text-bear'}>{row.status ?? DASH}</td>
                    <td className="is-muted">{latency(row.latency_ms)}</td>
                    <td className="small" title={row.error ?? ''}>{row.error || DASH}</td>
                    <td className="is-muted">{row.caller || DASH}</td>
                  </tr>
                  {open ? (
                    <tr className="api-log-detail">
                      <td colSpan={8}>
                        <CallDetail row={row} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div>
      <PageHead title="API logs" sub="Skylit and ProjectX calls from the runner, including errors. The last 500 from the past 24 hours stay after a refresh." />
      <div className="page">
        <Panel flush title="Calls" actions={
          <div className="row">
            <Segmented
              options={[
                { value: 'all', label: 'All APIs' },
                { value: 'skylit', label: 'Skylit' },
                { value: 'projectx', label: 'ProjectX' },
              ]}
              value={apiFilter}
              onChange={(v: string) => setApiFilter(v === 'skylit' || v === 'projectx' ? v : 'all')}
            />
            <Segmented
              options={[{ value: 'errors', label: 'Errors' }, { value: 'all', label: 'All calls' }]}
              value={status}
              onChange={(v: string) => setStatus(v === 'all' ? 'all' : 'errors')}
            />
            <Select aria-label="Endpoint" size="sm" options={endpointOptions} value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)} />
          </div>
        }>
          {content}
        </Panel>
      </div>
    </div>
  );
}
