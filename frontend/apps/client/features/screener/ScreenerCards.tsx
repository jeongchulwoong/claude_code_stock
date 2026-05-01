// Phase F5 — 공개 스크리너 카드 (presentational).
import { SCORE_HIGHLIGHT, type MarketFilter, type NormalizedItem } from './screenerModel';

export interface ScreenerCardsProps {
  items: NormalizedItem[];
  market: MarketFilter;
  onMarketChange: (m: MarketFilter) => void;
  search: string;
  onSearchChange: (q: string) => void;
  onRefresh?: () => void;
  isStale: boolean;
  updatedAt: string | null;
  hadError: boolean;
}

const MARKET_BUTTONS: Array<{ value: MarketFilter; label: string }> = [
  { value: 'all',      label: '전체' },
  { value: 'domestic', label: '국내' },
  { value: 'foreign',  label: '해외' },
];

function fmtPrice(n: number | null): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1000) return Math.round(n).toLocaleString('ko-KR');
  return n.toFixed(2);
}

function fmtScore(n: number | null): string {
  if (n == null) return '—';
  return n.toFixed(1);
}

function fmtUpdated(s: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function Card({ item }: { item: NormalizedItem }) {
  const highlight = (item.score ?? 0) >= SCORE_HIGHLIGHT;
  return (
    <article
      className={`screener-card${highlight ? ' screener-card--high' : ''}`}
      data-ticker={item.ticker}
      data-domestic={String(item.isDomestic)}
    >
      <header className="screener-card-head">
        <span className="screener-ticker qd-num">{item.ticker}</span>
        <span className="screener-market">{item.isDomestic ? '국내' : '해외'}</span>
      </header>
      <div className="screener-name">{item.name || '—'}</div>
      <div className="screener-meta">
        <span className="screener-score qd-num" data-highlight={String(highlight)}>
          점수 {fmtScore(item.score)}
        </span>
        <span className="screener-price qd-num">{fmtPrice(item.price)}</span>
      </div>
      {item.reasons.length > 0 && (
        <ul className="screener-reasons">
          {item.reasons.map((r, i) => (
            <li key={i} className="screener-reason">{r}</li>
          ))}
        </ul>
      )}
    </article>
  );
}

export default function ScreenerCards(props: ScreenerCardsProps) {
  const { items, market, onMarketChange, search, onSearchChange, onRefresh, isStale, updatedAt, hadError } = props;
  return (
    <section className="qd-screener" aria-label="공개 스크리너">
      <header className="screener-header">
        <h2 className="screener-title">공개 스크리너</h2>
        <span className="screener-updated qd-num" data-updated-at={updatedAt ?? ''}>
          최근 갱신 {fmtUpdated(updatedAt)}
        </span>
      </header>

      {(isStale || hadError) && (
        <div
          className="screener-banner"
          data-stale={String(isStale)}
          data-error={String(hadError)}
          role="status"
          aria-live="polite"
        >
          {hadError ? '데이터 갱신 실패 — 잠시 후 다시 시도하세요.' : '데이터 지연 — 마지막 갱신이 10분을 넘었습니다.'}
        </div>
      )}

      <div className="screener-controls">
        <div className="screener-segment" role="tablist" aria-label="시장 분류">
          {MARKET_BUTTONS.map((b) => {
            const active = market === b.value;
            return (
              <button
                key={b.value}
                type="button"
                className={active ? 'screener-tab is-active' : 'screener-tab'}
                data-market={b.value}
                role="tab"
                aria-selected={active ? 'true' : 'false'}
                onClick={() => onMarketChange(b.value)}
              >
                {b.label}
              </button>
            );
          })}
        </div>
        <input
          type="search"
          className="screener-search"
          data-role="screener-search"
          placeholder="종목 검색"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          aria-label="종목 검색"
        />
        {onRefresh && (
          <button
            type="button"
            className="screener-refresh"
            data-role="screener-refresh"
            onClick={onRefresh}
            aria-label="새로고침"
          >
            새로고침
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <div className="screener-empty">표시할 종목이 없습니다 (점수 70 이상 기준).</div>
      ) : (
        <div className="screener-grid" data-count={items.length}>
          {items.map((it) => <Card key={it.ticker} item={it} />)}
        </div>
      )}

      <footer className="screener-disclaimer">
        참고용 후보 정보입니다. 조건 통과 후보이며, 투자 판단은 본인 책임입니다.
      </footer>
    </section>
  );
}
