import type { FastifyInstance } from 'fastify';
import { query } from '../db/pool.js';

/**
 * Who has an account, and who is actually signing in.
 *
 * Built entirely from the audit log, which already records sign-ins. It
 * deliberately does not report what anyone looked up: reading is not
 * recorded anywhere, and what a clinician searches for can imply things
 * about their patients and about themselves. Adding that is a decision for
 * the people being measured to take knowingly, not a side effect of wanting
 * to know whether a pilot is being used.
 */
export default async function usageRoutes(app: FastifyInstance): Promise<void> {
  const canRead = app.requireCapability('audit:read');

  app.get('/usage', { onRequest: [canRead] }, async () => {
    const { rows: people } = await query<{
      id: string;
      email: string;
      display_name: string;
      role: string;
      status: string;
      created_at: Date;
      last_sign_in_at: Date | null;
      sign_ins_7d: number;
      sign_ins_30d: number;
    }>(
      `SELECT u.id, u.email, u.display_name, u.role, u.status, u.created_at,
              s.last_sign_in_at,
              COALESCE(s.sign_ins_7d, 0)  AS sign_ins_7d,
              COALESCE(s.sign_ins_30d, 0) AS sign_ins_30d
         FROM users u
         LEFT JOIN (
           SELECT actor_id,
                  max(occurred_at) AS last_sign_in_at,
                  count(*) FILTER (WHERE occurred_at > now() - interval '7 days')  AS sign_ins_7d,
                  count(*) FILTER (WHERE occurred_at > now() - interval '30 days') AS sign_ins_30d
             FROM audit_log
            WHERE action = 'auth.login'
            GROUP BY actor_id
         ) s ON s.actor_id = u.id
        ORDER BY s.last_sign_in_at DESC NULLS LAST, u.created_at`,
    );

    const { rows: totals } = await query<{
      accounts: number;
      active: number;
      pending: number;
      signed_in_7d: number;
      never_signed_in: number;
    }>(
      `SELECT count(*)::int AS accounts,
              count(*) FILTER (WHERE status = 'active')::int  AS active,
              count(*) FILTER (WHERE status = 'invited')::int AS pending,
              count(*) FILTER (WHERE id IN (
                SELECT actor_id FROM audit_log
                 WHERE action = 'auth.login' AND occurred_at > now() - interval '7 days'
              ))::int AS signed_in_7d,
              count(*) FILTER (WHERE status = 'active' AND id NOT IN (
                SELECT actor_id FROM audit_log WHERE action = 'auth.login' AND actor_id IS NOT NULL
              ))::int AS never_signed_in
         FROM users`,
    );

    // Daily counts for the last fortnight, zero-filled so a quiet day reads
    // as a quiet day rather than disappearing from the series.
    const { rows: daily } = await query<{ day: string; sign_ins: number; people: number }>(
      `SELECT to_char(d.day, 'YYYY-MM-DD') AS day,
              count(a.id)::int AS sign_ins,
              count(DISTINCT a.actor_id)::int AS people
         FROM generate_series(current_date - interval '13 days', current_date, interval '1 day') AS d(day)
         LEFT JOIN audit_log a
                ON a.action = 'auth.login'
               AND a.occurred_at >= d.day
               AND a.occurred_at <  d.day + interval '1 day'
        GROUP BY d.day
        ORDER BY d.day`,
    );

    return {
      totals: totals[0],
      daily,
      people: people.map((p) => ({
        id: p.id,
        email: p.email,
        displayName: p.display_name,
        role: p.role,
        status: p.status,
        createdAt: p.created_at.toISOString(),
        lastSignInAt: p.last_sign_in_at ? p.last_sign_in_at.toISOString() : null,
        signIns7d: p.sign_ins_7d,
        signIns30d: p.sign_ins_30d,
      })),
    };
  });
}
