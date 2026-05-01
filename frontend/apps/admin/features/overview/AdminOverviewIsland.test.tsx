// Phase F3 — Admin Overview Island 단위 테스트.
import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import AdminOverviewIsland from './AdminOverviewIsland';
import { EMPTY_SNAPSHOT, type OverviewSnapshot } from './overviewModel';

function snap(overrides: Partial<OverviewSnapshot>): OverviewSnapshot {
  return { ...EMPTY_SNAPSHOT, ...overrides, fetchedAt: 1_700_000_000_000 };
}

const POLL_OPT = { enabled: false, intervalMs: 0 } as const;

describe('AdminOverviewIsland (F3)', () => {
  it('F3 마커 노출', () => {
    const { container } = render(
      <AdminOverviewIsland snapshot={EMPTY_SNAPSHOT} pollingOptions={POLL_OPT} />,
    );
    const root = container.querySelector('[data-island="admin-overview"]');
    expect(root).not.toBeNull();
    expect(root?.getAttribute('data-phase')).toBe('F3');
  });

  it('LIVE + buying_power=0 → fail 톤 + 힌트', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({
          summary: { mode: 'LIVE' },
          balance: { buying_power: 0, tot_evlu_amt: 0, tot_evlt_pl: 0 },
        })}
        pollingOptions={POLL_OPT}
      />,
    );
    const bpCell = container.querySelector('[data-buying-power]');
    expect(bpCell?.getAttribute('data-tone')).toBe('fail');
    expect(container.querySelector('[data-buying-power-hint]')?.textContent).toContain('매수 여력 확인 필요');
  });

  it('LIVE + buying_power>0 → ok 톤', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({ summary: { mode: 'LIVE' }, balance: { buying_power: 240_152 } })}
        pollingOptions={POLL_OPT}
      />,
    );
    const bpCell = container.querySelector('[data-buying-power]');
    expect(bpCell?.getAttribute('data-tone')).toBe('ok');
    expect(container.querySelector('[data-buying-power-hint]')).toBeNull();
  });

  it('heartbeat_age_sec >= 60 → running-stale + warn', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({
          process: { main: { status: 'running', pid: 1234, heartbeat_age_sec: 120, stale: true } },
        })}
        pollingOptions={POLL_OPT}
      />,
    );
    const status = container.querySelector('[data-trader-status]');
    expect(status?.getAttribute('data-trader-status')).toBe('running-stale');
    expect(status?.getAttribute('data-tone')).toBe('warn');
    expect(status?.textContent).toContain('RUNNING');
    expect(status?.textContent).toContain('stale');
  });

  it('heartbeat fresh → running + ok', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({
          process: { main: { status: 'running', pid: 9999, heartbeat_age_sec: 8, stale: false } },
        })}
        pollingOptions={POLL_OPT}
      />,
    );
    const status = container.querySelector('[data-trader-status]');
    expect(status?.getAttribute('data-trader-status')).toBe('running');
    expect(status?.getAttribute('data-tone')).toBe('ok');
  });

  it('main.status=stopped → fail', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({ process: { main: { status: 'stopped' } } })}
        pollingOptions={POLL_OPT}
      />,
    );
    const status = container.querySelector('[data-trader-status]');
    expect(status?.getAttribute('data-trader-status')).toBe('stopped');
    expect(status?.getAttribute('data-tone')).toBe('fail');
  });

  it('빈 응답 — 6 카드 모두 렌더', () => {
    const { container } = render(
      <AdminOverviewIsland snapshot={EMPTY_SNAPSHOT} pollingOptions={POLL_OPT} />,
    );
    for (const id of ['mode-bp', 'pnl', 'order-state', 'daily-loss', 'data-health', 'trader']) {
      expect(container.querySelector(`[data-card="${id}"]`)).not.toBeNull();
    }
  });

  it('orders 혼합 → 체결/차단/미체결 정확 분류', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({
          orders: { orders: [
            { status: 'FILLED' }, { status: 'PARTIAL' },
            { status: 'BLOCKED' }, { status: 'ERROR' }, { status: 'CANCEL_FAILED' },
            { status: 'SENT' }, { status: 'UNFILLED' },
          ] },
        })}
        pollingOptions={POLL_OPT}
      />,
    );
    const totals = container.querySelector('[data-order-totals]');
    const text = (totals?.textContent ?? '').replace(/\s+/g, ' ');
    expect(text).toMatch(/체결\s*2/);
    expect(text).toMatch(/차단\s*3/);
    expect(text).toMatch(/미체결\s*2/);
  });

  it('hadError → "일부 응답 누락" 배지', () => {
    const { container } = render(
      <AdminOverviewIsland snapshot={snap({ hadError: true })} pollingOptions={POLL_OPT} />,
    );
    const badge = container.querySelector('[data-error-badge]');
    expect(badge?.textContent).toContain('일부 응답 누락');
  });

  it('island 안에 매수/매도/주문 실행 버튼 0건', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({ summary: { mode: 'LIVE' }, balance: { buying_power: 1_000_000 } })}
        pollingOptions={POLL_OPT}
      />,
    );
    expect(container.querySelectorAll('button')).toHaveLength(0);
    const html = container.innerHTML;
    for (const t of ['매수 실행', '매도 실행', '주문 실행', 'placeOrder', 'buyNow', 'sellNow', 'sendOrder']) {
      expect(html.includes(t)).toBe(false);
    }
  });

  it('일일 손실 80%+ → fail (progress)', () => {
    const { container } = render(
      <AdminOverviewIsland
        snapshot={snap({
          system: { daily_loss: { used_pct: 85, used_text: '8,500원', limit_text: '10,000원' } },
        })}
        pollingOptions={POLL_OPT}
      />,
    );
    const card = container.querySelector('[data-card="daily-loss"]');
    expect(card?.textContent).toContain('85%');
    const fill = container.querySelector('[data-loss-fill]') as HTMLElement | null;
    expect(fill?.style.background ?? '').toContain('--danger');
  });

  it('pollingOptions.enabled=false → fetcher 호출 0건', () => {
    const fetcher = vi.fn();
    render(
      <AdminOverviewIsland pollingOptions={{ enabled: false, intervalMs: 0, fetcher: fetcher as never }} />,
    );
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('snapshot 있으면 polling 자동 disabled', () => {
    const fetcher = vi.fn();
    render(
      <AdminOverviewIsland snapshot={EMPTY_SNAPSHOT} pollingOptions={{ intervalMs: 0, fetcher: fetcher as never }} />,
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
});
