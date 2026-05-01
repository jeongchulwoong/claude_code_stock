/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

// Phase F1+ — Multi-entry Vite config.
// admin / client 가 서로 다른 chunk 로 빌드되며, manifest.json 으로 Flask 가 asset URL 을 해석한다.
// modulePreload.resolveDependencies 로 client HTML 이 admin chunk 를 preload 하지 못하게 차단.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@shared': resolve(__dirname, 'shared'),
    },
  },
  build: {
    manifest: true,
    rollupOptions: {
      input: {
        admin:  resolve(__dirname, 'apps/admin/index.html'),
        client: resolve(__dirname, 'apps/client/index.html'),
        login:  resolve(__dirname, 'apps/login/index.html'),
      },
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor';
          return undefined;
        },
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
    modulePreload: {
      resolveDependencies(filename, deps, _ctx) {
        // client HTML 이 admin-* chunk 를 preload 하지 못하게 (격리 정책).
        if (filename.includes('apps/client') || filename.endsWith('client/index.html')) {
          return deps.filter((d) => !d.includes('admin'));
        }
        if (filename.includes('apps/admin') || filename.endsWith('admin/index.html')) {
          return deps.filter((d) => !d.includes('client'));
        }
        return deps;
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./shared/test/setup.ts'],
  },
});
