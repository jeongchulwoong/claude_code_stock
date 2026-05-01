// Phase F4 — RiskEventList (presentational).
import type { CompressedGroup, NormalizedRiskEvent } from './riskEventModel';

export interface RiskEventListProps {
  groups: CompressedGroup[];
  filter: { category: 'all' | string; ticker?: string };
  onToggleFilter: (category: string) => void;
  onTickerChange: (ticker: string) => void;
  onExpandGroup: (key: string) => void;
}

interface CategoryTag { cls: string; text: string; }

const TAG_MAP: Record<string, CategoryTag> = {
  order_error:              { cls: 'et-tag--err',   text: 'FAIL' },
  cancel_failed:            { cls: 'et-tag--cxl',   text: 'CXL-FAIL' },
  buy_blocked:              { cls: 'et-tag--block', text: 'BLOCK' },
  duplicate_order_blocked:  { cls: 'et-tag--block', text: 'DUP' },
  stop_loss:                { cls: 'et-tag--err',   text: 'STOP' },
  take_profit:              { cls: 'et-tag--ok',    text: 'TP' },
  time_stop:                { cls: 'et-tag--block', text: 'TIME' },
  force_close:              { cls: 'et-tag--err',   text: 'FORCE' },
  daily_loss_warning:       { cls: 'et-tag--err',   text: 'LOSS' },
  consecutive_loss_halt:    { cls: 'et-tag--err',   text: 'CHALT' },
  ord_alow_insufficient:    { cls: 'et-tag--block', text: 'CASH' },
  ml_score:                 { cls: 'et-tag--ok',    text: 'ML' },
};

const FILTER_BUTTONS: Array<{ filter: 'all' | string; label: string }> = [
  { filter: 'all',                     label: '전체' },
  { filter: 'buy_blocked',             label: '매수차단' },
  { filter: 'order_error',             label: '주문오류' },
  { filter: 'stop_loss',               label: '손절' },
  { filter: 'take_profit',             label: '익절' },
  { filter: 'time_stop',               label: '시간청산' },
  { filter: 'force_close',             label: '강제청산' },
  { filter: 'duplicate_order_blocked', label: '중복차단' },
  { filter: 'daily_loss_warning',      label: '손실한도' },
  { filter: 'consecutive_loss_halt',   label: '연속손실' },
  { filter: 'ml_score',                label: 'ML' },
];

function tagFor(category: string): CategoryTag {
  return TAG_MAP[category] ?? { cls: 'et-tag--block', text: 'EVT' };
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

function EventRow({ e }: { e: NormalizedRiskEvent }) {
  const tag = tagFor(e.category);
  const cls = e.metaReason ? 'event-item event-item--with-meta' : 'event-item';
  return (
    <li className={cls} data-level={e.level}>
      <span className="et-time qd-num">{fmtTime(e.time)}</span>
      <span className="et-msg">
        {e.ticker !== '-' && <b>{e.ticker}</b>}
        {e.ticker !== '-' && ' · '}
        {e.message || '-'}
      </span>
      <span className={`et-tag ${tag.cls}`}>{tag.text}</span>
      {e.metaReason && <span className="et-meta">↳ {e.metaReason}</span>}
    </li>
  );
}

function RollupRow({ group, onExpand }: { group: CompressedGroup; onExpand: (k: string) => void }) {
  const e = group.first;
  const tag = tagFor(e.category);
  const reason = e.metaReason || e.message || '-';
  return (
    <li className="event-item event-item--rollup" data-level={e.level}>
      <span className="et-time qd-num">{fmtTime(e.time)}</span>
      <span className="et-msg">
        {e.ticker !== '-' && <b>{e.ticker}</b>}
        {e.ticker !== '-' && ' · '}
        {reason} · 오늘 {group.count}회 반복
      </span>
      <span className={`et-tag ${tag.cls}`}>{tag.text}</span>
      <button
        type="button"
        className="event-filter et-expand"
        data-risk-expand={group.key}
        onClick={() => onExpand(group.key)}
      >
        펼치기
      </button>
    </li>
  );
}

export default function RiskEventList(props: RiskEventListProps) {
  const { groups, filter, onToggleFilter, onTickerChange, onExpandGroup } = props;
  return (
    <section data-island="admin-risk-events" data-phase="F4" aria-label="위험 이벤트 (read-only)">
      <div className="event-filters" data-role="risk-events-filters" role="tablist" aria-label="위험 이벤트 필터">
        {FILTER_BUTTONS.map((b) => {
          const active = filter.category === b.filter;
          return (
            <button
              key={b.filter}
              type="button"
              className={active ? 'event-filter is-active' : 'event-filter'}
              data-filter={b.filter}
              role="tab"
              aria-selected={active ? 'true' : 'false'}
              onClick={() => onToggleFilter(b.filter)}
            >
              {b.label}
            </button>
          );
        })}
      </div>
      <input
        type="search"
        className="rt-filter-input"
        data-role="risk-events-ticker"
        placeholder="종목 검색 (예: 005930)"
        value={filter.ticker ?? ''}
        onChange={(e) => onTickerChange(e.target.value)}
        aria-label="종목 검색"
      />
      {groups.length === 0 ? (
        <ul className="event-list" data-role="risk-events">
          <li className="event-empty">위험 이벤트 없음</li>
        </ul>
      ) : (
        <ul className="event-list" data-role="risk-events">
          {groups.map((g) => {
            if (g.display === 'rollup') return <RollupRow key={g.key} group={g} onExpand={onExpandGroup} />;
            return g.items.map((e, idx) => <EventRow key={`${g.key}-${idx}`} e={e} />);
          })}
        </ul>
      )}
      <div className="event-retention" data-role="risk-events-retention">최근 90일 보관</div>
    </section>
  );
}
