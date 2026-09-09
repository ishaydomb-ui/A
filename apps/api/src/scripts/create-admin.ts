/**
 * Creates or repairs the first administrator account.
 *
 * Used once when bootstrapping a deployment: without it there is no way in,
 * because there is no public registration. The password is read from stdin or
 * the BOOTSTRAP_PASSWORD environment variable, never from a command-line
 * argument, so it does not land in shell history or the process list.
 */
import { createInterface } from 'node:readline/promises';
import { pool, query } from '../db/pool.js';
import { hashPassword } from '../lib/crypto.js';
import { validatePassword } from '../services/auth.js';
import type { Role } from '@med/shared';

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
  const role: Role = 'admin';

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
     VALUES ($1::uuid, $2, 'users.bootstrap_admin', 'user', $1::text,
             '{"via":"create-admin script"}'::jsonb)`,
    [rows[0].id, email],
  );

  console.log(`Administrator ready: ${email}`);
  console.log('Two-factor authentication must be set up at first sign-in before a session is issued.');
}

main().then(() => pool.end()).then(() => process.exit(0)).catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
