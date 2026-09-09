import pg from 'pg';
import { config } from '../config.js';

/**
 * Postgres returns bigint/numeric as strings by default to avoid precision
 * loss. Our bigints are surrogate keys and counters well inside Number range,
 * so parsing them keeps the API surface simple.
 */
pg.types.setTypeParser(20, (v) => Number(v));

export const pool = new pg.Pool({
  connectionString: config.databaseUrl,
  max: config.dbPoolMax,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 10_000,
  ssl: config.dbSsl ? { rejectUnauthorized: true } : undefined,
});

export type Queryable = Pick<pg.PoolClient, 'query'>;

export async function query<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params: unknown[] = [],
  client?: Queryable,
): Promise<pg.QueryResult<T>> {
  return (client ?? pool).query<T>(text, params as never[]);
}

/** Runs `fn` inside a transaction, rolling back on any thrown error. */
export async function withTransaction<T>(
  fn: (client: pg.PoolClient) => Promise<T>,
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    try {
      await client.query('ROLLBACK');
    } catch {
      // The connection is already broken; releasing it discards it.
    }
    throw err;
  } finally {
    client.release();
  }
}

export async function closePool(): Promise<void> {
  await pool.end();
}
