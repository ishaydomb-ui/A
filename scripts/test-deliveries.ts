/**
 * Delivery parsing and tracking. Run with:  npx tsx scripts/test-deliveries.ts
 *
 * Every case is a real email from the household inbox. The negative cases carry
 * as much weight as the positive ones: an airline booking and a credit-card
 * statement both look like "order" mail and neither is a parcel.
 */
// Assigned unconditionally - this file deletes the database it points at.
process.env.DATABASE_PATH = "/tmp/beitenu-test-deliveries.sqlite";

import fs from "node:fs";
for (const suffix of ["", "-wal", "-shm"]) {
  fs.rmSync(`${process.env.DATABASE_PATH}${suffix}`, { force: true });
}

import { db, all, run as rawRun } from "../src/lib/db";
import { parseDeliveryEmail } from "../src/lib/email/parse-delivery";
import { ingestEmail, pendingDeliveries, staleDeliveries, setStatus } from "../src/lib/deliveries";

db();

let failures = 0;
function check(label: string, ok: boolean, detail?: unknown) {
  if (ok) console.log(`  ✓ ${label}`);
  else {
    failures++;
    console.log(`  ✗ ${label}`, detail ?? "");
  }
}

console.log("\n— Recognising real order mail —");

const iherbDelivered = parseDeliveryEmail({
  from: "noreply@info.iherb.com",
  subject: "Order Delivered #554558877",
  snippet: "Real Mushrooms, Reishi, Mushroom Extract Powder, 9 ... plus 6 more items",
});
check("iHerb delivery notice", iherbDelivered?.status === "delivered", iherbDelivered);
check("extracts the order number", iherbDelivered?.orderRef === "554558877", iherbDelivered?.orderRef);

const nextPickup = parseDeliveryEmail({
  from: "DoNotReply@m.nextdirect.com",
  subject: "Your order is ready for pickup.",
  snippet: "Your available items have now arrived at your local post office.",
});
check("Next ready-for-pickup", nextPickup?.status === "ready_for_pickup", nextPickup);

const evrit = parseDeliveryEmail({
  from: "service@e-vrit.co.il",
  subject: "ההזמנה שלך התקבלה!",
  snippet: "ההזמנה שלך התקבלה בהצלחה ועוד מעט בדרך אליך מספר הזמנה: 11519925",
});
check("Hebrew order confirmation", evrit?.status === "ordered", evrit);
check("extracts a Hebrew order number", evrit?.orderRef === "11519925", evrit?.orderRef);

const shipped = parseDeliveryEmail({
  from: "orders@ksp.co.il",
  subject: "ההזמנה שלך נשלחה",
  snippet: "מספר מעקב 12345678",
});
check("Hebrew 'shipped'", shipped?.status === "shipped", shipped);

console.log("\n— Things that are NOT parcels —");

const cases: Array<[string, string, string]> = [
  ["dontreply@arkia.co.il", "סיכום הזמנה ארקיע 13710917", "flight booking"],
  ["calmail@icc.co.il", "ישי- דף פירוט החיוב החודשי שלך זמין", "credit card statement"],
  ["service@cinema-city.co.il", "סינמה סיטי נתניה - אישור הזמנה", "cinema tickets"],
  ["ebay@ebay.com", "converse high tops men grey: 4 NEW!", "saved-search alert"],
  ["No_Reply@pizza.dominos.co.il", "משפחתית ב-39.90 ₪ (פרסומת)", "pizza promo"],
  ["noreplys@dpd.iec.co.il", "דומב ישי מספר חשבון חוזה 340574198", "electricity bill"],
  ["toktok@info.glovoapp.com", "First time? 50% off + free delivery", "food-delivery marketing"],
];
for (const [from, subject, what] of cases) {
  check(`ignores ${what}`, parseDeliveryEmail({ from, subject }) === null);
}

console.log("\n— One parcel, four emails —");

ingestEmail({
  from: "noreply@info.iherb.com",
  subject: "Thank you for your order #554558877",
  messageId: "m1",
});
ingestEmail({
  from: "noreply@info.iherb.com",
  subject: "Your order #554558877 has shipped",
  messageId: "m2",
});
const update = ingestEmail({
  from: "noreply@info.iherb.com",
  subject: "Update on Your Order #554558877-0",
  snippet: "Order Update",
  messageId: "m3",
});
check("a stateless update changes nothing", update.action === "unchanged", update.action);
check("and the suffixed ref matched the same parcel", update.delivery?.order_ref === "554558877");

const delivered = ingestEmail({
  from: "noreply@info.iherb.com",
  subject: "Order Delivered #554558877",
  messageId: "m4",
});
check("delivery advances the same row", delivered.action === "advanced", delivered.action);

const rows = all<{ n: number }>(`SELECT COUNT(*) AS n FROM deliveries WHERE vendor='iHerb'`)[0];
check("four emails produced ONE parcel", rows.n === 1, rows.n);

const late = ingestEmail({
  from: "noreply@info.iherb.com",
  subject: "Your order #554558877 has shipped",
  messageId: "m5",
});
check("a late 'shipped' does not reopen it", late.delivery?.status === "delivered", late.delivery?.status);

console.log("\n— The snapshot —");

ingestEmail({
  from: "DoNotReply@m.nextdirect.com",
  subject: "Your order is ready for pickup.",
  messageId: "m6",
});
ingestEmail({
  from: "service@e-vrit.co.il",
  subject: "ההזמנה שלך התקבלה! מספר הזמנה: 11519925",
  messageId: "m7",
});

const pending = pendingDeliveries();
check("shows only what is still in flight", pending.length === 2, pending.map((p) => p.vendor));
check(
  "ready-for-pickup sorts first — it needs action",
  pending[0].status === "ready_for_pickup",
  pending[0].status,
);
check(
  "delivered parcels drop out of the snapshot",
  !pending.some((p) => p.vendor === "iHerb"),
);

console.log("\n— Parcels that went quiet —");
rawRun(`UPDATE deliveries SET last_update = datetime('now','-30 days') WHERE vendor = 'עברית'`);
const stale = staleDeliveries(14);
check("flags an order that never arrived", stale.length === 1, stale.map((s) => s.vendor));
check("but not a recent one", !stale.some((s) => s.vendor === "Next"));

setStatus(stale[0].id, "delivered", "ishay");
check("marking it delivered clears it", staleDeliveries(14).length === 0);

console.log(
  failures === 0 ? `\n✅ All delivery checks passed.\n` : `\n❌ ${failures} check(s) failed.\n`,
);
process.exit(failures === 0 ? 0 : 1);
