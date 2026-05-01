// Phase F6 — JWT token storage + decode helpers (browser only).
//
// 저장 정책:
//   - localStorage 단일 키 'qd:auth:v1'.
//   - localStorage 가 사용 불가 (Safari private mode 등) 면 in-memory fallback.
//   - 절대 로그에 토큰 출력 안 함.
//   - 만료 5분 전부터는 stale 로 간주 (호출자가 갱신 결정).

export const TOKEN_KEY = 'qd:auth:v1';
const STALE_MARGIN_MS = 5 * 60 * 1000;

export interface StoredAuth {
  accessToken: string;
  role: 'admin' | 'client';
  issuedAt: string;
  expiresAt: string;
  ttlSec: number;
}

let _memCache: StoredAuth | null = null;

function _safeStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null;
    const s = window.localStorage;
    const k = '__qd_test__';
    s.setItem(k, '1');
    s.removeItem(k);
    return s;
  } catch {
    return null;
  }
}

export function loadAuth(): StoredAuth | null {
  if (_memCache) return _memCache;
  const s = _safeStorage();
  if (!s) return null;
  try {
    const raw = s.getItem(TOKEN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredAuth>;
    if (!parsed?.accessToken || (parsed.role !== 'admin' && parsed.role !== 'client')) {
      return null;
    }
    return parsed as StoredAuth;
  } catch {
    return null;
  }
}

export function saveAuth(auth: StoredAuth): void {
  _memCache = auth;
  const s = _safeStorage();
  if (!s) return;
  try {
    s.setItem(TOKEN_KEY, JSON.stringify(auth));
  } catch {
    /* quota / private mode — in-memory only. */
  }
}

export function clearAuth(): void {
  _memCache = null;
  const s = _safeStorage();
  if (!s) return;
  try {
    s.removeItem(TOKEN_KEY);
  } catch {
    /* noop. */
  }
}

export function isExpired(auth: StoredAuth | null, now: number = Date.now()): boolean {
  if (!auth) return true;
  const exp = Date.parse(auth.expiresAt);
  if (!Number.isFinite(exp)) return true;
  return now >= exp;
}

export function isStale(auth: StoredAuth | null, now: number = Date.now()): boolean {
  if (!auth) return true;
  const exp = Date.parse(auth.expiresAt);
  if (!Number.isFinite(exp)) return true;
  return now >= exp - STALE_MARGIN_MS;
}
