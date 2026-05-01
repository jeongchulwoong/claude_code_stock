// Phase F6 — Portfolio polling.
import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import type { PortfolioResponse } from './portfolioModel';

export interface UsePortfolioPollingOptions {
  enabled?: boolean;
  intervalMs?: number;
  fetcher?: typeof fetchJson;
}

export interface PortfolioSnapshot {
  data: PortfolioResponse | null;
  fetchedAt: number;
  hadError: boolean;
  loading: boolean;
}

export const EMPTY_PORTFOLIO_SNAPSHOT: PortfolioSnapshot = {
  data: null, fetchedAt: 0, hadError: false, loading: true,
};

export function usePortfolioPolling(opts: UsePortfolioPollingOptions = {}): PortfolioSnapshot {
  const enabled = opts.enabled ?? true;
  const intervalMs = opts.intervalMs ?? 15_000;
  const fetcher = opts.fetcher ?? fetchJson;
  const [snap, setSnap] = useState<PortfolioSnapshot>(EMPTY_PORTFOLIO_SNAPSHOT);

  useEffect(() => {
    if (!enabled) {
      setSnap((prev) => ({ ...prev, loading: false }));
      return;
    }
    let cancelled = false;
    const ctrl = new AbortController();

    async function tick() {
      try {
        const data = await fetcher<PortfolioResponse>(adminApi.portfolio, { signal: ctrl.signal });
        if (cancelled) return;
        setSnap({ data, fetchedAt: Date.now(), hadError: data == null, loading: false });
      } catch {
        if (cancelled) return;
        setSnap((prev) => ({ ...prev, hadError: true, fetchedAt: Date.now(), loading: false }));
      }
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
