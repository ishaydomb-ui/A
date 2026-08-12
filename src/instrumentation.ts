/**
 * Runs once when the server starts.
 *
 * This is what makes a hosted deploy self-configuring: the container comes up,
 * applies the schema, seeds the household, and starts the scheduler - without
 * anyone opening a terminal. That matters because neither of them should need a
 * laptop to run this.
 */
export async function register() {
  // Next also invokes this for the edge runtime, which has no filesystem.
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const { db, logActivity } = await import("./lib/db");
  const { seed } = await import("./lib/seed");

  try {
    db(); // creates the file and applies schema.sql
    const firstRun = seed();
    if (firstRun) {
      logActivity({
        actor: "automation",
        action: "initialised",
        summary: "First boot: household seeded with skills, trackers and automations",
      });
      console.log("[beitenu] first boot - household seeded");
    }
  } catch (err) {
    // A failure here means the volume is missing or unwritable. Log loudly and
    // let the health check fail rather than serving a half-working app.
    console.error("[beitenu] startup failed:", (err as Error).message);
    return;
  }

  if (process.env.ENABLE_SCHEDULER === "1") startScheduler();
}

/**
 * In-process scheduler.
 *
 * The platform's own cron would work, but it bills a separate container per
 * firing and needs its own auth round-trip. One interval inside the running
 * server is simpler and cheaper for a household-sized workload. The automations
 * themselves decide what is actually due, so a coarse tick is fine.
 */
function startScheduler() {
  const MINUTES = Number(process.env.SCHEDULER_INTERVAL_MINUTES || 15);
  const interval = Math.max(5, MINUTES) * 60_000;

  const tick = async () => {
    try {
      const { runDueAutomations } = await import("./lib/automations");
      const result = await runDueAutomations();
      if (result.ran.length) {
        console.log(`[beitenu] ran ${result.ran.length} automation(s)`);
      }
    } catch (err) {
      console.error("[beitenu] scheduler tick failed:", (err as Error).message);
    }
  };

  // Wait a little before the first tick so the server finishes booting and the
  // platform's health check passes before we start doing real work.
  setTimeout(tick, 60_000);
  setInterval(tick, interval);
  console.log(`[beitenu] scheduler on, every ${Math.max(5, MINUTES)} min`);
}
