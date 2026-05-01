// Phase F2 — Build artifact isolation. dist/ 가 없으면 skip.
import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(HERE, '..', '..');
const DIST_ROOT = resolve(FRONTEND_ROOT, 'dist');
const DIST_CLIENT_HTML = resolve(DIST_ROOT, 'apps', 'client', 'index.html');
const DIST_ASSETS = resolve(DIST_ROOT, 'assets');

const FORBIDDEN: readonly string[] = [
  '/api/admin',
  '/api/orders',
  '/api/balance',
  '/api/portfolio',
  '/api/admin/system_status',
  '/api/admin/risk_events',
  '/api/admin/process_status',
];

function distExists(): boolean {
  return existsSync(DIST_CLIENT_HTML) && existsSync(DIST_ASSETS);
}

describe.runIf(distExists())('client build artifact has no admin endpoint references', () => {
  it('dist/apps/client/index.html — admin chunk preload 0건', () => {
    const html = readFileSync(DIST_CLIENT_HTML, 'utf8');
    expect(html).not.toMatch(/href=["'][^"']*\/assets\/admin-[A-Za-z0-9_-]+\.js["']/);
    expect(html).not.toMatch(/src=["'][^"']*\/assets\/admin-[A-Za-z0-9_-]+\.js["']/);
  });

  it('dist/apps/client/index.html — admin endpoint 0건', () => {
    const html = readFileSync(DIST_CLIENT_HTML, 'utf8');
    const hits = FORBIDDEN.filter((s) => html.includes(s));
    expect(hits).toEqual([]);
  });

  const assets = distExists()
    ? readdirSync(DIST_ASSETS).filter(
        (n) => /^(client|vendor)-[A-Za-z0-9_-]+\.js$/.test(n) && !n.endsWith('.map'),
      )
    : [];

  it('dist/assets/ 에 client/vendor chunk 1개 이상 (sanity)', () => {
    expect(assets.length).toBeGreaterThan(0);
  });

  for (const name of assets) {
    it(`dist/assets/${name} — admin endpoint 0건`, () => {
      const text = readFileSync(join(DIST_ASSETS, name), 'utf8');
      const hits = FORBIDDEN.filter((s) => text.includes(s));
      expect(hits, `${name} 에 admin endpoint: ${hits.join(', ')}`).toEqual([]);
    });
  }
});

describe('build artifact scan readiness', () => {
  it('dist/ 있으면 isolation 활성, 없으면 skip', () => {
    expect(typeof distExists()).toBe('boolean');
  });
});
