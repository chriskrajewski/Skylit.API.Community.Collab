// The API client: same-origin fetch with the session cookie, the CSRF header on
// mutations, and one error type whose message is safe to show.
import type { OrderModeRequest, OrderModeResult, OrderModes } from './types';

let csrfToken = '';

export function setCsrf(token: string): void {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `request failed (HTTP ${status})`);
    this.status = status;
    this.detail = detail;
  }
}

type Listener = () => void;
const expiredListeners = new Set<Listener>();
export function onSessionExpired(fn: Listener): () => void {
  expiredListeners.add(fn);
  return () => expiredListeners.delete(fn);
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  if (method !== 'GET') headers['X-FSE-CSRF'] = csrfToken;
  const res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' });
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401) expiredListeners.forEach((fn) => fn());
    const detail = data && typeof data === 'object' && 'detail' in data
      ? (data as { detail: unknown }).detail
      : data;
    const err = new ApiError(res.status, detail);
    (err as ApiError & { body: unknown }).body = data;
    throw err;
  }
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {}),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body ?? {}),
  upload: <T>(url: string, form: FormData) => request<T>('POST', url, form),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

export function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${path}`;
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const d = err.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      return d.map((x) => (x && typeof x === 'object' && 'msg' in x ? String(x.msg) : String(x))).join('; ');
    }
    if (d && typeof d === 'object' && 'message' in d && typeof (d as { message: unknown }).message === 'string') {
      return (d as { message: string }).message;
    }
    if (d && typeof d === 'object' && 'output' in d) {
      return ((d as { output: string[] }).output ?? []).join('\n') || err.message;
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}
export function getOrderModes(): Promise<OrderModes> {
  return api.get<OrderModes>('/api/order-modes');
}
export function postOrderMode(body: OrderModeRequest): Promise<OrderModeResult> {
  return api.post<OrderModeResult>('/api/order-mode', body);
}
/** True for the 409 the API answers when saving would restart a running runner. */
export function isConfirmRequired(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && !!err.detail && typeof err.detail === 'object'
    && (err.detail as { code?: unknown }).code === 'confirm_required';
}
