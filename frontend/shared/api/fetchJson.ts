// Phase F2 — 공용 fetch 래퍼.

export interface FetchJsonOptions extends RequestInit {
  signal?: AbortSignal;
}

export async function fetchJson<T = unknown>(url: string, opts: FetchJsonOptions = {}): Promise<T> {
  const init: RequestInit = {
    method: 'GET',
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...(opts.headers ?? {}) },
    ...opts,
  };
  const r = await fetch(url, init);
  if (!r.ok) {
    throw new Error(`HTTP ${r.status} ${r.statusText} — ${url}`);
  }
  const text = await r.text();
  if (!text) return null as unknown as T;
  return JSON.parse(text) as T;
}
