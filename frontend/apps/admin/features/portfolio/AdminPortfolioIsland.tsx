// Phase F6 — Admin Portfolio Island. 부드러운 fade-slide + stagger 애니메이션.
import { useMemo } from 'react';
import { money, moneyShort, pct, qty as fmtQty } from '@shared/format/format';
import {
  EMPTY_PORTFOLIO_SNAPSHOT,
  usePortfolioPolling,
  type PortfolioSnapshot,
  type UsePortfolioPollingOptions,
} from './usePortfolioPolling';
import { normalizePortfolio, pnlTone, type PortfolioHolding } from './portfolioModel';
import './portfolio.css';

export interface AdminPortfolioIslandProps {
  snapshot?: PortfolioSnapshot;
  pollingOptions?: UsePortfolioPollingOptions;
}

function HoldingRow({ h }: { h: PortfolioHolding }) {
  const pnl = h.evlt_pl ?? null;
  const pnlPct = h.evlt_pl_pct ?? null;
  return (
    <article className="qd-pf-card">
      <header className="qd-pf-row">
        <span className="qd-pf-ticker qd-num">{h.ticker}</span>
        {h.style && <span className="qd-pf-style">{h.style === 'daytrading' ? '단타' : '장투'}</span>}
      </header>
      <div className="qd-pf-name">{h.name || '—'}</div>
      <div className="qd-pf-meta qd-num">
        <span>{fmtQty(h.qty ?? null)}주</span>
        <span>·</span>
        <span>{money(h.avg_price ?? null)}</span>
      </div>
      <div className="qd-pf-pnl qd-num qd-number-transition" data-pnl={pnlTone(pnl)}>
        {money(pnl)} <span className="qd-pf-pnl-pct">{pct(pnlPct)}</span>
      </div>
      {h.weight_pct != null && (
        <div className="qd-pf-weight" aria-label={`${h.weight_pct.toFixed(1)}% 비중`}>
          <div
            className="qd-pf-weight-fill"
            style={{ width: `${Math.max(0, Math.min(100, h.weight_pct))}%` }}
          />
        </div>
      )}
    </article>
  );
}

function HeroStat({ label, value, tone, accent }:
  { label: string; value: string; tone?: 'pos' | 'neg' | 'zero'; accent?: boolean }) {
  return (
    <div className={`qd-pf-hero-stat${accent ? ' qd-pf-hero-stat--accent' : ''}`}>
      <div className="qd-pf-hero-l">{label}</div>
      <div
        className="qd-pf-hero-v qd-num qd-number-transition"
        data-pnl={tone}
      >{value}</div>
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

  const { summary, holdings } = useMemo(
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
        <HeroStat
          label="평가금액"
          value={money(summary.totalEval)}
          accent
        />
        <HeroStat
          label="평가손익"
          value={`${money(summary.totalPnl)} (${pct(summary.totalPnlPct)})`}
          tone={pnlTone(summary.totalPnl)}
        />
        <HeroStat
          label="오늘 실현손익"
          value={money(summary.todayRealizedPnl)}
          tone={pnlTone(summary.todayRealizedPnl)}
        />
        <HeroStat
          label="매수가능"
          value={moneyShort(summary.buyingPower)}
        />
      </section>

      {showSkeleton ? (
        <SkeletonGrid />
      ) : holdings.length === 0 ? (
        <div className="qd-pf-empty qd-fade-in">
          <p>보유 종목이 없습니다.</p>
          <p className="qd-pf-empty-sub">
            매수 체결 시 자동으로 표시됩니다 — 본 화면은 읽기 전용입니다.
          </p>
        </div>
      ) : (
        <div className="qd-pf-grid qd-stagger" key={`${snap.fetchedAt}`}>
          {holdings.map((h) => (
            <HoldingRow key={h.ticker} h={h} />
          ))}
        </div>
      )}
    </section>
  );
}
