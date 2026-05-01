// Phase F2 — 공용 contract primitives.

export type IsoDateString = string;
export type OperatingMode = 'LIVE' | 'PAPER' | 'DRY' | (string & {});
export type HealthState = 'ok' | 'warn' | 'fail' | 'unknown' | (string & {});

export interface ApiOkEnvelope {
  ok?: boolean;
  error?: string | null;
}
