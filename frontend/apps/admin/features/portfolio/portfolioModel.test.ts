// Phase F8 — Portfolio model 단위 테스트.
//
// 핵심 invariant: 실제 /api/portfolio 응답 필드 (sector / weight / eval_amt / pnl / pnl_rate)
// 가 React 단일 NormalizedHolding shape 으로 정상 매핑된다.

import { describe, expect, it } from 'vitest';
import { normalizePortfolio, pnlTone, type PortfolioResponse } from './portfolioModel';

describe('normalizePortfolio — real API field mapping', () => {
  it('null response → empty summary + arrays', () => {
    const out = normalizePortfolio(null);
    expect(out.holdings).toEqual([]);
    expect(out.sectors).toEqual([]);
    expect(out.summary.totalEval).toBeNull();
    expect(out.summary.hasData).toBe(false);
  });

  it('실제 API holdings 필드 (sector / weight / eval_amt / pnl / pnl_rate) 정상 매핑', () => {
    const resp: PortfolioResponse = {
      ok: true,
      updated_at: '2026-05-02T10:30:00',
      buying_power: 240_152,
      tot_evlu_amt: 1_500_000,
      tot_pur_amt: 1_400_000,
      tot_evlt_pl: 100_000,
      tot_evlt_pl_rate: 7.14,
      realized_pnl: 50_000,
      today_realized_pnl: 12_000,
      holdings_count: 2,
      holdings: [
        {
          ticker: '005930.KS',
          name: '삼성전자',
          qty: 10,
          avg_price: 70_000,
          current_price: 71_500,
          eval_amt: 715_000,
          pnl: 15_000,
          pnl_rate: 2.14,
          sector: '반도체',
          weight: 47.67,
        },
        {
          ticker: '035720.KQ',
          name: '카카오',
          qty: 5,
          avg_price: 50_000,
          cur_price: 52_000,    // legacy key
          eval_amt: 260_000,
          pnl: 10_000,
          pnl_rate: 4.0,
          sector: '인터넷',
          weight: 17.33,
        },
      ],
      sectors: [
        { sector: '반도체', eval_amt: 715_000, weight: 47.67 },
        { sector: '인터넷', eval_amt: 260_000, weight: 17.33 },
      ],
    };
    const out = normalizePortfolio(resp);

    expect(out.summary.totalEval).toBe(1_500_000);
    expect(out.summary.totalCost).toBe(1_400_000);
    expect(out.summary.totalPnl).toBe(100_000);
    expect(out.summary.totalPnlPct).toBe(7.14);
    expect(out.summary.realizedPnl).toBe(50_000);
    expect(out.summary.todayRealizedPnl).toBe(12_000);
    expect(out.summary.buyingPower).toBe(240_152);
    expect(out.summary.holdingsCount).toBe(2);

    expect(out.holdings).toHaveLength(2);
    const samsung = out.holdings[0];
    expect(samsung?.ticker).toBe('005930.KS');
    expect(samsung?.name).toBe('삼성전자');
    expect(samsung?.qty).toBe(10);
    expect(samsung?.avgPrice).toBe(70_000);
    expect(samsung?.currentPrice).toBe(71_500);
    expect(samsung?.evalAmount).toBe(715_000);
    expect(samsung?.pnl).toBe(15_000);
    expect(samsung?.pnlPct).toBe(2.14);
    expect(samsung?.sector).toBe('반도체');
    expect(samsung?.weight).toBe(47.67);

    const kakao = out.holdings[1];
    // legacy 'cur_price' 도 currentPrice 로 정상 매핑.
    expect(kakao?.currentPrice).toBe(52_000);

    expect(out.sectors).toHaveLength(2);
    expect(out.sectors[0]?.sector).toBe('반도체');
    expect(out.sectors[0]?.weight).toBe(47.67);
  });

  it('sectors 가 비었으면 holdings 에서 자동 합산', () => {
    const resp: PortfolioResponse = {
      ok: true,
      tot_evlu_amt: 600_000,
      holdings: [
        { ticker: 'A', sector: 'IT', eval_amt: 400_000, qty: 1 },
        { ticker: 'B', sector: 'IT', eval_amt: 100_000, qty: 1 },
        { ticker: 'C', sector: '바이오', eval_amt: 100_000, qty: 1 },
      ],
      sectors: [],
    };
    const out = normalizePortfolio(resp);
    expect(out.sectors).toHaveLength(2);
    const it = out.sectors.find((s) => s.sector === 'IT');
    expect(it?.evalAmount).toBe(500_000);
    // total = 600_000, IT = 500_000 → 83.33%.
    expect(it?.weight).toBeCloseTo(83.33, 1);
  });

  it('legacy field 별칭 (evlt_pl / weight_pct / pnl_pct) 도 fallback 으로 받는다', () => {
    const resp: PortfolioResponse = {
      ok: true,
      holdings: [{
        ticker: 'X',
        evlt_pl: 5000,
        evlt_pl_pct: 1.5,
        weight_pct: 25.0,
      }],
    };
    const [h] = normalizePortfolio(resp).holdings;
    expect(h?.pnl).toBe(5000);
    expect(h?.pnlPct).toBe(1.5);
    expect(h?.weight).toBe(25.0);
  });

  it('ticker 없는 holding 은 drop', () => {
    const resp: PortfolioResponse = {
      ok: true,
      holdings: [
        { ticker: '', sector: 'IT' },
        { ticker: 'A', sector: 'IT', eval_amt: 100 },
      ],
    };
    expect(normalizePortfolio(resp).holdings).toHaveLength(1);
  });

  it('총 평가손익률 — tot_evlt_pl_rate / tot_evlt_pl_pct 양쪽 모두 받는다', () => {
    const a = normalizePortfolio({ ok: true, tot_evlt_pl_rate: 5.5 });
    const b = normalizePortfolio({ ok: true, tot_evlt_pl_pct: 5.5 });
    expect(a.summary.totalPnlPct).toBe(5.5);
    expect(b.summary.totalPnlPct).toBe(5.5);
  });

  it('sector 누락 시 "기타" 로 폴백', () => {
    const out = normalizePortfolio({ ok: true, holdings: [{ ticker: 'X' }] });
    expect(out.holdings[0]?.sector).toBe('기타');
  });

  it('숫자 NaN / Infinity 는 null 로 정리', () => {
    const resp: PortfolioResponse = {
      ok: true,
      buying_power: NaN,
      tot_evlu_amt: Infinity,
      holdings: [{ ticker: 'X', eval_amt: NaN, pnl: -Infinity }],
    };
    const out = normalizePortfolio(resp);
    expect(out.summary.buyingPower).toBeNull();
    expect(out.summary.totalEval).toBeNull();
    expect(out.holdings[0]?.evalAmount).toBeNull();
    expect(out.holdings[0]?.pnl).toBeNull();
  });
});

describe('pnlTone', () => {
  it('양수 → pos', () => { expect(pnlTone(100)).toBe('pos'); });
  it('음수 → neg', () => { expect(pnlTone(-100)).toBe('neg'); });
  it('0 / null / NaN → zero', () => {
    expect(pnlTone(0)).toBe('zero');
    expect(pnlTone(null)).toBe('zero');
    expect(pnlTone(NaN)).toBe('zero');
  });
});
