// Phase F4 — Risk Events polling hook.
import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import type { OrdersResponse, RiskEventsResponse } from '@shared/contracts';

export interface UseRiskEventsPollingOptions {
  enabled?: boolean;
  intervalMs?: number;
  limit?: number;
  fetcher?: typeof fetchJson;
}

export interface RiskEventsSnapshot {
  riskEvents: RiskEventsResponse | null;
  orders:     OrdersResponse | null;
  fetchedAt:  number;
  hadError:   boolean;
}

export const EMPTY_RISK_EVENTS_SNAPSHOT: RiskEventsSnapshot = {
  riskEvents: null,
  orders:     null,
  fetchedAt:  0,
  hadError:   false,
};

const DEFAULT_INTERVAL_MS = 10_000;
const DEFAULT_LIMIT = 200;

function clampLimit(n: number | undefined): number {
  const v = Number.isFinite(n as number) ? Number(n) : DEFAULT_LIMIT;
  return Math.max(1, Math.min(200, Math.floor(v)));
}

export function useRiskEventsPolling(opts: UseRiskEventsPollingOptions = {}): RiskEventsSnapshot {
  const enabled = opts.enabled ?? true;
  const intervalMs = opts.intervalMs ?? DEFAULT_INTERVAL_MS;
  const limit = clampLimit(opts.limit);
  const fetcher = opts.fetcher ?? fetchJson;
  const [snap, setSnap] = useState<RiskEventsSnapshot>(EMPTY_RISK_EVENTS_SNAPSHOT);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const ctrl = new AbortController();

    async function tick() {
      const results = await Promise.allSettled([
        fetcher<RiskEventsResponse>(`${adminApi.riskEvents}?limit=${limit}`, { signal: ctrl.signal }),
        fetcher<OrdersResponse>(adminApi.orders, { signal: ctrl.signal }),
      ]);
      if (cancelled) return;
      const [r, o] = results;
      const value = (x: PromiseSettledResult<unknown>) =>
        x.status === 'fulfilled' ? x.value : null;
      setSnap({
        riskEvents: value(r) as RiskEventsResponse | null,
        orders:     value(o) as OrdersResponse | null,
        fetchedAt:  Date.now(),
        hadError:   results.some((x) => x.status === 'fulfilled' && x.value == null)
                    || results.some((x) => x.status === 'rejected'),
      });
    }

    tick();
    if (intervalMs > 0) {
      const t = setInterval(tick, intervalMs);
      return () => { cancelled = true; clearInterval(t); ctrl.abort(); };
    }
    return () => { cancelled = true; ctrl.abort(); };
  }, [enabled, intervalMs, limit, fetcher]);

  return snap;
}
