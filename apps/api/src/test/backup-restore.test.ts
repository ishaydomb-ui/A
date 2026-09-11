/**
 * Backup and restore are exercised for real: the shipped ops scripts run
 * against a live PostgreSQL instance, producing an encrypted dump that is
 * then restored into a separate database and checked.
 *
 * A backup that has never been restored is not a backup, so this runs in the
 * normal test suite rather than being a manual procedure.
 */
import { execFile } from 'node:child_process';
import { mkdtemp, readdir, rm, appendFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { promisify } from 'node:util';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { pool, query } from '../db/pool.js';
import { resetDatabase, seedUser, shutdown } from './helpers.js';
import { createMedicationDraft } from '../services/catalogue.js';
import { SERTRALINE } from './fixtures.js';

const exec = promisify(execFile);
const OPS = resolve(import.meta.dirname, '../../../../ops');
const PASSPHRASE = 'backup-test-passphrase-not-a-real-secret';
const RESTORE_DB = 'medcat_backup_restore_test';

function connection(): { host: string; user: string; password: string; database: string } {
  const url = new URL(process.env.DATABASE_URL!);
  return {
    host: url.hostname,
    user: decodeURIComponent(url.username),
    password: decodeURIComponent(url.password),
    database: url.pathname.slice(1),
  };
}

let backupDir: string;
let env: NodeJS.ProcessEnv;

/** Runs one of the shipped ops scripts with the environment it expects. */
async function runScript(script: string, args: string[] = [], extra: NodeJS.ProcessEnv = {}) {
  return exec('sh', [join(OPS, script), ...args], {
    env: { ...env, ...extra },
    maxBuffer: 8 * 1024 * 1024,
  });
}

async function psql(database: string, sql: string): Promise<string> {
  const c = connection();
  const { stdout } = await exec('psql', ['-h', c.host, '-U', c.user, '-d', database, '-tAc', sql], {
    env: { ...process.env, PGPASSWORD: c.password },
  });
  return stdout.trim();
}

beforeAll(async () => {
  const c = connection();
  backupDir = await mkdtemp(join(tmpdir(), 'medcat-backup-'));
  env = {
    ...process.env,
    PGHOST: c.host,
    PGUSER: c.user,
    PGPASSWORD: c.password,
    PGDATABASE: c.database,
    BACKUP_PASSPHRASE: PASSPHRASE,
    BACKUP_DIR: backupDir,
    BACKUP_RETENTION_DAYS: '30',
  };

  await resetDatabase();
  const editor = await seedUser({ email: 'editor@example.org', role: 'editor' });
  await createMedicationDraft(
    { data: SERTRALINE, sourceLabel: 'Backup test', reviewedAt: '2026-01-01' },
    { id: editor.id, email: editor.email, role: 'editor' },
  );
  // The append-only tables are row-level protected, so the backup has to
  // carry rows for the guarantee to mean anything after a restore.
  await query(
    `INSERT INTO audit_log (actor_id, actor_email, action, entity_type, entity_id, detail)
     VALUES ($1::uuid, $2, 'auth.login', 'user', $1::text, '{"mfa":false}'::jsonb)`,
    [editor.id, editor.email],
  );
}, 120_000);

afterAll(async () => {
  const c = connection();
  await exec('dropdb', ['-h', c.host, '-U', c.user, '--if-exists', RESTORE_DB], {
    env: { ...process.env, PGPASSWORD: c.password },
  }).catch(() => undefined);
  await rm(backupDir, { recursive: true, force: true });
  await shutdown();
});

describe('backup', () => {
  let encrypted: string;

  it('produces an encrypted, verified dump', async () => {
    const { stdout } = await runScript('backup.sh');
    expect(stdout).toContain('verified');

    const files = await readdir(backupDir);
    const dumps = files.filter((f) => f.endsWith('.dump.gpg'));
    expect(dumps).toHaveLength(1);
    encrypted = join(backupDir, dumps[0]);

    // The plaintext dump must never be left on disk.
    expect(files.filter((f) => f.endsWith('.dump'))).toHaveLength(0);
  }, 120_000);

  it('writes a checksum alongside the backup', async () => {
    const files = await readdir(backupDir);
    expect(files.some((f) => f.endsWith('.dump.gpg.sha256'))).toBe(true);
  });

  it('is genuinely encrypted, not just compressed', async () => {
    const contents = await readFile(encrypted);
    // A PostgreSQL custom-format dump starts with the magic "PGDMP".
    expect(contents.subarray(0, 5).toString('latin1')).not.toBe('PGDMP');
    expect(contents.includes(Buffer.from('Sertraline'))).toBe(false);
  });
});

describe('restore', () => {
  async function backupPath(): Promise<string> {
    const files = await readdir(backupDir);
    return join(backupDir, files.find((f) => f.endsWith('.dump.gpg'))!);
  }

  it('restores every table into a fresh database', async () => {
    const before = {
      medications: await psql(connection().database, 'SELECT count(*) FROM medications'),
      users: await psql(connection().database, 'SELECT count(*) FROM users'),
      revisions: await psql(connection().database, 'SELECT count(*) FROM revisions'),
      versions: await psql(connection().database, 'SELECT count(*) FROM medication_versions'),
    };

    const { stdout } = await runScript('restore.sh', [await backupPath(), RESTORE_DB]);
    expect(stdout).toContain('done');

    expect(await psql(RESTORE_DB, 'SELECT count(*) FROM medications')).toBe(before.medications);
    expect(await psql(RESTORE_DB, 'SELECT count(*) FROM users')).toBe(before.users);
    expect(await psql(RESTORE_DB, 'SELECT count(*) FROM revisions')).toBe(before.revisions);
    expect(await psql(RESTORE_DB, 'SELECT count(*) FROM medication_versions')).toBe(before.versions);
    expect(Number(before.medications)).toBeGreaterThan(0);
  }, 180_000);

  it('restores clinical content unchanged', async () => {
    const name = await psql(
      RESTORE_DB,
      `SELECT data #>> '{generic_name,en,text}' FROM medication_versions LIMIT 1`,
    );
    expect(name).toBe('Sertraline');
  });

  it('keeps the append-only guarantee after a restore', async () => {
    await expect(psql(RESTORE_DB, `UPDATE audit_log SET action = 'tampered'`)).rejects.toThrow(
      /append-only/,
    );
    await expect(psql(RESTORE_DB, `DELETE FROM revisions`)).rejects.toThrow(/append-only/);
  });

  it('restores the extensions and indexes search depends on', async () => {
    const extensions = await psql(RESTORE_DB, `SELECT string_agg(extname, ',' ORDER BY extname) FROM pg_extension`);
    expect(extensions).toContain('pg_trgm');
    expect(extensions).toContain('unaccent');

    const trigram = await psql(
      RESTORE_DB,
      `SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexdef ILIKE '%gin_trgm_ops%'`,
    );
    expect(Number(trigram)).toBeGreaterThan(0);
  });

  it('reports the schema version it restored', async () => {
    const migrations = await psql(RESTORE_DB, 'SELECT count(*) FROM schema_migrations');
    expect(Number(migrations)).toBeGreaterThan(0);
  });

  it('refuses to overwrite a database that already holds data', async () => {
    await expect(runScript('restore.sh', [await backupPath(), RESTORE_DB])).rejects.toMatchObject({
      stdout: expect.stringContaining('REFUSING'),
    });
  }, 60_000);

  it('overwrites only when explicitly forced', async () => {
    const { stdout } = await runScript('restore.sh', [await backupPath(), RESTORE_DB], {
      RESTORE_FORCE: '1',
    });
    expect(stdout).toContain('RESTORE_FORCE=1');
    expect(stdout).toContain('done');
  }, 180_000);

  it('detects a backup that has been altered', async () => {
    const original = await backupPath();
    const tampered = join(backupDir, 'tampered.dump.gpg');
    await exec('cp', [original, tampered]);
    await exec('cp', [`${original}.sha256`, `${tampered}.sha256`]);
    await appendFile(tampered, 'x');

    await expect(runScript('restore.sh', [tampered, 'medcat_never_created'])).rejects.toMatchObject({
      stdout: expect.stringContaining('checksum mismatch'),
    });
  }, 60_000);

  it('refuses a backup that does not exist', async () => {
    await expect(
      runScript('restore.sh', [join(backupDir, 'nope.dump.gpg'), 'medcat_never_created']),
    ).rejects.toMatchObject({ stdout: expect.stringContaining('no such backup') });
  }, 60_000);
});
