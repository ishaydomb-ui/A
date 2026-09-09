import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import type { FastifyInstance } from 'fastify';
import type { Role } from '@med/shared';
import { buildApp } from '../app.js';
import { pool, query } from '../db/pool.js';
import { up as migrateUp } from '../db/migrate.js';
import { MemoryTransport, setTransport } from '../services/mail.js';
import { hashPassword } from '../lib/crypto.js';

const exec = promisify(execFile);

export const mailbox = new MemoryTransport();

/**
 * audit_log and revisions are append-only: the TRUNCATE privilege is revoked
 * and a trigger blocks UPDATE/DELETE even for the table owner. Wiping them
 * therefore takes a deliberate, privileged sequence — exactly what a restore
 * has to do — which is the point: it cannot happen by accident.
 */
const APPEND_ONLY = ['audit_log', 'revisions'];

export async function resetDatabase(): Promise<void> {
  await migrateUp(() => {});
  const { rows } = await query<{ tablename: string }>(
    `SELECT tablename FROM pg_tables
      WHERE schemaname = 'public' AND tablename <> 'schema_migrations'`,
  );
  const tables = rows.map((r) => `"${r.tablename}"`).join(', ');
  for (const t of APPEND_ONLY) {
    await query(`GRANT TRUNCATE ON TABLE ${t} TO CURRENT_USER`);
    await query(`ALTER TABLE ${t} DISABLE TRIGGER USER`);
  }
  await query(`TRUNCATE ${tables} RESTART IDENTITY CASCADE`);
  for (const t of APPEND_ONLY) {
    await query(`ALTER TABLE ${t} ENABLE TRIGGER USER`);
    await query(`REVOKE TRUNCATE ON TABLE ${t} FROM CURRENT_USER`);
  }
  // Settings are seeded by a migration; restore the rows TRUNCATE removed.
  await query(
    `INSERT INTO settings (key, value, needs_approval) VALUES
       ('institution_name', '"(pending approval)"'::jsonb, true),
       ('contact_email',    '"(pending approval)"'::jsonb, true),
       ('legal.privacy_policy', '"(placeholder — awaiting owner approval)"'::jsonb, true),
       ('legal.terms',          '"(placeholder — awaiting owner approval)"'::jsonb, true)
     ON CONFLICT (key) DO NOTHING`,
  );
}

export async function createTestApp(): Promise<FastifyInstance> {
  setTransport(mailbox);
  mailbox.clear();
  const app = await buildApp();
  await app.ready();
  return app;
}

export async function closeTestApp(app: FastifyInstance | undefined): Promise<void> {
  // Tolerates a suite whose beforeAll failed before the app was built.
  if (app) await app.close();
}

export async function shutdown(): Promise<void> {
  await pool.end();
}

export const TEST_PASSWORD = 'correct-horse-battery-staple-42';

/**
 * Creates an already-active user directly, bypassing the invitation flow.
 * The invitation flow itself is covered by its own tests.
 */
export async function seedUser(params: {
  email: string;
  role: Role;
  displayName?: string;
  password?: string;
  status?: 'active' | 'suspended' | 'invited' | 'deactivated';
}): Promise<{ id: string; email: string; password: string }> {
  const password = params.password ?? TEST_PASSWORD;
  const hash = await hashPassword(password);
  const { rows } = await query<{ id: string }>(
    `INSERT INTO users (email, email_normalized, display_name, role, status, password_hash,
                        password_changed_at, email_verified_at)
     VALUES ($1, $2, $3, $4, $5, $6, now(), now()) RETURNING id`,
    [
      params.email,
      params.email.toLowerCase(),
      params.displayName ?? params.email.split('@')[0],
      params.role,
      params.status ?? 'active',
      hash,
    ],
  );
  return { id: rows[0].id, email: params.email, password };
}

/** Extracts the session cookie from a login response for reuse in requests. */
export function sessionCookie(res: { headers: Record<string, unknown> }): string {
  const raw = res.headers['set-cookie'];
  const list = Array.isArray(raw) ? raw : [raw];
  const cookie = list.find((c): c is string => typeof c === 'string' && c.startsWith('medcat_session='));
  if (!cookie) throw new Error('no session cookie in response');
  return cookie.split(';')[0];
}

/**
 * Signs in and returns the cookie header. Roles that require MFA are not
 * supported here; those tests drive the challenge flow explicitly.
 */
export async function loginAs(
  app: FastifyInstance,
  email: string,
  password = TEST_PASSWORD,
): Promise<string> {
  const res = await app.inject({
    method: 'POST',
    url: '/api/auth/login',
    payload: { email, password },
  });
  if (res.statusCode !== 200) {
    throw new Error(`login failed (${res.statusCode}): ${res.body}`);
  }
  const body = res.json();
  if (body.status !== 'ok') throw new Error(`login returned status ${body.status}`);
  return sessionCookie(res);
}

/** Ensures the test database exists before the suite runs. */
export async function ensureTestDatabase(): Promise<void> {
  try {
    await query('SELECT 1');
  } catch {
    await exec('createdb', ['medcat_test']).catch(() => undefined);
  }
}
