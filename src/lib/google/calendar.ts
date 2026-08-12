import { all, one, run, logActivity } from "../db";
import { accessTokenFor } from "./oauth";
import { classifyEvent, type PersonRef } from "./classify";

/**
 * Google Calendar → local events table.
 *
 * Incremental by default: Google hands back a syncToken, we store it, and the
 * next run asks only for what changed. A routine sync is one small request.
 * When Google invalidates a token (410) we fall back to a bounded full window
 * rather than pulling a decade of history.
 */

const API = "https://www.googleapis.com/calendar/v3";

interface GoogleEvent {
  id: string;
  status?: string;
  summary?: string;
  description?: string;
  location?: string;
  start?: { date?: string; dateTime?: string };
  end?: { date?: string; dateTime?: string };
  recurringEventId?: string;
  attendees?: Array<{ email?: string }>;
  creator?: { email?: string };
  organizer?: { email?: string };
}

export interface SyncResult {
  calendar: string;
  upserted: number;
  deleted: number;
  fullResync: boolean;
  error?: string;
}

async function api(token: string, path: string, params: Record<string, string> = {}) {
  const url = new URL(`${API}${path}`);
  for (const [k, v] of Object.entries(params)) if (v) url.searchParams.set(k, v);
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    const err = new Error(`Calendar API ${res.status}: ${await res.text()}`) as Error & {
      status: number;
    };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** Discover the person's calendars and record them as sync candidates. */
export async function discoverCalendars(personId: number): Promise<string[]> {
  const token = await accessTokenFor(personId);
  if (!token) throw new Error("This person has not connected Google yet.");

  const data = (await api(token, "/users/me/calendarList")) as {
    items?: Array<{ id: string; summary?: string; selected?: boolean }>;
  };

  const found: string[] = [];
  for (const cal of data.items ?? []) {
    // Holiday calendars are read-only noise for a household planner; skip them
    // by default but leave the row so they can be switched on.
    const isHoliday = /holiday@group\.v\.calendar\.google\.com$/i.test(cal.id);
    run(
      `INSERT INTO calendar_sync (person_id, calendar_id, summary, enabled)
       VALUES (?, ?, ?, ?)
       ON CONFLICT(person_id, calendar_id) DO UPDATE SET summary = excluded.summary`,
      [personId, cal.id, cal.summary ?? cal.id, isHoliday ? 0 : 1],
    );
    found.push(cal.id);
  }
  return found;
}

export async function syncPerson(personId: number): Promise<SyncResult[]> {
  const calendars = all<{ calendar_id: string; sync_token: string | null }>(
    `SELECT calendar_id, sync_token FROM calendar_sync WHERE person_id = ? AND enabled = 1`,
    [personId],
  );
  if (calendars.length === 0) {
    await discoverCalendars(personId);
    return syncPerson(personId);
  }

  const results: SyncResult[] = [];
  for (const cal of calendars) {
    try {
      results.push(await syncCalendar(personId, cal.calendar_id, cal.sync_token));
    } catch (err) {
      const message = (err as Error).message;
      run(
        `UPDATE calendar_sync SET last_synced = datetime('now'), last_result = ?
         WHERE person_id = ? AND calendar_id = ?`,
        [`error: ${message}`.slice(0, 300), personId, cal.calendar_id],
      );
      results.push({
        calendar: cal.calendar_id,
        upserted: 0,
        deleted: 0,
        fullResync: false,
        error: message,
      });
    }
  }

  const total = results.reduce((s, r) => s + r.upserted, 0);
  if (total > 0) {
    logActivity({
      actor: "automation",
      action: "synced_calendar",
      summary: `Synced ${total} calendar event(s) across ${results.length} calendar(s)`,
      detail: results,
    });
  }
  return results;
}

async function syncCalendar(
  personId: number,
  calendarId: string,
  syncToken: string | null,
): Promise<SyncResult> {
  const token = await accessTokenFor(personId);
  if (!token) throw new Error("no Google connection");

  const people = all<PersonRef>(
    `SELECT key, name, name_he AS nameHe, email, role FROM people`,
  );
  const peopleByKey = new Map(
    all<{ id: number; key: string }>(`SELECT id, key FROM people`).map((p) => [p.key, p.id]),
  );

  let pageToken: string | undefined;
  let nextSyncToken: string | undefined;
  let upserted = 0;
  let deleted = 0;
  let fullResync = false;

  // A full sync still needs a bound, or the first run drags in years of history.
  const windowStart = new Date();
  windowStart.setMonth(windowStart.getMonth() - 1);
  const windowEnd = new Date();
  windowEnd.setMonth(windowEnd.getMonth() + 6);

  let useToken = syncToken;

  for (;;) {
    let data: {
      items?: GoogleEvent[];
      nextPageToken?: string;
      nextSyncToken?: string;
    };

    try {
      data = await api(token, `/calendars/${encodeURIComponent(calendarId)}/events`, {
        ...(useToken
          ? { syncToken: useToken }
          : {
              timeMin: windowStart.toISOString(),
              timeMax: windowEnd.toISOString(),
            }),
        // singleEvents expands recurring series into concrete dated instances,
        // which is what makes "which days" answerable at all.
        singleEvents: "true",
        maxResults: "250",
        ...(pageToken ? { pageToken } : {}),
      });
    } catch (err) {
      // 410 Gone = the stored token is too old. Start again from scratch.
      if ((err as { status?: number }).status === 410 && useToken) {
        useToken = null;
        pageToken = undefined;
        fullResync = true;
        run(`UPDATE calendar_sync SET sync_token = NULL WHERE person_id = ? AND calendar_id = ?`, [
          personId,
          calendarId,
        ]);
        continue;
      }
      throw err;
    }

    for (const ev of data.items ?? []) {
      if (ev.status === "cancelled") {
        const res = run(`DELETE FROM events WHERE external_id = ?`, [ev.id]);
        deleted += res.changes;
        continue;
      }

      const startsAt = ev.start?.dateTime ?? ev.start?.date;
      if (!startsAt) continue;
      const allDay = Boolean(ev.start?.date && !ev.start?.dateTime);

      const classification = classifyEvent(
        {
          title: ev.summary ?? "(no title)",
          description: ev.description,
          location: ev.location,
          allDay,
          creatorEmail: ev.creator?.email,
          organizerEmail: ev.organizer?.email,
        },
        people,
      );

      run(
        `INSERT INTO events
          (external_id, calendar_id, title, description, location, starts_at, ends_at,
           all_day, recurrence_id, attendees, kind, subject_id, owner_id, source, raw, synced_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'google', ?, datetime('now'))
         ON CONFLICT(external_id) DO UPDATE SET
           title = excluded.title, description = excluded.description,
           location = excluded.location, starts_at = excluded.starts_at,
           ends_at = excluded.ends_at, all_day = excluded.all_day,
           attendees = excluded.attendees, kind = excluded.kind,
           subject_id = excluded.subject_id, owner_id = excluded.owner_id,
           raw = excluded.raw, synced_at = datetime('now')`,
        [
          ev.id,
          calendarId,
          ev.summary ?? "(no title)",
          ev.description ?? null,
          ev.location ?? null,
          normalizeTime(startsAt, allDay),
          normalizeTime(ev.end?.dateTime ?? ev.end?.date ?? null, allDay),
          allDay ? 1 : 0,
          ev.recurringEventId ?? null,
          ev.attendees ? JSON.stringify(ev.attendees.map((a) => a.email).filter(Boolean)) : null,
          classification.kind,
          classification.subjectKey ? (peopleByKey.get(classification.subjectKey) ?? null) : null,
          classification.ownerKey ? (peopleByKey.get(classification.ownerKey) ?? null) : null,
          JSON.stringify(ev).slice(0, 8000),
        ],
      );
      upserted++;
    }

    if (data.nextPageToken) {
      pageToken = data.nextPageToken;
      continue;
    }
    nextSyncToken = data.nextSyncToken;
    break;
  }

  run(
    `UPDATE calendar_sync SET sync_token = ?, last_synced = datetime('now'), last_result = ?
     WHERE person_id = ? AND calendar_id = ?`,
    [
      nextSyncToken ?? null,
      `ok: ${upserted} upserted, ${deleted} removed`,
      personId,
      calendarId,
    ],
  );

  return { calendar: calendarId, upserted, deleted, fullResync };
}

/** All-day events arrive as bare dates; store them as midnight so ordering works. */
function normalizeTime(value: string | null, allDay: boolean): string | null {
  if (!value) return null;
  if (allDay && value.length === 10) return `${value}T00:00:00.000Z`;
  return new Date(value).toISOString();
}

/** Sync everyone who has connected Google. Used by the scheduler. */
export async function syncAll(): Promise<Record<string, SyncResult[]>> {
  const connected = all<{ id: number; key: string }>(
    `SELECT p.id, p.key FROM people p
     JOIN oauth_tokens t ON t.person_id = p.id AND t.provider = 'google'`,
  );
  const out: Record<string, SyncResult[]> = {};
  for (const person of connected) {
    try {
      out[person.key] = await syncPerson(person.id);
    } catch (err) {
      out[person.key] = [
        { calendar: "-", upserted: 0, deleted: 0, fullResync: false, error: (err as Error).message },
      ];
    }
  }
  return out;
}

export function syncStatus() {
  return all(
    `SELECT p.key AS person, cs.calendar_id, cs.summary, cs.enabled,
            cs.last_synced, cs.last_result
     FROM calendar_sync cs JOIN people p ON p.id = cs.person_id
     ORDER BY p.key, cs.summary`,
  );
}

export function connectedPeople() {
  return all<{ key: string; name: string; connected_at: string }>(
    `SELECT p.key, p.name, t.updated_at AS connected_at
     FROM people p JOIN oauth_tokens t ON t.person_id = p.id AND t.provider = 'google'
     ORDER BY p.key`,
  );
}

export function personIdByKey(key: string): number | undefined {
  return one<{ id: number }>(`SELECT id FROM people WHERE key = ?`, [key])?.id;
}
