// Phase F2 — 표시 포맷 유틸 (admin/client 공용).

export function money(v: number | null | undefined, currency: 'KRW' | 'USD' = 'KRW'): string {
  if (v == null || !Number.isFinite(v)) return '—';
  if (currency === 'KRW') return `${Math.round(v).toLocaleString('ko-KR')}원`;
  return `$${v.toFixed(2)}`;
}

export function moneyShort(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}억`;
  if (a >= 10_000)      return `${Math.round(v / 10_000).toLocaleString('ko-KR')}만`;
  return Math.round(v).toLocaleString('ko-KR');
}

export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}%`;
}

export function qty(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return Math.round(v).toLocaleString('ko-KR');
}

function _hms(d: Date): { hh: string; mm: string; ss: string } {
  return {
    hh: String(d.getHours()).padStart(2, '0'),
    mm: String(d.getMinutes()).padStart(2, '0'),
    ss: String(d.getSeconds()).padStart(2, '0'),
  };
}

export function timeHHMMSS(input: string | number | Date | null | undefined): string {
  if (input == null) return '—';
  const d = typeof input === 'string' || typeof input === 'number' ? new Date(input) : input;
  if (Number.isNaN(d.getTime())) return String(input);
  const { hh, mm, ss } = _hms(d);
  return `${hh}:${mm}:${ss}`;
}

export function timeMDHM(input: string | number | Date | null | undefined): string {
  if (input == null) return '—';
  const d = typeof input === 'string' || typeof input === 'number' ? new Date(input) : input;
  if (Number.isNaN(d.getTime())) return String(input);
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  const { hh, mm } = _hms(d);
  return `${M}/${D} ${hh}:${mm}`;
}

export function pnlClass(v: number | null | undefined): 'pos' | 'neg' | 'zero' {
  if (v == null || !Number.isFinite(v) || v === 0) return 'zero';
  return v > 0 ? 'pos' : 'neg';
}
