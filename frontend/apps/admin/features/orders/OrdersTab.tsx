// Phase F7 — Orders tab. /api/orders read-only — 매수/매도 트리거 0건.

import { useEffect, useState } from 'react';
import { adminApi } from '@shared/api/adminApi';
import { fetchJson } from '@shared/api/fetchJson';
import { money, pct, qty as fmtQty, timeMDHM } from '@shared/format/format';
import type { OrderItem, OrdersResponse } from '@shared/contracts';

const STATUS_TONE: Record<string, string> = {
  FILLED: 'ok', PARTIAL: 'ok',
  SENT: 'muted', UNFILLED: 'muted',
  BLOCKED: 'warn', ERROR: 'fail', CANCEL_FAILED: 'fail',
  CANCELLED: 'muted',
};

function statusLabel(s: string | null | undefined): string {
  return String(s ?? '—').toUpperCase();
}

export default function OrdersTab() {
  const [orders, setOrders] = useState<OrderItem[] | null>(null);
  const [hadError, setHadError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    async function tick() {
      try {
        const r = await fetchJson<OrdersResponse>(adminApi.orders, { signal: ctrl.signal });
        if (cancelled) return;
        setOrders(r?.orders ?? r?.items ?? []);
        setHadError(false);
      } catch {
        if (cancelled) return;
        setHadError(true);
      }
      if (!cancelled) {
        setLoading(false);
        setUpdatedAt(Date.now());
      }
    }
    tick();
    const t = setInterval(tick, 15_000);
    return () => { cancelled = true; clearInterval(t); ctrl.abort(); };
  }, []);

  return (
    <section data-tab="orders" className="qd-fade-in">
      <header className="qd-tab-h">
        <h2>주문 내역</h2>
        <span className="qd-tab-meta">
          {orders == null ? '—' : `${orders.length}건`} · 마지막 갱신 <span className="qd-num">{updatedAt ? new Date(updatedAt).toLocaleTimeString('ko-KR') : '—'}</span>
          {hadError && <span style={{ marginLeft: 8, color: 'var(--qd-amber)' }}>· 갱신 실패</span>}
        </span>
      </header>

      {loading && orders == null ? (
        <div className="qd-skeleton qd-skeleton-card" style={{ height: 200 }} />
      ) : orders == null || orders.length === 0 ? (
        <div className="qd-tab-stub">
          <h2>주문 없음</h2>
          <p>아직 체결되거나 차단된 주문이 없습니다.</p>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="qd-tab-table">
            <thead>
              <tr>
                <th>시각</th>
                <th>종목</th>
                <th>구분</th>
                <th>수량</th>
                <th>가격</th>
                <th>상태</th>
                <th>실현손익</th>
                <th>사유</th>
              </tr>
            </thead>
            <tbody>
              {orders.slice(0, 200).map((o, i) => {
                const tone = STATUS_TONE[String(o.status ?? '').toUpperCase()] ?? 'muted';
                const reason = o.reject_msg ?? o.reason ?? '';
                return (
                  <tr key={`${o.order_id ?? o.broker_ord_no ?? i}-${i}`}>
                    <td className="qd-num">{timeMDHM(o.timestamp ?? null)}</td>
                    <td>
                      <span className="qd-num"><b>{o.ticker ?? '—'}</b></span>
                      {o.name && <span style={{ marginLeft: 6, color: 'var(--qd-text-3)' }}>{o.name}</span>}
                    </td>
                    <td><span data-tone={o.order_type === 'BUY' ? 'ok' : 'fail'}>{o.order_type ?? '—'}</span></td>
                    <td className="qd-num">{fmtQty(o.qty ?? null)}{o.filled_qty != null ? ` (체결 ${fmtQty(o.filled_qty)})` : ''}</td>
                    <td className="qd-num">{money(o.price ?? null)}{o.avg_fill_price != null ? ` (평균 ${money(o.avg_fill_price)})` : ''}</td>
                    <td><span data-tone={tone}>{statusLabel(o.status)}</span></td>
                    <td className="qd-num" data-pnl={o.realized_pnl != null && o.realized_pnl > 0 ? 'pos' : (o.realized_pnl != null && o.realized_pnl < 0 ? 'neg' : 'zero')}>
                      {o.realized_pnl != null ? `${money(o.realized_pnl)} (${pct(null)})` : '—'}
                    </td>
                    <td style={{ color: 'var(--qd-text-3)' }}>{reason ? String(reason).slice(0, 60) : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
