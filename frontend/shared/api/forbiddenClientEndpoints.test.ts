// Phase F2 — Source-level client isolation.
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(HERE, '..', '..');
const CLIENT_ROOT = resolve(FRONTEND_ROOT, 'apps', 'client');
const SHARED_ROOT = resolve(FRONTEND_ROOT, 'shared');

const FORBIDDEN: readonly string[] = [
  '/api/admin',
  '/api/orders',
  '/api/balance',
  '/api/portfolio',
  '/api/admin/system_status',
  '/api/admin/risk_events',
  '/api/admin/process_status',
];

const SCAN_EXTENSIONS = /\.(ts|tsx|js|jsx|html|css)$/i;
const SELF = fileURLToPath(import.meta.url);
const EXCLUDE_FILES: readonly string[] = [
  resolve(SHARED_ROOT, 'api', 'adminApi.ts'),
  SELF,
  resolve(SHARED_ROOT, 'api', 'buildIsolation.test.ts'),
];
const EXCLUDE_DIRS: readonly string[] = ['node_modules', 'dist', '.vite', 'coverage'];

function listFiles(root: string): string[] {
  const out: string[] = [];
  const stack: string[] = [root];
  while (stack.length > 0) {
    const cur = stack.pop();
    if (!cur) continue;
    let entries: string[];
    try { entries = readdirSync(cur); } catch { continue; }
    for (const name of entries) {
      const full = join(cur, name);
      let st;
      try { st = statSync(full); } catch { continue; }
      if (st.isDirectory()) {
        if (EXCLUDE_DIRS.includes(name)) continue;
        stack.push(full);
      } else if (SCAN_EXTENSIONS.test(name)) {
        out.push(full);
      }
    }
  }
  return out;
}

describe('client/shared source has no admin endpoint references', () => {
  const files = [...listFiles(CLIENT_ROOT), ...listFiles(SHARED_ROOT)].filter(
    (f) => !EXCLUDE_FILES.includes(f),
  );
  it('스캔 대상 파일이 1개 이상', () => {
    expect(files.length).toBeGreaterThan(0);
  });
  for (const file of files) {
    const rel = file.replace(FRONTEND_ROOT, '').replace(/\\/g, '/');
    it(`${rel} — admin endpoint 미포함`, () => {
      const text = readFileSync(file, 'utf8');
      const hits = FORBIDDEN.filter((needle) => text.includes(needle));
      expect(hits, `forbidden endpoints found in ${rel}: ${hits.join(', ')}`).toEqual([]);
    });
  }
});

describe('shared/api/index.ts must not re-export adminApi', () => {
  const indexPath = resolve(SHARED_ROOT, 'api', 'index.ts');
  it('./adminApi 모듈 import 금지', () => {
    const text = readFileSync(indexPath, 'utf8');
    expect(text).not.toMatch(/from ['"]\.\/adminApi['"]/);
    expect(text).not.toMatch(/import\s*\([^)]*['"]\.\/adminApi['"]/);
    expect(text).not.toMatch(/export\s+\{[^}]*adminApi/);
  });
  it('publicApi / fetchJson 만 export', () => {
    const text = readFileSync(indexPath, 'utf8');
    expect(text).toContain("from './publicApi'");
    expect(text).toContain("from './fetchJson'");
  });
});
