import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";

let _db: Database.Database | null = null;

/**
 * Single shared SQLite handle. SQLite is deliberate here: this is a two-person
 * household app, not a multi-tenant SaaS. One file, no ops, trivially backed up.
 * All access goes through this module so swapping to Postgres later is one file.
 */
export function db(): Database.Database {
  if (_db) return _db;

  const file = process.env.DATABASE_PATH || "./data/beitenu.sqlite";
  fs.mkdirSync(path.dirname(file), { recursive: true });

  const conn = new Database(file);
  conn.pragma("journal_mode = WAL");
  conn.pragma("foreign_keys = ON");

  const schemaPath = path.join(process.cwd(), "db", "schema.sql");
  conn.exec(fs.readFileSync(schemaPath, "utf8"));

  _db = conn;
  return conn;
}

export function all<T = Record<string, unknown>>(sql: string, params: unknown[] = []): T[] {
  return db().prepare(sql).all(...(params as never[])) as T[];
}

export function one<T = Record<string, unknown>>(sql: string, params: unknown[] = []): T | undefined {
  return db().prepare(sql).get(...(params as never[])) as T | undefined;
}

export function run(sql: string, params: unknown[] = []) {
  return db().prepare(sql).run(...(params as never[]));
}

/** Parse a JSON column that may be null/invalid without throwing. */
export function json<T>(value: unknown, fallback: T): T {
  if (typeof value !== "string" || !value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

/**
 * Every state change the agent makes gets a row here. This is what makes
 * "act on routine things automatically" safe - nothing is invisible.
 */
export function logActivity(entry: {
  actor: string;
  action: string;
  summary: string;
  entityType?: string;
  entityId?: string | number;
  detail?: unknown;
  skillKey?: string;
}) {
  run(
    `INSERT INTO activity (actor, action, entity_type, entity_id, summary, detail, skill_key)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [
      entry.actor,
      entry.action,
      entry.entityType ?? null,
      entry.entityId != null ? String(entry.entityId) : null,
      entry.summary,
      entry.detail ? JSON.stringify(entry.detail) : null,
      entry.skillKey ?? null,
    ],
  );
}
