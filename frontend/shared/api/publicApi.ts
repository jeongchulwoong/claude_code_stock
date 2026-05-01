// Phase F2 — Public API endpoint registry.
// /client 에서 호출 가능한 endpoint 만 포함. admin endpoint 는 절대 들어오지 않는다.
export const publicApi = {
  publicScreener: '/api/public/screener',
  health: '/api/health',
} as const;

export type PublicApiKey = keyof typeof publicApi;
