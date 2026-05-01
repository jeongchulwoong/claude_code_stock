// Phase F4 — Admin Risk Events Island.
import { useMemo, useState } from 'react';
import {
  EMPTY_RISK_EVENTS_SNAPSHOT,
  useRiskEventsPolling,
  type RiskEventsSnapshot,
  type UseRiskEventsPollingOptions,
} from './useRiskEventsPolling';
import {
  normalizeFromOrders,
  normalizeFromRiskEvents,
  sortAndCompress,
} from './riskEventModel';
import RiskEventList from './RiskEventList';

export interface AdminRiskEventsIslandProps {
  snapshot?: RiskEventsSnapshot;
  pollingOptions?: UseRiskEventsPollingOptions;
}

export default function AdminRiskEventsIsland(props: AdminRiskEventsIslandProps) {
  const pollingOptions: UseRiskEventsPollingOptions = {
    ...(props.pollingOptions ?? {}),
    enabled: props.snapshot == null && (props.pollingOptions?.enabled ?? true),
  };
  const polled = useRiskEventsPolling(pollingOptions);
  const snap = props.snapshot ?? polled ?? EMPTY_RISK_EVENTS_SNAPSHOT;

  const [category, setCategory] = useState<'all' | string>('all');
  const [ticker, setTicker] = useState<string>('');
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(() => new Set());

  const groups = useMemo(() => {
    const events = [
      ...normalizeFromRiskEvents(snap.riskEvents),
      ...normalizeFromOrders(snap.orders),
    ];
    return sortAndCompress(events, expandedKeys, { category, ticker });
  }, [snap.riskEvents, snap.orders, expandedKeys, category, ticker]);

  return (
    <RiskEventList
      groups={groups}
      filter={{ category, ticker }}
      onToggleFilter={(c) => setCategory(c)}
      onTickerChange={(t) => setTicker(t)}
      onExpandGroup={(k) => setExpandedKeys((prev) => {
        const next = new Set(prev);
        next.add(k);
        return next;
      })}
    />
  );
}
