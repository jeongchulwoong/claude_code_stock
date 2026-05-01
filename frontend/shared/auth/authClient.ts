// Phase F6 — Auth API client.
//
// `/api/auth/*` 와 통신. fetchJson 의존성 없음 (Bearer 헤더는 스토리지 직접 읽음).

import { clearAuth, loadAuth, saveAuth, type StoredAuth } from './tokenStorage';

export interface LoginResponse {
  ok: boolean;
  access_token: string;
  token_type: 'Bearer';
  role: 'admin' | 'client';
  expires_at: string;
  issued_at: string;
  ttl_sec: number;
}

export class AuthError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'AuthError';
  }
}

export async function login(password: string): Promise<StoredAuth> {
  const r = await fetch('/api/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j?.error) msg = String(j.error);
    } catch { /* keep default */ }
    throw new AuthError(r.status, msg);
  }
  const data = (await r.json()) as LoginResponse;
  if (data.role !== 'admin' && data.role !== 'client') {
    throw new AuthError(500, '응답 role 이상');
  }
  const stored: StoredAuth = {
    accessToken: data.access_token,
    role:        data.role,
    issuedAt:    data.issued_at,
    expiresAt:   data.expires_at,
    ttlSec:      data.ttl_sec,
  };
  saveAuth(stored);
  return stored;
}

export async function logout(): Promise<void> {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
  } catch {
    /* swallow — clear client side anyway. */
  }
  clearAuth();
}

export interface MeResponse {
  ok: boolean;
  authenticated: boolean;
  role?: 'admin' | 'client';
  error?: string;
}

export async function me(): Promise<MeResponse> {
  const auth = loadAuth();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (auth?.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`;
  const r = await fetch('/api/auth/me', { credentials: 'same-origin', headers });
  if (r.status === 401) {
    return { ok: false, authenticated: false };
  }
  return (await r.json()) as MeResponse;
}
