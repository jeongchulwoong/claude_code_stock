// Phase F4 — Realtime section (presentational only).
import type { NormalizedRiskEvent } from '../riskEvents/riskEventModel';
import type { RealtimeBucket } from './realtimeModel';

export interface RealtimeSectionProps {
  title: string;
  bucket: RealtimeBucket;
  events: NormalizedRiskEvent[];
}

function fmtTime(time: string): string {
  if (!time) return '—';
  const d = new Date(time);
  if (Number.isNaN(d.getTime())) return time;
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

const EMPTY_TEXT: Record<RealtimeBucket, string> = {
  critical: 'Critical 이벤트 없음',
  orders:   '주문 이벤트 없음',
  risk:     '리스크 이벤트 없음',
  system:   '시스템 이벤트 없음',
};

export default function RealtimeSection({ title, bucket, events }: RealtimeSectionProps) {
  return (
    <section
      className={`rt-section${bucket === 'critical' ? ' rt-section--critical' : ''}`}
      data-rt-bucket={bucket}
      aria-label={`Realtime ${bucket} 섹션`}
    >
      <header className="rt-section-header">
        <span className="rt-section-title">{title}</span>
        <span className="rt-section-count qd-num" data-rt-count={bucket}>
          {events.length}
        </span>
      </header>
      {events.length === 0 ? (
        <ul className="event-list rt-list">
          <li className="event-empty">{EMPTY_TEXT[bucket]}</li>
        </ul>
      ) : (
        <ul className="event-list rt-list">
          {events.map((e, idx) => (
            <li key={`${bucket}-${idx}`} className="event-item" data-level={e.level}>
              <span className="et-time qd-num">{fmtTime(e.time)}</span>
              <span className="et-msg">
                {e.ticker !== '-' && <b>{e.ticker}</b>}
                {e.ticker !== '-' && ' · '}
                {e.message || '-'}
              </span>
              <span className="et-tag">{e.category.toUpperCase().slice(0, 6)}</span>
              {e.metaReason && <span className="et-meta">↳ {e.metaReason}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
