// Phase F2/F6 — 공용 fetch 래퍼 (JWT Bearer 자동 부착).

import { loadAuth } from '../auth/tokenStorage';

export interface FetchJsonOptions extends RequestInit {
  signal?: AbortSignal;
  /** false 로 설정하면 Bearer 자동 부착 안 함 (public endpoint 등). */
  withAuth?: boolean;
}

export async function fetchJson<T = unknown>(url: string, opts: FetchJsonOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(opts.headers as Record<string, string> ?? {}),
  };
  // 기본값: withAuth !== false 면 토큰 부착.
  if (opts.withAuth !== false) {
    const auth = loadAuth();
    if (auth?.accessToken && !headers.Authorization) {
      headers.Authorization = `Bearer ${auth.accessToken}`;
    }
  }
  const init: RequestInit = {
    method: 'GET',
    credentials: 'same-origin',
    ...opts,
    headers,
  };
  const r = await fetch(url, init);
  if (!r.ok) {
    throw new Error(`HTTP ${r.status} ${r.statusText} — ${url}`);
  }
  const text = await r.text();
  if (!text) return null as unknown as T;
  return JSON.parse(text) as T;
}
