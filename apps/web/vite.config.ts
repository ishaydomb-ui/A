import { defineConfig } from 'vite';
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
});
