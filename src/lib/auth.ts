import { one } from "./db";

/**
 * Database-backed auth helpers. Node runtime only — the edge-safe session
 * primitives live in session.ts and are re-exported here for convenience.
 */

export {
  SESSION_COOKIE,
  SESSION_MAX_AGE,
  signSession,
  verifySession,
} from "./session";

/**
 * Who may sign in at all.
 *
 * This dashboard holds the family's entire life, so access is an allowlist of
 * household adults - not "anyone with a Google account". An email that is not
 * on it is refused at the callback, before any session is issued.
 */
export function personByEmail(email: string): { id: number; key: string; name: string } | null {
  return (
    one<{ id: number; key: string; name: string }>(
      `SELECT id, key, name FROM people
       WHERE role = 'adult' AND lower(email) = lower(?)`,
      [email.trim()],
    ) ?? null
  );
}

/** Reads the signed-in person inside a Node route handler or server component. */
export async function currentPerson(): Promise<{ id: number; key: string; name: string } | null> {
  const { cookies } = await import("next/headers");
  const { verifySession, SESSION_COOKIE } = await import("./session");
  const store = await cookies();
  const key = await verifySession(store.get(SESSION_COOKIE)?.value);
  if (!key) return null;
  return (
    one<{ id: number; key: string; name: string }>(
      `SELECT id, key, name FROM people WHERE key = ?`,
      [key],
    ) ?? null
  );
}

/**
 * Actor for an action. Falls back to the given default only when sign-in is not
 * configured at all, so local development still works before OAuth exists.
 */
export async function actorKey(fallback = "ishay"): Promise<string> {
  if (!process.env.GOOGLE_CLIENT_ID) return fallback;
  return (await currentPerson())?.key ?? fallback;
}
