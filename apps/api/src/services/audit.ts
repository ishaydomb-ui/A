import type { FastifyRequest } from 'fastify';
import { query, type Queryable } from '../db/pool.js';
import { logger } from '../lib/logger.js';

export interface AuditEntry {
  action: string;
  actorId?: string | null;
  actorEmail?: string | null;
  entityType?: string | null;
  entityId?: string | null;
  ip?: string | null;
  userAgent?: string | null;
  detail?: Record<string, unknown>;
}

/**
 * Appends to the audit log. Failure to write an audit record is logged loudly
 * but never breaks the request that triggered it — except for writes made
 * inside a caller's transaction, where the caller decides.
 */
export async function audit(entry: AuditEntry, client?: Queryable): Promise<void> {
  const detail = entry.detail ?? {};
  try {
    await query(
      `INSERT INTO audit_log (actor_id, actor_email, action, entity_type, entity_id, ip, user_agent, detail)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [
        entry.actorId ?? null,
        entry.actorEmail ?? null,
        entry.action,
        entry.entityType ?? null,
        entry.entityId ?? null,
        entry.ip ?? null,
        entry.userAgent ?? null,
        JSON.stringify(detail),
      ],
      client,
    );
  } catch (err) {
    logger.error({ err, action: entry.action }, 'failed to write audit log entry');
    if (client) throw err;
  }
}

/** Pulls the request-scoped identity and client fingerprint for an audit row. */
export function auditContext(req: FastifyRequest): Pick<AuditEntry, 'actorId' | 'actorEmail' | 'ip' | 'userAgent'> {
  return {
    actorId: req.currentUser?.id ?? null,
    actorEmail: req.currentUser?.email ?? null,
    ip: clientIp(req),
    userAgent: req.headers['user-agent'] ?? null,
  };
}

export function clientIp(req: FastifyRequest): string | null {
  const ip = req.ip;
  if (!ip) return null;
  // Postgres `inet` rejects IPv4-mapped IPv6 in this form.
  return ip.startsWith('::ffff:') ? ip.slice(7) : ip;
}
