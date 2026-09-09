import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    globals: false,
    // Integration tests share one Postgres database, so they must not run
    // concurrently with each other.
    fileParallelism: false,
    hookTimeout: 60_000,
    testTimeout: 60_000,
    setupFiles: ['src/test/setup.ts'],
    include: ['src/**/*.test.ts'],
  },
});
