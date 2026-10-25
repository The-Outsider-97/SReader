// The one place that talks to the service. Every change carries the request marker; errors keep the service's own message.
import type { ApiErrorBody } from './types.js';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.details = body.details ?? {};
  }
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export class Api {
  /** Reports whether the person's system prefers dark colours; sent with every request so "system" themes resolve correctly. */
  dark: () => boolean = () => false;
  constructor(private readonly doFetch: Fetch = (i, init) => fetch(i, init)) {}

  async request<T>(method: string, path: string, body?: unknown, extra: Record<string, string> = {}, keepalive = false): Promise<T> {
    const headers: Record<string, string> = { ...extra };
    const init: RequestInit = { method, headers, credentials: 'same-origin', keepalive };
    if (this.dark()) headers['X-Reader-Dark'] = '1';
    if (method !== 'GET') headers['X-SLAI-Reader'] = '1';
    if (body instanceof ArrayBuffer || body instanceof Uint8Array || (typeof Blob !== 'undefined' && body instanceof Blob)) {
      init.body = body as BodyInit;
    } else if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res: Response;
    try {
      res = await this.doFetch(path, init);
    } catch {
      throw new ApiError(0, { code: 'offline', message: 'Reader could not be reached. Is it still running?' });
    }
    const text = await res.text();
    let json: { ok?: boolean; data?: unknown; error?: ApiErrorBody } | null = null;
    try { json = text ? JSON.parse(text) : null; } catch { json = null; }
    if (!res.ok || !json || json.ok === false) {
      throw new ApiError(res.status, json?.error ?? { code: 'error', message: `The request failed (${res.status}).` });
    }
    return json.data as T;
  }

  get<T>(path: string): Promise<T> { return this.request<T>('GET', path); }
  post<T>(path: string, body: unknown = {}): Promise<T> { return this.request<T>('POST', path, body); }
  put<T>(path: string, body: unknown = {}, keepalive = false): Promise<T> { return this.request<T>('PUT', path, body, {}, keepalive); }
  patch<T>(path: string, body: unknown = {}): Promise<T> { return this.request<T>('PATCH', path, body); }
  del<T>(path: string): Promise<T> { return this.request<T>('DELETE', path); }
  upload<T>(name: string, data: ArrayBuffer, password?: string): Promise<T> {
    return this.request<T>('POST', `/api/upload?name=${encodeURIComponent(name)}`, data, password ? { 'X-Reader-Password': password } : {});
  }
}

export const qs = (params: Record<string, string | number | boolean | undefined | null>): string => {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join('&')}` : '';
};
