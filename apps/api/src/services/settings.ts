import { query, type Queryable } from '../db/pool.js';

/**
 * System settings. Values that need the owner's sign-off — institution name,
 * legal texts — carry a `needs_approval` flag so the interface can render
 * them as pending rather than pretending they are final.
 */
export interface SettingRow {
  key: string;
  value: unknown;
  needs_approval: boolean;
  updated_at: Date;
}

/**
 * The settings the system expects to exist, with their defaults.
 *
 * Migrations seed these for an existing database; `ensureDefaultSettings` puts
 * any missing one back at start-up. Keeping the list here rather than only in
 * SQL means a new setting cannot be added to a migration and then quietly go
 * missing wherever the database is rebuilt.
 */
export const DEFAULT_SETTINGS: ReadonlyArray<{
  key: string;
  value: unknown;
  needsApproval: boolean;
}> = [
  { key: 'institution_name', value: '(pending approval)', needsApproval: true },
  { key: 'contact_email', value: '(pending approval)', needsApproval: true },
  { key: 'legal.privacy_policy', value: '(placeholder — awaiting owner approval)', needsApproval: true },
  { key: 'legal.terms', value: '(placeholder — awaiting owner approval)', needsApproval: true },
  { key: 'publication.allow_unvalidated', value: false, needsApproval: false },
  {
    key: 'publication.unvalidated_notice',
    value:
      'This catalogue is in evaluation. Its content comes from an unverified website snapshot, ' +
      'has not been checked against an authoritative source, and has not been reviewed by a ' +
      'clinician. Do not use it for clinical decisions.',
    needsApproval: false,
  },
];

/** Restores any missing default. Never overwrites a value already set. */
export async function ensureDefaultSettings(client?: Queryable): Promise<void> {
  for (const setting of DEFAULT_SETTINGS) {
    await query(
      `INSERT INTO settings (key, value, needs_approval)
       VALUES ($1, $2, $3) ON CONFLICT (key) DO NOTHING`,
      [setting.key, JSON.stringify(setting.value), setting.needsApproval],
      client,
    );
  }
}

export async function getSetting<T>(key: string, fallback: T, client?: Queryable): Promise<T> {
  const { rows } = await query<{ value: T }>(
    'SELECT value FROM settings WHERE key = $1',
    [key],
    client,
  );
  return rows[0] ? rows[0].value : fallback;
}

export async function setSetting(
  key: string,
  value: unknown,
  actorId: string,
  needsApproval?: boolean,
): Promise<void> {
  await query(
    `INSERT INTO settings (key, value, needs_approval, updated_at, updated_by)
     VALUES ($1, $2, COALESCE($3, false), now(), $4)
     ON CONFLICT (key) DO UPDATE
       SET value = EXCLUDED.value,
           needs_approval = COALESCE($3, settings.needs_approval),
           updated_at = now(),
           updated_by = EXCLUDED.updated_by`,
    [key, JSON.stringify(value), needsApproval ?? null, actorId],
  );
}

export async function listSettings(): Promise<SettingRow[]> {
  const { rows } = await query<SettingRow>(
    'SELECT key, value, needs_approval, updated_at FROM settings ORDER BY key',
  );
  return rows;
}

/** Whether records may be published before clinical review has happened. */
export async function unvalidatedPublicationAllowed(client?: Queryable): Promise<boolean> {
  return getSetting<boolean>('publication.allow_unvalidated', false, client);
}
