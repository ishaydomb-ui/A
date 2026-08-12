import { all, one } from "./db";

/**
 * Schedule reasoning.
 *
 * "Which days am I picking up the kids?" must never be answered from the model's
 * memory. It resolves here, against mirrored calendar rows, and returns exact
 * dates. Events are classified once at sync time (events.kind / owner_id) so
 * these questions are cheap and consistent.
 */

export interface ScheduleEvent {
  id: number;
  title: string;
  starts_at: string;
  ends_at: string | null;
  all_day: number;
  location: string | null;
  kind: string | null;
  subject_name: string | null;
  owner_name: string | null;
}

const SELECT = `
  SELECT e.id, e.title, e.starts_at, e.ends_at, e.all_day, e.location, e.kind,
         s.name AS subject_name, o.name AS owner_name
  FROM events e
  LEFT JOIN people s ON s.id = e.subject_id
  LEFT JOIN people o ON o.id = e.owner_id
`;

export function eventsBetween(fromISO: string, toISO: string, kind?: string): ScheduleEvent[] {
  const params: unknown[] = [fromISO, toISO];
  let sql = `${SELECT} WHERE e.starts_at >= ? AND e.starts_at < ?`;
  if (kind) {
    sql += ` AND e.kind = ?`;
    params.push(kind);
  }
  return all<ScheduleEvent>(`${sql} ORDER BY e.starts_at`, params);
}

/**
 * Who is doing pickup/dropoff on each day in a window.
 * Returns one entry per day that has a run, with who owns it.
 */
export function pickupSchedule(
  fromISO: string,
  toISO: string,
  personKey?: string,
): Array<{ date: string; title: string; time: string | null; owner: string | null }> {
  const params: unknown[] = [fromISO, toISO];
  let sql = `${SELECT} WHERE e.starts_at >= ? AND e.starts_at < ? AND e.kind IN ('pickup','dropoff')`;
  if (personKey) {
    sql += ` AND o.key = ?`;
    params.push(personKey);
  }
  const rows = all<ScheduleEvent>(`${sql} ORDER BY e.starts_at`, params);
  return rows.map((r) => ({
    date: r.starts_at.slice(0, 10),
    title: r.title,
    time: r.all_day ? null : r.starts_at.slice(11, 16),
    owner: r.owner_name,
  }));
}

/**
 * Days a person is unavailable (on-call shift, reserve duty, travel).
 * This is what makes "can we book Thursday?" answerable without guessing.
 */
export function unavailability(
  fromISO: string,
  toISO: string,
  personKey?: string,
): Array<{ person: string | null; title: string; from: string; to: string | null }> {
  const params: unknown[] = [fromISO, toISO];
  let sql = `${SELECT} WHERE e.starts_at < ? AND (e.ends_at IS NULL OR e.ends_at > ?)
             AND e.kind IN ('oncall','travel','reserve')`;
  // note: reversed bounds - we want events overlapping the window
  const overlapParams: unknown[] = [toISO, fromISO];
  if (personKey) {
    sql += ` AND (s.key = ? OR o.key = ?)`;
    overlapParams.push(personKey, personKey);
  }
  void params;
  const rows = all<ScheduleEvent>(`${sql} ORDER BY e.starts_at`, overlapParams);
  return rows.map((r) => ({
    person: r.subject_name ?? r.owner_name,
    title: r.title,
    from: r.starts_at,
    to: r.ends_at,
  }));
}

/** Everything happening today, for the dashboard header and the morning digest. */
export function today(): ScheduleEvent[] {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return eventsBetween(start.toISOString(), end.toISOString());
}

export function upcoming(days = 7): ScheduleEvent[] {
  const start = new Date();
  const end = new Date(start);
  end.setDate(end.getDate() + days);
  return eventsBetween(start.toISOString(), end.toISOString());
}

/**
 * Conflict check used before the agent books anything.
 * Returns overlapping events so it can say "that clashes with Berry's class".
 */
export function conflicts(startISO: string, endISO: string): ScheduleEvent[] {
  return all<ScheduleEvent>(
    `${SELECT} WHERE e.starts_at < ? AND (e.ends_at IS NULL OR e.ends_at > ?)
     ORDER BY e.starts_at`,
    [endISO, startISO],
  );
}

export function personByKey(key: string) {
  return one<{ id: number; key: string; name: string }>(
    `SELECT id, key, name FROM people WHERE key = ?`,
    [key],
  );
}
