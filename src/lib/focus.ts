import { all, one, run, logActivity } from "./db";

/**
 * "This is what matters right now."
 *
 * Most of the dashboard is standing furniture — schedule, parcels, budget. Now
 * and then one thing dominates for a week or two: a birthday party, a house
 * move, a medical episode. Focus pins that to the top, above everything routine.
 *
 * Two deliberate constraints keep it simple. It points at anything by loose
 * reference rather than a foreign key, so a focus can be a tracker, a case, or
 * just a heading with a link to a spreadsheet in Drive. And it carries an
 * expiry, so last month's party clears itself instead of becoming permanent
 * furniture — a "focus" that never ends isn't a focus.
 */

export interface Focus {
  id: number;
  title: string;
  note: string | null;
  entity_type: string | null;
  entity_ref: string | null;
  url: string | null;
  until: string | null;
  position: number;
  created_by: string;
  created_at: string;
}

export function setFocus(input: {
  title: string;
  note?: string;
  entityType?: "tracker" | "case" | "document" | "task" | "url";
  entityRef?: string;
  url?: string;
  until?: string;
  actor?: string;
}): Focus {
  const res = run(
    `INSERT INTO focus (title, note, entity_type, entity_ref, url, until, created_by)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [
      input.title,
      input.note ?? null,
      input.entityType ?? null,
      input.entityRef ?? null,
      input.url ?? null,
      input.until ?? null,
      input.actor ?? "agent",
    ],
  );
  logActivity({
    actor: input.actor ?? "agent",
    action: "set_focus",
    entityType: "focus",
    entityId: res.lastInsertRowid as number,
    summary: `Focus: ${input.title}${input.until ? ` (until ${input.until})` : ""}`,
  });
  return one<Focus>(`SELECT * FROM focus WHERE id = ?`, [res.lastInsertRowid as number])!;
}

/** Current focuses, expired ones dropped. */
export function activeFocus(): Focus[] {
  return all<Focus>(
    `SELECT * FROM focus
     WHERE until IS NULL OR date(until) >= date('now')
     ORDER BY position, created_at DESC`,
  );
}

export function clearFocus(id: number, actor = "agent"): boolean {
  const item = one<{ title: string }>(`SELECT title FROM focus WHERE id = ?`, [id]);
  if (!item) return false;
  run(`DELETE FROM focus WHERE id = ?`, [id]);
  logActivity({
    actor,
    action: "cleared_focus",
    entityType: "focus",
    entityId: id,
    summary: `Cleared focus: ${item.title}`,
  });
  return true;
}

/** Housekeeping: remove focuses whose date has passed. */
export function sweepFocus(): number {
  return run(`DELETE FROM focus WHERE until IS NOT NULL AND date(until) < date('now')`).changes;
}

/** Where a focus points, for rendering a link. */
export function focusHref(item: Focus): string | null {
  if (item.url) return item.url;
  switch (item.entity_type) {
    case "tracker":
      return `/trackers/${item.entity_ref}`;
    case "case":
      return `/cases`;
    case "document":
      return item.entity_ref ?? null;
    default:
      return null;
  }
}
