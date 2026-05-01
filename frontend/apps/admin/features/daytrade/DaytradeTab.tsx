// Phase F7 — 분단타 control snapshot tab. /api/admin/minute_daytrade read-only.

import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import { money, moneyShort } from '@shared/format/format';

interface MinuteDaytradeResponse {
  ok?: boolean;
  mode?: string;
  description?: string;
  buying_power?: number;
  capital_limit?: number;
  daytrade_bucket?: number;
  max_invest_per_trade?: number;
  max_one_share_price?: number;
  scan_interval_minutes?: number;
  entry_start?: string;
  entry_end?: string;
  no_hold_after?: string;
  force_close_time?: string;
  max_daily_entries?: number;
  min_confidence?: number;
  min_strategies?: number;
  min_volume_ratio?: number;
  rsi_min?: number;
  rsi_max?: number;
  min_price?: number;
  cash_buffer_pct?: number;
  slippage_buffer_pct?: number;
  stop_loss_pct?: number;
  take_profit_pct?: number;
  time_stop_soft_min?: number;
  time_stop_hard_min?: number;
  requires_restart?: boolean;
}

interface Row { label: string; value: string; }

function pct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${(v * 100).toFixed(digits)}%`;
}

function toRows(d: MinuteDaytradeResponse): { capital: Row[]; signal: Row[]; time: Row[]; pnl: Row[] } {
  return {
    capital: [
      { label: '매수가능',           value: money(d.buying_power ?? null) },
      { label: '단타 자본 한도',     value: money(d.capital_limit ?? null) },
      { label: '1회 단타 버킷',      value: money(d.daytrade_bucket ?? null) },
      { label: '1주 가능 상한',      value: money(d.max_one_share_price ?? null) },
      { label: '1회 절대 상한',      value: money(d.max_invest_per_trade ?? null) },
    ],
    time: [
      { label: '스캔 주기',          value: `${d.scan_interval_minutes ?? '—'} 분` },
      { label: '진입 시작',          value: d.entry_start ?? '—' },
      { label: '진입 종료',          value: d.entry_end ?? '—' },
      { label: '신규 보유 금지',     value: d.no_hold_after ?? '—' },
      { label: '강제 청산',          value: d.force_close_time ?? '—' },
    ],
    signal: [
      { label: '하루 진입 한도',     value: `${d.max_daily_entries ?? '—'} 회` },
      { label: 'AI 신뢰도 하한',     value: d.min_confidence != null ? `${d.min_confidence}점` : '—' },
      { label: '전략 통과 최소',     value: d.min_strategies != null ? `${d.min_strategies}개` : '—' },
      { label: '거래량비율 ≥',       value: d.min_volume_ratio != null ? `${d.min_volume_ratio.toFixed(1)}x` : '—' },
      { label: 'RSI 범위',           value: d.rsi_min != null && d.rsi_max != null ? `${d.rsi_min}~${d.rsi_max}` : '—' },
      { label: '저가 필터',          value: money(d.min_price ?? null) },
    ],
    pnl: [
      { label: '손절 비율',          value: pct(d.stop_loss_pct, 2) },
      { label: '익절 비율',          value: pct(d.take_profit_pct, 2) },
      { label: 'Soft 시간청산',      value: `${d.time_stop_soft_min ?? '—'} 분` },
      { label: 'Hard 시간청산',      value: `${d.time_stop_hard_min ?? '—'} 분` },
      { label: '현금 버퍼',          value: pct(d.cash_buffer_pct, 1) },
      { label: '슬리피지 버퍼',      value: pct(d.slippage_buffer_pct, 2) },
    ],
  };
}

function Group({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <article className="qd-card">
      <header className="qd-card-h">{title}</header>
      <ul className="qd-card-list">
        {rows.map((r) => (
          <li key={r.label}>
            <span style={{ color: 'var(--qd-text-3)' }}>{r.label}</span>
            <span className="qd-num">{r.value}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export default function DaytradeTab() {
  const [data, setData] = useState<MinuteDaytradeResponse | null>(null);
  const [hadError, setHadError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    async function tick() {
      try {
        const r = await fetchJson<MinuteDaytradeResponse>(adminApi.minuteDaytrade, { signal: ctrl.signal });
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
    const t = setInterval(tick, 30_000);
    return () => { cancelled = true; clearInterval(t); ctrl.abort(); };
  }, []);

  if (loading && !data) {
    return <div className="qd-skeleton qd-skeleton-card" style={{ height: 220 }} />;
  }
  if (!data) {
    return (
      <div className="qd-tab-stub">
        <h2>분단타 데이터 없음</h2>
        <p>응답을 받지 못했습니다. 잠시 후 다시 시도하세요.</p>
      </div>
    );
  }

  const groups = toRows(data);
  return (
    <section data-tab="daytrade" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>분단타 운영 콘솔</h2>
        <span className="qd-tab-meta">
          {data.mode ?? '—'} · 매수가능 <span className="qd-num">{moneyShort(data.buying_power ?? null)}</span>
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 갱신 실패</span>}
        </span>
      </header>
      {data.description && (
        <p style={{ margin: '0 0 12px 0', color: 'var(--qd-text-3)', fontSize: 12 }}>{data.description}</p>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }} className="qd-stagger">
        <Group title="자금" rows={groups.capital} />
        <Group title="시간" rows={groups.time} />
        <Group title="신호 / 정책" rows={groups.signal} />
        <Group title="손익 / 시간청산" rows={groups.pnl} />
      </div>
      {data.requires_restart && (
        <p style={{ marginTop: 12, fontSize: 12, color: 'var(--qd-amber)' }}>
          정책 변경 적용은 main.py 재시작이 필요합니다 (수동).
        </p>
      )}
    </section>
  );
}
