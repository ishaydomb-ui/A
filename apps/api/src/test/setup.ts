/**
 * Test environment. Loaded before any module that reads config, so the
 * application always sees a complete, test-only configuration.
 */
process.env.NODE_ENV = 'test';
process.env.DATABASE_URL ??= 'postgres://medapp:devpassword@127.0.0.1:5432/medcat_test';
process.env.APP_SECRET ??= 'test-only-secret-value-not-used-in-any-real-deployment';
process.env.PUBLIC_URL ??= 'http://localhost:5173';
process.env.COOKIE_SECURE = 'false';
process.env.LOG_LEVEL = 'silent';
process.env.IMPORT_STORAGE_DIR ??= './var/test-imports';
// Keep Argon2 at production strength but allow generous timeouts.
process.env.LOGIN_MAX_ATTEMPTS ??= '8';
// Source lookup is opt-in. The suite installs a stand-in provider and never
// reaches the network; the off-by-default behaviour has its own test.
process.env.SOURCE_LOOKUP_ENABLED ??= 'true';
