import { query } from '../db/pool.js';
import { tooManyRequests } from '../lib/errors.js';

/**
 * Fixed-window rate limiter backed by Postgres, so limits hold across API
 * replicas and survive a restart. Windows are cheap rows cleaned up lazily.
 */
export interface LimitRule {
  /** Window length in milliseconds. */
  windowMs: number;
  /** Maximum requests permitted per window. */
  max: number;
}

export const LIMITS = {
  login: { windowMs: 15 * 60_000, max: 10 },
  passwordReset: { windowMs: 60 * 60_000, max: 5 },
  mfa: { windowMs: 15 * 60_000, max: 10 },
  inviteAccept: { windowMs: 60 * 60_000, max: 10 },
  search: { windowMs: 60_000, max: 120 },
  import: { windowMs: 60 * 60_000, max: 20 },
  mutation: { windowMs: 60_000, max: 60 },
} as const satisfies Record<string, LimitRule>;

export type LimitName = keyof typeof LIMITS;

export interface LimitResult {
  allowed: boolean;
  remaining: number;
  retryAfterSeconds: number;
}

export async function consume(name: LimitName, key: string): Promise<LimitResult> {
  const rule = LIMITS[name];
  const now = Date.now();
  const windowStart = new Date(Math.floor(now / rule.windowMs) * rule.windowMs);
  const bucket = `${name}:${key}`;

  const { rows } = await query<{ count: number }>(
    `INSERT INTO rate_limit_counters (bucket, window_start, count)
     VALUES ($1, $2, 1)
     ON CONFLICT (bucket, window_start)
     DO UPDATE SET count = rate_limit_counters.count + 1
     RETURNING count`,
    [bucket, windowStart],
  );
  const count = rows[0]?.count ?? 1;
  const resetAt = windowStart.getTime() + rule.windowMs;
  return {
    allowed: count <= rule.max,
    remaining: Math.max(0, rule.max - count),
    retryAfterSeconds: Math.max(1, Math.ceil((resetAt - now) / 1000)),
  };
}

/** Consumes a slot and throws 429 when the limit is exceeded. */
export async function enforce(name: LimitName, key: string): Promise<void> {
  const result = await consume(name, key);
  if (!result.allowed) {
    throw tooManyRequests('Too many requests. Please wait and try again.', {
      retryAfterSeconds: result.retryAfterSeconds,
    });
  }
}

/** Clears a bucket after a successful action, so a valid login resets the count. */
export async function reset(name: LimitName, key: string): Promise<void> {
  await query('DELETE FROM rate_limit_counters WHERE bucket = $1', [`${name}:${key}`]);
}

/** Removes windows that can no longer be current. Called periodically. */
export async function purgeExpired(): Promise<number> {
  const maxWindow = Math.max(...Object.values(LIMITS).map((l) => l.windowMs));
  const { rowCount } = await query(
    'DELETE FROM rate_limit_counters WHERE window_start < $1',
    [new Date(Date.now() - maxWindow * 2)],
  );
  return rowCount ?? 0;
}
