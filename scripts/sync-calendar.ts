/**
 * Sync calendars from the command line, for the first run and for debugging.
 *
 * Run with:  npx tsx scripts/sync-calendar.ts [person-key]
 */
import { db } from "../src/lib/db";
import { syncAll, syncPerson, personIdByKey, syncStatus } from "../src/lib/google/calendar";

db();

const who = process.argv[2];

async function main() {
  if (who) {
    const id = personIdByKey(who);
    if (!id) {
      console.error(`No person with key "${who}".`);
      process.exit(1);
    }
    console.log(JSON.stringify(await syncPerson(id), null, 2));
  } else {
    const results = await syncAll();
    if (Object.keys(results).length === 0) {
      console.log(
        "Nobody has connected Google yet. Sign in at /login first, then run this again.",
      );
      return;
    }
    console.log(JSON.stringify(results, null, 2));
  }

  console.log("\nCalendars:");
  console.table(syncStatus());
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
