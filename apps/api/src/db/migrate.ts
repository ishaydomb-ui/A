/**
 * Minimal forward-only SQL migration runner.
 *
 * Deliberately dependency-free: migrations are plain .sql files applied in
 * filename order inside a transaction, with a checksum recorded so an already
 * applied file cannot be edited unnoticed. This keeps the schema portable to
 * any Postgres host with no framework lock-in.
 */
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pool, withTransaction } from './pool.js';

const MIGRATIONS_DIR = join(dirname(fileURLToPath(import.meta.url)), '../../migrations');

interface Migration {
  name: string;
  sql: string;
  checksum: string;
}

async function loadMigrations(): Promise<Migration[]> {
  const entries = (await readdir(MIGRATIONS_DIR)).filter((f) => f.endsWith('.sql')).sort();
  const out: Migration[] = [];
  for (const name of entries) {
    const sql = await readFile(join(MIGRATIONS_DIR, name), 'utf8');
    out.push({ name, sql, checksum: createHash('sha256').update(sql).digest('hex') });
  }
  return out;
}

async function ensureTable(): Promise<void> {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      name        text PRIMARY KEY,
      checksum    text        NOT NULL,
      applied_at  timestamptz NOT NULL DEFAULT now(),
      duration_ms integer     NOT NULL
    )
  `);
}

async function applied(): Promise<Map<string, string>> {
  const { rows } = await pool.query<{ name: string; checksum: string }>(
    'SELECT name, checksum FROM schema_migrations',
  );
  return new Map(rows.map((r) => [r.name, r.checksum]));
}

export async function up(log: (msg: string) => void = console.log): Promise<string[]> {
  await ensureTable();
  const done = await applied();
  const all = await loadMigrations();
  const ran: string[] = [];

  for (const m of all) {
    const prior = done.get(m.name);
    if (prior) {
      if (prior !== m.checksum) {
        throw new Error(
          `Migration ${m.name} has changed since it was applied (checksum mismatch). ` +
            'Add a new migration instead of editing an applied one.',
        );
      }
      continue;
    }
    const started = Date.now();
    await withTransaction(async (client) => {
      await client.query(m.sql);
      await client.query(
        'INSERT INTO schema_migrations (name, checksum, duration_ms) VALUES ($1, $2, $3)',
        [m.name, m.checksum, Date.now() - started],
      );
    });
    log(`applied ${m.name} (${Date.now() - started}ms)`);
    ran.push(m.name);
  }
  if (ran.length === 0) log('database is up to date');
  return ran;
}

export async function status(log: (msg: string) => void = console.log): Promise<void> {
  await ensureTable();
  const done = await applied();
  for (const m of await loadMigrations()) {
    const prior = done.get(m.name);
    const state = !prior ? 'PENDING' : prior === m.checksum ? 'applied' : 'CHECKSUM MISMATCH';
    log(`${state.padEnd(18)} ${m.name}`);
  }
}

const invokedDirectly = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (invokedDirectly) {
  const cmd = process.argv[2] ?? 'up';
  const run = cmd === 'status' ? status : up;
  run()
    .then(() => pool.end())
    .then(() => process.exit(0))
    .catch((err) => {
      console.error(err instanceof Error ? err.message : err);
      process.exit(1);
    });
}
