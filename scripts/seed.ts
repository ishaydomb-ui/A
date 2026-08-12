/**
 * Seed from the command line. The hosted app seeds itself on boot
 * (see src/instrumentation.ts) - this is for local use.
 *
 * Run with:  npm run db:seed
 */
import { db } from "../src/lib/db";
import { seed } from "../src/lib/seed";

db(); // applies schema.sql
const firstRun = seed();

console.log(
  firstRun
    ? "Seeded a fresh household: people, budget categories, trackers, skills, automations."
    : "Household already existed - refreshed trackers, skills and standing facts.",
);
