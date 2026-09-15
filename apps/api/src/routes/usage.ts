import type { FastifyInstance } from 'fastify';
import { query } from '../db/pool.js';

/**
 * Who has an account, who is signing in, how much they use it, and where
 * they get stuck.
 *
 * Built from the audit log. Per person it reports volume — sign-ins,
 * searches, records opened, failed sign-ins — rather than a transcript of
 * what they read: the counts answer "is this working for them", which is
 * the question, and a list of every drug a colleague looked up answers a
 * different one nobody asked. The raw rows remain in audit_log if a
 * specific incident ever has to be reconstructed.
 *
 * Searches that returned nothing are shown with their terms, because there
 * the term is the point: it names content the catalogue does not have.
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
      last_active_at: Date | null;
      sign_ins_7d: number;
      sign_ins_30d: number;
      searches_30d: number;
      views_30d: number;
      empty_searches_30d: number;
      failed_logins_30d: number;
      lockouts_30d: number;
      last_device: string | null;
    }>(
      `SELECT u.id, u.email, u.display_name, u.role, u.status, u.created_at,
              a.last_sign_in_at,
              a.last_active_at,
              COALESCE(a.sign_ins_7d, 0)         AS sign_ins_7d,
              COALESCE(a.sign_ins_30d, 0)        AS sign_ins_30d,
              COALESCE(a.searches_30d, 0)        AS searches_30d,
              COALESCE(a.views_30d, 0)           AS views_30d,
              COALESCE(a.empty_searches_30d, 0)  AS empty_searches_30d,
              COALESCE(f.failed_logins_30d, 0)   AS failed_logins_30d,
              COALESCE(f.lockouts_30d, 0)        AS lockouts_30d,
              a.last_device
         FROM users u
         LEFT JOIN (
           SELECT actor_id,
                  max(occurred_at) FILTER (WHERE action = 'auth.login') AS last_sign_in_at,
                  max(occurred_at) AS last_active_at,
                  count(*) FILTER (WHERE action = 'auth.login'
                                     AND occurred_at > now() - interval '7 days')  AS sign_ins_7d,
                  count(*) FILTER (WHERE action = 'auth.login'
                                     AND occurred_at > now() - interval '30 days') AS sign_ins_30d,
                  count(*) FILTER (WHERE action = 'catalogue.searched'
                                     AND occurred_at > now() - interval '30 days') AS searches_30d,
                  count(*) FILTER (WHERE action = 'catalogue.viewed'
                                     AND occurred_at > now() - interval '30 days') AS views_30d,
                  count(*) FILTER (WHERE action = 'catalogue.searched'
                                     AND (detail->>'results')::int = 0
                                     AND occurred_at > now() - interval '30 days') AS empty_searches_30d,
                  (array_agg(user_agent ORDER BY occurred_at DESC)
                     FILTER (WHERE user_agent IS NOT NULL))[1] AS last_device
             FROM audit_log
            WHERE actor_id IS NOT NULL
            GROUP BY actor_id
         ) a ON a.actor_id = u.id
         LEFT JOIN (
           -- Failures are matched on the address tried, because a sign-in
           -- that fails has no session and so no actor id to attribute it to.
           SELECT lower(actor_email) AS email,
                  count(*) FILTER (WHERE action = 'auth.login_failed')   AS failed_logins_30d,
                  count(*) FILTER (WHERE action = 'auth.account_locked') AS lockouts_30d
             FROM audit_log
            WHERE action IN ('auth.login_failed', 'auth.account_locked')
              AND occurred_at > now() - interval '30 days'
            GROUP BY lower(actor_email)
         ) f ON f.email = u.email_normalized
        ORDER BY a.last_active_at DESC NULLS LAST, u.created_at`,
    );

    // What people looked for and did not find — the catalogue's own gaps,
    // in their words.
    const { rows: gaps } = await query<{ term: string; times: number; people: number }>(
      `SELECT detail->>'q' AS term, count(*)::int AS times, count(DISTINCT actor_id)::int AS people
         FROM audit_log
        WHERE action = 'catalogue.searched'
          AND (detail->>'results')::int = 0
          AND occurred_at > now() - interval '30 days'
        GROUP BY detail->>'q'
        ORDER BY times DESC, term
        LIMIT 25`,
    );

    const { rows: popular } = await query<{ slug: string; views: number; people: number }>(
      `SELECT entity_id AS slug, count(*)::int AS views, count(DISTINCT actor_id)::int AS people
         FROM audit_log
        WHERE action = 'catalogue.viewed'
          AND occurred_at > now() - interval '30 days'
          AND entity_id IS NOT NULL
        GROUP BY entity_id
        ORDER BY views DESC, slug
        LIMIT 10`,
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
      gaps,
      popular,
      people: people.map((p) => ({
        id: p.id,
        email: p.email,
        displayName: p.display_name,
        role: p.role,
        status: p.status,
        createdAt: p.created_at.toISOString(),
        lastSignInAt: p.last_sign_in_at ? p.last_sign_in_at.toISOString() : null,
        lastActiveAt: p.last_active_at ? p.last_active_at.toISOString() : null,
        signIns7d: p.sign_ins_7d,
        signIns30d: p.sign_ins_30d,
        searches30d: p.searches_30d,
        views30d: p.views_30d,
        emptySearches30d: p.empty_searches_30d,
        failedLogins30d: p.failed_logins_30d,
        lockouts30d: p.lockouts_30d,
        device: describeDevice(p.last_device),
      })),
    };
  });
}

/** Enough of the user agent to recognise a device, and no more. */
function describeDevice(userAgent: string | null): string | null {
  if (!userAgent) return null;
  const os = /iPhone|iPad/i.test(userAgent)
    ? 'iPhone'
    : /Android/i.test(userAgent)
      ? 'Android'
      : /Macintosh/i.test(userAgent)
        ? 'Mac'
        : /Windows/i.test(userAgent)
          ? 'Windows'
          : /Linux|X11/i.test(userAgent)
            ? 'Linux'
            : null;
  const browser = /Edg\//i.test(userAgent)
    ? 'Edge'
    : /Chrome\//i.test(userAgent)
      ? 'Chrome'
      : /Firefox\//i.test(userAgent)
        ? 'Firefox'
        : /Safari\//i.test(userAgent)
          ? 'Safari'
          : null;
  return [os, browser].filter(Boolean).join(' · ') || null;
}
