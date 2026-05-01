// Phase F5 — Public Screener model (순수 함수).
import type { PublicScreenerItem, PublicScreenerResponse } from '@shared/contracts';

export type MarketFilter = 'all' | 'domestic' | 'foreign';

export const SENSITIVE_KEYS = [
  'account_no', 'account-no', 'token', 'appkey', 'secret',
  'password', 'authorization', 'bearer',
] as const;

export const STALE_THRESHOLD_MS = 10 * 60 * 1000;
export const SCORE_HIGHLIGHT = 80;
export const REASONS_DISPLAY_MAX = 3;

export interface NormalizedItem {
  ticker: string;
  name: string;
  price: number | null;
  score: number | null;
  reasons: string[];
  isDomestic: boolean;
  screenedAt: string | null;
}

export interface NormalizedScreener {
  items: NormalizedItem[];
  updatedAt: string | null;
  isStale: boolean;
  count: number;
}

const REDACTED_MARK = '[redacted]';

function isSensitive(s: string): boolean {
  const low = s.toLowerCase();
  return SENSITIVE_KEYS.some((k) => low.includes(k));
}

function maskText(s: unknown, maxLen = 80): string {
  const t = String(s ?? '');
  if (isSensitive(t)) return REDACTED_MARK;
  return t.length > maxLen ? t.slice(0, maxLen) : t;
}

function asFiniteNumber(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function asTickerString(v: unknown): string {
  return String(v ?? '').trim().toUpperCase();
}

function deriveDomestic(item: PublicScreenerItem): boolean {
  if (typeof item.is_domestic === 'boolean') return item.is_domestic;
  const t = asTickerString(item.ticker);
  return t.endsWith('.KS') || t.endsWith('.KQ');
}

function pickReasons(item: PublicScreenerItem): string[] {
  if (Array.isArray(item.reasons)) {
    return item.reasons
      .filter((r): r is string => typeof r === 'string' && r.length > 0)
      .slice(0, REASONS_DISPLAY_MAX)
      .map((r) => maskText(r, 40));
  }
  if (typeof item.reason === 'string' && item.reason.length > 0) {
    return [maskText(item.reason, 40)];
  }
  return [];
}

export function normalizeItem(item: PublicScreenerItem | null | undefined): NormalizedItem | null {
  if (!item) return null;
  const ticker = asTickerString(item.ticker);
  if (!ticker) return null;
  return {
    ticker,
    name: maskText(item.name ?? '', 60),
    price: asFiniteNumber(item.price ?? item.current_price),
    score: asFiniteNumber(item.score),
    reasons: pickReasons(item),
    isDomestic: deriveDomestic(item),
    screenedAt: typeof item.screened_at === 'string' ? item.screened_at : null,
  };
}

export function sortByScoreDesc(items: NormalizedItem[]): NormalizedItem[] {
  return [...items].sort((a, b) => {
    const sa = a.score ?? -Infinity;
    const sb = b.score ?? -Infinity;
    if (sa !== sb) return sb - sa;
    return a.ticker < b.ticker ? -1 : a.ticker > b.ticker ? 1 : 0;
  });
}

export function isStale(updatedAt: string | null, now: number): boolean {
  if (!updatedAt) return true;
  const t = Date.parse(updatedAt);
  if (!Number.isFinite(t)) return true;
  return now - t > STALE_THRESHOLD_MS;
}

export function applyMarketFilter(
  items: NormalizedItem[],
  market: MarketFilter,
  searchTicker?: string,
): NormalizedItem[] {
  const q = (searchTicker ?? '').trim().toLowerCase();
  return items.filter((it) => {
    if (market === 'domestic' && !it.isDomestic) return false;
    if (market === 'foreign' && it.isDomestic) return false;
    if (q) {
      const haystack = `${it.ticker} ${it.name}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

export function normalizeScreener(
  resp: PublicScreenerResponse | null,
  now: number,
): NormalizedScreener {
  if (!resp) return { items: [], updatedAt: null, isStale: true, count: 0 };
  const rawList = (Array.isArray(resp.items) ? resp.items : Array.isArray(resp.screener) ? resp.screener : []) ?? [];
  const items = sortByScoreDesc(rawList.map(normalizeItem).filter((x): x is NormalizedItem => x !== null));
  const updatedAt = typeof resp.updated_at === 'string' ? resp.updated_at : null;
  return { items, updatedAt, isStale: isStale(updatedAt, now), count: items.length };
}
