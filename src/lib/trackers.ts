import { all, one, run, json, logActivity } from "./db";

/**
 * Trackers are the extensibility primitive. "Coupons", "movies to watch",
 * "gift ideas", "home maintenance" are all the same shape: a named rubric with
 * user-defined fields. Adding a new one is an INSERT, not a migration.
 */

export type FieldType =
  | "text"
  | "number"
  | "money"
  | "date"
  | "bool"
  | "select"
  | "url"
  | "person";

export interface TrackerField {
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  options?: string[];
}

export interface TrackerBehaviors {
  /** field name holding an expiry date; lifted into tracker_items.expires_at */
  expireField?: string;
  /** what to do once expired: 'archive' hides it, 'flag' keeps it visible */
  expireAction?: "archive" | "flag";
  /** warn this many days before expiry */
  notifyBeforeDays?: number;
  /** field names that together identify a duplicate */
  dedupeOn?: string[];
}

export interface Tracker {
  id: number;
  key: string;
  name: string;
  icon: string | null;
  description: string | null;
  fields: TrackerField[];
  view: string;
  behaviors: TrackerBehaviors;
  builtin: number;
}

interface TrackerRow {
  id: number;
  key: string;
  name: string;
  icon: string | null;
  description: string | null;
  fields: string;
  view: string;
  behaviors: string | null;
  builtin: number;
}

function hydrate(row: TrackerRow): Tracker {
  return {
    ...row,
    fields: json<TrackerField[]>(row.fields, []),
    behaviors: json<TrackerBehaviors>(row.behaviors, {}),
  };
}

export function listTrackers(): Tracker[] {
  return all<TrackerRow>(`SELECT * FROM trackers ORDER BY builtin DESC, name`).map(hydrate);
}

export function getTracker(key: string): Tracker | undefined {
  const row = one<TrackerRow>(`SELECT * FROM trackers WHERE key = ?`, [key]);
  return row ? hydrate(row) : undefined;
}

export function createTracker(input: {
  key: string;
  name: string;
  icon?: string;
  description?: string;
  fields: TrackerField[];
  view?: string;
  behaviors?: TrackerBehaviors;
  actor?: string;
}): Tracker {
  run(
    `INSERT INTO trackers (key, name, icon, description, fields, view, behaviors)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [
      input.key,
      input.name,
      input.icon ?? null,
      input.description ?? null,
      JSON.stringify(input.fields),
      input.view ?? "list",
      JSON.stringify(input.behaviors ?? {}),
    ],
  );
  logActivity({
    actor: input.actor ?? "agent",
    action: "created_tracker",
    entityType: "tracker",
    entityId: input.key,
    summary: `Created tracker "${input.name}"`,
    detail: { fields: input.fields.map((f) => f.name) },
  });
  return getTracker(input.key)!;
}

export interface TrackerItem {
  id: number;
  tracker_id: number;
  data: Record<string, unknown>;
  status: string;
  expires_at: string | null;
  owner_id: number | null;
  created_at: string;
  updated_at: string;
}

interface ItemRow extends Omit<TrackerItem, "data"> {
  data: string;
}

export function addItem(
  trackerKey: string,
  data: Record<string, unknown>,
  opts: { status?: string; ownerId?: number; actor?: string } = {},
): TrackerItem | { duplicateOf: number } {
  const tracker = getTracker(trackerKey);
  if (!tracker) throw new Error(`No tracker "${trackerKey}"`);

  // Dedupe before insert so repeated intake (a forwarded coupon email twice,
  // the same film mentioned in two chats) doesn't produce clutter.
  const dedupeOn = tracker.behaviors.dedupeOn ?? [];
  if (dedupeOn.length) {
    const existing = queryItems(trackerKey, { status: "any" });
    const dup = existing.find((item) =>
      dedupeOn.every(
        (f) =>
          String(item.data[f] ?? "").trim().toLowerCase() ===
          String(data[f] ?? "").trim().toLowerCase(),
      ),
    );
    if (dup) return { duplicateOf: dup.id };
  }

  const expireField = tracker.behaviors.expireField;
  const expiresAt = expireField ? (data[expireField] as string | undefined) ?? null : null;

  const res = run(
    `INSERT INTO tracker_items (tracker_id, data, status, expires_at, owner_id)
     VALUES (?, ?, ?, ?, ?)`,
    [
      tracker.id,
      JSON.stringify(data),
      opts.status ?? "active",
      expiresAt,
      opts.ownerId ?? null,
    ],
  );
  logActivity({
    actor: opts.actor ?? "agent",
    action: "added_tracker_item",
    entityType: "tracker_item",
    entityId: res.lastInsertRowid as number,
    summary: `Added to ${tracker.name}: ${summarize(tracker, data)}`,
    detail: data,
  });
  return getItem(res.lastInsertRowid as number)!;
}

export function getItem(id: number): TrackerItem | undefined {
  const row = one<ItemRow>(`SELECT * FROM tracker_items WHERE id = ?`, [id]);
  return row ? { ...row, data: json(row.data, {}) } : undefined;
}

/**
 * The query the agent calls for "which coupons are still available".
 * `available` means: active, and not past its expiry date.
 */
export function queryItems(
  trackerKey: string,
  opts: {
    status?: string | "any";
    availableOnly?: boolean;
    expiringWithinDays?: number;
    match?: Record<string, unknown>;
    limit?: number;
  } = {},
): TrackerItem[] {
  const tracker = getTracker(trackerKey);
  if (!tracker) throw new Error(`No tracker "${trackerKey}"`);

  const where: string[] = ["tracker_id = ?"];
  const params: unknown[] = [tracker.id];

  if (opts.availableOnly) {
    where.push("status = 'active'");
    where.push("(expires_at IS NULL OR date(expires_at) >= date('now'))");
  } else if (opts.status && opts.status !== "any") {
    where.push("status = ?");
    params.push(opts.status);
  }

  if (opts.expiringWithinDays != null) {
    where.push("expires_at IS NOT NULL");
    where.push(`date(expires_at) <= date('now', '+' || ? || ' days')`);
    where.push("date(expires_at) >= date('now')");
    params.push(opts.expiringWithinDays);
  }

  const rows = all<ItemRow>(
    `SELECT * FROM tracker_items WHERE ${where.join(" AND ")}
     ORDER BY (expires_at IS NULL), expires_at ASC, created_at DESC
     LIMIT ?`,
    [...params, opts.limit ?? 200],
  );

  let items = rows.map((r) => ({ ...r, data: json<Record<string, unknown>>(r.data, {}) }));

  // Field matching happens in JS because the payload is schemaless JSON.
  if (opts.match) {
    items = items.filter((item) =>
      Object.entries(opts.match!).every(([k, v]) =>
        String(item.data[k] ?? "").toLowerCase().includes(String(v).toLowerCase()),
      ),
    );
  }
  return items;
}

export function updateItem(
  id: number,
  patch: { data?: Record<string, unknown>; status?: string; actor?: string },
): TrackerItem | undefined {
  const current = getItem(id);
  if (!current) return undefined;

  const merged = patch.data ? { ...current.data, ...patch.data } : current.data;
  run(
    `UPDATE tracker_items SET data = ?, status = ?, updated_at = datetime('now') WHERE id = ?`,
    [JSON.stringify(merged), patch.status ?? current.status, id],
  );
  logActivity({
    actor: patch.actor ?? "agent",
    action: "updated_tracker_item",
    entityType: "tracker_item",
    entityId: id,
    summary: `Updated item #${id}${patch.status ? ` -> ${patch.status}` : ""}`,
    detail: patch,
  });
  return getItem(id);
}

/**
 * Housekeeping run by the scheduler: retire anything past its expiry so
 * "what's still available" stays true without anyone tidying up.
 */
export function sweepExpired(): { archived: number; flagged: number } {
  let archived = 0;
  let flagged = 0;
  for (const tracker of listTrackers()) {
    if (!tracker.behaviors.expireField) continue;
    const action = tracker.behaviors.expireAction ?? "archive";
    const res = run(
      `UPDATE tracker_items SET status = ?, updated_at = datetime('now')
       WHERE tracker_id = ? AND status = 'active'
         AND expires_at IS NOT NULL AND date(expires_at) < date('now')`,
      [action === "archive" ? "expired" : "flagged", tracker.id],
    );
    if (action === "archive") archived += res.changes;
    else flagged += res.changes;
  }
  return { archived, flagged };
}

/** Best-effort human label for an item, used in activity lines and digests. */
export function summarize(tracker: Tracker, data: Record<string, unknown>): string {
  const first = tracker.fields.find((f) => f.type === "text" && data[f.name]);
  return String(data[first?.name ?? tracker.fields[0]?.name ?? "id"] ?? "item");
}
