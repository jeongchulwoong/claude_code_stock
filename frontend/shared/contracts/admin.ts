// Phase F2 — Admin (`/advanced`) API contract.

import type { ApiOkEnvelope, HealthState, IsoDateString, OperatingMode } from './common';

export interface SummaryResponse extends ApiOkEnvelope {
  mode?: OperatingMode;
  market?: string;
  realized_pnl?: number | null;
  realized_pnl_pct?: number | null;
  updated_at?: IsoDateString | null;
}

export interface BalanceResponse extends ApiOkEnvelope {
  buying_power?: number | null;
  tot_evlu_amt?: number | null;
  tot_evlt_pl?: number | null;
  tot_evlt_pl_pct?: number | null;
  entr?: number | null;
  d2_entra?: number | null;
  updated_at?: IsoDateString | null;
}

export type OrderStatus =
  | 'SENT' | 'FILLED' | 'PARTIAL' | 'CANCELLED'
  | 'BLOCKED' | 'ERROR' | 'UNFILLED' | 'CANCEL_FAILED' | 'MEMORY_CLEARED'
  | (string & {});

export type OrderSide = 'BUY' | 'SELL' | (string & {});

export interface OrderItem {
  order_id?: string | null;
  ticker?: string | null;
  name?: string | null;
  order_type?: OrderSide;
  qty?: number | null;
  filled_qty?: number | null;
  price?: number | null;
  avg_fill_price?: number | null;
  status?: OrderStatus;
  reason?: string | null;
  reject_msg?: string | null;
  strategy?: string | null;
  broker_ord_no?: string | null;
  realized_pnl?: number | null;
  timestamp?: IsoDateString | null;
}

export interface OrdersResponse extends ApiOkEnvelope {
  orders?: OrderItem[];
  items?: OrderItem[];
  retention_days?: number | null;
}

export interface DailyLossInfo {
  used_pct?: number | null;
  used_text?: string | null;
  limit_text?: string | null;
}

export interface SystemStatusResponse extends ApiOkEnvelope {
  kiwoom?: HealthState;
  rest?: HealthState;
  websocket?: HealthState;
  telegram?: HealthState;
  daily_loss?: DailyLossInfo | null;
  pending_count?: number | null;
  updated_at?: IsoDateString | null;
}

// ──────────────────────────────────────────────────────────────────
// daytradeState — runtime_state.json 기반 단타 게이트 / 시간청산 상태.
// ──────────────────────────────────────────────────────────────────

export type DaytradeState =
  | 'idle' | 'allowed' | 'holding' | 'no_capacity_today'
  | 'loss_halt' | 'halted' | (string & {});

export interface DaytradeSection {
  state?: DaytradeState;
  state_label?: string | null;
  today_entries?: number | null;
  max_daily_entries?: number | null;
  open_ticker?: string | null;
  consecutive_losses?: number | null;
  loss_halt_threshold?: number | null;
  entry_window?: string | null;
  force_close_time?: string | null;
  stop_loss_pct?: number | null;
  take_profit_pct?: number | null;
  halted?: boolean | null;
  trade_date?: string | null;
  traded_tickers?: string[];
  open_entry_time?: IsoDateString | null;
  time_stop_hard_min?: number | null;
  time_stop_soft_min?: number | null;
  time_stop_soft_r?: number | null;
}

export interface DaytradeStateResponse extends ApiOkEnvelope {
  updated_at?: IsoDateString | null;
  daytrade?: DaytradeSection;
  snapshot_missing?: boolean | null;
}

export type RiskLevel = 'info' | 'warn' | 'error' | 'critical' | (string & {});
export type RiskCategory =
  | 'order_error' | 'buy_blocked' | 'cancel_failed'
  | 'stop_loss' | 'take_profit' | 'time_stop' | 'force_close'
  | 'daily_loss_warning' | 'consecutive_loss_halt'
  | 'duplicate_order_blocked' | 'ord_alow_insufficient'
  | 'system' | 'ml_score'
  | (string & {});

export interface RiskEvent {
  id?: number;
  ts?: IsoDateString;
  level?: RiskLevel;
  category?: RiskCategory;
  ticker?: string | null;
  message?: string | null;
  meta?: Record<string, unknown> | null;
}

export interface RiskEventsResponse extends ApiOkEnvelope {
  events?: RiskEvent[];
  retention_days?: number | null;
}

export type ProcessRunStatus = 'running' | 'stopped' | 'stale_lock' | 'unknown' | (string & {});

export interface MainProcessStatus {
  status?: ProcessRunStatus;
  pid?: number | null;
  started_at?: IsoDateString | null;
  heartbeat_at?: IsoDateString | null;
  heartbeat_age_sec?: number | null;
  phase?: string | null;
  hostname?: string | null;
  stale?: boolean | null;
}

export interface ProcessStatusResponse extends ApiOkEnvelope {
  main?: MainProcessStatus;
  /** 하위 호환용 별칭 — 일부 응답이 main_v2 키를 그대로 보내기도 한다. */
  main_v2?: MainProcessStatus;
}
