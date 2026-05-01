// Phase F4 — Realtime 분류/필터.
import type { NormalizedRiskEvent } from '../riskEvents/riskEventModel';

export type RealtimeBucket = 'critical' | 'orders' | 'risk' | 'system';

export interface RealtimeBuckets {
  critical: NormalizedRiskEvent[];
  orders:   NormalizedRiskEvent[];
  risk:     NormalizedRiskEvent[];
  system:   NormalizedRiskEvent[];
}

const ORDER_CATEGORIES: ReadonlySet<string> = new Set([
  'order_error', 'cancel_failed', 'duplicate_order_blocked',
  'ord_alow_insufficient', 'buy_blocked',
  'buy_limit_placed', 'buy_reprice', 'buy_not_filled', 'buy_abandoned_expensive',
]);

const RISK_CATEGORIES: ReadonlySet<string> = new Set([
  'stop_loss', 'take_profit', 'time_stop', 'force_close',
  'daily_loss_warning', 'consecutive_loss_halt', 'slippage_warning',
  'spread_wide_reprice', 'intraday_chasing_guard', 'reference_price_adjusted',
]);

export function bucketRealtimeEvents(events: NormalizedRiskEvent[]): RealtimeBuckets {
  const out: RealtimeBuckets = { critical: [], orders: [], risk: [], system: [] };
  for (const e of events) {
    if (e.level === 'critical')           { out.critical.push(e); continue; }
    if (ORDER_CATEGORIES.has(e.category)) { out.orders.push(e); continue; }
    if (RISK_CATEGORIES.has(e.category))  { out.risk.push(e); continue; }
    out.system.push(e);
  }
  for (const k of ['critical', 'orders', 'risk', 'system'] as RealtimeBucket[]) {
    out[k].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  }
  return out;
}

export interface RealtimeFilter {
  level: 'all' | RealtimeBucket;
  type: 'all' | 'blocked' | 'errors';
  ticker?: string;
}

const BLOCKED_CATEGORIES: ReadonlySet<string> = new Set([
  'buy_blocked', 'duplicate_order_blocked', 'ord_alow_insufficient', 'consecutive_loss_halt',
]);

const ERROR_CATEGORIES: ReadonlySet<string> = new Set(['order_error', 'cancel_failed']);

export function filterRealtimeBucket(events: NormalizedRiskEvent[], filter: RealtimeFilter): NormalizedRiskEvent[] {
  const ticker = filter.ticker?.trim().toLowerCase() ?? '';
  return events.filter((e) => {
    if (filter.type === 'blocked' && !BLOCKED_CATEGORIES.has(e.category)) return false;
    if (filter.type === 'errors' && !ERROR_CATEGORIES.has(e.category))    return false;
    if (ticker && !e.ticker.toLowerCase().includes(ticker)) return false;
    return true;
  });
}
