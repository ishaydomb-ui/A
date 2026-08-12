import { all, run, json, logActivity } from "./db";
import { ask } from "./agent";
import { sweepExpired } from "./trackers";

/**
 * Trigger -> action rules.
 *
 * Automations are rows, not code, so a new "when X happens do Y" is added from
 * the dashboard without a deploy. The action is almost always "run this skill",
 * which keeps automated work identical to work done by hand.
 */

interface AutomationRow {
  id: number;
  name: string;
  trigger_type: string;
  trigger_config: string;
  action_type: string;
  action_config: string;
  last_run_at: string | null;
}

export async function runDueAutomations(): Promise<{
  ran: Array<{ name: string; result: string }>;
  skipped: number;
}> {
  const rows = all<AutomationRow>(`SELECT * FROM automations WHERE enabled = 1`);
  const ran: Array<{ name: string; result: string }> = [];
  let skipped = 0;

  // Pull calendars first: every downstream rule reasons about the schedule,
  // so stale events would make the whole tick answer yesterday's questions.
  try {
    const { syncAll } = await import("./google/calendar");
    await syncAll();
  } catch (err) {
    logActivity({
      actor: "automation",
      action: "calendar_sync_failed",
      summary: `Calendar sync failed: ${(err as Error).message}`,
    });
  }

  // Housekeeping next so anything downstream sees an accurate picture.
  const swept = sweepExpired();
  if (swept.archived || swept.flagged) {
    logActivity({
      actor: "automation",
      action: "swept_expired",
      summary: `Retired ${swept.archived} expired item(s), flagged ${swept.flagged}`,
    });
  }

  for (const row of rows) {
    if (!isDue(row)) {
      skipped++;
      continue;
    }
    try {
      const result = await runAutomation(row);
      run(
        `UPDATE automations SET last_run_at = datetime('now'), last_result = ? WHERE id = ?`,
        [result.slice(0, 500), row.id],
      );
      ran.push({ name: row.name, result });
    } catch (err) {
      const message = (err as Error).message;
      run(
        `UPDATE automations SET last_run_at = datetime('now'), last_result = ? WHERE id = ?`,
        [`error: ${message}`, row.id],
      );
      ran.push({ name: row.name, result: `error: ${message}` });
    }
  }

  return { ran, skipped };
}

/**
 * Minimal cron matching - enough for the hourly/daily/weekly rules a household
 * actually uses, without pulling in a scheduling dependency.
 * Supports "m h dom mon dow" with numbers, '*' and comma lists.
 */
function isDue(row: AutomationRow): boolean {
  const config = json<Record<string, unknown>>(row.trigger_config, {});

  if (row.trigger_type === "schedule") {
    const cron = config.cron as string | undefined;
    if (!cron) return false;
    if (!cronMatches(cron, new Date())) return false;
    // Don't fire twice within the same hour if the tick runs every 15 minutes.
    if (row.last_run_at) {
      const last = new Date(row.last_run_at + "Z").getTime();
      if (Date.now() - last < 55 * 60 * 1000) return false;
    }
    return true;
  }

  if (row.trigger_type === "case_stale") {
    return (
      all<{ n: number }>(
        `SELECT COUNT(*) AS n FROM cases
         WHERE status IN ('open','waiting') AND chase_after IS NOT NULL
           AND date(chase_after) <= date('now')`,
      )[0]?.n > 0
    );
  }

  if (row.trigger_type === "pantry_expiring") {
    const days = (config.withinDays as number) ?? 2;
    return (
      all<{ n: number }>(
        `SELECT COUNT(*) AS n FROM pantry_items
         WHERE expires_at IS NOT NULL
           AND date(expires_at) <= date('now', '+' || ? || ' days')`,
        [days],
      )[0]?.n > 0
    );
  }

  if (row.trigger_type === "fact_expiring") {
    const days = (config.withinDays as number) ?? 30;
    return (
      all<{ n: number }>(
        `SELECT COUNT(*) AS n FROM facts
         WHERE valid_until IS NOT NULL
           AND date(valid_until) >= date('now')
           AND date(valid_until) <= date('now', '+' || ? || ' days')`,
        [days],
      )[0]?.n > 0
    );
  }

  if (row.trigger_type === "tracker_expiring") {
    const days = (config.withinDays as number) ?? 14;
    return (
      all<{ n: number }>(
        `SELECT COUNT(*) AS n FROM tracker_items
         WHERE status='active' AND expires_at IS NOT NULL
           AND date(expires_at) <= date('now', '+' || ? || ' days')`,
        [days],
      )[0]?.n > 0
    );
  }

  return false;
}

function cronMatches(cron: string, now: Date): boolean {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  const [min, hour, dom, mon, dow] = parts;

  const field = (spec: string, value: number): boolean => {
    if (spec === "*") return true;
    return spec
      .split(",")
      .some((token) => {
        if (token.includes("/")) {
          const [, step] = token.split("/");
          return Number(step) > 0 && value % Number(step) === 0;
        }
        if (token.includes("-")) {
          const [lo, hi] = token.split("-").map(Number);
          return value >= lo && value <= hi;
        }
        return Number(token) === value;
      });
  };

  // Tolerate a coarse tick: treat the whole hour as matching the minute field.
  return (
    field(hour, now.getHours()) &&
    field(dom, now.getDate()) &&
    field(mon, now.getMonth() + 1) &&
    field(dow, now.getDay()) &&
    (min === "*" || now.getMinutes() >= Number(min.split(",")[0] || 0) - 8)
  );
}

async function runAutomation(row: AutomationRow): Promise<string> {
  const config = json<Record<string, unknown>>(row.action_config, {});

  if (row.action_type === "run_skill") {
    const skill = config.skill as string;
    const reply = await ask({
      message: `Run the "${skill}" skill now. This is an automated run triggered by "${row.name}".`,
      actor: "ishay",
      channel: "automation" as never,
    });
    return reply.text.slice(0, 500);
  }

  if (row.action_type === "agent_prompt") {
    const reply = await ask({
      message: config.prompt as string,
      actor: "ishay",
      channel: "automation" as never,
    });
    return reply.text.slice(0, 500);
  }

  return `No handler for action type "${row.action_type}"`;
}

export function listAutomations() {
  return all(
    `SELECT id, name, description, trigger_type, action_type, enabled, last_run_at, last_result
     FROM automations ORDER BY name`,
  );
}
