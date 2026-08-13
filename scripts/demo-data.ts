/**
 * Populate a database with a realistic week, so the screens can be looked at
 * with something in them. Uses the household's real patterns — Hebrew event
 * titles, the actual vendors, the real budget categories.
 *
 * Demo only. Never point this at the live database.
 *   npx tsx scripts/demo-data.ts
 */
process.env.DATABASE_PATH = process.env.DEMO_DB || "/tmp/beitenu-demo.sqlite";
process.env.CREDENTIALS_KEY ||= "0".repeat(64);

import fs from "node:fs";
for (const suffix of ["", "-wal", "-shm"]) {
  fs.rmSync(`${process.env.DATABASE_PATH}${suffix}`, { force: true });
}

import { db, run, one } from "../src/lib/db";
import { seed } from "../src/lib/seed";
import { addItem } from "../src/lib/trackers";
import { rememberFact } from "../src/lib/facts";
import { setFocus } from "../src/lib/focus";
import { createDraft } from "../src/lib/drafts";
import { requestApproval } from "../src/lib/approvals";
import { ingestEmail } from "../src/lib/deliveries";
import { getRoom } from "../src/lib/room";

db();
seed();

const day = (offset: number, time = "00:00") => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const [h, m] = time.split(":").map(Number);
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};
const date = (offset: number) => day(offset).slice(0, 10);

const person = (key: string) => one<{ id: number }>(`SELECT id FROM people WHERE key=?`, [key])!.id;
const ishay = person("ishay");
const liran = person("liran");
const yanai = person("yanai");
const berry = person("berry");

// ------------------------------------------------------------------ calendar
const events: Array<[string, string, string | null, string, number | null, number | null]> = [
  ["לאסוף את ינאי וברי מהגן", day(0, "14:00"), day(0, "15:00"), "pickup", null, ishay],
  ["חוג ברי", day(0, "17:00"), day(0, "18:00"), "class", berry, ishay],
  ["לירן בתורנות", day(1, "08:00"), day(2, "08:00"), "oncall", liran, liran],
  ["חוג ינאי", day(2, "17:30"), day(2, "18:30"), "class", yanai, liran],
  ["תור רופא שיניים ינאי וברי", day(3, "18:20"), day(3, "19:00"), "appointment", null, ishay],
  ["לאסוף את ינאי וברי מהגן", day(3, "14:00"), day(3, "15:00"), "pickup", null, liran],
  ["יום הולדת לברי – מסיבה בפארק", day(9, "16:00"), day(9, "18:30"), "occasion", berry, ishay],
  ["טכנאי מזגנים", day(5, "09:00"), day(5, "11:00"), "home", null, ishay],
];
events.forEach(([title, start, end, kind, subject, owner], i) => {
  run(
    `INSERT INTO events (external_id, calendar_id, title, starts_at, ends_at, kind, subject_id, owner_id, source, synced_at)
     VALUES (?, 'primary', ?, ?, ?, ?, ?, ?, 'google', datetime('now'))`,
    [`demo-${i}`, title, start, end, kind, subject, owner],
  );
});

// ------------------------------------------------------------------ focus
setFocus({
  title: "יום הולדת 3 לברי",
  note: "Saturday at the park — 14 kids, cake ordered, still need the entertainer",
  entityType: "tracker",
  entityRef: "gifts",
  until: date(10),
  actor: "ishay",
});

// ------------------------------------------------------------------ tasks
const tasks: Array<[string, string | null, number | null, string, string]> = [
  ["להזמין מתנפח למסיבה", date(2), ishay, "high", "kids"],
  ["לשלם ארנונה", date(1), liran, "urgent", "money"],
  ["לחדש ביטוח רכב", date(4), ishay, "normal", "admin"],
  ["לקנות מתנה ליום הולדת של דניאל", date(6), liran, "normal", "kids"],
];
tasks.forEach(([title, due, assignee, priority, area]) => {
  run(
    `INSERT INTO tasks (title, due_at, assignee_id, priority, area, source) VALUES (?,?,?,?,?,'agent')`,
    [title, due, assignee, priority, area],
  );
});

// ------------------------------------------------------------------ case
run(
  `INSERT INTO cases (title, status, summary, subject_id, reference, due_at, next_action, next_action_at, chase_after)
   VALUES (?, 'waiting', ?, ?, ?, ?, ?, ?, ?)`,
  [
    "ערר על שיבוץ ברי – גן תות יער",
    "הוגש ערר למחוז ת״א. אין מענה מאז 9.8",
    berry,
    "238514046",
    date(3),
    "לפנות שוב למנהלת המחוז",
    date(0),
    date(-1),
  ],
);

// ------------------------------------------------------------------ trackers
addItem("coupons", { title: "BuyMe 150₪", store: "BuyMe", value: 150, expires: date(21) });
addItem("coupons", { title: "שופרסל 50₪", store: "שופרסל", value: 50, expires: date(6) });
addItem("coupons", { title: "Castro 100₪", store: "Castro", value: 100, expires: date(45) });
addItem("gifts", { idea: "אופניים 16 אינץ׳", recipient: "ברי", occasion: "יום הולדת", budget: 600 });
addItem("watchlist", { title: "Dune: Part Two", kind: "film", where: "Netflix", who: "us" });

// ------------------------------------------------------------------ facts
rememberFact({ subject: "ינאי", label: "מספר ת״ז", value: "312345678", category: "identity" });
rememberFact({ subject: "ברי", label: "מספר ת״ז", value: "238514046", category: "identity" });
rememberFact({ subject: "אמא", label: "קוד בניין", value: "4821#", category: "access" });
rememberFact({
  subject: "מחסן",
  label: "איפה המקדחה",
  value: "מדף עליון, ארגז כלים כחול",
  category: "location",
});
rememberFact({
  subject: "ישי",
  label: "רישיון נהיגה",
  value: "בתוקף",
  category: "admin",
  validUntil: date(24),
});
rememberFact({ subject: "ישי", label: "בדיקות דם", value: "שגרתי", occurredOn: date(-14) });

// ------------------------------------------------------------------ deliveries
ingestEmail({ from: "noreply@info.iherb.com", subject: "Order Delivered #554558877", messageId: "d1" });
ingestEmail({ from: "DoNotReply@m.nextdirect.com", subject: "Your order is ready for pickup.", messageId: "d2" });
ingestEmail({ from: "service@e-vrit.co.il", subject: "ההזמנה שלך התקבלה! מספר הזמנה: 11519925", messageId: "d3" });
run(`UPDATE deliveries SET last_update = datetime('now','-21 days') WHERE vendor='עברית'`);

// ------------------------------------------------------------------ food
run(`INSERT INTO meal_plan (plan_date, meal, title, cook_id) VALUES (?,'dinner',?,?)`, [
  date(0), "פסטה ברוטב עגבניות + סלט", ishay,
]);
run(`INSERT INTO meal_plan (plan_date, meal, title, cook_id) VALUES (?,'dinner',?,?)`, [
  date(1), "שאריות + טוסטים (לירן בתורנות)", ishay,
]);
run(`INSERT INTO meal_plan (plan_date, meal, title, cook_id) VALUES (?,'dinner',?,?)`, [
  date(2), "עוף בתנור עם ירקות שורש", liran,
]);
["חלב", "יוגורט לילדים", "עגבניות"].forEach((name, i) =>
  run(
    `INSERT INTO pantry_items (name, qty, unit, expires_at, staple) VALUES (?,1,'יח',?,?)`,
    [name, date(i + 1), i === 0 ? 1 : 0],
  ),
);
const list = run(
  `INSERT INTO grocery_lists (name, chain, status, est_total) VALUES (?,?,'open',?)`,
  ["Groceries this week", "shufersal", 412.5],
);
["חלב 3%", "לחם מחיטה מלאה", "עגבניות", "חזה עוף", "יוגורט"].forEach((n) =>
  run(`INSERT INTO grocery_items (list_id, name, qty, est_price, source) VALUES (?,?,1,?, 'meal_plan')`, [
    list.lastInsertRowid, n, Math.round(Math.random() * 30 + 6),
  ]),
);

// ------------------------------------------------------------------ draft + approval
createDraft({
  to: ["district.ta@education.gov.il"],
  cc: ["lirikor@gmail.com"],
  subject: "תזכורת – ערר על שיבוץ ברי קורוטקין ברזלי (ת״ז 238514046)",
  body:
    "לכבוד מנהלת מחוז תל אביב,\n\nביום 9.8.2026 הגשנו ערר על החלטת עיריית תל אביב-יפו לדחות את בקשתנו " +
    "להעביר את בננו ברי לגן תות יער. עד היום לא התקבל מענה ענייני.\n\nלאור פתיחת שנת הלימודים בעוד " +
    "כשלושה שבועות, נבקש הכרעה מנומקת בהקדם.\n\nבכבוד רב,\nעו״ד ישי דומב\nד״ר לירן קורוטקין ברזלי",
  language: "he",
  skillKey: "bureaucracy-escalation",
  actor: "agent",
});

requestApproval({
  kind: "fill_cart",
  title: "Fill שופרסל basket: Groceries this week",
  summary:
    "17 items, estimated 412 ILS. The worker stops at the filled basket — it will not check out or pay.",
  payload: { listId: list.lastInsertRowid, chain: "shufersal", estimate: 412.5 },
  risk: "high",
  requestedBy: "agent",
  skillKey: "grocery-run",
});

// ------------------------------------------------------------------ the room
const room = getRoom();
const chat: Array<[string, number | null, string]> = [
  ["user", ishay, "חושב שעדיף לעשות את המסיבה בפארק ולא בבית"],
  ["user", liran, "מסכימה. בבית זה בלגן שלם ואין מקום ל-14 ילדים"],
  ["user", ishay, "בשעה 4? ככה זה לא חם מדי"],
  ["user", liran, "כן. אבל צריך לבדוק שאני לא בתורנות"],
  ["user", ishay, "תבדוק אם לירן בתורנות בשבת הקרובה"],
  [
    "assistant",
    null,
    "לירן בתורנות מחר (יום ג׳) עד יום ד׳ בבוקר — שבת פנויה. אין התנגשויות ב-16:00.\nיצרתי משימה: להזמין מתנפח למסיבה, ליום ג׳.",
  ],
  ["user", liran, "מעולה. צריך גם מתנפח וגם ליצן?"],
  ["user", ishay, "רק מתנפח, ליצן זה יותר מדי"],
];
chat.forEach(([role, pid, content]) =>
  run(
    `INSERT INTO messages (conversation_id, role, channel, content, person_id) VALUES (?,?,'room',?,?)`,
    [room, role, content, pid],
  ),
);

console.log(`Demo household ready at ${process.env.DATABASE_PATH}`);
