import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // The API is same-origin in production behind the reverse proxy; in
      // development it is proxied so cookies behave identically.
      '/api': { target: 'http://127.0.0.1:4000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:4000', changeOrigin: true },
      '/readyz': { target: 'http://127.0.0.1:4000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },

  // Everything under e2e/ belongs to Playwright. Without this Vitest collects
  // those files as its own, fails to resolve `@playwright/test`, and reports
  // the whole client suite as broken.
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    passWithNoTests: true,
  },
});
