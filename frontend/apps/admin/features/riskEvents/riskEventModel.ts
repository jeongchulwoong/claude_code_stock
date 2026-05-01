// Phase F4 — Risk Events 정규화 / 압축 / redaction (순수 함수).

import type { OrdersResponse, RiskEventsResponse } from '@shared/contracts';

export const COMPRESS_THRESHOLD = 3;
export const MAX_RENDERED_ROWS  = 30;

export const SENSITIVE_KEYS = [
  'account_no', 'account-no', 'token', 'appkey', 'secret',
  'password', 'authorization', 'bearer',
] as const;

const REDACTED_MARK = '[redacted sensitive message]';
const MESSAGE_MAX_LEN = 80;
const META_REASON_MAX_LEN = 32;

export type RiskLevel = 'info' | 'warn' | 'error' | 'critical';

export interface NormalizedRiskEvent {
  id?: number;
  source: 'risk_events' | 'orders';
  time: string;
  ts: number;
  level: RiskLevel;
  category: string;
  ticker: string;
  message: string;
  metaReason: string;
}

export interface CompressedGroup {
  key: string;
  first: NormalizedRiskEvent;
  count: number;
  expanded: boolean;
  display: 'rollup' | 'flat';
  items: NormalizedRiskEvent[];
}

function hasForbiddenSubstring(s: unknown): boolean {
  if (s == null) return false;
  let low: string;
  try { low = String(s).toLowerCase(); } catch { return false; }
  return SENSITIVE_KEYS.some((bad) => low.includes(bad));
}

export function maskRedactedText(text: string, maxLen: number = MESSAGE_MAX_LEN): string {
  const s = String(text ?? '');
  if (hasForbiddenSubstring(s)) return REDACTED_MARK;
  if (s.length > maxLen) return s.slice(0, maxLen);
  return s;
}

function pickMetaReason(meta: Record<string, unknown> | null | undefined): string {
  if (!meta || typeof meta !== 'object') return '';
  const m = meta as Record<string, unknown>;
  for (const k of ['reason', 'strategy', 'detail']) {
    const v = m[k];
    if (typeof v === 'string' && v) {
      return maskRedactedText(v, META_REASON_MAX_LEN);
    }
  }
  return '';
}

function parseLevel(raw: unknown): RiskLevel {
  const s = String(raw ?? 'info').toLowerCase();
  if (s === 'critical') return 'critical';
  if (s === 'error')    return 'error';
  if (s === 'warn' || s === 'warning') return 'warn';
  return 'info';
}

function parseTs(time: unknown): number {
  if (!time) return 0;
  const n = Date.parse(String(time));
  return Number.isFinite(n) ? n : 0;
}

export function normalizeFromRiskEvents(resp: RiskEventsResponse | null): NormalizedRiskEvent[] {
  if (!resp || !Array.isArray(resp.events)) return [];
  const out: NormalizedRiskEvent[] = [];
  for (const e of resp.events) {
    if (!e) continue;
    const time = String(e.ts ?? '');
    const ticker = String(e.ticker ?? '').trim() || '-';
    const meta = (e.meta && typeof e.meta === 'object') ? (e.meta as Record<string, unknown>) : null;
    out.push({
      id: typeof e.id === 'number' ? e.id : undefined,
      source: 'risk_events',
      time,
      ts: parseTs(time),
      level: parseLevel(e.level),
      category: String(e.category ?? '').toLowerCase(),
      ticker,
      message: maskRedactedText(String(e.message ?? '')),
      metaReason: pickMetaReason(meta),
    });
  }
  return out;
}

export function normalizeFromOrders(resp: OrdersResponse | null): NormalizedRiskEvent[] {
  if (!resp) return [];
  const list = (Array.isArray(resp.orders) ? resp.orders : Array.isArray(resp.items) ? resp.items : []) ?? [];
  const out: NormalizedRiskEvent[] = [];
  for (const o of list) {
    if (!o) continue;
    const status = String(o.status ?? '').toUpperCase();
    let category: string | null = null;
    let level: RiskLevel = 'warn';
    if (status === 'ERROR')              { category = 'order_error'; level = 'error'; }
    else if (status === 'CANCEL_FAILED') { category = 'cancel_failed'; level = 'error'; }
    else if (status === 'BLOCKED')       { category = 'buy_blocked'; level = 'warn'; }
    else continue;
    const time = String(o.timestamp ?? '');
    const reason = String(o.reject_msg ?? o.reason ?? '');
    const ticker = String(o.ticker ?? '').trim() || '-';
    out.push({
      source: 'orders',
      time,
      ts: parseTs(time),
      level,
      category,
      ticker,
      message: maskRedactedText(reason),
      metaReason: '',
    });
  }
  return out;
}

export function groupKey(e: NormalizedRiskEvent): string {
  const reason = (e.metaReason || e.message).slice(0, 32);
  return `${e.ticker}|${e.category}|${reason}`;
}

export interface RiskEventFilter {
  category: 'all' | string;
  ticker?: string;
}

function passesFilter(e: NormalizedRiskEvent, filter: RiskEventFilter): boolean {
  if (filter.category !== 'all' && e.category !== filter.category) return false;
  if (filter.ticker && filter.ticker.trim()) {
    const q = filter.ticker.trim().toLowerCase();
    if (!e.ticker.toLowerCase().includes(q)) return false;
  }
  return true;
}

export function pinCriticalFirst(groups: CompressedGroup[]): CompressedGroup[] {
  const critical: CompressedGroup[] = [];
  const others: CompressedGroup[] = [];
  for (const g of groups) {
    if (g.first.level === 'critical') critical.push(g);
    else others.push(g);
  }
  return [...critical, ...others];
}

export function sortAndCompress(
  events: NormalizedRiskEvent[],
  expandedKeys: ReadonlySet<string>,
  filter: RiskEventFilter,
): CompressedGroup[] {
  const filtered = events.filter((e) => passesFilter(e, filter));
  filtered.sort((a, b) => (b.ts || 0) - (a.ts || 0));

  const groups = new Map<string, CompressedGroup>();
  const order: string[] = [];
  for (const e of filtered) {
    const k = groupKey(e);
    let g = groups.get(k);
    if (!g) {
      g = { key: k, first: e, count: 0, expanded: expandedKeys.has(k), display: 'flat', items: [] };
      groups.set(k, g);
      order.push(k);
    }
    g.count += 1;
    g.items.push(e);
  }

  let rendered = 0;
  const out: CompressedGroup[] = [];
  const orderedGroups = pinCriticalFirst(
    order.map((k) => groups.get(k)).filter((g): g is CompressedGroup => Boolean(g)),
  );
  for (const g of orderedGroups) {
    const expanded = expandedKeys.has(g.key);
    const shouldRollup = g.count >= COMPRESS_THRESHOLD && !expanded;
    g.display = shouldRollup ? 'rollup' : 'flat';
    g.expanded = expanded;
    const rowsThisGroup = g.display === 'rollup' ? 1 : g.items.length;
    if (rendered + rowsThisGroup > MAX_RENDERED_ROWS) {
      const remaining = MAX_RENDERED_ROWS - rendered;
      if (remaining <= 0) break;
      if (g.display === 'flat') {
        out.push({ ...g, items: g.items.slice(0, remaining) });
        rendered = MAX_RENDERED_ROWS;
        break;
      } else {
        break;
      }
    }
    rendered += rowsThisGroup;
    out.push(g);
  }
  return out;
}
