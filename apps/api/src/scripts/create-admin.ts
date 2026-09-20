/**
 * Creates or repairs an account directly, without an invitation.
 *
 * Needed once when bootstrapping a deployment: there is no public
 * registration, so without this there is no way in at all. It also serves as a
 * fallback for adding someone when no mail server is configured.
 *
 * Prefer the invitation flow for everyone after the first administrator: it
 * lets the person choose their own password, which this cannot, and it proves
 * they control the address.
 *
 * The password is read from stdin or BOOTSTRAP_PASSWORD, never from a
 * command-line argument, so it does not land in shell history or the process
 * list.
 */
import { createInterface } from 'node:readline/promises';
import { pool, query } from '../db/pool.js';
import { hashPassword } from '../lib/crypto.js';
import { validatePassword } from '../services/auth.js';
import { ROLES, type Role } from '@med/shared';

async function prompt(question: string, silent = false): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  if (silent) {
    // Suppress echo while a password is typed.
    const output = rl as unknown as { output: NodeJS.WriteStream };
    const write = output.output.write.bind(output.output);
    (output.output as unknown as { write: (c: string) => boolean }).write = (chunk: string) =>
      /\n|\r/.test(chunk) ? write(chunk) : true;
  }
  const answer = await rl.question(question);
  rl.close();
  if (silent) process.stdout.write('\n');
  return answer.trim();
}

async function main(): Promise<void> {
  const email = process.env.BOOTSTRAP_EMAIL ?? (await prompt('Administrator email: '));
  const name = process.env.BOOTSTRAP_NAME ?? (await prompt('Full name: '));
  const password = process.env.BOOTSTRAP_PASSWORD ?? (await prompt('Password: ', true));

  const requested = process.env.BOOTSTRAP_ROLE ?? 'admin';
  if (!(ROLES as readonly string[]).includes(requested)) {
    throw new Error(`Unknown role "${requested}". One of: ${ROLES.join(', ')}`);
  }
  const role = requested as Role;

  validatePassword(password, email);
  const hash = await hashPassword(password);

  const { rows } = await query<{ id: string }>(
    `INSERT INTO users (email, email_normalized, display_name, role, status, password_hash,
                        password_changed_at, email_verified_at)
     VALUES ($1, $2, $3, $4, 'active', $5, now(), now())
     ON CONFLICT (email_normalized) DO UPDATE
       SET password_hash = EXCLUDED.password_hash, password_changed_at = now(),
           role = EXCLUDED.role, status = 'active', failed_login_count = 0, locked_until = NULL
     RETURNING id`,
    [email, email.toLowerCase(), name, role, hash],
  );

  await query(
    `INSERT INTO audit_log (actor_id, actor_email, action, entity_type, entity_id, detail)
     VALUES ($1::uuid, $2, 'users.bootstrap_account', 'user', $1::text,
             $3::jsonb)`,
    [rows[0].id, email, JSON.stringify({ via: 'create-admin script', role })],
  );

  console.log(`Account ready: ${email} (${role})`);
  if (role === 'admin') {
    console.log('Two-factor authentication must be set up at first sign-in before a session is issued.');
  } else {
    console.log('Two-factor authentication is optional for this role and can be enabled from the account page.');
  }
}

main().then(() => pool.end()).then(() => process.exit(0)).catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
