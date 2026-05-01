// Phase F7 — Admin Screener tab. /api/screener (admin endpoint) read-only.

import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import { money } from '@shared/format/format';

interface ScreenerItem {
  ticker: string;
  name?: string | null;
  price?: number | null;
  score?: number | null;
  reasons?: string[] | string | null;
  is_domestic?: boolean | null;
  screened_at?: string | null;
}

interface ScreenerResponse {
  ok?: boolean;
  items?: ScreenerItem[];
  count?: number;
  updated_at?: string | null;
}

export default function ScreenerTab() {
  const [data, setData] = useState<ScreenerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [hadError, setHadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    async function tick() {
      try {
        const r = await fetchJson<ScreenerResponse>(adminApi.screener, { signal: ctrl.signal });
        if (cancelled) return;
        setData(r);
        setHadError(false);
      } catch {
        if (cancelled) return;
        setHadError(true);
      }
      if (!cancelled) setLoading(false);
    }
    tick();
    const t = setInterval(tick, 60_000);
    return () => { cancelled = true; clearInterval(t); ctrl.abort(); };
  }, []);

  const items = (data?.items ?? []).slice().sort((a, b) => (b.score ?? 0) - (a.score ?? 0));

  return (
    <section data-tab="screener" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>스크리너 (admin)</h2>
        <span className="qd-tab-meta">
          {items.length}건 · {data?.updated_at ? new Date(data.updated_at).toLocaleTimeString('ko-KR') : '—'}
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 갱신 실패</span>}
        </span>
      </header>

      {loading && !data ? (
        <div className="qd-skeleton qd-skeleton-card" style={{ height: 200 }} />
      ) : items.length === 0 ? (
        <div className="qd-tab-stub">
          <h2>표시할 종목 없음</h2>
          <p>스크리너 결과가 없거나 점수 70 이상 종목이 비어있습니다.</p>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="qd-tab-table">
            <thead>
              <tr>
                <th>티커</th>
                <th>종목명</th>
                <th>시장</th>
                <th>점수</th>
                <th>가격</th>
                <th>사유</th>
              </tr>
            </thead>
            <tbody>
              {items.slice(0, 200).map((it) => {
                const reasons = Array.isArray(it.reasons) ? it.reasons.join(' · ')
                              : typeof it.reasons === 'string' ? it.reasons : '';
                const tone = (it.score ?? 0) >= 80 ? 'fail' : (it.score ?? 0) >= 75 ? 'warn' : 'ok';
                return (
                  <tr key={it.ticker}>
                    <td className="qd-num"><b>{it.ticker}</b></td>
                    <td>{it.name ?? '—'}</td>
                    <td style={{ color: 'var(--qd-text-3)', fontSize: 12 }}>
                      {it.is_domestic ? '국내' : '해외'}
                    </td>
                    <td className="qd-num" data-tone={tone}>
                      {it.score != null ? it.score.toFixed(1) : '—'}
                    </td>
                    <td className="qd-num">{money(it.price ?? null)}</td>
                    <td style={{ color: 'var(--qd-text-2)' }}>{reasons || '—'}</td>
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
