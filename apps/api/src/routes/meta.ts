import type { FastifyInstance } from 'fastify';
import { query } from '../db/pool.js';
import { config } from '../config.js';

const startedAt = Date.now();

export default async function metaRoutes(app: FastifyInstance): Promise<void> {
  /** Liveness: is the process running? Never touches the database. */
  app.get('/healthz', async () => ({
    status: 'ok',
    uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
  }));

  /**
   * Readiness: can this instance actually serve traffic? Checks the database
   * and that migrations have been applied, so a half-deployed instance is
   * taken out of the load balancer rather than serving errors.
   */
  app.get('/readyz', async (_req, reply) => {
    try {
      const { rows } = await query<{ count: number; latest: string | null }>(
        `SELECT count(*)::int AS count, max(name) AS latest FROM schema_migrations`,
      );
      if ((rows[0]?.count ?? 0) === 0) {
        return reply.code(503).send({ status: 'not_ready', reason: 'no migrations applied' });
      }
      return { status: 'ready', migrations: rows[0].count, latestMigration: rows[0].latest };
    } catch (err) {
      app.log.error({ err }, 'readiness check failed');
      return reply.code(503).send({ status: 'not_ready', reason: 'database unavailable' });
    }
  });

  /**
   * Public site settings. Values still awaiting the owner's approval are
   * reported as such rather than being quietly rendered as if approved.
   */
  app.get('/api/settings/public', async () => {
    const { rows } = await query<{ key: string; value: unknown; needs_approval: boolean }>(
      `SELECT key, value, needs_approval FROM settings
        WHERE key IN ('institution_name', 'contact_email', 'legal.privacy_policy', 'legal.terms')`,
    );
    const settings: Record<string, { value: unknown; needsApproval: boolean }> = {};
    for (const row of rows) {
      settings[row.key] = { value: row.value, needsApproval: row.needs_approval };
    }
    return { settings, environment: config.env };
  });
}
