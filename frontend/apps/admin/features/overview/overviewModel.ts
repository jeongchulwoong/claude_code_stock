// Phase F3 — Overview model + derive helpers (순수 함수).

import type {
  BalanceResponse,
  OrdersResponse,
  ProcessStatusResponse,
  SummaryResponse,
  SystemStatusResponse,
} from '@shared/contracts';

export type Tone = 'ok' | 'warn' | 'fail' | 'muted';

export interface OverviewSnapshot {
  summary: SummaryResponse | null;
  balance: BalanceResponse | null;
  orders:  OrdersResponse  | null;
  system:  SystemStatusResponse | null;
  process: ProcessStatusResponse | null;
  fetchedAt: number;
  hadError: boolean;
}

export const EMPTY_SNAPSHOT: OverviewSnapshot = {
  summary: null,
  balance: null,
  orders:  null,
  system:  null,
  process: null,
  fetchedAt: 0,
  hadError: false,
};

export interface ModeView {
  mode: string;
  tone: Tone;
}

export function deriveMode(snap: OverviewSnapshot): ModeView {
  const m = String(snap.summary?.mode ?? '').toUpperCase();
  if (m === 'LIVE')   return { mode: 'LIVE',   tone: 'warn' };
  if (m === 'PAPER')  return { mode: 'PAPER',  tone: 'ok' };
  if (m === 'DRY')    return { mode: 'DRY',    tone: 'muted' };
  return { mode: m || '—', tone: 'muted' };
}

export interface BuyingPowerView {
  amount: number | null;
  tone: Tone;
  hint: string | null;
}

export function deriveBuyingPower(snap: OverviewSnapshot): BuyingPowerView {
  const bp = snap.balance?.buying_power ?? null;
  const mode = String(snap.summary?.mode ?? '').toUpperCase();
  if (bp == null) return { amount: null, tone: 'muted', hint: null };
  if (mode === 'LIVE' && bp <= 0) {
    return { amount: bp, tone: 'fail', hint: '매수 여력 확인 필요' };
  }
  return { amount: bp, tone: 'ok', hint: null };
}

export interface OrderTotals {
  filled: number;
  blocked: number;
  unfilled: number;
}

export function deriveOrderTotals(snap: OverviewSnapshot): OrderTotals {
  const list = snap.orders?.orders ?? snap.orders?.items ?? [];
  let filled = 0, blocked = 0, unfilled = 0;
  for (const o of list) {
    const s = String(o?.status ?? '').toUpperCase();
    if (s === 'FILLED' || s === 'PARTIAL') filled += 1;
    else if (s === 'BLOCKED' || s === 'ERROR' || s === 'CANCEL_FAILED') blocked += 1;
    else if (s === 'SENT' || s === 'UNFILLED') unfilled += 1;
  }
  return { filled, blocked, unfilled };
}

export interface DailyLossView {
  pct: number;
  tone: Tone;
  usedText: string | null;
  limitText: string | null;
}

export function deriveDailyLoss(snap: OverviewSnapshot): DailyLossView {
  const dl = snap.system?.daily_loss ?? null;
  const used = Number(dl?.used_pct ?? 0) || 0;
  let tone: Tone = 'ok';
  if (used >= 80) tone = 'fail';
  else if (used >= 50) tone = 'warn';
  else if (used <= 0)  tone = 'muted';
  return {
    pct: used,
    tone,
    usedText:  dl?.used_text  ?? null,
    limitText: dl?.limit_text ?? null,
  };
}

export interface DataHealthRow {
  label: string;
  state: string;
  tone: Tone;
}

function _toneFromHealth(h: string | null | undefined): Tone {
  const s = String(h ?? '').toLowerCase();
  if (s === 'ok')   return 'ok';
  if (s === 'warn') return 'warn';
  if (s === 'fail') return 'fail';
  return 'muted';
}

export function deriveDataHealth(snap: OverviewSnapshot): DataHealthRow[] {
  const sys = snap.system;
  return [
    { label: 'Kiwoom',   state: String(sys?.kiwoom    ?? '—'), tone: _toneFromHealth(sys?.kiwoom) },
    { label: 'REST',     state: String(sys?.rest      ?? '—'), tone: _toneFromHealth(sys?.rest) },
    { label: 'WS',       state: String(sys?.websocket ?? '—'), tone: _toneFromHealth(sys?.websocket) },
    { label: 'Telegram', state: String(sys?.telegram  ?? '—'), tone: _toneFromHealth(sys?.telegram) },
  ];
}

export interface TraderHealthView {
  status: 'running' | 'running-stale' | 'stopped' | 'unknown';
  tone: Tone;
  pid: number | null;
  heartbeatAgeSec: number | null;
}

export function deriveTraderHealth(snap: OverviewSnapshot): TraderHealthView {
  const m = snap.process?.main ?? snap.process?.main_v2 ?? null;
  const raw = String(m?.status ?? '').toLowerCase();
  const age = Number(m?.heartbeat_age_sec ?? -1);
  if (raw === 'running') {
    const stale = (m?.stale === true) || (age >= 60);
    return {
      status: stale ? 'running-stale' : 'running',
      tone:   stale ? 'warn'          : 'ok',
      pid: m?.pid ?? null,
      heartbeatAgeSec: Number.isFinite(age) && age >= 0 ? age : null,
    };
  }
  if (raw === 'stopped') {
    return { status: 'stopped', tone: 'fail', pid: m?.pid ?? null, heartbeatAgeSec: null };
  }
  return { status: 'unknown', tone: 'muted', pid: null, heartbeatAgeSec: null };
}
