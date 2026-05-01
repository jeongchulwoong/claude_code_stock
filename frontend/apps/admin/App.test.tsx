// Phase F3 — Admin App 회귀 테스트.
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import App from './App';

const NO_POLL = { enabled: false } as const;

describe('Admin App (Phase F3 island wrapper)', () => {
  it('data-phase="F3" + data-app="admin" 마커 노출', () => {
    const { container } = render(<App overviewPollingOptions={NO_POLL} />);
    const root = container.querySelector('[data-app="admin"]');
    expect(root).not.toBeNull();
    expect(root?.getAttribute('data-phase')).toBe('F3');
  });

  it('AdminOverviewIsland 렌더 (data-island="admin-overview")', () => {
    const { container } = render(<App overviewPollingOptions={NO_POLL} />);
    expect(container.querySelector('[data-island="admin-overview"]')).not.toBeNull();
  });

  it('매수/매도/주문 실행 트리거 0건', () => {
    const { container } = render(<App overviewPollingOptions={NO_POLL} />);
    const html = container.innerHTML;
    for (const banned of ['매수 실행', '매도 실행', '주문 실행', 'placeOrder', 'buyNow', 'sellNow']) {
      expect(html.includes(banned)).toBe(false);
    }
  });
});
