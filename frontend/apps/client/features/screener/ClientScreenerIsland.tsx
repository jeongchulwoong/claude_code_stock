// Phase F5 — Client Screener Island.
import { useMemo, useState } from 'react';
import {
  applyMarketFilter,
  normalizeScreener,
  type MarketFilter,
  type NormalizedScreener,
} from './screenerModel';
import {
  EMPTY_SCREENER_SNAPSHOT,
  usePublicScreenerPolling,
  type PublicScreenerSnapshot,
  type UsePublicScreenerPollingOptions,
} from './usePublicScreenerPolling';
import ScreenerCards from './ScreenerCards';

export interface ClientScreenerIslandProps {
  snapshot?: PublicScreenerSnapshot;
  pollingOptions?: UsePublicScreenerPollingOptions;
  nowProvider?: () => number;
}

export default function ClientScreenerIsland(props: ClientScreenerIslandProps) {
  const pollingOptions: UsePublicScreenerPollingOptions = {
    ...(props.pollingOptions ?? {}),
    enabled: props.snapshot == null && (props.pollingOptions?.enabled ?? true),
  };
  const polled = usePublicScreenerPolling(pollingOptions);
  const snap = props.snapshot ?? polled ?? EMPTY_SCREENER_SNAPSHOT;
  const now = props.nowProvider ?? Date.now;

  const [market, setMarket] = useState<MarketFilter>('all');
  const [search, setSearch] = useState<string>('');
  const [refreshTick, setRefreshTick] = useState(0);

  const normalized: NormalizedScreener = useMemo(
    () => normalizeScreener(snap.screener, now()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [snap.screener, snap.fetchedAt, refreshTick],
  );

  const visibleItems = useMemo(
    () => applyMarketFilter(normalized.items, market, search),
    [normalized.items, market, search],
  );

  return (
    <ScreenerCards
      items={visibleItems}
      market={market}
      onMarketChange={(m) => setMarket(m)}
      search={search}
      onSearchChange={(q) => setSearch(q)}
      onRefresh={() => setRefreshTick((n) => n + 1)}
      isStale={normalized.isStale}
      updatedAt={normalized.updatedAt}
      hadError={snap.hadError}
    />
  );
}
