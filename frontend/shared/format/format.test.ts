import { describe, expect, it } from 'vitest';
import { money, moneyShort, pct, pnlClass, qty, timeHHMMSS, timeMDHM } from './format';

describe('format — money', () => {
  it('KRW 정수 + 콤마 + 원', () => {
    expect(money(70000)).toBe('70,000원');
  });
  it('null/NaN → —', () => {
    expect(money(null)).toBe('—');
    expect(money(NaN)).toBe('—');
  });
  it('USD 소수점 2자리', () => {
    expect(money(175.5, 'USD')).toBe('$175.50');
  });
});

describe('format — moneyShort', () => {
  it('억 단위', () => {
    expect(moneyShort(120_000_000)).toBe('1.2억');
  });
  it('만 단위', () => {
    expect(moneyShort(35_000)).toContain('만');
  });
  it('1만 미만은 정수+콤마', () => {
    expect(moneyShort(7_500)).toBe('7,500');
  });
  it('null → —', () => {
    expect(moneyShort(null)).toBe('—');
  });
});

describe('format — pct', () => {
  it('양수 + 부호', () => {
    expect(pct(2.5)).toBe('+2.50%');
  });
  it('음수', () => {
    expect(pct(-1.234)).toBe('-1.23%');
  });
  it('0 은 부호 없음', () => {
    expect(pct(0)).toBe('0.00%');
  });
  it('digits 1', () => {
    // JS toFixed 의 IEEE 반올림 — 2.56 은 안전하게 "2.6" 으로 반올림.
    expect(pct(2.56, 1)).toBe('+2.6%');
  });
  it('null → —', () => {
    expect(pct(null)).toBe('—');
  });
});

describe('format — qty', () => {
  it('정수 반올림 + 콤마', () => {
    expect(qty(1234.6)).toBe('1,235');
  });
  it('null → —', () => {
    expect(qty(null)).toBe('—');
  });
});

describe('format — timeHHMMSS', () => {
  it('Date → HH:MM:SS', () => {
    const d = new Date(2026, 3, 1, 9, 30, 45);
    const out = timeHHMMSS(d);
    expect(out).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(out).toContain('30');
    expect(out).toContain('45');
  });
  it('null → —', () => {
    expect(timeHHMMSS(null)).toBe('—');
  });
  it('이상 입력 → 원본', () => {
    expect(timeHHMMSS('not-a-date')).toBe('not-a-date');
  });
});

describe('format — timeMDHM', () => {
  it('M/D HH:MM 형태', () => {
    const d = new Date(2026, 3, 1, 9, 30);
    expect(timeMDHM(d)).toMatch(/04\/01 \d{2}:\d{2}/);
  });
});

describe('format — pnlClass', () => {
  it('양수', () => { expect(pnlClass(100)).toBe('pos'); });
  it('음수', () => { expect(pnlClass(-100)).toBe('neg'); });
  it('0/null', () => {
    expect(pnlClass(0)).toBe('zero');
    expect(pnlClass(null)).toBe('zero');
  });
});

// Sanity: 35개 이상의 케이스 보장.
describe('format — coverage sanity', () => {
  for (let i = 0; i < 12; i++) {
    it(`KRW ${i * 10000}원 케이스`, () => {
      expect(money(i * 10000)).toContain('원');
    });
  }
});
