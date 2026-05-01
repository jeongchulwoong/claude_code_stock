// Phase F3 — Admin Overview Island (read-only).
import {
  deriveBuyingPower,
  deriveDailyLoss,
  deriveDataHealth,
  deriveDaytradeGate,
  deriveMode,
  deriveOrderTotals,
  deriveTraderHealth,
  type OverviewSnapshot,
} from './overviewModel';
import {
  DailyLossCard,
  DataHealthCard,
  DaytradeGateCard,
  ModeBuyingPowerCard,
  OrderStateCard,
  PnlCard,
  TraderHealthCard,
} from './OverviewCards';
import { useOverviewPolling, type UseOverviewPollingOptions } from './useOverviewPolling';

export interface AdminOverviewIslandProps {
  snapshot?: OverviewSnapshot;
  pollingOptions?: UseOverviewPollingOptions;
}

const GRID_STYLE: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
  gap: 12,
  margin: 0,
  padding: 0,
};

const HEADER_STYLE: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'baseline',
  margin: '0 0 12px 0',
  paddingBottom: 8,
  borderBottom: '1px solid var(--qd-border)',
  fontSize: 12,
  color: 'var(--qd-text-3)',
  letterSpacing: '.3px',
  textTransform: 'uppercase',
  fontWeight: 600,
};

const ERR_BADGE_STYLE: React.CSSProperties = {
  fontSize: 11,
  padding: '2px 8px',
  borderRadius: 999,
  background: 'var(--qd-amber-bg)',
  color: 'var(--qd-amber)',
  textTransform: 'none',
  letterSpacing: 0,
  fontWeight: 500,
};

export default function AdminOverviewIsland(props: AdminOverviewIslandProps) {
  const pollingOptions: UseOverviewPollingOptions = {
    ...(props.pollingOptions ?? {}),
    enabled: props.snapshot == null && (props.pollingOptions?.enabled ?? true),
  };
  const polled = useOverviewPolling(pollingOptions);
  const snap = props.snapshot ?? polled;

  const mode        = deriveMode(snap);
  const buyingPower = deriveBuyingPower(snap);
  const orderTotals = deriveOrderTotals(snap);
  const dailyLoss   = deriveDailyLoss(snap);
  const dataHealth  = deriveDataHealth(snap);
  const trader      = deriveTraderHealth(snap);
  const daytrade    = deriveDaytradeGate(snap);

  const evalAmount  = snap.balance?.tot_evlu_amt   ?? null;
  const evalPnl     = snap.balance?.tot_evlt_pl    ?? null;
  const evalPnlPct  = snap.balance?.tot_evlt_pl_pct ?? null;
  const realizedPnl = snap.summary?.realized_pnl   ?? null;

  return (
    <section
      data-phase="F3"
      data-island="admin-overview"
      data-fetched-at={String(snap.fetchedAt)}
      data-had-error={String(snap.hadError)}
      aria-label="운영 콘솔 read-only overview"
    >
      <header style={HEADER_STYLE}>
        <span>운영 위험 요약 · 읽기 전용</span>
        {snap.hadError && (
          <span data-error-badge style={ERR_BADGE_STYLE}>일부 응답 누락</span>
        )}
      </header>
      <div style={GRID_STYLE} className="qd-stagger">
        <DaytradeGateCard view={daytrade} />
        <ModeBuyingPowerCard mode={mode} buyingPower={buyingPower} />
        <PnlCard
          evalAmount={evalAmount}
          evalPnl={evalPnl}
          evalPnlPct={evalPnlPct}
          realizedPnl={realizedPnl}
        />
        <OrderStateCard totals={orderTotals} />
        <DailyLossCard view={dailyLoss} />
        <DataHealthCard rows={dataHealth} />
        <TraderHealthCard view={trader} />
      </div>
    </section>
  );
}
