// Phase F7 — Settings tab. /api/config read-only display. 설정 변경 0건 (read-only).

import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';

interface ConfigResponse {
  ok?: boolean;
  risk?: Record<string, unknown>;
  long_risk?: Record<string, unknown>;
  schedule?: Record<string, unknown>;
  daytrade_ml?: Record<string, unknown>;
}

function fmtVal(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? '✓' : '·';
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString('ko-KR') : v.toFixed(3);
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '…' : v;
  try { return JSON.stringify(v).slice(0, 80); } catch { return String(v); }
}

function ConfigCard({ title, data }: { title: string; data: Record<string, unknown> | undefined }) {
  if (!data || Object.keys(data).length === 0) return null;
  const entries = Object.entries(data).slice(0, 30);
  return (
    <article className="qd-card">
      <header className="qd-card-h">{title}</header>
      <ul className="qd-card-list">
        {entries.map(([k, v]) => (
          <li key={k}>
            <span style={{ color: 'var(--qd-text-3)', fontFamily: 'var(--qd-font-num)', fontSize: 11 }}>{k}</span>
            <span className="qd-num">{fmtVal(v)}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export default function SettingsTab() {
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [hadError, setHadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    fetchJson<ConfigResponse>(adminApi.config, { signal: ctrl.signal })
      .then((r) => { if (!cancelled) { setData(r); setLoading(false); } })
      .catch(() => { if (!cancelled) { setHadError(true); setLoading(false); } });
    return () => { cancelled = true; ctrl.abort(); };
  }, []);

  if (loading && !data) {
    return <div className="qd-skeleton qd-skeleton-card" style={{ height: 200 }} />;
  }

  return (
    <section data-tab="settings" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>설정 (read-only)</h2>
        <span className="qd-tab-meta">
          /api/config — 변경은 user_config.json 수동 편집 후 main 재시작
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 갱신 실패</span>}
        </span>
      </header>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }} className="qd-stagger">
        <ConfigCard title="Risk" data={data?.risk as Record<string, unknown> | undefined} />
        <ConfigCard title="Long Risk" data={data?.long_risk as Record<string, unknown> | undefined} />
        <ConfigCard title="Schedule" data={data?.schedule as Record<string, unknown> | undefined} />
        <ConfigCard title="Daytrade ML" data={data?.daytrade_ml as Record<string, unknown> | undefined} />
      </div>
      <p style={{ marginTop: 12, fontSize: 12, color: 'var(--qd-text-3)' }}>
        본 화면은 표시 전용입니다. 운영 정책 변경은 user_config.json 또는 .env 를 직접 수정하고
        main.py 를 수동 재시작해야 합니다.
      </p>
    </section>
  );
}
