import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { ROLES, type Role } from '@med/shared';
import { config } from '../config.js';
import { query } from '../db/pool.js';
import { badRequest, conflict, notFound } from '../lib/errors.js';
import { audit, auditContext } from '../services/audit.js';
import * as auth from '../services/auth.js';
import { invitationMail, sendMail } from '../services/mail.js';
import { countAdmins, findById, listUsers, toPublicUser } from '../services/users.js';

const roleSchema = z.enum(ROLES as unknown as [Role, ...Role[]]);

export default async function userRoutes(app: FastifyInstance): Promise<void> {
  const requireUserAdmin = app.requireCapability('users:manage');

  app.get('/', { onRequest: [requireUserAdmin] }, async () => ({ users: await listUsers() }));

  app.post('/invitations', { onRequest: [requireUserAdmin] }, async (req) => {
    const body = z
      .object({
        email: z.string().trim().email().max(320),
        displayName: z.string().trim().min(1).max(200),
        role: roleSchema,
      })
      .parse(req.body);

    const result = await auth.inviteUser({ ...body, invitedBy: req.currentUser!.id });
    const link = `${config.publicUrl}/accept-invitation?token=${encodeURIComponent(result.token)}`;
    await sendMail(
      invitationMail(body.email, body.displayName, link, Math.round(config.inviteExpiryMs / 3_600_000)),
    );
    await audit({
      ...auditContext(req),
      action: 'users.invited',
      entityType: 'user',
      entityId: result.userId,
      detail: { email: body.email, role: body.role },
    });

    return {
      status: 'ok',
      invitationId: result.invitationId,
      userId: result.userId,
      expiresAt: result.expiresAt.toISOString(),
      // Shown to the admin so the invitation can be delivered by hand when no
      // SMTP server is configured. It is never stored in plaintext.
      invitationLink: link,
    };
  });

  app.post('/:id/invitations/resend', { onRequest: [requireUserAdmin] }, async (req) => {
    const { id } = z.object({ id: z.string().uuid() }).parse(req.params);
    const user = await findById(id);
    if (!user) throw notFound('user_not_found', 'No such user.');
    if (user.status !== 'invited') {
      throw badRequest('already_active', 'That account has already been set up.');
    }
    const result = await auth.inviteUser({
      email: user.email,
      displayName: user.display_name,
      role: user.role,
      invitedBy: req.currentUser!.id,
    });
    const link = `${config.publicUrl}/accept-invitation?token=${encodeURIComponent(result.token)}`;
    await sendMail(invitationMail(user.email, user.display_name, link, Math.round(config.inviteExpiryMs / 3_600_000)));
    await audit({ ...auditContext(req), action: 'users.invitation_resent', entityType: 'user', entityId: id });
    return { status: 'ok', invitationLink: link, expiresAt: result.expiresAt.toISOString() };
  });

  app.patch('/:id', { onRequest: [requireUserAdmin] }, async (req) => {
    const { id } = z.object({ id: z.string().uuid() }).parse(req.params);
    const body = z
      .object({
        displayName: z.string().trim().min(1).max(200).optional(),
        role: roleSchema.optional(),
        status: z.enum(['active', 'suspended', 'deactivated']).optional(),
      })
      .parse(req.body);

    const user = await findById(id);
    if (!user) throw notFound('user_not_found', 'No such user.');

    // Guard rails: an administrator must not be able to lock everyone out,
    // including themselves, by demoting or disabling the last active admin.
    const losingAdmin =
      user.role === 'admin' &&
      user.status === 'active' &&
      ((body.role && body.role !== 'admin') || (body.status && body.status !== 'active'));
    if (losingAdmin && (await countAdmins()) <= 1) {
      throw conflict('last_admin', 'This is the last active administrator; promote another one first.');
    }
    if (user.id === req.currentUser!.id && body.status && body.status !== 'active') {
      throw conflict('self_disable', 'You cannot disable your own account.');
    }

    const { rows } = await query(
      `UPDATE users
          SET display_name = COALESCE($2, display_name),
              role         = COALESCE($3, role),
              status       = COALESCE($4, status),
              updated_at   = now()
        WHERE id = $1 RETURNING *`,
      [id, body.displayName ?? null, body.role ?? null, body.status ?? null],
    );

    // A change of role or a suspension takes effect immediately, not at the
    // next natural session expiry.
    if (body.role || (body.status && body.status !== 'active')) {
      await auth.revokeAllSessions(id);
    }
    await audit({
      ...auditContext(req), action: 'users.updated', entityType: 'user', entityId: id,
      detail: {
        before: { role: user.role, status: user.status, displayName: user.display_name },
        after: body,
      },
    });
    return { user: toPublicUser(rows[0] as never) };
  });

  app.post('/:id/mfa/reset', { onRequest: [requireUserAdmin] }, async (req) => {
    const { id } = z.object({ id: z.string().uuid() }).parse(req.params);
    const user = await findById(id);
    if (!user) throw notFound('user_not_found', 'No such user.');
    await query(
      `UPDATE users SET mfa_secret = NULL, mfa_enabled_at = NULL, mfa_recovery_codes = NULL, updated_at = now()
        WHERE id = $1`,
      [id],
    );
    await auth.revokeAllSessions(id);
    await audit({
      ...auditContext(req), action: 'users.mfa_reset', entityType: 'user', entityId: id,
      detail: { reason: 'administrator reset' },
    });
    return { status: 'ok' };
  });

  app.get('/audit-log', { onRequest: [app.requireCapability('audit:read')] }, async (req) => {
    const q = z
      .object({
        limit: z.coerce.number().int().min(1).max(200).default(50),
        before: z.coerce.number().int().positive().optional(),
        action: z.string().max(100).optional(),
        actorId: z.string().uuid().optional(),
      })
      .parse(req.query);

    const { rows } = await query(
      `SELECT id, occurred_at, actor_id, actor_email, action, entity_type, entity_id, ip, detail
         FROM audit_log
        WHERE ($1::bigint IS NULL OR id < $1)
          AND ($2::text   IS NULL OR action = $2)
          AND ($3::uuid   IS NULL OR actor_id = $3)
        ORDER BY id DESC
        LIMIT $4`,
      [q.before ?? null, q.action ?? null, q.actorId ?? null, q.limit],
    );
    return { entries: rows, nextBefore: rows.length === q.limit ? rows[rows.length - 1].id : null };
  });
}
