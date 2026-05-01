// Phase F4 — Admin Realtime Island.
import { useMemo, useState } from 'react';
import {
  normalizeFromOrders,
  normalizeFromRiskEvents,
  type NormalizedRiskEvent,
} from '../riskEvents/riskEventModel';
import {
  useRealtimePolling,
  type RealtimeSnapshot,
  type UseRealtimePollingOptions,
} from './useRealtimePolling';
import {
  bucketRealtimeEvents,
  filterRealtimeBucket,
  type RealtimeBucket,
} from './realtimeModel';
import RealtimeSection from './RealtimeSection';

export interface AdminRealtimeIslandProps {
  snapshot?: RealtimeSnapshot;
  pollingOptions?: UseRealtimePollingOptions;
}

const LEVEL_BUTTONS: Array<{ value: 'all' | RealtimeBucket; label: string }> = [
  { value: 'all', label: '전체' },
  { value: 'critical', label: 'Critical' },
  { value: 'orders', label: '주문' },
  { value: 'risk',   label: '리스크' },
  { value: 'system', label: '시스템' },
];

const TYPE_BUTTONS: Array<{ value: 'all' | 'blocked' | 'errors'; label: string }> = [
  { value: 'all', label: '기본' },
  { value: 'blocked', label: '차단만 보기' },
  { value: 'errors',  label: '오류만 보기' },
];

const EMPTY_RT_SNAPSHOT: RealtimeSnapshot = {
  riskEvents: null, orders: null, fetchedAt: 0, hadError: false,
};

export default function AdminRealtimeIsland(props: AdminRealtimeIslandProps) {
  const pollingOptions: UseRealtimePollingOptions = {
    ...(props.pollingOptions ?? {}),
    enabled: props.snapshot == null && (props.pollingOptions?.enabled ?? true),
  };
  const polled = useRealtimePolling(pollingOptions);
  const snap = props.snapshot ?? polled ?? EMPTY_RT_SNAPSHOT;

  const [level, setLevel] = useState<'all' | RealtimeBucket>('all');
  const [type, setType] = useState<'all' | 'blocked' | 'errors'>('all');
  const [ticker, setTicker] = useState<string>('');

  const buckets = useMemo(() => {
    const events: NormalizedRiskEvent[] = [
      ...normalizeFromRiskEvents(snap.riskEvents),
      ...normalizeFromOrders(snap.orders),
    ];
    return bucketRealtimeEvents(events);
  }, [snap.riskEvents, snap.orders]);

  const visibleBuckets = useMemo(() => {
    const filt = { type, ticker };
    return {
      critical: filterRealtimeBucket(buckets.critical, { level: 'all', ...filt }),
      orders:   filterRealtimeBucket(buckets.orders,   { level: 'all', ...filt }),
      risk:     filterRealtimeBucket(buckets.risk,     { level: 'all', ...filt }),
      system:   filterRealtimeBucket(buckets.system,   { level: 'all', ...filt }),
    };
  }, [buckets, type, ticker]);

  const showSection = (b: RealtimeBucket) => level === 'all' || level === b;

  return (
    <section data-island="admin-realtime" data-phase="F4" aria-label="실시간 운영 이벤트 (read-only)">
      <div className="rt-filters">
        <div className="rt-filter-group" data-role="rt-filter-level" role="tablist">
          {LEVEL_BUTTONS.map((b) => {
            const active = level === b.value;
            return (
              <button
                key={b.value}
                type="button"
                className={active ? 'event-filter is-active' : 'event-filter'}
                data-rt-level={b.value}
                role="tab"
                aria-selected={active ? 'true' : 'false'}
                onClick={() => setLevel(b.value)}
              >
                {b.label}
              </button>
            );
          })}
        </div>
        <div className="rt-filter-group" data-role="rt-filter-type" role="tablist">
          {TYPE_BUTTONS.map((b) => {
            const active = type === b.value;
            return (
              <button
                key={b.value}
                type="button"
                className={active ? 'event-filter is-active' : 'event-filter'}
                data-rt-type={b.value}
                role="tab"
                aria-selected={active ? 'true' : 'false'}
                onClick={() => setType(b.value)}
              >
                {b.label}
              </button>
            );
          })}
        </div>
        <input
          type="search"
          className="rt-filter-input"
          data-role="rt-filter-ticker"
          placeholder="종목 검색"
          value={ticker}
          onChange={(ev) => setTicker(ev.target.value)}
          aria-label="종목 검색"
        />
      </div>

      {showSection('critical') && <RealtimeSection title="Critical" bucket="critical" events={visibleBuckets.critical} />}
      {showSection('orders')   && <RealtimeSection title="Orders"   bucket="orders"   events={visibleBuckets.orders} />}
      {showSection('risk')     && <RealtimeSection title="Risk"     bucket="risk"     events={visibleBuckets.risk} />}
      {showSection('system')   && <RealtimeSection title="System"   bucket="system"   events={visibleBuckets.system} />}
    </section>
  );
}
