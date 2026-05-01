// Phase F8 — Chart tab. SVG line/bar (no external chart lib).
//
// 데이터:
//   /api/daily_pnl       — 날짜별 실현손익 line + count bar.
//   /api/ticker_stats    — 종목별 거래 횟수 horizontal bar (top 10).
//
// 모든 차트는 reduced-motion 존중 (transition durations come from tokens).

import { useEffect, useMemo, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import { money } from '@shared/format/format';

interface DailyPnl { date: string; pnl: number; count: number; }
interface TickerStat {
  ticker: string;
  total: number;
  buys: number;
  sells: number;
  avg_price: number;
  total_amount: number;
}

// ──────────────────────────────────────────────────────────────────
// Daily PnL line chart.
// ──────────────────────────────────────────────────────────────────

interface LineProps { data: DailyPnl[]; }

function DailyPnlChart({ data }: LineProps) {
  const W = 720, H = 220, PAD = 36;
  if (data.length === 0) {
    return <div className="qd-tab-stub"><h2>일별 손익 데이터 없음</h2></div>;
  }
  const xs = data.map((_, i) => i);
  const ys = data.map((d) => d.pnl);
  const yMax = Math.max(...ys, 0);
  const yMin = Math.min(...ys, 0);
  const range = (yMax - yMin) || 1;
  const xStep = (W - PAD * 2) / Math.max(1, xs.length - 1);
  const yToPx = (v: number) => H - PAD - ((v - yMin) / range) * (H - PAD * 2);
  const xToPx = (i: number) => PAD + i * xStep;

  const pathD = data.map((d, i) => {
    const cmd = i === 0 ? 'M' : 'L';
    return `${cmd} ${xToPx(i)} ${yToPx(d.pnl)}`;
  }).join(' ');

  const fillD = `${pathD} L ${xToPx(data.length - 1)} ${yToPx(0)} L ${xToPx(0)} ${yToPx(0)} Z`;

  // Y axis ticks (0, max).
  const yZero = yToPx(0);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="qd-chart-svg" role="img" aria-label="일별 실현손익">
      <line x1={PAD} y1={yZero} x2={W - PAD} y2={yZero} stroke="var(--qd-border)" strokeDasharray="3 3" />
      <path d={fillD} fill="var(--qd-blue-bg)" opacity={0.6} />
      <path d={pathD} fill="none" stroke="var(--qd-blue)" strokeWidth={2}
            strokeLinecap="round" strokeLinejoin="round" />
      {data.map((d, i) => (
        <circle key={i} cx={xToPx(i)} cy={yToPx(d.pnl)} r={3}
                fill={d.pnl > 0 ? 'var(--qd-green)' : d.pnl < 0 ? 'var(--qd-red)' : 'var(--qd-text-3)'}>
          <title>{`${d.date} · ${money(d.pnl)} (${d.count}건)`}</title>
        </circle>
      ))}
      <text x={PAD} y={20} fontSize={10} fill="var(--qd-text-3)">최대 {money(yMax)}</text>
      <text x={PAD} y={H - 6} fontSize={10} fill="var(--qd-text-3)">최소 {money(yMin)}</text>
    </svg>
  );
}

// ──────────────────────────────────────────────────────────────────
// Ticker bar chart (top 10 by total trades).
// ──────────────────────────────────────────────────────────────────

interface TickerBarProps { items: TickerStat[]; }

function TickerBars({ items }: TickerBarProps) {
  const top = [...items].sort((a, b) => b.total - a.total).slice(0, 10);
  if (top.length === 0) return null;
  const max = Math.max(...top.map((t) => t.total)) || 1;

  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
      {top.map((t) => (
        <li key={t.ticker} style={{ display: 'grid', gridTemplateColumns: '90px 1fr 56px', alignItems: 'center', gap: 8 }}>
          <span className="qd-num" style={{ fontSize: 12 }}>
            <b>{t.ticker}</b>
          </span>
          <div style={{ position: 'relative', height: 22, background: 'var(--qd-surface-2)', borderRadius: 4 }}>
            <div
              style={{
                position: 'absolute',
                top: 0, bottom: 0, left: 0,
                width: `${(t.total / max) * 100}%`,
                background: 'linear-gradient(90deg, var(--qd-blue) 0%, var(--qd-blue) 60%, var(--qd-amber) 100%)',
                borderRadius: 4,
                transition: 'width var(--qd-dur-slow) var(--qd-ease)',
              }}
            />
            <span style={{
              position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
              fontSize: 11, color: 'var(--qd-text)', mixBlendMode: 'normal',
            }}>
              매수 {t.buys} · 매도 {t.sells}
            </span>
          </div>
          <span className="qd-num" style={{ textAlign: 'right', fontSize: 12, fontWeight: 600 }}>
            {t.total}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ──────────────────────────────────────────────────────────────────
// Tab.
// ──────────────────────────────────────────────────────────────────

export default function ChartTab() {
  const [daily, setDaily] = useState<DailyPnl[] | null>(null);
  const [tickers, setTickers] = useState<TickerStat[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [hadError, setHadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    Promise.allSettled([
      fetchJson<DailyPnl[]>(adminApi.dailyPnl, { signal: ctrl.signal }),
      fetchJson<TickerStat[]>(adminApi.tickerStats, { signal: ctrl.signal }),
    ]).then((results) => {
      if (cancelled) return;
      const [d, t] = results;
      setDaily(d.status === 'fulfilled' ? (d.value ?? []) : []);
      setTickers(t.status === 'fulfilled' ? (t.value ?? []) : []);
      setHadError(d.status !== 'fulfilled' || t.status !== 'fulfilled');
      setLoading(false);
    });
    return () => { cancelled = true; ctrl.abort(); };
  }, []);

  const stats = useMemo(() => {
    const d = daily ?? [];
    const total = d.reduce((s, x) => s + x.pnl, 0);
    const positive = d.filter((x) => x.pnl > 0).length;
    const negative = d.filter((x) => x.pnl < 0).length;
    return { total, positive, negative, days: d.length };
  }, [daily]);

  if (loading) {
    return <div className="qd-skeleton qd-skeleton-card" style={{ height: 240 }} />;
  }

  return (
    <section data-tab="chart" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>차트</h2>
        <span className="qd-tab-meta">
          {stats.days}일 · 누적 <span className="qd-num"
            data-pnl={stats.total > 0 ? 'pos' : stats.total < 0 ? 'neg' : 'zero'}>
            {money(stats.total)}
          </span>
          · 양수 {stats.positive}일 / 음수 {stats.negative}일
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 일부 응답 누락</span>}
        </span>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 12 }} className="qd-stagger">
        <article className="qd-card">
          <header className="qd-card-h">일별 실현손익 (line)</header>
          {daily && daily.length > 0 ? (
            <DailyPnlChart data={daily} />
          ) : (
            <div style={{ padding: 16, textAlign: 'center', color: 'var(--qd-text-3)', fontSize: 12 }}>
              데이터 없음
            </div>
          )}
        </article>

        <article className="qd-card">
          <header className="qd-card-h">종목별 체결 횟수 (top 10)</header>
          {tickers && tickers.length > 0 ? (
            <TickerBars items={tickers} />
          ) : (
            <div style={{ padding: 16, textAlign: 'center', color: 'var(--qd-text-3)', fontSize: 12 }}>
              데이터 없음
            </div>
          )}
        </article>
      </div>
    </section>
  );
}
