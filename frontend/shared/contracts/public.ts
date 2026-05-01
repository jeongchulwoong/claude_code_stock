// Phase F2 — Public (`/client`) API contract.
import type { ApiOkEnvelope } from './common';

export interface PublicScreenerItem {
  ticker: string;
  name?: string | null;
  score?: number | null;
  market?: string | null;
  reason?: string | null;
  reasons?: string[] | null;
  current_price?: number | null;
  price?: number | null;
  change_pct?: number | null;
  volume_ratio?: number | null;
  is_domestic?: boolean | null;
  screened_at?: string | null;
  updated_at?: string | null;
}

export interface PublicScreenerResponse extends ApiOkEnvelope {
  items?: PublicScreenerItem[];
  screener?: PublicScreenerItem[];
  filters?: { market?: string; min_score?: number };
  updated_at?: string | null;
  retention_minutes?: number | null;
}
