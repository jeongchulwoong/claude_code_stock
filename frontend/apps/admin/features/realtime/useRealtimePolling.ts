// Phase F4 — Realtime polling hook (RiskEvents hook 재사용).
import {
  useRiskEventsPolling,
  type RiskEventsSnapshot,
  type UseRiskEventsPollingOptions,
} from '../riskEvents/useRiskEventsPolling';

export type UseRealtimePollingOptions = UseRiskEventsPollingOptions;
export type RealtimeSnapshot = RiskEventsSnapshot;

export function useRealtimePolling(opts: UseRealtimePollingOptions = {}): RealtimeSnapshot {
  return useRiskEventsPolling(opts);
}
