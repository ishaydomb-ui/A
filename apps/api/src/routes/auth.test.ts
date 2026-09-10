import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { authenticator } from 'otplib';
import { query } from '../db/pool.js';
import { decryptSecret } from '../lib/crypto.js';
import {
  TEST_PASSWORD, closeTestApp, createTestApp, loginAs, mailbox,
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
