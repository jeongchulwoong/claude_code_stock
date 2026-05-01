// Phase F8 — Strategy stats tab. /api/strategy_stats — 전략별 신호/체결/승률/누적손익.

import { useEffect, useMemo, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import { money } from '@shared/format/format';

interface StrategyRow {
  signals?: number;
  executed?: number;
  win_rate?: number;
  total_pnl?: number;
  best_pnl?: number;
  worst_pnl?: number;
  avg_hold?: number;
}

type StatsResponse = Record<string, StrategyRow>;

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v.toFixed(1)}%`;
}

function fmtNum(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return Math.round(v).toLocaleString('ko-KR');
}

function pnlTone(v: number | null | undefined): 'pos' | 'neg' | 'zero' {
  if (v == null || !Number.isFinite(v) || v === 0) return 'zero';
  return v > 0 ? 'pos' : 'neg';
}

export default function StrategiesTab() {
  const [data, setData] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [hadError, setHadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    fetchJson<StatsResponse>(adminApi.strategyStats, { signal: ctrl.signal })
      .then((r) => { if (!cancelled) { setData(r ?? {}); setLoading(false); } })
      .catch(() => { if (!cancelled) { setHadError(true); setLoading(false); } });
    return () => { cancelled = true; ctrl.abort(); };
  }, []);

  const rows = useMemo(() => {
    if (!data) return [] as Array<{ strategy: string; row: StrategyRow }>;
    return Object.entries(data)
      .map(([strategy, row]) => ({ strategy, row }))
      .sort((a, b) => (b.row.total_pnl ?? 0) - (a.row.total_pnl ?? 0));
  }, [data]);

  // 전체 합계.
  const totals = useMemo(() => {
    let signals = 0, executed = 0, total_pnl = 0;
    for (const { row } of rows) {
      signals += row.signals ?? 0;
      executed += row.executed ?? 0;
      total_pnl += row.total_pnl ?? 0;
    }
    return { signals, executed, total_pnl, count: rows.length };
  }, [rows]);

  if (loading && !data) {
    return <div className="qd-skeleton qd-skeleton-card" style={{ height: 200 }} />;
  }

  return (
    <section data-tab="strategies" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>전략 성과</h2>
        <span className="qd-tab-meta">
          {totals.count} 전략 · 신호 {fmtNum(totals.signals)} · 체결 {fmtNum(totals.executed)}
          · 누적 <span className="qd-num" data-pnl={pnlTone(totals.total_pnl)}>{money(totals.total_pnl)}</span>
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 갱신 실패</span>}
        </span>
      </header>

      {rows.length === 0 ? (
        <div className="qd-tab-stub">
          <h2>전략 통계 없음</h2>
          <p>아직 누적된 전략 신호/체결이 없습니다.</p>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="qd-tab-table">
            <thead>
              <tr>
                <th>전략</th>
                <th>신호</th>
                <th>체결</th>
                <th>실행률</th>
                <th>승률</th>
                <th>누적 손익</th>
                <th>최고</th>
                <th>최악</th>
                <th>평균 보유</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ strategy, row }) => {
                const execRate = (row.signals ?? 0) > 0
                  ? ((row.executed ?? 0) / (row.signals ?? 1)) * 100
                  : 0;
                return (
                  <tr key={strategy}>
                    <td><b>{strategy}</b></td>
                    <td className="qd-num">{fmtNum(row.signals)}</td>
                    <td className="qd-num">{fmtNum(row.executed)}</td>
                    <td className="qd-num">{fmtPct(execRate)}</td>
                    <td className="qd-num" data-tone={(row.win_rate ?? 0) >= 50 ? 'ok' : 'muted'}>
                      {fmtPct(row.win_rate)}
                    </td>
                    <td className="qd-num" data-pnl={pnlTone(row.total_pnl)}>
                      {money(row.total_pnl ?? null)}
                    </td>
                    <td className="qd-num" data-pnl="pos">{money(row.best_pnl ?? null)}</td>
                    <td className="qd-num" data-pnl="neg">{money(row.worst_pnl ?? null)}</td>
                    <td className="qd-num">{row.avg_hold != null ? `${row.avg_hold.toFixed(1)}일` : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
