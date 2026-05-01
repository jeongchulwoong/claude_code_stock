// Phase F6.1 — Portfolio normalize. 실제 /api/portfolio 응답 shape 기준.
//
// API 실제 키:
//   holdings[].sector / weight / eval_amt / pnl / pnl_rate
//   sectors[].sector / eval_amt / weight
// (legacy 키 evlt_pl / evlt_pl_pct / weight_pct / pnl_pct 도 fallback 으로 받는다.)

import type { ApiOkEnvelope } from '@shared/contracts';

export interface PortfolioHolding {
  ticker?: string | null;
  code?: string | null;
  name?: string | null;
  qty?: number | null;
  avg_price?: number | null;
  current_price?: number | null;
  cur_price?: number | null;
  eval_amt?: number | null;
  pnl?: number | null;
  pnl_rate?: number | null;
  pnl_pct?: number | null;
  evlt_pl?: number | null;
  evlt_pl_pct?: number | null;
  sector?: string | null;
  weight?: number | null;
  weight_pct?: number | null;
  style?: 'daytrading' | 'longterm' | (string & {});
}

export interface PortfolioSector {
  sector?: string | null;
  name?: string | null;
  eval_amt?: number | null;
  weight?: number | null;
  weight_pct?: number | null;
  evlt_pl?: number | null;
}

export interface PortfolioResponse extends ApiOkEnvelope {
  updated_at?: string | null;
  buying_power?: number | null;
  entr?: number | null;
  d2_entra?: number | null;
  tot_evlu_amt?: number | null;
  tot_pur_amt?: number | null;
  tot_evlt_pl?: number | null;
  tot_evlt_pl_rate?: number | null;
  tot_evlt_pl_pct?: number | null;
  realized_pnl?: number | null;
  today_realized_pnl?: number | null;
  holdings_count?: number | null;
  holdings?: PortfolioHolding[];
  sectors?: PortfolioSector[];
  source?: string | null;
}

export type Tone = 'pos' | 'neg' | 'zero';

// Normalized — UI 가 사용하는 단일 shape.
export interface NormalizedHolding {
  ticker: string;
  name: string;
  qty: number | null;
  avgPrice: number | null;
  currentPrice: number | null;
  evalAmount: number | null;
  pnl: number | null;
  pnlPct: number | null;
  sector: string;
  weight: number | null;
  style: 'daytrading' | 'longterm' | null;
}

export interface NormalizedSector {
  sector: string;
  evalAmount: number;
  weight: number;
}

export interface PortfolioSummary {
  totalEval: number | null;
  totalCost: number | null;
  totalPnl: number | null;
  totalPnlPct: number | null;
  todayRealizedPnl: number | null;
  realizedPnl: number | null;
  buyingPower: number | null;
  holdingsCount: number;
  updatedAt: string | null;
  hasData: boolean;
  source: string | null;
}

function num(v: unknown): number | null {
  if (v == null || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizeHolding(h: PortfolioHolding): NormalizedHolding | null {
  const ticker = String(h.ticker ?? h.code ?? '').trim();
  if (!ticker) return null;
  return {
    ticker,
    name:         String(h.name ?? '').trim() || ticker,
    qty:          num(h.qty),
    avgPrice:     num(h.avg_price),
    currentPrice: num(h.current_price ?? h.cur_price),
    evalAmount:   num(h.eval_amt),
    pnl:          num(h.pnl ?? h.evlt_pl),
    pnlPct:       num(h.pnl_rate ?? h.pnl_pct ?? h.evlt_pl_pct),
    sector:       String(h.sector ?? '기타').trim() || '기타',
    weight:       num(h.weight ?? h.weight_pct),
    style:        h.style === 'daytrading' ? 'daytrading'
                  : h.style === 'longterm' ? 'longterm' : null,
  };
}

function normalizeSector(s: PortfolioSector): NormalizedSector | null {
  const name = String(s.sector ?? s.name ?? '').trim();
  if (!name) return null;
  const eval_ = num(s.eval_amt) ?? 0;
  const weight = num(s.weight ?? s.weight_pct) ?? 0;
  if (eval_ <= 0 && weight <= 0) return null;
  return { sector: name, evalAmount: eval_, weight };
}

export function normalizePortfolio(resp: PortfolioResponse | null): {
  summary: PortfolioSummary;
  holdings: NormalizedHolding[];
  sectors: NormalizedSector[];
} {
  if (!resp) {
    return {
      summary: {
        totalEval: null, totalCost: null, totalPnl: null, totalPnlPct: null,
        todayRealizedPnl: null, realizedPnl: null, buyingPower: null,
        holdingsCount: 0, updatedAt: null, hasData: false, source: null,
      },
      holdings: [],
      sectors: [],
    };
  }

  const holdingsRaw = Array.isArray(resp.holdings) ? resp.holdings : [];
  const holdings = holdingsRaw
    .map(normalizeHolding)
    .filter((h): h is NormalizedHolding => h !== null);

  const sectorsRaw = Array.isArray(resp.sectors) ? resp.sectors : [];
  let sectors = sectorsRaw
    .map(normalizeSector)
    .filter((s): s is NormalizedSector => s !== null);

  // sectors 가 비었지만 holdings 가 있으면 holdings 에서 합산.
  if (sectors.length === 0 && holdings.length > 0) {
    const acc = new Map<string, number>();
    for (const h of holdings) {
      acc.set(h.sector, (acc.get(h.sector) ?? 0) + (h.evalAmount ?? 0));
    }
    const totalEval = Array.from(acc.values()).reduce((a, b) => a + b, 0) || 1;
    sectors = Array.from(acc.entries())
      .filter(([, v]) => v > 0)
      .map(([sector, evalAmount]) => ({
        sector,
        evalAmount,
        weight: Math.round((evalAmount / totalEval) * 10000) / 100,
      }))
      .sort((a, b) => b.evalAmount - a.evalAmount);
  }

  const summary: PortfolioSummary = {
    totalEval:        num(resp.tot_evlu_amt),
    totalCost:        num(resp.tot_pur_amt),
    totalPnl:         num(resp.tot_evlt_pl),
    totalPnlPct:      num(resp.tot_evlt_pl_rate ?? resp.tot_evlt_pl_pct),
    todayRealizedPnl: num(resp.today_realized_pnl),
    realizedPnl:      num(resp.realized_pnl),
    buyingPower:      num(resp.buying_power),
    holdingsCount:    Number(resp.holdings_count ?? holdings.length) || 0,
    updatedAt:        typeof resp.updated_at === 'string' ? resp.updated_at : null,
    hasData:          Boolean(resp.ok ?? true),
    source:           typeof resp.source === 'string' ? resp.source : null,
  };

  return { summary, holdings, sectors };
}

export function pnlTone(v: number | null | undefined): Tone {
  if (v == null || !Number.isFinite(v) || v === 0) return 'zero';
  return v > 0 ? 'pos' : 'neg';
}
