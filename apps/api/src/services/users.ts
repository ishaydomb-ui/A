import type { Role } from '@med/shared';
import { query, type Queryable } from '../db/pool.js';

export type UserStatus = 'invited' | 'active' | 'suspended' | 'deactivated';

export interface UserRow {
  id: string;
  email: string;
  email_normalized: string;
  display_name: string;
  role: Role;
  status: UserStatus;
  password_hash: string | null;
  password_changed_at: Date | null;
  email_verified_at: Date | null;
  mfa_secret: string | null;
  mfa_enabled_at: Date | null;
  mfa_recovery_codes: string[] | null;
  failed_login_count: number;
  locked_until: Date | null;
  last_login_at: Date | null;
  created_at: Date;
}

/** The shape returned to clients — never includes hashes or secrets. */
export interface PublicUser {
  id: string;
  email: string;
  displayName: string;
  role: Role;
  status: UserStatus;
  mfaEnabled: boolean;
  emailVerified: boolean;
  lastLoginAt: string | null;
  createdAt: string;
}

export function toPublicUser(row: UserRow): PublicUser {
  return {
    id: row.id,
    email: row.email,
    displayName: row.display_name,
    role: row.role,
    status: row.status,
    mfaEnabled: !!row.mfa_enabled_at,
    emailVerified: !!row.email_verified_at,
    lastLoginAt: row.last_login_at?.toISOString() ?? null,
    createdAt: row.created_at.toISOString(),
  };
}

/**
 * Email addresses are compared case-insensitively on the whole address.
 * Provider-specific tricks (dot-folding, plus-tags) are deliberately NOT
 * applied: two addresses that differ are treated as two different people.
 */
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export async function findByEmail(email: string, client?: Queryable): Promise<UserRow | null> {
  const { rows } = await query<UserRow>(
    'SELECT * FROM users WHERE email_normalized = $1',
    [normalizeEmail(email)],
    client,
  );
  return rows[0] ?? null;
}

export async function findById(id: string, client?: Queryable): Promise<UserRow | null> {
  const { rows } = await query<UserRow>('SELECT * FROM users WHERE id = $1', [id], client);
  return rows[0] ?? null;
}

export async function listUsers(): Promise<PublicUser[]> {
  const { rows } = await query<UserRow>('SELECT * FROM users ORDER BY created_at DESC');
  return rows.map(toPublicUser);
}

export async function countAdmins(client?: Queryable): Promise<number> {
  const { rows } = await query<{ n: number }>(
    `SELECT count(*)::int AS n FROM users WHERE role = 'admin' AND status = 'active'`,
    [],
    client,
  );
  return rows[0]?.n ?? 0;
}
