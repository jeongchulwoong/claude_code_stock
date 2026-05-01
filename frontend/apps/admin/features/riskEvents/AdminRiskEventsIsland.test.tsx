import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import AdminRiskEventsIsland from './AdminRiskEventsIsland';
import type { RiskEventsSnapshot } from './useRiskEventsPolling';

const T0 = '2026-04-01T09:30:00';

function snap(overrides: Partial<RiskEventsSnapshot>): RiskEventsSnapshot {
  return {
    riskEvents: null, orders: null,
    fetchedAt: 1_700_000_000_000, hadError: false, ...overrides,
  };
}

const POLL_OFF = { enabled: false, intervalMs: 0 } as const;

describe('AdminRiskEventsIsland (F4)', () => {
  it('F4 마커', () => {
    const { container } = render(
      <AdminRiskEventsIsland snapshot={snap({})} pollingOptions={POLL_OFF} />,
    );
    const root = container.querySelector('[data-island="admin-risk-events"]');
    expect(root?.getAttribute('data-phase')).toBe('F4');
  });

  it('빈 응답 → "위험 이벤트 없음"', () => {
    const { container } = render(
      <AdminRiskEventsIsland snapshot={snap({})} pollingOptions={POLL_OFF} />,
    );
    expect(container.querySelector('.event-empty')?.textContent).toContain('위험 이벤트 없음');
  });

  it('critical event → 첫 번째 행', () => {
    const { container } = render(
      <AdminRiskEventsIsland
        snapshot={snap({
          riskEvents: { ok: true, events: [
            { id: 1, ts: T0, level: 'warn', category: 'buy_blocked', ticker: '005930.KS', message: 'RSI', meta: { reason: '버퍼' } },
            { id: 2, ts: T0, level: 'critical', category: 'order_error', ticker: '035720.KS', message: '치명' },
          ] },
        })}
        pollingOptions={POLL_OFF}
      />,
    );
    const items = container.querySelectorAll('.event-item');
    expect(items[0]?.getAttribute('data-level')).toBe('critical');
  });

  it('동일 group 3건 → "오늘 3회 반복" + 펼치기', () => {
    const events = [1, 2, 3].map((i) => ({
      id: i, ts: T0, level: 'warn' as const, category: 'buy_blocked',
      ticker: '005930.KS', message: 'RSI', meta: { reason: '버퍼' },
    }));
    const { container } = render(
      <AdminRiskEventsIsland snapshot={snap({ riskEvents: { ok: true, events } })} pollingOptions={POLL_OFF} />,
    );
    const rollup = container.querySelector('.event-item--rollup');
    expect(rollup?.textContent).toContain('오늘 3회 반복');
    expect(container.querySelector('button[data-risk-expand]')).not.toBeNull();
  });

  it('펼치기 클릭 → 3건 노출', () => {
    const events = [1, 2, 3].map((i) => ({
      id: i, ts: T0, level: 'warn' as const, category: 'buy_blocked',
      ticker: 'X', message: 'r', meta: { reason: '버퍼' },
    }));
    const { container } = render(
      <AdminRiskEventsIsland snapshot={snap({ riskEvents: { ok: true, events } })} pollingOptions={POLL_OFF} />,
    );
    const btn = container.querySelector('button[data-risk-expand]') as HTMLButtonElement;
    fireEvent.click(btn);
    expect(container.querySelector('.event-item--rollup')).toBeNull();
    expect(container.querySelectorAll('.event-item').length).toBe(3);
  });

  it('카테고리 필터 → 다른 카테고리 숨김', () => {
    const { container } = render(
      <AdminRiskEventsIsland
        snapshot={snap({ riskEvents: { ok: true, events: [
          { id: 1, ts: T0, level: 'warn',  category: 'buy_blocked', ticker: '005930.KS', message: 'a', meta: { reason: 'r1' } },
          { id: 2, ts: T0, level: 'error', category: 'order_error', ticker: '035720.KS', message: 'b' },
        ] } })}
        pollingOptions={POLL_OFF}
      />,
    );
    expect(container.querySelectorAll('.event-item').length).toBe(2);
    const btn = container.querySelector('button[data-filter="buy_blocked"]') as HTMLButtonElement;
    fireEvent.click(btn);
    expect(container.querySelectorAll('.event-item').length).toBe(1);
  });

  it('ticker 검색 → 매칭 행만', () => {
    const { container } = render(
      <AdminRiskEventsIsland
        snapshot={snap({ riskEvents: { ok: true, events: [
          { id: 1, ts: T0, level: 'warn', category: 'buy_blocked', ticker: '005930.KS', message: 'a', meta: { reason: 'r1' } },
          { id: 2, ts: T0, level: 'warn', category: 'buy_blocked', ticker: '035720.KS', message: 'b', meta: { reason: 'r2' } },
        ] } })}
        pollingOptions={POLL_OFF}
      />,
    );
    const search = container.querySelector('input[data-role="risk-events-ticker"]') as HTMLInputElement;
    fireEvent.change(search, { target: { value: '0059' } });
    expect(container.querySelectorAll('.event-item').length).toBe(1);
  });

  it('orders BLOCKED/ERROR/CANCEL_FAILED 도 표시', () => {
    const { container } = render(
      <AdminRiskEventsIsland
        snapshot={snap({ orders: { ok: true, orders: [
          { status: 'FILLED',  ticker: '005930.KS', timestamp: T0 },
          { status: 'BLOCKED', ticker: '035720.KS', timestamp: T0, reason: 'risk halt' },
          { status: 'ERROR',   ticker: '068270.KS', timestamp: T0, reject_msg: 'rc=-1' },
        ] } })}
        pollingOptions={POLL_OFF}
      />,
    );
    expect(container.querySelectorAll('.event-item').length).toBe(2);
  });

  it('account_no/token 등 민감값 미노출', () => {
    const { container } = render(
      <AdminRiskEventsIsland
        snapshot={snap({ riskEvents: { ok: true, events: [{
          id: 1, ts: T0, level: 'warn', category: 'order_error', ticker: '005930.KS', message: '주문실패',
          meta: { account_no: '1234567890', token: 'abcd', appkey: 'secret-xyz', reason: '버퍼 부족' },
        }] } })}
        pollingOptions={POLL_OFF}
      />,
    );
    const html = container.innerHTML;
    expect(html).not.toContain('1234567890');
    expect(html).not.toContain('abcd');
    expect(html).not.toContain('secret-xyz');
    expect(html).toContain('버퍼 부족');
  });

  it('주문 실행 트리거 0건', () => {
    const { container } = render(
      <AdminRiskEventsIsland
        snapshot={snap({ riskEvents: { ok: true, events: [{ id: 1, ts: T0, level: 'warn', category: 'buy_blocked', ticker: 'X', message: 'r' }] } })}
        pollingOptions={POLL_OFF}
      />,
    );
    const html = container.innerHTML;
    for (const t of ['매수 실행', '매도 실행', '주문 실행', 'placeOrder', 'buyNow', 'sellNow', 'sendOrder']) {
      expect(html.includes(t)).toBe(false);
    }
  });

  it('enabled=false → fetcher 호출 0건', () => {
    const fetcher = vi.fn();
    render(<AdminRiskEventsIsland pollingOptions={{ enabled: false, intervalMs: 0, fetcher: fetcher as never }} />);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('snapshot prop → polling 자동 비활성', () => {
    const fetcher = vi.fn();
    render(<AdminRiskEventsIsland snapshot={snap({})} pollingOptions={{ intervalMs: 0, fetcher: fetcher as never }} />);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
