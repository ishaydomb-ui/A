import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests run against the real stack: Vite serving the built client,
 * the Fastify API, and a real PostgreSQL database seeded by the global setup.
 */
const CHROMIUM = process.env.PLAYWRIGHT_CHROMIUM_PATH;

export const E2E_DATABASE_URL =
  process.env.E2E_DATABASE_URL ?? 'postgres://medapp:devpassword@127.0.0.1:5432/medcat_e2e';
export const E2E_APP_SECRET =
  process.env.APP_SECRET ?? 'e2e-only-secret-value-not-used-in-any-real-deployment';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: CHROMIUM ? { executablePath: CHROMIUM } : {},
  },
  /**
   * The suite starts and stops the whole stack itself, so a run is
   * reproducible and never depends on what happens to be running locally.
   */
  webServer: [
    {
      command: 'pnpm --filter @med/api exec tsx src/server.ts',
      cwd: '../..',
      url: 'http://127.0.0.1:4000/readyz',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: {
        NODE_ENV: 'test',
        PORT: '4000',
        HOST: '127.0.0.1',
        DATABASE_URL: E2E_DATABASE_URL,
        APP_SECRET: E2E_APP_SECRET,
        PUBLIC_URL: 'http://127.0.0.1:5173',
        COOKIE_SECURE: 'false',
        IMPORT_STORAGE_DIR: './var/e2e-imports',
        LOG_LEVEL: 'error',
        // The suite drives a whole test run through a handful of accounts, so
        // one "user" issues far more requests than any real clinician would.
        // The limiter itself is covered by the API integration tests; here it
        // is raised so it does not mask genuine failures.
        RATE_LIMIT_SEARCH_PER_MINUTE: '100000',
        RATE_LIMIT_LOGIN_PER_ACCOUNT: '1000',
        RATE_LIMIT_LOGIN_PER_IP: '10000',
      },
    },
    {
      command: 'pnpm exec vite --host 127.0.0.1 --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173/',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
  ],

  projects: [
    // Signs in once per role and stores the sessions the other projects reuse.
    { name: 'setup', testMatch: /auth\.setup\.ts/ },

    // Genuine sign-in, sign-out and MFA journeys, which must start signed out.
    {
      name: 'auth-desktop',
      testMatch: /auth\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },

    {
      name: 'desktop',
      testIgnore: /auth\.spec\.ts/,
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },

    // A phone profile, because the catalogue is used mostly on a phone.
    {
      name: 'mobile',
      testIgnore: /auth\.spec\.ts/,
      dependencies: ['setup'],
      use: { ...devices['Pixel 5'] },
    },
  ],
});
