// Phase F5 — Public Screener polling.
import { useEffect, useState } from 'react';
import { fetchJson } from '@shared/api/fetchJson';
import { publicApi } from '@shared/api/publicApi';
import type { PublicScreenerResponse } from '@shared/contracts';
import type { MarketFilter } from './screenerModel';

export interface UsePublicScreenerPollingOptions {
  enabled?: boolean;
  intervalMs?: number;
  market?: MarketFilter;
  fetcher?: typeof fetchJson;
}

export interface PublicScreenerSnapshot {
  screener: PublicScreenerResponse | null;
  fetchedAt: number;
  hadError: boolean;
}

export const EMPTY_SCREENER_SNAPSHOT: PublicScreenerSnapshot = {
  screener: null, fetchedAt: 0, hadError: false,
};

const DEFAULT_INTERVAL_MS = 10_000;

export function usePublicScreenerPolling(opts: UsePublicScreenerPollingOptions = {}): PublicScreenerSnapshot {
  const enabled = opts.enabled ?? true;
  const intervalMs = opts.intervalMs ?? DEFAULT_INTERVAL_MS;
  const market = opts.market ?? 'all';
  const fetcher = opts.fetcher ?? fetchJson;
  const [snap, setSnap] = useState<PublicScreenerSnapshot>(EMPTY_SCREENER_SNAPSHOT);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const ctrl = new AbortController();

    async function tick() {
      try {
        const data = await fetcher<PublicScreenerResponse>(
          `${publicApi.publicScreener}?market=${market}`,
          { signal: ctrl.signal },
        );
        if (cancelled) return;
        setSnap({ screener: data, fetchedAt: Date.now(), hadError: data == null });
      } catch {
        if (cancelled) return;
        setSnap((prev) => ({ ...prev, hadError: true, fetchedAt: Date.now() }));
      }
    }

    tick();
    if (intervalMs > 0) {
      const t = setInterval(tick, intervalMs);
      return () => { cancelled = true; clearInterval(t); ctrl.abort(); };
    }
    return () => { cancelled = true; ctrl.abort(); };
  }, [enabled, intervalMs, market, fetcher]);

  return snap;
}
