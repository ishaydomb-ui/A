/**
 * Household facts tests. Run with:  npx tsx scripts/test-facts.ts
 *
 * Covers the three question shapes the store exists to answer, and the one
 * property that matters most: a sensitive value must not be readable by
 * anyone who gets hold of the database file.
 */
process.env.CREDENTIALS_KEY ||= "0".repeat(64);
// Fixed path, assigned unconditionally: this file DELETES the database it points
// at, so it must never inherit DATABASE_PATH from the environment. Honouring an
// inherited value would let `DATABASE_PATH=/data/beitenu.sqlite npm test` wipe the
// real household.
process.env.DATABASE_PATH = "/tmp/beitenu-test-facts.sqlite";

import fs from "node:fs";
fs.rmSync(process.env.DATABASE_PATH!, { force: true });
fs.rmSync(`${process.env.DATABASE_PATH!}-wal`, { force: true });

import { db, all, run } from "../src/lib/db";
import { rememberFact, recallFacts, expiringFacts, forgetFact } from "../src/lib/facts";

db();
run(`INSERT OR IGNORE INTO people (key, name, role) VALUES ('ishay','Ishay','adult')`);

let failures = 0;
function check(label: string, ok: boolean, detail?: unknown) {
  if (ok) console.log(`  ✓ ${label}`);
  else {
    failures++;
    console.log(`  ✗ ${label}`, detail ?? "");
  }
}

const iso = (days: number) =>
  new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

console.log("\n— Standing facts —");
rememberFact({ subject: "Yanai", label: "ID number", value: "312345678", category: "identity" });
rememberFact({ subject: "mum", label: "building code", value: "4821#", category: "access" });
rememberFact({
  subject: "garage",
  label: "location of the drill",
  value: "top shelf, blue toolbox",
  category: "location",
});

check(
  "answers 'what is Yanai's ID'",
  recallFacts({ query: "yanai" })[0]?.value === "312345678",
);
check(
  "answers 'what's mum's building code'",
  recallFacts({ query: "building code" })[0]?.value === "4821#",
);
check(
  "answers 'where did we put the drill'",
  recallFacts({ query: "drill" })[0]?.value === "top shelf, blue toolbox",
);
check("subject lookup is case-insensitive", recallFacts({ subject: "yanai" }).length === 1);

console.log("\n— Sensitive values are encrypted at rest —");
const raw = all<{ value: string; sensitive: number }>(
  `SELECT value, sensitive FROM facts WHERE lower(label) = 'id number'`,
)[0];
check("identity facts default to sensitive", raw.sensitive === 1);
check("the ID is NOT stored in plaintext", !raw.value.includes("312345678"), raw.value);
check(
  "a location fact stays readable (not over-encrypted)",
  all<{ value: string }>(`SELECT value FROM facts WHERE category = 'location'`)[0].value ===
    "top shelf, blue toolbox",
);

console.log("\n— Updating rather than duplicating —");
rememberFact({ subject: "mum", label: "building code", value: "9999#", category: "access" });
const codes = recallFacts({ query: "building code" });
check("re-stating a fact updates it in place", codes.length === 1, codes.length);
check("and the new value wins", codes[0].value === "9999#");

console.log("\n— 'When was the last time…' —");
rememberFact({ subject: "ishay", label: "blood test", value: "routine", occurredOn: "2026-02-11" });
rememberFact({ subject: "ishay", label: "blood test", value: "routine", occurredOn: "2026-07-30" });
const tests = recallFacts({ query: "blood test" });
check("occurrences accumulate rather than overwrite", tests.length === 2, tests.length);
const latest = recallFacts({ query: "blood test", latestOnly: true });
check("latest_only returns just the most recent", latest.length === 1, latest.length);
check("and it is the newest date", latest[0].occurred_on === "2026-07-30", latest[0].occurred_on);

console.log("\n— Renewals —");
rememberFact({
  subject: "ishay",
  label: "driving licence",
  value: "expires soon",
  category: "admin",
  validUntil: iso(20),
});
rememberFact({
  subject: "ishay",
  label: "passport",
  value: "fine for ages",
  category: "admin",
  validUntil: iso(900),
});
const due = expiringFacts(30);
check("surfaces a renewal due within 30 days", due.length === 1, due.map((d) => d.label));
check("ignores one that is years away", !due.some((d) => d.label === "passport"));

rememberFact({
  subject: "flat",
  label: "alarm code",
  value: "1234",
  category: "access",
  validUntil: iso(10),
});
const dueSensitive = expiringFacts(30).find((f) => f.label === "alarm code");
check(
  "a renewal prompt withholds the secret itself",
  dueSensitive?.value === "(sensitive)",
  dueSensitive?.value,
);

console.log("\n— Forgetting —");
const target = recallFacts({ query: "drill" })[0];
check("deletes on request", forgetFact(target.id));
check("and it is really gone", recallFacts({ query: "drill" }).length === 0);

console.log("\n— Nothing leaks into the activity log —");
const leaked = all<{ n: number }>(
  `SELECT COUNT(*) AS n FROM activity
   WHERE summary LIKE '%312345678%' OR summary LIKE '%9999#%' OR detail LIKE '%312345678%'`,
)[0].n;
check("values never appear in the audit trail", leaked === 0, leaked);

console.log(
  failures === 0 ? `\n✅ All facts checks passed.\n` : `\n❌ ${failures} check(s) failed.\n`,
);
process.exit(failures === 0 ? 0 : 1);
