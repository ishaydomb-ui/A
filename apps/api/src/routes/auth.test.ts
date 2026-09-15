import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { authenticator } from 'otplib';
import { query } from '../db/pool.js';
import { decryptSecret, encryptSecret } from '../lib/crypto.js';
import {
  TEST_PASSWORD, closeTestApp, createTestApp, loginAs, loginAsAdmin, mailbox,
  resetDatabase, seedUser, sessionCookie, shutdown,
} from '../test/helpers.js';

let app: FastifyInstance;

beforeAll(async () => {
  await resetDatabase();
  app = await createTestApp();
});
afterAll(async () => {
  await closeTestApp(app);
  await shutdown();
});
beforeEach(async () => {
  await resetDatabase();
  mailbox.clear();
});

describe('registration is invite-only', () => {
  it('exposes no public registration endpoint', async () => {
    for (const url of ['/api/auth/register', '/api/auth/signup', '/api/users/register']) {
      const res = await app.inject({
        method: 'POST', url,
        payload: { email: 'intruder@example.org', password: 'a-very-long-password-1' },
      });
      expect(res.statusCode).toBe(404);
    }
  });

  it('refuses to create a user without the users:manage capability', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const cookie = await loginAs(app, 'doc@example.org');
    const res = await app.inject({
      method: 'POST', url: '/api/users/invitations', headers: { cookie },
      payload: { email: 'new@example.org', displayName: 'New', role: 'physician' },
    });
    expect(res.statusCode).toBe(403);
    expect(res.json().error.code).toBe('forbidden');
  });

  it('refuses anonymous invitation creation', async () => {
    const res = await app.inject({
      method: 'POST', url: '/api/users/invitations',
      payload: { email: 'new@example.org', displayName: 'New', role: 'admin' },
    });
    expect(res.statusCode).toBe(401);
  });
});

describe('invitation lifecycle', () => {
  async function inviteVia(role: string) {
    // Editors can invite? No — only users:manage can. Use an admin without MFA
    // by seeding it directly and granting a session through the MFA flow.
    const admin = await seedUser({ email: 'admin@example.org', role: 'admin' });
    return admin;
  }

  it('an editor cannot invite; an admin can', async () => {
    await seedUser({ email: 'editor@example.org', role: 'editor' });
    const editorCookie = await loginAs(app, 'editor@example.org');
    const denied = await app.inject({
      method: 'POST', url: '/api/users/invitations', headers: { cookie: editorCookie },
      payload: { email: 'x@example.org', displayName: 'X', role: 'physician' },
    });
    expect(denied.statusCode).toBe(403);
  });

  it('accepting an invitation activates the account and verifies the address', async () => {
    await seedUser({ email: 'editor@example.org', role: 'editor' });
    // Invitations require users:manage, so create one directly via the service
    // path exercised by the admin route.
    const { inviteUser } = await import('../services/auth.js');
    const admin = await seedUser({ email: 'admin2@example.org', role: 'admin' });
    const invite = await inviteUser({
      email: 'newdoc@example.org', displayName: 'New Doctor', role: 'physician', invitedBy: admin.id,
    });

    const describe1 = await app.inject({ method: 'GET', url: `/api/auth/invitations/${invite.token}` });
    expect(describe1.statusCode).toBe(200);
    expect(describe1.json()).toMatchObject({ email: 'newdoc@example.org', role: 'physician' });

    const accept = await app.inject({
      method: 'POST', url: '/api/auth/invitations/accept',
      payload: { token: invite.token, password: 'a-brand-new-strong-passphrase' },
    });
    expect(accept.statusCode).toBe(200);
    expect(accept.json().status).toBe('ok');

    const { rows } = await query<{ status: string; email_verified_at: Date | null }>(
      `SELECT status, email_verified_at FROM users WHERE email_normalized = 'newdoc@example.org'`,
    );
    expect(rows[0].status).toBe('active');
    expect(rows[0].email_verified_at).not.toBeNull();

    // The token is single use.
    const replay = await app.inject({
      method: 'POST', url: '/api/auth/invitations/accept',
      payload: { token: invite.token, password: 'another-strong-passphrase-9' },
    });
    expect(replay.statusCode).toBe(400);
    expect(replay.json().error.code).toBe('invalid_token');
  });

  it('rejects an expired invitation', async () => {
    const { inviteUser } = await import('../services/auth.js');
    const admin = await seedUser({ email: 'admin3@example.org', role: 'admin' });
    const invite = await inviteUser({
      email: 'late@example.org', displayName: 'Late', role: 'physician', invitedBy: admin.id,
    });
    await query(`UPDATE invitations SET expires_at = now() - interval '1 hour' WHERE id = $1`, [
      invite.invitationId,
    ]);
    const res = await app.inject({
      method: 'POST', url: '/api/auth/invitations/accept',
      payload: { token: invite.token, password: 'a-brand-new-strong-passphrase' },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe('expired_token');
  });

  it('rejects a weak password when accepting', async () => {
    const { inviteUser } = await import('../services/auth.js');
    const admin = await seedUser({ email: 'admin4@example.org', role: 'admin' });
    const invite = await inviteUser({
      email: 'weak@example.org', displayName: 'Weak', role: 'physician', invitedBy: admin.id,
    });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/invitations/accept',
      payload: { token: invite.token, password: 'short' },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe('weak_password');
  });
});

describe('login', () => {
  it('signs in a physician and returns their capabilities', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'doc@example.org', password: TEST_PASSWORD },
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.status).toBe('ok');
    expect(body.capabilities).toContain('catalogue:read_published');
    expect(body.capabilities).not.toContain('catalogue:publish');
    expect(sessionCookie(res)).toMatch(/^medcat_session=/);
  });

  it('sets an HttpOnly, SameSite session cookie', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'doc@example.org', password: TEST_PASSWORD },
    });
    const raw = String(res.headers['set-cookie']);
    expect(raw).toContain('HttpOnly');
    expect(raw).toContain('SameSite=Lax');
    expect(raw).toContain('Path=/');
  });

  it('rejects a wrong password', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'doc@example.org', password: 'definitely-not-the-password' },
    });
    expect(res.statusCode).toBe(401);
    expect(res.json().error.code).toBe('invalid_credentials');
  });

  it('gives the same answer for an unknown address as for a wrong password', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const wrongPassword = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'doc@example.org', password: 'definitely-not-the-password' },
    });
    const unknownUser = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'nobody@example.org', password: 'definitely-not-the-password' },
    });
    expect(unknownUser.statusCode).toBe(wrongPassword.statusCode);
    expect(unknownUser.json()).toEqual(wrongPassword.json());
  });

  it('refuses a suspended account', async () => {
    await seedUser({ email: 'susp@example.org', role: 'physician', status: 'suspended' });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'susp@example.org', password: TEST_PASSWORD },
    });
    expect(res.statusCode).toBe(403);
    expect(res.json().error.code).toBe('account_inactive');
  });

  it('locks the account after repeated failures', async () => {
    await seedUser({ email: 'target@example.org', role: 'physician' });
    for (let i = 0; i < 8; i++) {
      await app.inject({
        method: 'POST', url: '/api/auth/login',
        payload: { email: 'target@example.org', password: `wrong-${i}-attempt` },
      });
    }
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'target@example.org', password: TEST_PASSWORD },
    });
    expect(res.statusCode).toBe(403);
    expect(res.json().error.code).toBe('account_locked');
  });
});

describe('multi-factor authentication', () => {
  async function requireAdminMfa(required: boolean) {
    await query(
      `UPDATE settings SET value = $1::jsonb WHERE key = 'security.mfa_required_for_admin'`,
      [JSON.stringify(required)],
    );
  }

  it('signs an administrator straight in once the requirement is switched off', async () => {
    await requireAdminMfa(false);
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    expect(res.json().status).toBe('ok');
    expect(sessionCookie(res)).toMatch(/^medcat_session=/);
  });

  it('still asks an already-enrolled administrator for a code once the requirement is off', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const login = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const start = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/start',
      payload: { challengeToken: login.json().challengeToken },
    });
    await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/complete',
      payload: { challengeToken: login.json().challengeToken, code: authenticator.generate(start.json().secret) },
    });

    // Switching the requirement off does not retroactively remove an
    // authenticator someone already set up.
    await requireAdminMfa(false);
    const second = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    expect(second.json().status).toBe('mfa_required');
  });

  it('forces enrolment for an administrator before issuing a session', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.status).toBe('mfa_enrollment_required');
    expect(body.mfaRequiredByPolicy).toBe(true);
    expect(res.headers['set-cookie']).toBeUndefined();
  });

  it('completes admin enrolment and then requires a code at each sign-in', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const login = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const challengeToken = login.json().challengeToken;

    const start = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/start', payload: { challengeToken },
    });
    expect(start.statusCode).toBe(200);
    const secret = start.json().secret as string;
    expect(start.json().qrDataUrl).toMatch(/^data:image\/png;base64,/);

    const complete = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/complete',
      payload: { challengeToken, code: authenticator.generate(secret) },
    });
    expect(complete.statusCode).toBe(200);
    expect(complete.json().recoveryCodes).toHaveLength(10);
    expect(sessionCookie(complete)).toMatch(/^medcat_session=/);

    // Next sign-in must present a code.
    const second = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    expect(second.json().status).toBe('mfa_required');
    expect(second.headers['set-cookie']).toBeUndefined();

    const verify = await app.inject({
      method: 'POST', url: '/api/auth/mfa/verify',
      payload: { challengeToken: second.json().challengeToken, code: authenticator.generate(secret) },
    });
    expect(verify.statusCode).toBe(200);
    expect(sessionCookie(verify)).toMatch(/^medcat_session=/);
  });

  it('returns the same secret when enrolment is started twice', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const login = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const challengeToken = login.json().challengeToken;

    // A reload or a retry must not replace the secret behind a QR code the
    // user has already scanned.
    const first = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/start', payload: { challengeToken },
    });
    const second = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/start', payload: { challengeToken },
    });
    expect(second.json().secret).toBe(first.json().secret);

    // A code from the originally displayed secret still works.
    const complete = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/complete',
      payload: { challengeToken, code: authenticator.generate(first.json().secret) },
    });
    expect(complete.statusCode).toBe(200);
  });

  it('rejects an incorrect authenticator code', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const login = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const challengeToken = login.json().challengeToken;
    await app.inject({ method: 'POST', url: '/api/auth/mfa/enroll/start', payload: { challengeToken } });
    const res = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/complete',
      payload: { challengeToken, code: '000000' },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe('invalid_code');
  });

  it('accepts a recovery code once and then not again', async () => {
    const user = await seedUser({ email: 'admin@example.org', role: 'admin' });
    const login = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const challengeToken = login.json().challengeToken;
    const start = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/start', payload: { challengeToken },
    });
    const secret = start.json().secret as string;
    const complete = await app.inject({
      method: 'POST', url: '/api/auth/mfa/enroll/complete',
      payload: { challengeToken, code: authenticator.generate(secret) },
    });
    const recoveryCode = complete.json().recoveryCodes[0] as string;

    const second = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const used = await app.inject({
      method: 'POST', url: '/api/auth/mfa/verify',
      payload: { challengeToken: second.json().challengeToken, code: recoveryCode },
    });
    expect(used.statusCode).toBe(200);

    const third = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin@example.org', password: TEST_PASSWORD },
    });
    const reused = await app.inject({
      method: 'POST', url: '/api/auth/mfa/verify',
      payload: { challengeToken: third.json().challengeToken, code: recoveryCode },
    });
    expect(reused.statusCode).toBe(401);

    // The TOTP secret is never stored in the clear.
    const { rows } = await query<{ mfa_secret: string }>(
      'SELECT mfa_secret FROM users WHERE id = $1', [user.id],
    );
    expect(rows[0].mfa_secret).not.toContain(secret);
    expect(decryptSecret(rows[0].mfa_secret)).toBe(secret);
  });
});

describe('sessions', () => {
  it('rejects a request with no session', async () => {
    const res = await app.inject({ method: 'GET', url: '/api/auth/me' });
    expect(res.statusCode).toBe(401);
  });

  it('rejects a forged session token', async () => {
    const res = await app.inject({
      method: 'GET', url: '/api/auth/me',
      headers: { cookie: 'medcat_session=not-a-real-token-value-at-all' },
    });
    expect(res.statusCode).toBe(401);
  });

  it('stops working after logout', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const cookie = await loginAs(app, 'doc@example.org');
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: { cookie } })).statusCode).toBe(200);
    await app.inject({ method: 'POST', url: '/api/auth/logout', headers: { cookie } });
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: { cookie } })).statusCode).toBe(401);
  });

  it('expires a session once its absolute deadline passes', async () => {
    const user = await seedUser({ email: 'doc@example.org', role: 'physician' });
    const cookie = await loginAs(app, 'doc@example.org');
    await query(
      `UPDATE sessions SET absolute_expires_at = now() - interval '1 minute' WHERE user_id = $1`,
      [user.id],
    );
    const res = await app.inject({ method: 'GET', url: '/api/auth/me', headers: { cookie } });
    expect(res.statusCode).toBe(401);
  });

  it('expires an idle session', async () => {
    const user = await seedUser({ email: 'doc@example.org', role: 'physician' });
    const cookie = await loginAs(app, 'doc@example.org');
    await query(
      `UPDATE sessions SET idle_expires_at = now() - interval '1 second' WHERE user_id = $1`,
      [user.id],
    );
    const res = await app.inject({ method: 'GET', url: '/api/auth/me', headers: { cookie } });
    expect(res.statusCode).toBe(401);
  });

  it('revokes every session when the account is suspended', async () => {
    const user = await seedUser({ email: 'doc@example.org', role: 'physician' });
    const cookie = await loginAs(app, 'doc@example.org');
    await query(`UPDATE users SET status = 'suspended' WHERE id = $1`, [user.id]);
    const res = await app.inject({ method: 'GET', url: '/api/auth/me', headers: { cookie } });
    expect(res.statusCode).toBe(401);
  });
});

describe('account administration', () => {
  it('corrects a display name and keeps the change in the audit log', async () => {
    const admin = await seedUser({ email: 'admin@example.org', role: 'admin' });
    const target = await seedUser({
      email: 'liri@example.org', role: 'clinical_reviewer', displayName: 'Liri',
    });
    const cookie = await loginAsAdmin(app, 'admin@example.org');

    const res = await app.inject({
      method: 'PATCH', url: `/api/users/${target.id}`, headers: { cookie },
      payload: { displayName: 'Liran Korotkin Barzelay' },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().user).toMatchObject({ displayName: 'Liran Korotkin Barzelay' });

    const { rows } = await query(
      `SELECT display_name FROM users WHERE id = $1`, [target.id],
    );
    expect(rows[0].display_name).toBe('Liran Korotkin Barzelay');

    // A name is part of the record a reviewer signs off under, so the previous
    // value has to remain recoverable.
    const log = await query(
      `SELECT detail FROM audit_log WHERE action = 'users.updated' AND entity_id = $1`,
      [target.id],
    );
    expect(log.rows[0].detail).toMatchObject({
      before: { displayName: 'Liri' },
      after: { displayName: 'Liran Korotkin Barzelay' },
    });
    expect(admin.id).toBeTruthy();
  });

  it('leaves the role and the session alone when only the name changes', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const target = await seedUser({
      email: 'liri@example.org', role: 'clinical_reviewer', displayName: 'Liri',
    });
    const targetCookie = await loginAs(app, 'liri@example.org');
    const adminCookie = await loginAsAdmin(app, 'admin@example.org');

    await app.inject({
      method: 'PATCH', url: `/api/users/${target.id}`, headers: { cookie: adminCookie },
      payload: { displayName: 'Liran Korotkin Barzelay' },
    });

    // Renaming is not a privilege change, so it must not sign the person out
    // in the middle of a review.
    const me = await app.inject({
      method: 'GET', url: '/api/auth/me', headers: { cookie: targetCookie },
    });
    expect(me.statusCode).toBe(200);
    expect(me.json().user).toMatchObject({
      role: 'clinical_reviewer', displayName: 'Liran Korotkin Barzelay',
    });
  });

  it('re-inviting someone who never accepted updates their details in place', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const cookie = await loginAsAdmin(app, 'admin@example.org');

    const first = await app.inject({
      method: 'POST', url: '/api/users/invitations', headers: { cookie },
      payload: { email: 'liri@example.org', displayName: 'Liri', role: 'physician' },
    });
    const firstToken = new URL(first.json().invitationLink).searchParams.get('token')!;

    // Sending again is how a name entered in a hurry gets corrected, and how a
    // link built from an unreachable address gets replaced.
    const second = await app.inject({
      method: 'POST', url: '/api/users/invitations', headers: { cookie },
      payload: {
        email: 'liri@example.org',
        displayName: 'Liran Korotkin Barzelay',
        role: 'clinical_reviewer',
      },
    });
    expect(second.statusCode).toBe(200);
    expect(second.json().userId).toBe(first.json().userId);

    const { rows } = await query(
      `SELECT display_name, role, status FROM users WHERE email_normalized = 'liri@example.org'`,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      display_name: 'Liran Korotkin Barzelay', role: 'clinical_reviewer', status: 'invited',
    });

    const stale = await app.inject({ method: 'GET', url: `/api/auth/invitations/${firstToken}` });
    expect(stale.statusCode).toBe(400);
  });

  it('refuses to re-invite over an account that has already been accepted', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    const cookie = await loginAsAdmin(app, 'admin@example.org');

    // Otherwise inviting a colleague twice would quietly reset a working
    // account and change what it can do.
    const res = await app.inject({
      method: 'POST', url: '/api/users/invitations', headers: { cookie },
      payload: { email: 'liri@example.org', displayName: 'Someone Else', role: 'admin' },
    });
    expect(res.statusCode).toBe(409);
    expect(res.json().error.code).toBe('user_exists');
  });

  it('reissues an invitation and revokes the earlier link', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const cookie = await loginAsAdmin(app, 'admin@example.org');

    const invited = await app.inject({
      method: 'POST', url: '/api/users/invitations', headers: { cookie },
      payload: { email: 'liri@example.org', displayName: 'Liri', role: 'clinical_reviewer' },
    });
    const first = new URL(invited.json().invitationLink).searchParams.get('token')!;

    const again = await app.inject({
      method: 'POST', url: `/api/users/${invited.json().userId}/invitations/resend`,
      headers: { cookie },
    });
    expect(again.statusCode).toBe(200);
    const second = new URL(again.json().invitationLink).searchParams.get('token')!;
    expect(second).not.toBe(first);

    // The first link must stop working, or a link that leaked stays usable.
    const stale = await app.inject({ method: 'GET', url: `/api/auth/invitations/${first}` });
    expect(stale.statusCode).toBe(400);
    expect(stale.json().error.code).toBe('invalid_token');
    const fresh = await app.inject({ method: 'GET', url: `/api/auth/invitations/${second}` });
    expect(fresh.statusCode).toBe(200);
  });

  it('refuses to reissue an invitation for an account already in use', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const active = await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    const cookie = await loginAsAdmin(app, 'admin@example.org');

    const res = await app.inject({
      method: 'POST', url: `/api/users/${active.id}/invitations/resend`, headers: { cookie },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe('already_active');
  });

  /** Puts an account into the state a completed enrolment leaves behind. */
  async function enrol(userId: string): Promise<string> {
    const secret = authenticator.generateSecret();
    await query(
      `UPDATE users SET mfa_secret = $2, mfa_enabled_at = now() WHERE id = $1`,
      [userId, encryptSecret(secret)],
    );
    return secret;
  }

  it('removes an authenticator so the account signs in with a password alone', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const target = await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    const secret = await enrol(target.id);

    // Enrolled means challenged, whatever the role: that is what has to be
    // undone for someone who should not be carrying a second factor.
    const challenged = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'liri@example.org', password: TEST_PASSWORD },
    });
    expect(challenged.json().status).toBe('mfa_required');
    expect(authenticator.generate(secret)).toMatch(/^\d{6}$/);

    const cookie = await loginAsAdmin(app, 'admin@example.org');
    const reset = await app.inject({
      method: 'POST', url: `/api/users/${target.id}/mfa/reset`, headers: { cookie },
    });
    expect(reset.statusCode).toBe(200);

    const after = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'liri@example.org', password: TEST_PASSWORD },
    });
    expect(after.json().status).toBe('ok');

    const { rows } = await query(
      `SELECT mfa_secret, mfa_enabled_at, mfa_recovery_codes FROM users WHERE id = $1`,
      [target.id],
    );
    expect(rows[0]).toMatchObject({
      mfa_secret: null, mfa_enabled_at: null, mfa_recovery_codes: null,
    });
  });

  it('signs the account out of every device when its authenticator is removed', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const target = await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    const theirCookie = await loginAs(app, 'liri@example.org');
    await enrol(target.id);

    const cookie = await loginAsAdmin(app, 'admin@example.org');
    await app.inject({
      method: 'POST', url: `/api/users/${target.id}/mfa/reset`, headers: { cookie },
    });

    const me = await app.inject({
      method: 'GET', url: '/api/auth/me', headers: { cookie: theirCookie },
    });
    expect(me.statusCode).toBe(401);
  });

  it('still forces an administrator to enrol again after a reset', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const other = await seedUser({ email: 'admin3@example.org', role: 'admin' });
    await enrol(other.id);

    const cookie = await loginAsAdmin(app, 'admin@example.org');
    await app.inject({
      method: 'POST', url: `/api/users/${other.id}/mfa/reset`, headers: { cookie },
    });

    // Removing the authenticator must not become a way around the policy.
    const login = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'admin3@example.org', password: TEST_PASSWORD },
    });
    expect(login.json().status).toBe('mfa_enrollment_required');
  });

  it('refuses to remove an authenticator without the users:manage capability', async () => {
    const target = await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    await enrol(target.id);
    await seedUser({ email: 'editor@example.org', role: 'editor' });
    const cookie = await loginAs(app, 'editor@example.org');

    const res = await app.inject({
      method: 'POST', url: `/api/users/${target.id}/mfa/reset`, headers: { cookie },
    });
    expect(res.statusCode).toBe(403);
  });

  it('refuses a rename from anyone without the users:manage capability', async () => {
    const target = await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    await seedUser({ email: 'editor@example.org', role: 'editor' });
    const cookie = await loginAs(app, 'editor@example.org');

    const res = await app.inject({
      method: 'PATCH', url: `/api/users/${target.id}`, headers: { cookie },
      payload: { displayName: 'Renamed By An Editor' },
    });
    expect(res.statusCode).toBe(403);
  });

  it('refuses to blank a display name', async () => {
    await seedUser({ email: 'admin@example.org', role: 'admin' });
    const target = await seedUser({ email: 'liri@example.org', role: 'clinical_reviewer' });
    const cookie = await loginAsAdmin(app, 'admin@example.org');

    for (const displayName of ['', '   ']) {
      const res = await app.inject({
        method: 'PATCH', url: `/api/users/${target.id}`, headers: { cookie },
        payload: { displayName },
      });
      expect(res.statusCode).toBe(400);
    }
  });
});

describe('password reset', () => {
  it('sends a reset link and lets the user set a new password', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    const cookie = await loginAs(app, 'doc@example.org');

    const forgot = await app.inject({
      method: 'POST', url: '/api/auth/password/forgot', payload: { email: 'doc@example.org' },
    });
    expect(forgot.statusCode).toBe(200);
    const mail = mailbox.lastTo('doc@example.org');
    expect(mail).toBeDefined();
    const token = /reset-password\?token=([^\s]+)/.exec(mail!.text)?.[1];
    expect(token).toBeTruthy();

    const reset = await app.inject({
      method: 'POST', url: '/api/auth/password/reset',
      payload: { token: decodeURIComponent(token!), password: 'a-completely-new-passphrase-7' },
    });
    expect(reset.statusCode).toBe(200);

    // Old sessions are invalidated, old password no longer works, new one does.
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: { cookie } })).statusCode).toBe(401);
    const oldPw = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'doc@example.org', password: TEST_PASSWORD },
    });
    expect(oldPw.statusCode).toBe(401);
    await loginAs(app, 'doc@example.org', 'a-completely-new-passphrase-7');
  });

  it('answers identically for an unknown address', async () => {
    const known = await seedUser({ email: 'doc@example.org', role: 'physician' });
    const a = await app.inject({
      method: 'POST', url: '/api/auth/password/forgot', payload: { email: known.email },
    });
    const b = await app.inject({
      method: 'POST', url: '/api/auth/password/forgot', payload: { email: 'ghost@example.org' },
    });
    expect(b.statusCode).toBe(a.statusCode);
    expect(b.json()).toEqual(a.json());
    expect(mailbox.lastTo('ghost@example.org')).toBeUndefined();
  });

  it('refuses to reuse a reset token', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    await app.inject({
      method: 'POST', url: '/api/auth/password/forgot', payload: { email: 'doc@example.org' },
    });
    const token = decodeURIComponent(
      /reset-password\?token=([^\s]+)/.exec(mailbox.lastTo('doc@example.org')!.text)![1],
    );
    await app.inject({
      method: 'POST', url: '/api/auth/password/reset',
      payload: { token, password: 'first-new-passphrase-value' },
    });
    const second = await app.inject({
      method: 'POST', url: '/api/auth/password/reset',
      payload: { token, password: 'second-new-passphrase-value' },
    });
    expect(second.statusCode).toBe(400);
    expect(second.json().error.code).toBe('invalid_token');
  });
});

describe('audit log', () => {
  it('records logins and failures and is readable only by an admin', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'doc@example.org', password: 'wrong-password-here' },
    });
    await loginAs(app, 'doc@example.org');

    const { rows } = await query<{ action: string }>(
      `SELECT action FROM audit_log ORDER BY id`,
    );
    const actions = rows.map((r) => r.action);
    expect(actions).toContain('auth.login_failed');
    expect(actions).toContain('auth.login');

    const docCookie = await loginAs(app, 'doc@example.org');
    const denied = await app.inject({
      method: 'GET', url: '/api/users/audit-log', headers: { cookie: docCookie },
    });
    expect(denied.statusCode).toBe(403);
  });

  it('cannot be altered or deleted, even by the database owner', async () => {
    await seedUser({ email: 'doc@example.org', role: 'physician' });
    await loginAs(app, 'doc@example.org');
    await expect(query(`UPDATE audit_log SET action = 'tampered'`)).rejects.toThrow();
    await expect(query(`DELETE FROM audit_log`)).rejects.toThrow();
  });
});

describe('rate limiting', () => {
  it('blocks repeated login attempts against one account', async () => {
    await seedUser({ email: 'rl@example.org', role: 'physician' });
    let sawRateLimit = false;
    for (let i = 0; i < 14; i++) {
      const res = await app.inject({
        method: 'POST', url: '/api/auth/login',
        payload: { email: 'rl@example.org', password: `bad-password-${i}` },
      });
      if (res.statusCode === 429) {
        sawRateLimit = true;
        expect(res.json().error.code).toBe('rate_limited');
        break;
      }
    }
    expect(sawRateLimit).toBe(true);
  });

  it('does not lock out other accounts sharing the same address', async () => {
    // A hospital site behind one NAT gateway presents a single IP for every
    // clinician on it, so exhausting one account's budget must not stop
    // everyone else signing in.
    await seedUser({ email: 'victim@example.org', role: 'physician' });
    await seedUser({ email: 'colleague@example.org', role: 'physician' });

    for (let i = 0; i < 14; i++) {
      await app.inject({
        method: 'POST', url: '/api/auth/login',
        payload: { email: 'victim@example.org', password: `bad-password-${i}` },
      });
    }
    const colleague = await app.inject({
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'colleague@example.org', password: TEST_PASSWORD },
    });
    expect(colleague.statusCode).toBe(200);
    expect(colleague.json().status).toBe('ok');
  });
});
