import { all, one, run, logActivity } from "./db";
import { encrypt, decrypt } from "./crypto";

/**
 * Household facts — the quiet backbone of "just ask it anything".
 *
 * Three shapes of question, one store:
 *   standing    "what's Yanai's ID number", "mum's building code"
 *   occurrence  "when was the last blood test"      (occurred_on set)
 *   expiring    "when do I renew my licence"        (valid_until set)
 *
 * Deliberately low-profile: there is no facts tab in the main navigation. You
 * tell the assistant something and it keeps it; you ask and it knows. The page
 * exists so nothing is trapped where only the agent can reach it.
 *
 * Sensitive values (ID numbers, door codes, policy numbers) are encrypted at
 * rest with the same key as store credentials, and are excluded from digests
 * and any outbound email — a door code should not turn up in a morning brief.
 */

export type FactCategory =
  | "identity"
  | "access"
  | "location"
  | "medical"
  | "vehicle"
  | "admin"
  | "contact"
  | "other";

export interface Fact {
  id: number;
  subject: string;
  label: string;
  value: string;
  sensitive: number;
  category: string | null;
  occurred_on: string | null;
  valid_until: string | null;
  remind_days_before: number;
  source: string;
  created_at: string;
  updated_at: string;
}

/** Categories where a value should be encrypted unless told otherwise. */
const SENSITIVE_BY_DEFAULT: FactCategory[] = ["identity", "access"];

function encodeValue(value: string, sensitive: boolean): string {
  if (!sensitive) return value;
  try {
    return encrypt(value);
  } catch {
    // No CREDENTIALS_KEY configured. Storing a door code in plaintext when the
    // caller asked for encryption would be a silent downgrade, so refuse.
    throw new Error(
      "Cannot store a sensitive fact: CREDENTIALS_KEY is not set. " +
        "Set it, or store this fact with sensitive=false if it is not private.",
    );
  }
}

function decodeValue(fact: Fact): string {
  if (!fact.sensitive) return fact.value;
  try {
    return decrypt(fact.value);
  } catch {
    return "(encrypted — CREDENTIALS_KEY missing or changed)";
  }
}

export function rememberFact(input: {
  subject: string;
  label: string;
  value: string;
  category?: FactCategory;
  sensitive?: boolean;
  occurredOn?: string;
  validUntil?: string;
  remindDaysBefore?: number;
  source?: string;
  actor?: string;
}): { id: number; updated: boolean } {
  const sensitive =
    input.sensitive ?? SENSITIVE_BY_DEFAULT.includes(input.category ?? "other");

  const subject = input.subject.trim().toLowerCase();
  const label = input.label.trim();

  // A dated occurrence is always a new row - that is the point of a log.
  // Everything else updates in place, so "what's the code" has one answer.
  const existing = input.occurredOn
    ? undefined
    : one<{ id: number }>(
        `SELECT id FROM facts WHERE lower(subject) = ? AND lower(label) = ?
           AND occurred_on IS NULL`,
        [subject, label.toLowerCase()],
      );

  if (existing) {
    run(
      `UPDATE facts SET value = ?, sensitive = ?, category = COALESCE(?, category),
         valid_until = COALESCE(?, valid_until),
         remind_days_before = COALESCE(?, remind_days_before),
         updated_at = datetime('now')
       WHERE id = ?`,
      [
        encodeValue(input.value, sensitive),
        sensitive ? 1 : 0,
        input.category ?? null,
        input.validUntil ?? null,
        input.remindDaysBefore ?? null,
        existing.id,
      ],
    );
    logActivity({
      actor: input.actor ?? "agent",
      action: "updated_fact",
      entityType: "fact",
      entityId: existing.id,
      // Never log the value itself - the activity feed is not a secret store.
      summary: `Updated ${subject}: ${label}`,
    });
    return { id: existing.id, updated: true };
  }

  const res = run(
    `INSERT INTO facts
       (subject, label, value, sensitive, category, occurred_on, valid_until,
        remind_days_before, source)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      subject,
      label,
      encodeValue(input.value, sensitive),
      sensitive ? 1 : 0,
      input.category ?? null,
      input.occurredOn ?? null,
      input.validUntil ?? null,
      input.remindDaysBefore ?? 30,
      input.source ?? "agent",
    ],
  );
  logActivity({
    actor: input.actor ?? "agent",
    action: "remembered_fact",
    entityType: "fact",
    entityId: res.lastInsertRowid as number,
    summary: `Remembered ${subject}: ${label}`,
  });
  return { id: res.lastInsertRowid as number, updated: false };
}

export interface FactResult {
  id: number;
  subject: string;
  label: string;
  value: string;
  category: string | null;
  sensitive: boolean;
  occurred_on: string | null;
  valid_until: string | null;
}

/**
 * Look facts up. `latestOnly` answers "when was the last time we..." by
 * returning just the most recent dated occurrence per subject+label.
 */
export function recallFacts(opts: {
  query?: string;
  subject?: string;
  category?: string;
  latestOnly?: boolean;
  limit?: number;
}): FactResult[] {
  const where: string[] = ["1=1"];
  const params: unknown[] = [];

  if (opts.subject) {
    where.push("lower(subject) LIKE ?");
    params.push(`%${opts.subject.toLowerCase()}%`);
  }
  if (opts.category) {
    where.push("category = ?");
    params.push(opts.category);
  }
  if (opts.query) {
    // Sensitive values are ciphertext, so searching the value column would
    // silently miss them. Match on subject and label only.
    where.push("(lower(subject) LIKE ? OR lower(label) LIKE ?)");
    const like = `%${opts.query.toLowerCase()}%`;
    params.push(like, like);
  }

  const rows = all<Fact>(
    `SELECT * FROM facts WHERE ${where.join(" AND ")}
     ORDER BY (occurred_on IS NULL) DESC, occurred_on DESC, updated_at DESC
     LIMIT ?`,
    [...params, opts.limit ?? 50],
  );

  let results = rows;
  if (opts.latestOnly) {
    const seen = new Set<string>();
    results = rows.filter((r) => {
      const key = `${r.subject}|${r.label.toLowerCase()}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  return results.map((f) => ({
    id: f.id,
    subject: f.subject,
    label: f.label,
    value: decodeValue(f),
    category: f.category,
    sensitive: Boolean(f.sensitive),
    occurred_on: f.occurred_on,
    valid_until: f.valid_until,
  }));
}

/**
 * Renewals coming up. Feeds the morning brief so a licence expiry surfaces
 * before it becomes a problem, rather than when someone remembers to ask.
 */
export function expiringFacts(withinDays?: number): FactResult[] {
  const rows = all<Fact>(
    `SELECT * FROM facts WHERE valid_until IS NOT NULL
       AND date(valid_until) >= date('now')
       AND date(valid_until) <= date('now', '+' || COALESCE(?, remind_days_before) || ' days')
     ORDER BY valid_until`,
    [withinDays ?? null],
  );
  return rows.map((f) => ({
    id: f.id,
    subject: f.subject,
    label: f.label,
    // Renewal prompts never need the secret itself, only that it is due.
    value: f.sensitive ? "(sensitive)" : f.value,
    category: f.category,
    sensitive: Boolean(f.sensitive),
    occurred_on: f.occurred_on,
    valid_until: f.valid_until,
  }));
}

export function forgetFact(id: number, actor = "agent"): boolean {
  const fact = one<{ subject: string; label: string }>(
    `SELECT subject, label FROM facts WHERE id = ?`,
    [id],
  );
  if (!fact) return false;
  run(`DELETE FROM facts WHERE id = ?`, [id]);
  logActivity({
    actor,
    action: "forgot_fact",
    entityType: "fact",
    entityId: id,
    summary: `Deleted ${fact.subject}: ${fact.label}`,
  });
  return true;
}

/** Subjects we hold anything about, for the settings page. */
export function factSubjects(): Array<{ subject: string; n: number }> {
  return all<{ subject: string; n: number }>(
    `SELECT subject, COUNT(*) AS n FROM facts GROUP BY subject ORDER BY n DESC`,
  );
}
