import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import App from './App';

const NO_POLL = { enabled: false } as const;
const NOW = Date.parse('2026-04-01T09:30:00');
const ISLAND_PROPS = { pollingOptions: NO_POLL, nowProvider: () => NOW };

describe('Client App (Phase F5)', () => {
  it('data-app="client" + data-phase="F5"', () => {
    const { container } = render(<App islandProps={ISLAND_PROPS} />);
    const root = container.querySelector('[data-app="client"]');
    expect(root?.getAttribute('data-phase')).toBe('F5');
    expect(root?.getAttribute('data-island')).toBe('client-screener');
  });

  it('ClientScreenerIsland 렌더', () => {
    const { container } = render(<App islandProps={ISLAND_PROPS} />);
    expect(container.querySelector('.qd-screener')).not.toBeNull();
  });

  it('금지 wording 0건', () => {
    const { container } = render(<App islandProps={ISLAND_PROPS} />);
    const html = container.innerHTML;
    for (const banned of ['매수', '매도', '주문', '체결', '계좌', '잔고', '보유', '수익률', '평가금액']) {
      expect(html.includes(banned)).toBe(false);
    }
  });
});
