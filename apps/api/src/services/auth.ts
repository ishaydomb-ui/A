import { authenticator } from 'otplib';
import type { Role } from '@med/shared';
import { mfaRequiredForRole } from '@med/shared';
import { config } from '../config.js';
import { query, withTransaction, type Queryable } from '../db/pool.js';
import {
  decryptSecret,
  encryptSecret,
  generateRecoveryCode,
  generateToken,
  hashPassword,
  hashToken,
  verifyPassword,
} from '../lib/crypto.js';
import { badRequest, conflict, forbidden, unauthorized } from '../lib/errors.js';
import { audit } from './audit.js';
import { findByEmail, findById, normalizeEmail, type UserRow } from './users.js';

/** TOTP: 30-second step, one step of clock drift tolerated in each direction. */
authenticator.options = { step: 30, window: 1 };

export interface ClientInfo {
  ip: string | null;
  userAgent: string | null;
}

// --- Password policy --------------------------------------------------------

/**
 * Length-first policy, per NIST SP 800-63B: long passphrases beat short
 * complex ones. Composition rules are intentionally not imposed.
 */
export const MIN_PASSWORD_LENGTH = 12;
const COMMON_PASSWORDS = new Set([
  'password', 'password1', 'passw0rd', '123456789012', 'qwertyuiop12',
  'letmein12345', 'administrator', 'welcome12345', 'iloveyou1234',
]);

export function validatePassword(password: string, email?: string): void {
  if (password.length < MIN_PASSWORD_LENGTH) {
    throw badRequest('weak_password', `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
  }
  if (password.length > 200) {
    throw badRequest('weak_password', 'Password must be at most 200 characters.');
  }
  const lower = password.toLowerCase();
  if (COMMON_PASSWORDS.has(lower)) {
    throw badRequest('weak_password', 'That password is too common. Choose a different one.');
  }
  if (email) {
    const local = normalizeEmail(email).split('@')[0];
    if (local && local.length >= 4 && lower.includes(local)) {
      throw badRequest('weak_password', 'Password must not contain your email address.');
    }
  }
}

// --- Invitations ------------------------------------------------------------

export interface InvitationResult {
  invitationId: string;
  userId: string;
  /** Raw token — returned once, shown only to the inviting admin. */
  token: string;
  expiresAt: Date;
}

/**
 * Creates an invited (not yet active) user plus a single-use invitation token.
 * There is no self-service registration path anywhere in the API.
 */
export async function inviteUser(
  params: { email: string; displayName: string; role: Role; invitedBy: string },
  client?: Queryable,
): Promise<InvitationResult> {
  const run = async (db: Queryable): Promise<InvitationResult> => {
    const emailNormalized = normalizeEmail(params.email);
    const existing = await findByEmail(emailNormalized, db);
    if (existing && existing.status !== 'invited') {
      throw conflict('user_exists', 'A user with that email address already exists.');
    }

    let userId: string;
    if (existing) {
      // Re-inviting someone who never accepted: keep the row, refresh details.
      await query(
        `UPDATE users SET display_name = $2, role = $3, updated_at = now() WHERE id = $1`,
        [existing.id, params.displayName, params.role],
        db,
      );
      await query(
        `UPDATE invitations SET revoked_at = now()
          WHERE email_normalized = $1 AND accepted_at IS NULL AND revoked_at IS NULL`,
        [emailNormalized],
        db,
      );
      userId = existing.id;
    } else {
      const { rows } = await query<{ id: string }>(
        `INSERT INTO users (email, email_normalized, display_name, role, status, created_by)
         VALUES ($1, $2, $3, $4, 'invited', $5) RETURNING id`,
        [params.email.trim(), emailNormalized, params.displayName, params.role, params.invitedBy],
        db,
      );
      userId = rows[0].id;
    }

    const token = generateToken();
    const expiresAt = new Date(Date.now() + config.inviteExpiryMs);
    const { rows: inv } = await query<{ id: string }>(
      `INSERT INTO invitations (email_normalized, role, display_name, token_hash, expires_at, invited_by)
       VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`,
      [emailNormalized, params.role, params.displayName, hashToken(token), expiresAt, params.invitedBy],
      db,
    );

    return { invitationId: inv[0].id, userId, token, expiresAt };
  };

  return client ? run(client) : withTransaction(run);
}

export interface AcceptedInvitation {
  user: UserRow;
  /** True when the account's role makes MFA mandatory. */
  mfaRequired: boolean;
}

export async function acceptInvitation(token: string, password: string): Promise<AcceptedInvitation> {
  return withTransaction(async (db) => {
    const { rows } = await query<{
      id: string; email_normalized: string; role: Role; display_name: string;
      expires_at: Date; accepted_at: Date | null; revoked_at: Date | null;
    }>(
      `SELECT id, email_normalized, role, display_name, expires_at, accepted_at, revoked_at
         FROM invitations WHERE token_hash = $1 FOR UPDATE`,
      [hashToken(token)],
      db,
    );
    const invitation = rows[0];
    if (!invitation) throw badRequest('invalid_token', 'This invitation link is not valid.');
    if (invitation.revoked_at) throw badRequest('invalid_token', 'This invitation has been revoked.');
    if (invitation.accepted_at) throw badRequest('invalid_token', 'This invitation has already been used.');
    if (invitation.expires_at.getTime() < Date.now()) {
      throw badRequest('expired_token', 'This invitation has expired. Ask an administrator for a new one.');
    }

    validatePassword(password, invitation.email_normalized);

    const user = await findByEmail(invitation.email_normalized, db);
    if (!user) throw badRequest('invalid_token', 'This invitation link is not valid.');

    const passwordHash = await hashPassword(password);
    // Accepting the invitation proves control of the mailbox the link was
    // sent to, so this also verifies the address.
    const { rows: updated } = await query<UserRow>(
      `UPDATE users
          SET password_hash = $2, password_changed_at = now(), email_verified_at = now(),
              status = 'active', failed_login_count = 0, locked_until = NULL, updated_at = now()
        WHERE id = $1 RETURNING *`,
      [user.id, passwordHash],
      db,
    );
    await query('UPDATE invitations SET accepted_at = now() WHERE id = $1', [invitation.id], db);
    await audit(
      { action: 'auth.invitation_accepted', actorId: user.id, actorEmail: user.email, entityType: 'user', entityId: user.id },
      db,
    );

    return { user: updated[0], mfaRequired: mfaRequiredForRole(updated[0].role) };
  });
}

// --- Sessions ---------------------------------------------------------------

export interface SessionRow {
  id: string;
  user_id: string;
  absolute_expires_at: Date;
  idle_expires_at: Date;
  revoked_at: Date | null;
  mfa_satisfied: boolean;
}

export interface IssuedSession {
  token: string;
  sessionId: string;
  expiresAt: Date;
}

export async function createSession(
  userId: string,
  info: ClientInfo,
  mfaSatisfied: boolean,
  client?: Queryable,
): Promise<IssuedSession> {
  const token = generateToken(48);
  const now = Date.now();
  const absolute = new Date(now + config.sessionAbsoluteMs);
  const idle = new Date(now + config.sessionIdleMs);
  const { rows } = await query<{ id: string }>(
    `INSERT INTO sessions (user_id, token_hash, absolute_expires_at, idle_expires_at, ip, user_agent, mfa_satisfied)
     VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id`,
    [userId, hashToken(token), absolute, idle, info.ip, info.userAgent, mfaSatisfied],
    client,
  );
  return { token, sessionId: rows[0].id, expiresAt: absolute };
}

export interface AuthenticatedUser {
  id: string;
  email: string;
  displayName: string;
  role: Role;
  sessionId: string;
  mfaSatisfied: boolean;
}

/**
 * Resolves a session token to a user, enforcing revocation, absolute expiry
 * and idle expiry, and sliding the idle window forward on success.
 */
export async function resolveSession(token: string): Promise<AuthenticatedUser | null> {
  const { rows } = await query<{
    session_id: string; mfa_satisfied: boolean; absolute_expires_at: Date; idle_expires_at: Date;
    revoked_at: Date | null; user_id: string; email: string; display_name: string;
    role: Role; status: string;
  }>(
    `SELECT s.id AS session_id, s.mfa_satisfied, s.absolute_expires_at, s.idle_expires_at, s.revoked_at,
            u.id AS user_id, u.email, u.display_name, u.role, u.status
       FROM sessions s JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1`,
    [hashToken(token)],
  );
  const row = rows[0];
  if (!row) return null;

  const now = Date.now();
  if (row.revoked_at) return null;
  if (row.absolute_expires_at.getTime() <= now) return null;
  if (row.idle_expires_at.getTime() <= now) return null;
  if (row.status !== 'active') return null;

  // Slide the idle window, capped by the absolute deadline.
  const nextIdle = new Date(Math.min(now + config.sessionIdleMs, row.absolute_expires_at.getTime()));
  await query(
    'UPDATE sessions SET last_seen_at = now(), idle_expires_at = $2 WHERE id = $1',
    [row.session_id, nextIdle],
  );

  return {
    id: row.user_id,
    email: row.email,
    displayName: row.display_name,
    role: row.role,
    sessionId: row.session_id,
    mfaSatisfied: row.mfa_satisfied,
  };
}

export async function revokeSession(sessionId: string): Promise<void> {
  await query('UPDATE sessions SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL', [sessionId]);
}

export async function revokeAllSessions(userId: string, client?: Queryable): Promise<number> {
  const { rowCount } = await query(
    'UPDATE sessions SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL',
    [userId],
    client,
  );
  return rowCount ?? 0;
}

export async function purgeExpiredSessions(): Promise<number> {
  const { rowCount } = await query(
    `DELETE FROM sessions
      WHERE absolute_expires_at < now() - interval '7 days'
         OR (revoked_at IS NOT NULL AND revoked_at < now() - interval '7 days')`,
  );
  return rowCount ?? 0;
}

// --- Login ------------------------------------------------------------------

export type LoginOutcome =
  | { status: 'ok'; user: UserRow; session: IssuedSession }
  | { status: 'mfa_required'; user: UserRow; challengeToken: string }
  | { status: 'mfa_enrollment_required'; user: UserRow; challengeToken: string };

/**
 * A dummy Argon2id verification target. Comparing against it when the account
 * does not exist keeps the response time of "unknown user" and "wrong
 * password" indistinguishable, so login cannot be used to enumerate accounts.
 */
let dummyHash: string | null = null;
async function equalizeTiming(password: string): Promise<void> {
  dummyHash ??= await hashPassword('timing-equalization-placeholder');
  await verifyPassword(dummyHash, password);
}

export async function login(
  email: string,
  password: string,
  info: ClientInfo,
): Promise<LoginOutcome> {
  const user = await findByEmail(email);

  if (!user || !user.password_hash) {
    await equalizeTiming(password);
    throw unauthorized('invalid_credentials', 'Email or password is incorrect.');
  }
  if (user.locked_until && user.locked_until.getTime() > Date.now()) {
    throw forbidden('account_locked', 'This account is temporarily locked. Try again later.');
  }
  if (user.status === 'suspended' || user.status === 'deactivated') {
    await equalizeTiming(password);
    throw forbidden('account_inactive', 'This account is not active. Contact an administrator.');
  }
  if (user.status === 'invited') {
    await equalizeTiming(password);
    throw forbidden('invitation_pending', 'Finish setting up your account using your invitation link.');
  }

  const ok = await verifyPassword(user.password_hash, password);
  if (!ok) {
    await registerFailedLogin(user);
    throw unauthorized('invalid_credentials', 'Email or password is incorrect.');
  }

  await query(
    'UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = $1',
    [user.id],
  );

  if (user.mfa_enabled_at && user.mfa_secret) {
    return { status: 'mfa_required', user, challengeToken: await issueMfaChallenge(user.id) };
  }
  if (mfaRequiredForRole(user.role)) {
    // Policy requires MFA for this role but it is not set up yet: no session
    // is issued until enrolment completes.
    return { status: 'mfa_enrollment_required', user, challengeToken: await issueMfaChallenge(user.id) };
  }

  const session = await createSession(user.id, info, true);
  await query('UPDATE users SET last_login_at = now() WHERE id = $1', [user.id]);
  await audit({
    action: 'auth.login', actorId: user.id, actorEmail: user.email, entityType: 'user',
    entityId: user.id, ip: info.ip, userAgent: info.userAgent, detail: { mfa: false },
  });
  return { status: 'ok', user, session };
}

async function registerFailedLogin(user: UserRow): Promise<void> {
  const next = user.failed_login_count + 1;
  const shouldLock = next >= config.loginMaxAttempts;
  await query(
    `UPDATE users SET failed_login_count = $2, locked_until = $3 WHERE id = $1`,
    [user.id, shouldLock ? 0 : next, shouldLock ? new Date(Date.now() + config.loginLockoutMs) : null],
  );
  await audit({
    action: shouldLock ? 'auth.account_locked' : 'auth.login_failed',
    actorId: user.id,
    actorEmail: user.email,
    entityType: 'user',
    entityId: user.id,
    detail: { attempt: next },
  });
}

// --- MFA --------------------------------------------------------------------

/**
 * Short-lived token that proves the password step succeeded, so the MFA step
 * never has to re-accept the password. Stored as a session row that is not
 * yet usable for API access.
 */
const MFA_CHALLENGE_MS = 5 * 60_000;

async function issueMfaChallenge(userId: string): Promise<string> {
  const token = generateToken(32);
  const expires = new Date(Date.now() + MFA_CHALLENGE_MS);
  await query(
    `INSERT INTO auth_tokens (user_id, purpose, token_hash, expires_at)
     VALUES ($1, 'mfa_challenge', $2, $3)`,
    [userId, hashToken(`mfa:${token}`), expires],
  );
  return token;
}

async function consumeMfaChallenge(token: string): Promise<string> {
  const { rows } = await query<{ id: string; user_id: string; expires_at: Date; used_at: Date | null }>(
    `SELECT id, user_id, expires_at, used_at FROM auth_tokens
      WHERE token_hash = $1 AND purpose = 'mfa_challenge'`,
    [hashToken(`mfa:${token}`)],
  );
  const row = rows[0];
  if (!row || row.used_at || row.expires_at.getTime() < Date.now()) {
    throw unauthorized('invalid_challenge', 'This sign-in attempt has expired. Please sign in again.');
  }
  return row.user_id;
}

async function markChallengeUsed(token: string): Promise<void> {
  await query('UPDATE auth_tokens SET used_at = now() WHERE token_hash = $1', [hashToken(`mfa:${token}`)]);
}

export interface MfaEnrollment {
  secret: string;
  otpauthUrl: string;
}

/**
 * Starts (or resumes) TOTP enrolment.
 *
 * Idempotent while an enrolment is pending: if the user already has a secret
 * that has not been confirmed, the same one is returned. Generating a fresh
 * secret on every call would mean a second call — a reload, a retry, a
 * double-invoked effect — silently replacing the secret behind a QR code the
 * user has already scanned, so their first correct code would be rejected.
 *
 * Once MFA is enabled, calling this again does start a genuine re-enrolment
 * with a new secret.
 */
export async function beginMfaEnrollment(userId: string, email: string): Promise<MfaEnrollment> {
  const existing = await findById(userId);
  const pending = existing?.mfa_secret && !existing.mfa_enabled_at;

  const secret = pending ? decryptSecret(existing!.mfa_secret!) : authenticator.generateSecret();
  const otpauthUrl = authenticator.keyuri(email, 'Medication Catalogue', secret);

  if (!pending) {
    // Held encrypted but not yet enabled; confirming a code activates it.
    await query('UPDATE users SET mfa_secret = $2, updated_at = now() WHERE id = $1', [
      userId,
      encryptSecret(secret),
    ]);
  }
  return { secret, otpauthUrl };
}

export async function confirmMfaEnrollment(userId: string, code: string): Promise<string[]> {
  const user = await findById(userId);
  if (!user?.mfa_secret) throw badRequest('mfa_not_started', 'Start multi-factor setup first.');
  const secret = decryptSecret(user.mfa_secret);
  if (!authenticator.check(code.replace(/\s/g, ''), secret)) {
    throw badRequest('invalid_code', 'That code is not correct. Check your authenticator app and try again.');
  }
  const codes = Array.from({ length: 10 }, generateRecoveryCode);
  const hashes = await Promise.all(codes.map((c) => hashPassword(c)));
  await query(
    'UPDATE users SET mfa_enabled_at = now(), mfa_recovery_codes = $2, updated_at = now() WHERE id = $1',
    [userId, hashes],
  );
  await audit({ action: 'auth.mfa_enabled', actorId: userId, entityType: 'user', entityId: userId });
  return codes;
}

export async function verifyMfaAndCreateSession(
  challengeToken: string,
  code: string,
  info: ClientInfo,
): Promise<{ user: UserRow; session: IssuedSession }> {
  const userId = await consumeMfaChallenge(challengeToken);
  const user = await findById(userId);
  if (!user?.mfa_secret) throw unauthorized('invalid_challenge', 'Multi-factor authentication is not set up.');

  const cleaned = code.replace(/\s|-/g, '').toUpperCase();
  const secret = decryptSecret(user.mfa_secret);
  let accepted = authenticator.check(code.replace(/\s/g, ''), secret);
  let usedRecoveryCode = false;

  if (!accepted && user.mfa_recovery_codes?.length) {
    for (let i = 0; i < user.mfa_recovery_codes.length; i++) {
      const stored = user.mfa_recovery_codes[i];
      // Recovery codes are shown hyphenated; accept either form.
      if (await verifyPassword(stored, `${cleaned.slice(0, 5)}-${cleaned.slice(5)}`)) {
        const remaining = user.mfa_recovery_codes.filter((_, idx) => idx !== i);
        await query('UPDATE users SET mfa_recovery_codes = $2 WHERE id = $1', [user.id, remaining]);
        accepted = true;
        usedRecoveryCode = true;
        break;
      }
    }
  }

  if (!accepted) {
    await audit({
      action: 'auth.mfa_failed', actorId: user.id, actorEmail: user.email,
      entityType: 'user', entityId: user.id, ip: info.ip, userAgent: info.userAgent,
    });
    throw unauthorized('invalid_code', 'That code is not correct.');
  }

  await markChallengeUsed(challengeToken);
  const session = await createSession(user.id, info, true);
  await query('UPDATE users SET last_login_at = now() WHERE id = $1', [user.id]);
  await audit({
    action: 'auth.login', actorId: user.id, actorEmail: user.email, entityType: 'user',
    entityId: user.id, ip: info.ip, userAgent: info.userAgent,
    detail: { mfa: true, recoveryCode: usedRecoveryCode },
  });
  return { user, session };
}

/** Enrolment during login: verify the new secret, then issue the session. */
export async function completeEnrollmentAndCreateSession(
  challengeToken: string,
  code: string,
  info: ClientInfo,
): Promise<{ user: UserRow; session: IssuedSession; recoveryCodes: string[] }> {
  const userId = await consumeMfaChallenge(challengeToken);
  const recoveryCodes = await confirmMfaEnrollment(userId, code);
  await markChallengeUsed(challengeToken);
  const user = await findById(userId);
  if (!user) throw unauthorized('invalid_challenge', 'Sign-in attempt is no longer valid.');
  const session = await createSession(user.id, info, true);
  await query('UPDATE users SET last_login_at = now() WHERE id = $1', [user.id]);
  return { user, session, recoveryCodes };
}

// --- Password reset ---------------------------------------------------------

export async function createPasswordReset(email: string): Promise<{ token: string; user: UserRow } | null> {
  const user = await findByEmail(email);
  // Callers must respond identically whether or not this returns null, so a
  // reset request cannot be used to discover which addresses are registered.
  if (!user || user.status === 'deactivated' || user.status === 'suspended') return null;

  const token = generateToken();
  await query(
    `INSERT INTO auth_tokens (user_id, purpose, token_hash, expires_at)
     VALUES ($1, 'password_reset', $2, $3)`,
    [user.id, hashToken(token), new Date(Date.now() + config.resetExpiryMs)],
  );
  return { token, user };
}

export async function resetPassword(token: string, newPassword: string): Promise<UserRow> {
  return withTransaction(async (db) => {
    const { rows } = await query<{ id: string; user_id: string; expires_at: Date; used_at: Date | null }>(
      `SELECT id, user_id, expires_at, used_at FROM auth_tokens
        WHERE token_hash = $1 AND purpose = 'password_reset' FOR UPDATE`,
      [hashToken(token)],
      db,
    );
    const row = rows[0];
    if (!row || row.used_at) throw badRequest('invalid_token', 'This reset link is not valid.');
    if (row.expires_at.getTime() < Date.now()) {
      throw badRequest('expired_token', 'This reset link has expired. Request a new one.');
    }

    const user = await findById(row.user_id, db);
    if (!user) throw badRequest('invalid_token', 'This reset link is not valid.');
    validatePassword(newPassword, user.email);

    const hash = await hashPassword(newPassword);
    const { rows: updated } = await query<UserRow>(
      `UPDATE users SET password_hash = $2, password_changed_at = now(),
              failed_login_count = 0, locked_until = NULL, updated_at = now()
        WHERE id = $1 RETURNING *`,
      [user.id, hash],
      db,
    );
    await query('UPDATE auth_tokens SET used_at = now() WHERE id = $1', [row.id], db);
    // Any session opened with the old password is no longer trusted.
    await revokeAllSessions(user.id, db);
    await audit(
      { action: 'auth.password_reset', actorId: user.id, actorEmail: user.email, entityType: 'user', entityId: user.id },
      db,
    );
    return updated[0];
  });
}

export async function changePassword(
  userId: string,
  currentPassword: string,
  newPassword: string,
  keepSessionId: string,
): Promise<void> {
  const user = await findById(userId);
  if (!user?.password_hash) throw unauthorized();
  if (!(await verifyPassword(user.password_hash, currentPassword))) {
    throw badRequest('invalid_credentials', 'Your current password is not correct.');
  }
  validatePassword(newPassword, user.email);
  const hash = await hashPassword(newPassword);
  await query(
    `UPDATE users SET password_hash = $2, password_changed_at = now(), updated_at = now() WHERE id = $1`,
    [userId, hash],
  );
  await query(
    'UPDATE sessions SET revoked_at = now() WHERE user_id = $1 AND id <> $2 AND revoked_at IS NULL',
    [userId, keepSessionId],
  );
  await audit({ action: 'auth.password_changed', actorId: userId, entityType: 'user', entityId: userId });
}

// --- Challenge and invitation helpers used by the HTTP layer ----------------

/**
 * Reads a pending MFA challenge without consuming it, so the enrolment screen
 * can show a QR code before the user submits their first code.
 */
export async function peekMfaChallenge(token: string): Promise<{ userId: string; email: string }> {
  const userId = await consumeMfaChallenge(token);
  const user = await findById(userId);
  if (!user) throw unauthorized('invalid_challenge', 'Sign-in attempt is no longer valid.');
  return { userId, email: user.email };
}

/** Issues a challenge outside the login flow (e.g. straight after accepting an invitation). */
export async function issueMfaChallengeFor(userId: string): Promise<string> {
  return issueMfaChallenge(userId);
}

export interface InvitationSummary {
  email: string;
  displayName: string;
  role: Role;
  expiresAt: string;
}

/** Describes a pending invitation for the acceptance page. */
export async function describeInvitation(token: string): Promise<InvitationSummary | null> {
  const { rows } = await query<{
    email_normalized: string; display_name: string; role: Role;
    expires_at: Date; accepted_at: Date | null; revoked_at: Date | null;
  }>(
    `SELECT email_normalized, display_name, role, expires_at, accepted_at, revoked_at
       FROM invitations WHERE token_hash = $1`,
    [hashToken(token)],
  );
  const row = rows[0];
  if (!row || row.accepted_at || row.revoked_at || row.expires_at.getTime() < Date.now()) return null;
  return {
    email: row.email_normalized,
    displayName: row.display_name,
    role: row.role,
    expiresAt: row.expires_at.toISOString(),
  };
}
