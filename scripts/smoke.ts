/**
 * Exercises the data layer the agent depends on, without calling the model.
 * This is what proves "which coupons are still available" returns the right
 * rows - the agent only has to read them out.
 *
 * Run with:  npx tsx scripts/smoke.ts
 */
import { db, run, all } from "../src/lib/db";
import { addItem, queryItems, createTracker, sweepExpired, getTracker } from "../src/lib/trackers";
import { requestApproval, listApprovals, decide } from "../src/lib/approvals";
import { pickupSchedule, conflicts } from "../src/lib/schedule";

db();

let failures = 0;
function check(label: string, condition: boolean, detail?: unknown) {
  if (condition) {
    console.log(`  ✓ ${label}`);
  } else {
    failures++;
    console.log(`  ✗ ${label}`, detail ?? "");
  }
}

const iso = (daysFromNow: number) =>
  new Date(Date.now() + daysFromNow * 86_400_000).toISOString().slice(0, 10);

console.log("\n— Trackers: coupon availability —");
run(`DELETE FROM tracker_items WHERE tracker_id = (SELECT id FROM trackers WHERE key='coupons')`);

addItem("coupons", { title: "BuyMe 150₪", store: "BuyMe", value: 150, expires: iso(30) });
addItem("coupons", { title: "Shufersal 50₪", store: "Shufersal", value: 50, expires: iso(5) });
addItem("coupons", { title: "Expired cinema", store: "Cinema City", value: 60, expires: iso(-3) });
const used = addItem("coupons", { title: "Used voucher", store: "Next", value: 80, expires: iso(60) });
if ("id" in used) {
  run(`UPDATE tracker_items SET status='used' WHERE id = ?`, [used.id]);
}

const available = queryItems("coupons", { availableOnly: true });
check("returns only unexpired, unused coupons", available.length === 2, available.map((i) => i.data.title));
check(
  "excludes the expired one",
  !available.some((i) => i.data.title === "Expired cinema"),
);
check("excludes the used one", !available.some((i) => i.data.title === "Used voucher"));

const expiringSoon = queryItems("coupons", { expiringWithinDays: 7 });
check("finds coupons expiring within 7 days", expiringSoon.length === 1, expiringSoon.length);

const dup = addItem("coupons", { title: "BuyMe 150₪", store: "BuyMe", value: 150, expires: iso(30) });
check("dedupes a repeated coupon", "duplicateOf" in dup);

const swept = sweepExpired();
check("sweep retires expired items", swept.archived + swept.flagged >= 1, swept);

console.log("\n— Trackers: creating a new rubric on the fly —");
run(`DELETE FROM trackers WHERE key = 'books'`);
createTracker({
  key: "books",
  name: "Books to read",
  icon: "📚",
  fields: [
    { name: "title", label: "Title", type: "text", required: true },
    { name: "author", label: "Author", type: "text" },
  ],
  behaviors: { dedupeOn: ["title"] },
});
addItem("books", { title: "Project Hail Mary", author: "Andy Weir" });
check("new tracker exists without a migration", !!getTracker("books"));
check("new tracker holds items", queryItems("books", { status: "any" }).length === 1);

console.log("\n— Schedule: who does pickup —");
run(`DELETE FROM events WHERE external_id LIKE 'smoke-%'`);
const ishay = all<{ id: number }>(`SELECT id FROM people WHERE key='ishay'`)[0];
const liran = all<{ id: number }>(`SELECT id FROM people WHERE key='liran'`)[0];

run(
  `INSERT INTO events (external_id, title, starts_at, ends_at, kind, owner_id)
   VALUES ('smoke-1', 'Pickup Yanai & Berry', ?, ?, 'pickup', ?)`,
  [`${iso(1)}T14:00:00.000Z`, `${iso(1)}T15:00:00.000Z`, ishay.id],
);
run(
  `INSERT INTO events (external_id, title, starts_at, ends_at, kind, owner_id)
   VALUES ('smoke-2', 'Pickup Yanai & Berry', ?, ?, 'pickup', ?)`,
  [`${iso(2)}T14:00:00.000Z`, `${iso(2)}T15:00:00.000Z`, liran.id],
);

const allPickups = pickupSchedule(iso(0), iso(7));
check("finds both pickup runs", allPickups.length === 2, allPickups);

const mine = pickupSchedule(iso(0), iso(7), "ishay");
check("filters pickups to one person", mine.length === 1 && mine[0].owner === "Ishay", mine);

const clash = conflicts(`${iso(1)}T14:30:00.000Z`, `${iso(1)}T15:30:00.000Z`);
check("detects a scheduling clash", clash.length === 1, clash.map((c) => c.title));

console.log("\n— Approvals: nothing happens without a human —");
const req = requestApproval({
  kind: "fill_cart",
  title: "Fill Shufersal basket (smoke test)",
  payload: { listId: 0, items: [] },
  risk: "high",
});
check("approval starts pending", req.status === "pending");
check("approval appears in the queue", listApprovals("pending").some((a) => a.id === req.approvalId));

decide(req.approvalId, "rejected", "ishay");
check(
  "rejected approval leaves the queue",
  !listApprovals("pending").some((a) => a.id === req.approvalId),
);

console.log("\n— Audit trail —");
const activity = all<{ n: number }>(`SELECT COUNT(*) AS n FROM activity`)[0];
check("every action was logged", activity.n > 0, activity);

console.log(
  failures === 0 ? "\n✅ All checks passed.\n" : `\n❌ ${failures} check(s) failed.\n`,
);
process.exit(failures === 0 ? 0 : 1);
