import { buildApp } from './app.js';
import { config } from './config.js';
import { closePool } from './db/pool.js';
import { up } from './db/migrate.js';
import { logger } from './lib/logger.js';
import { initMail } from './services/mail.js';
import { purgeExpiredSessions } from './services/auth.js';
import { ensureDefaultSettings } from './services/settings.js';
import { purgeExpired as purgeRateLimits } from './services/rateLimit.js';

async function main(): Promise<void> {
  if (config.runMigrationsOnStart) {
    logger.info('applying pending migrations');
    const applied = await up((msg) => logger.info(msg));
    logger.info({ applied: applied.length }, 'migrations up to date');
  }

  await initMail();
  // A setting added by a later migration must exist even where the database
  // was created some other way.
  await ensureDefaultSettings();
  const app = await buildApp();

  // Housekeeping: expired sessions and rate-limit windows are pruned hourly.
  const housekeeping = setInterval(() => {
    void purgeExpiredSessions().catch((err) => logger.error({ err }, 'session purge failed'));
    void purgeRateLimits().catch((err) => logger.error({ err }, 'rate limit purge failed'));
  }, 3_600_000);
  housekeeping.unref();

  await app.listen({ port: config.port, host: config.host });
  logger.info({ port: config.port, env: config.env }, 'api listening');

  const shutdown = async (signal: string): Promise<void> => {
    logger.info({ signal }, 'shutting down');
    clearInterval(housekeeping);
    try {
      await app.close();
      await closePool();
      process.exit(0);
    } catch (err) {
      logger.error({ err }, 'error during shutdown');
      process.exit(1);
    }
  };
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));
}

main().catch((err) => {
  logger.fatal({ err }, 'failed to start');
  process.exit(1);
});
