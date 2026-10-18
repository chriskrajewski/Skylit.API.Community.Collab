import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppRoutes } from '../App';
import { TestSession } from '../lib/session';
import { ToastProvider } from '../components/Toasts';
import { etDateTime } from '../lib/format';
import {
  API_LOGS_EMPTY, API_LOGS_LOADING, API_LOGS_NO_BODY, API_LOGS_NO_HEADERS, API_LOGS_NO_MATCH,
  API_LOGS_TRUNCATED,
} from '../pages/ApiLogs';
import type { Session } from '../lib/types';

const SESSION: Session = {
  user: { sub: 'dev', email: 'operator@localhost' }, csrf: 'csrf-token', env: 'local',
  enable_live: false, dev_auth: true, secret_store: 'local', executor: 'local', runner_controller: 'process',
  plugin_orders: false, topstep_verified_on: null,
};

const WHEN = 1_760_632_500;

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <TestSession session={SESSION}><ToastProvider><AppRoutes /></ToastProvider></TestSession>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const LIVE = {
  run_dir: null, host_log: [], heartbeat: null, faults: [], plugins: [],
  blocks: { root: { halt_file: false, blocks: [] }, plugins: {}, halted: false },
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function other(url: string) {
  if (url.startsWith('/api/live')) return json(LIVE);
  return json([]);
}

describe('API logs', () => {
  it('shows the empty log', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/api-logs')) return json({ calls: [], total: 0, endpoints: [] });
      return other(url);
    });
    renderAt('/api-logs');
    expect(screen.getByRole('heading', { name: 'API logs' })).toBeInTheDocument();
    expect(await screen.findByText(API_LOGS_EMPTY)).toBeInTheDocument();
  });

  it('shows loading and an error without dropping the page', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).startsWith('/api/api-logs')) return new Promise(() => undefined);
      return Promise.resolve(other(String(input)));
    });
    const pending = renderAt('/api-logs');
    expect(screen.getByRole('heading', { name: 'API logs' })).toBeInTheDocument();
    expect(screen.getByText(API_LOGS_LOADING)).toBeInTheDocument();
    pending.unmount();
    vi.restoreAllMocks();

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).startsWith('/api/api-logs')) {
        return json({ detail: 'the API log could not be read' }, 500);
      }
      return other(String(input));
    });
    renderAt('/api-logs');
    expect(await screen.findByText('the API log could not be read')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'API logs' })).toBeInTheDocument();
  });

  it('renders a call and filters by API, status and endpoint', async () => {
    const urls: string[] = [];
    const row = {
      id: '1', t: WHEN, api: 'projectx', method: 'POST', path: '/api/Order/place',
      status: 200, latency_ms: 42, error: 'errorCode 2 — bracket rejected', caller: 'plugin_a', ok: false,
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      urls.push(url);
      if (!url.startsWith('/api/api-logs')) return other(url);
      if (url.includes('api=skylit')) {
        return json({ calls: [], total: 0, endpoints: ['/api/Order/place', '/v1/stream'] });
      }
      return json({
        calls: [row],
        total: 2,
        endpoints: ['/api/Order/place', '/v1/stream'],
      });
    });
    renderAt('/api-logs');
    expect(await screen.findByText(etDateTime(WHEN))).toBeInTheDocument();
    expect(screen.getAllByText('ProjectX').length).toBeGreaterThan(0);
    expect(screen.getByText('POST')).toBeInTheDocument();
    expect(screen.getAllByText('/api/Order/place').length).toBeGreaterThan(0);
    expect(screen.getByText('200')).toBeInTheDocument();
    expect(screen.getByText('42 ms')).toBeInTheDocument();
    expect(screen.getByText('errorCode 2 — bracket rejected')).toBeInTheDocument();
    expect(screen.getByText('plugin_a')).toBeInTheDocument();
    expect(screen.getByText('200').className).toContain('text-bear');

    await userEvent.click(screen.getByRole('button', { name: 'All calls' }));
    await waitFor(() => expect(urls.some((url) => url.includes('status=all'))).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: 'Skylit' }));
    expect(await screen.findByText(API_LOGS_NO_MATCH)).toBeInTheDocument();
    await waitFor(() => expect(urls.some((url) => url.includes('api=skylit'))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText('Endpoint'), '/v1/stream');
    await waitFor(() => expect(urls.some((url) => url.includes('endpoint=%2Fv1%2Fstream'))).toBe(true));
  });

  it('expands a row into the request and response', async () => {
    const secret = 'super-secret-token';
    const projectx = {
      id: 'px', t: WHEN, api: 'projectx', method: 'POST', path: '/api/Order/place',
      status: 200, latency_ms: 42, error: 'errorCode 2 — bracket rejected', caller: 'plugin_a', ok: false,
      url: 'https://api.projectx.invalid/api/Order/place',
      req_headers: { Authorization: `Bearer ${secret}`, Accept: 'application/json' },
      req_body: `{"customTag":"fse-1","token":"${secret}"}`,
      req_truncated: false,
      res_body: '{"success":false,"errorCode":2,"errorMessage":"bracket rejected"}',
      res_truncated: true,
      res_note: null,
    };
    const stream = {
      id: 'sk', t: WHEN + 1, api: 'skylit', method: 'GET', path: '/v1/stream',
      status: 200, latency_ms: 12, error: null, caller: 'map_feed', ok: false,
      url: 'https://api.skylit.ai/v1/stream?symbols=SPX',
      req_headers: {},
      req_body: null,
      req_truncated: false,
      res_body: null,
      res_truncated: false,
      res_note: 'Event stream. Frames are not stored.',
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (!url.startsWith('/api/api-logs')) return other(url);
      return json({ calls: [projectx, stream], total: 2, endpoints: [] });
    });
    renderAt('/api-logs');
    expect(await screen.findByText('errorCode 2 — bracket rejected')).toBeInTheDocument();
    expect(screen.queryByText(API_LOGS_NO_BODY)).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('errorCode 2 — bracket rejected'));
    expect(screen.getByText('Request')).toBeInTheDocument();
    expect(screen.getByText('Response')).toBeInTheDocument();
    expect(screen.getByText('https://api.projectx.invalid/api/Order/place')).toBeInTheDocument();
    expect(screen.getByText(/customTag/)).toBeInTheDocument();
    expect(screen.getByText(/"errorCode":2/)).toBeInTheDocument();
    expect(screen.getByText('application/json')).toBeInTheDocument();
    expect(screen.getByText(API_LOGS_TRUNCATED)).toBeInTheDocument();
    expect(screen.queryByText(secret)).not.toBeInTheDocument();
    expect(screen.queryByText(/Authorization/)).not.toBeInTheDocument();
    expect(screen.getByText(/\[REDACTED\]/)).toBeInTheDocument();

    await userEvent.click(screen.getByText('errorCode 2 — bracket rejected'));
    expect(screen.queryByText('Request')).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('/v1/stream'));
    expect(screen.getByText(API_LOGS_NO_BODY)).toBeInTheDocument();
    expect(screen.getByText(API_LOGS_NO_HEADERS)).toBeInTheDocument();
    expect(screen.getByText('Event stream. Frames are not stored.')).toBeInTheDocument();
  });
});
