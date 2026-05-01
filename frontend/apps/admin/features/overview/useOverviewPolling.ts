// Phase F3 — Admin Overview polling hook.
import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import type {
  BalanceResponse,
  DaytradeStateResponse,
  OrdersResponse,
  ProcessStatusResponse,
  SummaryResponse,
  SystemStatusResponse,
} from '@shared/contracts';
import { EMPTY_SNAPSHOT, type OverviewSnapshot } from './overviewModel';

export interface UseOverviewPollingOptions {
  enabled?: boolean;
  intervalMs?: number;
  fetcher?: typeof fetchJson;
}

const DEFAULT_INTERVAL_MS = 10_000;

export function useOverviewPolling(opts: UseOverviewPollingOptions = {}): OverviewSnapshot {
  const enabled = opts.enabled ?? true;
  const intervalMs = opts.intervalMs ?? DEFAULT_INTERVAL_MS;
  const fetcher = opts.fetcher ?? fetchJson;
  const [snap, setSnap] = useState<OverviewSnapshot>(EMPTY_SNAPSHOT);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const ctrl = new AbortController();

    async function tick() {
      const results = await Promise.allSettled([
        fetcher<SummaryResponse>(adminApi.summary,             { signal: ctrl.signal }),
        fetcher<BalanceResponse>(adminApi.balance,             { signal: ctrl.signal }),
        fetcher<OrdersResponse>(adminApi.orders,               { signal: ctrl.signal }),
        fetcher<SystemStatusResponse>(adminApi.systemStatus,   { signal: ctrl.signal }),
        fetcher<ProcessStatusResponse>(adminApi.processStatus, { signal: ctrl.signal }),
        fetcher<DaytradeStateResponse>(adminApi.daytradeState, { signal: ctrl.signal }),
      ]);
      if (cancelled) return;
      const [s, b, o, sys, ps, dt] = results;
      const value = (r: PromiseSettledResult<unknown>) =>
        r.status === 'fulfilled' ? r.value : null;
      setSnap({
        summary:  value(s)   as SummaryResponse | null,
        balance:  value(b)   as BalanceResponse | null,
        orders:   value(o)   as OrdersResponse  | null,
        system:   value(sys) as SystemStatusResponse | null,
        process:  value(ps)  as ProcessStatusResponse | null,
        daytrade: value(dt)  as DaytradeStateResponse | null,
        fetchedAt: Date.now(),
        hadError: results.some((r) => r.status === 'fulfilled' && r.value == null)
                  || results.some((r) => r.status === 'rejected'),
      });
    }

    tick();
    if (intervalMs > 0) {
      const t = setInterval(tick, intervalMs);
      return () => { cancelled = true; clearInterval(t); ctrl.abort(); };
    }
    return () => { cancelled = true; ctrl.abort(); };
  }, [enabled, intervalMs, fetcher]);

  return snap;
}
