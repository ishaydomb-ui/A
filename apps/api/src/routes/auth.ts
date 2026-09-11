import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { ROLE_CAPABILITIES } from '@med/shared';
import { config } from '../config.js';
import { badRequest, unauthorized } from '../lib/errors.js';
import { clearSessionCookie, setSessionCookie } from '../plugins/auth.js';
import { audit, auditContext, clientIp } from '../services/audit.js';
import * as auth from '../services/auth.js';
import { passwordResetMail, sendMail } from '../services/mail.js';
import * as limits from '../services/rateLimit.js';
import { findById, toPublicUser } from '../services/users.js';
import QRCode from 'qrcode';

const emailSchema = z.string().trim().min(3).max(320).email();
const passwordSchema = z.string().min(1).max(200);

export default async function authRoutes(app: FastifyInstance): Promise<void> {
  /**
   * There is deliberately no POST /register. Accounts exist only because an
   * administrator created them.
   */

  app.post('/login', async (req, reply) => {
    const body = z.object({ email: emailSchema, password: passwordSchema }).parse(req.body);
    const ip = clientIp(req);
    const info = { ip, userAgent: req.headers['user-agent'] ?? null };

    // Limit by address and by source IP: neither a single account nor a
    // single client can be used to grind through passwords.
    await limits.enforce('loginAccount', body.email.toLowerCase());
    await limits.enforce('loginIp', `ip:${ip ?? 'unknown'}`);

    const outcome = await auth.login(body.email, body.password, info);

    if (outcome.status === 'ok') {
      await limits.reset('loginAccount', body.email.toLowerCase());
      setSessionCookie(reply, outcome.session.token, outcome.session.expiresAt);
      return {
        status: 'ok',
        user: toPublicUser(outcome.user),
        capabilities: ROLE_CAPABILITIES[outcome.user.role],
      };
    }

    // MFA still outstanding: no session cookie is issued yet.
    return {
      status: outcome.status,
      challengeToken: outcome.challengeToken,
      mfaRequiredByPolicy: await auth.mfaMandatoryForRole(outcome.user.role),
    };
  });

  app.post('/mfa/verify', async (req, reply) => {
    const body = z
      .object({ challengeToken: z.string().min(10), code: z.string().min(4).max(20) })
      .parse(req.body);
    const ip = clientIp(req);
    await limits.enforce('mfa', `ip:${ip ?? 'unknown'}`);
    const info = { ip, userAgent: req.headers['user-agent'] ?? null };
    const { user, session } = await auth.verifyMfaAndCreateSession(body.challengeToken, body.code, info);
    setSessionCookie(reply, session.token, session.expiresAt);
    return { status: 'ok', user: toPublicUser(user), capabilities: ROLE_CAPABILITIES[user.role] };
  });

  /** Enrolment forced at first sign-in for roles where MFA is mandatory. */
  app.post('/mfa/enroll/start', async (req) => {
    const body = z.object({ challengeToken: z.string().min(10) }).parse(req.body);
    await limits.enforce('mfa', `ip:${clientIp(req) ?? 'unknown'}`);
    const pending = await auth.peekMfaChallenge(body.challengeToken);
    const enrollment = await auth.beginMfaEnrollment(pending.userId, pending.email);
    return {
      secret: enrollment.secret,
      otpauthUrl: enrollment.otpauthUrl,
      qrDataUrl: await QRCode.toDataURL(enrollment.otpauthUrl),
    };
  });

  app.post('/mfa/enroll/complete', async (req, reply) => {
    const body = z
      .object({ challengeToken: z.string().min(10), code: z.string().min(6).max(10) })
      .parse(req.body);
    const ip = clientIp(req);
    await limits.enforce('mfa', `ip:${ip ?? 'unknown'}`);
    const info = { ip, userAgent: req.headers['user-agent'] ?? null };
    const { user, session, recoveryCodes } = await auth.completeEnrollmentAndCreateSession(
      body.challengeToken,
      body.code,
      info,
    );
    setSessionCookie(reply, session.token, session.expiresAt);
    return {
      status: 'ok',
      user: toPublicUser(user),
      capabilities: ROLE_CAPABILITIES[user.role],
      recoveryCodes,
    };
  });

  /** Voluntary enrolment for a signed-in user whose role does not require it. */
  app.post('/mfa/setup', { onRequest: [app.requireAuth] }, async (req) => {
    const user = req.currentUser!;
    const enrollment = await auth.beginMfaEnrollment(user.id, user.email);
    return {
      secret: enrollment.secret,
      otpauthUrl: enrollment.otpauthUrl,
      qrDataUrl: await QRCode.toDataURL(enrollment.otpauthUrl),
    };
  });

  app.post('/mfa/setup/confirm', { onRequest: [app.requireAuth] }, async (req) => {
    const body = z.object({ code: z.string().min(6).max(10) }).parse(req.body);
    const recoveryCodes = await auth.confirmMfaEnrollment(req.currentUser!.id, body.code);
    await audit({ ...auditContext(req), action: 'auth.mfa_enabled', entityType: 'user', entityId: req.currentUser!.id });
    return { recoveryCodes };
  });

  app.post('/invitations/accept', async (req, reply) => {
    const body = z
      .object({ token: z.string().min(10), password: passwordSchema })
      .parse(req.body);
    await limits.enforce('inviteAccept', `ip:${clientIp(req) ?? 'unknown'}`);
    const { user, mfaRequired } = await auth.acceptInvitation(body.token, body.password);

    // A role that mandates MFA must complete enrolment before it gets a
    // session, so we hand back a challenge rather than a cookie.
    if (mfaRequired) {
      const challengeToken = await auth.issueMfaChallengeFor(user.id);
      return { status: 'mfa_enrollment_required', challengeToken, mfaRequiredByPolicy: true };
    }

    const info = { ip: clientIp(req), userAgent: req.headers['user-agent'] ?? null };
    const session = await auth.createSession(user.id, info, true);
    setSessionCookie(reply, session.token, session.expiresAt);
    return { status: 'ok', user: toPublicUser(user), capabilities: ROLE_CAPABILITIES[user.role] };
  });

  /** Lets the accept-invitation page show who the invitation is for. */
  app.get('/invitations/:token', async (req) => {
    const { token } = z.object({ token: z.string().min(10) }).parse(req.params);
    const info = await auth.describeInvitation(token);
    if (!info) throw badRequest('invalid_token', 'This invitation link is not valid or has expired.');
    return info;
  });

  app.post('/password/forgot', async (req) => {
    const body = z.object({ email: emailSchema }).parse(req.body);
    await limits.enforce('passwordResetAccount', body.email.toLowerCase());
    await limits.enforce('passwordResetIp', `ip:${clientIp(req) ?? 'unknown'}`);

    const created = await auth.createPasswordReset(body.email);
    if (created) {
      const link = `${config.publicUrl}/reset-password?token=${encodeURIComponent(created.token)}`;
      await sendMail(passwordResetMail(created.user.email, link, Math.round(config.resetExpiryMs / 60000)));
      await audit({
        ...auditContext(req), action: 'auth.password_reset_requested',
        actorId: created.user.id, actorEmail: created.user.email,
        entityType: 'user', entityId: created.user.id,
      });
    }
    // Identical response either way: a caller cannot learn whether the
    // address is registered.
    return { status: 'ok', message: 'If that address has an account, a reset link has been sent.' };
  });

  app.post('/password/reset', async (req) => {
    const body = z
      .object({ token: z.string().min(10), password: passwordSchema })
      .parse(req.body);
    await limits.enforce('passwordResetIp', `ip:${clientIp(req) ?? 'unknown'}`);
    await auth.resetPassword(body.token, body.password);
    return { status: 'ok', message: 'Your password has been changed. Please sign in.' };
  });

  app.post('/password/change', { onRequest: [app.requireAuth] }, async (req) => {
    const body = z
      .object({ currentPassword: passwordSchema, newPassword: passwordSchema })
      .parse(req.body);
    const user = req.currentUser!;
    await auth.changePassword(user.id, body.currentPassword, body.newPassword, user.sessionId);
    return { status: 'ok' };
  });

  app.get('/me', async (req) => {
    if (!req.currentUser) throw unauthorized();
    const row = await findById(req.currentUser.id);
    if (!row) throw unauthorized();
    return { user: toPublicUser(row), capabilities: ROLE_CAPABILITIES[row.role] };
  });

  app.post('/logout', async (req, reply) => {
    if (req.currentUser) {
      await auth.revokeSession(req.currentUser.sessionId);
      await audit({ ...auditContext(req), action: 'auth.logout' });
    }
    clearSessionCookie(reply);
    return { status: 'ok' };
  });

  app.post('/logout-all', { onRequest: [app.requireAuth] }, async (req, reply) => {
    const count = await auth.revokeAllSessions(req.currentUser!.id);
    await audit({ ...auditContext(req), action: 'auth.logout_all', detail: { sessions: count } });
    clearSessionCookie(reply);
    return { status: 'ok', revoked: count };
  });
}

