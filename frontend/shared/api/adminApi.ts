// Phase F2 — Admin API endpoint registry.
// /client 에서는 절대 import 하지 않는다. 격리는 forbiddenClientEndpoints.test.ts + buildIsolation.test.ts 가 검증.

export const adminApi = {
  systemStatus:         '/api/admin/system_status',
  balance:              '/api/balance',
  portfolio:            '/api/portfolio',
  summary:              '/api/summary',
  orders:               '/api/orders',
  dailyPnl:             '/api/daily_pnl',
  tickerStats:          '/api/ticker_stats',
  strategyStats:        '/api/strategy_stats',
  aiLog:                '/api/ai_log',
  stocks:               '/api/stocks',
  chart:                '/api/chart',
  screener:             '/api/screener',
  config:               '/api/config',
  daytradeState:        '/api/admin/daytrade_state',
  riskEvents:           '/api/admin/risk_events',
  processStatus:        '/api/admin/process_status',
  minuteDaytrade:       '/api/admin/minute_daytrade',
} as const;

export type AdminApiKey = keyof typeof adminApi;
