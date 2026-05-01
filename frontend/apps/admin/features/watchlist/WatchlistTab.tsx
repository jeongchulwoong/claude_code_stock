// Phase F8 — Watchlist tab. /api/config 의 watch_names / long_watch_names / priority_watch_names
// read-only 표시. 편집 (POST /api/config) 은 후속 phase 에서 별도 권한 게이트와 함께 진행.

import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';

interface ConfigPayload {
  ok?: boolean;
  watch_names?: string[];
  long_watch_names?: string[];
  priority_watch_names?: string[];
  foreign_watch_names?: string[];
  [key: string]: unknown;
}

interface ListProps {
  title: string;
  hint: string | null;
  items: string[];
}

function WatchList({ title, hint, items }: ListProps) {
  return (
    <article className="qd-card">
      <header className="qd-card-h">
        {title}
        <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--qd-text-3)' }}>
          ({items.length}종)
        </span>
      </header>
      {hint && <p style={{ margin: '0 0 8px 0', fontSize: 11, color: 'var(--qd-text-3)' }}>{hint}</p>}
      {items.length === 0 ? (
        <div style={{ padding: 16, textAlign: 'center', color: 'var(--qd-text-3)', fontSize: 12 }}>
          비어 있음
        </div>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {items.slice(0, 200).map((name) => (
            <li
              key={name}
              className="qd-num"
              style={{
                fontSize: 12,
                padding: '3px 8px',
                background: 'var(--qd-surface-2)',
                color: 'var(--qd-text-2)',
                borderRadius: 4,
              }}
            >
              {name}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export default function WatchlistTab() {
  const [data, setData] = useState<ConfigPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [hadError, setHadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    fetchJson<ConfigPayload>(adminApi.config, { signal: ctrl.signal })
      .then((r) => { if (!cancelled) { setData(r); setLoading(false); } })
      .catch(() => { if (!cancelled) { setHadError(true); setLoading(false); } });
    return () => { cancelled = true; ctrl.abort(); };
  }, []);

  if (loading && !data) {
    return <div className="qd-skeleton qd-skeleton-card" style={{ height: 200 }} />;
  }

  const watch    = Array.isArray(data?.watch_names) ? data!.watch_names : [];
  const longw    = Array.isArray(data?.long_watch_names) ? data!.long_watch_names : [];
  const priority = Array.isArray(data?.priority_watch_names) ? data!.priority_watch_names : [];
  const foreign  = Array.isArray(data?.foreign_watch_names) ? data!.foreign_watch_names : [];

  return (
    <section data-tab="watchlist" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>Watchlist</h2>
        <span className="qd-tab-meta">
          read-only 표시 · 편집은 user_config.json 직접 수정 후 main 재시작
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 갱신 실패</span>}
        </span>
      </header>

      <div
        style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}
        className="qd-stagger"
      >
        <WatchList
          title="Priority (분단타 1분 스캔)"
          hint="user_config.priority_watch_names — 가장 자주 스캔되는 종목"
          items={priority}
        />
        <WatchList
          title="Watch (단타 후보)"
          hint="user_config.watch_names — 단타 일반 후보"
          items={watch}
        />
        <WatchList
          title="Long Watch (장투 후보)"
          hint="user_config.long_watch_names — 장투 후보"
          items={longw}
        />
        <WatchList
          title="Foreign Watch (해외)"
          hint="user_config.foreign_watch_names — 해외 종목"
          items={foreign}
        />
      </div>

      <p style={{ marginTop: 12, fontSize: 12, color: 'var(--qd-text-3)' }}>
        본 화면은 표시 전용입니다. 운영 watchlist 변경은 user_config.json 직접 수정 후
        main.py 재시작이 필요합니다 (안전 정책).
      </p>
    </section>
  );
}
