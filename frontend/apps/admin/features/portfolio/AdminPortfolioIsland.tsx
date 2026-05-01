// Phase F6.1 — Admin Portfolio Island. Sector chart + holdings grid + smooth transitions.
//
// 데이터 계약: portfolioModel.normalizePortfolio.
// /api/portfolio 의 실제 응답 (sector / weight / eval_amt / pnl / pnl_rate) 정상 매핑.

import { useMemo } from 'react';
import { money, moneyShort, pct, qty as fmtQty } from '@shared/format/format';
import {
  EMPTY_PORTFOLIO_SNAPSHOT,
  usePortfolioPolling,
  type PortfolioSnapshot,
  type UsePortfolioPollingOptions,
} from './usePortfolioPolling';
import {
  normalizePortfolio,
  pnlTone,
  type NormalizedHolding,
} from './portfolioModel';
import SectorChart from './SectorChart';
import './portfolio.css';

export interface AdminPortfolioIslandProps {
  snapshot?: PortfolioSnapshot;
  pollingOptions?: UsePortfolioPollingOptions;
}

function HoldingRow({ h }: { h: NormalizedHolding }) {
  const pnlClass = pnlTone(h.pnl);
  return (
    <article className="qd-pf-card">
      <header className="qd-pf-row">
        <span className="qd-pf-ticker qd-num">{h.ticker}</span>
        <span className="qd-pf-sector">{h.sector}</span>
      </header>
      <div className="qd-pf-name" title={h.name}>{h.name}</div>
      <div className="qd-pf-meta qd-num">
        <span>{fmtQty(h.qty)}주</span>
        <span aria-hidden="true">·</span>
        <span>평단 {money(h.avgPrice)}</span>
        {h.currentPrice != null && (
          <>
            <span aria-hidden="true">·</span>
            <span>현재 {money(h.currentPrice)}</span>
          </>
        )}
      </div>
      <div className="qd-pf-pnl qd-num qd-number-transition" data-pnl={pnlClass}>
        {money(h.pnl)} <span className="qd-pf-pnl-pct">{pct(h.pnlPct)}</span>
      </div>
      {h.weight != null && h.weight > 0 && (
        <div className="qd-pf-weight" aria-label={`${h.weight.toFixed(1)}% 비중`}>
          <div
            className="qd-pf-weight-fill"
            style={{ width: `${Math.max(0, Math.min(100, h.weight))}%` }}
          />
          <span className="qd-pf-weight-l qd-num">{h.weight.toFixed(1)}%</span>
        </div>
      )}
    </article>
  );
}

function HeroStat({
  label, value, tone, accent, hint,
}: {
  label: string;
  value: string;
  tone?: 'pos' | 'neg' | 'zero';
  accent?: boolean;
  hint?: string | null;
}) {
  return (
    <div className={`qd-pf-hero-stat${accent ? ' qd-pf-hero-stat--accent' : ''}`}>
      <div className="qd-pf-hero-l">{label}</div>
      <div
        className="qd-pf-hero-v qd-num qd-number-transition"
        data-pnl={tone}
      >{value}</div>
      {hint && <div className="qd-pf-hero-hint">{hint}</div>}
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="qd-pf-grid qd-stagger" aria-busy="true">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="qd-pf-card">
          <div className="qd-skeleton qd-skeleton-line" style={{ width: '40%' }} />
          <div className="qd-skeleton qd-skeleton-line" style={{ width: '70%' }} />
          <div className="qd-skeleton qd-skeleton-line" style={{ width: '55%' }} />
          <div className="qd-skeleton qd-skeleton-line" style={{ width: '60%' }} />
        </div>
      ))}
    </div>
  );
}

function fmtUpdatedAt(s: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

export default function AdminPortfolioIsland(props: AdminPortfolioIslandProps) {
  const pollingOptions: UsePortfolioPollingOptions = {
    ...(props.pollingOptions ?? {}),
    enabled: props.snapshot == null && (props.pollingOptions?.enabled ?? true),
  };
  const polled = usePortfolioPolling(pollingOptions);
  const snap = props.snapshot ?? polled ?? EMPTY_PORTFOLIO_SNAPSHOT;

  const { summary, holdings, sectors } = useMemo(
    () => normalizePortfolio(snap.data),
    [snap.data],
  );

  const showSkeleton = snap.loading && !snap.data;

  return (
    <section
      data-island="admin-portfolio"
      data-phase="F6"
      className="qd-pf-island qd-fade-in"
      aria-label="포트폴리오 (read-only)"
    >
      <header className="qd-pf-head">
        <div>
          <h2 className="qd-pf-title">포트폴리오</h2>
          <p className="qd-pf-sub">
            보유 {summary.holdingsCount}종 · 마지막 갱신 <span className="qd-num">{fmtUpdatedAt(summary.updatedAt)}</span>
          </p>
        </div>
        {snap.hadError && (
          <span className="qd-pf-err qd-fade-in" role="status">데이터 갱신 실패</span>
        )}
      </header>

      <section className="qd-pf-hero qd-stagger">
        <HeroStat label="평가금액" value={money(summary.totalEval)} accent />
        <HeroStat
          label="평가손익(미실현)"
          value={`${money(summary.totalPnl)} (${pct(summary.totalPnlPct)})`}
          tone={pnlTone(summary.totalPnl)}
        />
        <HeroStat
          label="실현손익(누적)"
          value={money(summary.realizedPnl)}
          tone={pnlTone(summary.realizedPnl)}
          hint={summary.todayRealizedPnl != null ? `오늘 ${money(summary.todayRealizedPnl)}` : null}
        />
        <HeroStat label="매수가능" value={moneyShort(summary.buyingPower)} />
      </section>

      <section className="qd-pf-split">
        <div className="qd-pf-sector-card">
          <header className="qd-pf-section-h">섹터 분포</header>
          <SectorChart sectors={sectors} />
        </div>
        <div className="qd-pf-holdings-card">
          <header className="qd-pf-section-h">
            보유 종목 <span className="qd-pf-section-count qd-num">{holdings.length}</span>
          </header>
          {showSkeleton ? (
            <SkeletonGrid />
          ) : holdings.length === 0 ? (
            <div className="qd-pf-empty qd-fade-in">
              <p>보유 종목이 없습니다.</p>
              <p className="qd-pf-empty-sub">매수 체결 시 자동으로 표시됩니다.</p>
            </div>
          ) : (
            <div className="qd-pf-grid qd-stagger" key={`${snap.fetchedAt}`}>
              {holdings.map((h) => <HoldingRow key={h.ticker} h={h} />)}
            </div>
          )}
        </div>
      </section>
    </section>
  );
}
