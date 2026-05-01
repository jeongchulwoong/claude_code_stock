// Phase F3 — 6 cards (presentational only).
import type {
  BuyingPowerView,
  DailyLossView,
  DataHealthRow,
  ModeView,
  OrderTotals,
  TraderHealthView,
} from './overviewModel';

interface ModeBPProps { mode: ModeView; buyingPower: BuyingPowerView; }

function fmtKRW(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${Math.round(v).toLocaleString('ko-KR')}원`;
}

function fmtPct(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}%`;
}

export function ModeBuyingPowerCard({ mode, buyingPower }: ModeBPProps) {
  return (
    <article className="qd-card" data-card="mode-bp">
      <header className="qd-card-h">운영 모드 / 매수 여력</header>
      <div className="qd-card-mode" data-tone={mode.tone}>{mode.mode}</div>
      <div className="qd-card-bp qd-num" data-buying-power data-tone={buyingPower.tone}>
        {fmtKRW(buyingPower.amount)}
      </div>
      {buyingPower.hint && (
        <div className="qd-card-hint" data-buying-power-hint>{buyingPower.hint}</div>
      )}
    </article>
  );
}

interface PnlProps {
  evalAmount: number | null;
  evalPnl: number | null;
  evalPnlPct: number | null;
  realizedPnl: number | null;
}

export function PnlCard({ evalAmount, evalPnl, evalPnlPct, realizedPnl }: PnlProps) {
  return (
    <article className="qd-card" data-card="pnl">
      <header className="qd-card-h">잔고 / 손익</header>
      <div className="qd-card-row"><span>평가금액</span><span className="qd-num">{fmtKRW(evalAmount)}</span></div>
      <div className="qd-card-row">
        <span>평가손익</span>
        <span className="qd-num" data-pnl={evalPnl == null ? 'zero' : (evalPnl > 0 ? 'pos' : (evalPnl < 0 ? 'neg' : 'zero'))}>
          {fmtKRW(evalPnl)} ({fmtPct(evalPnlPct)})
        </span>
      </div>
      <div className="qd-card-row"><span>실현손익(오늘)</span><span className="qd-num">{fmtKRW(realizedPnl)}</span></div>
    </article>
  );
}

interface OrderProps { totals: OrderTotals; }

export function OrderStateCard({ totals }: OrderProps) {
  return (
    <article className="qd-card" data-card="order-state">
      <header className="qd-card-h">주문 상태</header>
      <div className="qd-card-totals" data-order-totals>
        <span>체결 {totals.filled}</span> · <span>차단 {totals.blocked}</span> · <span>미체결 {totals.unfilled}</span>
      </div>
    </article>
  );
}

interface DailyLossProps { view: DailyLossView; }

export function DailyLossCard({ view }: DailyLossProps) {
  const fillStyle: React.CSSProperties = {
    width: `${Math.max(0, Math.min(100, view.pct))}%`,
    background: view.tone === 'fail' ? 'var(--danger)' : view.tone === 'warn' ? 'var(--warning)' : 'var(--ok)',
  };
  return (
    <article className="qd-card" data-card="daily-loss">
      <header className="qd-card-h">일일 손실 한도</header>
      <div className="qd-card-pct" data-tone={view.tone}>{view.pct}%</div>
      <div className="qd-card-bar"><div data-loss-fill style={fillStyle} /></div>
      {view.usedText && view.limitText && (
        <div className="qd-card-meta">{view.usedText} / {view.limitText}</div>
      )}
    </article>
  );
}

interface DataHealthProps { rows: DataHealthRow[]; }

export function DataHealthCard({ rows }: DataHealthProps) {
  return (
    <article className="qd-card" data-card="data-health">
      <header className="qd-card-h">데이터 헬스</header>
      <ul className="qd-card-list">
        {rows.map((r) => (
          <li key={r.label} data-tone={r.tone}>
            <span>{r.label}</span><span>{r.state}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

interface TraderProps { view: TraderHealthView; }

export function TraderHealthCard({ view }: TraderProps) {
  const text =
    view.status === 'running'        ? 'RUNNING' :
    view.status === 'running-stale'  ? `RUNNING (stale ${view.heartbeatAgeSec ?? '?'}s)` :
    view.status === 'stopped'        ? 'STOPPED' : '—';
  return (
    <article className="qd-card" data-card="trader">
      <header className="qd-card-h">Trader</header>
      <div className="qd-card-trader qd-num" data-trader-status={view.status} data-tone={view.tone}>
        {text}
      </div>
      {view.pid != null && <div className="qd-card-meta">pid {view.pid}</div>}
    </article>
  );
}
