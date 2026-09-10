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
   * Operational status for monitoring and for the administrator's own view.
   *
   * Deliberately behind the audit capability: the counts describe editorial
   * workload and would tell an anonymous caller how much unpublished content
   * exists. Liveness and readiness stay open for the orchestrator.
   */
  app.get('/api/admin/status', { onRequest: [app.requireCapability('audit:read')] }, async () => {
    const [workflow, findings, sessions, imports, users] = await Promise.all([
      query<{ state: string; count: number }>(
        `SELECT state::text AS state, count(*)::int AS count
           FROM medication_versions GROUP BY state ORDER BY state`,
      ),
      query<{ severity: string; count: number }>(
        `SELECT severity::text AS severity, count(*)::int AS count
           FROM review_findings WHERE status IN ('open','acknowledged')
          GROUP BY severity ORDER BY severity`,
      ),
      query<{ active: number }>(
        `SELECT count(*)::int AS active FROM sessions
          WHERE revoked_at IS NULL AND absolute_expires_at > now() AND idle_expires_at > now()`,
      ),
      query<{ id: string; original_filename: string; status: string; committed_at: Date | null }>(
        `SELECT id, original_filename, status::text AS status, committed_at
           FROM import_batches ORDER BY uploaded_at DESC LIMIT 1`,
      ),
      query<{ role: string; status: string; count: number }>(
        `SELECT role::text AS role, status::text AS status, count(*)::int AS count
           FROM users GROUP BY role, status`,
      ),
    ]);

    return {
      uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
      environment: config.env,
      catalogue: Object.fromEntries(workflow.rows.map((r) => [r.state, r.count])),
      openFindings: Object.fromEntries(findings.rows.map((r) => [r.severity, r.count])),
      activeSessions: sessions.rows[0]?.active ?? 0,
      lastImport: imports.rows[0] ?? null,
      users: users.rows,
    };
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
