// Phase F6 — Portfolio normalize.

import type { ApiOkEnvelope } from '@shared/contracts';

export interface PortfolioHolding {
  ticker: string;
  name?: string | null;
  qty?: number | null;
  avg_price?: number | null;
  current_price?: number | null;
  evlt_pl?: number | null;
  evlt_pl_pct?: number | null;
  weight_pct?: number | null;
  style?: 'daytrading' | 'longterm' | (string & {});
}

export interface PortfolioSector {
  name: string;
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

export function normalizePortfolio(resp: PortfolioResponse | null): {
  summary: PortfolioSummary;
  holdings: PortfolioHolding[];
  sectors: PortfolioSector[];
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
  const num = (v: unknown): number | null => {
    if (v == null) return null;
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const summary: PortfolioSummary = {
    totalEval:        num(resp.tot_evlu_amt),
    totalCost:        num(resp.tot_pur_amt),
    totalPnl:         num(resp.tot_evlt_pl),
    totalPnlPct:      num(resp.tot_evlt_pl_pct ?? resp.tot_evlt_pl_rate),
    todayRealizedPnl: num(resp.today_realized_pnl),
    realizedPnl:      num(resp.realized_pnl),
    buyingPower:      num(resp.buying_power),
    holdingsCount:    Number(resp.holdings_count ?? (resp.holdings?.length ?? 0)) || 0,
    updatedAt:        typeof resp.updated_at === 'string' ? resp.updated_at : null,
    hasData:          Boolean(resp.ok ?? true),
    source:           typeof resp.source === 'string' ? resp.source : null,
  };
  const holdings = Array.isArray(resp.holdings) ? resp.holdings.filter((h) => !!h?.ticker) : [];
  const sectors  = Array.isArray(resp.sectors)  ? resp.sectors.filter((s) => !!s?.name)    : [];
  return { summary, holdings, sectors };
}

export function pnlTone(v: number | null | undefined): Tone {
  if (v == null || !Number.isFinite(v) || v === 0) return 'zero';
  return v > 0 ? 'pos' : 'neg';
}
